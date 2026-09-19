"""
NR-AI Model-Assisted Engineering Reasoning & Multi-Tier Confidence Engine.

Combines deterministic multi-signal evidence with advisory model hypotheses:
  SOURCE_EVIDENCE + BUILD_EVIDENCE + TEST_EVIDENCE + RUNTIME_EVIDENCE + UI_EVIDENCE + MODEL_HYPOTHESIS
  -> CONFIRMED / STRONGLY_SUPPORTED / POSSIBLE / UNRESOLVED

Authoritative Invariant:
  Model suggestions remain strictly advisory. Deterministic evidence is authoritative.
  A model hypothesis alone can NEVER produce CONFIRMED status.
  CONFIRMED strictly requires authoritative deterministic proof (e.g. reproduced runtime failure,
  direct test assertion failure, or verified compiler error correlating with AST).
"""

from dataclasses import dataclass, field, asdict
from enum import Enum
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

logger = logging.getLogger("NRAI.AndroidModelReasoning")


class EngineeringConfidence(str, Enum):
    CONFIRMED = "CONFIRMED"
    STRONGLY_SUPPORTED = "STRONGLY_SUPPORTED"
    POSSIBLE = "POSSIBLE"
    UNRESOLVED = "UNRESOLVED"


class EvidenceSignalKind(str, Enum):
    SOURCE_AST = "SOURCE_AST"
    BUILD_GRADLE = "BUILD_GRADLE"
    RESOURCE_GRAPH = "RESOURCE_GRAPH"
    COMPOSE_SEMANTICS = "COMPOSE_SEMANTICS"
    TEST_EXECUTION = "TEST_EXECUTION"
    RUNTIME_LOGCAT = "RUNTIME_LOGCAT"
    UI_HIERARCHY = "UI_HIERARCHY"
    MODEL_ADVISORY = "MODEL_ADVISORY"


@dataclass
class EvidenceSignal:
    kind: EvidenceSignalKind
    description: str
    is_authoritative: bool
    weight: float = 1.0

    @property
    def summary(self) -> str:
        return self.description

    @property
    def authoritative(self) -> bool:
        return self.is_authoritative

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind.value,
            "description": self.description,
            "is_authoritative": self.is_authoritative,
            "weight": self.weight,
        }


@dataclass
class EngineeringDiagnosis:
    defect_summary: str
    confidence: EngineeringConfidence
    evidence_signals: List[EvidenceSignal] = field(default_factory=list)
    advisory_model_notes: Optional[str] = None
    target_file: Optional[str] = None
    target_symbol: Optional[str] = None
    recommended_patch_strategy: str = ""
    suggested_tests: List[str] = field(default_factory=list)

    @property
    def signals(self) -> List[EvidenceSignal]:
        return self.evidence_signals

    def to_dict(self) -> Dict[str, Any]:
        return {
            "defect_summary": self.defect_summary,
            "confidence": self.confidence.value,
            "evidence_signals": [s.to_dict() for s in self.evidence_signals],
            "advisory_model_notes": self.advisory_model_notes,
            "target_file": self.target_file,
            "target_symbol": self.target_symbol,
            "recommended_patch_strategy": self.recommended_patch_strategy,
            "suggested_tests": self.suggested_tests,
        }


