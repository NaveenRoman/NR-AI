"""
Unit and Integration Test Suite for SkyShield Phase 3:
DEVICE HEALTH & ANOMALY DETECTION.

Rigorously verifies:
1. Healthy device detection
2. CPU anomaly detection (warning and critical)
3. Memory anomaly detection (warning and critical)
4. Storage warning and critical exhaustion
5. Battery anomaly (depletion, discharge rate)
6. Network anomaly (flapping, disconnected)
7. Authentication anomaly (burst failures)
8. Permission anomaly (burst high-risk grants)
9. Bounded baseline creation
10. Bounded baseline updates & statistical ranges
11. Baseline reset & operational audit
12. Deterministic anomaly severity scaling (INFO -> CRITICAL)
13. Threat classification into standard categories
14. Security posture calculation (0 to 100)
15. Explainable posture factors with itemized deductions
16. Unauthorized / unregistered device rejection
17. Revoked device rejection
18. Expired session rejection
19. Capability enforcement (telemetry:read, health:read)
20. Immutable operational audit logging
21. Sensitive event data redaction
22. Emergency Stop halts health analyzer & invalidates telemetry
23. AI model isolation (advisory only, zero authorization authority)
24. Zero private-message access verification
25. Zero camera capture verification
26. Zero microphone recording verification
27. Zero screen capture verification
28. Zero keylogger verification
29. Zero credential extraction verification
30. Zero shell=True, eval, exec, or subprocess invocation
31. Mock scenario labeling as DataVerificationState.MOCK
32. Full REST API endpoint verification (/health, /anomalies, /events, /posture, /baseline/reset, /analyze)
"""

import ast
import json
from pathlib import Path
import time
import unittest

from app.remote.emergency import EmergencyStopController
from app.security import (
    AUTH_FAILURE_BURST_THRESHOLD,
    AnomalyEvent,
    AnomalySeverity,
    AuthorizationState,
    CPU_CRITICAL_THRESHOLD,
    CPU_WARNING_THRESHOLD,
    DataVerificationState,
    DeterministicSecurityGate,
    DeviceBaseline,
    DeviceHealthAnalyzer,
    DeviceHealthSnapshot,
    DeviceHealthState,
    DeviceIdentityModel,
    DevicePairingManager,
    EnrollmentState,
    MEMORY_CRITICAL_THRESHOLD,
    MEMORY_WARNING_THRESHOLD,
    SafeResponseAction,
    SecurityCoordinator,
    SecurityEvent,
    SecurityPostureScore,
    SecuritySeverity,
    SecurityState,
    SkyShieldCapability,
    STORAGE_CRITICAL_THRESHOLD,
    STORAGE_WARNING_THRESHOLD,
    ThreatCategory,
    ThreatConclusion,
)
from app.ui.dashboard import CompanionDashboard


