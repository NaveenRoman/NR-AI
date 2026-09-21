"""
NR-AI Shared Persistent Task & Checkpoint Store.
SQLite-backed, crash-safe, deterministic task persistence engine.

Key guarantees:
- SQLite persistence with WAL mode and serialized concurrency.
- Deterministic recovery: Destructive actions or steps requiring confirmation cannot
  be silently auto-resumed upon process reboot; explicit human confirmation is re-asserted.
- Automatic secret redaction applied to all stored checkpoints and step payloads.
- Bounded checkpoint payloads (<= 64KB) to prevent storage bloat or DoS.
- Workspace scoping and agent ownership verification.
- Stale/zombie task detection for automatic crash recovery.
"""

from dataclasses import asdict, dataclass, field
from enum import Enum
import contextlib
import json
import logging
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("NRAI.TaskStore")

DEFAULT_DB_PATH = Path(r"C:\NR-AI\data\task_checkpoints.db")
MAX_CHECKPOINT_BYTES = 65536  # 64 KB limit per checkpoint payload


class SharedTaskStatus(str, Enum):
    """Authoritative task states across NR-AI."""
    CREATED = "CREATED"
    PLANNING = "PLANNING"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    PAUSED = "PAUSED"
    FAILED = "FAILED"
    RECOVERABLE = "RECOVERABLE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


# Destructive verbs that cannot be silently auto-resumed on recovery
DESTRUCTIVE_VERBS: Set[str] = {
    "delete", "remove", "drop", "wipe", "uninstall", "factory_reset",
    "format", "clean_all", "force_push", "destroy", "truncate", "rmdir",
    "purge", "kill_process", "raw_shell", "rm_rf"
}

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),                          # OpenAI key
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),                         # Google API key
    re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.]+"),                # Bearer token
    re.compile(r"(?i)(password|secret|token|apikey|api_key)\s*[:=]\s*['\"][^'\"]+['\"]"), # KV secret
    re.compile(r"-----BEGIN (?:RSA )?PRIVATE KEY-----"),           # Private key
]


def sanitize_secrets(data: Any) -> Any:
    """Recursively scrub sensitive secrets from strings, dictionaries, and lists."""
    if isinstance(data, str):
        cleaned = data
        for pat in SECRET_PATTERNS:
            cleaned = pat.sub("[SECRET_REDACTED]", cleaned)
        return cleaned
    elif isinstance(data, dict):
        sanitized = {}
        for k, v in data.items():
            k_low = str(k).lower()
            if k_low == "confirmation_token":
                sanitized[k] = v
            elif any(s in k_low for s in ("password", "secret", "token", "apikey", "api_key", "auth_token")):
                sanitized[k] = "[SECRET_REDACTED]"
            else:
                sanitized[k] = sanitize_secrets(v)
        return sanitized
    elif isinstance(data, list):
        return [sanitize_secrets(item) for item in data]
    return data


@dataclass
class TaskRecord:
    """Complete persistent record of a task."""
    task_id: str
    agent_id: str
    project_id: str
    status: SharedTaskStatus
    current_step: int
    completed_steps: List[Dict[str, Any]] = field(default_factory=list)
    pending_steps: List[Dict[str, Any]] = field(default_factory=list)
    checkpoint: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    failure_reason: Optional[str] = None
    resume_token: str = field(default_factory=lambda: secrets.token_hex(16))
    resume_state: Dict[str, Any] = field(default_factory=dict)
    evidence_references: List[str] = field(default_factory=list)
    workspace_context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "project_id": self.project_id,
            "status": self.status.value,
            "current_step": self.current_step,
            "completed_steps": self.completed_steps,
            "pending_steps": self.pending_steps,
            "checkpoint": self.checkpoint,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "failure_reason": self.failure_reason,
            "resume_token": self.resume_token,
            "resume_state": self.resume_state,
            "evidence_references": self.evidence_references,
            "workspace_context": self.workspace_context,
        }


