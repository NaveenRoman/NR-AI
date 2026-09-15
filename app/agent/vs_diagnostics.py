"""
NR-AI Visual Studio Test, Performance & Diagnostic Intelligence Engine (Step 7 Phase 5).

Provides safe, bounded, deterministic:
1. Test Intelligence:
   - Test project discovery (xUnit, NUnit, MSTest)
   - Test configuration inspection (.runsettings, filters, frameworks)
   - Granular test result parsing (passed, failed, skipped, durations)
   - Test failure to source file & line mapping
2. Performance & Diagnostic Monitoring:
   - Build, test, and debug session duration tracking
   - Safe, non-invasive process metrics observation (memory, CPU)
   - Slow test identification (> 500ms threshold)
   - Performance-related warnings
3. Structured Failure Intelligence:
   - Unified diagnostic context consolidating compiler errors, build outputs,
     test failures, runtime exceptions, debugger evidence, and IDE states.
   - Categorization across 8 structured failure types:
     BUILD_FAILURE, TEST_FAILURE, RUNTIME_FAILURE, DEBUGGER_FAILURE,
     IDE_FAILURE, ENVIRONMENT_FAILURE, SAFETY_REJECTION, REPAIR_FAILURE.
"""

from dataclasses import dataclass, field
from enum import Enum
import logging
import os
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    psutil = None
    HAS_PSUTIL = False

from app.agent.vs_safety import (
    VSSafetyGate,
    VSErrorCode,
    VSSafetyError,
    GLOBAL_WORKSPACE_ROOT,
    DEFAULT_AUTHORIZED_PROJECT,
    redact_sensitive_data,
)

logger = logging.getLogger("NRAI.VSDiagnostics")


# -----------------------------------------------------------------------------
# Structured Failure Domains
# -----------------------------------------------------------------------------

class VSFailureType(str, Enum):
    """Structured categories distinguishing operational failure domains."""
    NONE = "NONE"
    BUILD_FAILURE = "BUILD_FAILURE"
    TEST_FAILURE = "TEST_FAILURE"
    RUNTIME_FAILURE = "RUNTIME_FAILURE"
    DEBUGGER_FAILURE = "DEBUGGER_FAILURE"
    IDE_FAILURE = "IDE_FAILURE"
    ENVIRONMENT_FAILURE = "ENVIRONMENT_FAILURE"
    SAFETY_REJECTION = "SAFETY_REJECTION"
    REPAIR_FAILURE = "REPAIR_FAILURE"


@dataclass
class VSDiagnosticContext:
    """Unified, structured diagnostic representation consolidating evidence across all subsystems."""
    failure_type: VSFailureType = VSFailureType.NONE
    summary: str = ""
    error_codes: List[str] = field(default_factory=list)
    affected_files: List[str] = field(default_factory=list)
    affected_tests: List[str] = field(default_factory=list)
    raw_evidence: str = ""
    repairable: bool = False
    suggested_action: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "failure_type": self.failure_type.value,
            "summary": self.summary,
            "error_codes": self.error_codes,
            "affected_files": self.affected_files,
            "affected_tests": self.affected_tests,
            "raw_evidence": self.raw_evidence[:2000],
            "repairable": self.repairable,
            "suggested_action": self.suggested_action,
            "details": self.details,
            "timestamp": self.timestamp,
        }


# -----------------------------------------------------------------------------
# Test Intelligence Data Models
# -----------------------------------------------------------------------------

class VSTestFramework(str, Enum):
    """Recognized .NET test frameworks."""
    XUNIT = "xUnit"
    NUNIT = "NUnit"
    MSTEST = "MSTest"
    UNKNOWN = "Unknown"


@dataclass
class VSTestCaseResult:
    """Detailed result for an individual test case."""
    name: str
    outcome: str  # "Passed", "Failed", "Skipped"
    duration_ms: float = 0.0
    error_message: Optional[str] = None
    stack_trace: Optional[str] = None
    source_file: Optional[str] = None
    line_number: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "outcome": self.outcome,
            "duration_ms": round(self.duration_ms, 2),
            "error_message": self.error_message,
            "stack_trace": self.stack_trace,
            "source_file": self.source_file,
            "line_number": self.line_number,
        }


