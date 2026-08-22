import enum
import re
import sys
from typing import Any, Callable, Dict, Optional

import speech_recognition as sr
from app.config.voice_config import VoiceConfig

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def safe_print(msg: str) -> None:
    try:
        print(msg)
    except Exception:
        try:
            print(msg.encode("ascii", "replace").decode("ascii"))
        except Exception:
            pass


class ListeningState(enum.Enum):
    IDLE = "idle"
    CALIBRATING = "calibrating"
    LISTENING = "listening"
    PROCESSING = "processing"
    SUCCESS = "success"
    NO_SPEECH = "no_speech"
    UNRECOGNIZED = "unrecognized"
    ERROR = "error"
    STOPPED = "stopped"


class BaseSpeechRecognizer:
    """Interface for pluggable speech recognition engines."""

    def recognize(
        self, recognizer: sr.Recognizer, audio: sr.AudioData
    ) -> Optional[str]:
        raise NotImplementedError


class GoogleSpeechRecognizer(BaseSpeechRecognizer):
    """Google Web Speech API recognizer."""

    def recognize(
        self, recognizer: sr.Recognizer, audio: sr.AudioData
    ) -> Optional[str]:
        try:
            return recognizer.recognize_google(audio)
        except sr.UnknownValueError:
            return None
        except sr.RequestError as e:
            safe_print(f"🌐 Speech recognition service error: {e}")
            return None


class MockSpeechRecognizer(BaseSpeechRecognizer):
    """Mock speech recognizer for deterministic automated testing."""

    def __init__(self, queued_texts: Optional[list] = None):
        self.queued_texts = list(queued_texts or [])

    def queue_text(self, text: str) -> None:
        self.queued_texts.append(text)

    def recognize(
        self, recognizer: sr.Recognizer, audio: sr.AudioData
    ) -> Optional[str]:
        if self.queued_texts:
            return self.queued_texts.pop(0)
        return None


