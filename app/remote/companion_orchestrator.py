"""
NR-AI Companion Orchestrator & End-to-End Execution Pipeline.
Step 10 Phase 5 — Autonomous Mobile Companion UX, End-to-End Orchestration & Polish.

Unifies session management, screen streaming, voice audio pipeline, scoped remote actions,
telemetry updates, and Text-To-Speech response contracts into a cohesive mobile companion agent.
"""

from dataclasses import dataclass, field
from enum import Enum
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from app.remote.audit import SecurityAuditLogger
from app.remote.companion_client_contract import (
    CompanionNotification,
    CompanionUIState,
    ConfirmationDialogPayload,
    TTSResponseContract,
    sanitize_tts_response,
)
from app.remote.companion_resilience import CompanionResilienceManager
from app.remote.config import (
    COMPANION_DEGRADED_MODE,
    COMPANION_INVALID_STATE,
    MAX_TTS_RESPONSE_CHARS,
    REMOTE_ACTION_NOT_ALLOWED,
    REMOTE_AUTH_REQUIRED,
    REMOTE_CONFIRMATION_EXPIRED,
    REMOTE_CONFIRMATION_INVALID,
    REMOTE_CONFIRMATION_REQUIRED,
    REMOTE_PERMISSION_DENIED,
    REMOTE_SAFETY_REJECTED,
    REMOTE_STOPPED,
    REMOTE_TARGET_STALE,
)
from app.remote.emergency import EmergencyStopController
from app.remote.permissions import PhonePermissionScope
from app.remote.remote_actions import (
    ACTION_ALIASES,
    REMOTE_ACTION_ALLOWLIST,
    RemoteActionRequest,
    RemoteActionResult,
    RemoteActionRiskLevel,
    RemoteActionType,
    normalize_action_type,
    validate_remote_action_request,
)
from app.remote.audio import AudioRequest
from app.remote.auth import SessionManager
from app.remote.remote_action_safety import RemoteActionSafetyGate
from app.remote.remote_action_session import RemoteActionSessionManager
from app.remote.stream import StreamManager
from app.remote.telemetry import TelemetryHub
from app.remote.voice_session import VoiceIntentType, VoiceSessionManager

StreamSessionManager = StreamManager

logger = logging.getLogger("NRAI.CompanionOrchestrator")


class CompanionOrchestratorState(str, Enum):
    UNPAIRED = "UNPAIRED"
    PAIRING = "PAIRING"
    AUTHENTICATED = "AUTHENTICATED"
    IDLE_CONNECTED = "IDLE_CONNECTED"
    STREAMING_OBSERVATION = "STREAMING_OBSERVATION"
    LISTENING_VOICE = "LISTENING_VOICE"
    EVALUATING_ACTION = "EVALUATING_ACTION"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    EXECUTING_ACTION = "EXECUTING_ACTION"
    VERIFYING_RESULT = "VERIFYING_RESULT"
    REPORTING_RESPONSE = "REPORTING_RESPONSE"
    # Terminal / Interrupted
    DISCONNECTED = "DISCONNECTED"
    RECONNECTING = "RECONNECTING"
    EXPIRED = "EXPIRED"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


