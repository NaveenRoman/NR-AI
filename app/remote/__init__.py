"""
NR-AI Remote Transport & Security Foundation.
Step 10 Phase 1, 2 & 3 — Secure Phone <-> PC Communication, Telemetry, Frame Streaming & Voice Pipeline.
"""

from app.remote.audio import (
    ALLOWED_AUDIO_FORMATS,
    AudioFormat,
    AudioRequest,
    VoiceResponse,
    create_audio_request,
    validate_audio_request,
)
from app.remote.audit import AuditRecord, SecurityAuditLogger, redact_sensitive_data
from app.remote.auth import Session, SessionManager
from app.remote.config import (
    ALLOWED_HOSTS,
    AUDIO_CHECKSUM_MISMATCH,
    AUDIO_RATE_LIMITED,
    AUDIO_REPLAY_DETECTED,
    AUDIO_TOO_LARGE,
    AUDIO_TOO_LONG,
    AUDIO_TOO_SHORT,
    DEFAULT_FPS,
    DEFAULT_HOST,
    DEFAULT_PORT,
    EMPTY_AUDIO,
    INVALID_AUDIO_REQUEST,
    INVALID_CHANNEL_COUNT,
    INVALID_SAMPLE_RATE,
    MAX_AUDIO_BYTES,
    MAX_AUDIO_CHANNELS,
    MAX_AUDIO_DURATION_SECONDS,
    MAX_AUDIO_REQUESTS_PER_MINUTE,
    MAX_AUDIO_SAMPLE_RATE,
    MAX_CONCURRENT_STREAMS_PER_DEVICE,
    MAX_CONCURRENT_VOICE_SESSIONS_PER_DEVICE,
    MAX_FRAME_BYTES,
    MAX_FRAME_HEIGHT,
    MAX_FRAME_WIDTH,
    MAX_FRAMES_PER_SECOND,
    MAX_GLOBAL_CONCURRENT_STREAMS,
    MAX_GLOBAL_CONCURRENT_VOICE_SESSIONS,
    MAX_PAYLOAD_BYTES,
    MAX_QUEUE_BYTES,
    MAX_QUEUED_FRAMES,
    MAX_RECONNECT_ATTEMPTS,
    MAX_RECONNECT_WINDOW_SECONDS,
    MAX_REQUEST_BYTES,
    MAX_STREAM_LIFETIME_SECONDS,
    MAX_TELEMETRY_EVENT_BYTES,
    MAX_TELEMETRY_PAYLOAD_BYTES,
    MAX_TRANSCRIPTION_ATTEMPTS,
    MAX_TRANSCRIPTION_SECONDS,
    MIN_AUDIO_CHANNELS,
    MIN_AUDIO_DURATION_SECONDS,
    MIN_AUDIO_SAMPLE_RATE,
    MIN_FRAMES_PER_SECOND,
    NONCE_TIMESTAMP_TOLERANCE_SECONDS,
    PROHIBITED_HOSTS,
    SPEECH_TO_TEXT_UNAVAILABLE,
    STREAM_INACTIVITY_TIMEOUT_SECONDS,
    TRANSCRIPTION_FAILED,
    TRANSCRIPTION_TIMEOUT,
    UNSUPPORTED_AUDIO_FORMAT,
    VOICE_CONFIRMATION_TIMEOUT_SECONDS,
    VOICE_PERMISSION_DENIED,
    VOICE_SESSION_EXPIRED,
    VOICE_SESSION_TTL_SECONDS,
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
from app.remote.rate_limiter import RateLimiter, VoiceRateLimiter
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
from app.remote.speech_to_text import (
    DevelopmentSpeechToTextProvider,
    SpeechToTextProvider,
    WhisperSpeechToTextProvider,
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
    VoiceTelemetryEventType,
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
from app.remote.voice_intent import (
    VoiceIntent,
    VoiceIntentParser,
    VoiceIntentType,
)
from app.remote.voice_safety import (
    VoiceCommandSafetyGate,
    VoiceSafetyDecision,
)
from app.remote.voice_session import (
    VALID_VOICE_TRANSITIONS,
    VoiceCommandSession,
    VoiceSessionError,
    VoiceSessionManager,
    VoiceSessionState,
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
    "VoiceRateLimiter",
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
    # Telemetry (Phase 2 & 3)
    "TelemetryEventType",
    "VoiceTelemetryEventType",
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
    # Audio Protocol (Phase 3)
    "AudioFormat",
    "ALLOWED_AUDIO_FORMATS",
    "AudioRequest",
    "VoiceResponse",
    "create_audio_request",
    "validate_audio_request",
    # Speech to Text (Phase 3)
    "SpeechToTextProvider",
    "DevelopmentSpeechToTextProvider",
    "WhisperSpeechToTextProvider",
    # Voice Intent & Safety (Phase 3)
    "VoiceIntentType",
    "VoiceIntent",
    "VoiceIntentParser",
    "VoiceSafetyDecision",
    "VoiceCommandSafetyGate",
    # Voice Session (Phase 3)
    "VoiceSessionState",
    "VoiceSessionError",
    "VALID_VOICE_TRANSITIONS",
    "VoiceCommandSession",
    "VoiceSessionManager",
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
    "MAX_AUDIO_BYTES",
    "MAX_AUDIO_DURATION_SECONDS",
    "MIN_AUDIO_DURATION_SECONDS",
    "MAX_AUDIO_SAMPLE_RATE",
    "MIN_AUDIO_SAMPLE_RATE",
    "MAX_AUDIO_CHANNELS",
    "MIN_AUDIO_CHANNELS",
    "MAX_AUDIO_REQUESTS_PER_MINUTE",
    "MAX_TRANSCRIPTION_SECONDS",
    "MAX_TRANSCRIPTION_ATTEMPTS",
    "MAX_CONCURRENT_VOICE_SESSIONS_PER_DEVICE",
    "MAX_GLOBAL_CONCURRENT_VOICE_SESSIONS",
    "VOICE_SESSION_TTL_SECONDS",
    "VOICE_CONFIRMATION_TIMEOUT_SECONDS",
    "AUDIO_TOO_LARGE",
    "AUDIO_TOO_LONG",
    "AUDIO_TOO_SHORT",
    "INVALID_SAMPLE_RATE",
    "INVALID_CHANNEL_COUNT",
    "UNSUPPORTED_AUDIO_FORMAT",
    "EMPTY_AUDIO",
    "INVALID_AUDIO_REQUEST",
    "AUDIO_RATE_LIMITED",
    "TRANSCRIPTION_TIMEOUT",
    "TRANSCRIPTION_FAILED",
    "VOICE_SESSION_EXPIRED",
    "VOICE_PERMISSION_DENIED",
    "AUDIO_CHECKSUM_MISMATCH",
    "AUDIO_REPLAY_DETECTED",
    "SPEECH_TO_TEXT_UNAVAILABLE",
]
