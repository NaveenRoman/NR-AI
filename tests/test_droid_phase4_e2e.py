"""
Tests for Droid Phase 4 Component 13: 19-Stage Real-World Engineering Loop.
"""
import unittest
from pathlib import Path
from app.agent.android_e2e_engine import AndroidE2EEngine, E2EWorkflowStage, E2EVerificationStatus

class TestDroidPhase4E2E(unittest.TestCase):
    def setUp(self):
        self.engine = AndroidE2EEngine()
        self.project_path = Path(r"C:\NR-AI\nr_android_test")

    def test_19_stage_loop_mock_execution(self):
        rep = self.engine.run_phase4_engineering_loop(
            engineering_goal="Fix UI state disconnect in MainScreen",
            project_path=self.project_path,
            mock_mode=True,
        )
        self.assertEqual(rep.verification_status, E2EVerificationStatus.VERIFIED)
        self.assertEqual(rep.current_stage, E2EWorkflowStage.COMPLETE)
        self.assertIn(E2EWorkflowStage.UNDERSTAND_GOAL.value, rep.checkpoints)
        self.assertIn(E2EWorkflowStage.BUILD_GRAPH.value, rep.checkpoints)
        self.assertIn(E2EWorkflowStage.SOURCE_ANALYSIS.value, rep.checkpoints)
        self.assertIn(E2EWorkflowStage.IMPACT_ANALYSIS.value, rep.checkpoints)
        self.assertIn(E2EWorkflowStage.MEMORY_UPDATE.value, rep.checkpoints)
        self.assertIn(E2EWorkflowStage.REPORT.value, rep.checkpoints)
        self.assertTrue(rep.memory_persisted)
