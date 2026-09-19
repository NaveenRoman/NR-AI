"""
Tests for Droid Phase 2: Companion Routing & Remote REST Endpoints.
"""

import unittest
from unittest.mock import MagicMock
from pathlib import Path

from app.brain.companion import NRCompanion, CommandCategory
from app.ui.dashboard import CompanionDashboard
from app.agent.android_device_lifecycle import DeviceLifecycleReport, DeviceLifecycleState


class TestDroidPhase2Galaxy(unittest.TestCase):

    def test_companion_routes_phase2_commands(self):
        """Verify natural language Phase 2 commands route to Droid."""
        comp = NRCompanion()

        self.assertEqual(comp.classify_command("boot pixel 6"), CommandCategory.ANDROID_STUDIO)
        self.assertEqual(comp.classify_command("preview compose"), CommandCategory.ANDROID_STUDIO)
        self.assertEqual(comp.classify_command("take screenshot"), CommandCategory.ANDROID_STUDIO)
        self.assertEqual(comp.classify_command("run full e2e"), CommandCategory.ANDROID_STUDIO)

    def test_companion_handles_boot_command(self):
        """Verify _handle_android_studio executes boot when command has 'boot pixel 6'."""
        comp = NRCompanion()
        mock_u = MagicMock()
        mock_u.boot_device.return_value = DeviceLifecycleReport(
            avd_name="Pixel_6_API_34",
            state=DeviceLifecycleState.READY,
            serial="emulator-5554",
            boot_completed=True,
            message="Device reached ready.",
        )
        comp.unified_android_agent = mock_u

        resp = comp._handle_android_studio("boot pixel 6")
        self.assertIn("Pixel_6_API_34", resp.text)
        self.assertEqual(resp.category, CommandCategory.ANDROID_STUDIO)
        self.assertEqual(resp.routed_to, "Droid")

    def test_dashboard_snapshot_includes_droid_status(self):
        """Verify dashboard snapshot includes droid_status."""
        comp = NRCompanion()
        mock_u = MagicMock()
        mock_u.get_device_lifecycle_status.return_value = DeviceLifecycleReport(
            avd_name="Pixel_6_API_34",
            state=DeviceLifecycleState.AVAILABLE,
            message="Device installed and available.",
        )
        comp.unified_android_agent = mock_u

        dash = CompanionDashboard(companion=comp)
        snap = dash.get_status_snapshot()
        self.assertIn("droid_status", snap)
        self.assertEqual(snap["droid_status"]["avd_name"], "Pixel_6_API_34")
        self.assertEqual(snap["droid_status"]["state"], "AVAILABLE")


if __name__ == "__main__":
    unittest.main()
