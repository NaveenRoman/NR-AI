r"""
NR-AI Step 10: Companion Natural-Language Development Workflow Test Battery.

Deterministic tests covering:
1. Structured Development Intent parsing & injection defense
2. Separation of single OS open vs complex multi-agent dev workflow
3. Deterministic Android project scaffolding inside C:\NR-AI\dev_projects
4. Path traversal, credential safety, and overwrite protections
5. Safety Gate validation for dev_projects paths
6. Authenticated Emergency Stop reset with operator confirmation
7. CompanionAgentBridge multi-agent routing, audit logging & TTS contracts
8. CompanionOrchestrator natural-language development command ingress
"""

import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from app.agent.android_safety import (
    AndroidErrorCode,
    AndroidSafetyError,
    AndroidSafetyGate,
    AUTHORIZED_PROJECT_PATH,
    DEV_PROJECTS_ROOT,
    EmergencyStopActiveError,
)
from app.agent.android_scaffold import (
    AndroidProjectScaffolder,
    PROHIBITED_FILENAMES,
    PROTECTED_PROJECT_NAMES,
)
from app.agent.android_tools import AndroidToolRegistry, SafeGradleRunner
from app.memory.audit_logger import AuditLogger
from app.remote.auth import SessionManager
from app.remote.companion_agent_bridge import CompanionAgentBridge
from app.remote.companion_client_contract import TTSResponseContract
from app.remote.companion_orchestrator import (
    CompanionCommandResult,
    CompanionOrchestrator,
    CompanionOrchestratorState,
)
from app.remote.dev_intent import (
    DevelopmentIntent,
    DevelopmentIntentParser,
    DevelopmentWorkflowType,
    TargetEnvironment,
)
from app.remote.emergency import EmergencyStopController


