"""
NR-AI Remote Transport & Security Foundation.
Step 10 Phase 1 & 2 — Secure Phone <-> PC Communication, Telemetry & Frame Streaming.
"""

from app.remote.audit import AuditRecord, SecurityAuditLogger, redact_sensitive_data
from app.remote.auth import Session, SessionManager
from app.remote.config import (
    ALLOWED_HOSTS,
    DEFAULT_FPS,
    DEFAULT_HOST,
    DEFAULT_PORT,
    MAX_CONCURRENT_STREAMS_PER_DEVICE,
    MAX_FRAME_BYTES,
    MAX_FRAME_HEIGHT,
    MAX_FRAME_WIDTH,
    MAX_FRAMES_PER_SECOND,
    MAX_GLOBAL_CONCURRENT_STREAMS,
    MAX_PAYLOAD_BYTES,
    MAX_QUEUE_BYTES,
    MAX_QUEUED_FRAMES,
    MAX_RECONNECT_ATTEMPTS,
    MAX_RECONNECT_WINDOW_SECONDS,
    MAX_REQUEST_BYTES,
    MAX_STREAM_LIFETIME_SECONDS,
    MAX_TELEMETRY_EVENT_BYTES,
    MAX_TELEMETRY_PAYLOAD_BYTES,
    MIN_FRAMES_PER_SECOND,
    NONCE_TIMESTAMP_TOLERANCE_SECONDS,
    PROHIBITED_HOSTS,
    STREAM_INACTIVITY_TIMEOUT_SECONDS,
)
from app.remote.emergency import EmergencyStopController, EmergencyStopStatus
from app.remote.frame import (
    ALLOWED_FRAME_ENCODINGS,
    FrameEncoding,
    StreamFrame,
    create_stream_frame,
    validate_frame,
)
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
from app.remote.screen_capture import (
    LocalScreenCaptureEngine,
    MockScreenCaptureEngine,
    ScreenCaptureEngine,
)
from app.remote.server import (
    SecureDashboardServer,
    SecureGateway,
    SecurityBindingError,
)
from app.remote.stream import (
    StreamBackpressureQueue,
    StreamManager,
    StreamSession,
    StreamState,
    StreamStateError,
    VALID_STREAM_TRANSITIONS,
)
from app.remote.telemetry import (
    TelemetryDispatcher,
    TelemetryEvent,
    TelemetryEventType,
    parse_and_validate_telemetry_event,
    validate_telemetry_event,
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
    # Telemetry (Phase 2)
    "TelemetryEventType",
    "TelemetryEvent",
    "TelemetryDispatcher",
    "parse_and_validate_telemetry_event",
    "validate_telemetry_event",
    # Frame (Phase 2)
    "FrameEncoding",
    "ALLOWED_FRAME_ENCODINGS",
    "StreamFrame",
    "create_stream_frame",
    "validate_frame",
    # Screen Capture (Phase 2)
    "ScreenCaptureEngine",
    "MockScreenCaptureEngine",
    "LocalScreenCaptureEngine",
    # Stream Lifecycle & Backpressure (Phase 2)
    "StreamState",
    "StreamStateError",
    "VALID_STREAM_TRANSITIONS",
    "StreamBackpressureQueue",
    "StreamSession",
    "StreamManager",
    # Constants
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "ALLOWED_HOSTS",
    "PROHIBITED_HOSTS",
    "MAX_REQUEST_BYTES",
    "MAX_PAYLOAD_BYTES",
    "NONCE_TIMESTAMP_TOLERANCE_SECONDS",
    "MAX_TELEMETRY_EVENT_BYTES",
    "MAX_TELEMETRY_PAYLOAD_BYTES",
    "MAX_FRAME_WIDTH",
    "MAX_FRAME_HEIGHT",
    "MAX_FRAME_BYTES",
    "DEFAULT_FPS",
    "MAX_FRAMES_PER_SECOND",
    "MIN_FRAMES_PER_SECOND",
    "MAX_QUEUED_FRAMES",
    "MAX_QUEUE_BYTES",
    "MAX_STREAM_LIFETIME_SECONDS",
    "STREAM_INACTIVITY_TIMEOUT_SECONDS",
    "MAX_RECONNECT_ATTEMPTS",
    "MAX_RECONNECT_WINDOW_SECONDS",
    "MAX_CONCURRENT_STREAMS_PER_DEVICE",
    "MAX_GLOBAL_CONCURRENT_STREAMS",
]
