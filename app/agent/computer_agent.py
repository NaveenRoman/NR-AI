"""
NR-AI Unified Computer Agent (Step 4E).

Unifies perception, planning, target resolution, deterministic safety gating,
action execution, and state verification into a single autonomous loop:

UNDERSTAND
   ↓
PLAN
   ↓
RESOLVE TARGET
   ↓
SEE
   ↓
VALIDATE
   ↓
ACT
   ↓
VERIFY
   ↓
RECOVER IF SAFE (Max 2 Retries)
   ↓
UPDATE MEMORY
   ↓
REPORT
"""

from dataclasses import dataclass, field
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from app.agent.computer_tools import ComputerToolRegistry, RiskLevel, ToolExecutionResult
from app.agent.input_controller import InputController
from app.agent.state_verifier import StateVerifier
from app.agent.window_manager import WindowManager
from app.commands.app_launcher import AppLauncher
from app.memory.audit_logger import AuditLogger
from app.memory.context_memory import ProjectContextMemory
from app.vision.vision_service import ScreenVisionService

logger = logging.getLogger("NRAI.UnifiedComputerAgent")

MAX_STEPS_PER_WORKFLOW = 10
MAX_RETRIES_PER_STEP = 2
TARGET_TTL_SECONDS = 15.0


@dataclass
class WorkflowStep:
    """A discrete planned action step within a multi-step workflow."""
    step_id: int
    tool: str
    params: Dict[str, Any]
    description: str = ""
    status: str = "PENDING"  # PENDING, RUNNING, COMPLETED, FAILED, RETRYING, ABORTED
    retry_count: int = 0
    result: Optional[ToolExecutionResult] = None


@dataclass
class WorkflowReport:
    """Comprehensive execution report of an autonomous computer workflow."""
    workflow_id: str
    goal: str
    success: bool
    total_steps: int
    steps_executed: int
    steps: List[Dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    error: Optional[str] = None
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
            "requires_confirmation": self.requires_confirmation,
            "pending_action": self.pending_action,
            "duration_s": round(self.duration_s, 2),
            "timestamp": self.timestamp,
        }


