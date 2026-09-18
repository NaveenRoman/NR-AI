"""
NR-AI SkyShield: Deterministic Device Health & Anomaly Analyzer.
Step 10 Phase 3 — Device Health & Anomaly Detection.

Provides:
- Deterministic threshold evaluation (CPU, Memory, Storage, Battery, Network, Uptime, Permissions, Clock)
- Bounded device baseline tracking with rolling statistical windows
- Anomaly detection with concrete empirical evidence and confidence scores
- Threat categorization (DEVICE_HEALTH, AUTHENTICATION, NETWORK, PERMISSION, etc.)
- Transparent, explainable Security Posture calculation (0 to 100)
- Deterministic mock scenario generators explicitly labeled DataVerificationState.MOCK
- AI Advisory explanation generator routed through strict security invariants
"""

from datetime import datetime, timezone
import hashlib
import logging
import secrets
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from app.security.health_models import (
    AnomalyEvent,
    AnomalySeverity,
    DeviceBaseline,
    DeviceHealthSnapshot,
    DeviceHealthState,
    SafeResponseAction,
    SecurityPostureScore,
    TelemetryProcessingState,
    ThreatCategory,
    ThreatConclusion,
)
from app.security.models import DataVerificationState, redact_sensitive_data

logger = logging.getLogger("NRAI.SkyShield.HealthAnalyzer")


# ==============================================================================
# Deterministic Thresholds (Documented & Configurable)
# ==============================================================================

CPU_WARNING_THRESHOLD = 80.0             # CPU > 80% triggers WARNING
CPU_CRITICAL_THRESHOLD = 95.0            # CPU > 95% triggers CRITICAL
MEMORY_WARNING_THRESHOLD = 85.0          # Memory > 85% triggers WARNING
MEMORY_CRITICAL_THRESHOLD = 95.0         # Memory > 95% triggers CRITICAL
STORAGE_WARNING_THRESHOLD = 85.0         # Storage > 85% triggers WARNING
STORAGE_CRITICAL_THRESHOLD = 95.0        # Storage > 95% triggers CRITICAL
BATTERY_LOW_WARNING_THRESHOLD = 15.0     # Battery <= 15% triggers WARNING while discharging
BATTERY_CRITICAL_THRESHOLD = 5.0         # Battery <= 5% triggers CRITICAL
AUTH_FAILURE_BURST_THRESHOLD = 3         # >= 3 auth failures within 5m triggers AUTH_FAILURE_BURST
PAIRING_FAILURE_BURST_THRESHOLD = 3      # >= 3 pairing failures triggers PAIRING_FAILURE_BURST
CLOCK_SKEW_TOLERANCE_SECONDS = 120.0     # > 120s drift triggers clock anomaly
RAPID_PERMISSION_THRESHOLD = 2           # > 2 new high-risk permissions triggers PERMISSION_CHANGE


