"""
NR-AI AndroidStudioAgent: Goal-Driven Safe Android Studio & Toolchain Agent (Step 6 Phase 1).

Executes bounded, verified workflows across Android Studio, Gradle, and ADB
using deterministic tools, strict path/device allowlists, and ground-truth verification.

Core Loop:
1. UNDERSTAND GOAL
2. INSPECT ENVIRONMENT
3. BUILD PLAN (Deterministic rules or Advisory Model via ModelRouter)
4. SELECT NEXT ACTION
5. VALIDATE ACTION (Safety Gate, Tool Allowlist, Project/Device bounds)
6. CHECK EMERGENCY STOP
7. EXECUTE DETERMINISTIC TOOL
8. CAPTURE AFTER STATE
9. VERIFY RESULT (AndroidVerifier)
10. RECOVER IF SAFE (Bounded retry <= 2)
11. UPDATE CONTEXT MEMORY & AUDIT LOG
12. REPORT
"""

from dataclasses import dataclass, field
from enum import Enum
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
    RiskLevel,
)
from app.agent.android_tools import (
    AndroidToolRegistry,
    AndroidToolResult,
)
from app.agent.android_verifier import (
    AndroidVerificationReport,
    AndroidVerifier,
)
from app.agent.model_router import CapabilityUnavailableError, ModelRouter
from app.config.model_config import ModelCapability
from app.memory.audit_logger import AuditLogger
from app.memory.context_memory import ProjectContextMemory

logger = logging.getLogger("NRAI.AndroidStudioAgent")

MAX_WORKFLOW_STEPS = 15
MAX_RETRIES_PER_STEP = 2


# -----------------------------------------------------------------------------
# Structured Action Intent & Workflow Report
# -----------------------------------------------------------------------------

@dataclass
class AndroidActionIntent:
    """A strongly validated, discrete action intent proposed by the planner or model."""
    tool: str
    params: Dict[str, Any] = field(default_factory=dict)
    description: str = ""
    expected_outcome: str = ""

    def validate(self, safety_gate: AndroidSafetyGate) -> None:
        """Validates that the intent strictly adheres to authorized boundaries."""
        safety_gate.validate_tool_name(self.tool)
        if not isinstance(self.params, dict):
            raise AndroidSafetyError(
                AndroidErrorCode.ACTION_NOT_ALLOWED,
                "Intent params must be a dictionary.",
            )


@dataclass
class AndroidWorkflowReport:
    """Comprehensive execution report of an autonomous Android Studio workflow."""
    workflow_id: str
    goal: str
    success: bool
    total_steps: int
    steps_executed: int
    steps: List[Dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    error: Optional[str] = None
    error_code: Optional[str] = None
    requires_confirmation: bool = False
    pending_action: Optional[Dict[str, Any]] = None
    duration_s: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "goal": self.goal,
            "success": self.success,
            "total_steps": self.total_steps,
            "steps_executed": self.steps_executed,
            "steps": self.steps,
            "summary": self.summary,
            "error": self.error,
            "error_code": self.error_code,
            "requires_confirmation": self.requires_confirmation,
            "pending_action": self.pending_action,
            "duration_s": round(self.duration_s, 2),
            "timestamp": self.timestamp,
        }


# -----------------------------------------------------------------------------
# AndroidStudioAgent Implementation
# -----------------------------------------------------------------------------

