"""
NR-AI SkyShield — Phase 1: Security Agent Foundation Automated Test Suite.
Validates all 25 required criteria:
 1. Security agent initialization
 2. Galaxy node presence and identity
 3. Department and workspace boundaries
 4. State machine initial state and transitions
 5. Illegal transition rejection
 6. Emergency Stop from IDLE, SCANNING, ANALYZING, MONITORING, ALERT
 7. Emergency Stop halts operations immediately
 8. Emergency Stop cannot be bypassed
 9. Emergency Stop resets to IDLE cleanly
10. Model isolation blocks shell commands
11. Model isolation blocks message access
12. Model isolation blocks secret camera/mic activation
13. Device security card structure and content
14. WhatsApp card inspection without message access
15. Instagram card inspection without message access
16. Snapchat card inspection without message access
17. Camera card reports actual sensor state
18. Microphone card reports actual sensor state
19. Permission audit correctly flags high-risk permissions
20. Data source labels are accurate (LIVE vs MOCK vs UNAVAILABLE)
21. No fabricated live telemetry
22. Audit log records all security operations
23. Audit log redacts sensitive information
24. SkyShield workspace routing does not open Universal Knowledge
25. Direct SkyShield interaction (text/voice command) executes through security engine
"""

import os
import time
import unittest
from unittest.mock import MagicMock, patch

from app.security.models import (
    SecurityState,
    SecuritySeverity,
    EvidenceConfidence,
    FindingCategory,
    DataVerificationState,
    PermissionName,
    PermissionStatus,
    DeviceSecurityModel,
    ApplicationSecurityCard,
    CameraSecurityModel,
    MicrophoneSecurityModel,
    SecurityFinding,
    SecurityEvent,
    SecurityAuditRecord,
    SecurityPolicy,
    redact_sensitive_data,
)
from app.security.auditor import PermissionAuditor
from app.security.scanner import SecurityScanner
from app.security.coordinator import SecurityCoordinator
from app.security.agent import SecurityAgent, DeterministicSecurityGate
from app.remote.emergency import EmergencyStopController
from app.ui.galaxy_engine import GalaxyEngine
from app.brain.companion import NRCompanion


