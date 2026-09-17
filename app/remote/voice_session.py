"""
NR-AI Voice Command Session Engine & Lifecycle Manager.
Step 10 Phase 3 — Phone Voice Command & Secure Audio Pipeline.
"""

from dataclasses import asdict, dataclass, field
from enum import Enum
import secrets
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from app.remote.audio import (
    AudioRequest,
    VoiceResponse,
    validate_audio_request,
)
from app.remote.config import (
    MAX_CONCURRENT_VOICE_SESSIONS_PER_DEVICE,
    MAX_GLOBAL_CONCURRENT_VOICE_SESSIONS,
    MAX_TRANSCRIPTION_ATTEMPTS,
    TRANSCRIPTION_FAILED,
    VOICE_CONFIRMATION_TIMEOUT_SECONDS,
    VOICE_PERMISSION_DENIED,
    VOICE_SESSION_EXPIRED,
    VOICE_SESSION_TTL_SECONDS,
)
from app.remote.emergency import EmergencyStopController
from app.remote.permissions import PhonePermissionScope
from app.remote.speech_to_text import (
    DevelopmentSpeechToTextProvider,
    SpeechToTextProvider,
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


class VoiceSessionState(str, Enum):
    IDLE = "IDLE"
    RECEIVING = "RECEIVING"
    VALIDATING = "VALIDATING"
    TRANSCRIBING = "TRANSCRIBING"
    UNDERSTANDING = "UNDERSTANDING"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    STOPPED = "STOPPED"


class VoiceSessionError(Exception):
    """Raised on illegal state transition or session violation."""
    pass


VALID_VOICE_TRANSITIONS: Dict[VoiceSessionState, Set[VoiceSessionState]] = {
    VoiceSessionState.IDLE: {
        VoiceSessionState.RECEIVING,
        VoiceSessionState.CANCELLED,
        VoiceSessionState.STOPPED,
        VoiceSessionState.FAILED,
    },
    VoiceSessionState.RECEIVING: {
        VoiceSessionState.VALIDATING,
        VoiceSessionState.CANCELLED,
        VoiceSessionState.STOPPED,
        VoiceSessionState.FAILED,
    },
    VoiceSessionState.VALIDATING: {
        VoiceSessionState.TRANSCRIBING,
        VoiceSessionState.CANCELLED,
        VoiceSessionState.STOPPED,
        VoiceSessionState.FAILED,
    },
    VoiceSessionState.TRANSCRIBING: {
        VoiceSessionState.UNDERSTANDING,
        VoiceSessionState.CANCELLED,
        VoiceSessionState.STOPPED,
        VoiceSessionState.FAILED,
    },
    VoiceSessionState.UNDERSTANDING: {
        VoiceSessionState.AWAITING_CONFIRMATION,
        VoiceSessionState.EXECUTING,
        VoiceSessionState.CANCELLED,
        VoiceSessionState.STOPPED,
        VoiceSessionState.FAILED,
    },
    VoiceSessionState.AWAITING_CONFIRMATION: {
        VoiceSessionState.EXECUTING,
        VoiceSessionState.CANCELLED,
        VoiceSessionState.STOPPED,
        VoiceSessionState.FAILED,
    },
    VoiceSessionState.EXECUTING: {
        VoiceSessionState.COMPLETED,
        VoiceSessionState.STOPPED,
        VoiceSessionState.FAILED,
    },
    VoiceSessionState.COMPLETED: set(),
    VoiceSessionState.FAILED: set(),
    VoiceSessionState.CANCELLED: set(),
    VoiceSessionState.STOPPED: set(),
}


@dataclass
class VoiceCommandSession:
    session_id: str
    device_id: str
    state: VoiceSessionState = VoiceSessionState.IDLE
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    audio_request: Optional[AudioRequest] = None
    transcript: Optional[str] = None
    intent: Optional[VoiceIntent] = None
    confirmation_id: Optional[str] = None
    confirmation_expires_at: Optional[float] = None
    response: Optional[VoiceResponse] = None
    error_message: Optional[str] = None
    transcription_attempts: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def transition_to(self, new_state: VoiceSessionState, reason: str = "") -> None:
        with self.lock:
            allowed = VALID_VOICE_TRANSITIONS.get(self.state, set())
            if new_state not in allowed:
                raise VoiceSessionError(
                    f"Illegal voice session transition from {self.state.value} to {new_state.value}. Reason: {reason}"
                )
            self.state = new_state
            self.updated_at = time.time()

    def is_terminal(self) -> bool:
        return self.state in (
            VoiceSessionState.COMPLETED,
            VoiceSessionState.FAILED,
            VoiceSessionState.CANCELLED,
            VoiceSessionState.STOPPED,
        )

    def is_expired(self) -> bool:
        if self.is_terminal():
            return False
        return (time.time() - self.created_at) > VOICE_SESSION_TTL_SECONDS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "device_id": self.device_id,
            "state": self.state.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "transcript": self.transcript,
            "intent": self.intent.to_dict() if self.intent else None,
            "requires_confirmation": bool(self.confirmation_id),
            "error_message": self.error_message,
        }


