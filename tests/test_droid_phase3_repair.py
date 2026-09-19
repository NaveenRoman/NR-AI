"""
Tests for Droid Phase 3: Autonomous Bounded Repair Orchestrator.
"""

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from app.agent.android_repair_orchestrator import (
    AutonomousRepairOrchestrator,
    RepairProposal,
    RepairExecutionResult,
    RepairOperation,
)
from app.agent.android_safety import AndroidSafetyGate


class TestDroidPhase3Repair(unittest.TestCase):

    def setUp(self):
        self.safety = AndroidSafetyGate()
        self.orchestrator = AutonomousRepairOrchestrator(safety_gate=self.safety)

    def test_max_repair_attempts_enforced(self):
        task_id = "T-BOUNDED-REPAIR"
        p = Path(r"C:\NR-AI\nr_android_test\app\src\main\java\com\nrai\test\MainActivity.kt")
        curr_hash = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else ""

        prop = RepairProposal(
            file_path=str(p),
            target_sha256=curr_hash,
            operation=RepairOperation.REPLACE_EXACT,
            replacement="// fix",
            reason="Test max attempts",
            task_id=task_id,
        )

        self.orchestrator.code_repair.apply_edit = MagicMock(return_value=MagicMock(success=False, error="Simulated fail", error_code="FAIL"))

        # Attempt 1
        res1 = self.orchestrator.execute_repair(prop, validate_build=False)
        self.assertEqual(res1.attempt_number, 1)

        # Attempt 2
        res2 = self.orchestrator.execute_repair(prop, validate_build=False)
        self.assertEqual(res2.attempt_number, 2)

        # Attempt 3 -> strictly rejected
        res3 = self.orchestrator.execute_repair(prop, validate_build=False)
        self.assertFalse(res3.success)
        self.assertEqual(res3.error, "MAX_REPAIR_ATTEMPTS_EXCEEDED")

    def test_validate_proposal_target_hash_mismatch(self):
        p = Path(r"C:\NR-AI\nr_android_test\app\src\main\java\com\nrai\test\MainActivity.kt")
        prop = RepairProposal(
            file_path=str(p),
            target_sha256="0000000000000000000000000000000000000000000000000000000000000000",
            operation=RepairOperation.REPLACE_EXACT,
            replacement="// stale",
            reason="Test stale detection",
        )
        valid, errs = self.orchestrator.validate_proposal_schema(prop)
        self.assertFalse(valid)
        self.assertTrue(any("STALE_TARGET" in e for e in errs))


if __name__ == "__main__":
    unittest.main()
