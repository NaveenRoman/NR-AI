"""
Tests for ClapDetector: Local audio transient analysis, crest factor, debounce, emergency stop.
"""

import unittest
from app.voice.clap_detector import ClapDetector, ClapSensitivity
from app.remote.emergency import EmergencyStopController


class TestClapDetector(unittest.TestCase):

    def setUp(self):
        self.estop = EmergencyStopController()
        self.clap = ClapDetector(emergency_stop=self.estop)

    def test_clap_detected_on_sharp_transient(self):
        # Synthetic clap: silence -> sharp peak (0.95) -> rapid decay
        samples = [0.01] * 20 + [0.95] + [0.02] * 40 + [0.005] * 40
        res = self.clap.process_audio_samples(samples)
        self.assertTrue(res.detected)
        self.assertEqual(res.reason, "CLAP_DETECTED")
        self.assertGreater(res.crest_factor, 3.2)
        self.assertGreater(res.decay_ratio, 0.5)

    def test_continuous_noise_rejected(self):
        # Continuous noise (speech / hum): consistent amplitude, low crest factor
        samples = [0.45, -0.42, 0.44, -0.43] * 25
        res = self.clap.process_audio_samples(samples)
        self.assertFalse(res.detected)
        self.assertEqual(res.reason, "CRITERIA_NOT_MET")

    def test_debounce_cooldown(self):
        samples = [0.01] * 20 + [0.95] + [0.02] * 40 + [0.005] * 40
        res1 = self.clap.process_audio_samples(samples)
        self.assertTrue(res1.detected)

        # Immediate follow-up within 1.5s cooldown
        res2 = self.clap.process_audio_samples(samples)
        self.assertFalse(res2.detected)
        self.assertEqual(res2.reason, "COOLDOWN_ACTIVE")

    def test_emergency_stop_halts_detection(self):
        self.estop.trigger(triggered_by="Unit_Test", reason="Testing E-Stop")
        samples = [0.01] * 20 + [0.95] + [0.02] * 40 + [0.005] * 40
        res = self.clap.process_audio_samples(samples)
        self.assertFalse(res.detected)
        self.assertEqual(res.reason, "EMERGENCY_STOP_ACTIVE")
        self.estop.reset()


if __name__ == "__main__":
    unittest.main()