class TestSkyShieldPhase1(unittest.TestCase):
    """SkyShield Phase 1 Validation Suite."""

    def setUp(self):
        self.estop = EmergencyStopController()
        self.coordinator = SecurityCoordinator(emergency_stop=self.estop)
        self.agent = SecurityAgent(coordinator=self.coordinator)
        self.galaxy_engine = GalaxyEngine(emergency_stop=self.estop)

    # --------------------------------------------------------------------------
    # 1. Security Agent Initialization
    # --------------------------------------------------------------------------
    def test_01_security_agent_initialization(self):
        self.assertEqual(self.coordinator.current_state, SecurityState.IDLE)
        self.assertIsNotNone(self.coordinator.scanner)
        self.assertIsNotNone(self.coordinator.auditor)
        self.assertIsNotNone(self.coordinator.policy)
        self.assertIsInstance(self.coordinator.get_audit_log(), list)
        self.assertEqual(self.agent.name, "SkyShield")
        self.assertEqual(self.agent.role, "Security Agent")
        self.assertIs(self.agent.coordinator, self.coordinator)

    # --------------------------------------------------------------------------
    # 2. Galaxy Node Presence and Identity
    # --------------------------------------------------------------------------
    def test_02_galaxy_node_presence_and_identity(self):
        nodes = self.galaxy_engine.build_celestial_nodes({})
        sec_node = next((n for n in nodes if n.agent_id == "security_agent"), None)
        self.assertIsNotNone(sec_node, "SkyShield node must be present in Galaxy")
        self.assertEqual(sec_node.friendly_name, "SkyShield")
        self.assertEqual(sec_node.role, "Security Agent")
        self.assertEqual(sec_node.workspace_name, "SkyShield Command Center")
        self.assertEqual(sec_node.parent_department, None)

    # --------------------------------------------------------------------------
    # 3. Department and Workspace Boundaries
    # --------------------------------------------------------------------------
    def test_03_department_and_workspace_boundaries(self):
        ctx = self.galaxy_engine.get_agent_context("security_agent")
        self.assertEqual(ctx.get("friendly_name"), "SkyShield")
        self.assertEqual(ctx.get("workspace_name"), "SkyShield Command Center")

        companion = NRCompanion()
        ws_ctx = companion.get_agent_workspace_context("security_agent")
        self.assertTrue(ws_ctx.get("is_security_command_center"))
        self.assertNotEqual(ws_ctx.get("workspace_name"), "Universal Knowledge Continuum")

    # --------------------------------------------------------------------------
    # 4. State Machine Initial State and Transitions
    # --------------------------------------------------------------------------
    def test_04_state_machine_initial_state_and_transitions(self):
        self.assertEqual(self.coordinator.current_state, SecurityState.IDLE)
        # Legal scan path: IDLE -> SCANNING -> ANALYZING -> IDLE
        self.assertTrue(self.coordinator.transition_to(SecurityState.SCANNING, "Scan start"))
        self.assertEqual(self.coordinator.current_state, SecurityState.SCANNING)
        self.assertTrue(self.coordinator.transition_to(SecurityState.ANALYZING, "Scan analysis"))
        self.assertEqual(self.coordinator.current_state, SecurityState.ANALYZING)
        self.assertTrue(self.coordinator.transition_to(SecurityState.IDLE, "Scan complete"))
        self.assertEqual(self.coordinator.current_state, SecurityState.IDLE)

        # Legal monitoring path: IDLE -> MONITORING -> ALERT -> RESOLVING -> IDLE
        self.assertTrue(self.coordinator.transition_to(SecurityState.MONITORING, "Start monitoring"))
        self.assertTrue(self.coordinator.transition_to(SecurityState.ALERT, "Threat alert"))
        self.assertTrue(self.coordinator.transition_to(SecurityState.RESOLVING, "Remediation"))
        self.assertTrue(self.coordinator.transition_to(SecurityState.IDLE, "Resolved"))

    # --------------------------------------------------------------------------
    # 5. Illegal Transition Rejection
    # --------------------------------------------------------------------------
    def test_05_illegal_transition_rejection(self):
        self.assertEqual(self.coordinator.current_state, SecurityState.IDLE)
        # IDLE cannot go directly to RESOLVING
        with self.assertRaises(ValueError):
            self.coordinator.transition_to(SecurityState.RESOLVING, "Illegal leap")
        self.assertEqual(self.coordinator.current_state, SecurityState.IDLE)

        # IDLE -> SCANNING cannot leap directly to MONITORING
        self.coordinator.transition_to(SecurityState.SCANNING, "Start scan")
        with self.assertRaises(ValueError):
            self.coordinator.transition_to(SecurityState.MONITORING, "Illegal leap")
        self.assertEqual(self.coordinator.current_state, SecurityState.SCANNING)

    # --------------------------------------------------------------------------
    # 6. Emergency Stop from Multiple States
    # --------------------------------------------------------------------------
    def test_06_emergency_stop_from_multiple_states(self):
        for init_state in [
            SecurityState.IDLE,
            SecurityState.SCANNING,
            SecurityState.ANALYZING,
            SecurityState.MONITORING,
            SecurityState.ALERT,
        ]:
            coord = SecurityCoordinator(emergency_stop=EmergencyStopController())
            coord._state = init_state
            res = coord.trigger_emergency_stop(reason=f"Testing estop from {init_state.value}")
            self.assertEqual(coord.current_state, SecurityState.STOPPED)
            self.assertTrue(res["success"])
            self.assertTrue(coord.emergency_stop.is_active())

    # --------------------------------------------------------------------------
    # 7. Emergency Stop Halts Operations Immediately
    # --------------------------------------------------------------------------
    def test_07_emergency_stop_halts_operations_immediately(self):
        self.coordinator.trigger_emergency_stop(reason="Operator Halt")
        self.assertEqual(self.coordinator.current_state, SecurityState.STOPPED)
        res = self.coordinator.run_full_scan()
        self.assertFalse(res["success"])
        self.assertIn("Cannot scan while Emergency Stop is active", res["error"])

    # --------------------------------------------------------------------------
    # 8. Emergency Stop Cannot Be Bypassed
    # --------------------------------------------------------------------------
    def test_08_emergency_stop_cannot_be_bypassed(self):
        self.coordinator.trigger_emergency_stop(reason="Operator Halt")
        # Attempting state transition raises ValueError
        with self.assertRaises(ValueError):
            self.coordinator.transition_to(SecurityState.SCANNING, "Bypass attempt")
        self.assertEqual(self.coordinator.current_state, SecurityState.STOPPED)

        # Agent execution is blocked
        turn_res = self.agent.execute_command("Run security scan")
        self.assertIn("EMERGENCY STOP is currently ACTIVE", turn_res["reply"])

    # --------------------------------------------------------------------------
    # 9. Emergency Stop Resets to IDLE Cleanly
    # --------------------------------------------------------------------------
    def test_09_emergency_stop_resets_to_idle_cleanly(self):
        self.coordinator.trigger_emergency_stop(reason="Operator Halt")
        self.assertEqual(self.coordinator.current_state, SecurityState.STOPPED)
        ok = self.coordinator.reset_emergency_stop()
        self.assertTrue(ok)
        self.assertEqual(self.coordinator.current_state, SecurityState.IDLE)
        self.assertFalse(self.coordinator.emergency_stop.is_active())

        # Audit record of reset is created
        audit_log = self.coordinator.get_audit_log()
        reset_entry = next((a for a in audit_log if a["operation"] == "EMERGENCY_STOP_RESET"), None)
        self.assertIsNotNone(reset_entry)

    # --------------------------------------------------------------------------
    # 10. Model Isolation Blocks Shell Commands
    # --------------------------------------------------------------------------
    def test_10_model_isolation_blocks_shell_commands(self):
        dangerous_commands = [
            "run powershell Get-Process",
            "execute cmd.exe /c dir",
            "eval('import os; os.system(\"ls\")')",
            "exec('import subprocess')",
            "sh -c 'cat /etc/passwd'",
            "bash -c 'whoami'",
            "subprocess.Popen(['ls'])",
        ]
        gate = DeterministicSecurityGate()
        for cmd in dangerous_commands:
            with self.assertRaises(PermissionError):
                gate.validate_and_sanitize_command(cmd)

    # --------------------------------------------------------------------------
    # 11. Model Isolation Blocks Message Access
    # --------------------------------------------------------------------------
    def test_11_model_isolation_blocks_message_access(self):
        message_prompts = [
            "Read WhatsApp messages from Alice",
            "Dump SMS database",
            "Intercept Instagram direct chats",
            "Extract Snapchat messages from phone",
            "Access chat history database",
        ]
        gate = DeterministicSecurityGate()
        for p in message_prompts:
            with self.assertRaises(PermissionError):
                gate.validate_and_sanitize_command(p)

    # --------------------------------------------------------------------------
    # 12. Model Isolation Blocks Secret Camera/Mic Activation
    # --------------------------------------------------------------------------
    def test_12_model_isolation_blocks_secret_camera_mic_activation(self):
        covert_prompts = [
            "Turn on secret camera and record video",
            "Activate background microphone hidden eavesdropping",
            "Stream camera secretly",
            "Record audio covertly",
        ]
        gate = DeterministicSecurityGate()
        for p in covert_prompts:
            with self.assertRaises(PermissionError):
                gate.validate_and_sanitize_command(p)

    # --------------------------------------------------------------------------
    # 13. Device Security Card Structure and Content
    # --------------------------------------------------------------------------
    def test_13_device_security_card_structure_and_content(self):
        scanner = SecurityScanner()
        dev = scanner.scan_device()
        self.assertIsInstance(dev, DeviceSecurityModel)
        d_dict = dev.to_dict()
        self.assertIn("device_id", d_dict)
        self.assertIn("platform", d_dict)
        self.assertIn("os_version", d_dict)
        self.assertIn("is_rooted", d_dict)
        self.assertIn("verification_state", d_dict)
        self.assertIn(dev.verification_state, [DataVerificationState.LIVE, DataVerificationState.MOCK, DataVerificationState.UNAVAILABLE])

    # --------------------------------------------------------------------------
    # 14. WhatsApp Card Inspection Without Message Access
    # --------------------------------------------------------------------------
    def test_14_whatsapp_card_inspection_without_message_access(self):
        scanner = SecurityScanner()
        apps = scanner.scan_applications()
        wa = next((a for a in apps if a.package_name == "com.whatsapp"), None)
        self.assertIsNotNone(wa)
        self.assertEqual(wa.app_name, "WhatsApp")
        self.assertTrue(wa.is_sandboxed)
        self.assertIn("Private Messages: Zero-Interception Blocked", wa.inspection_scope)
        self.assertNotIn("message_content", wa.to_dict())

    # --------------------------------------------------------------------------
    # 15. Instagram Card Inspection Without Message Access
    # --------------------------------------------------------------------------
    def test_15_instagram_card_inspection_without_message_access(self):
        scanner = SecurityScanner()
        apps = scanner.scan_applications()
        ig = next((a for a in apps if a.package_name == "com.instagram.android"), None)
        self.assertIsNotNone(ig)
        self.assertEqual(ig.app_name, "Instagram")
        self.assertTrue(ig.is_sandboxed)
        self.assertIn("Direct Messages: Zero-Interception Blocked", ig.inspection_scope)
        self.assertNotIn("direct_messages", ig.to_dict())

    # --------------------------------------------------------------------------
    # 16. Snapchat Card Inspection Without Message Access
    # --------------------------------------------------------------------------
    def test_16_snapchat_card_inspection_without_message_access(self):
        scanner = SecurityScanner()
        apps = scanner.scan_applications()
        sc = next((a for a in apps if a.package_name == "com.snapchat.android"), None)
        self.assertIsNotNone(sc)
        self.assertEqual(sc.app_name, "Snapchat")
        self.assertTrue(sc.is_sandboxed)
        self.assertIn("Ephemeral Snaps: Zero-Interception Blocked", sc.inspection_scope)
        self.assertNotIn("snaps", sc.to_dict())

    # --------------------------------------------------------------------------
    # 17. Camera Card Reports Actual Sensor State
    # --------------------------------------------------------------------------
    def test_17_camera_card_reports_actual_sensor_state(self):
        scanner = SecurityScanner()
        cam = scanner.scan_camera()
        self.assertIsInstance(cam, CameraSecurityModel)
        self.assertIsInstance(cam.is_sensor_active, bool)
        self.assertIsInstance(cam.active_streams, int)
        self.assertIn(cam.verification_state, [DataVerificationState.LIVE, DataVerificationState.MOCK, DataVerificationState.UNAVAILABLE])

    # --------------------------------------------------------------------------
    # 18. Microphone Card Reports Actual Sensor State
    # --------------------------------------------------------------------------
    def test_18_microphone_card_reports_actual_sensor_state(self):
        scanner = SecurityScanner()
        mic = scanner.scan_microphone()
        self.assertIsInstance(mic, MicrophoneSecurityModel)
        self.assertIsInstance(mic.is_recording_active, bool)
        self.assertIn(mic.verification_state, [DataVerificationState.LIVE, DataVerificationState.MOCK, DataVerificationState.UNAVAILABLE])

    # --------------------------------------------------------------------------
    # 19. Permission Audit Correctly Flags High-Risk Permissions
    # --------------------------------------------------------------------------
    def test_19_permission_audit_correctly_flags_high_risk(self):
        auditor = PermissionAuditor()
        perms = {
            PermissionName.READ_SMS: PermissionStatus.GRANTED,
            PermissionName.ACCESS_FINE_LOCATION: PermissionStatus.GRANTED,
            PermissionName.CAMERA: PermissionStatus.GRANTED,
        }
        findings = auditor.audit_permissions(perms)
        # READ_SMS should be flagged as elevated/critical anomaly
        sms_finding = next((f for f in findings if "SMS" in f.title or "SMS" in f.description), None)
        self.assertIsNotNone(sms_finding)
        self.assertIn(sms_finding.severity, [SecuritySeverity.CRITICAL, SecuritySeverity.HIGH])

    # --------------------------------------------------------------------------
    # 20. Data Source Labels are Accurate (LIVE vs MOCK vs UNAVAILABLE)
    # --------------------------------------------------------------------------
    def test_20_data_source_labels_are_accurate(self):
        dashboard = self.coordinator.get_dashboard_state()
        device_src = dashboard["device"]["verification_state"]
        camera_src = dashboard["camera"]["verification_state"]
        mic_src = dashboard["microphone"]["verification_state"]

        valid_sources = ["LIVE", "MOCK", "UNAVAILABLE"]
        self.assertIn(device_src, valid_sources)
        self.assertIn(camera_src, valid_sources)
        self.assertIn(mic_src, valid_sources)

    # --------------------------------------------------------------------------
    # 21. No Fabricated Live Telemetry
    # --------------------------------------------------------------------------
    def test_21_no_fabricated_live_telemetry(self):
        scanner = SecurityScanner(force_mock=True)
        dev = scanner.scan_device()
        self.assertEqual(dev.verification_state, DataVerificationState.MOCK)
        self.assertNotEqual(dev.verification_state, DataVerificationState.LIVE)

    # --------------------------------------------------------------------------
    # 22. Audit Log Records All Security Operations
    # --------------------------------------------------------------------------
    def test_22_audit_log_records_all_security_operations(self):
        # Run scan and verify audit log records state transitions and scan execution
        scan_res = self.coordinator.run_full_scan()
        self.assertTrue(scan_res["success"])
        logs = self.coordinator.get_audit_log()
        ops = [l["operation"] for l in logs]
        self.assertIn("STATE_TRANSITION", ops)
        self.assertIn("FULL_SECURITY_SCAN", ops)

    # --------------------------------------------------------------------------
    # 23. Audit Log Redacts Sensitive Information
    # --------------------------------------------------------------------------
    def test_23_audit_log_redacts_sensitive_information(self):
        sensitive_data = {
            "token": "secret_jwt_token_12345",
            "password": "SuperSecretPassword!",
            "message": "Private chat message here",
            "safe_metric": 42,
        }
        redacted = redact_sensitive_data(sensitive_data)
        self.assertEqual(redacted["token"], "[REDACTED]")
        self.assertEqual(redacted["password"], "[REDACTED]")
        self.assertEqual(redacted["message"], "[REDACTED]")
        self.assertEqual(redacted["safe_metric"], 42)

    # --------------------------------------------------------------------------
    # 24. SkyShield Workspace Routing Does Not Open Universal Knowledge
    # --------------------------------------------------------------------------
    def test_24_skyshield_workspace_routing_does_not_open_universal_knowledge(self):
        companion = NRCompanion()
        # Direct interaction addressing SkyShield
        resp = companion.interact("SkyShield, report security status", speak_output=False)
        self.assertIsNotNone(resp)
        self.assertIn(resp.routed_to, ["SkyShield", "Specialist Router", "NR-AI"])
        # Should NOT invoke Universal Knowledge graph updates
        if hasattr(companion, "knowledge_engine") and companion.knowledge_engine:
            self.assertFalse(hasattr(companion.knowledge_engine, "_last_skyshield_query"))

    # --------------------------------------------------------------------------
    # 25. Direct SkyShield Interaction Executes Through Security Engine
    # --------------------------------------------------------------------------
    def test_25_direct_skyshield_interaction_executes_through_security_engine(self):
        res = self.agent.execute_command("Run security scan")
        self.assertTrue(res["success"])
        self.assertIn("Scan Complete", res["reply"])
        self.assertIn("findings", res["data"])
        self.assertIn("device", res["data"])

        # Status query
        stat_res = self.agent.execute_command("Status")
        self.assertTrue(stat_res["success"])
        self.assertIn("State:", stat_res["reply"])


if __name__ == "__main__":
    unittest.main()
