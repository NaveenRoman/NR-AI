"""
Tests for NR-AI Unified Memory Integration (Phase 2).
Verifies:
- Task checkpoint save and authorized restore
- Cross-workspace task leakage prevention (WORKSPACE_LEAKAGE_DENIED)
- Project context restoration with workspace matching
- Private agent scratchpad isolation (AGENT_ISOLATION_DENIED)
- Zero cross-domain leakage
"""

from pathlib import Path
import tempfile
import unittest

from app.memory.boundaries import MemoryBoundaryManager
from app.memory.integration import UnifiedMemoryIntegrator
from app.task.checkpoint_store import TaskCheckpointStore
from app.agent.engineering_context import ActiveProjectContext, ActiveProjectContextManager


class TestMemoryIntegration(unittest.TestCase):
    """Unit test suite for UnifiedMemoryIntegrator."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_tasks.db"
        self.ctx_file = Path(self.temp_dir.name) / "test_active_context.json"

        self.boundary_mgr = MemoryBoundaryManager()
        self.task_store = TaskCheckpointStore(db_path=self.db_path)
        self.project_mgr = ActiveProjectContextManager(context_file=self.ctx_file)

        self.integrator = UnifiedMemoryIntegrator(
            boundary_manager=self.boundary_mgr,
            task_store=self.task_store,
            project_manager=self.project_mgr,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_save_and_restore_checkpoint_under_authorized_workspace(self):
        """Verify saving a task checkpoint and restoring it within authorized workspace."""
        task_id = "task_android_99"
        ws = r"C:\NR-AI\dev_projects\NR-AI"

        ok = self.integrator.save_task_checkpoint(
            task_id=task_id,
            agent_id="Droid",
            workspace_scope=ws,
            step_number=3,
            checkpoint_data={"build_target": "assembleDebug", "apk_size": 9153},
        )
        self.assertTrue(ok)

        # Restore from authorized caller in same workspace
        success, data, msg = self.integrator.restore_task_checkpoint(
            task_id=task_id,
            caller_agent_id="Droid",
            caller_workspace=ws,
        )
        self.assertTrue(success)
        self.assertIsNotNone(data)
        self.assertEqual(data["build_target"], "assembleDebug")

    def test_cross_workspace_checkpoint_restoration_blocked(self):
        """Verify restoring a checkpoint from an alien workspace is strictly rejected."""
        task_id = "task_isolated_1"
        ws_a = r"C:\NR-AI\workspace_a"
        ws_b = r"C:\NR-AI\workspace_b"

        self.integrator.save_task_checkpoint(
            task_id=task_id,
            agent_id="WorkerA",
            workspace_scope=ws_a,
            step_number=1,
            checkpoint_data={"secret_plan": "deploy_alpha"},
        )

        # Caller in workspace B attempts to restore task from workspace A
        success, data, msg = self.integrator.restore_task_checkpoint(
            task_id=task_id,
            caller_agent_id="WorkerB",
            caller_workspace=ws_b,
        )
        self.assertFalse(success)
        self.assertIsNone(data)
        self.assertIn("WORKSPACE_LEAKAGE_DENIED", msg)

    def test_private_agent_scratchpad_isolation(self):
        """Verify private agent scratchpad cannot be read or written by peer agents."""
        # 1. Agent Droid writes to own scratchpad
        ok, msg = self.integrator.update_agent_scratchpad(
            caller_agent_id="Droid",
            target_agent_id="Droid",
            key="last_inspected_symbol",
            value="MainActivity.java",
        )
        self.assertTrue(ok)

        # 2. Agent Droid reads own scratchpad
        ok_read, pad, _ = self.integrator.access_agent_scratchpad(
            caller_agent_id="Droid",
            target_agent_id="Droid",
        )
        self.assertTrue(ok_read)
        self.assertEqual(pad.get("last_inspected_symbol"), "MainActivity.java")

        # 3. Peer agent (e.g. BrowserAgent) tries to access Droid's scratchpad -> REJECTED
        alien_read, alien_pad, alien_msg = self.integrator.access_agent_scratchpad(
            caller_agent_id="BrowserAgent",
            target_agent_id="Droid",
        )
        self.assertFalse(alien_read)
        self.assertIsNone(alien_pad)
        self.assertIn("AGENT_ISOLATION_DENIED", alien_msg)

    def test_restore_project_context_within_workspace(self):
        """Verify restoring active project context matches workspace boundary."""
        ws = r"C:\NR-AI\dev_projects\NR-AI"
        ctx = ActiveProjectContext(
            project_id="proj_101",
            project_name="NR-AI",
            canonical_path=ws,
            active_feature="MainActivity",
        )
        self.project_mgr.set_active_context(ctx)

        # Authorized caller
        success, retrieved_ctx, msg = self.integrator.restore_project_context(
            caller_agent_id="Droid",
            caller_workspace=ws,
        )
        self.assertTrue(success)
        self.assertEqual(retrieved_ctx.project_name, "NR-AI")


if __name__ == "__main__":
    unittest.main()
