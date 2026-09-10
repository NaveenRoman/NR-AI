"""
Tests for NR AI 10-Agent Multi-Model Orchestration System.
"""

import shutil
import tempfile
import time
import unittest
from pathlib import Path

from app.agent.agent_slot import AgentSlot, SlotManager
from app.agent.concurrency_manager import (
    LockAcquisitionError,
    ResourceLockManager,
)
from app.agent.consensus_engine import (
    ConsensusDecision,
    ConsensusEngine,
    DecisionCase,
    ExecutionEvidence,
    VerificationReview,
)
from app.agent.multi_agent_orchestrator import (
    MultiAgentOrchestrator,
    OrchestratorTask,
    TaskStatus,
)
from app.config.model_config import (
    GEMINI_3_6_FLASH,
    GPT_5_6_SOL,
    GPT_6_ASTRA,
    PROVIDER_GOOGLE,
    PROVIDER_OPENAI,
)


class TestAgentSlots(unittest.TestCase):
    def setUp(self):
        self.mgr = SlotManager()

    def test_exactly_10_slots_created(self):
        slots = self.mgr.get_all_slots()
        self.assertEqual(len(slots), 10)
        slot_ids = [s.slot_id for s in slots]
        self.assertEqual(slot_ids, list(range(1, 11)))

    def test_slot_roles_and_preferred_models(self):
        slot1 = self.mgr.get_slot(1)
        self.assertIsNotNone(slot1)
        self.assertEqual(slot1.preferred_model, GPT_6_ASTRA)
        self.assertIn("Architect", slot1.role)

        slot5 = self.mgr.get_slot(5)
        self.assertIsNotNone(slot5)
        self.assertEqual(slot5.preferred_model, GEMINI_3_6_FLASH)
        self.assertEqual(slot5.provider, PROVIDER_GOOGLE)

        slot6 = self.mgr.get_slot(6)
        self.assertIsNotNone(slot6)
        self.assertEqual(slot6.preferred_model, GPT_5_6_SOL)

        slot10 = self.mgr.get_slot(10)
        self.assertIsNotNone(slot10)
        self.assertIn("Quality Gate", slot10.role)

    def test_fallback_assigned_when_live_api_unverified(self):
        for slot in self.mgr.get_all_slots():
            self.assertTrue(bool(slot.active_model))
            # If live API is not verified, fallback_reason must be populated
            if not slot.is_live_api_verified:
                self.assertIsNotNone(slot.fallback_reason)

    def test_slot_assignment_and_release(self):
        slot = self.mgr.get_idle_slot()
        self.assertIsNotNone(slot)
        assigned = self.mgr.assign_task_to_slot(slot.slot_id, "test-task-1")
        self.assertTrue(assigned)
        self.assertEqual(slot.status, "BUSY")

        self.mgr.release_slot(slot.slot_id, success=True)
        self.assertEqual(slot.status, "IDLE")
        self.assertEqual(slot.tasks_completed, 1)


class TestConcurrencyManager(unittest.TestCase):
    def setUp(self):
        self.lock_mgr = ResourceLockManager(default_timeout=0.5)

    def test_concurrent_read_locks(self):
        # Multiple tasks can hold read locks on the same file
        self.assertTrue(self.lock_mgr.acquire_read_lock("shared_file.py", "task-1"))
        self.assertTrue(self.lock_mgr.acquire_read_lock("shared_file.py", "task-2"))
        self.lock_mgr.release_read_lock("shared_file.py", "task-1")
        self.lock_mgr.release_read_lock("shared_file.py", "task-2")

    def test_exclusive_write_lock_blocks_second_writer(self):
        self.assertTrue(self.lock_mgr.acquire_write_lock("exclusive.py", "task-1"))
        with self.assertRaises(LockAcquisitionError):
            self.lock_mgr.acquire_write_lock("exclusive.py", "task-2", timeout=0.2)
        self.lock_mgr.release_write_lock("exclusive.py", "task-1")

        # Now task-2 can acquire it
        self.assertTrue(self.lock_mgr.acquire_write_lock("exclusive.py", "task-2"))
        self.lock_mgr.release_write_lock("exclusive.py", "task-2")

    def test_conflict_detection(self):
        self.lock_mgr.acquire_write_lock("conflict.py", "task-writer")
        conflict = self.lock_mgr.detect_conflict("conflict.py", "task-reader", is_write=False)
        self.assertIsNotNone(conflict)
        self.assertIn("task-writer", conflict)
        self.lock_mgr.release_write_lock("conflict.py", "task-writer")

    def test_release_all_for_task(self):
        self.lock_mgr.acquire_write_lock("f1.py", "task-crash")
        self.lock_mgr.acquire_read_lock("f2.py", "task-crash")
        released = self.lock_mgr.release_all_for_task("task-crash")
        self.assertEqual(released, 2)
        # Should now be free
        self.assertTrue(self.lock_mgr.acquire_write_lock("f1.py", "task-clean"))
        self.lock_mgr.release_write_lock("f1.py", "task-clean")


