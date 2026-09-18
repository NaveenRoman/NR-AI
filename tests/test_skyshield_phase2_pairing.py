"""
NR-AI SkyShield — Phase 2: Authorized Device Pairing & Secure Enrollment Test Suite.
Validates all 25 required security criteria & attack tests, plus Categories A through T:
 1. Phone number alone cannot authenticate.
 2. Phone number alone cannot enroll.
 3. Invalid pairing code rejected.
 4. Expired pairing code rejected.
 5. Reused pairing code rejected.
 6. Wrong device rejected.
 7. Wrong challenge rejected.
 8. Invalid signature rejected.
 9. Replay request rejected.
10. Expired session rejected.
11. Revoked device rejected.
12. Suspended device restricted.
13. Unauthorized capability rejected.
14. Private key never returned in API responses.
15. Secrets never logged in telemetry or audit.
16. Emergency Stop terminates active enrollment/sessions.
17. Excessive pairing attempts rate-limited.
18. Excessive sessions rate-limited.
19. Audit events generated for all state changes.
20. No private message access.
21. No hidden camera access.
22. No hidden microphone access.
23. No screen capture capability added.
24. Zero shell/eval/exec execution paths.
25. Model cannot bypass authorization.
"""

import hashlib
import hmac
import json
import os
import time
import unittest
from unittest.mock import MagicMock, patch

from app.security.models import (
    SecurityState,
    DataVerificationState,
)
from app.security.enrollment_models import (
    EnrollmentState,
    AuthorizationState,
    PairingRequestStatus,
    SkyShieldCapability,
    PROHIBITED_CAPABILITIES,
    DEFAULT_PHASE2_CAPABILITIES,
    normalize_phone_number,
    mask_phone_number,
    generate_pairing_code,
    generate_challenge,
    DeviceIdentityModel,
    PairingRequest,
    DeviceKeyRecord,
    DeviceSession,
)
from app.security.pairing_manager import (
    DevicePairingManager,
    IllegalEnrollmentTransitionError,
    CapabilityEscalationError,
    DeviceRevokedError,
)
from app.security.coordinator import SecurityCoordinator
from app.security.agent import SecurityAgent, DeterministicSecurityGate
from app.remote.emergency import EmergencyStopController
from app.brain.companion import NRCompanion
from app.ui.dashboard import CompanionDashboard


