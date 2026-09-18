"""
NR-AI SkyShield: Device Health & Anomaly Detection Models.
Step 10 Phase 3 — Device Health & Anomaly Detection.

Defines deterministic data structures, enumerations, and snapshot models for:
- Device Health state lifecycle (HEALTHY, DEGRADED, WARNING, CRITICAL, UNKNOWN, UNAVAILABLE)
- Bounded metric baselines and rolling statistics
- Anomaly events with deterministic evidence and confidence ratings
- Threat classifications and security conclusions (OBSERVED, SUSPECTED, NOT_VERIFIED)
- Explainable security posture score calculation
- Safe response recommendations
- Explicit provenance enforcement (LIVE, MOCK, UNAVAILABLE)
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import time
from typing import Any, Dict, List, Optional, Union

from app.security.models import DataVerificationState, redact_sensitive_data


# ==============================================================================
# 1. Health & Anomaly Enums
# ==============================================================================

class DeviceHealthState(str, Enum):
    """Deterministic health classification for authorized devices."""
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"
    UNAVAILABLE = "UNAVAILABLE"


class AnomalySeverity(str, Enum):
    """Severity tiering for detected device anomalies."""
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ThreatCategory(str, Enum):
    """Deterministic categorization of security and operational threats."""
    DEVICE_HEALTH = "DEVICE_HEALTH"
    AUTHENTICATION = "AUTHENTICATION"
    AUTHORIZATION = "AUTHORIZATION"
    NETWORK = "NETWORK"
    PERMISSION = "PERMISSION"
    RESOURCE_EXHAUSTION = "RESOURCE_EXHAUSTION"
    CONFIGURATION = "CONFIGURATION"
    UNKNOWN = "UNKNOWN"


class ThreatConclusion(str, Enum):
    """Epistemic evidence level for security conclusions (strictly no unsubstantiated claims)."""
    OBSERVED = "OBSERVED"
    SUSPECTED = "SUSPECTED"
    NOT_VERIFIED = "NOT_VERIFIED"


class TelemetryProcessingState(str, Enum):
    """Operational telemetry pipeline state reflecting actual processing."""
    IDLE = "IDLE"
    COLLECTING = "COLLECTING"
    ANALYZING = "ANALYZING"
    ANOMALY_DETECTED = "ANOMALY_DETECTED"
    VERIFYING = "VERIFYING"
    REPORTING = "REPORTING"
    ERROR = "ERROR"
    STOPPED = "STOPPED"


class SafeResponseAction(str, Enum):
    """Standard safe response recommendations."""
    REAUTHENTICATE = "REAUTHENTICATE"
    REVIEW_PERMISSIONS = "REVIEW_PERMISSIONS"
    RECONNECT_DEVICE = "RECONNECT_DEVICE"
    SUSPEND_DEVICE = "SUSPEND_DEVICE"
    REVOKE_DEVICE = "REVOKE_DEVICE"
    EMERGENCY_STOP = "EMERGENCY_STOP"


# ==============================================================================
# 2. Device Health Snapshot Model
# ==============================================================================

@dataclass
class DeviceHealthSnapshot:
    """
    Deterministic snapshot of device operational health and security metrics.
    Data source is explicitly labeled (LIVE, MOCK, UNAVAILABLE); never fabricated.
    """
    device_id: str
    platform: str = "android"
    os_version: str = "Android 14 (API 34)"
    battery_level: float = 85.0              # 0.0 - 100.0%
    charging_state: str = "DISCHARGING"      # "CHARGING", "DISCHARGING", "FULL", "NOT_CHARGING"
    storage_usage: Dict[str, Any] = field(default_factory=lambda: {
        "used_gb": 45.0,
        "total_gb": 128.0,
        "percentage": 35.15,
    })
    memory_usage: Dict[str, Any] = field(default_factory=lambda: {
        "used_mb": 3800.0,
        "total_mb": 8192.0,
        "percentage": 46.38,
    })
    cpu_usage: float = 18.5                  # 0.0 - 100.0%
    network_state: str = "CONNECTED"         # "CONNECTED", "DISCONNECTED", "FLAPPING"
    network_type: str = "WIFI"               # "WIFI", "CELLULAR", "ETHERNET", "NONE"
    uptime: float = 86400.0                  # Uptime in seconds
    last_boot: float = field(default_factory=lambda: time.time() - 86400.0)
    last_seen: float = field(default_factory=time.time)
    security_posture: str = DeviceHealthState.HEALTHY.value
    permission_summary: Dict[str, Any] = field(default_factory=lambda: {
        "high_risk_count": 0,
        "total_granted": 12,
        "high_risk_permissions": [],
    })
    timestamp: float = field(default_factory=time.time)
    data_source: DataVerificationState = DataVerificationState.MOCK
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serializes health snapshot to dictionary with safe redaction."""
        raw = {
            "device_id": self.device_id,
            "platform": self.platform,
            "os_version": self.os_version,
            "battery_level": round(float(self.battery_level), 1),
            "charging_state": str(self.charging_state).upper(),
            "storage_usage": dict(self.storage_usage),
            "memory_usage": dict(self.memory_usage),
            "cpu_usage": round(float(self.cpu_usage), 1),
            "network_state": str(self.network_state).upper(),
            "network_type": str(self.network_type).upper(),
            "uptime": round(float(self.uptime), 1),
            "last_boot": self.last_boot,
            "last_boot_iso": datetime.fromtimestamp(self.last_boot, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "last_seen": self.last_seen,
            "last_seen_iso": datetime.fromtimestamp(self.last_seen, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "security_posture": self.security_posture,
            "permission_summary": dict(self.permission_summary),
            "timestamp": self.timestamp,
            "timestamp_iso": datetime.fromtimestamp(self.timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "data_source": self.data_source.value,
            "metadata": dict(self.metadata),
        }
        return redact_sensitive_data(raw)


# ==============================================================================
# 3. Anomaly Event Model
# ==============================================================================

@dataclass
class AnomalyEvent:
    """
    Deterministic anomaly observation with concrete evidence and severity.
    Severity is never vaguely AI-generated without empirical evidence.
    """
    anomaly_id: str
    device_id: str
    category: ThreatCategory
    severity: AnomalySeverity
    observed_value: Any
    expected_range: Any
    detected_at: float = field(default_factory=time.time)
    evidence: str = ""
    confidence: float = 1.0                  # 0.0 - 1.0 deterministic confidence
    status: str = "DETECTED"                 # "DETECTED", "INVESTIGATING", "RESOLVED", "IGNORED"
    conclusion: ThreatConclusion = ThreatConclusion.OBSERVED
    recommendation: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serializes anomaly event with ISO timestamp and safe data."""
        return {
            "anomaly_id": self.anomaly_id,
            "device_id": self.device_id,
            "category": self.category.value if isinstance(self.category, ThreatCategory) else str(self.category),
            "severity": self.severity.value if isinstance(self.severity, AnomalySeverity) else str(self.severity),
            "observed_value": self.observed_value,
            "expected_range": self.expected_range,
            "detected_at": self.detected_at,
            "detected_at_iso": datetime.fromtimestamp(self.detected_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "evidence": self.evidence,
            "confidence": round(float(self.confidence), 2),
            "status": self.status,
            "conclusion": self.conclusion.value if isinstance(self.conclusion, ThreatConclusion) else str(self.conclusion),
            "recommendation": self.recommendation,
        }


# ==============================================================================
# 4. Device Baseline Model
# ==============================================================================

MAX_BASELINE_HISTORY_POINTS = 50


@dataclass
class DeviceBaseline:
    """
    Bounded rolling baseline of normal device behavior.
    Maintains bounded memory windows to prevent unbounded storage growth.
    """
    device_id: str
    created_at: float = field(default_factory=time.time)
    last_updated: float = field(default_factory=time.time)
    
    # Bounded metric histories (max MAX_BASELINE_HISTORY_POINTS elements each)
    cpu_history: List[float] = field(default_factory=list)
    memory_history: List[float] = field(default_factory=list)
    storage_history: List[float] = field(default_factory=list)
    battery_discharge_rates: List[float] = field(default_factory=list)
    reconnect_timestamps: List[float] = field(default_factory=list)
    auth_failure_timestamps: List[float] = field(default_factory=list)

    # Calculated baselines
    cpu_mean: float = 20.0
    cpu_std: float = 5.0
    memory_mean: float = 45.0
    memory_std: float = 5.0

    def record_snapshot(self, snapshot: DeviceHealthSnapshot) -> None:
        """Appends snapshot observation and recalculates running stats within bounds."""
        now = snapshot.timestamp or time.time()
        self.last_updated = now

        # 1. CPU
        self.cpu_history.append(float(snapshot.cpu_usage))
        if len(self.cpu_history) > MAX_BASELINE_HISTORY_POINTS:
            self.cpu_history.pop(0)
        self._recompute_stats()

        # 2. Memory
        mem_pct = snapshot.memory_usage.get("percentage")
        if mem_pct is not None:
            self.memory_history.append(float(mem_pct))
            if len(self.memory_history) > MAX_BASELINE_HISTORY_POINTS:
                self.memory_history.pop(0)

        # 3. Storage
        stor_pct = snapshot.storage_usage.get("percentage")
        if stor_pct is not None:
            self.storage_history.append(float(stor_pct))
            if len(self.storage_history) > MAX_BASELINE_HISTORY_POINTS:
                self.storage_history.pop(0)

        # 4. Clean old timestamps older than 1 hour
        one_hour_ago = now - 3600.0
        self.reconnect_timestamps = [t for t in self.reconnect_timestamps if t >= one_hour_ago]
        self.auth_failure_timestamps = [t for t in self.auth_failure_timestamps if t >= one_hour_ago]

    def record_reconnect(self, timestamp: Optional[float] = None) -> None:
        """Records a network reconnect event timestamp."""
        ts = timestamp or time.time()
        self.reconnect_timestamps.append(ts)
        if len(self.reconnect_timestamps) > MAX_BASELINE_HISTORY_POINTS:
            self.reconnect_timestamps.pop(0)

    def record_auth_failure(self, timestamp: Optional[float] = None) -> None:
        """Records an authentication failure timestamp."""
        ts = timestamp or time.time()
        self.auth_failure_timestamps.append(ts)
        if len(self.auth_failure_timestamps) > MAX_BASELINE_HISTORY_POINTS:
            self.auth_failure_timestamps.pop(0)

    def reset(self) -> None:
        """Clears all historical baseline windows and restores defaults."""
        now = time.time()
        self.created_at = now
        self.last_updated = now
        self.cpu_history.clear()
        self.memory_history.clear()
        self.storage_history.clear()
        self.battery_discharge_rates.clear()
        self.reconnect_timestamps.clear()
        self.auth_failure_timestamps.clear()
        self.cpu_mean = 20.0
        self.cpu_std = 5.0
        self.memory_mean = 45.0
        self.memory_std = 5.0

    def _recompute_stats(self) -> None:
        """Recomputes simple mean and standard deviation."""
        if self.cpu_history:
            self.cpu_mean = sum(self.cpu_history) / len(self.cpu_history)
            if len(self.cpu_history) > 1:
                variance = sum((x - self.cpu_mean) ** 2 for x in self.cpu_history) / (len(self.cpu_history) - 1)
                self.cpu_std = variance ** 0.5
            else:
                self.cpu_std = 5.0

        if self.memory_history:
            self.memory_mean = sum(self.memory_history) / len(self.memory_history)
            if len(self.memory_history) > 1:
                variance = sum((x - self.memory_mean) ** 2 for x in self.memory_history) / (len(self.memory_history) - 1)
                self.memory_std = variance ** 0.5
            else:
                self.memory_std = 5.0

    def to_dict(self) -> Dict[str, Any]:
        """Serializes baseline summary."""
        return {
            "device_id": self.device_id,
            "created_at": self.created_at,
            "created_at_iso": datetime.fromtimestamp(self.created_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "last_updated": self.last_updated,
            "last_updated_iso": datetime.fromtimestamp(self.last_updated, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "sample_count": len(self.cpu_history),
            "cpu_normal_range": f"{max(0.0, self.cpu_mean - 2 * self.cpu_std):.1f}% - {min(100.0, self.cpu_mean + 2 * self.cpu_std):.1f}%",
            "memory_normal_range": f"{max(0.0, self.memory_mean - 2 * self.memory_std):.1f}% - {min(100.0, self.memory_mean + 2 * self.memory_std):.1f}%",
            "reconnects_last_hour": len(self.reconnect_timestamps),
            "auth_failures_last_hour": len(self.auth_failure_timestamps),
        }


# ==============================================================================
# 5. Security Posture Score Model
# ==============================================================================

@dataclass
class SecurityPostureScore:
    """
    Transparent, explainable security posture score.
    Never invented or altered by an LLM; computed deterministically with
    fully itemized contributing factor deductions.
    """
    device_id: str
    score: int                               # 0 - 100
    rating: str                              # "EXCELLENT", "GOOD", "FAIR", "DEGRADED", "CRITICAL"
    contributing_factors: List[Dict[str, Any]] = field(default_factory=list)
    calculated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "device_id": self.device_id,
            "score": self.score,
            "rating": self.rating,
            "contributing_factors": list(self.contributing_factors),
            "calculated_at": self.calculated_at,
            "calculated_at_iso": datetime.fromtimestamp(self.calculated_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        }
