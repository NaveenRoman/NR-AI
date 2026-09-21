"""
NR AI Voice Package.
Comprehensive Voice Intelligence: Providers, VAD, Barge-in, Session Management, and Telemetry.
"""

from app.voice.listener import (
    BaseSpeechRecognizer,
    GoogleSpeechRecognizer,
    ListeningState,
    MockSpeechRecognizer,
    VoiceListener,
)
from app.voice.speaker import (
    AssistantState,
    BaseTTS,
    MemoryTTS,
    Pyttsx3TTS,
    SilentTTS,
    VoiceSpeaker,
)

from app.voice.provider import (
    STTProvider,
    TTSProvider,
    TranscriptionResult,
    TranscriptionSegment,
    TTSResult,
    VoiceProviderRegistry,
    SpeechRecognitionSTTProvider,
    MockSTTProvider,
    SAPI5TTSProvider,
    MemoryTTSProvider,
    SilentTTSProvider,
    FasterWhisperSTTProvider,
    KokoroTTSProvider,
)

from app.voice.vad import VoiceActivityDetector, VADState, VADResult
from app.voice.barge_in import BargeInController
from app.voice.session import VoiceSessionManager, VoiceSessionState
from app.voice.telemetry import VoicePerformanceTelemetry, MetricProvenance, VoiceTurnTelemetry

__all__ = [
    "VoiceListener",
    "VoiceSpeaker",
    "ListeningState",
    "AssistantState",
    "BaseSpeechRecognizer",
    "GoogleSpeechRecognizer",
    "MockSpeechRecognizer",
    "BaseTTS",
    "Pyttsx3TTS",
    "MemoryTTS",
    "SilentTTS",
    "STTProvider",
    "TTSProvider",
    "TranscriptionResult",
    "TranscriptionSegment",
    "TTSResult",
    "VoiceProviderRegistry",
    "SpeechRecognitionSTTProvider",
    "MockSTTProvider",
    "FasterWhisperSTTProvider",
    "KokoroTTSProvider",
    "SAPI5TTSProvider",
    "MemoryTTSProvider",
    "SilentTTSProvider",
    "VoiceActivityDetector",
    "VADState",
    "VADResult",
    "BargeInController",
    "VoiceSessionManager",
    "VoiceSessionState",
    "VoicePerformanceTelemetry",
    "MetricProvenance",
    "VoiceTurnTelemetry",
]
