"""
Voice Performance Telemetry Engine for NR-AI.
OpenJarvis-inspired empirical latency tracking with strict measurement provenance.

Measurement Invariants:
- MEASURED: Monotonically timed via time.perf_counter() with microsecond resolution.
- ESTIMATED: Heuristically calculated from audio sample counts or buffer durations.
- UNAVAILABLE: When the provider, hardware, or pipeline stage cannot produce the metric.
- Never report simulated or synthetic timings as MEASURED.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("NRAI.Voice.Telemetry")


class MetricProvenance(str, Enum):
    """Authoritative measurement provenance tag."""
    MEASURED = "MEASURED"
    ESTIMATED = "ESTIMATED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass
class TelemetryMetric:
    """An individual latency or throughput metric tagged with measurement provenance."""
    name: str
    value_ms: Optional[float]
    provenance: MetricProvenance
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "value_ms": round(self.value_ms, 2) if self.value_ms is not None else None,
            "provenance": self.provenance.value,
            "notes": self.notes,
        }


@dataclass
class VoiceTurnTelemetry:
    """Comprehensive performance telemetry for a single conversational voice turn."""
    turn_id: str
    timestamp: float
    stt_provider: str = "unknown"
    tts_provider: str = "unknown"
    audio_duration_sec: Optional[float] = None

    # Pipeline timings
    stt_latency_ms: Optional[float] = None
    processing_latency_ms: Optional[float] = None
    tts_first_chunk_ms: Optional[float] = None
    tts_total_ms: Optional[float] = None
    total_turnaround_ms: Optional[float] = None
    real_time_factor: Optional[float] = None

    # Provenance tags
    metrics: Dict[str, TelemetryMetric] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "timestamp": self.timestamp,
            "stt_provider": self.stt_provider,
            "tts_provider": self.tts_provider,
            "audio_duration_sec": self.audio_duration_sec,
            "stt_latency_ms": round(self.stt_latency_ms, 2) if self.stt_latency_ms is not None else None,
            "processing_latency_ms": round(self.processing_latency_ms, 2) if self.processing_latency_ms is not None else None,
            "tts_first_chunk_ms": round(self.tts_first_chunk_ms, 2) if self.tts_first_chunk_ms is not None else None,
            "tts_total_ms": round(self.tts_total_ms, 2) if self.tts_total_ms is not None else None,
            "total_turnaround_ms": round(self.total_turnaround_ms, 2) if self.total_turnaround_ms is not None else None,
            "real_time_factor": round(self.real_time_factor, 3) if self.real_time_factor is not None else None,
            "metrics": {k: v.to_dict() for k, v in self.metrics.items()},
        }


class VoicePerformanceTelemetry:
    """
    Precision telemetry collector for voice interaction loops.
    """

    def __init__(self, session_id: Optional[str] = None):
        self.session_id = session_id or str(uuid.uuid4())
        self._history: List[VoiceTurnTelemetry] = []

        # In-flight turn state
        self._current_turn_id: Optional[str] = None
        self._t_turn_start: Optional[float] = None
        self._t_speech_end: Optional[float] = None
        self._t_stt_complete: Optional[float] = None
        self._t_proc_complete: Optional[float] = None
        self._t_tts_first_byte: Optional[float] = None
        self._t_tts_complete: Optional[float] = None

        self._stt_provider_id: str = "unknown"
        self._tts_provider_id: str = "unknown"
        self._input_audio_duration_sec: Optional[float] = None
        self._output_audio_duration_sec: Optional[float] = None

    def start_turn(
        self,
        turn_id: Optional[str] = None,
        stt_provider: str = "unknown",
        tts_provider: str = "unknown",
    ) -> str:
        """Mark start of a new conversational turn."""
        self._current_turn_id = turn_id or str(uuid.uuid4())
        now = time.perf_counter()
        self._t_turn_start = now
        self._t_speech_end = None
        self._t_stt_complete = None
        self._t_proc_complete = None
        self._t_tts_first_byte = None
        self._t_tts_complete = None

        self._stt_provider_id = stt_provider
        self._tts_provider_id = tts_provider
        self._input_audio_duration_sec = None
        self._output_audio_duration_sec = None
        return self._current_turn_id

    def mark_speech_end(self, audio_duration_sec: Optional[float] = None) -> None:
        """User finished speaking (VAD silence threshold reached)."""
        self._t_speech_end = time.perf_counter()
        self._input_audio_duration_sec = audio_duration_sec

    def mark_transcription_complete(self, text: str = "") -> None:
        """Speech-to-Text inference finished."""
        self._t_stt_complete = time.perf_counter()

    def mark_processing_complete(self) -> None:
        """Central Brain / ModelRouter reasoning finished."""
        self._t_proc_complete = time.perf_counter()

    def mark_tts_first_chunk(self) -> None:
        """First synthesized audio chunk or byte emitted (TTFB)."""
        self._t_tts_first_byte = time.perf_counter()

    def mark_tts_complete(self, audio_duration_sec: Optional[float] = None) -> VoiceTurnTelemetry:
        """TTS synthesis completed. Computes metrics and archives turn."""
        now = time.perf_counter()
        self._t_tts_complete = now
        self._output_audio_duration_sec = audio_duration_sec

        turn = VoiceTurnTelemetry(
            turn_id=self._current_turn_id or str(uuid.uuid4()),
            timestamp=time.time(),
            stt_provider=self._stt_provider_id,
            tts_provider=self._tts_provider_id,
            audio_duration_sec=self._input_audio_duration_sec,
        )

        # 1. STT Latency
        if self._t_speech_end and self._t_stt_complete:
            stt_ms = (self._t_stt_complete - self._t_speech_end) * 1000.0
            turn.stt_latency_ms = stt_ms
            turn.metrics["stt_latency"] = TelemetryMetric(
                "stt_latency", stt_ms, MetricProvenance.MEASURED, "End of speech to STT complete"
            )
            # Compute RTF (Real Time Factor) for STT if audio duration is known
            if self._input_audio_duration_sec and self._input_audio_duration_sec > 0:
                rtf = (stt_ms / 1000.0) / self._input_audio_duration_sec
                turn.real_time_factor = rtf
                turn.metrics["stt_rtf"] = TelemetryMetric(
                    "stt_rtf", rtf, MetricProvenance.MEASURED, "Inference time / speech audio duration"
                )
        else:
            turn.metrics["stt_latency"] = TelemetryMetric(
                "stt_latency", None, MetricProvenance.UNAVAILABLE, "Timers not captured"
            )

        # 2. Processing Latency
        if self._t_stt_complete and self._t_proc_complete:
            proc_ms = (self._t_proc_complete - self._t_stt_complete) * 1000.0
            turn.processing_latency_ms = proc_ms
            turn.metrics["processing_latency"] = TelemetryMetric(
                "processing_latency", proc_ms, MetricProvenance.MEASURED, "STT complete to LLM plan complete"
            )

        # 3. TTS First Chunk (TTFB)
        if self._t_proc_complete and self._t_tts_first_byte:
            ttfb_ms = (self._t_tts_first_byte - self._t_proc_complete) * 1000.0
            turn.tts_first_chunk_ms = ttfb_ms
            turn.metrics["tts_first_chunk"] = TelemetryMetric(
                "tts_first_chunk", ttfb_ms, MetricProvenance.MEASURED, "Plan complete to first TTS audio byte"
            )

        # 4. TTS Total Duration
        if self._t_proc_complete and self._t_tts_complete:
            tts_total_ms = (self._t_tts_complete - self._t_proc_complete) * 1000.0
            turn.tts_total_ms = tts_total_ms
            turn.metrics["tts_total"] = TelemetryMetric(
                "tts_total", tts_total_ms, MetricProvenance.MEASURED, "Plan complete to full audio synthesis"
            )

        # 5. Total Roundtrip Turnaround (Speech End to Sound Emitter)
        emitter_point = self._t_tts_first_byte or self._t_tts_complete
        if self._t_speech_end and emitter_point:
            total_turnaround = (emitter_point - self._t_speech_end) * 1000.0
            turn.total_turnaround_ms = total_turnaround
            turn.metrics["total_turnaround"] = TelemetryMetric(
                "total_turnaround", total_turnaround, MetricProvenance.MEASURED, "User speech end to initial audio output"
            )

        self._history.append(turn)
        logger.info(
            "Voice turn completed: STT=%.1fms, Turnaround=%.1fms, STT_Provider=%s, TTS_Provider=%s",
            turn.stt_latency_ms or 0.0,
            turn.total_turnaround_ms or 0.0,
            turn.stt_provider,
            turn.tts_provider,
        )
        return turn

    def get_history(self) -> List[Dict[str, Any]]:
        """Return history of all completed voice turns."""
        return [t.to_dict() for t in self._history]
