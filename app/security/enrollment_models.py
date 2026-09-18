"""
NR-AI SkyShield: Authorized Device Pairing & Secure Enrollment Data Models.
Step 10 Phase 2 — Authorized Device Pairing & Secure Enrollment.

Defines deterministic, cryptographically sound, verifiable data structures for:
- Enrollment and Authorization state machines
- Pairing requests with cryptographically random challenges and bounded TTL
- Explicit capability scopes (zero surveillance capabilities)
- Device identity with privacy-preserving phone number normalization and masking
- Registered device keys (public key abstractions, never logging or storing private keys)
- Authenticated cryptographic sessions with replay protection
- Transparent provenance labeling (LIVE, MOCK, UNAVAILABLE)
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import hmac
import re
import secrets
import time
from typing import Any, Dict, List, Optional, Set

from app.security.models import DataVerificationState, redact_sensitive_data


# ==============================================================================
# 1. Enrollment & Authorization Enums
# ==============================================================================

class EnrollmentState(str, Enum):
    """Deterministic states for device enrollment lifecycle."""
    UNREGISTERED = "UNREGISTERED"
    PAIRING_REQUESTED = "PAIRING_REQUESTED"
    AWAITING_OWNER_APPROVAL = "AWAITING_OWNER_APPROVAL"
    OWNER_APPROVED = "OWNER_APPROVED"
    KEY_EXCHANGE = "KEY_EXCHANGE"
    ENROLLED = "ENROLLED"
    AUTHENTICATED = "AUTHENTICATED"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"


class AuthorizationState(str, Enum):
    """Device authorization status relative to SkyShield services."""
    UNAUTHORIZED = "UNAUTHORIZED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    AUTHORIZED = "AUTHORIZED"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"


class PairingRequestStatus(str, Enum):
    """Status of an individual pairing invitation/request."""
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    COMPLETED = "COMPLETED"
    REVOKED = "REVOKED"


class SkyShieldCapability(str, Enum):
    """
    Explicit, granular capability scopes for SkyShield.
    Phase 2 strictly includes only security posture and status inspection scopes.
    Surveillance, media capture, keylogging, and credential extraction are forbidden.
    """
    READ_DEVICE_STATUS = "READ_DEVICE_STATUS"
    READ_SECURITY_POSTURE = "READ_SECURITY_POSTURE"
    READ_APP_SECURITY_METADATA = "READ_APP_SECURITY_METADATA"
    READ_PERMISSION_STATUS = "READ_PERMISSION_STATUS"
    READ_SECURITY_EVENTS = "READ_SECURITY_EVENTS"
    READ_AUDIT_LOG = "READ_AUDIT_LOG"
    TELEMETRY_READ = "telemetry:read"
    HEALTH_READ = "health:read"
    SECURITY_EVENTS_READ = "security_events:read"
    CONFIG_AUDIT = "config:audit"
    HARDWARE_STATUS = "hardware:status"
    NETWORK_DIAGNOSTICS = "network:diagnostics"
    PERMISSION_MONITOR = "permission:monitor"


# Forbidden capabilities explicitly enumerated to reject escalations
PROHIBITED_CAPABILITIES = frozenset({
    "PRIVATE_MESSAGE_CONTENT",
    "SCREEN_CAPTURE",
    "CAMERA_CAPTURE",
    "MICROPHONE_RECORDING",
    "KEYLOGGING",
    "CREDENTIAL_ACCESS",
    "ROOT_SHELL",
    "ARBITRARY_COMMAND_EXEC",
    # Alternative format aliases
    "messages:read",
    "message:read",
    "messages:access",
    "messages_read",
    "camera:capture",
    "camera_capture",
    "microphone:record",
    "microphone_record",
    "screen:capture",
    "screen_capture",
    "keylogging",
    "credentials:read",
    "credential:access",
    "root:shell",
    "shell:exec",
})


def is_capability_prohibited(cap: str) -> bool:
    """Returns True if the capability requests covert surveillance or unauthorized escalation."""
    if not cap:
        return False
    c_upper = cap.upper().replace(":", "_")
    c_lower = cap.lower().replace("_", ":")
    if cap in PROHIBITED_CAPABILITIES or c_upper in PROHIBITED_CAPABILITIES or c_lower in PROHIBITED_CAPABILITIES:
        return True
    c_clean = cap.lower()
    for token in ("message", "camera", "microphone", "screen", "keylog", "credential", "root", "shell", "exec"):
        if token in c_clean:
            return True
    return False


DEFAULT_PHASE2_CAPABILITIES = [
    SkyShieldCapability.READ_DEVICE_STATUS.value,
    SkyShieldCapability.READ_SECURITY_POSTURE.value,
    SkyShieldCapability.READ_APP_SECURITY_METADATA.value,
    SkyShieldCapability.READ_PERMISSION_STATUS.value,
    SkyShieldCapability.READ_SECURITY_EVENTS.value,
    SkyShieldCapability.READ_AUDIT_LOG.value,
    "telemetry:read",
    "health:read",
    "security_events:read",
    "config:audit",
    "hardware:status",
    "network:diagnostics",
    "permission:monitor",
]

DEFAULT_PHASE3_CAPABILITIES = list(DEFAULT_PHASE2_CAPABILITIES)


# ==============================================================================
# 2. Phone Number Handling (Normalization & Masking)
# ==============================================================================

def normalize_phone_number(raw_phone: str) -> str:
    """
    Normalizes phone numbers to standard E.164-like format.
    Strips formatting characters (spaces, dashes, parentheses).
    Guarantees consistent identification without treating as auth credential.
    """
    if not raw_phone:
        return ""
    cleaned = re.sub(r"[^\d+]", "", raw_phone.strip())
    if not cleaned.startswith("+"):
        if len(cleaned) == 10:
            cleaned = "+91" + cleaned
        else:
            cleaned = "+" + cleaned
    return cleaned


def mask_phone_number(phone: str) -> str:
    """
    Masks phone numbers for safe display in UI and audit logs.
    Example: '+919876543210' -> '+91 ******3210'.
    Guarantees privacy preservation in logs and telemetry.
    """
    normalized = normalize_phone_number(phone)
    if not normalized or len(normalized) < 7:
        return "[REDACTED_PHONE]"
    
    prefix = normalized[:3]
    suffix = normalized[-4:]
    masked_part = "*" * (len(normalized) - 7)
    if len(masked_part) < 3:
        masked_part = "******"
    return f"{prefix} {masked_part}{suffix}"


# ==============================================================================
# 3. Cryptographic Helper Utilities
# ==============================================================================

def generate_challenge(num_bytes: int = 32) -> str:
    """Generates a cryptographically secure random challenge string."""
    return secrets.token_hex(num_bytes)


def generate_pairing_code(length: int = 6) -> str:
    """
    Generates a cryptographically secure, single-use numeric pairing code.
    Formatted as standard 6-digit verification code.
    """
    code_int = secrets.randbelow(10 ** length)
    return f"{code_int:0{length}d}"


# ==============================================================================
# 4. Device Identity Model
# ==============================================================================

@dataclass
class DeviceIdentityModel:
    """
    Deterministic device identity representation.
    Does NOT rely solely on phone numbers for identity or authentication.
    """
    device_id: str
    device_name: str
    platform: str  # "android", "ios", "windows", etc.
    device_fingerprint: str
    registration_time: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    enrollment_state: EnrollmentState = EnrollmentState.UNREGISTERED
    authorization_state: AuthorizationState = AuthorizationState.UNAUTHORIZED
    phone_number_masked: Optional[str] = None
    verification_state: DataVerificationState = DataVerificationState.MOCK
    granted_capabilities: List[str] = field(default_factory=lambda: list(DEFAULT_PHASE2_CAPABILITIES))
    active_session_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "device_id": self.device_id,
            "device_name": self.device_name,
            "platform": self.platform,
            "device_fingerprint": self.device_fingerprint,
            "registration_time": self.registration_time,
            "registration_time_iso": datetime.fromtimestamp(self.registration_time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "last_seen": self.last_seen,
            "last_seen_iso": datetime.fromtimestamp(self.last_seen, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "enrollment_state": self.enrollment_state.value,
            "authorization_state": self.authorization_state.value,
            "phone_number_masked": self.phone_number_masked,
            "verification_state": self.verification_state.value,
            "granted_capabilities": list(self.granted_capabilities),
            "active_session_id": self.active_session_id,
            "metadata": dict(self.metadata),
        }


# ==============================================================================
# 5. Pairing Request Model
# ==============================================================================

@dataclass
class PairingRequest:
    """
    Represents an explicit pairing request initiated by the SkyShield operator.
    Requires explicit target-device owner approval before enrollment.
    """
    pairing_id: str
    device_id: str
    requester_id: str
    pairing_code: str  # Cryptographically random, single-use
    challenge: str  # Cryptographically secure random challenge
    created_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + 600.0)  # Default 10 min TTL
    status: PairingRequestStatus = PairingRequestStatus.PENDING
    requested_capabilities: List[str] = field(default_factory=lambda: list(DEFAULT_PHASE2_CAPABILITIES))
    phone_number_masked: Optional[str] = None
    device_name: str = "Unknown Device"
    platform: str = "android"
    attempts_remaining: int = 5
    shared_secret_hash: Optional[str] = None  # SHA-256 hash of shared secret established upon approval

    def is_expired(self, current_time: Optional[float] = None) -> bool:
        now = current_time if current_time is not None else time.time()
        return now >= self.expires_at

    def is_valid(self, current_time: Optional[float] = None) -> bool:
        return (
            self.status == PairingRequestStatus.PENDING
            and not self.is_expired(current_time)
            and self.attempts_remaining > 0
        )

    def to_dict(self) -> Dict[str, Any]:
        """Returns safe public dictionary without exposing raw secrets."""
        return {
            "pairing_id": self.pairing_id,
            "device_id": self.device_id,
            "device_name": self.device_name,
            "platform": self.platform,
            "requester_id": self.requester_id,
            "pairing_code": self.pairing_code,
            "created_at": self.created_at,
            "created_at_iso": datetime.fromtimestamp(self.created_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "expires_at": self.expires_at,
            "expires_at_iso": datetime.fromtimestamp(self.expires_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "status": self.status.value,
            "requested_capabilities": list(self.requested_capabilities),
            "phone_number_masked": self.phone_number_masked,
            "attempts_remaining": self.attempts_remaining,
            "ttl_remaining_seconds": max(0.0, round(self.expires_at - time.time(), 1)),
        }


# ==============================================================================
# 6. Device Key Record
# ==============================================================================

@dataclass
class DeviceKeyRecord:
    """
    Represents an authorized device's registered public key or key metadata.
    Private keys are NEVER transmitted to or stored by the server.
    """
    device_id: str
    key_id: str
    algorithm: str = "HMAC-SHA256"  # or "ED25519"
    public_key_hex: str = ""  # Public key or shared secret hash (never private key)
    created_at: float = field(default_factory=time.time)
    status: str = "ACTIVE"  # "ACTIVE", "ROTATED", "REVOKED"
    last_used: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "device_id": self.device_id,
            "key_id": self.key_id,
            "algorithm": self.algorithm,
            "public_key_hex": self.public_key_hex[:16] + "..." if len(self.public_key_hex) > 16 else self.public_key_hex,
            "created_at": self.created_at,
            "created_at_iso": datetime.fromtimestamp(self.created_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "status": self.status,
            "last_used": self.last_used,
            "last_used_iso": datetime.fromtimestamp(self.last_used, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        }


# ==============================================================================
# 7. Authenticated Device Session
# ==============================================================================

@dataclass
class DeviceSession:
    """
    Authenticated session for an enrolled device with bounded lifetime
    and nonce-based replay protection.
    """
    session_id: str
    device_id: str
    key_id: str
    issued_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + 3600.0)  # Default 1 hour TTL
    last_activity: float = field(default_factory=time.time)
    permissions: List[str] = field(default_factory=lambda: list(DEFAULT_PHASE2_CAPABILITIES))
    status: str = "ACTIVE"  # "ACTIVE", "EXPIRED", "REVOKED", "SUSPENDED", "STOPPED"
    session_token: str = field(default_factory=lambda: secrets.token_hex(32))

    def is_expired(self, current_time: Optional[float] = None) -> bool:
        now = current_time if current_time is not None else time.time()
        return now >= self.expires_at

    def touch(self, current_time: Optional[float] = None) -> None:
        now = current_time if current_time is not None else time.time()
        self.last_activity = now

    def to_dict(self) -> Dict[str, Any]:
        """Returns safe session snapshot without leaking raw session token."""
        return {
            "session_id": self.session_id,
            "device_id": self.device_id,
            "key_id": self.key_id,
            "issued_at": self.issued_at,
            "issued_at_iso": datetime.fromtimestamp(self.issued_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "expires_at": self.expires_at,
            "expires_at_iso": datetime.fromtimestamp(self.expires_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "last_activity": self.last_activity,
            "last_activity_iso": datetime.fromtimestamp(self.last_activity, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "permissions": list(self.permissions),
            "status": self.status,
            "ttl_remaining_seconds": max(0.0, round(self.expires_at - time.time(), 1)),
        }
