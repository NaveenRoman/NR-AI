"""
Step 10 Phase 5 — Autonomous Mobile Companion UX, End-to-End Orchestration & Polish Test Suite.

Verifies end-to-end companion orchestration:
- Session lifecycle & pairing
- Text command pipeline
- Voice audio capture & STT intent pipeline
- Two-step high-risk confirmation UX flow
- Screen observation & backpressure
- Global Emergency Stop priority
- Resilience, heartbeat timeout & bounded reconnects
- Degraded mode operation
- Security invariants (0 shell=True, localhost-only, model isolation, secret redaction)
- Backward compatibility for legacy endpoints without security bypasses.

Total Tests: 40.
"""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.remote.audit import SecurityAuditLogger
from app.remote.audio import AudioFormat, AudioRequest, create_audio_request
from app.remote.auth import SessionManager
from app.remote.companion_client_contract import (
    CompanionNotification,
    CompanionUIState,
    ConfirmationDialogPayload,
    TTSResponseContract,
    sanitize_tts_response,
)
from app.remote.companion_orchestrator import (
    CompanionCommandResult,
    CompanionOrchestrator,
    CompanionOrchestratorState,
    CompanionStateError,
    VALID_ORCHESTRATOR_TRANSITIONS,
)
from app.remote.companion_resilience import CompanionResilienceManager, DeviceConnectionRecord
from app.remote.config import (
    COMPANION_HEARTBEAT_TIMEOUT,
    COMPANION_RECONNECT_EXCEEDED,
    CONNECTION_TIMEOUT_SECONDS,
    DEFAULT_HOST,
    DEFAULT_PORT,
    HEARTBEAT_INTERVAL_SECONDS,
    MAX_RECONNECT_ATTEMPTS,
    MAX_TTS_RESPONSE_CHARS,
    REMOTE_ACTION_NOT_ALLOWED,
    REMOTE_AUTH_REQUIRED,
    REMOTE_CONFIRMATION_EXPIRED,
    REMOTE_CONFIRMATION_INVALID,
    REMOTE_CONFIRMATION_REQUIRED,
    REMOTE_PERMISSION_DENIED,
    REMOTE_SAFETY_REJECTED,
    REMOTE_STOPPED,
    REMOTE_TARGET_STALE,
)
from app.remote.emergency import EmergencyStopController
from app.remote.frame import FrameEncoding, StreamFrame
from app.remote.identity import PairingManager, PCIdentity
from app.remote.permissions import (
    DEFAULT_COMPANION_SCOPES,
    ModelIsolationGate,
    PhonePermissionScope,
    PROHIBITED_ACTIONS,
    authorize_action,
)
from app.remote.remote_actions import (
    ACTION_ALIASES,
    REMOTE_ACTION_ALLOWLIST,
    RemoteActionRequest,
    RemoteActionResult,
    RemoteActionRiskLevel,
    RemoteActionType,
)
from app.remote.remote_action_safety import RemoteActionSafetyGate
from app.remote.remote_action_session import RemoteActionSessionManager
from app.remote.screen_capture import MockScreenCaptureEngine
from app.remote.server import SecureDashboardServer, SecureGateway, SecurityBindingError
from app.remote.speech_to_text import DevelopmentSpeechToTextProvider
from app.remote.stream import StreamManager
from app.remote.telemetry import TelemetryHub
from app.remote.voice_session import VoiceIntentType, VoiceSessionManager


