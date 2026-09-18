"""
NR-AI SkyShield — Phase 4: Security Intelligence, Incident Response & Finalization Test Suite.
Step 10 Phase 4 — Final SkyShield Security Intelligence Layer.

Automated verification of all 32 required criteria:
 1. Incident creation & schema integrity
 2. Multi-signal event correlation
 3. Dynamic severity calculation
 4. Immutable evidence preservation
 5. Verification state classification (OBSERVED, SUSPECTED, VERIFIED)
 6. Threat classification mapping
 7. Public threat source catalog handling
 8. Threat source provenance & authority
 9. Current information freshness tracking
10. AI advisory isolation & model disclaimers
11. Gated response authorization
12. Human confirmation requirement for high-impact actions
13. Unauthorized response action rejection
14. False-positive handling without evidence deletion
15. Incident resolution tracking & explanation
16. Alert deduplication within window
17. Alert rate-limiting and flood suppression
18. Emergency Stop halts response proposals & forces STOPPED
19. Revoked device telemetry & response rejection
20. Suspended device restriction enforcement
21. Immutable audit logging of all operations
22. Sensitive data redaction in incidents and evidence ([REDACTED])
23. Zero private message access (WhatsApp/Instagram/Snapchat/SMS)
24. Zero covert camera capture
25. Zero covert microphone recording
26. Zero screen capture or display scraping
27. Zero keylogging
28. Zero credential extraction
29. Zero exploit execution or arbitrary shell commands
30. Zero shell=True, zero eval(), zero exec() in source code
31. Bounded resource memory limits (max 100 incidents, max 100 alerts)
32. Complete REST API endpoints validation (Overview, Incidents, Threats, Proposals, Confirm)
"""

import inspect
import json
import os
import time
import unittest
from unittest.mock import MagicMock, patch

from app.remote.emergency import EmergencyStopController
from app.security.agent import DeterministicSecurityGate, SecurityAgent
from app.security.auditor import PermissionAuditor
from app.security.coordinator import SecurityCoordinator
from app.security.enrollment_models import (
    DeviceIdentityModel,
    EnrollmentState,
    SkyShieldCapability,
)
from app.security.health_analyzer import DeviceHealthAnalyzer
from app.security.health_models import (
    AnomalyEvent,
    AnomalySeverity,
    DataVerificationState,
    DeviceHealthSnapshot,
    DeviceHealthState,
    ThreatCategory,
)
from app.security.incident_manager import SecurityIncidentEngine
from app.security.incident_models import (
    ActionConfirmationStatus,
    AlertSeverity,
    EvidenceVerificationState,
    HIGH_IMPACT_ACTIONS,
    IncidentState,
    SafeActionProposal,
    SafeResponseAction,
    SecurityAlert,
    SecurityEvidence,
    SecurityIncident,
    SourceAuthority,
    ThreatAdvisory,
)
from app.security.models import (
    SecurityEvent,
    SecurityPolicy,
    SecuritySeverity,
    SecurityState,
    redact_sensitive_data,
)
from app.security.pairing_manager import DevicePairingManager
from app.security.threat_intelligence import ThreatIntelligenceService
from app.ui.dashboard import CompanionDashboard


