"""
Tests for Droid Phase 3: Persistent Task Recovery.
"""

import tempfile
import unittest
from pathlib import Path
from app.agent.droid_task_state import DroidTaskStateStore, TaskState, TaskStatus


class TestDroidPhase3TaskRecovery(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_recovery.db"
        self.store = DroidTaskStateStore(db_path=self.db_path)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_task_state_19_enums(self):
        expected_states = [
            "IDLE", "INSPECTING", "REPRODUCING", "COLLECTING_EVIDENCE", "DIAGNOSING",
            "PLANNING_REPAIR", "VALIDATING_REPAIR", "REPAIRING", "BUILDING", "DEPLOYING",
            "LAUNCHING", "VERIFYING", "TESTING", "REGRESSION_CHECK", "COMPLETED", "FAILED",
            "STOPPED", "BLOCKED", "ROLLING_BACK",
        ]
        for st in expected_states:
            self.assertTrue(hasattr(TaskState, st))
            self.assertEqual(getattr(TaskState, st).value, st)

    def test_task_recovery_destructive_action_guard(self):
        task = self.store.create_task(
            project_id="nr_android_test",
            workflow="DESTRUCTIVE_CLEAN",
            initial_steps=["delete_all_databases", "verify"],
        )
        res = self.store.resume_task(task.task_id)
        self.assertFalse(res.can_resume)
        self.assertTrue(res.requires_confirmation)
        self.assertEqual(res.status, TaskStatus.REQUIRES_CONFIRMATION.value)

    def test_task_recovery_safe_steps_resume(self):
        task = self.store.create_task(
            project_id="nr_android_test",
            workflow="SAFE_INSPECTION",
            initial_steps=["inspect_hierarchy", "capture_logcat", "verify_screen"],
        )
        res = self.store.resume_task(task.task_id)
        self.assertTrue(res.can_resume)
        self.assertFalse(res.requires_confirmation)


if __name__ == "__main__":
    unittest.main()
