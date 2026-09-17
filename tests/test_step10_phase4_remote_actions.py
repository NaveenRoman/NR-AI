"""
Step 10 Phase 4 — Scoped Remote Actions & Computer Control Safety Test Suite.
Verifies remote action schema, multi-layer safety gating, allowlist enforcement,
prohibited command scanning, model isolation, target TTL, confirmation lifecycle,
Emergency Stop integration, rate limiting, audit logging, and backward compatibility.
Total Tests: 40.
"""

from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.remote.audit import SecurityAuditLogger
from app.remote.auth import SessionManager
from app.remote.config import (
    MAX_REMOTE_ACTION_PAYLOAD_BYTES,
    MAX_REMOTE_ACTION_TIMEOUT_SECONDS,
    REMOTE_ACTION_EXPIRED,
    REMOTE_ACTION_MALFORMED,
    REMOTE_ACTION_NOT_ALLOWED,
    REMOTE_ACTION_REPLAYED,
    REMOTE_AUTH_REQUIRED,
    REMOTE_CONCURRENCY_LIMIT,
    REMOTE_CONFIRMATION_EXPIRED,
    REMOTE_CONFIRMATION_INVALID,
    REMOTE_CONFIRMATION_REQUIRED,
    REMOTE_CONFIRMATION_TIMEOUT_SECONDS,
    REMOTE_EXECUTION_FAILED,
    REMOTE_PERMISSION_DENIED,
    REMOTE_RATE_LIMITED,
    REMOTE_SAFETY_REJECTED,
    REMOTE_STOPPED,
    REMOTE_TARGET_INVALID,
    REMOTE_TARGET_STALE,
    REMOTE_TARGET_TTL_SECONDS,
    REMOTE_VERIFICATION_FAILED,
)
from app.remote.emergency import EmergencyStopController
from app.remote.identity import PairingManager, PCIdentity
from app.remote.permissions import (
    PhonePermissionScope,
    authorize_action,
    ModelIsolationGate,
    PROHIBITED_ACTIONS,
)
from app.remote.protocol import SecureRequest
from app.remote.rate_limiter import RemoteActionRateLimiter
from app.remote.remote_actions import (
    ACTION_ALIASES,
    REMOTE_ACTION_ALLOWLIST,
    RemoteActionDefinition,
    RemoteActionRequest,
    RemoteActionResult,
    RemoteActionRiskLevel,
    RemoteActionType,
    normalize_action_type,
    validate_remote_action_request,
)
from app.remote.remote_action_safety import (
    HIGH_RISK_KEYWORDS,
    RemoteActionSafetyGate,
    SafetyEvaluationResult,
)
from app.remote.remote_action_session import (
    RemoteActionSession,
    RemoteActionSessionManager,
    RemoteActionState,
    RemoteActionStateError,
    VALID_TRANSITIONS as VALID_REMOTE_ACTION_TRANSITIONS,
)
from app.remote.server import (
    SecureDashboardServer,
    SecureGateway,
    SecurityBindingError,
)


def _make_sample_request(
    action_type: str = "computer.list_windows",
    params: dict = None,
    session_id: str = "SES-TEST-001",
    device_id: str = "DEV-TEST-001",
    action_id: str = "ACT-001",
    nonce: str = "NONCE-001",
    timestamp: float = None,
    target: str = None,
    token: str = None,
) -> dict:
    """Helper to create a structured remote action request dictionary."""
    d = {
        "action_id": action_id,
        "session_id": session_id,
        "device_id": device_id,
        "action_type": action_type,
        "parameters": params if params is not None else {},
        "request_timestamp": timestamp if timestamp is not None else time.time(),
        "request_nonce": nonce,
    }
    if target is not None:
        d["target"] = target
    if token is not None:
        d["confirmation_token"] = token
    return d


