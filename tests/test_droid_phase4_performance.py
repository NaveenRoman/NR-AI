"""
Tests for Droid Phase 4 Component 9: Performance & Runtime Diagnostics.
"""
import unittest
from app.agent.android_performance import (
    AndroidPerformanceDiagnostics,
    MetricStatus,
)

class TestDroidPhase4Performance(unittest.TestCase):
    def setUp(self):
        self.perf = AndroidPerformanceDiagnostics()

    def test_anr_and_crash_detection(self):
        sample_logcat = """
09-19 12:00:01.123 1000 1000 E AndroidRuntime: FATAL EXCEPTION: main
09-19 12:00:01.123 1000 1000 E AndroidRuntime: Process: com.nrai.test, PID: 12345
09-19 12:00:01.124 1000 1000 E AndroidRuntime: java.lang.ArithmeticException: / by zero
"""
        anr, crashes, anomalies = self.perf.check_anr_and_crashes(
            serial="emulator-5554",
            package_name="com.nrai.test",
            logcat_text=sample_logcat,
        )
        self.assertFalse(anr)
        self.assertEqual(crashes, 1)
        self.assertEqual(len(anomalies), 1)

    def test_report_generation(self):
        report = self.perf.generate_report(
            serial="emulator-5554",
            package_name="com.nrai.test",
            logcat_text="",
        )
        self.assertEqual(report.package_name, "com.nrai.test")
        self.assertEqual(report.device_serial, "emulator-5554")