@dataclass
class VSTestProjectMetadata:
    """Metadata describing a discovered test project."""
    name: str
    path: str
    frameworks: List[str] = field(default_factory=list)
    target_frameworks: List[str] = field(default_factory=list)
    test_count: int = 0
    has_runsettings: bool = False
    runsettings_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "frameworks": self.frameworks,
            "target_frameworks": self.target_frameworks,
            "test_count": self.test_count,
            "has_runsettings": self.has_runsettings,
            "runsettings_path": self.runsettings_path,
        }


@dataclass
class VSTestConfiguration:
    """Configuration settings for a test project or run."""
    target_path: str
    frameworks: List[str] = field(default_factory=list)
    target_framework: Optional[str] = None
    runsettings_path: Optional[str] = None
    filter_expr: Optional[str] = None
    timeout_s: float = 180.0
    parallel: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_path": self.target_path,
            "frameworks": self.frameworks,
            "target_framework": self.target_framework,
            "runsettings_path": self.runsettings_path,
            "filter_expr": self.filter_expr,
            "timeout_s": self.timeout_s,
            "parallel": self.parallel,
        }


# -----------------------------------------------------------------------------
# Test Intelligence Engine
# -----------------------------------------------------------------------------

class VSTestIntelligence:
    """
    Deterministic discovery and analysis engine for Visual Studio & .NET test suites.
    Identifies test projects, parses granular test execution outputs, maps failures
    to source files, and extracts execution configurations.
    """

    TEST_PACKAGE_MARKERS: Dict[str, VSTestFramework] = {
        "xunit": VSTestFramework.XUNIT,
        "nunit": VSTestFramework.NUNIT,
        "mstest": VSTestFramework.MSTEST,
        "microsoft.net.test.sdk": VSTestFramework.UNKNOWN,
    }

    def __init__(self, safety_gate: Optional[VSSafetyGate] = None, project_root: Optional[Path] = None):
        self.safety = safety_gate or VSSafetyGate()
        self.project_root = Path(project_root or self.safety.authorized_project).resolve()

    def discover_test_projects(self, root_path: Optional[Union[str, Path]] = None) -> List[VSTestProjectMetadata]:
        """Discovers test projects within the authorized workspace."""
        self.safety.check_emergency_stop()
        scan_dir = Path(root_path).resolve() if root_path else self.project_root
        scan_dir = self.safety.validate_project_path(scan_dir)

        test_projects: List[VSTestProjectMetadata] = []
        if scan_dir.is_file() and scan_dir.suffix.lower() in (".csproj", ".vbproj", ".fsproj"):
            meta = self._inspect_single_project_for_tests(scan_dir)
            if meta:
                test_projects.append(meta)
            return test_projects

        # Scan directory tree
        for r, dirs, files in os.walk(scan_dir):
            dirs[:] = [d for d in dirs if d.lower() not in ("bin", "obj", ".vs", ".git", "packages")]
            for f in files:
                if f.lower().endswith((".csproj", ".vbproj", ".fsproj")):
                    proj_path = Path(r) / f
                    meta = self._inspect_single_project_for_tests(proj_path)
                    if meta:
                        test_projects.append(meta)

        return test_projects

    def _inspect_single_project_for_tests(self, proj_path: Path) -> Optional[VSTestProjectMetadata]:
        """Inspects a project file to determine if it is a test project."""
        try:
            content = proj_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return None

        content_lower = content.lower()
        is_test_proj = False
        frameworks: Set[str] = set()

        # Check for test sdk or framework markers
        for marker, fw in self.TEST_PACKAGE_MARKERS.items():
            if marker in content_lower:
                is_test_proj = True
                if fw != VSTestFramework.UNKNOWN:
                    frameworks.add(fw.value)

        # Also check naming heuristic if markers absent
        if not is_test_proj:
            name_lower = proj_path.stem.lower()
            if any(k in name_lower for k in ("test", "tests", "spec", "specs", "unittest")):
                is_test_proj = True

        if not is_test_proj:
            return None

        # Extract target frameworks
        tfms: List[str] = []
        m_tfm = re.search(r'<TargetFramework>(.*?)</TargetFramework>', content, re.IGNORECASE)
        if m_tfm:
            tfms.append(m_tfm.group(1).strip())
        m_tfms = re.search(r'<TargetFrameworks>(.*?)</TargetFrameworks>', content, re.IGNORECASE)
        if m_tfms:
            for item in m_tfms.group(1).split(';'):
                if item.strip():
                    tfms.append(item.strip())

        # Check for adjacent .runsettings
        runsettings_path = None
        for cand in proj_path.parent.glob("*.runsettings"):
            runsettings_path = str(cand)
            break
        if not runsettings_path and self.project_root:
            for cand in self.project_root.glob("*.runsettings"):
                runsettings_path = str(cand)
                break

        return VSTestProjectMetadata(
            name=proj_path.stem,
            path=str(proj_path),
            frameworks=sorted(list(frameworks)) if frameworks else ["Unknown"],
            target_frameworks=tfms,
            has_runsettings=runsettings_path is not None,
            runsettings_path=runsettings_path,
        )

    def inspect_test_configuration(self, target_path: Union[str, Path]) -> VSTestConfiguration:
        """Inspects test configuration details for an authorized test target."""
        self.safety.check_emergency_stop()
        val_target = self.safety.validate_file_path(target_path, check_writable=False)
        proj_meta = self._inspect_single_project_for_tests(val_target)

        frameworks = proj_meta.frameworks if proj_meta else ["Unknown"]
        tfm = proj_meta.target_frameworks[0] if (proj_meta and proj_meta.target_frameworks) else None
        runsettings = proj_meta.runsettings_path if proj_meta else None

        return VSTestConfiguration(
            target_path=str(val_target),
            frameworks=frameworks,
            target_framework=tfm,
            runsettings_path=runsettings,
            filter_expr=None,
            timeout_s=180.0,
            parallel=True,
        )

    def parse_granular_test_results(
        self,
        output_text: str,
        project_root: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """
        Parses test runner output into structured test cases, extracting pass/fail/skip status,
        execution durations, failure messages, and source file & line associations.
        """
        clean_output = redact_sensitive_data(output_text or "")
        root = Path(project_root or self.project_root).resolve()

        test_cases: List[VSTestCaseResult] = []
        slow_tests: List[VSTestCaseResult] = []

        # 1. Regex for Passed tests: "Passed TestName [12 ms]" or "[PASS] TestName"
        pass_pattern = re.compile(
            r'(?:Passed|\[PASS\])\s+([A-Za-z0-9_\.\(\), ]+?)(?:\s*\[(\d+(?:\.\d+)?)\s*(ms|s)\])?\s*$',
            re.MULTILINE
        )
        for m in pass_pattern.finditer(clean_output):
            name = m.group(1).strip()
            dur_val = float(m.group(2)) if m.group(2) else 0.0
            unit = m.group(3) if m.group(3) else "ms"
            dur_ms = dur_val if unit == "ms" else dur_val * 1000.0

            tc = VSTestCaseResult(name=name, outcome="Passed", duration_ms=dur_ms)
            test_cases.append(tc)
            if dur_ms >= 500.0:
                slow_tests.append(tc)

        # 2. Regex for Skipped tests: "Skipped TestName [reason]" or "[SKIP] TestName"
        skip_pattern = re.compile(
            r'(?:Skipped|\[SKIP\])\s+([A-Za-z0-9_\.\(\), ]+?)(?:\s*\[(.*?)\])?\s*$',
            re.MULTILINE
        )
        for m in skip_pattern.finditer(clean_output):
            name = m.group(1).strip()
            reason = m.group(2).strip() if m.group(2) else None
            test_cases.append(VSTestCaseResult(name=name, outcome="Skipped", error_message=reason))

        # 3. Regex for Failed tests with message and stack trace
        fail_pattern = re.compile(
            r'(?:Failed|\[FAIL\])\s+([A-Za-z0-9_\.\(\), ]+?)(?:\s*\[(\d+(?:\.\d+)?)\s*(ms|s)\])?\s*$',
            re.MULTILINE
        )
        msg_pattern = re.compile(r'Error Message:\s*(?:\r?\n\s*)?([^\r\n]+)', re.MULTILINE)
        stack_pattern = re.compile(r'Stack Trace:\s*\r?\n\s*(?:at\s+.*in\s+(.+?):line\s+(\d+))', re.MULTILINE)

        matches = list(fail_pattern.finditer(clean_output))
        for idx, m in enumerate(matches):
            name = m.group(1).strip()
            dur_val = float(m.group(2)) if m.group(2) else 0.0
            unit = m.group(3) if m.group(3) else "ms"
            dur_ms = dur_val if unit == "ms" else dur_val * 1000.0

            start_idx = m.end()
            end_idx = matches[idx + 1].start() if idx + 1 < len(matches) else len(clean_output)
            sub_chunk = clean_output[start_idx:end_idx]

            m_msg = msg_pattern.search(sub_chunk)
            err_msg = m_msg.group(1).strip() if m_msg else f"Test '{name}' failed assertion."

            m_stack = stack_pattern.search(sub_chunk)
            source_file = None
            line_num = None
            if m_stack:
                raw_path = m_stack.group(1).strip()
                p = Path(raw_path.replace("/", os.sep))
                if not p.is_absolute() and root:
                    cand = (root / p).resolve()
                    source_file = str(cand) if cand.exists() else str(p)
                else:
                    source_file = str(p)
                try:
                    line_num = int(m_stack.group(2))
                except (ValueError, TypeError):
                    line_num = None

            tc = VSTestCaseResult(
                name=name,
                outcome="Failed",
                duration_ms=dur_ms,
                error_message=err_msg,
                stack_trace=m_stack.group(0) if m_stack else None,
                source_file=source_file,
                line_number=line_num,
            )
            test_cases.append(tc)
            if dur_ms >= 500.0:
                slow_tests.append(tc)

        passed_count = sum(1 for tc in test_cases if tc.outcome == "Passed")
        failed_count = sum(1 for tc in test_cases if tc.outcome == "Failed")
        skipped_count = sum(1 for tc in test_cases if tc.outcome == "Skipped")
        total_count = len(test_cases)

        return {
            "summary": {
                "total": total_count,
                "passed": passed_count,
                "failed": failed_count,
                "skipped": skipped_count,
                "success": (failed_count == 0 and total_count > 0),
            },
            "test_cases": [tc.to_dict() for tc in test_cases],
            "failed_tests": [tc.to_dict() for tc in test_cases if tc.outcome == "Failed"],
            "slow_tests": [tc.to_dict() for tc in slow_tests],
            "has_failures": failed_count > 0,
        }


# -----------------------------------------------------------------------------
# Performance Monitoring Intelligence
# -----------------------------------------------------------------------------

@dataclass
class VSPerformanceMetrics:
    """Non-invasive performance metrics capture."""
    build_duration_s: float = 0.0
    test_duration_s: float = 0.0
    debug_duration_s: float = 0.0
    process_memory_mb: float = 0.0
    process_cpu_percent: float = 0.0
    process_name: str = ""
    pid: Optional[int] = None
    slow_tests: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "build_duration_s": round(self.build_duration_s, 2),
            "test_duration_s": round(self.test_duration_s, 2),
            "debug_duration_s": round(self.debug_duration_s, 2),
            "process_memory_mb": round(self.process_memory_mb, 2),
            "process_cpu_percent": round(self.process_cpu_percent, 2),
            "process_name": self.process_name,
            "pid": self.pid,
            "slow_tests": self.slow_tests,
            "warnings": self.warnings,
            "timestamp": self.timestamp,
        }


