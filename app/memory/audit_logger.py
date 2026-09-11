import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


class AuditLogger:
    """
    NR AI Persistent Activity & Audit Logger.

    Records structured logs of all user actions, plans, executions,
    tests, errors, recovery patches, and final verification results.
    """

    def __init__(self, log_dir: Optional[str] = None):
        self.log_dir = Path(log_dir or "data/audit").resolve()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = f"session_{int(time.time() * 1000)}"
        self.log_file = self.log_dir / f"{self.session_id}.json"
        self.entries: List[Dict[str, Any]] = []

    def _sanitize_details(self, obj: Any) -> Any:
        import re
        if isinstance(obj, dict):
            res = {}
            for k, v in obj.items():
                k_lower = str(k).lower()
                if any(sec in k_lower for sec in ("password", "passwd", "secret", "token", "api_key", "apikey")):
                    res[k] = "[REDACTED]"
                else:
                    res[k] = self._sanitize_details(v)
            return res
        elif isinstance(obj, list):
            return [self._sanitize_details(item) for item in obj]
        elif isinstance(obj, str):
            redacted = obj
            patterns = [
                (r'(?i)(password\s*[:=]\s*)["\']?[^\s"\']+["\']?', r'\1[REDACTED]'),
                (r'(?i)(secret\s*[:=]\s*)["\']?[^\s"\']+["\']?', r'\1[REDACTED]'),
                (r'(?i)(token\s*[:=]\s*)["\']?[^\s"\']+["\']?', r'\1[REDACTED]'),
                (r'AIzaSy[A-Za-z0-9_\-]{20,}', '[REDACTED_API_KEY]'),
                (r'sk-proj-[A-Za-z0-9_\-]{20,}', '[REDACTED_API_KEY]'),
            ]
            for pat, repl in patterns:
                redacted = re.sub(pat, repl, redacted)
            return redacted
        return obj

    def log_event(
        self,
        event_type: str,
        details: Dict[str, Any],
        status: str = "success",
    ) -> Dict[str, Any]:
        """Appends a structured event to the audit trail and writes to disk."""
        entry = {
            "timestamp": time.time(),
            "formatted_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "event_type": event_type,
            "action": event_type,
            "status": status,
            "details": self._sanitize_details(details),
        }
        self.entries.append(entry)
        self._flush_to_disk()
        return entry

    def log_user_request(self, query: str) -> None:
        self.log_event("user_request", {"query": query})

    def log_plan_generated(self, plan: Dict[str, Any]) -> None:
        self.log_event("plan_generated", {
            "intent": plan.get("intent"),
            "action_count": len(plan.get("actions", [])),
            "actions": [a.get("type") for a in plan.get("actions", [])],
        })

    def log_action_executed(self, action: Dict[str, Any], result: Dict[str, Any]) -> None:
        status = "success" if result.get("success") else "failure"
        self.log_event("action_executed", {
            "action_type": action.get("type"),
            "description": action.get("description"),
            "result_message": result.get("message"),
        }, status=status)

    def log_file_modification(self, filepath: str, action: str, backup: Optional[str] = None) -> None:
        self.log_event("file_modification", {
            "file": str(filepath),
            "action": action,
            "backup": str(backup) if backup else None,
        })

    def log_error_recovery(
        self,
        error_info: Dict[str, Any],
        recovery_result: Dict[str, Any],
    ) -> None:
        status = "success" if recovery_result.get("success") else "failure"
        self.log_event("error_recovery", {
            "error_type": error_info.get("type"),
            "failing_line": error_info.get("line"),
            "recovery_stage": recovery_result.get("stage"),
            "attempts": recovery_result.get("attempts"),
        }, status=status)

    def _flush_to_disk(self) -> None:
        """Flushes in-memory entries to JSON audit log."""
        try:
            payload = {
                "session_id": self.session_id,
                "total_events": len(self.entries),
                "events": self.entries,
            }
            self.log_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            pass

    def get_entries(self) -> List[Dict[str, Any]]:
        return list(self.entries)

    def get_recent_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        return list(self.entries)[-limit:]
