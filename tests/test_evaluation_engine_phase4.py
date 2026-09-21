"""
Unit tests for Deterministic Evaluation Engine and Evidence Dominance in NR-AI Phase 4.
"""

import unittest

from app.evaluation.evaluator import DeterministicEvaluator
from app.evaluation.models import (
    EvaluationCase,
    EvaluationCategory,
    EvaluationResult,
    EvaluationStatus,
    EvidenceType,
)


class TestEvaluationEnginePhase4(unittest.TestCase):

    def test_evaluation_categories_count(self):
        self.assertEqual(len(EvaluationCategory), 13)
        self.assertEqual(len(EvaluationStatus), 4)

    def test_evidence_dominance_rule(self):
        case = EvaluationCase(
            case_id="TEST-001",
            category=EvaluationCategory.SECURITY_GUARDRAILS,
            title="Check Evidence Dominance",
            description="Model claims pass but produces no evidence",
            expected_criteria={"blocked": True},
        )

        res = DeterministicEvaluator.evaluate_case(
            case=case,
            execution_output={"blocked": False},
            claimed_success=True,
        )

        self.assertEqual(res.status, EvaluationStatus.FAIL)
        self.assertEqual(res.score, 0.0)
        self.assertFalse(res.evidence_verified)

    def test_successful_evidence_verification(self):
        case = EvaluationCase(
            case_id="TEST-002",
            category=EvaluationCategory.COMMAND_ROUTING,
            title="Verify Route Criteria",
            description="Verify target agent route",
            expected_criteria={"route_success": True, "target_agent": "aegis"},
        )

        res = DeterministicEvaluator.evaluate_case(
            case=case,
            execution_output={"route_success": True, "target_agent": "aegis"},
            claimed_success=True,
            execution_time_ms=12.5,
        )

        self.assertEqual(res.status, EvaluationStatus.PASS)
        self.assertEqual(res.score, 1.0)
        self.assertTrue(res.evidence_verified)
        self.assertEqual(len(res.evidence_records), 2)

    def test_exit_code_and_regex_evidence(self):
        case = EvaluationCase(
            case_id="TEST-003",
            category=EvaluationCategory.AGENT_EXECUTION,
            title="Exit Code and Regex",
            description="Check exit code 0 and greeting regex",
            expected_criteria={
                "expected_exit_code": 0,
                "expected_regex": r"SUCCESS:\s+initialized",
            },
        )

        res = DeterministicEvaluator.evaluate_case(
            case=case,
            execution_output={
                "exit_code": 0,
                "output_text": "System boot. SUCCESS:  initialized and ready.",
            },
            claimed_success=True,
        )

        self.assertEqual(res.status, EvaluationStatus.PASS)
        self.assertTrue(res.evidence_verified)

        res_fail = DeterministicEvaluator.evaluate_case(
            case=case,
            execution_output={
                "exit_code": 1,
                "output_text": "System boot. SUCCESS:  initialized and ready.",
            },
            claimed_success=True,
        )

        self.assertEqual(res_fail.status, EvaluationStatus.FAIL)
        self.assertFalse(res_fail.evidence_verified)

    def test_telemetry_latency_evidence(self):
        case = EvaluationCase(
            case_id="TEST-004",
            category=EvaluationCategory.LATENCY_PERFORMANCE,
            title="Latency Bounding",
            description="Ensure latency <= 100ms",
            expected_criteria={"max_latency_ms": 100.0},
        )

        res_ok = DeterministicEvaluator.evaluate_case(
            case=case,
            execution_output={"latency_ms": 45.2},
            claimed_success=True,
        )
        self.assertEqual(res_ok.status, EvaluationStatus.PASS)

        res_slow = DeterministicEvaluator.evaluate_case(
            case=case,
            execution_output={"latency_ms": 150.0},
            claimed_success=True,
        )
        self.assertEqual(res_slow.status, EvaluationStatus.FAIL)

    def test_batch_evaluation(self):
        case1 = EvaluationCase(
            case_id="BATCH-01",
            category=EvaluationCategory.GALAXY_UI,
            title="Node test",
            description="",
            expected_criteria={"rendered": True},
        )
        case2 = EvaluationCase(
            case_id="BATCH-02",
            category=EvaluationCategory.MCP_INTEGRATION,
            title="MCP test",
            description="",
            expected_criteria={"valid_schema": True},
        )

        batch = [
            (case1, {"rendered": True}, True),
            (case2, {"valid_schema": True}, True),
        ]

        results = DeterministicEvaluator.evaluate_batch(batch)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r.status == EvaluationStatus.PASS for r in results))


if __name__ == "__main__":
    unittest.main()
