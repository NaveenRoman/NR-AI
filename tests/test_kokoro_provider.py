"""
Unit tests for KokoroTTSProvider.
Phase 3 Local Voice Intelligence - Kokoro ONNX TTS Provider verification.
"""

from __future__ import annotations

import io
import unittest
from unittest.mock import MagicMock, patch
import numpy as np

from app.voice.providers.kokoro_provider import KokoroTTSProvider, DEFAULT_KOKORO_VOICES
from app.voice.provider import TTSResult


class TestKokoroProvider(unittest.TestCase):

    def test_01_init_parameters(self):
        provider = KokoroTTSProvider(
            default_voice="af_heart",
            speed=1.1,
            lazy_load=True,
        )
        self.assertEqual(provider.default_voice, "af_heart")
        self.assertEqual(provider.default_speed, 1.1)

    def test_02_health_reports_false_when_weights_absent(self):
        # When model files are not present on disk, health() must report False
        with patch.object(KokoroTTSProvider, "has_model_files", return_value=False):
            provider = KokoroTTSProvider()
            self.assertFalse(provider.health())

    def test_03_available_voices(self):
        provider = KokoroTTSProvider()
        voices = provider.available_voices()
        self.assertIn("af_heart", voices)
        self.assertIn("am_adam", voices)
        self.assertIn("bf_emma", voices)

    def test_04_empty_text_synthesis(self):
        provider = KokoroTTSProvider()
        res = provider.synthesize("")
        self.assertEqual(res.audio, b"")
        self.assertEqual(res.duration_seconds, 0.0)

        res_ws = provider.synthesize("   \n\t  ")
        self.assertEqual(res_ws.audio, b"")

    def test_05_synthesize_with_mock_kokoro_instance(self):
        mock_kokoro = MagicMock()
        # Generate 24000 samples of float32 audio (1.0 second at 24kHz)
        samples = np.zeros(24000, dtype=np.float32)
        mock_kokoro.create.return_value = (samples, 24000)
        mock_kokoro.get_voices.return_value = ["af_heart", "af_bella"]

        provider = KokoroTTSProvider(_instance_override=mock_kokoro)
        self.assertTrue(provider.health())

        result = provider.synthesize("System operational.", voice_id="af_heart")
        self.assertIsInstance(result, TTSResult)
        self.assertGreater(len(result.audio), 44)  # Valid WAV header + data
        self.assertEqual(result.format, "wav")
        self.assertEqual(result.sample_rate, 24000)
        self.assertEqual(result.duration_seconds, 1.0)
        self.assertEqual(result.voice_id, "af_heart")
        self.assertIn("inference_time_seconds", result.metadata)

    def test_06_model_lifecycle_unload(self):
        mock_kokoro = MagicMock()
        provider = KokoroTTSProvider(_instance_override=mock_kokoro)
        self.assertTrue(provider._is_loaded)

        provider.unload_model()
        self.assertFalse(provider._is_loaded)
        self.assertIsNone(provider._kokoro)

    def test_07_synthesis_exception_handling(self):
        mock_kokoro = MagicMock()
        mock_kokoro.create.side_effect = RuntimeError("ONNX inference failure")

        provider = KokoroTTSProvider(_instance_override=mock_kokoro)
        res = provider.synthesize("Test failure")
        self.assertEqual(res.audio, b"")
        self.assertEqual(res.duration_seconds, 0.0)


if __name__ == "__main__":
    unittest.main()