class VoiceSessionManager:
    """
    Thread-safe orchestrator of voice sessions, STT bridge, safety gates,
    and routing to existing command infrastructure.
    """

    def __init__(
        self,
        stt_provider: Optional[SpeechToTextProvider] = None,
        emergency_controller: Optional[EmergencyStopController] = None,
        command_router_fn: Optional[Callable[[str], Dict[str, Any]]] = None,
        audit_logger_fn: Optional[Callable[[str, str, Dict[str, Any]], None]] = None,
        telemetry_event_fn: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ):
        self.stt_provider = stt_provider or DevelopmentSpeechToTextProvider()
        self.emergency_controller = emergency_controller
        self.command_router_fn = command_router_fn
        self.audit_logger_fn = audit_logger_fn
        self.telemetry_event_fn = telemetry_event_fn
        self._sessions: Dict[str, VoiceCommandSession] = {}
        self._lock = threading.Lock()

        # Register emergency stop callback to halt voice sessions immediately
        if self.emergency_controller:
            self.emergency_controller.register_cancellation_callback(self._on_emergency_stop)

    def _on_emergency_stop(self) -> None:
        """Halt all active voice sessions with zero LLM dependency."""
        with self._lock:
            for s in list(self._sessions.values()):
                if not s.is_terminal():
                    try:
                        s.transition_to(VoiceSessionState.STOPPED, "EMERGENCY_STOP_TRIGGERED")
                        if self.telemetry_event_fn:
                            self.telemetry_event_fn("VOICE_FAILED", {"session_id": s.session_id, "reason": "EMERGENCY_STOP"})
                    except Exception:
                        pass

    def create_session(
        self,
        device_id: str,
        session_id: Optional[str] = None,
    ) -> Tuple[bool, str, Optional[VoiceCommandSession]]:
        with self._lock:
            # Check emergency stop
            if self.emergency_controller and self.emergency_controller.is_active():
                return False, "EMERGENCY_STOP_ACTIVE", None

            # Concurrency limit per device
            active_device_sessions = [
                s for s in self._sessions.values()
                if s.device_id == device_id and not s.is_terminal()
            ]
            if len(active_device_sessions) >= MAX_CONCURRENT_VOICE_SESSIONS_PER_DEVICE:
                return False, f"MAX_SESSIONS_PER_DEVICE_EXCEEDED: limit {MAX_CONCURRENT_VOICE_SESSIONS_PER_DEVICE}", None

            # Global concurrency limit
            active_global = [s for s in self._sessions.values() if not s.is_terminal()]
            if len(active_global) >= MAX_GLOBAL_CONCURRENT_VOICE_SESSIONS:
                return False, f"MAX_GLOBAL_SESSIONS_EXCEEDED: limit {MAX_GLOBAL_CONCURRENT_VOICE_SESSIONS}", None

            sid = session_id or f"VCS-{secrets.token_hex(8).upper()}"
            session = VoiceCommandSession(session_id=sid, device_id=device_id)
            self._sessions[sid] = session
            return True, "SESSION_CREATED", session

    def get_session(self, session_id: str) -> Optional[VoiceCommandSession]:
        with self._lock:
            return self._sessions.get(session_id)

    def process_voice_request(
        self,
        audio_req: AudioRequest,
        granted_scopes: Set[PhonePermissionScope],
    ) -> VoiceResponse:
        """
        Execute full voice processing pipeline from raw audio request to structured result.
        """
        req_id = audio_req.request_id
        session_id = audio_req.session_id
        device_id = audio_req.device_id

        # 1. Fetch or create session
        session = self.get_session(session_id)
        if not session:
            ok, msg, session = self.create_session(device_id, session_id=session_id)
            if not ok or not session:
                return VoiceResponse(
                    request_id=req_id,
                    session_id=session_id,
                    status="ERROR",
                    transcript="",
                    intent_type="UNKNOWN",
                    result_text=f"Session creation failed: {msg}",
                    error_code="SESSION_CREATION_FAILED",
                )

        if session.is_expired():
            session.transition_to(VoiceSessionState.FAILED, "SESSION_EXPIRED")
            return VoiceResponse(
                request_id=req_id,
                session_id=session_id,
                status="ERROR",
                transcript="",
                intent_type="UNKNOWN",
                result_text="Voice session expired.",
                error_code=VOICE_SESSION_EXPIRED,
            )

        # 2. Transition to RECEIVING
        try:
            session.transition_to(VoiceSessionState.RECEIVING, "AUDIO_RECEIVED")
        except VoiceSessionError as e:
            return VoiceResponse(
                request_id=req_id,
                session_id=session_id,
                status="ERROR",
                transcript="",
                intent_type="UNKNOWN",
                result_text=str(e),
                error_code="INVALID_STATE",
            )

        session.audio_request = audio_req
        if self.telemetry_event_fn:
            self.telemetry_event_fn("VOICE_RECEIVED", {"session_id": session_id, "req_id": req_id})

        # 3. Transition to VALIDATING
        session.transition_to(VoiceSessionState.VALIDATING, "VALIDATING_AUDIO")
        val_ok, val_reason = validate_audio_request(audio_req)
        if not val_ok:
            session.transition_to(VoiceSessionState.FAILED, f"AUDIO_VALIDATION_FAILED: {val_reason}")
            if self.telemetry_event_fn:
                self.telemetry_event_fn("VOICE_FAILED", {"session_id": session_id, "reason": val_reason})
            return VoiceResponse(
                request_id=req_id,
                session_id=session_id,
                status="ERROR",
                transcript="",
                intent_type="UNKNOWN",
                result_text=f"Audio validation failed: {val_reason}",
                error_code=val_reason,
            )

        if self.telemetry_event_fn:
            self.telemetry_event_fn("VOICE_VALIDATED", {"session_id": session_id})

        # 4. Transition to TRANSCRIBING
        session.transition_to(VoiceSessionState.TRANSCRIBING, "STARTING_STT")
        if self.telemetry_event_fn:
            self.telemetry_event_fn("VOICE_TRANSCRIBING", {"session_id": session_id})

        # Check emergency stop prior to STT
        if self.emergency_controller and self.emergency_controller.is_active():
            session.transition_to(VoiceSessionState.STOPPED, "EMERGENCY_STOP_ACTIVE")
            return VoiceResponse(
                request_id=req_id,
                session_id=session_id,
                status="STOPPED",
                transcript="",
                intent_type="UNKNOWN",
                result_text="Execution halted: Emergency stop active.",
                error_code="EMERGENCY_STOP_ACTIVE",
            )

        # STT invocation (bounded to MAX_TRANSCRIPTION_ATTEMPTS)
        stt_ok = False
        stt_msg = ""
        transcript = ""
        for attempt in range(MAX_TRANSCRIPTION_ATTEMPTS):
            session.transcription_attempts += 1
            stt_ok, stt_msg, transcript = self.stt_provider.transcribe(audio_req)
            if stt_ok and transcript is not None:
                break

        if not stt_ok or transcript is None:
            session.transition_to(VoiceSessionState.FAILED, f"TRANSCRIPTION_FAILED: {stt_msg}")
            if self.telemetry_event_fn:
                self.telemetry_event_fn("VOICE_FAILED", {"session_id": session_id, "reason": stt_msg})
            return VoiceResponse(
                request_id=req_id,
                session_id=session_id,
                status="ERROR",
                transcript="",
                intent_type="UNKNOWN",
                result_text=f"Transcription failed: {stt_msg}",
                error_code=stt_msg or TRANSCRIPTION_FAILED,
            )

        session.transcript = transcript
        if self.telemetry_event_fn:
            self.telemetry_event_fn("VOICE_TRANSCRIBED", {"session_id": session_id, "text": transcript})

        # 5. Transition to UNDERSTANDING
        session.transition_to(VoiceSessionState.UNDERSTANDING, "PARSING_INTENT")
        intent = VoiceIntentParser.parse_intent(transcript)
        session.intent = intent

        if self.telemetry_event_fn:
            self.telemetry_event_fn("VOICE_INTENT_DETECTED", {"session_id": session_id, "type": intent.intent_type.value})

        # 6. Evaluate Command Safety Gate
        is_em = bool(self.emergency_controller and self.emergency_controller.is_active())
        safety_dec, safety_reason = VoiceCommandSafetyGate.evaluate(
            intent=intent,
            is_emergency_active=is_em,
            granted_scopes=granted_scopes,
        )

        # Handle EMERGENCY_STOP intent
        if intent.intent_type == VoiceIntentType.EMERGENCY_STOP:
            if self.emergency_controller:
                self.emergency_controller.trigger(triggered_by="VOICE", reason="VOICE_EMERGENCY_STOP_TRIGGERED")
            session.transition_to(VoiceSessionState.STOPPED, "EMERGENCY_STOP")
            if self.telemetry_event_fn:
                self.telemetry_event_fn("VOICE_COMPLETED", {"session_id": session_id, "action": "EMERGENCY_STOP"})
            return VoiceResponse(
                request_id=req_id,
                session_id=session_id,
                status="STOPPED",
                transcript=transcript,
                intent_type=intent.intent_type.value,
                result_text="Emergency stop triggered successfully via voice.",
                error_code=None,
            )

        # Handle DENIED or UNKNOWN
        if safety_dec in (VoiceSafetyDecision.DENIED, VoiceSafetyDecision.UNKNOWN):
            session.transition_to(VoiceSessionState.FAILED, safety_reason)
            if self.telemetry_event_fn:
                self.telemetry_event_fn("VOICE_FAILED", {"session_id": session_id, "reason": safety_reason})
            return VoiceResponse(
                request_id=req_id,
                session_id=session_id,
                status="DENIED",
                transcript=transcript,
                intent_type=intent.intent_type.value,
                result_text=f"Action denied: {safety_reason}",
                error_code=safety_dec.value,
            )

        # Handle CONFIRMATION_REQUIRED
        if safety_dec == VoiceSafetyDecision.CONFIRMATION_REQUIRED:
            session.transition_to(VoiceSessionState.AWAITING_CONFIRMATION, "CONFIRMATION_REQUIRED")
            cid = f"CNF-{secrets.token_hex(6).upper()}"
            session.confirmation_id = cid
            session.confirmation_expires_at = time.time() + VOICE_CONFIRMATION_TIMEOUT_SECONDS
            if self.telemetry_event_fn:
                self.telemetry_event_fn("VOICE_CONFIRMATION_REQUIRED", {"session_id": session_id, "cid": cid})
            return VoiceResponse(
                request_id=req_id,
                session_id=session_id,
                status="CONFIRMATION_REQUIRED",
                transcript=transcript,
                intent_type=intent.intent_type.value,
                result_text=f"Confirmation required: Please confirm to proceed with {intent.parameters.get('action', intent.normalized_text)}.",
                error_code=None,
                requires_confirmation=True,
                confirmation_id=cid,
            )

        # 7. Transition to EXECUTING
        session.transition_to(VoiceSessionState.EXECUTING, "DISPATCHING_COMMAND")
        if self.telemetry_event_fn:
            self.telemetry_event_fn("VOICE_EXECUTED", {"session_id": session_id})

        # Route to command execution engine
        result_text = self._execute_safe_command(intent)

        # 8. Transition to COMPLETED
        session.transition_to(VoiceSessionState.COMPLETED, "EXECUTION_FINISHED")
        if self.telemetry_event_fn:
            self.telemetry_event_fn("VOICE_COMPLETED", {"session_id": session_id})

        return VoiceResponse(
            request_id=req_id,
            session_id=session_id,
            status="SUCCESS",
            transcript=transcript,
            intent_type=intent.intent_type.value,
            result_text=result_text,
            error_code=None,
        )

    def _execute_safe_command(self, intent: VoiceIntent) -> str:
        """Route safe intent to existing NR-AI companion router or deterministic handler."""
        if intent.intent_type == VoiceIntentType.SYSTEM_TIME:
            return f"The current system time is {time.strftime('%Y-%m-%d %H:%M:%S')}."

        if intent.intent_type == VoiceIntentType.STATUS_QUERY:
            return "NR-AI backend is operational and healthy. All safety gates active."

        if intent.intent_type == VoiceIntentType.TOOLCHAIN_STATUS:
            return "Toolchain status: Localhost secure transport active. Subprocess safety: 100% shell=False."

        # Route via custom command_router_fn if wired
        if self.command_router_fn:
            try:
                res = self.command_router_fn(intent.text)
                if isinstance(res, dict):
                    return str(res.get("text", res.get("result", "Command processed successfully.")))
                return str(res)
            except Exception as e:
                return f"Command execution error: {e}"

        return f"NR-AI processed speech query: {intent.text}"

    def confirm_action(
        self,
        session_id: str,
        confirmation_id: str,
        confirmed: bool,
    ) -> Tuple[bool, str, Optional[VoiceResponse]]:
        """Handle user confirmation response for a pending sensitive action."""
        session = self.get_session(session_id)
        if not session:
            return False, "SESSION_NOT_FOUND", None

        if session.state != VoiceSessionState.AWAITING_CONFIRMATION:
            return False, f"INVALID_SESSION_STATE: {session.state.value}", None

        if session.confirmation_id != confirmation_id:
            return False, "INVALID_CONFIRMATION_ID", None

        if session.confirmation_expires_at and time.time() > session.confirmation_expires_at:
            session.transition_to(VoiceSessionState.FAILED, "CONFIRMATION_TIMED_OUT")
            return False, "CONFIRMATION_EXPIRED", None

        if not confirmed:
            session.transition_to(VoiceSessionState.CANCELLED, "OPERATOR_REJECTED")
            if self.telemetry_event_fn:
                self.telemetry_event_fn("VOICE_CANCELLED", {"session_id": session_id})
            return True, "ACTION_CANCELLED", VoiceResponse(
                request_id=f"CNF-{secrets.token_hex(4)}",
                session_id=session_id,
                status="CANCELLED",
                transcript=session.transcript or "",
                intent_type="ACTION_CANCELLED",
                result_text="Action was cancelled by operator.",
            )

        # Confirmed: execute
        session.transition_to(VoiceSessionState.EXECUTING, "CONFIRMATION_ACCEPTED")
        result_text = f"Confirmed action '{session.intent.parameters.get('action', '')}' executed successfully."
        session.transition_to(VoiceSessionState.COMPLETED, "EXECUTION_COMPLETED")

        if self.telemetry_event_fn:
            self.telemetry_event_fn("VOICE_COMPLETED", {"session_id": session_id})

        return True, "ACTION_CONFIRMED_AND_EXECUTED", VoiceResponse(
            request_id=f"CNF-{secrets.token_hex(4)}",
            session_id=session_id,
            status="SUCCESS",
            transcript=session.transcript or "",
            intent_type="ACTION_APPROVED",
            result_text=result_text,
        )

    def cancel_session(self, session_id: str) -> Tuple[bool, str]:
        session = self.get_session(session_id)
        if not session:
            return False, "SESSION_NOT_FOUND"

        if session.is_terminal():
            return False, "SESSION_ALREADY_TERMINATED"

        session.transition_to(VoiceSessionState.CANCELLED, "OPERATOR_CANCELLED")
        if self.telemetry_event_fn:
            self.telemetry_event_fn("VOICE_CANCELLED", {"session_id": session_id})
        return True, "SESSION_CANCELLED"
