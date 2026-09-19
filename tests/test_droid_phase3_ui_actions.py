"""
Tests for Droid Phase 3: Approved UI Action Engine & Sensitive Input Protection.
"""

import unittest
from unittest.mock import MagicMock
from app.agent.android_ui_actions import ApprovedUIActionEngine, ApprovedUIActionType, InputSensitivity
from app.agent.android_ui import AndroidTarget, AndroidUISnapshot
from app.agent.android_safety import AndroidSafetyGate


class TestDroidPhase3UIActions(unittest.TestCase):

    def setUp(self):
        self.safety = AndroidSafetyGate()
        self.engine = ApprovedUIActionEngine(safety_gate=self.safety)

    def test_classify_input_text_safe(self):
        self.assertEqual(self.engine.classify_input_text("Naveen"), InputSensitivity.SAFE_TEST_INPUT)
        self.assertEqual(self.engine.classify_input_text("search query"), InputSensitivity.SAFE_TEST_INPUT)
        self.assertEqual(self.engine.classify_input_text("42"), InputSensitivity.SAFE_TEST_INPUT)

    def test_classify_input_text_sensitive(self):
        self.assertEqual(self.engine.classify_input_text("my_password_123"), InputSensitivity.SENSITIVE_INPUT)
        self.assertEqual(self.engine.classify_input_text("sk-abcdef1234567890abcdef123456"), InputSensitivity.SENSITIVE_INPUT)
        self.assertEqual(self.engine.classify_input_text("bearer secret_token"), InputSensitivity.SENSITIVE_INPUT)
        self.assertEqual(self.engine.classify_input_text("api_key=AIzaSyD_test"), InputSensitivity.SENSITIVE_INPUT)

    def test_type_text_blocks_sensitive_input(self):
        res = self.engine.type_text_into_target("id", "passwordField", "secret_pass_123")
        self.assertFalse(res.success)
        self.assertTrue(res.sensitive_blocked)
        self.assertEqual(res.error, "SENSITIVE_INPUT_BLOCKED")

    def test_resolve_target_hierarchy(self):
        t1 = AndroidTarget(
            target_id="t1", semantic_type="BUTTON", text="Submit", content_desc="Submit Button",
            resource_id="com.nrai.test:id/btn_submit", class_name="android.widget.Button",
            package_name="com.nrai.test", bounds=(0, 0, 100, 100), center=(50, 50),
            clickable=True, enabled=True, selected=False, focused=False, device_serial="emulator-5554",
            screen_width=1080, screen_height=2400,
        )
        snap = AndroidUISnapshot(snapshot_id="s1", device_serial="emulator-5554", targets=[t1])

        # Resource ID
        res, reason = self.engine.resolve_target("resource_id", "btn_submit", snapshot=snap)
        self.assertEqual(res.target_id, "t1")
        self.assertEqual(reason, "RESOURCE_ID")

        # Text
        res, reason = self.engine.resolve_target("text", "Submit", snapshot=snap)
        self.assertEqual(res.target_id, "t1")
        self.assertEqual(reason, "VISIBLE_TEXT")

    def test_resolve_target_stale_rejection(self):
        t_stale = AndroidTarget(
            target_id="t_stale", semantic_type="BUTTON", text="Old", content_desc="Old",
            resource_id="id/old", class_name="android.widget.Button",
            package_name="com.nrai.test", bounds=(0, 0, 10, 10), center=(5, 5),
            clickable=True, enabled=True, selected=False, focused=False, device_serial="emulator-5554",
            screen_width=1080, screen_height=2400, timestamp=0.0, ttl_seconds=5.0,
        )
        snap = AndroidUISnapshot(snapshot_id="s_stale", device_serial="emulator-5554", targets=[t_stale])
        res, reason = self.engine.resolve_target("resource_id", "id/old", snapshot=snap)
        self.assertIsNone(res)
        self.assertEqual(reason, "STALE_TARGET")


if __name__ == "__main__":
    unittest.main()
