"""
Tests for NR-AI Pre-Droid Foundation Hardening

Validates:
  1. Companion routing aliases ("Droid", "Android", "AndroidStudio", "AndroidStudioAgent")
  2. Persistent SQLite task state subsystem (CREATE, UPDATE, CHECKPOINT, RESUME, FAIL, COMPLETE, CANCEL)
  3. Safe resumption: destructive actions blocked from auto-resume without confirmation
  4. Secret exclusion: automatic redaction of API keys and bearer tokens
  5. Android host environment inspection and AVD readiness verification
  6. Offline handling & timeout handling
  7. Authorized AVD and device policy enforcement (Pixel_6_API_34 allowed, Pixel_6_API_35 blocked)
  8. Emergency Stop integration and abort enforcement
"""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.agent.android_readiness import (
    AndroidHostReadinessChecker,
    AvdReadinessState,
    AvdStatusReport,
)
from app.agent.android_safety import (
    AndroidErrorCode,
    AndroidSafetyError,
    AndroidSafetyGate,
    EmergencyStopActiveError,
    AUTHORIZED_AVDS,
    BLOCKED_AVDS,
)
from app.agent.droid_task_state import (
    DroidResumeResult,
    DroidTaskRecord,
    DroidTaskStateStore,
    TaskState,
    TaskStatus,
    sanitize_value,
)
from app.brain.companion import CommandCategory, NRCompanion