class MockUnifiedComputerAgent:
    """Mock of Step 4E UnifiedComputerAgent for deterministic tests."""
    def __init__(self):
        self.executed_tools = []
        self.executed_workflows = []
        self.tool_registry = MagicMock()
        mock_res = MagicMock()
        mock_res.success = True
        mock_res.verified = True
        mock_res.data = {"status": "ok"}
        mock_res.message = "Tool executed successfully"
        mock_res.error = None
        self.tool_registry.execute_tool.return_value = mock_res

    def execute_workflow(self, user_goal: str, user_confirmed: bool = False):
        self.executed_workflows.append((user_goal, user_confirmed))
        mock_wf = MagicMock()
        mock_wf.success = True
        mock_wf.summary = f"Workflow completed for: {user_goal}"
        mock_wf.error = None
        mock_wf.to_dict.return_value = {"goal": user_goal, "success": True}
        return mock_wf


class TestStep10Phase4RemoteActions(unittest.TestCase):
    """Deterministic Unit & Integration Tests for Step 10 Phase 4."""

    def setUp(self):
        self.emergency_stop = EmergencyStopController()
        self.audit_logger = SecurityAuditLogger()
        self.mock_agent = MockUnifiedComputerAgent()
        self.safety_gate = RemoteActionSafetyGate(emergency_controller=self.emergency_stop)
        self.rate_limiter = RemoteActionRateLimiter(requests_per_minute=30, burst_limit=5)
        self.session_manager = RemoteActionSessionManager(
            computer_agent=self.mock_agent,
            safety_gate=self.safety_gate,
            emergency_controller=self.emergency_stop,
            rate_limiter=self.rate_limiter,
            audit_logger=self.audit_logger,
            confirmation_timeout=30.0,
            ttl_seconds=120.0,
        )
        self.scopes_with_action = {
            PhonePermissionScope.READ_STATUS,
            PhonePermissionScope.READ_DEVICE_INFO,
            PhonePermissionScope.APPROVED_COMPUTER_ACTION,
        }
        self.scopes_without_action = {
            PhonePermissionScope.READ_STATUS,
            PhonePermissionScope.READ_DEVICE_INFO,
        }

    # =========================================================================
    # Group 1: Action Schema & Validation (Tests 01-03, 37)
    # =========================================================================

    def test_01_action_schema_validation(self):
        """01. Valid structured action request passes schema validation and parses correctly."""
        raw = _make_sample_request(
            action_type="computer.open_app",
            params={"app_name": "notepad.exe"},
            target="notepad.exe",
        )
        valid, err, req_obj = validate_remote_action_request(raw)
        self.assertTrue(valid)
        self.assertIsNone(err)
        self.assertIsNotNone(req_obj)
        self.assertEqual(req_obj.action_type, "computer.open_app")
        self.assertEqual(req_obj.parameters.get("app_name"), "notepad.exe")

    def test_02_malformed_action_rejected(self):
        """02. Malformed action requests (missing fields, bad types) are rejected."""
        # Missing action_type
        raw1 = _make_sample_request()
        del raw1["action_type"]
        v1, err1, _ = validate_remote_action_request(raw1)
        self.assertFalse(v1)
        self.assertEqual(err1, REMOTE_ACTION_MALFORMED)

        # Missing session_id
        raw2 = _make_sample_request()
        raw2["session_id"] = ""
        v2, err2, _ = validate_remote_action_request(raw2)
        self.assertFalse(v2)
        self.assertEqual(err2, REMOTE_ACTION_MALFORMED)

        # Unknown field injected
        raw3 = _make_sample_request()
        raw3["unexpected_injected_field"] = "malicious_payload"
        v3, err3, _ = validate_remote_action_request(raw3)
        self.assertFalse(v3)
        self.assertEqual(err3, REMOTE_ACTION_MALFORMED)

    def test_03_unknown_action_rejected(self):
        """03. Unknown actions not in the allowlist are rejected with REMOTE_ACTION_NOT_ALLOWED."""
        raw = _make_sample_request(action_type="computer.delete_system_files")
        valid, err, _ = validate_remote_action_request(raw)
        self.assertFalse(valid)
        self.assertEqual(err, REMOTE_ACTION_NOT_ALLOWED)

    def test_37_request_size_boundary(self):
        """37. Requests exceeding the 64KB payload limit are deterministically rejected."""
        huge_params = {"data": "A" * (MAX_REMOTE_ACTION_PAYLOAD_BYTES + 100)}
        raw = _make_sample_request(params=huge_params)
        valid, err, _ = validate_remote_action_request(raw)
        self.assertFalse(valid)
        self.assertEqual(err, REMOTE_ACTION_MALFORMED)

    # =========================================================================
    # Group 2: Authentication & Authorization Scopes (Tests 04-07, 36)
    # =========================================================================

    def test_04_authentication_required(self):
        """04. Actions without a valid authenticated session are rejected."""
        gateway = SecureGateway()
        # Non-authenticated session ID
        valid, msg, sess = gateway.session_manager.validate_session("INVALID-SES-999", "DEV-001")
        self.assertFalse(valid)
        self.assertIsNone(sess)

    def test_05_permission_scope_enforced(self):
        """05. Device without PhonePermissionScope.APPROVED_COMPUTER_ACTION is denied."""
        raw = _make_sample_request(action_type="computer.list_windows")
        _, _, req_obj = validate_remote_action_request(raw)
        result = self.session_manager.process_action_request(req_obj, scopes=self.scopes_without_action)
        self.assertFalse(result.success)
        self.assertEqual(result.status, "DENIED")
        self.assertEqual(result.error, REMOTE_PERMISSION_DENIED)

    def test_06_session_expiry_enforced(self):
        """06. Stale action requests with drifted timestamps are rejected as expired."""
        stale_ts = time.time() - 120.0  # 120s in the past
        raw = _make_sample_request(timestamp=stale_ts)
        valid, err, _ = validate_remote_action_request(raw)
        self.assertFalse(valid)
        self.assertEqual(err, REMOTE_ACTION_EXPIRED)

    def test_07_replay_protection_enforced(self):
        """07. Replayed request nonces are detected and blocked."""
        raw = _make_sample_request(nonce="REPLAY-NONCE-12345")
        _, _, req1 = validate_remote_action_request(raw)
        res1 = self.session_manager.process_action_request(req1, scopes=self.scopes_with_action)
        self.assertTrue(res1.success)

        # Send same nonce again
        _, _, req2 = validate_remote_action_request(raw)
        res2 = self.session_manager.process_action_request(req2, scopes=self.scopes_with_action)
        self.assertFalse(res2.success)
        self.assertEqual(res2.error, REMOTE_ACTION_REPLAYED)

    def test_36_session_concurrency_global(self):
        """36. Global concurrent remote action limit is enforced."""
        mgr = RemoteActionSessionManager(
            computer_agent=self.mock_agent,
            safety_gate=self.safety_gate,
            emergency_controller=self.emergency_stop,
            rate_limiter=self.rate_limiter,
            audit_logger=self.audit_logger,
            max_global=2,
        )
        # Create 2 active mock sessions
        for i in range(2):
            raw = _make_sample_request(
                session_id=f"SES-G-{i}",
                device_id=f"DEV-G-{i}",
                action_id=f"ACT-G-{i}",
                nonce=f"NONCE-G-{i}",
                action_type="computer.type_text",
                params={"text": "delete all"},  # forces AWAITING_CONFIRMATION so session stays active
            )
            _, _, req = validate_remote_action_request(raw)
            mgr.process_action_request(req, scopes=self.scopes_with_action)

        # Third global session should exceed limit
        raw3 = _make_sample_request(
            session_id="SES-G-3",
            device_id="DEV-G-3",
            action_id="ACT-G-3",
            nonce="NONCE-G-3",
        )
        _, _, req3 = validate_remote_action_request(raw3)
        res3 = mgr.process_action_request(req3, scopes=self.scopes_with_action)
        self.assertFalse(res3.success)
        self.assertEqual(res3.error, REMOTE_CONCURRENCY_LIMIT)

    # =========================================================================
    # Group 3: Rate Limiting & Concurrency (Tests 08-09)
    # =========================================================================

    def test_08_rate_limiting_enforced(self):
        """08. RemoteActionRateLimiter limits rapid action flooding."""
        limiter = RemoteActionRateLimiter(requests_per_minute=30, burst_limit=2)
        # Consume burst of 2
        ok1, _, _ = limiter.allow_request("DEV-RATE-01")
        ok2, _, _ = limiter.allow_request("DEV-RATE-01")
        ok3, reason, _ = limiter.allow_request("DEV-RATE-01")
        self.assertTrue(ok1)
        self.assertTrue(ok2)
        self.assertFalse(ok3)
        self.assertIn("RATE_LIMIT_EXCEEDED", reason)

    def test_09_concurrency_limit_per_device(self):
        """09. Concurrency limit per device (max 1 active action) is enforced."""
        # Start high-risk action 1 which awaits confirmation (leaves session active)
        raw1 = _make_sample_request(
            session_id="SES-CONC-01",
            device_id="DEV-CONC-SAME",
            action_id="ACT-CONC-01",
            nonce="NONCE-CONC-01",
            action_type="computer.type_text",
            params={"text": "delete all"},
        )
        _, _, req1 = validate_remote_action_request(raw1)
        res1 = self.session_manager.process_action_request(req1, scopes=self.scopes_with_action)
        self.assertEqual(res1.status, "AWAITING_CONFIRMATION")

        # Try starting action 2 on same device while action 1 is active
        raw2 = _make_sample_request(
            session_id="SES-CONC-02",
            device_id="DEV-CONC-SAME",
            action_id="ACT-CONC-02",
            nonce="NONCE-CONC-02",
        )
        _, _, req2 = validate_remote_action_request(raw2)
        res2 = self.session_manager.process_action_request(req2, scopes=self.scopes_with_action)
        self.assertFalse(res2.success)
        self.assertEqual(res2.error, REMOTE_CONCURRENCY_LIMIT)

    # =========================================================================
    # Group 4: Allowlist & Prohibited Command Scanning (Tests 10-19)
    # =========================================================================

    def test_10_allowlist_enforcement(self):
        """10. All 13 approved RemoteActionTypes pass allowlist validation."""
        for act_enum in RemoteActionType:
            raw_type = act_enum.value
            self.assertIn(raw_type, REMOTE_ACTION_ALLOWLIST)
            norm = normalize_action_type(raw_type)
            self.assertEqual(norm, raw_type)

    def test_11_prohibited_action_rejected(self):
        """11. Actions in PROHIBITED_ACTIONS are rejected by authorize_action and safety gate."""
        for prob in ["shell", "powershell", "cmd", "exec", "eval", "adb_shell", "remote.click", "remote.type"]:
            res = authorize_action(prob, granted_scopes=self.scopes_with_action)
            self.assertFalse(res.allowed)
            self.assertEqual(res.decision_code, "PROHIBITED_ACTION")

    def test_12_prohibited_shell_blocked(self):
        """12. Shell command invocations (bash, sh) in parameters are blocked."""
        raw = _make_sample_request(
            action_type="computer.type_text",
            params={"text": "bash -c 'rm -rf /'"},
        )
        _, _, req = validate_remote_action_request(raw)
        res = self.session_manager.process_action_request(req, scopes=self.scopes_with_action)
        self.assertFalse(res.success)
        self.assertEqual(res.error, REMOTE_SAFETY_REJECTED)

    def test_13_prohibited_powershell_blocked(self):
        """13. PowerShell execution attempts in parameters are blocked."""
        raw = _make_sample_request(
            action_type="computer.type_text",
            params={"text": "powershell.exe -ExecutionPolicy Bypass -Command Get-Process"},
        )
        _, _, req = validate_remote_action_request(raw)
        res = self.session_manager.process_action_request(req, scopes=self.scopes_with_action)
        self.assertFalse(res.success)
        self.assertEqual(res.error, REMOTE_SAFETY_REJECTED)

    def test_14_prohibited_cmd_blocked(self):
        """14. CMD invocations in parameters are blocked."""
        raw = _make_sample_request(
            action_type="computer.type_text",
            params={"text": "cmd.exe /c dir"},
        )
        _, _, req = validate_remote_action_request(raw)
        res = self.session_manager.process_action_request(req, scopes=self.scopes_with_action)
        self.assertFalse(res.success)
        self.assertEqual(res.error, REMOTE_SAFETY_REJECTED)

    def test_15_prohibited_exec_blocked(self):
        """15. Python exec() primitives in parameters are blocked."""
        raw = _make_sample_request(
            action_type="computer.type_text",
            params={"text": "exec('import os; os.system(1)')"},
        )
        _, _, req = validate_remote_action_request(raw)
        res = self.session_manager.process_action_request(req, scopes=self.scopes_with_action)
        self.assertFalse(res.success)
        self.assertEqual(res.error, REMOTE_SAFETY_REJECTED)

    def test_16_prohibited_eval_blocked(self):
        """16. Python eval() primitives in parameters are blocked."""
        raw = _make_sample_request(
            action_type="computer.type_text",
            params={"text": "eval('__import__(\"os\").getcwd()')"},
        )
        _, _, req = validate_remote_action_request(raw)
        res = self.session_manager.process_action_request(req, scopes=self.scopes_with_action)
        self.assertFalse(res.success)
        self.assertEqual(res.error, REMOTE_SAFETY_REJECTED)

    def test_17_prohibited_adb_shell_blocked(self):
        """17. ADB shell invocations in parameters are blocked."""
        raw = _make_sample_request(
            action_type="computer.type_text",
            params={"text": "adb shell input tap 100 100"},
        )
        _, _, req = validate_remote_action_request(raw)
        res = self.session_manager.process_action_request(req, scopes=self.scopes_with_action)
        self.assertFalse(res.success)
        self.assertEqual(res.error, REMOTE_SAFETY_REJECTED)

    def test_18_filesystem_restriction_blocked(self):
        """18. Destructive filesystem operations (rmdir /s, format, del /f) are blocked."""
        for cmd in ["rmdir /s /q C:\\Windows", "format C:", "del /f /q *.*"]:
            raw = _make_sample_request(
                action_type="computer.type_text",
                params={"text": cmd},
                nonce=f"NONCE-FS-{hash(cmd)}",
            )
            _, _, req = validate_remote_action_request(raw)
            res = self.session_manager.process_action_request(req, scopes=self.scopes_with_action)
            self.assertFalse(res.success)
            self.assertEqual(res.error, REMOTE_SAFETY_REJECTED)

    def test_19_registry_restriction_blocked(self):
        """19. Windows registry manipulation commands (reg add, regedit) are blocked."""
        for reg_cmd in ["reg add HKLM\\Software\\Test", "regedit /s patch.reg"]:
            raw = _make_sample_request(
                action_type="computer.type_text",
                params={"text": reg_cmd},
                nonce=f"NONCE-REG-{hash(reg_cmd)}",
            )
            _, _, req = validate_remote_action_request(raw)
            res = self.session_manager.process_action_request(req, scopes=self.scopes_with_action)
            self.assertFalse(res.success)
            self.assertEqual(res.error, REMOTE_SAFETY_REJECTED)

    # =========================================================================
    # Group 5: Model Isolation & Target Freshness (Tests 20-23)
    # =========================================================================

    def test_20_model_isolation_enforced(self):
        """20. Model advisory proposals cannot propose shell commands or raw executions."""
        # Malicious shell proposal from model
        bad_prop = {"action": "powershell", "params": {"cmd": "whoami"}}
        ok1, reason1, _ = RemoteActionSafetyGate.is_model_proposal_safe(bad_prop)
        self.assertFalse(ok1)
        self.assertIn("Prohibited", reason1)

        # Valid advisory proposal
        good_prop = {"action": "computer.open_app", "params": {"app_name": "notepad"}}
        ok2, _, sanitized = RemoteActionSafetyGate.is_model_proposal_safe(good_prop)
        self.assertTrue(ok2)
        self.assertEqual(sanitized["action"], "computer.open_app")

    def test_21_target_ttl_freshness(self):
        """21. Target within the 15-second TTL passes freshness validation."""
        now = time.time()
        fresh_target = {"timestamp": now - 5.0, "center": (100, 200)}
        raw = _make_sample_request(
            action_type="computer.click_target",
            params={"target_name": "Submit Button"},
        )
        _, _, req = validate_remote_action_request(raw)
        eval_res = self.safety_gate.evaluate_request(req, cached_target=fresh_target, current_time=now)
        self.assertTrue(eval_res.passed)

    def test_22_stale_target_rejected(self):
        """22. Target older than 15-second TTL is rejected with REMOTE_TARGET_STALE."""
        now = time.time()
        stale_target = {"timestamp": now - 20.0, "center": (100, 200)}
        raw = _make_sample_request(
            action_type="computer.click_target",
            params={"target_name": "Submit Button"},
        )
        _, _, req = validate_remote_action_request(raw)
        eval_res = self.safety_gate.evaluate_request(req, cached_target=stale_target, current_time=now)
        self.assertFalse(eval_res.passed)
        self.assertEqual(eval_res.decision_code, REMOTE_TARGET_STALE)

    def test_23_invalid_target_rejected(self):
        """23. Click action missing both target_name and coordinates is rejected."""
        raw = _make_sample_request(
            action_type="computer.click_target",
            params={},  # neither target_name nor coordinates
        )
        valid, err, _ = validate_remote_action_request(raw)
        self.assertFalse(valid)
        self.assertEqual(err, REMOTE_ACTION_MALFORMED)

    # =========================================================================
    # Group 6: Confirmation Lifecycle (Tests 24-27)
    # =========================================================================

    def test_24_confirmation_required_for_high_risk(self):
        """24. High-risk keywords deterministically trigger AWAITING_CONFIRMATION state."""
        raw = _make_sample_request(
            action_type="computer.type_text",
            params={"text": "Please delete all project records"},
        )
        _, _, req = validate_remote_action_request(raw)
        res = self.session_manager.process_action_request(req, scopes=self.scopes_with_action)
        self.assertTrue(res.success)
        self.assertTrue(res.requires_confirmation)
        self.assertEqual(res.status, "AWAITING_CONFIRMATION")
        self.assertIsNotNone(res.confirmation_token)
        self.assertTrue(res.confirmation_token.startswith("CONF-"))

    def test_25_valid_confirmation_executes(self):
        """25. Presenting valid confirmation token executes the pending action."""
        raw = _make_sample_request(
            session_id="SES-CONF-OK",
            action_id="ACT-CONF-OK",
            device_id="DEV-CONF-OK",
            nonce="NONCE-CONF-OK",
            action_type="computer.type_text",
            params={"text": "format drive d:"},
        )
        # Note: 'format drive' keyword triggers confirmation requirement
        raw["parameters"] = {"text": "delete all temporary logs"}
        _, _, req = validate_remote_action_request(raw)
        init_res = self.session_manager.process_action_request(req, scopes=self.scopes_with_action)
        self.assertEqual(init_res.status, "AWAITING_CONFIRMATION")
        token = init_res.confirmation_token

        # Confirm with correct token
        ok, msg, final_res = self.session_manager.confirm_action(
            session_id="SES-CONF-OK",
            action_id="ACT-CONF-OK",
            device_id="DEV-CONF-OK",
            confirmation_token=token,
            confirmed=True,
        )
        self.assertTrue(ok)
        self.assertIsNotNone(final_res)
        self.assertEqual(final_res.status, "SUCCESS")
        self.assertTrue(final_res.success)

    def test_26_expired_confirmation_rejected(self):
        """26. Confirmation presented after the 30-second window is rejected as expired."""
        now = time.time()
        raw = _make_sample_request(
            session_id="SES-CONF-EXP",
            action_id="ACT-CONF-EXP",
            device_id="DEV-CONF-EXP",
            nonce="NONCE-CONF-EXP",
            action_type="computer.type_text",
            params={"text": "delete all temporary cache"},
        )
        _, _, req = validate_remote_action_request(raw)
        init_res = self.session_manager.process_action_request(req, scopes=self.scopes_with_action, current_time=now)
        token = init_res.confirmation_token

        # Confirm at now + 35.0s (past 30s timeout)
        ok, msg, res = self.session_manager.confirm_action(
            session_id="SES-CONF-EXP",
            action_id="ACT-CONF-EXP",
            device_id="DEV-CONF-EXP",
            confirmation_token=token,
            confirmed=True,
            current_time=now + 35.0,
        )
        self.assertFalse(ok)
        self.assertEqual(msg, REMOTE_CONFIRMATION_EXPIRED)
        self.assertEqual(res.status, "FAILED")

    def test_27_wrong_session_confirmation_rejected(self):
        """27. Invalid confirmation token or mismatched session ID is rejected."""
        raw = _make_sample_request(
            session_id="SES-CONF-MIS",
            action_id="ACT-CONF-MIS",
            device_id="DEV-CONF-MIS",
            nonce="NONCE-CONF-MIS",
            action_type="computer.type_text",
            params={"text": "delete all items"},
        )
        _, _, req = validate_remote_action_request(raw)
        init_res = self.session_manager.process_action_request(req, scopes=self.scopes_with_action)

        # Confirm with WRONG token
        ok, msg, res = self.session_manager.confirm_action(
            session_id="SES-CONF-MIS",
            action_id="ACT-CONF-MIS",
            device_id="DEV-CONF-MIS",
            confirmation_token="CONF-WRONG-TOKEN",
            confirmed=True,
        )
        self.assertFalse(ok)
        self.assertEqual(msg, REMOTE_CONFIRMATION_INVALID)
        self.assertEqual(res.status, "DENIED")

    # =========================================================================
    # Group 7: Emergency Stop Integration (Tests 28-30)
    # =========================================================================

    def test_28_emergency_stop_before_execution(self):
        """28. Active Emergency Stop immediately blocks new remote actions with REMOTE_STOPPED."""
        self.emergency_stop.trigger(triggered_by="TEST_ADMIN", reason="Safety test")
        raw = _make_sample_request(action_type="computer.list_windows")
        _, _, req = validate_remote_action_request(raw)
        res = self.session_manager.process_action_request(req, scopes=self.scopes_with_action)
        self.assertFalse(res.success)
        self.assertEqual(res.status, "STOPPED")
        self.assertEqual(res.error, REMOTE_STOPPED)

    def test_29_emergency_stop_during_workflow(self):
        """29. Emergency Stop triggered mid-session halts and marks session STOPPED."""
        # Start high-risk session awaiting confirmation
        raw = _make_sample_request(
            session_id="SES-ESTOP-01",
            action_type="computer.type_text",
            params={"text": "delete all scratch files"},
        )
        _, _, req = validate_remote_action_request(raw)
        self.session_manager.process_action_request(req, scopes=self.scopes_with_action)

        # Fire E-stop
        self.emergency_stop.trigger(triggered_by="TEST_OPERATOR", reason="Immediate halt")

        sess = self.session_manager.get_session("SES-ESTOP-01")
        self.assertIsNotNone(sess)
        self.assertEqual(sess.state, RemoteActionState.STOPPED)

    def test_30_queued_action_cancellation(self):
        """30. Cancellation callback cleans up active sessions upon Emergency Stop."""
        raw = _make_sample_request(
            session_id="SES-QUEUE-01",
            action_type="computer.type_text",
            params={"text": "wipe disk free space"},
        )
        _, _, req = validate_remote_action_request(raw)
        self.session_manager.process_action_request(req, scopes=self.scopes_with_action)

        status = self.emergency_stop.trigger(triggered_by="UNIT_TEST", reason="Abort test")
        self.assertTrue(status.is_active)
        self.assertGreater(status.callbacks_executed, 0)

        sess = self.session_manager.get_session("SES-QUEUE-01")
        self.assertEqual(sess.state, RemoteActionState.STOPPED)

    # =========================================================================
    # Group 8: Audit Logging & Secret Redaction (Tests 31-32)
    # =========================================================================

    def test_31_audit_logging_recorded(self):
        """31. Completed remote actions generate structured audit records."""
        raw = _make_sample_request(
            session_id="SES-AUDIT-01",
            action_type="computer.list_windows",
        )
        _, _, req = validate_remote_action_request(raw)
        self.session_manager.process_action_request(req, scopes=self.scopes_with_action)

        records = self.audit_logger.get_records()
        self.assertTrue(any(r.event_type == "REMOTE_ACTION_COMPLETED" for r in records))

    def test_32_secret_redaction_in_audit(self):
        """32. Passwords and credentials are automatically redacted in audit logs."""
        raw = _make_sample_request(
            action_type="computer.type_text",
            params={"text": "my_secret_password_123", "is_sensitive": True},
        )
        _, _, req = validate_remote_action_request(raw)
        self.assertIsNotNone(req)
        self.session_manager.process_action_request(req, scopes=self.scopes_with_action)

        records = self.audit_logger.get_records()
        self.assertTrue(len(records) > 0)
        for r in records:
            meta_str = str(r.safe_metadata)
            self.assertNotIn("my_secret_password_123", meta_str)

        # Also verify request.to_safe_dict() redacts sensitive content
        safe_d = req.to_safe_dict()
        self.assertEqual(safe_d["parameters"]["text"], "[REDACTED]")

    # =========================================================================
    # Group 9: Execution & Verification (Tests 33-35, 38)
    # =========================================================================

    def test_33_successful_safe_action(self):
        """33. Safe actions execute and return SUCCESS with verified=True."""
        raw = _make_sample_request(
            action_type="computer.focus_window",
            params={"target": "Notepad"},
            target="Notepad",
        )
        _, _, req = validate_remote_action_request(raw)
        res = self.session_manager.process_action_request(req, scopes=self.scopes_with_action)
        self.assertTrue(res.success)
        self.assertEqual(res.status, "SUCCESS")
        self.assertTrue(res.verified)

    def test_34_failed_action_handled(self):
        """34. Tool failure is handled cleanly without exceptions, returning FAILED status."""
        # Configure tool registry to fail
        fail_res = MagicMock()
        fail_res.success = False
        fail_res.verified = False
        fail_res.error = "WINDOW_NOT_FOUND"
        fail_res.message = "Could not find window Notepad"
        self.mock_agent.tool_registry.execute_tool.return_value = fail_res

        raw = _make_sample_request(
            action_type="computer.focus_window",
            params={"target": "Notepad"},
        )
        _, _, req = validate_remote_action_request(raw)
        res = self.session_manager.process_action_request(req, scopes=self.scopes_with_action)
        self.assertFalse(res.success)
        self.assertEqual(res.status, "FAILED")
        self.assertEqual(res.error, "WINDOW_NOT_FOUND")

    def test_35_verification_failure_handled(self):
        """35. Tool state verification failure results in verified=False."""
        unverified_res = MagicMock()
        unverified_res.success = True
        unverified_res.verified = False
        unverified_res.message = "Executed but verification timed out"
        unverified_res.error = None
        self.mock_agent.tool_registry.execute_tool.return_value = unverified_res

        raw = _make_sample_request(
            action_type="computer.verify",
            params={"expected_text": "Loaded Successfully"},
        )
        _, _, req = validate_remote_action_request(raw)
        res = self.session_manager.process_action_request(req, scopes=self.scopes_with_action)
        self.assertTrue(res.success)
        self.assertFalse(res.verified)

    def test_38_action_timeout_boundary(self):
        """38. Action execution duration is tracked and stays bounded."""
        raw = _make_sample_request(action_type="computer.list_windows")
        _, _, req = validate_remote_action_request(raw)
        res = self.session_manager.process_action_request(req, scopes=self.scopes_with_action)
        self.assertLess(res.duration_s, MAX_REMOTE_ACTION_TIMEOUT_SECONDS)

    # =========================================================================
    # Group 10: Localhost Boundary & Backward Compatibility (Tests 39-40)
    # =========================================================================

    def test_39_localhost_only_boundary(self):
        """39. Secure server enforces localhost binding; 0.0.0.0 raises SecurityBindingError."""
        gateway = SecureGateway()
        with self.assertRaises(SecurityBindingError):
            SecureDashboardServer(gateway=gateway, host="0.0.0.0", port=8585)
        with self.assertRaises(SecurityBindingError):
            SecureDashboardServer(gateway=gateway, host="192.168.1.100", port=8585)

    def test_40_backward_compatibility_step10(self):
        """40. Backward compatibility: Phases 1-3 voice, telemetry, stream endpoints remain operational."""
        gateway = SecureGateway()
        # Phase 1: Emergency stop
        self.assertFalse(gateway.emergency_stop.is_active())
        # Phase 2: Stream manager initialized
        self.assertIsNotNone(gateway.stream_manager)
        # Phase 3: Voice session manager initialized
        self.assertIsNotNone(gateway.voice_session_manager)
        # Phase 4: Remote action session manager initialized
        self.assertIsNotNone(gateway.remote_action_session_manager)

        # Verify action mappings in ACTION_SCOPE_MAP
        from app.remote.permissions import ACTION_SCOPE_MAP
        self.assertIn("action.remote_execute", ACTION_SCOPE_MAP)
        self.assertIn("voice.audio_upload", ACTION_SCOPE_MAP)
        self.assertIn("stream.start", ACTION_SCOPE_MAP)
        self.assertIn("telemetry.read", ACTION_SCOPE_MAP)


if __name__ == "__main__":
    unittest.main()
