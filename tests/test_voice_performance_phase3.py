"""
Unit tests for VoicePerformanceTelemetry.
Phase 3 Local Voice Intelligence - Latency and measurement provenance verification.
"""

from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from app.voice.telemetry import (
    VoicePerformanceTelemetry,
    MetricProvenance,
    VoiceTurnTelemetry,
)


class TestVoicePerformancePhase3(unittest.TestCase):

    def test_01_single_turn_lifecycle_and_timings(self):
        telem = VoicePerformanceTelemetry()
        turn_id = telem.start_turn(stt_provider="faster-whisper", tts_provider="sapi5")
        self.assertIsNotNone(turn_id)

        # Simulate pipeline events with small delays
        t0 = time.perf_counter()
        with patch("time.perf_counter", side_effect=[
            t0,        # start_turn
            t0 + 0.1,  # speech_end (100ms)
            t0 + 0.3,  # stt_complete (+200ms -> stt_latency = 200ms)
            t0 + 0.4,  # proc_complete (+100ms -> proc_latency = 100ms)
            t0 + 0.45, # tts_first_byte (+50ms -> ttfb = 50ms)
            t0 + 0.65, # tts_complete (+250ms -> tts_total = 250ms)
        ]):
            telem.start_turn(turn_id="turn-1", stt_provider="faster-whisper", tts_provider="sapi5")
            telem.mark_speech_end(audio_duration_sec=2.0)
            telem.mark_transcription_complete("test command")
            telem.mark_processing_complete()
            telem.mark_tts_first_chunk()
            turn = telem.mark_tts_complete(audio_duration_sec=1.5)

        self.assertEqual(turn.turn_id, "turn-1")
        self.assertEqual(turn.stt_provider, "faster-whisper")
        self.assertEqual(turn.tts_provider, "sapi5")

        # Check calculated latencies
        self.assertAlmostEqual(turn.stt_latency_ms or 0.0, 200.0, places=1)
        self.assertAlmostEqual(turn.processing_latency_ms or 0.0, 100.0, places=1)
        self.assertAlmostEqual(turn.tts_first_chunk_ms or 0.0, 50.0, places=1)
        self.assertAlmostEqual(turn.tts_total_ms or 0.0, 250.0, places=1)
        # Total turnaround: speech end (t0 + 0.1) to tts_first_byte (t0 + 0.45) = 350ms
        self.assertAlmostEqual(turn.total_turnaround_ms or 0.0, 350.0, places=1)

        # Real time factor: 200ms STT inference / 2.0s audio duration = 0.1
        self.assertAlmostEqual(turn.real_time_factor or 0.0, 0.1, places=2)

    def test_02_provenance_tagging(self):
        telem = VoicePerformanceTelemetry()
        t0 = time.perf_counter()
        with patch("time.perf_counter", side_effect=[
            t0,
            t0 + 0.1,
            t0 + 0.25,
            t0 + 0.35,
            t0 + 0.4,
            t0 + 0.5,
        ]):
            telem.start_turn()
            telem.mark_speech_end()
            telem.mark_transcription_complete()
            telem.mark_processing_complete()
            telem.mark_tts_first_chunk()
            turn = telem.mark_tts_complete()

        # All completed steps should have MEASURED provenance
        self.assertEqual(turn.metrics["stt_latency"].provenance, MetricProvenance.MEASURED)
        self.assertEqual(turn.metrics["processing_latency"].provenance, MetricProvenance.MEASURED)
        self.assertEqual(turn.metrics["tts_first_chunk"].provenance, MetricProvenance.MEASURED)
        self.assertEqual(turn.metrics["total_turnaround"].provenance, MetricProvenance.MEASURED)

    def test_03_unavailable_provenance_when_stage_skipped(self):
        telem = VoicePerformanceTelemetry()
        telem.start_turn()
        # Skipped speech_end and STT complete (e.g. text command entered directly)
        telem.mark_processing_complete()
        turn = telem.mark_tts_complete()

        self.assertEqual(turn.metrics["stt_latency"].provenance, MetricProvenance.UNAVAILABLE)
        self.assertIsNone(turn.metrics["stt_latency"].value_ms)

    def test_04_serialization_to_dict(self):
        telem = VoicePerformanceTelemetry()
        telem.start_turn(turn_id="turn-json")
        telem.mark_speech_end()
        telem.mark_transcription_complete()
        telem.mark_processing_complete()
        turn = telem.mark_tts_complete()

        data = turn.to_dict()
        self.assertIsInstance(data, dict)
        self.assertEqual(data["turn_id"], "turn-json")
        self.assertIn("metrics", data)
        self.assertIn("timestamp", data)

    def test_05_multi_turn_history(self):
        telem = VoicePerformanceTelemetry()
        telem.start_turn(turn_id="turn-1")
        telem.mark_tts_complete()

        telem.start_turn(turn_id="turn-2")
        telem.mark_tts_complete()

        history = telem.get_history()
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["turn_id"], "turn-1")
        self.assertEqual(history[1]["turn_id"], "turn-2")


if __name__ == "__main__":
    unittest.main()