class TestConsensusEngine(unittest.TestCase):
    def setUp(self):
        self.engine = ConsensusEngine()

    def test_case_a_accept(self):
        rev1 = VerificationReview(slot_id=6, verifier_name="Reviewer1", model_id=GPT_5_6_SOL, passed=True)
        rev2 = VerificationReview(slot_id=5, verifier_name="Reviewer2", model_id=GEMINI_3_6_FLASH, passed=True)
        ev = ExecutionEvidence(command=["test"], exit_code=0, stdout="OK", stderr="")
        report = self.engine.evaluate(rev1, rev2, ev)
        self.assertEqual(report.decision, ConsensusDecision.ACCEPT)
        self.assertEqual(report.case, DecisionCase.CASE_A)

    def test_case_b_investigate_on_disagreement(self):
        rev1 = VerificationReview(slot_id=6, verifier_name="Reviewer1", model_id=GPT_5_6_SOL, passed=True)
        rev2 = VerificationReview(slot_id=5, verifier_name="Reviewer2", model_id=GEMINI_3_6_FLASH, passed=False, objections=["Edge case unhandled"])
        ev = ExecutionEvidence(command=["test"], exit_code=0, stdout="OK", stderr="")
        report = self.engine.evaluate(rev1, rev2, ev)
        self.assertEqual(report.decision, ConsensusDecision.INVESTIGATE)
        self.assertEqual(report.case, DecisionCase.CASE_B)
        self.assertIn("Edge case unhandled", report.objections)

    def test_case_c_reject_on_both_fail(self):
        rev1 = VerificationReview(slot_id=6, verifier_name="Reviewer1", model_id=GPT_5_6_SOL, passed=False, objections=["Bad architecture"])
        rev2 = VerificationReview(slot_id=5, verifier_name="Reviewer2", model_id=GEMINI_3_6_FLASH, passed=False, objections=["Security issue"])
        ev = ExecutionEvidence(command=["test"], exit_code=0, stdout="OK", stderr="")
        report = self.engine.evaluate(rev1, rev2, ev)
        self.assertEqual(report.decision, ConsensusDecision.REJECT)
        self.assertEqual(report.case, DecisionCase.CASE_C)

    def test_case_d_reject_when_real_test_fails_even_if_llms_pass(self):
        rev1 = VerificationReview(slot_id=6, verifier_name="Reviewer1", model_id=GPT_5_6_SOL, passed=True)
        rev2 = VerificationReview(slot_id=5, verifier_name="Reviewer2", model_id=GEMINI_3_6_FLASH, passed=True)
        # Real test failed with exit code 1!
        ev = ExecutionEvidence(command=["test"], exit_code=1, stdout="", stderr="ZeroDivisionError: division by zero", tests_failed=1)
        report = self.engine.evaluate(rev1, rev2, ev)
        self.assertEqual(report.decision, ConsensusDecision.REJECT)
        self.assertEqual(report.case, DecisionCase.CASE_D)
        self.assertTrue(any("Real tool execution failed" in obj for obj in report.objections))


class TestMultiAgentOrchestrator(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="nrai_orch_test_")
        self.orchestrator = MultiAgentOrchestrator(workspace=self.temp_dir)

    def tearDown(self):
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    def test_concurrent_execution_of_10_tasks(self):
        tasks = []
        for i in range(1, 11):
            tasks.append(
                OrchestratorTask(
                    task_id=f"concurrent-task-{i:02d}",
                    name=f"Worker Task {i}",
                    task_type="code",
                    target_file=f"task_{i}.py",
                    initial_code=f"# Task {i}\nprint('Task {i} complete')\n",
                    assigned_slot_id=i,
                )
            )

        start = time.time()
        results = self.orchestrator.run_tasks_concurrently(tasks, timeout=30.0)
        elapsed = time.time() - start

        self.assertEqual(len(results), 10)
        for r in results:
            self.assertEqual(r.status, TaskStatus.COMPLETED)
            self.assertIsNotNone(r.evidence)
            self.assertEqual(r.evidence.exit_code, 0)
            self.assertEqual(r.consensus_report.decision, ConsensusDecision.ACCEPT)

        # Confirm all 10 files exist and run
        for i in range(1, 11):
            file_path = Path(self.temp_dir) / f"task_{i}.py"
            self.assertTrue(file_path.exists())

    def test_controlled_failure_and_self_healing_recovery(self):
        # Inject intentional syntax error missing colon
        buggy_code = "def calculate_total(x, y)\n    return x + y\n\nif __name__ == '__main__':\n    print(calculate_total(10, 20))\n"

        task = OrchestratorTask(
            task_id="recovery-test-01",
            name="Calculate Total Recovery Test",
            task_type="code",
            target_file="calculate.py",
            initial_code=buggy_code,
            max_retries=3,
        )

        results = self.orchestrator.run_tasks_concurrently([task], timeout=20.0)
        res = results[0]

        # Must recover successfully after 1 retry
        self.assertEqual(res.status, TaskStatus.COMPLETED)
        self.assertGreaterEqual(res.retry_count, 1)
        self.assertEqual(res.consensus_report.decision, ConsensusDecision.ACCEPT)
        self.assertEqual(res.evidence.exit_code, 0)
        self.assertIn("30", res.evidence.stdout)


if __name__ == "__main__":
    unittest.main()
