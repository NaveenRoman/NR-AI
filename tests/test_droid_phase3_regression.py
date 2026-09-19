"""
Tests for Droid Phase 3: Regression Protection Engine.
"""

import unittest
from app.agent.android_regression import AndroidRegressionEngine, TestComparisonReport
from app.agent.android_test_results import JUnitReport, TestCaseResult
from app.agent.android_safety import AndroidSafetyGate


class TestDroidPhase3Regression(unittest.TestCase):

    def setUp(self):
        self.safety = AndroidSafetyGate()
        self.engine = AndroidRegressionEngine(safety_gate=self.safety)

    def test_identify_affected_tests(self):
        files = ["app/src/main/java/com/nrai/test/LoginManager.kt", "app/src/main/java/com/nrai/test/UserSession.kt"]
        affected = self.engine.identify_affected_tests(files)
        self.assertIn("LoginManagerTest", affected)
        self.assertIn("UserSessionTest", affected)

    def test_compare_test_runs_clean(self):
        pre = JUnitReport(total_tests=10, total_failures=0, total_errors=0, total_skipped=0, total_time_sec=1.0, suites=[], failures=[], is_success=True)
        post = JUnitReport(total_tests=10, total_failures=0, total_errors=0, total_skipped=0, total_time_sec=1.0, suites=[], failures=[], is_success=True)
        rep = self.engine.compare_test_runs(pre, post)
        self.assertTrue(rep.passed_cleanly)
        self.assertEqual(len(rep.new_failures), 0)

    def test_compare_test_runs_detects_regression(self):
        t_fail = TestCaseResult(name="testAuth", classname="AuthTest", time_sec=0.1, status="FAILED", failure_message="Assertion error")
        pre = JUnitReport(total_tests=5, total_failures=0, total_errors=0, total_skipped=0, total_time_sec=0.5, suites=[], failures=[], is_success=True)
        post = JUnitReport(total_tests=5, total_failures=1, total_errors=0, total_skipped=0, total_time_sec=0.5, suites=[], failures=[t_fail], is_success=False)
        rep = self.engine.compare_test_runs(pre, post)
        self.assertFalse(rep.passed_cleanly)
        self.assertIn("testAuth", rep.new_failures)
        self.assertIn("REGRESSION DETECTED", rep.message)


if __name__ == "__main__":
    unittest.main()