VALID_ORCHESTRATOR_TRANSITIONS: Dict[CompanionOrchestratorState, Set[CompanionOrchestratorState]] = {
    CompanionOrchestratorState.UNPAIRED: {
        CompanionOrchestratorState.PAIRING,
        CompanionOrchestratorState.AUTHENTICATED,
        CompanionOrchestratorState.STOPPED,
        CompanionOrchestratorState.FAILED,
    },
    CompanionOrchestratorState.PAIRING: {
        CompanionOrchestratorState.AUTHENTICATED,
        CompanionOrchestratorState.UNPAIRED,
        CompanionOrchestratorState.FAILED,
        CompanionOrchestratorState.STOPPED,
    },
    CompanionOrchestratorState.AUTHENTICATED: {
        CompanionOrchestratorState.IDLE_CONNECTED,
        CompanionOrchestratorState.DISCONNECTED,
        CompanionOrchestratorState.EXPIRED,
        CompanionOrchestratorState.STOPPED,
    },
    CompanionOrchestratorState.IDLE_CONNECTED: {
        CompanionOrchestratorState.STREAMING_OBSERVATION,
        CompanionOrchestratorState.LISTENING_VOICE,
        CompanionOrchestratorState.EVALUATING_ACTION,
        CompanionOrchestratorState.DISCONNECTED,
        CompanionOrchestratorState.EXPIRED,
        CompanionOrchestratorState.STOPPED,
    },
    CompanionOrchestratorState.STREAMING_OBSERVATION: {
        CompanionOrchestratorState.IDLE_CONNECTED,
        CompanionOrchestratorState.LISTENING_VOICE,
        CompanionOrchestratorState.EVALUATING_ACTION,
        CompanionOrchestratorState.DISCONNECTED,
        CompanionOrchestratorState.STOPPED,
        CompanionOrchestratorState.FAILED,
    },
    CompanionOrchestratorState.LISTENING_VOICE: {
        CompanionOrchestratorState.EVALUATING_ACTION,
        CompanionOrchestratorState.IDLE_CONNECTED,
        CompanionOrchestratorState.STREAMING_OBSERVATION,
        CompanionOrchestratorState.STOPPED,
        CompanionOrchestratorState.FAILED,
    },
    CompanionOrchestratorState.EVALUATING_ACTION: {
        CompanionOrchestratorState.AWAITING_CONFIRMATION,
        CompanionOrchestratorState.EXECUTING_ACTION,
        CompanionOrchestratorState.REPORTING_RESPONSE,
        CompanionOrchestratorState.IDLE_CONNECTED,
        CompanionOrchestratorState.FAILED,
        CompanionOrchestratorState.STOPPED,
    },
    CompanionOrchestratorState.AWAITING_CONFIRMATION: {
        CompanionOrchestratorState.CONFIRMED,
        CompanionOrchestratorState.IDLE_CONNECTED,
        CompanionOrchestratorState.EXPIRED,
        CompanionOrchestratorState.STOPPED,
        CompanionOrchestratorState.FAILED,
    },
    CompanionOrchestratorState.CONFIRMED: {
        CompanionOrchestratorState.EXECUTING_ACTION,
        CompanionOrchestratorState.STOPPED,
        CompanionOrchestratorState.FAILED,
    },
    CompanionOrchestratorState.EXECUTING_ACTION: {
        CompanionOrchestratorState.VERIFYING_RESULT,
        CompanionOrchestratorState.REPORTING_RESPONSE,
        CompanionOrchestratorState.FAILED,
        CompanionOrchestratorState.STOPPED,
    },
    CompanionOrchestratorState.VERIFYING_RESULT: {
        CompanionOrchestratorState.REPORTING_RESPONSE,
        CompanionOrchestratorState.FAILED,
        CompanionOrchestratorState.STOPPED,
    },
    CompanionOrchestratorState.REPORTING_RESPONSE: {
        CompanionOrchestratorState.IDLE_CONNECTED,
        CompanionOrchestratorState.STREAMING_OBSERVATION,
        CompanionOrchestratorState.STOPPED,
        CompanionOrchestratorState.FAILED,
    },
    CompanionOrchestratorState.DISCONNECTED: {
        CompanionOrchestratorState.RECONNECTING,
        CompanionOrchestratorState.UNPAIRED,
        CompanionOrchestratorState.EXPIRED,
        CompanionOrchestratorState.STOPPED,
    },
    CompanionOrchestratorState.RECONNECTING: {
        CompanionOrchestratorState.AUTHENTICATED,
        CompanionOrchestratorState.IDLE_CONNECTED,
        CompanionOrchestratorState.DISCONNECTED,
        CompanionOrchestratorState.EXPIRED,
        CompanionOrchestratorState.STOPPED,
    },
    CompanionOrchestratorState.EXPIRED: {
        CompanionOrchestratorState.UNPAIRED,
        CompanionOrchestratorState.PAIRING,
    },
    CompanionOrchestratorState.STOPPED: {
        CompanionOrchestratorState.IDLE_CONNECTED,
        CompanionOrchestratorState.UNPAIRED,
    },
    CompanionOrchestratorState.FAILED: {
        CompanionOrchestratorState.IDLE_CONNECTED,
        CompanionOrchestratorState.UNPAIRED,
    },
}


