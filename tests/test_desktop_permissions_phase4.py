"""
Unit tests for Desktop Shell Permission Validator and Security Boundaries in NR-AI Phase 4.
"""

import unittest

from app.desktop.permissions import (
    DesktopIntentType,
    DesktopPermissionValidator,
    IntentValidationResult,
)


class TestDesktopPermissionsPhase4(unittest.TestCase):

    def test_valid_desktop_intents(self):
        # 1. NAVIGATE_VIEW
        res = DesktopPermissionValidator.validate_intent("NAVIGATE_VIEW", {"view": "galaxy"})
        self.assertTrue(res.is_authorized)
        self.assertEqual(res.sanitized_payload["view"], "galaxy")

        # 2. SELECT_AGENT
        res = DesktopPermissionValidator.validate_intent("SELECT_AGENT", {"agent_id": "nr_ai"})
        self.assertTrue(res.is_authorized)
        self.assertEqual(res.sanitized_payload["agent_id"], "nr_ai")

        # 3. TRIGGER_VOICE_ACTION
        res = DesktopPermissionValidator.validate_intent("TRIGGER_VOICE_ACTION", {"action": "start_listening"})
        self.assertTrue(res.is_authorized)
        self.assertEqual(res.sanitized_payload["action"], "start_listening")

        # 4. TRIGGER_EMERGENCY_STOP
        res = DesktopPermissionValidator.validate_intent("TRIGGER_EMERGENCY_STOP", {"reason": "test"})
        self.assertTrue(res.is_authorized)

        # 5. QUERY_HEALTH
        res = DesktopPermissionValidator.validate_intent("QUERY_HEALTH", {})
        self.assertTrue(res.is_authorized)

        # 6. SUBMIT_TASK
        res = DesktopPermissionValidator.validate_intent("SUBMIT_TASK", {"instruction": "Run daily diagnostic"})
        self.assertTrue(res.is_authorized)

    def test_unknown_intent_rejected(self):
        res = DesktopPermissionValidator.validate_intent("EXECUTE_ARBITRARY_CODE", {})
        self.assertFalse(res.is_authorized)
        self.assertIn("UNKNOWN_INTENT_TYPE", res.rejection_reason or "")

    def test_unauthorized_view_rejected(self):
        res = DesktopPermissionValidator.validate_intent("NAVIGATE_VIEW", {"view": "arbitrary_admin_panel"})
        self.assertFalse(res.is_authorized)
        self.assertIn("UNAUTHORIZED_DESKTOP_VIEW", res.rejection_reason or "")

    def test_unauthorized_agent_rejected(self):
        res = DesktopPermissionValidator.validate_intent("SELECT_AGENT", {"agent_id": "malicious_injected_agent"})
        self.assertFalse(res.is_authorized)
        self.assertIn("UNAUTHORIZED_AGENT", res.rejection_reason or "")

    def test_prohibited_command_patterns_rejected(self):
        prohibited_payloads = [
            {"instruction": "run cmd.exe /c whoami"},
            {"instruction": "powershell -Command Get-Process"},
            {"instruction": "eval('2 + 2')"},
            {"instruction": "exec('import os; os.system(\"calc\")')"},
            {"instruction": "subprocess.run(['ls'])"},
            {"instruction": "cat file | grep secret"},
            {"instruction": "test & calc"},
            {"instruction": "echo `id`"},
        ]

        for p in prohibited_payloads:
            res = DesktopPermissionValidator.validate_intent("SUBMIT_TASK", p)
            self.assertFalse(res.is_authorized, f"Failed to reject prohibited payload: {p}")
            self.assertIn("PROHIBITED_COMMAND_EXECUTION_BLOCKED", res.rejection_reason or "")

    def test_oversized_payload_rejected(self):
        huge_payload = {"instruction": "A" * 70000}
        res = DesktopPermissionValidator.validate_intent("SUBMIT_TASK", huge_payload)
        self.assertFalse(res.is_authorized)
        self.assertEqual(res.rejection_reason, "PAYLOAD_EXCEEDS_SIZE_LIMIT_64KB")


if __name__ == "__main__":
    unittest.main()
