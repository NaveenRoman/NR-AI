import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


class ProjectContextMemory:
    """
    NR AI Project & Session Context Memory.

    Maintains multi-turn context across user interactions:
    - Active project path
    - Tracked generated and modified files
    - Previous action & plan history
    - Error and recovery attempt logs
    - Rollback checkpoint stack
    - Follow-up conversational resolution ("that", "the login page", "the server", "undo")
    """

    def __init__(self, workspace: Optional[str] = None):
        self.workspace = Path(workspace or os.getcwd()).resolve()
        self.active_project_path: Optional[str] = None
        self.recent_files_modified: List[str] = []
        self.recent_commands_executed: List[str] = []
        self.plan_history: List[Dict[str, Any]] = []
        self.error_history: List[Dict[str, Any]] = []
        self.rollback_stack: List[Dict[str, Any]] = []
        self.last_user_query: str = ""
        self.last_response: str = ""
        self.cancellation_requested: bool = False
        self.active_services: Dict[str, Any] = {}

    def set_active_project(self, project_path: str) -> None:
        """Sets the active project working directory."""
        self.active_project_path = project_path

    def get_active_project(self) -> str:
        """Returns the active project path or workspace root."""
        return self.active_project_path or str(self.workspace)

    def record_file_modified(self, filepath: str, backup_path: Optional[str] = None) -> None:
        """Tracks a modified file and pushes to rollback stack."""
        normalized = str(filepath).replace("\\", "/")
        if normalized in self.recent_files_modified:
            self.recent_files_modified.remove(normalized)
        self.recent_files_modified.insert(0, normalized)

        if backup_path:
            self.rollback_stack.append({
                "file": normalized,
                "backup_path": str(backup_path),
                "timestamp": time.time(),
            })

    def record_plan(self, plan: Dict[str, Any]) -> None:
        """Records a generated task plan."""
        self.plan_history.append({
            "timestamp": time.time(),
            "plan": plan,
        })

    def record_command(self, command: str) -> None:
        """Records a run terminal/system command."""
        self.recent_commands_executed.insert(0, command)

    def record_error(self, error_info: Dict[str, Any]) -> None:
        """Records an encountered error and diagnostic."""
        self.error_history.append({
            "timestamp": time.time(),
            "error": error_info,
        })

    def get_last_modified_file(self) -> Optional[str]:
        """Returns the most recently modified file path."""
        return self.recent_files_modified[0] if self.recent_files_modified else None

    def get_last_error_file(self) -> Optional[str]:
        """Returns the file associated with the most recent error."""
        if not self.error_history:
            return None
        latest = self.error_history[-1].get("error", {})
        return latest.get("filename") or latest.get("file")

    def resolve_followup_target(self, query: str) -> Optional[str]:
        """
        Resolves entity references in follow-up queries:
        - "update that" -> last modified file
        - "the login page" -> App.jsx / auth.py / login file
        - "the models" -> models.py
        - "the server" -> app.py / server.py
        """
        text = query.lower()

        if "that" in text or "the file" in text or "last file" in text:
            return self.get_last_modified_file()

        if "login" in text or "auth" in text:
            for f in self.recent_files_modified:
                if "auth" in f.lower() or "login" in f.lower() or "app.jsx" in f.lower():
                    return f
            return "backend/auth.py"

        if "model" in text or "database" in text or "db" in text:
            for f in self.recent_files_modified:
                if "model" in f.lower() or "db" in f.lower():
                    return f
            return "backend/models.py"

        if "server" in text or "api" in text:
            for f in self.recent_files_modified:
                if "app.py" in f.lower() or "server" in f.lower() or "api" in f.lower():
                    return f
            return "backend/app.py"

        if "test" in text:
            for f in self.recent_files_modified:
                if "test" in f.lower():
                    return f
            return "tests/test_backend.py"

        return self.get_last_modified_file()

    def pop_rollback(self) -> Optional[Dict[str, Any]]:
        """Pops the most recent checkpoint for undo/rollback."""
        if not self.rollback_stack:
            return None
        return self.rollback_stack.pop()

    def clear_cancellation(self) -> None:
        self.cancellation_requested = False

    def request_cancellation(self) -> None:
        self.cancellation_requested = True

    def is_cancelled(self) -> bool:
        return self.cancellation_requested

    def get_summary(self) -> Dict[str, Any]:
        """Returns a snapshot of the current session state."""
        return {
            "active_project": self.get_active_project(),
            "recent_files_count": len(self.recent_files_modified),
            "recent_files": self.recent_files_modified[:5],
            "rollback_checkpoints": len(self.rollback_stack),
            "total_plans": len(self.plan_history),
            "total_errors": len(self.error_history),
            "cancelled": self.cancellation_requested,
        }
