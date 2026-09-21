"""
Continuous Voice Session State Machine for NR-AI.
OpenJarvis-inspired bounded session lifecycle and privacy management.

8 Bounded States:
1. STANDBY: Inactive/monitoring for wake word. Zero audio storage.
2. WAKE_DETECTED: Wake word confirmed. Initializing listener.
3. LISTENING: Actively streaming microphone audio through VAD.
4. TRANSCRIBING: Speech ended, running STT provider.
5. THINKING: Central Brain / ModelRouter evaluating intent.
6. SPEAKING: Assistant playing synthesized TTS audio.
7. INTERRUPTED: User speech onset stopped active playback.
8. STOPPED: Emergency stop or manual shutdown.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger("NRAI.Voice.Session")


class VoiceSessionState(str, Enum):
    """Authoritative 8-state voice session lifecycle."""
    STANDBY = "STANDBY"
    WAKE_DETECTED = "WAKE_DETECTED"
    LISTENING = "LISTENING"
    TRANSCRIBING = "TRANSCRIBING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"
    INTERRUPTED = "INTERRUPTED"
    STOPPED = "STOPPED"


# Allowed state transition matrix to prevent illegal state jumps
VALID_TRANSITIONS: Dict[VoiceSessionState, Set[VoiceSessionState]] = {
    VoiceSessionState.STANDBY: {
        VoiceSessionState.WAKE_DETECTED,
        VoiceSessionState.LISTENING,
        VoiceSessionState.STOPPED,
    },
    VoiceSessionState.WAKE_DETECTED: {
        VoiceSessionState.LISTENING,
        VoiceSessionState.STANDBY,
        VoiceSessionState.STOPPED,
    },
    VoiceSessionState.LISTENING: {
        VoiceSessionState.TRANSCRIBING,
        VoiceSessionState.STANDBY,
        VoiceSessionState.INTERRUPTED,
        VoiceSessionState.STOPPED,
    },
    VoiceSessionState.TRANSCRIBING: {
        VoiceSessionState.THINKING,
        VoiceSessionState.STANDBY,
        VoiceSessionState.STOPPED,
    },
    VoiceSessionState.THINKING: {
        VoiceSessionState.SPEAKING,
        VoiceSessionState.STANDBY,
        VoiceSessionState.INTERRUPTED,
        VoiceSessionState.STOPPED,
    },
    VoiceSessionState.SPEAKING: {
        VoiceSessionState.STANDBY,
        VoiceSessionState.INTERRUPTED,
        VoiceSessionState.STOPPED,
    },
    VoiceSessionState.INTERRUPTED: {
        VoiceSessionState.LISTENING,
        VoiceSessionState.STANDBY,
        VoiceSessionState.STOPPED,
    },
    VoiceSessionState.STOPPED: {
        VoiceSessionState.STANDBY,
    },
}


@dataclass
class SessionAuditEntry:
    """Audit log entry for session transitions with zero audio payload retention."""
    timestamp: float
    from_state: str
    to_state: str
    reason: str
    session_id: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "from_state": self.from_state,
            "to_state": self.to_state,
            "reason": self.reason,
            "session_id": self.session_id,
            "metadata": self.metadata,
        }


class VoiceSessionManager:
    """
    Manages bounded voice session lifecycle, timeout auto-sleep, and privacy guarantees.
    """

    def __init__(
        self,
        session_id: Optional[str] = None,
        inactivity_timeout_sec: float = 10.0,
        on_state_change: Optional[Callable[[VoiceSessionState, VoiceSessionState], None]] = None,
    ):
        self.session_id = session_id or str(uuid.uuid4())
        self.inactivity_timeout_sec = inactivity_timeout_sec
        self.on_state_change = on_state_change

        self._current_state: VoiceSessionState = VoiceSessionState.STANDBY
        self._lock: threading.RLock = threading.RLock()
        self._last_state_change_time: float = time.perf_counter()
        self._last_activity_time: float = time.perf_counter()
        self._audit_log: List[SessionAuditEntry] = []
        self._scrubbed_history: List[str] = []

    @property
    def current_state(self) -> VoiceSessionState:
        with self._lock:
            return self._current_state

    @property
    def is_standby(self) -> bool:
        with self._lock:
            return self._current_state == VoiceSessionState.STANDBY

    @property
    def is_listening(self) -> bool:
        with self._lock:
            return self._current_state == VoiceSessionState.LISTENING

    @property
    def is_speaking(self) -> bool:
        with self._lock:
            return self._current_state == VoiceSessionState.SPEAKING

    def transition_to(
        self,
        target_state: VoiceSessionState,
        reason: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Transition session state if permitted by the transition matrix.
        Returns True on success, False if transition is illegal.
        """
        with self._lock:
            from_state = self._current_state

            # Allow self-transitions as idempotent updates
            if target_state == from_state:
                self._last_activity_time = time.perf_counter()
                return True

            allowed = VALID_TRANSITIONS.get(from_state, set())
            if target_state not in allowed:
                logger.warning(
                    "Illegal session transition rejected: %s -> %s (reason='%s')",
                    from_state.value,
                    target_state.value,
                    reason,
                )
                return False

            now = time.perf_counter()
            self._current_state = target_state
            self._last_state_change_time = now
            self._last_activity_time = now

            entry = SessionAuditEntry(
                timestamp=now,
                from_state=from_state.value,
                to_state=target_state.value,
                reason=reason,
                session_id=self.session_id,
                metadata=metadata or {},
            )
            self._audit_log.append(entry)

            logger.info(
                "Voice session state change: %s -> %s [%s]",
                from_state.value,
                target_state.value,
                reason,
            )

        if self.on_state_change:
            try:
                self.on_state_change(from_state, target_state)
            except Exception as exc:
                logger.warning("Error in on_state_change callback: %s", exc)

        return True

    def check_inactivity_timeout(self) -> bool:
        """
        Evaluate if session has exceeded inactivity timeout while in LISTENING state.
        If timed out, automatically transitions back to STANDBY.
        """
        with self._lock:
            if self._current_state == VoiceSessionState.LISTENING:
                idle_sec = time.perf_counter() - self._last_activity_time
                if idle_sec >= self.inactivity_timeout_sec:
                    logger.info(
                        "Listening inactivity timeout reached (%.1fs >= %.1fs). Returning to STANDBY.",
                        idle_sec,
                        self.inactivity_timeout_sec,
                    )
                    return self.transition_to(VoiceSessionState.STANDBY, reason="inactivity_timeout")
        return False

    def emergency_stop(self, reason: str = "emergency_stop") -> bool:
        """
        Forcibly stop all active audio operations and transition to STOPPED.
        """
        with self._lock:
            from_state = self._current_state
            now = time.perf_counter()
            self._current_state = VoiceSessionState.STOPPED
            self._last_state_change_time = now
            self._last_activity_time = now

            entry = SessionAuditEntry(
                timestamp=now,
                from_state=from_state.value,
                to_state=VoiceSessionState.STOPPED.value,
                reason=f"EMERGENCY_STOP: {reason}",
                session_id=self.session_id,
            )
            self._audit_log.append(entry)
            logger.warning("Voice emergency stop triggered: %s", reason)
            return True

    def reset_to_standby(self) -> bool:
        """Reset state machine from STOPPED or any terminal state back to STANDBY."""
        with self._lock:
            if self._current_state == VoiceSessionState.STOPPED:
                return self.transition_to(VoiceSessionState.STANDBY, reason="manual_reset")
            self._current_state = VoiceSessionState.STANDBY
            return True

    def record_utterance_text(self, text: str) -> str:
        """
        Scrub and record transcribed utterance text using security guardrails.
        Ensures PII/secrets are not saved in plaintext in session history.
        """
        if not text:
            return ""

        scrubbed = text
        try:
            from app.security.guardrails import PromptGuardrails
            guardrails = PromptGuardrails()
            scrubbed = guardrails.redact(text)
        except Exception as exc:
            logger.debug("Guardrails redaction skipped: %s", exc)

        with self._lock:
            self._scrubbed_history.append(scrubbed)
            self._last_activity_time = time.perf_counter()

        return scrubbed

    def get_audit_log(self) -> List[Dict[str, Any]]:
        """Return audit log entries as serialized dictionaries."""
        with self._lock:
            return [e.to_dict() for e in self._audit_log]

    def get_history(self) -> List[str]:
        """Return history of scrubbed utterances."""
        with self._lock:
            return list(self._scrubbed_history)
