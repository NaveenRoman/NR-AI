"""
Tests for Droid Phase 3: Root Cause & Evidence Correlation Engine.
"""

import unittest
from app.agent.android_root_cause import (
    RootCauseAnalysisEngine,
    RootCauseReport,
    RootCauseClassification,
)
from app.agent.android_failure_evidence import EvidenceRecord, EvidenceType, EvidenceSeverity
from app.agent.android_safety import AndroidSafetyGate


class TestDroidPhase3RootCause(unittest.TestCase):

    def setUp(self):
        self.safety = AndroidSafetyGate()
        self.engine = RootCauseAnalysisEngine(safety_gate=self.safety)

    def test_analyze_empty_evidence(self):
        report = self.engine.analyze([])
        self.assertEqual(report.classification, RootCauseClassification.UNRESOLVED)

    def test_analyze_null_pointer_exception(self):
        ev = EvidenceRecord(
            evidence_id="ev_01",
            type=EvidenceType.LOGCAT,
            timestamp=0.0,
            project_id="nr_android_test",
            task_id="T-1",
            source="AndroidRuntime",
            location="MainActivity.kt:42",
            message="FATAL EXCEPTION: java.lang.NullPointerException",
            severity=EvidenceSeverity.FATAL,
            structured_data={"stack_snippet": "at com.nrai.test.MainActivity.onCreate(MainActivity.kt:42)"},
        )
        report = self.engine.analyze([ev])
        self.assertEqual(report.classification, RootCauseClassification.CONFIRMED)
        self.assertEqual(report.failure_type, "NullPointerException")
        self.assertEqual(report.primary_location, "MainActivity.kt:42")
        self.assertGreaterEqual(len(report.repair_candidates), 1)

    def test_analyze_resource_not_found(self):
        ev = EvidenceRecord(
            evidence_id="ev_02",
            type=EvidenceType.LOGCAT,
            timestamp=0.0,
            project_id="nr_android_test",
            task_id="T-2",
            source="AndroidRuntime",
            message="android.content.res.Resources$NotFoundException: resource string/welcome_title",
            severity=EvidenceSeverity.ERROR,
        )
        report = self.engine.analyze([ev])
        self.assertEqual(report.classification, RootCauseClassification.CONFIRMED)
        self.assertIn("Resource", report.failure_type)
        self.assertEqual(report.repair_candidates[0].target_type, "RESOURCE")

    def test_analyze_class_not_found(self):
        ev = EvidenceRecord(
            evidence_id="ev_03",
            type=EvidenceType.LOGCAT,
            timestamp=0.0,
            project_id="nr_android_test",
            task_id="T-3",
            source="AndroidRuntime",
            message="java.lang.ClassNotFoundException: class okhttp3.OkHttpClient",
            severity=EvidenceSeverity.ERROR,
        )
        report = self.engine.analyze([ev])
        self.assertEqual(report.classification, RootCauseClassification.STRONGLY_SUPPORTED)
        self.assertEqual(report.repair_candidates[0].target_type, "GRADLE")

    def test_analyze_generic_inconclusive(self):
        ev = EvidenceRecord(
            evidence_id="ev_04",
            type=EvidenceType.DEVICE_STATE,
            timestamp=0.0,
            project_id="nr_android_test",
            task_id="T-4",
            source="Device",
            message="Battery level changed to 95%",
            severity=EvidenceSeverity.INFO,
        )
        report = self.engine.analyze([ev])
        self.assertEqual(report.classification, RootCauseClassification.POSSIBLE)


if __name__ == "__main__":
    unittest.main()
