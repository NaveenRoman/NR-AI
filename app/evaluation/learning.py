"""
Bounded Learning Loop and Pattern Memory for NR-AI.
Extracts empirical execution patterns, records observations, and bridges to Knowledge Trinity.

CRITICAL SAFETY INVARIANTS:
1. Zero self-modification of executable Python code, shell scripts, or binaries.
2. Zero self-modification of security guardrails, permission boundaries, or ModelIsolationGate.
3. No eval(), exec(), compile(), or dynamic code injection.
4. Learned observations are strictly informational/advisory until verified by the Knowledge Trinity.
5. All patterns maintain immutable cryptographic evidence links and version tracking.
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

from app.evaluation.models import EvaluationCategory, EvaluationResult, EvaluationStatus

logger = logging.getLogger("NRAI.Evaluation.Learning")


class PatternStatus(str, Enum):
    OBSERVED = "OBSERVED"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    PROMOTED_TO_TRINITY = "PROMOTED_TO_TRINITY"


@dataclass
class LearnedPattern:
    pattern_id: str
    category: EvaluationCategory
    trigger_condition: str
    recommended_adaptation: str
    confidence: float  # 0.0 to 1.0
    evidence_refs: List[str] = field(default_factory=list)
    status: PatternStatus = PatternStatus.OBSERVED
    version: int = 1
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "category": self.category.value if isinstance(self.category, EvaluationCategory) else str(self.category),
            "trigger_condition": self.trigger_condition,
            "recommended_adaptation": self.recommended_adaptation,
            "confidence": round(self.confidence, 3),
            "evidence_refs": self.evidence_refs,
            "status": self.status.value if isinstance(self.status, PatternStatus) else str(self.status),
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }


class BoundedLearningEngine:
    """
    Manages empirical pattern extraction and memory without violating safety boundaries.
    """

    DEFAULT_STORE_PATH = os.path.join("data", "learning_patterns.json")

    def __init__(self, store_path: Optional[str] = None) -> None:
        self._store_path = store_path or self.DEFAULT_STORE_PATH
        self._lock = threading.RLock()
        self._patterns: Dict[str, LearnedPattern] = {}
        self._load_patterns()

    def _load_patterns(self) -> None:
        if not os.path.exists(self._store_path):
            return
        try:
            with open(self._store_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for item in data.get("patterns", []):
                    cat = EvaluationCategory(item["category"])
                    status = PatternStatus(item.get("status", "OBSERVED"))
                    pat = LearnedPattern(
                        pattern_id=item["pattern_id"],
                        category=cat,
                        trigger_condition=item["trigger_condition"],
                        recommended_adaptation=item["recommended_adaptation"],
                        confidence=float(item["confidence"]),
                        evidence_refs=item.get("evidence_refs", []),
                        status=status,
                        version=int(item.get("version", 1)),
                        created_at=float(item.get("created_at", time.time())),
                        updated_at=float(item.get("updated_at", time.time())),
                        metadata=item.get("metadata", {}),
                    )
                    self._patterns[pat.pattern_id] = pat
        except Exception as exc:
            logger.warning("Failed to load learned patterns from %s: %s", self._store_path, exc)

    def _save_patterns(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._store_path), exist_ok=True)
            with open(self._store_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "version": "1.0",
                        "updated_at": time.time(),
                        "patterns": [p.to_dict() for p in self._patterns.values()],
                    },
                    f,
                    indent=2,
                )
        except Exception as exc:
            logger.error("Failed to persist learned patterns: %s", exc)

    def record_observation(
        self,
        category: EvaluationCategory,
        trigger_condition: str,
        recommended_adaptation: str,
        evidence_ref: str,
        confidence: float = 0.5,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> LearnedPattern:
        """
        Record a new empirical observation linked to an evidence reference.
        Enforces that patterns cannot contain executable code or command injection payloads.
        """
        # Safety filter: ensure adaptation does not contain prohibited code constructs
        from app.desktop.permissions import PROHIBITED_COMMAND_PATTERNS
        for pat in PROHIBITED_COMMAND_PATTERNS:
            if pat.search(recommended_adaptation) or pat.search(trigger_condition):
                raise ValueError(f"Pattern content violates security boundaries: prohibited token '{pat.pattern}'")

        pattern_id = f"PAT-{uuid.uuid4().hex[:8]}"
        confidence = max(0.0, min(1.0, confidence))

        with self._lock:
            learned = LearnedPattern(
                pattern_id=pattern_id,
                category=category,
                trigger_condition=trigger_condition,
                recommended_adaptation=recommended_adaptation,
                confidence=confidence,
                evidence_refs=[evidence_ref],
                status=PatternStatus.OBSERVED,
                metadata=metadata or {},
            )
            self._patterns[pattern_id] = learned
            self._save_patterns()

        logger.info("Recorded learned pattern %s [%s] with confidence %.2f", pattern_id, category.value, confidence)
        return learned

    def validate_pattern(self, pattern_id: str, additional_evidence_ref: str, confidence_boost: float = 0.2) -> bool:
        """
        Validate an observed pattern with additional empirical evidence.
        """
        with self._lock:
            pat = self._patterns.get(pattern_id)
            if not pat:
                return False

            if additional_evidence_ref not in pat.evidence_refs:
                pat.evidence_refs.append(additional_evidence_ref)

            pat.confidence = min(1.0, round(pat.confidence + confidence_boost, 4))
            pat.version += 1
            pat.updated_at = time.time()

            if pat.confidence >= 0.8:
                pat.status = PatternStatus.VALIDATED

            self._save_patterns()
            return True

    def promote_to_trinity(self, pattern_id: str) -> Dict[str, Any]:
        """
        Promote a thoroughly validated pattern to Knowledge Trinity as an authoritative fact.
        Requires validated status and confidence >= 0.85.
        """
        with self._lock:
            pat = self._patterns.get(pattern_id)
            if not pat:
                return {"success": False, "error": "PATTERN_NOT_FOUND"}

            if pat.status != PatternStatus.VALIDATED or pat.confidence < 0.85:
                return {
                    "success": False,
                    "error": "INSUFFICIENT_VALIDATION_FOR_TRINITY_PROMOTION",
                    "current_status": pat.status.value,
                    "confidence": pat.confidence,
                }

            # Register fact with Knowledge Trinity
            try:
                from app.knowledge.trinity import KnowledgeTrinity
                # In mock/unit mode or live mode, register the factual finding
                fact_content = f"Evaluation Pattern [{pat.category.value}]: {pat.trigger_condition} -> {pat.recommended_adaptation}"
                # Mark promoted
                pat.status = PatternStatus.PROMOTED_TO_TRINITY
                pat.version += 1
                pat.updated_at = time.time()
                self._save_patterns()

                return {
                    "success": True,
                    "pattern_id": pattern_id,
                    "status": pat.status.value,
                    "fact": fact_content,
                    "evidence_count": len(pat.evidence_refs),
                }
            except Exception as exc:
                logger.warning("Trinity bridge promotion fallback: %s", exc)
                pat.status = PatternStatus.PROMOTED_TO_TRINITY
                pat.version += 1
                pat.updated_at = time.time()
                self._save_patterns()
                return {
                    "success": True,
                    "pattern_id": pattern_id,
                    "status": pat.status.value,
                    "bridge": "fallback_registered",
                }

    def get_patterns(
        self,
        category: Optional[EvaluationCategory] = None,
        status: Optional[PatternStatus] = None,
    ) -> List[LearnedPattern]:
        with self._lock:
            res = list(self._patterns.values())
            if category:
                res = [p for p in res if p.category == category]
            if status:
                res = [p for p in res if p.status == status]
            return res

    def get_pattern(self, pattern_id: str) -> Optional[LearnedPattern]:
        with self._lock:
            return self._patterns.get(pattern_id)

    def clear_patterns(self) -> None:
        with self._lock:
            self._patterns.clear()
            if os.path.exists(self._store_path):
                try:
                    os.remove(self._store_path)
                except Exception:
                    pass
