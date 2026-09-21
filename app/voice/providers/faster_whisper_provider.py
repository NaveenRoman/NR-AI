"""
Faster-Whisper STT Provider for NR-AI.
OpenJarvis-inspired local Speech-to-Text provider powered by CTranslate2.

Hardware Architecture Rules:
- CPU-first INT8 quantized inference for hosts without discrete NVIDIA CUDA GPUs.
- Lazy model loading to preserve system RAM when voice listening is inactive.
- Explicit resource unloading and garbage collection on session idle/stop.
- Deterministic fallback when package or model weights are unavailable.
"""

from __future__ import annotations

import gc
import io
import logging
import math
import os
import time
from typing import Any, List, Optional

from app.voice.provider import (
    STTProvider,
    TranscriptionResult,
    TranscriptionSegment,
    VoiceProviderRegistry,
)

logger = logging.getLogger("NRAI.Voice.FasterWhisper")


@VoiceProviderRegistry.register_stt("faster-whisper")
class FasterWhisperSTTProvider(STTProvider):
    """
    High-performance local STT provider using Faster-Whisper (CTranslate2).
    """

    provider_id: str = "faster-whisper"

    def __init__(
        self,
        model_size: str = "base",
        device: str = "auto",
        compute_type: str = "auto",
        download_root: Optional[str] = None,
        cpu_threads: Optional[int] = None,
        beam_size: int = 1,
        vad_filter: bool = True,
        lazy_load: bool = True,
    ):
        self.model_size = model_size
        self.download_root = download_root
        self.beam_size = beam_size
        self.vad_filter = vad_filter

        # Hardware-aware device and compute type configuration
        if device == "auto":
            cuda_available = False
            try:
                import torch
                cuda_available = torch.cuda.is_available()
            except ImportError:
                pass
            self.device = "cuda" if cuda_available else "cpu"
        else:
            self.device = device

        if compute_type == "auto":
            self.compute_type = "float16" if self.device == "cuda" else "int8"
        else:
            self.compute_type = compute_type

        # Bound CPU threads to prevent thread contention on Windows host
        available_cores = os.cpu_count() or 4
        self.cpu_threads = cpu_threads or min(4, available_cores)

        self._model: Any = None
        self._is_loaded: bool = False
        self._load_error: Optional[str] = None

        if not lazy_load:
            self.load_model()

    @property
    def is_loaded(self) -> bool:
        return self._is_loaded and (self._model is not None)

    def health(self) -> bool:
        """Return True if faster_whisper is available and not in permanent failure state."""
        try:
            import faster_whisper  # noqa: F401
            return self._load_error is None
        except Exception:
            return False

    def load_model(self) -> None:
        """Instantiate and load the WhisperModel into memory."""
        if self.is_loaded:
            return

        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            self._load_error = f"faster_whisper package not installed: {exc}"
            raise RuntimeError(self._load_error) from exc

        try:
            logger.info(
                "Loading Faster-Whisper model '%s' on %s (%s, threads=%d)...",
                self.model_size,
                self.device,
                self.compute_type,
                self.cpu_threads,
            )
            t0 = time.perf_counter()
            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
                cpu_threads=self.cpu_threads,
                download_root=self.download_root,
            )
            self._is_loaded = True
            self._load_error = None
            elapsed = time.perf_counter() - t0
            logger.info("Faster-Whisper model loaded successfully in %.2fs.", elapsed)
        except Exception as exc:
            self._load_error = str(exc)
            self._is_loaded = False
            self._model = None
            logger.error("Failed to load Faster-Whisper model: %s", exc)
            raise

    def unload_model(self) -> None:
        """Unload the model and trigger garbage collection to free host RAM."""
        if self._model is not None:
            logger.info("Unloading Faster-Whisper model '%s' from memory...", self.model_size)
            del self._model
            self._model = None
            self._is_loaded = False
            gc.collect()

    def transcribe(
        self,
        audio_bytes: bytes,
        *,
        format: str = "wav",
        language: Optional[str] = None,
    ) -> TranscriptionResult:
        """
        Transcribe raw audio bytes to text using Faster-Whisper.
        """
        if not audio_bytes or len(audio_bytes) < 44:
            return TranscriptionResult(text="", duration_seconds=0.0)

        if not self.health():
            raise RuntimeError(f"FasterWhisperSTTProvider is unhealthy: {self._load_error or 'package not installed'}")

        if not self.is_loaded:
            self.load_model()

        t_start = time.perf_counter()
        audio_buf = io.BytesIO(audio_bytes)

        try:
            segments, info = self._model.transcribe(
                audio_buf,
                beam_size=self.beam_size,
                language=language,
                vad_filter=self.vad_filter,
            )

            seg_list: List[TranscriptionSegment] = []
            text_chunks: List[str] = []

            for seg in segments:
                text_clean = seg.text.strip()
                if not text_clean:
                    continue
                # avg_logprob is typically negative log probability
                conf = None
                if hasattr(seg, "avg_logprob") and seg.avg_logprob is not None:
                    try:
                        conf = max(0.0, min(1.0, math.exp(seg.avg_logprob)))
                    except OverflowError:
                        conf = 1.0

                seg_list.append(
                    TranscriptionSegment(
                        text=text_clean,
                        start=round(seg.start, 3),
                        end=round(seg.end, 3),
                        confidence=round(conf, 3) if conf is not None else None,
                    )
                )
                text_chunks.append(text_clean)

            full_text = " ".join(text_chunks).strip()
            total_duration = round(getattr(info, "duration", time.perf_counter() - t_start), 3)
            detected_lang = getattr(info, "language", language)

            # Overall confidence: mean of segment confidences or 1.0
            avg_conf: Optional[float] = None
            confs = [s.confidence for s in seg_list if s.confidence is not None]
            if confs:
                avg_conf = round(sum(confs) / len(confs), 3)

            return TranscriptionResult(
                text=full_text,
                language=detected_lang,
                confidence=avg_conf,
                duration_seconds=total_duration,
                segments=seg_list,
            )

        except Exception as exc:
            logger.warning("Faster-Whisper transcription error: %s", exc)
            return TranscriptionResult(
                text="",
                duration_seconds=round(time.perf_counter() - t_start, 3),
            )
