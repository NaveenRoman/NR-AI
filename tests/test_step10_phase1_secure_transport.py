"""
Unit and Integration Tests for Step 10 Phase 1:
Secure Phone <-> PC Communication Foundation.

Validates all 24 required security contracts:
1. Device identity generation
2. Pairing success
3. Pairing rejection (invalid/expired code)
4. Revoked device rejection
5. Authentication success & session creation
6. Authentication failure & invalid credential handling
7. Expired session rejection
8. Replay attack rejection (nonce & timestamp drift)
9. Malformed request rejection
10. Invalid / ungranted scope rejection
11. Unauthorized & prohibited action rejection (shell, powershell, adb, fs)
12. Request and payload size boundaries
13. Rate limiting & authentication lockout
14. Emergency stop deterministic halt (zero LLM dependency)
15. Security audit logging generation
16. Audit log credential & secret redaction
17. Localhost boundary enforcement
18. Public bind prevention (0.0.0.0 rejection)
19. Model isolation gate (LLMs blocked from direct tools/sockets/shell)
20. Transport integrity & ciphertext tampering detection
21. Session cleanup for expired tokens
22. Concurrent session bounds (per-device & global)
23. Credential rotation and immediate revocation propagation
24. Existing Android /api/command backward compatibility
"""

import hashlib
import hmac
import json
import os
from pathlib import Path
import socket
import sys
import time
import unittest
import urllib.request
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.remote import (
    ALLOWED_HOSTS,
    AuditRecord,
    DEFAULT_HOST,
    DeviceIdentity,
    EmergencyStopController,
    EmergencyStopStatus,
    EncryptedPacket,
    MAX_PAYLOAD_BYTES,
    MAX_REQUEST_BYTES,
    ModelIsolationGate,
    PairingManager,
    PairingState,
    PCIdentity,
    PhonePermissionScope,
    PROHIBITED_ACTIONS,
    PROHIBITED_HOSTS,
    RateLimiter,
    SecureDashboardServer,
    SecureGateway,
    SecureRequest,
    SecureResponse,
    SecureTransport,
    SecurityAuditLogger,
    SecurityBindingError,
    Session,
    SessionManager,
    TransportIntegrityError,
    TransportModeError,
    TransportSecurityMode,
    authorize_action,
    parse_and_validate_request,
    redact_sensitive_data,
)


def _sign_request(req: SecureRequest, secret_hex: str) -> str:
    """Helper to compute valid HMAC-SHA256 signature for a SecureRequest."""
    canonical = req.compute_canonical_string()
    secret_bytes = bytes.fromhex(secret_hex)
    return hmac.new(secret_bytes, canonical.encode("utf-8"), hashlib.sha256).hexdigest()


class DummyCompanion:
    """Mock companion for backward compatibility and interaction testing."""
    def interact(self, cmd: str, speak_output: bool = False):
        return {
            "text": f"Companion executed: {cmd}",
            "category": "TEST_COMMAND",
            "success": True,
        }

    def get_status_snapshot(self):
        return {
            "assistant_status": "🟢 Online",
            "avatar_mode": "IDLE",
            "active_agent": "Agent-1-Architect",
        }


