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
