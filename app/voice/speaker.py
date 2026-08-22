import sys
from typing import Any, List, Optional

import pyttsx3
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


class BaseTTS:
    """Interface for Text-to-Speech engines."""

    def speak(self, text: str) -> None:
        raise NotImplementedError


class Pyttsx3TTS(BaseTTS):
    """Offline Text-to-Speech using pyttsx3."""

    def __init__(self, config: VoiceConfig):
        self.config = config
        self._engine: Optional[pyttsx3.Engine] = None
        self._init_engine()

    def _init_engine(self) -> None:
        try:
            self._engine = pyttsx3.init()
            self._engine.setProperty("rate", self.config.tts_rate)
            self._engine.setProperty("volume", self.config.tts_volume)

            voices = self._engine.getProperty("voices")
            if voices and 0 <= self.config.tts_voice_index < len(voices):
                self._engine.setProperty(
                    "voice", voices[self.config.tts_voice_index].id
                )
        except Exception as e:
            safe_print(f"⚠️ TTS Engine initialization warning: {e}")
            self._engine = None

    def speak(self, text: str) -> None:
        if not self._engine:
            self._init_engine()

        if self._engine:
            try:
                self._engine.say(text)
                self._engine.runAndWait()
            except Exception as e:
                safe_print(f"⚠️ Speech synthesis error: {e}")


class MemoryTTS(BaseTTS):
    """In-memory TTS engine that records spoken utterances for testing."""

    def __init__(self):
        self.spoken_history: List[str] = []

    def speak(self, text: str) -> None:
        self.spoken_history.append(text)


class SilentTTS(BaseTTS):
    """Silent TTS engine for quiet mode."""

    def speak(self, text: str) -> None:
        pass


class VoiceSpeaker:
    """
    NR AI Text-to-Speech Audio Feedback Engine.

    Features:
        - Concise user-facing speech synthesis
        - Avoids speaking large code dumps or raw tracebacks
        - Configurable rate, volume, and voice
        - Silent / headless mode support
    """

    def __init__(
        self,
        config: Optional[VoiceConfig] = None,
        tts_backend: Optional[BaseTTS] = None,
    ):
        self.config = config or VoiceConfig()

        if tts_backend:
            self.backend = tts_backend
        elif self.config.silent_mode or not self.config.tts_enabled:
            self.backend = SilentTTS()
        else:
            self.backend = Pyttsx3TTS(self.config)

    def _clean_for_speech(self, text: str) -> str:
        """
        Sanitize text before speaking:
        - Avoids speaking code blocks verbatim
        - Summarizes large multi-line outputs
        """
        if not text:
            return ""

        # If text is too long or contains code blocks, summarize for audio
        lines = text.strip().splitlines()
        if len(lines) > 4:
            # If starts with task completed output, synthesize short summary
            if "Task completed successfully" in lines[0]:
                return "Task completed successfully."
            return f"{lines[0]} and {len(lines) - 1} more lines."

        return text.strip()

    def speak(self, text: str, force: bool = False) -> None:
        """Speak user-facing response if TTS is enabled."""
        if not text:
            return

        safe_print(f"🤖 NR AI: {text}")

        if not self.config.tts_enabled and not force:
            return

        speech_text = self._clean_for_speech(text)
        if speech_text:
            self.backend.speak(speech_text)


if __name__ == "__main__":
    safe_print("================================")
    safe_print("       NR AI SPEAKER TEST")
    safe_print("================================")

    speaker = VoiceSpeaker()
    speaker.speak(
        "Hello. I am NR AI. Voice and audio feedback subsystems are fully operational."
    )

    safe_print("\n================================")
    safe_print("🟢 VOICE SPEAKER MODULE READY")
    safe_print("================================")