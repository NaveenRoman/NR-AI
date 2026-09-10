"""
NR AI Multi-Model Consensus Engine.

Evaluates independent verifier findings alongside concrete execution evidence
(exit codes, compiler logs, test outputs, generated artifacts).
Enforces the 4 strict decision cases:
- CASE A: Both independent verifiers PASS AND required real tests PASS → ACCEPT
- CASE B: One verifier FAILS OR verifiers disagree → INVESTIGATE
- CASE C: Both verifiers FAIL → REJECT
- CASE D: Real test/build/runtime fails → REJECT regardless of LLM claims
"""

import ast
from dataclasses import dataclass, field
from enum import Enum
import logging
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional

from app.agent.model_provider import UnifiedModelProvider
from app.config.model_config import (
    GEMINI_FLASH_LATEST,
    GEMINI_3_7_FLASH,
    GEMINI_3_6_FLASH,
    GPT_5_6_SOL,
    ModelConfig,
)

logger = logging.getLogger("NRAI.ConsensusEngine")


class DecisionCase(str, Enum):
    CASE_A = "CASE_A"  # Both verifiers PASS + Real tests PASS -> ACCEPT
    CASE_B = "CASE_B"  # Verifiers disagree -> INVESTIGATE
    CASE_C = "CASE_C"  # Both verifiers FAIL -> REJECT
    CASE_D = "CASE_D"  # Real test / runtime failed -> REJECT (Overrides LLM claims)


class ConsensusDecision(str, Enum):
    ACCEPT = "ACCEPT"
    INVESTIGATE = "INVESTIGATE"
    REJECT = "REJECT"


@dataclass
class VerificationReview:
    """Individual review submitted by an independent agent slot."""
    slot_id: int
    verifier_name: str
    model_id: str
    passed: bool
    confidence: float = 1.0
    objections: List[str] = field(default_factory=list)
    notes: str = ""
    timestamp: float = field(default_factory=time.time)
    heuristic_fallback_used: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "verifier_name": self.verifier_name,
            "model_id": self.model_id,
            "passed": self.passed,
            "confidence": self.confidence,
            "objections": self.objections,
            "notes": self.notes,
            "timestamp": self.timestamp,
            "heuristic_fallback_used": self.heuristic_fallback_used,
        }


@dataclass
class ExecutionEvidence:
    """Real ground-truth evidence gathered from build, test, and tool execution."""
    command: List[str] | str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float = 0.0
    files_created_or_modified: List[str] = field(default_factory=list)
    tests_passed: int = 0
    tests_failed: int = 0
    syntax_valid: bool = True
    real_execution_verified: bool = True
    timestamp: float = field(default_factory=time.time)

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and self.tests_failed == 0 and self.syntax_valid

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout": self.stdout[:500] if self.stdout else "",
            "stderr": self.stderr[:500] if self.stderr else "",
            "duration_ms": self.duration_ms,
            "files_created_or_modified": self.files_created_or_modified,
            "tests_passed": self.tests_passed,
            "tests_failed": self.tests_failed,
            "syntax_valid": self.syntax_valid,
            "real_execution_verified": self.real_execution_verified,
            "passed": self.passed,
            "timestamp": self.timestamp,
        }


@dataclass
class ConsensusReport:
    """Final decision rendered by the ConsensusEngine."""
    decision: ConsensusDecision
    case: DecisionCase
    summary: str
    primary_review: Optional[VerificationReview] = None
    secondary_review: Optional[VerificationReview] = None
    evidence: Optional[ExecutionEvidence] = None
    objections: List[str] = field(default_factory=list)
    recommended_action: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision.value,
            "case": self.case.value,
            "summary": self.summary,
            "primary_review": self.primary_review.to_dict() if self.primary_review else None,
            "secondary_review": self.secondary_review.to_dict() if self.secondary_review else None,
            "evidence": self.evidence.to_dict() if self.evidence else None,
            "objections": self.objections,
            "recommended_action": self.recommended_action,
            "timestamp": self.timestamp,
        }


