"""
NR-AI Remote Transport & Security Configuration.
Step 10 Phase 1 — Secure Phone <-> PC Communication Foundation.
"""

from typing import List

# Network & Binding Constraints
DEFAULT_HOST: str = "127.0.0.1"
DEFAULT_PORT: int = 8585
ALLOWED_HOSTS: List[str] = ["127.0.0.1", "localhost"]
PROHIBITED_HOSTS: List[str] = ["0.0.0.0", "*", ""]

# Request Size Boundaries (Bytes)
MAX_REQUEST_BYTES: int = 102400   # 100 KB hard limit for entire HTTP body
MAX_PAYLOAD_BYTES: int = 65536    # 64 KB limit for JSON payload field

# Session & Authentication Bounds
DEFAULT_SESSION_TTL_SECONDS: int = 3600    # 1 hour default session lifetime
MAX_SESSION_TTL_SECONDS: int = 86400       # 24 hours maximum allowable TTL
MIN_SESSION_TTL_SECONDS: int = 60          # 1 minute minimum TTL
NONCE_TIMESTAMP_TOLERANCE_SECONDS: int = 60 # +/- 60 seconds replay tolerance

# Rate Limiting & Lockout Bounds
RATE_LIMIT_RPS: float = 10.0      # 10 requests / second nominal
RATE_LIMIT_BURST: int = 15        # Burst ceiling of 15 requests
MAX_FAILED_AUTH_ATTEMPTS: int = 5 # 5 failed attempts before lockout
AUTH_LOCKOUT_SECONDS: int = 300   # 5 minute lockout after repeated failures

# Pairing Bounds
PAIRING_CODE_TTL_SECONDS: int = 300 # 5 minutes for pairing OTP code
MAX_PAIRING_ATTEMPTS: int = 5       # Max 5 wrong pairing attempts before OTP invalidation
PAIRING_CODE_LENGTH: int = 6        # 6-digit numeric OTP

# Concurrency Bounds
MAX_CONCURRENT_SESSIONS_PER_DEVICE: int = 2
MAX_GLOBAL_ACTIVE_SESSIONS: int = 10

# Secret Lengths (Bytes)
DEVICE_SECRET_BYTES: int = 32 # 256 bits
SESSION_KEY_BYTES: int = 32   # 256 bits (AES-256)
AES_GCM_NONCE_BYTES: int = 12 # 96 bits for AES-GCM IV
AES_GCM_TAG_BYTES: int = 16   # 128 bits auth tag

# Step 10 Phase 2 — Telemetry & Streaming Bounds
MAX_TELEMETRY_EVENT_BYTES: int = 32768   # 32 KB limit per telemetry event
MAX_TELEMETRY_PAYLOAD_BYTES: int = 16384 # 16 KB limit for event payload
MAX_FRAME_WIDTH: int = 1920              # Max horizontal resolution
MAX_FRAME_HEIGHT: int = 1080             # Max vertical resolution
MAX_FRAME_BYTES: int = 524288            # 512 KB limit per compressed frame
DEFAULT_FPS: float = 10.0                # Default streaming framerate
MAX_FRAMES_PER_SECOND: float = 30.0      # Maximum allowable FPS
MIN_FRAMES_PER_SECOND: float = 1.0       # Minimum allowable FPS
MAX_QUEUED_FRAMES: int = 5               # Maximum frames held in backpressure queue
MAX_QUEUE_BYTES: int = 2097152           # 2 MB total memory ceiling for pending frames
MAX_STREAM_LIFETIME_SECONDS: int = 7200  # 2 hours maximum stream lifetime
STREAM_INACTIVITY_TIMEOUT_SECONDS: int = 30 # 30s before stream marked reconnecting/stale
MAX_RECONNECT_ATTEMPTS: int = 3          # Max 3 reconnect attempts before stream termination
MAX_RECONNECT_WINDOW_SECONDS: int = 60   # 60s window to reconnect
MAX_CONCURRENT_STREAMS_PER_DEVICE: int = 1 # Max 1 active screen stream per device
MAX_GLOBAL_CONCURRENT_STREAMS: int = 5    # Max 5 active screen streams across all devices

# Step 10 Phase 3 — Voice Command & Audio Pipeline Bounds
MAX_AUDIO_BYTES: int = 10485760             # 10 MB hard ceiling for audio requests
MAX_AUDIO_DURATION_SECONDS: float = 60.0    # 60 seconds maximum audio duration
MIN_AUDIO_DURATION_SECONDS: float = 0.1     # 100 milliseconds minimum audio duration
MAX_AUDIO_SAMPLE_RATE: int = 48000          # 48 kHz max sample rate
MIN_AUDIO_SAMPLE_RATE: int = 8000           # 8 kHz min sample rate
MAX_AUDIO_CHANNELS: int = 2                 # Stereo max
MIN_AUDIO_CHANNELS: int = 1                 # Mono min
MAX_AUDIO_REQUESTS_PER_MINUTE: int = 20     # Max 20 voice requests per minute per device
MAX_TRANSCRIPTION_SECONDS: float = 60.0     # 60s timeout for transcription processing
MAX_TRANSCRIPTION_ATTEMPTS: int = 2         # Maximum 2 transcription attempts per request
MAX_CONCURRENT_VOICE_SESSIONS_PER_DEVICE: int = 1 # Max 1 active voice session per device
MAX_GLOBAL_CONCURRENT_VOICE_SESSIONS: int = 5    # Max 5 active voice sessions globally
VOICE_SESSION_TTL_SECONDS: float = 120.0    # 2 minutes session lifetime
VOICE_CONFIRMATION_TIMEOUT_SECONDS: float = 30.0 # 30s window to confirm sensitive actions

