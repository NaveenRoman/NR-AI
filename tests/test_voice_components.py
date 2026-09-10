"""
NR-AI Voice Component Verification Suite.

Tests:
1. Microphone hardware detection and probe.
2. Wake word detection for 'Hey NR', 'Hello NR', 'OK NR', 'Hey NR AI', and rejection of random speech.
3. Text-to-Speech engine initialization, voice selection, and speech synthesis execution.
4. VoiceListener and VoiceSpeaker state transitions.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config.voice_config import VoiceConfig
from app.voice.listener import ListeningState, MockSpeechRecognizer, VoiceListener
from app.voice.speaker import AssistantState, MemoryTTS, Pyttsx3TTS, VoiceSpeaker


class TestVoiceComponents(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = VoiceConfig.from_env()
        cls.listener = VoiceListener(config=cls.config)
        cls.speaker = VoiceSpeaker(config=cls.config)

    def test_01_microphone_detection(self):
        """Verify microphone device probe detects hardware."""
        probe = self.listener.probe_microphone()
        print(f"\n[Microphone Probe] Available: {probe['available']}, Devices: {probe['device_count']}, Active: {probe['active_device']}")
        self.assertIn("available", probe)
        self.assertIn("device_count", probe)
        self.assertIn("active_device", probe)
        self.assertTrue(probe["available"], "Real microphone hardware must be detected on system")
        self.assertGreater(probe["device_count"], 0, "At least one audio input device must be present")

    def test_02_wake_word_detection_positive(self):
        """Test wake word detection on target phrases."""
        test_cases = [
            ("Hey NR", True, ""),
            ("Hey NR, what is the capital of Japan?", True, "what is the capital of Japan"),
            ("Hello NR", True, ""),
            ("Hello NR check status", True, "check status"),
            ("OK NR", True, ""),
            ("OK NR work on Android", True, "work on Android"),
            ("Hey NR AI", True, ""),
            ("Hey NR AI build the project", True, "build the project"),
            # Phonetic variations handled by listener
            ("hey and are what is the time", True, "what is the time"),
            ("hello n r", True, ""),
        ]
        for phrase, expected_wake, expected_cmd in test_cases:
            is_wake, extracted = self.listener.detect_wake_word(phrase)
            print(f"[Wake Test Positive] '{phrase}' -> Wake: {is_wake}, Extracted: '{extracted}'")
            self.assertEqual(is_wake, expected_wake, f"Failed wake detection for: '{phrase}'")
            if expected_cmd:
                self.assertEqual(extracted.lower(), expected_cmd.lower())

    def test_03_wake_word_rejection_negative(self):
        """Verify random conversational speech does NOT trigger wake word."""
        negative_cases = [
            "Good morning everyone",
            "What is the weather like today in Seattle?",
            "Can you explain quantum computing to me?",
            "Open the browser and search for python documentation",
            "The quick brown fox jumps over the lazy dog",
            "Are you listening to this conversation?",
            "No, that is not what I meant",
            "Just testing without any activation phrase",
        ]
        for phrase in negative_cases:
            is_wake, extracted = self.listener.detect_wake_word(phrase)
            print(f"[Wake Test Negative] '{phrase}' -> Wake: {is_wake}")
            self.assertFalse(is_wake, f"Random phrase incorrectly triggered wake word: '{phrase}'")
            self.assertIsNone(extracted)

    def test_04_tts_engine_and_speaker_execution(self):
        """Verify TTS engine actually executes and generates audio waveform."""
        import pyttsx3
        engine = pyttsx3.init()
        voices = engine.getProperty("voices")
        self.assertGreater(len(voices), 0, "At least one system TTS voice must be available")
        print(f"\n[TTS Engine] Available voices count: {len(voices)}")
        for i, v in enumerate(voices[:3]):
            print(f"  Voice {i}: {v.name} ({v.id})")

        # Verify real audio file synthesis
        wav_path = os.path.join(tempfile.gettempdir(), "test_tts_component.wav")
        if os.path.exists(wav_path):
            os.remove(wav_path)

        engine.save_to_file("NR AI voice feedback test successful.", wav_path)
        engine.runAndWait()

        self.assertTrue(os.path.exists(wav_path), "TTS engine must write audio file")
        file_size = os.path.getsize(wav_path)
        print(f"[TTS Audio Generation] Output WAV size: {file_size} bytes at {wav_path}")
        self.assertGreater(file_size, 1000, "Generated TTS audio must contain valid waveform data")
        os.remove(wav_path)

    def test_05_listener_state_transitions(self):
        """Verify listener state transitions correctly."""
        self.listener.start()
        self.assertEqual(self.listener.state, ListeningState.IDLE)

        self.listener.pause()
        self.assertTrue(self.listener.is_muted())
        self.assertEqual(self.listener.state, ListeningState.MUTED)

        self.listener.resume(cooldown=0.0)
        self.assertFalse(self.listener.is_muted())
        self.assertEqual(self.listener.state, ListeningState.IDLE)

        self.listener.stop()
        self.assertEqual(self.listener.state, ListeningState.STOPPED)
        self.listener.start()


if __name__ == "__main__":
    unittest.main(verbosity=2)