class AndroidStudioAgent:
    """
    Autonomous, Goal-Driven Android Studio Agent Foundation.
    Coordinates perception, tool invocation, safety gating, and verification.
    """

    def __init__(
        self,
        tool_registry: Optional[AndroidToolRegistry] = None,
        verifier: Optional[AndroidVerifier] = None,
        safety_gate: Optional[AndroidSafetyGate] = None,
        model_router: Optional[ModelRouter] = None,
        audit_logger: Optional[AuditLogger] = None,
        memory: Optional[ProjectContextMemory] = None,
    ):
        self.safety = safety_gate or AndroidSafetyGate()
        self.tools = tool_registry or AndroidToolRegistry(safety_gate=self.safety)
        self.verifier = verifier or AndroidVerifier(safety_gate=self.safety, tool_registry=self.tools)
        self.router = model_router or ModelRouter()
        self.audit = audit_logger or AuditLogger()
        self.memory = memory or ProjectContextMemory()

        from app.agent.android_code_repair import AndroidCodeRepairEngine
        self.code_repair = AndroidCodeRepairEngine(
            project_path=self.safety.authorized_project,
            safety_gate=self.safety,
            gradle_runner=self.tools.gradle,
            audit_logger=self.audit,
            router=self.router,
        )

        from app.agent.android_ui import AndroidUIController, AndroidTargetRegistry
        self.ui_controller = AndroidUIController(
            safety_gate=self.safety,
            adb_client=self.tools.adb,
            target_registry=AndroidTargetRegistry(safety_gate=self.safety),
            audit_logger=self.audit,
        )

        from app.agent.android_diagnostics import AndroidDiagnosticsController
        self.diagnostics_controller = AndroidDiagnosticsController(
            safety_gate=self.safety,
            adb_client=self.tools.adb,
            audit_logger=self.audit,
        )

    # -------------------------------------------------------------------------
    # Goal Parsing & Deterministic Planning
    # -------------------------------------------------------------------------

    def plan_goal(self, goal: str) -> List[AndroidActionIntent]:
        """
        Translates user goal into a deterministic sequence of AndroidActionIntents.
        """
        g = (goal or "").strip().lower()
        intents: List[AndroidActionIntent] = []

        # 0. Pipeline: Build -> Deploy -> Run -> Verify
        if any(w in g for w in ("build and deploy", "deploy app", "build and run", "run pipeline", "build, deploy, run", "deploy")):
            serial = "15930545720012G" if "15930545720012g" in g or "vivo" in g else "emulator-5554"
            intents.append(AndroidActionIntent(
                tool="android.build_project",
                params={"action": "DEBUG_ASSEMBLE"},
                description="Execute debug build on authorized project",
            ))
            intents.append(AndroidActionIntent(
                tool="android.get_build_status",
                params={},
                description="Verify build output APK",
            ))
            intents.append(AndroidActionIntent(
                tool="android.list_devices",
                params={},
                description="Verify attached devices",
            ))
            intents.append(AndroidActionIntent(
                tool="android.install_test_apk",
                params={"serial": serial},
                description=f"Install verified NR-AI debug APK to {serial}",
            ))
            intents.append(AndroidActionIntent(
                tool="android.launch_app",
                params={"serial": serial, "package_name": AUTHORIZED_PACKAGE_NAME},
                description=f"Launch {AUTHORIZED_PACKAGE_NAME} on {serial}",
            ))
            intents.append(AndroidActionIntent(
                tool="android.capture_log",
                params={"serial": serial, "package_name": AUTHORIZED_PACKAGE_NAME, "lines": 50},
                description=f"Capture filtered logcat from {serial}",
            ))
            intents.append(AndroidActionIntent(
                tool="android.verify_app",
                params={"serial": serial, "package_name": AUTHORIZED_PACKAGE_NAME},
                description=f"Verify {AUTHORIZED_PACKAGE_NAME} on {serial}",
            ))
            return intents

        # 1. Verification
        if any(w in g for w in ("verify all", "verify environment", "check environment", "verify android")):
            intents.append(AndroidActionIntent(
                tool="android.inspect_project",
                params={"project_path": str(self.safety.authorized_project)},
                description="Verify authorized project structure",
            ))
            intents.append(AndroidActionIntent(
                tool="android.list_devices",
                params={},
                description="Verify attached devices against allowlist",
            ))
            intents.append(AndroidActionIntent(
                tool="android.get_build_status",
                params={},
                description="Verify build output status",
            ))
            return intents

        # 2. Build Project
        if any(w in g for w in ("build project", "build debug", "assemble", "assembledebug", "gradle build")):
            intents.append(AndroidActionIntent(
                tool="android.build_project",
                params={"action": "DEBUG_ASSEMBLE"},
                description="Execute debug build on authorized project",
            ))
            intents.append(AndroidActionIntent(
                tool="android.get_build_status",
                params={},
                description="Check output APK after build",
            ))
            return intents

        # 3. Clean Project
        if "clean" in g and "project" in g:
            intents.append(AndroidActionIntent(
                tool="android.build_project",
                params={"action": "CLEAN"},
                description="Execute clean on authorized project",
            ))
            return intents

        # 4. Launch / Open Android Studio
        if any(w in g for w in ("launch studio", "launch android studio", "open studio", "open android studio")):
            intents.append(AndroidActionIntent(
                tool="android.launch_studio",
                params={},
                description="Launch Android Studio executable",
            ))
            return intents

        # 5. Focus Android Studio
        if any(w in g for w in ("focus studio", "focus android studio", "switch to studio")):
            intents.append(AndroidActionIntent(
                tool="android.focus_studio",
                params={},
                description="Bring Android Studio window to foreground",
            ))
            return intents

        # 6. Open Project in Studio
        if "open project" in g:
            intents.append(AndroidActionIntent(
                tool="android.open_project",
                params={"project_path": str(self.safety.authorized_project)},
                description="Open authorized project in Android Studio",
            ))
            return intents

        # 7. Inspect Project
        if "inspect" in g:
            intents.append(AndroidActionIntent(
                tool="android.inspect_project",
                params={"project_path": str(self.safety.authorized_project)},
                description="Inspect modules, SDKs, and dependencies of authorized project",
            ))
            return intents

        # 8. List Devices
        if "devices" in g or "list devices" in g:
            intents.append(AndroidActionIntent(
                tool="android.list_devices",
                params={},
                description="Query attached ADB devices",
            ))
            return intents

        # 9. List Emulators
        if "emulators" in g or "list avds" in g:
            intents.append(AndroidActionIntent(
                tool="android.list_emulators",
                params={},
                description="Query installed AVD emulators",
            ))
            return intents

        # 10. Start Emulator
        if "start emulator" in g or "launch emulator" in g:
            intents.append(AndroidActionIntent(
                tool="android.start_emulator",
                params={"avd_name": "Pixel_6_API_34"},
                description="Start authorized Pixel_6_API_34 emulator",
            ))
            return intents

        # 11. Stop Emulator
        if "stop emulator" in g or "kill emulator" in g:
            intents.append(AndroidActionIntent(
                tool="android.stop_emulator",
                params={"serial": "emulator-5554"},
                description="Stop emulator-5554",
            ))
            return intents

        # 12. Install APK
        if "install" in g and "apk" in g:
            # Extract serial if specified, otherwise default to emulator-5554
            serial = "15930545720012G" if "15930545720012g" in g or "vivo" in g else "emulator-5554"
            intents.append(AndroidActionIntent(
                tool="android.install_test_apk",
                params={"serial": serial},
                description=f"Install verified NR-AI debug APK to {serial}",
            ))
            return intents

        # 13. Launch App
        if "launch app" in g or "run app" in g or "start app" in g:
            serial = "15930545720012G" if "15930545720012g" in g or "vivo" in g else "emulator-5554"
            intents.append(AndroidActionIntent(
                tool="android.launch_app",
                params={"serial": serial, "package_name": AUTHORIZED_PACKAGE_NAME},
                description=f"Launch {AUTHORIZED_PACKAGE_NAME} on {serial}",
            ))
            return intents

        # 14. Capture Log / Logcat
        if "log" in g or "logcat" in g:
            serial = "15930545720012G" if "15930545720012g" in g or "vivo" in g else "emulator-5554"
            intents.append(AndroidActionIntent(
                tool="android.capture_log",
                params={"serial": serial, "lines": 100},
                description=f"Capture logcat from {serial}",
            ))
            return intents

        # 15. Verify App
        if "verify app" in g or "app status" in g:
            serial = "15930545720012G" if "15930545720012g" in g or "vivo" in g else "emulator-5554"
            intents.append(AndroidActionIntent(
                tool="android.verify_app",
                params={"serial": serial, "package_name": AUTHORIZED_PACKAGE_NAME},
                description=f"Verify {AUTHORIZED_PACKAGE_NAME} state on {serial}",
            ))
            return intents

        # Default fallback: inspect project
        intents.append(AndroidActionIntent(
            tool="android.inspect_project",
            params={"project_path": str(self.safety.authorized_project)},
            description="Inspect authorized project status",
        ))
        return intents

    # -------------------------------------------------------------------------
    # Advisory Model Consultation
    # -------------------------------------------------------------------------

    def consult_model(self, user_goal: str) -> Optional[str]:
        """
        Consults an advisory LLM for suggestions or diagnostic reasoning.
        Models never execute OS commands or tools directly.
        """
        try:
            spec = self.router.route_with_capabilities(
                required_capabilities={ModelCapability.CODING, ModelCapability.REASONING},
                prompt=f"Android automation goal: {user_goal}",
            )
            return f"Model '{spec.model_id}' advisory: analyze goal '{user_goal}' using authorized tools."
        except Exception as e:
            logger.info(f"Advisory model consultation skipped: {e}")
            return None

    # -------------------------------------------------------------------------
    # Workflow Execution Loop
    # -------------------------------------------------------------------------

    def execute_workflow(
        self,
        user_goal: str,
        user_confirmed: bool = False,
    ) -> AndroidWorkflowReport:
        """
        Executes a bounded, verified multi-step Android workflow.
        """
        workflow_id = f"android_wf_{uuid.uuid4().hex[:8]}"
        start_time = time.time()
        logger.info(f"[{workflow_id}] Starting Android workflow: '{user_goal}' (Confirmed: {user_confirmed})")

        # Check emergency stop at workflow entry
        if self.safety.is_emergency_stop_active():
            return AndroidWorkflowReport(
                workflow_id=workflow_id,
                goal=user_goal,
                success=False,
                total_steps=0,
                steps_executed=0,
                summary="Workflow halted: EMERGENCY STOP is active.",
                error="EMERGENCY STOP is active. All Android operations are frozen.",
                error_code=AndroidErrorCode.EMERGENCY_STOPPED.value,
                duration_s=time.time() - start_time,
            )

        # Check if user goal is an end-to-end pipeline goal
        g_lower = (user_goal or "").strip().lower()
        pipeline_keywords = (
            "build and deploy", "deploy app", "build and run", "run pipeline",
            "build, deploy, run", "pipeline", "deploy", "build and verify",
        )
        if any(kw in g_lower for kw in pipeline_keywords):
            serial = "15930545720012G" if ("15930545720012g" in g_lower or "vivo" in g_lower) else ("emulator-5554" if "emulator" in g_lower else None)
            return self.execute_pipeline(serial=serial, user_confirmed=user_confirmed, workflow_id=workflow_id, goal=user_goal)

        # Check for vague / unrestricted modification requests
        if any(w in g_lower for w in ("edit anything", "modify the project however you want", "arbitrary edit")):
            return AndroidWorkflowReport(
                workflow_id=workflow_id,
                goal=user_goal,
                success=False,
                total_steps=0,
                steps_executed=0,
                summary="Unrestricted or vague code modification is strictly forbidden.",
                error="Vague or unrestricted code modification requests rejected.",
                error_code=AndroidErrorCode.ACTION_NOT_ALLOWED.value,
                duration_s=time.time() - start_time,
            )

        # Check for build repair / self-healing requests
        if any(kw in g_lower for kw in ("fix build", "repair build", "heal build", "fix android build", "repair android build", "repair compilation error")):
            return self.execute_build_repair(user_goal=user_goal, workflow_id=workflow_id, start_time=start_time)

        # Check for code inspection requests
        if "inspect code" in g_lower:
            return self._execute_code_inspection_workflow(user_goal=user_goal, workflow_id=workflow_id, start_time=start_time)

        # Check for explain build error requests
        if any(kw in g_lower for kw in ("explain build error", "explain error", "why is android build failing", "why is the android build failing", "why is build failing", "diagnose build error")):
            return self._execute_explain_error_workflow(user_goal=user_goal, workflow_id=workflow_id, start_time=start_time)

        # Check for UI inspection / perception requests
        if any(kw in g_lower for kw in ("inspect the android screen", "inspect android screen", "what is on the android screen", "what is on screen", "screen state", "inspect screen")):
            return self._execute_ui_inspect_workflow(user_goal=user_goal, workflow_id=workflow_id, start_time=start_time)

        # Check for UI target discovery requests
        if any(kw in g_lower for kw in ("find the ", "find button", "find element", "find ui", "locate ")):
            return self._execute_ui_find_workflow(user_goal=user_goal, workflow_id=workflow_id, start_time=start_time)

        # Check for UI interaction requests
        if any(kw in g_lower for kw in ("tap ", "click ", "scroll down", "scroll up", "scroll ", "swipe ", "go back", "press back", "press home")):
            return self._execute_ui_interaction_workflow(user_goal=user_goal, workflow_id=workflow_id, start_time=start_time)

        # Check for UI verification requests
        if any(kw in g_lower for kw in ("verify the android screen", "verify android screen", "verify ui", "verify screen")):
            return self._execute_ui_verify_workflow(user_goal=user_goal, workflow_id=workflow_id, start_time=start_time)

        # Check for Android runtime error diagnosis / crash analysis
        if any(kw in g_lower for kw in ("why did the android app crash", "why did the app crash", "diagnose android error", "diagnose crash", "android crash", "what happened in android", "inspect android runtime", "diagnose runtime")):
            serial = "15930545720012G" if ("15930545720012g" in g_lower or "vivo" in g_lower) else ("emulator-5554" if "emulator" in g_lower else None)
            return self._execute_runtime_diagnosis_workflow(user_goal=user_goal, workflow_id=workflow_id, start_time=start_time, serial=serial or "emulator-5554")

        # Check for Android logcat inspection requests
        if any(kw in g_lower for kw in ("show android logs", "check android logs", "capture android logs", "android logcat", "show logcat", "check logcat")):
            serial = "15930545720012G" if ("15930545720012g" in g_lower or "vivo" in g_lower) else ("emulator-5554" if "emulator" in g_lower else None)
            return self._execute_logcat_inspection_workflow(user_goal=user_goal, workflow_id=workflow_id, start_time=start_time, serial=serial or "emulator-5554")

        # Plan actions
        intents = self.plan_goal(user_goal)
        executed_steps: List[Dict[str, Any]] = []

        overall_success = True
        error_msg = None
        error_code = None
        requires_confirmation = False
        pending_action = None

        for idx, intent in enumerate(intents[:MAX_WORKFLOW_STEPS]):
            step_num = idx + 1

            # Validate intent structure and allowlists
            try:
                intent.validate(self.safety)
            except AndroidSafetyError as se:
                overall_success = False
                error_msg = se.message
                error_code = se.code.value
                executed_steps.append({
                    "step": step_num,
                    "tool": intent.tool,
                    "success": False,
                    "error": se.message,
                    "error_code": se.code.value,
                })
                break

            # Execute tool with bounded retry
            retry_count = 0
            step_result: Optional[AndroidToolResult] = None

            while retry_count <= MAX_RETRIES_PER_STEP:
                step_result = self.tools.execute_tool(
                    tool_name=intent.tool,
                    params=intent.params,
                    user_confirmed=user_confirmed,
                )

                if step_result.requires_confirmation:
                    requires_confirmation = True
                    pending_action = {"tool": intent.tool, "params": intent.params}
                    break

                if step_result.success:
                    break

                # If emergency stop occurred, abort immediately
                if step_result.error_code == AndroidErrorCode.EMERGENCY_STOPPED.value:
                    break

                # If non-retryable error (e.g. unauthorized project/device), do not retry
                if step_result.error_code in (
                    AndroidErrorCode.PROJECT_NOT_AUTHORIZED.value,
                    AndroidErrorCode.DEVICE_NOT_AUTHORIZED.value,
                    AndroidErrorCode.APK_NOT_AUTHORIZED.value,
                    AndroidErrorCode.PACKAGE_MISMATCH.value,
                    AndroidErrorCode.ACTION_NOT_ALLOWED.value,
                ):
                    break

                retry_count += 1
                logger.info(f"[{workflow_id}] Step {step_num} failed attempt {retry_count}. Retrying...")
                time.sleep(0.5)

            executed_steps.append({
                "step": step_num,
                "tool": intent.tool,
                "success": step_result.success if step_result else False,
                "data": step_result.data if step_result else {},
                "message": step_result.message if step_result else "",
                "error": step_result.error if step_result else "Execution failed",
                "error_code": step_result.error_code if step_result else None,
                "retries": retry_count,
            })

            if requires_confirmation:
                overall_success = False
                error_msg = step_result.error if step_result else "Confirmation required"
                error_code = AndroidErrorCode.ACTION_NOT_ALLOWED.value
                break

            if not step_result or not step_result.success:
                overall_success = False
                error_msg = step_result.error if step_result else "Step failed"
                error_code = step_result.error_code if step_result else AndroidErrorCode.ACTION_NOT_ALLOWED.value
                break

        duration = time.time() - start_time
        summary = (
            f"Android workflow completed successfully in {round(duration, 2)}s ({len(executed_steps)} steps)."
            if overall_success
            else f"Android workflow stopped at step {len(executed_steps)}: {error_msg}"
        )

        report = AndroidWorkflowReport(
            workflow_id=workflow_id,
            goal=user_goal,
            success=overall_success,
            total_steps=len(intents),
            steps_executed=len(executed_steps),
            steps=executed_steps,
            summary=summary,
            error=error_msg,
            error_code=error_code,
            requires_confirmation=requires_confirmation,
            pending_action=pending_action,
            duration_s=duration,
        )

        # Log final workflow audit
        self._audit_workflow(report)
        return report

    # -------------------------------------------------------------------------
    # Pipeline Execution (Step 6 Phase 2)
    # -------------------------------------------------------------------------

    def execute_pipeline(
        self,
        serial: Optional[str] = None,
        user_confirmed: bool = False,
        workflow_id: Optional[str] = None,
        goal: str = "Build, deploy, run, and verify com.nrai.test",
    ) -> AndroidWorkflowReport:
        """
        Executes the Step 6 Phase 2 deterministic pipeline:
        BUILD -> VERIFY BUILD -> SELECT AUTHORIZED DEVICE -> INSTALL AUTHORIZED APK ->
        LAUNCH com.nrai.test -> CAPTURE STATE -> VERIFY APPLICATION -> REPORT RESULT
        """
        wf_id = workflow_id or f"android_pipe_{uuid.uuid4().hex[:8]}"
        start_time = time.time()
        steps: List[Dict[str, Any]] = []

        def check_stop() -> Optional[AndroidWorkflowReport]:
            if self.safety.is_emergency_stop_active():
                return self._build_pipeline_report(
                    wf_id, goal, False, steps,
                    summary="Pipeline halted: EMERGENCY STOP is active.",
                    error="EMERGENCY STOP is active. All Android operations are frozen.",
                    error_code=AndroidErrorCode.EMERGENCY_STOPPED.value,
                    start_time=start_time,
                )
            return None

        # 1. Check Emergency Stop #1
        stop_rep = check_stop()
        if stop_rep:
            return stop_rep

        # 2. Stage 1: Build Project (DEBUG_ASSEMBLE)
        build_tool_res = self.tools.execute_tool("android.build_project", {"action": "DEBUG_ASSEMBLE"})
        steps.append({
            "stage": "BUILD",
            "tool": "android.build_project",
            "success": build_tool_res.success,
            "data": build_tool_res.data,
            "error": build_tool_res.error,
            "error_code": build_tool_res.error_code,
        })
        if not build_tool_res.success:
            return self._build_pipeline_report(
                wf_id, goal, False, steps,
                summary=f"Build failed: {build_tool_res.error}",
                error=build_tool_res.error or "Build failed",
                error_code=AndroidErrorCode.BUILD_FAILED.value,
                start_time=start_time,
            )

        # 3. Check Emergency Stop #2
        stop_rep = check_stop()
        if stop_rep:
            return stop_rep

        # 4. Stage 2: Verify Build Artifact & Locate APK
        status_res = self.tools.execute_tool("android.get_build_status", {})
        apk_info = status_res.data.get("apk_info", {})
        apk_path_str = apk_info.get("path")

        if not status_res.data.get("has_debug_apk") or not apk_path_str:
            steps.append({
                "stage": "VERIFY_BUILD",
                "tool": "android.get_build_status",
                "success": False,
                "error": "Debug APK was not found after build.",
                "error_code": AndroidErrorCode.APK_NOT_FOUND.value,
            })
            return self._build_pipeline_report(
                wf_id, goal, False, steps,
                summary="Build artifact verification failed: APK not found.",
                error="Debug APK was not found in authorized build directory.",
                error_code=AndroidErrorCode.APK_NOT_FOUND.value,
                start_time=start_time,
            )

        apk_path = Path(apk_path_str)
        try:
            validated_apk = self.safety.validate_apk_path(apk_path)
            self.safety.validate_package_name(AUTHORIZED_PACKAGE_NAME)
        except AndroidSafetyError as se:
            steps.append({
                "stage": "VERIFY_BUILD",
                "tool": "android.get_build_status",
                "success": False,
                "error": se.message,
                "error_code": se.code.value,
            })
            return self._build_pipeline_report(
                wf_id, goal, False, steps,
                summary=f"APK validation failed: {se.message}",
                error=se.message,
                error_code=se.code.value,
                start_time=start_time,
            )

        steps.append({
            "stage": "VERIFY_BUILD",
            "tool": "android.get_build_status",
            "success": True,
            "data": {"apk_path": str(validated_apk), "apk_info": apk_info},
        })

        # 5. Check Emergency Stop #3
        stop_rep = check_stop()
        if stop_rep:
            return stop_rep

        # 6. Stage 3: Select Authorized Device
        target_device = None
        if serial:
            try:
                target_device = self.safety.validate_device_serial(serial)
            except AndroidSafetyError as se:
                steps.append({
                    "stage": "SELECT_DEVICE",
                    "tool": "android.list_devices",
                    "success": False,
                    "error": se.message,
                    "error_code": se.code.value,
                })
                return self._build_pipeline_report(
                    wf_id, goal, False, steps,
                    summary=f"Device selection failed: {se.message}",
                    error=se.message,
                    error_code=se.code.value,
                    start_time=start_time,
                )

        dev_res = self.tools.execute_tool("android.list_devices", {})
        authorized_devs = dev_res.data.get("authorized_devices", []) if dev_res and dev_res.data else []
        if not authorized_devs:
            time.sleep(0.5)
            dev_res = self.tools.execute_tool("android.list_devices", {})
            authorized_devs = dev_res.data.get("authorized_devices", []) if dev_res and dev_res.data else []

        if not authorized_devs:
            steps.append({
                "stage": "SELECT_DEVICE",
                "tool": "android.list_devices",
                "success": False,
                "error": "No authorized device connected.",
                "error_code": AndroidErrorCode.DEVICE_NOT_FOUND.value,
            })
            return self._build_pipeline_report(
                wf_id, goal, False, steps,
                summary="Device selection failed: No authorized device found.",
                error="No authorized test device connected (emulator-5554 or 15930545720012G).",
                error_code=AndroidErrorCode.DEVICE_NOT_FOUND.value,
                start_time=start_time,
            )

        if target_device:
            found = next((d for d in authorized_devs if d.get("serial") == target_device), None)
            if not found:
                steps.append({
                    "stage": "SELECT_DEVICE",
                    "tool": "android.list_devices",
                    "success": False,
                    "error": f"Specified device '{target_device}' is not connected.",
                    "error_code": AndroidErrorCode.DEVICE_NOT_FOUND.value,
                })
                return self._build_pipeline_report(
                    wf_id, goal, False, steps,
                    summary=f"Device selection failed: '{target_device}' not found or offline.",
                    error=f"Device '{target_device}' is not connected.",
                    error_code=AndroidErrorCode.DEVICE_NOT_FOUND.value,
                    start_time=start_time,
                )
        else:
            # Prefer emulator-5554 for deterministic automated testing
            em_dev = next((d for d in authorized_devs if d.get("serial") == "emulator-5554"), None)
            target_device = em_dev["serial"] if em_dev else authorized_devs[0]["serial"]

        steps.append({
            "stage": "SELECT_DEVICE",
            "tool": "android.list_devices",
            "success": True,
            "data": {"selected_device": target_device, "devices": authorized_devs},
        })

        # 7. Check Emergency Stop #4
        stop_rep = check_stop()
        if stop_rep:
            return stop_rep

        # 8. Stage 4: High-Risk Installation Confirmation Gate
        if not user_confirmed:
            steps.append({
                "stage": "INSTALL_CONFIRMATION",
                "tool": "android.install_test_apk",
                "success": False,
                "error": "Confirmation required for high-risk action: android.install_test_apk",
                "error_code": AndroidErrorCode.INSTALL_CONFIRMATION_REQUIRED.value,
                "requires_confirmation": True,
            })
            return self._build_pipeline_report(
                wf_id, goal, False, steps,
                summary=f"INSTALL_CONFIRMATION_REQUIRED: Confirmation required to install APK on {target_device}.",
                error="Explicit user confirmation required for APK installation.",
                error_code=AndroidErrorCode.INSTALL_CONFIRMATION_REQUIRED.value,
                requires_confirmation=True,
                pending_action={"tool": "android.install_test_apk", "params": {"serial": target_device, "apk_path": str(validated_apk)}},
                start_time=start_time,
            )

        # 9. Check Emergency Stop #5
        stop_rep = check_stop()
        if stop_rep:
            return stop_rep

        # 10. Stage 5: Revalidate & Install APK with bounded retry (<= 2)
        try:
            self.safety.revalidate_apk_integrity(validated_apk)
            self.safety.validate_device_serial(target_device)
        except AndroidSafetyError as se:
            steps.append({
                "stage": "INSTALL_APK",
                "tool": "android.install_test_apk",
                "success": False,
                "error": se.message,
                "error_code": se.code.value,
            })
            return self._build_pipeline_report(
                wf_id, goal, False, steps,
                summary=f"Pre-install revalidation failed: {se.message}",
                error=se.message,
                error_code=se.code.value,
                start_time=start_time,
            )

        install_retry = 0
        install_res = None
        while install_retry <= MAX_RETRIES_PER_STEP:
            stop_rep = check_stop()
            if stop_rep:
                return stop_rep

            install_res = self.tools.execute_tool(
                "android.install_test_apk",
                {"serial": target_device, "apk_path": str(validated_apk)},
                user_confirmed=True,
            )
            if install_res.success:
                break
            install_retry += 1
            if install_retry <= MAX_RETRIES_PER_STEP:
                time.sleep(0.5)

        steps.append({
            "stage": "INSTALL_APK",
            "tool": "android.install_test_apk",
            "success": install_res.success if install_res else False,
            "data": install_res.data if install_res else {},
            "error": install_res.error if install_res else "Install failed",
            "error_code": install_res.error_code if install_res else AndroidErrorCode.INSTALL_FAILED.value,
            "retries": install_retry,
        })

        if not install_res or not install_res.success:
            return self._build_pipeline_report(
                wf_id, goal, False, steps,
                summary=f"APK installation failed after {install_retry} retries: {install_res.error if install_res else 'Unknown error'}",
                error=install_res.error if install_res else "Installation failed",
                error_code=AndroidErrorCode.INSTALL_FAILED.value,
                start_time=start_time,
            )

        # 11. Check Emergency Stop #6
        stop_rep = check_stop()
        if stop_rep:
            return stop_rep

        # 12. Stage 6: Launch App (ONLY com.nrai.test) with bounded retry (<= 2)
        launch_retry = 0
        launch_res = None
        while launch_retry <= MAX_RETRIES_PER_STEP:
            stop_rep = check_stop()
            if stop_rep:
                return stop_rep

            self.safety.validate_device_serial(target_device)
            launch_res = self.tools.execute_tool(
                "android.launch_app",
                {"serial": target_device, "package_name": AUTHORIZED_PACKAGE_NAME},
            )
            if launch_res.success:
                break
            launch_retry += 1
            if launch_retry <= MAX_RETRIES_PER_STEP:
                time.sleep(0.5)

        steps.append({
            "stage": "LAUNCH_APP",
            "tool": "android.launch_app",
            "success": launch_res.success if launch_res else False,
            "data": launch_res.data if launch_res else {},
            "error": launch_res.error if launch_res else "Launch failed",
            "error_code": launch_res.error_code if launch_res else AndroidErrorCode.LAUNCH_FAILED.value,
            "retries": launch_retry,
        })

        if not launch_res or not launch_res.success:
            return self._build_pipeline_report(
                wf_id, goal, False, steps,
                summary=f"App launch failed after {launch_retry} retries: {launch_res.error if launch_res else 'Unknown error'}",
                error=launch_res.error if launch_res else "Launch failed",
                error_code=AndroidErrorCode.LAUNCH_FAILED.value,
                start_time=start_time,
            )

        # 13. Check Emergency Stop #7
        stop_rep = check_stop()
        if stop_rep:
            return stop_rep

        # 14. Stage 7: State Capture
        app_state = self.tools.adb.get_app_state(target_device, AUTHORIZED_PACKAGE_NAME)
        logcat_sample = self.tools.adb.capture_logcat(target_device, lines=50, filter_package=AUTHORIZED_PACKAGE_NAME)

        steps.append({
            "stage": "CAPTURE_STATE",
            "tool": "android.capture_log",
            "success": True,
            "data": {"app_state": app_state, "logcat_sample": logcat_sample[-500:] if len(logcat_sample) > 500 else logcat_sample},
        })

        # 15. Check Emergency Stop #8
        stop_rep = check_stop()
        if stop_rep:
            return stop_rep

        # 16. Stage 8: Ground-Truth Verification (Checks A through J)
        verif_rep = self.verifier.run_pipeline_verification(
            build_result=build_tool_res.data,
            apk_path=validated_apk,
            serial=target_device,
            install_result=install_res.data if install_res else {},
            launch_result=launch_res.data if launch_res else {},
            app_state=app_state,
            package_name=AUTHORIZED_PACKAGE_NAME,
            safety_violation=False,
        )

        steps.append({
            "stage": "GROUND_TRUTH_VERIFY",
            "tool": "android.verify_app",
            "success": verif_rep.all_passed,
            "data": verif_rep.to_dict(),
            "error": None if verif_rep.all_passed else "One or more verification checks failed.",
            "error_code": None if verif_rep.all_passed else AndroidErrorCode.VERIFICATION_FAILED.value,
        })

        overall_ok = verif_rep.all_passed
        summary = (
            f"Android pipeline completed successfully: Built, verified, installed, launched, and verified on {target_device} in {round(time.time() - start_time, 2)}s."
            if overall_ok
            else f"Android pipeline finished with verification failures: {verif_rep.passed_checks}/{verif_rep.total_checks} checks passed."
        )

        report = self._build_pipeline_report(
            wf_id, goal, overall_ok, steps,
            summary=summary,
            error=None if overall_ok else "Pipeline verification failed.",
            error_code=None if overall_ok else AndroidErrorCode.VERIFICATION_FAILED.value,
            start_time=start_time,
        )
        return report

    def _build_pipeline_report(
        self,
        workflow_id: str,
        goal: str,
        success: bool,
        steps: List[Dict[str, Any]],
        summary: str,
        error: Optional[str] = None,
        error_code: Optional[str] = None,
        requires_confirmation: bool = False,
        pending_action: Optional[Dict[str, Any]] = None,
        start_time: float = 0.0,
    ) -> AndroidWorkflowReport:
        duration = time.time() - start_time
        report = AndroidWorkflowReport(
            workflow_id=workflow_id,
            goal=goal,
            success=success,
            total_steps=8,
            steps_executed=len(steps),
            steps=steps,
            summary=summary,
            error=error,
            error_code=error_code,
            requires_confirmation=requires_confirmation,
            pending_action=pending_action,
            duration_s=duration,
        )
        self._audit_workflow(report)
        return report

    def _audit_workflow(self, report: AndroidWorkflowReport) -> None:
        try:
            self.audit.log_event(
                event_type="ANDROID_WORKFLOW_REPORT",
                details={
                    "workflow_id": report.workflow_id,
                    "goal": report.goal,
                    "success": report.success,
                    "total_steps": report.total_steps,
                    "steps_executed": report.steps_executed,
                    "summary": report.summary,
                    "error_code": report.error_code,
                    "requires_confirmation": report.requires_confirmation,
                },
                status="SUCCESS" if report.success else "FAILED",
            )
        except Exception as e:
            logger.warning(f"Failed to log workflow audit: {e}")

    # -------------------------------------------------------------------------
    # Code Editing, Inspection & Build Repair (Step 6 Phase 3)
    # -------------------------------------------------------------------------

    def execute_code_edit(self, proposal: Any) -> Any:
        """Applies a validated code edit proposal via AndroidCodeRepairEngine."""
        return self.code_repair.apply_edit(proposal)

    def inspect_code(self, file_path: Optional[str] = None) -> Dict[str, Any]:
        """Inspects bounded code context within authorized project."""
        return self.code_repair.inspect_code_context(file_path)

    def execute_build_repair(
        self,
        user_goal: str = "Fix build errors",
        workflow_id: Optional[str] = None,
        start_time: Optional[float] = None,
        deterministic_patch_provider: Optional[Callable] = None,
        max_attempts: int = MAX_REPAIR_ATTEMPTS,
    ) -> AndroidWorkflowReport:
        """
        Executes bounded, self-healing build error repair loop:
        Build -> Parse Error -> Propose Repair -> Validate -> Apply -> Rebuild -> Verify/Rollback.
        """
        wf_id = workflow_id or f"android_repair_{uuid.uuid4().hex[:8]}"
        t0 = start_time or time.time()

        if self.safety.is_emergency_stop_active():
            report = AndroidWorkflowReport(
                workflow_id=wf_id,
                goal=user_goal,
                success=False,
                total_steps=0,
                steps_executed=0,
                summary="Workflow halted: EMERGENCY STOP is active.",
                error="EMERGENCY STOP is active. All Android operations are frozen.",
                error_code=AndroidErrorCode.EMERGENCY_STOPPED.value,
                duration_s=time.time() - t0,
            )
            self._audit_workflow(report)
            return report

        repair_res = self.code_repair.repair_build(
            user_goal=user_goal,
            max_attempts=max_attempts,
            deterministic_patch_provider=deterministic_patch_provider,
        )

        steps = []
        if repair_res.initial_error:
            steps.append({
                "stage": "DIAGNOSE_BUILD",
                "category": repair_res.initial_error.category.value,
                "message": repair_res.initial_error.message,
                "file": repair_res.initial_error.file_path,
                "line": repair_res.initial_error.line,
            })

        for edit in repair_res.applied_edits:
            steps.append({
                "stage": "CODE_EDIT",
                "file": edit.file_path,
                "success": edit.success,
                "diff": edit.diff,
                "lines_changed": edit.lines_changed,
            })

        if repair_res.rolled_back:
            steps.append({
                "stage": "ROLLBACK",
                "success": True,
                "files": [e.file_path for e in repair_res.applied_edits],
            })

        report = AndroidWorkflowReport(
            workflow_id=wf_id,
            goal=user_goal,
            success=repair_res.success,
            total_steps=max(1, len(steps)),
            steps_executed=len(steps),
            steps=steps,
            summary=repair_res.summary,
            error=repair_res.summary if not repair_res.success else None,
            error_code=repair_res.error_code,
            duration_s=time.time() - t0,
        )
        self._audit_workflow(report)
        return report

    def _execute_code_inspection_workflow(
        self,
        user_goal: str,
        workflow_id: str,
        start_time: float,
    ) -> AndroidWorkflowReport:
        """Executes bounded code inspection and returns structured workflow report."""
        res = self.inspect_code()
        steps = [{
            "stage": "INSPECT_CODE",
            "success": True,
            "data": res,
        }]
        report = AndroidWorkflowReport(
            workflow_id=workflow_id,
            goal=user_goal,
            success=True,
            total_steps=1,
            steps_executed=1,
            steps=steps,
            summary=f"Project code inspected: {res.get('total_allowed_files', 0)} allowed files identified.",
            duration_s=time.time() - start_time,
        )
        self._audit_workflow(report)
        return report

    def _execute_explain_error_workflow(
        self,
        user_goal: str,
        workflow_id: str,
        start_time: float,
    ) -> AndroidWorkflowReport:
        """Builds project, parses compiler error, and returns structured explanation."""
        build_res = self.tools.gradle.run_action("DEBUG_ASSEMBLE")
        raw_out = build_res.get("output_sample", "") or str(build_res.get("diagnosis", ""))
        parsed_err = self.code_repair.analyzer.analyze_build_output(raw_out, self.safety.authorized_project)

        advice = self.consult_model(f"Explain build error: {parsed_err.diagnosis}")
        steps = [{
            "stage": "EXPLAIN_ERROR",
            "success": True,
            "data": {
                "error": parsed_err.to_dict(),
                "advice": advice,
            },
        }]
        report = AndroidWorkflowReport(
            workflow_id=workflow_id,
            goal=user_goal,
            success=True,
            total_steps=1,
            steps_executed=1,
            steps=steps,
            summary=f"Build error explained: {parsed_err.category.value} - {parsed_err.diagnosis}",
            duration_s=time.time() - start_time,
        )
        self._audit_workflow(report)
        return report

    # -------------------------------------------------------------------------
    # Android UI Perception & Interaction Methods (Step 6 Phase 5)
    # -------------------------------------------------------------------------

    def inspect_screen(self, serial: str = "emulator-5554"):
        """Captures and returns current UI snapshot on authorized device."""
        if self.safety.is_emergency_stop_active():
            raise EmergencyStopActiveError()
        return self.ui_controller.inspect_ui(serial)

    def find_ui_target(self, query: str, serial: str = "emulator-5554") -> List[Dict[str, Any]]:
        """Finds UI targets matching query on authorized device."""
        if self.safety.is_emergency_stop_active():
            raise EmergencyStopActiveError()
        return self.ui_controller.find_ui_element(serial, query)

    def execute_ui_action(
        self,
        action: str,
        params: Optional[Dict[str, Any]] = None,
        serial: str = "emulator-5554",
    ) -> Dict[str, Any]:
        """Executes a bounded, safe UI action on authorized device."""
        if self.safety.is_emergency_stop_active():
            raise EmergencyStopActiveError()
        return self.ui_controller.execute_action(serial, action, params)

    def _execute_ui_inspect_workflow(
        self,
        user_goal: str,
        workflow_id: str,
        start_time: float,
        serial: str = "emulator-5554",
    ) -> AndroidWorkflowReport:
        if self.safety.is_emergency_stop_active():
            report = AndroidWorkflowReport(
                workflow_id=workflow_id,
                goal=user_goal,
                success=False,
                total_steps=0,
                steps_executed=0,
                summary="Workflow halted: EMERGENCY STOP is active.",
                error="EMERGENCY STOP is active. All Android operations are frozen.",
                error_code=AndroidErrorCode.EMERGENCY_STOPPED.value,
                duration_s=time.time() - start_time,
            )
            self._audit_workflow(report)
            return report

        try:
            snapshot = self.ui_controller.inspect_ui(serial)
            steps = [{
                "stage": "OBSERVATION",
                "success": True,
                "targets_count": len(snapshot.targets),
                "foreground_app": snapshot.foreground_app,
                "visible_texts": snapshot.visible_text_items[:20],
            }]
            summary = (
                f"Screen inspected on {serial}: {len(snapshot.targets)} targets discovered. "
                f"Foreground app: {snapshot.foreground_app.get('package')}/{snapshot.foreground_app.get('activity')}."
            )
            report = AndroidWorkflowReport(
                workflow_id=workflow_id,
                goal=user_goal,
                success=True,
                total_steps=1,
                steps_executed=1,
                steps=steps,
                summary=summary,
                duration_s=time.time() - start_time,
            )
        except AndroidSafetyError as se:
            report = AndroidWorkflowReport(
                workflow_id=workflow_id,
                goal=user_goal,
                success=False,
                total_steps=1,
                steps_executed=0,
                summary=f"Screen inspection failed: {se.message}",
                error=se.message,
                error_code=se.code.value,
                duration_s=time.time() - start_time,
            )
        self._audit_workflow(report)
        return report

    def _execute_ui_find_workflow(
        self,
        user_goal: str,
        workflow_id: str,
        start_time: float,
        serial: str = "emulator-5554",
    ) -> AndroidWorkflowReport:
        if self.safety.is_emergency_stop_active():
            report = AndroidWorkflowReport(
                workflow_id=workflow_id,
                goal=user_goal,
                success=False,
                total_steps=0,
                steps_executed=0,
                summary="Workflow halted: EMERGENCY STOP is active.",
                error="EMERGENCY STOP is active. All Android operations are frozen.",
                error_code=AndroidErrorCode.EMERGENCY_STOPPED.value,
                duration_s=time.time() - start_time,
            )
            self._audit_workflow(report)
            return report

        m = re.search(r"(?:find\s+(?:the\s+)?|locate\s+)(.+)", user_goal, re.IGNORECASE)
        query = m.group(1).strip() if m else user_goal

        try:
            matches = self.ui_controller.find_ui_element(serial, query)
            steps = [{
                "stage": "TARGET_DISCOVERY",
                "success": bool(matches),
                "query": query,
                "matches_count": len(matches),
                "matches": matches[:5],
            }]
            success = len(matches) > 0
            summary = f"Found {len(matches)} matching target(s) for '{query}' on {serial}." if success else f"No targets matching '{query}' found on {serial}."
            report = AndroidWorkflowReport(
                workflow_id=workflow_id,
                goal=user_goal,
                success=success,
                total_steps=1,
                steps_executed=1,
                steps=steps,
                summary=summary,
                error=None if success else f"Target '{query}' not found.",
                error_code=None if success else AndroidErrorCode.TARGET_NOT_FOUND.value,
                duration_s=time.time() - start_time,
            )
        except AndroidSafetyError as se:
            report = AndroidWorkflowReport(
                workflow_id=workflow_id,
                goal=user_goal,
                success=False,
                total_steps=1,
                steps_executed=0,
                summary=f"Target discovery failed: {se.message}",
                error=se.message,
                error_code=se.code.value,
                duration_s=time.time() - start_time,
            )
        self._audit_workflow(report)
        return report

    def _execute_ui_interaction_workflow(
        self,
        user_goal: str,
        workflow_id: str,
        start_time: float,
        serial: str = "emulator-5554",
    ) -> AndroidWorkflowReport:
        if self.safety.is_emergency_stop_active():
            report = AndroidWorkflowReport(
                workflow_id=workflow_id,
                goal=user_goal,
                success=False,
                total_steps=0,
                steps_executed=0,
                summary="Workflow halted: EMERGENCY STOP is active.",
                error="EMERGENCY STOP is active. All Android operations are frozen.",
                error_code=AndroidErrorCode.EMERGENCY_STOPPED.value,
                duration_s=time.time() - start_time,
            )
            self._audit_workflow(report)
            return report

        g_lower = user_goal.lower()
        steps: List[Dict[str, Any]] = []

        try:
            if "go back" in g_lower or "press back" in g_lower:
                res = self.ui_controller.back(serial)
                steps.append({"stage": "PROPOSED_ACTION", "action": "back"})
                steps.append({"stage": "EXECUTED_ACTION", "action": "back", "success": res.get("success", False)})
                success = res.get("success", False)
                summary = f"Pressed Back on {serial}."
            elif "press home" in g_lower:
                res = self.ui_controller.home(serial)
                steps.append({"stage": "PROPOSED_ACTION", "action": "home"})
                steps.append({"stage": "EXECUTED_ACTION", "action": "home", "success": res.get("success", False)})
                success = res.get("success", False)
                summary = f"Pressed Home on {serial}."
            elif "scroll down" in g_lower or "scroll" in g_lower or "swipe" in g_lower:
                direction = "up" if "scroll up" in g_lower else "down"
                res = self.ui_controller.scroll(serial, direction=direction)
                steps.append({"stage": "PROPOSED_ACTION", "action": "scroll", "direction": direction})
                steps.append({"stage": "EXECUTED_ACTION", "action": "scroll", "success": res.get("success", False)})
                success = res.get("success", False)
                summary = f"Scrolled {direction} on {serial}."
            elif "tap " in g_lower or "click " in g_lower:
                m = re.search(r"(?:tap|click)\s+(?:the\s+)?(.+)", user_goal, re.IGNORECASE)
                target_query = m.group(1).strip() if m else ""

                if target_query.startswith("android.target."):
                    target_id = target_query
                else:
                    matches = self.ui_controller.find_ui_element(serial, target_query)
                    if not matches:
                        raise AndroidSafetyError(
                            AndroidErrorCode.TARGET_NOT_FOUND,
                            f"Target '{target_query}' not found on screen.",
                        )
                    target_id = matches[0]["target_id"]

                steps.append({"stage": "PROPOSED_ACTION", "action": "tap_target", "target_id": target_id})
                res = self.ui_controller.tap_target(serial, target_id)
                steps.append({
                    "stage": "EXECUTED_ACTION",
                    "action": "tap_target",
                    "target_id": target_id,
                    "coordinates": res.get("coordinates"),
                    "success": res.get("success", False),
                })
                steps.append({
                    "stage": "VERIFIED_ACTION",
                    "foreground_app": res.get("foreground_app"),
                })
                success = res.get("success", False)
                summary = f"Tapped target '{target_id}' at {res.get('coordinates')} on {serial}."
            else:
                raise AndroidSafetyError(
                    AndroidErrorCode.ACTION_NOT_ALLOWED,
                    f"Unsupported UI interaction command: '{user_goal}'.",
                )

            report = AndroidWorkflowReport(
                workflow_id=workflow_id,
                goal=user_goal,
                success=success,
                total_steps=len(steps),
                steps_executed=len(steps),
                steps=steps,
                summary=summary,
                duration_s=time.time() - start_time,
            )
        except AndroidSafetyError as se:
            report = AndroidWorkflowReport(
                workflow_id=workflow_id,
                goal=user_goal,
                success=False,
                total_steps=max(1, len(steps)),
                steps_executed=len(steps),
                steps=steps,
                summary=f"UI interaction failed: {se.message}",
                error=se.message,
                error_code=se.code.value,
                duration_s=time.time() - start_time,
            )
        self._audit_workflow(report)
        return report

    def _execute_ui_verify_workflow(
        self,
        user_goal: str,
        workflow_id: str,
        start_time: float,
        serial: str = "emulator-5554",
    ) -> AndroidWorkflowReport:
        if self.safety.is_emergency_stop_active():
            report = AndroidWorkflowReport(
                workflow_id=workflow_id,
                goal=user_goal,
                success=False,
                total_steps=0,
                steps_executed=0,
                summary="Workflow halted: EMERGENCY STOP is active.",
                error="EMERGENCY STOP is active. All Android operations are frozen.",
                error_code=AndroidErrorCode.EMERGENCY_STOPPED.value,
                duration_s=time.time() - start_time,
            )
            self._audit_workflow(report)
            return report

        try:
            res = self.ui_controller.verify_ui_state(serial)
            steps = [{
                "stage": "VERIFIED_ACTION",
                "success": res.get("success", False),
                "foreground_app": res.get("foreground_app"),
                "targets_count": res.get("targets_count", 0),
            }]
            summary = f"UI state verified on {serial}: foreground app is {res.get('foreground_app', {}).get('package')}."
            report = AndroidWorkflowReport(
                workflow_id=workflow_id,
                goal=user_goal,
                success=res.get("success", False),
                total_steps=1,
                steps_executed=1,
                steps=steps,
                summary=summary,
                duration_s=time.time() - start_time,
            )
        except AndroidSafetyError as se:
            report = AndroidWorkflowReport(
                workflow_id=workflow_id,
                goal=user_goal,
                success=False,
                total_steps=1,
                steps_executed=0,
                summary=f"UI verification failed: {se.message}",
                error=se.message,
                error_code=se.code.value,
                duration_s=time.time() - start_time,
            )
        self._audit_workflow(report)
        return report

    def capture_runtime_logs(
        self,
        serial: str = "emulator-5554",
        lines: int = 100,
        severity: Optional[str] = None,
        package_name: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> str:
        """Captures bounded, filtered, and sanitized logcat entries on authorized device."""
        if self.safety.is_emergency_stop_active():
            raise EmergencyStopActiveError()
        return self.diagnostics_controller.capture_logs(
            serial=serial,
            lines=lines,
            severity=severity,
            package_name=package_name,
            tag=tag,
        )

    def get_diagnostic_snapshot(
        self,
        serial: str = "emulator-5554",
        package_name: str = AUTHORIZED_PACKAGE_NAME,
        lines: int = 200,
        severity: Optional[str] = None,
    ):
        """Creates a comprehensive, immutable DiagnosticSnapshot on authorized device."""
        if self.safety.is_emergency_stop_active():
            raise EmergencyStopActiveError()
        return self.diagnostics_controller.create_diagnostic_snapshot(
            serial=serial,
            package_name=package_name,
            lines=lines,
            severity=severity,
        )

    def diagnose_runtime_error(
        self,
        serial: str = "emulator-5554",
        package_name: str = AUTHORIZED_PACKAGE_NAME,
        lines: int = 300,
    ) -> Dict[str, Any]:
        """Performs a deterministic crash diagnosis session on authorized device."""
        if self.safety.is_emergency_stop_active():
            raise EmergencyStopActiveError()
        return self.diagnostics_controller.diagnose_crash(
            serial=serial,
            package_name=package_name,
            lines=lines,
        )

    def _execute_runtime_diagnosis_workflow(
        self,
        user_goal: str,
        workflow_id: str,
        start_time: float,
        serial: str = "emulator-5554",
    ) -> AndroidWorkflowReport:
        if self.safety.is_emergency_stop_active():
            report = AndroidWorkflowReport(
                workflow_id=workflow_id,
                goal=user_goal,
                success=False,
                total_steps=0,
                steps_executed=0,
                summary="Workflow halted: EMERGENCY STOP is active.",
                error="EMERGENCY STOP is active. All Android operations are frozen.",
                error_code=AndroidErrorCode.EMERGENCY_STOPPED.value,
                duration_s=time.time() - start_time,
            )
            self._audit_workflow(report)
            return report

        try:
            diag_res = self.diagnostics_controller.diagnose_crash(serial, AUTHORIZED_PACKAGE_NAME)
            steps = [{
                "stage": "DIAGNOSTIC_ANALYSIS",
                "success": True,
                "has_crash": diag_res.get("has_crash", False),
                "detected_errors_count": diag_res.get("detected_errors_count", 0),
                "summary": diag_res.get("summary"),
            }]
            summary = f"Runtime diagnostics on {serial}: {diag_res.get('summary')}"
            report = AndroidWorkflowReport(
                workflow_id=workflow_id,
                goal=user_goal,
                success=True,
                total_steps=1,
                steps_executed=1,
                steps=steps,
                summary=summary,
                duration_s=time.time() - start_time,
            )
        except AndroidSafetyError as se:
            report = AndroidWorkflowReport(
                workflow_id=workflow_id,
                goal=user_goal,
                success=False,
                total_steps=1,
                steps_executed=0,
                summary=f"Runtime diagnosis failed: {se.message}",
                error=se.message,
                error_code=se.code.value,
                duration_s=time.time() - start_time,
            )
        self._audit_workflow(report)
        return report

    def _execute_logcat_inspection_workflow(
        self,
        user_goal: str,
        workflow_id: str,
        start_time: float,
        serial: str = "emulator-5554",
    ) -> AndroidWorkflowReport:
        if self.safety.is_emergency_stop_active():
            report = AndroidWorkflowReport(
                workflow_id=workflow_id,
                goal=user_goal,
                success=False,
                total_steps=0,
                steps_executed=0,
                summary="Workflow halted: EMERGENCY STOP is active.",
                error="EMERGENCY STOP is active. All Android operations are frozen.",
                error_code=AndroidErrorCode.EMERGENCY_STOPPED.value,
                duration_s=time.time() - start_time,
            )
            self._audit_workflow(report)
            return report

        try:
            logs = self.diagnostics_controller.capture_logs(serial, lines=100, package_name=AUTHORIZED_PACKAGE_NAME)
            line_count = len(logs.splitlines())
            steps = [{
                "stage": "LOGCAT_INSPECTION",
                "success": True,
                "lines_captured": line_count,
            }]
            summary = f"Captured {line_count} logcat lines from {serial} for {AUTHORIZED_PACKAGE_NAME}."
            report = AndroidWorkflowReport(
                workflow_id=workflow_id,
                goal=user_goal,
                success=True,
                total_steps=1,
                steps_executed=1,
                steps=steps,
                summary=summary,
                duration_s=time.time() - start_time,
            )
        except AndroidSafetyError as se:
            report = AndroidWorkflowReport(
                workflow_id=workflow_id,
                goal=user_goal,
                success=False,
                total_steps=1,
                steps_executed=0,
                summary=f"Logcat inspection failed: {se.message}",
                error=se.message,
                error_code=se.code.value,
                duration_s=time.time() - start_time,
            )
        self._audit_workflow(report)
        return report
