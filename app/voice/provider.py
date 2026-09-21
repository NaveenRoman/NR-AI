"""
NR-AI Voice Provider Abstraction & Registry.
Inspired by OpenJarvis pluggable speech architecture, adapted for NR-AI.

Architecture Rules:
- Voice is strictly an input/output channel; Central Intelligence remains the brain.
- Pluggable STT and TTS backends with graceful fallback chains.
- Safe offline-first operation on Windows host (SAPI5 / SpeechRecognition).
- Zero mandatory external C/CUDA dependencies; optional backends auto-detected.
"""

from __future__ import annotations

import io
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, Type

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Data Models
# -----------------------------------------------------------------------------

@dataclass
class TranscriptionSegment:
    text: str
    start: float = 0.0
    end: float = 0.0
    confidence: Optional[float] = None


@dataclass
class TranscriptionResult:
    text: str
    language: Optional[str] = None
    confidence: Optional[float] = None
    duration_seconds: float = 0.0
    segments: List[TranscriptionSegment] = field(default_factory=list)


@dataclass
class TTSResult:
    audio: bytes = b""
    format: str = "wav"
    duration_seconds: float = 0.0
    voice_id: str = ""
    sample_rate: int = 24000
    metadata: Dict[str, Any] = field(default_factory=dict)


# -----------------------------------------------------------------------------
# Abstract Interfaces
# -----------------------------------------------------------------------------

class STTProvider(ABC):
    """Abstract base class for Speech-to-Text providers."""

    provider_id: str = "base"

    @abstractmethod
    def transcribe(
        self,
        audio_bytes: bytes,
        *,
        format: str = "wav",
        language: Optional[str] = None,
    ) -> TranscriptionResult:
        """Transcribe raw audio bytes to text."""
        raise NotImplementedError

    @abstractmethod
    def health(self) -> bool:
        """Return True if provider is ready and functional."""
        raise NotImplementedError

    def supported_formats(self) -> List[str]:
        return ["wav"]


class TTSProvider(ABC):
    """Abstract base class for Text-to-Speech providers."""

    provider_id: str = "base"

    @abstractmethod
    def synthesize(
        self,
        text: str,
        *,
        voice_id: str = "",
        speed: float = 1.0,
        output_format: str = "wav",
    ) -> TTSResult:
        """Synthesize text to audio bytes or output directly to speakers."""
        raise NotImplementedError

    @abstractmethod
    def health(self) -> bool:
        """Return True if provider is ready and functional."""
        raise NotImplementedError

    def available_voices(self) -> List[str]:
        return []


# -----------------------------------------------------------------------------
# Voice Provider Registry
# -----------------------------------------------------------------------------

