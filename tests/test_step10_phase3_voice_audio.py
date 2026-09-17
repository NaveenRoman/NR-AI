"""
Step 10 Phase 3 — Phone Voice Command & Secure Audio Pipeline Test Suite.
Verifies audio request validation, speech-to-text bridge, intent parsing,
voice safety gate, voice session lifecycle, rate limiting, and gateway endpoints.
Total Tests: 40.
"""

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

from app.remote.audio import (
    AudioFormat,
    AudioRequest,
    VoiceResponse,
    create_audio_request,
    validate_audio_request,
)
from app.remote.config import (
    AUDIO_CHECKSUM_MISMATCH,
    AUDIO_TOO_LARGE,
    AUDIO_TOO_LONG,
    AUDIO_TOO_SHORT,
    EMPTY_AUDIO,
    INVALID_AUDIO_REQUEST,
    INVALID_CHANNEL_COUNT,
    INVALID_SAMPLE_RATE,
    MAX_AUDIO_BYTES,
    MAX_AUDIO_DURATION_SECONDS,
    MIN_AUDIO_DURATION_SECONDS,
    SPEECH_TO_TEXT_UNAVAILABLE,
    TRANSCRIPTION_FAILED,
    TRANSCRIPTION_TIMEOUT,
    UNSUPPORTED_AUDIO_FORMAT,
    VOICE_CONFIRMATION_TIMEOUT_SECONDS,
    VOICE_SESSION_EXPIRED,
)
from app.remote.auth import SessionManager
from app.remote.emergency import EmergencyStopController
from app.remote.identity import PairingManager, PCIdentity
from app.remote.permissions import PhonePermissionScope
from app.remote.protocol import SecureRequest
from app.remote.rate_limiter import VoiceRateLimiter
from app.remote.server import SecureGateway
from app.remote.speech_to_text import (
    DevelopmentSpeechToTextProvider,
    WhisperSpeechToTextProvider,
)
from app.remote.voice_intent import (
    VoiceIntent,
    VoiceIntentParser,
    VoiceIntentType,
)
from app.remote.voice_safety import (
    VoiceCommandSafetyGate,
    VoiceSafetyDecision,
)
from app.remote.voice_session import (
    VoiceCommandSession,
    VoiceSessionError,
    VoiceSessionManager,
    VoiceSessionState,
)


def _make_mock_audio_bytes(duration_ms: float = 1000.0, text_hint: str = "what time is it") -> bytes:
    """Helper to generate a mock audio payload with embedded text header."""
    header = f"RIFF1234WAVEfmt MOCK_AUDIO_TEXT:{text_hint}:END".encode("utf-8")
    padding = b"\x00" * max(0, int(duration_ms * 2) - len(header))
    return header + padding


def _sign_request(req: SecureRequest, secret_hex: str) -> str:
    """Helper to compute valid HMAC-SHA256 signature for a SecureRequest."""
    canonical = req.compute_canonical_string()
    secret_bytes = bytes.fromhex(secret_hex)
    return hmac.new(secret_bytes, canonical.encode("utf-8"), hashlib.sha256).hexdigest()


