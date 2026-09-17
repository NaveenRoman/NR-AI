"""
NR-AI Remote Action Session Lifecycle & Dispatch Engine.
Step 10 Phase 4 — Scoped Remote Actions & Computer Control Safety.

Manages the bounded state machine for remote computer actions:
RECEIVED -> AUTHENTICATING -> AUTHORIZED -> VALIDATING -> [AWAITING_CONFIRMATION] -> EXECUTING -> VERIFYING -> COMPLETED
with strict failure states (REJECTED, FAILED, CANCELLED, STOPPED, EXPIRED).

Enforces concurrency boundaries, cryptographic confirmation tokens, replay protection,
audit logging, and deterministic Emergency Stop interruption.
"""

from dataclasses import dataclass, field
from enum import Enum
import logging
import secrets
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from app.remote.audit import SecurityAuditLogger
from app.remote.config import (
    MAX_CONCURRENT_REMOTE_ACTIONS_PER_DEVICE,
    MAX_GLOBAL_CONCURRENT_REMOTE_ACTIONS,
    MAX_REMOTE_ACTION_TIMEOUT_SECONDS,
    REMOTE_ACTION_EXPIRED,
    REMOTE_ACTION_NOT_ALLOWED,
    REMOTE_ACTION_REPLAYED,
    REMOTE_AUTH_REQUIRED,
    REMOTE_CONCURRENCY_LIMIT,
    REMOTE_CONFIRMATION_EXPIRED,
    REMOTE_CONFIRMATION_INVALID,
    REMOTE_CONFIRMATION_REQUIRED,
    REMOTE_CONFIRMATION_TIMEOUT_SECONDS,
    REMOTE_EXECUTION_FAILED,
    REMOTE_PERMISSION_DENIED,
    REMOTE_RATE_LIMITED,
    REMOTE_SAFETY_REJECTED,
    REMOTE_STOPPED,
    REMOTE_TARGET_INVALID,
    REMOTE_TARGET_STALE,
    REMOTE_VERIFICATION_FAILED,
    REMOTE_ACTION_TTL_SECONDS,
)
from app.remote.emergency import EmergencyStopController
from app.remote.permissions import PhonePermissionScope
from app.remote.rate_limiter import RemoteActionRateLimiter
from app.remote.remote_actions import (
    REMOTE_ACTION_ALLOWLIST,
    RemoteActionRequest,
    RemoteActionResult,
    RemoteActionRiskLevel,
    RemoteActionType,
)
from app.remote.remote_action_safety import RemoteActionSafetyGate, SafetyEvaluationResult

logger = logging.getLogger("NRAI.RemoteActionSession")


class RemoteActionState(str, Enum):
    RECEIVED = "RECEIVED"
    AUTHENTICATING = "AUTHENTICATING"
    AUTHORIZED = "AUTHORIZED"
    VALIDATING = "VALIDATING"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    # Failure / Terminal States
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    STOPPED = "STOPPED"
    EXPIRED = "EXPIRED"


# Permitted state transition map
VALID_TRANSITIONS: Dict[RemoteActionState, Set[RemoteActionState]] = {
    RemoteActionState.RECEIVED: {
        RemoteActionState.AUTHENTICATING,
        RemoteActionState.REJECTED,
        RemoteActionState.STOPPED,
    },
    RemoteActionState.AUTHENTICATING: {
        RemoteActionState.AUTHORIZED,
        RemoteActionState.REJECTED,
        RemoteActionState.STOPPED,
    },
    RemoteActionState.AUTHORIZED: {
        RemoteActionState.VALIDATING,
        RemoteActionState.REJECTED,
        RemoteActionState.STOPPED,
    },
    RemoteActionState.VALIDATING: {
        RemoteActionState.AWAITING_CONFIRMATION,
        RemoteActionState.EXECUTING,
        RemoteActionState.REJECTED,
        RemoteActionState.STOPPED,
    },
    RemoteActionState.AWAITING_CONFIRMATION: {
        RemoteActionState.CONFIRMED,
        RemoteActionState.CANCELLED,
        RemoteActionState.EXPIRED,
        RemoteActionState.STOPPED,
        RemoteActionState.FAILED,
    },
    RemoteActionState.CONFIRMED: {
        RemoteActionState.EXECUTING,
        RemoteActionState.STOPPED,
        RemoteActionState.FAILED,
    },
    RemoteActionState.EXECUTING: {
        RemoteActionState.VERIFYING,
        RemoteActionState.COMPLETED,
        RemoteActionState.FAILED,
        RemoteActionState.STOPPED,
    },
    RemoteActionState.VERIFYING: {
        RemoteActionState.COMPLETED,
        RemoteActionState.FAILED,
        RemoteActionState.STOPPED,
    },
    # Terminal states have no valid outgoing transitions
    RemoteActionState.COMPLETED: set(),
    RemoteActionState.REJECTED: set(),
    RemoteActionState.FAILED: set(),
    RemoteActionState.CANCELLED: set(),
    RemoteActionState.STOPPED: set(),
    RemoteActionState.EXPIRED: set(),
}


