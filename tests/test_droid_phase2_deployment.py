"""
Tests for Droid Phase 2: Application Deployment Pipeline.
"""

import tempfile
import unittest
from unittest.mock import MagicMock
from pathlib import Path

from app.agent.android_device_lifecycle import (
    DeviceLifecycleController,
    DeploymentConfiguration,
    DeploymentResult,
    DeviceLifecycleState,
)


class TestDroidPhase2Deployment(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name).resolve()

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_deploy_missing_device(self):
        """Verify deployment fails if no authorized device is available."""
        mock_readiness = MagicMock()
        mock_readiness.determine_avd_state.return_value = MagicMock(
            state="not_installed",
            serial=None,
        )
        ctrl = DeviceLifecycleController(readiness_checker=mock_readiness)
        ctrl._active_serial = None
        ctrl.adb = MagicMock()
        ctrl.adb.list_devices.return_value = []

        res = ctrl.deploy(serial=None, config=DeploymentConfiguration(package_name="com.nrai.test"))
        self.assertFalse(res.success)
        self.assertIn("No running authorized device", res.error)

    def test_deploy_missing_apk(self):
        """Verify deployment fails cleanly when target APK is missing."""
        ctrl = DeviceLifecycleController()
        ctrl._active_serial = "emulator-5554"

        non_existent = self.tmp_path / "missing.apk"
        res = ctrl.deploy(
            serial="emulator-5554",
            config=DeploymentConfiguration(apk_path=non_existent, package_name="com.nrai.test")
        )
        self.assertFalse(res.success)
        self.assertIn("Target APK does not exist", res.error)
        self.assertEqual(ctrl.current_state, DeviceLifecycleState.FAILED)

    def test_deploy_success_pipeline(self):
        """Verify 6-stage verified deployment pipeline passes when ADB signals succeed."""
        mock_adb = MagicMock()
        mock_adb.install_apk.return_value = (True, "Success")
        mock_adb.is_package_installed.return_value = True
        mock_adb.launch_package.return_value = True
        mock_adb.get_process_pid.return_value = 12345
        mock_adb.get_foreground_app.return_value = {"package": "com.nrai.test", "activity": "com.nrai.test.MainActivity"}

        ctrl = DeviceLifecycleController(adb_client=mock_adb)
        ctrl._active_serial = "emulator-5554"

        dummy_apk = self.tmp_path / "app-debug.apk"
        dummy_apk.write_bytes(b"dummy apk content")

        res = ctrl.deploy(
            serial="emulator-5554",
            config=DeploymentConfiguration(apk_path=dummy_apk, package_name="com.nrai.test", activity_name="MainActivity")
        )

        self.assertTrue(res.success)
        self.assertEqual(res.state, "RUNNING")
        self.assertTrue(res.installed)
        self.assertTrue(res.launched)
        self.assertEqual(res.pid, 12345)
        self.assertEqual(res.foreground_activity, "com.nrai.test.MainActivity")
        self.assertEqual(ctrl.current_state, DeviceLifecycleState.RUNNING)

    def test_deploy_install_failure(self):
        """Verify deployment halts if APK installation fails."""
        mock_adb = MagicMock()
        mock_adb.install_apk.return_value = (False, "INSTALL_FAILED_INSUFFICIENT_STORAGE")

        ctrl = DeviceLifecycleController(adb_client=mock_adb)
        ctrl._active_serial = "emulator-5554"

        dummy_apk = self.tmp_path / "app-debug.apk"
        dummy_apk.write_bytes(b"dummy apk")

        res = ctrl.deploy(
            serial="emulator-5554",
            config=DeploymentConfiguration(apk_path=dummy_apk)
        )

        self.assertFalse(res.success)
        self.assertIn("APK installation failed", res.error)
        self.assertEqual(ctrl.current_state, DeviceLifecycleState.FAILED)

    def test_stop_app(self):
        """Verify stop_app issues am force-stop cleanly."""
        mock_adb = MagicMock()
        mock_adb._run_adb.return_value = (0, "", "")

        ctrl = DeviceLifecycleController(adb_client=mock_adb)
        ctrl._current_state = DeviceLifecycleState.RUNNING

        stopped = ctrl.stop_app(serial="emulator-5554", package_name="com.nrai.test")
        self.assertTrue(stopped)
        self.assertEqual(ctrl.current_state, DeviceLifecycleState.READY)
        mock_adb._run_adb.assert_called_with(
            ["-s", "emulator-5554", "shell", "am", "force-stop", "com.nrai.test"],
            timeout=5.0
        )


if __name__ == "__main__":
    unittest.main()