# Deterministic Validation Error Codes
AUDIO_TOO_LARGE: str = "AUDIO_TOO_LARGE"
AUDIO_TOO_LONG: str = "AUDIO_TOO_LONG"
AUDIO_TOO_SHORT: str = "AUDIO_TOO_SHORT"
INVALID_SAMPLE_RATE: str = "INVALID_SAMPLE_RATE"
INVALID_CHANNEL_COUNT: str = "INVALID_CHANNEL_COUNT"
UNSUPPORTED_AUDIO_FORMAT: str = "UNSUPPORTED_AUDIO_FORMAT"
EMPTY_AUDIO: str = "EMPTY_AUDIO"
INVALID_AUDIO_REQUEST: str = "INVALID_AUDIO_REQUEST"
AUDIO_RATE_LIMITED: str = "AUDIO_RATE_LIMITED"
TRANSCRIPTION_TIMEOUT: str = "TRANSCRIPTION_TIMEOUT"
TRANSCRIPTION_FAILED: str = "TRANSCRIPTION_FAILED"
VOICE_SESSION_EXPIRED: str = "VOICE_SESSION_EXPIRED"
VOICE_PERMISSION_DENIED: str = "VOICE_PERMISSION_DENIED"
AUDIO_CHECKSUM_MISMATCH: str = "AUDIO_CHECKSUM_MISMATCH"
AUDIO_REPLAY_DETECTED: str = "AUDIO_REPLAY_DETECTED"
SPEECH_TO_TEXT_UNAVAILABLE: str = "SPEECH_TO_TEXT_UNAVAILABLE"

# Step 10 Phase 4 — Scoped Remote Actions & Computer Control Safety Bounds
MAX_REMOTE_ACTION_PAYLOAD_BYTES: int = 65536     # 64 KB limit for remote action payload
MAX_REMOTE_ACTION_TIMEOUT_SECONDS: float = 30.0  # 30 seconds execution timeout
REMOTE_CONFIRMATION_TIMEOUT_SECONDS: float = 30.0 # 30 seconds confirmation timeout
REMOTE_ACTION_TTL_SECONDS: float = 120.0         # 2 minutes session lifetime
MAX_CONCURRENT_REMOTE_ACTIONS_PER_DEVICE: int = 1 # Max 1 active remote action per device
MAX_GLOBAL_CONCURRENT_REMOTE_ACTIONS: int = 5    # Max 5 active remote actions globally
MAX_REMOTE_ACTION_RATE_PER_MINUTE: int = 30      # Max 30 actions per minute per device
REMOTE_TARGET_TTL_SECONDS: float = 15.0          # 15 seconds target freshness TTL

# Step 10 Phase 4 — Deterministic Error Taxonomy
REMOTE_AUTH_REQUIRED: str = "REMOTE_AUTH_REQUIRED"
REMOTE_PERMISSION_DENIED: str = "REMOTE_PERMISSION_DENIED"
REMOTE_ACTION_NOT_ALLOWED: str = "REMOTE_ACTION_NOT_ALLOWED"
REMOTE_ACTION_MALFORMED: str = "REMOTE_ACTION_MALFORMED"
REMOTE_ACTION_EXPIRED: str = "REMOTE_ACTION_EXPIRED"
REMOTE_ACTION_REPLAYED: str = "REMOTE_ACTION_REPLAYED"
REMOTE_CONFIRMATION_REQUIRED: str = "REMOTE_CONFIRMATION_REQUIRED"
REMOTE_CONFIRMATION_INVALID: str = "REMOTE_CONFIRMATION_INVALID"
REMOTE_CONFIRMATION_EXPIRED: str = "REMOTE_CONFIRMATION_EXPIRED"
REMOTE_TARGET_STALE: str = "REMOTE_TARGET_STALE"
REMOTE_TARGET_INVALID: str = "REMOTE_TARGET_INVALID"
REMOTE_RATE_LIMITED: str = "REMOTE_RATE_LIMITED"
REMOTE_CONCURRENCY_LIMIT: str = "REMOTE_CONCURRENCY_LIMIT"
REMOTE_SAFETY_REJECTED: str = "REMOTE_SAFETY_REJECTED"
REMOTE_EXECUTION_FAILED: str = "REMOTE_EXECUTION_FAILED"
REMOTE_VERIFICATION_FAILED: str = "REMOTE_VERIFICATION_FAILED"
REMOTE_STOPPED: str = "REMOTE_STOPPED"
REMOTE_SESSION_EXPIRED: str = "REMOTE_SESSION_EXPIRED"

# ==============================================================================
# Step 10 Phase 5: Companion Orchestrator & Resilience Bounds
# ==============================================================================
HEARTBEAT_INTERVAL_SECONDS: float = 10.0
CONNECTION_TIMEOUT_SECONDS: float = 30.0
MAX_RECONNECT_ATTEMPTS: int = 3
RECONNECT_WINDOW_SECONDS: float = 60.0
RECONNECT_GRACE_PERIOD_SECONDS: float = 60.0
MAX_TTS_RESPONSE_CHARS: int = 400
COMPANION_ORCHESTRATOR_TIMEOUT_SECONDS: float = 30.0

# Phase 5 Error Taxonomy
COMPANION_RECONNECT_EXCEEDED: str = "COMPANION_RECONNECT_EXCEEDED"
COMPANION_HEARTBEAT_TIMEOUT: str = "COMPANION_HEARTBEAT_TIMEOUT"
COMPANION_DEGRADED_MODE: str = "COMPANION_DEGRADED_MODE"
COMPANION_INVALID_STATE: str = "COMPANION_INVALID_STATE"
