"""
NR-AI Step 6 Phase 2 Acceptance Test Suite: Android Build -> Deploy -> Run -> Verify Loop.

Validates:
A. Build allowlist: DEBUG_ASSEMBLE, CLEAN, CHECK only; arbitrary tasks rejected.
B. Project boundary: strictly inside C:\\NR-AI\\nr_android_test.
C. APK boundary: resides inside authorized build directory.
D. APK package validation: matches com.nrai.test; mismatch rejected.
E. Device allowlist: allows emulator-5554 and 15930545720012G.
F. Unknown device rejection: DEVICE_NOT_AUTHORIZED returned.
G. Installation confirmation gate: INSTALL_CONFIRMATION_REQUIRED when unconfirmed.
H. Emergency stop: terminates pipeline immediately at any stage.
I. Build timeout: bounded timeout enforcement and diagnostics.
J. Bounded retries: maximum 2 retries on recoverable failures.
K. Launch package allowlist: allows only com.nrai.test.
L. Installation verification: ground truth package check.
M. Launch verification: process PID and running state check.
N. Logcat package filtering: filtered strictly to com.nrai.test.
O. Audit logging: structured events recorded in AuditLogger.
P. Model advisory isolation: advisory model cannot execute tools or OS directly.
Q. Full deterministic workflow: complete Build -> Deploy -> Run -> Verify loop.
R. Step 6 Phase 1 regression: existing Phase 1 checks pass.
S. Step 5 regression: browser safety & agent pass.
T. Step 4E regression: computer agent safety passes.
"""

from pathlib import Path
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.android_safety import (
    ALLOWED_ANDROID_TOOLS,
    AUTHORIZED_BUILD_ACTIONS,
    AUTHORIZED_DEVICE_SERIALS,
    AUTHORIZED_PACKAGE_NAME,
    AUTHORIZED_PROJECT_PATH,
    AndroidErrorCode,
    AndroidSafetyError,
    AndroidSafetyGate,
    EmergencyStopActiveError,
    RiskLevel,
)
from app.agent.android_studio_agent import (
    AndroidActionIntent,
    AndroidStudioAgent,
    AndroidWorkflowReport,
)
from app.agent.android_tools import (
    AndroidToolRegistry,
    AndroidToolResult,
    SafeAdbClient,
    SafeEmulatorManager,
    SafeGradleRunner,
)
from app.agent.android_verifier import (
    AndroidVerificationReport,
    AndroidVerifier,
    CheckResult,
)
from app.brain.companion import CommandCategory, NRCompanion
from app.memory.audit_logger import AuditLogger


