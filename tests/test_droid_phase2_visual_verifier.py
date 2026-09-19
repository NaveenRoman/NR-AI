"""
Tests for Droid Phase 2: Visual Verification Engine & Screenshot Manager.
"""

import tempfile
import time
import unittest
from unittest.mock import MagicMock
from pathlib import Path

from app.agent.android_ui import AndroidTarget, AndroidUISnapshot
from app.agent.android_visual_verifier import (
    VisualVerificationEngine,
    VisualAssertion,
    AssertionType,
    VisualVerificationStatus,
    SafeScreenshotManager,
)


class TestDroidPhase2VisualVerifier(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name).resolve()

        self.target = AndroidTarget(
            target_id="android.target.001",
            semantic_type="button",
            text="Submit Order",
            content_desc="Submit Order Button",
            resource_id="com.nrai.test:id/btn_submit",
            class_name="android.widget.Button",
            package_name="com.nrai.test",
            bounds=(100, 500, 400, 600),
            center=(250, 550),
            clickable=True,
            enabled=True,
            selected=False,
            focused=False,
            device_serial="emulator-5554",
            screen_width=1080,
            screen_height=2400,
        )
        self.sample_snapshot = AndroidUISnapshot(
            snapshot_id="snap_test",
            device_serial="emulator-5554",
            foreground_app={"package": "com.nrai.test", "activity": "com.nrai.test.OrderActivity"},
            screen_dimensions=(1080, 2400),
            timestamp=time.time(),
            targets=[self.target],
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_visual_assertions_pass(self):
        """Verify assertions pass when all elements match expected state."""
        engine = VisualVerificationEngine()
        assertions = [
            VisualAssertion(AssertionType.SCREEN_NOT_EMPTY, query="screen", expected_value=1),
            VisualAssertion(AssertionType.ELEMENT_VISIBLE, query="Submit Order"),
            VisualAssertion(AssertionType.ELEMENT_ENABLED, query="Submit Order"),
            VisualAssertion(AssertionType.ELEMENT_CLICKABLE, query="Submit Order"),
            VisualAssertion(AssertionType.ELEMENT_TEXT_EQUALS, query="Submit Order", expected_value="Submit Order"),
            VisualAssertion(AssertionType.SCREEN_NAVIGATED, query="OrderActivity", expected_value="com.nrai.test"),
        ]

        report = engine.verify(assertions, self.sample_snapshot)
        self.assertEqual(report.status, VisualVerificationStatus.PASS)
        self.assertEqual(report.passed_count, 6)
        self.assertEqual(report.failed_count, 0)

    def test_visual_assertion_fail(self):
        """Verify report status is FAIL when a mandatory assertion fails."""
        engine = VisualVerificationEngine()
        assertions = [
            VisualAssertion(AssertionType.ELEMENT_VISIBLE, query="NonExistentButton"),
        ]
        report = engine.verify(assertions, self.sample_snapshot)
        self.assertEqual(report.status, VisualVerificationStatus.FAIL)
        self.assertEqual(report.failed_count, 1)

    def test_visual_assertion_partial(self):
        """Verify optional assertion failures yield PARTIAL status instead of FAIL."""
        engine = VisualVerificationEngine()
        assertions = [
            VisualAssertion(AssertionType.ELEMENT_VISIBLE, query="Submit Order"),
            VisualAssertion(AssertionType.ELEMENT_VISIBLE, query="OptionalHelpIcon", optional=True),
        ]
        report = engine.verify(assertions, self.sample_snapshot)
        self.assertEqual(report.status, VisualVerificationStatus.PARTIAL)
        self.assertEqual(report.passed_count, 1)
        self.assertEqual(report.optional_failed_count, 1)
        self.assertEqual(report.failed_count, 0)

    def test_safe_screenshot_manager_redaction_and_retention(self):
        """Verify screenshot manager sanitizes labels, redacts secrets, and rotates files."""
        mock_adb = MagicMock()
        mock_adb.capture_screen.side_effect = lambda serial, dest_path: dest_path.write_bytes(b"\x89PNG fake png")

        mgr = SafeScreenshotManager(adb_client=mock_adb, root_dir=self.tmp_path, max_items=3)

        for i in range(5):
            p = mgr.capture_screenshot("emulator-5554", label=f"token_ghp_secret_{i}")
            self.assertNotIn("ghp_", p.name)

        items = mgr.list_screenshots()
        self.assertLessEqual(len(items), 3)


if __name__ == "__main__":
    unittest.main()
