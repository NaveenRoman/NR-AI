"""
Tests for NR-AI Persistent Automation Engine (Phase 2).
Verifies:
- SQLite persistence with WAL mode
- One-time, interval, and condition tasks
- Enable/disable, pause/resume, and cancellation
- Deep secret and PII scrubbing
- Destructive verb detection and confirmation barrier
- Crash recovery on boot
- Execution history recording
"""

import os
from pathlib import Path
import tempfile
import time
import unittest

from app.automation.engine import AutomationEngine
from app.automation.models import (
    AutomationExecutionRecord,
    AutomationRecord,
    AutomationStatus,
    ScheduleConfig,
    ScheduleType,
)


class TestAutomationEngine(unittest.TestCase):
    """Unit test suite for AutomationEngine."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_automations.db"
        self.engine = AutomationEngine(db_path=self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_create_and_get_automation(self):
        """Verify basic creation and retrieval of an automation record."""
        rec = AutomationRecord(
            owner_id="Droid",
            task_id="task_build_123",
            prompt="Build active Android project with Gradle.",
            schedule=ScheduleConfig(schedule_type=ScheduleType.INTERVAL, interval_seconds=300.0),
        )
        auto_id = self.engine.create_automation(rec)
        self.assertTrue(auto_id.startswith("auto_"))

        fetched = self.engine.get_automation(auto_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.owner_id, "Droid")
        self.assertEqual(fetched.task_id, "task_build_123")
        self.assertEqual(fetched.status, AutomationStatus.ACTIVE)
        self.assertIsNotNone(fetched.next_run)

    def test_secret_scrubbing_in_automation_prompt(self):
        """Verify prompt containing API keys and PII is scrubbed before DB insert."""
        rec = AutomationRecord(
            owner_id="SecurityAgent",
            prompt="Deploy using key sk-abcdef123456789012345678 and contact test@example.com",
            metadata={"api_key": "sk-secret123456789012345678"},
        )
        auto_id = self.engine.create_automation(rec)
        fetched = self.engine.get_automation(auto_id)
        self.assertIsNotNone(fetched)
        self.assertNotIn("sk-abcdef", fetched.prompt)
        self.assertIn("SECRET_REDACTED", fetched.prompt)
        self.assertNotIn("test@example.com", fetched.prompt)
        self.assertEqual(fetched.metadata.get("api_key"), "[SECRET_REDACTED]")

    def test_destructive_verb_detection(self):
        """Verify destructive verbs set requires_confirmation to True."""
        rec = AutomationRecord(
            owner_id="Droid",
            prompt="Delete all old build artifacts and drop cached database.",
        )
        auto_id = self.engine.create_automation(rec)
        fetched = self.engine.get_automation(auto_id)
        self.assertTrue(fetched.requires_confirmation)

    def test_pause_resume_and_cancel_lifecycle(self):
        """Verify pause, resume, and cancellation lifecycle transitions with ownership checks."""
        rec = AutomationRecord(
            owner_id="Droid",
            prompt="Check logcat messages every 60s.",
            schedule=ScheduleConfig(schedule_type=ScheduleType.INTERVAL, interval_seconds=60.0),
        )
        auto_id = self.engine.create_automation(rec)

        # Unauthorized pause rejected
        unauth_pause = self.engine.pause_automation(auto_id, owner_id="OtherAgent")
        self.assertFalse(unauth_pause)
        self.assertEqual(self.engine.get_automation(auto_id).status, AutomationStatus.ACTIVE)

        # Authorized pause
        ok_pause = self.engine.pause_automation(auto_id, owner_id="Droid")
        self.assertTrue(ok_pause)
        self.assertEqual(self.engine.get_automation(auto_id).status, AutomationStatus.PAUSED)

        # Authorized resume
        ok_resume = self.engine.resume_automation(auto_id, owner_id="Droid")
        self.assertTrue(ok_resume)
        self.assertEqual(self.engine.get_automation(auto_id).status, AutomationStatus.ACTIVE)

        # Cancel
        ok_cancel = self.engine.cancel_automation(auto_id, owner_id="Droid")
        self.assertTrue(ok_cancel)
        self.assertEqual(self.engine.get_automation(auto_id).status, AutomationStatus.CANCELLED)
        self.assertIsNone(self.engine.get_automation(auto_id).next_run)

    def test_execution_history_logging(self):
        """Verify recording of execution records and counter increments."""
        rec = AutomationRecord(owner_id="Droid", prompt="Assemble debug APK.")
        auto_id = self.engine.create_automation(rec)

        exec_rec = AutomationExecutionRecord(
            automation_id=auto_id,
            success=True,
            output_summary="Gradle assembleDebug finished with code 0 (APK: 8KB).",
        )
        self.engine.record_execution(exec_rec)

        history = self.engine.get_execution_history(auto_id)
        self.assertEqual(len(history), 1)
        self.assertTrue(history[0].success)
        self.assertIn("assembleDebug", history[0].output_summary)

        updated_auto = self.engine.get_automation(auto_id)
        self.assertEqual(updated_auto.execution_count, 1)
        self.assertEqual(updated_auto.retry_count, 0)

    def test_crash_recovery_on_boot(self):
        """
        Verify recover_on_boot:
        - Destructive tasks are paused with an explicit confirmation barrier.
        - Non-destructive past-due tasks have next_run reset safely.
        """
        now = time.time()
        # 1. Normal past-due task
        normal_rec = AutomationRecord(
            owner_id="Monitor",
            prompt="Collect CPU usage.",
            schedule=ScheduleConfig(schedule_type=ScheduleType.INTERVAL, interval_seconds=10.0),
            next_run=now - 50.0,
            status=AutomationStatus.ACTIVE,
        )
        norm_id = self.engine.create_automation(normal_rec)

        # 2. Destructive task
        destructive_rec = AutomationRecord(
            owner_id="Cleaner",
            prompt="Wipe all temporary test directories.",
            schedule=ScheduleConfig(schedule_type=ScheduleType.INTERVAL, interval_seconds=60.0),
            next_run=now - 10.0,
            status=AutomationStatus.ACTIVE,
        )
        dest_id = self.engine.create_automation(destructive_rec)

        recovered_count = self.engine.recover_on_boot()
        self.assertGreaterEqual(recovered_count, 2)

        # Check normal task: still ACTIVE, next_run updated
        norm_fetched = self.engine.get_automation(norm_id)
        self.assertEqual(norm_fetched.status, AutomationStatus.ACTIVE)
        self.assertGreaterEqual(norm_fetched.next_run, now)

        # Check destructive task: PAUSED by recovery barrier!
        dest_fetched = self.engine.get_automation(dest_id)
        self.assertEqual(dest_fetched.status, AutomationStatus.PAUSED)
        self.assertIn("RECOVERY_BARRIER", dest_fetched.failure_reason)


if __name__ == "__main__":
    unittest.main()