class VoiceListener:
    """
    NR AI Speech Recognition and Microphone Capture Engine.

    Features:
        - Microphone start / stop control
        - Clear listening state tracking
        - Ambient noise auto-calibration
        - Command normalization and filler word stripping
        - Safe timeout and network error handling
        - Pluggable speech engine architecture
    """

    def __init__(
        self,
        config: Optional[VoiceConfig] = None,
        recognizer_backend: Optional[BaseSpeechRecognizer] = None,
    ):
        self.config = config or VoiceConfig()
        self.recognizer = sr.Recognizer()
        self.recognizer.energy_threshold = self.config.energy_threshold
        self.recognizer.dynamic_energy_threshold = self.config.dynamic_energy_threshold

        self.backend: BaseSpeechRecognizer = (
            recognizer_backend or GoogleSpeechRecognizer()
        )
        self.state = ListeningState.IDLE
        self._is_active = True
        self._microphone: Optional[sr.Microphone] = None

    def _get_microphone(self) -> Optional[sr.Microphone]:
        if self._microphone is None:
            try:
                self._microphone = sr.Microphone()
            except Exception as e:
                safe_print(f"⚠️ Microphone initialization warning: {e}")
                self._microphone = None
        return self._microphone

    def start(self) -> None:
        """Start or enable the listener."""
        self._is_active = True
        self.state = ListeningState.IDLE
        safe_print("🟢 Voice listener started.")

    def stop(self) -> None:
        """Stop or pause the listener."""
        self._is_active = False
        self.state = ListeningState.STOPPED
        safe_print("🛑 Voice listener stopped.")

    def is_listening(self) -> bool:
        return self._is_active and self.state in (
            ListeningState.LISTENING,
            ListeningState.PROCESSING,
        )

    def normalize_command(self, text: Optional[str]) -> Optional[str]:
        """
        Normalize spoken transcript:
        - Strips punctuation and trailing periods
        - Normalizes multiple spaces
        - Cleans polite filler words like 'please', 'can you', 'could you'
        """
        if not text:
            return None

        cleaned = text.strip()
        # Remove leading punctuation
        cleaned = re.sub(r"^[^\w\s]+", "", cleaned)
        # Remove trailing punctuation
        cleaned = re.sub(r"[.!?,;:]+$", "", cleaned).strip()

        # Remove leading polite fillers
        filler_patterns = [
            r"^(?:please\s+)",
            r"^(?:can\s+you\s+(?:please\s+)?)",
            r"^(?:could\s+you\s+(?:please\s+)?)",
            r"^(?:would\s+you\s+(?:please\s+)?)",
            r"^(?:nr\s+ai\s+(?:please\s+)?)",
            r"^(?:hey\s+nr\s+ai\s+)",
            r"^(?:ok\s+nr\s+ai\s+)",
        ]
        for pat in filler_patterns:
            cleaned = re.sub(pat, "", cleaned, flags=re.IGNORECASE).strip()

        return cleaned

    def listen(self, timeout: Optional[float] = None) -> Optional[str]:
        """
        Capture speech from microphone and convert to normalized text.
        """
        if not self._is_active:
            self.state = ListeningState.STOPPED
            return None

        # Check if using Mock recognizer with pre-queued inputs
        if isinstance(self.backend, MockSpeechRecognizer):
            self.state = ListeningState.PROCESSING
            res = self.backend.recognize(self.recognizer, None)
            if res:
                self.state = ListeningState.SUCCESS
                norm = self.normalize_command(res)
                safe_print(f"👤 You (Simulated Voice): {norm}")
                return norm
            self.state = ListeningState.NO_SPEECH
            return None

        mic = self._get_microphone()
        if not mic:
            safe_print("⚠️ No microphone device detected.")
            self.state = ListeningState.ERROR
            return None

        actual_timeout = timeout or self.config.listen_timeout

        try:
            with mic as source:
                self.state = ListeningState.CALIBRATING
                if self.config.ambient_noise_duration > 0:
                    self.recognizer.adjust_for_ambient_noise(
                        source, duration=self.config.ambient_noise_duration
                    )

                self.state = ListeningState.LISTENING
                safe_print("\n🎙️ NR AI is listening...")

                try:
                    audio = self.recognizer.listen(
                        source,
                        timeout=actual_timeout,
                        phrase_time_limit=self.config.phrase_time_limit,
                    )
                except sr.WaitTimeoutError:
                    self.state = ListeningState.NO_SPEECH
                    safe_print("⏱️ No speech detected.")
                    return None

            self.state = ListeningState.PROCESSING
            safe_print("🧠 Processing speech...")
            raw_text = self.backend.recognize(self.recognizer, audio)

            if not raw_text:
                self.state = ListeningState.UNRECOGNIZED
                safe_print("❓ NR AI couldn't understand that.")
                return None

            self.state = ListeningState.SUCCESS
            normalized = self.normalize_command(raw_text)
            safe_print(f"👤 You: {normalized}")
            return normalized

        except Exception as e:
            self.state = ListeningState.ERROR
            safe_print(f"❌ Audio capture error: {e}")
            return None
        finally:
            if self.state not in (
                ListeningState.SUCCESS,
                ListeningState.STOPPED,
            ):
                self.state = ListeningState.IDLE


if __name__ == "__main__":
    safe_print("================================")
    safe_print("       NR AI VOICE TEST")
    safe_print("================================")

    listener = VoiceListener()
    safe_print("Testing command normalization:")
    test_phrases = [
        "Please open the file menu and save.",
        "Can you create a Python calculator and run it?",
        "Hey NR AI fix the errors in main.py!",
    ]
    for p in test_phrases:
        norm = listener.normalize_command(p)
        safe_print(f"  '{p}' -> '{norm}'")

    safe_print("\nTesting microphone capture (waiting 3s)...")
    res = listener.listen(timeout=3.0)
    if res:
        safe_print(f"✅ Recognized: {res}")
    else:
        safe_print(f"State after listen: {listener.state.value}")

    safe_print("\n================================")
    safe_print("🟢 VOICE LISTENER MODULE READY")
    safe_print("================================")