class TestAudioValidation(unittest.TestCase):
    """Tests 01-08: Audio request bounds and format validation."""

    def test_01_valid_audio_request_success(self):
        raw = _make_mock_audio_bytes(1000.0)
        req = create_audio_request(
            session_id="SES-001",
            device_id="DEV-001",
            audio_format=AudioFormat.WAV,
            sample_rate=16000,
            channels=1,
            duration_ms=1000.0,
            audio_bytes=raw,
        )
        valid, reason = validate_audio_request(req)
        self.assertTrue(valid)
        self.assertEqual(reason, "AUDIO_REQUEST_VALID")

    def test_02_empty_audio_payload_rejected(self):
        req = create_audio_request(
            session_id="SES-002",
            device_id="DEV-001",
            audio_format=AudioFormat.WAV,
            sample_rate=16000,
            channels=1,
            duration_ms=500.0,
            audio_bytes=b"",
        )
        valid, reason = validate_audio_request(req)
        self.assertFalse(valid)
        self.assertEqual(reason, EMPTY_AUDIO)

    def test_03_audio_too_large_rejected(self):
        oversized = b"\x01" * (MAX_AUDIO_BYTES + 1024)
        req = create_audio_request(
            session_id="SES-003",
            device_id="DEV-001",
            audio_format=AudioFormat.WAV,
            sample_rate=16000,
            channels=1,
            duration_ms=5000.0,
            audio_bytes=oversized,
        )
        valid, reason = validate_audio_request(req)
        self.assertFalse(valid)
        self.assertEqual(reason, AUDIO_TOO_LARGE)

    def test_04_audio_too_short_rejected(self):
        raw = _make_mock_audio_bytes(50.0)
        req = create_audio_request(
            session_id="SES-004",
            device_id="DEV-001",
            audio_format=AudioFormat.WAV,
            sample_rate=16000,
            channels=1,
            duration_ms=50.0,  # 0.05s < 0.1s minimum
            audio_bytes=raw,
        )
        valid, reason = validate_audio_request(req)
        self.assertFalse(valid)
        self.assertEqual(reason, AUDIO_TOO_SHORT)

    def test_05_audio_too_long_rejected(self):
        raw = _make_mock_audio_bytes(65000.0)
        req = create_audio_request(
            session_id="SES-005",
            device_id="DEV-001",
            audio_format=AudioFormat.WAV,
            sample_rate=16000,
            channels=1,
            duration_ms=65000.0,  # 65s > 60s max
            audio_bytes=raw,
        )
        valid, reason = validate_audio_request(req)
        self.assertFalse(valid)
        self.assertEqual(reason, AUDIO_TOO_LONG)

    def test_06_invalid_sample_rate_rejected(self):
        raw = _make_mock_audio_bytes(1000.0)
        req = create_audio_request(
            session_id="SES-006",
            device_id="DEV-001",
            audio_format=AudioFormat.WAV,
            sample_rate=4000,  # Below 8000 minimum
            channels=1,
            duration_ms=1000.0,
            audio_bytes=raw,
        )
        valid, reason = validate_audio_request(req)
        self.assertFalse(valid)
        self.assertEqual(reason, INVALID_SAMPLE_RATE)

    def test_07_invalid_channel_count_rejected(self):
        raw = _make_mock_audio_bytes(1000.0)
        req = create_audio_request(
            session_id="SES-007",
            device_id="DEV-001",
            audio_format=AudioFormat.WAV,
            sample_rate=16000,
            channels=6,  # > 2 channels
            duration_ms=1000.0,
            audio_bytes=raw,
        )
        valid, reason = validate_audio_request(req)
        self.assertFalse(valid)
        self.assertEqual(reason, INVALID_CHANNEL_COUNT)

    def test_08_unsupported_audio_format_rejected(self):
        raw = _make_mock_audio_bytes(1000.0)
        req = AudioRequest(
            request_id="AUD-INV",
            session_id="SES-008",
            device_id="DEV-001",
            timestamp=time.time(),
            nonce="nonce-inv",
            audio_format="MP4_VIDEO",  # unsupported
            sample_rate=16000,
            channels=1,
            duration_ms=1000.0,
            payload_size=len(raw),
            checksum=hashlib.sha256(raw).hexdigest(),
            audio_bytes=raw,
        )
        valid, reason = validate_audio_request(req)
        self.assertFalse(valid)
        self.assertEqual(reason, UNSUPPORTED_AUDIO_FORMAT)


