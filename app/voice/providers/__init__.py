"""
NR-AI Voice Providers Package.
Exposes Faster-Whisper STT and Kokoro ONNX TTS providers.
"""

from app.voice.providers.faster_whisper_provider import FasterWhisperSTTProvider
from app.voice.providers.kokoro_provider import KokoroTTSProvider

__all__ = [
    "FasterWhisperSTTProvider",
    "KokoroTTSProvider",
]
