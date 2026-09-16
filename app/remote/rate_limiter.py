"""
NR-AI Rate Limiting & Authentication Lockout Engine.
Step 10 Phase 1 — Secure Phone <-> PC Communication Foundation.
"""

from dataclasses import dataclass
import threading
import time
from typing import Dict, Optional, Tuple

from app.remote.config import (
    AUTH_LOCKOUT_SECONDS,
    MAX_FAILED_AUTH_ATTEMPTS,
    RATE_LIMIT_BURST,
    RATE_LIMIT_RPS,
)


@dataclass
class TokenBucket:
    capacity: float
    tokens: float
    fill_rate: float
    last_update: float

    def consume(self, amount: float = 1.0, current_time: Optional[float] = None) -> bool:
        now = current_time if current_time is not None else time.time()
        elapsed = max(0.0, now - self.last_update)
        self.last_update = now
        # Replenish tokens based on elapsed time
        self.tokens = min(self.capacity, self.tokens + (elapsed * self.fill_rate))
        if self.tokens >= amount:
            self.tokens -= amount
            return True
        return False


@dataclass
class LockoutRecord:
    failure_count: int
    locked_until: Optional[float] = None


class RateLimiter:
    """
    Thread-safe per-client token-bucket rate limiter with authentication failure tracking
    and temporary lockout enforcement.
    """

    def __init__(
        self,
        rate_limit_rps: float = RATE_LIMIT_RPS,
        burst_limit: int = RATE_LIMIT_BURST,
        max_auth_failures: int = MAX_FAILED_AUTH_ATTEMPTS,
        lockout_duration_seconds: int = AUTH_LOCKOUT_SECONDS,
    ):
        self.rps = rate_limit_rps
        self.burst = burst_limit
        self.max_auth_failures = max_auth_failures
        self.lockout_duration = lockout_duration_seconds

        self._buckets: Dict[str, TokenBucket] = {}
        self._auth_records: Dict[str, LockoutRecord] = {}
        self._lock = threading.Lock()

    def allow_request(
        self,
        client_id: str,
        current_time: Optional[float] = None,
    ) -> Tuple[bool, str, Optional[float]]:
        """
        Check if an incoming request from client_id is allowed under rate limits.
        Returns (allowed, reason, retry_after_seconds).
        """
        now = current_time if current_time is not None else time.time()
        with self._lock:
            # 1. Check if client is locked out due to auth failures
            auth_rec = self._auth_records.get(client_id)
            if auth_rec and auth_rec.locked_until:
                if now < auth_rec.locked_until:
                    retry_after = auth_rec.locked_until - now
                    return False, f"AUTH_LOCKOUT_ACTIVE: try again in {retry_after:.1f}s", retry_after
                else:
                    # Lockout expired, reset record
                    auth_rec.locked_until = None
                    auth_rec.failure_count = 0

            # 2. Token bucket check
            bucket = self._buckets.get(client_id)
            if not bucket:
                bucket = TokenBucket(
                    capacity=float(self.burst),
                    tokens=float(self.burst),
                    fill_rate=float(self.rps),
                    last_update=now,
                )
                self._buckets[client_id] = bucket

            if bucket.consume(1.0, current_time=now):
                return True, "ALLOWED", None

            # Calculate retry-after time
            needed = 1.0 - bucket.tokens
            retry_after = max(0.1, needed / bucket.fill_rate)
            return False, f"RATE_LIMIT_EXCEEDED: maximum {self.rps} req/sec", retry_after

    def record_auth_failure(
        self,
        client_id: str,
        current_time: Optional[float] = None,
    ) -> Tuple[bool, int, Optional[float]]:
        """
        Record a failed authentication attempt.
        Returns (is_now_locked_out, total_failures, locked_until_timestamp).
        """
        now = current_time if current_time is not None else time.time()
        with self._lock:
            rec = self._auth_records.get(client_id)
            if not rec:
                rec = LockoutRecord(failure_count=0)
                self._auth_records[client_id] = rec

            rec.failure_count += 1
            if rec.failure_count >= self.max_auth_failures:
                rec.locked_until = now + self.lockout_duration
                return True, rec.failure_count, rec.locked_until

            return False, rec.failure_count, None

    def record_auth_success(self, client_id: str) -> None:
        """
        Reset auth failure counter upon successful authentication.
        """
        with self._lock:
            if client_id in self._auth_records:
                del self._auth_records[client_id]

    def is_locked_out(
        self,
        client_id: str,
        current_time: Optional[float] = None,
    ) -> Tuple[bool, Optional[float]]:
        now = current_time if current_time is not None else time.time()
        with self._lock:
            rec = self._auth_records.get(client_id)
            if rec and rec.locked_until and now < rec.locked_until:
                return True, rec.locked_until - now
            return False, None
