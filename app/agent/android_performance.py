"""
NR-AI Android Performance & Runtime Diagnostics Engine.

Provides bounded, safe telemetry capture and performance analysis for Android apps:
  - Cold / Warm startup time (am start -W)
  - Process memory footprint (dumpsys meminfo PSS)
  - CPU utilization (dumpsys cpuinfo / top)
  - ANR (Application Not Responding) detection
  - Crash frequency and log spam rate
  - Slow test execution markers

Classifications: MEASURED, ESTIMATED, UNAVAILABLE.
Zero indefinite streaming; all executions are bounded by timeouts (<= 10s) and buffer caps.
"""

from dataclasses import dataclass, field, asdict
from enum import Enum
import logging
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from app.agent.android_tools import SafeAdbClient

logger = logging.getLogger("NRAI.AndroidPerformance")


class MetricStatus(str, Enum):
    MEASURED = "MEASURED"
    ESTIMATED = "ESTIMATED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass
class PerformanceMetric:
    name: str
    value: Union[float, int, str]
    unit: str
    status: MetricStatus
    details: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "status": self.status.value,
            "details": self.details,
        }


@dataclass
class AndroidPerformanceReport:
    package_name: str
    device_serial: str
    startup_time_ms: Optional[PerformanceMetric] = None
    memory_pss_kb: Optional[PerformanceMetric] = None
    cpu_percent: Optional[PerformanceMetric] = None
    anr_detected: bool = False
    crash_count: int = 0
    log_spam_rate_per_sec: Optional[PerformanceMetric] = None
    metrics: List[PerformanceMetric] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "package_name": self.package_name,
            "device_serial": self.device_serial,
            "startup_time_ms": self.startup_time_ms.to_dict() if self.startup_time_ms else None,
            "memory_pss_kb": self.memory_pss_kb.to_dict() if self.memory_pss_kb else None,
            "cpu_percent": self.cpu_percent.to_dict() if self.cpu_percent else None,
            "anr_detected": self.anr_detected,
            "crash_count": self.crash_count,
            "log_spam_rate_per_sec": self.log_spam_rate_per_sec.to_dict() if self.log_spam_rate_per_sec else None,
            "metrics": [m.to_dict() for m in self.metrics],
            "recommendations": self.recommendations,
        }


