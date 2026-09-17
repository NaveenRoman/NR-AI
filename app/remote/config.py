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
