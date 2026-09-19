"""
NR-AI Unified Android Agent: End-to-End Autonomous Android Integration Layer (Step 6 Phase 7).

Coordinates the full lifecycle of Android operations:
Understand Request -> Inspect State -> Build / Run / Inspect -> Observe UI / Runtime
-> Diagnose Errors -> Generate Advisory Repair -> Validate Proposal -> Apply Bounded Repair
-> Rebuild / Retest -> Verify Final State -> Report Authoritative Evidence.

Invariants Preserved:
1. Deterministic Tool Execution: All actual actions execute through existing safe tool layers with shell=False.
2. Advisory Model Role: Models never execute tools, shell commands, or modify files directly.
3. State Machine: Strictly bounded transitions across explicit workflow states.
4. Cross-Domain Error Handling: Distinguishes build vs runtime vs ANR vs UI vs device vs environment failures.
5. Safe Bounded Repair: Maximum 2 attempts, atomic checkpoints, pre-edit backup, byte-for-byte rollback.
6. Authoritative Ground Truth: Deterministic evidence > Model claims.
7. Emergency Stop: Immediate freeze at any workflow step.
8. Audit Trail: Redacted event logging for all transitions and operations.
"""

from dataclasses import dataclass, field
from enum import Enum
import json
import logging
import os
from pathlib import Path
import re
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
import uuid

from app.agent.android_safety import (
    ALLOWED_ANDROID_TOOLS,
    AUTHORIZED_DEVICE_SERIALS,
    AUTHORIZED_PACKAGE_NAME,
    AUTHORIZED_PROJECT_PATH,
    AndroidErrorCode,
    AndroidSafetyError,
    AndroidSafetyGate,
    EmergencyStopActiveError,
    MAX_REPAIR_ATTEMPTS,
)
from app.agent.android_tools import (
    AndroidToolRegistry,
    AndroidToolResult,
    SafeAdbClient,
    SafeGradleRunner,
)
from app.agent.android_verifier import (
    AndroidVerificationReport,
    AndroidVerifier,
)
from app.agent.android_code_repair import (
    AndroidCodeRepairEngine,
    AndroidErrorAnalyzer,
    AndroidErrorCategory,
    AndroidBuildError,
    EditProposal,
)
from app.agent.android_ui import (
    AndroidTargetRegistry,
    AndroidUIController,
    AndroidUIDistiller,
)
from app.agent.android_diagnostics import (
    AndroidDiagnosticsController,
    AndroidLogParser,
    AndroidRuntimeError,
    DiagnosticSnapshot,
    LogLevel,
    RuntimeErrorType,
    redact_sensitive_runtime_data,
)
from app.agent.model_router import CapabilityUnavailableError, ModelRouter
from app.config.model_config import ModelCapability
from app.memory.audit_logger import AuditLogger
from app.memory.context_memory import ProjectContextMemory
from app.agent.android_project_registry import AndroidProjectRegistry, AndroidProjectRecord
from app.agent.android_gradle_intelligence import GradleVersionCatalogEngine, VersionCatalogReport
from app.agent.android_ast import AndroidASTEngine, SourceASTReport
from app.agent.android_resource_graph import AndroidResourceGraphEngine, ResourceGraphReport
from app.agent.android_compose import JetpackComposeIntelligenceEngine, ComposeIntelligenceReport
from app.agent.android_test_results import AndroidTestResultParser, JUnitReport, LintReport
from app.agent.droid_task_state import DroidTaskStateStore
from app.agent.android_device_lifecycle import (
    DeviceLifecycleController,
    DeviceLifecycleReport,
    DeviceLifecycleState,
    BootConfiguration,
    DeploymentConfiguration,
    DeploymentResult,
)
from app.agent.android_compose_preview import (
    ComposePreviewEngine,
    ComposePreviewReport,
    LivePreviewResult,
    PreviewRenderStatus,
)
from app.agent.android_runtime_semantics import (
    RuntimeComposeSemanticsCorrelator,
    CorrelatedSemanticsReport,
    CorrelatedSemanticsNode,
    CorrelationEvidence,
)

from app.agent.android_reproduction import FailureReproductionEngine, ReproductionPlan, ReproductionResult, ReproductionState
from app.agent.android_ui_actions import ApprovedUIActionEngine, ApprovedUIActionType, UIActionResult
from app.agent.android_failure_evidence import FailureEvidenceCollector, EvidenceType, EvidenceRecord
from app.agent.android_root_cause import RootCauseAnalysisEngine, RootCauseReport, RootCauseClassification
from app.agent.android_repair_orchestrator import AutonomousRepairOrchestrator, RepairProposal, RepairExecutionResult, RepairOperation
from app.agent.android_regression import AndroidRegressionEngine, TestComparisonReport
from app.agent.android_e2e_engine import AndroidE2EEngine, E2EExecutionReport, E2EWorkflowStage, Phase4ExecutionReport

from app.agent.android_visual_verifier import (
    VisualVerificationEngine,
    VisualVerificationReport,
    VisualVerificationStatus,
    VisualAssertion,
    AssertionType,
    SafeScreenshotManager,
)
from app.agent.android_studio_intelligence import AndroidStudioIntelligence, AndroidStudioProjectSnapshot
from app.agent.android_project_graph import AndroidProjectGraphEngine, AndroidKnowledgeGraph
from app.agent.android_semantic_engine import AndroidSemanticEngine
from app.agent.android_resource_graph import AndroidResourceGraph
from app.agent.android_compose_intelligence import AndroidComposeIntelligence
from app.agent.android_test_intelligence import AndroidTestIntelligenceEngine
from app.agent.android_ui_debugger import AndroidUIDebugger
from app.agent.android_performance import AndroidPerformanceDiagnostics
from app.agent.android_project_memory import AndroidProjectMemoryStore
from app.agent.android_impact import AndroidImpactAnalyzer
from app.agent.android_model_reasoning import AndroidModelReasoningEngine

logger = logging.getLogger("NRAI.UnifiedAndroidAgent")

MAX_WORKFLOW_STEPS = 15
MAX_RETRIES_PER_STEP = 2


# -----------------------------------------------------------------------------
# States, Error Domains, and Workflow Types
# -----------------------------------------------------------------------------

class UnifiedAndroidState(str, Enum):
    """Explicit lifecycle states for the Unified Android Agent."""
    IDLE = "IDLE"
    INSPECTING = "INSPECTING"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    OBSERVING = "OBSERVING"
    DIAGNOSING = "DIAGNOSING"
    REPAIRING = "REPAIRING"
    REBUILDING = "REBUILDING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"
    # Phase 4 States
    INSPECTING_PROJECT = "INSPECTING_PROJECT"
    BUILDING_GRAPH = "BUILDING_GRAPH"
    ANALYZING_SOURCE = "ANALYZING_SOURCE"
    ANALYZING_RESOURCES = "ANALYZING_RESOURCES"
    ANALYZING_COMPOSE = "ANALYZING_COMPOSE"
    ANALYZING_TESTS = "ANALYZING_TESTS"
    CORRELATING_EVIDENCE = "CORRELATING_EVIDENCE"
    IMPACT_ANALYSIS = "IMPACT_ANALYSIS"


class UnifiedErrorDomain(str, Enum):
    """Structured categories distinguishing operational failure domains."""
    NONE = "NONE"
    BUILD_ERROR = "BUILD_ERROR"
    RUNTIME_CRASH = "RUNTIME_CRASH"
    ANR = "ANR"
    UI_STATE_FAILURE = "UI_STATE_FAILURE"
    DEVICE_UNAVAILABLE = "DEVICE_UNAVAILABLE"
    ENVIRONMENT_FAILURE = "ENVIRONMENT_FAILURE"
    SAFETY_REJECTION = "SAFETY_REJECTION"
    REPAIR_FAILURE = "REPAIR_FAILURE"


class UnifiedWorkflowType(str, Enum):
    """Recognized high-level Android workflows."""
    INSPECT_PROJECT = "INSPECT_PROJECT"
    BUILD_PROJECT = "BUILD_PROJECT"
    DIAGNOSE_BUILD = "DIAGNOSE_BUILD"
    DIAGNOSE_RUNTIME = "DIAGNOSE_RUNTIME"
    INSPECT_UI = "INSPECT_UI"
    INSPECT_LOGS = "INSPECT_LOGS"
    AUTONOMOUS_REPAIR = "AUTONOMOUS_REPAIR"
    END_TO_END = "END_TO_END"


class BuildAction(str, Enum):
    DEBUG_ASSEMBLE = "DEBUG_ASSEMBLE"
    CLEAN = "CLEAN"
    CHECK = "CHECK"


# -----------------------------------------------------------------------------
# Plan and Result Data Models
# -----------------------------------------------------------------------------

@dataclass
class UnifiedPlanStep:
    """A discrete, validated step in an autonomous Android workflow."""
    step_number: int
    action_type: str
    description: str
    params: Dict[str, Any] = field(default_factory=dict)
    expected_outcome: str = ""
    advisory_rationale: Optional[str] = None


@dataclass
class UnifiedAndroidPlan:
    """Structured plan generated for an Android workflow."""
    workflow_id: str
    goal: str
    workflow_type: UnifiedWorkflowType
    steps: List[UnifiedPlanStep] = field(default_factory=list)
    advisory_notes: Optional[str] = None
    created_at: float = field(default_factory=time.time)