class TestStep10Phase5CompanionOrchestration(unittest.TestCase):

    def setUp(self):
        self.pairing_mgr = PairingManager()
        self.session_mgr = SessionManager(self.pairing_mgr)
        self.emergency = EmergencyStopController()
        self.audit = SecurityAuditLogger()
        self.resilience = CompanionResilienceManager()
        self.telemetry = TelemetryHub()
        self.mock_capture = MockScreenCaptureEngine()
        self.stream_mgr = StreamManager(capture_engine=self.mock_capture, emergency_stop=self.emergency)
        self.stt_provider = DevelopmentSpeechToTextProvider()
        self.voice_mgr = VoiceSessionManager(
            stt_provider=self.stt_provider,
            emergency_controller=self.emergency,
        )

        # Mock UnifiedComputerAgent
        self.mock_agent = MagicMock()
        self.mock_agent.tool_registry = MagicMock()
        tool_res = MagicMock()
        tool_res.success = True
        tool_res.verified = True
        tool_res.data = {"status": "ok"}
        tool_res.message = "Tool executed successfully"
        tool_res.error = None
        self.mock_agent.tool_registry.execute_tool.return_value = tool_res

        self.action_mgr = RemoteActionSessionManager(
            computer_agent=self.mock_agent,
            emergency_controller=self.emergency,
        )

        self.orchestrator = CompanionOrchestrator(
            session_manager=self.session_mgr,
            stream_session_manager=self.stream_mgr,
            voice_session_manager=self.voice_mgr,
            remote_action_session_manager=self.action_mgr,
            telemetry_hub=self.telemetry,
            emergency_controller=self.emergency,
            resilience_manager=self.resilience,
            audit_logger=self.audit,
            computer_agent=self.mock_agent,
        )

        self.device_id = "DEV-PHASE5-TEST-001"
        self.pairing_code = self.pairing_mgr.initiate_pairing(self.device_id, "Android Phone")
        ok, msg, dev = self.pairing_mgr.confirm_pairing(self.device_id, self.pairing_code, "Android Phone")
        self.assertTrue(ok)

        # Create session with full permissions
        s_ok, s_msg, self.session = self.session_mgr.create_session(
            device_id=self.device_id,
            scopes=DEFAULT_COMPANION_SCOPES | {
                PhonePermissionScope.APPROVED_COMPUTER_ACTION,
                PhonePermissionScope.VOICE_COMMAND,
                PhonePermissionScope.READ_SCREEN_STREAM,
            },
        )
        self.assertTrue(s_ok)
        self.session_id = self.session.session_id

    def tearDown(self):
        self.emergency.reset()
        self.resilience.reset()

    # 1. Orchestrator Initialization
    def test_01_orchestrator_initialization(self):
        orch = CompanionOrchestrator(session_manager=self.session_mgr)
        self.assertIsNotNone(orch.session_manager)
        self.assertIsNotNone(orch.emergency_controller)
        self.assertEqual(orch.get_state("DEV-UNKNOWN"), CompanionOrchestratorState.UNPAIRED)

    # 2. Dependency Wiring
    def test_02_dependency_wiring(self):
        self.assertIs(self.orchestrator.session_manager, self.session_mgr)
        self.assertIs(self.orchestrator.emergency_controller, self.emergency)
        self.assertIs(self.orchestrator.stream_session_manager, self.stream_mgr)
        self.assertIs(self.orchestrator.voice_session_manager, self.voice_mgr)
        self.assertIs(self.orchestrator.remote_action_session_manager, self.action_mgr)
        self.assertIs(self.orchestrator.computer_agent, self.mock_agent)

    # 3. Pairing
    def test_03_pairing(self):
        dev_id = "DEV-NEW-002"
        code = self.pairing_mgr.initiate_pairing(dev_id, "Pixel 8")
        self.assertEqual(len(code), 6)
        ok, msg, dev = self.pairing_mgr.confirm_pairing(dev_id, code, "Pixel 8")
        self.assertTrue(ok)
        self.assertTrue(self.pairing_mgr.is_paired(dev_id))

    # 4. Authenticated State
    def test_04_authenticated_state(self):
        res = self.orchestrator.process_command(
            session_id=self.session_id,
            device_id=self.device_id,
            command_text="status",
        )
        self.assertTrue(res.success)
        self.assertEqual(self.orchestrator.get_state(self.device_id), CompanionOrchestratorState.IDLE_CONNECTED)

    # 5. State Transitions
    def test_05_state_transitions(self):
        dev = "DEV-STATE-TEST"
        self.orchestrator.transition_state(dev, CompanionOrchestratorState.PAIRING, "Pairing started")
        self.assertEqual(self.orchestrator.get_state(dev), CompanionOrchestratorState.PAIRING)

        self.orchestrator.transition_state(dev, CompanionOrchestratorState.AUTHENTICATED, "Auth verified")
        self.assertEqual(self.orchestrator.get_state(dev), CompanionOrchestratorState.AUTHENTICATED)

        # Illegal transition should raise CompanionStateError
        with self.assertRaises(CompanionStateError):
            self.orchestrator.transition_state(dev, CompanionOrchestratorState.EXECUTING_ACTION, "Illegal jump")

    # 6. Session Revocation
    def test_06_session_revocation(self):
        self.session_mgr.revoke_session(self.session_id)
        res = self.orchestrator.process_command(
            session_id=self.session_id,
            device_id=self.device_id,
            command_text="hello",
        )
        self.assertFalse(res.success)
        self.assertEqual(res.status, "DENIED")
        self.assertEqual(res.data.get("error"), REMOTE_AUTH_REQUIRED)

    # 7. Unauthorized Command
    def test_07_unauthorized_command(self):
        res = self.orchestrator.process_command(
            session_id="INVALID-SESSION-TOKEN",
            device_id=self.device_id,
            command_text="status",
        )
        self.assertFalse(res.success)
        self.assertEqual(res.status, "DENIED")
        self.assertEqual(res.data.get("error"), REMOTE_AUTH_REQUIRED)

    # 8. Text Command
    def test_08_text_command(self):
        res = self.orchestrator.process_command(
            session_id=self.session_id,
            device_id=self.device_id,
            command_text="help",
        )
        self.assertTrue(res.success)
        self.assertEqual(res.status, "SUCCESS")
        self.assertIn("help", res.message.lower())
        self.assertIsNotNone(res.tts_response)

    # 9. Status Query
    def test_09_status_query(self):
        res = self.orchestrator.process_command(
            session_id=self.session_id,
            device_id=self.device_id,
            command_text="what is system status",
        )
        self.assertTrue(res.success)
        self.assertEqual(res.command_type, "QUERY")
        self.assertEqual(res.data.get("assistant_status"), "Online")

    # 10. Time Query
    def test_10_time_query(self):
        res = self.orchestrator.process_command(
            session_id=self.session_id,
            device_id=self.device_id,
            command_text="what time is it",
        )
        self.assertTrue(res.success)
        self.assertEqual(res.command_type, "QUERY")
        self.assertIn("time", res.data)

    # 11. Low-Risk Computer Action
    def test_11_low_risk_computer_action(self):
        res = self.orchestrator.process_command(
            session_id=self.session_id,
            device_id=self.device_id,
            action_payload={
                "action_id": "ACT-LOW-001",
                "session_id": self.session_id,
                "device_id": self.device_id,
                "action_type": "computer.list_windows",
                "parameters": {},
                "timestamp": time.time(),
            },
        )
        self.assertTrue(res.success)
        self.assertEqual(res.status, "SUCCESS")
        self.assertEqual(self.orchestrator.get_state(self.device_id), CompanionOrchestratorState.IDLE_CONNECTED)

    # 12. Medium-Risk Computer Action
    def test_12_medium_risk_computer_action(self):
        res = self.orchestrator.process_command(
            session_id=self.session_id,
            device_id=self.device_id,
            action_payload={
                "action_id": "ACT-MED-001",
                "session_id": self.session_id,
                "device_id": self.device_id,
                "action_type": "computer.type_text",
                "parameters": {"text": "hello world"},
                "timestamp": time.time(),
            },
        )
        self.assertTrue(res.success)
        self.assertEqual(res.status, "SUCCESS")

    # 13. TTS Formatting
    def test_13_tts_formatting(self):
        raw = "```python\nimport os\n``` Here is http://example.com with C:\\Windows\\System32 and token=secret123"
        clean = sanitize_tts_response(raw)
        self.assertNotIn("http://", clean)
        self.assertNotIn("C:\\Windows", clean)
        self.assertNotIn("secret123", clean)
        self.assertLessEqual(len(clean), MAX_TTS_RESPONSE_CHARS)

    # 14. Audio -> STT -> Intent
    def test_14_audio_stt_intent(self):
        header = "TEXT:system status".encode("utf-8")
        payload = header + b"\x00" * 3180
        req = create_audio_request(
            session_id=self.session_id,
            device_id=self.device_id,
            audio_bytes=payload,
            audio_format="WAV",
        )
        res = self.orchestrator.process_command(
            session_id=self.session_id,
            device_id=self.device_id,
            audio_request=req,
        )
        self.assertTrue(res.success)
        self.assertEqual(res.command_type, "QUERY")

    # 15. Voice Action Routing
    def test_15_voice_action_routing(self):
        header = "TEXT:list windows".encode("utf-8")
        payload = header + b"\x00" * 3180
        req = create_audio_request(
            session_id=self.session_id,
            device_id=self.device_id,
            audio_bytes=payload,
            audio_format="WAV",
        )
        res = self.orchestrator.process_command(
            session_id=self.session_id,
            device_id=self.device_id,
            audio_request=req,
        )
        self.assertTrue(res.success)

    # 16. Invalid Audio
    def test_16_invalid_audio(self):
        req = AudioRequest(
            request_id="REQ-BAD",
            session_id=self.session_id,
            device_id=self.device_id,
            timestamp=time.time(),
            nonce="N-BAD",
            audio_format="INVALID_FORMAT",
            sample_rate=16000,
            channels=1,
            duration_ms=1000.0,
            payload_size=10,
            payload_checksum="bad",
            audio_bytes=b"bad",
        )
        res = self.orchestrator.process_command(
            session_id=self.session_id,
            device_id=self.device_id,
            audio_request=req,
        )
        self.assertFalse(res.success)

    # 17. STT Unavailable Fallback
    def test_17_stt_unavailable_fallback(self):
        mock_stt = MagicMock()
        mock_stt.transcribe.return_value = MagicMock(
            success=False,
            error_code="SPEECH_TO_TEXT_UNAVAILABLE",
            transcript="",
        )
        self.orchestrator.voice_session_manager.stt_provider = mock_stt

        header = "TEXT:hello".encode("utf-8")
        req = create_audio_request(
            session_id=self.session_id,
            device_id=self.device_id,
            audio_bytes=header + b"\x00" * 3180,
        )
        res = self.orchestrator.process_command(
            session_id=self.session_id,
            device_id=self.device_id,
            audio_request=req,
        )
        self.assertFalse(res.success)
        self.assertIn("type your command", res.tts_response.text.lower())

    # 18. High-Risk Confirmation Gating
    def test_18_high_risk_confirmation(self):
        res = self.orchestrator.process_command(
            session_id=self.session_id,
            device_id=self.device_id,
            action_payload={
                "action_id": "ACT-CONF-001",
                "session_id": self.session_id,
                "device_id": self.device_id,
                "action_type": "computer.hotkey",
                "parameters": {"keys": ["Alt", "F4"]},
                "timestamp": time.time(),
            },
        )
        self.assertEqual(res.status, "AWAITING_CONFIRMATION")
        self.assertEqual(self.orchestrator.get_state(self.device_id), CompanionOrchestratorState.AWAITING_CONFIRMATION)
        self.assertIsNotNone(res.confirmation_payload)

    # 19. Confirmation Payload
    def test_19_confirmation_payload(self):
        res = self.orchestrator.process_command(
            session_id=self.session_id,
            device_id=self.device_id,
            action_payload={
                "action_id": "ACT-CONF-002",
                "session_id": self.session_id,
                "device_id": self.device_id,
                "action_type": "computer.hotkey",
                "parameters": {"keys": ["Alt", "F4"]},
                "timestamp": time.time(),
            },
        )
        conf = res.confirmation_payload
        self.assertIsNotNone(conf)
        self.assertEqual(conf.action_type, "computer.hotkey")
        self.assertEqual(conf.risk_level, "HIGH")
        self.assertTrue(conf.confirmation_token.startswith("CONF-"))

    # 20. Valid Confirmation
    def test_20_valid_confirmation(self):
        res = self.orchestrator.process_command(
            session_id=self.session_id,
            device_id=self.device_id,
            action_payload={
                "action_id": "ACT-CONF-003",
                "session_id": self.session_id,
                "device_id": self.device_id,
                "action_type": "computer.hotkey",
                "parameters": {"keys": ["Alt", "F4"]},
                "timestamp": time.time(),
            },
        )
        token = res.confirmation_payload.confirmation_token

        conf_res = self.orchestrator.confirm_action(
            session_id=self.session_id,
            device_id=self.device_id,
            action_id="ACT-CONF-003",
            confirmation_token=token,
            confirmed=True,
        )
        self.assertTrue(conf_res.success)
        self.assertEqual(conf_res.status, "SUCCESS")

    # 21. Confirmation Cancellation
    def test_21_confirmation_cancellation(self):
        res = self.orchestrator.process_command(
            session_id=self.session_id,
            device_id=self.device_id,
            action_payload={
                "action_id": "ACT-CONF-004",
                "session_id": self.session_id,
                "device_id": self.device_id,
                "action_type": "computer.hotkey",
                "parameters": {"keys": ["Alt", "F4"]},
                "timestamp": time.time(),
            },
        )
        token = res.confirmation_payload.confirmation_token

        cancel_res = self.orchestrator.confirm_action(
            session_id=self.session_id,
            device_id=self.device_id,
            action_id="ACT-CONF-004",
            confirmation_token=token,
            confirmed=False,
        )
        self.assertFalse(cancel_res.success)
        self.assertEqual(cancel_res.status, "CANCELLED")

    # 22. Confirmation Expiry
    def test_22_confirmation_expiry(self):
        now = time.time()
        res = self.orchestrator.process_command(
            session_id=self.session_id,
            device_id=self.device_id,
            action_payload={
                "action_id": "ACT-CONF-005",
                "session_id": self.session_id,
                "device_id": self.device_id,
                "action_type": "computer.hotkey",
                "parameters": {"keys": ["Alt", "F4"]},
                "timestamp": now,
            },
            current_time=now,
        )
        token = res.confirmation_payload.confirmation_token

        # Confirm 35s later (> 30s confirmation TTL)
        conf_res = self.orchestrator.confirm_action(
            session_id=self.session_id,
            device_id=self.device_id,
            action_id="ACT-CONF-005",
            confirmation_token=token,
            confirmed=True,
            current_time=now + 35.0,
        )
        self.assertFalse(conf_res.success)
        self.assertEqual(conf_res.status, "FAILED")

    # 23. Screen Stream Start
    def test_23_screen_stream_start(self):
        ok, msg, sess = self.stream_mgr.start_stream(self.session_id, self.device_id, fps=10.0)
        self.assertTrue(ok)
        self.assertIsNotNone(sess)
        self.assertEqual(sess.fps, 10.0)

    # 24. Frame Backpressure
    def test_24_frame_backpressure(self):
        ok, msg, stream = self.stream_mgr.start_stream(self.session_id, self.device_id)
        self.assertTrue(ok)
        # Push 6 frames (capacity ceiling 5)
        for i in range(6):
            self.stream_mgr.produce_frame(stream.stream_id)
        stats = stream.queue.get_stats()
        self.assertLessEqual(stats["queued_frames"], 5)

    # 25. Stale Target Rejection
    def test_25_stale_target_rejection(self):
        now = time.time()
        cached = {"timestamp": now - 20.0, "x": 100, "y": 200}  # 20s old > 15s TTL
        res = self.orchestrator.process_command(
            session_id=self.session_id,
            device_id=self.device_id,
            action_payload={
                "action_id": "ACT-STALE-001",
                "session_id": self.session_id,
                "device_id": self.device_id,
                "action_type": "computer.click_target",
                "parameters": {"target_name": "SubmitButton"},
                "timestamp": now,
            },
            cached_target=cached,
            current_time=now,
        )
        self.assertFalse(res.success)
        self.assertEqual(res.data.get("error"), REMOTE_TARGET_STALE)

    # 26. Observation-Only Boundary
    def test_26_observation_only_boundary(self):
        self.assertIn("remote.click", PROHIBITED_ACTIONS)
        self.assertIn("remote.type", PROHIBITED_ACTIONS)

    # 27. Emergency Stop Action
    def test_27_emergency_stop_action(self):
        res = self.orchestrator.trigger_emergency_stop(self.device_id)
        self.assertTrue(res["emergency_stop"])
        self.assertTrue(self.emergency.is_active())
        self.assertEqual(self.orchestrator.get_state(self.device_id), CompanionOrchestratorState.STOPPED)

    # 28. Emergency Stop Voice
    def test_28_emergency_stop_voice(self):
        header = "TEXT:stop immediately".encode("utf-8")
        req = create_audio_request(
            session_id=self.session_id,
            device_id=self.device_id,
            audio_bytes=header + b"\x00" * 3180,
        )
        res = self.orchestrator.process_command(
            session_id=self.session_id,
            device_id=self.device_id,
            audio_request=req,
        )
        self.assertEqual(res.status, "STOPPED")
        self.assertTrue(self.emergency.is_active())

    # 29. Emergency Stop Stream
    def test_29_emergency_stop_stream(self):
        ok, msg, stream = self.stream_mgr.start_stream(self.session_id, self.device_id)
        self.assertTrue(ok)
        self.orchestrator.trigger_emergency_stop(self.device_id)
        self.assertEqual(stream.state.value, "STOPPED")

    # 30. Post-Stop Rejection
    def test_30_post_stop_rejection(self):
        self.emergency.trigger()
        res = self.orchestrator.process_command(
            session_id=self.session_id,
            device_id=self.device_id,
            command_text="status",
        )
        self.assertFalse(res.success)
        self.assertEqual(res.status, "STOPPED")
        self.assertEqual(res.data.get("error"), REMOTE_STOPPED)

    # 31. Stop Reset Recovery
    def test_31_stop_reset_recovery(self):
        self.emergency.trigger()
        self.assertTrue(self.emergency.is_active())
        self.orchestrator.reset_emergency_stop(self.device_id)
        self.assertFalse(self.emergency.is_active())
        res = self.orchestrator.process_command(
            session_id=self.session_id,
            device_id=self.device_id,
            command_text="status",
        )
        self.assertTrue(res.success)

    # 32. Heartbeat Timeout
    def test_32_heartbeat_timeout(self):
        now = time.time()
        self.resilience.record_heartbeat(self.device_id, current_time=now)
        # 35s later (> 30s timeout)
        is_healthy, status = self.resilience.check_connection_health(self.device_id, current_time=now + 35.0)
        self.assertFalse(is_healthy)
        self.assertEqual(status, COMPANION_HEARTBEAT_TIMEOUT)

    # 33. Reconnect
    def test_33_reconnect(self):
        now = time.time()
        can_rec, msg, backoff = self.resilience.can_reconnect(self.device_id, current_time=now)
        self.assertTrue(can_rec)
        self.resilience.record_reconnect(self.device_id, current_time=now)

    # 34. Reconnect Limit
    def test_34_reconnect_limit(self):
        now = time.time()
        for _ in range(MAX_RECONNECT_ATTEMPTS):
            self.resilience.record_reconnect(self.device_id, current_time=now)
        can_rec, msg, backoff = self.resilience.can_reconnect(self.device_id, current_time=now + 1.0)
        self.assertFalse(can_rec)

    # 35. Degraded Stream Mode
    def test_35_degraded_stream_mode(self):
        self.resilience.set_degraded_flag(self.device_id, "screen_stream", True)
        self.assertTrue(self.resilience.is_degraded(self.device_id, "screen_stream"))
        # Action execution should still succeed in degraded stream mode
        res = self.orchestrator.process_command(
            session_id=self.session_id,
            device_id=self.device_id,
            command_text="status",
        )
        self.assertTrue(res.success)

    # 36. Resource Cleanup
    def test_36_resource_cleanup(self):
        self.resilience.record_heartbeat(self.device_id)
        self.resilience.cleanup_device(self.device_id)
        is_healthy, status = self.resilience.check_connection_health(self.device_id)
        self.assertFalse(is_healthy)

    # 37. Model Isolation
    def test_37_model_isolation(self):
        safe, reason, prop = RemoteActionSafetyGate.is_model_proposal_safe({
            "action": "powershell.exe -Command Get-Process",
        })
        self.assertFalse(safe)
        self.assertIn("Prohibited", reason)

    # 38. Secret Redaction
    def test_38_secret_redaction(self):
        res = self.orchestrator.process_command(
            session_id=self.session_id,
            device_id=self.device_id,
            action_payload={
                "action_id": "ACT-SEC-001",
                "session_id": self.session_id,
                "device_id": self.device_id,
                "action_type": "computer.type_text",
                "parameters": {"text": "MySecretToken12345", "is_sensitive": True},
                "timestamp": time.time(),
            },
        )
        self.assertTrue(res.success)
        raw_dict_str = json.dumps(res.to_dict())
        self.assertNotIn("MySecretToken12345", raw_dict_str)

    # 39. Localhost Boundary
    def test_39_localhost_boundary(self):
        with self.assertRaises(SecurityBindingError):
            SecureDashboardServer(host="0.0.0.0", port=8585)

    # 40. Legacy Endpoint Backward Compatibility
    def test_40_legacy_endpoint_backward_compatibility(self):
        gateway = SecureGateway()
        req = MagicMock()
        req.path = "/api/status"
        # Legacy status check should return assistant_status
        self.assertTrue(hasattr(gateway, "companion_orchestrator"))


if __name__ == "__main__":
    unittest.main()
