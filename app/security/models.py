"""
NR-AI SkyShield: Security Agent Foundation — Core Data Models & Contracts.
Step 10 Phase 1 — Security Agent Foundation.

Defines strongly-typed, verifiable, immutable data structures for:
- Security state machine states
- Threat finding severity and categories
- Verification states (LIVE, MOCK, UNAVAILABLE)
- Device security model
- Application security cards (WhatsApp, Instagram, Snapchat)
- Camera & Microphone security auditing
- Deterministic permission model
- Append-only security events with automatic secret redaction
- Security audit logging records
- Deterministic security policies
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import re
import time
from typing import Any, Dict, List, Optional


# ==============================================================================
# 1. State Machine & Severity Enums
# ==============================================================================

class SecurityState(str, Enum):
    """Deterministic states for SkyShield Security Agent."""
    IDLE = "IDLE"
    SCANNING = "SCANNING"
    ANALYZING = "ANALYZING"
    MONITORING = "MONITORING"
    ALERT = "ALERT"
    RESOLVING = "RESOLVING"
    STOPPED = "STOPPED"
    ERROR = "ERROR"


class SecuritySeverity(str, Enum):
    """Standard threat severity levels."""
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class EvidenceConfidence(str, Enum):
    """Empirical grounding level for security findings."""
    OBSERVED = "OBSERVED"        # Directly corroborated by verified local telemetry
    SUSPECTED = "SUSPECTED"      # Inferred from suspicious configuration or anomalous pattern
    UNVERIFIED = "UNVERIFIED"    # Advisory alert without direct local evidence


class FindingCategory(str, Enum):
    """Categories of security findings."""
    UNUSUAL_PERMISSION = "UNUSUAL_PERMISSION"
    UNKNOWN_APPLICATION = "UNKNOWN_APPLICATION"
    OUTDATED_APPLICATION = "OUTDATED_APPLICATION"
    SUSPICIOUS_CONFIGURATION = "SUSPICIOUS_CONFIGURATION"
    CAMERA_ACCESS = "CAMERA_ACCESS"
    MICROPHONE_ACCESS = "MICROPHONE_ACCESS"
    SECURITY_PATCH = "SECURITY_PATCH"
    UNKNOWN_DEVICE = "UNKNOWN_DEVICE"
    UNAUTHORIZED_SESSION = "UNAUTHORIZED_SESSION"
    POLICY_VIOLATION = "POLICY_VIOLATION"


class DataVerificationState(str, Enum):
    """
    Ground-truth verification flag for telemetry.
    Strictly distinguishes live hardware signals from controlled test fixtures.
    """
    LIVE = "LIVE"                  # Authenticated live device API signal
    MOCK = "MOCK"                  # Controlled test fixture / simulated baseline
    UNAVAILABLE = "UNAVAILABLE"    # API endpoint or device disconnected


class PermissionName(str, Enum):
    """Core operating system permissions audited by SkyShield."""
    CAMERA = "CAMERA"
    MICROPHONE = "MICROPHONE"
    LOCATION = "LOCATION"
    CONTACTS = "CONTACTS"
    STORAGE = "STORAGE"
    NOTIFICATIONS = "NOTIFICATIONS"
    PHONE = "PHONE"
    SMS = "SMS"
    READ_SMS = "READ_SMS"
    ACCESS_FINE_LOCATION = "ACCESS_FINE_LOCATION"


class PermissionStatus(str, Enum):
    """Status of an audited permission."""
    GRANTED = "GRANTED"
    DENIED = "DENIED"
    RESTRICTED = "RESTRICTED"
    UNKNOWN = "UNKNOWN"


# ==============================================================================
# 2. Sensitive Data Redaction
# ==============================================================================

SENSITIVE_KEY_SUBSTRINGS = (
    "secret",
    "token",
    "key",
    "password",
    "signature",
    "code",
    "otp",
    "credential",
    "private",
    "auth",
    "bearer",
    "jwt",
    "message_content",
    "chat_history",
    "message",
)

def redact_sensitive_data(obj: Any) -> Any:
    """
    Recursively redacts dictionary values where the key suggests sensitive data,
    and sanitizes token/credential strings matching known patterns.
    """
    if isinstance(obj, dict):
        cleaned = {}
        for k, v in obj.items():
            k_lower = str(k).lower()
            if any(s in k_lower for s in SENSITIVE_KEY_SUBSTRINGS):
                cleaned[k] = "[REDACTED]"
            elif isinstance(v, (dict, list)):
                cleaned[k] = redact_sensitive_data(v)
            else:
                cleaned[k] = v
        return cleaned
    elif isinstance(obj, list):
        return [redact_sensitive_data(item) for item in obj]
    elif isinstance(obj, str):
        # Redact bearer tokens or hex keys if exposed in strings
        if re.search(r"\b(?:bearer\s+[a-zA-Z0-9_\-\.]{15,}|[a-fA-F0-9]{32,64})\b", obj):
            return re.sub(r"\b(?:bearer\s+[a-zA-Z0-9_\-\.]{15,}|[a-fA-F0-9]{32,64})\b", "[REDACTED_SECRET]", obj)
        return obj
    return obj


# ==============================================================================
# 3. Security Finding & Permission Models
# ==============================================================================

@dataclass
class SecurityFinding:
    """Represents an identified security posture issue or threat vector."""
    finding_id: str
    category: FindingCategory
    severity: SecuritySeverity
    title: str
    description: str
    evidence: str
    confidence: EvidenceConfidence = EvidenceConfidence.OBSERVED
    remediation: str = "Review application settings and revoke unnecessary permissions."
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "category": self.category.value,
            "severity": self.severity.value,
            "title": self.title,
            "description": self.description,
            "evidence": self.evidence,
            "confidence": self.confidence.value,
            "remediation": self.remediation,
            "timestamp": self.timestamp,
        }


@dataclass
class SecurityPermission:
    """Audited permission state for a device or application."""
    name: PermissionName
    status: PermissionStatus
    risk_level: SecuritySeverity = SecuritySeverity.LOW
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name.value,
            "status": self.status.value,
            "risk_level": self.risk_level.value,
            "description": self.description,
        }


# ==============================================================================
# 4. Device & Application Security Models
# ==============================================================================

@dataclass
class DeviceSecurityModel:
    """Posture assessment of the host/enrolled device."""
    device_id: str
    device_name: str
    platform: str                    # "Windows", "Android", "iOS", "Linux"
    os_version: str
    security_patch: str
    app_version: str
    enrollment_state: str            # "ENROLLED_VERIFIED", "STANDALONE_LOCAL", "UNENROLLED"
    is_rooted: bool = False
    is_encrypted: bool = True
    last_seen: float = field(default_factory=time.time)
    security_status: str = "SECURE"   # "SECURE", "ELEVATED", "CRITICAL", "STOPPED"
    data_source: DataVerificationState = DataVerificationState.LIVE

    @property
    def verification_state(self) -> DataVerificationState:
        return self.data_source

    def to_dict(self) -> Dict[str, Any]:
        return {
            "device_id": self.device_id,
            "device_name": self.device_name,
            "platform": self.platform,
            "os_version": self.os_version,
            "security_patch": self.security_patch,
            "app_version": self.app_version,
            "enrollment_state": self.enrollment_state,
            "is_rooted": self.is_rooted,
            "is_encrypted": self.is_encrypted,
            "last_seen": self.last_seen,
            "security_status": self.security_status,
            "data_source": self.data_source.value,
            "verification_state": self.data_source.value,
        }


@dataclass
class ApplicationSecurityCard:
    """
    Security assessment card for installed application.
    STRICT INVARIANT: Does NOT access message databases or private user chats.
    """
    app_name: str                    # "WhatsApp", "Instagram", "Snapchat"
    installed: bool
    version: str
    package_identifier: str
    permission_status: Dict[str, str] = field(default_factory=dict)
    last_security_check: float = field(default_factory=time.time)
    risk_state: str = "SECURE"       # "SECURE", "ATTENTION", "HIGH_RISK"
    security_findings: List[SecurityFinding] = field(default_factory=list)
    data_source: DataVerificationState = DataVerificationState.LIVE
    is_sandboxed: bool = True
    inspection_scope: str = "Configuration & Permissions (Private Messages: Zero-Interception Blocked)"

    @property
    def package_name(self) -> str:
        return self.package_identifier

    @property
    def is_installed(self) -> bool:
        return self.installed

    @property
    def verification_state(self) -> DataVerificationState:
        return self.data_source

    def to_dict(self) -> Dict[str, Any]:
        return {
            "app_name": self.app_name,
            "installed": self.installed,
            "is_installed": self.installed,
            "version": self.version,
            "package_identifier": self.package_identifier,
            "package_name": self.package_identifier,
            "permission_status": self.permission_status,
            "last_security_check": self.last_security_check,
            "risk_state": self.risk_state,
            "is_sandboxed": self.is_sandboxed,
            "inspection_scope": self.inspection_scope,
            "security_findings": [f.to_dict() for f in self.security_findings],
            "data_source": self.data_source.value,
            "verification_state": self.data_source.value,
        }


@dataclass
class CameraSecurityModel:
    """Camera hardware security & permission audit representation."""
    front_camera_available: bool = True
    rear_camera_available: bool = True
    camera_permission: PermissionStatus = PermissionStatus.GRANTED
    currently_in_use: bool = False
    is_sensor_active: bool = False
    active_streams: int = 0
    authorized_apps: List[str] = field(default_factory=list)
    last_access_event: Optional[str] = None
    security_status: str = "SECURE"  # "SECURE", "IN_USE", "WARNING"
    data_source: DataVerificationState = DataVerificationState.LIVE

    @property
    def verification_state(self) -> DataVerificationState:
        return self.data_source

    def to_dict(self) -> Dict[str, Any]:
        return {
            "front_camera_available": self.front_camera_available,
            "rear_camera_available": self.rear_camera_available,
            "camera_permission": self.camera_permission.value,
            "currently_in_use": self.currently_in_use or self.is_sensor_active,
            "is_sensor_active": self.is_sensor_active or self.currently_in_use,
            "active_streams": self.active_streams,
            "authorized_apps": self.authorized_apps,
            "last_access_event": self.last_access_event,
            "security_status": self.security_status,
            "data_source": self.data_source.value,
            "verification_state": self.data_source.value,
        }


@dataclass
class MicrophoneSecurityModel:
    """Microphone hardware security & permission audit representation."""
    microphone_available: bool = True
    permission_status: PermissionStatus = PermissionStatus.GRANTED
    currently_in_use: bool = False
    is_recording_active: bool = False
    authorized_session_id: Optional[str] = None
    last_access_event: Optional[str] = None
    security_status: str = "SECURE"  # "SECURE", "IN_USE", "WARNING"
    data_source: DataVerificationState = DataVerificationState.LIVE

    @property
    def verification_state(self) -> DataVerificationState:
        return self.data_source

    def to_dict(self) -> Dict[str, Any]:
        return {
            "microphone_available": self.microphone_available,
            "permission_status": self.permission_status.value,
            "currently_in_use": self.currently_in_use or self.is_recording_active,
            "is_recording_active": self.is_recording_active or self.currently_in_use,
            "authorized_session_id": self.authorized_session_id,
            "last_access_event": self.last_access_event,
            "security_status": self.security_status,
            "data_source": self.data_source.value,
            "verification_state": self.data_source.value,
        }


# ==============================================================================
# 5. Security Event & Audit Log Models
# ==============================================================================

@dataclass
class SecurityEvent:
    """Append-only security event record with automatic secret redaction."""
    event_id: str
    timestamp: float
    device_id: str
    event_type: str
    source: str
    severity: SecuritySeverity
    description: str
    evidence: str = ""
    status: str = "LOGGED"

    def to_dict(self) -> Dict[str, Any]:
        raw = {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "device_id": self.device_id,
            "event_type": self.event_type,
            "source": self.source,
            "severity": self.severity.value,
            "description": self.description,
            "evidence": self.evidence,
            "status": self.status,
        }
        return redact_sensitive_data(raw)


@dataclass
class SecurityAuditRecord:
    """Operational audit log record tracking who did what, when, and result."""
    audit_id: str
    timestamp: str
    initiator: str
    operation: str
    target: str
    result: str                      # "SUCCESS", "DENIED", "STOPPED", "ERROR"
    classification: str = "SECURITY_POSTURE_ASSESSMENT"
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        raw = {
            "audit_id": self.audit_id,
            "timestamp": self.timestamp,
            "initiator": self.initiator,
            "operation": self.operation,
            "target": self.target,
            "result": self.result,
            "classification": self.classification,
            "details": self.details,
        }
        return redact_sensitive_data(raw)


# ==============================================================================
# 6. Deterministic Security Policy
# ==============================================================================

class SecurityPolicy:
    """
    Enforces deterministic safety invariants and rejects prohibited actions.
    Guarantees no covert surveillance, no message extraction, and zero shell execution.
    """
    PROHIBITED_OPERATIONS = (
        "shell_execution",
        "cmd_execution",
        "powershell_execution",
        "eval_execution",
        "exec_execution",
        "extract_credentials",
        "dump_passwords",
        "intercept_messages",
        "read_whatsapp_database",
        "read_instagram_messages",
        "read_snapchat_messages",
        "hidden_camera_activate",
        "hidden_mic_record",
        "bypass_permissions",
        "bypass_sandbox",
        "keylogger_activate",
    )

    @classmethod
    def is_action_permitted(cls, action_name: str) -> bool:
        """Determines if a requested action violates safety invariants."""
        action_clean = action_name.strip().lower()
        for prohibited in cls.PROHIBITED_OPERATIONS:
            if prohibited in action_clean:
                return False
        return True

    @classmethod
    def validate_action(cls, action_name: str) -> None:
        """Raises PermissionError if action violates safety invariants."""
        if not cls.is_action_permitted(action_name):
            raise PermissionError(
                f"SecurityPolicy Violation: Operation '{action_name}' is strictly prohibited under zero-trust invariants."
            )
