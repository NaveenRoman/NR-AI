"""
Unit tests for BargeInController.
Phase 3 Local Voice Intelligence - Instant playback cancellation and onset audio capture.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app.voice.barge_in import BargeInController


class TestVoiceBargeInPhase3(unittest.TestCase):

    def test_01_init_state(self):
        controller = BargeInController(barge_in_threshold=500.0)
        self.assertFalse(controller.is_speaking)
        self.assertFalse(controller.was_interrupted)
        self.assertEqual(controller.interruption_count, 0)
        self.assertEqual(controller.get_onset_audio(), b"")

    def test_02_speech_playback_start_and_stop(self):
        controller = BargeInController()
        controller.start_speaking()
        self.assertTrue(controller.is_speaking)
        self.assertFalse(controller.was_interrupted)

        controller.stop_speaking()
        self.assertFalse(controller.is_speaking)

    def test_03_no_interruption_when_not_speaking(self):
        callback = MagicMock()
        controller = BargeInController(barge_in_threshold=500.0, stop_playback_callback=callback)

        # Chunk with high energy while NOT speaking should not trigger interruption
        interrupted = controller.evaluate_incoming_chunk(b"loud_noise", chunk_energy=1000.0)
        self.assertFalse(interrupted)
        self.assertFalse(controller.was_interrupted)
        callback.assert_not_called()

    def test_04_no_interruption_for_low_energy_chunk(self):
        callback = MagicMock()
        controller = BargeInController(barge_in_threshold=500.0, stop_playback_callback=callback)
        controller.start_speaking()

        interrupted = controller.evaluate_incoming_chunk(b"quiet_noise", chunk_energy=200.0)
        self.assertFalse(interrupted)
        self.assertTrue(controller.is_speaking)
        self.assertFalse(controller.was_interrupted)
        callback.assert_not_called()

    def test_05_barge_in_trigger_and_onset_capture(self):
        callback = MagicMock()
        controller = BargeInController(barge_in_threshold=500.0, stop_playback_callback=callback)
        controller.start_speaking()

        onset_bytes = b"user_said_hey_stop"
        interrupted = controller.evaluate_incoming_chunk(onset_bytes, chunk_energy=850.0)

        self.assertTrue(interrupted)
        self.assertFalse(controller.is_speaking)
        self.assertTrue(controller.was_interrupted)
        self.assertEqual(controller.interruption_count, 1)
        callback.assert_called_once()
        self.assertEqual(controller.get_onset_audio(), onset_bytes)

    def test_06_callback_exception_safety(self):
        failing_callback = MagicMock(side_effect=RuntimeError("Audio hardware error"))
        controller = BargeInController(stop_playback_callback=failing_callback)
        controller.start_speaking()

        # Should not raise exception
        interrupted = controller.trigger_interruption(reason="test_error_safety")
        self.assertTrue(interrupted)
        self.assertTrue(controller.was_interrupted)

    def test_07_reset(self):
        controller = BargeInController()
        controller.start_speaking()
        controller.trigger_interruption("manual", onset_chunk=b"chunk")

        self.assertTrue(controller.was_interrupted)
        self.assertEqual(controller.get_onset_audio(), b"chunk")

        controller.reset()
        self.assertFalse(controller.was_interrupted)
        self.assertFalse(controller.is_speaking)
        self.assertEqual(controller.get_onset_audio(), b"")


if __name__ == "__main__":
    unittest.main()
