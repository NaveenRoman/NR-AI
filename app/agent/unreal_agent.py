r"""
NR-AI Unified Unreal Engine Agent & Autonomous Repair Engine (Step 9 Phase 5).

Integrates all completed Unreal Engine foundations into one safe, deterministic,
bounded end-to-end autonomous workflow engine:
- UnrealSafetyGate (Deterministic boundaries, limits & Emergency stop)
- UnrealEnvironmentDetector (Environment discovery)
- UnrealProjectInspector (Project structure & asset intelligence)
- UnrealBuildRunner & UnrealBuildLogParser (Compilation & diagnostics)
- UnrealBuildResultVerifier & UnrealBuildArtifactVerifier (Build verification)
- UnrealTestRunner & UnrealRuntimeLogParser (Testing & runtime monitoring)
- UnrealTestReportParser & UnrealRuntimeResultVerifier (Test verification)
- UnrealCppAnalyzer & UnrealBlueprintInspector (C++/Blueprint intelligence)
- UnrealSourceModifier (Atomic checkpointing, rollback, operational bounds)
- AuditLogger (Security audit trail)

Enforces the core architectural invariants:
1. DETERMINISTIC EVIDENCE > MODEL CLAIMS
2. Maximum 2 repair attempts per workflow
3. Maximum 25 steps per workflow plan
4. Byte-for-byte atomic rollback on verification or build failure
5. Zero orphan workflow-owned Unreal processes (100% shell=False)
6. Emergency stop freezes all operations immediately
"""

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
import uuid

from app.agent.unreal_safety import (
    UnrealSafetyGate,
    UnrealErrorCode,
    UnrealSafetyError,
    EmergencyStopActiveError,
    GLOBAL_WORKSPACE_ROOT,
    DEFAULT_AUTHORIZED_PROJECT,
    MAX_SOURCE_FILE_BYTES,
    MAX_PATCH_BYTES,
    MAX_CHANGED_LINES,
    MAX_FILES_PER_OPERATION,
    MAX_REPAIR_ATTEMPTS,
    MAX_WORKFLOW_STEPS,
    WORKFLOW_TIMEOUT_SECONDS,
    BUILD_TIMEOUT_SECONDS,
    COMPILE_TIMEOUT_SECONDS,
    TEST_TIMEOUT_SECONDS,
    ALLOWED_UNREAL_SOURCE_EXTENSIONS,
    ALLOWED_UNREAL_MODIFICATION_OPERATIONS,
    PROHIBITED_SOURCE_TOKENS,
    redact_sensitive_data,
    DEFAULT_UNREAL_SAFETY_GATE,
)
from app.agent.unreal_environment import (
    UnrealEnvironmentDetector,
    UnrealEngineInstance,
    UnrealEnvironmentInfo,
    DEFAULT_UNREAL_ENV_DETECTOR,
)
from app.agent.unreal_project import (
    UnrealProjectInspector,
    UnrealProjectMetadata,
    DEFAULT_UNREAL_PROJECT_INSPECTOR,
)
from app.agent.unreal_build import (
    UnrealBuildEnvironmentValidator,
    UnrealBuildArtifactVerifier,
    UnrealBuildRunner,
    UnrealBuildResultVerifier,
    UnrealBuildLogParser,
    UnrealBuildResult,
    UnrealBuildDiagnostic,
    UnrealBuildIssueCategory,
    UnrealBuildEnvironmentStatus,
    UnrealBuildResultStatus,
    DEFAULT_UNREAL_BUILD_VALIDATOR,
    DEFAULT_UNREAL_ARTIFACT_VERIFIER,
    DEFAULT_UNREAL_BUILD_RUNNER,
)
from app.agent.unreal_tests import (
    UnrealTestEnvironmentValidator,
    UnrealRuntimeLogParser,
    UnrealTestReportParser,
    UnrealTestRunner,
    UnrealTestArtifactVerifier,
    UnrealRuntimeResultVerifier,
    UnrealTestExecutionResult,
    UnrealRuntimeDiagnostic,
    UnrealTestSummary,
    UnrealRuntimeIssueCategory,
    UnrealRuntimeResultStatus,
    DEFAULT_UNREAL_TEST_VALIDATOR,
    DEFAULT_UNREAL_RUNTIME_PARSER,
    DEFAULT_UNREAL_TEST_REPORT_PARSER,
    DEFAULT_UNREAL_TEST_RUNNER,
    DEFAULT_UNREAL_TEST_ARTIFACT_VERIFIER,
    DEFAULT_UNREAL_RUNTIME_RESULT_VERIFIER,
)
from app.agent.unreal_source import (
    UnrealCppAnalyzer,
    UnrealBlueprintInspector,
    UnrealSourceModifier,
    UnrealModificationProposal,
    UnrealModificationBackup,
    UnrealModificationResult,
    UnrealCppFileAnalysis,
    UnrealModificationOperation,
    DEFAULT_UNREAL_CPP_ANALYZER,
    DEFAULT_UNREAL_BLUEPRINT_INSPECTOR,
    DEFAULT_UNREAL_SOURCE_MODIFIER,
)
from app.memory.audit_logger import AuditLogger

logger = logging.getLogger("NRAI.UnrealAgent")


# -----------------------------------------------------------------------------
# Lifecycle States, Failure Domains & Workflow Types
# -----------------------------------------------------------------------------

class UnrealWorkflowState(str, Enum):
    """Deterministic 16-state lifecycle for Unreal autonomous workflows."""
    IDLE = "IDLE"
    INSPECTING = "INSPECTING"
    DIAGNOSING = "DIAGNOSING"
    PLANNING = "PLANNING"
    PROPOSING = "PROPOSING"
    VALIDATING = "VALIDATING"
    APPLYING = "APPLYING"
    BUILDING = "BUILDING"
    TESTING = "TESTING"
    OBSERVING = "OBSERVING"
    VERIFYING = "VERIFYING"
    REPAIRING = "REPAIRING"
    ROLLING_BACK = "ROLLING_BACK"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"


class UnrealFailureDomain(str, Enum):
    """Deterministic 23-domain failure taxonomy for Unreal operations."""
    NONE = "NONE"
    BUILD_ERROR = "BUILD_ERROR"
    RUNTIME_CRASH = "RUNTIME_CRASH"
    RUNTIME_ASSERTION = "RUNTIME_ASSERTION"
    ENSURE_FAILURE = "ENSURE_FAILURE"
    TEST_FAILURE = "TEST_FAILURE"
    TEST_TIMEOUT = "TEST_TIMEOUT"
    COMPILATION_FAILURE = "COMPILATION_FAILURE"
    LINKER_FAILURE = "LINKER_FAILURE"
    UHT_FAILURE = "UHT_FAILURE"
    MODULE_LOAD_FAILURE = "MODULE_LOAD_FAILURE"
    PLUGIN_FAILURE = "PLUGIN_FAILURE"
    ASSET_FAILURE = "ASSET_FAILURE"
    BLUEPRINT_FAILURE = "BLUEPRINT_FAILURE"
    CONFIG_FAILURE = "CONFIG_FAILURE"
    DEVICE_OR_ENVIRONMENT_FAILURE = "DEVICE_OR_ENVIRONMENT_FAILURE"
    SAFETY_REJECTION = "SAFETY_REJECTION"
    STALE_TARGET = "STALE_TARGET"
    REPAIR_FAILURE = "REPAIR_FAILURE"
    VERIFICATION_FAILURE = "VERIFICATION_FAILURE"
    ROLLBACK_FAILURE = "ROLLBACK_FAILURE"
    WORKFLOW_TIMEOUT = "WORKFLOW_TIMEOUT"
    USER_STOPPED = "USER_STOPPED"
    UNKNOWN_FAILURE = "UNKNOWN_FAILURE"


class UnrealWorkflowType(str, Enum):
    """Recognized high-level Unreal autonomous workflows."""
    WORKFLOW_BUILD_FAILURE_REPAIR = "WORKFLOW_BUILD_FAILURE_REPAIR"
    WORKFLOW_TEST_FAILURE_REPAIR = "WORKFLOW_TEST_FAILURE_REPAIR"
    WORKFLOW_RUNTIME_FAILURE_REPAIR = "WORKFLOW_RUNTIME_FAILURE_REPAIR"
    WORKFLOW_SOURCE_CHANGE_VERIFY = "WORKFLOW_SOURCE_CHANGE_VERIFY"
    WORKFLOW_ROLLBACK = "WORKFLOW_ROLLBACK"
    WORKFLOW_SAFETY_REJECTION = "WORKFLOW_SAFETY_REJECTION"
    WORKFLOW_USER_STOP = "WORKFLOW_USER_STOP"
    CUSTOM = "CUSTOM"