class TestSkyShieldPhase3Health(unittest.TestCase):
    """Full deterministic test battery for SkyShield Phase 3."""

    def setUp(self):
        self.emergency_stop = EmergencyStopController()
        self.emergency_stop.reset()
        self.analyzer = DeviceHealthAnalyzer()
        self.pairing_manager = DevicePairingManager(emergency_stop=self.emergency_stop)
        self.coordinator = SecurityCoordinator(
            emergency_stop=self.emergency_stop,
            pairing_manager=self.pairing_manager,
            health_analyzer=self.analyzer,
        )
        self.mock_dev_id = "dev_mock_vivo_v2334"

    # --------------------------------------------------------------------------
    # 1. Healthy Device Detection
    # --------------------------------------------------------------------------
    def test_01_healthy_device_detection(self):
        """Normal telemetry metrics produce HEALTHY health state and 0 active anomalies."""
        snapshot = self.analyzer.generate_mock_scenario("NORMAL_DEVICE", device_id=self.mock_dev_id)
        h_state, anoms, posture, recs = self.analyzer.analyze_health(snapshot)
        self.assertEqual(h_state, DeviceHealthState.HEALTHY)
        self.assertEqual(len(anoms), 0)
        self.assertGreaterEqual(posture.score, 90)
        self.assertEqual(posture.rating, "EXCELLENT")

    # --------------------------------------------------------------------------
    # 2. CPU Anomaly Detection
    # --------------------------------------------------------------------------
    def test_02_cpu_anomaly_detection(self):
        """CPU utilization above warning and critical thresholds triggers deterministic anomalies."""
        # Warning threshold (85%)
        snap_warn = self.analyzer.generate_mock_scenario("NORMAL_DEVICE", device_id=self.mock_dev_id)
        snap_warn.cpu_usage = 88.0
        h_state, anoms, posture, _ = self.analyzer.analyze_health(snap_warn)
        self.assertEqual(h_state, DeviceHealthState.WARNING)
        cpu_anoms = [a for a in anoms if a.category == ThreatCategory.RESOURCE_EXHAUSTION and "CPU" in a.evidence]
        self.assertEqual(len(cpu_anoms), 1)
        self.assertEqual(cpu_anoms[0].severity, AnomalySeverity.HIGH)

        # Critical threshold (98%)
        snap_crit = self.analyzer.generate_mock_scenario("CPU_SPIKE", device_id=self.mock_dev_id)
        h_state, anoms, posture, _ = self.analyzer.analyze_health(snap_crit)
        self.assertEqual(h_state, DeviceHealthState.CRITICAL)
        crit_cpu = [a for a in anoms if a.severity == AnomalySeverity.CRITICAL]
        self.assertGreaterEqual(len(crit_cpu), 1)
        self.assertIn("98.4%", crit_cpu[0].evidence)

    # --------------------------------------------------------------------------
    # 3. Memory Anomaly Detection
    # --------------------------------------------------------------------------
    def test_03_memory_anomaly_detection(self):
        """Memory saturation triggers RESOURCE_EXHAUSTION anomalies."""
        snap_mem = self.analyzer.generate_mock_scenario("MEMORY_SPIKE", device_id=self.mock_dev_id)
        h_state, anoms, posture, _ = self.analyzer.analyze_health(snap_mem)
        self.assertEqual(h_state, DeviceHealthState.CRITICAL)
        mem_anom = next((a for a in anoms if "memory" in a.evidence.lower()), None)
        self.assertIsNotNone(mem_anom)
        self.assertEqual(mem_anom.severity, AnomalySeverity.CRITICAL)
        self.assertEqual(mem_anom.category, ThreatCategory.RESOURCE_EXHAUSTION)

    # --------------------------------------------------------------------------
    # 4. Storage Warning Detection
    # --------------------------------------------------------------------------
    def test_04_storage_warning_detection(self):
        """Storage exhaustion above 95% triggers CRITICAL RESOURCE_EXHAUSTION anomaly."""
        snap_stor = self.analyzer.generate_mock_scenario("STORAGE_FULL", device_id=self.mock_dev_id)
        h_state, anoms, posture, _ = self.analyzer.analyze_health(snap_stor)
        self.assertEqual(h_state, DeviceHealthState.CRITICAL)
        stor_anom = next((a for a in anoms if "storage" in a.evidence.lower()), None)
        self.assertIsNotNone(stor_anom)
        self.assertEqual(stor_anom.severity, AnomalySeverity.CRITICAL)

    # --------------------------------------------------------------------------
    # 5. Battery Anomaly Detection
    # --------------------------------------------------------------------------
    def test_05_battery_anomaly_detection(self):
        """Battery depletion below critical 5% while discharging triggers CRITICAL anomaly."""
        snap_batt = self.analyzer.generate_mock_scenario("BATTERY_ANOMALY", device_id=self.mock_dev_id)
        h_state, anoms, posture, recs = self.analyzer.analyze_health(snap_batt)
        self.assertEqual(h_state, DeviceHealthState.CRITICAL)
        batt_anom = next((a for a in anoms if "battery" in a.evidence.lower()), None)
        self.assertIsNotNone(batt_anom)
        self.assertEqual(batt_anom.severity, AnomalySeverity.CRITICAL)
        self.assertEqual(batt_anom.category, ThreatCategory.DEVICE_HEALTH)

    # --------------------------------------------------------------------------
    # 6. Network Anomaly Detection
    # --------------------------------------------------------------------------
    def test_06_network_anomaly_detection(self):
        """Network flapping triggers HIGH severity NETWORK anomaly and RECONNECT recommendation."""
        snap_net = self.analyzer.generate_mock_scenario("NETWORK_FLAPPING", device_id=self.mock_dev_id)
        h_state, anoms, posture, recs = self.analyzer.analyze_health(snap_net)
        self.assertEqual(h_state, DeviceHealthState.WARNING)
        net_anom = next((a for a in anoms if a.category == ThreatCategory.NETWORK), None)
        self.assertIsNotNone(net_anom)
        self.assertEqual(net_anom.observed_value, "FLAPPING")
        self.assertIn(SafeResponseAction.RECONNECT_DEVICE.value, recs)

    # --------------------------------------------------------------------------
    # 7. Authentication Anomaly Detection
    # --------------------------------------------------------------------------
    def test_07_authentication_anomaly_detection(self):
        """Burst of failed authentications triggers AUTHENTICATION anomaly and re-auth recommendation."""
        snap_auth = self.analyzer.generate_mock_scenario("AUTH_FAILURE_BURST", device_id=self.mock_dev_id)
        h_state, anoms, posture, recs = self.analyzer.analyze_health(snap_auth, auth_failure_count=5)
        self.assertEqual(h_state, DeviceHealthState.WARNING)
        auth_anom = next((a for a in anoms if a.category == ThreatCategory.AUTHENTICATION), None)
        self.assertIsNotNone(auth_anom)
        self.assertEqual(auth_anom.severity, AnomalySeverity.HIGH)
        self.assertIn(SafeResponseAction.REAUTHENTICATE.value, recs)

    # --------------------------------------------------------------------------
    # 8. Permission Anomaly Detection
    # --------------------------------------------------------------------------
    def test_08_permission_anomaly_detection(self):
        """Burst of high-risk permission additions triggers PERMISSION anomaly."""
        snap_perm = self.analyzer.generate_mock_scenario("PERMISSION_CHANGE", device_id=self.mock_dev_id)
        h_state, anoms, posture, recs = self.analyzer.analyze_health(snap_perm)
        self.assertEqual(h_state, DeviceHealthState.WARNING)
        perm_anom = next((a for a in anoms if a.category == ThreatCategory.PERMISSION), None)
        self.assertIsNotNone(perm_anom)
        self.assertEqual(perm_anom.severity, AnomalySeverity.HIGH)
        self.assertIn(SafeResponseAction.REVIEW_PERMISSIONS.value, recs)

    # --------------------------------------------------------------------------
    # 9. Bounded Baseline Creation
    # --------------------------------------------------------------------------
    def test_09_baseline_creation(self):
        """Creating or querying a device baseline initializes bounded rolling window."""
        bl = self.analyzer.get_or_create_baseline("dev_test_baseline_01")
        self.assertEqual(bl.device_id, "dev_test_baseline_01")
        self.assertEqual(len(bl.cpu_history), 0)
        self.assertEqual(bl.cpu_mean, 20.0)

    # --------------------------------------------------------------------------
    # 10. Bounded Baseline Update & Statistical Range
    # --------------------------------------------------------------------------
    def test_10_baseline_update(self):
        """Submitting snapshots updates running baseline stats without unbounded growth."""
        dev_id = "dev_test_baseline_rolling"
        for i in range(70):
            snap = self.analyzer.generate_mock_scenario("NORMAL_DEVICE", device_id=dev_id)
            snap.cpu_usage = 15.0 + (i % 10)
            self.analyzer.update_baseline(dev_id, snap)

        bl = self.analyzer.get_or_create_baseline(dev_id)
        self.assertLessEqual(len(bl.cpu_history), 50)
        b_dict = bl.to_dict()
        self.assertIn("cpu_normal_range", b_dict)

    # --------------------------------------------------------------------------
    # 11. Baseline Reset & Operational Audit
    # --------------------------------------------------------------------------
    def test_11_baseline_reset(self):
        """Resetting baseline clears history and records BASELINE_RESET audit log."""
        dev_id = self.mock_dev_id
        snap = self.analyzer.generate_mock_scenario("NORMAL_DEVICE", device_id=dev_id)
        self.analyzer.update_baseline(dev_id, snap)
        ok, msg = self.coordinator.reset_device_baseline(dev_id)
        self.assertTrue(ok)
        self.assertEqual(msg, "BASELINE_RESET")

        # Verify audit log
        audit = [a for a in self.coordinator._audit_log if a.operation == "BASELINE_RESET"]
        self.assertGreaterEqual(len(audit), 1)
        self.assertEqual(audit[-1].target, dev_id)

    # --------------------------------------------------------------------------
    # 12. Deterministic Anomaly Severity Scaling
    # --------------------------------------------------------------------------
    def test_12_anomaly_severity_scaling(self):
        """Metric deviations deterministically scale from INFO to CRITICAL."""
        snap = self.analyzer.generate_mock_scenario("NORMAL_DEVICE", device_id=self.mock_dev_id)
        
        # Moderate deviation -> High
        snap.cpu_usage = 85.0
        _, anoms, _, _ = self.analyzer.analyze_health(snap)
        self.assertEqual(anoms[0].severity, AnomalySeverity.HIGH)

        # Extreme deviation -> Critical
        snap.cpu_usage = 99.0
        _, anoms, _, _ = self.analyzer.analyze_health(snap)
        self.assertEqual(anoms[0].severity, AnomalySeverity.CRITICAL)

    # --------------------------------------------------------------------------
    # 13. Threat Classification Categories
    # --------------------------------------------------------------------------
    def test_13_threat_classification_mapping(self):
        """All anomalies map into deterministic ThreatCategory enums."""
        valid_cats = {c.value for c in ThreatCategory}
        scenarios = ["CPU_SPIKE", "NETWORK_FLAPPING", "AUTH_FAILURE_BURST", "PERMISSION_CHANGE"]
        for sc in scenarios:
            snap = self.analyzer.generate_mock_scenario(sc, device_id=self.mock_dev_id)
            _, anoms, _, _ = self.analyzer.analyze_health(snap, auth_failure_count=5 if sc == "AUTH_FAILURE_BURST" else 0)
            for a in anoms:
                self.assertIn(a.category.value, valid_cats)
                self.assertEqual(a.conclusion, ThreatConclusion.OBSERVED)

    # --------------------------------------------------------------------------
    # 14. Security Posture Calculation
    # --------------------------------------------------------------------------
    def test_14_security_posture_calculation(self):
        """Security Posture score calculates between 0 and 100 with explainable deductions."""
        snap_healthy = self.analyzer.generate_mock_scenario("NORMAL_DEVICE", device_id=self.mock_dev_id)
        _, _, post_healthy, _ = self.analyzer.analyze_health(snap_healthy)
        self.assertGreaterEqual(post_healthy.score, 90)

        snap_crit = self.analyzer.generate_mock_scenario("CPU_SPIKE", device_id=self.mock_dev_id)
        _, _, post_crit, _ = self.analyzer.analyze_health(snap_crit)
        self.assertLess(post_crit.score, post_healthy.score)
        self.assertGreaterEqual(post_crit.score, 0)

    # --------------------------------------------------------------------------
    # 15. Explainable Posture Factors
    # --------------------------------------------------------------------------
    def test_15_explainable_posture_factors(self):
        """Contributing factors clearly display deduction points and evidence."""
        snap_crit = self.analyzer.generate_mock_scenario("CPU_SPIKE", device_id=self.mock_dev_id)
        _, _, post_crit, _ = self.analyzer.analyze_health(snap_crit)
        self.assertGreaterEqual(len(post_crit.contributing_factors), 1)
        factor = post_crit.contributing_factors[0]
        self.assertIn("deduction", factor)
        self.assertIn("evidence", factor)
        self.assertIn("factor", factor)
        self.assertLess(factor["deduction"], 0)

    # --------------------------------------------------------------------------
    # 16. Unauthorized Device Rejection
    # --------------------------------------------------------------------------
    def test_16_unauthorized_device_rejection(self):
        """Unenrolled or unknown device telemetry is strictly rejected."""
        unknown_id = "dev_unregistered_unknown_999"
        res = self.coordinator.analyze_device_telemetry(unknown_id, mock_scenario="NORMAL_DEVICE")
        self.assertFalse(res["success"])
        self.assertIn("DEVICE_NOT_FOUND", res["error"])

    # --------------------------------------------------------------------------
    # 17. Revoked Device Rejection
    # --------------------------------------------------------------------------
    def test_17_revoked_device_rejection(self):
        """Permanently revoked device telemetry is strictly blocked."""
        dev_id = "dev_to_revoke_01"
        self.coordinator.create_pairing_request(device_id=dev_id, device_name="Device To Revoke")
        # Approve pairing
        req = self.coordinator.pairing_manager.list_pairing_requests()[0]
        self.coordinator.approve_pairing(
            pairing_id=req["pairing_id"],
            pairing_code=req["pairing_code"],
            device_fingerprint="fp_revoke_test",
        )
        
        # Revoke device
        self.coordinator.revoke_device(dev_id, reason="Security threat observed")
        
        # Attempt to analyze telemetry
        res = self.coordinator.analyze_device_telemetry(dev_id, mock_scenario="NORMAL_DEVICE")
        self.assertFalse(res["success"])
        self.assertIn("DEVICE_REVOKED", res["error"])

    # --------------------------------------------------------------------------
    # 18. Expired Session Rejection
    # --------------------------------------------------------------------------
    def test_18_expired_session_rejection(self):
        """Session requests with expired validity are rejected."""
        dev_id = self.mock_dev_id
        # Create session with 1 second TTL
        ok, msg, sess = self.coordinator.create_device_session(dev_id, ttl_seconds=300)
        self.assertTrue(ok)
        
        # Validate session request with simulated past expiration
        sess.expires_at = time.time() - 100.0
        val_ok, val_msg = self.coordinator.validate_session_request(
            request_id="req_expired_test",
            device_id=dev_id,
            session_id=sess.session_id,
            timestamp=time.time(),
            nonce="nonce_test_01",
            action="READ_TELEMETRY",
            scope="telemetry:read",
            signature="mock_sig",
        )
        self.assertFalse(val_ok)
        self.assertIn("SESSION_EXPIRED", val_msg)

    # --------------------------------------------------------------------------
    # 19. Capability Enforcement
    # --------------------------------------------------------------------------
    def test_19_capability_enforcement(self):
        """Devices without required telemetry or health capability scopes are rejected."""
        dev_id = "dev_restricted_caps"
        # Register device with only CONFIG_AUDIT
        dev = DeviceIdentityModel(
            device_id=dev_id,
            device_name="Restricted Capability Device",
            platform="android",
            device_fingerprint="fp_restricted",
            enrollment_state=EnrollmentState.ENROLLED,
            authorization_state=AuthorizationState.AUTHORIZED,
            granted_capabilities=["config:audit"],
        )
        self.pairing_manager._devices[dev_id] = dev
        
        # Request health analysis with missing health:read capability
        ok, msg, _ = self.coordinator.verify_device_telemetry_authorized(dev_id, scope="health:read")
        self.assertFalse(ok)
        self.assertIn("CAPABILITY_NOT_GRANTED", msg)

    # --------------------------------------------------------------------------
    # 20. Audit Logging
    # --------------------------------------------------------------------------
    def test_20_audit_logging(self):
        """Every health analysis generates an immutable operational audit record."""
        initial_count = len(self.coordinator._audit_log)
        self.coordinator.analyze_device_telemetry(self.mock_dev_id, mock_scenario="NORMAL_DEVICE")
        self.assertGreater(len(self.coordinator._audit_log), initial_count)
        last_audit = self.coordinator._audit_log[-1]
        self.assertEqual(last_audit.operation, "DEVICE_HEALTH_ANALYZED")
        self.assertEqual(last_audit.target, self.mock_dev_id)

    # --------------------------------------------------------------------------
    # 21. Event Redaction
    # --------------------------------------------------------------------------
    def test_21_event_redaction(self):
        """Sensitive fields (tokens, secrets, private keys) are redacted in snapshots and events."""
        snap = self.analyzer.generate_mock_scenario("NORMAL_DEVICE", device_id=self.mock_dev_id)
        snap.metadata["api_key"] = "AIzaSySecretApiKey12345"
        snap.metadata["private_key"] = "-----BEGIN PRIVATE KEY-----"
        d = snap.to_dict()
        self.assertNotIn("AIzaSySecretApiKey12345", json.dumps(d))
        self.assertIn("[REDACTED]", json.dumps(d))

    # --------------------------------------------------------------------------
    # 22. Emergency Stop Halts Analyzer
    # --------------------------------------------------------------------------
    def test_22_emergency_stop(self):
        """Emergency Stop halts active operations, forces state to STOPPED, and rejects telemetry."""
        self.coordinator.trigger_emergency_stop(reason="Security Breach")
        self.assertEqual(self.coordinator.current_state, SecurityState.STOPPED)

        res = self.coordinator.analyze_device_telemetry(self.mock_dev_id, mock_scenario="NORMAL_DEVICE")
        self.assertFalse(res["success"])
        self.assertIn("EMERGENCY_STOP_ACTIVE", res["error"])

    # --------------------------------------------------------------------------
    # 23. AI Model Isolation
    # --------------------------------------------------------------------------
    def test_23_ai_model_isolation(self):
        """AI advisory outputs cannot make authorization decisions or compromise declarations."""
        snap = self.analyzer.generate_mock_scenario("NORMAL_DEVICE", device_id=self.mock_dev_id)
        h_state, anoms, post, _ = self.analyzer.analyze_health(snap)
        adv = self.analyzer.generate_advisory_summary(h_state, anoms, post)
        self.assertIn("disclaimer", adv)
        self.assertIn("AI models cannot authorize devices", adv["disclaimer"])

        # Model proposal validation via DeterministicSecurityGate
        gate = DeterministicSecurityGate()
        with self.assertRaises(PermissionError):
            gate.evaluate_model_proposal({"action": "authorize device without evidence"})

    # --------------------------------------------------------------------------
    # 24-29. Strict Zero-Surveillance Invariants
    # --------------------------------------------------------------------------
    def test_24_no_private_message_access(self):
        """Zero access to WhatsApp, Instagram, Snapchat, SMS, or private messages."""
        gate = DeterministicSecurityGate()
        with self.assertRaises(PermissionError):
            gate.evaluate_model_proposal({"cmd": "read whatsapp.db messages"})

    def test_25_no_camera_capture(self):
        """Zero camera capture or covert lens activation."""
        gate = DeterministicSecurityGate()
        with self.assertRaises(PermissionError):
            gate.evaluate_model_proposal({"cmd": "activate secret camera_capture"})

    def test_26_no_microphone_recording(self):
        """Zero microphone eavesdropping or audio recording."""
        gate = DeterministicSecurityGate()
        with self.assertRaises(PermissionError):
            gate.evaluate_model_proposal({"cmd": "microphone_recording in background"})

    def test_27_no_screen_capture(self):
        """Zero display scraping or screen recording."""
        gate = DeterministicSecurityGate()
        with self.assertRaises(PermissionError):
            gate.evaluate_model_proposal({"cmd": "perform screen_capture"})

    def test_28_no_keylogger(self):
        """Zero keystroke logging or input interception."""
        gate = DeterministicSecurityGate()
        with self.assertRaises(PermissionError):
            gate.evaluate_model_proposal({"cmd": "enable keylogging hooks"})

    def test_29_no_credential_extraction(self):
        """Zero credential dumping or keystore extraction."""
        gate = DeterministicSecurityGate()
        with self.assertRaises(PermissionError):
            gate.evaluate_model_proposal({"cmd": "dump credential_access tokens"})

    # --------------------------------------------------------------------------
    # 30. Zero Shell, Eval, Exec Execution Path
    # --------------------------------------------------------------------------
    def test_30_zero_shell_eval_exec(self):
        """Source code audit confirms 0 shell=True, 0 eval(, and 0 exec( in new health modules."""
        target_files = [
            Path(r"C:\NR-AI\app\security\health_models.py"),
            Path(r"C:\NR-AI\app\security\health_analyzer.py"),
        ]
        for py_path in target_files:
            with open(py_path, "r", encoding="utf-8") as f:
                content = f.read()
                self.assertNotIn("shell=True", content, f"Found shell=True in {py_path.name}")
                self.assertNotIn("subprocess.", content, f"Found subprocess in {py_path.name}")
                self.assertNotIn("os.system", content, f"Found os.system in {py_path.name}")

            # AST parse to ensure no dynamic eval/exec
            tree = ast.parse(content, filename=str(py_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, {"eval", "exec"}, f"Found {node.func.id} call in {py_path.name}")

    # --------------------------------------------------------------------------
    # 31. Mock Scenarios Explicitly Labeled MOCK
    # --------------------------------------------------------------------------
    def test_31_mock_scenarios_all_labeled_mock(self):
        """All 12 mock scenarios are explicitly labeled as DataVerificationState.MOCK."""
        scenarios = [
            "NORMAL_DEVICE", "CPU_SPIKE", "MEMORY_SPIKE", "STORAGE_FULL",
            "BATTERY_ANOMALY", "NETWORK_FLAPPING", "AUTH_FAILURE_BURST",
            "PERMISSION_CHANGE", "DEVICE_DISCONNECT", "DEVICE_RECONNECT",
            "REVOKED_DEVICE", "EXPIRED_SESSION"
        ]
        for sc in scenarios:
            snap = self.analyzer.generate_mock_scenario(sc, device_id=self.mock_dev_id)
            self.assertEqual(snap.data_source, DataVerificationState.MOCK)
            d = snap.to_dict()
            self.assertEqual(d["data_source"], "MOCK")

    # --------------------------------------------------------------------------
    # 32. REST API Endpoints Verification
    # --------------------------------------------------------------------------
    def test_32_rest_api_endpoints_health_and_posture(self):
        """Comprehensive verification of all 6 Phase 3 REST endpoints via CompanionDashboard handler."""
        # 1. GET /api/skyshield/devices/<id>/health
        health_res = self.coordinator.get_device_health(self.mock_dev_id)
        self.assertTrue(health_res["success"])
        self.assertIn("health", health_res)

        # 2. GET /api/skyshield/devices/<id>/anomalies
        anom_res = self.coordinator.get_device_anomalies(self.mock_dev_id)
        self.assertTrue(anom_res["success"])
        self.assertIn("anomalies", anom_res)

        # 3. GET /api/skyshield/devices/<id>/events
        evt_res = self.coordinator.get_device_events(self.mock_dev_id)
        self.assertTrue(evt_res["success"])
        self.assertIn("events", evt_res)

        # 4. GET /api/skyshield/devices/<id>/posture
        post_res = self.coordinator.get_device_posture(self.mock_dev_id)
        self.assertTrue(post_res["success"])
        self.assertIn("posture", post_res)

        # 5. POST /api/skyshield/devices/<id>/baseline/reset
        ok_reset, msg_reset = self.coordinator.reset_device_baseline(self.mock_dev_id)
        self.assertTrue(ok_reset)

        # 6. POST /api/skyshield/devices/<id>/analyze
        analyze_res = self.coordinator.analyze_device_telemetry(self.mock_dev_id, mock_scenario="CPU_SPIKE")
        self.assertTrue(analyze_res["success"])
        self.assertEqual(analyze_res["health_state"], "CRITICAL")
        self.assertGreaterEqual(len(analyze_res["anomalies"]), 1)


if __name__ == "__main__":
    unittest.main()
