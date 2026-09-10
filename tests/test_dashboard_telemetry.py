"""
NR-AI Dashboard Telemetry Verification Suite.

Tests:
1. Dashboard telemetry snapshot generation.
2. Honest reporting of:
   - Microphone status (hardware available, active device name, count)
   - Wake word status (enabled, configured words, last recognized wake phrase)
   - Recognized command
   - Current route
   - Current agent
   - Assistant status (Idle when not speaking/listening, no fake active status)
   - Model execution metadata (requested model, actual model used, provider, HTTP status, live API success)
3. Absence of fake active/listening indicators when idle.
"""

import json
import sys
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.brain.companion import NRCompanion
from app.config.model_config import ModelConfig
from app.config.voice_config import VoiceConfig
from app.ui.avatar_state import AvatarMode
from app.ui.dashboard import CompanionDashboard


class TestDashboardTelemetry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = ModelConfig.from_env()
        cls.voice_config = VoiceConfig.from_env()
        cls.companion = NRCompanion(config=cls.config, voice_config=cls.voice_config)
        cls.dashboard = CompanionDashboard(companion=cls.companion)

    def test_01_telemetry_snapshot_structure(self):
        """Verify dashboard snapshot contains all required operational keys."""
        snapshot = self.dashboard.get_status_snapshot()
        print("\n[Dashboard Telemetry Snapshot]")
        print(json.dumps(snapshot, indent=2))

        required_keys = [
            "assistant_status",
            "microphone",
            "recognized_wake_phrase",
            "recognized_command",
            "current_route",
            "current_agent",
            "current_task_status",
            "model_execution",
            "orchestrator_metrics",
        ]
        for k in required_keys:
            self.assertIn(k, snapshot, f"Dashboard snapshot missing required key: '{k}'")

    def test_02_microphone_and_wake_word_honesty(self):
        """Verify microphone and wake word telemetry honestly reflect real state."""
        snapshot = self.dashboard.get_status_snapshot()
        mic = snapshot["microphone"]

        print(f"\n[Microphone Telemetry] Available: {mic['available']}, Active Device: {mic['device_name']}, Status: {mic['status']}")
        self.assertTrue(mic["available"], "Microphone must be reported as available on hardware")
        self.assertNotEqual(mic["device_name"], "None")
        self.assertTrue(mic["wake_word_enabled"])
        self.assertIn("hey nr", mic["wake_words"])

    def test_03_honest_idle_assistant_status(self):
        """Verify assistant does NOT display fake listening or active status when idle."""
        snapshot = self.dashboard.get_status_snapshot()
        status = snapshot["assistant_status"]
        raw_state = snapshot["raw_state"]

        print(f"\n[Assistant State Telemetry] Status: \"{status}\", Raw Mode: \"{raw_state}\"")
        self.assertEqual(raw_state, AvatarMode.IDLE.value)
        self.assertIn("Idle", status)
        self.assertNotIn("Listening", status, "Must not display fake 'Listening' status when idle")
        self.assertNotIn("Speaking", status, "Must not display fake 'Speaking' status when idle")

    def test_04_model_execution_telemetry_after_interaction(self):
        """Verify dashboard telemetry updates model execution honestly after a real interaction."""
        # Execute interaction
        cmd = "What is the capital of Japan?"
        resp = self.companion.interact(cmd, speak_output=False, wake_phrase_checked=True)

        snapshot = self.dashboard.get_status_snapshot()
        model_exec = snapshot["model_execution"]

        print("\n[Dashboard Telemetry After Real Interaction]")
        print(f"  Recognized Command: {snapshot['recognized_command']}")
        print(f"  Current Route:      {snapshot['current_route']}")
        print(f"  Current Agent:      {snapshot['current_agent']}")
        print(f"  Assistant Status:   {snapshot['assistant_status']}")
        print(f"  Requested Model:    {model_exec.get('requested_model')}")
        print(f"  Actual Model Used:  {model_exec.get('actual_model_used')}")
        print(f"  Provider:           {model_exec.get('provider')}")
        print(f"  HTTP Status:        {model_exec.get('http_status')}")
        print(f"  Live API Success:   {model_exec.get('live_api_success')}")
        print(f"  Fallback Used:      {model_exec.get('fallback_used')}")

        self.assertEqual(snapshot["recognized_command"], cmd)
        self.assertEqual(str(model_exec.get("http_status")), "200")
        self.assertEqual(model_exec.get("live_api_success"), "YES")
        self.assertIn("Gemini", model_exec.get("provider"))
        self.assertNotEqual(model_exec.get("actual_model_used"), "NONE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
