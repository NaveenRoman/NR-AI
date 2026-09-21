"""
Unit tests for FasterWhisperSTTProvider.
Phase 3 Local Voice Intelligence - STT Provider verification.
"""

from __future__ import annotations

import io
import unittest
from unittest.mock import MagicMock, patch

from app.voice.providers.faster_whisper_provider import FasterWhisperSTTProvider
from app.voice.provider import TranscriptionResult, TranscriptionSegment


class DummySegment:
    def __init__(self, text: str, start: float = 0.0, end: float = 1.0, avg_logprob: float = -0.1):
        self.text = text
        self.start = start
        self.end = end
        self.avg_logprob = avg_logprob


class DummyInfo:
    def __init__(self, language: str = "en", duration: float = 1.0):
        self.language = language
        self.duration = duration


class TestFasterWhisperProvider(unittest.TestCase):

    def test_01_init_parameters(self):
        provider = FasterWhisperSTTProvider(
            model_size="base",
            device="cpu",
            compute_type="int8",
            cpu_threads=4,
            lazy_load=True,
        )
        self.assertEqual(provider.model_size, "base")
        self.assertEqual(provider.device, "cpu")
        self.assertEqual(provider.compute_type, "int8")
        self.assertEqual(provider.cpu_threads, 4)
        self.assertFalse(provider.is_loaded)

    def test_02_cpu_int8_default_on_non_cuda_host(self):
        with patch("torch.cuda.is_available", return_value=False):
            provider = FasterWhisperSTTProvider(device="auto", compute_type="auto")
            self.assertEqual(provider.device, "cpu")
            self.assertEqual(provider.compute_type, "int8")

    def test_03_health_check(self):
        provider = FasterWhisperSTTProvider()
        self.assertTrue(provider.health())

        # Test simulated absence
        with patch.dict("sys.modules", {"faster_whisper": None}):
            self.assertFalse(provider.health())

    def test_04_empty_or_short_audio_handling(self):
        provider = FasterWhisperSTTProvider()
        res_empty = provider.transcribe(b"")
        self.assertEqual(res_empty.text, "")
        self.assertEqual(res_empty.duration_seconds, 0.0)

        res_short = provider.transcribe(b"too_short")
        self.assertEqual(res_short.text, "")

    def test_05_transcription_with_mocked_model(self):
        provider = FasterWhisperSTTProvider()
        mock_model = MagicMock()
        mock_model.transcribe.return_value = (
            [DummySegment("Hello NR AI", 0.0, 1.2), DummySegment("run tests", 1.2, 2.5)],
            DummyInfo(language="en", duration=2.5),
        )
        provider._model = mock_model
        provider._is_loaded = True

        dummy_wav = b"RIFF" + b"\x00" * 100
        result = provider.transcribe(dummy_wav)

        self.assertIsInstance(result, TranscriptionResult)
        self.assertEqual(result.text, "Hello NR AI run tests")
        self.assertEqual(result.language, "en")
        self.assertEqual(result.duration_seconds, 2.5)
        self.assertEqual(len(result.segments), 2)
        self.assertEqual(result.segments[0].text, "Hello NR AI")
        self.assertGreaterEqual(result.confidence or 0.0, 0.8)

    def test_06_model_lifecycle_unload(self):
        provider = FasterWhisperSTTProvider()
        provider._model = MagicMock()
        provider._is_loaded = True

        self.assertTrue(provider.is_loaded)
        provider.unload_model()
        self.assertFalse(provider.is_loaded)
        self.assertIsNone(provider._model)

    def test_07_transcription_exception_handling(self):
        provider = FasterWhisperSTTProvider()
        mock_model = MagicMock()
        mock_model.transcribe.side_effect = RuntimeError("CTranslate2 compute error")
        provider._model = mock_model
        provider._is_loaded = True

        dummy_wav = b"RIFF" + b"\x00" * 100
        result = provider.transcribe(dummy_wav)
        self.assertEqual(result.text, "")


if __name__ == "__main__":
    unittest.main()
