"""
NR-AI SkyShield: Safe Response Engine.
Step 10 Phase 4 — Security Intelligence, Incident Response & Finalization.

Coordinates gated execution of defensive security response actions.
Implements the 4-stage response pipeline:
  AI Proposal -> Deterministic Security Gate -> Authorization Gate -> Human Confirmation -> Execution

Strictly prevents autonomous execution of high-impact actions without explicit human approval.
Integrates with EmergencyStopController to halt all response actions immediately.
"""

from datetime import datetime, timezone
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from app.remote.emergency import EmergencyStopController
from app.security.incident_models import (
    HIGH_IMPACT_ACTIONS,
    ActionConfirmationStatus,
    SafeActionProposal,
    SafeResponseAction,
)
from app.security.models import SecurityPolicy

logger = logging.getLogger("NRAI.SkyShield.SafeResponse")


class SafeResponseEngine:
    """
    Gated response coordinator enforcing defense-in-depth authorization.
    All high-impact actions require explicit operator confirmation.
    """

    def __init__(
        self,
        emergency_stop: Optional[EmergencyStopController] = None,
        audit_logger_fn: Optional[Callable[..., None]] = None,
    ):
        self.emergency_stop = emergency_stop or EmergencyStopController()
        self._audit_logger = audit_logger_fn or (lambda **kwargs: None)
        self._proposals: Dict[str, SafeActionProposal] = {}
        self._lock = threading.RLock()
        self._proposal_counter = 0

        # Register callback with emergency stop
        self.emergency_stop.register_cancellation_callback(self._on_emergency_stop_fired)

    def _on_emergency_stop_fired(self) -> None:
        """Immediately cancels all pending proposals when emergency stop activates."""
        with self._lock:
            for prop in self._proposals.values():
                if prop.status == ActionConfirmationStatus.PENDING_CONFIRMATION:
                    prop.status = ActionConfirmationStatus.CANCELLED
                    prop.reason += " [CANCELLED: Emergency Stop Triggered]"
            logger.warning("SafeResponseEngine: All pending action proposals cancelled due to Emergency Stop.")

    def is_emergency_stopped(self) -> bool:
        if callable(getattr(self.emergency_stop, "is_active", None)):
            return self.emergency_stop.is_active()
        return bool(getattr(self.emergency_stop, "_is_active", False))

    def propose_action(
        self,
        action: Union[str, SafeResponseAction],
        device_id: str,
        incident_id: Optional[str] = None,
        reason: str = "",
        initiated_by: str = "Operator",
        details: Optional[Dict[str, Any]] = None,
    ) -> SafeActionProposal:
        """
        Creates a gated action proposal.
        Determines deterministically if human confirmation is required.
        """
        with self._lock:
            if self.is_emergency_stopped():
                raise RuntimeError("Cannot propose response actions while Emergency Stop is active.")

            act_str = action.value if hasattr(action, "value") else str(action)
            try:
                act_enum = SafeResponseAction(act_str)
            except ValueError:
                raise ValueError(f"Invalid response action '{act_str}'. Allowed: {[a.value for a in SafeResponseAction]}")

            # 1. Deterministic Security Gate Check
            if not SecurityPolicy.is_action_permitted(act_str):
                raise PermissionError(f"SecurityPolicy strictly prohibits action '{act_str}'.")

            # 2. Check if human confirmation is required
            requires_human = act_enum in HIGH_IMPACT_ACTIONS or initiated_by.startswith("AI")

            self._proposal_counter += 1
            prop_id = f"prop_{int(time.time())}_{self._proposal_counter:04d}"
            proposal = SafeActionProposal(
                proposal_id=prop_id,
                action=act_str,
                device_id=device_id,
                incident_id=incident_id,
                reason=reason,
                requires_human_confirmation=requires_human,
                status=ActionConfirmationStatus.PENDING_CONFIRMATION,
                initiated_by=initiated_by,
                details=details or {},
            )
            self._proposals[prop_id] = proposal

            self._audit_logger(
                initiator=initiated_by,
                operation="PROPOSE_SAFE_RESPONSE",
                target=device_id,
                result="PENDING_CONFIRMATION" if requires_human else "AUTO_READY",
                details={
                    "proposal_id": prop_id,
                    "action": act_str,
                    "requires_human_confirmation": requires_human,
                    "reason": reason,
                },
            )
            return proposal

    def confirm_and_execute_action(
        self,
        proposal_id: str,
        operator_confirmed: bool = True,
        confirmed_by: str = "Operator",
        executor_fn: Optional[Callable[[SafeActionProposal], Tuple[bool, str]]] = None,
    ) -> Tuple[bool, str, Optional[SafeActionProposal]]:
        """
        Confirms a pending response action and dispatches it through the executor.
        If operator_confirmed is False for a high-impact action, execution is strictly rejected.
        """
        with self._lock:
            if self.is_emergency_stopped():
                return False, "EMERGENCY_STOP_ACTIVE", None

            proposal = self._proposals.get(proposal_id)
            if not proposal:
                return False, "PROPOSAL_NOT_FOUND", None

            if proposal.status != ActionConfirmationStatus.PENDING_CONFIRMATION:
                return False, f"PROPOSAL_ALREADY_{proposal.status.value}", proposal

            # Check human confirmation invariant
            if proposal.requires_human_confirmation and not operator_confirmed:
                proposal.status = ActionConfirmationStatus.REJECTED
                self._audit_logger(
                    initiator=confirmed_by,
                    operation="REJECT_SAFE_RESPONSE",
                    target=proposal.device_id,
                    result="REJECTED_BY_OPERATOR",
                    details={"proposal_id": proposal_id, "action": proposal.action},
                )
                return False, "OPERATOR_CONFIRMATION_REFUSED", proposal

            # Mark confirmed
            proposal.status = ActionConfirmationStatus.CONFIRMED
            proposal.confirmed_by = confirmed_by

            # Dispatch execution if executor provided
            if executor_fn:
                try:
                    success, msg = executor_fn(proposal)
                    if success:
                        proposal.status = ActionConfirmationStatus.EXECUTED
                        proposal.executed_at = time.time()
                        self._audit_logger(
                            initiator=confirmed_by,
                            operation="EXECUTE_SAFE_RESPONSE",
                            target=proposal.device_id,
                            result="SUCCESS",
                            details={"proposal_id": proposal_id, "action": proposal.action, "output": msg},
                        )
                        return True, msg, proposal
                    else:
                        self._audit_logger(
                            initiator=confirmed_by,
                            operation="EXECUTE_SAFE_RESPONSE",
                            target=proposal.device_id,
                            result="FAILED",
                            details={"proposal_id": proposal_id, "action": proposal.action, "error": msg},
                        )
                        return False, msg, proposal
                except Exception as ex:
                    logger.error(f"Executor failed for proposal {proposal_id}: {ex}")
                    return False, f"EXECUTION_EXCEPTION: {ex}", proposal

            proposal.status = ActionConfirmationStatus.EXECUTED
            proposal.executed_at = time.time()
            return True, "CONFIRMED_AND_LOGGED", proposal

    def list_proposals(self, device_id: Optional[str] = None, limit: int = 50) -> List[SafeActionProposal]:
        """Lists proposals up to limit."""
        with self._lock:
            res = list(self._proposals.values())
            if device_id:
                res = [p for p in res if p.device_id == device_id]
            res.sort(key=lambda x: x.proposal_id, reverse=True)
            return res[:limit]

    def get_proposal(self, proposal_id: str) -> Optional[SafeActionProposal]:
        with self._lock:
            return self._proposals.get(proposal_id)