class RemoteActionStateError(Exception):
    """Raised when an illegal state transition is attempted."""
    pass


@dataclass
class RemoteActionSession:
    """Represents the complete lifecycle of a single scoped remote action execution."""
    session_id: str
    action_id: str
    device_id: str
    request: RemoteActionRequest
    state: RemoteActionState = RemoteActionState.RECEIVED
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + REMOTE_ACTION_TTL_SECONDS)
    confirmation_token: Optional[str] = None
    confirmation_expires_at: Optional[float] = None
    result: Optional[RemoteActionResult] = None
    error: Optional[str] = None
    state_history: List[Tuple[str, float, str]] = field(default_factory=list)

    def __post_init__(self):
        self.state_history.append((self.state.value, self.created_at, "Session initialized"))

    def transition_to(self, new_state: RemoteActionState, reason: str = "") -> None:
        """Transitions to new_state if the transition is allowed by the formal state machine."""
        if new_state == self.state:
            return

        valid_targets = VALID_TRANSITIONS.get(self.state, set())
        if new_state not in valid_targets:
            raise RemoteActionStateError(
                f"Invalid remote action transition from {self.state.value} to {new_state.value} (reason: {reason})"
            )

        now = time.time()
        self.state = new_state
        self.updated_at = now
        self.state_history.append((new_state.value, now, reason))

    def is_terminal(self) -> bool:
        return self.state in (
            RemoteActionState.COMPLETED,
            RemoteActionState.REJECTED,
            RemoteActionState.FAILED,
            RemoteActionState.CANCELLED,
            RemoteActionState.STOPPED,
            RemoteActionState.EXPIRED,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "action_id": self.action_id,
            "device_id": self.device_id,
            "action_type": self.request.action_type,
            "state": self.state.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
            "requires_confirmation": self.confirmation_token is not None,
            "confirmation_token": "[PRESENT]" if self.confirmation_token else None,
            "confirmation_expires_at": self.confirmation_expires_at,
            "error": self.error,
            "result": self.result.to_dict() if self.result else None,
        }


