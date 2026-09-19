"""
Tests for Droid Phase 3: Failure Reproduction Engine.
"""

import unittest
from unittest.mock import MagicMock
from app.agent.android_reproduction import (
    FailureReproductionEngine,
    ReproductionPlan,
    ReproductionResult,
    ReproductionState,
    ReproductionAction,
    ReproductionActionType,
)
from app.agent.android_safety import AndroidSafetyGate, EmergencyStopActiveError


class TestDroidPhase3Reproduction(unittest.TestCase):

    def setUp(self):
        self.safety = AndroidSafetyGate()
        self.engine = FailureReproductionEngine(safety_gate=self.safety)

    def test_create_plan_from_description(self):
        plan = self.engine.create_plan_from_description("App crashes when login button is pressed")
        self.assertIn("REPRO", plan.workflow_name)
        self.assertGreaterEqual(len(plan.actions), 3)
        self.assertTrue(any(a.action_type == ReproductionActionType.LAUNCH_APP for a in plan.actions))
        self.assertTrue(any("FATAL" in s or "NullPointer" in s for s in plan.failure_signals))

    def test_validate_plan_success(self):
        plan = self.engine.create_plan_from_description("Simple button click")
        valid, errs = self.engine.validate_plan(plan)
        self.assertTrue(valid)
        self.assertEqual(len(errs), 0)

    def test_validate_plan_rejects_excessive_actions(self):
        plan = self.engine.create_plan_from_description("Simple test")
        for i in range(20):
            plan.actions.append(ReproductionAction(action_type=ReproductionActionType.WAIT_FOR_SCREEN))
        valid, errs = self.engine.validate_plan(plan)
        self.assertFalse(valid)
        self.assertTrue(any("exceeds maximum allowed actions" in e for e in errs))

    def test_validate_plan_rejects_excessive_timeout(self):
        plan = self.engine.create_plan_from_description("Simple test")
        plan.timeout_seconds = 500.0
        valid, errs = self.engine.validate_plan(plan)
        self.assertFalse(valid)
        self.assertTrue(any("timeout exceeds maximum" in e for e in errs))

    def test_execute_plan_reproduced(self):
        mock_ui = MagicMock()
        mock_ui.launch_app.return_value = {"success": True}
        mock_diag = MagicMock()
        mock_diag.capture_logcat.return_value = MagicMock(
            raw_text="FATAL EXCEPTION: main\nProcess: com.nrai.test\njava.lang.NullPointerException",
            crash_stack_trace="java.lang.NullPointerException at com.nrai.test.MainActivity",
            failure_location="MainActivity.kt:42",
        )
        plan = self.engine.create_plan_from_description("Crash on start")
        res = self.engine.execute_plan(plan, mock_ui, diagnostics_controller=mock_diag)
        self.assertEqual(res.state, ReproductionState.REPRODUCED)
        self.assertIsNotNone(res.detected_failure_signal)

    def test_execute_plan_not_reproduced(self):
        mock_ui = MagicMock()
        mock_ui.launch_app.return_value = {"success": True}
        mock_diag = MagicMock()
        mock_diag.capture_logcat.return_value = MagicMock(raw_text="Normal app operation log", crash_stack_trace=None, failure_location=None)
        plan = self.engine.create_plan_from_description("Crash on start")
        res = self.engine.execute_plan(plan, mock_ui, diagnostics_controller=mock_diag)
        self.assertEqual(res.state, ReproductionState.NOT_REPRODUCED)


if __name__ == "__main__":
    unittest.main()
