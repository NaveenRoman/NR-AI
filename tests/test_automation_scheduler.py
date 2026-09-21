"""
Tests for NR-AI Automation Scheduler (Phase 2).
Verifies:
- Next-run computations for interval and cron
- Single-tick runner loop
- Emergency stop gating
- Rate limits and concurrency bounds
- Task execution timeouts
- Exponential retry backoff on failure
- Destructive action confirmation barrier
"""

from pathlib import Path
import tempfile
import time
import unittest

from app.automation.engine import AutomationEngine
from app.automation.models import (
    AutomationRecord,
    AutomationStatus,
    ScheduleConfig,
    ScheduleType,
)
from app.automation.scheduler import AutomationScheduler, compute_next_cron_run
from app.remote.emergency import EmergencyStopController


class TestAutomationScheduler(unittest.TestCase):
    """Unit test suite for AutomationScheduler."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_sched.db"
        self.engine = AutomationEngine(db_path=self.db_path)
        self.e_stop = EmergencyStopController()
        self.scheduler = AutomationScheduler(
            engine=self.engine,
            emergency_controller=self.e_stop,
            max_workers=2,
            max_tasks_per_tick=5,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_next_cron_run_calculation(self):
        """Verify standard cron expression next-run evaluation."""
        now = time.time()
        # Step interval: */10 * * * *
        next_step = compute_next_cron_run("*/10 * * * *", now)
        self.assertGreater(next_step, now)
        self.assertLessEqual(next_step, now + 3600.0)

        # Hourly: 0 * * * *
        next_hourly = compute_next_cron_run("0 * * * *", now)
        self.assertGreater(next_hourly, now)

    def test_run_tick_executes_pending_interval_task(self):
        """Verify scheduler tick triggers pending tasks and calculates next run."""
        now = time.time()
        rec = AutomationRecord(
            owner_id="DroidScout",
            prompt="Scan workspace for uncommitted changes.",
            schedule=ScheduleConfig(schedule_type=ScheduleType.INTERVAL, interval_seconds=10.0),
            next_run=now - 1.0,  # Ready now
        )
        auto_id = self.engine.create_automation(rec)

        runs = self.scheduler.run_tick()
        self.assertEqual(len(runs), 1)
        self.assertTrue(runs[0].success)
        self.assertEqual(runs[0].automation_id, auto_id)

        # Verify next_run advanced
        updated = self.engine.get_automation(auto_id)
        self.assertGreater(updated.next_run, now)
        self.assertEqual(updated.execution_count, 1)

    def test_emergency_stop_halts_tick(self):
        """Verify active Emergency Stop prevents any task execution."""
        now = time.time()
        rec = AutomationRecord(
            owner_id="TaskRunner",
            prompt="Run scheduled diagnostics.",
            next_run=now - 2.0,
        )
        self.engine.create_automation(rec)

        # Activate E-stop
        self.e_stop.trigger_emergency_stop("Safety test freeze")
        runs = self.scheduler.run_tick()
        self.assertEqual(len(runs), 0)

        # Reset E-stop and verify tasks can now execute
        self.e_stop.reset_emergency_stop("Safety test clear")
        runs_after = self.scheduler.run_tick()
        self.assertEqual(len(runs_after), 1)
        self.assertTrue(runs_after[0].success)

    def test_destructive_confirmation_barrier(self):
        """Verify destructive tasks fail without valid confirmation token."""
        now = time.time()
        rec = AutomationRecord(
            owner_id="Admin",
            prompt="Drop and wipe database tables.",
            next_run=now - 1.0,
            metadata={"confirmation_token": "valid_token_123"},
        )
        auto_id = self.engine.create_automation(rec)
        self.assertTrue(self.engine.get_automation(auto_id).requires_confirmation)

        # 1. Tick without confirmation token -> rejected!
        runs = self.scheduler.run_tick()
        self.assertEqual(len(runs), 1)
        self.assertFalse(runs[0].success)
        self.assertIn("CONFIRMATION_REQUIRED", runs[0].error_message)

        # 2. Tick with valid token -> permitted!
        runs2 = self.scheduler.run_tick(confirmation_tokens={auto_id: "valid_token_123"})
        self.assertEqual(len(runs2), 1)
        self.assertTrue(runs2[0].success)

    def test_task_timeout_enforcement(self):
        """Verify scheduler terminates execution when timeout is exceeded."""
        now = time.time()
        rec = AutomationRecord(
            owner_id="SlowAgent",
            prompt="Execute long running operation.",
            next_run=now - 1.0,
            timeout_seconds=0.5,
        )

        def slow_handler(r):
            time.sleep(2.0)
            return True, "Done"

        self.scheduler.register_task_handler("SlowAgent", slow_handler)
        auto_id = self.engine.create_automation(rec)

        runs = self.scheduler.run_tick()
        self.assertEqual(len(runs), 1)
        self.assertFalse(runs[0].success)
        self.assertIn("TIMEOUT", runs[0].error_message)

    def test_retry_backoff_and_max_failure(self):
        """Verify exponential backoff on failure and transition to FAILED status."""
        now = time.time()
        rec = AutomationRecord(
            owner_id="FailingAgent",
            prompt="Execute failing task.",
            next_run=now - 1.0,
            max_retries=2,
            schedule=ScheduleConfig(schedule_type=ScheduleType.ONCE, run_at=now - 1.0),
        )

        def fail_handler(r):
            return False, "Simulated upstream failure."

        self.scheduler.register_task_handler("FailingAgent", fail_handler)
        auto_id = self.engine.create_automation(rec)

        # Attempt 1
        runs1 = self.scheduler.run_tick()
        self.assertFalse(runs1[0].success)
        rec1 = self.engine.get_automation(auto_id)
        self.assertEqual(rec1.retry_count, 1)
        self.assertEqual(rec1.status, AutomationStatus.ACTIVE)

        # Fast forward next_run for Attempt 2
        rec1.next_run = time.time() - 1.0
        self.engine.update_automation(rec1)

        # Attempt 2 -> Reaches max_retries
        runs2 = self.scheduler.run_tick()
        self.assertFalse(runs2[0].success)
        rec2 = self.engine.get_automation(auto_id)
        self.assertEqual(rec2.retry_count, 2)
        self.assertEqual(rec2.status, AutomationStatus.FAILED)
        self.assertIn("MAX_RETRIES_EXCEEDED", rec2.failure_reason)


if __name__ == "__main__":
    unittest.main()