class TestAudioIntegrityAndSafety(unittest.TestCase):
    """Tests 09-12: Audio integrity, checksum verification, and secret exclusion."""

    def test_09_checksum_mismatch_rejected(self):
        raw = _make_mock_audio_bytes(1000.0)
        req = AudioRequest(
            request_id="AUD-TAMPER",
            session_id="SES-009",
            device_id="DEV-001",
            timestamp=time.time(),
            nonce="nonce-tamper",
            audio_format="WAV",
            sample_rate=16000,
            channels=1,
            duration_ms=1000.0,
            payload_size=len(raw),
            checksum="0000000000000000000000000000000000000000000000000000000000000000",
            audio_bytes=raw,
        )
        valid, reason = validate_audio_request(req)
        self.assertFalse(valid)
        self.assertEqual(reason, AUDIO_CHECKSUM_MISMATCH)

    def test_10_metadata_dict_strictly_excludes_raw_audio(self):
        raw = b"\x01\x02\x03\x04SECRET_PAYLOAD"
        req = create_audio_request(
            session_id="SES-010",
            device_id="DEV-001",
            audio_format=AudioFormat.WAV,
            sample_rate=16000,
            channels=1,
            duration_ms=1000.0,
            audio_bytes=raw,
        )
        meta = req.to_metadata_dict()
        self.assertNotIn("audio_bytes", meta)
        self.assertNotIn("payload_hex", meta)
        self.assertNotIn(b"SECRET_PAYLOAD", str(meta).encode("utf-8"))
        self.assertIn("checksum", meta)
        self.assertIn("payload_size", meta)

    def test_11_create_audio_request_computes_sha256(self):
        raw = b"AUDIO_SAMPLE_12345"
        req = create_audio_request(
            session_id="SES-011",
            device_id="DEV-001",
            audio_format=AudioFormat.WAV,
            sample_rate=16000,
            channels=1,
            duration_ms=500.0,
            audio_bytes=raw,
        )
        expected_hash = hashlib.sha256(raw).hexdigest()
        self.assertEqual(req.checksum, expected_hash)
        self.assertEqual(req.payload_size, len(raw))

    def test_12_non_bytes_payload_raises_type_error(self):
        with self.assertRaises(TypeError):
            create_audio_request(
                session_id="SES-012",
                device_id="DEV-001",
                audio_format=AudioFormat.WAV,
                sample_rate=16000,
                channels=1,
                duration_ms=500.0,
                audio_bytes="not-bytes-string",  # type: ignore
            )


class TestSpeechToTextProviders(unittest.TestCase):
    """Tests 13-18: STT providers, fixtures, and offline fallback."""

    def test_13_development_stt_custom_fixture(self):
        provider = DevelopmentSpeechToTextProvider()
        raw = b"AUDIO_FIXTURE_1"
        cs = hashlib.sha256(raw).hexdigest()
        provider.register_fixture(cs, "custom mapped voice command")

        req = create_audio_request(
            session_id="SES-013",
            device_id="DEV-001",
            audio_format=AudioFormat.WAV,
            sample_rate=16000,
            channels=1,
            duration_ms=1000.0,
            audio_bytes=raw,
        )
        ok, msg, text = provider.transcribe(req)
        self.assertTrue(ok)
        self.assertEqual(text, "custom mapped voice command")

    def test_14_development_stt_metadata_hint(self):
        provider = DevelopmentSpeechToTextProvider()
        raw = b"AUDIO_GENERIC"
        req = create_audio_request(
            session_id="SES-014",
            device_id="DEV-001",
            audio_format=AudioFormat.WAV,
            sample_rate=16000,
            channels=1,
            duration_ms=1000.0,
            audio_bytes=raw,
            metadata={"expected_transcript": "open project status"},
        )
        ok, msg, text = provider.transcribe(req)
        self.assertTrue(ok)
        self.assertEqual(text, "open project status")

    def test_15_development_stt_mock_header(self):
        provider = DevelopmentSpeechToTextProvider()
        raw = _make_mock_audio_bytes(1000.0, text_hint="system health check")
        req = create_audio_request(
            session_id="SES-015",
            device_id="DEV-001",
            audio_format=AudioFormat.WAV,
            sample_rate=16000,
            channels=1,
            duration_ms=1000.0,
            audio_bytes=raw,
        )
        ok, msg, text = provider.transcribe(req)
        self.assertTrue(ok)
        self.assertEqual(text, "system health check")

    def test_16_development_stt_simulate_failure(self):
        provider = DevelopmentSpeechToTextProvider()
        provider.set_simulate_failure(True)
        raw = _make_mock_audio_bytes(1000.0)
        req = create_audio_request(
            session_id="SES-016",
            device_id="DEV-001",
            audio_format=AudioFormat.WAV,
            sample_rate=16000,
            channels=1,
            duration_ms=1000.0,
            audio_bytes=raw,
        )
        ok, msg, text = provider.transcribe(req)
        self.assertFalse(ok)
        self.assertEqual(msg, TRANSCRIPTION_FAILED)
        self.assertIsNone(text)

    def test_17_development_stt_simulate_timeout(self):
        provider = DevelopmentSpeechToTextProvider()
        provider.set_simulate_timeout(True)
        raw = _make_mock_audio_bytes(1000.0)
        req = create_audio_request(
            session_id="SES-017",
            device_id="DEV-001",
            audio_format=AudioFormat.WAV,
            sample_rate=16000,
            channels=1,
            duration_ms=1000.0,
            audio_bytes=raw,
        )
        ok, msg, text = provider.transcribe(req)
        self.assertFalse(ok)
        self.assertEqual(msg, TRANSCRIPTION_TIMEOUT)
        self.assertIsNone(text)

    def test_18_whisper_stt_missing_returns_unavailable(self):
        whisper_provider = WhisperSpeechToTextProvider()
        # In current environment, whisper package is not installed
        if not whisper_provider._is_available:
            ok, msg = whisper_provider.health_check()
            self.assertFalse(ok)
            self.assertEqual(msg, SPEECH_TO_TEXT_UNAVAILABLE)
            raw = _make_mock_audio_bytes(1000.0)
            req = create_audio_request(
                session_id="SES-018",
                device_id="DEV-001",
                audio_format=AudioFormat.WAV,
                sample_rate=16000,
                channels=1,
                duration_ms=1000.0,
                audio_bytes=raw,
            )
            t_ok, t_msg, t_text = whisper_provider.transcribe(req)
            self.assertFalse(t_ok)
            self.assertEqual(t_msg, SPEECH_TO_TEXT_UNAVAILABLE)
            self.assertIsNone(t_text)
        else:
            self.assertEqual(whisper_provider.get_provider_type(), "LOCAL_WHISPER_STT")