@dataclass
class UnifiedExecutionResult:
    """Comprehensive execution report of an autonomous Android workflow."""
    request_id: str
    workflow_id: str
    goal: str
    workflow_type: UnifiedWorkflowType
    state: UnifiedAndroidState
    state_history: List[Dict[str, Any]] = field(default_factory=list)
    success: bool = False
    error_domain: UnifiedErrorDomain = UnifiedErrorDomain.NONE
    summary: str = ""
    error_message: Optional[str] = None
    error_code: Optional[str] = None
    diagnostics: Optional[Dict[str, Any]] = None
    repair_attempts: int = 0
    repair_history: List[Dict[str, Any]] = field(default_factory=list)
    verification_report: Optional[Dict[str, Any]] = None
    evidence: Dict[str, Any] = field(default_factory=dict)
    duration_s: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "workflow_id": self.workflow_id,
            "goal": self.goal,
            "workflow_type": self.workflow_type.value,
            "state": self.state.value,
            "state_history": self.state_history,
            "success": self.success,
            "error_domain": self.error_domain.value,
            "summary": self.summary,
            "error_message": self.error_message,
            "error_code": self.error_code,
            "diagnostics": self.diagnostics,
            "repair_attempts": self.repair_attempts,
            "repair_history": self.repair_history,
            "verification_report": self.verification_report,
            "evidence": self.evidence,
            "duration_s": round(self.duration_s, 2),
            "timestamp": self.timestamp,
        }

    @property
    def error(self) -> Optional[str]:
        return self.error_message


# -----------------------------------------------------------------------------
# Deterministic State Machine
# -----------------------------------------------------------------------------

class UnifiedStateMachine:
    """
    Manages deterministic transitions across explicit lifecycle states.
    Strictly validates transitions and guarantees emergency stop halt.
    """

    ALLOWED_TRANSITIONS: Dict[UnifiedAndroidState, Set[UnifiedAndroidState]] = {
        UnifiedAndroidState.IDLE: {
            UnifiedAndroidState.INSPECTING,
            UnifiedAndroidState.PLANNING,
            UnifiedAndroidState.EXECUTING,
            UnifiedAndroidState.OBSERVING,
            UnifiedAndroidState.DIAGNOSING,
            UnifiedAndroidState.INSPECTING_PROJECT,
            UnifiedAndroidState.BUILDING_GRAPH,
            UnifiedAndroidState.ANALYZING_SOURCE,
            UnifiedAndroidState.ANALYZING_RESOURCES,
            UnifiedAndroidState.ANALYZING_COMPOSE,
            UnifiedAndroidState.ANALYZING_TESTS,
            UnifiedAndroidState.CORRELATING_EVIDENCE,
            UnifiedAndroidState.IMPACT_ANALYSIS,
            UnifiedAndroidState.FAILED,
            UnifiedAndroidState.STOPPED,
        },
        UnifiedAndroidState.INSPECTING: {
            UnifiedAndroidState.PLANNING,
            UnifiedAndroidState.EXECUTING,
            UnifiedAndroidState.OBSERVING,
            UnifiedAndroidState.DIAGNOSING,
            UnifiedAndroidState.VERIFYING,
            UnifiedAndroidState.COMPLETED,
            UnifiedAndroidState.FAILED,
            UnifiedAndroidState.STOPPED,
        },
        UnifiedAndroidState.PLANNING: {
            UnifiedAndroidState.EXECUTING,
            UnifiedAndroidState.INSPECTING,
            UnifiedAndroidState.OBSERVING,
            UnifiedAndroidState.DIAGNOSING,
            UnifiedAndroidState.FAILED,
            UnifiedAndroidState.STOPPED,
        },
        UnifiedAndroidState.EXECUTING: {
            UnifiedAndroidState.OBSERVING,
            UnifiedAndroidState.DIAGNOSING,
            UnifiedAndroidState.REPAIRING,
            UnifiedAndroidState.VERIFYING,
            UnifiedAndroidState.COMPLETED,
            UnifiedAndroidState.FAILED,
            UnifiedAndroidState.STOPPED,
        },
        UnifiedAndroidState.OBSERVING: {
            UnifiedAndroidState.DIAGNOSING,
            UnifiedAndroidState.VERIFYING,
            UnifiedAndroidState.COMPLETED,
            UnifiedAndroidState.FAILED,
            UnifiedAndroidState.STOPPED,
        },
        UnifiedAndroidState.DIAGNOSING: {
            UnifiedAndroidState.REPAIRING,
            UnifiedAndroidState.VERIFYING,
            UnifiedAndroidState.COMPLETED,
            UnifiedAndroidState.FAILED,
            UnifiedAndroidState.STOPPED,
        },
        UnifiedAndroidState.REPAIRING: {
            UnifiedAndroidState.REBUILDING,
            UnifiedAndroidState.FAILED,
            UnifiedAndroidState.STOPPED,
        },
        UnifiedAndroidState.REBUILDING: {
            UnifiedAndroidState.VERIFYING,
            UnifiedAndroidState.DIAGNOSING,
            UnifiedAndroidState.REPAIRING,
            UnifiedAndroidState.FAILED,
            UnifiedAndroidState.STOPPED,
        },
        UnifiedAndroidState.VERIFYING: {
            UnifiedAndroidState.COMPLETED,
            UnifiedAndroidState.DIAGNOSING,
            UnifiedAndroidState.REPAIRING,
            UnifiedAndroidState.FAILED,
            UnifiedAndroidState.STOPPED,
        },
        UnifiedAndroidState.INSPECTING_PROJECT: {UnifiedAndroidState.COMPLETED, UnifiedAndroidState.FAILED, UnifiedAndroidState.STOPPED, UnifiedAndroidState.IDLE},
        UnifiedAndroidState.BUILDING_GRAPH: {UnifiedAndroidState.COMPLETED, UnifiedAndroidState.FAILED, UnifiedAndroidState.STOPPED, UnifiedAndroidState.IDLE},
        UnifiedAndroidState.ANALYZING_SOURCE: {UnifiedAndroidState.COMPLETED, UnifiedAndroidState.FAILED, UnifiedAndroidState.STOPPED, UnifiedAndroidState.IDLE},
        UnifiedAndroidState.ANALYZING_RESOURCES: {UnifiedAndroidState.COMPLETED, UnifiedAndroidState.FAILED, UnifiedAndroidState.STOPPED, UnifiedAndroidState.IDLE},
        UnifiedAndroidState.ANALYZING_COMPOSE: {UnifiedAndroidState.COMPLETED, UnifiedAndroidState.FAILED, UnifiedAndroidState.STOPPED, UnifiedAndroidState.IDLE},
        UnifiedAndroidState.ANALYZING_TESTS: {UnifiedAndroidState.COMPLETED, UnifiedAndroidState.FAILED, UnifiedAndroidState.STOPPED, UnifiedAndroidState.IDLE},
        UnifiedAndroidState.CORRELATING_EVIDENCE: {UnifiedAndroidState.COMPLETED, UnifiedAndroidState.FAILED, UnifiedAndroidState.STOPPED, UnifiedAndroidState.IDLE},
        UnifiedAndroidState.IMPACT_ANALYSIS: {UnifiedAndroidState.COMPLETED, UnifiedAndroidState.FAILED, UnifiedAndroidState.STOPPED, UnifiedAndroidState.IDLE},
        UnifiedAndroidState.COMPLETED: set(),
        UnifiedAndroidState.FAILED: set(),
        UnifiedAndroidState.STOPPED: set(),
    }

    def __init__(self, safety_gate: AndroidSafetyGate, audit_logger: Optional[AuditLogger] = None):
        self.safety = safety_gate
        self.audit = audit_logger
        self.current_state = UnifiedAndroidState.IDLE
        self.history: List[Dict[str, Any]] = [
            {"state": UnifiedAndroidState.IDLE.value, "timestamp": time.time(), "detail": "Initialized"}
        ]

    def transition(self, to_state: UnifiedAndroidState, detail: str = "") -> None:
        """Transitions to next state if valid, checking emergency stop first."""
        # Emergency stop overrides any non-stopped transition
        if self.safety.is_emergency_stop_active() and to_state != UnifiedAndroidState.STOPPED:
            to_state = UnifiedAndroidState.STOPPED
            detail = "Emergency stop activated: halting all operations."

        if to_state == self.current_state:
            return

        allowed = self.ALLOWED_TRANSITIONS.get(self.current_state, set())
        # Transitioning to STOPPED is always allowed from any state if emergency stop occurs
        if to_state != UnifiedAndroidState.STOPPED and to_state not in allowed:
            raise AndroidSafetyError(
                AndroidErrorCode.ACTION_NOT_ALLOWED,
                f"Invalid state transition from {self.current_state.value} to {to_state.value}.",
            )

        from_state = self.current_state
        self.current_state = to_state
        entry = {
            "from_state": from_state.value,
            "state": to_state.value,
            "timestamp": time.time(),
            "detail": redact_sensitive_runtime_data(detail),
        }
        self.history.append(entry)

        if self.audit:
            try:
                self.audit.log_event("UNIFIED_STATE_TRANSITION", entry)
            except Exception:
                pass


# -----------------------------------------------------------------------------
# Unified Task Planner (Advisory)
# -----------------------------------------------------------------------------

