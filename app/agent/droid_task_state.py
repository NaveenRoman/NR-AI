"""
Droid Persistent Task State Subsystem (SQLite-backed)

Preserves structured workflow state across process restarts and interruptions.
Enforces deterministic safety:
  - Destructive/high-risk actions are blocked from automatic resumption.
  - Strict secret scrubbing (API keys, bearer tokens, passwords) before persistence.
  - Bounded structured data only.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger("DroidTaskState")

DEFAULT_DB_PATH = Path(r"C:\NR-AI\data\droid_task_state.db")


# -----------------------------------------------------------------------------
# Enums
# -----------------------------------------------------------------------------

class TaskStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    REQUIRES_CONFIRMATION = "REQUIRES_CONFIRMATION"


class TaskState(str, Enum):
    INITIALIZED = "INITIALIZED"
    INSPECTING = "INSPECTING"
    BUILDING = "BUILDING"
    TESTING = "TESTING"
    DEPLOYING = "DEPLOYING"
    REPAIRING = "REPAIRING"
    ROLLING_BACK = "ROLLING_BACK"
    INTERRUPTED = "INTERRUPTED"
    HALTED = "HALTED"
    DONE = "DONE"


# Verbs and patterns considered high-risk / destructive for auto-resume
DESTRUCTIVE_VERBS = {
    "delete", "remove", "drop", "wipe", "uninstall", "factory_reset",
    "format", "clean_all", "force_push", "destroy", "truncate", "rmdir",
    "purge", "kill_process", "raw_shell", "rm_rf"
}


# -----------------------------------------------------------------------------
# Secret Sanitizer
# -----------------------------------------------------------------------------

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),                          # OpenAI key
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),                         # Google API key
    re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.]+"),                # Bearer token
    re.compile(r"(?i)(password|secret|token|apikey|api_key|key)\s*[:=]\s*['\"][^'\"]+['\"]"), # Config kv
]

def sanitize_value(val: Any) -> Any:
    """Recursively scrub secrets from strings, dictionaries, and lists."""
    if isinstance(val, str):
        cleaned = val
        for pat in SECRET_PATTERNS:
            cleaned = pat.sub("[REDACTED_SECRET]", cleaned)
        return cleaned
    elif isinstance(val, dict):
        new_d = {}
        for k, v in val.items():
            k_low = str(k).lower()
            if any(s in k_low for s in ("password", "secret", "token", "apikey", "api_key", "auth", "key")):
                new_d[k] = "[REDACTED_SECRET]"
            else:
                new_d[k] = sanitize_value(v)
        return new_d
    elif isinstance(val, list):
        return [sanitize_value(item) for item in val]
    return val


# -----------------------------------------------------------------------------
# Data Models
# -----------------------------------------------------------------------------

@dataclass
class DroidTaskRecord:
    task_id: str
    project_id: str
    agent: str
    workflow: str
    current_state: str
    current_step: str
    completed_steps: List[Dict[str, Any]] = field(default_factory=list)
    pending_steps: List[Dict[str, Any]] = field(default_factory=list)
    last_error: Optional[str] = None
    checkpoint: Optional[Dict[str, Any]] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    status: str = TaskStatus.PENDING.value

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DroidResumeResult:
    task_id: str
    can_resume: bool
    status: str
    next_step: Optional[Dict[str, Any]]
    checkpoint: Optional[Dict[str, Any]]
    requires_confirmation: bool = False
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# -----------------------------------------------------------------------------
# SQLite Task State Store
# -----------------------------------------------------------------------------

class DroidTaskStateStore:
    """
    Lightweight, atomic SQLite store for managing Droid and Android persistent tasks.
    """

    def __init__(self, db_path: Optional[Union[str, Path]] = None):
        if db_path is None:
            self.db_path = DEFAULT_DB_PATH
        elif str(db_path) == ":memory:":
            self.db_path = Path(":memory:")
        else:
            self.db_path = Path(db_path).resolve()

        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if str(self.db_path) == ":memory:":
            if not hasattr(self, "_mem_conn"):
                self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
            return self._mem_conn
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init_db(self) -> None:
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS droid_task_states (
                        task_id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL,
                        agent TEXT NOT NULL,
                        workflow TEXT NOT NULL,
                        current_state TEXT NOT NULL,
                        current_step TEXT NOT NULL,
                        completed_steps TEXT NOT NULL,
                        pending_steps TEXT NOT NULL,
                        last_error TEXT,
                        checkpoint TEXT,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        status TEXT NOT NULL
                    );
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_droid_task_proj_status
                    ON droid_task_states(project_id, status);
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_droid_task_agent_status
                    ON droid_task_states(agent, status);
                """)
        finally:
            if str(self.db_path) != ":memory:":
                conn.close()

    def create_task(
        self,
        project_id: str,
        workflow: str,
        initial_steps: List[Union[str, Dict[str, Any]]],
        agent: str = "Droid",
        initial_state: str = TaskState.INITIALIZED.value,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DroidTaskRecord:
        """Create and persist a new task record."""
        task_id = f"droid_task_{uuid.uuid4().hex[:12]}"
        now = time.time()

        # Format initial steps into structured dicts
        normalized_pending: List[Dict[str, Any]] = []
        for i, s in enumerate(initial_steps):
            if isinstance(s, str):
                normalized_pending.append({
                    "step_id": f"step_{i+1:03d}",
                    "name": s,
                    "action": s,
                    "destructive": any(verb in s.lower() for verb in DESTRUCTIVE_VERBS),
                    "created_at": now,
                })
            elif isinstance(s, dict):
                step_copy = dict(s)
                action_name = step_copy.get("name") or step_copy.get("action") or ""
                is_destr = step_copy.get("destructive", any(v in str(action_name).lower() for v in DESTRUCTIVE_VERBS))
                step_copy.setdefault("step_id", f"step_{i+1:03d}")
                step_copy["destructive"] = bool(is_destr)
                normalized_pending.append(step_copy)

        current_step_name = normalized_pending[0]["name"] if normalized_pending else "none"

        # Sanitize metadata
        clean_checkpoint = sanitize_value(metadata) if metadata else None
        clean_pending = sanitize_value(normalized_pending)

        record = DroidTaskRecord(
            task_id=task_id,
            project_id=project_id,
            agent=agent,
            workflow=workflow,
            current_state=initial_state,
            current_step=current_step_name,
            completed_steps=[],
            pending_steps=clean_pending,
            last_error=None,
            checkpoint=clean_checkpoint,
            created_at=now,
            updated_at=now,
            status=TaskStatus.PENDING.value,
        )

        conn = self._get_connection()
        try:
            with conn:
                conn.execute("""
                    INSERT INTO droid_task_states (
                        task_id, project_id, agent, workflow, current_state,
                        current_step, completed_steps, pending_steps, last_error,
                        checkpoint, created_at, updated_at, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    record.task_id,
                    record.project_id,
                    record.agent,
                    record.workflow,
                    record.current_state,
                    record.current_step,
                    json.dumps(record.completed_steps),
                    json.dumps(record.pending_steps),
                    record.last_error,
                    json.dumps(record.checkpoint) if record.checkpoint is not None else None,
                    record.created_at,
                    record.updated_at,
                    record.status,
                ))
            logger.info(f"Created Droid task '{task_id}' for project '{project_id}' ({workflow}).")
            return record
        finally:
            if str(self.db_path) != ":memory:":
                conn.close()

    def get_task(self, task_id: str) -> Optional[DroidTaskRecord]:
        """Fetch a task record by ID."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM droid_task_states WHERE task_id = ?", (task_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return DroidTaskRecord(
                task_id=row[0],
                project_id=row[1],
                agent=row[2],
                workflow=row[3],
                current_state=row[4],
                current_step=row[5],
                completed_steps=json.loads(row[6]),
                pending_steps=json.loads(row[7]),
                last_error=row[8],
                checkpoint=json.loads(row[9]) if row[9] is not None else None,
                created_at=row[10],
                updated_at=row[11],
                status=row[12],
            )
        finally:
            if str(self.db_path) != ":memory:":
                conn.close()

    def list_tasks(
        self,
        project_id: Optional[str] = None,
        status: Optional[Union[str, TaskStatus]] = None,
        limit: int = 50,
    ) -> List[DroidTaskRecord]:
        """List tasks with optional filters."""
        conn = self._get_connection()
        try:
            query = "SELECT * FROM droid_task_states WHERE 1=1"
            params: List[Any] = []
            if project_id:
                query += " AND project_id = ?"
                params.append(project_id)
            if status:
                stat_val = status.value if isinstance(status, TaskStatus) else str(status)
                query += " AND status = ?"
                params.append(stat_val)
            query += " ORDER BY updated_at DESC LIMIT ?"
            params.append(limit)

            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            records: List[DroidTaskRecord] = []
            for row in rows:
                records.append(DroidTaskRecord(
                    task_id=row[0],
                    project_id=row[1],
                    agent=row[2],
                    workflow=row[3],
                    current_state=row[4],
                    current_step=row[5],
                    completed_steps=json.loads(row[6]),
                    pending_steps=json.loads(row[7]),
                    last_error=row[8],
                    checkpoint=json.loads(row[9]) if row[9] is not None else None,
                    created_at=row[10],
                    updated_at=row[11],
                    status=row[12],
                ))
            return records
        finally:
            if str(self.db_path) != ":memory:":
                conn.close()

    def update_task(
        self,
        task_id: str,
        current_state: Optional[str] = None,
        current_step: Optional[str] = None,
        status: Optional[Union[str, TaskStatus]] = None,
        last_error: Optional[str] = None,
    ) -> DroidTaskRecord:
        """Update basic fields of a task."""
        task = self.get_task(task_id)
        if not task:
            raise KeyError(f"Task '{task_id}' not found.")

        now = time.time()
        if current_state is not None:
            task.current_state = current_state
        if current_step is not None:
            task.current_step = current_step
        if status is not None:
            task.status = status.value if isinstance(status, TaskStatus) else str(status)
        if last_error is not None:
            task.last_error = sanitize_value(last_error)
        task.updated_at = now

        conn = self._get_connection()
        try:
            with conn:
                conn.execute("""
                    UPDATE droid_task_states
                    SET current_state = ?, current_step = ?, status = ?,
                        last_error = ?, updated_at = ?
                    WHERE task_id = ?
                """, (
                    task.current_state,
                    task.current_step,
                    task.status,
                    task.last_error,
                    task.updated_at,
                    task_id,
                ))
            return task
        finally:
            if str(self.db_path) != ":memory:":
                conn.close()

    def checkpoint_task(
        self,
        task_id: str,
        checkpoint_data: Dict[str, Any],
        completed_step: Optional[Union[str, Dict[str, Any]]] = None,
        next_step: Optional[str] = None,
        current_state: Optional[str] = None,
    ) -> DroidTaskRecord:
        """
        Atomically records task progress and sets a safe checkpoint.
        Moves completed step from pending to completed list.
        """
        task = self.get_task(task_id)
        if not task:
            raise KeyError(f"Task '{task_id}' not found.")

        now = time.time()
        clean_checkpoint = sanitize_value(checkpoint_data)

        # Merge new checkpoint with existing
        existing_cp = task.checkpoint or {}
        existing_cp.update(clean_checkpoint)
        existing_cp["last_checkpoint_time"] = now
        task.checkpoint = existing_cp

        if completed_step is not None:
            if isinstance(completed_step, str):
                comp_dict = {"name": completed_step, "completed_at": now}
            else:
                comp_dict = dict(completed_step)
                comp_dict.setdefault("completed_at", now)
            task.completed_steps.append(sanitize_value(comp_dict))

            # Pop from pending_steps if present
            task.pending_steps = [
                s for s in task.pending_steps
                if s.get("name") != (completed_step if isinstance(completed_step, str) else completed_step.get("name"))
            ]

        if next_step is not None:
            task.current_step = next_step
        elif task.pending_steps:
            task.current_step = task.pending_steps[0].get("name", "in_progress")
        else:
            task.current_step = "all_steps_completed"

        if current_state is not None:
            task.current_state = current_state
        task.status = TaskStatus.RUNNING.value
        task.updated_at = now

        conn = self._get_connection()
        try:
            with conn:
                conn.execute("""
                    UPDATE droid_task_states
                    SET current_state = ?, current_step = ?, completed_steps = ?,
                        pending_steps = ?, checkpoint = ?, status = ?, updated_at = ?
                    WHERE task_id = ?
                """, (
                    task.current_state,
                    task.current_step,
                    json.dumps(task.completed_steps),
                    json.dumps(task.pending_steps),
                    json.dumps(task.checkpoint),
                    task.status,
                    task.updated_at,
                    task_id,
                ))
            logger.info(f"Checkpointed Droid task '{task_id}': step='{task.current_step}'.")
            return task
        finally:
            if str(self.db_path) != ":memory:":
                conn.close()

    def resume_task(self, task_id: str, allow_destructive_override: bool = False) -> DroidResumeResult:
        """
        Safely inspects task state and determines if it can resume execution.
        CRITICAL GUARD: Destructive or high-risk actions are blocked from automatic resumption.
        """
        task = self.get_task(task_id)
        if not task:
            return DroidResumeResult(
                task_id=task_id,
                can_resume=False,
                status="NOT_FOUND",
                next_step=None,
                checkpoint=None,
                reason="Task not found in store.",
            )

        if task.status in (TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value):
            return DroidResumeResult(
                task_id=task_id,
                can_resume=False,
                status=task.status,
                next_step=None,
                checkpoint=task.checkpoint,
                reason=f"Task is already {task.status}.",
            )

        # Determine next action
        next_step: Optional[Dict[str, Any]] = None
        if task.pending_steps:
            next_step = task.pending_steps[0]
        elif task.current_step and task.current_step != "all_steps_completed":
            next_step = {"name": task.current_step, "action": task.current_step}

        if not next_step:
            self.complete_task(task_id, {"reason": "All steps were already finished."})
            return DroidResumeResult(
                task_id=task_id,
                can_resume=False,
                status=TaskStatus.COMPLETED.value,
                next_step=None,
                checkpoint=task.checkpoint,
                reason="All steps completed.",
            )

        # Destructive action detection
        step_name = str(next_step.get("name", "")).lower()
        step_action = str(next_step.get("action", "")).lower()
        is_destr = next_step.get("destructive", False) or any(
            v in step_name or v in step_action for v in DESTRUCTIVE_VERBS
        )

        if is_destr and not allow_destructive_override:
            # Block automatic execution! Require human confirmation
            self.update_task(
                task_id=task_id,
                status=TaskStatus.REQUIRES_CONFIRMATION,
                current_state=TaskState.HALTED.value,
            )
            return DroidResumeResult(
                task_id=task_id,
                can_resume=False,
                status=TaskStatus.REQUIRES_CONFIRMATION.value,
                next_step=next_step,
                checkpoint=task.checkpoint,
                requires_confirmation=True,
                reason=f"Action '{next_step.get('name')}' is high-risk/destructive. Automatic resumption blocked without explicit confirmation.",
            )

        # Safe to resume
        self.update_task(task_id=task_id, status=TaskStatus.RUNNING, current_state=TaskState.BUILDING.value)
        return DroidResumeResult(
            task_id=task_id,
            can_resume=True,
            status=TaskStatus.RUNNING.value,
            next_step=next_step,
            checkpoint=task.checkpoint,
            requires_confirmation=False,
            reason="Resumed bounded safe workflow.",
        )

    def fail_task(self, task_id: str, error_message: str) -> DroidTaskRecord:
        """Mark task as FAILED with sanitized error message."""
        clean_err = sanitize_value(error_message)
        return self.update_task(
            task_id=task_id,
            status=TaskStatus.FAILED,
            current_state=TaskState.HALTED.value,
            last_error=str(clean_err),
        )

    def complete_task(self, task_id: str, summary: Optional[Dict[str, Any]] = None) -> DroidTaskRecord:
        """Mark task as COMPLETED."""
        task = self.get_task(task_id)
        if not task:
            raise KeyError(f"Task '{task_id}' not found.")

        now = time.time()
        task.status = TaskStatus.COMPLETED.value
        task.current_state = TaskState.DONE.value
        task.current_step = "finished"
        task.pending_steps = []
        task.updated_at = now
        if summary:
            cp = task.checkpoint or {}
            cp["completion_summary"] = sanitize_value(summary)
            task.checkpoint = cp

        conn = self._get_connection()
        try:
            with conn:
                conn.execute("""
                    UPDATE droid_task_states
                    SET current_state = ?, current_step = ?, pending_steps = ?,
                        checkpoint = ?, status = ?, updated_at = ?
                    WHERE task_id = ?
                """, (
                    task.current_state,
                    task.current_step,
                    json.dumps(task.pending_steps),
                    json.dumps(task.checkpoint),
                    task.status,
                    task.updated_at,
                    task_id,
                ))
            logger.info(f"Completed Droid task '{task_id}'.")
            return task
        finally:
            if str(self.db_path) != ":memory:":
                conn.close()

    def cancel_task(self, task_id: str, reason: str = "Cancelled by user") -> DroidTaskRecord:
        """Mark task as CANCELLED."""
        clean_reason = sanitize_value(reason)
        return self.update_task(
            task_id=task_id,
            status=TaskStatus.CANCELLED,
            current_state=TaskState.HALTED.value,
            last_error=f"CANCELLED: {clean_reason}",
        )
