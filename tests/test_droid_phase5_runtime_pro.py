import unittest

from app.agent.android_runtime_diagnostics_pro import (
    AndroidRuntimeDiagnosticsPro,
    PerformanceRating,
)


class TestDroidPhase5RuntimePro(unittest.TestCase):

    def setUp(self):
        self.engine = AndroidRuntimeDiagnosticsPro()

    def test_parse_gfxinfo_metrics(self):
        sample_gfx = """
Applications Graphics Acceleration Info:
com.nrai.test/com.nrai.test.MainActivity/uptime 12345 (dumpsys gfxinfo)

Total frames rendered: 500
Janky frames: 15 (3.00%)
50th percentile: 6ms
90th percentile: 11ms
95th percentile: 14ms
99th percentile: 20ms
Number Missed Vsync: 2
"""
        report = self.engine.parse_gfxinfo_text("com.nrai.test", sample_gfx)
        self.assertEqual(report.total_frames, 500)
        self.assertEqual(report.janky_frames, 15)
        self.assertEqual(report.janky_percent, 3.0)
        self.assertEqual(report.p95_ms, 14.0)
        self.assertEqual(report.rating, PerformanceRating.EXCELLENT)

    def test_scan_strict_mode_violations(self):
        logcat = """
09-19 16:30:00.123 1234 1234 D StrictMode: StrictMode policy violation: android.os.strictmode.DiskReadViolation
    at android.os.StrictMode$AndroidBlockGuardPolicy.onReadFromDisk(StrictMode.java:1620)
    at java.io.File.exists(File.java:816)
09-19 16:30:00.140 1234 1234 D StrictMode: StrictMode policy violation: android.os.strictmode.LeakedClosableViolation
"""
        violations = self.engine.scan_strict_mode_violations(logcat)
        self.assertEqual(len(violations), 2)
        self.assertEqual(violations[0].violation_type, "DISK_READ")
        self.assertEqual(violations[1].violation_type, "RESOURCE_LEAK")


if __name__ == "__main__":
    unittest.main()