class UnifiedAndroidPlanner:
    """
    Deterministic & advisory task planner for Android workflows.
    Produces structured UnifiedAndroidPlan instances.
    Strictly advisory: possesses ZERO direct execution capability.
    """

    def __init__(self, model_router: Optional[ModelRouter] = None):
        self.router = model_router or ModelRouter()

    def plan_goal(self, goal: str, workflow_id: Optional[str] = None) -> UnifiedAndroidPlan:
        """Deterministically parses goal into a structured plan."""
        w_id = workflow_id or f"wf_{uuid.uuid4().hex[:8]}"
        g = (goal or "").strip().lower()

        if (
            ("inspect" in g and ("project" in g or "code" in g))
            or "project status" in g
            or "project metadata" in g
        ):
            w_type = UnifiedWorkflowType.INSPECT_PROJECT
            steps = [
                UnifiedPlanStep(1, "android.inspect_project", "Inspect project root, build files, and package identity", {}, "Project metadata extracted"),
                UnifiedPlanStep(2, "android.get_build_status", "Verify build artifacts and compile outputs", {}, "Build outputs checked"),
                UnifiedPlanStep(3, "verify_state", "Verify project readiness", {}, "Readiness confirmed"),
            ]
        elif (
            ("diagnose" in g and "build" in g)
            or "build error" in g
            or "build failure" in g
            or "why is build failing" in g
            or "compile error" in g
            or "why is the build failing" in g
            or "why is android build failing" in g
        ):
            w_type = UnifiedWorkflowType.DIAGNOSE_BUILD
            steps = [
                UnifiedPlanStep(1, "diagnose_build_failure", "Analyze Gradle build failure and compiler error output", {}, "Structured build error diagnosed"),
                UnifiedPlanStep(2, "verify_diagnosis", "Verify diagnostic evidence against compiler output", {}, "Error classified"),
            ]
        elif (
            ("diagnose" in g and ("runtime" in g or "crash" in g))
            or "runtime failure" in g
            or "why did app crash" in g
            or "runtime error" in g
            or "fatal exception" in g
            or ("android" in g and "crash" in g)
            or "android runtime" in g
            or "diagnose android runtime" in g
        ):
            w_type = UnifiedWorkflowType.DIAGNOSE_RUNTIME
            steps = [
                UnifiedPlanStep(1, "android.read_logcat", "Capture recent logcat messages with error severity", {"lines": 200, "severity": "E"}, "Logcat retrieved"),
                UnifiedPlanStep(2, "diagnose_runtime_crash", "Extract runtime exceptions, ANRs, and trace source files", {}, "Runtime crash diagnosed"),
            ]
        elif (
            ("build" in g and ("android" in g or "project" in g or "app" in g))
            or ("compile" in g and ("android" in g or "project" in g or "app" in g))
            or "assemble debug" in g
            or "gradle build" in g
        ):
            w_type = UnifiedWorkflowType.BUILD_PROJECT
            steps = [
                UnifiedPlanStep(1, "android.build_project", "Execute deterministic Gradle assembleDebug build", {"action": "DEBUG_ASSEMBLE"}, "Build completed"),
                UnifiedPlanStep(2, "android.get_build_status", "Verify APK artifact generated", {}, "APK verified"),
            ]
        elif (
            ("inspect" in g and ("ui" in g or "screen" in g))
            or "android ui" in g
            or "view screen" in g
        ):
            w_type = UnifiedWorkflowType.INSPECT_UI
            steps = [
                UnifiedPlanStep(1, "android.capture_screenshot", "Capture screen image and UI hierarchy", {}, "Screenshot and UI dump captured"),
                UnifiedPlanStep(2, "distill_ui", "Distill hierarchy into interactive targets with TTL", {}, "Targets registered"),
            ]
        elif (
            ("inspect" in g and "log" in g)
            or "android logs" in g
            or "show logs" in g
            or "read logcat" in g
        ):
            w_type = UnifiedWorkflowType.INSPECT_LOGS
            steps = [
                UnifiedPlanStep(1, "android.read_logcat", "Read bounded logcat messages with redaction", {"lines": 100}, "Logs captured"),
            ]
        elif (
            "repair" in g
            or "fix build" in g
            or "repair compilation" in g
            or "autonomous repair" in g
        ):
            w_type = UnifiedWorkflowType.AUTONOMOUS_REPAIR
            steps = [
                UnifiedPlanStep(1, "diagnose_build_failure", "Diagnose current build failure", {}, "Error diagnosed"),
                UnifiedPlanStep(2, "generate_repair_proposal", "Consult advisory model for bounded edit proposal", {}, "Proposal validated"),
                UnifiedPlanStep(3, "apply_repair", "Create backup checkpoint and apply atomic patch", {}, "Patch applied"),
                UnifiedPlanStep(4, "rebuild_project", "Rebuild project to verify repair resolution", {}, "Rebuild verified"),
            ]
        else:
            w_type = UnifiedWorkflowType.END_TO_END
            steps = [
                UnifiedPlanStep(1, "android.inspect_project", "Inspect project environment", {}, "Project verified"),
                UnifiedPlanStep(2, "android.build_project", "Build project", {"action": "DEBUG_ASSEMBLE"}, "Build verified"),
                UnifiedPlanStep(3, "verify_state", "Verify end-to-end outcome", {}, "All checks pass"),
            ]

        return UnifiedAndroidPlan(
            workflow_id=w_id,
            goal=goal,
            workflow_type=w_type,
            steps=steps,
        )

    def plan_with_advisory_model(self, goal: str, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Consults advisory model for high-level workflow planning.
        Model output is parsed as strict JSON. Model CANNOT execute tools.
        """
        sys_prompt = (
            "You are an ADVISORY planner for Android operations.\n"
            "You do NOT have execution authority. You cannot execute tools, shell commands, or ADB.\n"
            "Return only a valid JSON object describing your proposed plan with keys:\n"
            '{"workflow_type": str, "steps": [{"step_number": int, "action": str, "description": str}], "rationale": str}'
        )
        user_prompt = f"User goal: {goal}\nContext: {json.dumps(context)}"
        try:
            resp = self.router.execute(
                prompt=user_prompt,
                system_prompt=sys_prompt,
                task_type="highest",
                required_capabilities={ModelCapability.REASONING},
            )
            if resp.get("success") and resp.get("content"):
                match = re.search(r"\{[\s\S]*\}", resp["content"])
                if match:
                    return json.loads(match.group(0))
        except Exception:
            pass
        return None


# -----------------------------------------------------------------------------
# Cross-Domain Error Classifier
# -----------------------------------------------------------------------------

def classify_unified_error(
    raw_error: Union[str, Exception],
    error_code: Optional[str] = None,
    diagnostics: Optional[Dict[str, Any]] = None,
) -> UnifiedErrorDomain:
    """
    Accurately classifies an operational failure into one of 8 distinct domains.
    Prevents confusing environment/device failures with code/build failures.
    """
    err_str = str(raw_error).lower()
    code_str = str(error_code or "").upper()

    # 1. Repair Failure
    if any(k in code_str for k in (
        "REPAIR_FAILED", "REPAIR_UNSUPPORTED", "TOO_MANY_FILES_CHANGED",
        "PATCH_TOO_LARGE", "MALFORMED_PROPOSAL", "STALE_TARGET",
        "EDIT_VALIDATION_FAILED", "ROLLBACK_FAILED", "MALFORMED"
    )):
        return UnifiedErrorDomain.REPAIR_FAILURE
    if any(k in err_str for k in ("maximum repair attempts", "repair failed after", "stale target", "malformed proposal", "prohibited command")):
        return UnifiedErrorDomain.REPAIR_FAILURE

    # 2. Safety Rejection
    if any(k in code_str for k in (
        "NOT_AUTHORIZED", "ACTION_NOT_ALLOWED", "HIGH_RISK", "PROTECTED_FILE",
        "COORDINATES_OUT_OF_BOUNDS", "SCREEN_DIMENSION_MISMATCH"
    )):
        return UnifiedErrorDomain.SAFETY_REJECTION
    if "emergency stop" in err_str or code_str == "EMERGENCY_STOPPED":
        return UnifiedErrorDomain.SAFETY_REJECTION

    # 5. UI State Failure
    if any(k in code_str for k in ("TARGET_NOT_FOUND", "UI_EXTRACTION_FAILED", "SCREEN_CAPTURE_FAILED")):
        return UnifiedErrorDomain.UI_STATE_FAILURE

    # 6. Runtime Crash & ANR
    if diagnostics and diagnostics.get("has_crash"):
        err_type = diagnostics.get("primary_error", {}).get("error_type", "")
        if "ANR" in err_type:
            return UnifiedErrorDomain.ANR
        return UnifiedErrorDomain.RUNTIME_CRASH
    if "fatal exception" in err_str or "nullpointerexception" in err_str or "runtime exception" in err_str:
        return UnifiedErrorDomain.RUNTIME_CRASH
    if "anr in" in err_str or "application not responding" in err_str:
        return UnifiedErrorDomain.ANR

    # 7. Build Error
    if code_str == "BUILD_FAILED" or "compilation error" in err_str or "build failed" in err_str or "unresolved reference" in err_str:
        return UnifiedErrorDomain.BUILD_ERROR

    return UnifiedErrorDomain.BUILD_ERROR if "build" in err_str else UnifiedErrorDomain.ENVIRONMENT_FAILURE


# -----------------------------------------------------------------------------
# Unified Android Agent
# -----------------------------------------------------------------------------

class UnifiedAndroidAgent:
    """
    Final Integration Layer coordinating AndroidStudioAgent, SafeAdbClient,
    SafeGradleRunner, AndroidSafetyGate, AndroidCodeRepairEngine,
    AndroidDiagnosticsController, AndroidUIController, AndroidVerifier,
    ModelRouter, and AuditLogger.
    """

    def __init__(
        self,
        safety_gate: Optional[AndroidSafetyGate] = None,
        tool_registry: Optional[AndroidToolRegistry] = None,
        verifier: Optional[AndroidVerifier] = None,
        model_router: Optional[ModelRouter] = None,
        audit_logger: Optional[AuditLogger] = None,
        code_repair: Optional[AndroidCodeRepairEngine] = None,
        ui_controller: Optional[AndroidUIController] = None,
        diagnostics_controller: Optional[AndroidDiagnosticsController] = None,
        memory: Optional[ProjectContextMemory] = None,
        project_registry: Optional[AndroidProjectRegistry] = None,
        gradle_intelligence: Optional[GradleVersionCatalogEngine] = None,
        ast_engine: Optional[AndroidASTEngine] = None,
        resource_graph: Optional[AndroidResourceGraphEngine] = None,
        compose_intelligence: Optional[JetpackComposeIntelligenceEngine] = None,
        test_parser: Optional[AndroidTestResultParser] = None,
        task_state_store: Optional[DroidTaskStateStore] = None,
        lifecycle_controller: Optional[DeviceLifecycleController] = None,
        preview_engine: Optional[ComposePreviewEngine] = None,
        semantics_correlator: Optional[RuntimeComposeSemanticsCorrelator] = None,
        visual_verifier: Optional[VisualVerificationEngine] = None,
        screenshot_manager: Optional[SafeScreenshotManager] = None,
        reproduction_engine: Optional[FailureReproductionEngine] = None,
        ui_action_engine: Optional[ApprovedUIActionEngine] = None,
        evidence_collector: Optional[FailureEvidenceCollector] = None,
        root_cause_engine: Optional[RootCauseAnalysisEngine] = None,
        repair_orchestrator: Optional[AutonomousRepairOrchestrator] = None,
        regression_engine: Optional[AndroidRegressionEngine] = None,
        e2e_engine: Optional[AndroidE2EEngine] = None,
    ):
        self.project_registry = project_registry or AndroidProjectRegistry()
        self.safety = safety_gate or AndroidSafetyGate(project_registry=self.project_registry)
        if hasattr(self.safety, "project_registry") and self.safety.project_registry is None:
            self.safety.project_registry = self.project_registry

        self.tools = tool_registry or AndroidToolRegistry(safety_gate=self.safety)
        self.verifier = verifier or AndroidVerifier(safety_gate=self.safety, tool_registry=self.tools)
        self.router = model_router or ModelRouter()
        self.audit = audit_logger or AuditLogger()
        self.memory = memory or ProjectContextMemory()

        self.code_repair = code_repair or AndroidCodeRepairEngine(
            project_path=self.safety.authorized_project,
            safety_gate=self.safety,
            gradle_runner=self.tools.gradle,
            audit_logger=self.audit,
            router=self.router,
        )

        self.ui_controller = ui_controller or AndroidUIController(
            safety_gate=self.safety,
            adb_client=self.tools.adb,
            target_registry=AndroidTargetRegistry(safety_gate=self.safety),
            audit_logger=self.audit,
        )

        self.diagnostics_controller = diagnostics_controller or AndroidDiagnosticsController(
            safety_gate=self.safety,
            adb_client=self.tools.adb,
            audit_logger=self.audit,
        )

        self.gradle_intelligence = gradle_intelligence or GradleVersionCatalogEngine(safety_gate=self.safety)
        self.ast_engine = ast_engine or AndroidASTEngine(safety_gate=self.safety)
        self.resource_graph = resource_graph or AndroidResourceGraphEngine()
        self.compose_intelligence = compose_intelligence or JetpackComposeIntelligenceEngine(ast_engine=self.ast_engine)
        self.test_parser = test_parser or AndroidTestResultParser()
        self.task_state_store = task_state_store or DroidTaskStateStore()

        self.lifecycle_controller = lifecycle_controller or DeviceLifecycleController(
            safety_gate=self.safety,
            adb_client=self.tools.adb,
            audit_logger=self.audit,
            sdk_root=self.safety.sdk_root if hasattr(self.safety, 'sdk_root') else None,
        )
        self.preview_engine = preview_engine or ComposePreviewEngine(
            compose_intelligence=self.compose_intelligence,
            safety_gate=self.safety,
        )
        self.semantics_correlator = semantics_correlator or RuntimeComposeSemanticsCorrelator(
            compose_intelligence=self.compose_intelligence,
        )
        self.reproduction_engine = reproduction_engine or FailureReproductionEngine(safety_gate=self.safety, audit_logger=self.audit)
        self.ui_action_engine = ui_action_engine or ApprovedUIActionEngine(safety_gate=self.safety, audit_logger=self.audit)
        self.evidence_collector = evidence_collector or FailureEvidenceCollector(safety_gate=self.safety, audit_logger=self.audit)
        self.root_cause_engine = root_cause_engine or RootCauseAnalysisEngine(
            ast_engine=self.ast_engine,
            resource_graph=self.resource_graph,
            gradle_intelligence=self.gradle_intelligence,
            compose_intelligence=self.compose_intelligence,
            safety_gate=self.safety,
            audit_logger=self.audit,
        )
        self.repair_orchestrator = repair_orchestrator or AutonomousRepairOrchestrator(
            code_repair_engine=self.code_repair,
            ast_engine=self.ast_engine,
            gradle_runner=self.tools.gradle,
            safety_gate=self.safety,
            audit_logger=self.audit,
        )
        self.regression_engine = regression_engine or AndroidRegressionEngine(
            gradle_runner=self.tools.gradle,
            test_parser=self.test_parser,
            safety_gate=self.safety,
            audit_logger=self.audit,
        )
        self.screenshot_manager = screenshot_manager or SafeScreenshotManager(
            adb_client=self.tools.adb,
            safety_gate=self.safety,
        )
        self.visual_verifier = visual_verifier or VisualVerificationEngine(
            screenshot_manager=self.screenshot_manager,
            audit_logger=self.audit,
            safety_gate=self.safety,
        )
        self.e2e_engine = e2e_engine or AndroidE2EEngine(
            reproduction_engine=self.reproduction_engine,
            ui_actions=self.ui_action_engine,
            evidence_collector=self.evidence_collector,
            root_cause_engine=self.root_cause_engine,
            repair_orchestrator=self.repair_orchestrator,
            regression_engine=self.regression_engine,
            lifecycle_controller=self.lifecycle_controller,
            visual_verifier=self.visual_verifier,
            task_state_store=self.task_state_store,
            safety_gate=self.safety,
            audit_logger=self.audit,
        )

        # Phase 4 Intelligence Subsystems
        self.studio_intelligence = AndroidStudioIntelligence()
        self.project_graph_engine = AndroidProjectGraphEngine()
        self.semantic_engine = AndroidSemanticEngine()
        self.resource_graph_phase4 = AndroidResourceGraph()
        self.compose_intelligence_phase4 = AndroidComposeIntelligence()
        self.test_intelligence = AndroidTestIntelligenceEngine()
        self.ui_debugger = AndroidUIDebugger()
        self.performance_diagnostics = AndroidPerformanceDiagnostics(adb_client=self.tools.adb)
        self.project_memory = AndroidProjectMemoryStore()
        self.impact_analyzer = AndroidImpactAnalyzer()
        self.model_reasoning = AndroidModelReasoningEngine()

        self.planner = UnifiedAndroidPlanner(model_router=self.router)

    def inspect_version_catalog(self, toml_path: Optional[Union[str, Path]] = None) -> VersionCatalogReport:
        """Inspect and parse a Gradle libs.versions.toml catalog."""
        target = toml_path or (self.safety.authorized_project / "gradle" / "libs.versions.toml")
        return self.gradle_intelligence.parse_catalog_file(target)

    def inspect_source_ast(self, file_path: Union[str, Path]) -> SourceASTReport:
        """Inspect and extract structured AST from a Kotlin or Java source file."""
        return self.ast_engine.parse_file(file_path)

    def inspect_resource_graph(self, project_root: Optional[Union[str, Path]] = None) -> ResourceGraphReport:
        """Build bidirectional XML resource and reference graph."""
        root = project_root or self.safety.authorized_project
        return self.resource_graph.build_graph(root)

    def inspect_compose(self, file_path: Union[str, Path]) -> ComposeIntelligenceReport:
        """Analyze Jetpack Compose architecture, components, and state."""
        return self.compose_intelligence.analyze_file(file_path)

    def parse_test_results(self, xml_input: Union[str, Path]) -> JUnitReport:
        """Parse JUnit XML test results with location extraction and redaction."""
        return self.test_parser.parse_junit_xml(xml_input)

    def parse_lint_results(self, xml_input: Union[str, Path]) -> LintReport:
        """Parse Android Lint XML quality reports."""
        return self.test_parser.parse_lint_xml(xml_input)

    # -------------------------------------------------------------------------
    # Droid Phase 2: Live Device, Compose Preview, Runtime Semantics & Verification
    # -------------------------------------------------------------------------

    def boot_device(
        self,
        avd_name: str = "Pixel_6_API_34",
        timeout_seconds: float = 180.0,
        no_window: bool = False,
    ) -> DeviceLifecycleReport:
        """Boot authorized AVD with bounded empirical polling and checkpoint recording."""
        self.safety.check_emergency_stop()
        cfg = BootConfiguration(avd_name=avd_name, timeout_seconds=timeout_seconds, no_window=no_window)
        rep = self.lifecycle_controller.boot(cfg)

        if rep.state == DeviceLifecycleState.READY:
            task = self.task_state_store.get_active_task()
            if task:
                self.task_state_store.save_checkpoint(
                    task_id=task.task_id,
                    checkpoint_name="DEVICE_BOOTED",
                    data=rep.to_dict(),
                )
        return rep

    def get_device_lifecycle_status(self, avd_name: str = "Pixel_6_API_34") -> DeviceLifecycleReport:
        """Query current empirical lifecycle state of device."""
        return self.lifecycle_controller.get_status(avd_name)

    def stop_device(self, serial: Optional[str] = None, avd_name: str = "Pixel_6_API_34") -> DeviceLifecycleReport:
        """Gracefully stop device and clean up processes."""
        return self.lifecycle_controller.stop(serial=serial, avd_name=avd_name)

    def deploy_and_launch(
        self,
        apk_path: Optional[Union[str, Path]] = None,
        package_name: str = AUTHORIZED_PACKAGE_NAME,
        activity_name: str = "MainActivity",
        serial: Optional[str] = None,
    ) -> DeploymentResult:
        """Execute 6-step verified deployment pipeline."""
        self.safety.check_emergency_stop()
        cfg = DeploymentConfiguration(
            apk_path=apk_path,
            package_name=package_name,
            activity_name=activity_name,
        )
        res = self.lifecycle_controller.deploy(serial=serial, config=cfg)

        if res.success:
            task = self.task_state_store.get_active_task()
            if task:
                self.task_state_store.save_checkpoint(
                    task_id=task.task_id,
                    checkpoint_name="APP_DEPLOYED",
                    data=res.to_dict(),
                )
        return res

    def analyze_compose_previews(self, file_path_or_source: Union[str, Path]) -> ComposePreviewReport:
        """Analyze Jetpack Compose @Preview annotations and parameters."""
        self.safety.check_emergency_stop()
        path_candidate = Path(str(file_path_or_source))
        if path_candidate.exists() and path_candidate.is_file():
            return self.preview_engine.analyze_file(path_candidate)
        else:
            return self.preview_engine.analyze_source(str(file_path_or_source))

    def render_compose_preview(
        self,
        function_name: str,
        file_path: Union[str, Path],
        serial: Optional[str] = None,
    ) -> LivePreviewResult:
        """Attempt live preview render; returns PREVIEW_UNAVAILABLE truthfully if unconfigured."""
        return self.preview_engine.render_preview(function_name, file_path, device_serial=serial)

    def correlate_runtime_semantics(
        self,
        source_file_or_code: Union[str, Path],
        serial: Optional[str] = None,
    ) -> CorrelatedSemanticsReport:
        """Correlate source Compose components with runtime UI hierarchy targets."""
        self.safety.check_emergency_stop()
        # 1. Capture runtime UI snapshot
        ui_snapshot = self.ui_controller.inspect_screen(serial=serial)

        # 2. Extract Compose intelligence from file or source
        p = Path(str(source_file_or_code))
        if p.exists() and p.is_file():
            compose_rep = self.compose_intelligence.analyze_file(p)
        else:
            compose_rep = self.compose_intelligence.analyze_source(str(source_file_or_code))

        # 3. Correlate
        report = self.semantics_correlator.correlate(compose_rep, ui_snapshot)

        task = self.task_state_store.get_active_task()
        if task:
            self.task_state_store.save_checkpoint(
                task_id=task.task_id,
                checkpoint_name="SEMANTICS_CORRELATED",
                data={"correlation_ratio": report.correlation_ratio, "correlated_count": report.correlated_count},
            )
        return report

    def verify_ui_state(
        self,
        assertions: List[Union[Dict[str, Any], VisualAssertion]],
        serial: Optional[str] = None,
        capture_screenshot: bool = True,
        semantics_report: Optional[CorrelatedSemanticsReport] = None,
    ) -> VisualVerificationReport:
        """Evaluate structured visual and UI assertions against device state."""
        self.safety.check_emergency_stop()

        # Capture snapshot
        ui_snapshot = self.ui_controller.inspect_screen(serial=serial)

        screenshot_path = None
        if capture_screenshot and ui_snapshot.device_serial:
            try:
                screenshot_path = self.screenshot_manager.capture_screenshot(
                    serial=ui_snapshot.device_serial,
                    label="verify",
                )
            except Exception as e:
                logger.warning(f"Could not capture screenshot during verify_ui_state: {e}")

        # Normalize assertions
        parsed_assertions: List[VisualAssertion] = []
        for a in assertions:
            if isinstance(a, VisualAssertion):
                parsed_assertions.append(a)
            elif isinstance(a, dict):
                atype_str = a.get("assertion_type", "ELEMENT_VISIBLE")
                atype = AssertionType(atype_str) if atype_str in AssertionType.__members__ else AssertionType.ELEMENT_VISIBLE
                parsed_assertions.append(VisualAssertion(
                    assertion_type=atype,
                    query=a.get("query", ""),
                    expected_value=a.get("expected_value"),
                    optional=a.get("optional", False),
                    description=a.get("description", ""),
                ))

        report = self.visual_verifier.verify(
            assertions=parsed_assertions,
            ui_snapshot=ui_snapshot,
            semantics_report=semantics_report,
            screenshot_path=screenshot_path,
        )

        if report.status == VisualVerificationStatus.PASS:
            task = self.task_state_store.get_active_task()
            if task:
                self.task_state_store.save_checkpoint(
                    task_id=task.task_id,
                    checkpoint_name="UI_VERIFIED",
                    data=report.to_dict(),
                )
        return report

    def capture_verified_screenshot(
        self,
        serial: Optional[str] = None,
        label: str = "manual",
    ) -> Dict[str, Any]:
        """Capture bounded, sanitized screenshot from authorized device."""
        target_serial = serial or self.lifecycle_controller._active_serial or "emulator-5554"
        dest = self.screenshot_manager.capture_screenshot(serial=target_serial, label=label)
        return {
            "screenshot_path": str(dest),
            "filename": dest.name,
            "serial": target_serial,
            "timestamp": time.time(),
        }

    def run_full_e2e_verification(
        self,
        project_path: Optional[Union[str, Path]] = None,
        avd_name: str = "Pixel_6_API_34",
        assertions: Optional[List[VisualAssertion]] = None,
    ) -> Dict[str, Any]:
        """Run full end-to-end verification pipeline."""
        self.safety.check_emergency_stop()
        t0 = time.time()

        # Step 1: Boot device if not ready
        status = self.get_device_lifecycle_status(avd_name)
        if status.state != DeviceLifecycleState.READY and status.state != DeviceLifecycleState.RUNNING:
            status = self.boot_device(avd_name=avd_name)
            if status.state != DeviceLifecycleState.READY:
                return {
                    "success": False,
                    "stage": "BOOT",
                    "lifecycle_status": status.to_dict(),
                    "message": f"Could not boot device '{avd_name}'.",
                }

        serial = status.serial

        # Step 2: Deploy app
        deploy_res = self.deploy_and_launch(serial=serial)
        if not deploy_res.success:
            return {
                "success": False,
                "stage": "DEPLOY",
                "deployment_result": deploy_res.to_dict(),
                "message": f"Deployment failed: {deploy_res.error}",
            }

        # Step 3: Run semantics correlation on MainActivity
        root = Path(project_path or self.safety.authorized_project)
        main_act = root / "app" / "src" / "main" / "java" / "com" / "nrai" / "test" / "MainActivity.kt"
        semantics_rep = None
        if main_act.exists():
            try:
                semantics_rep = self.correlate_runtime_semantics(main_act, serial=serial)
            except Exception as e:
                logger.warning(f"Semantics correlation failed: {e}")

        # Step 4: Verify UI state
        default_assertions = assertions or [
            VisualAssertion(AssertionType.SCREEN_NOT_EMPTY, query="screen", expected_value=1),
            VisualAssertion(AssertionType.SCREEN_NAVIGATED, query="MainActivity", expected_value="com.nrai.test"),
        ]
        verify_rep = self.verify_ui_state(
            assertions=default_assertions,
            serial=serial,
            capture_screenshot=True,
            semantics_report=semantics_rep,
        )

        overall_success = verify_rep.status in (VisualVerificationStatus.PASS, VisualVerificationStatus.PARTIAL)

        return {
            "success": overall_success,
            "stage": "COMPLETED",
            "avd_name": avd_name,
            "serial": serial,
            "deployment": deploy_res.to_dict(),
            "semantics": semantics_rep.to_dict() if semantics_rep else None,
            "verification": verify_rep.to_dict(),
            "duration_seconds": time.time() - t0,
        }


    # -------------------------------------------------------------------------
    # Core Autonomous Execution Loop
    # -------------------------------------------------------------------------

    def execute_workflow(
        self,
        goal: str,
        request_id: Optional[str] = None,
        serial: str = "emulator-5554",
        allow_repair: bool = True,
        max_steps: int = MAX_WORKFLOW_STEPS,
    ) -> UnifiedExecutionResult:
        """
        Executes a bounded, verified unified Android workflow.
        Returns a complete UnifiedExecutionResult.
        """
        start_time = time.time()
        req_id = request_id or f"req_{uuid.uuid4().hex[:8]}"
        wf_id = f"wf_{uuid.uuid4().hex[:8]}"

        state_machine = UnifiedStateMachine(safety_gate=self.safety, audit_logger=self.audit)

        # 1. Emergency Stop Check
        if self.safety.is_emergency_stop_active():
            state_machine.transition(UnifiedAndroidState.STOPPED, "Workflow halted: EMERGENCY STOP active.")
            return self._build_result(
                req_id, wf_id, goal, UnifiedWorkflowType.END_TO_END,
                state_machine, False, UnifiedErrorDomain.SAFETY_REJECTION,
                summary="Workflow halted: EMERGENCY STOP is active.",
                error="EMERGENCY STOP is active. All Android operations frozen.",
                error_code=AndroidErrorCode.EMERGENCY_STOPPED.value,
                duration_s=time.time() - start_time,
            )

        # 2. Plan Generation
        try:
            state_machine.transition(UnifiedAndroidState.PLANNING, f"Formulating plan for: {goal}")
            plan = self.planner.plan_goal(goal, workflow_id=wf_id)
        except Exception as e:
            state_machine.transition(UnifiedAndroidState.FAILED, f"Planning failed: {e}")
            return self._build_result(
                req_id, wf_id, goal, UnifiedWorkflowType.END_TO_END,
                state_machine, False, UnifiedErrorDomain.ENVIRONMENT_FAILURE,
                summary=f"Planning failed: {e}",
                error=str(e),
                duration_s=time.time() - start_time,
            )

        # 3. Step Execution Dispatch
        w_type = plan.workflow_type
        evidence: Dict[str, Any] = {}
        diagnostics: Optional[Dict[str, Any]] = None
        repair_history: List[Dict[str, Any]] = []
        repair_attempts = 0

        try:
            if w_type == UnifiedWorkflowType.INSPECT_PROJECT:
                result = self._execute_inspect_project(plan, state_machine, start_time)
            elif w_type == UnifiedWorkflowType.BUILD_PROJECT:
                result = self._execute_build_project(plan, state_machine, start_time, allow_repair)
            elif w_type == UnifiedWorkflowType.DIAGNOSE_BUILD:
                result = self._execute_diagnose_build(plan, state_machine, start_time)
            elif w_type == UnifiedWorkflowType.DIAGNOSE_RUNTIME:
                result = self._execute_diagnose_runtime(plan, state_machine, start_time, serial)
            elif w_type == UnifiedWorkflowType.INSPECT_UI:
                result = self._execute_inspect_ui(plan, state_machine, start_time, serial)
            elif w_type == UnifiedWorkflowType.INSPECT_LOGS:
                result = self._execute_inspect_logs(plan, state_machine, start_time, serial)
            elif w_type == UnifiedWorkflowType.AUTONOMOUS_REPAIR:
                result = self._execute_autonomous_repair(plan, state_machine, start_time)
            else:
                result = self._execute_end_to_end(plan, state_machine, start_time, serial, allow_repair)

            result.request_id = req_id
            self._audit_final_result(result)
            return result

        except EmergencyStopActiveError:
            state_machine.transition(UnifiedAndroidState.STOPPED, "Halted by emergency stop.")
            return self._build_result(
                req_id, wf_id, goal, w_type,
                state_machine, False, UnifiedErrorDomain.SAFETY_REJECTION,
                summary="Workflow halted: EMERGENCY STOP activated.",
                error="EMERGENCY STOP is active.",
                error_code=AndroidErrorCode.EMERGENCY_STOPPED.value,
                duration_s=time.time() - start_time,
            )
        except AndroidSafetyError as se:
            state_machine.transition(UnifiedAndroidState.FAILED, f"Safety violation: {se.message}")
            domain = classify_unified_error(se.message, se.code.value)
            return self._build_result(
                req_id, wf_id, goal, w_type,
                state_machine, False, domain,
                summary=f"Safety gate blocked workflow: {se.message}",
                error=se.message,
                error_code=se.code.value,
                duration_s=time.time() - start_time,
            )
        except Exception as e:
            state_machine.transition(UnifiedAndroidState.FAILED, f"Execution failed: {e}")
            domain = classify_unified_error(e)
            return self._build_result(
                req_id, wf_id, goal, w_type,
                state_machine, False, domain,
                summary=f"Workflow execution failed: {e}",
                error=str(e),
                duration_s=time.time() - start_time,
            )

    # -------------------------------------------------------------------------
    # Specialized Workflow Handlers
    # -------------------------------------------------------------------------

    def _execute_inspect_project(
        self, plan: UnifiedAndroidPlan, sm: UnifiedStateMachine, start_time: float
    ) -> UnifiedExecutionResult:
        sm.transition(UnifiedAndroidState.INSPECTING, "Inspecting project metadata and structure")
        res = self.tools.inspect_project()
        if not res.success:
            sm.transition(UnifiedAndroidState.FAILED, res.message)
            return self._build_result(
                "", plan.workflow_id, plan.goal, plan.workflow_type,
                sm, False, UnifiedErrorDomain.ENVIRONMENT_FAILURE,
                summary="Project inspection failed",
                error=res.message,
                error_code=res.error_code,
                duration_s=time.time() - start_time,
            )

        build_res = self.tools.get_build_status()
        sm.transition(UnifiedAndroidState.VERIFYING, "Verifying project structure")
        evidence = {
            "project_metadata": res.data,
            "build_status": build_res.data,
        }
        sm.transition(UnifiedAndroidState.COMPLETED, "Project inspection completed successfully")
        return self._build_result(
            "", plan.workflow_id, plan.goal, plan.workflow_type,
            sm, True, UnifiedErrorDomain.NONE,
            summary="Android project inspection completed successfully.",
            evidence=evidence,
            duration_s=time.time() - start_time,
        )

    def _execute_build_project(
        self, plan: UnifiedAndroidPlan, sm: UnifiedStateMachine, start_time: float, allow_repair: bool
    ) -> UnifiedExecutionResult:
        sm.transition(UnifiedAndroidState.EXECUTING, "Invoking Gradle debug build")
        res = self.tools.build_project(action=BuildAction.DEBUG_ASSEMBLE)

        if res.success:
            sm.transition(UnifiedAndroidState.VERIFYING, "Verifying build outputs")
            apk_path = self.tools.gradle.find_debug_apk()
            evidence = {
                "build_action": BuildAction.DEBUG_ASSEMBLE.value,
                "apk_path": str(apk_path) if apk_path else None,
                "build_output_sample": res.output[:500] if res.output else "",
            }
            sm.transition(UnifiedAndroidState.COMPLETED, "Build completed and verified")
            return self._build_result(
                "", plan.workflow_id, plan.goal, plan.workflow_type,
                sm, True, UnifiedErrorDomain.NONE,
                summary="Android project built successfully.",
                evidence=evidence,
                duration_s=time.time() - start_time,
            )
        else:
            sm.transition(UnifiedAndroidState.DIAGNOSING, "Analyzing build failure")
            analyzer = AndroidErrorAnalyzer(self.safety.authorized_project)
            structured_err = analyzer.categorize_build_failure(res.output or res.message)
            diagnostics = structured_err.to_dict()

            if allow_repair and structured_err.category != AndroidErrorCategory.UNKNOWN_BUILD_ERROR:
                return self._execute_repair_loop(plan, sm, structured_err, start_time)

            sm.transition(UnifiedAndroidState.FAILED, "Build failed and repair not executed")
            return self._build_result(
                "", plan.workflow_id, plan.goal, plan.workflow_type,
                sm, False, UnifiedErrorDomain.BUILD_ERROR,
                summary=f"Build failed: {structured_err.category.value} - {structured_err.message}",
                error=structured_err.message,
                diagnostics=diagnostics,
                duration_s=time.time() - start_time,
            )

    def _execute_diagnose_build(
        self, plan: UnifiedAndroidPlan, sm: UnifiedStateMachine, start_time: float
    ) -> UnifiedExecutionResult:
        sm.transition(UnifiedAndroidState.DIAGNOSING, "Analyzing build error output")
        diag_res = self.code_repair.diagnose_build_failure()
        has_error = diag_res.get("has_error", False)
        err_info = diag_res.get("error_info")

        domain = UnifiedErrorDomain.BUILD_ERROR if has_error else UnifiedErrorDomain.NONE
        summary = f"Build diagnosis: {diag_res.get('summary')}"
        sm.transition(UnifiedAndroidState.COMPLETED, summary)
        return self._build_result(
            "", plan.workflow_id, plan.goal, plan.workflow_type,
            sm, True, domain,
            summary=summary,
            diagnostics=diag_res,
            evidence={"has_error": has_error, "error_info": err_info},
            duration_s=time.time() - start_time,
        )

    def _execute_diagnose_runtime(
        self, plan: UnifiedAndroidPlan, sm: UnifiedStateMachine, start_time: float, serial: str
    ) -> UnifiedExecutionResult:
        sm.transition(UnifiedAndroidState.OBSERVING, f"Observing runtime state on {serial}")
        dev_info = self.tools.adb.get_device_info(serial)
        if not dev_info.get("model") or dev_info.get("model") == "unknown":
            devices = self.tools.adb.list_devices()
            if not any(d.get("serial") == serial for d in devices):
                sm.transition(UnifiedAndroidState.FAILED, f"Device {serial} unavailable")
                return self._build_result(
                    "", plan.workflow_id, plan.goal, plan.workflow_type,
                    sm, False, UnifiedErrorDomain.DEVICE_UNAVAILABLE,
                    summary=f"Device '{serial}' is not available or offline.",
                    error=f"Device '{serial}' unavailable through ADB.",
                    error_code=AndroidErrorCode.DEVICE_NOT_FOUND.value,
                    duration_s=time.time() - start_time,
                )

        sm.transition(UnifiedAndroidState.DIAGNOSING, "Parsing runtime logs and crash patterns")
        diag_res = self.diagnostics_controller.diagnose_crash(serial, AUTHORIZED_PACKAGE_NAME)
        has_crash = diag_res.get("has_crash", False)
        primary_err = diag_res.get("primary_error") or {}
        err_type = primary_err.get("error_type", "")

        if "ANR" in err_type:
            domain = UnifiedErrorDomain.ANR
        elif has_crash:
            domain = UnifiedErrorDomain.RUNTIME_CRASH
        else:
            domain = UnifiedErrorDomain.NONE

        summary = f"Runtime diagnostics on {serial}: {diag_res.get('summary')}"
        sm.transition(UnifiedAndroidState.COMPLETED, summary)
        return self._build_result(
            "", plan.workflow_id, plan.goal, plan.workflow_type,
            sm, True, domain,
            summary=summary,
            diagnostics=diag_res,
            evidence={"device_info": dev_info, "has_crash": has_crash},
            duration_s=time.time() - start_time,
        )

    def _execute_inspect_ui(
        self, plan: UnifiedAndroidPlan, sm: UnifiedStateMachine, start_time: float, serial: str
    ) -> UnifiedExecutionResult:
        sm.transition(UnifiedAndroidState.OBSERVING, f"Capturing UI hierarchy on {serial}")
        ui_res = self.ui_controller.capture_and_inspect(serial)
        if not ui_res.get("success"):
            sm.transition(UnifiedAndroidState.FAILED, ui_res.get("error", "UI inspection failed"))
            domain = classify_unified_error(ui_res.get("error", ""), ui_res.get("error_code"))
            return self._build_result(
                "", plan.workflow_id, plan.goal, plan.workflow_type,
                sm, False, domain,
                summary=f"UI inspection failed: {ui_res.get('error')}",
                error=ui_res.get("error"),
                error_code=ui_res.get("error_code"),
                duration_s=time.time() - start_time,
            )

        summary = f"Captured {ui_res.get('target_count', 0)} interactive UI targets on {serial}."
        sm.transition(UnifiedAndroidState.COMPLETED, summary)
        return self._build_result(
            "", plan.workflow_id, plan.goal, plan.workflow_type,
            sm, True, UnifiedErrorDomain.NONE,
            summary=summary,
            evidence=ui_res,
            duration_s=time.time() - start_time,
        )

    def _execute_inspect_logs(
        self, plan: UnifiedAndroidPlan, sm: UnifiedStateMachine, start_time: float, serial: str
    ) -> UnifiedExecutionResult:
        sm.transition(UnifiedAndroidState.OBSERVING, f"Capturing bounded logcat on {serial}")
        logs = self.diagnostics_controller.capture_logs(serial, lines=100, package_name=AUTHORIZED_PACKAGE_NAME)
        line_count = len(logs.splitlines())
        summary = f"Captured {line_count} sanitized logcat lines from {serial}."
        sm.transition(UnifiedAndroidState.COMPLETED, summary)
        return self._build_result(
            "", plan.workflow_id, plan.goal, plan.workflow_type,
            sm, True, UnifiedErrorDomain.NONE,
            summary=summary,
            evidence={"lines_captured": line_count, "log_sample": logs[:1000]},
            duration_s=time.time() - start_time,
        )

    def _execute_autonomous_repair(
        self, plan: UnifiedAndroidPlan, sm: UnifiedStateMachine, start_time: float
    ) -> UnifiedExecutionResult:
        sm.transition(UnifiedAndroidState.DIAGNOSING, "Diagnosing build for autonomous repair")
        diag = self.code_repair.diagnose_build_failure()
        if not diag.get("has_error"):
            sm.transition(UnifiedAndroidState.COMPLETED, "No error detected: project builds cleanly")
            return self._build_result(
                "", plan.workflow_id, plan.goal, plan.workflow_type,
                sm, True, UnifiedErrorDomain.NONE,
                summary="No error detected: project builds cleanly.",
                evidence=diag,
                duration_s=time.time() - start_time,
            )

        raw_err = diag.get("error_info", {})
        analyzer = AndroidErrorAnalyzer(self.safety.authorized_project)
        try:
            cat = AndroidErrorCategory(raw_err.get("category", "UNKNOWN_BUILD_ERROR"))
        except (ValueError, KeyError):
            cat = AndroidErrorCategory.UNKNOWN_BUILD_ERROR

        structured_err = AndroidBuildError(
            category=cat,
            file_path=str(raw_err.get("file_path", "")) if raw_err.get("file_path") else None,
            line=raw_err.get("line_number") or raw_err.get("line"),
            message=raw_err.get("message", "Compilation error"),
        )
        return self._execute_repair_loop(plan, sm, structured_err, start_time)

    def _execute_repair_loop(
        self,
        plan: UnifiedAndroidPlan,
        sm: UnifiedStateMachine,
        error: AndroidBuildError,
        start_time: float,
    ) -> UnifiedExecutionResult:
        """Executes bounded, safe autonomous repair loop with pre-edit backup & rollback."""
        sm.transition(UnifiedAndroidState.REPAIRING, f"Starting autonomous repair for {error.category.value}")
        repair_res = self.code_repair.attempt_autonomous_repair(max_attempts=MAX_REPAIR_ATTEMPTS)

        repair_history = repair_res.get("attempts_history", [])
        repair_attempts = repair_res.get("attempts", 0)

        if repair_res.get("success"):
            sm.transition(UnifiedAndroidState.REBUILDING, "Rebuilding project after successful patch")
            sm.transition(UnifiedAndroidState.VERIFYING, "Verifying build resolution")
            sm.transition(UnifiedAndroidState.COMPLETED, "Autonomous repair succeeded and verified")
            return self._build_result(
                "", plan.workflow_id, plan.goal, plan.workflow_type,
                sm, True, UnifiedErrorDomain.NONE,
                summary=f"Autonomous repair succeeded after {repair_attempts} attempt(s).",
                repair_attempts=repair_attempts,
                repair_history=repair_history,
                evidence=repair_res,
                duration_s=time.time() - start_time,
            )
        else:
            sm.transition(UnifiedAndroidState.FAILED, "Autonomous repair failed or exhausted bounds")
            domain = classify_unified_error(repair_res.get("message", ""), repair_res.get("error_code"))
            if domain in (UnifiedErrorDomain.BUILD_ERROR, UnifiedErrorDomain.NONE):
                domain = UnifiedErrorDomain.REPAIR_FAILURE
            return self._build_result(
                "", plan.workflow_id, plan.goal, plan.workflow_type,
                sm, False, domain,
                summary=f"Autonomous repair failed: {repair_res.get('message')}",
                error=repair_res.get("message"),
                error_code=repair_res.get("error_code"),
                repair_attempts=repair_attempts,
                repair_history=repair_history,
                evidence=repair_res,
                duration_s=time.time() - start_time,
            )

    def _execute_end_to_end(
        self,
        plan: UnifiedAndroidPlan,
        sm: UnifiedStateMachine,
        start_time: float,
        serial: str,
        allow_repair: bool,
    ) -> UnifiedExecutionResult:
        # Step 1: Inspect
        sm.transition(UnifiedAndroidState.INSPECTING, "Inspecting project")
        insp = self.tools.inspect_project()
        if not insp.success:
            sm.transition(UnifiedAndroidState.FAILED, insp.message)
            return self._build_result(
                "", plan.workflow_id, plan.goal, plan.workflow_type,
                sm, False, UnifiedErrorDomain.ENVIRONMENT_FAILURE,
                summary="Project inspection failed",
                error=insp.message,
                duration_s=time.time() - start_time,
            )

        # Step 2: Build
        sm.transition(UnifiedAndroidState.EXECUTING, "Executing debug build")
        build_res = self.tools.build_project(action=BuildAction.DEBUG_ASSEMBLE)
        if not build_res.success:
            sm.transition(UnifiedAndroidState.DIAGNOSING, "Analyzing build failure")
            analyzer = AndroidErrorAnalyzer(self.safety.authorized_project)
            struct_err = analyzer.categorize_build_failure(build_res.output or build_res.message)
            if allow_repair and struct_err.category != AndroidErrorCategory.UNKNOWN_BUILD_ERROR:
                return self._execute_repair_loop(plan, sm, struct_err, start_time)
            sm.transition(UnifiedAndroidState.FAILED, "Build failed")
            return self._build_result(
                "", plan.workflow_id, plan.goal, plan.workflow_type,
                sm, False, UnifiedErrorDomain.BUILD_ERROR,
                summary=f"Build failed: {struct_err.message}",
                error=struct_err.message,
                diagnostics=struct_err.to_dict(),
                duration_s=time.time() - start_time,
            )

        # Step 3: Verify with AndroidVerifier
        sm.transition(UnifiedAndroidState.VERIFYING, "Running deterministic verifier suite")
        report = self.verifier.run_all_checks(project_path=self.safety.authorized_project)
        apk_path = self.tools.gradle.find_debug_apk()

        evidence = {
            "project_metadata": insp.data,
            "apk_path": str(apk_path) if apk_path else None,
            "verification_report": report.to_dict(),
        }

        sm.transition(UnifiedAndroidState.COMPLETED, "End-to-end workflow completed and verified")
        return self._build_result(
            "", plan.workflow_id, plan.goal, plan.workflow_type,
            sm, True, UnifiedErrorDomain.NONE,
            summary=f"Unified Android workflow completed successfully. Checks: {report.passed_checks}/{report.total_checks} passed.",
            verification_report=report.to_dict(),
            evidence=evidence,
            duration_s=time.time() - start_time,
        )

    # -------------------------------------------------------------------------
    # Verification Authority & Result Formatting
    # -------------------------------------------------------------------------

    def verify_workflow_outcome(
        self,
        result: UnifiedExecutionResult,
        model_claim: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Authoritative verification ensuring:
        DETERMINISTIC EVIDENCE > MODEL CLAIM.
        If a model claims success but ground truth failed, evidence overrides model.
        """
        if not model_claim:
            return {"verified": result.success, "overridden": False, "assessment": "EVIDENCE_ONLY"}

        claimed_success = bool(model_claim.get("success", False) or model_claim.get("build_success", False))
        claimed_crash = bool(model_claim.get("crash_detected", False))

        overridden = False
        override_reason = None

        if claimed_success and not result.success:
            overridden = True
            override_reason = (
                "Deterministic verification OVERRIDE: Advisory model claimed success, "
                f"but ground-truth state is {result.state.value} with error domain {result.error_domain.value}."
            )
            final_success = False
        elif claimed_crash and result.error_domain != UnifiedErrorDomain.RUNTIME_CRASH and result.error_domain != UnifiedErrorDomain.ANR:
            overridden = True
            override_reason = (
                "Deterministic verification OVERRIDE: Advisory model claimed a crash exists, "
                "but ground-truth diagnostic evidence contains zero unhandled runtime exceptions."
            )
            final_success = result.success
        else:
            final_success = result.success

        return {
            "verified": final_success,
            "overridden": overridden,
            "override_reason": override_reason,
            "deterministic_evidence": result.evidence,
        }

    def _build_result(
        self,
        request_id: str,
        workflow_id: str,
        goal: str,
        workflow_type: UnifiedWorkflowType,
        state_machine: UnifiedStateMachine,
        success: bool,
        error_domain: UnifiedErrorDomain,
        summary: str = "",
        error: Optional[str] = None,
        error_code: Optional[str] = None,
        diagnostics: Optional[Dict[str, Any]] = None,
        repair_attempts: int = 0,
        repair_history: Optional[List[Dict[str, Any]]] = None,
        verification_report: Optional[Dict[str, Any]] = None,
        evidence: Optional[Dict[str, Any]] = None,
        duration_s: float = 0.0,
    ) -> UnifiedExecutionResult:
        clean_summary = redact_sensitive_runtime_data(summary)
        clean_error = redact_sensitive_runtime_data(error) if error else None
        return UnifiedExecutionResult(
            request_id=request_id,
            workflow_id=workflow_id,
            goal=goal,
            workflow_type=workflow_type,
            state=state_machine.current_state,
            state_history=state_machine.history,
            success=success,
            error_domain=error_domain,
            summary=clean_summary,
            error_message=clean_error,
            error_code=error_code,
            diagnostics=diagnostics,
            repair_attempts=repair_attempts,
            repair_history=repair_history or [],
            verification_report=verification_report,
            evidence=evidence or {},
            duration_s=duration_s,
        )

    def _audit_final_result(self, res: UnifiedExecutionResult) -> None:
        try:
            sanitized = json.loads(redact_sensitive_runtime_data(json.dumps(res.to_dict(), default=str)))
            self.audit.log_event("UNIFIED_ANDROID_WORKFLOW_RESULT", sanitized, status="success" if res.success else "failure")
        except Exception as e:
            logger.warning(f"Failed to audit workflow result: {e}")

    # -------------------------------------------------------------------------
    # Droid Phase 3: Autonomous Debugging, Repair & E2E Engineering
    # -------------------------------------------------------------------------

    def reproduce_failure(
        self,
        bug_description: str,
        project_id: str = "nr_android_test",
        package_name: str = AUTHORIZED_PACKAGE_NAME,
        target_screen: Optional[str] = "MainActivity",
        serial: Optional[str] = None,
    ) -> ReproductionResult:
        self.safety.check_emergency_stop()
        plan = self.reproduction_engine.create_plan_from_description(
            bug_description=bug_description,
            project_id=project_id,
            package_name=package_name,
            target_screen=target_screen,
        )
        res = self.reproduction_engine.execute_plan(
            plan=plan,
            ui_action_engine=self.ui_action_engine,
            diagnostics_controller=self.diagnostics_controller,
            serial=serial,
        )
        if res.state == ReproductionState.REPRODUCED:
            task = self.task_state_store.get_active_task()
            if task:
                self.task_state_store.save_checkpoint(
                    task_id=task.task_id,
                    checkpoint_name="FAILURE_REPRODUCED",
                    data=res.to_dict(),
                )
        return res

    def execute_ui_action(
        self,
        action_type: str,
        identifier_type: str = "resource_id",
        identifier_value: str = "",
        text: Optional[str] = None,
        serial: Optional[str] = None,
    ) -> UIActionResult:
        self.safety.check_emergency_stop()
        atype = action_type.upper()
        if atype == "TAP":
            return self.ui_action_engine.tap_by_identifier(
                identifier_type=identifier_type,
                identifier_value=identifier_value,
                serial=serial,
            )
        elif atype == "TYPE_TEXT":
            return self.ui_action_engine.type_text_into_target(
                identifier_type=identifier_type,
                identifier_value=identifier_value,
                text=text or "",
                serial=serial,
            )
        elif atype == "PRESS_BACK":
            return self.ui_action_engine.press_back(serial=serial)
        elif atype == "SCROLL":
            return self.ui_action_engine.scroll(direction=text or "DOWN", serial=serial)
        else:
            return UIActionResult(
                success=False,
                action_type=ApprovedUIActionType.TAP,
                message=f"Unsupported UI action: {action_type}",
                error="ACTION_NOT_ALLOWED",
            )

    def collect_failure_evidence(
        self,
        task_id: str,
        project_id: str = "nr_android_test",
        logcat_text: Optional[str] = None,
        error_message: Optional[str] = None,
        test_case_name: Optional[str] = None,
    ) -> List[EvidenceRecord]:
        records = []
        if logcat_text:
            records.extend(self.evidence_collector.collect_from_logcat(logcat_text, task_id=task_id, project_id=project_id))
        if error_message:
            records.append(self.evidence_collector.collect_from_build_error(error_message, task_id=task_id, project_id=project_id))
        return records

    def diagnose_root_cause(
        self,
        task_id: str,
        evidence_records: Optional[List[EvidenceRecord]] = None,
        project_path: Optional[str] = None,
    ) -> RootCauseReport:
        records = evidence_records or self.evidence_collector.get_records_by_task(task_id)
        return self.root_cause_engine.analyze(records, project_path=project_path)

    def propose_and_apply_repair(
        self,
        proposal: RepairProposal,
        validate_build: bool = True,
    ) -> RepairExecutionResult:
        res = self.repair_orchestrator.execute_repair(proposal, validate_build=validate_build)
        if res.success:
            task = self.task_state_store.get_active_task()
            if task:
                self.task_state_store.save_checkpoint(
                    task_id=task.task_id,
                    checkpoint_name="REPAIR_APPLIED",
                    data=res.to_dict(),
                )
        return res

    def run_regression_check(
        self,
        changed_files: List[Union[str, Path]],
        task_id: str = "T-DEFAULT",
    ) -> TestComparisonReport:
        affected = self.regression_engine.identify_affected_tests(changed_files)
        report = self.regression_engine.run_tests(task_id=task_id)
        return self.regression_engine.compare_test_runs(report, report, affected_classes=affected)

    def run_autonomous_engineering_loop(
        self,
        bug_description: str,
        project_id: str = "nr_android_test",
        package_name: str = AUTHORIZED_PACKAGE_NAME,
        target_screen: Optional[str] = "MainActivity",
        serial: Optional[str] = None,
        auto_repair: bool = True,
        mock_mode: bool = False,
    ) -> E2EExecutionReport:
        return self.e2e_engine.run_engineering_loop(
            bug_description=bug_description,
            project_id=project_id,
            package_name=package_name,
            target_screen=target_screen,
            serial=serial,
            auto_repair=auto_repair,
            mock_mode=mock_mode,
        )

    # -------------------------------------------------------------------------
    # Phase 4 Advanced Android Engineering Intelligence Operations
    # -------------------------------------------------------------------------

    def inspect_android_studio_project(
        self,
        project_path: Optional[Union[str, Path]] = None,
    ) -> AndroidStudioProjectSnapshot:
        """Inspects project structure, AGP, Gradle, JBR, and Android Studio environment."""
        p = project_path or self.safety.authorized_project
        return self.studio_intelligence.inspect_project(p)

    def build_gradle_knowledge_graph(
        self,
        project_path: Optional[Union[str, Path]] = None,
    ) -> AndroidKnowledgeGraph:
        """Constructs an authoritative multi-module Android Knowledge Graph."""
        p = project_path or self.safety.authorized_project
        return self.project_graph_engine.build_knowledge_graph(p)

    def analyze_kotlin_semantics(
        self,
        project_path: Optional[Union[str, Path]] = None,
        file_path: Optional[Union[str, Path]] = None,
    ) -> Dict[str, Any]:
        """Indexes Kotlin/Java AST structural facts and semantic inferences."""
        p = Path(project_path or self.safety.authorized_project).resolve()
        if file_path:
            facts = self.semantic_engine.analyze_file(file_path)
            return {"file": str(file_path), "facts": [f.to_dict() for f in facts], "symbols": len(self.semantic_engine.symbols)}
        facts_count = 0
        for kfile in list(p.glob("**/src/**/*.kt"))[:10]:
            facts_count += len(self.semantic_engine.analyze_file(kfile))
        return {"project_path": str(p), "total_facts": facts_count, "total_symbols": len(self.semantic_engine.symbols)}

    def check_compose_state_flow(
        self,
        project_path: Optional[Union[str, Path]] = None,
    ) -> Dict[str, Any]:
        """Analyzes Jetpack Compose composables, state holders, and interaction flows."""
        p = project_path or self.safety.authorized_project
        report = self.compose_intelligence_phase4.analyze_project(p)
        return report.to_dict()

    def audit_android_xml_resources(
        self,
        project_path: Optional[Union[str, Path]] = None,
    ) -> Dict[str, Any]:
        """Audits Android XML resources, layout references, and configuration qualifiers."""
        p = project_path or self.safety.authorized_project
        report = self.resource_graph_phase4.build_graph(p)
        return report.to_dict()

    def diagnose_test_failure(
        self,
        project_path: Optional[Union[str, Path]] = None,
    ) -> Dict[str, Any]:
        """Scans test reports and diagnoses root causes with source correlation."""
        p = project_path or self.safety.authorized_project
        report = self.test_intelligence.analyze_project_tests(p)
        return report.to_dict()

    def debug_android_ui_behavior(
        self,
        action: str = "tap",
        target: str = "button",
        pre_elements: Optional[List[str]] = None,
        post_elements: Optional[List[str]] = None,
        logcat_snippet: str = "",
    ) -> Dict[str, Any]:
        """Diagnoses UI behavior anomalies, state divergence, and disconnected callbacks."""
        pre = pre_elements or ["Item 0", "Counter: 0"]
        post = post_elements or ["Item 0", "Counter: 0"]
        diag = self.ui_debugger.diagnose_interaction(
            pre_elements=pre,
            post_elements=post,
            action=action,
            target_text=target,
            logcat_snippet=logcat_snippet,
        )
        return diag.to_dict()

    def measure_startup_performance(
        self,
        serial: Optional[str] = None,
        component: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Measures bounded startup, memory, CPU, and ANR metrics on authorized device."""
        s = serial or "emulator-5554"
        comp = component or f"{AUTHORIZED_PACKAGE_NAME}/.MainActivity"
        report = self.performance_diagnostics.generate_report(
            serial=s,
            package_name=AUTHORIZED_PACKAGE_NAME,
            component=comp,
        )
        return report.to_dict()

    def calculate_blast_radius(
        self,
        changed_files: Optional[List[str]] = None,
        project_path: Optional[Union[str, Path]] = None,
    ) -> Dict[str, Any]:
        """Calculates impact radius across modules, files, resources, and test sets."""
        p = Path(project_path or self.safety.authorized_project).resolve()
        kg = self.project_graph_engine.build_knowledge_graph(p)
        files = changed_files or [str(f) for f in list(p.glob("**/MainActivity.kt"))[:1]]
        report = self.impact_analyzer.analyze_impact(files, kg=kg)
        return report.to_dict()

    def run_advanced_engineering_loop(
        self,
        engineering_goal: str,
        project_path: Optional[Union[str, Path]] = None,
        mock_mode: bool = False,
    ) -> Phase4ExecutionReport:
        """Runs the 19-stage Advanced Android Engineering Intelligence Loop."""
        p = project_path or self.safety.authorized_project
        return self.e2e_engine.run_phase4_engineering_loop(
            engineering_goal=engineering_goal,
            project_path=p,
            mock_mode=mock_mode,
        )