class TestStep6Phase2AndroidDeploy(unittest.TestCase):
    """Acceptance test suite for Step 6 Phase 2 Android Build -> Deploy -> Run -> Verify."""

    def setUp(self):
        AndroidSafetyGate.deactivate_emergency_stop()
        self.audit = AuditLogger()
        self.safety = AndroidSafetyGate()
        self.tools = AndroidToolRegistry(safety_gate=self.safety, audit_logger=self.audit)
        self.verifier = AndroidVerifier(safety_gate=self.safety, tool_registry=self.tools)
        self.agent = AndroidStudioAgent(
            tool_registry=self.tools,
            verifier=self.verifier,
            safety_gate=self.safety,
            audit_logger=self.audit,
        )

    def tearDown(self):
        AndroidSafetyGate.deactivate_emergency_stop()

    # -------------------------------------------------------------------------
    # Test A: Build Allowlist Enforcement
    # -------------------------------------------------------------------------
    def test_A_build_allowlist(self):
        """Verify only DEBUG_ASSEMBLE, CLEAN, CHECK are authorized; arbitrary tasks rejected."""
        self.assertIn("DEBUG_ASSEMBLE", AUTHORIZED_BUILD_ACTIONS)
        self.assertIn("CLEAN", AUTHORIZED_BUILD_ACTIONS)
        self.assertIn("CHECK", AUTHORIZED_BUILD_ACTIONS)
        self.assertEqual(len(AUTHORIZED_BUILD_ACTIONS), 3)

        # Authorized task returns valid gradle arguments
        args = self.safety.validate_build_action("DEBUG_ASSEMBLE")
        self.assertEqual(args, ["assembleDebug"])

        # Arbitrary tasks must be rejected
        for forbidden in ("assembleRelease", "bundleRelease", "publish", "uploadApk", "customTask"):
            with self.assertRaises(AndroidSafetyError) as ctx:
                self.safety.validate_build_action(forbidden)
            self.assertEqual(ctx.exception.code, AndroidErrorCode.BUILD_NOT_ALLOWED)

    # -------------------------------------------------------------------------
    # Test B: Project Boundary Enforcement
    # -------------------------------------------------------------------------
    def test_B_project_boundary(self):
        """Verify project path resolves strictly inside C:\\NR-AI\\nr_android_test."""
        valid_path = self.safety.validate_project_path(AUTHORIZED_PROJECT_PATH)
        self.assertEqual(valid_path, AUTHORIZED_PROJECT_PATH)

        # Traversal and external paths rejected
        for bad_path in (
            r"C:\Windows\System32",
            r"C:\NR-AI\nr_android_test\..\..",
            r"C:\NR-AI\app",
            r"C:\other_project",
        ):
            with self.assertRaises(AndroidSafetyError) as ctx:
                self.safety.validate_project_path(bad_path)
            self.assertEqual(ctx.exception.code, AndroidErrorCode.PROJECT_NOT_AUTHORIZED)

    # -------------------------------------------------------------------------
    # Test C: APK Boundary Enforcement
    # -------------------------------------------------------------------------
    def test_C_apk_boundary(self):
        """Verify APK path must reside in authorized project build directory."""
        apk_dir = AUTHORIZED_PROJECT_PATH / "app" / "build" / "outputs" / "apk" / "debug"
        apk_file = apk_dir / "app-debug.apk"

        if apk_file.exists():
            validated = self.safety.validate_apk_path(apk_file)
            self.assertEqual(validated, apk_file.resolve())

        # External APK rejected
        with self.assertRaises(AndroidSafetyError) as ctx1:
            self.safety.validate_apk_path(r"C:\Downloads\malware.apk")
        self.assertEqual(ctx1.exception.code, AndroidErrorCode.APK_NOT_AUTHORIZED)

        # Non-APK file rejected
        with self.assertRaises(AndroidSafetyError) as ctx2:
            self.safety.validate_apk_path(AUTHORIZED_PROJECT_PATH / "app" / "build" / "test.txt")
        self.assertEqual(ctx2.exception.code, AndroidErrorCode.APK_NOT_AUTHORIZED)

    # -------------------------------------------------------------------------
    # Test D: APK Package Validation
    # -------------------------------------------------------------------------
    def test_D_apk_package_validation(self):
        """Verify package identity must be com.nrai.test; mismatch rejected."""
        self.assertEqual(self.safety.validate_package_name("com.nrai.test"), "com.nrai.test")

        for bad_pkg in ("com.android.settings", "com.evil.app", "com.nrai.other", "org.test"):
            with self.assertRaises(AndroidSafetyError) as ctx:
                self.safety.validate_package_name(bad_pkg)
            self.assertEqual(ctx.exception.code, AndroidErrorCode.PACKAGE_MISMATCH)

    # -------------------------------------------------------------------------
    # Test E: Device Allowlist Enforcement
    # -------------------------------------------------------------------------
    def test_E_device_allowlist(self):
        """Verify only emulator-5554 and 15930545720012G are allowed."""
        self.assertIn("emulator-5554", AUTHORIZED_DEVICE_SERIALS)
        self.assertIn("15930545720012G", AUTHORIZED_DEVICE_SERIALS)
        self.assertEqual(len(AUTHORIZED_DEVICE_SERIALS), 2)

        self.assertEqual(self.safety.validate_device_serial("emulator-5554"), "emulator-5554")
        self.assertEqual(self.safety.validate_device_serial("15930545720012G"), "15930545720012G")

    # -------------------------------------------------------------------------
    # Test F: Unknown Device Rejection
    # -------------------------------------------------------------------------
    def test_F_unknown_device_rejection(self):
        """Verify arbitrary/rogue devices trigger DEVICE_NOT_AUTHORIZED."""
        for rogue in ("emulator-5556", "pixel_device_999", "192.168.1.50:5555", "unknown_serial"):
            with self.assertRaises(AndroidSafetyError) as ctx:
                self.safety.validate_device_serial(rogue)
            self.assertEqual(ctx.exception.code, AndroidErrorCode.DEVICE_NOT_AUTHORIZED)

        # Pipeline with unknown device serial stops with DEVICE_NOT_AUTHORIZED
        rep = self.agent.execute_pipeline(serial="rogue_device", user_confirmed=True)
        self.assertFalse(rep.success)
        self.assertEqual(rep.error_code, AndroidErrorCode.DEVICE_NOT_AUTHORIZED.value)

    # -------------------------------------------------------------------------
    # Test G: Installation Confirmation Gate
    # -------------------------------------------------------------------------
    def test_G_installation_confirmation_gate(self):
        """Verify high-risk APK installation strictly requires user confirmation."""
        # Unconfirmed installation tool call
        res = self.tools.execute_tool("android.install_test_apk", {"serial": "emulator-5554"}, user_confirmed=False)
        self.assertFalse(res.success)
        self.assertTrue(res.requires_confirmation)

        # Unconfirmed pipeline execution stops at confirmation gate
        with patch.object(self.tools, "execute_tool") as mock_exec:
            mock_exec.side_effect = [
                AndroidToolResult(success=True, tool="android.build_project", data={"success": True}),
                AndroidToolResult(
                    success=True,
                    tool="android.get_build_status",
                    data={
                        "has_debug_apk": True,
                        "apk_info": {
                            "path": str(AUTHORIZED_PROJECT_PATH / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"),
                            "name": "app-debug.apk",
                        },
                    },
                ),
                AndroidToolResult(
                    success=True,
                    tool="android.list_devices",
                    data={"authorized_devices": [{"serial": "emulator-5554"}]},
                ),
            ]
            rep = self.agent.execute_pipeline(serial="emulator-5554", user_confirmed=False)
            self.assertFalse(rep.success)
            self.assertTrue(rep.requires_confirmation)
            self.assertEqual(rep.error_code, AndroidErrorCode.INSTALL_CONFIRMATION_REQUIRED.value)
            self.assertIn("INSTALL_CONFIRMATION_REQUIRED", rep.summary)

    # -------------------------------------------------------------------------
    # Test H: Emergency Stop Freezes Operations
    # -------------------------------------------------------------------------
    def test_H_emergency_stop(self):
        """Verify activating emergency stop terminates pipeline and tool operations immediately."""
        AndroidSafetyGate.activate_emergency_stop()
        self.assertTrue(AndroidSafetyGate.is_emergency_stop_active())

        # Tool execution frozen
        res = self.tools.execute_tool("android.build_project", {"action": "DEBUG_ASSEMBLE"})
        self.assertFalse(res.success)
        self.assertEqual(res.error_code, AndroidErrorCode.EMERGENCY_STOPPED.value)

        # Pipeline execution frozen
        rep = self.agent.execute_pipeline(serial="emulator-5554", user_confirmed=True)
        self.assertFalse(rep.success)
        self.assertEqual(rep.error_code, AndroidErrorCode.EMERGENCY_STOPPED.value)
        self.assertIn("EMERGENCY STOP", rep.summary)

    # -------------------------------------------------------------------------
    # Test I: Build Timeout Enforcement & Diagnostics
    # -------------------------------------------------------------------------
    def test_I_build_timeout(self):
        """Verify build timeouts return bounded diagnostics with BUILD_FAILED."""
        with patch.object(self.tools.gradle, "run_action") as mock_run:
            mock_run.return_value = {
                "success": False,
                "returncode": -1,
                "output_sample": "Gradle build execution timed out.",
                "diagnosis": {"category": "timeout", "diagnosis": "Gradle build exceeded timeout limit."},
            }
            rep = self.agent.execute_pipeline(serial="emulator-5554", user_confirmed=True)
            self.assertFalse(rep.success)
            self.assertEqual(rep.error_code, AndroidErrorCode.BUILD_FAILED.value)
            self.assertIn("Build failed", rep.summary)

    # -------------------------------------------------------------------------
    # Test J: Bounded Retries (Maximum 2)
    # -------------------------------------------------------------------------
    def test_J_bounded_retries(self):
        """Verify recoverable failures (install/launch) retry at most 2 times."""
        with patch.object(self.tools, "execute_tool") as mock_exec:
            apk_path = str(AUTHORIZED_PROJECT_PATH / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk")
            mock_exec.side_effect = [
                AndroidToolResult(success=True, tool="android.build_project", data={"success": True}),
                AndroidToolResult(success=True, tool="android.get_build_status", data={"has_debug_apk": True, "apk_info": {"path": apk_path}}),
                AndroidToolResult(success=True, tool="android.list_devices", data={"authorized_devices": [{"serial": "emulator-5554"}]}),
                # Install attempts (1 initial + 2 retries = 3 calls)
                AndroidToolResult(success=False, tool="android.install_test_apk", error="Device busy", error_code="INSTALL_FAILED"),
                AndroidToolResult(success=False, tool="android.install_test_apk", error="Device busy", error_code="INSTALL_FAILED"),
                AndroidToolResult(success=False, tool="android.install_test_apk", error="Device busy", error_code="INSTALL_FAILED"),
            ]
            rep = self.agent.execute_pipeline(serial="emulator-5554", user_confirmed=True)
            self.assertFalse(rep.success)
            self.assertEqual(rep.error_code, AndroidErrorCode.INSTALL_FAILED.value)

            install_calls = [c for c in mock_exec.call_args_list if c[0][0] == "android.install_test_apk"]
            self.assertEqual(len(install_calls), 3)

    # -------------------------------------------------------------------------
    # Test K: Launch Package Allowlist
    # -------------------------------------------------------------------------
    def test_K_launch_package_allowlist(self):
        """Verify launch tool rejects arbitrary packages and allows only com.nrai.test."""
        res = self.tools.execute_tool("android.launch_app", {"serial": "emulator-5554", "package_name": "com.rogue.app"})
        self.assertFalse(res.success)
        self.assertEqual(res.error_code, AndroidErrorCode.PACKAGE_MISMATCH.value)

        with patch.object(self.tools.adb, "is_package_installed", return_value=True), \
             patch.object(self.tools.adb, "launch_package", return_value=True):
            res_ok = self.tools.execute_tool("android.launch_app", {"serial": "emulator-5554", "package_name": "com.nrai.test"})
            self.assertTrue(res_ok.success)

    # -------------------------------------------------------------------------
    # Test L: Installation Verification Check
    # -------------------------------------------------------------------------
    def test_L_installation_verification(self):
        """Verify package installation check queries package manager correctly."""
        with patch.object(self.tools.adb, "_run_adb") as mock_adb:
            mock_adb.return_value = (0, "package:com.nrai.test\n", "")
            installed = self.tools.adb.is_package_installed("emulator-5554", "com.nrai.test")
            self.assertTrue(installed)

            mock_adb.return_value = (0, "", "")
            not_installed = self.tools.adb.is_package_installed("emulator-5554", "com.nrai.test")
            self.assertFalse(not_installed)

    # -------------------------------------------------------------------------
    # Test M: Launch & Process Running State Verification
    # -------------------------------------------------------------------------
    def test_M_launch_verification(self):
        """Verify process PID queries and running state check."""
        def fake_adb(args, **kwargs):
            cmd = " ".join(args)
            if "pm" in cmd or "list" in cmd:
                return (0, "package:com.nrai.test\n", "")
            elif "pidof" in cmd:
                return (0, "12345\n", "")
            elif "get-state" in cmd:
                return (0, "device\n", "")
            return (0, "", "")

        with patch.object(self.tools.adb, "_run_adb", side_effect=fake_adb):
            pid = self.tools.adb.get_process_pid("emulator-5554", "com.nrai.test")
            self.assertEqual(pid, 12345)

            state = self.tools.adb.get_app_state("emulator-5554", "com.nrai.test")
            self.assertTrue(state["installed"])
            self.assertTrue(state["is_running"])
            self.assertEqual(state["pid"], 12345)
            self.assertEqual(state["device_status"], "device")

    # -------------------------------------------------------------------------
    # Test N: Logcat Package Filtering
    # -------------------------------------------------------------------------
    def test_N_logcat_package_filtering(self):
        """Verify logcat output is filtered strictly to com.nrai.test."""
        dummy_log = (
            "09-11 10:00:00.000  1000  1000 D SystemServer: System ready\n"
            "09-11 10:00:01.000  1234  1234 D com.nrai.test: MainActivity onCreate\n"
            "09-11 10:00:02.000   500   500 D OtherApp: unrelated log\n"
            "09-11 10:00:03.000  1234  1234 I com.nrai.test: App running\n"
        )
        with patch.object(self.tools.adb, "_run_adb", return_value=(0, dummy_log, "")):
            filtered = self.tools.adb.capture_logcat("emulator-5554", lines=100, filter_package="com.nrai.test")
            lines = filtered.splitlines()
            self.assertEqual(len(lines), 2)
            self.assertTrue(all("com.nrai.test" in l for l in lines))
            self.assertNotIn("SystemServer", filtered)
            self.assertNotIn("OtherApp", filtered)

    # -------------------------------------------------------------------------
    # Test O: Audit Logging
    # -------------------------------------------------------------------------
    def test_O_audit_logging(self):
        """Verify structured audit logging is generated for pipeline actions."""
        with patch.object(self.audit, "log_event") as mock_log:
            self.agent.execute_pipeline(serial="emulator-5554", user_confirmed=False)
            mock_log.assert_called()
            event_types = [c[1]["event_type"] for c in mock_log.call_args_list]
            self.assertIn("ANDROID_WORKFLOW_REPORT", event_types)

    # -------------------------------------------------------------------------
    # Test P: Model Advisory Isolation
    # -------------------------------------------------------------------------
    def test_P_model_advisory_isolation(self):
        """Verify advisory models remain purely advisory and cannot bypass gates."""
        with patch.object(self.agent.router, "route_with_capabilities") as mock_route:
            mock_route.return_value = MagicMock(provider="gemini", model_id="gemini-1.5-pro")
            advice = self.agent.consult_model("Deploy com.nrai.test to emulator")
            self.assertIsNotNone(advice)
            self.assertIn("advisory", advice.lower())

        intent = AndroidActionIntent(tool="os.system", params={"cmd": "rm -rf /"})
        with self.assertRaises(AndroidSafetyError) as ctx:
            intent.validate(self.safety)
        self.assertEqual(ctx.exception.code, AndroidErrorCode.ACTION_NOT_ALLOWED)

    # -------------------------------------------------------------------------
    # Test Q: Full Deterministic Workflow (Build -> Deploy -> Run -> Verify)
    # -------------------------------------------------------------------------
    def test_Q_full_deterministic_workflow(self):
        """Verify complete end-to-end pipeline execution with ground-truth verification."""
        apk_path = str(AUTHORIZED_PROJECT_PATH / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk")

        with patch.object(self.tools, "execute_tool") as mock_tool, \
             patch.object(self.tools.adb, "get_app_state") as mock_state, \
             patch.object(self.tools.adb, "capture_logcat", return_value="com.nrai.test log"), \
             patch.object(self.verifier, "run_pipeline_verification") as mock_verif:

            mock_tool.side_effect = [
                AndroidToolResult(success=True, tool="android.build_project", data={"success": True, "duration_s": 2.5}),
                AndroidToolResult(success=True, tool="android.get_build_status", data={"has_debug_apk": True, "apk_info": {"path": apk_path}}),
                AndroidToolResult(success=True, tool="android.list_devices", data={"authorized_devices": [{"serial": "emulator-5554"}]}),
                AndroidToolResult(success=True, tool="android.install_test_apk", data={"success": True}),
                AndroidToolResult(success=True, tool="android.launch_app", data={"success": True}),
            ]
            mock_state.return_value = {"installed": True, "is_running": True, "pid": 54321}

            mock_verif.return_value = AndroidVerificationReport(
                total_checks=10,
                passed_checks=10,
                failed_checks=0,
                all_passed=True,
                summary="Android Pipeline Verification: 10/10 checks passed.",
            )

            rep = self.agent.execute_pipeline(serial="emulator-5554", user_confirmed=True)
            self.assertTrue(rep.success)
            self.assertIsNone(rep.error)
            self.assertIn("Android pipeline completed successfully", rep.summary)
            self.assertEqual(rep.steps_executed, 7)

    # -------------------------------------------------------------------------
    # Test R: Step 6 Phase 1 Regression
    # -------------------------------------------------------------------------
    def test_R_step6_phase1_regression(self):
        """Verify Step 6 Phase 1 AndroidVerifier checks still pass 10/10."""
        report = self.verifier.run_full_verification()
        self.assertEqual(report.total_checks, 10)
        self.assertEqual(report.passed_checks, 10)
        self.assertTrue(report.all_passed)

    # -------------------------------------------------------------------------
    # Test S: Step 5 Browser Regression
    # -------------------------------------------------------------------------
    def test_S_step5_regression(self):
        """Verify Step 5 safety and consensus systems remain intact."""
        from app.agent.browser_safety import BrowserSafetyGate
        gate = BrowserSafetyGate()
        ok, _ = gate.validate_url("http://127.0.0.1:8585/test")
        self.assertTrue(ok)
        bad_js, _ = gate.validate_url("javascript:alert(1)")
        self.assertFalse(bad_js)
        bad_file, _ = gate.validate_url("file:///C:/Windows")
        self.assertFalse(bad_file)

    # -------------------------------------------------------------------------
    # Test T: Step 4E Regression
    # -------------------------------------------------------------------------
    def test_T_step4e_regression(self):
        """Verify Step 4E UnifiedComputerAgent safety and input freeze remain intact."""
        from app.agent.input_controller import InputController
        controller = InputController()
        controller.emergency_stop()
        self.assertTrue(controller.is_emergency_stopped())
        res = controller.click_target(100, 100)
        self.assertFalse(res.success)
        self.assertEqual(res.error, "EMERGENCY_STOP_ACTIVE")
        controller.reset_emergency_stop()
        self.assertFalse(controller.is_emergency_stopped())


if __name__ == "__main__":
    unittest.main()
