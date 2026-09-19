"""
Tests for Droid Phase 3: Security & Safety Policy Enforcements.
"""

import unittest
from unittest.mock import MagicMock
from app.agent.android_safety import AndroidSafetyGate, AndroidSafetyError, EmergencyStopActiveError
from app.agent.android_device_lifecycle import DeviceLifecycleController, BootConfiguration
from app.agent.android_reproduction import FailureReproductionEngine
from app.agent.android_repair_orchestrator import AutonomousRepairOrchestrator


class TestDroidPhase3Security(unittest.TestCase):

    def setUp(self):
        self.safety = AndroidSafetyGate()

    def test_blocked_avd_pixel_6_api_35(self):
        controller = DeviceLifecycleController(safety_gate=self.safety)
        with self.assertRaises(AndroidSafetyError):
            controller.boot(BootConfiguration(avd_name="Pixel_6_API_35"))

    def test_emergency_stop_halts_reproduction(self):
        self.safety.trigger_emergency_stop("Security test")
        engine = FailureReproductionEngine(safety_gate=self.safety)
        with self.assertRaises(EmergencyStopActiveError):
            engine.create_plan_from_description("Crash test")
        self.safety.reset_emergency_stop()

    def test_emergency_stop_halts_repair(self):
        self.safety.trigger_emergency_stop("Security test")
        orchestrator = AutonomousRepairOrchestrator(safety_gate=self.safety)
        with self.assertRaises(EmergencyStopActiveError):
            orchestrator.execute_repair(MagicMock())
        self.safety.reset_emergency_stop()

    def test_unauthorized_project_rejected(self):
        with self.assertRaises(AndroidSafetyError):
            self.safety.verify_project_path(r"C:\Windows\System32")


if __name__ == "__main__":
    unittest.main()
