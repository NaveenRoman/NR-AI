import unittest
from pathlib import Path

from app.agent.android_readiness_auditor import (
    AndroidReadinessAuditor,
    AuditStatus,
    ProjectReadinessScorecard,
)


class TestDroidPhase5ReadinessAuditor(unittest.TestCase):

    def setUp(self):
        self.auditor = AndroidReadinessAuditor()
        self.project_path = Path(r"C:\NR-AI\nr_android_test")

    def test_26_dimensions_audited(self):
        scorecard = self.auditor.audit_project_static_and_live(
            project_path=self.project_path,
            serial="emulator-5554",
        )
        self.assertEqual(len(scorecard.evaluations), 26)
        self.assertEqual(scorecard.fail_count, 0)
        self.assertGreaterEqual(scorecard.pass_count, 23)
        self.assertEqual(scorecard.overall_rating, "PASS")

    def test_dimension_names_integrity(self):
        expected_dims = {
            "PROJECT_DISCOVERY",
            "GRADLE_SYNC_BUILD",
            "APK_GENERATION",
            "INSTALLATION",
            "APPLICATION_LAUNCH",
            "UI_HIERARCHY",
            "UI_INTERACTION",
            "KOTLIN_JAVA_ANALYSIS",
            "XML_RESOURCE_ANALYSIS",
            "COMPOSE_ANALYSIS",
            "DEPENDENCY_GRAPH",
            "UNIT_TESTS",
            "INSTRUMENTATION_TESTS",
            "LOGCAT_CAPTURE",
            "CRASH_DIAGNOSIS",
            "ANR_DIAGNOSIS",
            "PERFORMANCE_DIAGNOSTICS",
            "SCREENSHOT_VERIFICATION",
            "CONTROLLED_BUG_REPRODUCTION",
            "BOUNDED_AUTONOMOUS_REPAIR",
            "REBUILD_VERIFICATION",
            "REDEPLOYMENT_VERIFICATION",
            "REGRESSION_VERIFICATION",
            "PERSISTENT_TASK_STATE",
            "PERSISTENT_ENGINEERING_MEMORY",
            "COMPANION_ROUTING",
        }
        actual_dims = {name for _, name in self.auditor.DIMENSIONS}
        self.assertEqual(expected_dims, actual_dims)


if __name__ == "__main__":
    unittest.main()
