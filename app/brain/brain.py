import re
import sys
from typing import Any, Dict, Optional

from app.agent.action_dispatcher import ActionDispatcher
from app.agent.autonomous_loop import AutonomousDevLoop
from app.agent.service_supervisor import ServiceSupervisor
from app.agent.task_planner import TaskPlanner
from app.commands.app_launcher import AppLauncher
from app.commands.system_command import SystemCommand
from app.commands.time_command import TimeCommand
from app.memory.audit_logger import AuditLogger
from app.memory.context_memory import ProjectContextMemory

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def safe_print(msg: str) -> None:
    try:
        print(msg)
    except Exception:
        try:
            print(msg.encode("ascii", "replace").decode("ascii"))
        except Exception:
            pass


class NRBrain:
    """
    NR AI Unified Command Router, Cognitive Hub & Product Supervisor.

    Receives voice or typed inputs, coordinates context memory,
    routes to specialized subsystems (Time, System, AppLauncher, Planner, DevLoop),
    manages long-running services, and maintains persistent audit logs.
    """

    def __init__(self, workspace: Optional[str] = None):
        self.name = "NR AI"

        self.memory = ProjectContextMemory(workspace=workspace)
        self.audit = AuditLogger()
        self.supervisor = ServiceSupervisor(workspace=workspace)

        self.time_command = TimeCommand()
        self.system_command = SystemCommand()
        self.app_launcher = AppLauncher()
        self.planner = TaskPlanner(memory=self.memory)
        self.dispatcher = ActionDispatcher(memory=self.memory)
        self.autonomous_loop = AutonomousDevLoop(
            planner=self.planner,
            dispatcher=self.dispatcher,
            memory=self.memory,
            audit_logger=self.audit,
        )

    def think(self, text: str) -> str:
        if not text:
            return "I didn't hear anything."

        command = text.lower().strip()
        self.memory.last_user_query = text
        self.audit.log_user_request(text)

        # -----------------------------
        # Conversation
        # -----------------------------
        if re.match(r"^(?:hello|hi|hey)(?:\s+(?:nr\s*ai|there|assistant|bot))?$", command):
            return "Hello. I am NR AI. I am ready to assist you."

        if "who are you" in command or "your name" in command:
            return "I am NR AI, your personal AI software and computer agent."

        if "what can you do" in command:
            return (
                "I can understand voice and text commands, autonomously architect and build "
                "full-stack software, diagnose and heal errors with automated checkpoints, "
                "manage background services, and control computer interfaces visually."
            )

        # -----------------------------
        # Status / Context Query
        # -----------------------------
        if command in {"status", "project status", "show status", "session status", "what are you working on"}:
            summary = self.memory.get_summary()
            return (
                f"Active project: {summary['active_project']}. "
                f"Modified files: {summary['recent_files_count']}. "
                f"Rollback checkpoints available: {summary['rollback_checkpoints']}."
            )

        # -----------------------------
        # Exit
        # -----------------------------
        if any(
            word == command or f" {word}" in command or f"{word} " in command
            for word in ["goodbye", "bye", "shutdown", "go offline"]
        ):
            self.supervisor.stop_all()
            return "Goodbye. NR AI is going offline."

        # -----------------------------
        # Time / Date
        # -----------------------------
        response = self.time_command.execute(command)
        if response:
            return response

        # -----------------------------
        # System Information
        # -----------------------------
        response = self.system_command.execute(command)
        if response:
            return response

        # -----------------------------
        # Application Launcher
        # -----------------------------
        response = self.app_launcher.execute(command)
        if response:
            return response

        # -----------------------------
        # Task Planner & Autonomous Execution
        # -----------------------------
        plan_res = self.planner.plan(text)
        if plan_res["success"]:
            self.memory.record_plan(plan_res)
            self.audit.log_plan_generated(plan_res)

            actions = plan_res["actions"]
            safe_print(f"\n🧠 Plan generated with {len(actions)} action(s).")
            last_output = ""

            for idx, act in enumerate(actions, start=1):
                if self.memory.is_cancelled():
                    return "Operation cancelled by user."

                safe_print(f"▶️ Executing planned action {idx}/{len(actions)}: {act.get('type')}")
                act_res = self.dispatcher.execute(act)
                self.audit.log_action_executed(act, act_res)

                if not act_res.get("success"):
                    msg = act_res.get("message", "Action failed")
                    return f"I encountered an issue executing the task: {msg}"

                if act.get("type") in {"cancel_task", "rollback_last_change"} and act_res.get("message"):
                    return act_res["message"]

                if "output" in act_res and act_res["output"]:
                    last_output = act_res["output"]

            if last_output:
                return f"Task completed successfully. Output:\n{last_output}"
            return "Task completed successfully."

        # -----------------------------
        # Fallback
        # -----------------------------
        return (
            f"I heard you say: {text}. "
            "I don't have a command for that yet."
        )


if __name__ == "__main__":
    brain = NRBrain()

    safe_print("================================")
    safe_print("       NR AI BRAIN TEST")
    safe_print("================================")

    test_queries = [
        "What time is it?",
        "Create a Python calculator and run it",
        "Show project status",
    ]

    for q in test_queries:
        safe_print(f"\nUser: {q}")
        resp = brain.think(q)
        safe_print(f"NR AI: {resp}")

    safe_print("\n================================")
    safe_print("🟢 NR BRAIN TEST COMPLETE")
    safe_print("================================")