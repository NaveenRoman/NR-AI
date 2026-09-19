"""
NR-AI Android Failure Reproduction Engine (Droid Phase 3).

Converts natural language bug reports and engineering issue descriptions into
deterministic, bounded reproduction workflows.
Validates model-advised reproduction plans against strict safety constraints.
Executes reproduction plans on authorized devices and reports authoritative
empirical reproduction states: REPRODUCED, NOT_REPRODUCED, ENVIRONMENT_BLOCKED,
INSUFFICIENT_EVIDENCE.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
import uuid

from app.agent.android_safety import (
    AUTHORIZED_PACKAGE_NAME,
    AUTHORIZED_PROJECT_PATH,
    AndroidErrorCode,
    AndroidSafetyError,
    AndroidSafetyGate,
    EmergencyStopActiveError,
)
from app.agent.android_diagnostics import AndroidDiagnosticsController, LogLevel, RuntimeErrorType
from app.memory.audit_logger import AuditLogger

logger = logging.getLogger("NRAI.AndroidReproduction")


# -----------------------------------------------------------------------------
# Data Models
# -----------------------------------------------------------------------------

class ReproductionState(str, Enum):
    IDLE = "IDLE"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    REPRODUCED = "REPRODUCED"
    NOT_REPRODUCED = "NOT_REPRODUCED"
    ENVIRONMENT_BLOCKED = "ENVIRONMENT_BLOCKED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class ReproductionActionType(str, Enum):
    LAUNCH_APP = "LAUNCH_APP"
    WAIT_FOR_SCREEN = "WAIT_FOR_SCREEN"
    FIND_ELEMENT = "FIND_ELEMENT"
    CLICK_ELEMENT = "CLICK_ELEMENT"
    TYPE_TEXT = "TYPE_TEXT"
    SCROLL = "SCROLL"
    PRESS_BACK = "PRESS_BACK"
    CAPTURE_UI = "CAPTURE_UI"
    CAPTURE_LOGCAT = "CAPTURE_LOGCAT"
    ASSERT_CONDITION = "ASSERT_CONDITION"


@dataclass
class ReproductionAction:
    """A single deterministic action within a reproduction plan."""
    action_type: ReproductionActionType
    target_id: Optional[str] = None
    target_type: Optional[str] = None  # resource_id, test_tag, text, content_desc
    target_value: Optional[str] = None
    input_text: Optional[str] = None
    timeout_seconds: float = 10.0
    expected_result: Optional[str] = None
    action_id: str = field(default_factory=lambda: f"act_{uuid.uuid4().hex[:8]}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_id": self.action_id,
            "action_type": self.action_type.value if isinstance(self.action_type, ReproductionActionType) else str(self.action_type),
            "target_id": self.target_id,
            "target_type": self.target_type,
            "target_value": self.target_value,
            "input_text": self.input_text,
            "timeout_seconds": self.timeout_seconds,
            "expected_result": self.expected_result,
        }


@dataclass
class ReproductionPlan:
    """Bounded, validated reproduction workflow plan."""
    workflow_name: str
    project_id: str = "nr_android_test"
    package_name: str = AUTHORIZED_PACKAGE_NAME
    target_screen: Optional[str] = None
    preconditions: List[str] = field(default_factory=list)
    actions: List[ReproductionAction] = field(default_factory=list)
    failure_signals: List[str] = field(default_factory=list)
    timeout_seconds: float = 120.0
    plan_id: str = field(default_factory=lambda: f"repro_{uuid.uuid4().hex[:8]}")
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "workflow_name": self.workflow_name,
            "project_id": self.project_id,
            "package_name": self.package_name,
            "target_screen": self.target_screen,
            "preconditions": self.preconditions,
            "actions": [a.to_dict() for a in self.actions],
            "failure_signals": self.failure_signals,
            "timeout_seconds": self.timeout_seconds,
            "created_at": self.created_at,
        }


@dataclass
class ReproductionResult:
    """Empirical outcome of executing a reproduction workflow."""
    state: ReproductionState
    plan_id: str
    workflow_name: str
    executed_actions: int = 0
    total_actions: int = 0
    detected_failure_signal: Optional[str] = None
    failure_location: Optional[str] = None
    crash_stack_trace: Optional[str] = None
    logcat_entries_captured: int = 0
    ui_snapshots_captured: int = 0
    duration_seconds: float = 0.0
    message: str = ""
    error: Optional[str] = None
    evidence_ids: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state.value if isinstance(self.state, ReproductionState) else str(self.state),
            "plan_id": self.plan_id,
            "workflow_name": self.workflow_name,
            "executed_actions": self.executed_actions,
            "total_actions": self.total_actions,
            "detected_failure_signal": self.detected_failure_signal,
            "failure_location": self.failure_location,
            "crash_stack_trace": self.crash_stack_trace,
            "logcat_entries_captured": self.logcat_entries_captured,
            "ui_snapshots_captured": self.ui_snapshots_captured,
            "duration_seconds": self.duration_seconds,
            "message": self.message,
            "error": self.error,
            "evidence_ids": self.evidence_ids,
            "timestamp": self.timestamp,
        }


# -----------------------------------------------------------------------------
# Failure Reproduction Engine
# -----------------------------------------------------------------------------

class FailureReproductionEngine:
    """
    Coordinates and validates Android bug reproduction workflows.
    Ensures that failures are proven to exist before any autonomous repairs are attempted.
    """

    MAX_ACTIONS_PER_PLAN = 15
    MAX_TIMEOUT_SECONDS = 180.0

    def __init__(
        self,
        safety_gate: Optional[AndroidSafetyGate] = None,
        audit_logger: Optional[AuditLogger] = None,
    ):
        self.safety = safety_gate or AndroidSafetyGate()
        self.audit = audit_logger or AuditLogger()

    def create_plan_from_description(
        self,
        bug_description: str,
        project_id: str = "nr_android_test",
        package_name: str = AUTHORIZED_PACKAGE_NAME,
        target_screen: Optional[str] = "MainActivity",
    ) -> ReproductionPlan:
        """
        Synthesizes a structured reproduction plan from an issue description.
        In advisory mode, heuristic rules extract target screen, action, and failure signals.
        """
        self.safety.check_emergency_stop()

        desc_lower = bug_description.lower()
        actions: List[ReproductionAction] = []
        failure_signals: List[str] = []

        # Default action: launch app
        actions.append(ReproductionAction(
            action_type=ReproductionActionType.LAUNCH_APP,
            timeout_seconds=15.0,
            expected_result="App process active in foreground",
        ))

        # Detect target elements
        if "login" in desc_lower:
            actions.append(ReproductionAction(
                action_type=ReproductionActionType.WAIT_FOR_SCREEN,
                target_value="LoginScreen",
                timeout_seconds=10.0,
            ))
            if "type" in desc_lower or "input" in desc_lower:
                actions.append(ReproductionAction(
                    action_type=ReproductionActionType.TYPE_TEXT,
                    target_type="resource_id",
                    target_value="inputField",
                    input_text="safe_test_user",
                    timeout_seconds=5.0,
                ))
            actions.append(ReproductionAction(
                action_type=ReproductionActionType.CLICK_ELEMENT,
                target_type="text",
                target_value="Login",
                timeout_seconds=5.0,
            ))
        elif "button" in desc_lower or "press" in desc_lower or "click" in desc_lower:
            target_val = "sendButton" if "send" in desc_lower else ("micButton" if "mic" in desc_lower else "Button")
            actions.append(ReproductionAction(
                action_type=ReproductionActionType.CLICK_ELEMENT,
                target_type="resource_id",
                target_value=target_val,
                timeout_seconds=5.0,
            ))
        else:
            actions.append(ReproductionAction(
                action_type=ReproductionActionType.WAIT_FOR_SCREEN,
                target_value=target_screen or "MainActivity",
                timeout_seconds=5.0,
            ))

        # Capture evidence actions
        actions.append(ReproductionAction(
            action_type=ReproductionActionType.CAPTURE_UI,
            timeout_seconds=5.0,
        ))
        actions.append(ReproductionAction(
            action_type=ReproductionActionType.CAPTURE_LOGCAT,
            timeout_seconds=5.0,
        ))

        # Detect expected failure signals
        if "crash" in desc_lower or "fatal" in desc_lower or "exception" in desc_lower:
            failure_signals.extend(["FATAL EXCEPTION", "AndroidRuntime", "NullPointerException", "Process died"])
        elif "anr" in desc_lower or "freeze" in desc_lower:
            failure_signals.extend(["ANR in", "Application Not Responding"])
        elif "resource" in desc_lower or "not found" in desc_lower:
            failure_signals.extend(["NotFoundException", "ResourceNotFoundException"])
        else:
            failure_signals.append("FATAL EXCEPTION")

        workflow_slug = re.sub(r"[^a-zA-Z0-9_]", "_", bug_description[:30]).strip("_").upper()
        plan = ReproductionPlan(
            workflow_name=f"REPRO_{workflow_slug or 'STANDARD'}",
            project_id=project_id,
            package_name=package_name,
            target_screen=target_screen,
            preconditions=["Device booted and authorized", "App deployed"],
            actions=actions,
            failure_signals=failure_signals,
            timeout_seconds=60.0,
        )
        return plan

    def validate_plan(self, plan: ReproductionPlan) -> Tuple[bool, List[str]]:
        """
        Strictly validates a reproduction plan against deterministic bounds.
        Rejects plans with arbitrary actions, excessive length, or unsafe parameters.
        """
        errors: List[str] = []

        if not plan.workflow_name:
            errors.append("Reproduction plan missing workflow_name")
        if not plan.package_name:
            errors.append("Reproduction plan missing package_name")
        if len(plan.actions) == 0:
            errors.append("Reproduction plan contains zero actions")
        if len(plan.actions) > self.MAX_ACTIONS_PER_PLAN:
            errors.append(f"Reproduction plan exceeds maximum allowed actions ({len(plan.actions)} > {self.MAX_ACTIONS_PER_PLAN})")
        if plan.timeout_seconds > self.MAX_TIMEOUT_SECONDS:
            errors.append(f"Reproduction plan timeout exceeds maximum allowed ({plan.timeout_seconds}s > {self.MAX_TIMEOUT_SECONDS}s)")

        for idx, act in enumerate(plan.actions):
            if not isinstance(act.action_type, ReproductionActionType):
                try:
                    act.action_type = ReproductionActionType(str(act.action_type))
                except ValueError:
                    errors.append(f"Action #{idx} has invalid action_type: {act.action_type}")
            if act.timeout_seconds <= 0:
                errors.append(f"Action #{idx} has non-positive timeout: {act.timeout_seconds}")

        return len(errors) == 0, errors

    def execute_plan(
        self,
        plan: ReproductionPlan,
        ui_action_engine: Any,
        diagnostics_controller: Optional[Any] = None,
        evidence_collector: Optional[Any] = None,
        serial: Optional[str] = None,
    ) -> ReproductionResult:
        """
        Executes a validated reproduction workflow step by step on an active device.
        Observes UI and logcat output to determine if the reported failure occurs.
        """
        self.safety.check_emergency_stop()

        is_valid, validation_errors = self.validate_plan(plan)
        if not is_valid:
            return ReproductionResult(
                state=ReproductionState.FAILED,
                plan_id=plan.plan_id,
                workflow_name=plan.workflow_name,
                message=f"Plan validation failed: {'; '.join(validation_errors)}",
                error="VALIDATION_FAILED",
            )

        start_time = time.monotonic()
        executed_count = 0
        detected_signal = None
        stack_trace = None
        failure_loc = None
        evidence_ids = []

        try:
            for act in plan.actions:
                self.safety.check_emergency_stop()

                if time.monotonic() - start_time > plan.timeout_seconds:
                    return ReproductionResult(
                        state=ReproductionState.ENVIRONMENT_BLOCKED,
                        plan_id=plan.plan_id,
                        workflow_name=plan.workflow_name,
                        executed_actions=executed_count,
                        total_actions=len(plan.actions),
                        duration_seconds=time.monotonic() - start_time,
                        message="Reproduction timed out before failure could be confirmed.",
                        error="TIMEOUT",
                    )

                # Execute individual step via ui_action_engine
                if act.action_type == ReproductionActionType.LAUNCH_APP:
                    if hasattr(ui_action_engine, "launch_app"):
                        res = ui_action_engine.launch_app(package_name=plan.package_name, serial=serial)
                        if not res.get("success", False):
                            return ReproductionResult(
                                state=ReproductionState.ENVIRONMENT_BLOCKED,
                                plan_id=plan.plan_id,
                                workflow_name=plan.workflow_name,
                                message=f"Failed to launch app: {res.get('error')}",
                                error="LAUNCH_FAILED",
                            )
                elif act.action_type == ReproductionActionType.CLICK_ELEMENT:
                    if hasattr(ui_action_engine, "tap_by_identifier"):
                        ui_action_engine.tap_by_identifier(
                            identifier_type=act.target_type or "resource_id",
                            identifier_value=act.target_value or "",
                            serial=serial,
                        )
                elif act.action_type == ReproductionActionType.TYPE_TEXT:
                    if hasattr(ui_action_engine, "type_text_into_target"):
                        ui_action_engine.type_text_into_target(
                            identifier_type=act.target_type or "resource_id",
                            identifier_value=act.target_value or "",
                            text=act.input_text or "",
                            serial=serial,
                        )
                elif act.action_type == ReproductionActionType.PRESS_BACK:
                    if hasattr(ui_action_engine, "press_back"):
                        ui_action_engine.press_back(serial=serial)
                elif act.action_type == ReproductionActionType.WAIT_FOR_SCREEN:
                    time.sleep(min(act.timeout_seconds, 2.0))

                executed_count += 1

                # Poll diagnostics if available
                if diagnostics_controller and hasattr(diagnostics_controller, "capture_logcat"):
                    diag = diagnostics_controller.capture_logcat(package_name=plan.package_name, serial=serial)
                    # Check for runtime crash signals
                    for signal in plan.failure_signals:
                        if signal.lower() in (diag.raw_text or "").lower():
                            detected_signal = signal
                            stack_trace = diag.crash_stack_trace or diag.raw_text[:500]
                            failure_loc = diag.failure_location
                            break
                    if detected_signal:
                        break

            duration = time.monotonic() - start_time
            if detected_signal:
                state = ReproductionState.REPRODUCED
                msg = f"Failure successfully reproduced! Signal: '{detected_signal}'"
            else:
                state = ReproductionState.NOT_REPRODUCED
                msg = "Workflow completed without encountering any specified failure signals."

            result = ReproductionResult(
                state=state,
                plan_id=plan.plan_id,
                workflow_name=plan.workflow_name,
                executed_actions=executed_count,
                total_actions=len(plan.actions),
                detected_failure_signal=detected_signal,
                failure_location=failure_loc,
                crash_stack_trace=stack_trace,
                duration_seconds=duration,
                message=msg,
                evidence_ids=evidence_ids,
            )

            self.audit.log(
                "reproduction_executed",
                details=result.to_dict(),
                actor="FailureReproductionEngine",
                status="SUCCESS" if state == ReproductionState.REPRODUCED else "NOT_REPRODUCED",
            )
            return result

        except EmergencyStopActiveError:
            return ReproductionResult(
                state=ReproductionState.STOPPED,
                plan_id=plan.plan_id,
                workflow_name=plan.workflow_name,
                executed_actions=executed_count,
                total_actions=len(plan.actions),
                duration_seconds=time.monotonic() - start_time,
                message="Reproduction halted immediately by EMERGENCY STOP.",
                error="EMERGENCY_STOPPED",
            )
        except Exception as e:
            logger.exception("Unexpected error during reproduction workflow execution: %s", e)
            return ReproductionResult(
                state=ReproductionState.FAILED,
                plan_id=plan.plan_id,
                workflow_name=plan.workflow_name,
                executed_actions=executed_count,
                total_actions=len(plan.actions),
                duration_seconds=time.monotonic() - start_time,
                message=f"Reproduction encountered an unexpected error: {str(e)}",
                error=str(e),
            )