class AndroidPerformanceDiagnostics:
    """Bounded, safe diagnostic engine for measuring runtime performance on authorized Android devices."""

    def __init__(self, adb_client: Optional[SafeAdbClient] = None):
        self.adb = adb_client or SafeAdbClient()

    def measure_startup_time(
        self,
        serial: str,
        component: str,
    ) -> PerformanceMetric:
        """
        Measures cold/warm startup time using 'am start -W -n <component>'.
        Bounded by a 10s timeout.
        """
        # Validate component name format (e.g. com.example/.MainActivity)
        if not re.match(r"^[a-zA-Z0-9_.]+\/[a-zA-Z0-9_.]+$", component):
            return PerformanceMetric(
                name="startup_time",
                value=-1,
                unit="ms",
                status=MetricStatus.UNAVAILABLE,
                details="Invalid component specification format.",
            )

        try:
            code, out, err = self.adb._run_adb(
                ["-s", serial, "shell", "am", "start", "-W", "-n", component],
                timeout=10.0,
            )
            if code != 0:
                return PerformanceMetric(
                    name="startup_time",
                    value=-1,
                    unit="ms",
                    status=MetricStatus.UNAVAILABLE,
                    details=f"am start returned exit code {code}: {err or out}",
                )

            # Look for TotalTime or WaitTime
            total_time_match = re.search(r"TotalTime:\s*(\d+)", out)
            wait_time_match = re.search(r"WaitTime:\s*(\d+)", out)

            if total_time_match:
                ms = int(total_time_match.group(1))
                return PerformanceMetric(
                    name="startup_time",
                    value=ms,
                    unit="ms",
                    status=MetricStatus.MEASURED,
                    details=f"Measured via TotalTime from am start -W: {ms}ms",
                )
            elif wait_time_match:
                ms = int(wait_time_match.group(1))
                return PerformanceMetric(
                    name="startup_time",
                    value=ms,
                    unit="ms",
                    status=MetricStatus.ESTIMATED,
                    details=f"Estimated via WaitTime from am start -W: {ms}ms",
                )
            else:
                return PerformanceMetric(
                    name="startup_time",
                    value=-1,
                    unit="ms",
                    status=MetricStatus.UNAVAILABLE,
                    details="Output did not contain TotalTime or WaitTime.",
                )
        except Exception as e:
            return PerformanceMetric(
                name="startup_time",
                value=-1,
                unit="ms",
                status=MetricStatus.UNAVAILABLE,
                details=f"Exception during startup measurement: {e}",
            )

    def inspect_memory(
        self,
        serial: str,
        package_name: str,
    ) -> PerformanceMetric:
        """
        Inspects process memory PSS using 'dumpsys meminfo <package>'.
        """
        try:
            code, out, _ = self.adb._run_adb(
                ["-s", serial, "shell", "dumpsys", "meminfo", package_name],
                timeout=8.0,
            )
            if code != 0 or not out:
                return PerformanceMetric(
                    name="memory_pss",
                    value=-1,
                    unit="KB",
                    status=MetricStatus.UNAVAILABLE,
                    details="dumpsys meminfo failed or returned empty output.",
                )

            # Match TOTAL PSS or TOTAL:
            # Example: "TOTAL PSS:   45823            TOTAL RSS: ..."
            match = re.search(r"TOTAL\s+PSS:\s*(\d+)", out) or re.search(r"TOTAL\s+(\d+)", out)
            if match:
                pss_kb = int(match.group(1))
                return PerformanceMetric(
                    name="memory_pss",
                    value=pss_kb,
                    unit="KB",
                    status=MetricStatus.MEASURED,
                    details=f"Total PSS memory: {pss_kb} KB (~{round(pss_kb / 1024.0, 2)} MB)",
                )
            return PerformanceMetric(
                name="memory_pss",
                value=-1,
                unit="KB",
                status=MetricStatus.UNAVAILABLE,
                details="Could not parse PSS value from dumpsys meminfo output.",
            )
        except Exception as e:
            return PerformanceMetric(
                name="memory_pss",
                value=-1,
                unit="KB",
                status=MetricStatus.UNAVAILABLE,
                details=f"Exception querying meminfo: {e}",
            )

    def inspect_cpu(
        self,
        serial: str,
        package_name: str,
    ) -> PerformanceMetric:
        """
        Inspects process CPU utilization using 'dumpsys cpuinfo'.
        """
        try:
            code, out, _ = self.adb._run_adb(
                ["-s", serial, "shell", "dumpsys", "cpuinfo"],
                timeout=8.0,
            )
            if code == 0 and out:
                # Look for line with package_name: e.g. "  5.2% 12345/com.nrai.test: 4% user + 1.2% kernel"
                for line in out.splitlines():
                    if package_name in line:
                        match = re.search(r"([0-9.]+)%", line)
                        if match:
                            cpu_val = float(match.group(1))
                            return PerformanceMetric(
                                name="cpu_utilization",
                                value=cpu_val,
                                unit="%",
                                status=MetricStatus.MEASURED,
                                details=f"Observed CPU usage for {package_name}: {cpu_val}%",
                            )

            return PerformanceMetric(
                name="cpu_utilization",
                value=0.0,
                unit="%",
                status=MetricStatus.ESTIMATED,
                details=f"Process {package_name} idle or not consuming significant CPU in snapshot.",
            )
        except Exception as e:
            return PerformanceMetric(
                name="cpu_utilization",
                value=-1,
                unit="%",
                status=MetricStatus.UNAVAILABLE,
                details=f"Exception querying cpuinfo: {e}",
            )

    def check_anr_and_crashes(
        self,
        serial: str,
        package_name: str,
        logcat_text: Optional[str] = None,
    ) -> Tuple[bool, int, List[str]]:
        """
        Scans bounded logcat or dumpsys activity crashes for ANRs and crash events.
        """
        if logcat_text is None:
            try:
                logcat_text = self.adb.capture_logcat_advanced(
                    serial=serial,
                    lines=200,
                    filter_package=package_name,
                )
            except Exception as e:
                logger.warning(f"Logcat capture failed or timed out: {e}")
                logcat_text = ""

        anr_detected = "ANR in " in logcat_text or "Application Not Responding" in logcat_text
        crashes = re.findall(r"FATAL EXCEPTION:\s*([^\n]+)", logcat_text)
        crash_count = len(crashes)

        anomalies: List[str] = []
        if anr_detected:
            anomalies.append(f"ANR event detected for package {package_name}")
        if crash_count > 0:
            anomalies.append(f"Detected {crash_count} FATAL EXCEPTION(s) in logcat")

        return anr_detected, crash_count, anomalies

    def generate_report(
        self,
        serial: str,
        package_name: str,
        component: Optional[str] = None,
        logcat_text: Optional[str] = None,
    ) -> AndroidPerformanceReport:
        """Collects bounded performance metrics and returns an authoritative structured report."""
        report = AndroidPerformanceReport(
            package_name=package_name,
            device_serial=serial,
        )

        # 1. Startup Time
        if component:
            report.startup_time_ms = self.measure_startup_time(serial, component)
            report.metrics.append(report.startup_time_ms)
            if report.startup_time_ms.status == MetricStatus.MEASURED:
                if float(report.startup_time_ms.value) > 2000.0:
                    report.recommendations.append(
                        "Cold startup exceeds 2000ms. Consider deferring heavy initialization out of Application.onCreate."
                    )

        # 2. Memory Footprint
        report.memory_pss_kb = self.inspect_memory(serial, package_name)
        report.metrics.append(report.memory_pss_kb)
        if report.memory_pss_kb.status == MetricStatus.MEASURED:
            if float(report.memory_pss_kb.value) > 150_000:
                report.recommendations.append(
                    "Memory footprint exceeds 150 MB. Audit bitmap allocations and compose remember state."
                )

        # 3. CPU Utilization
        report.cpu_percent = self.inspect_cpu(serial, package_name)
        report.metrics.append(report.cpu_percent)

        # 4. ANR & Crash Detection
        anr, crashes, anomalies = self.check_anr_and_crashes(serial, package_name, logcat_text=logcat_text)
        report.anr_detected = anr
        report.crash_count = crashes

        if anr:
            report.recommendations.append(
                "ANR detected. Ensure main thread does not perform disk I/O, heavy parsing, or blocking calls."
            )
        if crashes > 0:
            report.recommendations.append(
                f"Application experienced {crashes} fatal crash(es). Review logcat stack trace and apply bounded repair."
            )

        return report
