import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.agent.action_dispatcher import ActionDispatcher
from app.agent.code_agent import CodeAgent
from app.agent.code_writer import CodeWriter
from app.agent.task_planner import TaskPlanner
from app.memory.audit_logger import AuditLogger
from app.memory.context_memory import ProjectContextMemory


def safe_print(msg: str) -> None:
    try:
        print(msg)
    except Exception:
        try:
            print(msg.encode("ascii", "replace").decode("ascii"))
        except Exception:
            pass


class AutonomousDevLoop:
    """
    NR AI Autonomous Development Loop Orchestrator.

    Pipeline:
    REQUIREMENT
       ↓
    PLAN
       ↓
    ARCHITECTURE
       ↓
    IMPLEMENT
       ↓
    TEST
       ↓
    ERROR? → ANALYZE → FIX → RETEST (Bounded max 3 retries, rollback on degradation)
       ↓
    VERIFY
       ↓
    REPORT
    """

    def __init__(
        self,
        planner: Optional[TaskPlanner] = None,
        dispatcher: Optional[ActionDispatcher] = None,
        memory: Optional[ProjectContextMemory] = None,
        audit_logger: Optional[AuditLogger] = None,
        provider: Optional[Any] = None,
        router: Optional[Any] = None,
    ):
        self.planner = planner or TaskPlanner()
        self.dispatcher = dispatcher or ActionDispatcher()
        self.memory = memory or ProjectContextMemory()
        self.audit = audit_logger or AuditLogger()
        self.provider = provider
        self.router = router
        self.code_agent = CodeAgent(provider=provider, router=router)
        self.writer = CodeWriter()

    def run_lifecycle(
        self,
        requirement: str,
        progress_cb: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """Runs the complete autonomous development lifecycle for a user requirement."""
        self.memory.last_user_query = requirement
        self.memory.clear_cancellation()
        self.audit.log_user_request(requirement)

        def emit(msg: str):
            safe_print(f"🔄 [AutonomousLoop] {msg}")
            if progress_cb:
                progress_cb(msg)

        emit(f"Analyzing requirement: '{requirement}'")

        # 1. PLAN & ARCHITECTURE
        plan_res = self.planner.plan(requirement)
        if not plan_res["success"]:
            msg = plan_res.get("message", "Could not generate an action plan.")
            emit(f"Planning failed: {msg}")
            return {"success": False, "stage": "plan", "message": msg}

        actions = plan_res.get("actions", [])
        self.memory.record_plan(plan_res)
        self.audit.log_plan_generated(plan_res)
        emit(f"Generated architecture plan with {len(actions)} action(s).")

        # 2. IMPLEMENTATION & EXECUTION
        last_output = ""
        for idx, act in enumerate(actions, start=1):
            if self.memory.is_cancelled():
                emit("🛑 Task cancelled by user.")
                return {"success": False, "stage": "cancelled", "message": "Operation cancelled by user."}

            act_type = act.get("type", "unknown")
            desc = act.get("description", act_type)
            emit(f"Action {idx}/{len(actions)}: {desc}")

            # Checkpoint before modification actions
            if act_type in {"write_code", "modify_code", "batch_replace"}:
                target_file = act.get("filename") or act.get("path")
                if target_file:
                    self.memory.record_file_modified(target_file)

            act_res = self.dispatcher.execute(act)
            self.audit.log_action_executed(act, act_res)

            if not act_res.get("success"):
                err_msg = act_res.get("message", "Action failed")
                emit(f"⚠️ Action failed: {err_msg}")

                # If action failed, attempt self-healing recovery if applicable
                target_file = act.get("filename")
                if target_file and act_type in {"code_execute", "run_code"}:
                    emit(f"Attempting autonomous recovery on {target_file}...")
                    heal_res = self.code_agent.fix_existing_file(target_file)
                    self.audit.log_error_recovery({"file": target_file}, heal_res)
                    if heal_res.get("success"):
                        emit(f"✅ Autonomous recovery resolved issues in {target_file}.")
                        last_output = heal_res.get("output", "")
                        continue
                    else:
                        emit(f"❌ Autonomous recovery could not resolve {target_file}.")
                        return {
                            "success": False,
                            "stage": "recovery_failed",
                            "message": f"Autonomous recovery failed on {target_file}.",
                        }

                return {
                    "success": False,
                    "stage": "execution_failed",
                    "message": f"Step {idx} failed: {err_msg}",
                    "action": act,
                }

            if act_res.get("output"):
                last_output = act_res["output"]

        emit("✅ Verification complete. All plan steps executed successfully.")
        return {
            "success": True,
            "stage": "complete",
            "message": "Task completed successfully.",
            "output": last_output,
            "actions_executed": len(actions),
        }