class TestVoiceIntentParser(unittest.TestCase):
    """Tests 19-24: Deterministic voice intent parsing."""

    def test_19_emergency_stop_keywords_parsed(self):
        triggers = ["stop", "emergency stop", "halt", "freeze", "abort", "kill all"]
        for phrase in triggers:
            intent = VoiceIntentParser.parse_intent(phrase)
            self.assertEqual(intent.intent_type, VoiceIntentType.EMERGENCY_STOP, f"Failed for '{phrase}'")
            self.assertEqual(intent.risk_level, "CRITICAL")
            self.assertFalse(intent.requires_confirmation)

    def test_20_prohibited_commands_parsed(self):
        prohibited = [
            "format c:",
            "delete system32",
            "rmdir /s",
            "shutdown -s",
            "taskkill",
        ]
        for phrase in prohibited:
            intent = VoiceIntentParser.parse_intent(phrase)
            self.assertEqual(intent.intent_type, VoiceIntentType.PROHIBITED, f"Failed for '{phrase}'")
            self.assertEqual(intent.risk_level, "CRITICAL")

    def test_21_status_and_time_queries_parsed(self):
        intent_time = VoiceIntentParser.parse_intent("what time is it")
        self.assertEqual(intent_time.intent_type, VoiceIntentType.SYSTEM_TIME)
        self.assertFalse(intent_time.requires_confirmation)

        intent_status = VoiceIntentParser.parse_intent("system status")
        self.assertEqual(intent_status.intent_type, VoiceIntentType.STATUS_QUERY)
        self.assertFalse(intent_status.requires_confirmation)

    def test_22_confirmation_responses_parsed(self):
        confirmations = ["yes", "confirm", "proceed", "yes confirm"]
        for phrase in confirmations:
            intent = VoiceIntentParser.parse_intent(phrase)
            self.assertEqual(intent.intent_type, VoiceIntentType.CONFIRMATION_RESPONSE)
            self.assertTrue(intent.parameters.get("confirmed"))

    def test_23_cancellation_responses_parsed(self):
        cancellations = ["cancel", "no", "abort action"]
        for phrase in cancellations:
            intent = VoiceIntentParser.parse_intent(phrase)
            self.assertEqual(intent.intent_type, VoiceIntentType.CONFIRMATION_RESPONSE)
            self.assertFalse(intent.parameters.get("confirmed"))

    def test_24_unknown_intent_parsed(self):
        intent = VoiceIntentParser.parse_intent("unrecognized random gibberish phrase xyz 123")
        self.assertEqual(intent.intent_type, VoiceIntentType.UNKNOWN)
        self.assertEqual(intent.confidence, 0.0)