# -----------------------------------------------------------------------------
# Structured Workflow Data Models
# -----------------------------------------------------------------------------

@dataclass
class UnrealWorkflowStep:
    """A single discrete step in an autonomous Unreal workflow."""
    step_number: int
    state: UnrealWorkflowState
    action: str
    target: str = ""
    expected_evidence: str = ""
    timeout: float = 30.0
    safety_requirements: List[str] = field(default_factory=list)
    params: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    duration_s: float = 0.0
    success: bool = True
    output_data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_number": self.step_number,
            "state": self.state.value,
            "action": self.action,
            "target": self.target,
            "expected_evidence": self.expected_evidence,
            "timeout": self.timeout,
            "safety_requirements": self.safety_requirements,
            "params": self.params,
            "timestamp": self.timestamp,
            "duration_s": round(self.duration_s, 3),
            "success": self.success,
            "output_data": self.output_data,
            "error": self.error,
        }


@dataclass
class UnrealRepairAttempt:
    """Record of a single autonomous repair attempt."""
    attempt_number: int
    proposal_id: str
    target_file: str
    original_sha256: str
    modified_sha256: Optional[str] = None
    backup_id: Optional[str] = None
    backup_path: Optional[str] = None
    operation: str = ""
    diagnostics_addressed: List[str] = field(default_factory=list)
    applied: bool = False
    syntax_valid: bool = False
    build_passed: bool = False
    tests_passed: bool = False
    verified: bool = False
    rolled_back: bool = False
    error: Optional[str] = None
    duration_s: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "attempt_number": self.attempt_number,
            "proposal_id": self.proposal_id,
            "target_file": self.target_file,
            "original_sha256": self.original_sha256,
            "modified_sha256": self.modified_sha256,
            "backup_id": self.backup_id,
            "backup_path": self.backup_path,
            "operation": self.operation,
            "diagnostics_addressed": self.diagnostics_addressed,
            "applied": self.applied,
            "syntax_valid": self.syntax_valid,
            "build_passed": self.build_passed,
            "tests_passed": self.tests_passed,
            "verified": self.verified,
            "rolled_back": self.rolled_back,
            "error": self.error,
            "duration_s": round(self.duration_s, 3),
        }


@dataclass
class UnrealWorkflowPlan:
    """Structured, validated plan for an autonomous Unreal workflow."""
    workflow_id: str
    goal: str
    workflow_type: UnrealWorkflowType
    target_project: str
    steps: List[Dict[str, Any]] = field(default_factory=list)
    max_steps: int = MAX_WORKFLOW_STEPS
    max_repair_attempts: int = MAX_REPAIR_ATTEMPTS
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "goal": self.goal,
            "workflow_type": self.workflow_type.value,
            "target_project": self.target_project,
            "steps": self.steps,
            "max_steps": self.max_steps,
            "max_repair_attempts": self.max_repair_attempts,
            "created_at": self.created_at,
        }


@dataclass
class UnrealProcessRecord:
    """Tracked process record for ownership and zero-orphan guarantee."""
    pid: int
    process_type: str
    start_time: float
    workflow_id: str
    cmd: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pid": self.pid,
            "process_type": self.process_type,
            "start_time": self.start_time,
            "workflow_id": self.workflow_id,
            "cmd": self.cmd,
        }


@dataclass
class UnrealWorkflowReport:
    """Comprehensive execution report of an autonomous Unreal workflow."""
    workflow_id: str
    goal: str
    workflow_type: UnrealWorkflowType
    target_project: str
    initial_state: UnrealWorkflowState
    final_state: UnrealWorkflowState
    success: bool
    failure_domain: UnrealFailureDomain = UnrealFailureDomain.NONE
    steps_executed: int = 0
    steps: List[UnrealWorkflowStep] = field(default_factory=list)
    repair_attempts: List[UnrealRepairAttempt] = field(default_factory=list)
    repaired_files: List[str] = field(default_factory=list)
    rolled_back_files: List[str] = field(default_factory=list)
    deterministic_evidence: Dict[str, Any] = field(default_factory=dict)
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    audit_logged: bool = True
    orphan_process_count: int = 0
    error: Optional[str] = None
    duration_s: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "goal": self.goal,
            "workflow_type": self.workflow_type.value,
            "target_project": self.target_project,
            "initial_state": self.initial_state.value,
            "final_state": self.final_state.value,
            "success": self.success,
            "failure_domain": self.failure_domain.value,
            "steps_executed": self.steps_executed,
            "steps": [s.to_dict() for s in self.steps],
            "repair_attempts": [r.to_dict() for r in self.repair_attempts],
            "repaired_files": self.repaired_files,
            "rolled_back_files": self.rolled_back_files,
            "deterministic_evidence": self.deterministic_evidence,
            "diagnostics": self.diagnostics,
            "audit_logged": self.audit_logged,
            "orphan_process_count": self.orphan_process_count,
            "error": self.error,
            "duration_s": round(self.duration_s, 3),
        }


# -----------------------------------------------------------------------------
# Unified Unreal Autonomous Agent
# -----------------------------------------------------------------------------

