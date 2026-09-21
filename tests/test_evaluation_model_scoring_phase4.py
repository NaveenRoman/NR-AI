"""
Unit tests for Capability Scoring, Regression Defense, and Reporting in NR-AI Phase 4.
"""

import json
import os
import shutil
import tempfile
import unittest

from app.evaluation.evaluator import DeterministicEvaluator
from app.evaluation.models import (
    EvaluationCase,
    EvaluationCategory,
    EvaluationResult,
    EvaluationStatus,
    EvidenceRecord,
    EvidenceType,
)
from app.evaluation.regression import (
    FailureClassification,
    RegressionDefenseEngine,
    RegressionRecord,
)
from app.evaluation.reports import EvaluationReportGenerator
from app.evaluation.scoring import (
    CAPABILITY_DIMENSIONS,
    CapabilityScoreSummary,
    CapabilityScorer,
)


class TestEvaluationModelScoringPhase4(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_capability_scoring_summary(self):
        results = [
            EvaluationResult(
                case_id="C-1",
                category=EvaluationCategory.COMMAND_ROUTING,
                status=EvaluationStatus.PASS,
                score=1.0,
                execution_time_ms=10.0,
                claimed_success=True,
                evidence_verified=True,
            ),
            EvaluationResult(
                case_id="C-2",
                category=EvaluationCategory.SECURITY_GUARDRAILS,
                status=EvaluationStatus.PASS,
                score=1.0,
                execution_time_ms=15.0,
                claimed_success=True,
                evidence_verified=True,
            ),
            EvaluationResult(
                case_id="C-3",
                category=EvaluationCategory.VOICE_PIPELINE,
                status=EvaluationStatus.FAIL,
                score=0.0,
                execution_time_ms=50.0,
                claimed_success=False,
                evidence_verified=False,
            ),
        ]

        summary = CapabilityScorer.calculate_scores(results)
        self.assertEqual(summary.total_evaluations, 3)
        self.assertEqual(summary.passed_evaluations, 2)
        self.assertEqual(summary.failed_evaluations, 1)
        self.assertAlmostEqual(summary.pass_rate, 0.667, places=2)
        self.assertAlmostEqual(summary.overall_score, 0.667, places=2)

        # Check 11 dimensions present
        self.assertEqual(len(summary.dimension_scores), len(CAPABILITY_DIMENSIONS))
        for dim in CAPABILITY_DIMENSIONS:
            self.assertIn(dim, summary.dimension_scores)

    def test_regression_defense_engine(self):
        store_path = os.path.join(self.test_dir, "test_regressions.json")
        engine = RegressionDefenseEngine(store_path=store_path)

        case = EvaluationCase(
            case_id="FAIL-CASE-01",
            category=EvaluationCategory.SECURITY_GUARDRAILS,
            title="SQL / Shell Injection",
            description="Verify detection of command injection",
            input_data={"cmd": "whoami"},
            expected_criteria={"blocked": True},
        )
        result = EvaluationResult(
            case_id=case.case_id,
            category=case.category,
            status=EvaluationStatus.FAIL,
            score=0.0,
            execution_time_ms=5.0,
            claimed_success=True,
            evidence_verified=False,
        )

        rec = engine.record_failure(
            case=case,
            result=result,
            classification=FailureClassification.INJECTION_ATTEMPT,
            description="Injected command was not blocked",
        )

        self.assertTrue(rec.regression_id.startswith("REG-"))
        self.assertFalse(rec.is_resolved)

        # Generate regression test case
        reg_cases = engine.get_regression_cases()
        self.assertEqual(len(reg_cases), 1)
        self.assertEqual(reg_cases[0].case_id, f"REGCASE-{rec.regression_id}")

        # Mark resolved
        engine.mark_resolved(rec.regression_id, notes="Added regex rule in permissions.py")
        self.assertEqual(len(engine.get_active_regressions()), 0)

    def test_evaluation_reporting(self):
        results = [
            EvaluationResult(
                case_id="REP-01",
                category=EvaluationCategory.GALAXY_UI,
                status=EvaluationStatus.PASS,
                score=1.0,
                execution_time_ms=8.0,
                claimed_success=True,
                evidence_verified=True,
                message="Galaxy UI state updated",
            )
        ]

        md_path = os.path.join(self.test_dir, "report.md")
        json_path = os.path.join(self.test_dir, "report.json")

        EvaluationReportGenerator.save_report(
            results=results,
            markdown_path=md_path,
            json_path=json_path,
            title="Test Report",
        )

        with open(md_path, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("# Test Report", content)
            self.assertIn("REP-01", content)
            self.assertIn("GALAXY_UI", content)

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            self.assertEqual(data["summary"]["passed_evaluations"], 1)


if __name__ == "__main__":
    unittest.main()
