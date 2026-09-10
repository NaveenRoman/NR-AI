import json
import os
import re
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
        self.active_target: Optional[Dict[str, Any]] = None
        self.recent_files_modified: List[str] = []
        self.recent_commands_executed: List[str] = []
        self.plan_history: List[Dict[str, Any]] = []
        self.error_history: List[Dict[str, Any]] = []
        self.rollback_stack: List[Dict[str, Any]] = []
        self.last_user_query: str = ""
        self.last_response: str = ""
        self.cancellation_requested: bool = False
        self.active_services: Dict[str, Any] = {}
        # Step 4E Unified Computer Agent context state
        self.active_window: Optional[Dict[str, Any]] = None
        self.active_visual_target: Optional[Dict[str, Any]] = None
        self.last_action: Optional[Dict[str, Any]] = None
        self.verification_result: Optional[Dict[str, Any]] = None
        self.workflow_state: Optional[Dict[str, Any]] = None

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

    def set_active_target(
        self,
        target: str,
        target_type: str = "generic",
        source_command: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Sets the active entity/target in session memory."""
        self.active_target = {
            "value": str(target).strip(),
            "type": target_type,
            "source_command": source_command or "",
            "metadata": metadata or {},
            "timestamp": time.time(),
        }

    def get_active_target(self) -> Optional[Dict[str, Any]]:
        """Returns the active target dictionary if present."""
        return self.active_target

    def get_active_target_value(self) -> Optional[str]:
        """Returns the raw target string value (path, url, name) or None."""
        return self.active_target["value"] if self.active_target else None

    def clear_active_target(self) -> None:
        """Clears the active target."""
        self.active_target = None

    def set_active_window(self, window_info: Dict[str, Any]) -> None:
        """Sets the active window state."""
        self.active_window = {
            "info": window_info,
            "timestamp": time.time(),
        }

    def get_active_window(self) -> Optional[Dict[str, Any]]:
        """Returns the active window info or None."""
        return self.active_window.get("info") if self.active_window else None

    def set_active_visual_target(
        self,
        target: str,
        coordinates: Any,
        window: Optional[str] = None,
        confidence: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Sets the active visual target with bounded coordinates."""
        self.active_visual_target = {
            "target": str(target).strip(),
            "coordinates": list(coordinates) if coordinates else None,
            "window": window,
            "confidence": float(confidence),
            "timestamp": time.time(),
            "metadata": metadata or {},
        }

    def get_active_visual_target(self, max_age_seconds: float = 15.0) -> Optional[Dict[str, Any]]:
        """
        Returns active visual target if fresher than max_age_seconds (default 15s).
        Returns None if expired or unset to prevent stale coordinate execution.
        """
        if not self.active_visual_target:
            return None
        age = time.time() - self.active_visual_target.get("timestamp", 0.0)
        if age > max_age_seconds:
            return None
        return self.active_visual_target

    def clear_active_visual_target(self) -> None:
        """Clears active visual target."""
        self.active_visual_target = None

    def set_last_action(self, action_data: Dict[str, Any]) -> None:
        """Stores the most recent computer action."""
        self.last_action = {
            "action": action_data,
            "timestamp": time.time(),
        }

    def get_last_action(self) -> Optional[Dict[str, Any]]:
        """Returns the most recent computer action."""
        return self.last_action.get("action") if self.last_action else None

    def set_verification_result(self, result: Dict[str, Any]) -> None:
        """Stores verification outcome of the last action."""
        self.verification_result = {
            "result": result,
            "timestamp": time.time(),
        }

    def get_verification_result(self) -> Optional[Dict[str, Any]]:
        """Returns verification outcome of the last action."""
        return self.verification_result.get("result") if self.verification_result else None

    def set_workflow_state(self, state: Dict[str, Any]) -> None:
        """Stores current computer workflow state."""
        self.workflow_state = {
            "state": state,
            "timestamp": time.time(),
        }

    def get_workflow_state(self) -> Optional[Dict[str, Any]]:
        """Returns current computer workflow state."""
        return self.workflow_state.get("state") if self.workflow_state else None

    def resolve_followup_target(self, query: str) -> Optional[str]:
        """
        Resolves entity references in follow-up queries:
        - "open it" -> active target
        - "what was that?" -> active target
        - "update that" -> last modified file or active target
        - "the login page" -> App.jsx / auth.py / login file
        - "the models" -> models.py
        - "the server" -> app.py / server.py
        """
        text = query.lower()

        # Check for pronoun references targeting the active entity
        words = set(re.findall(r"\b\w+\b", text))
        pronoun_hits = words.intersection({"it", "that", "this"})
        phrase_hits = any(phrase in text for phrase in ["the file", "the target", "last file", "the output"])

        if pronoun_hits or phrase_hits:
            if self.active_target:
                return self.active_target.get("value")
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

        if self.active_target:
            return self.active_target.get("value")

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
            "active_target": self.active_target,
            "recent_files_count": len(self.recent_files_modified),
            "recent_files": self.recent_files_modified[:5],
            "rollback_checkpoints": len(self.rollback_stack),
            "total_plans": len(self.plan_history),
            "total_errors": len(self.error_history),
            "cancelled": self.cancellation_requested,
            "active_window": self.active_window,
            "active_visual_target": self.get_active_visual_target(),
            "last_action": self.last_action,
            "verification_result": self.verification_result,
            "workflow_state": self.workflow_state,
        }