class DeviceHealthAnalyzer:
    """
    Master analyzer for deterministic device health inspection,
    anomaly detection, threat classification, and explainable posture scoring.
    """

    def __init__(self):
        self._baselines: Dict[str, DeviceBaseline] = {}
        self._lock = threading.RLock()

    # --------------------------------------------------------------------------
    # Baseline Management
    # --------------------------------------------------------------------------

    def get_or_create_baseline(self, device_id: str) -> DeviceBaseline:
        """Retrieves or creates a bounded device baseline."""
        with self._lock:
            if device_id not in self._baselines:
                self._baselines[device_id] = DeviceBaseline(device_id=device_id)
            return self._baselines[device_id]

    def update_baseline(self, device_id: str, snapshot: DeviceHealthSnapshot) -> DeviceBaseline:
        """Updates baseline with new snapshot observations."""
        with self._lock:
            baseline = self.get_or_create_baseline(device_id)
            baseline.record_snapshot(snapshot)
            return baseline

    def reset_baseline(self, device_id: str) -> DeviceBaseline:
        """Resets baseline history for specified device."""
        with self._lock:
            baseline = self.get_or_create_baseline(device_id)
            baseline.reset()
            return baseline

    # --------------------------------------------------------------------------
    # Deterministic Health & Anomaly Analysis
    # --------------------------------------------------------------------------

    def analyze_health(
        self,
        snapshot: DeviceHealthSnapshot,
        baseline: Optional[DeviceBaseline] = None,
        auth_failure_count: int = 0,
        pairing_failure_count: int = 0,
    ) -> Tuple[DeviceHealthState, List[AnomalyEvent], SecurityPostureScore, List[str]]:
        """
        Deterministically evaluates device health snapshot.
        Returns:
            (DeviceHealthState, List[AnomalyEvent], SecurityPostureScore, List[SafeResponseAction])
        """
        dev_id = snapshot.device_id
        bl = baseline or self.get_or_create_baseline(dev_id)
        now = time.time()
        anomalies: List[AnomalyEvent] = []
        safe_recommendations: List[str] = []

        # 0. Data Source Check
        if snapshot.data_source == DataVerificationState.UNAVAILABLE:
            posture = SecurityPostureScore(
                device_id=dev_id,
                score=0,
                rating="CRITICAL",
                contributing_factors=[{
                    "factor": "Telemetry Data Unavailable",
                    "deduction": -100,
                    "evidence": "Device telemetry source reported UNAVAILABLE.",
                }],
            )
            return DeviceHealthState.UNAVAILABLE, [], posture, [SafeResponseAction.RECONNECT_DEVICE.value]

        # 1. CPU Usage Analysis
        cpu = float(snapshot.cpu_usage)
        if cpu >= CPU_CRITICAL_THRESHOLD:
            anomalies.append(AnomalyEvent(
                anomaly_id=f"anom_cpu_{secrets.token_hex(6)}",
                device_id=dev_id,
                category=ThreatCategory.RESOURCE_EXHAUSTION,
                severity=AnomalySeverity.CRITICAL,
                observed_value=f"{cpu:.1f}%",
                expected_range=f"<{CPU_WARNING_THRESHOLD:.1f}%",
                detected_at=now,
                evidence=f"Critical CPU saturation observed at {cpu:.1f}%. Normal range is 10-35%.",
                confidence=0.98,
                recommendation="Investigate runaway background processes or thermal throttling.",
            ))
        elif cpu >= CPU_WARNING_THRESHOLD:
            anomalies.append(AnomalyEvent(
                anomaly_id=f"anom_cpu_{secrets.token_hex(6)}",
                device_id=dev_id,
                category=ThreatCategory.RESOURCE_EXHAUSTION,
                severity=AnomalySeverity.HIGH,
                observed_value=f"{cpu:.1f}%",
                expected_range=f"<{CPU_WARNING_THRESHOLD:.1f}%",
                detected_at=now,
                evidence=f"Elevated CPU utilization at {cpu:.1f}%. Exceeds warning threshold.",
                confidence=0.95,
                recommendation="Monitor application workload and background execution.",
            ))
        elif bl.cpu_history and cpu > (bl.cpu_mean + 3 * bl.cpu_std) and cpu > 60.0:
            anomalies.append(AnomalyEvent(
                anomaly_id=f"anom_cpu_{secrets.token_hex(6)}",
                device_id=dev_id,
                category=ThreatCategory.DEVICE_HEALTH,
                severity=AnomalySeverity.MEDIUM,
                observed_value=f"{cpu:.1f}%",
                expected_range=f"{bl.cpu_mean - 2*bl.cpu_std:.1f}% - {bl.cpu_mean + 2*bl.cpu_std:.1f}%",
                detected_at=now,
                evidence=f"Statistical CPU anomaly: {cpu:.1f}% exceeds 3-sigma baseline ({bl.cpu_mean:.1f}% ± {bl.cpu_std:.1f}%).",
                confidence=0.88,
                recommendation="Check for sudden computational spikes against baseline.",
            ))

        # 2. Memory Usage Analysis
        mem_pct = float(snapshot.memory_usage.get("percentage", 0.0))
        if mem_pct >= MEMORY_CRITICAL_THRESHOLD:
            anomalies.append(AnomalyEvent(
                anomaly_id=f"anom_mem_{secrets.token_hex(6)}",
                device_id=dev_id,
                category=ThreatCategory.RESOURCE_EXHAUSTION,
                severity=AnomalySeverity.CRITICAL,
                observed_value=f"{mem_pct:.1f}%",
                expected_range=f"<{MEMORY_WARNING_THRESHOLD:.1f}%",
                detected_at=now,
                evidence=f"Critical memory pressure at {mem_pct:.1f}%. Device near Out-Of-Memory (OOM) kill state.",
                confidence=0.99,
                recommendation="Restart memory-heavy background tasks or free cached app memory.",
            ))
        elif mem_pct >= MEMORY_WARNING_THRESHOLD:
            anomalies.append(AnomalyEvent(
                anomaly_id=f"anom_mem_{secrets.token_hex(6)}",
                device_id=dev_id,
                category=ThreatCategory.RESOURCE_EXHAUSTION,
                severity=AnomalySeverity.HIGH,
                observed_value=f"{mem_pct:.1f}%",
                expected_range=f"<{MEMORY_WARNING_THRESHOLD:.1f}%",
                detected_at=now,
                evidence=f"High memory utilization at {mem_pct:.1f}%. Exceeds standard operating bounds.",
                confidence=0.92,
                recommendation="Audit active background processes for potential memory leaks.",
            ))

        # 3. Storage Usage Analysis
        stor_pct = float(snapshot.storage_usage.get("percentage", 0.0))
        if stor_pct >= STORAGE_CRITICAL_THRESHOLD:
            anomalies.append(AnomalyEvent(
                anomaly_id=f"anom_stor_{secrets.token_hex(6)}",
                device_id=dev_id,
                category=ThreatCategory.RESOURCE_EXHAUSTION,
                severity=AnomalySeverity.CRITICAL,
                observed_value=f"{stor_pct:.1f}%",
                expected_range=f"<{STORAGE_WARNING_THRESHOLD:.1f}%",
                detected_at=now,
                evidence=f"Storage near complete exhaustion at {stor_pct:.1f}%. Critical system writes may fail.",
                confidence=0.99,
                recommendation="Clean temporary caches, unneeded downloads, or large media files immediately.",
            ))
        elif stor_pct >= STORAGE_WARNING_THRESHOLD:
            anomalies.append(AnomalyEvent(
                anomaly_id=f"anom_stor_{secrets.token_hex(6)}",
                device_id=dev_id,
                category=ThreatCategory.RESOURCE_EXHAUSTION,
                severity=AnomalySeverity.HIGH,
                observed_value=f"{stor_pct:.1f}%",
                expected_range=f"<{STORAGE_WARNING_THRESHOLD:.1f}%",
                detected_at=now,
                evidence=f"Storage capacity warning: {stor_pct:.1f}% consumed. Exceeds warning threshold.",
                confidence=0.95,
                recommendation="Review storage allocations and remove old app data.",
            ))

        # 4. Battery Anomaly Analysis
        batt = float(snapshot.battery_level)
        charging = str(snapshot.charging_state).upper()
        if batt <= BATTERY_CRITICAL_THRESHOLD and charging != "CHARGING":
            anomalies.append(AnomalyEvent(
                anomaly_id=f"anom_batt_{secrets.token_hex(6)}",
                device_id=dev_id,
                category=ThreatCategory.DEVICE_HEALTH,
                severity=AnomalySeverity.CRITICAL,
                observed_value=f"{batt:.1f}%",
                expected_range=">15.0%",
                detected_at=now,
                evidence=f"Battery critically depleted at {batt:.1f}% while discharging. Device shutdown imminent.",
                confidence=1.0,
                recommendation="Connect device to power source immediately.",
            ))
        elif batt <= BATTERY_LOW_WARNING_THRESHOLD and charging != "CHARGING":
            anomalies.append(AnomalyEvent(
                anomaly_id=f"anom_batt_{secrets.token_hex(6)}",
                device_id=dev_id,
                category=ThreatCategory.DEVICE_HEALTH,
                severity=AnomalySeverity.MEDIUM,
                observed_value=f"{batt:.1f}%",
                expected_range=">15.0%",
                detected_at=now,
                evidence=f"Low battery warning: {batt:.1f}% remaining while discharging.",
                confidence=0.95,
                recommendation="Enable battery saver mode and prepare to charge device.",
            ))

        # 5. Network Anomaly Analysis
        net_state = str(snapshot.network_state).upper()
        if net_state == "FLAPPING":
            anomalies.append(AnomalyEvent(
                anomaly_id=f"anom_net_{secrets.token_hex(6)}",
                device_id=dev_id,
                category=ThreatCategory.NETWORK,
                severity=AnomalySeverity.HIGH,
                observed_value="FLAPPING",
                expected_range="CONNECTED",
                detected_at=now,
                evidence="Network flapping detected: repeated disconnections and reconnects within brief interval.",
                confidence=0.94,
                recommendation="Check Wi-Fi/cellular signal strength or inspect gateway stability.",
            ))
            safe_recommendations.append(SafeResponseAction.RECONNECT_DEVICE.value)
        elif net_state == "DISCONNECTED":
            anomalies.append(AnomalyEvent(
                anomaly_id=f"anom_net_{secrets.token_hex(6)}",
                device_id=dev_id,
                category=ThreatCategory.NETWORK,
                severity=AnomalySeverity.MEDIUM,
                observed_value="DISCONNECTED",
                expected_range="CONNECTED",
                detected_at=now,
                evidence="Device network interface is currently disconnected.",
                confidence=1.0,
                recommendation="Verify device airplane mode, Wi-Fi or mobile data connectivity.",
            ))
            safe_recommendations.append(SafeResponseAction.RECONNECT_DEVICE.value)

        # 6. Uptime & Unexpected Reboot
        uptime = float(snapshot.uptime)
        time_since_boot = now - float(snapshot.last_boot)
        if uptime < 120.0 and (now - snapshot.last_seen) < 300.0 and snapshot.last_seen > (snapshot.last_boot + 3600.0):
            anomalies.append(AnomalyEvent(
                anomaly_id=f"anom_reboot_{secrets.token_hex(6)}",
                device_id=dev_id,
                category=ThreatCategory.DEVICE_HEALTH,
                severity=AnomalySeverity.MEDIUM,
                observed_value=f"Uptime {uptime:.0f}s",
                expected_range="Stable uptime",
                detected_at=now,
                evidence="Unexpected reboot detected: device uptime reset while previously active without scheduled maintenance.",
                confidence=0.90,
                recommendation="Inspect device system crash logs and kernel panic reports.",
            ))

        # 7. Rapid Permission Changes
        perm_summary = snapshot.permission_summary or {}
        high_risk_count = int(perm_summary.get("high_risk_count", 0))
        high_risk_perms = perm_summary.get("high_risk_permissions", [])
        if high_risk_count >= RAPID_PERMISSION_THRESHOLD:
            anomalies.append(AnomalyEvent(
                anomaly_id=f"anom_perm_{secrets.token_hex(6)}",
                device_id=dev_id,
                category=ThreatCategory.PERMISSION,
                severity=AnomalySeverity.HIGH,
                observed_value=f"{high_risk_count} high-risk permissions",
                expected_range="<2 high-risk permissions",
                detected_at=now,
                evidence=f"High-risk permission expansion: {high_risk_count} critical permissions granted ({', '.join(high_risk_perms[:3])}).",
                confidence=0.96,
                recommendation="Review permission grants in device settings immediately.",
            ))
            safe_recommendations.append(SafeResponseAction.REVIEW_PERMISSIONS.value)

        # 8. Authentication & Pairing Failure Bursts
        auth_failures = auth_failure_count or len(bl.auth_failure_timestamps)
        if auth_failures >= AUTH_FAILURE_BURST_THRESHOLD:
            anomalies.append(AnomalyEvent(
                anomaly_id=f"anom_auth_{secrets.token_hex(6)}",
                device_id=dev_id,
                category=ThreatCategory.AUTHENTICATION,
                severity=AnomalySeverity.HIGH,
                observed_value=f"{auth_failures} failures",
                expected_range=f"<{AUTH_FAILURE_BURST_THRESHOLD}",
                detected_at=now,
                evidence=f"Authentication failure burst: {auth_failures} consecutive failed attempts detected in observation window.",
                confidence=0.98,
                recommendation="Trigger device re-authentication and verify authorized operator access.",
            ))
            safe_recommendations.append(SafeResponseAction.REAUTHENTICATE.value)

        if pairing_failure_count >= PAIRING_FAILURE_BURST_THRESHOLD:
            anomalies.append(AnomalyEvent(
                anomaly_id=f"anom_pair_{secrets.token_hex(6)}",
                device_id=dev_id,
                category=ThreatCategory.AUTHORIZATION,
                severity=AnomalySeverity.HIGH,
                observed_value=f"{pairing_failure_count} failed attempts",
                expected_range=f"<{PAIRING_FAILURE_BURST_THRESHOLD}",
                detected_at=now,
                evidence=f"Repeated pairing code failures: {pairing_failure_count} invalid code entries observed.",
                confidence=0.99,
                recommendation="Verify pairing initiator credentials and suspend rogue pairing attempts.",
            ))

        # 9. Clock Skew / Timestamp Drift
        skew = abs(now - float(snapshot.timestamp))
        if skew > CLOCK_SKEW_TOLERANCE_SECONDS:
            anomalies.append(AnomalyEvent(
                anomaly_id=f"anom_clock_{secrets.token_hex(6)}",
                device_id=dev_id,
                category=ThreatCategory.CONFIGURATION,
                severity=AnomalySeverity.MEDIUM,
                observed_value=f"{skew:.1f}s drift",
                expected_range=f"<{CLOCK_SKEW_TOLERANCE_SECONDS:.1f}s",
                detected_at=now,
                evidence=f"Clock skew detected: device timestamp differs from server by {skew:.1f}s, exceeding 120s tolerance.",
                confidence=0.95,
                recommendation="Synchronize device system clock with network time protocol (NTP).",
            ))

        # Update baseline with current observation
        bl.record_snapshot(snapshot)

        # 10. Determine Health State
        if any(a.severity == AnomalySeverity.CRITICAL for a in anomalies):
            health_state = DeviceHealthState.CRITICAL
        elif any(a.severity == AnomalySeverity.HIGH for a in anomalies):
            health_state = DeviceHealthState.WARNING
        elif any(a.severity == AnomalySeverity.MEDIUM for a in anomalies):
            health_state = DeviceHealthState.DEGRADED
        elif any(a.severity == AnomalySeverity.LOW for a in anomalies):
            health_state = DeviceHealthState.HEALTHY
        else:
            health_state = DeviceHealthState.HEALTHY

        # 11. Compute Explainable Security Posture Score
        posture_score = self.calculate_security_posture(
            device_id=dev_id,
            anomalies=anomalies,
            snapshot=snapshot,
            auth_failures=auth_failures,
        )

        return health_state, anomalies, posture_score, safe_recommendations

    # --------------------------------------------------------------------------
    # Explainable Security Posture Calculation
    # --------------------------------------------------------------------------

    def calculate_security_posture(
        self,
        device_id: str,
        anomalies: List[AnomalyEvent],
        snapshot: DeviceHealthSnapshot,
        auth_failures: int = 0,
    ) -> SecurityPostureScore:
        """
        Calculates a deterministic, explainable security posture score (0 - 100).
        Base score is 100; every deduction is documented with mathematical rationale.
        """
        base_score = 100
        factors: List[Dict[str, Any]] = []

        # Deductions for active anomalies
        critical_count = sum(1 for a in anomalies if a.severity == AnomalySeverity.CRITICAL)
        if critical_count > 0:
            deduction = min(50, critical_count * 25)
            base_score -= deduction
            factors.append({
                "factor": "Critical Security/Health Anomalies",
                "deduction": -deduction,
                "evidence": f"{critical_count} critical anomalies detected (e.g. {anomalies[0].evidence[:60]}...).",
            })

        high_count = sum(1 for a in anomalies if a.severity == AnomalySeverity.HIGH)
        if high_count > 0:
            deduction = min(30, high_count * 12)
            base_score -= deduction
            factors.append({
                "factor": "High-Severity Anomalies",
                "deduction": -deduction,
                "evidence": f"{high_count} high-severity anomalies detected.",
            })

        medium_count = sum(1 for a in anomalies if a.severity == AnomalySeverity.MEDIUM)
        if medium_count > 0:
            deduction = min(20, medium_count * 6)
            base_score -= deduction
            factors.append({
                "factor": "Medium Anomalies / Deviations",
                "deduction": -deduction,
                "evidence": f"{medium_count} medium anomalies or metric deviations.",
            })

        # High-risk permissions deduction
        perm_summary = snapshot.permission_summary or {}
        high_risk_count = int(perm_summary.get("high_risk_count", 0))
        if high_risk_count > 0:
            deduction = min(20, high_risk_count * 5)
            base_score -= deduction
            factors.append({
                "factor": "High-Risk Permissions Active",
                "deduction": -deduction,
                "evidence": f"{high_risk_count} sensitive OS permissions granted on device.",
            })

        # Auth failures deduction
        if auth_failures >= AUTH_FAILURE_BURST_THRESHOLD:
            deduction = 15
            base_score -= deduction
            factors.append({
                "factor": "Authentication Failures Burst",
                "deduction": -deduction,
                "evidence": f"{auth_failures} authentication attempts failed in window.",
            })

        # OS Configuration deductions (e.g. metadata)
        meta = snapshot.metadata or {}
        if meta.get("rooted") or meta.get("bootloader_unlocked"):
            deduction = 25
            base_score -= deduction
            factors.append({
                "factor": "Compromised OS Integrity (Root/Unlocked)",
                "deduction": -deduction,
                "evidence": "Hardware reports rooted status or unlocked bootloader.",
            })

        # Final score clamping
        final_score = max(0, min(100, base_score))

        # Rating determination
        if final_score >= 90:
            rating = "EXCELLENT"
        elif final_score >= 75:
            rating = "GOOD"
        elif final_score >= 60:
            rating = "FAIR"
        elif final_score >= 40:
            rating = "DEGRADED"
        else:
            rating = "CRITICAL"

        if not factors:
            factors.append({
                "factor": "Optimal Operational Baseline",
                "deduction": 0,
                "evidence": "All hardware metrics and security boundaries within normal parameters.",
            })

        return SecurityPostureScore(
            device_id=device_id,
            score=final_score,
            rating=rating,
            contributing_factors=factors,
            calculated_at=time.time(),
        )

    # --------------------------------------------------------------------------
    # Deterministic Mock Scenarios
    # --------------------------------------------------------------------------

    def generate_mock_scenario(
        self,
        scenario_name: str,
        device_id: str = "dev_mock_vivo_v2334",
    ) -> DeviceHealthSnapshot:
        """
        Generates deterministic mock scenarios for test and verification.
        Every scenario explicitly tags data_source = DataVerificationState.MOCK.
        """
        now = time.time()
        name_clean = scenario_name.upper().strip()

        # Default healthy parameters
        params: Dict[str, Any] = {
            "device_id": device_id,
            "platform": "android",
            "os_version": "Android 14 (API 34) - Funtouch OS 14",
            "battery_level": 84.0,
            "charging_state": "DISCHARGING",
            "storage_usage": {"used_gb": 42.0, "total_gb": 128.0, "percentage": 32.8},
            "memory_usage": {"used_mb": 3400.0, "total_mb": 8192.0, "percentage": 41.5},
            "cpu_usage": 16.5,
            "network_state": "CONNECTED",
            "network_type": "WIFI",
            "uptime": 86400.0,
            "last_boot": now - 86400.0,
            "last_seen": now,
            "security_posture": DeviceHealthState.HEALTHY.value,
            "permission_summary": {
                "high_risk_count": 0,
                "total_granted": 14,
                "high_risk_permissions": [],
            },
            "timestamp": now,
            "data_source": DataVerificationState.MOCK,
            "metadata": {"scenario": name_clean, "model": "V2334 (Vivo V30 Lite)"},
        }

        if name_clean in ("NORMAL_DEVICE", "HEALTHY"):
            pass

        elif name_clean == "CPU_SPIKE":
            params["cpu_usage"] = 98.4
            params["security_posture"] = DeviceHealthState.CRITICAL.value

        elif name_clean == "MEMORY_SPIKE":
            params["memory_usage"] = {"used_mb": 7920.0, "total_mb": 8192.0, "percentage": 96.6}
            params["security_posture"] = DeviceHealthState.CRITICAL.value

        elif name_clean == "STORAGE_FULL":
            params["storage_usage"] = {"used_gb": 126.5, "total_gb": 128.0, "percentage": 98.8}
            params["security_posture"] = DeviceHealthState.CRITICAL.value

        elif name_clean == "BATTERY_ANOMALY":
            params["battery_level"] = 3.5
            params["charging_state"] = "DISCHARGING"
            params["security_posture"] = DeviceHealthState.CRITICAL.value

        elif name_clean == "NETWORK_FLAPPING":
            params["network_state"] = "FLAPPING"
            params["security_posture"] = DeviceHealthState.WARNING.value

        elif name_clean == "AUTH_FAILURE_BURST":
            params["metadata"]["auth_failures_burst"] = 5
            params["security_posture"] = DeviceHealthState.WARNING.value

        elif name_clean == "PERMISSION_CHANGE":
            params["permission_summary"] = {
                "high_risk_count": 4,
                "total_granted": 20,
                "high_risk_permissions": ["SMS", "CAMERA", "MICROPHONE", "LOCATION"],
            }
            params["security_posture"] = DeviceHealthState.WARNING.value

        elif name_clean == "DEVICE_DISCONNECT":
            params["network_state"] = "DISCONNECTED"
            params["network_type"] = "NONE"
            params["security_posture"] = DeviceHealthState.WARNING.value

        elif name_clean == "DEVICE_RECONNECT":
            params["network_state"] = "CONNECTED"
            params["uptime"] = 45.0
            params["last_boot"] = now - 45.0
            params["security_posture"] = DeviceHealthState.HEALTHY.value

        elif name_clean == "REVOKED_DEVICE":
            params["metadata"]["revoked"] = True
            params["security_posture"] = "REVOKED"

        elif name_clean == "EXPIRED_SESSION":
            params["metadata"]["session_expired"] = True
            params["security_posture"] = "EXPIRED"

        elif name_clean == "UNAVAILABLE":
            params["data_source"] = DataVerificationState.UNAVAILABLE
            params["security_posture"] = DeviceHealthState.UNAVAILABLE.value

        else:
            params["metadata"]["unknown_scenario"] = name_clean

        return DeviceHealthSnapshot(**params)

    # --------------------------------------------------------------------------
    # AI Advisory Explanation (Model Isolation Enforced)
    # --------------------------------------------------------------------------

    def generate_advisory_summary(
        self,
        health_state: DeviceHealthState,
        anomalies: List[AnomalyEvent],
        posture: SecurityPostureScore,
    ) -> Dict[str, Any]:
        """
        Generates structured human-readable advisory summaries.
        AI provides advisory context only:
        - CANNOT authorize devices
        - CANNOT declare device compromised without concrete evidence
        - CANNOT execute shell commands
        - CANNOT disable security controls
        """
        severity_summary = f"Device is currently in {health_state.value} state with a Security Posture score of {posture.score}/100 ({posture.rating})."
        
        if not anomalies:
            explanation = "All monitored hardware, operating system, and permission metrics are operating within expected baseline thresholds. No active anomalies detected."
            questions = ["Would you like to review historical telemetry trends?", "Do you want to run a proactive permission audit?"]
        else:
            findings_text = "; ".join(f"[{a.category.value}] {a.evidence}" for a in anomalies[:3])
            explanation = f"Detected {len(anomalies)} active anomaly condition(s): {findings_text}."
            questions = [
                "Would you like to inspect active background processes causing resource consumption?",
                "Do you want to re-authenticate or review recently granted permissions?",
            ]

        return {
            "advisory_summary": severity_summary,
            "technical_explanation": explanation,
            "suggested_investigation_questions": questions,
            "contributing_factors_summary": [f"{f['factor']}: {f['deduction']} pts ({f['evidence']})" for f in posture.contributing_factors],
            "disclaimer": "Advisory explanation generated deterministically. AI models cannot authorize devices or bypass security gates.",
        }
