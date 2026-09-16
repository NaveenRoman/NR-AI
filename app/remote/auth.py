"""
NR-AI Session Management, Authentication & Replay Protection.
Step 10 Phase 1 — Secure Phone <-> PC Communication Foundation.
"""

from dataclasses import dataclass, field
import hashlib
import hmac
import secrets
import threading
import time
from typing import Dict, Optional, Set, Tuple

from app.remote.config import (
    DEFAULT_SESSION_TTL_SECONDS,
    MAX_CONCURRENT_SESSIONS_PER_DEVICE,
    MAX_GLOBAL_ACTIVE_SESSIONS,
    MAX_SESSION_TTL_SECONDS,
    MIN_SESSION_TTL_SECONDS,
    NONCE_TIMESTAMP_TOLERANCE_SECONDS,
    SESSION_KEY_BYTES,
)
from app.remote.identity import PairingManager, PairingState
from app.remote.permissions import DEFAULT_COMPANION_SCOPES, PhonePermissionScope
from app.remote.protocol import SecureRequest


@dataclass
class Session:
    session_id: str
    device_id: str
    session_token: str
    session_key_hex: str  # Hex key for session encryption / authenticated framing
    created_at: float
    expires_at: float
    last_activity_at: float
    scopes: Set[PhonePermissionScope] = field(default_factory=lambda: set(DEFAULT_COMPANION_SCOPES))

    def is_expired(self, current_time: Optional[float] = None) -> bool:
        now = current_time if current_time is not None else time.time()
        return now >= self.expires_at

    def touch(self, current_time: Optional[float] = None) -> None:
        now = current_time if current_time is not None else time.time()
        self.last_activity_at = now