class TestStep10Phase1SecureTransport(unittest.TestCase):

    def setUp(self):
        self.pc_identity = PCIdentity(
            pc_id="PC-TEST-001",
            hostname="localhost-test",
            service_name="NR-AI Test Service",
        )
        self.pairing_mgr = PairingManager(pc_identity=self.pc_identity)
        self.session_mgr = SessionManager(self.pairing_mgr)
        self.rate_limiter = RateLimiter(rate_limit_rps=10.0, burst_limit=15)
        self.emergency_stop = EmergencyStopController()
        self.audit_logger = SecurityAuditLogger()
        self.companion = DummyCompanion()
        self.gateway = SecureGateway(
            companion=self.companion,
            pairing_manager=self.pairing_mgr,
            session_manager=self.session_mgr,
            rate_limiter=self.rate_limiter,
            emergency_stop=self.emergency_stop,
            audit_logger=self.audit_logger,
            transport_mode=TransportSecurityMode.ENCRYPTED_SESSION,
        )

    # 1. Device identity generation
    def test_01_device_identity_generation(self):
        dev = self.pairing_mgr.register_device(
            device_id="dev-phone-001",
            device_name="Pixel 8 Pro",
            platform="Android 14",
            app_version="2.4.0",
        )
        self.assertEqual(dev.device_id, "dev-phone-001")
        self.assertEqual(dev.device_name, "Pixel 8 Pro")
        self.assertEqual(dev.state, PairingState.UNPAIRED)
        self.assertTrue(len(dev.fingerprint) > 10)
        self.assertIsNone(dev.paired_at)

    # 2. Pairing success
    def test_02_pairing_success(self):
        code = self.pairing_mgr.generate_pairing_code(target_device_id="dev-phone-002")
        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())

        success, msg, secret = self.pairing_mgr.confirm_pairing(
            device_id="dev-phone-002",
            pairing_code=code,
            device_name="Samsung S24",
            platform="Android 14",
        )
        self.assertTrue(success)
        self.assertEqual(msg, "PAIRING_SUCCESS")
        self.assertIsNotNone(secret)
        self.assertEqual(len(secret), 64)  # 32 bytes hex

        dev = self.pairing_mgr.get_device("dev-phone-002")
        self.assertEqual(dev.state, PairingState.PAIRED)
        self.assertIsNotNone(dev.paired_at)
        self.assertTrue(self.pairing_mgr.is_device_paired("dev-phone-002"))

    # 3. Pairing rejection (invalid / expired code)
    def test_03_pairing_rejection_invalid_code(self):
        code = self.pairing_mgr.generate_pairing_code()
        
        # Wrong code
        success, msg, secret = self.pairing_mgr.confirm_pairing(
            device_id="dev-phone-bad",
            pairing_code="000000" if code != "000000" else "999999",
        )
        self.assertFalse(success)
        self.assertEqual(msg, "INVALID_PAIRING_CODE")
        self.assertIsNone(secret)

    # 4. Revoked device rejection
    def test_04_revoked_device_rejection(self):
        code = self.pairing_mgr.generate_pairing_code()
        self.pairing_mgr.confirm_pairing("dev-to-revoke", code)
        self.assertTrue(self.pairing_mgr.is_device_paired("dev-to-revoke"))

        # Explicitly revoke device
        self.pairing_mgr.revoke_device("dev-to-revoke", reason="Lost phone")
        self.assertTrue(self.pairing_mgr.is_device_revoked("dev-to-revoke"))
        self.assertFalse(self.pairing_mgr.is_device_paired("dev-to-revoke"))
        self.assertIsNone(self.pairing_mgr.get_device_secret("dev-to-revoke"))

        # Attempt to create session on revoked device must fail
        ok, msg, sess = self.session_mgr.create_session("dev-to-revoke")
        self.assertFalse(ok)
        self.assertEqual(msg, "DEVICE_REVOKED")

    # 5. Authentication success & session creation
    def test_05_authentication_success_session_creation(self):
        code = self.pairing_mgr.generate_pairing_code()
        _, _, secret = self.pairing_mgr.confirm_pairing("dev-auth-ok", code)

        ok, msg, session = self.session_mgr.create_session("dev-auth-ok", ttl_seconds=1800)
        self.assertTrue(ok)
        self.assertEqual(msg, "SESSION_CREATED")
        self.assertIsNotNone(session)
        self.assertTrue(session.session_id.startswith("SES-"))
        self.assertEqual(len(session.session_key_hex), 64)
        self.assertFalse(session.is_expired())

        # Validate session
        v_ok, v_msg, v_sess = self.session_mgr.validate_session(session.session_id, "dev-auth-ok")
        self.assertTrue(v_ok)
        self.assertEqual(v_sess.session_id, session.session_id)

    # 6. Authentication failure & invalid credentials
    def test_06_authentication_failure_invalid_credentials(self):
        code = self.pairing_mgr.generate_pairing_code()
        self.pairing_mgr.confirm_pairing("dev-auth-fail", code)

        # Wrong secret in gateway auth
        resp = self.gateway.handle_auth_request(
            {"device_id": "dev-auth-fail", "device_secret": "deadbeef" * 8},
            client_ip="127.0.0.1",
        )
        self.assertEqual(resp.status, "DENIED")
        self.assertEqual(resp.code, 401)
        self.assertIn("INVALID_CREDENTIALS", resp.error)

    # 7. Expired session rejection
    def test_07_expired_session_rejection(self):
        code = self.pairing_mgr.generate_pairing_code()
        self.pairing_mgr.confirm_pairing("dev-exp", code)
        ok, _, session = self.session_mgr.create_session("dev-exp", ttl_seconds=60)
        self.assertTrue(ok)

        # Artificially test expiration with simulated future time
        future = time.time() + 120
        self.assertTrue(session.is_expired(current_time=future))

        # Direct expiration check in session manager
        session.expires_at = time.time() - 10
        v_ok, v_msg, _ = self.session_mgr.validate_session(session.session_id, "dev-exp")
        self.assertFalse(v_ok)
        self.assertEqual(v_msg, "SESSION_EXPIRED")

    # 8. Replay attack rejection (reused nonce & timestamp drift)
    def test_08_replay_attack_rejection(self):
        code = self.pairing_mgr.generate_pairing_code()
        _, _, secret = self.pairing_mgr.confirm_pairing("dev-replay", code)
        _, _, session = self.session_mgr.create_session("dev-replay")

        now = time.time()
        req = SecureRequest(
            request_id="req-rep-1",
            device_id="dev-replay",
            session_id=session.session_id,
            timestamp=now,
            nonce="unique-nonce-12345",
            action="status.read",
            scope="READ_STATUS",
            payload={},
        )
        req.signature = _sign_request(req, secret)

        # First presentation: valid
        sig_ok, sig_msg = self.session_mgr.verify_request_signature(req)
        self.assertTrue(sig_ok)
        self.assertEqual(sig_msg, "SIGNATURE_VALID")

        # Second presentation with same nonce: replay rejection
        sig_ok2, sig_msg2 = self.session_mgr.verify_request_signature(req)
        self.assertFalse(sig_ok2)
        self.assertIn("REPLAY_DETECTED", sig_msg2)

        # Drifted timestamp (> 60s ago)
        req_drift = SecureRequest(
            request_id="req-rep-drift",
            device_id="dev-replay",
            session_id=session.session_id,
            timestamp=now - 120,
            nonce="nonce-drift-abc",
            action="status.read",
            scope="READ_STATUS",
            payload={},
        )
        req_drift.signature = _sign_request(req_drift, secret)
        drift_ok, drift_msg = self.session_mgr.verify_request_signature(req_drift)
        self.assertFalse(drift_ok)
        self.assertIn("REQUEST_EXPIRED_OR_DRIFTED", drift_msg)

    # 9. Malformed request rejection
    def test_09_malformed_request_rejection(self):
        # Empty string
        ok, req, err = parse_and_validate_request("")
        self.assertFalse(ok)
        self.assertIn("MALFORMED_JSON", err)

        # Missing required fields
        bad_json = json.dumps({"request_id": "r1", "device_id": "d1"})
        ok, req, err = parse_and_validate_request(bad_json)
        self.assertFalse(ok)
        self.assertIn("MISSING_REQUIRED_FIELD", err)

    # 10. Invalid / ungranted scope rejection
    def test_10_invalid_scope_rejection(self):
        # Grant only READ_STATUS
        granted_scopes = {PhonePermissionScope.READ_STATUS}
        res = authorize_action(
            action="command.send",
            granted_scopes=granted_scopes,
            is_emergency_active=False,
        )
        self.assertFalse(res.allowed)
        self.assertEqual(res.decision_code, "SCOPE_DENIED")
        self.assertEqual(res.required_scope, "SEND_COMMAND")

    # 11. Unauthorized & prohibited action rejection (shell, powershell, adb, filesystem)
    def test_11_unauthorized_action_rejection(self):
        all_scopes = set(PhonePermissionScope)
        
        # Test direct prohibited actions
        for prohibited in ("shell", "powershell", "cmd.exe", "adb_shell", "filesystem_delete"):
            res = authorize_action(
                action=prohibited,
                granted_scopes=all_scopes,
                is_emergency_active=False,
            )
            self.assertFalse(res.allowed)
            self.assertEqual(res.decision_code, "PROHIBITED_ACTION")

        # Test prohibited command payloads inside command.send
        res_cmd = authorize_action(
            action="command.send",
            granted_scopes=all_scopes,
            is_emergency_active=False,
            command_text="run powershell.exe Get-Process",
        )
        self.assertFalse(res_cmd.allowed)
        self.assertEqual(res_cmd.decision_code, "PROHIBITED_PAYLOAD")

    # 12. Request and payload size boundaries
    def test_12_request_and_payload_size_limits(self):
        # Oversized request body > 100 KB
        huge_text = "x" * (MAX_REQUEST_BYTES + 1000)
        ok, req, err = parse_and_validate_request(huge_text)
        self.assertFalse(ok)
        self.assertIn("REQUEST_TOO_LARGE", err)

        # Oversized payload field > 64 KB
        huge_payload = {"data": "y" * (MAX_PAYLOAD_BYTES + 500)}
        req_dict = {
            "request_id": "req-large",
            "device_id": "dev-1",
            "session_id": "ses-1",
            "timestamp": time.time(),
            "nonce": "n1",
            "action": "status.read",
            "scope": "READ_STATUS",
            "payload": huge_payload,
            "signature": "sig",
        }
        ok, req, err = parse_and_validate_request(json.dumps(req_dict))
        self.assertFalse(ok)
        self.assertIn("PAYLOAD_TOO_LARGE", err)

    # 13. Rate limiting & authentication lockout
    def test_13_rate_limiting_and_auth_lockout(self):
        limiter = RateLimiter(rate_limit_rps=2.0, burst_limit=3, max_auth_failures=3, lockout_duration_seconds=10)
        
        # 3 requests allowed under burst
        self.assertTrue(limiter.allow_request("client-1")[0])
        self.assertTrue(limiter.allow_request("client-1")[0])
        self.assertTrue(limiter.allow_request("client-1")[0])
        # 4th request immediately fails
        allowed, reason, retry_after = limiter.allow_request("client-1")
        self.assertFalse(allowed)
        self.assertIn("RATE_LIMIT_EXCEEDED", reason)
        self.assertGreater(retry_after, 0)

        # Auth lockout test
        self.assertFalse(limiter.record_auth_failure("auth-client")[0])
        self.assertFalse(limiter.record_auth_failure("auth-client")[0])
        is_locked, failures, locked_until = limiter.record_auth_failure("auth-client")
        self.assertTrue(is_locked)
        self.assertEqual(failures, 3)
        self.assertIsNotNone(locked_until)

        # Subsequent allow_request must be locked out
        allow_res, allow_msg, _ = limiter.allow_request("auth-client")
        self.assertFalse(allow_res)
        self.assertIn("AUTH_LOCKOUT_ACTIVE", allow_msg)

    # 14. Emergency stop deterministic halt (zero LLM dependency)
    def test_14_emergency_stop_deterministic_halt(self):
        estop = EmergencyStopController()
        cancelled_flags = []

        estop.register_cancellation_callback(lambda: cancelled_flags.append(True))
        self.assertFalse(estop.is_active())

        # Trigger emergency stop
        status = estop.trigger(triggered_by="TEST_OP", reason="Critical safety halt")
        self.assertTrue(estop.is_active())
        self.assertEqual(status.triggered_by, "TEST_OP")
        self.assertEqual(len(cancelled_flags), 1)

        # Normal command authorization must now be blocked
        res = authorize_action(
            action="command.send",
            granted_scopes=set(PhonePermissionScope),
            is_emergency_active=estop.is_active(),
        )
        self.assertFalse(res.allowed)
        self.assertEqual(res.decision_code, "EMERGENCY_STOP_ACTIVE")

        # Emergency stop itself and emergency status are still allowed
        res_stat = authorize_action(
            action="emergency.status",
            granted_scopes=set(PhonePermissionScope),
            is_emergency_active=estop.is_active(),
        )
        self.assertTrue(res_stat.allowed)

        # Reset
        estop.reset()
        self.assertFalse(estop.is_active())

    # 15. Security audit logging generation
    def test_15_audit_logging_generation(self):
        audit = SecurityAuditLogger()
        audit.log_event(
            event_type="PAIRING_SUCCESS",
            result="SUCCESS",
            device_id="dev-audit-1",
            client_ip="127.0.0.1",
            reason="Device paired",
        )
        records = audit.get_records(device_id="dev-audit-1")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].event_type, "PAIRING_SUCCESS")
        self.assertEqual(records[0].result, "SUCCESS")
        self.assertEqual(records[0].client_ip, "127.0.0.1")

    # 16. Audit log credential & secret redaction
    def test_16_audit_logging_secret_redaction(self):
        sensitive_metadata = {
            "token": "secret-session-token-123",
            "password": "mypassword",
            "shared_secret": "32bytehexsecret",
            "signature": "hmacsignaturestring",
            "safe_metric": 42,
            "nested": {
                "apiKey": "api-key-value",
                "normal": "visible",
            },
        }
        cleaned = redact_sensitive_data(sensitive_metadata)
        self.assertEqual(cleaned["token"], "[REDACTED]")
        self.assertEqual(cleaned["password"], "[REDACTED]")
        self.assertEqual(cleaned["shared_secret"], "[REDACTED]")
        self.assertEqual(cleaned["signature"], "[REDACTED]")
        self.assertEqual(cleaned["safe_metric"], 42)
        self.assertEqual(cleaned["nested"]["apiKey"], "[REDACTED]")
        self.assertEqual(cleaned["nested"]["normal"], "visible")

    # 17. Localhost boundary enforcement
    def test_17_localhost_boundary_enforcement(self):
        # Valid loopback hosts should not raise
        for valid_host in ALLOWED_HOSTS:
            SecureDashboardServer._validate_bind_address(valid_host)

    # 18. Public bind prevention (0.0.0.0 rejection)
    def test_18_public_bind_prevention(self):
        for prohibited in PROHIBITED_HOSTS:
            with self.assertRaises(SecurityBindingError):
                SecureDashboardServer._validate_bind_address(prohibited)

        # Arbitrary external IP
        with self.assertRaises(SecurityBindingError):
            SecureDashboardServer._validate_bind_address("192.168.1.100")

    # 19. Model isolation gate (LLMs blocked from direct tools/sockets/shell)
    def test_19_model_isolation_direct_access_blocked(self):
        # Proposal attempting shell execution
        shell_proposal = {"action": "shell", "params": {"cmd": "dir"}}
        ok, code, _ = ModelIsolationGate.sanitize_model_proposal(shell_proposal)
        self.assertFalse(ok)
        self.assertEqual(code, "PROHIBITED_ACTION_PROPOSED")

        # Proposal attempting raw network socket
        net_proposal = {"action": "socket.bind", "params": {"port": 9000}}
        ok, code, _ = ModelIsolationGate.sanitize_model_proposal(net_proposal)
        self.assertFalse(ok)
        self.assertEqual(code, "PROHIBITED_NETWORK_PROPOSAL")

        # Benign advisory proposal
        good_proposal = {"action": "status.read", "notes": "Check status"}
        ok, code, safe = ModelIsolationGate.sanitize_model_proposal(good_proposal)
        self.assertTrue(ok)
        self.assertEqual(safe["action"], "status.read")

    # 20. Transport integrity & ciphertext tampering detection
    def test_20_transport_integrity_tampering_detection(self):
        key = b"\x01" * 32
        transport = SecureTransport(mode=TransportSecurityMode.ENCRYPTED_SESSION)
        self.assertTrue(transport.is_encrypted())

        plaintext = b"Secure Phone Command Payload"
        packet = transport.encrypt(plaintext, key, associated_data=b"auth-ad")
        self.assertEqual(packet.mode, "ENCRYPTED_SESSION")

        # Decrypt unmodified packet
        decrypted = transport.decrypt(packet, key, associated_data=b"auth-ad")
        self.assertEqual(decrypted, plaintext)

        # Tamper with ciphertext
        tampered_cipher = bytearray.fromhex(packet.ciphertext_hex)
        tampered_cipher[0] ^= 0xFF
        tampered_packet = EncryptedPacket(
            mode=packet.mode,
            iv_hex=packet.iv_hex,
            ciphertext_hex=tampered_cipher.hex(),
            tag_hex=packet.tag_hex,
            associated_data=packet.associated_data,
        )

        with self.assertRaises(TransportIntegrityError):
            transport.decrypt(tampered_packet, key, associated_data=b"auth-ad")

    # 21. Session cleanup for expired tokens
    def test_21_session_cleanup_expired_tokens(self):
        code = self.pairing_mgr.generate_pairing_code()
        self.pairing_mgr.confirm_pairing("dev-cleanup", code)
        
        # Create session with 60s TTL
        ok, _, s1 = self.session_mgr.create_session("dev-cleanup", ttl_seconds=60)
        self.assertTrue(ok)
        self.assertEqual(self.session_mgr.get_active_session_count(), 1)

        # Manually backdate expiration to simulate expiry
        s1.expires_at = time.time() - 5
        removed = self.session_mgr.cleanup_expired()
        self.assertEqual(removed, 1)
        self.assertEqual(self.session_mgr.get_active_session_count(), 0)

    # 22. Concurrent session bounds (per-device & global)
    def test_22_concurrent_session_bounds(self):
        code = self.pairing_mgr.generate_pairing_code()
        self.pairing_mgr.confirm_pairing("dev-concur", code)

        # Max per device is 2: create 1st and 2nd
        ok1, _, s1 = self.session_mgr.create_session("dev-concur")
        ok2, _, s2 = self.session_mgr.create_session("dev-concur")
        self.assertTrue(ok1 and ok2)

        # 3rd session creation for same device evicts oldest (s1)
        ok3, _, s3 = self.session_mgr.create_session("dev-concur")
        self.assertTrue(ok3)

        # s1 should now be evicted
        v_ok, v_msg, _ = self.session_mgr.validate_session(s1.session_id, "dev-concur")
        self.assertFalse(v_ok)
        self.assertEqual(v_msg, "SESSION_NOT_FOUND")

        # s2 and s3 should remain
        v2_ok, _, _ = self.session_mgr.validate_session(s2.session_id, "dev-concur")
        v3_ok, _, _ = self.session_mgr.validate_session(s3.session_id, "dev-concur")
        self.assertTrue(v2_ok and v3_ok)

    # 23. Credential rotation and immediate revocation propagation
    def test_23_credential_rotation_and_revocation(self):
        code = self.pairing_mgr.generate_pairing_code()
        _, _, secret1 = self.pairing_mgr.confirm_pairing("dev-rotate", code)
        self.assertEqual(self.pairing_mgr.get_device_secret("dev-rotate"), secret1)

        # Rotate secret
        secret2 = self.pairing_mgr.rotate_device_secret("dev-rotate")
        self.assertNotEqual(secret1, secret2)
        self.assertEqual(self.pairing_mgr.get_device_secret("dev-rotate"), secret2)

        # Revoke device and verify all sessions immediately terminated
        _, _, s = self.session_mgr.create_session("dev-rotate")
        self.assertTrue(self.session_mgr.validate_session(s.session_id, "dev-rotate")[0])

        self.pairing_mgr.revoke_device("dev-rotate")
        # Validation must now fail with DEVICE_REVOKED
        v_ok, v_msg, _ = self.session_mgr.validate_session(s.session_id, "dev-rotate")
        self.assertFalse(v_ok)
        self.assertEqual(v_msg, "DEVICE_REVOKED")

    # 24. Existing Android /api/command backward compatibility
    def test_24_existing_android_api_command_compatibility(self):
        # Start test server on localhost on a free port
        server = SecureDashboardServer(gateway=self.gateway, host="127.0.0.1", port=0)
        
        # Test direct socket port bind to obtain free port
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        free_port = sock.getsockname()[1]
        sock.close()

        server.port = free_port
        started = server.start()
        self.assertTrue(started)

        try:
            # 1. Test GET /api/status (Android Companion health check)
            status_url = f"http://127.0.0.1:{free_port}/api/status"
            with urllib.request.urlopen(status_url, timeout=3) as resp:
                self.assertEqual(resp.status, 200)
                body = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(body["assistant_status"], "🟢 Online")

            # 2. Test POST /api/command (Android Companion sendCommand)
            cmd_url = f"http://127.0.0.1:{free_port}/api/command"
            cmd_payload = json.dumps({"command": "What time is it?"}).encode("utf-8")
            req = urllib.request.Request(
                cmd_url,
                data=cmd_payload,
                headers={"Content-Type": "application/json; charset=utf-8"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                self.assertEqual(resp.status, 200)
                body = json.loads(resp.read().decode("utf-8"))
                self.assertIn("Companion executed: What time is it?", body["text"])

            # 3. Test emergency stop blocks legacy /api/command
            self.emergency_stop.trigger(triggered_by="TEST", reason="Emergency test")
            req_blocked = urllib.request.Request(
                cmd_url,
                data=cmd_payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req_blocked, timeout=3)
            self.assertEqual(ctx.exception.code, 403)
            err_body = json.loads(ctx.exception.read().decode("utf-8"))
            self.assertEqual(err_body["status"], "EMERGENCY_STOPPED")

        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
