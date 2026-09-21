"""
Unit tests for VoiceProviderRegistry Phase 3 fallback chains and provider resolution.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.voice.provider import (
    STTProvider,
    TTSProvider,
    VoiceProviderRegistry,
    FasterWhisperSTTProvider,
    KokoroTTSProvider,
    SpeechRecognitionSTTProvider,
    SAPI5TTSProvider,
    MemoryTTSProvider,
    SilentTTSProvider,
    MockSTTProvider,
)


class TestVoiceProviderRegistryPhase3(unittest.TestCase):

    def test_01_discovery_orders(self):
        self.assertEqual(
            VoiceProviderRegistry.STT_DISCOVERY_ORDER,
            ["faster-whisper", "speech-recognition", "mock-stt"],
        )
        self.assertEqual(
            VoiceProviderRegistry.TTS_DISCOVERY_ORDER,
            ["kokoro", "sapi5", "memory-tts", "silent-tts"],
        )

    def test_02_stt_resolution_prefers_faster_whisper(self):
        with patch.object(FasterWhisperSTTProvider, "health", return_value=True):
            resolved = VoiceProviderRegistry.resolve_stt()
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved.provider_id, "faster-whisper")

    def test_03_stt_fallback_to_speech_recognition_when_whisper_fails(self):
        with patch.object(FasterWhisperSTTProvider, "health", return_value=False):
            with patch.object(SpeechRecognitionSTTProvider, "health", return_value=True):
                resolved = VoiceProviderRegistry.resolve_stt()
                self.assertIsNotNone(resolved)
                self.assertEqual(resolved.provider_id, "speech-recognition")

    def test_04_stt_fallback_to_mock_when_all_fail(self):
        with patch.object(FasterWhisperSTTProvider, "health", return_value=False):
            with patch.object(SpeechRecognitionSTTProvider, "health", return_value=False):
                resolved = VoiceProviderRegistry.resolve_stt()
                self.assertIsNotNone(resolved)
                self.assertEqual(resolved.provider_id, "mock-stt")

    def test_05_tts_resolution_prefers_kokoro_when_healthy(self):
        with patch.object(KokoroTTSProvider, "health", return_value=True):
            resolved = VoiceProviderRegistry.resolve_tts()
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved.provider_id, "kokoro")

    def test_06_tts_fallback_to_sapi5_when_kokoro_unhealthy(self):
        with patch.object(KokoroTTSProvider, "health", return_value=False):
            with patch.object(SAPI5TTSProvider, "health", return_value=True):
                resolved = VoiceProviderRegistry.resolve_tts()
                self.assertIsNotNone(resolved)
                self.assertEqual(resolved.provider_id, "sapi5")

    def test_07_tts_fallback_to_memory_when_sapi5_unhealthy(self):
        with patch.object(KokoroTTSProvider, "health", return_value=False):
            with patch.object(SAPI5TTSProvider, "health", return_value=False):
                resolved = VoiceProviderRegistry.resolve_tts()
                self.assertIsNotNone(resolved)
                self.assertEqual(resolved.provider_id, "memory-tts")

    def test_08_preferred_override(self):
        resolved = VoiceProviderRegistry.resolve_tts(preferred="silent-tts")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.provider_id, "silent-tts")


if __name__ == "__main__":
    unittest.main()