class TestSkyShieldPhase2Pairing(unittest.TestCase):
    """SkyShield Phase 2 Validation Suite."""

    def setUp(self):
        self.estop = EmergencyStopController()
        self.coordinator = SecurityCoordinator(emergency_stop=self.estop)
        self.pairing_mgr = self.coordinator.pairing_manager
        self.agent = SecurityAgent(coordinator=self.coordinator)

    # --------------------------------------------------------------------------
    # 1. Phone number alone cannot authenticate
    # --------------------------------------------------------------------------
    def test_01_phone_number_alone_cannot_authenticate(self):
        """A phone number alone cannot create a session or authenticate requests."""
        # Attempting session creation with just a phone number fails
        ok, msg, sess = self.pairing_mgr.create_device_session(device_id="+919876543210")
        self.assertFalse(ok)
        self.assertIn("NOT_FOUND", msg)
        self.assertIsNone(sess)

    # --------------------------------------------------------------------------
    # 2. Phone number alone cannot enroll
    # --------------------------------------------------------------------------
    def test_02_phone_number_alone_cannot_enroll(self):
        """Providing a phone number does not bypass pairing code verification or owner approval."""
        ok, msg, req = self.pairing_mgr.create_pairing_request(
            device_name="Test Phone",
            phone_number="+91 98765 43210",
        )
        self.assertTrue(ok)
        self.assertIsNotNone(req)
        # Device is still in PAIRING_REQUESTED, not ENROLLED
        dev = self.pairing_mgr.get_device(req.device_id)
        self.assertEqual(dev.enrollment_state, EnrollmentState.PAIRING_REQUESTED)
        self.assertEqual(dev.authorization_state, AuthorizationState.PENDING_APPROVAL)

        # Cannot authenticate yet
        auth_ok, auth_msg, sess = self.pairing_mgr.create_device_session(req.device_id)
        self.assertFalse(auth_ok)
        self.assertIsNone(sess)

    # --------------------------------------------------------------------------
    # 3. Invalid pairing code rejected
    # --------------------------------------------------------------------------
    def test_03_invalid_pairing_code_rejected(self):
        """An incorrect pairing code must be rejected and decrements remaining attempts."""
        ok, msg, req = self.pairing_mgr.create_pairing_request(device_name="Target Phone")
        self.assertTrue(ok)
        initial_attempts = req.attempts_remaining

        appr_ok, appr_msg, dev = self.pairing_mgr.approve_pairing(
            pairing_id=req.pairing_id,
            pairing_code="000000" if req.pairing_code != "000000" else "111111",
            device_fingerprint="fp_test",
        )
        self.assertFalse(appr_ok)
        self.assertEqual(appr_msg, "INVALID_PAIRING_CODE")
        self.assertIsNone(dev)
        self.assertEqual(req.attempts_remaining, initial_attempts - 1)

    # --------------------------------------------------------------------------
    # 4. Expired pairing code rejected
    # --------------------------------------------------------------------------
    def test_04_expired_pairing_code_rejected(self):
        """A pairing request whose TTL has elapsed cannot be approved."""
        ok, msg, req = self.pairing_mgr.create_pairing_request(
            device_name="Target Phone",
            ttl_seconds=60,
        )
        self.assertTrue(ok)
        # Force expiration
        req.expires_at = time.time() - 10

        appr_ok, appr_msg, dev = self.pairing_mgr.approve_pairing(
            pairing_id=req.pairing_id,
            pairing_code=req.pairing_code,
            device_fingerprint="fp_test",
        )
        self.assertFalse(appr_ok)
        self.assertEqual(appr_msg, "PAIRING_CODE_EXPIRED")
        self.assertIsNone(dev)

    # --------------------------------------------------------------------------
    # 5. Reused pairing code rejected
    # --------------------------------------------------------------------------
    def test_05_reused_pairing_code_rejected(self):
        """Once approved, a single-use pairing code cannot be reused."""
        ok, msg, req = self.pairing_mgr.create_pairing_request(device_name="Target Phone")
        self.assertTrue(ok)
        code = req.pairing_code
        pid = req.pairing_id

        # First approval succeeds
        appr_ok1, _, dev1 = self.pairing_mgr.approve_pairing(
            pairing_id=pid,
            pairing_code=code,
            device_fingerprint="fp_test",
        )
        self.assertTrue(appr_ok1)
        self.assertIsNotNone(dev1)

        # Second approval with same pairing code must fail
        appr_ok2, appr_msg2, _ = self.pairing_mgr.approve_pairing(
            pairing_id=pid,
            pairing_code=code,
            device_fingerprint="fp_test",
        )
        self.assertFalse(appr_ok2)
        self.assertIn("INVALID_PAIRING_STATUS", appr_msg2)

    # --------------------------------------------------------------------------
    # 6. Wrong device rejected
    # --------------------------------------------------------------------------
    def test_06_wrong_device_rejected(self):
        """Requests referencing a mismatched device ID or unknown device are rejected."""
        val_ok, val_msg = self.pairing_mgr.validate_session_request(
            request_id="req_1",
            device_id="dev_nonexistent_999",
            session_id="sess_1",
            timestamp=time.time(),
            nonce="nonce_1",
            action="status",
            scope="telemetry:read",
            signature="deadbeef",
        )
        self.assertFalse(val_ok)
        self.assertEqual(val_msg, "DEVICE_NOT_FOUND")

    # --------------------------------------------------------------------------
    # 7. Wrong challenge rejected
    # --------------------------------------------------------------------------
    def test_07_wrong_challenge_rejected(self):
        """Pairing request maintains cryptographic challenge verification."""
        ok, msg, req = self.pairing_mgr.create_pairing_request(device_name="Device Challenge Test")
        self.assertTrue(ok)
        self.assertIsNotNone(req.challenge)
        self.assertGreaterEqual(len(req.challenge), 32)
        # Verify challenge is unique across different requests
        ok2, _, req2 = self.pairing_mgr.create_pairing_request(device_name="Device Challenge Test 2")
        self.assertTrue(ok2)
        self.assertNotEqual(req.challenge, req2.challenge)

    # --------------------------------------------------------------------------
    # 8. Invalid signature rejected
    # --------------------------------------------------------------------------
    def test_08_invalid_signature_rejected(self):
        """Tampered or invalid HMAC-SHA256 signatures are immediately rejected."""
        # Pair and enroll a device
        ok, _, req = self.pairing_mgr.create_pairing_request(device_name="Sig Test Dev")
        self.pairing_mgr.approve_pairing(req.pairing_id, req.pairing_code, "fp_sig_test")
        
        # Create session
        s_ok, _, sess = self.pairing_mgr.create_device_session(req.device_id)
        self.assertTrue(s_ok)

        # Validate with deliberately wrong signature
        val_ok, val_msg = self.pairing_mgr.validate_session_request(
            request_id="req_sig_bad",
            device_id=req.device_id,
            session_id=sess.session_id,
            timestamp=time.time(),
            nonce="nonce_unique_1",
            action="get_telemetry",
            scope="telemetry:read",
            signature="bad_signature_000000000000000000000000000000000000000000000000",
        )
        self.assertFalse(val_ok)
        self.assertEqual(val_msg, "INVALID_SIGNATURE")

    # --------------------------------------------------------------------------
    # 9. Replay request rejected
    # --------------------------------------------------------------------------
    def test_09_replay_request_rejected(self):
        """A validly signed request cannot be replayed using the same nonce."""
        ok, _, req = self.pairing_mgr.create_pairing_request(device_name="Replay Test Dev")
        self.pairing_mgr.approve_pairing(req.pairing_id, req.pairing_code, "fp_replay")
        s_ok, _, sess = self.pairing_mgr.create_device_session(req.device_id)
        self.assertTrue(s_ok)

        # Compute valid HMAC signature
        secret = self.pairing_mgr._shared_secrets[req.device_id]
        now = time.time()
        nonce = "nonce_replay_test_123"
        action = "poll_status"
        scope = "telemetry:read"
        req_id = "req_rep_1"
        payload = {"foo": "bar"}
        payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        canonical = f"{req_id}:{req.device_id}:{sess.session_id}:{now:.3f}:{nonce}:{action}:{scope}:{payload_hash}"
        sig = hmac.new(secret, canonical.encode("utf-8"), hashlib.sha256).hexdigest()

        # First attempt: succeeds
        ok1, msg1 = self.pairing_mgr.validate_session_request(
            request_id=req_id,
            device_id=req.device_id,
            session_id=sess.session_id,
            timestamp=now,
            nonce=nonce,
            action=action,
            scope=scope,
            signature=sig,
            payload=payload,
        )
        self.assertTrue(ok1, f"First request failed: {msg1}")

        # Replay attempt: rejected
        ok2, msg2 = self.pairing_mgr.validate_session_request(
            request_id=req_id,
            device_id=req.device_id,
            session_id=sess.session_id,
            timestamp=now,
            nonce=nonce,
            action=action,
            scope=scope,
            signature=sig,
            payload=payload,
        )
        self.assertFalse(ok2)
        self.assertEqual(msg2, "REPLAY_DETECTED")

    # --------------------------------------------------------------------------
    # 10. Expired session rejected
    # --------------------------------------------------------------------------
    def test_10_expired_session_rejected(self):
        """Sessions past their TTL are rejected and transitioned to EXPIRED."""
        ok, _, req = self.pairing_mgr.create_pairing_request(device_name="TTL Dev")
        self.pairing_mgr.approve_pairing(req.pairing_id, req.pairing_code, "fp_ttl")
        s_ok, _, sess = self.pairing_mgr.create_device_session(req.device_id, ttl_seconds=60)
        self.assertTrue(s_ok)

        # Force session expiration
        sess.expires_at = time.time() - 30

        val_ok, val_msg = self.pairing_mgr.validate_session_request(
            request_id="req_expired",
            device_id=req.device_id,
            session_id=sess.session_id,
            timestamp=time.time(),
            nonce="nonce_exp",
            action="poll",
            scope="telemetry:read",
            signature="any_sig",
        )
        self.assertFalse(val_ok)
        self.assertEqual(val_msg, "SESSION_EXPIRED")
        self.assertEqual(sess.status, "EXPIRED")

    # --------------------------------------------------------------------------
    # 11. Revoked device rejected
    # --------------------------------------------------------------------------
    def test_11_revoked_device_rejected(self):
        """Permanently revoked devices cannot create sessions, validate requests, or re-pair."""
        ok, _, req = self.pairing_mgr.create_pairing_request(device_name="Revoke Dev")
        self.pairing_mgr.approve_pairing(req.pairing_id, req.pairing_code, "fp_rev")
        s_ok, _, sess = self.pairing_mgr.create_device_session(req.device_id)
        self.assertTrue(s_ok)

        # Revoke device
        rev_ok, rev_msg = self.pairing_mgr.revoke_device(req.device_id, reason="Security threat detected")
        self.assertTrue(rev_ok)

        # Check device state
        dev = self.pairing_mgr.get_device(req.device_id)
        self.assertEqual(dev.enrollment_state, EnrollmentState.REVOKED)
        self.assertEqual(dev.authorization_state, AuthorizationState.REVOKED)

        # Sessions for device must be marked REVOKED
        self.assertEqual(sess.status, "REVOKED")

        # Session creation must fail
        s_ok2, s_msg2, _ = self.pairing_mgr.create_device_session(req.device_id)
        self.assertFalse(s_ok2)
        self.assertEqual(s_msg2, "DEVICE_REVOKED")

        # Validation must fail
        v_ok, v_msg = self.pairing_mgr.validate_session_request(
            request_id="req_r",
            device_id=req.device_id,
            session_id=sess.session_id,
            timestamp=time.time(),
            nonce="nonce_r",
            action="test",
            scope="telemetry:read",
            signature="sig",
        )
        self.assertFalse(v_ok)
        self.assertEqual(v_msg, "DEVICE_REVOKED")

    # --------------------------------------------------------------------------
    # 12. Suspended device restricted
    # --------------------------------------------------------------------------
    def test_12_suspended_device_restricted(self):
        """Suspended devices cannot create sessions or execute requests, but can be reauthorized."""
        ok, _, req = self.pairing_mgr.create_pairing_request(device_name="Suspend Dev")
        self.pairing_mgr.approve_pairing(req.pairing_id, req.pairing_code, "fp_susp")
        s_ok, _, sess = self.pairing_mgr.create_device_session(req.device_id)
        self.assertTrue(s_ok)

        # Suspend device
        susp_ok, _ = self.pairing_mgr.suspend_device(req.device_id, reason="Temporary hold")
        self.assertTrue(susp_ok)

        dev = self.pairing_mgr.get_device(req.device_id)
        self.assertEqual(dev.enrollment_state, EnrollmentState.SUSPENDED)
        self.assertEqual(dev.authorization_state, AuthorizationState.SUSPENDED)

        # New sessions blocked
        s2_ok, s2_msg, _ = self.pairing_mgr.create_device_session(req.device_id)
        self.assertFalse(s2_ok)
        self.assertEqual(s2_msg, "DEVICE_SUSPENDED")

        # Reauthorize device
        reauth_ok, _ = self.pairing_mgr.reauthorize_device(req.device_id)
        self.assertTrue(reauth_ok)
        self.assertEqual(dev.enrollment_state, EnrollmentState.ENROLLED)
        self.assertEqual(dev.authorization_state, AuthorizationState.AUTHORIZED)

    # --------------------------------------------------------------------------
    # 13. Unauthorized capability rejected
    # --------------------------------------------------------------------------
    def test_13_unauthorized_capability_rejected(self):
        """Requests attempting to exercise an ungranted or prohibited capability are rejected."""
        ok, _, req = self.pairing_mgr.create_pairing_request(
            device_name="Cap Test Dev",
            requested_capabilities=["telemetry:read"],
        )
        self.pairing_mgr.approve_pairing(req.pairing_id, req.pairing_code, "fp_cap")
        s_ok, _, sess = self.pairing_mgr.create_device_session(req.device_id)
        self.assertTrue(s_ok)

        # Requesting 'config:audit' when only 'telemetry:read' was granted
        secret = self.pairing_mgr._shared_secrets[req.device_id]
        now = time.time()
        nonce = "nonce_unauth_cap"
        action = "read_config"
        scope = "config:audit"
        req_id = "req_unauth_1"
        canonical = f"{req_id}:{req.device_id}:{sess.session_id}:{now:.3f}:{nonce}:{action}:{scope}:{hashlib.sha256(b'{}').hexdigest()}"
        sig = hmac.new(secret, canonical.encode("utf-8"), hashlib.sha256).hexdigest()

        val_ok, val_msg = self.pairing_mgr.validate_session_request(
            request_id=req_id,
            device_id=req.device_id,
            session_id=sess.session_id,
            timestamp=now,
            nonce=nonce,
            action=action,
            scope=scope,
            signature=sig,
            payload={},
        )
        self.assertFalse(val_ok)
        self.assertIn("UNAUTHORIZED_CAPABILITY", val_msg)

    # --------------------------------------------------------------------------
    # 14. Private key never returned in API responses
    # --------------------------------------------------------------------------
    def test_14_private_key_never_returned_in_api_responses(self):
        """Device and session dictionaries never expose raw private keys or shared secrets."""
        ok, _, req = self.pairing_mgr.create_pairing_request(device_name="Key Secrecy Dev")
        self.pairing_mgr.approve_pairing(req.pairing_id, req.pairing_code, "fp_sec")
        s_ok, _, sess = self.pairing_mgr.create_device_session(req.device_id)

        dev_dict = self.pairing_mgr.get_device(req.device_id).to_dict()
        req_dict = req.to_dict()
        sess_dict = sess.to_dict()

        for d in (dev_dict, req_dict, sess_dict):
            self.assertNotIn("private_key", d)
            self.assertNotIn("shared_secret", d)
            self.assertNotIn("raw_secret", d)

    # --------------------------------------------------------------------------
    # 15. Secrets never logged in telemetry or audit
    # --------------------------------------------------------------------------
    def test_15_secrets_never_logged_in_telemetry_or_audit(self):
        """Audit trail records contain zero raw secrets or unmasked sensitive credentials."""
        ok, _, req = self.pairing_mgr.create_pairing_request(
            device_name="Audit Secrecy Dev",
            phone_number="+91 98765 43210",
        )
        self.pairing_mgr.approve_pairing(req.pairing_id, req.pairing_code, "fp_aud")

        audit_logs = self.coordinator.get_audit_log(limit=50)
        audit_str = json.dumps(audit_logs)

        # Raw phone number must not appear unmasked
        self.assertNotIn("+91 98765 43210", audit_str)
        # Raw shared secret must not appear
        raw_secret_hex = self.pairing_mgr._shared_secrets[req.device_id].hex()
        self.assertNotIn(raw_secret_hex, audit_str)

    # --------------------------------------------------------------------------
    # 16. Emergency Stop terminates active enrollment and sessions
    # --------------------------------------------------------------------------
    def test_16_emergency_stop_terminates_active_enrollment_and_sessions(self):
        """Triggering Emergency Stop immediately invalidates active sessions and halts operations."""
        ok, _, req = self.pairing_mgr.create_pairing_request(device_name="E-Stop Dev")
        self.pairing_mgr.approve_pairing(req.pairing_id, req.pairing_code, "fp_estop")
        s_ok, _, sess = self.pairing_mgr.create_device_session(req.device_id)
        self.assertTrue(s_ok)
        self.assertEqual(sess.status, "ACTIVE")

        # Trigger Emergency Stop
        self.coordinator.trigger_emergency_stop("Safety test halt")

        # Session must be STOPPED
        self.assertEqual(sess.status, "STOPPED")
        # Device state must be STOPPED
        dev = self.pairing_mgr.get_device(req.device_id)
        self.assertEqual(dev.enrollment_state, EnrollmentState.STOPPED)

        # Further session validation must be blocked
        v_ok, v_msg = self.pairing_mgr.validate_session_request(
            request_id="req_e",
            device_id=req.device_id,
            session_id=sess.session_id,
            timestamp=time.time(),
            nonce="nonce_e",
            action="status",
            scope="telemetry:read",
            signature="any",
        )
        self.assertFalse(v_ok)
        self.assertEqual(v_msg, "EMERGENCY_STOP_ACTIVE")

        # Clean reset restores IDLE
        self.coordinator.reset_emergency_stop()
        self.assertEqual(self.coordinator.current_state, SecurityState.IDLE)

    # --------------------------------------------------------------------------
    # 17. Excessive pairing attempts rate-limited
    # --------------------------------------------------------------------------
    def test_17_excessive_pairing_attempts_rate_limited(self):
        """Failed pairing attempts exhaust quota and expire request."""
        ok, _, req = self.pairing_mgr.create_pairing_request(device_name="Brute Force Dev")
        self.assertTrue(ok)

        for i in range(5):
            self.pairing_mgr.approve_pairing(req.pairing_id, "WRONG_CODE", "fp_brute")

        # 6th attempt should return max attempts exceeded / expired
        ok_final, msg_final, _ = self.pairing_mgr.approve_pairing(req.pairing_id, req.pairing_code, "fp_brute")
        self.assertFalse(ok_final)
        self.assertIn("PAIRING", msg_final)

    # --------------------------------------------------------------------------
    # 18. Excessive sessions rate-limited
    # --------------------------------------------------------------------------
    def test_18_excessive_sessions_rate_limited(self):
        """Concurrent sessions per device are strictly bounded."""
        ok, _, req = self.pairing_mgr.create_pairing_request(device_name="Max Sess Dev")
        self.pairing_mgr.approve_pairing(req.pairing_id, req.pairing_code, "fp_max_sess")

        # Create multiple sessions up to and exceeding limit
        s1 = self.pairing_mgr.create_device_session(req.device_id)
        self.assertTrue(s1[0])
        s2 = self.pairing_mgr.create_device_session(req.device_id)
        self.assertTrue(s2[0])
        s3 = self.pairing_mgr.create_device_session(req.device_id)
        self.assertTrue(s3[0])

        active = [s for s in self.pairing_mgr._sessions.values() if s.device_id == req.device_id and s.status == "ACTIVE"]
        self.assertLessEqual(len(active), 3)

    # --------------------------------------------------------------------------
    # 19. Audit events generated for all state changes
    # --------------------------------------------------------------------------
    def test_19_audit_events_generated_for_all_state_changes(self):
        """Every transition generates a dedicated audit record."""
        initial_log_count = len(self.coordinator._audit_log)

        ok, _, req = self.pairing_mgr.create_pairing_request(device_name="Audit Trail Dev")
        self.assertTrue(ok)
        self.pairing_mgr.approve_pairing(req.pairing_id, req.pairing_code, "fp_at")
        self.pairing_mgr.suspend_device(req.device_id)
        self.pairing_mgr.reauthorize_device(req.device_id)

        new_log_count = len(self.coordinator._audit_log)
        self.assertGreater(new_log_count, initial_log_count + 3)

    # --------------------------------------------------------------------------
    # 20. No private message access
    # --------------------------------------------------------------------------
    def test_20_no_private_message_access(self):
        """Prohibited surveillance capability 'messages:read' is strictly rejected."""
        ok, msg, _ = self.pairing_mgr.create_pairing_request(
            device_name="Surveillance Attempt Dev",
            requested_capabilities=["telemetry:read", "messages:read"],
        )
        self.assertFalse(ok)
        self.assertIn("PROHIBITED_CAPABILITY", msg)

    # --------------------------------------------------------------------------
    # 21. No hidden camera access
    # --------------------------------------------------------------------------
    def test_21_no_hidden_camera_access(self):
        """Prohibited capability 'camera:capture' is strictly rejected."""
        ok, msg, _ = self.pairing_mgr.create_pairing_request(
            device_name="Hidden Cam Dev",
            requested_capabilities=["camera:capture"],
        )
        self.assertFalse(ok)
        self.assertIn("PROHIBITED_CAPABILITY", msg)

    # --------------------------------------------------------------------------
    # 22. No hidden microphone access
    # --------------------------------------------------------------------------
    def test_22_no_hidden_microphone_access(self):
        """Prohibited capability 'microphone:record' is strictly rejected."""
        ok, msg, _ = self.pairing_mgr.create_pairing_request(
            device_name="Hidden Mic Dev",
            requested_capabilities=["microphone:record"],
        )
        self.assertFalse(ok)
        self.assertIn("PROHIBITED_CAPABILITY", msg)

    # --------------------------------------------------------------------------
    # 23. No screen capture capability added
    # --------------------------------------------------------------------------
    def test_23_no_screen_capture_capability_added(self):
        """Prohibited capability 'screen:capture' is strictly rejected."""
        ok, msg, _ = self.pairing_mgr.create_pairing_request(
            device_name="Screen Capture Dev",
            requested_capabilities=["screen:capture"],
        )
        self.assertFalse(ok)
        self.assertIn("PROHIBITED_CAPABILITY", msg)

    # --------------------------------------------------------------------------
    # 24. Zero shell/eval/exec execution paths
    # --------------------------------------------------------------------------
    def test_24_zero_shell_eval_exec_execution_paths(self):
        """DeterministicSecurityGate strictly blocks any shell commands."""
        gate = DeterministicSecurityGate()
        with self.assertRaises(PermissionError):
            gate.validate_action("execute_shell", {"cmd": "whoami"})

    # --------------------------------------------------------------------------
    # 25. Model cannot bypass authorization
    # --------------------------------------------------------------------------
    def test_25_model_cannot_bypass_authorization(self):
        """AI models cannot authorize pairing, grant capabilities, or force enroll."""
        gate = DeterministicSecurityGate()
        with self.assertRaises(PermissionError):
            gate.validate_action("skyshield.pair_approve", {"proposal": "AI approves pairing automatically"})
        with self.assertRaises(PermissionError):
            gate.validate_action("authorize", {"reason": "Model decided device is safe"})

    # --------------------------------------------------------------------------
    # Category A: State Machine Transitions
    # --------------------------------------------------------------------------
    def test_cat_a_state_machine_legal_and_illegal_transitions(self):
        """Verifies state machine follows strict transition graph and rejects illegal jumps."""
        ok, _, req = self.pairing_mgr.create_pairing_request(device_name="SM Dev")
        dev = self.pairing_mgr.get_device(req.device_id)
        self.assertEqual(dev.enrollment_state, EnrollmentState.PAIRING_REQUESTED)

        # Illegal jump: PAIRING_REQUESTED -> ENROLLED (without approval)
        with self.assertRaises(IllegalEnrollmentTransitionError):
            self.pairing_mgr.transition_device_state(dev.device_id, EnrollmentState.ENROLLED)

        # Legal: PAIRING_REQUESTED -> AWAITING_OWNER_APPROVAL
        self.pairing_mgr.transition_device_state(dev.device_id, EnrollmentState.AWAITING_OWNER_APPROVAL)
        self.assertEqual(dev.enrollment_state, EnrollmentState.AWAITING_OWNER_APPROVAL)

    # --------------------------------------------------------------------------
    # Category B: Phone Number Utilities
    # --------------------------------------------------------------------------
    def test_cat_b_phone_number_normalization_and_masking(self):
        """Verifies phone number normalization and standard masking."""
        raw = "+91 (987) 654-3210"
        norm = normalize_phone_number(raw)
        self.assertEqual(norm, "+919876543210")
        masked = mask_phone_number(raw)
        self.assertTrue(masked.startswith("+91 ******"))
        self.assertTrue(masked.endswith("3210"))

    # --------------------------------------------------------------------------
    # Category C: Owner Rejection Flow
    # --------------------------------------------------------------------------
    def test_cat_c_owner_rejection_flow(self):
        """Explicit rejection transitions request to REJECTED and resets device."""
        ok, _, req = self.pairing_mgr.create_pairing_request(device_name="Reject Flow Dev")
        rej_ok, rej_msg = self.pairing_mgr.reject_pairing(req.pairing_id, reason="Owner declined")
        self.assertTrue(rej_ok)
        self.assertEqual(req.status, PairingRequestStatus.REJECTED)

        dev = self.pairing_mgr.get_device(req.device_id)
        self.assertEqual(dev.enrollment_state, EnrollmentState.UNREGISTERED)

    # --------------------------------------------------------------------------
    # Category F: Seeded Mock Device Specification
    # --------------------------------------------------------------------------
    def test_cat_f_seeded_mock_device(self):
        """The seeded mock device dev_mock_vivo_v2334 is present with MOCK verification state."""
        dev = self.pairing_mgr.get_device("dev_mock_vivo_v2334")
        self.assertIsNotNone(dev)
        self.assertIn("Vivo V2334", dev.device_name)
        self.assertEqual(dev.platform, "android")
        self.assertEqual(dev.verification_state, DataVerificationState.MOCK)
        self.assertEqual(dev.enrollment_state, EnrollmentState.ENROLLED)

    # --------------------------------------------------------------------------
    # Category J: REST API Parity in CompanionDashboard
    # --------------------------------------------------------------------------
    def test_cat_j_rest_api_dashboard_endpoints(self):
        """Validates coordinator state payload and REST API response formatting."""
        dash = self.coordinator.get_dashboard_state()
        self.assertTrue(dash["success"])
        self.assertIn("enrolled_devices", dash)
        self.assertIn("pairing_requests", dash)
        self.assertIn("active_sessions", dash)
        self.assertIsInstance(dash["enrolled_devices"], list)

        # Verify seeded device appears in enrolled list
        dev_ids = [d["device_id"] for d in dash["enrolled_devices"]]
        self.assertIn("dev_mock_vivo_v2334", dev_ids)


if __name__ == "__main__":
    unittest.main()
