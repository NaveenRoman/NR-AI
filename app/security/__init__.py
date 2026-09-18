"""
NR-AI SkyShield: Security Agent Package.
Step 10 Phase 1 — Security Agent Foundation.
"""

from app.security.agent import DeterministicSecurityGate, SecurityAgent
from app.security.auditor import PermissionAuditor
from app.security.coordinator import SecurityCoordinator
from app.security.models import (
    ApplicationSecurityCard,
    CameraSecurityModel,
    DataVerificationState,
    DeviceSecurityModel,
    EvidenceConfidence,
    FindingCategory,
    MicrophoneSecurityModel,
    PermissionName,
    PermissionStatus,
    SecurityAuditRecord,
    SecurityEvent,
    SecurityFinding,
    SecurityPermission,
    SecurityPolicy,
    SecuritySeverity,
    SecurityState,
    redact_sensitive_data,
)
from app.security.enrollment_models import (
    DEFAULT_PHASE2_CAPABILITIES,
    PROHIBITED_CAPABILITIES,
    AuthorizationState,
    DeviceIdentityModel,
    DeviceKeyRecord,
    DeviceSession,
    EnrollmentState,
    PairingRequest,
    PairingRequestStatus,
    SkyShieldCapability,
    generate_challenge,
    generate_pairing_code,
    mask_phone_number,
    normalize_phone_number,
)
from app.security.pairing_manager import (
    CapabilityEscalationError,
    DevicePairingManager,
    DeviceRevokedError,
    IllegalEnrollmentTransitionError,
)
from app.security.scanner import SecurityScanner

__all__ = [
    "ApplicationSecurityCard",
    "AuthorizationState",
    "CameraSecurityModel",
    "CapabilityEscalationError",
    "DEFAULT_PHASE2_CAPABILITIES",
    "DataVerificationState",
    "DeterministicSecurityGate",
    "DeviceIdentityModel",
    "DeviceKeyRecord",
    "DevicePairingManager",
    "DeviceRevokedError",
    "DeviceSecurityModel",
    "DeviceSession",
    "EnrollmentState",
    "EvidenceConfidence",
    "FindingCategory",
    "IllegalEnrollmentTransitionError",
    "MicrophoneSecurityModel",
    "PROHIBITED_CAPABILITIES",
    "PairingRequest",
    "PairingRequestStatus",
    "PermissionAuditor",
    "PermissionName",
    "PermissionStatus",
    "SecurityAgent",
    "SecurityAuditRecord",
    "SecurityCoordinator",
    "SecurityEvent",
    "SecurityFinding",
    "SecurityPermission",
    "SecurityPolicy",
    "SecurityScanner",
    "SecuritySeverity",
    "SecurityState",
    "SkyShieldCapability",
    "generate_challenge",
    "generate_pairing_code",
    "mask_phone_number",
    "normalize_phone_number",
    "redact_sensitive_data",
]
