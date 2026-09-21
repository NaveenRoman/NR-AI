"""
NR-AI Evaluation and Learning Package.
Provides deterministic evaluation, capability scoring, benchmark datasets,
regression defense, and bounded empirical pattern learning.
"""

from app.evaluation.datasets import BENCHMARK_CASES, BenchmarkDataset
from app.evaluation.evaluator import DeterministicEvaluator
from app.evaluation.learning import (
    BoundedLearningEngine,
    LearnedPattern,
    PatternStatus,
)
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

__all__ = [
    "EvaluationCategory",
    "EvaluationStatus",
    "EvidenceType",
    "EvidenceRecord",
    "EvaluationCase",
    "EvaluationResult",
    "DeterministicEvaluator",
    "CapabilityScorer",
    "CapabilityScoreSummary",
    "CAPABILITY_DIMENSIONS",
    "BenchmarkDataset",
    "BENCHMARK_CASES",
    "BoundedLearningEngine",
    "LearnedPattern",
    "PatternStatus",
    "RegressionDefenseEngine",
    "RegressionRecord",
    "FailureClassification",
    "EvaluationReportGenerator",
]
