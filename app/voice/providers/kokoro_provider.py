"""
Kokoro TTS Provider for NR-AI.
OpenJarvis-inspired local Text-to-Speech provider powered by Kokoro ONNX.

Hardware Architecture Rules:
- ONNX CPU Execution Provider for consistent Windows x86_64 host execution.
- Strict provenance and health validation: requires both kokoro_onnx package and valid model files.
- Graceful deterministic fallback to Windows SAPI5 / MemoryTTS if model files are absent.
- Bounded memory footprint with lazy loading and explicit model disposal.
"""

from __future__ import annotations

import gc
import io
import logging
import os
import time
from pathlib import Path
from typing import Any, List, Optional

from app.voice.provider import TTSProvider, TTSResult, VoiceProviderRegistry

logger = logging.getLogger("NRAI.Voice.Kokoro")

# Standard Kokoro v0.19 voice identifiers
DEFAULT_KOKORO_VOICES: List[str] = [
    "af_heart",
    "af_bella",
    "af_sarah",
    "af_nicole",
    "af_sky",
    "am_adam",
    "am_michael",
    "bf_emma",
    "bf_isabella",
    "bm_george",
    "bm_lewis",
]

DEFAULT_MODEL_PATHS: List[str] = [
    os.path.join(os.getcwd(), "models", "kokoro", "kokoro-v0_19.onnx"),
    os.path.join(os.getcwd(), "models", "kokoro", "kokoro-v1.0.onnx"),
    os.path.expanduser("~/.cache/kokoro/kokoro-v0_19.onnx"),
]

DEFAULT_VOICE_PATHS: List[str] = [
    os.path.join(os.getcwd(), "models", "kokoro", "voices.bin"),
    os.path.join(os.getcwd(), "models", "kokoro", "voices.json"),
    os.path.expanduser("~/.cache/kokoro/voices.bin"),
]


@VoiceProviderRegistry.register_tts("kokoro")
class KokoroTTSProvider(TTSProvider):
    """
    Local neural TTS provider using Kokoro ONNX Runtime.
    """

    provider_id: str = "kokoro"

    def __init__(
        self,
        model_path: Optional[str] = None,
        voices_path: Optional[str] = None,
        default_voice: str = "af_heart",
        speed: float = 1.0,
        lazy_load: bool = True,
        _instance_override: Optional[Any] = None,
    ):
        self.default_voice = default_voice
        self.default_speed = speed
        self._kokoro: Any = _instance_override
        self._is_loaded: bool = _instance_override is not None

        # Resolve paths
        self.model_path = model_path or self._find_path(DEFAULT_MODEL_PATHS)
        self.voices_path = voices_path or self._find_path(DEFAULT_VOICE_PATHS)

        if not lazy_load and not self._is_loaded and self.has_model_files():
            self.load_model()

    @staticmethod
    def _find_path(candidates: List[str]) -> Optional[str]:
        for c in candidates:
            if os.path.exists(c):
                return c
        return None

    def has_model_files(self) -> bool:
        """Return True if model and voice assets exist on local disk."""
        if self._kokoro is not None:
            return True
        return bool(
            self.model_path
            and os.path.isfile(self.model_path)
            and self.voices_path
            and os.path.isfile(self.voices_path)
        )

    def health(self) -> bool:
        """
        Return True only if kokoro_onnx package is installed AND model assets are present.
        If assets are absent, returns False to trigger fallback to SAPI5.
        """
        try:
            import kokoro_onnx  # noqa: F401
            import soundfile  # noqa: F401
            return self.has_model_files()
        except Exception:
            return False

    def load_model(self) -> None:
        """Instantiate Kokoro ONNX inference session."""
        if self._is_loaded and self._kokoro is not None:
            return

        if not self.has_model_files():
            raise RuntimeError(
                f"Kokoro model files not found (model='{self.model_path}', voices='{self.voices_path}'). "
                "Kokoro requires local ONNX assets to run."
            )

        try:
            from kokoro_onnx import Kokoro
            logger.info("Loading Kokoro ONNX model from '%s'...", self.model_path)
            t0 = time.perf_counter()
            self._kokoro = Kokoro(
                model_path=self.model_path,
                voices_path=self.voices_path,
            )
            self._is_loaded = True
            elapsed = time.perf_counter() - t0
            logger.info("Kokoro ONNX model loaded successfully in %.2fs.", elapsed)
        except Exception as exc:
            self._is_loaded = False
            self._kokoro = None
            logger.error("Failed to load Kokoro ONNX model: %s", exc)
            raise

    def unload_model(self) -> None:
        """Release ONNX session and trigger garbage collection."""
        if self._kokoro is not None:
            logger.info("Unloading Kokoro ONNX model from memory...")
            del self._kokoro
            self._kokoro = None
            self._is_loaded = False
            gc.collect()

    def available_voices(self) -> List[str]:
        if self._kokoro is not None and hasattr(self._kokoro, "get_voices"):
            try:
                voices = self._kokoro.get_voices()
                if voices:
                    return list(voices)
            except Exception:
                pass
        return list(DEFAULT_KOKORO_VOICES)

    def synthesize(
        self,
        text: str,
        *,
        voice_id: str = "",
        speed: float = 1.0,
        output_format: str = "wav",
    ) -> TTSResult:
        """
        Synthesize text to WAV audio bytes using Kokoro ONNX.
        """
        if not text or not text.strip():
            return TTSResult(audio=b"", duration_seconds=0.0, format="wav")

        if not self.health():
            raise RuntimeError("KokoroTTSProvider is not healthy or model files are missing.")

        if not self._is_loaded:
            self.load_model()

        import soundfile as sf

        target_voice = voice_id or self.default_voice
        target_speed = speed or self.default_speed
        t0 = time.perf_counter()

        try:
            samples, sample_rate = self._kokoro.create(
                text=text.strip(),
                voice=target_voice,
                speed=target_speed,
            )

            # Encode float32 samples to 16-bit PCM WAV
            wav_buffer = io.BytesIO()
            sf.write(wav_buffer, samples, sample_rate, format="WAV", subtype="PCM_16")
            wav_bytes = wav_buffer.getvalue()

            duration = round(len(samples) / float(sample_rate), 3) if sample_rate > 0 else 0.0
            elapsed = time.perf_counter() - t0

            return TTSResult(
                audio=wav_bytes,
                format="wav",
                duration_seconds=duration,
                voice_id=target_voice,
                sample_rate=sample_rate,
                metadata={
                    "inference_time_seconds": round(elapsed, 3),
                    "sample_count": len(samples),
                },
            )
        except Exception as exc:
            logger.warning("Kokoro synthesis error: %s", exc)
            return TTSResult(audio=b"", duration_seconds=0.0, format="wav")
