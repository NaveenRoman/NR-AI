"""
NR-AI Persistent Project Engineering Memory Engine.

Maintains project-scoped engineering memory across sessions in SQLite:
  - Modules, build variants, important symbols
  - Known build and runtime issues
  - Known repaired defects with verified patch patterns
  - Successful and failed repair patterns
  - Test and dependency evolution history

Strict Security Guarantee:
  - Enforces mandatory secret and token redaction before any persistence
  - Strict project isolation (sandboxed by project_id)
"""

from dataclasses import dataclass, field, asdict
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from app.agent.android_code_repair import redact_sensitive_content
from app.agent.droid_task_state import DEFAULT_DB_PATH

logger = logging.getLogger("NRAI.AndroidProjectMemory")


@dataclass
class ProjectMemoryRecord:
    project_id: str
    modules: List[str] = field(default_factory=list)
    build_variants: List[str] = field(default_factory=lambda: ["debug", "release"])
    known_build_issues: List[Dict[str, Any]] = field(default_factory=list)
    known_runtime_issues: List[Dict[str, Any]] = field(default_factory=list)
    known_repaired_defects: List[Dict[str, Any]] = field(default_factory=list)
    important_symbols: List[str] = field(default_factory=list)
    test_history: List[Dict[str, Any]] = field(default_factory=list)
    dependency_history: List[Dict[str, Any]] = field(default_factory=list)
    successful_repair_patterns: List[Dict[str, Any]] = field(default_factory=list)
    failed_repair_patterns: List[Dict[str, Any]] = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AndroidProjectMemoryStore:
    """SQLite-backed persistent store for Android project engineering memory."""

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
                    CREATE TABLE IF NOT EXISTS project_engineering_memory (
                        project_id TEXT PRIMARY KEY,
                        modules TEXT NOT NULL,
                        build_variants TEXT NOT NULL,
                        known_build_issues TEXT NOT NULL,
                        known_runtime_issues TEXT NOT NULL,
                        known_repaired_defects TEXT NOT NULL,
                        important_symbols TEXT NOT NULL,
                        test_history TEXT NOT NULL,
                        dependency_history TEXT NOT NULL,
                        successful_repair_patterns TEXT NOT NULL,
                        failed_repair_patterns TEXT NOT NULL,
                        updated_at REAL NOT NULL
                    );
                """)
        finally:
            if str(self.db_path) != ":memory:":
                conn.close()

    def _sanitize_data(self, obj: Any) -> str:
        """Serializes to JSON and scrubs any credential, token, or secret."""
        raw_json = json.dumps(obj)
        redacted = redact_sensitive_content(raw_json)
        return redacted

    def save_memory(self, record: ProjectMemoryRecord) -> bool:
        """Saves or updates a project memory record."""
        record.updated_at = time.time()
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("""
                    INSERT INTO project_engineering_memory (
                        project_id, modules, build_variants,
                        known_build_issues, known_runtime_issues, known_repaired_defects,
                        important_symbols, test_history, dependency_history,
                        successful_repair_patterns, failed_repair_patterns, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(project_id) DO UPDATE SET
                        modules = excluded.modules,
                        build_variants = excluded.build_variants,
                        known_build_issues = excluded.known_build_issues,
                        known_runtime_issues = excluded.known_runtime_issues,
                        known_repaired_defects = excluded.known_repaired_defects,
                        important_symbols = excluded.important_symbols,
                        test_history = excluded.test_history,
                        dependency_history = excluded.dependency_history,
                        successful_repair_patterns = excluded.successful_repair_patterns,
                        failed_repair_patterns = excluded.failed_repair_patterns,
                        updated_at = excluded.updated_at;
                """, (
                    record.project_id,
                    self._sanitize_data(record.modules),
                    self._sanitize_data(record.build_variants),
                    self._sanitize_data(record.known_build_issues),
                    self._sanitize_data(record.known_runtime_issues),
                    self._sanitize_data(record.known_repaired_defects),
                    self._sanitize_data(record.important_symbols),
                    self._sanitize_data(record.test_history),
                    self._sanitize_data(record.dependency_history),
                    self._sanitize_data(record.successful_repair_patterns),
                    self._sanitize_data(record.failed_repair_patterns),
                    record.updated_at,
                ))
            return True
        except Exception as e:
            logger.error("Failed to save project memory for %s: %s", record.project_id, e)
            return False
        finally:
            if str(self.db_path) != ":memory:":
                conn.close()

    def load_memory(self, project_id: str) -> ProjectMemoryRecord:
        """Loads or initializes project engineering memory for the given project_id."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT modules, build_variants, known_build_issues, known_runtime_issues,
                       known_repaired_defects, important_symbols, test_history, dependency_history,
                       successful_repair_patterns, failed_repair_patterns, updated_at
                FROM project_engineering_memory WHERE project_id = ?
            """, (project_id,))
            row = cursor.fetchone()
            if not row:
                return ProjectMemoryRecord(project_id=project_id)

            return ProjectMemoryRecord(
                project_id=project_id,
                modules=json.loads(row[0]),
                build_variants=json.loads(row[1]),
                known_build_issues=json.loads(row[2]),
                known_runtime_issues=json.loads(row[3]),
                known_repaired_defects=json.loads(row[4]),
                important_symbols=json.loads(row[5]),
                test_history=json.loads(row[6]),
                dependency_history=json.loads(row[7]),
                successful_repair_patterns=json.loads(row[8]),
                failed_repair_patterns=json.loads(row[9]),
                updated_at=row[10],
            )
        except Exception as e:
            logger.error("Failed to load project memory for %s: %s", project_id, e)
            return ProjectMemoryRecord(project_id=project_id)
        finally:
            if str(self.db_path) != ":memory:":
                conn.close()

    def record_successful_repair(
        self,
        project_id: str,
        defect_type: str,
        file_path: str,
        patch_summary: str,
    ) -> None:
        """Records an empirically verified repair pattern."""
        mem = self.load_memory(project_id)
        entry = {
            "defect_type": defect_type,
            "file": Path(file_path).name,
            "summary": patch_summary,
            "timestamp": time.time(),
        }
        mem.successful_repair_patterns.append(entry)
        mem.known_repaired_defects.append(entry)
        # Cap list lengths to keep memory bounded
        mem.successful_repair_patterns = mem.successful_repair_patterns[-50:]
        mem.known_repaired_defects = mem.known_repaired_defects[-50:]
        self.save_memory(mem)

    def record_failed_repair(
        self,
        project_id: str,
        defect_type: str,
        reason: str,
    ) -> None:
        """Records a failed repair attempt to avoid repeating ineffective patches."""
        mem = self.load_memory(project_id)
        entry = {
            "defect_type": defect_type,
            "reason": reason,
            "timestamp": time.time(),
        }
        mem.failed_repair_patterns.append(entry)
        mem.failed_repair_patterns = mem.failed_repair_patterns[-50:]
        self.save_memory(mem)

    def record_test_result(
        self,
        project_id: str,
        test_name: str,
        passed: bool,
        error: Optional[str] = None,
    ) -> None:
        """Records test execution history."""
        mem = self.load_memory(project_id)
        entry = {
            "test_name": test_name,
            "passed": passed,
            "error": error[:200] if error else None,
            "timestamp": time.time(),
        }
        mem.test_history.append(entry)
        mem.test_history = mem.test_history[-100:]
        self.save_memory(mem)

    def get_relevant_repair_patterns(
        self,
        project_id: str,
        defect_type: str,
    ) -> List[Dict[str, Any]]:
        """Queries verified repair patterns relevant to a defect type."""
        mem = self.load_memory(project_id)
        return [
            p for p in mem.successful_repair_patterns
            if p.get("defect_type") == defect_type or defect_type in p.get("defect_type", "")
        ]

    def clear_memory(self, project_id: str) -> bool:
        """Wipes memory for a specific project."""
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("DELETE FROM project_engineering_memory WHERE project_id = ?", (project_id,))
            return True
        except Exception as e:
            logger.error("Failed clearing memory for %s: %s", project_id, e)
            return False
        finally:
            if str(self.db_path) != ":memory:":
                conn.close()