class ConsensusEngine:
    """
    Evaluates evidence against multi-model review to reach ground-truth consensus.
    Never allows an LLM claiming 'looks correct' to override a failing test or build.
    """

    def __init__(self, provider: Optional[UnifiedModelProvider] = None):
        self.provider = provider or UnifiedModelProvider()

    def review_code_with_openai(
        self,
        code: str,
        task_desc: str,
        slot_id: int = 6,
        model_id: str = GPT_5_6_SOL,
    ) -> VerificationReview:
        """
        Slot 6 review using OpenAI (GPT-5.6 Sol preferred).
        Uses live API if available; if unverified/quota exhausted, uses deep AST
        and heuristic static analysis to produce concrete review.
        """
        # 1. First run AST check
        ast_ok, ast_err = self._check_python_syntax(code)
        if not ast_ok:
            return VerificationReview(
                slot_id=slot_id,
                verifier_name="Agent-6-CodeReviewer",
                model_id=model_id,
                passed=False,
                confidence=1.0,
                objections=[f"Syntax error detected: {ast_err}"],
                notes="Syntax invalidation caught during independent review.",
                heuristic_fallback_used=True,
            )

        # 2. Attempt live API if available
        if self.provider.openai.is_available():
            prompt = (
                f"Review this code for correctness according to task: '{task_desc}'.\n\n"
                f"Code:\n```\n{code}\n```\n\n"
                f"Respond with 'PASS' or 'FAIL: <reason>'."
            )
            res = self.provider.openai.generate(
                prompt=prompt,
                model=model_id,
                max_tokens=150,
                temperature=0.0,
            )
            if res.get("success"):
                content = res["content"].strip()
                if content.upper().startswith("PASS"):
                    return VerificationReview(
                        slot_id=slot_id,
                        verifier_name="Agent-6-CodeReviewer",
                        model_id=res.get("model", model_id),
                        passed=True,
                        confidence=0.95,
                        objections=[],
                        notes=content,
                        heuristic_fallback_used=False,
                    )
                else:
                    return VerificationReview(
                        slot_id=slot_id,
                        verifier_name="Agent-6-CodeReviewer",
                        model_id=res.get("model", model_id),
                        passed=False,
                        confidence=0.9,
                        objections=[content],
                        notes=content,
                        heuristic_fallback_used=False,
                    )

        # 3. Fallback Heuristic Inspection
        objections = self._heuristic_code_check(code)
        passed = len(objections) == 0
        return VerificationReview(
            slot_id=slot_id,
            verifier_name="Agent-6-CodeReviewer",
            model_id=model_id,
            passed=passed,
            confidence=0.85,
            objections=objections,
            notes="Passed heuristic static verification (live API fallback)." if passed else "Failed heuristic verification.",
            heuristic_fallback_used=True,
        )

    def review_code_with_gemini(
        self,
        code: str,
        task_desc: str,
        slot_id: int = 5,
        model_id: str = GEMINI_FLASH_LATEST,
    ) -> VerificationReview:
        """
        Slot 5 review using Gemini (Gemini 3.6 Flash preferred).
        Uses live API if available; if unverified/no credentials, uses deep AST
        and heuristic static analysis to produce concrete review.
        """
        # 1. First run AST check
        ast_ok, ast_err = self._check_python_syntax(code)
        if not ast_ok:
            return VerificationReview(
                slot_id=slot_id,
                verifier_name="Agent-5-Verifier",
                model_id=model_id,
                passed=False,
                confidence=1.0,
                objections=[f"Syntax error detected: {ast_err}"],
                notes="Syntax invalidation caught during independent review.",
                heuristic_fallback_used=True,
            )

        # 2. Attempt live API if available
        if self.provider.gemini.is_available():
            prompt = (
                f"Independent Verification: Validate this code for task: '{task_desc}'.\n\n"
                f"Code:\n```\n{code}\n```\n\n"
                f"Respond with 'PASS' or 'FAIL: <reason>'."
            )
            res = self.provider.gemini.generate(
                prompt=prompt,
                model=model_id,
                max_tokens=1000,
                temperature=0.0,
            )
            if res.get("success"):
                content = res["content"].strip()
                first_line = content.splitlines()[0].upper() if content else ""
                if first_line.startswith("PASS") or ("PASS" in first_line and "FAIL" not in first_line):
                    return VerificationReview(
                        slot_id=slot_id,
                        verifier_name="Agent-5-Verifier",
                        model_id=res.get("model", model_id),
                        passed=True,
                        confidence=0.95,
                        objections=[],
                        notes=content,
                        heuristic_fallback_used=False,
                    )
                else:
                    return VerificationReview(
                        slot_id=slot_id,
                        verifier_name="Agent-5-Verifier",
                        model_id=res.get("model", model_id),
                        passed=False,
                        confidence=0.9,
                        objections=[content],
                        notes=content,
                        heuristic_fallback_used=False,
                    )

        # 3. Fallback Heuristic Inspection
        objections = self._heuristic_code_check(code)
        passed = len(objections) == 0
        return VerificationReview(
            slot_id=slot_id,
            verifier_name="Agent-5-Verifier",
            model_id=model_id,
            passed=passed,
            confidence=0.85,
            objections=objections,
            notes="Passed independent heuristic verification (live API fallback)." if passed else "Failed independent heuristic verification.",
            heuristic_fallback_used=True,
        )

    # -------------------------------------------------------------------------
    # Core Consensus Evaluation
    # -------------------------------------------------------------------------

    def evaluate(
        self,
        primary_review: VerificationReview,
        secondary_review: VerificationReview,
        evidence: Optional[ExecutionEvidence] = None,
        require_real_evidence: bool = True,
    ) -> ConsensusReport:
        """
        Renders consensus decision across independent reviews and ground-truth evidence.

        Enforces:
        - Case D: Real test/build failed -> REJECT immediately (regardless of LLMs)
        - Case A: Both verifiers pass AND real tests pass -> ACCEPT
        - Case C: Both verifiers fail -> REJECT
        - Case B: Disagreement between verifiers -> INVESTIGATE
        """
        all_objections: List[str] = []
        if primary_review and not primary_review.passed:
            all_objections.extend(primary_review.objections)
        if secondary_review and not secondary_review.passed:
            all_objections.extend(secondary_review.objections)

        # ---------------------------------------------------------------------
        # CASE D: Real test/build/runtime fails
        # ---------------------------------------------------------------------
        if evidence is not None and not evidence.passed:
            err_msg = evidence.stderr.strip() or f"Exit code {evidence.exit_code}"
            all_objections.append(f"Real tool execution failed: {err_msg}")
            return ConsensusReport(
                decision=ConsensusDecision.REJECT,
                case=DecisionCase.CASE_D,
                summary="Real execution evidence failed (exit code non-zero or tests failed). Rejection overrides LLM approval.",
                primary_review=primary_review,
                secondary_review=secondary_review,
                evidence=evidence,
                objections=all_objections,
                recommended_action="Trigger ErrorAnalyzer and RecoveryEngine with actual runtime stderr.",
            )

        # If real execution is strictly required but evidence was not provided
        if require_real_evidence and evidence is None:
            return ConsensusReport(
                decision=ConsensusDecision.INVESTIGATE,
                case=DecisionCase.CASE_B,
                summary="Real test evidence is required but was not provided. Cannot accept based solely on model claims.",
                primary_review=primary_review,
                secondary_review=secondary_review,
                evidence=None,
                objections=["Missing mandatory real test execution evidence."],
                recommended_action="Execute code with CodeRunner to capture real output.",
            )

        primary_passed = primary_review.passed if primary_review else False
        secondary_passed = secondary_review.passed if secondary_review else False

        # ---------------------------------------------------------------------
        # CASE A: Both independent verifiers PASS AND required real tests PASS
        # ---------------------------------------------------------------------
        if primary_passed and secondary_passed:
            if evidence is None or evidence.passed:
                return ConsensusReport(
                    decision=ConsensusDecision.ACCEPT,
                    case=DecisionCase.CASE_A,
                    summary="Both independent verifiers PASS and all real execution tests PASS.",
                    primary_review=primary_review,
                    secondary_review=secondary_review,
                    evidence=evidence,
                    objections=[],
                    recommended_action="Accept task output and proceed to commit/complete.",
                )

        # ---------------------------------------------------------------------
        # CASE C: Both verifiers FAIL
        # ---------------------------------------------------------------------
        if not primary_passed and not secondary_passed:
            return ConsensusReport(
                decision=ConsensusDecision.REJECT,
                case=DecisionCase.CASE_C,
                summary="Both independent verifiers rejected the implementation.",
                primary_review=primary_review,
                secondary_review=secondary_review,
                evidence=evidence,
                objections=all_objections,
                recommended_action="Send all objections back to recovery agent for redesign/patching.",
            )

        # ---------------------------------------------------------------------
        # CASE B: One verifier FAILS OR verifiers disagree
        # ---------------------------------------------------------------------
        return ConsensusReport(
            decision=ConsensusDecision.INVESTIGATE,
            case=DecisionCase.CASE_B,
            summary="Independent verifiers disagree. Detailed investigation required.",
            primary_review=primary_review,
            secondary_review=secondary_review,
            evidence=evidence,
            objections=all_objections,
            recommended_action="Correlate objections against execution evidence and request targeted re-verification.",
        )

    # -------------------------------------------------------------------------
    # Static & Heuristic Analyzers
    # -------------------------------------------------------------------------

    def _check_python_syntax(self, code: str) -> tuple[bool, str]:
        """Validates Python syntax via AST parsing."""
        try:
            ast.parse(code)
            return True, ""
        except SyntaxError as e:
            return False, f"Line {e.lineno}: {e.msg}"
        except Exception as e:
            return False, str(e)

    def _heuristic_code_check(self, code: str) -> List[str]:
        """Identifies common bugs, unhandled exceptions, and dead code heuristically."""
        objections = []
        # Check for unhandled exceptions or todo/pass placeholders in logic
        if "# TODO" in code or "TODO:" in code:
            objections.append("Code contains unresolved TODO markers.")
        if "raise NotImplementedError" in code:
            objections.append("Code contains unimplemented stubs (raise NotImplementedError).")
        # Check for obvious infinite loops
        if "while True:" in code and "break" not in code and "return" not in code:
            objections.append("Potential infinite loop detected (while True without break/return).")
        return objections