class SessionManager:
    """
    Manages authenticated sessions, token verification, session lifecycle,
    HMAC request signatures, and nonce-based replay protection.
    """

    def __init__(self, pairing_manager: PairingManager):
        self.pairing_manager = pairing_manager
        self._sessions: Dict[str, Session] = {}  # session_id -> Session
        # Nonce replay cache: (device_id, nonce) -> timestamp
        self._seen_nonces: Dict[Tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def create_session(
        self,
        device_id: str,
        ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
        scopes: Optional[Set[PhonePermissionScope]] = None,
    ) -> Tuple[bool, str, Optional[Session]]:
        """
        Create a new authenticated session for a paired device.
        """
        with self._lock:
            # 1. Check device existence and pairing state
            dev = self.pairing_manager.get_device(device_id)
            if not dev:
                return False, "DEVICE_NOT_FOUND", None

            if dev.state == PairingState.REVOKED:
                return False, "DEVICE_REVOKED", None

            if dev.state != PairingState.PAIRED:
                return False, "DEVICE_NOT_PAIRED", None

            # 2. Cleanup expired sessions first
            self._cleanup_expired_locked()

            # 3. Check global session bound
            if len(self._sessions) >= MAX_GLOBAL_ACTIVE_SESSIONS:
                return False, "GLOBAL_SESSION_LIMIT_REACHED", None

            # 4. Check per-device concurrent session bound
            device_sessions = [s for s in self._sessions.values() if s.device_id == device_id]
            if len(device_sessions) >= MAX_CONCURRENT_SESSIONS_PER_DEVICE:
                # Evict oldest session for this device
                device_sessions.sort(key=lambda s: s.created_at)
                oldest = device_sessions[0]
                self._sessions.pop(oldest.session_id, None)

            # 5. Bound TTL
            clamped_ttl = max(MIN_SESSION_TTL_SECONDS, min(ttl_seconds, MAX_SESSION_TTL_SECONDS))
            now = time.time()

            session_id = f"SES-{secrets.token_hex(12).upper()}"
            session_token = secrets.token_hex(32)
            session_key_hex = secrets.token_hex(SESSION_KEY_BYTES)

            session = Session(
                session_id=session_id,
                device_id=device_id,
                session_token=session_token,
                session_key_hex=session_key_hex,
                created_at=now,
                expires_at=now + clamped_ttl,
                last_activity_at=now,
                scopes=scopes if scopes is not None else set(DEFAULT_COMPANION_SCOPES),
            )
            self._sessions[session_id] = session
            return True, "SESSION_CREATED", session

    def validate_session(self, session_id: str, device_id: str) -> Tuple[bool, str, Optional[Session]]:
        """
        Validate active session existence, matching device, and expiration.
        """
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return False, "SESSION_NOT_FOUND", None

            if session.device_id != device_id:
                return False, "SESSION_DEVICE_MISMATCH", None

            # Verify device is still paired and not revoked
            if self.pairing_manager.is_device_revoked(device_id):
                # Clean up session immediately
                self._sessions.pop(session_id, None)
                return False, "DEVICE_REVOKED", None

            now = time.time()
            if session.is_expired(now):
                self._sessions.pop(session_id, None)
                return False, "SESSION_EXPIRED", None

            session.touch(now)
            return True, "SESSION_VALID", session

    def verify_request_signature(
        self,
        request: SecureRequest,
    ) -> Tuple[bool, str]:
        """
        Verify request HMAC-SHA256 signature and replay protection.
        Signature must match HMAC(device_shared_secret, canonical_string).
        """
        # 1. Fetch device secret
        secret_hex = self.pairing_manager.get_device_secret(request.device_id)
        if not secret_hex:
            if self.pairing_manager.is_device_revoked(request.device_id):
                return False, "DEVICE_REVOKED"
            return False, "DEVICE_SECRET_NOT_FOUND"

        # 2. Check timestamp window
        now = time.time()
        time_delta = abs(now - request.timestamp)
        if time_delta > NONCE_TIMESTAMP_TOLERANCE_SECONDS:
            return False, f"REQUEST_EXPIRED_OR_DRIFTED: drift {time_delta:.1f}s exceeds tolerance {NONCE_TIMESTAMP_TOLERANCE_SECONDS}s"

        with self._lock:
            # 3. Check replay nonce
            nonce_key = (request.device_id, request.nonce)
            if nonce_key in self._seen_nonces:
                return False, f"REPLAY_DETECTED: nonce '{request.nonce}' already used"

            # 4. Verify HMAC signature
            canonical_str = request.compute_canonical_string()
            secret_bytes = bytes.fromhex(secret_hex)
            expected_sig = hmac.new(
                secret_bytes,
                canonical_str.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()

            if not hmac.compare_digest(expected_sig.lower(), request.signature.lower()):
                return False, "INVALID_SIGNATURE"

            # 5. Record nonce in cache
            self._seen_nonces[nonce_key] = now
            self._cleanup_nonces_locked(now)

            return True, "SIGNATURE_VALID"

    def revoke_device_sessions(self, device_id: str) -> int:
        """
        Revoke and terminate all active sessions for a specified device.
        """
        with self._lock:
            to_delete = [
                sid for sid, sess in self._sessions.items() if sess.device_id == device_id
            ]
            for sid in to_delete:
                del self._sessions[sid]
            return len(to_delete)

    def cleanup_expired(self) -> int:
        with self._lock:
            return self._cleanup_expired_locked()

    def _cleanup_expired_locked(self) -> int:
        now = time.time()
        expired_ids = [
            sid for sid, sess in self._sessions.items() if sess.is_expired(now)
        ]
        for sid in expired_ids:
            del self._sessions[sid]
        self._cleanup_nonces_locked(now)
        return len(expired_ids)

    def _cleanup_nonces_locked(self, current_time: float) -> None:
        """Purge nonces older than 2x the tolerance window."""
        cutoff = current_time - (NONCE_TIMESTAMP_TOLERANCE_SECONDS * 2)
        expired_keys = [k for k, ts in self._seen_nonces.items() if ts < cutoff]
        for k in expired_keys:
            del self._seen_nonces[k]

    def get_active_session_count(self) -> int:
        with self._lock:
            return len(self._sessions)