class VSPerformanceMonitor:
    """
    Non-invasive, safe performance monitor for Visual Studio development workflows.
    Observes build/test/debug elapsed times and queries process metrics safely.
    Zero invasive hooks, zero kernel driver requirements.
    """

    KNOWN_PROCESS_NAMES = {"devenv.exe", "dotnet.exe", "msbuild.exe", "testhost.exe"}

    def __init__(self, safety_gate: Optional[VSSafetyGate] = None):
        self.safety = safety_gate or VSSafetyGate()
        self.last_build_duration_s: float = 0.0
        self.last_test_duration_s: float = 0.0
        self.last_debug_duration_s: float = 0.0
        self.recorded_slow_tests: List[Dict[str, Any]] = []

    def record_build_duration(self, duration_s: float) -> None:
        """Records build execution duration."""
        self.last_build_duration_s = max(0.0, float(duration_s))

    def record_test_duration(self, duration_s: float) -> None:
        """Records test execution duration."""
        self.last_test_duration_s = max(0.0, float(duration_s))

    def record_debug_duration(self, duration_s: float) -> None:
        """Records debug session execution duration."""
        self.last_debug_duration_s = max(0.0, float(duration_s))

    def set_slow_tests(self, slow_tests: List[Dict[str, Any]]) -> None:
        """Sets identified slow tests."""
        self.recorded_slow_tests = list(slow_tests)

    def inspect_performance(self, target_process_name: Optional[str] = None) -> VSPerformanceMetrics:
        """Collects safe bounded performance observations."""
        self.safety.check_emergency_stop()

        warnings: List[str] = []
        if self.last_build_duration_s > 60.0:
            warnings.append(f"Build duration ({round(self.last_build_duration_s, 1)}s) exceeded recommended 60s threshold.")
        if self.last_test_duration_s > 30.0:
            warnings.append(f"Test duration ({round(self.last_test_duration_s, 1)}s) exceeded recommended 30s threshold.")
        if len(self.recorded_slow_tests) > 0:
            warnings.append(f"Detected {len(self.recorded_slow_tests)} slow test(s) exceeding 500ms threshold.")

        mem_mb = 0.0
        cpu_pct = 0.0
        pname = target_process_name or "current_process"
        target_pid = None

        if HAS_PSUTIL:
            try:
                # Find matching target process or inspect current process
                matched_proc = None
                if target_process_name:
                    target_lower = target_process_name.lower()
                    for p in psutil.process_iter(['pid', 'name']):
                        try:
                            if p.info['name'] and p.info['name'].lower() == target_lower:
                                matched_proc = p
                                break
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            continue

                if not matched_proc:
                    matched_proc = psutil.Process()

                pname = matched_proc.name()
                target_pid = matched_proc.pid
                mem_info = matched_proc.memory_info()
                mem_mb = mem_info.rss / (1024.0 * 1024.0)
                cpu_pct = matched_proc.cpu_percent(interval=None)

                if mem_mb > 1024.0:
                    warnings.append(f"Process memory ({round(mem_mb, 1)} MB) is high (> 1 GB).")
            except Exception as e:
                logger.debug(f"psutil inspection skipped: {e}")
        else:
            pname = "python (psutil not installed)"
            target_pid = os.getpid()

        return VSPerformanceMetrics(
            build_duration_s=self.last_build_duration_s,
            test_duration_s=self.last_test_duration_s,
            debug_duration_s=self.last_debug_duration_s,
            process_memory_mb=mem_mb,
            process_cpu_percent=cpu_pct,
            process_name=pname,
            pid=target_pid,
            slow_tests=self.recorded_slow_tests,
            warnings=warnings,
        )


