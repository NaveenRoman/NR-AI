"""
NR-AI Companion Resilience & Disconnect Handling Engine.
Step 10 Phase 5 — Autonomous Mobile Companion UX, End-to-End Orchestration & Polish.

Manages connection health, heartbeat tracking, bounded reconnect attempts,
exponential backoff, graceful subsystem degradation, and clean teardown.
"""

from dataclasses import dataclass, field
import logging
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from app.remote.config import (
    COMPANION_HEARTBEAT_TIMEOUT,
    COMPANION_RECONNECT_EXCEEDED,
    CONNECTION_TIMEOUT_SECONDS,
    HEARTBEAT_INTERVAL_SECONDS,
    MAX_RECONNECT_ATTEMPTS,
    RECONNECT_GRACE_PERIOD_SECONDS,
    RECONNECT_WINDOW_SECONDS,
)

logger = logging.getLogger("NRAI.CompanionResilience")


@dataclass
class DeviceConnectionRecord:
    """Tracks network health, heartbeats, and reconnect attempts for a paired device."""
    device_id: str
    last_heartbeat: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    connected: bool = True
    reconnect_timestamps: List[float] = field(default_factory=list)
    degraded_flags: Dict[str, bool] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


class CompanionResilienceManager:
    """
    Coordinates connection health, heartbeats, bounded reconnects, and graceful degradation.
    Enforces:
    - 10-second heartbeat interval
    - 30-second connection timeout
    - Max 3 reconnect attempts in 60 seconds
    - Bounded session re-attachment within 60s grace period
    - Thread-safe zero-leak resource cleanup
    """

    def __init__(
        self,
        heartbeat_interval: float = HEARTBEAT_INTERVAL_SECONDS,
        connection_timeout: float = CONNECTION_TIMEOUT_SECONDS,
        max_reconnects: int = MAX_RECONNECT_ATTEMPTS,
        reconnect_window: float = RECONNECT_WINDOW_SECONDS,
        grace_period: float = RECONNECT_GRACE_PERIOD_SECONDS,
    ):
        self.heartbeat_interval = heartbeat_interval
        self.connection_timeout = connection_timeout
        self.max_reconnects = max_reconnects
        self.reconnect_window = reconnect_window
        self.grace_period = grace_period

        self._devices: Dict[str, DeviceConnectionRecord] = {}
        self._lock = threading.Lock()

    def record_heartbeat(self, device_id: str, current_time: Optional[float] = None) -> bool:
        """Records an incoming heartbeat or activity ping from a mobile device."""
        now = current_time if current_time is not None else time.time()
        with self._lock:
            rec = self._devices.get(device_id)
            if not rec:
                rec = DeviceConnectionRecord(device_id=device_id, last_heartbeat=now, last_active=now, connected=True)
                self._devices[device_id] = rec
            else:
                rec.last_heartbeat = now
                rec.last_active = now
                rec.connected = True
            return True

    def check_connection_health(
        self,
        device_id: str,
        current_time: Optional[float] = None,
    ) -> Tuple[bool, str]:
        """
        Evaluates whether a device's connection is active or timed out.
        Returns (is_healthy, status_code).
        """
        now = current_time if current_time is not None else time.time()
        with self._lock:
            rec = self._devices.get(device_id)
            if not rec:
                return False, "DEVICE_NOT_TRACKED"

            elapsed = now - rec.last_heartbeat
            if elapsed > self.connection_timeout:
                rec.connected = False
                return False, COMPANION_HEARTBEAT_TIMEOUT

            return True, "CONNECTED"

    def can_reconnect(
        self,
        device_id: str,
        current_time: Optional[float] = None,
    ) -> Tuple[bool, str, float]:
        """
        Checks if the device is permitted to reconnect under rate limits and backoff rules.
        Returns (can_reconnect, message, suggested_backoff_seconds).
        """
        now = current_time if current_time is not None else time.time()
        with self._lock:
            rec = self._devices.get(device_id)
            if not rec:
                return True, "Initial connection", 0.0

            # Prune reconnect timestamps outside window
            rec.reconnect_timestamps = [
                ts for ts in rec.reconnect_timestamps
                if now - ts <= self.reconnect_window
            ]

            if len(rec.reconnect_timestamps) >= self.max_reconnects:
                return (
                    False,
                    f"Reconnect rate limit reached: {len(rec.reconnect_timestamps)} attempts in {self.reconnect_window}s.",
                    self.reconnect_window,
                )

            # Exponential backoff based on recent attempts (1s, 2s, 4s...)
            attempts = len(rec.reconnect_timestamps)
            backoff = min(30.0, float(2 ** attempts))
            return True, "Reconnect permitted", backoff

    def record_reconnect(self, device_id: str, current_time: Optional[float] = None) -> None:
        """Registers a reconnect event for the specified device."""
        now = current_time if current_time is not None else time.time()
        with self._lock:
            rec = self._devices.get(device_id)
            if not rec:
                rec = DeviceConnectionRecord(device_id=device_id, last_heartbeat=now, last_active=now, connected=True)
                self._devices[device_id] = rec
            rec.reconnect_timestamps.append(now)
            rec.last_active = now
            rec.connected = True

    def can_reattach_session(
        self,
        session_expires_at: float,
        last_active: float,
        current_time: Optional[float] = None,
    ) -> Tuple[bool, str]:
        """
        Verifies whether an existing session can be safely reattached upon client reconnect.
        Guarantees that expired sessions or sessions disconnected beyond grace period are rejected.
        """
        now = current_time if current_time is not None else time.time()

        # Session absolute expiry
        if now > session_expires_at:
            return False, "Session has expired."

        # Disconnect grace period (max 60s since last active activity)
        if now - last_active > self.grace_period:
            return False, f"Reconnect grace period ({self.grace_period}s) exceeded. New authentication required."

        return True, "Session re-attachment permitted."

    def set_degraded_flag(self, device_id: str, component: str, degraded: bool) -> None:
        """Marks a subsystem (e.g. 'screen_stream', 'stt') as degraded for a specific device."""
        with self._lock:
            rec = self._devices.get(device_id)
            if not rec:
                rec = DeviceConnectionRecord(device_id=device_id)
                self._devices[device_id] = rec
            rec.degraded_flags[component] = degraded

    def is_degraded(self, device_id: str, component: str) -> bool:
        """Returns True if the specified component is operating in degraded mode."""
        with self._lock:
            rec = self._devices.get(device_id)
            if not rec:
                return False
            return rec.degraded_flags.get(component, False)

    def disconnect_device(self, device_id: str) -> None:
        """Marks device as disconnected cleanly."""
        with self._lock:
            rec = self._devices.get(device_id)
            if rec:
                rec.connected = False

    def cleanup_device(self, device_id: str) -> None:
        """Removes tracking state for a device."""
        with self._lock:
            self._devices.pop(device_id, None)

    def reset(self) -> None:
        """Resets all tracking records."""
        with self._lock:
            self._devices.clear()