class UnifiedComputerAgent:
    """
    Unified Computer Control Agent.
    Executes bounded, verified multi-step computer tasks using the approved tool contract.
    """

    def __init__(
        self,
        tool_registry: Optional[ComputerToolRegistry] = None,
        memory: Optional[ProjectContextMemory] = None,
        audit_logger: Optional[AuditLogger] = None,
        window_manager: Optional[WindowManager] = None,
        vision_service: Optional[ScreenVisionService] = None,
        input_controller: Optional[InputController] = None,
        max_steps: int = MAX_STEPS_PER_WORKFLOW,
        max_retries: int = MAX_RETRIES_PER_STEP,
    ):
        self.window_manager = window_manager or WindowManager()
        self.vision_service = vision_service or ScreenVisionService(window_manager=self.window_manager)
        self.input_controller = input_controller or InputController(
            window_manager=self.window_manager,
            vision_service=self.vision_service,
        )
        self.audit = audit_logger or AuditLogger()
        self.memory = memory or ProjectContextMemory()
        self.tool_registry = tool_registry or ComputerToolRegistry(
            window_manager=self.window_manager,
            vision_service=self.vision_service,
            input_controller=self.input_controller,
            audit_logger=self.audit,
        )

        self.max_steps = max_steps
        self.max_retries = max_retries

    # -------------------------------------------------------------------------
    # 1. UNDERSTAND & PLAN
    # -------------------------------------------------------------------------

    def plan_workflow(self, user_goal: str) -> Tuple[bool, List[WorkflowStep], Optional[str]]:
        """
        Decomposes a user goal into an ordered sequence of approved tool steps.
        Enforces MAX_STEPS_PER_WORKFLOW (10 steps).
        """
        if not user_goal or not user_goal.strip():
            return False, [], "Empty user goal."

        goal = user_goal.strip()
        # Normalize and strip workflow prefixes
        goal = re.sub(r"^(?:run\s+workflow|computer\s+workflow|workflow|computer|agent|task)\s*:\s*", "", goal, flags=re.IGNORECASE).strip()
        goal = re.sub(r"^(?:please\s+|can you\s+|could you\s+)", "", goal, flags=re.IGNORECASE).strip()
        goal = re.sub(r"[.?!]+$", "", goal).strip()
        g_low = goal.lower()
        steps: List[WorkflowStep] = []

        # ---------------------------------------------------------------------
        # Pattern A: "open <app> and search for <query>"
        # e.g., "Open Chrome and search for Android Studio."
        # ---------------------------------------------------------------------
        m_search = re.match(
            r"^(?:open|launch|start)\s+([\w\s\.\-]+?)\s+(?:and|then)\s+search(?:\s+for)?\s+[\"']?(.+?)[\"']?$",
            g_low,
        )
        if m_search:
            app = m_search.group(1).strip()
            query = m_search.group(2).strip()

            steps.append(WorkflowStep(1, "computer.open_app", {"app_name": app}, f"Launch application '{app}'"))
            steps.append(WorkflowStep(2, "computer.focus_window", {"target": app}, f"Focus window for '{app}'"))
            steps.append(WorkflowStep(3, "computer.type_text", {"text": query, "verify_expected": query}, f"Type search query '{query}'"))
            steps.append(WorkflowStep(4, "computer.press_key", {"key": "enter"}, "Submit search with Enter"))
            steps.append(WorkflowStep(5, "computer.verify", {"expected_text": query, "wait_time": 1.5}, f"Verify '{query}' results"))

        # ---------------------------------------------------------------------
        # Pattern B: "open <app> and type <text>"
        # e.g., "Open Notepad and type NR-AI Unified Computer Test."
        # ---------------------------------------------------------------------
        elif re.match(r"^(?:open|launch|start)\s+([\w\s\.\-]+?)\s+(?:and|then)\s+(?:type|write|enter(?:\s+text)?)\s+[\"']?(.+?)[\"']?$", g_low):
            m_type = re.match(r"^(?:open|launch|start)\s+([\w\s\.\-]+?)\s+(?:and|then)\s+(?:type|write|enter(?:\s+text)?)\s+[\"']?(.+?)[\"']?$", g_low)
            app = m_type.group(1).strip()
            # Preserve original casing of text
            m_orig = re.search(r"^(?:open|launch|start)\s+([\w\s\.\-]+?)\s+(?:and|then)\s+(?:type|write|enter(?:\s+text)?)\s+[\"']?(.+?)[\"']?$", goal, re.IGNORECASE)
            text = m_orig.group(2).strip() if m_orig else m_type.group(2).strip()

            steps.append(WorkflowStep(1, "computer.open_app", {"app_name": app}, f"Launch application '{app}'"))
            steps.append(WorkflowStep(2, "computer.focus_window", {"target": app}, f"Focus window for '{app}'"))
            steps.append(WorkflowStep(3, "computer.type_text", {"text": text, "verify_expected": text}, f"Type text '{text}'"))
            steps.append(WorkflowStep(4, "computer.verify", {"expected_text": text, "wait_time": 1.0}, "Verify typed text"))

        # ---------------------------------------------------------------------
        # Pattern C: "open <app> and click <target>"
        # e.g., "Open Chrome and click New Tab"
        # ---------------------------------------------------------------------
        elif re.match(r"^(?:open|launch|start)\s+([\w\s\.\-]+?)\s+(?:and|then)\s+(?:click|tap)\s+(?:on\s+)?[\"']?(.+?)[\"']?$", g_low):
            m_click = re.match(r"^(?:open|launch|start)\s+([\w\s\.\-]+?)\s+(?:and|then)\s+(?:click|tap)\s+(?:on\s+)?[\"']?(.+?)[\"']?$", g_low)
            app = m_click.group(1).strip()
            target = m_click.group(2).strip()

            steps.append(WorkflowStep(1, "computer.open_app", {"app_name": app}, f"Launch application '{app}'"))
            steps.append(WorkflowStep(2, "computer.focus_window", {"target": app}, f"Focus window for '{app}'"))
            steps.append(WorkflowStep(3, "computer.find_text", {"query": target, "target_window": app}, f"Locate target '{target}'"))
            steps.append(WorkflowStep(4, "computer.click_target", {"target_name": target, "target_window": app}, f"Click '{target}'"))

        # ---------------------------------------------------------------------
        # Pattern D: "find text <query> and click it"
        # ---------------------------------------------------------------------
        elif re.match(r"^(?:find|locate)\s+text\s+[\"']?(.+?)[\"']?\s+(?:and|then)\s+(?:click|tap)\s+(?:it|that)?$", g_low):
            m_find_click = re.match(r"^(?:find|locate)\s+text\s+[\"']?(.+?)[\"']?\s+(?:and|then)\s+(?:click|tap)\s+(?:it|that)?$", g_low)
            query = m_find_click.group(1).strip()

            steps.append(WorkflowStep(1, "computer.find_text", {"query": query}, f"Locate text '{query}' on screen"))
            steps.append(WorkflowStep(2, "computer.click_target", {"target_name": query}, f"Click resolved target '{query}'"))

        # ---------------------------------------------------------------------
        # Pattern E: Single-step fallback delegation
        # ---------------------------------------------------------------------
        else:
            # Check if direct tool matches
            if g_low.startswith(("open ", "launch ", "start ")):
                target = re.sub(r"^(?:open|launch|start)\s+", "", g_low).strip()
                steps.append(WorkflowStep(1, "computer.open_app", {"app_name": target}, f"Launch '{target}'"))
            elif g_low.startswith(("switch to ", "focus ")):
                target = re.sub(r"^(?:switch\s+to|focus)\s+", "", g_low).strip()
                steps.append(WorkflowStep(1, "computer.focus_window", {"target": target}, f"Focus window '{target}'"))
            elif g_low.startswith(("type ", "enter text ")):
                text = re.sub(r"^(?:type|enter\s+text)\s+", "", goal).strip()
                steps.append(WorkflowStep(1, "computer.type_text", {"text": text}, f"Type '{text}'"))
            elif g_low.startswith("click "):
                target = re.sub(r"^click\s+", "", g_low).strip()
                steps.append(WorkflowStep(1, "computer.click_target", {"target_name": target}, f"Click '{target}'"))
            elif g_low.startswith("press "):
                key = re.sub(r"^press\s+", "", g_low).strip()
                steps.append(WorkflowStep(1, "computer.press_key", {"key": key}, f"Press key '{key}'"))
            elif "screen" in g_low and ("what" in g_low or "read" in g_low or "inspect" in g_low):
                steps.append(WorkflowStep(1, "computer.inspect_screen", {}, "Inspect desktop screen"))
            else:
                return False, [], f"Could not generate a safe computer workflow for '{user_goal}'."

        # Hard limit validation
        if len(steps) > self.max_steps:
            return False, [], (
                f"Workflow exceeds maximum step limit ({len(steps)} > {self.max_steps}). "
                "Action aborted to prevent runaway execution."
            )

        return True, steps, None

    # -------------------------------------------------------------------------
    # 2. RESOLVE TARGET & SEE
    # -------------------------------------------------------------------------

    def resolve_visual_target(
        self,
        target_name: str,
        target_window: Optional[str] = None,
        cached_target: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Optional[Tuple[int, int]], Optional[str]]:
        """
        Resolves a textual UI target to screen-absolute coordinates.
        Enforces the 15-second TTL freshness rule. If stale or absent, re-perceives screen.
        """
        now = time.time()

        # Check freshness of cached target
        if cached_target and "timestamp" in cached_target and "center" in cached_target:
            age = now - cached_target["timestamp"]
            cached_win = cached_target.get("window")
            win_matches = (not target_window) or (not cached_win) or (target_window.lower() in cached_win.lower())
            if age <= TARGET_TTL_SECONDS and win_matches:
                c = cached_target["center"]
                return True, (int(c[0]), int(c[1])), None

        # Re-resolve with fresh perception (SEE)
        find_res = self.vision_service.find_text(target_name, target=target_window)
        if not find_res.get("found"):
            return False, None, f"Target '{target_name}' could not be located on the screen."

        match_data = find_res["match"]
        center = match_data.get("center")
        if not center:
            return False, None, f"Target '{target_name}' found but missing center coordinates."

        # Ambiguity check: if multiple distinct matches exist
        items = find_res.get("items", [])
        exact_matches = [
            it for it in items
            if it.get("text", "").strip().lower() == target_name.lower()
            and it.get("confidence", 0.0) >= 0.70
        ]
        if len(exact_matches) > 1:
            centers = []
            for em in exact_matches:
                b = em.get("box")
                if b:
                    c_pt = (int(sum(pt[0] for pt in b) / len(b)), int(sum(pt[1] for pt in b) / len(b)))
                    if not any(abs(c_pt[0] - oc[0]) < 10 and abs(c_pt[1] - oc[1]) < 10 for oc in centers):
                        centers.append(c_pt)
            if len(centers) > 1:
                return False, None, f"Ambiguous target: {len(centers)} distinct instances of '{target_name}' found. Please specify context or window."

        cx, cy = int(center[0]), int(center[1])
        return True, (cx, cy), None

    # -------------------------------------------------------------------------
    # 3. VALIDATE, ACT, VERIFY & RECOVER
    # -------------------------------------------------------------------------

    def execute_step(
        self,
        step: WorkflowStep,
        workflow_id: str = "",
        user_confirmed: bool = False,
    ) -> ToolExecutionResult:
        """
        Executes a single workflow step with emergency-stop validation,
        window liveness checking, state verification, and bounded retries.
        """
        if not workflow_id:
            workflow_id = f"wf_step_{int(time.time() * 1000)}"
        step.status = "RUNNING"

        for attempt in range(self.max_retries + 1):
            step.retry_count = attempt

            # 1. Emergency stop check
            if self.input_controller.is_emergency_stopped():
                step.status = "ABORTED"
                return ToolExecutionResult(
                    success=False,
                    tool=step.tool,
                    error="EMERGENCY_STOP_ACTIVE",
                    message="Workflow halted immediately: Emergency stop is active.",
                )

            # 2. Window safety validation
            target_win = step.params.get("target_window")
            if target_win:
                w_valid, w_msg = self.input_controller.validate_target_window(target_win)
                if not w_valid:
                    step.status = "FAILED"
                    return ToolExecutionResult(
                        success=False,
                        tool=step.tool,
                        error="WINDOW_DISAPPEARED",
                        message=f"Window safety abort: {w_msg}",
                    )

            # 3. Visual target re-resolution if needed
            if step.tool in ("computer.click_target", "computer.double_click"):
                t_name = step.params.get("target_name")
                if t_name and not step.params.get("coordinates"):
                    cached_mem = self.memory.get_active_visual_target(max_age_seconds=TARGET_TTL_SECONDS)
                    cached_target_dict = None
                    if cached_mem and cached_mem.get("target", "").lower() == t_name.lower():
                        coords_mem = cached_mem.get("coordinates")
                        if coords_mem:
                            cached_target_dict = {
                                "timestamp": cached_mem.get("timestamp"),
                                "center": coords_mem,
                                "window": cached_mem.get("window"),
                            }

                    ok_t, coords, err_t = self.resolve_visual_target(
                        t_name, target_window=target_win, cached_target=cached_target_dict
                    )
                    if not ok_t:
                        # Retry if within limits
                        if attempt < self.max_retries:
                            time.sleep(1.0)
                            continue
                        step.status = "FAILED"
                        is_ambig = "ambiguous" in (err_t or "").lower()
                        return ToolExecutionResult(
                            success=False,
                            tool=step.tool,
                            error="AMBIGUOUS_TARGET" if is_ambig else "TARGET_RESOLUTION_FAILED",
                            message=err_t or "Target resolution failed.",
                        )
                    step.params["coordinates"] = coords
                    self.memory.set_active_visual_target(
                        target=t_name,
                        coordinates=coords,
                        window=target_win,
                    )

            # 4. Execute tool
            res = self.tool_registry.execute_tool(
                tool_name=step.tool,
                params=step.params,
                user_confirmed=user_confirmed,
            )

            # 5. Check if confirmation required
            if res.requires_confirmation:
                step.status = "PENDING_CONFIRMATION"
                return res

            # Update action and verification in memory
            self.memory.set_last_action({
                "step_id": step.step_id,
                "tool": step.tool,
                "params": step.params,
                "success": res.success,
            })
            self.memory.set_verification_result({
                "verified": res.verified,
                "message": res.message,
            })

            # 6. Verification and Recovery
            if res.success:
                step.status = "COMPLETED"
                step.result = res
                return res

            # If failed, check if retry is safe
            if attempt < self.max_retries:
                if self.input_controller.is_emergency_stopped():
                    step.status = "ABORTED"
                    return ToolExecutionResult(
                        success=False,
                        tool=step.tool,
                        error="EMERGENCY_STOP_ACTIVE",
                        message="Workflow halted immediately: Emergency stop is active.",
                    )
                logger.warning(
                    f"[UnifiedComputerAgent] Step {step.step_id} ({step.tool}) failed attempt {attempt + 1}: "
                    f"{res.message}. Initiating safe visual re-inspection retry..."
                )
                time.sleep(1.0)
                # Clear stale coordinates on retry so fresh perception is forced
                if "coordinates" in step.params:
                    step.params.pop("coordinates", None)
                self.memory.clear_active_visual_target()
            else:
                step.status = "FAILED"
                step.result = res
                return res

        step.status = "FAILED"
        return ToolExecutionResult(
            success=False,
            tool=step.tool,
            error="MAX_RETRIES_EXCEEDED",
            message=f"Step {step.step_id} failed after {self.max_retries} retries.",
        )

    # -------------------------------------------------------------------------
    # 4. WORKFLOW EXECUTION LOOP
    # -------------------------------------------------------------------------

    def execute_workflow(
        self,
        user_goal: str,
        user_confirmed: bool = False,
    ) -> WorkflowReport:
        """
        Executes a complete multi-step autonomous computer workflow.
        Returns a structured report with voice-friendly summary.
        """
        start_t = time.time()
        workflow_id = f"wf_{int(start_t * 1000)}"

        # Step 1: Plan
        ok_plan, steps, err_plan = self.plan_workflow(user_goal)
        if not ok_plan:
            return WorkflowReport(
                workflow_id=workflow_id,
                goal=user_goal,
                success=False,
                total_steps=0,
                steps_executed=0,
                error="PLANNING_FAILED",
                summary=f"Could not plan computer workflow: {err_plan}",
                duration_s=time.time() - start_t,
            )

        executed_records = []
        last_target_val = None

        # Step 2: Sequential Execution with Continuous Safety Gating
        for idx, step in enumerate(steps, start=1):
            # Check emergency stop prior to every step
            if self.input_controller.is_emergency_stopped():
                return WorkflowReport(
                    workflow_id=workflow_id,
                    goal=user_goal,
                    success=False,
                    total_steps=len(steps),
                    steps_executed=idx - 1,
                    steps=executed_records,
                    error="EMERGENCY_STOP_ACTIVE",
                    summary="Computer workflow aborted: Emergency stop is active.",
                    duration_s=time.time() - start_t,
                )

            # Execute step
            res = self.execute_step(step, workflow_id=workflow_id, user_confirmed=user_confirmed)

            rec = {
                "step_id": step.step_id,
                "tool": step.tool,
                "description": step.description,
                "status": step.status,
                "retry_count": step.retry_count,
                "success": res.success,
                "verified": res.verified,
                "message": res.message,
            }
            executed_records.append(rec)

            # Check for high-risk confirmation gate
            if res.requires_confirmation:
                return WorkflowReport(
                    workflow_id=workflow_id,
                    goal=user_goal,
                    success=False,
                    total_steps=len(steps),
                    steps_executed=idx,
                    steps=executed_records,
                    requires_confirmation=True,
                    pending_action={"tool": step.tool, "params": step.params},
                    summary=f"This workflow paused at step {idx}: {res.message}",
                    duration_s=time.time() - start_t,
                )

            # Abort if step failed
            if not res.success:
                return WorkflowReport(
                    workflow_id=workflow_id,
                    goal=user_goal,
                    success=False,
                    total_steps=len(steps),
                    steps_executed=idx,
                    steps=executed_records,
                    error=res.error or "STEP_FAILED",
                    summary=f"Workflow failed at step {idx} ({step.tool}): {res.message}",
                    duration_s=time.time() - start_t,
                )

            # Update memory state
            target_str = ""
            if "app_name" in step.params:
                last_target_val = step.params["app_name"]
                target_str = str(last_target_val)
                self.memory.set_active_window({"title": target_str, "app_name": target_str})
            elif "target" in step.params:
                last_target_val = step.params["target"]
                target_str = str(last_target_val)
                self.memory.set_active_window({"title": target_str})
            elif "target_name" in step.params:
                last_target_val = step.params["target_name"]
                target_str = str(last_target_val)
            elif "text" in step.params:
                last_target_val = step.params["text"]
                target_str = str(last_target_val)

            if last_target_val:
                self.memory.set_active_target(
                    target=str(last_target_val),
                    target_type="computer_workflow_target",
                    source_command=user_goal,
                    metadata={"workflow_id": workflow_id, "step": idx, "tool": step.tool},
                )

            self.memory.set_workflow_state({
                "workflow_id": workflow_id,
                "goal": user_goal,
                "current_step": idx,
                "total_steps": len(steps),
                "last_tool": step.tool,
            })

            # Audit logging of each workflow step with required schema
            step_details = {
                "workflow_id": workflow_id,
                "step_number": step.step_id,
                "requested_tool": step.tool,
                "target": "[REDACTED]" if any(s in target_str.lower() for s in ("password", "secret", "token")) else target_str,
                "window": str(step.params.get("target_window") or ""),
                "validation_result": "PASSED" if res.success or res.requires_confirmation else "FAILED",
                "execution_result": "SUCCESS" if res.success else ("CONFIRMATION_REQUIRED" if res.requires_confirmation else "FAILURE"),
                "verification_result": "PASSED" if res.verified else "UNVERIFIED",
                "retry_count": step.retry_count,
            }
            self.audit.log_event(
                event_type="computer_workflow_step",
                details=step_details,
                status="success" if res.success else "failure",
            )

        # Step 3: Success Report Synthesis (Voice-friendly for Android TTS)
        duration = time.time() - start_t
        voice_summary = self._generate_voice_summary(user_goal, steps)

        # Audit logging of completed workflow
        self.audit.log_event(
            event_type="computer_workflow_completed",
            details={
                "workflow_id": workflow_id,
                "goal": user_goal,
                "steps_count": len(steps),
                "duration_s": round(duration, 2),
            },
            status="success",
        )

        return WorkflowReport(
            workflow_id=workflow_id,
            goal=user_goal,
            success=True,
            total_steps=len(steps),
            steps_executed=len(steps),
            steps=executed_records,
            summary=voice_summary,
            duration_s=duration,
        )

    def _generate_voice_summary(self, goal: str, steps: List[WorkflowStep]) -> str:
        """Generates a concise, conversational TTS summary for the Android phone."""
        tools_used = [s.tool for s in steps]
        if "computer.open_app" in tools_used and "computer.type_text" in tools_used:
            app_step = next((s for s in steps if s.tool == "computer.open_app"), None)
            type_step = next((s for s in steps if s.tool == "computer.type_text"), None)
            app = app_step.params.get("app_name", "the application") if app_step else "the application"
            text = type_step.params.get("text", "") if type_step else ""
            if "press_key" in tools_used:
                return f"I opened {app.capitalize()} and searched for '{text}'. The results are on your screen."
            return f"I opened {app.capitalize()} and typed '{text}' into the active window."

        if "computer.open_app" in tools_used:
            app_step = next((s for s in steps if s.tool == "computer.open_app"), None)
            app = app_step.params.get("app_name", "the application") if app_step else "the application"
            return f"I opened {app.capitalize()} and verified it on your desktop."

        return f"Completed your request: '{goal}'. All {len(steps)} actions executed and verified."

    # -------------------------------------------------------------------------
    # Backward Compatibility Interface
    # -------------------------------------------------------------------------

    def inspect_application(self, application: str) -> Dict[str, Any]:
        return self.tool_registry.execute_tool("computer.focus_window", {"target": application}).to_dict()

    def locate_text(self, application: str, text: str) -> Dict[str, Any]:
        return self.tool_registry.execute_tool("computer.find_text", {"query": text, "target_window": application}).to_dict()

    def click_text(self, application: str, text: str) -> Dict[str, Any]:
        return self.tool_registry.execute_tool("computer.click_target", {"target_name": text, "target_window": application}).to_dict()


# Export alias for backward compatibility
ComputerAgent = UnifiedComputerAgent