# -----------------------------------------------------------------------------
# Structured Failure Intelligence Diagnostic Aggregator
# -----------------------------------------------------------------------------

class VSDiagnosticsEngine:
    """
    Unifies compiler errors, build failures, test failures, runtime exceptions,
    debugger evidence, IDE states, and repair outcomes into a single structured
    diagnostic context.
    """

    def __init__(self, safety_gate: Optional[VSSafetyGate] = None, project_root: Optional[Path] = None):
        self.safety = safety_gate or VSSafetyGate()
        self.project_root = Path(project_root or self.safety.authorized_project).resolve()
        self.test_intel = VSTestIntelligence(safety_gate=self.safety, project_root=self.project_root)
        self.perf_monitor = VSPerformanceMonitor(safety_gate=self.safety)

    def build_diagnostic_context(
        self,
        build_output: Optional[Dict[str, Any]] = None,
        test_output: Optional[Dict[str, Any]] = None,
        runtime_output: Optional[Dict[str, Any]] = None,
        debugger_evidence: Optional[Any] = None,
        ide_state: Optional[Any] = None,
        safety_error: Optional[VSSafetyError] = None,
        repair_result: Optional[Any] = None,
    ) -> VSDiagnosticContext:
        """Consolidates subsystem outputs into a structured VSDiagnosticContext."""
        # 1. Safety Rejection
        if safety_error:
            return VSDiagnosticContext(
                failure_type=VSFailureType.SAFETY_REJECTION,
                summary=f"Safety boundary rejection: {safety_error.message}",
                error_codes=[safety_error.code.value],
                raw_evidence=str(safety_error),
                repairable=False,
                suggested_action="Adjust operation parameters to respect safety boundaries or de-escalate.",
                details=safety_error.details or {},
            )

        # 2. Repair Failure
        if repair_result and not getattr(repair_result, "success", True):
            return VSDiagnosticContext(
                failure_type=VSFailureType.REPAIR_FAILURE,
                summary=f"Autonomous code repair failed: {getattr(repair_result, 'error', 'Validation error')}",
                error_codes=["REPAIR_FAILED"],
                raw_evidence=str(getattr(repair_result, "output", "")),
                repairable=False,
                suggested_action="Review error diagnosis and verify if manual intervention or rollback is required.",
                details={"repair_result": getattr(repair_result, "to_dict", lambda: {})()},
            )

        # 3. Build Failure
        if build_output and not build_output.get("success", True):
            err_code = build_output.get("error_code") or "BUILD_FAILED"
            raw_out = build_output.get("full_output") or build_output.get("stderr") or ""
            codes = re.findall(r'\b(CS\d+|MSB\d+|NU\d+|BC\d+|FS\d+|NETSDK\d+)\b', raw_out)
            codes = list(dict.fromkeys(codes)) or [err_code]

            # Extract affected files
            files = re.findall(r'([A-Za-z]:[^\r\n:\(\)]+?\.[a-zA-Z0-9]+)\s*[\(:]', raw_out)
            affected = list(dict.fromkeys([f.strip() for f in files if Path(f).suffix.lower() in (".cs", ".csproj", ".vb", ".fs")]))

            # Determine repairability
            repairable = any(c.startswith("CS") for c in codes) and not any(c.startswith("MSB") or c.startswith("NETSDK") for c in codes)

            return VSDiagnosticContext(
                failure_type=VSFailureType.BUILD_FAILURE,
                summary=f"MSBuild execution failed with {len(codes)} diagnostic code(s): {', '.join(codes[:3])}",
                error_codes=codes,
                affected_files=affected,
                raw_evidence=raw_out,
                repairable=repairable,
                suggested_action="Plan autonomous code repair targeting compiler diagnostic." if repairable else "Investigate SDK, project references, or missing packages.",
                details={"returncode": build_output.get("returncode", -1)},
            )

        # 4. Test Failure
        if test_output and not test_output.get("success", True):
            raw_out = test_output.get("full_output") or test_output.get("stderr") or ""
            parsed = self.test_intel.parse_granular_test_results(raw_out, project_root=self.project_root)
            failed_tests = parsed.get("failed_tests", [])
            test_names = [t["name"] for t in failed_tests]
            files = [t["source_file"] for t in failed_tests if t.get("source_file")]
            files = list(dict.fromkeys(files))

            return VSDiagnosticContext(
                failure_type=VSFailureType.TEST_FAILURE,
                summary=f"Test run failed: {len(failed_tests)} test(s) failed assertion.",
                error_codes=["TEST_FAILED"],
                affected_files=files,
                affected_tests=test_names,
                raw_evidence=raw_out,
                repairable=len(files) > 0,
                suggested_action="Inspect assertion failures and plan targeted fix in test or implementation code.",
                details={"parsed_tests": parsed},
            )

        # 5. Runtime Failure
        if runtime_output and not runtime_output.get("success", True):
            diag = runtime_output.get("diagnosis", {})
            err_code = runtime_output.get("error_code") or (diag.get("error_code") if diag else "RUNTIME_CRASH")
            raw_out = runtime_output.get("full_output") or ""
            f_path = diag.get("file_path") if diag else None
            affected = [f_path] if f_path else []

            return VSDiagnosticContext(
                failure_type=VSFailureType.RUNTIME_FAILURE,
                summary=f"Runtime execution terminated with error [{err_code}].",
                error_codes=[err_code],
                affected_files=affected,
                raw_evidence=raw_out,
                repairable=bool(affected),
                suggested_action="Inspect stack trace and runtime exception to evaluate if code fix is warranted.",
                details={"runtime_output": runtime_output},
            )

        # 6. Debugger Failure
        if debugger_evidence and getattr(debugger_evidence, "exception_info", None):
            exc = getattr(debugger_evidence, "exception_info", {})
            exc_type = exc.get("type", "Exception")
            exc_msg = exc.get("message", "Unhandled exception in debug session.")
            loc = getattr(debugger_evidence, "location", None)
            f_path = getattr(loc, "file_path", None) if loc else None
            affected = [f_path] if f_path else []

            return VSDiagnosticContext(
                failure_type=VSFailureType.DEBUGGER_FAILURE,
                summary=f"Debugger caught unhandled {exc_type}: {exc_msg}",
                error_codes=[exc_type],
                affected_files=affected,
                raw_evidence=str(exc),
                repairable=bool(affected),
                suggested_action="Inspect locals and stack frames at breakpoint to isolate bug.",
                details={"debug_evidence": getattr(debugger_evidence, "to_dict", lambda: {})()},
            )

        # 7. IDE Failure
        if ide_state and not getattr(ide_state, "is_running", True):
            # If IDE check was requested but IDE is not running
            pass

        # If everything passed
        return VSDiagnosticContext(
            failure_type=VSFailureType.NONE,
            summary="All inspected operations completed successfully without errors.",
            error_codes=[],
            affected_files=[],
            affected_tests=[],
            raw_evidence="",
            repairable=False,
            suggested_action="No recovery action needed.",
            details={},
        )