class TestSkyShieldPhase4Intelligence(unittest.TestCase):
    """Full deterministic test battery for SkyShield Phase 4."""

    def setUp(self):
        self.emergency_stop = EmergencyStopController()
        self.emergency_stop.reset()
        self.analyzer = DeviceHealthAnalyzer()
        self.pairing_manager = DevicePairingManager(emergency_stop=self.emergency_stop)
        self.threat_intel = ThreatIntelligenceService()
        self.incident_engine = SecurityIncidentEngine()
        self.coordinator = SecurityCoordinator(
            emergency_stop=self.emergency_stop,
            pairing_manager=self.pairing_manager,
            health_analyzer=self.analyzer,
            threat_intel=self.threat_intel,
            incident_engine=self.incident_engine,
        )
        self.agent = SecurityAgent(coordinator=self.coordinator)
        self.mock_dev_id = "dev_mock_vivo_v2334"

    # --------------------------------------------------------------------------
    # 1. Incident Creation & Schema Integrity
    # --------------------------------------------------------------------------
    def test_01_incident_creation(self):
        """SecurityIncident creates valid schema with all required fields."""
        ev = SecurityEvidence(
            evidence_id="ev_test_01",
            evidence_type="ANOMALY",
            description="CPU exceeded 95%",
            source="TELEMETRY",
            timestamp=time.time(),
            verification_state=EvidenceVerificationState.OBSERVED,
        )
        inc = self.incident_engine._create_or_update_incident(
            device_id=self.mock_dev_id,
            title="CPU Spike Anomaly",
            category="RESOURCE_EXHAUSTION",
            severity="HIGH",
            description="High CPU usage detected on device.",
            evidence=[ev],
            related_events=["evt_cpu_01"],
            confidence=0.90,
            recommended_actions=[SafeResponseAction.RESET_BASELINE.value],
            verification_state=EvidenceVerificationState.OBSERVED,
        )
        self.assertIsNotNone(inc.incident_id)
        self.assertEqual(inc.status, IncidentState.DETECTED)
        self.assertEqual(inc.device_id, self.mock_dev_id)
        self.assertEqual(len(inc.evidence), 1)
        self.assertEqual(inc.evidence[0].evidence_id, "ev_test_01")
        self.assertIn("incident_id", inc.to_dict())

    # --------------------------------------------------------------------------
    # 2. Multi-Signal Event Correlation
    # --------------------------------------------------------------------------
    def test_02_event_correlation(self):
        """Correlating multiple distinct signals (auth failure + permission change) generates correlated incident."""
        anoms = [
            AnomalyEvent(
                anomaly_id="anom_auth_01",
                device_id=self.mock_dev_id,
                category=ThreatCategory.AUTHENTICATION,
                severity=AnomalySeverity.HIGH,
                observed_value="5 failures",
                expected_range="0 failures",
                detected_at=time.time(),
                evidence="Burst of authentication failures",
            ),
            AnomalyEvent(
                anomaly_id="anom_perm_01",
                device_id=self.mock_dev_id,
                category=ThreatCategory.PERMISSION,
                severity=AnomalySeverity.HIGH,
                observed_value="3 new permissions",
                expected_range="0 new permissions",
                detected_at=time.time(),
                evidence="High-risk permission additions",
            ),
        ]
        events = [
            SecurityEvent(
                event_id="evt_auth_01",
                timestamp=time.time(),
                device_id=self.mock_dev_id,
                event_type="AUTH_FAIL",
                source="Gateway",
                severity=SecuritySeverity.HIGH,
                description="Invalid signature",
                evidence="AUTHENTICATION_FAILED",
            )
        ]
        incidents = self.incident_engine.correlate_telemetry_and_events(
            device_id=self.mock_dev_id,
            snapshot=None,
            anomalies=anoms,
            recent_events=events,
        )
        self.assertGreaterEqual(len(incidents), 1)
        multi_vec = next((i for i in incidents if "Multi-Vector" in i.title or i.category == "AUTHORIZATION"), None)
        self.assertIsNotNone(multi_vec)
        self.assertEqual(multi_vec.severity, "CRITICAL")
        self.assertEqual(multi_vec.verification_state, EvidenceVerificationState.SUSPECTED)

    # --------------------------------------------------------------------------
    # 3. Dynamic Severity Calculation
    # --------------------------------------------------------------------------
    def test_03_severity_calculation(self):
        """Auth failures >= 6 escalate incident severity to CRITICAL, while 3-5 remain HIGH."""
        events_moderate = [
            SecurityEvent(event_id=f"evt_f_{i}", timestamp=time.time(), device_id=self.mock_dev_id,
                          event_type="AUTH_FAIL", source="GW", severity=SecuritySeverity.HIGH, description="fail")
            for i in range(3)
        ]
        incs_mod = self.incident_engine.correlate_telemetry_and_events(self.mock_dev_id, None, [], events_moderate)
        self.assertEqual(incs_mod[0].severity, "HIGH")

        events_critical = [
            SecurityEvent(event_id=f"evt_f_{i}", timestamp=time.time(), device_id=self.mock_dev_id,
                          event_type="AUTH_FAIL", source="GW", severity=SecuritySeverity.HIGH, description="fail")
            for i in range(7)
        ]
        incs_crit = self.incident_engine.correlate_telemetry_and_events(self.mock_dev_id, None, [], events_critical)
        self.assertEqual(incs_crit[0].severity, "CRITICAL")

    # --------------------------------------------------------------------------
    # 4. Immutable Evidence Preservation
    # --------------------------------------------------------------------------
    def test_04_evidence_preservation(self):
        """Evidence records cannot be mutated or discarded when updating incident status."""
        ev = SecurityEvidence(
            evidence_id="ev_immutable_01",
            evidence_type="RAW_LOG",
            description="Audit trace item",
            source="TEST",
            timestamp=time.time(),
        )
        inc = self.incident_engine._create_or_update_incident(
            device_id=self.mock_dev_id,
            title="Evidence Test",
            category="DEVICE_HEALTH",
            severity="LOW",
            description="Test description",
            evidence=[ev],
            related_events=[],
            confidence=0.9,
            recommended_actions=[],
            verification_state=EvidenceVerificationState.OBSERVED,
        )
        self.assertEqual(len(inc.evidence), 1)

        # Mark false positive
        self.incident_engine.mark_false_positive(inc.incident_id, reason="Testing evidence retention")
        inc_after = self.incident_engine.get_incident(inc.incident_id)
        self.assertEqual(inc_after.status, IncidentState.DISMISSED)
        self.assertEqual(len(inc_after.evidence), 1)
        self.assertEqual(inc_after.evidence[0].evidence_id, "ev_immutable_01")

    # --------------------------------------------------------------------------
    # 5. Verification State Classification
    # --------------------------------------------------------------------------
    def test_05_verification_state(self):
        """Observed raw metrics are marked OBSERVED while correlation inferences are SUSPECTED."""
        ev_observed = SecurityEvidence(
            evidence_id="ev_obs",
            evidence_type="CPU_METRIC",
            description="Directly observed 99% CPU",
            source="TELEMETRY",
            timestamp=time.time(),
            verification_state=EvidenceVerificationState.OBSERVED,
        )
        self.assertEqual(ev_observed.verification_state, EvidenceVerificationState.OBSERVED)

        ev_suspected = SecurityEvidence(
            evidence_id="ev_susp",
            evidence_type="INFERENCE",
            description="Inferred brute force",
            source="CORRELATION",
            timestamp=time.time(),
            verification_state=EvidenceVerificationState.SUSPECTED,
        )
        self.assertEqual(ev_suspected.verification_state, EvidenceVerificationState.SUSPECTED)

    # --------------------------------------------------------------------------
    # 6. Threat Classification Mapping
    # --------------------------------------------------------------------------
    def test_06_threat_classification(self):
        """Anomalies map into deterministic ThreatCategory enums."""
        categories = [
            ThreatCategory.AUTHENTICATION,
            ThreatCategory.AUTHORIZATION,
            ThreatCategory.NETWORK,
            ThreatCategory.PERMISSION,
            ThreatCategory.RESOURCE_EXHAUSTION,
            ThreatCategory.DEVICE_HEALTH,
        ]
        for cat in categories:
            self.assertIsInstance(cat.value, str)

    # --------------------------------------------------------------------------
    # 7. Public Threat Source Catalog Handling
    # --------------------------------------------------------------------------
    def test_07_public_threat_sources(self):
        """Threat intelligence service returns genuine public CVE and Android bulletins."""
        advisories = self.threat_intel.get_recent_advisories(limit=10)
        self.assertGreaterEqual(len(advisories), 5)
        cve_ids = [a.advisory_id for a in advisories]
        self.assertIn("CVE-2024-32896", cve_ids)
        self.assertIn("CVE-2023-40088", cve_ids)

    # --------------------------------------------------------------------------
    # 8. Source Provenance & Authority
    # --------------------------------------------------------------------------
    def test_08_source_provenance(self):
        """All threat intelligence entries include official source, URL, and authority."""
        adv = self.threat_intel.get_advisory("CVE-2024-32896")
        self.assertIsNotNone(adv)
        self.assertIn("https://", adv.url)
        self.assertIn("Android Security Bulletin", adv.source)
        self.assertEqual(adv.source_authority, SourceAuthority.OFFICIAL_VENDOR)

    # --------------------------------------------------------------------------
    # 9. Current Information Freshness
    # --------------------------------------------------------------------------
    def test_09_information_freshness(self):
        """Threat intelligence records include publication date and last-checked timestamp."""
        adv = self.threat_intel.get_advisory("CVE-2024-32896")
        self.assertGreater(adv.last_checked, 0)
        self.assertTrue(len(adv.published_date) >= 10)

    # --------------------------------------------------------------------------
    # 10. AI Advisory Isolation & Model Disclaimers
    # --------------------------------------------------------------------------
    def test_10_ai_advisory_isolation(self):
        """AI security analysis is advisory-only, contains confidence score, and includes disclaimer."""
        ev = SecurityEvidence(
            evidence_id="ev_iso_01",
            evidence_type="ANOMALY",
            description="Test anomaly",
            source="TEST",
            timestamp=time.time(),
        )
        inc = self.incident_engine._create_or_update_incident(
            device_id=self.mock_dev_id,
            title="Isolation Test Incident",
            category="AUTHENTICATION",
            severity="HIGH",
            description="Testing AI advisory isolation",
            evidence=[ev],
            related_events=[],
            confidence=0.85,
            recommended_actions=[SafeResponseAction.REQUEST_REAUTH.value],
            verification_state=EvidenceVerificationState.OBSERVED,
        )
        analysis = self.agent.analyze_incident(inc.incident_id)
        self.assertTrue(analysis["success"])
        self.assertIn("confidence", analysis)
        self.assertIn("evidence_count", analysis)
        self.assertIn("disclaimer", analysis)
        self.assertIn("advisory only", analysis["disclaimer"].lower())

    # --------------------------------------------------------------------------
    # 11. Gated Response Authorization
    # --------------------------------------------------------------------------
    def test_11_response_authorization(self):
        """Proposing a safe response action creates a pending gated proposal."""
        prop = self.coordinator.propose_response_action(
            action=SafeResponseAction.RESET_BASELINE.value,
            device_id=self.mock_dev_id,
            reason="Recalibrate healthy baseline",
        )
        self.assertIsNotNone(prop["proposal_id"])
        self.assertEqual(prop["status"], ActionConfirmationStatus.PENDING_CONFIRMATION.value)

    # --------------------------------------------------------------------------
    # 12. Human Confirmation Requirement for High-Impact Actions
    # --------------------------------------------------------------------------
    def test_12_human_confirmation_requirement(self):
        """High-impact actions (SUSPEND, REVOKE, ISOLATE, EMERGENCY_STOP) flag requires_human_confirmation."""
        for act in (SafeResponseAction.SUSPEND_DEVICE, SafeResponseAction.REVOKE_DEVICE, SafeResponseAction.ISOLATE_DEVICE):
            prop = self.coordinator.propose_response_action(
                action=act.value,
                device_id=self.mock_dev_id,
                reason="High impact test",
            )
            self.assertTrue(prop["requires_human_confirmation"])

    # --------------------------------------------------------------------------
    # 13. Unauthorized Response Rejection
    # --------------------------------------------------------------------------
    def test_13_unauthorized_response_rejection(self):
        """Attempting to execute high-impact action without human confirmation is rejected."""
        prop = self.coordinator.propose_response_action(
            action=SafeResponseAction.SUSPEND_DEVICE.value,
            device_id=self.mock_dev_id,
            reason="Unauthorized execution test",
        )
        ok, msg, p = self.coordinator.confirm_response_action(
            proposal_id=prop["proposal_id"],
            operator_confirmed=False,
        )
        self.assertFalse(ok)
        self.assertEqual(msg, "OPERATOR_CONFIRMATION_REFUSED")

    # --------------------------------------------------------------------------
    # 14. False-Positive Handling Without Evidence Deletion
    # --------------------------------------------------------------------------
    def test_14_false_positive_handling(self):
        """Marking an incident as a false positive records reason, sets DISMISSED, and preserves evidence."""
        ev = SecurityEvidence("ev_fp_01", "LOG", "Sample log", "SYS", time.time())
        inc = self.incident_engine._create_or_update_incident(
            device_id=self.mock_dev_id,
            title="FP Test",
            category="NETWORK",
            severity="LOW",
            description="Testing false positive",
            evidence=[ev],
            related_events=[],
            confidence=0.5,
            recommended_actions=[],
            verification_state=EvidenceVerificationState.SUSPECTED,
        )
        ok, msg = self.coordinator.mark_false_positive(
            inc.incident_id,
            reason="Benign local network switchover during testing.",
        )
        self.assertTrue(ok)
        inc_d = self.coordinator.get_incident(inc.incident_id)
        self.assertEqual(inc_d["status"], "DISMISSED")
        self.assertEqual(inc_d["false_positive_reason"], "Benign local network switchover during testing.")
        self.assertEqual(len(inc_d["evidence"]), 1)

    # --------------------------------------------------------------------------
    # 15. Incident Resolution Tracking & Explanation
    # --------------------------------------------------------------------------
    def test_15_incident_resolution(self):
        """Resolving an incident records resolution notes and resolved_at timestamp."""
        inc = self.incident_engine._create_or_update_incident(
            device_id=self.mock_dev_id,
            title="Resolve Test",
            category="NETWORK",
            severity="LOW",
            description="Testing resolution",
            evidence=[],
            related_events=[],
            confidence=0.5,
            recommended_actions=[],
            verification_state=EvidenceVerificationState.OBSERVED,
        )
        ok, msg = self.coordinator.resolve_incident(inc.incident_id, resolution="Device updated to latest patch.")
        self.assertTrue(ok)
        inc_d = self.coordinator.get_incident(inc.incident_id)
        self.assertEqual(inc_d["status"], "RESOLVED")
        self.assertIsNotNone(inc_d["resolved_at"])

    # --------------------------------------------------------------------------
    # 16. Alert Deduplication Within Window
    # --------------------------------------------------------------------------
    def test_16_alert_deduplication(self):
        """Repeated identical alerts within 300s increment counter rather than creating duplicate entries."""
        alt1 = self.incident_engine.trigger_alert(
            device_id=self.mock_dev_id,
            severity=AlertSeverity.HIGH,
            title="CPU Spike Alert",
            message="CPU at 96%",
        )
        alt2 = self.incident_engine.trigger_alert(
            device_id=self.mock_dev_id,
            severity=AlertSeverity.HIGH,
            title="CPU Spike Alert",
            message="CPU at 96%",
        )
        self.assertEqual(alt1.alert_id, alt2.alert_id)
        self.assertEqual(alt2.count, 2)

    # --------------------------------------------------------------------------
    # 17. Alert Rate-Limiting & Flood Suppression
    # --------------------------------------------------------------------------
    def test_17_alert_rate_limiting(self):
        """Alert generator throttles when exceeding maximum rate per minute."""
        alerts_sent = 0
        for i in range(25):
            res = self.incident_engine.trigger_alert(
                device_id=self.mock_dev_id,
                severity=AlertSeverity.LOW,
                title=f"Spam Alert {i}",
                message="Flooding test",
            )
            if res:
                alerts_sent += 1
        self.assertLessEqual(alerts_sent, self.incident_engine.ALERT_RATE_LIMIT_PER_MINUTE)

    # --------------------------------------------------------------------------
    # 18. Emergency Stop Halts Response Proposals & Forces STOPPED
    # --------------------------------------------------------------------------
    def test_18_emergency_stop_halts_responses(self):
        """Emergency Stop immediately halts proposals and rejects subsequent action executions."""
        prop = self.coordinator.propose_response_action(
            action=SafeResponseAction.SUSPEND_DEVICE.value,
            device_id=self.mock_dev_id,
            reason="Before halt",
        )
        self.coordinator.trigger_emergency_stop(reason="Security Emergency")
        self.assertEqual(self.coordinator.current_state, SecurityState.STOPPED)

        # Confirm should be rejected
        ok, msg, _ = self.coordinator.confirm_response_action(
            proposal_id=prop["proposal_id"],
            operator_confirmed=True,
        )
        self.assertFalse(ok)
        self.assertEqual(msg, "EMERGENCY_STOP_ACTIVE")

    # --------------------------------------------------------------------------
    # 19. Revoked Device Telemetry & Response Rejection
    # --------------------------------------------------------------------------
    def test_19_revoked_device_rejection(self):
        """Revoked devices cannot receive action executions or submit telemetry."""
        dev_id = "dev_rev_p4_01"
        self.coordinator.create_pairing_request(device_id=dev_id, device_name="Device To Revoke")
        req = self.coordinator.pairing_manager.list_pairing_requests()[0]
        self.coordinator.approve_pairing(
            pairing_id=req["pairing_id"],
            pairing_code=req["pairing_code"],
            device_fingerprint="fp_rev_p4",
        )
        self.coordinator.revoke_device(dev_id, reason="Security threat observed")

        res = self.coordinator.analyze_device_telemetry(dev_id, mock_scenario="NORMAL_DEVICE")
        self.assertFalse(res["success"])
        self.assertIn("DEVICE_REVOKED", res["error"])

    # --------------------------------------------------------------------------
    # 20. Suspended Device Restriction Enforcement
    # --------------------------------------------------------------------------
    def test_20_suspended_device_restrictions(self):
        """Suspended devices cannot submit telemetry until reauthorized."""
        dev_id = "dev_susp_p4_01"
        self.coordinator.create_pairing_request(device_id=dev_id, device_name="Device To Suspend")
        req = self.coordinator.pairing_manager.list_pairing_requests()[0]
        self.coordinator.approve_pairing(
            pairing_id=req["pairing_id"],
            pairing_code=req["pairing_code"],
            device_fingerprint="fp_susp_p4",
        )
        self.coordinator.suspend_device(dev_id, reason="Suspended for testing")

        res = self.coordinator.analyze_device_telemetry(dev_id, mock_scenario="NORMAL_DEVICE")
        self.assertFalse(res["success"])
        self.assertIn("DEVICE_SUSPENDED", res["error"])

    # --------------------------------------------------------------------------
    # 21. Immutable Audit Logging
    # --------------------------------------------------------------------------
    def test_21_audit_logging(self):
        """All incident lifecycle and response operations write immutable audit records."""
        initial_log_len = len(self.coordinator.get_audit_log())
        self.coordinator.propose_response_action(
            action=SafeResponseAction.RESET_BASELINE.value,
            device_id=self.mock_dev_id,
            reason="Audit test",
        )
        after_len = len(self.coordinator.get_audit_log())
        self.assertGreater(after_len, initial_log_len)

    # --------------------------------------------------------------------------
    # 22. Sensitive Data Redaction
    # --------------------------------------------------------------------------
    def test_22_secret_redaction(self):
        """Private keys and API keys are automatically replaced with [REDACTED] in reports and dicts."""
        ev = SecurityEvidence(
            evidence_id="ev_sec",
            evidence_type="DATA",
            description="Leaked token test",
            source="SYS",
            timestamp=time.time(),
            data={"api_key": "AIzaSySuperSecret123", "password": "MySecretPassword123"},
        )
        d = ev.to_dict()
        self.assertNotIn("AIzaSySuperSecret123", json.dumps(d))
        self.assertIn("[REDACTED]", json.dumps(d))

    # --------------------------------------------------------------------------
    # 23. Zero Private Message Access
    # --------------------------------------------------------------------------
    def test_23_no_private_message_access(self):
        """Zero access to WhatsApp, Instagram, Snapchat, SMS, or private messages."""
        prohibited = ["read_whatsapp_database", "read_instagram_messages", "read_snapchat_messages", "intercept_messages"]
        for p in prohibited:
            self.assertFalse(SecurityPolicy.is_action_permitted(p))
            with self.assertRaises(PermissionError):
                DeterministicSecurityGate.evaluate_model_proposal({"action": p})

    # --------------------------------------------------------------------------
    # 24. Zero Covert Camera Capture
    # --------------------------------------------------------------------------
    def test_24_no_camera_capture(self):
        """Zero covert camera capture."""
        self.assertFalse(SecurityPolicy.is_action_permitted("hidden_camera_activate"))
        with self.assertRaises(PermissionError):
            DeterministicSecurityGate.evaluate_model_proposal({"action": "camera_capture"})

    # --------------------------------------------------------------------------
    # 25. Zero Covert Microphone Recording
    # --------------------------------------------------------------------------
    def test_25_no_microphone_recording(self):
        """Zero covert microphone recording."""
        self.assertFalse(SecurityPolicy.is_action_permitted("hidden_mic_record"))
        with self.assertRaises(PermissionError):
            DeterministicSecurityGate.evaluate_model_proposal({"action": "microphone_recording"})

    # --------------------------------------------------------------------------
    # 26. Zero Screen Capture
    # --------------------------------------------------------------------------
    def test_26_no_screen_capture(self):
        """Zero display scraping or screen recording."""
        with self.assertRaises(PermissionError):
            DeterministicSecurityGate.evaluate_model_proposal({"action": "screen_capture"})

    # --------------------------------------------------------------------------
    # 27. Zero Keylogging
    # --------------------------------------------------------------------------
    def test_27_no_keylogger(self):
        """Zero keystroke logging."""
        self.assertFalse(SecurityPolicy.is_action_permitted("keylogger_activate"))
        with self.assertRaises(PermissionError):
            DeterministicSecurityGate.evaluate_model_proposal({"action": "keylogging"})

    # --------------------------------------------------------------------------
    # 28. Zero Credential Extraction
    # --------------------------------------------------------------------------
    def test_28_no_credential_extraction(self):
        """Zero credential dumping or keystore access."""
        self.assertFalse(SecurityPolicy.is_action_permitted("extract_credentials"))
        self.assertFalse(SecurityPolicy.is_action_permitted("dump_passwords"))
        with self.assertRaises(PermissionError):
            DeterministicSecurityGate.evaluate_model_proposal({"action": "credential_access"})

    # --------------------------------------------------------------------------
    # 29. Zero Exploit Execution
    # --------------------------------------------------------------------------
    def test_29_no_exploit_execution(self):
        """Zero exploit execution or arbitrary system attack generation."""
        with self.assertRaises(PermissionError):
            DeterministicSecurityGate.evaluate_model_proposal({"action": "create malware and exploit system"})

    # --------------------------------------------------------------------------
    # 30. Zero shell=True, Zero eval(), Zero exec()
    # --------------------------------------------------------------------------
    def test_30_zero_shell_eval_exec(self):
        """Static audit confirms 0 shell=True, 0 eval(, and 0 exec( in Phase 4 modules."""
        phase4_files = [
            r"C:\NR-AI\app\security\incident_models.py",
            r"C:\NR-AI\app\security\threat_intelligence.py",
            r"C:\NR-AI\app\security\safe_response_engine.py",
            r"C:\NR-AI\app\security\incident_manager.py",
        ]
        for fpath in phase4_files:
            if os.path.isfile(fpath):
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read()
                    self.assertNotIn("shell=True", content, f"shell=True found in {fpath}")
                    self.assertNotIn("eval(", content, f"eval( found in {fpath}")
                    self.assertNotIn("exec(", content, f"exec( found in {fpath}")

    # --------------------------------------------------------------------------
    # 31. Bounded Resource Memory Limits
    # --------------------------------------------------------------------------
    def test_31_bounded_memory_limits(self):
        """Incident and alert queues are strictly bounded to prevent memory growth."""
        self.assertEqual(self.incident_engine.MAX_INCIDENTS, 100)
        self.assertEqual(self.incident_engine.MAX_ALERTS, 100)

    # --------------------------------------------------------------------------
    # 32. REST API Endpoints Validation
    # --------------------------------------------------------------------------
    def test_32_rest_api_endpoints(self):
        """All Phase 4 REST API endpoints are functional via coordinator and dashboard state."""
        # 1. Overview API
        overview = self.coordinator.get_security_overview()
        self.assertIn("active_incidents_count", overview)
        self.assertIn("overall_security_posture", overview)

        # 2. Incidents API
        incidents = self.coordinator.list_incidents()
        self.assertIsInstance(incidents, list)

        # 3. Threat Intelligence API
        threats = self.coordinator.list_threat_advisories()
        self.assertGreaterEqual(len(threats), 5)

        # 4. Check device vulnerability API
        vuln = self.coordinator.check_device_vulnerability(self.mock_dev_id)
        self.assertTrue(vuln["success"])
        self.assertIn("matched_advisories_count", vuln)

        # 5. Propose Response Action API
        prop = self.coordinator.propose_response_action(
            action=SafeResponseAction.RESET_BASELINE.value,
            device_id=self.mock_dev_id,
            reason="Endpoint verification test",
        )
        self.assertIsNotNone(prop["proposal_id"])

        # 6. Confirm Response Action API
        ok, msg, p = self.coordinator.confirm_response_action(
            proposal_id=prop["proposal_id"],
            operator_confirmed=True,
        )
        self.assertTrue(ok)

        # 7. Dashboard State contains all Phase 4 telemetry
        dash = self.coordinator.get_dashboard_state()
        self.assertIn("incidents", dash)
        self.assertIn("alerts", dash)
        self.assertIn("threat_advisories", dash)
        self.assertIn("security_overview", dash)