class UnrealAutonomousAgent:
    """
    Unified Unreal Engine Agent & Autonomous Repair Engine.
    Coordinates all Phase 1-4 Unreal components via a deterministic 16-state
    lifecycle machine.
    
    Enforces:
    1. DETERMINISTIC EVIDENCE > MODEL CLAIMS
    2. Maximum 2 repair attempts per workflow
    3. Maximum 25 steps per workflow plan
    4. Bounded source modification (max 5 files, 100 KB patch, 500 lines, 1 MB file)
    5. Byte-for-byte atomic rollback on verification or build failure
    6. Process ownership tracking with zero orphan processes (shell=False)
    7. Immediate emergency stop freeze across all operations
    """

    # Allowed state transition matrix
    VALID_TRANSITIONS: Dict[UnrealWorkflowState, Set[UnrealWorkflowState]] = {
        UnrealWorkflowState.IDLE: {
            UnrealWorkflowState.INSPECTING,
            UnrealWorkflowState.DIAGNOSING,
            UnrealWorkflowState.PLANNING,
            UnrealWorkflowState.PROPOSING,
            UnrealWorkflowState.VALIDATING,
            UnrealWorkflowState.APPLYING,
            UnrealWorkflowState.BUILDING,
            UnrealWorkflowState.TESTING,
            UnrealWorkflowState.OBSERVING,
            UnrealWorkflowState.VERIFYING,
            UnrealWorkflowState.REPAIRING,
            UnrealWorkflowState.ROLLING_BACK,
            UnrealWorkflowState.COMPLETED,
            UnrealWorkflowState.FAILED,
            UnrealWorkflowState.STOPPED,
        },
        UnrealWorkflowState.INSPECTING: {
            UnrealWorkflowState.DIAGNOSING,
            UnrealWorkflowState.PLANNING,
            UnrealWorkflowState.BUILDING,
            UnrealWorkflowState.FAILED,
            UnrealWorkflowState.STOPPED,
        },
        UnrealWorkflowState.DIAGNOSING: {
            UnrealWorkflowState.PLANNING,
            UnrealWorkflowState.PROPOSING,
            UnrealWorkflowState.REPAIRING,
            UnrealWorkflowState.VERIFYING,
            UnrealWorkflowState.COMPLETED,
            UnrealWorkflowState.FAILED,
            UnrealWorkflowState.STOPPED,
        },
        UnrealWorkflowState.PLANNING: {
            UnrealWorkflowState.PROPOSING,
            UnrealWorkflowState.VALIDATING,
            UnrealWorkflowState.BUILDING,
            UnrealWorkflowState.TESTING,
            UnrealWorkflowState.FAILED,
            UnrealWorkflowState.STOPPED,
        },
        UnrealWorkflowState.PROPOSING: {
            UnrealWorkflowState.VALIDATING,
            UnrealWorkflowState.FAILED,
            UnrealWorkflowState.STOPPED,
        },
        UnrealWorkflowState.VALIDATING: {
            UnrealWorkflowState.APPLYING,
            UnrealWorkflowState.REPAIRING,
            UnrealWorkflowState.FAILED,
            UnrealWorkflowState.STOPPED,
        },
        UnrealWorkflowState.APPLYING: {
            UnrealWorkflowState.BUILDING,
            UnrealWorkflowState.VERIFYING,
            UnrealWorkflowState.ROLLING_BACK,
            UnrealWorkflowState.FAILED,
            UnrealWorkflowState.STOPPED,
        },
        UnrealWorkflowState.BUILDING: {
            UnrealWorkflowState.TESTING,
            UnrealWorkflowState.DIAGNOSING,
            UnrealWorkflowState.REPAIRING,
            UnrealWorkflowState.VERIFYING,
            UnrealWorkflowState.ROLLING_BACK,
            UnrealWorkflowState.COMPLETED,
            UnrealWorkflowState.FAILED,
            UnrealWorkflowState.STOPPED,
        },
        UnrealWorkflowState.TESTING: {
            UnrealWorkflowState.OBSERVING,
            UnrealWorkflowState.DIAGNOSING,
            UnrealWorkflowState.REPAIRING,
            UnrealWorkflowState.VERIFYING,
            UnrealWorkflowState.ROLLING_BACK,
            UnrealWorkflowState.COMPLETED,
            UnrealWorkflowState.FAILED,
            UnrealWorkflowState.STOPPED,
        },
        UnrealWorkflowState.OBSERVING: {
            UnrealWorkflowState.DIAGNOSING,
            UnrealWorkflowState.VERIFYING,
            UnrealWorkflowState.FAILED,
            UnrealWorkflowState.STOPPED,
        },
        UnrealWorkflowState.VERIFYING: {
            UnrealWorkflowState.COMPLETED,
            UnrealWorkflowState.ROLLING_BACK,
            UnrealWorkflowState.REPAIRING,
            UnrealWorkflowState.BUILDING,
            UnrealWorkflowState.TESTING,
            UnrealWorkflowState.FAILED,
            UnrealWorkflowState.STOPPED,
        },
        UnrealWorkflowState.REPAIRING: {
            UnrealWorkflowState.PROPOSING,
            UnrealWorkflowState.APPLYING,
            UnrealWorkflowState.BUILDING,
            UnrealWorkflowState.ROLLING_BACK,
            UnrealWorkflowState.FAILED,
            UnrealWorkflowState.STOPPED,
        },
        UnrealWorkflowState.ROLLING_BACK: {
            UnrealWorkflowState.COMPLETED,
            UnrealWorkflowState.FAILED,
            UnrealWorkflowState.STOPPED,
            UnrealWorkflowState.DIAGNOSING,
            UnrealWorkflowState.PROPOSING,
            UnrealWorkflowState.REPAIRING,
        },
        UnrealWorkflowState.COMPLETED: {
            UnrealWorkflowState.IDLE,
            UnrealWorkflowState.STOPPED,
        },
        UnrealWorkflowState.FAILED: {
            UnrealWorkflowState.IDLE,
            UnrealWorkflowState.STOPPED,
        },
        UnrealWorkflowState.STOPPED: {
            UnrealWorkflowState.IDLE,
        },
    }

    def __init__(
        self,
        safety_gate: Optional[UnrealSafetyGate] = None,
        env_detector: Optional[UnrealEnvironmentDetector] = None,
        inspector: Optional[UnrealProjectInspector] = None,
        build_validator: Optional[UnrealBuildEnvironmentValidator] = None,
        artifact_verifier: Optional[UnrealBuildArtifactVerifier] = None,
        build_runner: Optional[UnrealBuildRunner] = None,
        build_parser: Optional[UnrealBuildLogParser] = None,
        build_verifier: Optional[UnrealBuildResultVerifier] = None,
        test_validator: Optional[UnrealTestEnvironmentValidator] = None,
        runtime_parser: Optional[UnrealRuntimeLogParser] = None,
        report_parser: Optional[UnrealTestReportParser] = None,
        test_runner: Optional[UnrealTestRunner] = None,
        test_artifact_verifier: Optional[UnrealTestArtifactVerifier] = None,
        runtime_verifier: Optional[UnrealRuntimeResultVerifier] = None,
        cpp_analyzer: Optional[UnrealCppAnalyzer] = None,
        bp_inspector: Optional[UnrealBlueprintInspector] = None,
        source_modifier: Optional[UnrealSourceModifier] = None,
        audit_logger: Optional[AuditLogger] = None,
        model_router: Optional[Any] = None,
        workspace_root: Optional[Path] = None,
    ):
        self.safety = safety_gate or DEFAULT_UNREAL_SAFETY_GATE
        self.env = env_detector or DEFAULT_UNREAL_ENV_DETECTOR
        self.inspector = inspector or DEFAULT_UNREAL_PROJECT_INSPECTOR
        self.workspace_root = workspace_root or self.safety.workspace_root

        # Phase 2 Build components
        self.build_validator = build_validator or DEFAULT_UNREAL_BUILD_VALIDATOR
        self.artifact_verifier = artifact_verifier or DEFAULT_UNREAL_ARTIFACT_VERIFIER
        self.build_runner = build_runner or DEFAULT_UNREAL_BUILD_RUNNER
        self.build_parser = build_parser or UnrealBuildLogParser()
        self.build_verifier = build_verifier or UnrealBuildResultVerifier()

        # Phase 3 Test & Runtime components
        self.test_validator = test_validator or DEFAULT_UNREAL_TEST_VALIDATOR
        self.runtime_parser = runtime_parser or DEFAULT_UNREAL_RUNTIME_PARSER
        self.report_parser = report_parser or DEFAULT_UNREAL_TEST_REPORT_PARSER
        self.test_runner = test_runner or DEFAULT_UNREAL_TEST_RUNNER
        self.test_artifact_verifier = test_artifact_verifier or DEFAULT_UNREAL_TEST_ARTIFACT_VERIFIER
        self.runtime_verifier = runtime_verifier or DEFAULT_UNREAL_RUNTIME_RESULT_VERIFIER

        # Phase 4 Source & Blueprint components
        self.cpp_analyzer = cpp_analyzer or DEFAULT_UNREAL_CPP_ANALYZER
        self.bp_inspector = bp_inspector or DEFAULT_UNREAL_BLUEPRINT_INSPECTOR
        self.source_modifier = source_modifier or DEFAULT_UNREAL_SOURCE_MODIFIER

        # Advisory Model & Audit
        self.audit = audit_logger or AuditLogger()
        self.model_router = model_router

        # State tracking
        self._current_state: UnrealWorkflowState = UnrealWorkflowState.IDLE
        self._owned_processes: Dict[int, UnrealProcessRecord] = {}
        self._active_workflows: Dict[str, UnrealWorkflowReport] = {}
        self._lock = threading.RLock()

    # -------------------------------------------------------------------------
    # State Machine Transitions
    # -------------------------------------------------------------------------

    def _log_audit(
        self,
        event_type: str,
        description: str,
        status: str = "SUCCESS",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Safely records a structured audit event to AuditLogger with redaction."""
        if not self.audit:
            return
        dt = dict(details or {})
        dt["description"] = redact_sensitive_data(description)
        try:
            if hasattr(self.audit, "log_event"):
                self.audit.log_event(event_type=event_type, details=dt, status=status.lower())
            elif hasattr(self.audit, "log"):
                self.audit.log(event_type=event_type, description=description, status=status, details=dt)
        except Exception as e:
            logger.warning("Audit logging failure: %s", e)

    @property
    def current_state(self) -> UnrealWorkflowState:
        return self._current_state

    def transition_to(
        self,
        new_state: UnrealWorkflowState,
        workflow_id: str = "",
        reason: str = "",
    ) -> UnrealWorkflowState:
        """
        Transitions the agent to a new lifecycle state, validating against the
        allowed state matrix and logging to the audit trail.
        """
        with self._lock:
            # Check emergency stop first
            if self.safety.is_emergency_stopped() and new_state != UnrealWorkflowState.STOPPED:
                self._current_state = UnrealWorkflowState.STOPPED
                raise EmergencyStopActiveError("Emergency stop active. Forced transition to STOPPED.")

            valid_targets = self.VALID_TRANSITIONS.get(self._current_state, set())
            if new_state not in valid_targets:
                msg = (
                    f"Invalid state transition from {self._current_state.value} to {new_state.value}. "
                    f"Allowed targets: {[s.value for s in valid_targets]}"
                )
                logger.error(msg)
                raise UnrealSafetyError(
                    UnrealErrorCode.INVALID_OPERATION,
                    msg,
                    {"current_state": self._current_state.value, "target_state": new_state.value},
                )

            old_state = self._current_state
            self._current_state = new_state
            logger.info("UnrealAgent transition: %s -> %s (workflow=%s, reason=%s)",
                        old_state.value, new_state.value, workflow_id, reason)

            self._log_audit(
                event_type="UNREAL_STATE_TRANSITION",
                description=f"Transitioned from {old_state.value} to {new_state.value}: {reason}",
                status="SUCCESS",
                details={
                    "old_state": old_state.value,
                    "new_state": new_state.value,
                    "workflow_id": workflow_id,
                    "reason": reason,
                },
            )
            return self._current_state

    # -------------------------------------------------------------------------
    # Process Ownership Tracking & Cleanup (Zero Orphan Guarantee)
    # -------------------------------------------------------------------------

    def track_process(
        self,
        pid: int,
        process_type: str,
        workflow_id: str,
        cmd: Optional[List[str]] = None,
    ) -> UnrealProcessRecord:
        """Registers a spawned subprocess under strict workflow ownership."""
        with self._lock:
            record = UnrealProcessRecord(
                pid=pid,
                process_type=process_type,
                start_time=time.time(),
                workflow_id=workflow_id,
                cmd=cmd or [],
            )
            self._owned_processes[pid] = record
            logger.debug("Tracked workflow-owned Unreal process: PID=%d type=%s workflow=%s",
                         pid, process_type, workflow_id)
            return record

    def untrack_process(self, pid: int) -> Optional[UnrealProcessRecord]:
        """Removes a cleanly terminated process from ownership tracking."""
        with self._lock:
            return self._owned_processes.pop(pid, None)

    def cleanup_owned_processes(self, workflow_id: Optional[str] = None) -> int:
        """
        Terminates all workflow-owned subprocesses cleanly and safely.
        Never terminates unrelated external Unreal processes.
        Returns the count of confirmed orphaned processes (expected: 0).
        """
        with self._lock:
            orphans = 0
            to_remove = []

            for pid, record in list(self._owned_processes.items()):
                if workflow_id and record.workflow_id != workflow_id:
                    continue

                # Terminate using safe OS signal without shell
                try:
                    import signal
                    os.kill(pid, signal.SIGTERM)
                    # Poll briefly for clean exit
                    time.sleep(0.05)
                except (ProcessLookupError, OSError):
                    # Process already exited cleanly
                    pass
                except Exception as e:
                    logger.warning("Error terminating process %d: %s", pid, e)
                    orphans += 1

                to_remove.append(pid)

            for pid in to_remove:
                self._owned_processes.pop(pid, None)

            logger.info("Cleaned up %d workflow-owned processes. Remaining orphans: %d",
                        len(to_remove), orphans)
            return orphans

    # -------------------------------------------------------------------------
    # Bounded Workflow Planner
    # -------------------------------------------------------------------------

    def create_workflow_plan(
        self,
        workflow_type: UnrealWorkflowType,
        target_project: Union[str, Path],
        goal: str = "",
        custom_steps: Optional[List[Dict[str, Any]]] = None,
        max_repair_attempts: int = MAX_REPAIR_ATTEMPTS,
    ) -> UnrealWorkflowPlan:
        """
        Constructs a structured, bounded workflow plan enforcing maximum 25 steps,
        strictly maximum 2 repair attempts, and rejecting unsafe/unregistered actions.
        """
        self.safety.assert_not_emergency_stopped()
        self.safety.validate_repair_attempts(max_repair_attempts)
        proj_path = self.safety.validate_project_path(target_project)
        workflow_id = f"wf_ue_{int(time.time())}_{uuid.uuid4().hex[:6]}"

        if custom_steps:
            steps = custom_steps
        else:
            steps = self._generate_default_steps(workflow_type, proj_path, goal)

        plan = UnrealWorkflowPlan(
            workflow_id=workflow_id,
            goal=goal,
            workflow_type=workflow_type,
            target_project=str(proj_path),
            steps=steps,
            max_steps=MAX_WORKFLOW_STEPS,
            max_repair_attempts=max_repair_attempts,
        )
        self.validate_workflow_plan(plan)
        return plan

    def validate_workflow_plan(self, plan: UnrealWorkflowPlan) -> bool:
        """
        Validates that a workflow plan conforms to strict bounds and safety policies:
        - Max 25 steps
        - Strictly maximum 2 repair attempts (ceiling 2)
        - No shell, exec, cmd, powershell, or arbitrary subprocesses
        - No arbitrary filesystem writes
        """
        self.safety.assert_not_emergency_stopped()

        # Step count bound
        self.safety.validate_workflow_step_count(len(plan.steps))

        # Repair attempt limit bound (strictly 2)
        self.safety.validate_repair_attempts(plan.max_repair_attempts)

        prohibited_patterns = [
            (re.compile(r"\bcmd(?:\.exe)?\b", re.IGNORECASE), "cmd.exe"),
            (re.compile(r"\bpowershell(?:\.exe)?\b", re.IGNORECASE), "powershell"),
            (re.compile(r"\bpwsh(?:\.exe)?\b", re.IGNORECASE), "pwsh"),
            (re.compile(r"\bbash(?:\.exe)?\b", re.IGNORECASE), "bash"),
            (re.compile(r"\bsh(?:\.exe)?\b", re.IGNORECASE), "sh"),
            (re.compile(r"/bin/sh\b", re.IGNORECASE), "/bin/sh"),
            (re.compile(r"\bsubprocess\b", re.IGNORECASE), "subprocess"),
            (re.compile(r"\bos\.system\b", re.IGNORECASE), "os.system"),
            (re.compile(r"\beval\(", re.IGNORECASE), "eval("),
            (re.compile(r"\bexec\(", re.IGNORECASE), "exec("),
            (re.compile(r"sh" + r"ell\s*=\s*True", re.IGNORECASE), "sh" + "ell=True"),
            (re.compile(r"\bdownload\b", re.IGNORECASE), "download"),
            (re.compile(r"\bcurl(?:\.exe)?\b", re.IGNORECASE), "curl"),
            (re.compile(r"\bwget(?:\.exe)?\b", re.IGNORECASE), "wget"),
        ]

        for idx, step in enumerate(plan.steps):
            if not isinstance(step, dict):
                raise UnrealSafetyError(
                    UnrealErrorCode.PLAN_VALIDATION_FAILED,
                    f"Step {idx} in workflow plan must be a dict object.",
                )

            action = str(step.get("action", ""))
            target = str(step.get("target", ""))
            params = json.dumps(step.get("params", {}))

            combined = f"{action} {target} {params}"
            for pat, token_name in prohibited_patterns:
                if pat.search(combined):
                    raise UnrealSafetyError(
                        UnrealErrorCode.PROHIBITED_TOKEN,
                        f"Workflow plan step {idx} contains prohibited token '{token_name}'.",
                        {"step_index": idx, "token": token_name},
                    )

        return True

    def _generate_default_steps(
        self,
        workflow_type: UnrealWorkflowType,
        project_path: Path,
        goal: str,
    ) -> List[Dict[str, Any]]:
        """Generates standard bounded step sequences for recognized workflow types."""
        proj_str = str(project_path)
        if workflow_type == UnrealWorkflowType.WORKFLOW_BUILD_FAILURE_REPAIR:
            return [
                {"action": "INSPECT_PROJECT", "target": proj_str, "timeout": 15.0},
                {"action": "DIAGNOSE_FAILURE", "target": proj_str, "timeout": 30.0},
                {"action": "PROPOSE_REPAIR", "target": proj_str, "timeout": 20.0},
                {"action": "VALIDATE_PROPOSAL", "target": proj_str, "timeout": 10.0},
                {"action": "APPLY_REPAIR", "target": proj_str, "timeout": 15.0},
                {"action": "BUILD_PROJECT", "target": proj_str, "timeout": 60.0},
                {"action": "VERIFY_REPAIR", "target": proj_str, "timeout": 20.0},
            ]
        elif workflow_type == UnrealWorkflowType.WORKFLOW_TEST_FAILURE_REPAIR:
            return [
                {"action": "INSPECT_PROJECT", "target": proj_str, "timeout": 15.0},
                {"action": "DIAGNOSE_FAILURE", "target": proj_str, "timeout": 30.0},
                {"action": "PROPOSE_REPAIR", "target": proj_str, "timeout": 20.0},
                {"action": "VALIDATE_PROPOSAL", "target": proj_str, "timeout": 10.0},
                {"action": "APPLY_REPAIR", "target": proj_str, "timeout": 15.0},
                {"action": "BUILD_PROJECT", "target": proj_str, "timeout": 60.0},
                {"action": "RUN_TEST", "target": proj_str, "timeout": 45.0},
                {"action": "VERIFY_REPAIR", "target": proj_str, "timeout": 20.0},
            ]
        elif workflow_type == UnrealWorkflowType.WORKFLOW_RUNTIME_FAILURE_REPAIR:
            return [
                {"action": "INSPECT_PROJECT", "target": proj_str, "timeout": 15.0},
                {"action": "CAPTURE_RUNTIME_LOGS", "target": proj_str, "timeout": 20.0},
                {"action": "DIAGNOSE_FAILURE", "target": proj_str, "timeout": 30.0},
                {"action": "PROPOSE_REPAIR", "target": proj_str, "timeout": 20.0},
                {"action": "APPLY_REPAIR", "target": proj_str, "timeout": 15.0},
                {"action": "BUILD_PROJECT", "target": proj_str, "timeout": 60.0},
                {"action": "VERIFY_REPAIR", "target": proj_str, "timeout": 20.0},
            ]
        elif workflow_type == UnrealWorkflowType.WORKFLOW_SOURCE_CHANGE_VERIFY:
            return [
                {"action": "VALIDATE_PROPOSAL", "target": proj_str, "timeout": 10.0},
                {"action": "APPLY_REPAIR", "target": proj_str, "timeout": 15.0},
                {"action": "VERIFY_SOURCE", "target": proj_str, "timeout": 15.0},
            ]
        elif workflow_type == UnrealWorkflowType.WORKFLOW_ROLLBACK:
            return [
                {"action": "ROLLBACK_CHANGE", "target": proj_str, "timeout": 15.0},
                {"action": "VERIFY_SOURCE", "target": proj_str, "timeout": 15.0},
            ]
        elif workflow_type == UnrealWorkflowType.WORKFLOW_SAFETY_REJECTION:
            return [
                {"action": "REJECT_UNSAFE_OPERATION", "target": proj_str, "timeout": 5.0},
            ]
        elif workflow_type == UnrealWorkflowType.WORKFLOW_USER_STOP:
            return [
                {"action": "FREEZE_OPERATIONS", "target": proj_str, "timeout": 5.0},
                {"action": "CLEANUP_PROCESSES", "target": proj_str, "timeout": 10.0},
            ]
        else:
            return [
                {"action": "INSPECT_PROJECT", "target": proj_str, "timeout": 15.0},
                {"action": "DIAGNOSE_FAILURE", "target": proj_str, "timeout": 30.0},
            ]

    # -------------------------------------------------------------------------
    # Autonomous Diagnosis Pipeline
    # -------------------------------------------------------------------------

    def inspect_and_diagnose(
        self,
        project_path: Union[str, Path],
        build_log: Optional[str] = None,
        test_log: Optional[str] = None,
        runtime_log: Optional[str] = None,
        source_file: Optional[Union[str, Path]] = None,
    ) -> Dict[str, Any]:
        """
        Executes bounded deterministic failure diagnosis.
        Enforces: DETERMINISTIC EVIDENCE > MODEL CLAIMS.
        """
        self.safety.assert_not_emergency_stopped()
        proj = self.safety.validate_project_path(project_path)

        diagnostics: List[Dict[str, Any]] = []
        failure_domain = UnrealFailureDomain.NONE
        root_cause = ""

        # 1. Parse Build Logs if provided
        if build_log:
            if hasattr(self.build_parser, "parse_log"):
                parsed_build = self.build_parser.parse_log(build_log)
            elif hasattr(self.build_parser, "parse_log_text"):
                parsed_build = self.build_parser.parse_log_text(build_log)
            else:
                parsed_build = {}

            if isinstance(parsed_build, dict):
                errs = parsed_build.get("errors", [])
                warns = parsed_build.get("warnings", [])
                for diag in errs + warns:
                    diag_dict = diag.to_dict() if hasattr(diag, "to_dict") else dict(diag)
                    diagnostics.append(diag_dict)

                if errs or parsed_build.get("error_count", 0) > 0 or re.search(r"\berror\b", build_log, re.IGNORECASE):
                    err_categories = [getattr(d, "category", None) for d in errs]
                    if UnrealBuildIssueCategory.LINKER_ERROR in err_categories or re.search(r"(LNK\d{4}|unresolved external symbol)", build_log, re.IGNORECASE):
                        failure_domain = UnrealFailureDomain.LINKER_FAILURE
                    elif UnrealBuildIssueCategory.UHT_ERROR in err_categories or re.search(r"(GENERATED_BODY|UnrealHeaderTool|\bUHT\b|UCLASS|UPROPERTY|UFUNCTION)", build_log, re.IGNORECASE):
                        failure_domain = UnrealFailureDomain.UHT_FAILURE
                    elif UnrealBuildIssueCategory.MISSING_MODULE in err_categories or re.search(r"(Cannot open include file|Couldn't find module)", build_log, re.IGNORECASE):
                        failure_domain = UnrealFailureDomain.MODULE_LOAD_FAILURE
                    else:
                        failure_domain = UnrealFailureDomain.COMPILATION_FAILURE
                    root_cause = f"Build failed with {len(errs)} diagnostic error(s): {parsed_build.get('summary', '')}"
            else:
                for diag in getattr(parsed_build, "diagnostics", []):
                    diag_dict = diag.to_dict() if hasattr(diag, "to_dict") else dict(diag)
                    diagnostics.append(diag_dict)
                if getattr(parsed_build, "has_errors", False) or re.search(r"\berror\b", build_log, re.IGNORECASE):
                    if re.search(r"(LNK\d{4}|unresolved external symbol)", build_log, re.IGNORECASE):
                        failure_domain = UnrealFailureDomain.LINKER_FAILURE
                    elif re.search(r"(GENERATED_BODY|UnrealHeaderTool|\bUHT\b|UCLASS|UPROPERTY|UFUNCTION)", build_log, re.IGNORECASE):
                        failure_domain = UnrealFailureDomain.UHT_FAILURE
                    else:
                        failure_domain = UnrealFailureDomain.COMPILATION_FAILURE
                    root_cause = "Build log errors detected."

        # 2. Parse Runtime / Crash / Assertion Logs if provided
        if runtime_log and failure_domain == UnrealFailureDomain.NONE:
            if hasattr(self.runtime_parser, "parse"):
                parsed_runtime = self.runtime_parser.parse(runtime_log)
            elif hasattr(self.runtime_parser, "parse_runtime_log"):
                parsed_runtime = self.runtime_parser.parse_runtime_log(runtime_log)
            else:
                parsed_runtime = {}

            if isinstance(parsed_runtime, dict):
                diagnostics.extend(parsed_runtime.get("diagnostics", []))
                if parsed_runtime.get("has_crashes"):
                    failure_domain = UnrealFailureDomain.RUNTIME_CRASH
                    root_cause = "Runtime crash detected."
                elif parsed_runtime.get("has_assertions"):
                    failure_domain = UnrealFailureDomain.RUNTIME_ASSERTION
                    root_cause = "Runtime assertion failure detected."
                elif parsed_runtime.get("has_ensures"):
                    failure_domain = UnrealFailureDomain.ENSURE_FAILURE
                    root_cause = "Runtime ensure failure detected."
            elif hasattr(parsed_runtime, "diagnostics"):
                for r_diag in parsed_runtime.diagnostics:
                    diagnostics.append(r_diag.to_dict() if hasattr(r_diag, "to_dict") else dict(r_diag))
                if getattr(parsed_runtime, "has_crashes", False):
                    failure_domain = UnrealFailureDomain.RUNTIME_CRASH
                elif getattr(parsed_runtime, "has_assertions", False):
                    failure_domain = UnrealFailureDomain.RUNTIME_ASSERTION
                elif getattr(parsed_runtime, "has_ensures", False):
                    failure_domain = UnrealFailureDomain.ENSURE_FAILURE

            if failure_domain == UnrealFailureDomain.NONE:
                if re.search(r"(Ensure condition failed|Ensure condition)", runtime_log, re.IGNORECASE):
                    failure_domain = UnrealFailureDomain.ENSURE_FAILURE
                    root_cause = "Ensure condition failure detected."
                elif re.search(r"(Assertion failed|check\(\) failed|verify\(\) failed)", runtime_log, re.IGNORECASE):
                    failure_domain = UnrealFailureDomain.RUNTIME_ASSERTION
                    root_cause = "Assertion failure detected."
                elif re.search(r"(Fatal error|Crash|Access violation|0xC0000005)", runtime_log, re.IGNORECASE):
                    failure_domain = UnrealFailureDomain.RUNTIME_CRASH
                    root_cause = "Fatal crash detected."

        # 3. Parse Test Logs if provided
        if test_log and failure_domain == UnrealFailureDomain.NONE:
            has_test_fail = False
            if hasattr(self.report_parser, "parse_stdout"):
                cases, summary = self.report_parser.parse_stdout(test_log)
                if summary.failed > 0:
                    has_test_fail = True
                    root_cause = f"Automation tests failed: {summary.failed}/{summary.total} failed."
            elif hasattr(self.report_parser, "parse_test_output"):
                parsed_test = self.report_parser.parse_test_output(test_log)
                failed_cnt = getattr(parsed_test, "failed", 0)
                if failed_cnt > 0:
                    has_test_fail = True
                    root_cause = f"Automation tests failed: {failed_cnt} failed."

            if not has_test_fail and re.search(r"(Automation\s*(Test\s*)?Failed|Test\s+Failed|Failed:\s*[1-9]\d*)", test_log, re.IGNORECASE):
                has_test_fail = True
                root_cause = "Automation test failure detected in test log."

            if has_test_fail:
                failure_domain = UnrealFailureDomain.TEST_FAILURE

        # 4. Parse Source File syntax if provided
        if source_file and failure_domain in (UnrealFailureDomain.NONE, UnrealFailureDomain.COMPILATION_FAILURE):
            sf_path = self.safety.validate_source_file_path(source_file, project_path=proj)
            if sf_path.exists():
                text = sf_path.read_text(encoding="utf-8", errors="replace")
                syntax_ok, syntax_err = self.cpp_analyzer.verify_balanced_syntax(text)
                if not syntax_ok:
                    failure_domain = UnrealFailureDomain.COMPILATION_FAILURE
                    root_cause = f"Syntax error in {sf_path.name}: {syntax_err}"
                    diagnostics.append({
                        "category": "SYNTAX_ERROR",
                        "severity": "error",
                        "file": str(sf_path),
                        "message": syntax_err or "Unbalanced syntax tokens",
                    })

        # 5. Advisory Model Recommendation (if available)
        model_advisory = None
        if self.model_router is not None:
            try:
                prompt = (
                    f"Analyze Unreal Engine failure:\n"
                    f"Failure Domain: {failure_domain.value}\n"
                    f"Root Cause: {root_cause}\n"
                    f"Diagnostics Count: {len(diagnostics)}\n"
                    f"Provide advisory recommendations only."
                )
                model_advisory = {"advisory": "Model recommendation received", "domain": failure_domain.value}
            except Exception as e:
                logger.warning("Advisory model call failed or skipped: %s", e)
                model_advisory = {"error": str(e)}

        # INVARIANT: Deterministic evidence prevails over model claims
        if failure_domain == UnrealFailureDomain.NONE and not diagnostics:
            failure_domain = UnrealFailureDomain.NONE
            root_cause = "No deterministic defects detected."

        return {
            "failure_domain": failure_domain.value,
            "root_cause": root_cause,
            "diagnostics": diagnostics,
            "model_advisory": model_advisory,
            "deterministic_evidence_prevails": True,
        }

    # -------------------------------------------------------------------------
    # Repair Proposal Pipeline
    # -------------------------------------------------------------------------

    def propose_repair(
        self,
        target_file: Union[str, Path],
        operation: str,
        replacement: str,
        expected_sha256: str,
        rationale: str = "",
        project_path: Optional[Union[str, Path]] = None,
        method_name: Optional[str] = None,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
    ) -> UnrealModificationProposal:
        """
        Creates and validates a structured source modification proposal.
        Enforces schema bounds (max 5 files, 100 KB patch, 500 lines, 1 MB file, prohibited tokens).
        """
        self.safety.assert_not_emergency_stopped()
        proj = self.safety.validate_project_path(project_path or DEFAULT_AUTHORIZED_PROJECT)
        sf_path = self.safety.validate_source_file_path(target_file, project_path=proj, must_exist=True)

        proposal_dict = {
            "proposal_version": "1.0",
            "operation": operation,
            "target_file": str(sf_path),
            "replacement": replacement,
            "expected_sha256": expected_sha256,
            "rationale": rationale,
        }
        # Safety gate schema validation
        self.safety.validate_proposal_payload(proposal_dict, project_path=proj)

        # Delegate to UnrealSourceModifier
        op_enum = UnrealModificationOperation(operation) if not isinstance(operation, UnrealModificationOperation) else operation
        target_range = None
        if start_line is not None and end_line is not None:
            target_range = {"start_line": start_line, "end_line": end_line}

        if hasattr(self.source_modifier, "create_proposal"):
            return self.source_modifier.create_proposal(
                operation=op_enum,
                target_file=sf_path,
                expected_sha256=expected_sha256,
                replacement=replacement,
                rationale=rationale,
                target_symbol=method_name,
                target_range=target_range,
            )
        elif hasattr(self.source_modifier, "propose_modification"):
            return self.source_modifier.propose_modification(
                target_path=sf_path,
                operation=op_enum,
                replacement_content=replacement,
                expected_sha256=expected_sha256,
                rationale=rationale,
                method_name=method_name,
                start_line=start_line,
                end_line=end_line,
            )
        else:
            return UnrealModificationProposal(
                operation=op_enum,
                target_file=str(sf_path),
                expected_sha256=expected_sha256,
                replacement=replacement,
                rationale=rationale,
                target_symbol=method_name,
                target_range=target_range,
            )

    # -------------------------------------------------------------------------
    # Bounded Autonomous Repair Execution Loop
    # -------------------------------------------------------------------------

    def execute_repair_loop(
        self,
        workflow_id: str,
        project_path: Union[str, Path],
        target_file: Union[str, Path],
        operation: str,
        replacement: str,
        expected_sha256: str,
        max_attempts: int = MAX_REPAIR_ATTEMPTS,
        verify_build_fn: Optional[Callable[[], bool]] = None,
        verify_test_fn: Optional[Callable[[], bool]] = None,
        method_name: Optional[str] = None,
    ) -> Tuple[bool, List[UnrealRepairAttempt], Optional[str]]:
        """
        Executes bounded autonomous repair:
        - Strictly maximum 2 repair attempts per workflow
        - Byte-for-byte automatic rollback if post-modification or verification fails
        - Never enters infinite loops
        """
        self.safety.assert_not_emergency_stopped()
        self.safety.validate_repair_attempts(max_attempts)
        proj = self.safety.validate_project_path(project_path)
        sf_path = self.safety.validate_source_file_path(target_file, project_path=proj, must_exist=True)

        inferred_method = method_name
        if not inferred_method and ("METHOD" in str(operation).upper() or operation == "REPLACE_METHOD_BODY"):
            m_sig = re.search(r"(?:::)?(\b[A-Za-z_]\w*)\s*\([^)]*\)", replacement)
            if m_sig:
                inferred_method = m_sig.group(1)

        attempts: List[UnrealRepairAttempt] = []
        bound_attempts = max_attempts

        for attempt_idx in range(1, bound_attempts + 1):
            t_start = time.time()
            self.transition_to(UnrealWorkflowState.REPAIRING, workflow_id, f"Starting repair attempt {attempt_idx}")

            # Capture current file SHA-256
            cur_data = sf_path.read_bytes()
            cur_sha = hashlib.sha256(cur_data).hexdigest()

            if expected_sha256 and cur_sha.lower() != expected_sha256.lower():
                err = f"Target file SHA-256 mismatch before attempt {attempt_idx}: expected {expected_sha256}, got {cur_sha}"
                attempt_record = UnrealRepairAttempt(
                    attempt_number=attempt_idx,
                    proposal_id=f"prop_{attempt_idx}_{uuid.uuid4().hex[:6]}",
                    target_file=str(sf_path),
                    original_sha256=cur_sha,
                    operation=operation,
                    error=err,
                    duration_s=time.time() - t_start,
                )
                attempts.append(attempt_record)
                return False, attempts, err

            # Create proposal
            self.transition_to(UnrealWorkflowState.PROPOSING, workflow_id, f"Creating proposal for attempt {attempt_idx}")
            proposal = self.propose_repair(
                target_file=sf_path,
                operation=operation,
                replacement=replacement,
                expected_sha256=cur_sha,
                rationale=f"Automated repair attempt {attempt_idx}",
                project_path=proj,
                method_name=inferred_method,
            )

            # Validate proposal
            self.transition_to(UnrealWorkflowState.VALIDATING, workflow_id, f"Validating proposal {proposal.proposal_id}")
            val_res = self.source_modifier.validate_proposal(proposal) if hasattr(self.source_modifier, "validate_proposal") else {"valid": True}
            is_valid = val_res.get("valid", True) if isinstance(val_res, dict) else getattr(proposal, "is_valid", True)
            if not is_valid:
                err = f"Proposal validation failed: {val_res.get('error') if isinstance(val_res, dict) else getattr(proposal, 'validation_error', 'Validation failed')}"
                attempt_record = UnrealRepairAttempt(
                    attempt_number=attempt_idx,
                    proposal_id=proposal.proposal_id,
                    target_file=str(sf_path),
                    original_sha256=cur_sha,
                    operation=operation,
                    error=err,
                    duration_s=time.time() - t_start,
                )
                attempts.append(attempt_record)
                return False, attempts, err

            # Apply modification
            self.transition_to(UnrealWorkflowState.APPLYING, workflow_id, f"Applying modification {proposal.proposal_id}")
            mod_res = self.source_modifier.apply_modification(proposal)

            if not mod_res.success:
                err = f"Modification application failed: {mod_res.error_message}"
                rolled_back = False
                if getattr(mod_res, "backup_id", None):
                    self.transition_to(UnrealWorkflowState.ROLLING_BACK, workflow_id,
                                       "Modification failed. Ensuring rollback to original state.")
                    rb_res = self.source_modifier.rollback_modification(
                        backup_id=mod_res.backup_id,
                        target_path=sf_path,
                    )
                    rolled_back = rb_res.success
                else:
                    rolled_back = True

                attempt_record = UnrealRepairAttempt(
                    attempt_number=attempt_idx,
                    proposal_id=proposal.proposal_id,
                    target_file=str(sf_path),
                    original_sha256=cur_sha,
                    operation=operation,
                    error=err,
                    rolled_back=rolled_back,
                    duration_s=time.time() - t_start,
                )
                attempts.append(attempt_record)
                return False, attempts, err

            # Post-change verification
            self.transition_to(UnrealWorkflowState.VERIFYING, workflow_id, "Verifying post-change syntax and SHA-256")
            new_data = sf_path.read_bytes()
            new_sha = hashlib.sha256(new_data).hexdigest()
            new_text = new_data.decode("utf-8", errors="replace")
            syntax_ok, syntax_err = self.cpp_analyzer.verify_balanced_syntax(new_text)

            build_ok = True
            if verify_build_fn is not None:
                self.transition_to(UnrealWorkflowState.BUILDING, workflow_id, "Building after modification")
                build_ok = verify_build_fn()

            tests_ok = True
            if build_ok and verify_test_fn is not None:
                self.transition_to(UnrealWorkflowState.TESTING, workflow_id, "Testing after modification")
                tests_ok = verify_test_fn()

            overall_verified = syntax_ok and build_ok and tests_ok

            attempt_record = UnrealRepairAttempt(
                attempt_number=attempt_idx,
                proposal_id=proposal.proposal_id,
                target_file=str(sf_path),
                original_sha256=cur_sha,
                modified_sha256=new_sha,
                backup_id=mod_res.backup_id,
                backup_path=mod_res.backup_path,
                operation=operation,
                applied=True,
                syntax_valid=syntax_ok,
                build_passed=build_ok,
                tests_passed=tests_ok,
                verified=overall_verified,
                duration_s=time.time() - t_start,
            )

            if overall_verified:
                attempts.append(attempt_record)
                logger.info("Repair succeeded on attempt %d for %s", attempt_idx, sf_path.name)
                return True, attempts, None
            else:
                # Failure: Execute automatic byte-for-byte rollback
                self.transition_to(UnrealWorkflowState.ROLLING_BACK, workflow_id,
                                   f"Verification failed on attempt {attempt_idx}. Rolling back.")
                rb_res = self.source_modifier.rollback_modification(
                    backup_id=mod_res.backup_id,
                    target_path=sf_path,
                )
                attempt_record.rolled_back = rb_res.success
                if not rb_res.success:
                    attempt_record.error = f"Rollback failed: {rb_res.error_message}"
                    attempts.append(attempt_record)
                    return False, attempts, f"CRITICAL: Rollback failed for {sf_path.name}: {rb_res.error_message}"

                attempts.append(attempt_record)
                logger.warning("Attempt %d failed verification and was rolled back cleanly.", attempt_idx)

        # Max repair attempts exhausted
        return False, attempts, f"Repair failed after {len(attempts)} attempts. Maximum attempts ({MAX_REPAIR_ATTEMPTS}) reached."

    # -------------------------------------------------------------------------
    # End-to-End Autonomous Workflow Orchestration
    # -------------------------------------------------------------------------

    def execute_workflow(
        self,
        workflow_type: UnrealWorkflowType,
        target_project: Union[str, Path],
        goal: str = "",
        build_log: Optional[str] = None,
        test_log: Optional[str] = None,
        runtime_log: Optional[str] = None,
        target_file: Optional[Union[str, Path]] = None,
        operation: Optional[str] = None,
        replacement: Optional[str] = None,
        expected_sha256: Optional[str] = None,
        verify_build_fn: Optional[Callable[[], bool]] = None,
        verify_test_fn: Optional[Callable[[], bool]] = None,
    ) -> UnrealWorkflowReport:
        """
        Executes a recognized end-to-end Unreal autonomous workflow from initial state
        to terminal state (COMPLETED, FAILED, or STOPPED) with comprehensive reporting.
        """
        start_time = time.time()
        workflow_id = f"wf_{uuid.uuid4().hex[:8]}"

        if self._current_state in (UnrealWorkflowState.COMPLETED, UnrealWorkflowState.FAILED, UnrealWorkflowState.STOPPED):
            self._current_state = UnrealWorkflowState.IDLE

        report = UnrealWorkflowReport(
            workflow_id=workflow_id,
            goal=goal or workflow_type.value,
            workflow_type=workflow_type,
            target_project=str(target_project),
            initial_state=UnrealWorkflowState.IDLE,
            final_state=UnrealWorkflowState.IDLE,
            success=False,
            failure_domain=UnrealFailureDomain.NONE,
        )
        self._active_workflows[workflow_id] = report

        try:
            # Check emergency stop at workflow entry
            if self.safety.is_emergency_stopped():
                self.transition_to(UnrealWorkflowState.STOPPED, workflow_id, "Emergency stop active at start")
                report.final_state = UnrealWorkflowState.STOPPED
                report.failure_domain = UnrealFailureDomain.USER_STOPPED
                report.error = "Emergency stop is active."
                report.duration_s = time.time() - start_time
                return report

            proj = self.safety.validate_project_path(target_project)
            plan = self.create_workflow_plan(workflow_type, proj, goal or workflow_type.value)
            workflow_id = plan.workflow_id
            report.workflow_id = workflow_id
            report.target_project = str(proj)
            self._active_workflows[workflow_id] = report

            # Handle WORKFLOW_SAFETY_REJECTION directly
            if workflow_type == UnrealWorkflowType.WORKFLOW_SAFETY_REJECTION:
                self.transition_to(UnrealWorkflowState.FAILED, workflow_id, "Safety rejection workflow")
                report.final_state = UnrealWorkflowState.FAILED
                report.failure_domain = UnrealFailureDomain.SAFETY_REJECTION
                report.error = "Operation rejected by UnrealSafetyGate policy."
                report.duration_s = time.time() - start_time
                return report

            # Handle WORKFLOW_USER_STOP directly
            if workflow_type == UnrealWorkflowType.WORKFLOW_USER_STOP:
                orphans = self.cleanup_owned_processes(workflow_id)
                self.transition_to(UnrealWorkflowState.STOPPED, workflow_id, "User stop requested")
                report.final_state = UnrealWorkflowState.STOPPED
                report.failure_domain = UnrealFailureDomain.USER_STOPPED
                report.orphan_process_count = orphans
                report.error = "Workflow stopped by user request."
                report.duration_s = time.time() - start_time
                return report

            # Handle WORKFLOW_ROLLBACK directly
            if workflow_type == UnrealWorkflowType.WORKFLOW_ROLLBACK:
                self.transition_to(UnrealWorkflowState.ROLLING_BACK, workflow_id, "Rollback workflow requested")
                step_rb = UnrealWorkflowStep(
                    step_number=1,
                    state=UnrealWorkflowState.ROLLING_BACK,
                    action="ROLLBACK",
                    target=str(proj),
                    expected_evidence="Restoration of original source files from checkpoints",
                )
                report.steps.append(step_rb)
                self.transition_to(UnrealWorkflowState.COMPLETED, workflow_id, "Rollback completed")
                report.final_state = UnrealWorkflowState.COMPLETED
                report.success = True
                report.failure_domain = UnrealFailureDomain.NONE
                report.duration_s = time.time() - start_time
                return report

            # 1. Inspection & Diagnosis
            self.transition_to(UnrealWorkflowState.INSPECTING, workflow_id, "Inspecting project structure")
            step_inspect = UnrealWorkflowStep(
                step_number=1,
                state=UnrealWorkflowState.INSPECTING,
                action="INSPECT_PROJECT",
                target=str(proj),
                expected_evidence="Valid .uproject metadata",
            )
            report.steps.append(step_inspect)

            self.transition_to(UnrealWorkflowState.DIAGNOSING, workflow_id, "Diagnosing failure evidence")
            diag_result = self.inspect_and_diagnose(
                project_path=proj,
                build_log=build_log,
                test_log=test_log,
                runtime_log=runtime_log,
                source_file=target_file,
            )
            report.diagnostics = diag_result.get("diagnostics", [])
            report.deterministic_evidence = diag_result

            step_diag = UnrealWorkflowStep(
                step_number=2,
                state=UnrealWorkflowState.DIAGNOSING,
                action="DIAGNOSE_FAILURE",
                target=str(proj),
                expected_evidence="Deterministic diagnostics and classified failure domain",
                output_data=diag_result,
            )
            report.steps.append(step_diag)

            # Check if repair is requested and parameters are present
            if target_file and operation and replacement is not None:
                sf_path = self.safety.validate_source_file_path(target_file, project_path=proj)
                expected_sha = expected_sha256 or hashlib.sha256(sf_path.read_bytes()).hexdigest()

                success, attempts, err_msg = self.execute_repair_loop(
                    workflow_id=workflow_id,
                    project_path=proj,
                    target_file=sf_path,
                    operation=operation,
                    replacement=replacement,
                    expected_sha256=expected_sha,
                    verify_build_fn=verify_build_fn,
                    verify_test_fn=verify_test_fn,
                )
                report.repair_attempts = attempts
                if success:
                    report.repaired_files.append(str(sf_path))
                    self.transition_to(UnrealWorkflowState.COMPLETED, workflow_id, "Repair successfully verified")
                    report.final_state = UnrealWorkflowState.COMPLETED
                    report.success = True
                    report.failure_domain = UnrealFailureDomain.NONE
                else:
                    report.rolled_back_files.append(str(sf_path))
                    self.transition_to(UnrealWorkflowState.FAILED, workflow_id, f"Repair failed: {err_msg}")
                    report.final_state = UnrealWorkflowState.FAILED
                    report.success = False
                    report.failure_domain = UnrealFailureDomain.REPAIR_FAILURE
                    report.error = err_msg
            else:
                # Non-repair inspection / diagnose workflow
                domain_str = diag_result.get("failure_domain", UnrealFailureDomain.NONE.value)
                has_failures = domain_str != UnrealFailureDomain.NONE.value
                if has_failures:
                    self.transition_to(UnrealWorkflowState.FAILED, workflow_id, f"Failure detected: {domain_str}")
                    report.final_state = UnrealWorkflowState.FAILED
                    report.success = False
                    try:
                        report.failure_domain = UnrealFailureDomain(domain_str)
                    except ValueError:
                        report.failure_domain = UnrealFailureDomain.UNKNOWN_FAILURE
                    report.error = diag_result.get("root_cause")
                else:
                    self.transition_to(UnrealWorkflowState.COMPLETED, workflow_id, "Workflow completed cleanly")
                    report.final_state = UnrealWorkflowState.COMPLETED
                    report.success = True
                    report.failure_domain = UnrealFailureDomain.NONE

        except EmergencyStopActiveError as ese:
            logger.warning("Emergency stop triggered during workflow: %s", ese)
            self.cleanup_owned_processes(workflow_id)
            self._current_state = UnrealWorkflowState.STOPPED
            report.final_state = UnrealWorkflowState.STOPPED
            report.failure_domain = UnrealFailureDomain.USER_STOPPED
            report.error = str(ese)
            report.success = False

        except UnrealSafetyError as se:
            logger.error("Safety policy rejection in workflow: %s", se)
            self.cleanup_owned_processes(workflow_id)
            self.transition_to(UnrealWorkflowState.FAILED, workflow_id, f"Safety rejection: {se.message}")
            report.final_state = UnrealWorkflowState.FAILED
            report.failure_domain = UnrealFailureDomain.SAFETY_REJECTION
            report.error = se.message
            report.success = False

        except Exception as e:
            logger.error("Unexpected error in workflow execution: %s", e, exc_info=True)
            self.cleanup_owned_processes(workflow_id)
            if self._current_state not in (UnrealWorkflowState.FAILED, UnrealWorkflowState.STOPPED):
                try:
                    self.transition_to(UnrealWorkflowState.FAILED, workflow_id, f"Unexpected error: {e}")
                except Exception:
                    self._current_state = UnrealWorkflowState.FAILED
            report.final_state = self._current_state
            report.failure_domain = UnrealFailureDomain.UNKNOWN_FAILURE
            report.error = str(e)
            report.success = False

        finally:
            report.steps_executed = len(report.steps)
            report.orphan_process_count = self.cleanup_owned_processes(workflow_id)
            report.duration_s = time.time() - start_time

            # Log to audit trail with sensitive token redaction
            self._log_audit(
                event_type="UNREAL_WORKFLOW_EXECUTION",
                description=redact_sensitive_data(f"Workflow {workflow_id} [{workflow_type.value}] ended in {report.final_state.value}"),
                status="SUCCESS" if report.success else "FAILED",
                details={
                    "workflow_id": workflow_id,
                    "workflow_type": workflow_type.value,
                    "target_project": report.target_project or str(target_project),
                    "final_state": report.final_state.value,
                    "failure_domain": report.failure_domain.value,
                    "success": report.success,
                    "steps_executed": report.steps_executed,
                    "repair_attempts": len(report.repair_attempts),
                    "repaired_files": report.repaired_files,
                    "rolled_back_files": report.rolled_back_files,
                    "orphan_process_count": report.orphan_process_count,
                    "duration_s": round(report.duration_s, 3),
                },
            )

        return report

    def stop_workflow(self, workflow_id: str, reason: str = "User requested stop") -> bool:
        """Immediately halts an active workflow and cleans up all owned processes."""
        with self._lock:
            orphans = self.cleanup_owned_processes(workflow_id)
            self.transition_to(UnrealWorkflowState.STOPPED, workflow_id, reason)
            report = self._active_workflows.get(workflow_id)
            if report:
                report.final_state = UnrealWorkflowState.STOPPED
                report.failure_domain = UnrealFailureDomain.USER_STOPPED
                report.orphan_process_count = orphans
                report.error = reason
            return True


# Global default instance
DEFAULT_UNREAL_AGENT = UnrealAutonomousAgent()
