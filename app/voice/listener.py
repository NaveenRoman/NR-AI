import enum
import re
import sys
import time
from typing import Any, Callable, Dict, Optional, Tuple

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
    WAITING_FOR_WAKE_WORD = "waiting_for_wake_word"
    WAKE_WORD_DETECTED = "wake_word_detected"
    CALIBRATING = "calibrating"
    LISTENING = "listening"
    PROCESSING = "processing"
    SUCCESS = "success"
    NO_SPEECH = "no_speech"
    UNRECOGNIZED = "unrecognized"
    ERROR = "error"
    STOPPED = "stopped"
    MUTED = "muted"


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

class DummyVoiceListener:
    """Safe no-op voice listener for server and headless environments (zero PortAudio/C dependencies)."""

    def __init__(self, config: Optional[VoiceConfig] = None):
        self.config = config or VoiceConfig()
        self.state = ListeningState.IDLE
        self._is_active = False
        self._is_muted = True
        self.last_recognized_phrase: Optional[str] = None
        self.last_raw_speech: Optional[str] = None

    def pause(self) -> None:
        pass

    def resume(self, cooldown: float = 0.6) -> None:
        pass

    def is_muted(self) -> bool:
        return True

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def is_listening(self) -> bool:
        return False

    def normalize_command(self, text: Optional[str]) -> Optional[str]:
        return (text or "").strip()

    def listen(self, timeout: Optional[float] = None) -> Optional[str]:
        return None

    def probe_microphone(self) -> Dict[str, Any]:
        return {
            "available": False,
            "device_count": 0,
            "active_device": None,
            "status": "HEADLESS_MODE",
            "details": "Running in server/headless mode (microphone disabled).",
            "push_to_talk_fallback": True,
        }

    def detect_wake_word(self, text: Optional[str]) -> Tuple[bool, Optional[str]]:
        return False, None

    def listen_for_wake_word(self, timeout: float = 3.0) -> Tuple[bool, Optional[str]]:
        return False, None

    def listen_for_command(self, timeout: float = 8.0) -> Optional[str]:
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
        self._is_muted = False
        self._cooldown_until = 0.0
        self.last_recognized_phrase: Optional[str] = None
        self.last_raw_speech: Optional[str] = None

    def _get_microphone(self) -> Optional[sr.Microphone]:
        if self._microphone is None:
            try:
                self._microphone = sr.Microphone()
            except Exception as e:
                safe_print(f"⚠️ Microphone initialization warning: {e}")
                self._microphone = None
        return self._microphone

    def pause(self) -> None:
        """Mutes microphone capture during TTS output to prevent self-triggering."""
        self._is_muted = True
        self.state = ListeningState.MUTED

    def resume(self, cooldown: float = 0.6) -> None:
        """Resumes microphone capture after TTS with an acoustic cooldown delay."""
        self._is_muted = False
        self._cooldown_until = time.time() + cooldown
        if not self._is_active:
            self.state = ListeningState.STOPPED
            return
        self.state = ListeningState.IDLE

    def is_muted(self) -> bool:
        if self._is_muted:
            return True
        if time.time() < self._cooldown_until:
            return True
        return False

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

        if self.is_muted():
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

    def probe_microphone(self) -> Dict[str, Any]:
        """
        Inspect physical microphone availability and return hardware diagnosis.
        """
        if getattr(self, "_cached_mic_probe", None) is not None:
            return self._cached_mic_probe
        if getattr(self.config, "silent_mode", False):
            self._cached_mic_probe = {
                "available": False,
                "device_count": 0,
                "active_device": None,
                "status": "SILENT_MODE",
                "details": "Silent mode active; microphone querying disabled.",
                "push_to_talk_fallback": True,
            }
            return self._cached_mic_probe
        try:
            mics = sr.Microphone.list_microphone_names()
            if not mics:
                return {
                    "available": False,
                    "device_count": 0,
                    "active_device": None,
                    "status": "NO_DEVICES_FOUND",
                    "details": "No microphone audio input devices detected by operating system.",
                    "push_to_talk_fallback": True,
                }
            active_name = mics[0]
            for m in mics:
                if "microphone" in m.lower():
                    active_name = m
                    break
            return {
                "available": True,
                "device_count": len(mics),
                "active_device": active_name,
                "status": "MICROPHONE_READY",
                "details": f"Detected {len(mics)} audio input device(s). Primary: {active_name}",
                "push_to_talk_fallback": True,
            }
        except Exception as e:
            return {
                "available": False,
                "device_count": 0,
                "active_device": None,
                "status": "ERROR",
                "details": f"Microphone inspection failed: {e}",
                "push_to_talk_fallback": True,
            }

    def detect_wake_word(self, text: Optional[str]) -> Tuple[bool, Optional[str]]:
        """
        Check if recognized phrase begins with or contains a wake word:
        'Hey NR', 'Hello NR', 'OK NR', 'Hey NR AI', etc.
        Also handles phonetic/imperfect STT variants: 'hey and are', 'hello and are',
        'hey in our', 'a and r', 'a n r', 'hey enter', 'hello n r', 'hey n r'.
        Returns (is_wake_word, remaining_command).
        """
        if not text:
            return False, None

        self.last_raw_speech = text
        cleaned = text.strip()

        # Normalize common acoustic STT transcription errors for "NR"
        normalized = cleaned.lower()
        normalized = re.sub(r"\b(?:and\s+are|in\s+our|in\s+are|an\s+r|and\s+r|enter)\b", "nr", normalized)
        normalized = re.sub(r"\bn\s+r\b", "nr", normalized)

        wake_prefixes = [
            r"^(?:hey|hello|hi|ok|ay|a)\s+nr(?:\s+ai)?(?:[,\s]+(.*))?$",
            r"^nr(?:\s+ai)?(?:[,\s]+(.*))?$",
        ]
        for pat in wake_prefixes:
            m = re.match(pat, normalized, flags=re.IGNORECASE)
            if m:
                m_orig = re.match(pat, cleaned, flags=re.IGNORECASE)
                remainder = m_orig.group(1) if m_orig and m_orig.lastindex and m_orig.lastindex >= 1 else (m.group(1) if m.lastindex and m.lastindex >= 1 else "")
                cmd = self.normalize_command(remainder) if remainder else ""
                self.last_recognized_phrase = "Hey NR" if "hey" in cleaned.lower() else "Hello NR"
                safe_print(f"⚡ Wake Word Recognized: '{cleaned}' (Normalized: '{normalized}')")
                return True, cmd

        for ww in self.config.wake_words:
            ww_norm = ww.lower().replace(" ", "")
            norm_no_spaces = normalized.replace(" ", "")
            if norm_no_spaces == ww_norm or norm_no_spaces.startswith(ww_norm) or normalized.startswith(ww):
                rem = cleaned[len(ww):].lstrip(" ,:.-") if len(cleaned) > len(ww) else ""
                cmd = self.normalize_command(rem) if rem else ""
                self.last_recognized_phrase = ww.title()
                safe_print(f"⚡ Wake Word Recognized: '{cleaned}' (Matched Word: '{ww}')")
                return True, cmd

        return False, None

    def listen_for_wake_word(
        self, timeout: Optional[float] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Listens for audio and verifies if a wake word was spoken.
        Sets state to WAITING_FOR_WAKE_WORD.
        """
        if self.is_muted():
            return False, None

        self.state = ListeningState.WAITING_FOR_WAKE_WORD
        actual_timeout = timeout or 3.0
        raw_text = self.listen(timeout=actual_timeout)
        if not raw_text:
            return False, None

        if not self.config.wake_word_enabled:
            return True, raw_text

        is_wake, command = self.detect_wake_word(raw_text)
        if is_wake:
            self.state = ListeningState.WAKE_WORD_DETECTED
            return True, command

        return False, None

    def listen_for_command(
        self, timeout: float = 8.0, phrase_time_limit: float = 10.0
    ) -> Optional[str]:
        """
        Dedicated second-stage listener to capture user command following wake activation.
        """
        if self.is_muted():
            return None

        self.state = ListeningState.LISTENING
        safe_print("🎙️ Listening for command...")
        return self.listen(timeout=timeout)



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