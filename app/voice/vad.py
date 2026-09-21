"""
Voice Activity Detection (VAD) Engine for NR-AI.
OpenJarvis-inspired bounded, calibrated acoustic activity detector.

Key Features:
- Ambient noise calibration: dynamic speech threshold computation.
- Bounded timing constraints: 1.5s trailing silence timeout, 15.0s max utterance cutoff.
- Audio buffering with pre-speech and speech ring-buffer preservation.
- Zero external native C library dependency: operates on standard PCM audio chunks.
"""

from __future__ import annotations

import audioop
import logging
import math
import struct
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

logger = logging.getLogger("NRAI.Voice.VAD")


class VADState(str, Enum):
    """Acoustic speech activity state."""
    SILENCE = "SILENCE"
    SPEECH_STARTING = "SPEECH_STARTING"
    SPEECH = "SPEECH"
    SPEECH_ENDING = "SPEECH_ENDING"


@dataclass
class VADResult:
    """Frame-level or chunk-level voice activity evaluation result."""
    is_speech: bool
    state: VADState
    rms_energy: float
    threshold: float
    speech_duration_sec: float
    silence_duration_sec: float
    is_utterance_complete: bool = False
    is_max_cutoff_reached: bool = False


class VoiceActivityDetector:
    """
    Robust Voice Activity Detector with dynamic calibration and utterance bounds.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        sample_width: int = 2,  # 16-bit PCM = 2 bytes
        silence_timeout_sec: float = 1.5,
        max_utterance_sec: float = 15.0,
        min_speech_duration_sec: float = 0.25,
        default_energy_threshold: float = 500.0,  # Integer PCM RMS amplitude
        calibration_multiplier: float = 2.5,
    ):
        self.sample_rate = sample_rate
        self.sample_width = sample_width
        self.silence_timeout_sec = silence_timeout_sec
        self.max_utterance_sec = max_utterance_sec
        self.min_speech_duration_sec = min_speech_duration_sec
        self.energy_threshold = default_energy_threshold
        self.calibration_multiplier = calibration_multiplier

        self._state: VADState = VADState.SILENCE
        self._speech_frames_count: int = 0
        self._silence_frames_count: int = 0
        self._utterance_chunks: List[bytes] = []
        self._pre_speech_ring_buffer: List[bytes] = []
        self._max_pre_speech_chunks: int = 5  # ~200ms pre-roll

        self._utterance_start_time: Optional[float] = None
        self._last_speech_time: Optional[float] = None
        self._calibrated: bool = False

    @property
    def is_calibrated(self) -> bool:
        return self._calibrated

    @property
    def current_state(self) -> VADState:
        return self._state

    def calibrate(self, ambient_pcm_bytes: bytes) -> float:
        """
        Calibrate energy threshold using an ambient background noise sample.
        """
        if not ambient_pcm_bytes:
            return self.energy_threshold

        try:
            ambient_rms = float(audioop.rms(ambient_pcm_bytes, self.sample_width))
        except Exception:
            # Fallback manual calculation if audioop fails
            ambient_rms = self._calculate_manual_rms(ambient_pcm_bytes)

        # Baseline noise floor minimum of 150.0 to avoid hair-trigger trips
        calibrated_threshold = max(250.0, ambient_rms * self.calibration_multiplier)
        self.energy_threshold = calibrated_threshold
        self._calibrated = True
        logger.info(
            "VAD calibrated: ambient_rms=%.1f -> threshold=%.1f",
            ambient_rms,
            self.energy_threshold,
        )
        return self.energy_threshold

    def _calculate_manual_rms(self, pcm_bytes: bytes) -> float:
        """Manual 16-bit integer PCM RMS calculation fallback."""
        num_samples = len(pcm_bytes) // self.sample_width
        if num_samples == 0:
            return 0.0
        fmt = f"<{num_samples}h" if self.sample_width == 2 else f"<{num_samples}b"
        try:
            samples = struct.unpack(fmt, pcm_bytes[:num_samples * self.sample_width])
            sum_sq = sum(s * s for s in samples)
            return math.sqrt(sum_sq / num_samples)
        except Exception:
            return 0.0

    def compute_energy(self, chunk_bytes: bytes) -> float:
        """Compute RMS energy of a PCM chunk."""
        if not chunk_bytes:
            return 0.0
        try:
            return float(audioop.rms(chunk_bytes, self.sample_width))
        except Exception:
            return self._calculate_manual_rms(chunk_bytes)

    def process_chunk(self, chunk_bytes: bytes) -> VADResult:
        """
        Ingest a raw PCM chunk, evaluate voice activity, and manage utterance state.
        """
        if not chunk_bytes:
            return VADResult(
                is_speech=False,
                state=self._state,
                rms_energy=0.0,
                threshold=self.energy_threshold,
                speech_duration_sec=0.0,
                silence_duration_sec=0.0,
            )

        energy = self.compute_energy(chunk_bytes)
        is_speech_frame = energy >= self.energy_threshold
        now = time.perf_counter()

        chunk_duration_sec = (len(chunk_bytes) / self.sample_width) / float(self.sample_rate)
        is_utterance_complete = False
        is_max_cutoff_reached = False

        if self._state == VADState.SILENCE:
            if is_speech_frame:
                self._speech_frames_count += 1
                self._state = VADState.SPEECH_STARTING
                self._utterance_chunks.extend(self._pre_speech_ring_buffer)
                self._utterance_chunks.append(chunk_bytes)
                self._utterance_start_time = now
                self._last_speech_time = now
            else:
                # Maintain bounded pre-speech ring buffer
                self._pre_speech_ring_buffer.append(chunk_bytes)
                if len(self._pre_speech_ring_buffer) > self._max_pre_speech_chunks:
                    self._pre_speech_ring_buffer.pop(0)

        elif self._state == VADState.SPEECH_STARTING:
            self._utterance_chunks.append(chunk_bytes)
            if is_speech_frame:
                self._speech_frames_count += 1
                self._last_speech_time = now
                duration_so_far = (self._speech_frames_count * chunk_duration_sec)
                if duration_so_far >= self.min_speech_duration_sec:
                    self._state = VADState.SPEECH
            else:
                self._silence_frames_count += 1
                if (self._silence_frames_count * chunk_duration_sec) > 0.4:
                    # False trigger or brief blip — reset back to silence
                    self._state = VADState.SILENCE
                    self._speech_frames_count = 0
                    self._silence_frames_count = 0
                    self._utterance_chunks.clear()

        elif self._state == VADState.SPEECH:
            self._utterance_chunks.append(chunk_bytes)
            if is_speech_frame:
                self._last_speech_time = now
                self._silence_frames_count = 0
            else:
                self._silence_frames_count += 1
                self._state = VADState.SPEECH_ENDING

        elif self._state == VADState.SPEECH_ENDING:
            self._utterance_chunks.append(chunk_bytes)
            if is_speech_frame:
                # User resumed speaking
                self._state = VADState.SPEECH
                self._last_speech_time = now
                self._silence_frames_count = 0
            else:
                self._silence_frames_count += 1
                silence_elapsed = now - (self._last_speech_time or now)
                if silence_elapsed >= self.silence_timeout_sec:
                    is_utterance_complete = True
                    self._state = VADState.SILENCE

        # Check maximum utterance duration bound
        total_utterance_sec = 0.0
        if self._utterance_start_time:
            total_utterance_sec = now - self._utterance_start_time
            if total_utterance_sec >= self.max_utterance_sec:
                is_max_cutoff_reached = True
                is_utterance_complete = True
                self._state = VADState.SILENCE
                logger.info(
                    "Utterance reached max cutoff bound (%.2fs >= %.2fs). Closing speech window.",
                    total_utterance_sec,
                    self.max_utterance_sec,
                )

        silence_dur = (now - self._last_speech_time) if self._last_speech_time else 0.0

        return VADResult(
            is_speech=is_speech_frame,
            state=self._state,
            rms_energy=round(energy, 2),
            threshold=round(self.energy_threshold, 2),
            speech_duration_sec=round(total_utterance_sec, 3),
            silence_duration_sec=round(silence_dur, 3),
            is_utterance_complete=is_utterance_complete,
            is_max_cutoff_reached=is_max_cutoff_reached,
        )

    def get_buffered_utterance_bytes(self) -> bytes:
        """Retrieve all audio bytes accumulated for the current utterance."""
        return b"".join(self._utterance_chunks)

    def reset(self) -> None:
        """Reset state machine and clear all audio chunk buffers."""
        self._state = VADState.SILENCE
        self._speech_frames_count = 0
        self._silence_frames_count = 0
        self._utterance_chunks.clear()
        self._pre_speech_ring_buffer.clear()
        self._utterance_start_time = None
        self._last_speech_time = None
