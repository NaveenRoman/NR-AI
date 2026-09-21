"""
Unit tests for NR-AI Voice Provider Abstraction and Registry.
Inspired by OpenJarvis pluggable speech architecture.
"""

import unittest
from app.voice.provider import (
    STTProvider,
    TTSProvider,
    TranscriptionResult,
    TTSResult,
    VoiceProviderRegistry,
    SpeechRecognitionSTTProvider,
    MockSTTProvider,
    SAPI5TTSProvider,
    MemoryTTSProvider,
    SilentTTSProvider,
    FasterWhisperSTTProviderStub,
    KokoroTTSProviderStub,
)


class TestVoiceProviderAbstraction(unittest.TestCase):

    def test_01_provider_registration(self):
        stt_list = VoiceProviderRegistry.list_stt_providers()
        self.assertIn("speech-recognition", stt_list)
        self.assertIn("mock-stt", stt_list)
        self.assertIn("faster-whisper", stt_list)

        tts_list = VoiceProviderRegistry.list_tts_providers()
        self.assertIn("sapi5", tts_list)
        self.assertIn("memory-tts", tts_list)
        self.assertIn("silent-tts", tts_list)
        self.assertIn("kokoro", tts_list)

    def test_02_mock_stt_provider(self):
        provider = MockSTTProvider(["Hello NR AI", "Build the project"])
        self.assertTrue(provider.health())

        res1 = provider.transcribe(b"dummy_audio")
        self.assertEqual(res1.text, "Hello NR AI")
        self.assertEqual(res1.confidence, 1.0)

        res2 = provider.transcribe(b"dummy_audio")
        self.assertEqual(res2.text, "Build the project")

        # Empty queue returns empty string
        res3 = provider.transcribe(b"dummy_audio")
        self.assertEqual(res3.text, "")

    def test_03_memory_tts_provider(self):
        provider = MemoryTTSProvider()
        self.assertTrue(provider.health())

        res = provider.synthesize("Welcome back, Boss.")
        self.assertIsInstance(res, TTSResult)
        self.assertEqual(provider.spoken_history, ["Welcome back, Boss."])

        provider.synthesize("Opening Android Studio.")
        self.assertEqual(len(provider.spoken_history), 2)

    def test_04_silent_tts_provider(self):
        provider = SilentTTSProvider()
        self.assertTrue(provider.health())
        res = provider.synthesize("Silent speech")
        self.assertEqual(res.audio, b"")

    def test_05_faster_whisper_graceful_absence(self):
        provider = FasterWhisperSTTProviderStub()
        # Should report False without raising if faster_whisper is not installed
        self.assertFalse(provider.health())
        with self.assertRaises(RuntimeError):
            provider.transcribe(b"data")

    def test_06_kokoro_graceful_absence(self):
        provider = KokoroTTSProviderStub()
        self.assertFalse(provider.health())
        with self.assertRaises(RuntimeError):
            provider.synthesize("test")

    def test_07_stt_fallback_chain(self):
        # Requesting unavailable 'faster-whisper' should fall back to next healthy provider
        resolved = VoiceProviderRegistry.resolve_stt(preferred="faster-whisper")
        self.assertIsNotNone(resolved)
        self.assertTrue(resolved.health())
        # Should be speech-recognition or mock-stt
        self.assertIn(resolved.provider_id, ["speech-recognition", "mock-stt"])

    def test_08_tts_fallback_chain(self):
        # Requesting unavailable 'kokoro' should fall back to sapi5 or memory-tts
        resolved = VoiceProviderRegistry.resolve_tts(preferred="kokoro")
        self.assertIsNotNone(resolved)
        self.assertTrue(resolved.health())
        self.assertIn(resolved.provider_id, ["sapi5", "memory-tts", "silent-tts"])

    def test_09_custom_stt_registration(self):
        @VoiceProviderRegistry.register_stt("custom-test-stt")
        class CustomSTT(STTProvider):
            def health(self) -> bool:
                return True
            def transcribe(self, audio_bytes, **kwargs) -> TranscriptionResult:
                return TranscriptionResult(text="custom transcribed")

        resolved = VoiceProviderRegistry.resolve_stt(preferred="custom-test-stt")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.provider_id, "custom-test-stt")
        res = resolved.transcribe(b"")
        self.assertEqual(res.text, "custom transcribed")


if __name__ == "__main__":
    unittest.main()