class TestStep10CompanionDevWorkflow(unittest.TestCase):
    """Test battery validating safe, natural-language development workflows."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        AndroidSafetyGate.deactivate_emergency_stop()

    # -------------------------------------------------------------------------
    # 1. Intent Parsing & Disambiguation
    # -------------------------------------------------------------------------
    def test_01_parse_development_intent_login_activity(self):
        """Verifies parsing of the live multi-step developer command."""
        cmd = (
            "Open Android Studio and create a new Android project with a Login Activity. "
            "Use Kotlin. Create a simple login screen with username/email, password, "
            "Login button, and basic validation. Build the project and verify that it compiles."
        )
        self.assertTrue(DevelopmentIntentParser.is_development_command(cmd))
        intent = DevelopmentIntentParser.parse_intent(cmd)

        self.assertTrue(intent.is_valid)
        self.assertEqual(intent.target_environment, TargetEnvironment.ANDROID)
        self.assertEqual(intent.workflow_type, DevelopmentWorkflowType.CREATE_PROJECT)
        self.assertEqual(intent.language, "Kotlin")
        self.assertEqual(intent.template, "login_activity")
        self.assertTrue(intent.launch_toolchain)
        self.assertTrue(intent.build_required)
        self.assertEqual(intent.project_name, "DevLoginApp")
        self.assertIn("build_project", intent.requested_actions)

    def test_02_separation_single_open_vs_complex_workflow(self):
        """Ensures simple 'open <app>' is not misclassified as a dev workflow."""
        simple_cmd = "Open Android Studio"
        self.assertFalse(DevelopmentIntentParser.is_development_command(simple_cmd))
        intent = DevelopmentIntentParser.parse_intent(simple_cmd)
        self.assertEqual(intent.workflow_type, DevelopmentWorkflowType.OPEN_APP)

    def test_03_injection_defense_and_prohibited_tokens(self):
        """Validates that shell/command injection tokens are rejected."""
        malicious_cmds = [
            "create project and run cmd.exe /c calc",
            "open android studio and powershell -command evil",
            "build project; rm -rf /",
            "scaffold app && format c:",
        ]
        for bad_cmd in malicious_cmds:
            intent = DevelopmentIntentParser.parse_intent(bad_cmd)
            self.assertFalse(intent.is_valid)
            self.assertIsNotNone(intent.rejection_reason)
            self.assertIn("Prohibited execution token", intent.rejection_reason)

    # -------------------------------------------------------------------------
    # 2. Sandboxed Scaffolding & Protection
    # -------------------------------------------------------------------------
    def test_04_android_project_scaffolding_sandboxed(self):
        """Verifies complete, valid Android project scaffolding inside dev_projects."""
        test_proj_name = f"TestScaffold_{int(time.time())}"
        res = AndroidProjectScaffolder.scaffold_project(
            project_name=test_proj_name,
            template="login_activity",
            language="Kotlin",
            overwrite=True,
        )
        self.assertTrue(res["success"])
        target_path = Path(res["project_path"])
        self.assertTrue(target_path.exists())
        self.assertTrue(str(target_path).startswith(str(DEV_PROJECTS_ROOT)))

        # Verify critical files created
        expected_files = [
            target_path / "settings.gradle.kts",
            target_path / "build.gradle.kts",
            target_path / "app" / "build.gradle.kts",
            target_path / "app" / "src" / "main" / "AndroidManifest.xml",
            target_path / "app" / "src" / "main" / "res" / "layout" / "activity_login.xml",
            target_path / "app" / "src" / "main" / "res" / "values" / "strings.xml",
            target_path / "gradle.properties",
        ]
        for ef in expected_files:
            self.assertTrue(ef.exists(), f"Missing expected scaffolded file: {ef}")

        # Check content of LoginActivity
        kt_files = list(target_path.glob("**/LoginActivity.kt"))
        self.assertTrue(len(kt_files) >= 1)
        kt_code = kt_files[0].read_text(encoding="utf-8")
        self.assertIn("class LoginActivity : AppCompatActivity()", kt_code)
        self.assertIn("username", kt_code)
        self.assertIn("password", kt_code)

    def test_05_path_traversal_rejection(self):
        """Verifies path traversal attempts are neutralized."""
        evil_name = "../../../escaped_proj"
        res = AndroidProjectScaffolder.scaffold_project(project_name=evil_name, overwrite=True)
        # Sanitized name must still reside strictly under DEV_PROJECTS_ROOT
        target_path = Path(res["project_path"])
        self.assertTrue(target_path.resolve().is_relative_to(DEV_PROJECTS_ROOT))

    def test_06_overwrite_protection(self):
        """Verifies existing directories are not overwritten without explicit flag."""
        proj_name = f"OverwriteTest_{int(time.time())}"
        AndroidProjectScaffolder.scaffold_project(project_name=proj_name, overwrite=True)

        with self.assertRaises(AndroidSafetyError) as ctx:
            AndroidProjectScaffolder.scaffold_project(project_name=proj_name, overwrite=False)
        self.assertEqual(ctx.exception.code, AndroidErrorCode.ACTION_NOT_ALLOWED)

    def test_07_protected_project_names_rejection(self):
        """Verifies core project directories cannot be overwritten."""
        for prot_name in PROTECTED_PROJECT_NAMES:
            with self.assertRaises(AndroidSafetyError) as ctx:
                AndroidProjectScaffolder.scaffold_project(project_name=prot_name, overwrite=True)
            self.assertEqual(ctx.exception.code, AndroidErrorCode.PROJECT_NOT_AUTHORIZED)

    def test_08_prohibited_credential_writing(self):
        """Verifies credential and keystore writing is blocked."""
        target_p = DEV_PROJECTS_ROOT / "test_cred.jks"
        with self.assertRaises(AndroidSafetyError):
            AndroidProjectScaffolder._safe_write(target_p, "fake-keystore")

    # -------------------------------------------------------------------------
    # 3. Safety Gate Validation
    # -------------------------------------------------------------------------
    def test_09_safety_gate_dev_projects_path_validation(self):
        """Verifies Safety Gate authorizes paths under dev_projects and primary project."""
        gate = AndroidSafetyGate()
        # 1. Primary authorized project
        valid_prim = gate.validate_project_path(AUTHORIZED_PROJECT_PATH)
        self.assertEqual(valid_prim, AUTHORIZED_PROJECT_PATH)

        # 2. Subdirectory inside DEV_PROJECTS_ROOT
        sub_proj = DEV_PROJECTS_ROOT / "DevLoginApp"
        sub_proj.mkdir(parents=True, exist_ok=True)
        valid_sub = gate.validate_project_path(sub_proj)
        self.assertEqual(valid_sub, sub_proj.resolve())

        # 3. Path outside authorized boundaries
        with self.assertRaises(AndroidSafetyError) as ctx:
            gate.validate_project_path(Path(r"C:\Windows\System32"))
        self.assertEqual(ctx.exception.code, AndroidErrorCode.PROJECT_NOT_AUTHORIZED)

    # -------------------------------------------------------------------------
    # 4. Companion Multi-Agent Bridge & Routing
    # -------------------------------------------------------------------------
    def test_10_companion_agent_bridge_dispatch_android(self):
        """Verifies CompanionAgentBridge dispatches Android workflow deterministically."""
        bridge = CompanionAgentBridge()
        intent = DevelopmentIntent(
            target_environment=TargetEnvironment.ANDROID,
            application="Android Studio",
            workflow_type=DevelopmentWorkflowType.CREATE_PROJECT,
            project_name=f"BridgeApp_{int(time.time())}",
            requested_actions=["create_project"],
            natural_language_command="create android project",
            template="login_activity",
        )
        res = bridge.dispatch(intent, session_id="test-sess", device_id="test-dev")
        self.assertTrue(res.success)
        self.assertEqual(res.status, "SUCCESS")
        self.assertEqual(res.command_type, "DEV_WORKFLOW")
        self.assertIn("created successfully", res.message)
        self.assertIsNotNone(res.tts_response)
        self.assertIn("Android project", res.tts_response.text)

    def test_11_companion_bridge_emergency_stop(self):
        """Verifies CompanionAgentBridge halts immediately when emergency stop is active."""
        e_controller = EmergencyStopController()
        e_controller.trigger()
        bridge = CompanionAgentBridge(emergency_controller=e_controller)

        intent = DevelopmentIntent(
            target_environment=TargetEnvironment.ANDROID,
            application="Android Studio",
            workflow_type=DevelopmentWorkflowType.CREATE_PROJECT,
            project_name="StopApp",
            requested_actions=["create_project"],
            natural_language_command="create android project",
        )
        res = bridge.dispatch(intent, session_id="test-sess", device_id="test-dev")
        self.assertFalse(res.success)
        self.assertEqual(res.status, "STOPPED")
        self.assertIn("Emergency stop is active", res.message)

    # -------------------------------------------------------------------------
    # 5. Emergency Stop Reset Lifecycle
    # -------------------------------------------------------------------------
    def test_12_orchestrator_emergency_stop_and_reset(self):
        """Verifies orchestrator emergency stop trigger and authenticated reset."""
        device_id = "test-device-12"
        session_id = "sess-12"
        mock_sess = MagicMock(
            device_id=device_id,
            session_id=session_id,
            scopes=["android.read", "android.control", "remote.action"],
            expires_at=time.time() + 3600,
        )
        session_mgr = SessionManager(pairing_manager=MagicMock())
        session_mgr.validate_session = MagicMock(return_value=(True, "OK", mock_sess))
        session_mgr._sessions[session_id] = mock_sess

        orch = CompanionOrchestrator(session_manager=session_mgr)
        orch._states[device_id] = CompanionOrchestratorState.IDLE_CONNECTED

        # Trigger E-stop
        orch.trigger_emergency_stop(device_id, reason="Test halt")
        self.assertTrue(orch.emergency_controller.is_active())
        self.assertEqual(orch.get_state(device_id), CompanionOrchestratorState.STOPPED)

        # Rejection while stopped
        res_blocked = orch.process_command(session_id=session_id, device_id=device_id, command_text="What time is it?")
        self.assertEqual(res_blocked.status, "STOPPED")

        # Authenticated Reset
        ok = orch.reset_emergency_stop(device_id)
        self.assertTrue(ok)
        self.assertFalse(orch.emergency_controller.is_active())
        self.assertEqual(orch.get_state(device_id), CompanionOrchestratorState.IDLE_CONNECTED)

        # Commands succeed after reset
        res_after = orch.process_command(session_id=session_id, device_id=device_id, command_text="What time is it?")
        self.assertTrue(res_after.success)
        self.assertEqual(res_after.status, "SUCCESS")

    # -------------------------------------------------------------------------
    # 6. Natural Language Ingress to Multi-Agent Dispatch
    # -------------------------------------------------------------------------
    def test_13_orchestrator_natural_language_dev_workflow(self):
        """Verifies natural-language prompt routes through orchestrator to specialized agent."""
        device_id = "test-device-13"
        session_id = "sess-13"
        mock_sess = MagicMock(
            device_id=device_id,
            session_id=session_id,
            scopes=["android.read", "android.control", "remote.action"],
            expires_at=time.time() + 3600,
        )
        session_mgr = SessionManager(pairing_manager=MagicMock())
        session_mgr.validate_session = MagicMock(return_value=(True, "OK", mock_sess))
        session_mgr._sessions[session_id] = mock_sess

        orch = CompanionOrchestrator(session_manager=session_mgr)
        orch.transition_state(device_id, CompanionOrchestratorState.AUTHENTICATED, "Auth")
        orch.transition_state(device_id, CompanionOrchestratorState.IDLE_CONNECTED, "Ready")

        cmd = "create a new Android project with a Login Activity named TestLoginFlow"
        res = orch.process_command(session_id=session_id, device_id=device_id, command_text=cmd)

        self.assertTrue(res.success)
        self.assertEqual(res.status, "SUCCESS")
        self.assertEqual(res.command_type, "DEV_WORKFLOW")
        self.assertIn("TestLoginFlow", res.message)

    # -------------------------------------------------------------------------
    # 7. Secure HTTP Endpoints: Emergency Reset Authentication & Confirmation
    # -------------------------------------------------------------------------
    def test_14_secure_emergency_reset_endpoint_rejection_unauthenticated(self):
        """Verifies unauthenticated reset requests are rejected with 403."""
        import socket
        import urllib.error
        import urllib.request
        from app.remote.server import SecureDashboardServer, SecureGateway

        gateway = SecureGateway()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        free_port = sock.getsockname()[1]
        sock.close()

        server = SecureDashboardServer(gateway=gateway, host="127.0.0.1", port=free_port)
        self.assertTrue(server.start())
        self.addCleanup(server.stop)

        url = f"http://127.0.0.1:{free_port}/api/v2/secure/companion/emergency_reset"
        payload = json.dumps({"session_id": "invalid-sess", "device_id": "bad-dev", "operator_confirmation": True}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=3)
        self.assertEqual(ctx.exception.code, 403)

    def test_15_secure_emergency_reset_endpoint_rejection_no_operator_confirmation(self):
        """Verifies reset without operator confirmation is rejected with 400."""
        import socket
        import urllib.error
        import urllib.request
        from app.remote.server import SecureDashboardServer, SecureGateway

        gateway = SecureGateway()
        from app.remote.identity import DeviceIdentity, PairingState
        dev_id = "test-op-dev"
        gateway.pairing_manager._devices[dev_id] = DeviceIdentity(
            device_id=dev_id, device_name="TestDevice", platform="Android",
            app_version="1.0.0", fingerprint="fp", state=PairingState.PAIRED,
            paired_at=time.time()
        )
        ok, msg, sess = gateway.session_manager.create_session(device_id=dev_id, scopes={"remote.action", "android.control"})
        sess_id = sess.session_id

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        free_port = sock.getsockname()[1]
        sock.close()

        server = SecureDashboardServer(gateway=gateway, host="127.0.0.1", port=free_port)
        self.assertTrue(server.start())
        self.addCleanup(server.stop)

        url = f"http://127.0.0.1:{free_port}/api/v2/secure/companion/emergency_reset"
        payload = json.dumps({"session_id": sess_id, "device_id": dev_id, "operator_confirmation": False}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=3)
        self.assertEqual(ctx.exception.code, 400)

    def test_16_secure_emergency_reset_endpoint_success_and_audit(self):
        """Verifies authenticated reset with operator confirmation clears E-stop and audits."""
        import socket
        import urllib.request
        from app.remote.server import SecureDashboardServer, SecureGateway
        from app.remote.identity import DeviceIdentity, PairingState

        gateway = SecureGateway()
        dev_id = "test-op-dev-16"
        gateway.pairing_manager._devices[dev_id] = DeviceIdentity(
            device_id=dev_id, device_name="TestDevice", platform="Android",
            app_version="1.0.0", fingerprint="fp", state=PairingState.PAIRED,
            paired_at=time.time()
        )
        ok, msg, sess = gateway.session_manager.create_session(device_id=dev_id, scopes={"remote.action", "android.control"})
        sess_id = sess.session_id

        # Trigger emergency stop first
        gateway.emergency_stop.trigger(triggered_by="TEST", reason="Test halt")
        self.assertTrue(gateway.emergency_stop.is_active())

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        free_port = sock.getsockname()[1]
        sock.close()

        server = SecureDashboardServer(gateway=gateway, host="127.0.0.1", port=free_port)
        self.assertTrue(server.start())
        self.addCleanup(server.stop)

        url = f"http://127.0.0.1:{free_port}/api/v2/secure/companion/emergency_reset"
        payload = json.dumps({"session_id": sess_id, "device_id": dev_id, "operator_confirmation": True}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")

        with urllib.request.urlopen(req, timeout=3) as resp:
            self.assertEqual(resp.status, 200)
            body = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(body["status"], "RESET")
            self.assertFalse(body["emergency_stop"])

        # Confirm global emergency stop state is deactivated
        self.assertFalse(gateway.emergency_stop.is_active())


if __name__ == "__main__":
    unittest.main()
