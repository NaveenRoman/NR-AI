"""
Desktop Shell Lifecycle State Machine for NR-AI.
Manages transition integrity, connection status, bounded retry loops, and clean shutdown.

Invariants:
- Deterministic finite state machine: STOPPED, STARTING, RUNNING, RECONNECTING, CLOSING.
- Transition matrix enforced; invalid transitions raise LifecycleTransitionError.
- Bounded reconnection retries to prevent runaway connection loops.
"""

from __future__ import annotations

import logging
import threading
import time
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("NRAI.Desktop.Lifecycle")


class DesktopLifecycleState(str, Enum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    RECONNECTING = "RECONNECTING"
    CLOSING = "CLOSING"


class LifecycleTransitionError(Exception):
    """Raised when an illegal lifecycle state transition is requested."""
    pass


class DesktopLifecycleManager:
    """
    Thread-safe manager for desktop shell lifecycle and connection state.
    """

    VALID_TRANSITIONS: Dict[DesktopLifecycleState, List[DesktopLifecycleState]] = {
        DesktopLifecycleState.STOPPED: [DesktopLifecycleState.STARTING],
        DesktopLifecycleState.STARTING: [DesktopLifecycleState.RUNNING, DesktopLifecycleState.STOPPED, DesktopLifecycleState.CLOSING],
        DesktopLifecycleState.RUNNING: [DesktopLifecycleState.RECONNECTING, DesktopLifecycleState.CLOSING, DesktopLifecycleState.STOPPED],
        DesktopLifecycleState.RECONNECTING: [DesktopLifecycleState.RUNNING, DesktopLifecycleState.CLOSING, DesktopLifecycleState.STOPPED],
        DesktopLifecycleState.CLOSING: [DesktopLifecycleState.STOPPED],
    }

    def __init__(self, max_reconnect_attempts: int = 5, backoff_base_sec: float = 0.5) -> None:
        self._state: DesktopLifecycleState = DesktopLifecycleState.STOPPED
        self._lock = threading.RLock()
        self._max_reconnect_attempts = max_reconnect_attempts
        self._backoff_base_sec = backoff_base_sec
        self._reconnect_attempts = 0
        self._state_listeners: List[Callable[[DesktopLifecycleState, DesktopLifecycleState], None]] = []
        self._started_at: Optional[float] = None
        self._last_state_change: float = time.time()

    @property
    def current_state(self) -> DesktopLifecycleState:
        with self._lock:
            return self._state

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._state == DesktopLifecycleState.RUNNING

    @property
    def uptime_seconds(self) -> float:
        with self._lock:
            if self._started_at is None or self._state == DesktopLifecycleState.STOPPED:
                return 0.0
            return max(0.0, time.time() - self._started_at)

    def add_state_listener(self, listener: Callable[[DesktopLifecycleState, DesktopLifecycleState], None]) -> None:
        with self._lock:
            if listener not in self._state_listeners:
                self._state_listeners.append(listener)

    def remove_state_listener(self, listener: Callable[[DesktopLifecycleState, DesktopLifecycleState], None]) -> None:
        with self._lock:
            if listener in self._state_listeners:
                self._state_listeners.remove(listener)

    def _transition_to(self, new_state: DesktopLifecycleState, reason: str = "") -> None:
        with self._lock:
            old_state = self._state
            if old_state == new_state:
                return

            allowed = self.VALID_TRANSITIONS.get(old_state, [])
            if new_state not in allowed:
                msg = f"Illegal transition from {old_state.value} to {new_state.value} (reason: {reason})"
                logger.error(msg)
                raise LifecycleTransitionError(msg)

            self._state = new_state
            self._last_state_change = time.time()
            if new_state == DesktopLifecycleState.RUNNING and old_state != DesktopLifecycleState.RECONNECTING:
                self._started_at = time.time()
                self._reconnect_attempts = 0
            elif new_state == DesktopLifecycleState.STOPPED:
                self._started_at = None
                self._reconnect_attempts = 0

            logger.info("Desktop lifecycle state changed: %s -> %s (%s)", old_state.value, new_state.value, reason)
            listeners = list(self._state_listeners)

        for listener in listeners:
            try:
                listener(old_state, new_state)
            except Exception as exc:
                logger.error("Error in state transition listener: %s", exc, exc_info=True)

    def start(self) -> None:
        """Begin starting the desktop shell."""
        with self._lock:
            if self._state == DesktopLifecycleState.RUNNING:
                return
            self._transition_to(DesktopLifecycleState.STARTING, "desktop_start_requested")
            self._transition_to(DesktopLifecycleState.RUNNING, "desktop_started_successfully")

    def handle_disconnect(self, reason: str = "backend_unreachable") -> bool:
        """
        Handle a connection loss. Attempts bounded reconnection transition.
        Returns True if reconnection is allowed, False if max attempts exceeded.
        """
        with self._lock:
            if self._state not in (DesktopLifecycleState.RUNNING, DesktopLifecycleState.STARTING, DesktopLifecycleState.RECONNECTING):
                return False

            self._reconnect_attempts += 1
            if self._reconnect_attempts > self._max_reconnect_attempts:
                logger.warning("Max reconnect attempts (%d) reached. Initiating shutdown.", self._max_reconnect_attempts)
                self.close(f"max_reconnect_attempts_exceeded: {reason}")
                return False

            self._transition_to(DesktopLifecycleState.RECONNECTING, f"attempt_{self._reconnect_attempts}:{reason}")
            return True

    def handle_reconnected(self) -> None:
        """Mark reconnection successful."""
        with self._lock:
            if self._state == DesktopLifecycleState.RECONNECTING:
                self._reconnect_attempts = 0
                self._transition_to(DesktopLifecycleState.RUNNING, "reconnected_successfully")

    def close(self, reason: str = "normal_shutdown") -> None:
        """Cleanly close and stop the desktop shell."""
        with self._lock:
            if self._state == DesktopLifecycleState.STOPPED:
                return
            if self._state != DesktopLifecycleState.CLOSING:
                try:
                    self._transition_to(DesktopLifecycleState.CLOSING, reason)
                except LifecycleTransitionError:
                    pass
            self._transition_to(DesktopLifecycleState.STOPPED, reason)

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "state": self._state.value,
                "uptime_seconds": round(self.uptime_seconds, 2),
                "reconnect_attempts": self._reconnect_attempts,
                "max_reconnect_attempts": self._max_reconnect_attempts,
                "last_state_change": self._last_state_change,
            }