class TestPreDroidFoundation(unittest.TestCase):
    """Test suite for Pre-Droid Foundation Hardening."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_tasks.db"
        self.store = DroidTaskStateStore(self.db_path)
        self.safety = AndroidSafetyGate()
        self.readiness = AndroidHostReadinessChecker(safety_gate=self.safety)

    def tearDown(self):
        self.tmp_dir.cleanup()

    # -------------------------------------------------------------------------
    # 1. Routing Aliases
    # -------------------------------------------------------------------------

    def test_01_routing_aliases_resolve_to_same_specialist(self):
        """Verify 'Droid', 'Android', 'AndroidStudio', 'AndroidStudioAgent' resolve consistently."""
        companion = NRCompanion()

        aliases = ["Droid", "Android", "AndroidStudio", "AndroidStudioAgent"]
        for alias in aliases:
            aid, fname, rem = companion.resolve_addressed_agent(alias)
            self.assertEqual(
                aid,
                "android_unified_agent",
                f"Alias '{alias}' did not resolve to 'android_unified_agent'. Got '{aid}'."
            )
            self.assertEqual(
                fname,
                "Droid",
                f"Alias '{alias}' did not resolve to friendly name 'Droid'. Got '{fname}'."
            )
            self.assertEqual(rem, "")

    def test_02_routing_prefixed_commands_route_to_same_workflow_handler(self):
        """Verify workflow commands prefixed with any alias route to AndroidStudioAgent handler."""
        companion = NRCompanion()

        # Test with each alias prefix
        commands = [
            "android: inspect project",
            "droid: inspect project",
            "androidstudio: inspect project",
            "androidstudioagent: inspect project",
        ]

        for cmd in commands:
            resp = companion.interact(cmd, speak_output=False)
            self.assertEqual(
                resp.category,
                CommandCategory.ANDROID_STUDIO,
                f"Command '{cmd}' category was {resp.category} instead of ANDROID_STUDIO."
            )
            self.assertEqual(
                resp.routed_to,
                "AndroidStudioAgent",
                f"Command '{cmd}' routed_to was '{resp.routed_to}' instead of 'AndroidStudioAgent'."
            )
            self.assertTrue(
                "completed successfully" in resp.text.lower() or "inspected" in resp.text.lower(),
                f"Command '{cmd}' unexpected response: {resp.text}"
            )

    def test_03_no_duplicate_android_agents(self):
        """Verify companion maintains exactly one unified Android specialist agent instance."""
        companion = NRCompanion()
        self.assertTrue(hasattr(companion, "android_agent"))
        # android_agent is the single specialist instance
        self.assertIsNotNone(companion.android_agent)

    # -------------------------------------------------------------------------
    # 2. Persistent Task State
    # -------------------------------------------------------------------------

    def test_04_task_creation(self):
        """Verify task creation persists structured state into SQLite."""
        steps = ["inspect_project", "compile_debug_apk", "run_unit_tests"]
        task = self.store.create_task(
            project_id="nr_android_test",
            workflow="build_and_test",
            initial_steps=steps,
            agent="Droid",
            initial_state=TaskState.INITIALIZED.value,
        )

        self.assertTrue(task.task_id.startswith("droid_task_"))
        self.assertEqual(task.project_id, "nr_android_test")
        self.assertEqual(task.agent, "Droid")
        self.assertEqual(task.workflow, "build_and_test")
        self.assertEqual(task.current_state, TaskState.INITIALIZED.value)
        self.assertEqual(task.current_step, "inspect_project")
        self.assertEqual(len(task.pending_steps), 3)
        self.assertEqual(len(task.completed_steps), 0)
        self.assertEqual(task.status, TaskStatus.PENDING.value)

        # Verify disk persistence via fresh query
        fetched = self.store.get_task(task.task_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.task_id, task.task_id)
        self.assertEqual(fetched.current_step, "inspect_project")

    def test_05_task_checkpoint(self):
        """Verify atomic task checkpoint advances steps and records checkpoint data."""
        task = self.store.create_task(
            project_id="nr_android_test",
            workflow="build_and_deploy",
            initial_steps=["inspect_project", "compile_debug_apk", "verify_output"],
        )

        # Checkpoint first step
        updated = self.store.checkpoint_task(
            task_id=task.task_id,
            checkpoint_data={"inspection_hash": "abc1234", "git_clean": True},
            completed_step="inspect_project",
            current_state=TaskState.BUILDING.value,
        )

        self.assertEqual(updated.current_state, TaskState.BUILDING.value)
        self.assertEqual(updated.current_step, "compile_debug_apk")
        self.assertEqual(len(updated.completed_steps), 1)
        self.assertEqual(updated.completed_steps[0]["name"], "inspect_project")
        self.assertEqual(len(updated.pending_steps), 2)
        self.assertEqual(updated.checkpoint["inspection_hash"], "abc1234")

    def test_06_task_resume_safe_action(self):
        """Verify safe non-destructive action can be resumed automatically."""
        task = self.store.create_task(
            project_id="nr_android_test",
            workflow="build_project",
            initial_steps=["inspect_project", "compile_debug_apk"],
        )

        res: DroidResumeResult = self.store.resume_task(task.task_id)
        self.assertTrue(res.can_resume)
        self.assertEqual(res.status, TaskStatus.RUNNING.value)
        self.assertFalse(res.requires_confirmation)
        self.assertEqual(res.next_step["name"], "inspect_project")

    def test_07_task_resume_blocks_destructive_action(self):
        """CRITICAL: Destructive action MUST NOT resume automatically without confirmation."""
        task = self.store.create_task(
            project_id="nr_android_test",
            workflow="clean_and_reset",
            initial_steps=["inspect_project", "wipe_data_factory_reset"],
        )

        # Mark first step completed
        self.store.checkpoint_task(task.task_id, {}, completed_step="inspect_project")

        # Attempt resumption on destructive step
        res: DroidResumeResult = self.store.resume_task(task.task_id)
        self.assertFalse(res.can_resume)
        self.assertTrue(res.requires_confirmation)
        self.assertEqual(res.status, TaskStatus.REQUIRES_CONFIRMATION.value)
        self.assertIn("destructive", res.reason.lower())

        # Verify DB state reflects REQUIRES_CONFIRMATION
        fetched = self.store.get_task(task.task_id)
        self.assertEqual(fetched.status, TaskStatus.REQUIRES_CONFIRMATION.value)

        # Override with explicit confirmation
        override_res = self.store.resume_task(task.task_id, allow_destructive_override=True)
        self.assertTrue(override_res.can_resume)
        self.assertEqual(override_res.status, TaskStatus.RUNNING.value)

    def test_08_task_failure(self):
        """Verify task failure updates status and records sanitized error message."""
        task = self.store.create_task("nr_android_test", "build", ["compile"])
        failed = self.store.fail_task(task.task_id, "CompilationError: unresolved reference 'Button'")
        self.assertEqual(failed.status, TaskStatus.FAILED.value)
        self.assertEqual(failed.current_state, TaskState.HALTED.value)
        self.assertIn("CompilationError", failed.last_error)

    def test_09_task_completion(self):
        """Verify task completion marks status COMPLETED and clears pending queue."""
        task = self.store.create_task("nr_android_test", "build", ["compile"])
        completed = self.store.complete_task(task.task_id, {"apk_size_bytes": 8892729})
        self.assertEqual(completed.status, TaskStatus.COMPLETED.value)
        self.assertEqual(completed.current_state, TaskState.DONE.value)
        self.assertEqual(completed.current_step, "finished")
        self.assertEqual(len(completed.pending_steps), 0)
        self.assertEqual(completed.checkpoint["completion_summary"]["apk_size_bytes"], 8892729)

    def test_10_task_cancellation(self):
        """Verify task cancellation marks status CANCELLED."""
        task = self.store.create_task("nr_android_test", "build", ["compile"])
        cancelled = self.store.cancel_task(task.task_id, "User invoked abort")
        self.assertEqual(cancelled.status, TaskStatus.CANCELLED.value)
        self.assertIn("CANCELLED", cancelled.last_error)

    def test_11_secret_exclusion(self):
        """Verify API keys and bearer tokens are redacted before persistence."""
        task = self.store.create_task(
            project_id="nr_android_test",
            workflow="cloud_sync",
            initial_steps=["sync_step"],
            metadata={
                "openai_key": "sk-1234567890abcdef1234567890abcdef",
                "google_key": "AIzaSyD1234567890123456789012345678901",
                "auth_header": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
                "safe_config": "production_v1",
            },
        )

        self.assertNotIn("sk-1234", str(task.checkpoint))
        self.assertNotIn("AIzaSyD", str(task.checkpoint))
        self.assertNotIn("eyJhbGci", str(task.checkpoint))
        self.assertEqual(task.checkpoint["safe_config"], "production_v1")
        self.assertEqual(task.checkpoint["openai_key"], "[REDACTED_SECRET]")

    # -------------------------------------------------------------------------
    # 3. Android Host & AVD Readiness
    # -------------------------------------------------------------------------

    def test_12_avd_discovery_empirical(self):
        """Verify actual host environment discovery."""
        env = self.readiness.check_environment()
        self.assertIn("studio", env["components"])
        self.assertIn("sdk", env["components"])
        self.assertIn("adb", env["components"])
        self.assertIn("emulator", env["components"])
        self.assertIn("java", env["components"])
        self.assertIn("gradle", env["components"])
        self.assertIn("avds", env["components"])

        # Empirical checks on developer host
        self.assertTrue(env["components"]["studio"]["present"])
        self.assertTrue(env["components"]["adb"]["present"])
        self.assertTrue(env["components"]["emulator"]["present"])

    def test_13_avd_readiness_pixel_6_api_34(self):
        """Verify Pixel_6_API_34 posture is accurately evaluated."""
        status = self.readiness.determine_avd_state("Pixel_6_API_34")
        self.assertTrue(status.installed)
        self.assertTrue(status.authorized)
        self.assertFalse(status.blocked)
        # On host, if emulator is not currently running, state is 'available' (or 'ready' if running)
        self.assertIn(status.state, [AvdReadinessState.AVAILABLE.value, AvdReadinessState.READY.value])

    def test_14_blocked_avd_pixel_6_api_35_rejected(self):
        """CRITICAL POLICY TEST: Pixel_6_API_35 MUST be rejected and flagged blocked."""
        status = self.readiness.determine_avd_state("Pixel_6_API_35")
        self.assertTrue(status.blocked)
        self.assertFalse(status.authorized)
        self.assertEqual(status.state, AvdReadinessState.BLOCKED.value)

        # verify_avd_readiness must raise safety error if boot attempted
        with self.assertRaises(AndroidSafetyError) as ctx:
            self.readiness.verify_avd_readiness("Pixel_6_API_35", auto_start=True)
        self.assertEqual(ctx.exception.code, AndroidErrorCode.DEVICE_NOT_AUTHORIZED)

    def test_15_avd_unavailable_nonexistent(self):
        """Verify nonexistent AVD reports UNAVAILABLE posture cleanly."""
        status = self.readiness.determine_avd_state("NonExistent_AVD_99")
        self.assertFalse(status.installed)
        self.assertEqual(status.state, AvdReadinessState.UNAVAILABLE.value)

    def test_16_avd_timeout_handling(self):
        """Verify bounded readiness check handles timeout gracefully without hanging."""
        with patch.object(self.readiness, "determine_avd_state") as mock_state:
            with patch("subprocess.Popen") as mock_popen:
                mock_proc = MagicMock()
                mock_proc.stdout = MagicMock()
                mock_proc.stderr = MagicMock()
                mock_popen.return_value = mock_proc

                # Simulate an AVD that remains booting indefinitely
                mock_state.return_value = AvdStatusReport(
                    avd_name="Pixel_6_API_34",
                    installed=True,
                    authorized=True,
                    blocked=False,
                    state=AvdReadinessState.BOOTING.value,
                    serial="emulator-5554",
                    boot_completed=False,
                    message="Booting...",
                )

                # Test short 1.0s timeout
                report = self.readiness.verify_avd_readiness(
                    avd_name="Pixel_6_API_34",
                    auto_start=True,
                    timeout_seconds=1.0,
                )
                self.assertEqual(report.state, AvdReadinessState.TIMEOUT.value)
                self.assertIn("timed out", report.message.lower())

    def test_17_emergency_stop_aborts_readiness_check(self):
        """Verify emergency stop freezes readiness verification."""
        self.safety.activate_emergency_stop()
        try:
            with self.assertRaises(EmergencyStopActiveError):
                self.readiness.verify_avd_readiness("Pixel_6_API_34", auto_start=True)
        finally:
            self.safety.deactivate_emergency_stop()


if __name__ == "__main__":
    unittest.main()
