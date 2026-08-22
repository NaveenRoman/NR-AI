from dataclasses import dataclass, field
import os
from typing import Optional


@dataclass
class VoiceConfig:
    """Configuration for NR AI Voice Recognition and Speech Synthesis."""

    # Speech-to-Text settings
    voice_enabled: bool = True
    recognition_engine: str = "google"  # google, mock, offline
    listen_timeout: float = 5.0
    phrase_time_limit: float = 10.0
    ambient_noise_duration: float = 0.5
    energy_threshold: int = 300
    dynamic_energy_threshold: bool = True

    # Text-to-Speech settings
    tts_enabled: bool = True
    tts_rate: int = 175
    tts_volume: float = 1.0
    tts_voice_index: int = 0
    silent_mode: bool = False

    # Safety & Fallbacks
    fallback_to_text: bool = True
    speak_errors_only: bool = False

    @classmethod
    def from_env(cls) -> "VoiceConfig":
        """Load configuration from environment variables if present."""
        return cls(
            voice_enabled=os.getenv("NR_VOICE_ENABLED", "true").lower() == "true",
            recognition_engine=os.getenv("NR_SPEECH_ENGINE", "google"),
            listen_timeout=float(os.getenv("NR_LISTEN_TIMEOUT", "5.0")),
            phrase_time_limit=float(os.getenv("NR_PHRASE_TIME_LIMIT", "10.0")),
            tts_enabled=os.getenv("NR_TTS_ENABLED", "true").lower() == "true",
            tts_rate=int(os.getenv("NR_TTS_RATE", "175")),
            tts_volume=float(os.getenv("NR_TTS_VOLUME", "1.0")),
            silent_mode=os.getenv("NR_SILENT_MODE", "false").lower() == "true",
        )