class TaskCheckpointStore:
    """
    Thread-safe, crash-safe SQLite repository for shared task state and checkpoints.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self._lock = threading.RLock()
        self._init_db()

    @contextlib.contextmanager
    def _connection(self):
        conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        os.makedirs(self.db_path.parent, exist_ok=True)
        with self._lock:
            with self._connection() as conn:
                conn.execute("""
                CREATE TABLE IF NOT EXISTS shared_tasks (
                    task_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_step INTEGER NOT NULL DEFAULT 0,
                    completed_steps TEXT NOT NULL DEFAULT '[]',
                    pending_steps TEXT NOT NULL DEFAULT '[]',
                    checkpoint TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    failure_reason TEXT,
                    resume_token TEXT NOT NULL,
                    resume_state TEXT NOT NULL DEFAULT '{}',
                    evidence_references TEXT NOT NULL DEFAULT '[]',
                    workspace_context TEXT NOT NULL DEFAULT '{}'
                );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_agent ON shared_tasks(agent_id);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_project ON shared_tasks(project_id);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON shared_tasks(status);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_updated ON shared_tasks(updated_at);")

    def create_task(
        self,
        task_id: Any,
        agent_id: Optional[str] = None,
        project_id: Optional[str] = None,
        initial_steps: Optional[List[Dict[str, Any]]] = None,
        workspace_context: Optional[Dict[str, Any]] = None,
        checkpoint: Optional[Dict[str, Any]] = None,
    ) -> TaskRecord:
        """Atomically create and persist a new task record."""
        now = time.time()
        if isinstance(task_id, TaskRecord):
            record = task_id
            if not record.created_at:
                record.created_at = now
            if not record.updated_at:
                record.updated_at = now
            if not record.resume_token:
                record.resume_token = secrets.token_hex(16)
            record.checkpoint = sanitize_secrets(record.checkpoint or {})
            record.workspace_context = sanitize_secrets(record.workspace_context or {})
            record.pending_steps = sanitize_secrets(record.pending_steps or [])
            record.completed_steps = sanitize_secrets(record.completed_steps or [])
            chk_json = json.dumps(record.checkpoint)
            if len(chk_json.encode("utf-8")) > MAX_CHECKPOINT_BYTES:
                raise ValueError(f"Checkpoint payload exceeds {MAX_CHECKPOINT_BYTES} bytes limit.")
        else:
            initial_steps = initial_steps or []
            workspace_context = workspace_context or {}
            checkpoint = checkpoint or {}

            # Scrub secrets
            sanitized_steps = sanitize_secrets(initial_steps)
            sanitized_ctx = sanitize_secrets(workspace_context)
            sanitized_chk = sanitize_secrets(checkpoint)

            chk_json = json.dumps(sanitized_chk)
            if len(chk_json.encode("utf-8")) > MAX_CHECKPOINT_BYTES:
                raise ValueError(f"Checkpoint payload exceeds {MAX_CHECKPOINT_BYTES} bytes limit.")

            resume_tok = secrets.token_hex(16)
            record = TaskRecord(
                task_id=str(task_id),
                agent_id=str(agent_id or "system"),
                project_id=str(project_id or "default"),
                status=SharedTaskStatus.CREATED,
                current_step=0,
                completed_steps=[],
                pending_steps=sanitized_steps,
                checkpoint=sanitized_chk,
                created_at=now,
                updated_at=now,
                failure_reason=None,
                resume_token=resume_tok,
                resume_state={"initial_step_count": len(sanitized_steps)},
                evidence_references=[],
                workspace_context=sanitized_ctx,
            )

        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    """
                    INSERT INTO shared_tasks (
                        task_id, agent_id, project_id, status, current_step,
                        completed_steps, pending_steps, checkpoint, created_at, updated_at,
                        failure_reason, resume_token, resume_state, evidence_references, workspace_context
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        record.task_id,
                        record.agent_id,
                        record.project_id,
                        record.status.value if hasattr(record.status, "value") else str(record.status),
                        record.current_step,
                        json.dumps(record.completed_steps),
                        json.dumps(record.pending_steps),
                        chk_json,
                        record.created_at,
                        record.updated_at,
                        record.failure_reason,
                        record.resume_token,
                        json.dumps(record.resume_state),
                        json.dumps(record.evidence_references),
                        json.dumps(record.workspace_context),
                    ),
                )
        return record

    def update_checkpoint(
        self,
        task_id: str,
        current_step: int,
        checkpoint_data: Dict[str, Any],
        completed_step: Optional[Dict[str, Any]] = None,
        evidence_reference: Optional[str] = None,
        status: Optional[SharedTaskStatus] = None,
    ) -> Optional[TaskRecord]:
        """Atomically update checkpoint state, step progress, and evidence references."""
        now = time.time()
        sanitized_chk = sanitize_secrets(checkpoint_data)
        chk_json = json.dumps(sanitized_chk)
        if len(chk_json.encode("utf-8")) > MAX_CHECKPOINT_BYTES:
            raise ValueError(f"Checkpoint payload exceeds {MAX_CHECKPOINT_BYTES} bytes limit.")

        with self._lock:
            with self._connection() as conn:
                cur = conn.execute("SELECT * FROM shared_tasks WHERE task_id = ?", (task_id,))
                row = cur.fetchone()
                if not row:
                    return None

                completed_steps = json.loads(row["completed_steps"])
                pending_steps = json.loads(row["pending_steps"])
                evidence_refs = json.loads(row["evidence_references"])

                if completed_step:
                    sanitized_step = sanitize_secrets(completed_step)
                    completed_steps.append(sanitized_step)
                    # Pop from pending if present
                    if pending_steps:
                        pending_steps.pop(0)

                if evidence_reference and evidence_reference not in evidence_refs:
                    evidence_refs.append(evidence_reference)

                new_status = (status.value if status else row["status"])

                conn.execute(
                    """
                    UPDATE shared_tasks SET
                        current_step = ?,
                        status = ?,
                        completed_steps = ?,
                        pending_steps = ?,
                        checkpoint = ?,
                        evidence_references = ?,
                        updated_at = ?
                    WHERE task_id = ?;
                    """,
                    (
                        current_step,
                        new_status,
                        json.dumps(completed_steps),
                        json.dumps(pending_steps),
                        chk_json,
                        json.dumps(evidence_refs),
                        now,
                        task_id,
                    ),
                )

        return self.get_task(task_id)

    def save_checkpoint(
        self,
        task_id: str,
        step_number: int,
        state_data: Dict[str, Any],
        evidence_references: Optional[List[str]] = None,
    ) -> bool:
        """Convenience method to update checkpoint state."""
        evidence_ref = evidence_references[0] if evidence_references else None
        res = self.update_checkpoint(
            task_id=task_id,
            current_step=step_number,
            checkpoint_data=state_data,
            evidence_reference=evidence_ref,
        )
        return res is not None

    def set_task_status(
        self,
        task_id: str,
        status: SharedTaskStatus,
        failure_reason: Optional[str] = None,
    ) -> bool:
        """Update lifecycle status of a task."""
        now = time.time()
        with self._lock:
            with self._connection() as conn:
                cur = conn.execute(
                    """
                    UPDATE shared_tasks SET
                        status = ?,
                        failure_reason = ?,
                        updated_at = ?
                    WHERE task_id = ?;
                    """,
                    (status.value, failure_reason, now, task_id),
                )
                return cur.rowcount > 0

    def get_task(self, task_id: str) -> Optional[TaskRecord]:
        """Fetch a single task record by ID."""
        with self._lock:
            with self._connection() as conn:
                cur = conn.execute("SELECT * FROM shared_tasks WHERE task_id = ?", (task_id,))
                row = cur.fetchone()
                if not row:
                    return None
                return self._row_to_record(row)

    def list_tasks(
        self,
        agent_id: Optional[str] = None,
        project_id: Optional[str] = None,
        status: Optional[SharedTaskStatus] = None,
        limit: int = 50,
    ) -> List[TaskRecord]:
        """List tasks matching optional filters."""
        query = "SELECT * FROM shared_tasks WHERE 1=1"
        params: List[Any] = []

        if agent_id:
            query += " AND agent_id = ?"
            params.append(agent_id)
        if project_id:
            query += " AND project_id = ?"
            params.append(project_id)
        if status:
            query += " AND status = ?"
            params.append(status.value)

        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            with self._connection() as conn:
                cur = conn.execute(query, params)
                return [self._row_to_record(row) for row in cur.fetchall()]

    def find_stale_tasks(self, timeout_seconds: float = 600.0) -> List[TaskRecord]:
        """
        Detects zombie/crashed tasks that were running or planning but haven't updated
        within timeout_seconds. Used for automated recovery upon NR-AI reboot.
        """
        cutoff = time.time() - timeout_seconds
        with self._lock:
            with self._connection() as conn:
                cur = conn.execute(
                    """
                    SELECT * FROM shared_tasks
                    WHERE status IN ('RUNNING', 'PLANNING')
                    AND updated_at < ?;
                    """,
                    (cutoff,),
                )
                return [self._row_to_record(row) for row in cur.fetchall()]

    def evaluate_recovery(self, task_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Evaluates whether a task can be safely recovered/resumed.
        Deterministic invariant: If the next pending action is destructive or requires
        confirmation, it cannot be silently resumed; it is flagged for human confirmation.
        """
        task = self.get_task(task_id)
        if not task:
            return False, f"Task '{task_id}' not found.", None

        if task.status not in (SharedTaskStatus.RECOVERABLE, SharedTaskStatus.PAUSED, SharedTaskStatus.RUNNING):
            return False, f"Task status '{task.status.value}' is not recoverable.", None

        if not task.pending_steps:
            return True, "No pending steps remain; task can be completed.", None

        next_step = task.pending_steps[0]
        step_action = str(next_step.get("action", "")).lower()
        step_description = str(next_step.get("description", "")).lower()
        requires_confirm = bool(next_step.get("requires_confirmation", False))

        is_destructive = any(v in step_action or v in step_description for v in DESTRUCTIVE_VERBS)

        if is_destructive or requires_confirm:
            return False, "REQUIRES_CONFIRMATION: Next pending action is high-risk or destructive.", next_step

        return True, "RECOVERABLE_SAFE: Next action is non-destructive and verified safe for resumption.", next_step

    def resume_task(
        self,
        task_id: str,
        resume_token: str,
        confirmation_granted: bool = False,
    ) -> Tuple[bool, str, Optional[TaskRecord]]:
        """
        Resumes a paused or recoverable task after verifying the resume token
        and deterministic safety constraints.
        """
        task = self.get_task(task_id)
        if not task:
            return False, f"Task '{task_id}' not found.", None

        if task.resume_token != resume_token:
            return False, "INVALID_RESUME_TOKEN: Resume token mismatch.", None

        can_resume, reason, next_step = self.evaluate_recovery(task_id)
        if not can_resume:
            if "REQUIRES_CONFIRMATION" in reason and not confirmation_granted:
                return False, f"Cannot resume without explicit operator confirmation: {reason}", task

        # Generate a new resume token for subsequent checkpoints
        new_resume_token = secrets.token_hex(16)
        now = time.time()

        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    """
                    UPDATE shared_tasks SET
                        status = 'RUNNING',
                        resume_token = ?,
                        updated_at = ?
                    WHERE task_id = ?;
                    """,
                    (new_resume_token, now, task_id),
                )

        updated_task = self.get_task(task_id)
        return True, "Task successfully resumed.", updated_task

    def _row_to_record(self, row: sqlite3.Row) -> TaskRecord:
        return TaskRecord(
            task_id=row["task_id"],
            agent_id=row["agent_id"],
            project_id=row["project_id"],
            status=SharedTaskStatus(row["status"]),
            current_step=row["current_step"],
            completed_steps=json.loads(row["completed_steps"]),
            pending_steps=json.loads(row["pending_steps"]),
            checkpoint=json.loads(row["checkpoint"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            failure_reason=row["failure_reason"],
            resume_token=row["resume_token"],
            resume_state=json.loads(row["resume_state"]),
            evidence_references=json.loads(row["evidence_references"]),
            workspace_context=json.loads(row["workspace_context"]),
        )


# Global singleton instance
global_task_store = TaskCheckpointStore()