class TestVoiceSafetyGate(unittest.TestCase):
    """Tests 25-29: Deterministic safety gate evaluation."""

    def test_25_emergency_stop_allowed_when_emergency_active(self):
        intent = VoiceIntent(
            intent_id="INT-EM",
            text="stop",
            normalized_text="stop",
            intent_type=VoiceIntentType.EMERGENCY_STOP,
            confidence=1.0,
        )
        dec, reason = VoiceCommandSafetyGate.evaluate(
            intent=intent,
            is_emergency_active=True,
            granted_scopes={PhonePermissionScope.VOICE_COMMAND},
        )
        self.assertEqual(dec, VoiceSafetyDecision.SAFE)

    def test_26_commands_denied_when_emergency_active(self):
        intent = VoiceIntent(
            intent_id="INT-NORM",
            text="what time is it",
            normalized_text="what time is it",
            intent_type=VoiceIntentType.SYSTEM_TIME,
            confidence=1.0,
        )
        dec, reason = VoiceCommandSafetyGate.evaluate(
            intent=intent,
            is_emergency_active=True,
            granted_scopes={PhonePermissionScope.VOICE_COMMAND},
        )
        self.assertEqual(dec, VoiceSafetyDecision.DENIED)
        self.assertIn("EMERGENCY_STOP_ACTIVE", reason)

    def test_27_prohibited_intent_unconditionally_denied(self):
        intent = VoiceIntent(
            intent_id="INT-PROH",
            text="format c:",
            normalized_text="format c",
            intent_type=VoiceIntentType.PROHIBITED,
            confidence=1.0,
        )
        dec, reason = VoiceCommandSafetyGate.evaluate(
            intent=intent,
            is_emergency_active=False,
            granted_scopes={PhonePermissionScope.VOICE_COMMAND},
        )
        self.assertEqual(dec, VoiceSafetyDecision.DENIED)
        self.assertIn("PROHIBITED", reason)

    def test_28_unknown_intent_unconditionally_denied(self):
        intent = VoiceIntent(
            intent_id="INT-UNK",
            text="unknown",
            normalized_text="unknown",
            intent_type=VoiceIntentType.UNKNOWN,
            confidence=0.0,
        )
        dec, reason = VoiceCommandSafetyGate.evaluate(
            intent=intent,
            is_emergency_active=False,
            granted_scopes={PhonePermissionScope.VOICE_COMMAND},
        )
        self.assertEqual(dec, VoiceSafetyDecision.UNKNOWN)

    def test_29_missing_voice_scope_denied(self):
        intent = VoiceIntent(
            intent_id="INT-TIME",
            text="what time is it",
            normalized_text="what time is it",
            intent_type=VoiceIntentType.SYSTEM_TIME,
            confidence=1.0,
        )
        # Granted scopes lacks VOICE_COMMAND
        dec, reason = VoiceCommandSafetyGate.evaluate(
            intent=intent,
            is_emergency_active=False,
            granted_scopes={PhonePermissionScope.READ_STATUS},
        )
        self.assertEqual(dec, VoiceSafetyDecision.DENIED)
        self.assertIn("VOICE_PERMISSION_DENIED", reason)


