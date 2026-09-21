"""
Unit tests for VoiceActivityDetector (VAD).
Phase 3 Local Voice Intelligence - Acoustic energy calibration and bounded timing verification.
"""

from __future__ import annotations

import struct
import unittest
from unittest.mock import patch

from app.voice.vad import VoiceActivityDetector, VADState, VADResult


def make_pcm_sine(amplitude: int, num_samples: int = 1600) -> bytes:
    """Helper to generate constant amplitude 16-bit PCM samples."""
    return struct.pack(f"<{num_samples}h", *([amplitude] * num_samples))


class TestVoiceVADPhase3(unittest.TestCase):

    def test_01_init_defaults(self):
        vad = VoiceActivityDetector(
            sample_rate=16000,
            silence_timeout_sec=1.5,
            max_utterance_sec=15.0,
            min_speech_duration_sec=0.25,
        )
        self.assertEqual(vad.silence_timeout_sec, 1.5)
        self.assertEqual(vad.max_utterance_sec, 15.0)
        self.assertEqual(vad.min_speech_duration_sec, 0.25)
        self.assertEqual(vad.current_state, VADState.SILENCE)
        self.assertFalse(vad.is_calibrated)

    def test_02_ambient_calibration(self):
        vad = VoiceActivityDetector(calibration_multiplier=3.0)
        # Background noise with RMS ~100
        ambient = make_pcm_sine(amplitude=100, num_samples=8000)
        threshold = vad.calibrate(ambient)

        self.assertTrue(vad.is_calibrated)
        self.assertGreaterEqual(threshold, 300.0)
        self.assertEqual(vad.energy_threshold, threshold)

    def test_03_speech_onset_and_continuation(self):
        vad = VoiceActivityDetector(
            default_energy_threshold=400.0,
            min_speech_duration_sec=0.1,  # 100ms
        )
        # Silence chunk (amplitude 50)
        silence_chunk = make_pcm_sine(amplitude=50, num_samples=1600)
        res1 = vad.process_chunk(silence_chunk)
        self.assertFalse(res1.is_speech)
        self.assertEqual(res1.state, VADState.SILENCE)

        # Loud speech chunk (amplitude 2000, 1600 samples = 100ms at 16kHz)
        speech_chunk = make_pcm_sine(amplitude=2000, num_samples=1600)
        res2 = vad.process_chunk(speech_chunk)
        self.assertTrue(res2.is_speech)
        self.assertIn(res2.state, [VADState.SPEECH_STARTING, VADState.SPEECH])

        # Second speech chunk reaches min_speech_duration
        res3 = vad.process_chunk(speech_chunk)
        self.assertTrue(res3.is_speech)
        self.assertEqual(res3.state, VADState.SPEECH)

    def test_04_silence_timeout_completion(self):
        vad = VoiceActivityDetector(
            default_energy_threshold=400.0,
            silence_timeout_sec=0.3,  # Short timeout for fast testing
            min_speech_duration_sec=0.05,
        )
        speech_chunk = make_pcm_sine(amplitude=2000, num_samples=1600)
        silence_chunk = make_pcm_sine(amplitude=50, num_samples=1600)

        # Trigger speech
        vad.process_chunk(speech_chunk)
        vad.process_chunk(speech_chunk)
        self.assertEqual(vad.current_state, VADState.SPEECH)

        # Feed silence chunks until silence_timeout_sec (0.3s) is exceeded
        res = None
        for _ in range(5):
            res = vad.process_chunk(silence_chunk)
            if res.is_utterance_complete:
                break

        # Simulate elapsed time past silence_timeout_sec (0.3s)
        with patch("time.perf_counter", return_value=vad._last_speech_time + 1.0):
            res = vad.process_chunk(silence_chunk)
            self.assertTrue(res.is_utterance_complete)
            self.assertEqual(vad.current_state, VADState.SILENCE)

    def test_05_max_utterance_cutoff(self):
        vad = VoiceActivityDetector(
            default_energy_threshold=400.0,
            max_utterance_sec=2.0,  # 2.0s max cutoff
            min_speech_duration_sec=0.05,
        )
        speech_chunk = make_pcm_sine(amplitude=2000, num_samples=1600)
        vad.process_chunk(speech_chunk)

        # Mock time progressing past 2.0s
        with patch("time.perf_counter", return_value=vad._utterance_start_time + 2.5):
            res = vad.process_chunk(speech_chunk)
            self.assertTrue(res.is_utterance_complete)
            self.assertTrue(res.is_max_cutoff_reached)
            self.assertEqual(vad.current_state, VADState.SILENCE)

    def test_06_buffer_accumulation_and_reset(self):
        vad = VoiceActivityDetector(default_energy_threshold=400.0)
        speech_chunk = make_pcm_sine(amplitude=1500, num_samples=1600)

        vad.process_chunk(speech_chunk)
        vad.process_chunk(speech_chunk)

        buf = vad.get_buffered_utterance_bytes()
        self.assertGreaterEqual(len(buf), len(speech_chunk) * 2)

        vad.reset()
        self.assertEqual(len(vad.get_buffered_utterance_bytes()), 0)
        self.assertEqual(vad.current_state, VADState.SILENCE)


if __name__ == "__main__":
    unittest.main()
