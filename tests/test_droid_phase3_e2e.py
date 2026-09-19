"""
Tests for Droid Phase 3: End-to-End Engineering Loop.
"""

import unittest
from app.agent.android_e2e_engine import AndroidE2EEngine, E2EExecutionReport, E2EWorkflowStage, E2EVerificationStatus
from app.agent.android_safety import AndroidSafetyGate


class TestDroidPhase3E2E(unittest.TestCase):

    def setUp(self):
        self.safety = AndroidSafetyGate()
        self.engine = AndroidE2EEngine(safety_gate=self.safety)

    def test_e2e_loop_stops_if_unreproduced(self):
        report = self.engine.run_engineering_loop(
            bug_description="Unreproducible glitch in animation",
            mock_mode=False,
            auto_repair=True,
        )
        self.assertEqual(report.verification_status, E2EVerificationStatus.NOT_VERIFIED)
        self.assertFalse(report.initial_reproduced)
        self.assertIn("REPRODUCE", report.checkpoints)

    def test_e2e_loop_full_mock_success(self):
        report = self.engine.run_engineering_loop(
            bug_description="Crash on clicking send button",
            mock_mode=True,
            auto_repair=True,
        )
        self.assertEqual(report.verification_status, E2EVerificationStatus.VERIFIED)
        self.assertTrue(report.initial_reproduced)
        self.assertTrue(report.repair_applied)
        self.assertTrue(report.tests_passed)
        self.assertIn("COMPLETE", report.checkpoints)


if __name__ == "__main__":
    unittest.main()