class TestVoiceSessionLifecycle(unittest.TestCase):
    """Tests 30-35: Voice session state transitions and management."""

    def setUp(self):
        self.stt = DevelopmentSpeechToTextProvider()
        self.em = EmergencyStopController()
        self.manager = VoiceSessionManager(
            stt_provider=self.stt,
            emergency_controller=self.em,
        )

    def test_30_full_successful_session_lifecycle(self):
        raw = _make_mock_audio_bytes(1000.0, text_hint="what time is it")
        req = create_audio_request(
            session_id="SES-LIFECYCLE-1",
            device_id="DEV-001",
            audio_format=AudioFormat.WAV,
            sample_rate=16000,
            channels=1,
            duration_ms=1000.0,
            audio_bytes=raw,
        )
        resp = self.manager.process_voice_request(
            audio_req=req,
            granted_scopes={PhonePermissionScope.VOICE_COMMAND},
        )
        self.assertEqual(resp.status, "SUCCESS")
        self.assertIn("current system time", resp.result_text)
        session = self.manager.get_session(req.session_id)
        self.assertIsNotNone(session)
        self.assertEqual(session.state, VoiceSessionState.COMPLETED)

    def test_31_invalid_state_transition_raises_error(self):
        session = VoiceCommandSession(session_id="SES-TRANS-INV", device_id="DEV-001")
        self.assertEqual(session.state, VoiceSessionState.IDLE)
        # IDLE to COMPLETED directly is invalid
        with self.assertRaises(VoiceSessionError):
            session.transition_to(VoiceSessionState.COMPLETED)

    def test_32_confirmation_flow_approved(self):
        raw = _make_mock_audio_bytes(1000.0, text_hint="restart agent")
        req = create_audio_request(
            session_id="SES-CONFIRM-1",
            device_id="DEV-001",
            audio_format=AudioFormat.WAV,
            sample_rate=16000,
            channels=1,
            duration_ms=1000.0,
            audio_bytes=raw,
        )
        resp = self.manager.process_voice_request(
            audio_req=req,
            granted_scopes={PhonePermissionScope.VOICE_COMMAND},
        )
        self.assertEqual(resp.status, "CONFIRMATION_REQUIRED")
        self.assertTrue(resp.requires_confirmation)
        cid = resp.confirmation_id
        self.assertIsNotNone(cid)

        # Confirm action
        ok, msg, conf_resp = self.manager.confirm_action(
            session_id=req.session_id,
            confirmation_id=cid,
            confirmed=True,
        )
        self.assertTrue(ok)
        self.assertEqual(conf_resp.status, "SUCCESS")
        session = self.manager.get_session(req.session_id)
        self.assertEqual(session.state, VoiceSessionState.COMPLETED)

    def test_33_confirmation_flow_cancelled(self):
        raw = _make_mock_audio_bytes(1000.0, text_hint="clear memory")
        req = create_audio_request(
            session_id="SES-CONFIRM-2",
            device_id="DEV-001",
            audio_format=AudioFormat.WAV,
            sample_rate=16000,
            channels=1,
            duration_ms=1000.0,
            audio_bytes=raw,
        )
        resp = self.manager.process_voice_request(
            audio_req=req,
            granted_scopes={PhonePermissionScope.VOICE_COMMAND},
        )
        cid = resp.confirmation_id
        self.assertIsNotNone(cid)

        # Cancel confirmation
        ok, msg, conf_resp = self.manager.confirm_action(
            session_id=req.session_id,
            confirmation_id=cid,
            confirmed=False,
        )
        self.assertTrue(ok)
        self.assertEqual(conf_resp.status, "CANCELLED")
        session = self.manager.get_session(req.session_id)
        self.assertEqual(session.state, VoiceSessionState.CANCELLED)

    def test_34_cancel_active_session(self):
        ok, msg, session = self.manager.create_session("DEV-001")
        self.assertTrue(ok)
        session.transition_to(VoiceSessionState.RECEIVING)
        can_ok, can_msg = self.manager.cancel_session(session.session_id)
        self.assertTrue(can_ok)
        self.assertEqual(session.state, VoiceSessionState.CANCELLED)

    def test_35_emergency_stop_halts_all_sessions(self):
        ok, msg, session = self.manager.create_session("DEV-001")
        self.assertTrue(ok)
        session.transition_to(VoiceSessionState.RECEIVING)
        self.em.trigger(triggered_by="TEST", reason="OPERATOR_HALT")
        self.assertEqual(session.state, VoiceSessionState.STOPPED)