class RemoteActionSessionManager:
    """
    Coordinates remote action lifecycle, concurrency bounds, replay detection,
    deterministic safety checks, execution routing, and emergency stop integration.
    """

    def __init__(
        self,
        computer_agent: Optional[Any] = None,
        safety_gate: Optional[RemoteActionSafetyGate] = None,
        emergency_controller: Optional[EmergencyStopController] = None,
        rate_limiter: Optional[RemoteActionRateLimiter] = None,
        audit_logger: Optional[SecurityAuditLogger] = None,
        ttl_seconds: float = REMOTE_ACTION_TTL_SECONDS,
        confirmation_timeout: float = REMOTE_CONFIRMATION_TIMEOUT_SECONDS,
        max_per_device: int = MAX_CONCURRENT_REMOTE_ACTIONS_PER_DEVICE,
        max_global: int = MAX_GLOBAL_CONCURRENT_REMOTE_ACTIONS,
    ):
        self.computer_agent = computer_agent
        self.emergency_controller = emergency_controller or EmergencyStopController()
        self.safety_gate = safety_gate or RemoteActionSafetyGate(emergency_controller=self.emergency_controller)
        self.rate_limiter = rate_limiter or RemoteActionRateLimiter()
        self.audit_logger = audit_logger or SecurityAuditLogger()
        self.ttl_seconds = ttl_seconds
        self.confirmation_timeout = confirmation_timeout
        self.max_per_device = max_per_device
        self.max_global = max_global

        self._sessions: Dict[str, RemoteActionSession] = {}
        self._nonces_seen: Dict[str, float] = {}  # nonce -> timestamp
        self._lock = threading.Lock()

        # Register deterministic Emergency Stop cancellation callback
        self.emergency_controller.register_cancellation_callback(self._on_emergency_stop)

    def _on_emergency_stop(self) -> None:
        """Callback invoked immediately when Emergency Stop is activated."""
        with self._lock:
            for s_id, sess in list(self._sessions.items()):
                if not sess.is_terminal():
                    try:
                        sess.transition_to(RemoteActionState.STOPPED, reason="Emergency Stop activated")
                        sess.error = REMOTE_STOPPED
                    except Exception:
                        sess.state = RemoteActionState.STOPPED
                    self.audit_logger.log_event(
                        event_type="REMOTE_ACTION_STOPPED",
                        result="BLOCKED",
                        device_id=sess.device_id,
                        session_id=sess.session_id,
                        action=sess.request.action_type,
                        reason="Emergency Stop triggered: Active remote session halted.",
                    )

    def process_action_request(
        self,
        request: RemoteActionRequest,
        scopes: Set[PhonePermissionScope],
        cached_target: Optional[Dict[str, Any]] = None,
        current_time: Optional[float] = None,
    ) -> RemoteActionResult:
        """
        Processes an incoming remote computer action request through all security and authorization gates.
        """
        start_t = time.time()
        now = current_time if current_time is not None else start_t

        if request is None:
            return RemoteActionResult(
                action_id="unknown",
                session_id="unknown",
                device_id="unknown",
                action_type="unknown",
                status="FAILED",
                success=False,
                error=REMOTE_ACTION_MALFORMED,
                message="Request object is None or malformed.",
                duration_s=0.0,
            )

        # 1. Emergency Stop Check
        if self.emergency_controller.is_active():
            self.audit_logger.log_event(
                event_type="REMOTE_ACTION_REJECTED",
                result="BLOCKED",
                device_id=request.device_id,
                session_id=request.session_id,
                action=request.action_type,
                reason="Emergency stop is active.",
            )
            return RemoteActionResult(
                action_id=request.action_id,
                session_id=request.session_id,
                device_id=request.device_id,
                action_type=request.action_type,
                status="STOPPED",
                success=False,
                error=REMOTE_STOPPED,
                message="Computer action rejected: Emergency stop is active.",
                duration_s=time.time() - start_t,
            )

        # 2. Scope & Permission Check
        if PhonePermissionScope.APPROVED_COMPUTER_ACTION not in scopes:
            self.audit_logger.log_event(
                event_type="REMOTE_ACTION_REJECTED",
                result="DENIED",
                device_id=request.device_id,
                session_id=request.session_id,
                action=request.action_type,
                reason="Missing APPROVED_COMPUTER_ACTION permission scope.",
            )
            return RemoteActionResult(
                action_id=request.action_id,
                session_id=request.session_id,
                device_id=request.device_id,
                action_type=request.action_type,
                status="DENIED",
                success=False,
                error=REMOTE_PERMISSION_DENIED,
                message=f"Action '{request.action_type}' requires scope 'APPROVED_COMPUTER_ACTION'.",
                duration_s=time.time() - start_t,
            )

        # 3. Rate Limiting Check
        allowed, r_msg, _ = self.rate_limiter.allow_request(f"remote_action:{request.device_id}", current_time=now)
        if not allowed:
            self.audit_logger.log_event(
                event_type="REMOTE_ACTION_RATE_LIMIT",
                result="BLOCKED",
                device_id=request.device_id,
                session_id=request.session_id,
                action=request.action_type,
                reason=r_msg,
            )
            return RemoteActionResult(
                action_id=request.action_id,
                session_id=request.session_id,
                device_id=request.device_id,
                action_type=request.action_type,
                status="DENIED",
                success=False,
                error=REMOTE_RATE_LIMITED,
                message=r_msg,
                duration_s=time.time() - start_t,
            )

        # 4. Replay Protection & Concurrency Limits (under lock)
        with self._lock:
            # Replay check
            nonce_key = f"{request.device_id}:{request.request_nonce}"
            if nonce_key in self._nonces_seen:
                self.audit_logger.log_event(
                    event_type="REMOTE_ACTION_REPLAY",
                    result="BLOCKED",
                    device_id=request.device_id,
                    session_id=request.session_id,
                    action=request.action_type,
                    reason="Duplicate nonce detected.",
                )
                return RemoteActionResult(
                    action_id=request.action_id,
                    session_id=request.session_id,
                    device_id=request.device_id,
                    action_type=request.action_type,
                    status="DENIED",
                    success=False,
                    error=REMOTE_ACTION_REPLAYED,
                    message="Request nonce has already been used.",
                    duration_s=time.time() - start_t,
                )
            self._nonces_seen[nonce_key] = now

            # Clean expired nonces (older than 300s)
            for nk, n_ts in list(self._nonces_seen.items()):
                if now - n_ts > 300.0:
                    del self._nonces_seen[nk]

            # Concurrency check
            active_dev = sum(
                1 for s in self._sessions.values()
                if s.device_id == request.device_id and not s.is_terminal()
            )
            if active_dev >= self.max_per_device:
                return RemoteActionResult(
                    action_id=request.action_id,
                    session_id=request.session_id,
                    device_id=request.device_id,
                    action_type=request.action_type,
                    status="DENIED",
                    success=False,
                    error=REMOTE_CONCURRENCY_LIMIT,
                    message="Device already has an active remote computer action in progress.",
                    duration_s=time.time() - start_t,
                )

            active_global = sum(1 for s in self._sessions.values() if not s.is_terminal())
            if active_global >= self.max_global:
                return RemoteActionResult(
                    action_id=request.action_id,
                    session_id=request.session_id,
                    device_id=request.device_id,
                    action_type=request.action_type,
                    status="DENIED",
                    success=False,
                    error=REMOTE_CONCURRENCY_LIMIT,
                    message="System global remote action concurrency limit reached.",
                    duration_s=time.time() - start_t,
                )

            # Create session
            session = RemoteActionSession(
                session_id=request.session_id,
                action_id=request.action_id,
                device_id=request.device_id,
                request=request,
                state=RemoteActionState.RECEIVED,
                created_at=now,
                updated_at=now,
                expires_at=now + self.ttl_seconds,
            )
            session.transition_to(RemoteActionState.AUTHENTICATING, "Phone session verified")
            session.transition_to(RemoteActionState.AUTHORIZED, "Permission scope confirmed")
            session.transition_to(RemoteActionState.VALIDATING, "Passing to safety gate")
            self._sessions[request.session_id] = session

        # 5. Deterministic Safety Evaluation
        safety_eval: SafetyEvaluationResult = self.safety_gate.evaluate_request(
            request=request,
            cached_target=cached_target,
            current_time=now,
        )

        if not safety_eval.passed:
            with self._lock:
                session.transition_to(RemoteActionState.REJECTED, reason=safety_eval.reason)
                session.error = safety_eval.decision_code

            self.audit_logger.log_event(
                event_type="REMOTE_ACTION_REJECTED",
                result="DENIED",
                device_id=request.device_id,
                session_id=request.session_id,
                action=request.action_type,
                reason=safety_eval.reason,
                metadata={"code": safety_eval.decision_code},
            )
            res = RemoteActionResult(
                action_id=request.action_id,
                session_id=request.session_id,
                device_id=request.device_id,
                action_type=request.action_type,
                status="DENIED",
                success=False,
                error=safety_eval.decision_code,
                message=safety_eval.reason,
                risk_level=safety_eval.risk_level.value,
                duration_s=time.time() - start_t,
            )
            session.result = res
            return res

        # 6. High-Risk Confirmation Gating
        if safety_eval.requires_confirmation:
            conf_token = f"CONF-{secrets.token_hex(8)}"
            conf_expiry = now + self.confirmation_timeout
            with self._lock:
                session.transition_to(RemoteActionState.AWAITING_CONFIRMATION, reason=safety_eval.reason)
                session.confirmation_token = conf_token
                session.confirmation_expires_at = conf_expiry

            self.audit_logger.log_event(
                event_type="REMOTE_ACTION_CONFIRMATION_REQUIRED",
                result="SUCCESS",
                device_id=request.device_id,
                session_id=request.session_id,
                action=request.action_type,
                reason=safety_eval.reason,
                metadata={"risk": safety_eval.risk_level.value},
            )
            res = RemoteActionResult(
                action_id=request.action_id,
                session_id=request.session_id,
                device_id=request.device_id,
                action_type=request.action_type,
                status="AWAITING_CONFIRMATION",
                success=True,
                risk_level=safety_eval.risk_level.value,
                requires_confirmation=True,
                confirmation_token=conf_token,
                message=f"Action '{request.action_type}' requires explicit confirmation: {safety_eval.reason}",
                data={"confirmation_expires_at": conf_expiry},
                duration_s=time.time() - start_t,
            )
            session.result = res
            return res

        # 7. Safe Execution Dispatch
        return self._execute_session_action(session, start_t)

    def confirm_action(
        self,
        session_id: str,
        action_id: str,
        device_id: str,
        confirmation_token: str,
        confirmed: bool,
        current_time: Optional[float] = None,
    ) -> Tuple[bool, str, Optional[RemoteActionResult]]:
        """
        Confirms or cancels a pending high-risk remote computer action.
        """
        start_t = time.time()
        now = current_time if current_time is not None else start_t

        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return False, "Session not found.", None

            if session.device_id != device_id:
                return False, "Device ID mismatch for confirmation.", None

            if session.action_id != action_id:
                return False, "Action ID mismatch for confirmation.", None

            if session.state != RemoteActionState.AWAITING_CONFIRMATION:
                return False, f"Action is not awaiting confirmation (state: {session.state.value}).", None

            # Emergency stop check
            if self.emergency_controller.is_active():
                session.transition_to(RemoteActionState.STOPPED, reason="Emergency Stop active during confirmation")
                res = RemoteActionResult(
                    action_id=session.action_id,
                    session_id=session.session_id,
                    device_id=session.device_id,
                    action_type=session.request.action_type,
                    status="STOPPED",
                    success=False,
                    error=REMOTE_STOPPED,
                    message="Confirmation aborted: Emergency stop is active.",
                    duration_s=time.time() - start_t,
                )
                session.result = res
                return False, "Emergency stop active", res

            # Cancellation requested by user
            if not confirmed:
                session.transition_to(RemoteActionState.CANCELLED, reason="User declined confirmation")
                self.audit_logger.log_event(
                    event_type="REMOTE_ACTION_CANCELLED",
                    result="SUCCESS",
                    device_id=device_id,
                    session_id=session_id,
                    action=session.request.action_type,
                    reason="Action cancelled by user.",
                )
                res = RemoteActionResult(
                    action_id=session.action_id,
                    session_id=session.session_id,
                    device_id=session.device_id,
                    action_type=session.request.action_type,
                    status="CANCELLED",
                    success=False,
                    message="Action was cancelled by user.",
                    duration_s=time.time() - start_t,
                )
                session.result = res
                return True, "Action cancelled.", res

            # Check expiration
            if session.confirmation_expires_at and now > session.confirmation_expires_at:
                session.transition_to(RemoteActionState.EXPIRED, reason="Confirmation window expired")
                self.audit_logger.log_event(
                    event_type="REMOTE_CONFIRMATION_EXPIRED",
                    result="BLOCKED",
                    device_id=device_id,
                    session_id=session_id,
                    action=session.request.action_type,
                    reason="Confirmation token expired.",
                )
                res = RemoteActionResult(
                    action_id=session.action_id,
                    session_id=session.session_id,
                    device_id=session.device_id,
                    action_type=session.request.action_type,
                    status="FAILED",
                    success=False,
                    error=REMOTE_CONFIRMATION_EXPIRED,
                    message="Confirmation token has expired.",
                    duration_s=time.time() - start_t,
                )
                session.result = res
                return False, REMOTE_CONFIRMATION_EXPIRED, res

            # Token validation
            if not session.confirmation_token or session.confirmation_token != confirmation_token:
                self.audit_logger.log_event(
                    event_type="REMOTE_CONFIRMATION_INVALID",
                    result="DENIED",
                    device_id=device_id,
                    session_id=session_id,
                    action=session.request.action_type,
                    reason="Invalid confirmation token provided.",
                )
                res = RemoteActionResult(
                    action_id=session.action_id,
                    session_id=session.session_id,
                    device_id=session.device_id,
                    action_type=session.request.action_type,
                    status="DENIED",
                    success=False,
                    error=REMOTE_CONFIRMATION_INVALID,
                    message="Invalid confirmation token.",
                    duration_s=time.time() - start_t,
                )
                session.result = res
                return False, REMOTE_CONFIRMATION_INVALID, res

            session.transition_to(RemoteActionState.CONFIRMED, reason="Valid confirmation token presented")

        # Execute confirmed action
        result = self._execute_session_action(session, start_t, user_confirmed=True)
        return result.success, result.message, result

    def cancel_action(self, session_id: str, reason: str = "") -> Tuple[bool, str]:
        """Cancels an active or pending remote action session."""
        with self._lock:
            sess = self._sessions.get(session_id)
            if not sess:
                return False, "Session not found."
            if sess.is_terminal():
                return False, f"Session is already in terminal state ({sess.state.value})."

            sess.transition_to(RemoteActionState.CANCELLED, reason=reason or "Explicit cancel request")
            self.audit_logger.log_event(
                event_type="REMOTE_ACTION_CANCELLED",
                result="SUCCESS",
                device_id=sess.device_id,
                session_id=sess.session_id,
                action=sess.request.action_type,
                reason=reason or "Cancelled by caller",
            )
            return True, "Session cancelled successfully."

    def get_session(self, session_id: str) -> Optional[RemoteActionSession]:
        with self._lock:
            return self._sessions.get(session_id)

    def _execute_session_action(
        self,
        session: RemoteActionSession,
        start_t: float,
        user_confirmed: bool = False,
    ) -> RemoteActionResult:
        """Executes the action via UnifiedComputerAgent or ToolRegistry with state tracking."""
        with self._lock:
            session.transition_to(RemoteActionState.EXECUTING, "Dispatching to UnifiedComputerAgent")

        req = session.request
        act_type = req.action_type
        params = dict(req.parameters)

        # Emergency stop check immediately before dispatch
        if self.emergency_controller.is_active():
            with self._lock:
                session.transition_to(RemoteActionState.STOPPED, reason="Emergency Stop active before dispatch")
                session.error = REMOTE_STOPPED

            res = RemoteActionResult(
                action_id=req.action_id,
                session_id=req.session_id,
                device_id=req.device_id,
                action_type=act_type,
                status="STOPPED",
                success=False,
                error=REMOTE_STOPPED,
                message="Execution halted: Emergency stop is active.",
                duration_s=time.time() - start_t,
            )
            session.result = res
            return res

        # Execution dispatch
        exec_success = False
        exec_verified = False
        exec_data: Dict[str, Any] = {}
        exec_message = ""
        exec_error: Optional[str] = None
        try:
            if self.computer_agent is None:
                try:
                    from app.agent.computer_agent import UnifiedComputerAgent
                    self.computer_agent = UnifiedComputerAgent()
                except Exception as ce:
                    logger.error(f"Failed to auto-initialize UnifiedComputerAgent: {ce}")

            if self.computer_agent is not None:
                # 1. Autonomous Workflow
                if act_type == RemoteActionType.RUN_WORKFLOW.value:
                    goal = str(params.get("goal", ""))
                    wf_report = self.computer_agent.execute_workflow(
                        user_goal=goal,
                        user_confirmed=user_confirmed,
                    )
                    exec_success = getattr(wf_report, "success", False)
                    exec_verified = exec_success
                    exec_data = wf_report.to_dict() if hasattr(wf_report, "to_dict") else {}
                    exec_message = getattr(wf_report, "summary", "Workflow completed")
                    exec_error = getattr(wf_report, "error", None)

                # 2. Individual Approved Tools
                elif hasattr(self.computer_agent, "tool_registry"):
                    tool_res = self.computer_agent.tool_registry.execute_tool(
                        tool_name=act_type,
                        params=params,
                        user_confirmed=user_confirmed,
                    )
                    exec_success = getattr(tool_res, "success", False)
                    exec_verified = getattr(tool_res, "verified", False)
                    exec_data = getattr(tool_res, "data", {})
                    exec_message = getattr(tool_res, "message", "")
                    exec_error = getattr(tool_res, "error", None)

                # 3. Direct agent callable / mock
                elif callable(self.computer_agent):
                    mock_res = self.computer_agent(act_type, params)
                    if isinstance(mock_res, dict):
                        exec_success = mock_res.get("success", False)
                        exec_verified = mock_res.get("verified", False)
                        exec_data = mock_res.get("data", {})
                        exec_message = mock_res.get("message", "Executed successfully")
                        exec_error = mock_res.get("error")
                    else:
                        exec_success = True
                        exec_verified = True
                        exec_message = str(mock_res)
                else:
                    exec_success = False
                    exec_verified = False
                    exec_error = "INVALID_COMPUTER_AGENT"
                    exec_message = f"Computer agent of type {type(self.computer_agent)} does not support tool execution."
            else:
                exec_success = False
                exec_verified = False
                exec_error = "NO_COMPUTER_AGENT"
                exec_message = f"Cannot execute {act_type}: UnifiedComputerAgent is not available."

        except Exception as ex:
            logger.exception(f"Execution error for {act_type}: {ex}")
            exec_success = False
            exec_verified = False
            exec_error = REMOTE_EXECUTION_FAILED
            exec_message = f"Execution failed: {str(ex)}"

        duration = time.time() - start_t

        with self._lock:
            # Check if stopped mid-execution
            if self.emergency_controller.is_active():
                session.transition_to(RemoteActionState.STOPPED, reason="Emergency stop fired during execution")
                session.error = REMOTE_STOPPED
                final_status = "STOPPED"
                exec_success = False
                exec_error = REMOTE_STOPPED
                exec_message = "Execution stopped by Emergency Stop."
            elif exec_success:
                session.transition_to(RemoteActionState.VERIFYING, "Checking state verification")
                session.transition_to(RemoteActionState.COMPLETED, "Execution and verification successful")
                final_status = "SUCCESS"
            else:
                session.transition_to(RemoteActionState.FAILED, reason=exec_message)
                session.error = exec_error or REMOTE_EXECUTION_FAILED
                final_status = "FAILED"

        # Audit logging (safe redacted parameters)
        safe_meta = self.safety_gate.redact_parameters(params)
        safe_meta.update({
            "action_id": req.action_id,
            "duration_s": round(duration, 3),
            "verified": exec_verified,
        })

        self.audit_logger.log_event(
            event_type="REMOTE_ACTION_COMPLETED" if exec_success else "REMOTE_ACTION_FAILED",
            result="SUCCESS" if exec_success else "ERROR",
            device_id=req.device_id,
            session_id=req.session_id,
            action=act_type,
            reason=exec_message,
            metadata=safe_meta,
        )

        res = RemoteActionResult(
            action_id=req.action_id,
            session_id=req.session_id,
            device_id=req.device_id,
            action_type=act_type,
            status=final_status,
            success=exec_success,
            verified=exec_verified,
            message=exec_message,
            data=exec_data,
            error=exec_error,
            duration_s=duration,
        )
        session.result = res
        return res
