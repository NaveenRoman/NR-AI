"""
Dedicated Unit & Integration Tests: Shared SQLite Task Checkpoint Store.
Tests atomic checkpointing, crash recovery simulation, deterministic barriers, and secret scrubbing.
"""

import os
from pathlib import Path
import tempfile
import time
import unittest

from app.task.checkpoint_store import (
    DESTRUCTIVE_VERBS,
    SharedTaskStatus,
    TaskCheckpointStore,
    TaskRecord,
    sanitize_secrets,
)


class TestTaskCheckpointStore(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_tasks.db"
        self.store = TaskCheckpointStore(db_path=self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_create_and_fetch_task(self):
        """Test task creation and field retrieval."""
        task = self.store.create_task(
            task_id="TASK-001",
            agent_id="DroidAgent",
            project_id="NR-AI",
            initial_steps=[{"step": 1, "action": "inspect_project"}],
            workspace_context={"root": "C:\\NR-AI"},
            checkpoint={"files_indexed": 42},
        )
        self.assertEqual(task.task_id, "TASK-001")
        self.assertEqual(task.status, SharedTaskStatus.CREATED)
        self.assertEqual(len(task.pending_steps), 1)

        fetched = self.store.get_task("TASK-001")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.agent_id, "DroidAgent")
        self.assertEqual(fetched.checkpoint.get("files_indexed"), 42)

    def test_checkpoint_update(self):
        """Test step progression and checkpoint snapshot update."""
        self.store.create_task(
            task_id="TASK-002",
            agent_id="ArchitectAgent",
            project_id="NR-AI",
            initial_steps=[
                {"step": 1, "action": "design"},
                {"step": 2, "action": "code"},
            ],
        )

        updated = self.store.update_checkpoint(
            task_id="TASK-002",
            current_step=1,
            checkpoint_data={"active_module": "model_catalog"},
            completed_step={"step": 1, "action": "design", "result": "done"},
            evidence_reference="evidence/design_doc.md",
            status=SharedTaskStatus.RUNNING,
        )
        self.assertIsNotNone(updated)
        self.assertEqual(updated.current_step, 1)
        self.assertEqual(updated.status, SharedTaskStatus.RUNNING)
        self.assertEqual(len(updated.completed_steps), 1)
        self.assertEqual(len(updated.pending_steps), 1)
        self.assertIn("evidence/design_doc.md", updated.evidence_references)

    def test_secret_scrubbing_in_checkpoints(self):
        """Secrets must never be stored in plaintext in the database."""
        raw_checkpoint = {
            "api_key": "sk-1234567890abcdef1234567890abcdef",
            "bearer_header": "Bearer secret_token_xyz_123456789",
            "safe_param": "compiler_flag_ok",
        }
        task = self.store.create_task(
            task_id="TASK-SECRET",
            agent_id="TestAgent",
            project_id="NR-AI",
            checkpoint=raw_checkpoint,
        )
        fetched = self.store.get_task("TASK-SECRET")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.checkpoint["api_key"], "[SECRET_REDACTED]")
        self.assertNotIn("sk-1234567890abcdef", str(fetched.checkpoint))
        self.assertEqual(fetched.checkpoint["safe_param"], "compiler_flag_ok")

    def test_deterministic_recovery_barrier(self):
        """Destructive steps cannot be silently resumed; must require confirmation."""
        task = self.store.create_task(
            task_id="TASK-DESTRUCTIVE",
            agent_id="DroidAgent",
            project_id="NR-AI",
            initial_steps=[
                {"step": 1, "action": "delete_database", "description": "drop table users"},
            ],
        )
        self.store.set_task_status("TASK-DESTRUCTIVE", SharedTaskStatus.RECOVERABLE)

        can_resume, reason, next_step = self.store.evaluate_recovery("TASK-DESTRUCTIVE")
        self.assertFalse(can_resume)
        self.assertIn("REQUIRES_CONFIRMATION", reason)

        # Attempting resume without confirmation must fail
        ok, res_msg, _ = self.store.resume_task("TASK-DESTRUCTIVE", task.resume_token, confirmation_granted=False)
        self.assertFalse(ok)

        # Resuming with explicit confirmation must succeed
        ok2, res_msg2, updated = self.store.resume_task("TASK-DESTRUCTIVE", task.resume_token, confirmation_granted=True)
        self.assertTrue(ok2)
        self.assertEqual(updated.status, SharedTaskStatus.RUNNING)

    def test_safe_step_auto_resumption(self):
        """Non-destructive, safe steps evaluate as safe for resumption."""
        task = self.store.create_task(
            task_id="TASK-SAFE",
            agent_id="ResearchAgent",
            project_id="NR-AI",
            initial_steps=[
                {"step": 1, "action": "read_documentation", "description": "check openjarvis toml"},
            ],
        )
        self.store.set_task_status("TASK-SAFE", SharedTaskStatus.RECOVERABLE)

        can_resume, reason, _ = self.store.evaluate_recovery("TASK-SAFE")
        self.assertTrue(can_resume)
        self.assertIn("RECOVERABLE_SAFE", reason)

        ok, _, updated = self.store.resume_task("TASK-SAFE", task.resume_token)
        self.assertTrue(ok)
        self.assertEqual(updated.status, SharedTaskStatus.RUNNING)

    def test_stale_task_detection(self):
        """Tasks running but un-updated past timeout must be detected as stale."""
        task = self.store.create_task(
            task_id="TASK-ZOMBIE",
            agent_id="WorkerAgent",
            project_id="NR-AI",
        )
        self.store.set_task_status("TASK-ZOMBIE", SharedTaskStatus.RUNNING)

        # Backdate updated_at by 1000 seconds
        with self.store._lock:
            with self.store._connection() as conn:
                conn.execute(
                    "UPDATE shared_tasks SET updated_at = ? WHERE task_id = ?",
                    (time.time() - 1000, "TASK-ZOMBIE"),
                )

        stale_tasks = self.store.find_stale_tasks(timeout_seconds=600)
        self.assertEqual(len(stale_tasks), 1)
        self.assertEqual(stale_tasks[0].task_id, "TASK-ZOMBIE")


if __name__ == "__main__":
    unittest.main()
