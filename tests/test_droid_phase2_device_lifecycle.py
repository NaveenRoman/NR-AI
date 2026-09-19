"""
Tests for Droid Phase 2: Device Lifecycle & Boot Orchestration.
"""

import time
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

from app.agent.android_device_lifecycle import (
    DeviceLifecycleController,
    DeviceLifecycleReport,
    DeviceLifecycleState,
    BootConfiguration,
)
from app.agent.android_safety import AndroidSafetyGate, AndroidSafetyError, BLOCKED_AVDS
from app.agent.android_readiness import AvdStatusReport, AvdReadinessState


class TestDroidPhase2DeviceLifecycle(unittest.TestCase):

    def test_device_lifecycle_state_enum(self):
        """Verify all 16 lifecycle states are defined."""
        expected_states = {
            "UNKNOWN", "DISCOVERING", "AVAILABLE", "BOOTING", "BOOTED",
            "ADB_OFFLINE", "ADB_UNAUTHORIZED", "READY", "DEPLOYING",
            "INSTALLED", "LAUNCHING", "RUNNING", "STOPPING", "FAILED",
            "BLOCKED", "TIMEOUT"
        }
        actual = {s.value for s in DeviceLifecycleState}
        self.assertEqual(expected_states, actual)

    def test_policy_strictly_blocks_pixel_6_api_35(self):
        """Verify Pixel_6_API_35 is strictly blocked by policy."""
        self.assertIn("Pixel_6_API_35", BLOCKED_AVDS)

        ctrl = DeviceLifecycleController()
        with self.assertRaises(AndroidSafetyError) as exc_info:
            ctrl.boot(BootConfiguration(avd_name="Pixel_6_API_35"))
        self.assertIn("blocked by policy", str(exc_info.exception))
        self.assertEqual(ctrl.current_state, DeviceLifecycleState.BLOCKED)

    def test_status_for_blocked_avd(self):
        """Verify status query for Pixel_6_API_35 reports BLOCKED."""
        ctrl = DeviceLifecycleController()
        status = ctrl.get_status("Pixel_6_API_35")
        self.assertEqual(status.state, DeviceLifecycleState.BLOCKED)
        self.assertIn("blocked by policy", status.message)

    def test_device_discovery(self):
        """Verify discover_devices enumerates AVDs and attached devices."""
        mock_readiness = MagicMock()
        mock_readiness.list_installed_avds.return_value = ["Pixel_6_API_34", "Pixel_6_API_35"]
        mock_readiness.get_attached_devices.return_value = [{"serial": "emulator-5554", "status": "device"}]
        mock_readiness.determine_avd_state.side_effect = lambda avd: AvdStatusReport(
            avd_name=avd,
            installed=True,
            authorized=(avd == "Pixel_6_API_34"),
            blocked=(avd == "Pixel_6_API_35"),
            state="ready" if avd == "Pixel_6_API_34" else "blocked",
            serial="emulator-5554" if avd == "Pixel_6_API_34" else None,
            boot_completed=(avd == "Pixel_6_API_34"),
        )

        ctrl = DeviceLifecycleController(readiness_checker=mock_readiness)
        report = ctrl.discover_devices()

        self.assertIn("installed_avds", report)
        self.assertIn("Pixel_6_API_34", report["installed_avds"])
        self.assertEqual(report["authorized_avd_state"]["state"], "ready")
        self.assertEqual(ctrl.current_state, DeviceLifecycleState.READY)

    def test_boot_already_ready(self):
        """Verify boot short-circuits if AVD is already running and ready."""
        mock_readiness = MagicMock()
        mock_readiness.determine_avd_state.return_value = AvdStatusReport(
            avd_name="Pixel_6_API_34",
            installed=True,
            authorized=True,
            blocked=False,
            state="ready",
            serial="emulator-5554",
            boot_completed=True,
        )

        ctrl = DeviceLifecycleController(readiness_checker=mock_readiness)
        rep = ctrl.boot(BootConfiguration(avd_name="Pixel_6_API_34"))
        self.assertEqual(rep.state, DeviceLifecycleState.READY)
        self.assertEqual(rep.serial, "emulator-5554")

    def test_boot_timeout_cleanup(self):
        """Verify bounded boot loop cleanly times out and terminates process."""
        mock_readiness = MagicMock()
        mock_readiness.determine_avd_state.return_value = AvdStatusReport(
            avd_name="Pixel_6_API_34",
            installed=True,
            authorized=True,
            blocked=False,
            state="available",
            serial=None,
        )
        mock_readiness.get_attached_devices.return_value = []

        ctrl = DeviceLifecycleController(readiness_checker=mock_readiness)

        with patch("subprocess.Popen") as mock_popen, \
             patch.object(Path, "exists", return_value=True):
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None
            mock_popen.return_value = mock_proc

            rep = ctrl.boot(BootConfiguration(avd_name="Pixel_6_API_34", timeout_seconds=0.1))
            self.assertEqual(rep.state, DeviceLifecycleState.TIMEOUT)
            self.assertEqual(ctrl.current_state, DeviceLifecycleState.TIMEOUT)
            mock_proc.terminate.assert_called()

    def test_graceful_stop(self):
        """Verify graceful stop terminates process and invokes emu kill."""
        mock_adb = MagicMock()
        mock_adb.stop_emulator.return_value = True

        ctrl = DeviceLifecycleController(adb_client=mock_adb)
        mock_proc = MagicMock()
        ctrl._active_processes["Pixel_6_API_34"] = mock_proc

        rep = ctrl.stop(serial="emulator-5554", avd_name="Pixel_6_API_34")
        self.assertEqual(rep.state, DeviceLifecycleState.AVAILABLE)
        mock_adb.stop_emulator.assert_called_with("emulator-5554")
        mock_proc.terminate.assert_called()


if __name__ == "__main__":
    unittest.main()
