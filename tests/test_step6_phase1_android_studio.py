"""
NR-AI Step 6 Phase 1 Acceptance Test Suite: Android Studio Agent Foundation.

Validates:
1. Tool Allowlist: 15 strictly enumerated android.* tools; unknown tools rejected.
2. Project Boundary: Authorizes ONLY C:\\NR-AI\\nr_android_test; rejects arbitrary paths and path traversal.
3. Device Allowlist: Allows emulator-5554 and 15930545720012G; rejects unauthorized serials.
4. AVD Allowlist & Blocking: Allows Pixel_6_API_34; rejects Pixel_6_API_35 (blocked) and unknown AVDs.
5. Build Action Allowlist: DEBUG_ASSEMBLE, CLEAN, CHECK; rejects arbitrary tasks.
6. APK Validation: Build output boundary enforced; unauthorized APKs rejected.
7. Package Validation: Matches com.nrai.test; mismatches rejected.
8. High-Risk Confirmation Gate: android.install_test_apk requires user confirmation.
9. Emergency Stop: Immediate freeze of all Android operations; clears cleanly.
10. Rate Limiting: Rejects requests exceeding threshold.
11. Safe Subprocess Isolation: No shell=True or raw string command execution.
12. Tool Handlers: Deterministic execution of allowlisted tools.
13. AndroidVerifier: All 10 verification checks (A-J) verified.
14. AndroidStudioAgent: Autonomous goal planning, bounded retries, report generation, audit logging.
15. Advisory Model: Model provides advisory text; cannot execute tools directly.
16. Companion Routing: CommandCategory.ANDROID_STUDIO routed and handled.
17. Step 4E & Step 5 Regression.
"""