class VoiceProviderRegistry:
    """
    Central registry for STT and TTS providers with automatic fallback resolution.
    """

    _stt_providers: Dict[str, Type[STTProvider]] = {}
    _tts_providers: Dict[str, Type[TTSProvider]] = {}

    # Priority order for auto-discovery
    STT_DISCOVERY_ORDER: List[str] = [
        "faster-whisper",
        "speech-recognition",
        "mock-stt",
    ]

    TTS_DISCOVERY_ORDER: List[str] = [
        "sapi5",
        "kokoro",
        "memory-tts",
        "silent-tts",
    ]

    @classmethod
    def register_stt(cls, key: str) -> Callable[[Type[STTProvider]], Type[STTProvider]]:
        def decorator(provider_cls: Type[STTProvider]) -> Type[STTProvider]:
            provider_cls.provider_id = key
            cls._stt_providers[key] = provider_cls
            return provider_cls
        return decorator

    @classmethod
    def register_tts(cls, key: str) -> Callable[[Type[TTSProvider]], Type[TTSProvider]]:
        def decorator(provider_cls: Type[TTSProvider]) -> Type[TTSProvider]:
            provider_cls.provider_id = key
            cls._tts_providers[key] = provider_cls
            return provider_cls
        return decorator

    @classmethod
    def get_stt_class(cls, key: str) -> Optional[Type[STTProvider]]:
        return cls._stt_providers.get(key)

    @classmethod
    def get_tts_class(cls, key: str) -> Optional[Type[TTSProvider]]:
        return cls._tts_providers.get(key)

    @classmethod
    def list_stt_providers(cls) -> List[str]:
        return list(cls._stt_providers.keys())

    @classmethod
    def list_tts_providers(cls) -> List[str]:
        return list(cls._tts_providers.keys())

    @classmethod
    def resolve_stt(cls, preferred: Optional[str] = None) -> Optional[STTProvider]:
        """
        Resolve healthy STT provider starting with preferred, then fallback order.
        """
        candidates = [preferred] if preferred else []
        candidates.extend(k for k in cls.STT_DISCOVERY_ORDER if k not in candidates)

        for key in candidates:
            if not key or key not in cls._stt_providers:
                continue
            try:
                inst = cls._stt_providers[key]()
                if inst.health():
                    return inst
            except Exception as e:
                logger.debug("STT provider '%s' failed health check: %s", key, e)
                continue
        return None

    @classmethod
    def resolve_tts(cls, preferred: Optional[str] = None) -> Optional[TTSProvider]:
        """
        Resolve healthy TTS provider starting with preferred, then fallback order.
        """
        candidates = [preferred] if preferred else []
        candidates.extend(k for k in cls.TTS_DISCOVERY_ORDER if k not in candidates)

        for key in candidates:
            if not key or key not in cls._tts_providers:
                continue
            try:
                inst = cls._tts_providers[key]()
                if inst.health():
                    return inst
            except Exception as e:
                logger.debug("TTS provider '%s' failed health check: %s", key, e)
                continue
        return None


# -----------------------------------------------------------------------------
# Built-in STT Providers
# -----------------------------------------------------------------------------

@VoiceProviderRegistry.register_stt("speech-recognition")
class SpeechRecognitionSTTProvider(STTProvider):
    """Primary STT provider wrapping SpeechRecognition library."""

    def __init__(self):
        import speech_recognition as sr
        self.recognizer = sr.Recognizer()

    def health(self) -> bool:
        try:
            import speech_recognition as sr
            return True
        except ImportError:
            return False

    def transcribe(
        self,
        audio_bytes: bytes,
        *,
        format: str = "wav",
        language: Optional[str] = None,
    ) -> TranscriptionResult:
        import speech_recognition as sr
        if not audio_bytes:
            return TranscriptionResult(text="")

        buf = io.BytesIO(audio_bytes)
        try:
            with sr.AudioFile(buf) as source:
                audio_data = self.recognizer.record(source)
            text = self.recognizer.recognize_google(audio_data, language=language or "en-US")
            return TranscriptionResult(text=text.strip(), language=language or "en-US")
        except sr.UnknownValueError:
            return TranscriptionResult(text="")
        except Exception as e:
            logger.warning("SpeechRecognition STT error: %s", e)
            return TranscriptionResult(text="")


@VoiceProviderRegistry.register_stt("mock-stt")
class MockSTTProvider(STTProvider):
    """Deterministic mock provider for unit tests."""

    def __init__(self, queued_responses: Optional[List[str]] = None):
        self.queue: List[str] = list(queued_responses or [])

    def queue_text(self, text: str) -> None:
        self.queue.append(text)

    def health(self) -> bool:
        return True

    def transcribe(
        self,
        audio_bytes: bytes,
        *,
        format: str = "wav",
        language: Optional[str] = None,
    ) -> TranscriptionResult:
        text = self.queue.pop(0) if self.queue else ""
        return TranscriptionResult(text=text, confidence=1.0)


@VoiceProviderRegistry.register_stt("faster-whisper")
class FasterWhisperSTTProviderStub(STTProvider):
    """Pluggable adapter for Faster-Whisper (CTranslate2)."""

    def __init__(self, model_size: str = "base", device: str = "auto"):
        self.model_size = model_size
        self.device = device
        self._model = None

    def health(self) -> bool:
        try:
            import faster_whisper
            return True
        except ImportError:
            return False

    def transcribe(
        self,
        audio_bytes: bytes,
        *,
        format: str = "wav",
        language: Optional[str] = None,
    ) -> TranscriptionResult:
        if not self.health():
            raise RuntimeError("faster_whisper is not installed.")
        # Implementation when installed
        return TranscriptionResult(text="")


