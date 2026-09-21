"""
NR-AI Persistent Automation Engine.
SQLite WAL-backed repository with deterministic safety gates,
crash recovery, secret redaction, and ownership validation.
"""

from dataclasses import asdict
import contextlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from app.automation.models import (
    AutomationExecutionRecord,
    AutomationRecord,
    AutomationStatus,
    ScheduleConfig,
    ScheduleType,
)
from app.task.checkpoint_store import DESTRUCTIVE_VERBS, sanitize_secrets
from app.security.guardrails import PromptGuardrails

logger = logging.getLogger("NRAI.AutomationEngine")

DEFAULT_AUTOMATIONS_DB_PATH = Path(r"C:\NR-AI\data\automations.db")


class AutomationEngine:
    """
    Persistent SQLite manager for background automations and scheduled tasks.
    Guarantees thread-safety, WAL crash recovery, secret scrubbing, and destructive confirmation.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path or DEFAULT_AUTOMATIONS_DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    @contextlib.contextmanager
    def _transaction(self):
        with self._lock:
            conn = self._get_connection()
            try:
                yield conn
                conn.commit()
            except Exception as e:
                conn.rollback()
                logger.error(f"Automation DB transaction error: {e}", exc_info=True)
                raise
            finally:
                conn.close()

    def _init_db(self) -> None:
        """Create automations and execution history tables with indexes."""
        with self._transaction() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS automations (
                    automation_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    project_id TEXT,
                    prompt TEXT NOT NULL,
                    schedule_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    last_run REAL,
                    next_run REAL,
                    failure_reason TEXT,
                    execution_count INTEGER DEFAULT 0,
                    retry_count INTEGER DEFAULT 0,
                    max_retries INTEGER DEFAULT 3,
                    timeout_seconds REAL DEFAULT 30.0,
                    evidence_json TEXT NOT NULL,
                    workspace_scope TEXT NOT NULL,
                    requires_confirmation INTEGER DEFAULT 0,
                    metadata_json TEXT NOT NULL
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS automation_executions (
                    run_id TEXT PRIMARY KEY,
                    automation_id TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    completed_at REAL,
                    success INTEGER NOT NULL,
                    output_summary TEXT,
                    error_message TEXT,
                    evidence_path TEXT,
                    FOREIGN KEY (automation_id) REFERENCES automations(automation_id) ON DELETE CASCADE
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_automations_status ON automations(status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_automations_next_run ON automations(next_run);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_automations_owner ON automations(owner_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_executions_automation ON automation_executions(automation_id);")

    def _sanitize_prompt(self, text: str) -> str:
        """Scrub secrets and PII from automation instructions using Phase 1 Guardrails."""
        if not text:
            return ""
        scrubbed = sanitize_secrets(text)
        if isinstance(scrubbed, str):
            sanitized_res = PromptGuardrails.sanitize_text(scrubbed)
            return sanitized_res.sanitized_text
        return str(scrubbed)

    def _detect_destructive(self, prompt: str) -> bool:
        """Deterministically detect if an instruction contains destructive operations."""
        words = re.findall(r"\b\w+\b", prompt.lower())
        return any(w in DESTRUCTIVE_VERBS for w in words)

    def create_automation(self, record: AutomationRecord) -> str:
        """
        Store a new automation record after scrubbing secrets and asserting destructive confirmation.
        """
        # 1. Scrub prompt and metadata
        record.prompt = self._sanitize_prompt(record.prompt)
        record.metadata = sanitize_secrets(record.metadata)

        # 2. Check for destructive verbs
        if self._detect_destructive(record.prompt):
            record.requires_confirmation = True

        # 3. Compute initial next_run if not set
        now = time.time()
        record.created_at = now
        record.updated_at = now
        if record.next_run is None:
            if record.schedule.schedule_type == ScheduleType.ONCE:
                record.next_run = record.schedule.run_at or now
            elif record.schedule.schedule_type == ScheduleType.INTERVAL:
                record.next_run = now + max(0.1, record.schedule.interval_seconds)

        with self._transaction() as conn:
            conn.execute("""
                INSERT INTO automations (
                    automation_id, owner_id, task_id, project_id, prompt,
                    schedule_json, status, created_at, updated_at, last_run,
                    next_run, failure_reason, execution_count, retry_count,
                    max_retries, timeout_seconds, evidence_json, workspace_scope,
                    requires_confirmation, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                record.automation_id,
                record.owner_id,
                record.task_id,
                record.project_id,
                record.prompt,
                json.dumps(record.schedule.to_dict()),
                record.status.value,
                record.created_at,
                record.updated_at,
                record.last_run,
                record.next_run,
                record.failure_reason,
                record.execution_count,
                record.retry_count,
                record.max_retries,
                record.timeout_seconds,
                json.dumps(record.evidence_references),
                record.workspace_scope,
                1 if record.requires_confirmation else 0,
                json.dumps(record.metadata),
            ))
        return record.automation_id

    def get_automation(self, automation_id: str) -> Optional[AutomationRecord]:
        """Retrieve an automation record by ID."""
        with self._transaction() as conn:
            row = conn.execute("SELECT * FROM automations WHERE automation_id = ?;", (automation_id,)).fetchone()
            if not row:
                return None
            return self._row_to_record(row)

    def list_automations(
        self,
        owner_id: Optional[str] = None,
        status: Optional[AutomationStatus] = None,
    ) -> List[AutomationRecord]:
        """List automation records with optional owner and status filtering."""
        query = "SELECT * FROM automations WHERE 1=1"
        params: List[Any] = []
        if owner_id:
            query += " AND owner_id = ?"
            params.append(owner_id)
        if status:
            query += " AND status = ?"
            params.append(status.value)
        query += " ORDER BY created_at DESC;"

        with self._transaction() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_record(r) for r in rows]

    def update_automation(self, record: AutomationRecord) -> bool:
        """Update an existing automation record."""
        record.updated_at = time.time()
        record.prompt = self._sanitize_prompt(record.prompt)
        record.metadata = sanitize_secrets(record.metadata)

        with self._transaction() as conn:
            cur = conn.execute("""
                UPDATE automations SET
                    owner_id = ?, task_id = ?, project_id = ?, prompt = ?,
                    schedule_json = ?, status = ?, updated_at = ?, last_run = ?,
                    next_run = ?, failure_reason = ?, execution_count = ?,
                    retry_count = ?, max_retries = ?, timeout_seconds = ?,
                    evidence_json = ?, workspace_scope = ?, requires_confirmation = ?,
                    metadata_json = ?
                WHERE automation_id = ?;
            """, (
                record.owner_id,
                record.task_id,
                record.project_id,
                record.prompt,
                json.dumps(record.schedule.to_dict()),
                record.status.value,
                record.updated_at,
                record.last_run,
                record.next_run,
                record.failure_reason,
                record.execution_count,
                record.retry_count,
                record.max_retries,
                record.timeout_seconds,
                json.dumps(record.evidence_references),
                record.workspace_scope,
                1 if record.requires_confirmation else 0,
                json.dumps(record.metadata),
                record.automation_id,
            ))
            return cur.rowcount > 0

    def pause_automation(self, automation_id: str, owner_id: Optional[str] = None) -> bool:
        """Pause an active automation."""
        rec = self.get_automation(automation_id)
        if not rec:
            return False
        if owner_id and rec.owner_id != owner_id and owner_id != "system":
            logger.warning(f"Pause rejected: caller '{owner_id}' does not own '{automation_id}'")
            return False
        rec.status = AutomationStatus.PAUSED
        return self.update_automation(rec)

    def resume_automation(self, automation_id: str, owner_id: Optional[str] = None) -> bool:
        """Resume a paused automation."""
        rec = self.get_automation(automation_id)
        if not rec:
            return False
        if owner_id and rec.owner_id != owner_id and owner_id != "system":
            logger.warning(f"Resume rejected: caller '{owner_id}' does not own '{automation_id}'")
            return False
        rec.status = AutomationStatus.ACTIVE
        # Refresh next run if in the past
        now = time.time()
        if rec.next_run and rec.next_run < now:
            if rec.schedule.schedule_type == ScheduleType.INTERVAL:
                rec.next_run = now + rec.schedule.interval_seconds
            else:
                rec.next_run = now
        return self.update_automation(rec)

    def cancel_automation(self, automation_id: str, owner_id: Optional[str] = None) -> bool:
        """Cancel an automation."""
        rec = self.get_automation(automation_id)
        if not rec:
            return False
        if owner_id and rec.owner_id != owner_id and owner_id != "system":
            logger.warning(f"Cancel rejected: caller '{owner_id}' does not own '{automation_id}'")
            return False
        rec.status = AutomationStatus.CANCELLED
        rec.next_run = None
        return self.update_automation(rec)

    def record_execution(self, exec_rec: AutomationExecutionRecord) -> bool:
        """Log an execution outcome in the audit history and update the parent automation."""
        exec_rec.output_summary = self._sanitize_prompt(exec_rec.output_summary)
        if exec_rec.error_message:
            exec_rec.error_message = self._sanitize_prompt(exec_rec.error_message)

        with self._transaction() as conn:
            conn.execute("""
                INSERT INTO automation_executions (
                    run_id, automation_id, started_at, completed_at,
                    success, output_summary, error_message, evidence_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                exec_rec.run_id,
                exec_rec.automation_id,
                exec_rec.started_at,
                exec_rec.completed_at or time.time(),
                1 if exec_rec.success else 0,
                exec_rec.output_summary,
                exec_rec.error_message,
                exec_rec.evidence_path,
            ))

            # Update parent automation counters
            parent = conn.execute("SELECT * FROM automations WHERE automation_id = ?;", (exec_rec.automation_id,)).fetchone()
            if parent:
                new_exec_count = parent["execution_count"] + 1
                new_retry_count = 0 if exec_rec.success else parent["retry_count"] + 1
                failure_reason = None if exec_rec.success else exec_rec.error_message
                now = time.time()
                conn.execute("""
                    UPDATE automations SET
                        last_run = ?,
                        execution_count = ?,
                        retry_count = ?,
                        failure_reason = ?,
                        updated_at = ?
                    WHERE automation_id = ?;
                """, (now, new_exec_count, new_retry_count, failure_reason, now, exec_rec.automation_id))
            return True

    def get_execution_history(self, automation_id: str, limit: int = 50) -> List[AutomationExecutionRecord]:
        """Fetch execution history for an automation."""
        with self._transaction() as conn:
            rows = conn.execute("""
                SELECT * FROM automation_executions
                WHERE automation_id = ?
                ORDER BY started_at DESC
                LIMIT ?;
            """, (automation_id, limit)).fetchall()
            history: List[AutomationExecutionRecord] = []
            for r in rows:
                history.append(AutomationExecutionRecord(
                    run_id=r["run_id"],
                    automation_id=r["automation_id"],
                    started_at=r["started_at"],
                    completed_at=r["completed_at"],
                    success=bool(r["success"]),
                    output_summary=r["output_summary"] or "",
                    error_message=r["error_message"],
                    evidence_path=r["evidence_path"],
                ))
            return history

    def recover_on_boot(self) -> int:
        """
        Recover automations on process startup:
        - Identify any tasks that were marked active but missed scheduled ticks while offline.
        - Enforce that tasks with destructive actions NEVER silently auto-execute without explicit re-confirmation.
        - Recalculate clean next_run timestamps.
        """
        now = time.time()
        recovered_count = 0
        with self._transaction() as conn:
            rows = conn.execute("""
                SELECT * FROM automations
                WHERE status = ?;
            """, (AutomationStatus.ACTIVE.value,)).fetchall()

            for row in rows:
                rec = self._row_to_record(row)
                needs_update = False

                # If task requires confirmation, ensure it remains guarded and cannot silently fire
                if rec.requires_confirmation:
                    rec.failure_reason = "RECOVERY_BARRIER: Destructive operation paused across restart awaiting explicit confirmation."
                    rec.status = AutomationStatus.PAUSED
                    needs_update = True
                elif rec.next_run and rec.next_run < now:
                    # Catch-up: align to current time for next tick
                    if rec.schedule.schedule_type == ScheduleType.INTERVAL:
                        rec.next_run = now + 1.0
                    elif rec.schedule.schedule_type == ScheduleType.ONCE:
                        rec.next_run = now + 0.5
                    needs_update = True

                if needs_update:
                    conn.execute("""
                        UPDATE automations SET
                            status = ?, next_run = ?, failure_reason = ?, updated_at = ?
                        WHERE automation_id = ?;
                    """, (rec.status.value, rec.next_run, rec.failure_reason, now, rec.automation_id))
                    recovered_count += 1

        logger.info(f"Automation recovery on boot: processed {recovered_count} automations.")
        return recovered_count

    def _row_to_record(self, row: sqlite3.Row) -> AutomationRecord:
        """Convert a database row into an AutomationRecord."""
        try:
            sched_dict = json.loads(row["schedule_json"])
            schedule = ScheduleConfig.from_dict(sched_dict)
        except Exception:
            schedule = ScheduleConfig()

        try:
            evidence = json.loads(row["evidence_json"])
        except Exception:
            evidence = []

        try:
            metadata = json.loads(row["metadata_json"])
        except Exception:
            metadata = {}

        return AutomationRecord(
            automation_id=row["automation_id"],
            owner_id=row["owner_id"],
            task_id=row["task_id"],
            project_id=row["project_id"],
            prompt=row["prompt"],
            schedule=schedule,
            status=AutomationStatus(row["status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_run=row["last_run"],
            next_run=row["next_run"],
            failure_reason=row["failure_reason"],
            execution_count=row["execution_count"],
            retry_count=row["retry_count"],
            max_retries=row["max_retries"],
            timeout_seconds=row["timeout_seconds"],
            evidence_references=evidence,
            workspace_scope=row["workspace_scope"],
            requires_confirmation=bool(row["requires_confirmation"]),
            metadata=metadata,
        )
