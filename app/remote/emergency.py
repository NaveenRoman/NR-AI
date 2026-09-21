"""
NR-AI Emergency Stop Controller.
Step 10 Phase 1 — Secure Phone <-> PC Communication Foundation.

Provides a thread-safe, deterministic emergency-stop mechanism that does NOT
rely on any Large Language Model or probabilistic reasoning.
"""

from dataclasses import dataclass
import threading
import time
from typing import Callable, Dict, List, Optional


@dataclass
class EmergencyStopStatus:
    is_active: bool
    triggered_at: Optional[float]
    triggered_by: Optional[str]
    reason: Optional[str]
    callbacks_executed: int

    def to_dict(self) -> Dict[str, any]:
        return {
            "is_active": self.is_active,
            "triggered_at": self.triggered_at,
            "triggered_by": self.triggered_by,
            "reason": self.reason,
            "callbacks_executed": self.callbacks_executed,
        }


class EmergencyStopController:
    """
    Thread-safe emergency stop controller with registered cancellation hooks.
    """

    def __init__(self):
        self._is_active: bool = False
        self._triggered_at: Optional[float] = None
        self._triggered_by: Optional[str] = None
        self._reason: Optional[str] = None
        self._callbacks: List[Callable[[], None]] = []
        self._callbacks_executed: int = 0
        self._lock = threading.Lock()

    def register_cancellation_callback(self, callback: Callable[[], None]) -> None:
        """Register a callback to be invoked immediately when emergency stop fires."""
        with self._lock:
            if callback not in self._callbacks:
                self._callbacks.append(callback)

    def trigger(
        self,
        triggered_by: str = "MANUAL",
        reason: str = "Emergency stop triggered by operator",
    ) -> EmergencyStopStatus:
        """
        Immediately activate emergency stop and fire all cancellation callbacks.
        Guaranteed to be deterministic and independent of any LLM decision.
        """
        callbacks_to_run = []
        with self._lock:
            self._is_active = True
            self._triggered_at = time.time()
            self._triggered_by = str(triggered_by)
            self._reason = str(reason)
            callbacks_to_run = list(self._callbacks)

        # Run callbacks outside lock to prevent deadlocks
        executed = 0
        for cb in callbacks_to_run:
            try:
                cb()
                executed += 1
            except Exception:
                pass

        with self._lock:
            self._callbacks_executed = executed
            return self._get_status_locked()

    def reset(self, reset_by: str = "ADMIN") -> bool:
        """
        Explicitly reset emergency stop state.
        """
        with self._lock:
            self._is_active = False
            self._triggered_at = None
            self._triggered_by = None
            self._reason = None
            self._callbacks_executed = 0
            return True

    def is_active(self) -> bool:
        with self._lock:
            return self._is_active

    def is_emergency_active(self) -> bool:
        return self.is_active()

    def trigger_emergency_stop(
        self,
        reason: str = "Emergency stop triggered by operator",
        triggered_by: str = "MANUAL",
    ) -> EmergencyStopStatus:
        return self.trigger(triggered_by=triggered_by, reason=reason)

    def reset_emergency_stop(self, reset_by: str = "ADMIN") -> bool:
        return self.reset(reset_by=reset_by)

    def get_status(self) -> EmergencyStopStatus:
        with self._lock:
            return self._get_status_locked()

    def _get_status_locked(self) -> EmergencyStopStatus:
        return EmergencyStopStatus(
            is_active=self._is_active,
            triggered_at=self._triggered_at,
            triggered_by=self._triggered_by,
            reason=self._reason,
            callbacks_executed=self._callbacks_executed,
        )
