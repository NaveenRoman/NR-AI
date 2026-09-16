"""
NR-AI Remote Transport & Security Foundation.
Step 10 Phase 1 — Secure Phone <-> PC Communication Foundation.
"""

from app.remote.audit import AuditRecord, SecurityAuditLogger, redact_sensitive_data
from app.remote.auth import Session, SessionManager
from app.remote.config import (
    ALLOWED_HOSTS,
    DEFAULT_HOST,
    DEFAULT_PORT,
    MAX_PAYLOAD_BYTES,
    MAX_REQUEST_BYTES,
    NONCE_TIMESTAMP_TOLERANCE_SECONDS,
    PROHIBITED_HOSTS,
)
from app.remote.emergency import EmergencyStopController, EmergencyStopStatus
from app.remote.identity import (
    DeviceIdentity,
    PairingManager,
    PairingState,
    PCIdentity,
)
from app.remote.permissions import (
    DEFAULT_COMPANION_SCOPES,
    ModelIsolationGate,
    PhonePermissionScope,
    PROHIBITED_ACTIONS,
    authorize_action,
)
from app.remote.protocol import (
    SecureRequest,
    SecureResponse,
    parse_and_validate_request,
)
from app.remote.rate_limiter import RateLimiter
from app.remote.server import (
    SecureDashboardServer,
    SecureGateway,
    SecurityBindingError,
)
from app.remote.transport import (
    EncryptedPacket,
    SecureTransport,
    TransportIntegrityError,
    TransportModeError,
    TransportSecurityMode,
)

__all__ = [
    # Identity & Pairing
    "DeviceIdentity",
    "PCIdentity",
    "PairingState",
    "PairingManager",
    # Auth & Sessions
    "Session",
    "SessionManager",
    # Permissions & Isolation
    "PhonePermissionScope",
    "DEFAULT_COMPANION_SCOPES",
    "PROHIBITED_ACTIONS",
    "authorize_action",
    "ModelIsolationGate",
    # Protocol & Schema
    "SecureRequest",
    "SecureResponse",
    "parse_and_validate_request",
    # Transport
    "TransportSecurityMode",
    "EncryptedPacket",
    "SecureTransport",
    "TransportIntegrityError",
    "TransportModeError",
    # Rate Limiter
    "RateLimiter",
    # Emergency Stop
    "EmergencyStopController",
    "EmergencyStopStatus",
    # Audit
    "SecurityAuditLogger",
    "AuditRecord",
    "redact_sensitive_data",
    # Server & Gateway
    "SecureGateway",
    "SecureDashboardServer",
    "SecurityBindingError",
    # Constants
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "ALLOWED_HOSTS",
    "PROHIBITED_HOSTS",
    "MAX_REQUEST_BYTES",
    "MAX_PAYLOAD_BYTES",
    "NONCE_TIMESTAMP_TOLERANCE_SECONDS",
]