class CompanionStateError(Exception):
    """Raised when an illegal orchestrator state transition is attempted."""
    pass


@dataclass
class CompanionCommandResult:
    """Consolidated result returned to the Android companion for commands and actions."""
    session_id: str
    device_id: str
    command_type: str  # QUERY, COMPUTER_ACTION, VOICE_COMMAND, CONFIRMATION, EMERGENCY_STOP
    status: str  # SUCCESS, AWAITING_CONFIRMATION, DENIED, FAILED, STOPPED
    success: bool
    message: str
    data: Dict[str, Any] = field(default_factory=dict)
    tts_response: Optional[TTSResponseContract] = None
    confirmation_payload: Optional[ConfirmationDialogPayload] = None
    duration_s: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "device_id": self.device_id,
            "command_type": self.command_type,
            "status": self.status,
            "success": self.success,
            "message": self.message,
            "data": self.data,
            "tts_response": self.tts_response.to_dict() if self.tts_response else None,
            "confirmation_payload": self.confirmation_payload.to_dict() if self.confirmation_payload else None,
            "duration_s": round(self.duration_s, 3),
            "timestamp": self.timestamp,
        }


class CompanionOrchestrator:
    """
    Unifies and coordinates the full Step 10 end-to-end mobile companion experience:
    - Authenticated session lifecycle
    - Screen frame observation streaming
    - Voice audio capture and intent processing
    - Scoped computer control actions
    - Two-step confirmation gating for high-risk tools
    - Resilient connection and reconnect tracking
    - Deterministic Emergency Stop priority
    - Natural language Text-to-Speech synthesis response contracts
    """

    def __init__(
        self,
        session_manager: SessionManager,
        stream_session_manager: Optional[StreamSessionManager] = None,
        voice_session_manager: Optional[VoiceSessionManager] = None,
        remote_action_session_manager: Optional[RemoteActionSessionManager] = None,
        telemetry_hub: Optional[TelemetryHub] = None,
        emergency_controller: Optional[EmergencyStopController] = None,
        resilience_manager: Optional[CompanionResilienceManager] = None,
        audit_logger: Optional[SecurityAuditLogger] = None,
        computer_agent: Optional[Any] = None,
    ):
        self.session_manager = session_manager
        self.emergency_controller = emergency_controller or EmergencyStopController()
        self.stream_session_manager = stream_session_manager or StreamManager(
            emergency_stop=self.emergency_controller,
        )
        self.stream_manager = self.stream_session_manager
        self.voice_session_manager = voice_session_manager or VoiceSessionManager(
            emergency_controller=self.emergency_controller,
        )
        self.remote_action_session_manager = remote_action_session_manager or RemoteActionSessionManager(
            computer_agent=computer_agent,
            emergency_controller=self.emergency_controller,
        )
        self.telemetry_hub = telemetry_hub or TelemetryHub()
        self.resilience_manager = resilience_manager or CompanionResilienceManager()
        self.audit_logger = audit_logger or SecurityAuditLogger()
        self.computer_agent = computer_agent

        self._states: Dict[str, CompanionOrchestratorState] = {}  # device_id -> state
        self._lock = threading.Lock()

        # Register Emergency Stop priority callback
        self.emergency_controller.register_cancellation_callback(self._on_emergency_stop)

    def get_state(self, device_id: str) -> CompanionOrchestratorState:
        with self._lock:
            return self._states.get(device_id, CompanionOrchestratorState.UNPAIRED)

    def transition_state(self, device_id: str, new_state: CompanionOrchestratorState, reason: str = "") -> None:
        """Transitions device companion state in a strictly validated state machine."""
        with self._lock:
            current = self._states.get(device_id, CompanionOrchestratorState.UNPAIRED)
            if current == new_state:
                return

            valid = VALID_ORCHESTRATOR_TRANSITIONS.get(current, set())
            if new_state not in valid:
                raise CompanionStateError(
                    f"Illegal orchestrator transition for {device_id} from {current.value} to {new_state.value} ({reason})"
                )

            self._states[device_id] = new_state
            logger.debug(f"[Orchestrator] {device_id}: {current.value} -> {new_state.value} ({reason})")

    def _on_emergency_stop(self) -> None:
        """Callback triggered immediately when Emergency Stop is activated."""
        with self._lock:
            for dev_id in list(self._states.keys()):
                self._states[dev_id] = CompanionOrchestratorState.STOPPED

        self.audit_logger.log_event(
            event_type="COMPANION_EMERGENCY_STOP",
            result="BLOCKED",
            action="global.emergency_stop",
            reason="Emergency Stop triggered: Halting all companion orchestrator sessions.",
        )

    def process_command(
        self,
        session_id: str,
        device_id: str,
        command_text: Optional[str] = None,
        audio_request: Optional[AudioRequest] = None,
        action_payload: Optional[Dict[str, Any]] = None,
        cached_target: Optional[Dict[str, Any]] = None,
        current_time: Optional[float] = None,
    ) -> CompanionCommandResult:
        """
        Main unified ingress pipeline for mobile companion commands.
        Processes text commands, voice audio, or structured action requests through
        authorization, intent parsing, deterministic safety gates, and execution.
        """
        start_t = time.time()
        now = current_time if current_time is not None else start_t

        # 1. Emergency Stop Check (Priority 1)
        if self.emergency_controller.is_active():
            self._states[device_id] = CompanionOrchestratorState.STOPPED
            return self._build_result(
                session_id=session_id,
                device_id=device_id,
                command_type="EMERGENCY_STOP",
                status="STOPPED",
                success=False,
                error_code=REMOTE_STOPPED,
                message="Computer action rejected: Emergency stop is active.",
                start_t=start_t,
                speech_text="Emergency stop is currently active. All computer actions are halted.",
            )

        # 2. Authentication & Session Validation
        valid_sess, auth_msg, sess = self.session_manager.validate_session(session_id, device_id, current_time=now)
        if not valid_sess or not sess:
            return self._build_result(
                session_id=session_id,
                device_id=device_id,
                command_type="AUTH",
                status="DENIED",
                success=False,
                error_code=REMOTE_AUTH_REQUIRED,
                message=f"Authentication failed: {auth_msg}",
                start_t=start_t,
                speech_text="Authentication required. Please pair or reconnect your device.",
            )

        # Record heartbeat
        self.resilience_manager.record_heartbeat(device_id, current_time=now)

        # 3. Connection Health Check
        is_healthy, h_status = self.resilience_manager.check_connection_health(device_id, current_time=now)
        if not is_healthy:
            self._states[device_id] = CompanionOrchestratorState.DISCONNECTED
            return self._build_result(
                session_id=session_id,
                device_id=device_id,
                command_type="CONNECTION",
                status="DENIED",
                success=False,
                error_code="COMPANION_HEARTBEAT_TIMEOUT",
                message="Connection timed out due to missed heartbeats.",
                start_t=start_t,
                speech_text="Connection timed out. Reconnecting to PC.",
            )

        cur_state = self.get_state(device_id)
        if cur_state in (CompanionOrchestratorState.UNPAIRED, CompanionOrchestratorState.PAIRING):
            self.transition_state(device_id, CompanionOrchestratorState.AUTHENTICATED, "Session verified")
            self.transition_state(device_id, CompanionOrchestratorState.IDLE_CONNECTED, "Ready for command")
        elif cur_state == CompanionOrchestratorState.AUTHENTICATED:
            self.transition_state(device_id, CompanionOrchestratorState.IDLE_CONNECTED, "Ready for command")
        elif cur_state in (CompanionOrchestratorState.FAILED, CompanionOrchestratorState.STOPPED):
            if not self.emergency_controller.is_active():
                self.transition_state(device_id, CompanionOrchestratorState.IDLE_CONNECTED, "Ready for command")

        # 4. Voice Command Route
        if audio_request is not None:
            return self._handle_voice_pipeline(sess, audio_request, start_t, now)

        # 5. Structured Action Request Route
        if action_payload is not None:
            return self._handle_action_payload(sess, action_payload, cached_target, start_t, now)

        # 6. Text Command Route
        if command_text is not None:
            return self._handle_text_command(sess, command_text, cached_target, start_t, now)

        return self._build_result(
            session_id=session_id,
            device_id=device_id,
            command_type="UNKNOWN",
            status="FAILED",
            success=False,
            error_code="INVALID_REQUEST",
            message="No text command, audio request, or action payload supplied.",
            start_t=start_t,
            speech_text="No command received.",
        )

    def _handle_voice_pipeline(
        self,
        sess: Any,
        audio_request: AudioRequest,
        start_t: float,
        now: float,
    ) -> CompanionCommandResult:
        """Processes voice audio through STT transcription, intent parsing, and safety routing."""
        self.transition_state(sess.device_id, CompanionOrchestratorState.LISTENING_VOICE, "Audio ingested")

        voice_res = self.voice_session_manager.process_voice_request(
            audio_req=audio_request,
            granted_scopes=sess.scopes,
        )

        # Check if Emergency Stop was triggered or is active
        raw_intent = voice_res.intent_type
        intent_val = raw_intent.value if hasattr(raw_intent, "value") else str(raw_intent)
        if (
            voice_res.status == "STOPPED"
            or intent_val in (VoiceIntentType.EMERGENCY_STOP.value, "EMERGENCY_STOP")
            or self.emergency_controller.is_active()
        ):
            self.trigger_emergency_stop(sess.device_id, reason="Voice Emergency Stop triggered")
            return self._build_result(
                session_id=sess.session_id,
                device_id=sess.device_id,
                command_type="EMERGENCY_STOP",
                status="STOPPED",
                success=False,
                error_code=REMOTE_STOPPED,
                message="Emergency stop activated via voice command.",
                start_t=start_t,
                speech_text="Emergency stop activated. All actions halted.",
            )

        if voice_res.status != "SUCCESS":
            self.transition_state(sess.device_id, CompanionOrchestratorState.FAILED, "Voice processing failed")
            # If STT was unavailable, suggest typed input
            if voice_res.error_code == "SPEECH_TO_TEXT_UNAVAILABLE":
                speech = "Speech recognition is currently unavailable. Please type your command."
            else:
                speech = f"Voice command failed: {voice_res.result_text}"
            return self._build_result(
                session_id=sess.session_id,
                device_id=sess.device_id,
                command_type="VOICE_COMMAND",
                status=voice_res.status,
                success=False,
                error_code=voice_res.error_code,
                message=voice_res.result_text,
                data=voice_res.to_dict(),
                start_t=start_t,
                speech_text=speech,
            )

        # Handle parsed intent
        transcript = voice_res.transcript or ""

        # If voice maps to a scoped computer action or natural query
        return self._route_intent_or_query(sess, transcript, intent_val, voice_res.to_dict(), None, start_t, now)

    def _handle_text_command(
        self,
        sess: Any,
        command_text: str,
        cached_target: Optional[Dict[str, Any]],
        start_t: float,
        now: float,
    ) -> CompanionCommandResult:
        """Parses and executes a text command."""
        self.transition_state(sess.device_id, CompanionOrchestratorState.EVALUATING_ACTION, "Evaluating text command")

        cleaned = command_text.strip()
        cleaned_lower = cleaned.lower()

        # Emergency stop check
        if any(term in cleaned_lower for term in ("stop", "halt", "freeze", "kill all", "abort")):
            self.trigger_emergency_stop(sess.device_id, reason=f"Emergency stop command: '{cleaned}'")
            return self._build_result(
                session_id=sess.session_id,
                device_id=sess.device_id,
                command_type="EMERGENCY_STOP",
                status="STOPPED",
                success=False,
                error_code=REMOTE_STOPPED,
                message="Emergency stop activated.",
                start_t=start_t,
                speech_text="Emergency stop activated. All actions halted.",
            )

        # Informational Queries (Time / System Status)
        if any(q in cleaned_lower for q in ("time", "what time", "current time")):
            cur_time_str = time.strftime("%I:%M %p")
            self.transition_state(sess.device_id, CompanionOrchestratorState.REPORTING_RESPONSE, "Time reported")
            self.transition_state(sess.device_id, CompanionOrchestratorState.IDLE_CONNECTED, "Ready")
            return self._build_result(
                session_id=sess.session_id,
                device_id=sess.device_id,
                command_type="QUERY",
                status="SUCCESS",
                success=True,
                message=f"Current time is {cur_time_str}.",
                data={"time": cur_time_str},
                start_t=start_t,
                speech_text=f"The time is {cur_time_str}.",
            )

        if any(q in cleaned_lower for q in ("status", "system status", "health", "state")):
            status_summary = {
                "assistant_status": "Online",
                "companion_state": self.get_state(sess.device_id).value,
                "emergency_stop": self.emergency_controller.is_active(),
            }
            self.transition_state(sess.device_id, CompanionOrchestratorState.REPORTING_RESPONSE, "Status reported")
            self.transition_state(sess.device_id, CompanionOrchestratorState.IDLE_CONNECTED, "Ready")
            return self._build_result(
                session_id=sess.session_id,
                device_id=sess.device_id,
                command_type="QUERY",
                status="SUCCESS",
                success=True,
                message="System is operational.",
                data=status_summary,
                start_t=start_t,
                speech_text="NR-AI system is online and operational.",
            )

        # Check if text maps directly to an approved tool name or alias
        mapped_action = None
        for alias, act_type in ACTION_ALIASES.items():
            if cleaned_lower == alias or cleaned_lower.startswith(f"{alias} "):
                mapped_action = act_type
                break

        if mapped_action:
            action_req_data = {
                "action_id": f"ACT-{int(now*1000)}",
                "session_id": sess.session_id,
                "device_id": sess.device_id,
                "action_type": mapped_action,
                "parameters": {},
                "timestamp": now,
            }
            return self._handle_action_payload(sess, action_req_data, cached_target, start_t, now)

        # Default conversational / advisory reply
        self.transition_state(sess.device_id, CompanionOrchestratorState.REPORTING_RESPONSE, "Advisory response")
        self.transition_state(sess.device_id, CompanionOrchestratorState.IDLE_CONNECTED, "Ready")
        reply = f"Acknowledged command: \"{cleaned}\"."
        return self._build_result(
            session_id=sess.session_id,
            device_id=sess.device_id,
            command_type="COMMAND",
            status="SUCCESS",
            success=True,
            message=reply,
            start_t=start_t,
            speech_text=reply,
        )

    def _handle_action_payload(
        self,
        sess: Any,
        action_payload: Dict[str, Any],
        cached_target: Optional[Dict[str, Any]],
        start_t: float,
        now: float,
    ) -> CompanionCommandResult:
        """Dispatches an action payload through Phase 4 allowlist, safety, confirmation, and execution."""
        self.transition_state(sess.device_id, CompanionOrchestratorState.EVALUATING_ACTION, "Evaluating action")

        # Validate schema
        valid, err_code, action_req = validate_remote_action_request(action_payload, current_time=now)
        if not valid or not action_req:
            self.transition_state(sess.device_id, CompanionOrchestratorState.FAILED, f"Validation failed: {err_code}")
            return self._build_result(
                session_id=sess.session_id,
                device_id=sess.device_id,
                command_type="COMPUTER_ACTION",
                status="DENIED",
                success=False,
                error_code=err_code,
                message=f"Action validation rejected: {err_code}",
                start_t=start_t,
                speech_text=f"Action request was invalid: {err_code}.",
            )

        # Process through RemoteActionSessionManager
        act_res: RemoteActionResult = self.remote_action_session_manager.process_action_request(
            request=action_req,
            scopes=sess.scopes,
            cached_target=cached_target,
            current_time=now,
        )

        if act_res.status == "AWAITING_CONFIRMATION":
            self.transition_state(sess.device_id, CompanionOrchestratorState.AWAITING_CONFIRMATION, "Confirmation required")
            conf_payload = ConfirmationDialogPayload(
                action_id=act_res.action_id,
                action_type=act_res.action_type,
                description=f"Action '{act_res.action_type}' requires confirmation.",
                risk_level=act_res.risk_level,
                confirmation_token=act_res.confirmation_token or "",
                expires_at=act_res.data.get("confirmation_expires_at", now + 30.0),
                timeout_seconds=30.0,
            )
            return self._build_result(
                session_id=sess.session_id,
                device_id=sess.device_id,
                command_type="COMPUTER_ACTION",
                status="AWAITING_CONFIRMATION",
                success=True,
                message=act_res.message,
                data=act_res.to_dict(),
                confirmation_payload=conf_payload,
                start_t=start_t,
                speech_text=f"Action {act_res.action_type} requires operator confirmation.",
            )

        if act_res.success:
            self.transition_state(sess.device_id, CompanionOrchestratorState.EXECUTING_ACTION, "Executing")
            self.transition_state(sess.device_id, CompanionOrchestratorState.VERIFYING_RESULT, "Verifying")
            self.transition_state(sess.device_id, CompanionOrchestratorState.REPORTING_RESPONSE, "Reporting")
            self.transition_state(sess.device_id, CompanionOrchestratorState.IDLE_CONNECTED, "Ready")
            speech = f"Successfully executed {act_res.action_type}."
            return self._build_result(
                session_id=sess.session_id,
                device_id=sess.device_id,
                command_type="COMPUTER_ACTION",
                status="SUCCESS",
                success=True,
                message=act_res.message or f"Executed {act_res.action_type}",
                data=act_res.to_dict(),
                start_t=start_t,
                speech_text=speech,
            )
        else:
            self.transition_state(sess.device_id, CompanionOrchestratorState.FAILED, f"Execution failed: {act_res.error}")
            speech = f"Action {act_res.action_type} failed: {act_res.message}"
            return self._build_result(
                session_id=sess.session_id,
                device_id=sess.device_id,
                command_type="COMPUTER_ACTION",
                status=act_res.status,
                success=False,
                error_code=act_res.error,
                message=act_res.message,
                data=act_res.to_dict(),
                start_t=start_t,
                speech_text=speech,
            )

    def _route_intent_or_query(
        self,
        sess: Any,
        transcript: str,
        intent_type: Any,
        extra_data: Dict[str, Any],
        cached_target: Optional[Dict[str, Any]],
        start_t: float,
        now: float,
    ) -> CompanionCommandResult:
        """Routes recognized voice intent to appropriate query or action."""
        intent_val = intent_type.value if hasattr(intent_type, "value") else str(intent_type)
        if intent_val in (
            VoiceIntentType.SYSTEM_TIME.value,
            VoiceIntentType.STATUS_QUERY.value,
            VoiceIntentType.TOOLCHAIN_STATUS.value,
            "SYSTEM_TIME",
            "STATUS_QUERY",
            "TOOLCHAIN_STATUS",
        ):
            return self._handle_text_command(sess, transcript, cached_target, start_t, now)

        if intent_val in (VoiceIntentType.ACTION_APPROVED.value, "ACTION_APPROVED"):
            # Map transcript to action if possible
            for alias, act_type in ACTION_ALIASES.items():
                if alias in transcript.lower():
                    action_payload = {
                        "action_id": f"VOICE-ACT-{int(now*1000)}",
                        "session_id": sess.session_id,
                        "device_id": sess.device_id,
                        "action_type": act_type,
                        "parameters": {},
                        "timestamp": now,
                    }
                    return self._handle_action_payload(sess, action_payload, cached_target, start_t, now)

        return self._handle_text_command(sess, transcript, cached_target, start_t, now)

    def confirm_action(
        self,
        session_id: str,
        device_id: str,
        action_id: str,
        confirmation_token: str,
        confirmed: bool,
        current_time: Optional[float] = None,
    ) -> CompanionCommandResult:
        """Processes two-step operator confirmation for a pending high-risk action."""
        start_t = time.time()
        now = current_time if current_time is not None else start_t

        if self.emergency_controller.is_active():
            self._states[device_id] = CompanionOrchestratorState.STOPPED
            return self._build_result(
                session_id=session_id,
                device_id=device_id,
                command_type="CONFIRMATION",
                status="STOPPED",
                success=False,
                error_code=REMOTE_STOPPED,
                message="Confirmation aborted: Emergency stop is active.",
                start_t=start_t,
                speech_text="Action aborted: Emergency stop is active.",
            )

        if not confirmed:
            self.transition_state(device_id, CompanionOrchestratorState.IDLE_CONNECTED, "Action cancelled by user")
            return self._build_result(
                session_id=session_id,
                device_id=device_id,
                command_type="CONFIRMATION",
                status="CANCELLED",
                success=False,
                message="Action was cancelled by the user.",
                start_t=start_t,
                speech_text="Action cancelled.",
            )

        self.transition_state(device_id, CompanionOrchestratorState.CONFIRMED, "Confirmation accepted")
        self.transition_state(device_id, CompanionOrchestratorState.EXECUTING_ACTION, "Dispatching confirmed action")

        ok, msg, res = self.remote_action_session_manager.confirm_action(
            session_id=session_id,
            action_id=action_id,
            device_id=device_id,
            confirmation_token=confirmation_token,
            confirmed=confirmed,
            current_time=now,
        )

        if ok and res and res.success:
            self.transition_state(device_id, CompanionOrchestratorState.VERIFYING_RESULT, "Verified")
            self.transition_state(device_id, CompanionOrchestratorState.REPORTING_RESPONSE, "Reporting")
            self.transition_state(device_id, CompanionOrchestratorState.IDLE_CONNECTED, "Ready")
            speech = f"Action confirmed and executed successfully: {res.action_type}."
            return self._build_result(
                session_id=session_id,
                device_id=device_id,
                command_type="CONFIRMATION",
                status="SUCCESS",
                success=True,
                message=res.message or "Confirmed and executed.",
                data=res.to_dict(),
                start_t=start_t,
                speech_text=speech,
            )
        else:
            self.transition_state(device_id, CompanionOrchestratorState.FAILED, f"Confirmation failed: {msg}")
            speech = f"Confirmation failed: {msg}"
            return self._build_result(
                session_id=session_id,
                device_id=device_id,
                command_type="CONFIRMATION",
                status="FAILED",
                success=False,
                error_code=res.error if res else "CONFIRMATION_FAILED",
                message=msg,
                data=res.to_dict() if res else {},
                start_t=start_t,
                speech_text=speech,
            )

    def trigger_emergency_stop(self, device_id: str, reason: str = "Operator Emergency Stop") -> Dict[str, Any]:
        """
        Triggers thread-safe global Emergency Stop.
        Halts stream sessions, cancels voice sessions, halts action sessions, and logs audit event.
        """
        self.emergency_controller.trigger()
        with self._lock:
            self._states[device_id] = CompanionOrchestratorState.STOPPED

        self.audit_logger.log_event(
            event_type="COMPANION_EMERGENCY_STOP",
            result="BLOCKED",
            device_id=device_id,
            action="global.emergency_stop",
            reason=reason,
        )
        return {
            "status": "STOPPED",
            "message": "Emergency Stop successfully engaged. All activities halted.",
            "emergency_stop": True,
        }

    def reset_emergency_stop(self, device_id: str) -> bool:
        """Resets Emergency Stop state when authorized."""
        self.emergency_controller.reset()
        with self._lock:
            self._states[device_id] = CompanionOrchestratorState.IDLE_CONNECTED

        self.audit_logger.log_event(
            event_type="COMPANION_EMERGENCY_STOP_RESET",
            result="SUCCESS",
            device_id=device_id,
            action="global.emergency_stop_reset",
            reason="Emergency Stop reset by operator.",
        )
        return True

    def _build_result(
        self,
        session_id: str,
        device_id: str,
        command_type: str,
        status: str,
        success: bool,
        message: str,
        start_t: float,
        speech_text: str,
        error_code: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
        confirmation_payload: Optional[ConfirmationDialogPayload] = None,
    ) -> CompanionCommandResult:
        """Helper to construct sanitized CompanionCommandResult."""
        duration = time.time() - start_t
        sanitized = sanitize_tts_response(speech_text, max_chars=MAX_TTS_RESPONSE_CHARS)
        tts = TTSResponseContract(
            text=sanitized,
            raw_reply=speech_text,
            truncated=len(speech_text) > MAX_TTS_RESPONSE_CHARS,
            status=status,
        )
        res_data = dict(data or {})
        if error_code:
            res_data["error"] = error_code

        return CompanionCommandResult(
            session_id=session_id,
            device_id=device_id,
            command_type=command_type,
            status=status,
            success=success,
            message=message,
            data=res_data,
            tts_response=tts,
            confirmation_payload=confirmation_payload,
            duration_s=duration,
        )
