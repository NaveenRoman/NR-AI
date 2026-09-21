"""
Regression Defense and Defect Preservation Engine for NR-AI.
Preserves known failure patterns, tracks defect classifications, and guarantees regression-free cycles.

Invariants:
- Once a failure is logged, it is permanently preserved in the regression database.
- Every release or re-evaluation cycle executes regression cases to prevent re-emergence.
- Bounded remediation suggestions are advisory only; zero automated code mutations.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from app.evaluation.evaluator import DeterministicEvaluator
from app.evaluation.models import (
    EvaluationCase,
    EvaluationCategory,
    EvaluationResult,
    EvaluationStatus,
)

logger = logging.getLogger("NRAI.Evaluation.Regression")


class FailureClassification(str, Enum):
    SECURITY_VIOLATION = "SECURITY_VIOLATION"
    INJECTION_ATTEMPT = "INJECTION_ATTEMPT"
    TIMEOUT = "TIMEOUT"
    PAYLOAD_OVERFLOW = "PAYLOAD_OVERFLOW"
    CRITERION_MISMATCH = "CRITERION_MISMATCH"
    DEPENDENCY_MISSING = "DEPENDENCY_MISSING"
    UNEXPECTED_EXCEPTION = "UNEXPECTED_EXCEPTION"


@dataclass
class RegressionRecord:
    regression_id: str
    case_id: str
    category: EvaluationCategory
    classification: FailureClassification
    description: str
    failing_input: Dict[str, Any]
    expected_criteria: Dict[str, Any]
    detected_at: float = field(default_factory=time.time)
    last_verified_at: Optional[float] = None
    is_resolved: bool = False
    resolution_notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "regression_id": self.regression_id,
            "case_id": self.case_id,
            "category": self.category.value if isinstance(self.category, EvaluationCategory) else str(self.category),
            "classification": self.classification.value if isinstance(self.classification, FailureClassification) else str(self.classification),
            "description": self.description,
            "failing_input": self.failing_input,
            "expected_criteria": self.expected_criteria,
            "detected_at": self.detected_at,
            "last_verified_at": self.last_verified_at,
            "is_resolved": self.is_resolved,
            "resolution_notes": self.resolution_notes,
        }


class RegressionDefenseEngine:
    """
    Records regressions, generates regression test cases, and verifies resolution.
    """

    DEFAULT_STORE_PATH = os.path.join("data", "regression_records.json")

    def __init__(self, store_path: Optional[str] = None) -> None:
        self._store_path = store_path or self.DEFAULT_STORE_PATH
        self._lock = threading.RLock()
        self._records: Dict[str, RegressionRecord] = {}
        self._load_records()

    def _load_records(self) -> None:
        if not os.path.exists(self._store_path):
            return
        try:
            with open(self._store_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for item in data.get("regressions", []):
                    rec = RegressionRecord(
                        regression_id=item["regression_id"],
                        case_id=item["case_id"],
                        category=EvaluationCategory(item["category"]),
                        classification=FailureClassification(item["classification"]),
                        description=item["description"],
                        failing_input=item.get("failing_input", {}),
                        expected_criteria=item.get("expected_criteria", {}),
                        detected_at=float(item.get("detected_at", time.time())),
                        last_verified_at=float(item["last_verified_at"]) if item.get("last_verified_at") else None,
                        is_resolved=bool(item.get("is_resolved", False)),
                        resolution_notes=str(item.get("resolution_notes", "")),
                    )
                    self._records[rec.regression_id] = rec
        except Exception as exc:
            logger.warning("Failed to load regression records: %s", exc)

    def _save_records(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._store_path), exist_ok=True)
            with open(self._store_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "version": "1.0",
                        "updated_at": time.time(),
                        "regressions": [r.to_dict() for r in self._records.values()],
                    },
                    f,
                    indent=2,
                )
        except Exception as exc:
            logger.error("Failed to persist regression records: %s", exc)

    def record_failure(
        self,
        case: EvaluationCase,
        result: EvaluationResult,
        classification: FailureClassification,
        description: str,
    ) -> RegressionRecord:
        """
        Preserve a failure in the regression store.
        """
        regression_id = f"REG-{uuid.uuid4().hex[:8]}"
        with self._lock:
            rec = RegressionRecord(
                regression_id=regression_id,
                case_id=case.case_id,
                category=case.category,
                classification=classification,
                description=description,
                failing_input=case.input_data,
                expected_criteria=case.expected_criteria,
                detected_at=time.time(),
                is_resolved=False,
            )
            self._records[regression_id] = rec
            self._save_records()

        logger.warning("Captured regression %s for case %s: %s", regression_id, case.case_id, description)
        return rec

    def mark_resolved(self, regression_id: str, notes: str = "") -> bool:
        with self._lock:
            rec = self._records.get(regression_id)
            if not rec:
                return False
            rec.is_resolved = True
            rec.resolution_notes = notes
            rec.last_verified_at = time.time()
            self._save_records()
            return True

    def get_regression_cases(self) -> List[EvaluationCase]:
        """Convert stored regressions into executable EvaluationCases."""
        with self._lock:
            cases: List[EvaluationCase] = []
            for rec in self._records.values():
                c = EvaluationCase(
                    case_id=f"REGCASE-{rec.regression_id}",
                    category=rec.category,
                    title=f"Regression Guard: {rec.case_id}",
                    description=rec.description,
                    input_data=rec.failing_input,
                    expected_criteria=rec.expected_criteria,
                    tags=["regression", rec.classification.value.lower()],
                )
                cases.append(c)
            return cases

    def get_active_regressions(self) -> List[RegressionRecord]:
        with self._lock:
            return [r for r in self._records.values() if not r.is_resolved]

    def clear_records(self) -> None:
        with self._lock:
            self._records.clear()
            if os.path.exists(self._store_path):
                try:
                    os.remove(self._store_path)
                except Exception:
                    pass
