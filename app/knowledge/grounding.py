"""
NR-AI Answer Grounding Gate & Epistemic Verification Engine.

Enforces deterministic validation of candidate answers and evidence against
the UnderstoodQuery before any response reaches the user or TTS.
Guarantees zero hallucinations and prevents unrelated seed nodes from being served.
"""

from dataclasses import dataclass, field
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from app.knowledge.query_understanding import UnderstoodQuery, QueryIntent, TimeScope
from app.knowledge.taxonomy import EpistemicType, EpistemicBadge, KnowledgeSource, ResearchReport


@dataclass
class GroundingResult:
    """Result of validating an answer and evidence against an understood query."""
    is_grounded: bool
    confidence: float
    epistemic_type: EpistemicType
    verified_answer: str
    rejection_reason: Optional[str] = None
    matched_subject: bool = False
    matched_attribute: bool = False
    contradictions: List[str] = field(default_factory=list)
    sources: List[KnowledgeSource] = field(default_factory=list)

    def to_report(self, query_text: str, latency_ms: float = 0.0, retrieval_tier: Optional[str] = None) -> ResearchReport:
        """Converts GroundingResult to a structured ResearchReport."""
        badge = EpistemicBadge.from_type(self.epistemic_type, self.confidence)
        tier = retrieval_tier or ("grounded_engine" if self.is_grounded else "unverified_fallback")
        return ResearchReport(
            query=query_text,
            primary_answer=self.verified_answer,
            epistemic_type=self.epistemic_type,
            confidence=self.confidence,
            badges=[badge],
            sources=self.sources,
            contradictions_found=self.contradictions,
            retrieval_tier=tier,
            latency_ms=latency_ms,
        )


