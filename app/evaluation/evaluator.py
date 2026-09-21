"""
Deterministic Evaluation Engine for NR-AI.
Evaluates agent/model execution results against objective empirical evidence.

Invariants:
- Execution evidence strictly dominates model claims.
- If an agent or model claims success but execution evidence fails or is absent,
  status is strictly evaluated as FAIL or NOT_VERIFIED.
- Zero subjective LLM self-evaluation without verifiable telemetry, exit codes,
  regex patterns, or artifacts.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional

from app.evaluation.models import (
    EvaluationCase,
    EvaluationCategory,
    EvaluationResult,
    EvaluationStatus,
    EvidenceRecord,
    EvidenceType,
)

logger = logging.getLogger("NRAI.Evaluation.Evaluator")


class DeterministicEvaluator:
    """
    Executes and scores evaluation cases based strictly on verifiable evidence.
    """

    @classmethod
    def evaluate_case(
        cls,
        case: EvaluationCase,
        execution_output: Dict[str, Any],
        claimed_success: bool = True,
        execution_time_ms: float = 0.0,
    ) -> EvaluationResult:
        """
        Evaluate a single case against execution output and criteria.
        """
        start_t = time.perf_counter()
        expected = case.expected_criteria
        evidence_records: List[EvidenceRecord] = []
        is_evidence_verified = True
        failure_reasons: List[str] = []

        # 1. Inspect exit codes if expected
        if "expected_exit_code" in expected:
            actual_code = execution_output.get("exit_code")
            verified = (actual_code == expected["expected_exit_code"])
            evidence_records.append(
                EvidenceRecord(
                    evidence_type=EvidenceType.EXIT_CODE,
                    verified=verified,
                    details={"expected": expected["expected_exit_code"], "actual": actual_code},
                )
            )
            if not verified:
                is_evidence_verified = False
                failure_reasons.append(f"Exit code mismatch: expected {expected['expected_exit_code']}, got {actual_code}")

        # 2. Inspect required artifact files if expected
        if "expected_artifact_path" in expected:
            art_path = expected["expected_artifact_path"]
            exists = os.path.isfile(art_path)
            size = os.path.getsize(art_path) if exists else 0
            verified = exists and size > 0
            evidence_records.append(
                EvidenceRecord(
                    evidence_type=EvidenceType.ARTIFACT_FILE,
                    verified=verified,
                    details={"path": art_path, "exists": exists, "size_bytes": size},
                )
            )
            if not verified:
                is_evidence_verified = False
                failure_reasons.append(f"Artifact file missing or empty: {art_path}")

        # 3. Inspect regex matches if expected
        if "expected_regex" in expected:
            pat = expected["expected_regex"]
            text_to_search = str(execution_output.get("output_text", ""))
            matched = bool(re.search(pat, text_to_search))
            evidence_records.append(
                EvidenceRecord(
                    evidence_type=EvidenceType.REGEX_MATCH,
                    verified=matched,
                    details={"regex": pat, "matched": matched},
                )
            )
            if not matched:
                is_evidence_verified = False
                failure_reasons.append(f"Regex pattern '{pat}' not found in output")

        # 4. Inspect telemetry metrics (e.g. latency)
        if "max_latency_ms" in expected:
            actual_lat = execution_output.get("latency_ms", execution_time_ms)
            verified = actual_lat <= expected["max_latency_ms"]
            evidence_records.append(
                EvidenceRecord(
                    evidence_type=EvidenceType.TELEMETRY_METRIC,
                    verified=verified,
                    details={"max_allowed_ms": expected["max_latency_ms"], "actual_ms": actual_lat},
                )
            )
            if not verified:
                is_evidence_verified = False
                failure_reasons.append(f"Latency threshold exceeded: {actual_lat}ms > {expected['max_latency_ms']}ms")

        # 5. Inspect direct key-value criteria in execution output
        for key, exp_val in expected.items():
            if key in ("expected_exit_code", "expected_artifact_path", "expected_regex", "max_latency_ms"):
                continue
            act_val = execution_output.get(key)
            verified = (act_val == exp_val)
            evidence_records.append(
                EvidenceRecord(
                    evidence_type=EvidenceType.API_RESPONSE,
                    verified=verified,
                    details={"key": key, "expected": exp_val, "actual": act_val},
                )
            )
            if not verified:
                is_evidence_verified = False
                failure_reasons.append(f"Criterion mismatch for '{key}': expected {exp_val}, got {act_val}")

        elapsed_ms = (time.perf_counter() - start_t) * 1000.0 + execution_time_ms

        # 6. Apply Execution Evidence Dominance Rule
        # If no evidence was inspectable, cannot declare PASS
        if not evidence_records:
            evidence_records.append(
                EvidenceRecord(
                    evidence_type=EvidenceType.NONE,
                    verified=False,
                    details={"reason": "NO_VERIFIABLE_EVIDENCE_PRODUCED"},
                )
            )
            return EvaluationResult(
                case_id=case.case_id,
                category=case.category,
                status=EvaluationStatus.NOT_VERIFIED,
                score=0.0,
                execution_time_ms=elapsed_ms,
                claimed_success=claimed_success,
                evidence_verified=False,
                evidence_records=evidence_records,
                message="Case has no verifiable empirical criteria; marked NOT_VERIFIED.",
            )

        if is_evidence_verified and claimed_success:
            final_status = EvaluationStatus.PASS
            score = 1.0
            msg = "All empirical verification criteria satisfied."
        elif is_evidence_verified and not claimed_success:
            final_status = EvaluationStatus.FAIL
            score = 0.0
            msg = f"Evidence verified but execution claimed failure: {'; '.join(failure_reasons)}"
        else:
            final_status = EvaluationStatus.FAIL
            score = 0.0
            msg = f"Verification failed: {'; '.join(failure_reasons)}"

        return EvaluationResult(
            case_id=case.case_id,
            category=case.category,
            status=final_status,
            score=score,
            execution_time_ms=elapsed_ms,
            claimed_success=claimed_success,
            evidence_verified=is_evidence_verified,
            evidence_records=evidence_records,
            message=msg,
        )

    @classmethod
    def evaluate_batch(
        cls,
        cases_with_outputs: List[tuple[EvaluationCase, Dict[str, Any], bool]],
    ) -> List[EvaluationResult]:
        """
        Evaluate a batch of test cases.
        """
        results: List[EvaluationResult] = []
        for case, out, claimed in cases_with_outputs:
            res = cls.evaluate_case(case, out, claimed_success=claimed)
            results.append(res)
        return results
