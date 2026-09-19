"""
Tests for Droid Phase 2: UnifiedAndroidAgent Integration & Task Checkpoints.
"""

import tempfile
import unittest
from unittest.mock import MagicMock
from pathlib import Path

from app.agent.android_unified_agent import UnifiedAndroidAgent
from app.agent.android_device_lifecycle import DeviceLifecycleReport, DeviceLifecycleState, DeploymentResult
from app.agent.android_visual_verifier import VisualVerificationReport, VisualVerificationStatus
from app.agent.droid_task_state import DroidTaskStateStore


class TestDroidPhase2Integration(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name).resolve()

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_unified_agent_phase2_subsystems_initialized(self):
        """Verify UnifiedAndroidAgent initializes all Phase 2 engines."""
        agent = UnifiedAndroidAgent()
        self.assertTrue(hasattr(agent, "lifecycle_controller"))
        self.assertTrue(hasattr(agent, "preview_engine"))
        self.assertTrue(hasattr(agent, "semantics_correlator"))
        self.assertTrue(hasattr(agent, "visual_verifier"))
        self.assertTrue(hasattr(agent, "screenshot_manager"))

    def test_unified_agent_boot_records_checkpoint(self):
        """Verify boot_device persists a checkpoint to DroidTaskStateStore on success."""
        db_path = self.tmp_path / "test_droid.db"
        store = DroidTaskStateStore(db_path=db_path)
        task = store.create_task("T-BOOT-001", "Boot and deploy test")

        mock_lifecycle = MagicMock()
        mock_lifecycle.boot.return_value = DeviceLifecycleReport(
            avd_name="Pixel_6_API_34",
            state=DeviceLifecycleState.READY,
            serial="emulator-5554",
            boot_completed=True,
        )

        agent = UnifiedAndroidAgent(
            lifecycle_controller=mock_lifecycle,
            task_state_store=store,
        )

        rep = agent.boot_device("Pixel_6_API_34")
        self.assertEqual(rep.state, DeviceLifecycleState.READY)

        checkpoints = store.get_checkpoints(task.task_id)
        self.assertGreaterEqual(len(checkpoints), 1)
        self.assertEqual(checkpoints[0]["name"], "DEVICE_BOOTED")

    def test_unified_agent_deploy_records_checkpoint(self):
        """Verify deploy_and_launch persists APP_DEPLOYED checkpoint."""
        db_path = self.tmp_path / "test_droid.db"
        store = DroidTaskStateStore(db_path=db_path)
        task = store.create_task("T-DEPLOY-001", "Deployment verification")

        mock_lifecycle = MagicMock()
        mock_lifecycle.deploy.return_value = DeploymentResult(
            success=True,
            state="RUNNING",
            package_name="com.nrai.test",
            activity_name="MainActivity",
            installed=True,
            launched=True,
            pid=54321,
        )

        agent = UnifiedAndroidAgent(
            lifecycle_controller=mock_lifecycle,
            task_state_store=store,
        )

        res = agent.deploy_and_launch()
        self.assertTrue(res.success)

        checkpoints = store.get_checkpoints(task.task_id)
        self.assertTrue(any(c["name"] == "APP_DEPLOYED" for c in checkpoints))

    def test_unified_agent_preview_analysis(self):
        """Verify analyze_compose_previews parses composables."""
        agent = UnifiedAndroidAgent()
        src = '@Preview(name = "Quick Preview")\n@Composable\nfun Quick() { Text("Hello") }'
        rep = agent.analyze_compose_previews(src)
        self.assertEqual(rep.total_previews, 1)
        self.assertEqual(rep.previews[0].parameters.name, "Quick Preview")


if __name__ == "__main__":
    unittest.main()
