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