class AndroidModelReasoningEngine:
    """Evaluates multi-tier engineering confidence and synthesizes diagnoses."""

    def evaluate_confidence(self, signals: List[EvidenceSignal]) -> EngineeringConfidence:
        """
        Authoritatively evaluates confidence.
        Rule: Model hypothesis alone can never produce CONFIRMED or STRONGLY_SUPPORTED.
        """
        if not signals:
            return EngineeringConfidence.UNRESOLVED

        authoritative_signals = [s for s in signals if s.is_authoritative]
        model_signals = [s for s in signals if s.kind == EvidenceSignalKind.MODEL_ADVISORY]

        # Invariant: If ONLY model advisory signals exist, confidence is at most POSSIBLE
        if not authoritative_signals and model_signals:
            return EngineeringConfidence.POSSIBLE

        has_runtime_or_test = any(
            s.kind in (EvidenceSignalKind.RUNTIME_LOGCAT, EvidenceSignalKind.TEST_EXECUTION, EvidenceSignalKind.BUILD_GRADLE)
            for s in authoritative_signals
        )
        has_source_or_resource = any(
            s.kind in (EvidenceSignalKind.SOURCE_AST, EvidenceSignalKind.COMPOSE_SEMANTICS, EvidenceSignalKind.RESOURCE_GRAPH)
            for s in authoritative_signals
        )

        total_auth_weight = sum(s.weight for s in authoritative_signals)

        # CONFIRMED strictly requires BOTH an authoritative runtime/test/build proof AND matching source/AST correlation
        if has_runtime_or_test and has_source_or_resource and total_auth_weight >= 2.0:
            return EngineeringConfidence.CONFIRMED

        # STRONGLY_SUPPORTED requires multiple authoritative signals (e.g. static anomaly + logcat or 2+ static signals)
        if len(authoritative_signals) >= 2 or total_auth_weight >= 1.5:
            return EngineeringConfidence.STRONGLY_SUPPORTED

        if authoritative_signals or model_signals:
            return EngineeringConfidence.POSSIBLE

        return EngineeringConfidence.UNRESOLVED

    def synthesize_diagnosis(
        self,
        target_file: Optional[str],
        ast_facts: List[Dict[str, Any]],
        test_failures: List[Dict[str, Any]],
        logcat_snippets: List[str],
        compose_anomalies: List[Dict[str, Any]],
        model_suggestion: Optional[str] = None,
    ) -> EngineeringDiagnosis:
        """Synthesizes all evidence signals into an authoritative engineering diagnosis."""
        signals: List[EvidenceSignal] = []
        target_symbol: Optional[str] = None
        defect_summary = "General Android Engineering Inspection"
        patch_strategy = "Inspect targeted source files and apply bounded corrections."

        # 1. Test failure evidence (authoritative)
        if test_failures:
            tf = test_failures[0]
            signals.append(EvidenceSignal(
                kind=EvidenceSignalKind.TEST_EXECUTION,
                description=f"Direct test failure in {tf.get('test_class')}.{tf.get('test_method')}: {tf.get('failure_message')}",
                is_authoritative=True,
                weight=1.5,
            ))
            if tf.get("target_symbol"):
                target_symbol = tf.get("target_symbol")
            if tf.get("target_source_file") and not target_file:
                target_file = tf.get("target_source_file")
            defect_summary = f"Test Failure: {tf.get('failure_message')}"

        # 2. Runtime logcat evidence (authoritative)
        for log in logcat_snippets:
            if "FATAL EXCEPTION" in log or "Exception" in log:
                signals.append(EvidenceSignal(
                    kind=EvidenceSignalKind.RUNTIME_LOGCAT,
                    description=f"Logcat fatal exception trace observed: {log[:120]}",
                    is_authoritative=True,
                    weight=1.5,
                ))
                defect_summary = f"Runtime Crash: {log[:80]}"
            elif "ANR" in log:
                signals.append(EvidenceSignal(
                    kind=EvidenceSignalKind.RUNTIME_LOGCAT,
                    description="Logcat ANR event observed",
                    is_authoritative=True,
                    weight=1.5,
                ))
                defect_summary = "ANR: Main thread blocked"

        # 3. Compose anomalies (authoritative static analysis)
        for ca in compose_anomalies:
            signals.append(EvidenceSignal(
                kind=EvidenceSignalKind.COMPOSE_SEMANTICS,
                description=f"Compose pattern anomaly {ca.get('kind')} in {ca.get('composable_name')}",
                is_authoritative=True,
                weight=1.0,
            ))
            if not target_symbol:
                target_symbol = ca.get("composable_name")
            defect_summary = f"Compose Interaction Anomaly: {ca.get('kind')}"
            patch_strategy = "Wire event callback to state mutation and verify recomposition."

        # 4. AST facts (authoritative structural analysis)
        if ast_facts:
            signals.append(EvidenceSignal(
                kind=EvidenceSignalKind.SOURCE_AST,
                description=f"Indexed {len(ast_facts)} structural AST facts for {target_file or 'target'}",
                is_authoritative=True,
                weight=0.8,
            ))

        # 5. Model advisory hypothesis (non-authoritative)
        if model_suggestion:
            signals.append(EvidenceSignal(
                kind=EvidenceSignalKind.MODEL_ADVISORY,
                description=f"Model advisory hypothesis: {model_suggestion[:150]}",
                is_authoritative=False,
                weight=0.5,
            ))

        confidence = self.evaluate_confidence(signals)

        return EngineeringDiagnosis(
            defect_summary=defect_summary,
            confidence=confidence,
            evidence_signals=signals,
            advisory_model_notes=model_suggestion,
            target_file=target_file,
            target_symbol=target_symbol,
            recommended_patch_strategy=patch_strategy,
            suggested_tests=[f"{target_symbol or 'App'}Test.kt"] if target_symbol else [],
        )
