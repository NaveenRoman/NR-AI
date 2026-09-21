"""
Unit tests for Voice Privacy and Security Controls.
Phase 3 Local Voice Intelligence - Zero audio retention and secret scrubbing verification.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.voice.session import VoiceSessionManager, VoiceSessionState
from app.voice.vad import VoiceActivityDetector
from app.security.guardrails import PromptGuardrails


class TestVoicePrivacyPhase3(unittest.TestCase):

    def test_01_no_audio_files_created_on_disk(self):
        sm = VoiceSessionManager()
        vad = VoiceActivityDetector()

        # Simulate conversational interaction in memory
        sm.transition_to(VoiceSessionState.WAKE_DETECTED, "wake")
        sm.transition_to(VoiceSessionState.LISTENING, "listening")

        # Ingest 10 chunks of synthetic audio
        chunk = b"\x00" * 3200
        for _ in range(10):
            vad.process_chunk(chunk)

        sm.transition_to(VoiceSessionState.STANDBY, "done")

        # Verify no .wav, .pcm, or temporary audio dump files were written in cwd
        audio_files = [f for f in os.listdir(".") if f.endswith((".wav", ".pcm", ".raw"))]
        self.assertEqual(len(audio_files), 0, f"Found leaked audio files on disk: {audio_files}")

    def test_02_zero_retention_in_standby(self):
        sm = VoiceSessionManager()
        self.assertEqual(sm.current_state, VoiceSessionState.STANDBY)

        # Audit log has no audio data
        for entry in sm.get_audit_log():
            self.assertNotIn("audio", entry)

        # Scrubbed history starts completely empty
        self.assertEqual(len(sm.get_history()), 0)

    def test_03_secret_scrubbing_api_keys(self):
        sm = VoiceSessionManager()
        # Utterance containing OpenAI and AWS API keys
        raw_text = "Please configure OPENAI_API_KEY sk-proj-1234567890abcdef1234567890abcdef and AWS AKIAIOSFODNN7EXAMPLE"
        scrubbed = sm.record_utterance_text(raw_text)

        # Verification: Raw secrets must not be present
        self.assertNotIn("sk-proj-1234567890abcdef1234567890abcdef", scrubbed)
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", scrubbed)

        # Redaction tokens should be present
        self.assertIn("REDACTED", scrubbed)

        # Stored history must also be scrubbed
        history = sm.get_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0], scrubbed)

    def test_04_pii_scrubbing_credit_card_and_email(self):
        sm = VoiceSessionManager()
        raw_text = "My email is user.test@example.com and card number is 4532-0150-1234-5678"
        scrubbed = sm.record_utterance_text(raw_text)

        self.assertNotIn("4532-0150-1234-5678", scrubbed)
        self.assertNotIn("user.test@example.com", scrubbed)
        self.assertIn("REDACTED", scrubbed)

    def test_05_audit_log_privacy_hygiene(self):
        sm = VoiceSessionManager()
        sm.transition_to(
            VoiceSessionState.WAKE_DETECTED,
            reason="wake",
            metadata={"source": "mic_realtek", "energy": 842.1},
        )
        audit = sm.get_audit_log()
        self.assertEqual(len(audit), 1)
        # Check that metadata does not contain raw audio or binary data
        for k, v in audit[0]["metadata"].items():
            self.assertNotIsInstance(v, bytes)


if __name__ == "__main__":
    unittest.main()