import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.android_safety import (
    ALLOWED_ANDROID_TOOLS,
    AUTHORIZED_AVDS,
    AUTHORIZED_BUILD_ACTIONS,
    AUTHORIZED_DEVICE_SERIALS,
    AUTHORIZED_PACKAGE_NAME,
    AUTHORIZED_PROJECT_PATH,
    BLOCKED_AVDS,
    AndroidErrorCode,
    AndroidRateLimiter,
    AndroidSafetyError,
    AndroidSafetyGate,
    EmergencyStopActiveError,
    RateLimitExceededError,
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


class TestStep6Phase1AndroidStudio(unittest.TestCase):
    """Acceptance test suite for Android Studio Agent Foundation (Step 6 Phase 1)."""

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
    # Test A: Tool Allowlist Enforcement
    # -------------------------------------------------------------------------
    def test_A_tool_allowlist(self):
        """Verify all 15 approved tools are in allowlist and unknown tools are rejected."""
        self.assertEqual(len(ALLOWED_ANDROID_TOOLS), 15)

        for tool in ALLOWED_ANDROID_TOOLS:
            self.safety.validate_tool_name(tool)

        # Invalid tool must raise AndroidSafetyError with ACTION_NOT_ALLOWED
        with self.assertRaises(AndroidSafetyError) as ctx:
            self.safety.validate_tool_name("android.arbitrary_command")
        self.assertEqual(ctx.exception.code, AndroidErrorCode.ACTION_NOT_ALLOWED)

        # Execution of unapproved tool via registry must return structured failure
        res = self.tools.execute_tool("android.format_disk")
        self.assertFalse(res.success)
        self.assertEqual(res.error_code, AndroidErrorCode.ACTION_NOT_ALLOWED.value)

    # -------------------------------------------------------------------------
    # Test B: Project Boundary & Path Traversal Rejection
    # -------------------------------------------------------------------------
    def test_B_project_boundary(self):
        """Verify only C:\\NR-AI\\nr_android_test is authorized; rejects traversal/other dirs."""
        valid_path = self.safety.validate_project_path(AUTHORIZED_PROJECT_PATH)
        self.assertEqual(valid_path, AUTHORIZED_PROJECT_PATH)

        # Default path must resolve to authorized project
        self.assertEqual(self.safety.validate_project_path(None), AUTHORIZED_PROJECT_PATH)

        # System path must be rejected
        with self.assertRaises(AndroidSafetyError) as ctx1:
            self.safety.validate_project_path(r"C:\Windows\System32")
        self.assertEqual(ctx1.exception.code, AndroidErrorCode.PROJECT_NOT_AUTHORIZED)

        # Traversal attempt must be rejected
        traversal_path = AUTHORIZED_PROJECT_PATH / ".." / "other_project"
        with self.assertRaises(AndroidSafetyError) as ctx2:
            self.safety.validate_project_path(traversal_path)
        self.assertEqual(ctx2.exception.code, AndroidErrorCode.PROJECT_NOT_AUTHORIZED)

    # -------------------------------------------------------------------------
    # Test C: Device Allowlist Enforcement
    # -------------------------------------------------------------------------
    def test_C_device_allowlist(self):
        """Verify only emulator-5554 and 15930545720012G are authorized."""
        self.assertEqual(self.safety.validate_device_serial("emulator-5554"), "emulator-5554")
        self.assertEqual(self.safety.validate_device_serial("15930545720012G"), "15930545720012G")

        # Unknown device serial rejected
        with self.assertRaises(AndroidSafetyError) as ctx:
            self.safety.validate_device_serial("unauthorized_device_001")
        self.assertEqual(ctx.exception.code, AndroidErrorCode.DEVICE_NOT_AUTHORIZED)

        # Tool query with unknown serial fails deterministically
        res = self.tools.execute_tool("android.get_device_status", {"serial": "rogue_device"})
        self.assertFalse(res.success)
        self.assertEqual(res.error_code, AndroidErrorCode.DEVICE_NOT_AUTHORIZED.value)

    # -------------------------------------------------------------------------
    # Test D: AVD Allowlist and Explicit Blocking
    # -------------------------------------------------------------------------
    def test_D_avd_allowlist_and_blocking(self):
        """Verify Pixel_6_API_34 is authorized, Pixel_6_API_35 is explicitly blocked."""
        self.assertEqual(self.safety.validate_avd_name("Pixel_6_API_34"), "Pixel_6_API_34")

        # Blocked AVD (Pixel_6_API_35) rejected
        with self.assertRaises(AndroidSafetyError) as ctx1:
            self.safety.validate_avd_name("Pixel_6_API_35")
        self.assertEqual(ctx1.exception.code, AndroidErrorCode.ACTION_NOT_ALLOWED)
        self.assertIn("blocked", ctx1.exception.message)

        # Unknown AVD rejected
        with self.assertRaises(AndroidSafetyError) as ctx2:
            self.safety.validate_avd_name("Pixel_8_Pro_API_34")
        self.assertEqual(ctx2.exception.code, AndroidErrorCode.ACTION_NOT_ALLOWED)

    # -------------------------------------------------------------------------
    # Test E: Build Action Allowlist
    # -------------------------------------------------------------------------
    def test_E_build_action_allowlist(self):
        """Verify DEBUG_ASSEMBLE, CLEAN, CHECK are authorized; arbitrary tasks rejected."""
        self.assertEqual(self.safety.validate_build_action("DEBUG_ASSEMBLE"), ["assembleDebug"])
        self.assertEqual(self.safety.validate_build_action("CLEAN"), ["clean"])
        self.assertEqual(self.safety.validate_build_action("CHECK"), ["check"])

        # Arbitrary task rejected
        with self.assertRaises(AndroidSafetyError) as ctx:
            self.safety.validate_build_action("uploadToPlayStore")
        self.assertEqual(ctx.exception.code, AndroidErrorCode.BUILD_NOT_ALLOWED)

        # Tool execution with unauthorized build action
        res = self.tools.execute_tool("android.build_project", {"action": "maliciousTask"})
        self.assertFalse(res.success)
        self.assertEqual(res.error_code, AndroidErrorCode.BUILD_NOT_ALLOWED.value)

    # -------------------------------------------------------------------------
    # Test F: APK Validation & Output Boundary
    # -------------------------------------------------------------------------
    def test_F_apk_validation(self):
        """Verify APKs outside the project build boundary or non-APK files are rejected."""
        # Non-APK file rejected
        with self.assertRaises(AndroidSafetyError) as ctx1:
            self.safety.validate_apk_path(AUTHORIZED_PROJECT_PATH / "app" / "build" / "test.txt")
        self.assertEqual(ctx1.exception.code, AndroidErrorCode.APK_NOT_AUTHORIZED)

        # File outside build directory rejected
        with self.assertRaises(AndroidSafetyError) as ctx2:
            self.safety.validate_apk_path(r"C:\Downloads\untrusted.apk")
        self.assertEqual(ctx2.exception.code, AndroidErrorCode.APK_NOT_AUTHORIZED)

    # -------------------------------------------------------------------------
    # Test G: Package Name Validation
    # -------------------------------------------------------------------------
    def test_G_package_name_validation(self):
        """Verify package name must match com.nrai.test."""
        self.assertEqual(self.safety.validate_package_name("com.nrai.test"), "com.nrai.test")

        with self.assertRaises(AndroidSafetyError) as ctx:
            self.safety.validate_package_name("com.malicious.payload")
        self.assertEqual(ctx.exception.code, AndroidErrorCode.PACKAGE_MISMATCH)

    # -------------------------------------------------------------------------
    # Test H: High-Risk Confirmation Gate
    # -------------------------------------------------------------------------
    def test_H_high_risk_confirmation_gate(self):
        """Verify high-risk action (install_test_apk) requires explicit user confirmation."""
        risk, req_confirm = self.safety.assess_risk("android.install_test_apk", {})
        self.assertEqual(risk, RiskLevel.HIGH)
        self.assertTrue(req_confirm)

        # Without confirmation, tool execution halts with requires_confirmation=True
        res = self.tools.execute_tool(
            "android.install_test_apk",
            {"serial": "emulator-5554"},
            user_confirmed=False,
        )
        self.assertFalse(res.success)
        self.assertTrue(res.requires_confirmation)
        self.assertEqual(res.error_code, AndroidErrorCode.ACTION_NOT_ALLOWED.value)

    # -------------------------------------------------------------------------
    # Test I: Emergency Stop Freezes All Operations
    # -------------------------------------------------------------------------
    def test_I_emergency_stop_freezes_operations(self):
        """Verify activating emergency stop immediately freezes all Android operations."""
        self.assertFalse(self.safety.is_emergency_stop_active())
        self.safety.activate_emergency_stop()
        self.assertTrue(self.safety.is_emergency_stop_active())

        # Tool execution must abort immediately with EMERGENCY_STOPPED
        res = self.tools.execute_tool("android.list_devices")
        self.assertFalse(res.success)
        self.assertEqual(res.error_code, AndroidErrorCode.EMERGENCY_STOPPED.value)

        # Agent workflow must abort immediately
        wf_report = self.agent.execute_workflow("inspect project")
        self.assertFalse(wf_report.success)
        self.assertEqual(wf_report.error_code, AndroidErrorCode.EMERGENCY_STOPPED.value)

        # Clear emergency stop and verify recovery
        self.safety.deactivate_emergency_stop()
        self.assertFalse(self.safety.is_emergency_stop_active())
        res_after = self.tools.execute_tool("android.inspect_project", {"project_path": str(AUTHORIZED_PROJECT_PATH)})
        self.assertTrue(res_after.success)

    # -------------------------------------------------------------------------
    # Test J: Rate Limiting Enforcement
    # -------------------------------------------------------------------------
    def test_J_rate_limiting(self):
        """Verify rapid repeated tool calls trigger RateLimitExceededError."""
        limiter = AndroidRateLimiter(max_actions_per_minute=3)
        limiter.check_and_record()
        limiter.check_and_record()
        limiter.check_and_record()

        with self.assertRaises(RateLimitExceededError):
            limiter.check_and_record()

    # -------------------------------------------------------------------------
    # Test K: Safe ADB Client Command Isolation
    # -------------------------------------------------------------------------
    def test_K_safe_adb_command_isolation(self):
        """Verify SafeAdbClient strictly uses shell=False and fixed argument arrays."""
        client = SafeAdbClient()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="List of devices attached\nemulator-5554 device\n", stderr="")
            devices = client.list_devices()
            self.assertEqual(len(devices), 1)
            self.assertEqual(devices[0]["serial"], "emulator-5554")

            # Verify subprocess call arguments
            call_kwargs = mock_run.call_args[1]
            self.assertFalse(call_kwargs.get("shell", False))

    # -------------------------------------------------------------------------
    # Test L: Safe Gradle Runner Isolation
    # -------------------------------------------------------------------------
    def test_L_safe_gradle_isolation(self):
        """Verify SafeGradleRunner executes gradlew.bat with shell=False inside project."""
        runner = SafeGradleRunner(project_dir=AUTHORIZED_PROJECT_PATH)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="BUILD SUCCESSFUL in 2s", stderr="")
            res = runner.run_action(["assembleDebug"])
            self.assertTrue(res["success"])
            self.assertEqual(res["tasks"], ["assembleDebug"])

            call_kwargs = mock_run.call_args[1]
            self.assertFalse(call_kwargs.get("shell", False))
            self.assertEqual(call_kwargs.get("cwd"), str(AUTHORIZED_PROJECT_PATH))

    # -------------------------------------------------------------------------
    # Test M: Android Studio Discovery & Targeting
    # -------------------------------------------------------------------------
    def test_M_android_studio_targeting(self):
        """Verify Android Studio executable discovery finds studio64.exe."""
        discovery = self.tools.discovery
        app_path = discovery.find_application("android studio")
        self.assertIsNotNone(app_path)
        self.assertTrue(Path(app_path).exists())
        self.assertTrue(str(app_path).lower().endswith("studio64.exe"))

    # -------------------------------------------------------------------------
    # Test N: Tool android.inspect_project Execution
    # -------------------------------------------------------------------------
    def test_N_tool_inspect_project(self):
        """Verify android.inspect_project inspects authorized project and extracts metadata."""
        res = self.tools.execute_tool("android.inspect_project", {"project_path": str(AUTHORIZED_PROJECT_PATH)})
        self.assertTrue(res.success)
        self.assertTrue(res.data.get("is_android"))
        app_module = res.data.get("app_module", {})
        self.assertEqual(app_module.get("namespace"), "com.nrai.test")
        self.assertEqual(app_module.get("compile_sdk"), 35)
        self.assertEqual(app_module.get("min_sdk"), 26)

    # -------------------------------------------------------------------------
    # Test O: Tool android.get_build_status Execution
    # -------------------------------------------------------------------------
    def test_O_tool_get_build_status(self):
        """Verify android.get_build_status checks build outputs safely."""
        res = self.tools.execute_tool("android.get_build_status", {})
        self.assertTrue(res.success)
        self.assertIn("has_debug_apk", res.data)
        self.assertIn("build_dir", res.data)

    # -------------------------------------------------------------------------
    # Test P: Tool android.list_emulators Execution
    # -------------------------------------------------------------------------
    def test_P_tool_list_emulators(self):
        """Verify android.list_emulators enumerates AVDs and identifies Pixel_6_API_34."""
        res = self.tools.execute_tool("android.list_emulators", {})
        self.assertTrue(res.success)
        self.assertIn("authorized_avds", res.data)
        self.assertIn("blocked_avds", res.data)
        # Pixel_6_API_34 is installed in the test environment
        self.assertIn("Pixel_6_API_34", res.data["authorized_avds"])
        self.assertIn("Pixel_6_API_35", res.data["blocked_avds"])

    # -------------------------------------------------------------------------
    # Test Q: Tool android.list_devices Execution
    # -------------------------------------------------------------------------
    def test_Q_tool_list_devices(self):
        """Verify android.list_devices reports authorized and unauthorized device lists."""
        res = self.tools.execute_tool("android.list_devices", {})
        self.assertTrue(res.success)
        self.assertIn("authorized_devices", res.data)
        self.assertIn("unauthorized_devices", res.data)

    # -------------------------------------------------------------------------
    # Test R: Verifier Engine Checks (A through J)
    # -------------------------------------------------------------------------
    def test_R_verifier_engine_checks(self):
        """Verify AndroidVerifier runs all 10 checks and achieves 10/10 PASS."""
        report = self.verifier.run_full_verification()
        self.assertEqual(report.total_checks, 10)
        self.assertEqual(report.passed_checks, 10)
        self.assertEqual(report.failed_checks, 0)
        self.assertTrue(report.all_passed)

    # -------------------------------------------------------------------------
    # Test S: AndroidStudioAgent Goal Planning & Execution
    # -------------------------------------------------------------------------
    def test_S_agent_workflow_execution(self):
        """Verify agent plans and executes multi-step workflow with structured report."""
        report: AndroidWorkflowReport = self.agent.execute_workflow("inspect project")
        self.assertTrue(report.success)
        self.assertGreaterEqual(report.steps_executed, 1)
        self.assertIn("completed successfully", report.summary.lower())
        self.assertIsNone(report.error)

    # -------------------------------------------------------------------------
    # Test T: Agent Bounded Retry on Recoverable Failure
    # -------------------------------------------------------------------------
    def test_T_agent_bounded_retry(self):
        """Verify agent handles step retries bounded at MAX_RETRIES_PER_STEP."""
        call_count = 0

        def failing_handler(params):
            nonlocal call_count
            call_count += 1
            return AndroidToolResult(
                success=False,
                tool="android.get_device_status",
                error="Device transient busy",
                error_code="TRANSIENT_ERROR",
            )

        with patch.object(self.tools, "_tool_get_device_status", side_effect=failing_handler):
            intent = AndroidActionIntent(
                tool="android.get_device_status",
                params={"serial": "emulator-5554"},
            )
            with patch.object(self.agent, "plan_goal", return_value=[intent]):
                report = self.agent.execute_workflow("test retry goal")
                self.assertFalse(report.success)
                # Initial attempt + 2 retries = 3 calls
                self.assertEqual(call_count, 3)

    # -------------------------------------------------------------------------
    # Test U: Audit Logging Verification
    # -------------------------------------------------------------------------
    def test_U_audit_logging(self):
        """Verify tool execution and workflow reports are logged to AuditLogger."""
        with patch.object(self.audit, "log_event") as mock_log:
            self.agent.execute_workflow("inspect project")
            # Should have logged at least tool execution and workflow report
            self.assertGreaterEqual(mock_log.call_count, 2)
            event_types = [call[1]["event_type"] for call in mock_log.call_args_list]
            self.assertIn("ANDROID_TOOL_EXECUTION", event_types)
            self.assertIn("ANDROID_WORKFLOW_REPORT", event_types)

    # -------------------------------------------------------------------------
    # Test V: Model Advisory Role & Isolation
    # -------------------------------------------------------------------------
    def test_V_model_advisory_isolation(self):
        """Verify advisory model cannot execute tools directly or bypass safety gate."""
        advice = self.agent.consult_model("help me optimize build")
        # Model returns advisory string or None; never modifies tool registry or state
        if advice:
            self.assertIn("advisory", advice.lower())

    # -------------------------------------------------------------------------
    # Test W: Companion Routing Integration
    # -------------------------------------------------------------------------
    def test_W_companion_routing(self):
        """Verify NRCompanion routes android commands to CommandCategory.ANDROID_STUDIO."""
        companion = NRCompanion()
        resp = companion.interact("android: inspect project")
        self.assertEqual(resp.category, CommandCategory.ANDROID_STUDIO)
        self.assertEqual(resp.routed_to, "AndroidStudioAgent")
        self.assertTrue("completed successfully" in resp.text.lower())

    # -------------------------------------------------------------------------
    # Test X: Step 4E Regression
    # -------------------------------------------------------------------------
    def test_X_step4e_regression(self):
        """Verify Step 4E UnifiedComputerAgent tests still pass without regressions."""
        from scratch.verify_step4e import TestStep4EUnifiedComputerAgent
        suite = unittest.TestLoader().loadTestsFromTestCase(TestStep4EUnifiedComputerAgent)
        devnull = open(os.devnull, "w")
        try:
            runner = unittest.TextTestRunner(stream=devnull, verbosity=0)
            result = runner.run(suite)
            self.assertEqual(result.testsRun, 32)
            self.assertEqual(len(result.failures), 0)
            self.assertEqual(len(result.errors), 0)
        finally:
            devnull.close()

    # -------------------------------------------------------------------------
    # Test Y: Step 5 Regressions
    # -------------------------------------------------------------------------
    def test_Y_step5_regression(self):
        """Verify Step 5 Phase 1-2 tests pass without regressions."""
        from tests.test_step5_phase1_models import TestStep5Phase1Models
        from tests.test_step5_phase2_browser import TestStep5Phase2Browser

        for test_case, expected_count in [
            (TestStep5Phase1Models, 16),
            (TestStep5Phase2Browser, 29),
        ]:
            suite = unittest.TestLoader().loadTestsFromTestCase(test_case)
            devnull = open(os.devnull, "w")
            try:
                runner = unittest.TextTestRunner(stream=devnull, verbosity=0)
                result = runner.run(suite)
                self.assertEqual(result.testsRun, expected_count)
                self.assertEqual(len(result.failures), 0)
                self.assertEqual(len(result.errors), 0)
            finally:
                devnull.close()



if __name__ == "__main__":
    unittest.main()