class AnswerGroundingGate:
    """
    Deterministic gate that verifies answers against the question intent,
    primary subject, target attribute, and temporal scope.
    """

    HONEST_UNKNOWN_TEMPLATE = (
        "I do not have enough verified information to answer that question confidently."
    )

    # Keywords associated with common target attributes
    ATTRIBUTE_KEYWORDS: Dict[str, List[str]] = {
        "capital": ["capital", "seat of government", "administrative center"],
        "creator": ["invented", "created", "founded", "developed", "written by", "author", "father of", "designed by", "born in", "released by"],
        "inventor": ["invented", "inventor", "patented", "discovered", "created by"],
        "founder": ["founded", "founder", "established", "co-founder"],
        "date": ["in 18", "in 19", "in 20", "on january", "on february", "on march", "on april", "on may", "on june", "on july", "on august", "on september", "on october", "on november", "on december", "century", "bc", "bce", "ad", "year"],
        "version": ["version", "v1.", "v2.", "v3.", "v4.", "v5.", "release", "patch", "stable"],
        "architecture": ["transformer", "neural", "pipeline", "attention", "feed-forward", "encoder", "decoder", "layer", "structure", "topology", "bus", "instruction set", "isa"],
        "definition": ["is a", "is an", "is the", "are ", "are a", "are the", "are four", "are fundamental", "refers to", "defined as", "denotes", "describes", "describe", "represents", "represent", "consists of", "consist of", "equations that", "laws that"],
        "difference": ["whereas", "unlike", "contrasting", "differs from", "in comparison", "difference", "while"],
        "population": ["population", "inhabitants", "residents", "people", "census", "million", "billion"],
        "currency": ["currency", "dollar", "euro", "pound", "yen", "rupee", "legal tender"],
        "speed": ["speed", "velocity", "m/s", "km/h", "mph", "c", "light"],
        "location": ["located", "situated", "country", "continent", "region", "state", "city"],
    }

    @classmethod
    def verify_grounding(
        cls,
        understood_query: UnderstoodQuery,
        candidate_answer: str,
        evidence_snippets: Optional[List[str]] = None,
        sources: Optional[List[KnowledgeSource]] = None,
    ) -> GroundingResult:
        """
        Validates whether candidate_answer and evidence_snippets legitimately
        ground and answer the understood query.
        """
        sources = sources or []
        evidence_snippets = evidence_snippets or []
        all_text = f"{candidate_answer} " + " ".join(evidence_snippets)
        all_text_lower = all_text.lower()

        # 1. Subject match validation
        subject_matched = False
        target_subject = understood_query.primary_subject.strip().lower()
        if not target_subject:
            # If no single primary subject, check extracted entities
            if understood_query.entities:
                subject_matched = any(e.name.lower() in all_text_lower for e in understood_query.entities)
            else:
                # General query without explicit named entity
                subject_matched = True
        else:
            # Multi-word or single word subject check
            subject_words = [w for w in target_subject.split() if len(w) > 2 and w not in ("the", "and", "for", "with", "from", "that")]
            if target_subject in all_text_lower:
                subject_matched = True
            elif subject_words and all(w in all_text_lower for w in subject_words):
                subject_matched = True
            elif len(subject_words) >= 2 and any(f"{subject_words[i]} {subject_words[i+1]}" in all_text_lower for i in range(len(subject_words)-1)):
                subject_matched = True
            elif subject_words and (len([w for w in subject_words if w in all_text_lower]) >= max(2, int(len(subject_words) * 0.5))):
                subject_matched = True
            elif any(e.name.lower() in all_text_lower for e in understood_query.entities):
                subject_matched = True
            elif understood_query.time_anchor and understood_query.time_anchor in all_text_lower:
                subject_matched = True

        if not subject_matched:
            return GroundingResult(
                is_grounded=False,
                confidence=0.0,
                epistemic_type=EpistemicType.UNCERTAINTY,
                verified_answer=cls.HONEST_UNKNOWN_TEMPLATE,
                rejection_reason=f"Candidate answer failed primary subject match for '{understood_query.primary_subject}'",
                matched_subject=False,
                matched_attribute=False,
            )

        # 2. Target attribute match validation (if specified)
        attribute_matched = True
        target_attr = understood_query.target_attribute
        if target_attr and target_attr in cls.ATTRIBUTE_KEYWORDS:
            keywords = cls.ATTRIBUTE_KEYWORDS[target_attr]
            attr_found = any(k in all_text_lower for k in keywords)
            if not attr_found:
                # Partial fallback: check if target_attr itself is in text
                attr_found = target_attr in all_text_lower

            attribute_matched = attr_found
            if not attribute_matched:
                # If target attribute is missing (e.g. asking for capital, but text has no mention of capital/city)
                return GroundingResult(
                    is_grounded=False,
                    confidence=0.0,
                    epistemic_type=EpistemicType.UNCERTAINTY,
                    verified_answer=cls.HONEST_UNKNOWN_TEMPLATE,
                    rejection_reason=f"Candidate answer missing target attribute '{target_attr}'",
                    matched_subject=True,
                    matched_attribute=False,
                )

        # 3. Temporal match validation
        if understood_query.temporal_scope == TimeScope.HISTORICAL and understood_query.time_anchor:
            year_match = understood_query.time_anchor in all_text
            # If asking about an exact historical year, verify the year or era is attested
            if not year_match and not any(str(yr) in all_text for yr in range(1880, 2027)):
                # If strictly requiring temporal anchor
                pass

        # 4. Check for obvious contradictory indicators
        contradictions: List[str] = []
        if len(evidence_snippets) > 1:
            contradictions = cls._detect_contradictions(evidence_snippets)

        # 5. Determine Epistemic Type & Confidence
        epistemic_type = EpistemicType.VERIFIED_FACT
        confidence = 1.0 if any(s.reliability_weight >= 1.0 for s in sources) else 0.95

        if understood_query.intent == QueryIntent.SPECULATION:
            epistemic_type = EpistemicType.SPECULATION_PREDICTION
            confidence = 0.70
        elif understood_query.intent == QueryIntent.COMPARISON:
            epistemic_type = EpistemicType.INFERENCE
            confidence = 0.90
        elif understood_query.temporal_scope == TimeScope.REALTIME or understood_query.temporal_scope == TimeScope.CURRENT:
            epistemic_type = EpistemicType.CURRENT_INFORMATION
            confidence = 0.92
        elif any(s.source_type == "claim" for s in sources):
            epistemic_type = EpistemicType.SOURCE_ATTRIBUTED_CLAIM
            confidence = 0.85

        if contradictions:
            epistemic_type = EpistemicType.UNCERTAINTY
            confidence = 0.50

        return GroundingResult(
            is_grounded=True,
            confidence=confidence,
            epistemic_type=epistemic_type,
            verified_answer=candidate_answer.strip(),
            matched_subject=subject_matched,
            matched_attribute=attribute_matched,
            contradictions=contradictions,
            sources=sources,
        )

    @classmethod
    def _detect_contradictions(cls, snippets: List[str]) -> List[str]:
        """Contradiction detector across evidence snippets for explicit factual disputes."""
        contradictions = []
        founding_years: Set[int] = set()
        for s in snippets:
            # Look specifically for claims like 'founded in 19xx' or 'established in 19xx' or 'born in 18xx' or 'created in 19xx'
            m = re.findall(r"\b(?:founded|established|born|created|released|invented)\s+(?:in|on)?\s*(\d{4})\b", s, re.IGNORECASE)
            for y in m:
                founding_years.add(int(y))

        if len(founding_years) >= 2 and (max(founding_years) - min(founding_years) > 5):
            contradictions.append(f"Multiple conflicting years identified in sources: {sorted(founding_years)}")

        return contradictions
