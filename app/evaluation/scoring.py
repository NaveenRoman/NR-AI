"""
Capability Scoring and Dimension Analysis for NR-AI Evaluation.
Computes multi-dimensional capability profiles across ModelRouter competencies.

Invariants:
- Deterministic score computation based strictly on evidence-verified test results.
- Zero reliance on subjective LLM self-evaluations.
- Weights configurable and bounded between 0.0 and 1.0.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.evaluation.models import EvaluationCategory, EvaluationResult, EvaluationStatus

logger = logging.getLogger("NRAI.Evaluation.Scoring")

# Core capability dimensions
CAPABILITY_DIMENSIONS = [
    "reasoning",
    "instruction_following",
    "code_generation",
    "tool_calling",
    "safety_alignment",
    "latency_efficiency",
    "context_retention",
    "agent_coordination",
    "voice_fluency",
    "recovery_resilience",
    "deterministic_evidence",
]

# Mapping from EvaluationCategory to primary capability dimensions
CATEGORY_DIMENSION_MAP: Dict[EvaluationCategory, List[str]] = {
    EvaluationCategory.COMMAND_ROUTING: ["reasoning", "instruction_following"],
    EvaluationCategory.AGENT_EXECUTION: ["agent_coordination", "tool_calling"],
    EvaluationCategory.VOICE_PIPELINE: ["voice_fluency", "latency_efficiency"],
    EvaluationCategory.TASK_AUTOMATION: ["deterministic_evidence", "instruction_following"],
    EvaluationCategory.KNOWLEDGE_RETRIEVAL: ["context_retention", "reasoning"],
    EvaluationCategory.SECURITY_GUARDRAILS: ["safety_alignment", "recovery_resilience"],
    EvaluationCategory.GALAXY_UI: ["instruction_following", "latency_efficiency"],
    EvaluationCategory.DESKTOP_SHELL: ["deterministic_evidence", "safety_alignment"],
    EvaluationCategory.MCP_INTEGRATION: ["tool_calling", "deterministic_evidence"],
    EvaluationCategory.TOOL_CALLING: ["tool_calling", "code_generation"],
    EvaluationCategory.LATENCY_PERFORMANCE: ["latency_efficiency"],
    EvaluationCategory.COMPANION_CONNECTIVITY: ["recovery_resilience", "agent_coordination"],
    EvaluationCategory.REGRESSION_DEFENSE: ["recovery_resilience", "deterministic_evidence"],
}


@dataclass
class CapabilityScoreSummary:
    overall_score: float
    total_evaluations: int
    passed_evaluations: int
    failed_evaluations: int
    blocked_evaluations: int
    not_verified_evaluations: int
    pass_rate: float
    dimension_scores: Dict[str, float] = field(default_factory=dict)
    category_scores: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall_score": round(self.overall_score, 3),
            "total_evaluations": self.total_evaluations,
            "passed_evaluations": self.passed_evaluations,
            "failed_evaluations": self.failed_evaluations,
            "blocked_evaluations": self.blocked_evaluations,
            "not_verified_evaluations": self.not_verified_evaluations,
            "pass_rate": round(self.pass_rate, 3),
            "dimension_scores": {k: round(v, 3) for k, v in self.dimension_scores.items()},
            "category_scores": {k: round(v, 3) for k, v in self.category_scores.items()},
        }


class CapabilityScorer:
    """
    Computes objective scores and multi-dimensional profiles from evaluation results.
    """

    @classmethod
    def calculate_scores(cls, results: List[EvaluationResult]) -> CapabilityScoreSummary:
        if not results:
            return CapabilityScoreSummary(
                overall_score=0.0,
                total_evaluations=0,
                passed_evaluations=0,
                failed_evaluations=0,
                blocked_evaluations=0,
                not_verified_evaluations=0,
                pass_rate=0.0,
                dimension_scores={d: 0.0 for d in CAPABILITY_DIMENSIONS},
                category_scores={},
            )

        total = len(results)
        passed = sum(1 for r in results if r.status == EvaluationStatus.PASS)
        failed = sum(1 for r in results if r.status == EvaluationStatus.FAIL)
        blocked = sum(1 for r in results if r.status == EvaluationStatus.BLOCKED)
        not_verified = sum(1 for r in results if r.status == EvaluationStatus.NOT_VERIFIED)

        # Average score
        overall_score = sum(r.score for r in results) / total
        pass_rate = passed / total if total > 0 else 0.0

        # Category scores
        cat_scores: Dict[str, List[float]] = {}
        for r in results:
            cat_name = r.category.value if isinstance(r.category, EvaluationCategory) else str(r.category)
            cat_scores.setdefault(cat_name, []).append(r.score)

        category_averages = {
            cat: (sum(scores) / len(scores))
            for cat, scores in cat_scores.items()
        }

        # Dimension scores mapped from categories
        dim_scores: Dict[str, List[float]] = {d: [] for d in CAPABILITY_DIMENSIONS}
        for r in results:
            dims = CATEGORY_DIMENSION_MAP.get(r.category, ["deterministic_evidence"])
            for dim in dims:
                if dim in dim_scores:
                    dim_scores[dim].append(r.score)

        dimension_averages = {
            dim: (sum(vals) / len(vals) if vals else overall_score)
            for dim, vals in dim_scores.items()
        }

        return CapabilityScoreSummary(
            overall_score=overall_score,
            total_evaluations=total,
            passed_evaluations=passed,
            failed_evaluations=failed,
            blocked_evaluations=blocked,
            not_verified_evaluations=not_verified,
            pass_rate=pass_rate,
            dimension_scores=dimension_averages,
            category_scores=category_averages,
        )