class TestVoiceRateLimiterAndGateway(unittest.TestCase):
    """Tests 36-40: Voice rate limits and Gateway API endpoints."""

    def test_36_voice_rate_limiter_enforces_limit(self):
        limiter = VoiceRateLimiter(requests_per_minute=20, burst_limit=3)
        device_id = "DEV-RATE-TEST"
        # 3 requests allowed under burst
        for _ in range(3):
            allowed, reason, retry = limiter.allow_request(device_id)
            self.assertTrue(allowed)
        # 4th request in same instant exceeds burst
        allowed, reason, retry = limiter.allow_request(device_id)
        self.assertFalse(allowed)
        self.assertIsNotNone(retry)

    def test_37_gateway_voice_upload_unauthenticated_rejected(self):
        pc_id = PCIdentity("PC-TEST", "localhost", "Test")
        pairing = PairingManager(pc_id)
        session_mgr = SessionManager(pairing)
        gateway = SecureGateway(pairing_manager=pairing, session_manager=session_mgr)
        req = SecureRequest(
            request_id="REQ-01",
            device_id="DEV-001",
            session_id="INVALID-SES",
            timestamp=time.time(),
            nonce="nonce-1",
            action="voice.audio_upload",
            scope=PhonePermissionScope.VOICE_COMMAND.value,
            payload={},
            signature="bad-sig",
        )
        resp = gateway.handle_secure_command(req.to_json(), "127.0.0.1")
        self.assertEqual(resp.code, 401)

    def test_38_gateway_voice_upload_invalid_signature_rejected(self):
        pc_id = PCIdentity("PC-TEST", "localhost", "Test")
        pairing = PairingManager(pc_id)
        session_mgr = SessionManager(pairing)
        gateway = SecureGateway(pairing_manager=pairing, session_manager=session_mgr)
        code = pairing.generate_pairing_code()
        _, _, secret = pairing.confirm_pairing("DEV-P3", code)
        _, _, session = session_mgr.create_session("DEV-P3")
        req = SecureRequest(
            request_id="REQ-02",
            device_id="DEV-P3",
            session_id=session.session_id,
            timestamp=time.time(),
            nonce="nonce-2",
            action="voice.audio_upload",
            scope=PhonePermissionScope.VOICE_COMMAND.value,
            payload={},
            signature="00" * 32,
        )
        resp = gateway.handle_secure_command(req.to_json(), "127.0.0.1")
        self.assertEqual(resp.code, 403)
        self.assertIn("SIGNATURE", resp.error)

    def test_39_gateway_voice_status_endpoint(self):
        pc_id = PCIdentity("PC-TEST", "localhost", "Test")
        pairing = PairingManager(pc_id)
        session_mgr = SessionManager(pairing)
        gateway = SecureGateway(pairing_manager=pairing, session_manager=session_mgr)
        code = pairing.generate_pairing_code()
        _, _, secret = pairing.confirm_pairing("DEV-P3", code)
        _, _, session = session_mgr.create_session("DEV-P3")
        gateway.voice_session_manager.create_session("DEV-P3", session_id=session.session_id)

        req = SecureRequest(
            request_id="REQ-03",
            device_id="DEV-P3",
            session_id=session.session_id,
            timestamp=time.time(),
            nonce="nonce-3",
            action="voice.status",
            scope=PhonePermissionScope.READ_STATUS.value,
            payload={},
        )
        req.signature = req.sign(secret)
        resp = gateway.handle_secure_command(req.to_json(), "127.0.0.1")
        self.assertEqual(resp.code, 200)
        self.assertEqual(resp.data["session_id"], session.session_id)

    def test_40_gateway_voice_cancel_endpoint(self):
        pc_id = PCIdentity("PC-TEST", "localhost", "Test")
        pairing = PairingManager(pc_id)
        session_mgr = SessionManager(pairing)
        gateway = SecureGateway(pairing_manager=pairing, session_manager=session_mgr)
        code = pairing.generate_pairing_code()
        _, _, secret = pairing.confirm_pairing("DEV-P3", code)
        _, _, session = session_mgr.create_session("DEV-P3")
        gateway.voice_session_manager.create_session("DEV-P3", session_id=session.session_id)

        req = SecureRequest(
            request_id="REQ-04",
            device_id="DEV-P3",
            session_id=session.session_id,
            timestamp=time.time(),
            nonce="nonce-4",
            action="voice.cancel",
            scope=PhonePermissionScope.VOICE_COMMAND.value,
            payload={},
        )
        req.signature = req.sign(secret)
        resp = gateway.handle_secure_command(req.to_json(), "127.0.0.1")
        self.assertEqual(resp.code, 200)
        self.assertEqual(resp.data["status"], "CANCELLED")


if __name__ == "__main__":
    unittest.main()