# -----------------------------------------------------------------------------
# Built-in TTS Providers
# -----------------------------------------------------------------------------

@VoiceProviderRegistry.register_tts("sapi5")
class SAPI5TTSProvider(TTSProvider):
    """Native Windows SAPI5 TTS provider using pyttsx3."""

    def __init__(self, rate: int = 200, volume: float = 1.0, voice_index: int = 0):
        self.rate = rate
        self.volume = volume
        self.voice_index = voice_index
        self._engine = None

    def health(self) -> bool:
        try:
            import pyttsx3
            eng = pyttsx3.init()
            voices = eng.getProperty("voices")
            return len(voices) > 0
        except Exception:
            return False

    def available_voices(self) -> List[str]:
        try:
            import pyttsx3
            eng = pyttsx3.init()
            return [v.name for v in eng.getProperty("voices")]
        except Exception:
            return []

    def synthesize(
        self,
        text: str,
        *,
        voice_id: str = "",
        speed: float = 1.0,
        output_format: str = "wav",
    ) -> TTSResult:
        if not text:
            return TTSResult(audio=b"")

        import pyttsx3
        engine = pyttsx3.init()
        engine.setProperty("rate", int(self.rate * speed))
        engine.setProperty("volume", self.volume)

        voices = engine.getProperty("voices")
        if voice_id:
            for v in voices:
                if voice_id.lower() in v.name.lower() or voice_id == v.id:
                    engine.setProperty("voice", v.id)
                    break
        elif 0 <= self.voice_index < len(voices):
            engine.setProperty("voice", voices[self.voice_index].id)

        try:
            engine.say(text)
            engine.runAndWait()
            return TTSResult(audio=b"", duration_seconds=len(text) * 0.05)
        except Exception as e:
            logger.warning("SAPI5 TTS error: %s", e)
            return TTSResult(audio=b"")


@VoiceProviderRegistry.register_tts("memory-tts")
class MemoryTTSProvider(TTSProvider):
    """In-memory TTS provider that records spoken utterances for test assertions."""

    def __init__(self):
        self.spoken_history: List[str] = []

    def health(self) -> bool:
        return True

    def synthesize(
        self,
        text: str,
        *,
        voice_id: str = "",
        speed: float = 1.0,
        output_format: str = "wav",
    ) -> TTSResult:
        if text:
            self.spoken_history.append(text)
        return TTSResult(audio=b"dummy_pcm", duration_seconds=1.0)


@VoiceProviderRegistry.register_tts("silent-tts")
class SilentTTSProvider(TTSProvider):
    """Silent TTS provider for quiet and headless testing."""

    def health(self) -> bool:
        return True

    def synthesize(
        self,
        text: str,
        *,
        voice_id: str = "",
        speed: float = 1.0,
        output_format: str = "wav",
    ) -> TTSResult:
        return TTSResult(audio=b"")


@VoiceProviderRegistry.register_tts("kokoro")
class KokoroTTSProviderStub(TTSProvider):
    """Pluggable adapter for Kokoro ONNX TTS."""

    def health(self) -> bool:
        try:
            import kokoro
            return True
        except ImportError:
            return False

    def synthesize(
        self,
        text: str,
        *,
        voice_id: str = "",
        speed: float = 1.0,
        output_format: str = "wav",
    ) -> TTSResult:
        if not self.health():
            raise RuntimeError("kokoro is not installed.")
        return TTSResult(audio=b"")


# -----------------------------------------------------------------------------
# Module Exports
# -----------------------------------------------------------------------------

__all__ = [
    "STTProvider",
    "TTSProvider",
    "TranscriptionResult",
    "TranscriptionSegment",
    "TTSResult",
    "VoiceProviderRegistry",
    "SpeechRecognitionSTTProvider",
    "MockSTTProvider",
    "SAPI5TTSProvider",
    "MemoryTTSProvider",
    "SilentTTSProvider",
]
