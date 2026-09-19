"""
Tests for Droid Phase 3: Failure Evidence Collector.
"""

import unittest
from app.agent.android_failure_evidence import (
    FailureEvidenceCollector,
    EvidenceType,
    EvidenceSeverity,
    redact_sensitive_evidence,
)
from app.agent.android_safety import AndroidSafetyGate


class TestDroidPhase3Evidence(unittest.TestCase):

    def setUp(self):
        self.safety = AndroidSafetyGate()
        self.collector = FailureEvidenceCollector(safety_gate=self.safety)

    def test_record_evidence_normal(self):
        rec = self.collector.record_evidence(
            evidence_type=EvidenceType.BUILD,
            source="Gradle",
            message="Unresolved reference: SymbolX",
            task_id="T-100",
            location="MainActivity.kt:12",
        )
        self.assertEqual(rec.type, EvidenceType.BUILD)
        self.assertEqual(rec.redaction_status, "CLEAN")
        self.assertEqual(len(self.collector.get_records_by_task("T-100")), 1)

    def test_record_evidence_redacts_secrets(self):
        rec = self.collector.record_evidence(
            evidence_type=EvidenceType.LOGCAT,
            source="Runtime",
            message="Crash using token: sk-1234567890123456789012 and password='super_secret_val'",
            task_id="T-101",
        )
        self.assertEqual(rec.redaction_status, "REDACTED")
        self.assertNotIn("sk-1234567890123456789012", rec.message)
        self.assertIn("[REDACTED_SECRET]", rec.message)

    def test_collect_from_logcat(self):
        log_sample = """
09-19 07:00:00.000  1000  1000 E AndroidRuntime: FATAL EXCEPTION: main
09-19 07:00:00.001  1000  1000 E AndroidRuntime: Process: com.nrai.test, PID: 12345
09-19 07:00:00.002  1000  1000 E AndroidRuntime: java.lang.NullPointerException: Attempt to invoke virtual method
09-19 07:00:00.003  1000  1000 E AndroidRuntime: \tat com.nrai.test.MainActivity.onCreate(MainActivity.kt:42)
"""
        records = self.collector.collect_from_logcat(log_sample, task_id="T-102")
        self.assertGreaterEqual(len(records), 1)
        self.assertTrue(any(r.severity == EvidenceSeverity.FATAL for r in records))
        self.assertTrue(any(r.location and "MainActivity.kt:42" in r.location for r in records))

    def test_collect_from_build_error(self):
        rec = self.collector.collect_from_build_error(
            error_message="Type mismatch: inferred type is Int but String was expected",
            task_id="T-103",
            file_path="MainActivity.kt",
            line=55,
        )
        self.assertEqual(rec.type, EvidenceType.BUILD)
        self.assertEqual(rec.location, "MainActivity.kt:55")


if __name__ == "__main__":
    unittest.main()
