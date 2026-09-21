"""
Unit tests for Bounded Learning Engine and Zero Self-Modification Invariant in NR-AI Phase 4.
"""

import os
import shutil
import tempfile
import unittest

from app.evaluation.learning import (
    BoundedLearningEngine,
    LearnedPattern,
    PatternStatus,
)
from app.evaluation.models import EvaluationCategory


class TestEvaluationLearningPhase4(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.store_file = os.path.join(self.test_dir, "test_patterns.json")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_learning_record_observation(self):
        engine = BoundedLearningEngine(store_path=self.store_file)

        pat = engine.record_observation(
            category=EvaluationCategory.TASK_AUTOMATION,
            trigger_condition="Disk full during checkpoint serialization",
            recommended_adaptation="Rotate old checkpoint files before serialization",
            evidence_ref="EVAL-CHK-001",
            confidence=0.5,
        )

        self.assertTrue(pat.pattern_id.startswith("PAT-"))
        self.assertEqual(pat.status, PatternStatus.OBSERVED)
        self.assertEqual(pat.confidence, 0.5)
        self.assertEqual(len(pat.evidence_refs), 1)
        self.assertTrue(os.path.exists(self.store_file))

    def test_learning_zero_self_modification_invariant(self):
        engine = BoundedLearningEngine(store_path=self.store_file)

        # Attempting to inject code or shell commands must raise ValueError
        with self.assertRaises(ValueError):
            engine.record_observation(
                category=EvaluationCategory.SECURITY_GUARDRAILS,
                trigger_condition="High CPU detected",
                recommended_adaptation="powershell.exe -Command Stop-Process -Name nrai",
                evidence_ref="EVAL-INJ-001",
            )

        with self.assertRaises(ValueError):
            engine.record_observation(
                category=EvaluationCategory.AGENT_EXECUTION,
                trigger_condition="Function missing",
                recommended_adaptation="eval('def patch(): pass')",
                evidence_ref="EVAL-INJ-002",
            )

    def test_learning_pattern_validation_and_trinity_promotion(self):
        engine = BoundedLearningEngine(store_path=self.store_file)

        pat = engine.record_observation(
            category=EvaluationCategory.VOICE_PIPELINE,
            trigger_condition="Audio buffer underrun on 16kHz stream",
            recommended_adaptation="Increase audio frame chunk size to 1024 samples",
            evidence_ref="EVAL-VOICE-001",
            confidence=0.6,
        )

        # Cannot promote when confidence is 0.6 and status is OBSERVED
        promo_fail = engine.promote_to_trinity(pat.pattern_id)
        self.assertFalse(promo_fail["success"])
        self.assertIn("INSUFFICIENT_VALIDATION", promo_fail["error"])

        # Validate with additional evidence
        engine.validate_pattern(pat.pattern_id, "EVAL-VOICE-002", confidence_boost=0.3)
        pat_updated = engine.get_pattern(pat.pattern_id)
        self.assertAlmostEqual(pat_updated.confidence, 0.9, places=2)
        self.assertEqual(pat_updated.status, PatternStatus.VALIDATED)

        # Now promotion succeeds
        promo_ok = engine.promote_to_trinity(pat.pattern_id)
        self.assertTrue(promo_ok["success"])
        self.assertEqual(promo_ok["status"], PatternStatus.PROMOTED_TO_TRINITY.value)

    def test_learning_persistence_reload(self):
        engine1 = BoundedLearningEngine(store_path=self.store_file)
        p = engine1.record_observation(
            category=EvaluationCategory.COMMAND_ROUTING,
            trigger_condition="Ambiguous query",
            recommended_adaptation="Request clarification prompt",
            evidence_ref="EVAL-ROUT-01",
        )

        engine2 = BoundedLearningEngine(store_path=self.store_file)
        loaded = engine2.get_pattern(p.pattern_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.trigger_condition, "Ambiguous query")


if __name__ == "__main__":
    unittest.main()
