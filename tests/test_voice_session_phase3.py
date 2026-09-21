"""
Unit tests for VoiceSessionManager.
Phase 3 Local Voice Intelligence - 8-state bounded lifecycle and privacy verification.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.voice.session import VoiceSessionManager, VoiceSessionState


class TestVoiceSessionPhase3(unittest.TestCase):

    def test_01_initial_state(self):
        sm = VoiceSessionManager()
        self.assertEqual(sm.current_state, VoiceSessionState.STANDBY)
        self.assertTrue(sm.is_standby)
        self.assertFalse(sm.is_listening)
        self.assertFalse(sm.is_speaking)

    def test_02_valid_lifecycle_transitions(self):
        sm = VoiceSessionManager()

        # STANDBY -> WAKE_DETECTED
        self.assertTrue(sm.transition_to(VoiceSessionState.WAKE_DETECTED, "wake_word_detected"))
        self.assertEqual(sm.current_state, VoiceSessionState.WAKE_DETECTED)

        # WAKE_DETECTED -> LISTENING
        self.assertTrue(sm.transition_to(VoiceSessionState.LISTENING, "speech_start"))
        self.assertTrue(sm.is_listening)

        # LISTENING -> TRANSCRIBING
        self.assertTrue(sm.transition_to(VoiceSessionState.TRANSCRIBING, "utterance_complete"))
        self.assertEqual(sm.current_state, VoiceSessionState.TRANSCRIBING)

        # TRANSCRIBING -> THINKING
        self.assertTrue(sm.transition_to(VoiceSessionState.THINKING, "stt_complete"))
        self.assertEqual(sm.current_state, VoiceSessionState.THINKING)

        # THINKING -> SPEAKING
        self.assertTrue(sm.transition_to(VoiceSessionState.SPEAKING, "response_ready"))
        self.assertTrue(sm.is_speaking)

        # SPEAKING -> STANDBY
        self.assertTrue(sm.transition_to(VoiceSessionState.STANDBY, "playback_concluded"))
        self.assertTrue(sm.is_standby)

    def test_03_illegal_transitions_rejected(self):
        sm = VoiceSessionManager()
        # Cannot jump from STANDBY directly to THINKING or SPEAKING
        self.assertFalse(sm.transition_to(VoiceSessionState.THINKING, "illegal_jump"))
        self.assertEqual(sm.current_state, VoiceSessionState.STANDBY)

        self.assertFalse(sm.transition_to(VoiceSessionState.SPEAKING, "illegal_jump"))
        self.assertEqual(sm.current_state, VoiceSessionState.STANDBY)

    def test_04_inactivity_timeout_auto_sleep(self):
        sm = VoiceSessionManager(inactivity_timeout_sec=5.0)
        sm.transition_to(VoiceSessionState.LISTENING, "user_initiated")

        # Simulate 6.0 seconds passing with no activity
        with patch("time.perf_counter", return_value=sm._last_activity_time + 6.0):
            timed_out = sm.check_inactivity_timeout()
            self.assertTrue(timed_out)
            self.assertEqual(sm.current_state, VoiceSessionState.STANDBY)

    def test_05_emergency_stop(self):
        sm = VoiceSessionManager()
        sm.transition_to(VoiceSessionState.LISTENING, "listening")

        self.assertTrue(sm.emergency_stop(reason="user_voice_command_stop"))
        self.assertEqual(sm.current_state, VoiceSessionState.STOPPED)

        # Cannot jump to LISTENING while STOPPED
        self.assertFalse(sm.transition_to(VoiceSessionState.LISTENING, "attempt_resume"))

        # Must reset to STANDBY
        self.assertTrue(sm.reset_to_standby())
        self.assertEqual(sm.current_state, VoiceSessionState.STANDBY)

    def test_06_audit_log_records_transitions_without_audio(self):
        sm = VoiceSessionManager()
        sm.transition_to(VoiceSessionState.WAKE_DETECTED, "wake_word")
        sm.transition_to(VoiceSessionState.LISTENING, "mic_open")

        audit = sm.get_audit_log()
        self.assertEqual(len(audit), 2)
        self.assertEqual(audit[0]["from_state"], "STANDBY")
        self.assertEqual(audit[0]["to_state"], "WAKE_DETECTED")
        self.assertEqual(audit[1]["to_state"], "LISTENING")

        # Verify no audio payload fields exist in audit
        for entry in audit:
            self.assertNotIn("audio_bytes", entry)
            self.assertNotIn("audio_pcm", entry)

    def test_07_scrubbed_utterance_history(self):
        sm = VoiceSessionManager()
        clean = sm.record_utterance_text("My API key is sk-1234567890abcdef1234567890abcdef")
        history = sm.get_history()

        self.assertEqual(len(history), 1)
        # Secret should be redacted or masked
        self.assertNotIn("sk-1234567890abcdef1234567890abcdef", history[0])


if __name__ == "__main__":
    unittest.main()
