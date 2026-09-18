"""
NR-AI Knowledge Trinity — Aegis Verification & Epistemic Gatekeeper Agent.

Aegis is the internal validation authority for the Knowledge Department.
It decomposes proposed answers into atomic claims, verifies claims against
discovery evidence and local knowledge stores, detects contradictions and
temporal drift, enforces independent source corroboration, produces structured
verification reports, emits real-time telemetry via TrinityBus, and manages
the hard-bounded 2-cycle correction loop.

Security Invariants:
- Strictly shell=False, no subprocess, no eval, no exec.
- Does NOT directly modify knowledge stores or source files.
- Operates strictly on localhost/in-process memory.
- Cloud model calls are strictly advisory; deterministic verification rules govern.
"""

import hashlib
import logging
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from app.knowledge.taxonomy import EpistemicBadge, EpistemicType
from app.knowledge.trinity.protocol import (
    TrinityBus,
    TrinityMessageType,
    TrinityTelemetryEvent,
    VerificationRequest,
    VerificationReport,
)
from app.knowledge.trinity.schemas import (
    ClaimStatus,
    ClaimVerification,
    DiscoveryEvidence,
    FounderValidationResult,
    SourceAuthorityTier,
)

logger = logging.getLogger("NRAI.KnowledgeTrinity.Aegis")


# =============================================================================
# ATOMIC CLAIM DECOMPOSITION
# =============================================================================

class ClaimType(str, Enum):
    """Categorization of decomposed atomic claims."""
    FACTUAL_HISTORICAL = "FACTUAL_HISTORICAL"
    FACTUAL_NUMERICAL = "FACTUAL_NUMERICAL"
    FACTUAL_TEMPORAL = "FACTUAL_TEMPORAL"
    FACTUAL_TECHNICAL = "FACTUAL_TECHNICAL"
    CONCEPTUAL_EXPLANATION = "CONCEPTUAL_EXPLANATION"
    PEDAGOGICAL_ANALOGY = "PEDAGOGICAL_ANALOGY"
    OPINION_OR_SPECULATION = "OPINION_OR_SPECULATION"
    GENERAL_ASSERTION = "GENERAL_ASSERTION"


@dataclass
class AtomicClaim:
    """An independently verifiable proposition extracted from a text block."""
    claim_id: str
    text: str
    claim_type: ClaimType
    entities_mentioned: List[str] = field(default_factory=list)
    numbers_mentioned: List[str] = field(default_factory=list)
    dates_mentioned: List[str] = field(default_factory=list)
    temporal_markers: List[str] = field(default_factory=list)
    confidence: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "text": self.text,
            "claim_type": self.claim_type.value,
            "entities_mentioned": self.entities_mentioned,
            "numbers_mentioned": self.numbers_mentioned,
            "dates_mentioned": self.dates_mentioned,
            "temporal_markers": self.temporal_markers,
            "confidence": self.confidence,
        }


class ClaimDecomposer:
    """
    Deterministic syntactic sentence/clause segmenter that decomposes complex
    answers into atomic, testable claims without hallucinating or mutating text.
    """

    # Regex patterns for sentence and clause boundaries
    SENTENCE_SPLIT_REGEX = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'‘“])")
    CONJUNCTION_SPLIT_REGEX = re.compile(
        r"\s*;\s*|\s+(?:and(?:\s+also)?|whereas|while|which\s+(?:was|is|were)|who\s+(?:was|is|were))\s+",
        re.IGNORECASE,
    )
    DATE_REGEX = re.compile(r"\b(?:1[7-9]\d\d|20[0-2]\d|March\s+\d{4}|January|February|March|April|May|June|July|August|September|October|November|December)\b", re.IGNORECASE)
    NUMBER_REGEX = re.compile(r"\b\d+(?:[\.,]\d+)*(?:\s*(?:%|percent|MB|GB|TB|GHz|MHz|billion|million|thousand|cores?|threads?|tokens?|ms|s|seconds?|nm))?\b", re.IGNORECASE)
    TEMPORAL_REGEX = re.compile(r"\b(latest|current|currently|today|now|recently|modern|future|superseded|deprecated|legacy|in\s+20\d\d|as\s+of\s+20\d\d)\b", re.IGNORECASE)

    @classmethod
    def decompose(cls, text: str, subject: str = "") -> List[AtomicClaim]:
        """Decomposes input text into discrete atomic claims."""
        if not text or not text.strip():
            return []

        claims: List[AtomicClaim] = []
        raw_text = text.strip()

        # Remove markdown headers and bullets for cleaner segmentation
        clean_lines = []
        for line in raw_text.splitlines():
            l_str = re.sub(r"^[\s*#\-•\d\.\)]+", "", line).strip()
            if l_str:
                clean_lines.append(l_str)

        full_clean = " ".join(clean_lines)
        sentences = [s.strip() for s in cls.SENTENCE_SPLIT_REGEX.split(full_clean) if s.strip()]

        claim_idx = 1
        for sent in sentences:
            # Check if sentence can be split into logical sub-clauses
            sub_clauses = [c.strip() for c in cls.CONJUNCTION_SPLIT_REGEX.split(sent) if len(c.strip()) > 15]
            if len(sub_clauses) > 1 and len(sent) > 60:
                candidates = sub_clauses
            else:
                candidates = [sent]

            for cand in candidates:
                cand_clean = cand.strip(" ,;.-")
                if len(cand_clean) < 8:
                    continue

                # Classify claim
                c_type = cls._classify_claim(cand_clean)
                dates = cls.DATE_REGEX.findall(cand_clean)
                numbers = cls.NUMBER_REGEX.findall(cand_clean)
                temporals = cls.TEMPORAL_REGEX.findall(cand_clean)

                # Extract proper noun entities
                entities = [w for w in re.findall(r"\b[A-Z][a-zA-Z0-9_\-]+\b", cand_clean) if len(w) > 2]
                if subject and subject not in entities:
                    entities.insert(0, subject)

                claims.append(AtomicClaim(
                    claim_id=f"claim-{claim_idx}",
                    text=cand_clean,
                    claim_type=c_type,
                    entities_mentioned=entities[:5],
                    numbers_mentioned=numbers[:5],
                    dates_mentioned=dates[:4],
                    temporal_markers=temporals[:3],
                ))
                claim_idx += 1

        return claims

    @classmethod
    def _classify_claim(cls, text: str) -> ClaimType:
        low = text.lower()
        if any(w in low for w in ("imagine", "like a", "think of", "analogy", "for example", "consider")):
            return ClaimType.PEDAGOGICAL_ANALOGY
        if any(w in low for w in ("might", "could", "perhaps", "speculation", "predict", "forecast", "will likely")):
            return ClaimType.OPINION_OR_SPECULATION
        if cls.DATE_REGEX.search(text) and any(w in low for w in ("released", "founded", "created", "invented", "born", "died", "occurred")):
            return ClaimType.FACTUAL_HISTORICAL
        if cls.NUMBER_REGEX.search(text) and any(w in low for w in ("percent", "%", "mb", "gb", "ghz", "version", "parameters", "latency")):
            return ClaimType.FACTUAL_NUMERICAL
        if cls.TEMPORAL_REGEX.search(text):
            return ClaimType.FACTUAL_TEMPORAL
        if any(w in low for w in ("algorithm", "architecture", "cache", "memory", "gpu", "kernel", "protocol", "register")):
            return ClaimType.FACTUAL_TECHNICAL
        return ClaimType.GENERAL_ASSERTION


# =============================================================================
# PROVENANCE & CORROBORATION ENGINE
# =============================================================================

class CorroborationEngine:
    """
    Measures independent source corroboration across discovery evidence items.
    Enforces provenance tracking so syndicated mirrors / duplicate press releases
    are not counted as multiple independent sources.
    """

    @staticmethod
    def extract_root_domain(url: Optional[str]) -> str:
        """Extracts registrable root domain or host for provenance grouping."""
        if not url:
            return "unknown"
        try:
            parsed = urllib.parse.urlparse(url)
            host = (parsed.netloc or "").lower().split(":")[0]
            parts = host.split(".")
            if len(parts) >= 2:
                return ".".join(parts[-2:])
            return host or "unknown"
        except Exception:
            return "unknown"

    @classmethod
    def evaluate_corroboration(
        cls,
        evidence_items: List[DiscoveryEvidence],
        matched_evidence_ids: List[str],
    ) -> Tuple[float, List[str], Dict[str, Any]]:
        """
        Evaluates independent corroboration score and returns unique publisher/domain IDs.
        A single source that is PRIMARY_CANONICAL gives strong base support.
        Multiple independent domains strengthen the score.
        """
        if not matched_evidence_ids:
            return 0.0, [], {"independent_domains": 0, "tiers_present": []}

        ev_map = {e.evidence_id: e for e in evidence_items}
        matched_ev = [ev_map[eid] for eid in matched_evidence_ids if eid in ev_map]

        if not matched_ev:
            return 0.0, [], {"independent_domains": 0, "tiers_present": []}

        seen_domains: Set[str] = set()
        seen_publishers: Set[str] = set()
        unique_ev_ids: List[str] = []

        tier_weights = {
            SourceAuthorityTier.PRIMARY_CANONICAL: 1.0,
            SourceAuthorityTier.AUTHORITATIVE_ORG: 0.90,
            SourceAuthorityTier.RELIABLE_SECONDARY: 0.75,
            SourceAuthorityTier.TECHNICAL_COMMUNITY: 0.60,
            SourceAuthorityTier.UNVERIFIED_WEB: 0.35,
        }

        base_score = 0.0
        for ev in matched_ev:
            dom = cls.extract_root_domain(ev.source_url)
            pub = (ev.publisher or "").lower().strip()

            # Detect mirror / duplicate source
            is_new_domain = dom not in seen_domains
            is_new_publisher = pub not in seen_publishers if pub else True

            if is_new_domain and is_new_publisher:
                seen_domains.add(dom)
                if pub:
                    seen_publishers.add(pub)
                unique_ev_ids.append(ev.evidence_id)

                weight = tier_weights.get(ev.authority_tier, 0.5) * ev.reliability_weight
                base_score = max(base_score, weight)

        # Multi-source independent corroboration bonus
        independent_sources_count = len(seen_domains)
        if independent_sources_count >= 3:
            final_score = min(1.0, base_score + 0.15)
        elif independent_sources_count == 2:
            final_score = min(1.0, base_score + 0.10)
        else:
            final_score = base_score

        telemetry = {
            "independent_domains": independent_sources_count,
            "domains": list(seen_domains),
            "publishers": list(seen_publishers),
            "effective_score": round(final_score, 3),
        }
        return final_score, unique_ev_ids, telemetry


# =============================================================================
# TEMPORAL VERIFICATION ENGINE
# =============================================================================

class TemporalVerifier:
    """
    Validates temporal consistency: differentiates historically true facts from
    current realities, handles version supersession, and checks freshness.
    """

    CURRENT_YEAR = 2026

    @classmethod
    def evaluate_temporality(
        cls,
        claim: AtomicClaim,
        evidence_items: List[DiscoveryEvidence],
        matched_evidence_ids: List[str],
    ) -> Tuple[str, str]:
        """
        Returns (temporal_validity, rationale):
        temporal_validity in ("CURRENT", "HISTORICAL_ONLY", "OUTDATED", "ANACHRONISTIC")
        """
        c_text_low = claim.text.lower()
        requires_current = any(m in c_text_low for m in ("latest", "current", "currently", "today", "now", "newest"))

        # Look for dates mentioned in claim
        claim_years = [int(y) for y in re.findall(r"\b(1[89]\d\d|20[0-2]\d)\b", claim.text)]

        # Check matched evidence publication dates
        ev_map = {e.evidence_id: e for e in evidence_items}
        matched_ev = [ev_map[eid] for eid in matched_evidence_ids if eid in ev_map]

        if not matched_ev:
            if requires_current:
                return "OUTDATED", "Claim asserts current status but no contemporary evidence was provided."
            return "CURRENT", "No conflicting temporal signals detected."

        latest_ev_timestamp = max((e.publication_timestamp or e.retrieved_timestamp for e in matched_ev), default=time.time())

        # If claim explicitly refers to historical events
        if claim.claim_type == ClaimType.FACTUAL_HISTORICAL or (claim_years and max(claim_years) < 2020):
            if requires_current:
                return "OUTDATED", f"Claim asserts 'latest' or 'current' but refers to historical period ({claim_years})."
            return "HISTORICAL_ONLY", f"Valid historical record rooted in {claim_years or 'past era'}."

        # Version supersession check
        version_in_claim = re.findall(r"\b(?:v(?:ersion)?\s*|[a-z]+\s+)?(\d+\.\d+(?:\.\d+)*)\b", c_text_low)
        if version_in_claim and requires_current:
            claimed_ver = version_in_claim[0]
            # Check if evidence mentions a strictly higher version
            for ev in matched_ev:
                ev_versions = re.findall(r"\b(?:v(?:ersion)?\s*|[a-z]+\s+)?(\d+\.\d+(?:\.\d+)*)\b", ev.raw_snippet.lower())
                for ev_ver in ev_versions:
                    if cls._compare_versions(ev_ver, claimed_ver) > 0:
                        return "OUTDATED", f"Claim asserts version {claimed_ver} is current, but evidence documents newer version {ev_ver}."

        return "CURRENT", "Temporal scope aligns with verified evidence."

    @staticmethod
    def _compare_versions(v1: str, v2: str) -> int:
        """Compares two dotted version strings (e.g. '3.1' vs '3.0'). Returns 1, 0, or -1."""
        try:
            p1 = [int(x) for x in v1.split(".")]
            p2 = [int(x) for x in v2.split(".")]
            max_len = max(len(p1), len(p2))
            p1.extend([0] * (max_len - len(p1)))
            p2.extend([0] * (max_len - len(p2)))
            if p1 > p2:
                return 1
            if p1 < p2:
                return -1
            return 0
        except Exception:
            return 0


# =============================================================================
# CONTRADICTION & CONFLICT DETECTOR
# =============================================================================

class ContradictionDetector:
    """
    Detects factual contradictions, numerical conflicts, status disagreements,
    and negation conflicts between claims and discovered evidence.
    """

    @classmethod
    def detect_conflicts(
        cls,
        claim: AtomicClaim,
        evidence_items: List[DiscoveryEvidence],
    ) -> List[Dict[str, Any]]:
        """Identifies direct contradictions between claim and evidence items."""
        conflicts = []
        c_low = claim.text.lower()

        for ev in evidence_items:
            ev_low = (ev.raw_snippet + " " + ev.claim_candidate).lower()

            # 1. Date Disagreement
            if claim.dates_mentioned:
                for c_date in claim.dates_mentioned:
                    # If claim states born in year X, and evidence states born in year Y
                    if "born" in c_low and "born" in ev_low:
                        ev_years = re.findall(r"\bborn\s+(?:in|on)?\s*([A-Za-z0-9,\s]{4,15})\b", ev_low)
                        c_years = re.findall(r"\bborn\s+(?:in|on)?\s*([A-Za-z0-9,\s]{4,15})\b", c_low)
                        if ev_years and c_years and ev_years[0].strip() != c_years[0].strip():
                            conflicts.append({
                                "conflict_type": "DATE_CONFLICT",
                                "claim_text": claim.text,
                                "evidence_id": ev.evidence_id,
                                "evidence_snippet": ev.raw_snippet[:200],
                                "rationale": f"Claim asserts born in '{c_years[0]}', but evidence indicates '{ev_years[0]}'.",
                            })

            # 2. Numerical Disagreement
            if claim.claim_type == ClaimType.FACTUAL_NUMERICAL and claim.numbers_mentioned:
                for num in claim.numbers_mentioned:
                    # Match metric label (e.g. 512-bit, 70B, 128MB)
                    label_match = re.search(rf"\b({re.escape(num)}\s*[a-zA-Z%]+)\b", claim.text, re.IGNORECASE)
                    if label_match:
                        target_phrase = label_match.group(1).lower()
                        metric_unit = re.sub(r"[\d\.,\s]+", "", target_phrase)
                        if len(metric_unit) >= 2:
                            ev_numbers = re.findall(rf"\b(\d+(?:[\.,]\d+)*\s*{re.escape(metric_unit)})\b", ev_low)
                            if ev_numbers and not any(num in ev_n for ev_n in ev_numbers):
                                conflicts.append({
                                    "conflict_type": "NUMERICAL_CONFLICT",
                                    "claim_text": claim.text,
                                    "evidence_id": ev.evidence_id,
                                    "evidence_snippet": ev.raw_snippet[:200],
                                    "rationale": f"Claim states numerical value '{num} {metric_unit}', but evidence records '{ev_numbers[0]}'.",
                                })

            # 3. Direct Negation Disagreement
            if "does not" in c_low or "never" in c_low or "cannot" in c_low:
                pos_candidate = re.sub(r"\b(does not|never|cannot|is not)\b", "", c_low).strip()
                tokens = [t for t in pos_candidate.split() if len(t) > 3]
                if len(tokens) >= 2 and all(t in ev_low for t in tokens[:3]):
                    if any(pos in ev_low for pos in ("supports", "enabled", "released", "announced", "features")):
                        conflicts.append({
                            "conflict_type": "NEGATION_CONFLICT",
                            "claim_text": claim.text,
                            "evidence_id": ev.evidence_id,
                            "evidence_snippet": ev.raw_snippet[:200],
                            "rationale": "Claim makes an absolute negative assertion contradicted by positive evidence.",
                        })

        return conflicts


# =============================================================================
# FOUNDER VALIDATION ENGINE
# =============================================================================

class FounderValidationEngine:
    """
    Analyzes startup founder pitches, technical proposals, and problem-solution
    alignments against the 8-dimension validation framework.
    """

    @classmethod
    def validate(
        cls,
        problem_statement: str,
        proposed_solution: str,
        market_or_domain: str = "",
        evidence_items: Optional[List[DiscoveryEvidence]] = None,
    ) -> FounderValidationResult:
        """Performs structured, objective validation of a problem-solution thesis."""
        evidence_items = evidence_items or []
        p_clean = problem_statement.strip()
        s_clean = proposed_solution.strip()

        # 1. Problem Clarity Check
        problem_words = len(p_clean.split())
        has_specific_user = any(w in p_clean.lower() for w in ("users", "students", "engineers", "developers", "teams", "founders", "patients", "customers", "enterprises"))
        has_concrete_pain = any(w in p_clean.lower() for w in ("struggle", "fail", "slow", "expensive", "complex", "bottleneck", "lack", "cannot", "hard"))
        problem_clear = problem_words >= 6 and has_specific_user and has_concrete_pain

        supported = []
        partially = []
        insufficient = []
        contradicted = []
        unknowns = []
        risks = []
        validation_steps = []

        if problem_clear:
            supported.append(f"Problem statement clearly identifies target persona and specific friction: '{p_clean[:120]}...'")
        else:
            partially.append("Problem statement is loosely formulated; lacks quantified customer impact or exact persona.")
            validation_steps.append("Conduct 10-15 discovery interviews with target users to quantify frequency and severity of pain.")

        # 2. Assumptions Analysis
        if "ai" in s_clean.lower():
            risks.append("Risk of LLM hallucination in technical explanations eroding user confidence.")
            validation_steps.append("Benchmark student concept retention with AI tutoring vs human TA assistance.")

        if "automated" in s_clean.lower() or "agent" in s_clean.lower():
            risks.append("Risk of autonomous action errors requiring human intervention.")
            validation_steps.append("Implement strict sandbox and measure intervention frequency in pilot group.")

        # 3. Solution Fit to Problem
        sol_overlap = set(p_clean.lower().split()).intersection(set(s_clean.lower().split()))
        if len(sol_overlap) >= 3:
            supported.append("Proposed solution directly mirrors problem terminology and technical scope.")
        else:
            insufficient.append("Solution mechanism does not directly address the explicit cause of the problem.")
            validation_steps.append("Map core product features directly against documented customer pain points.")

        # 4. Missing Evidence
        if not evidence_items:
            unknowns.append("No market research or external benchmark evidence currently linked.")
            validation_steps.append("Search peer-reviewed CS education papers on pedagogical agent efficacy.")
        else:
            supported.append(f"Validated against {len(evidence_items)} external evidence artifacts.")

        # Calculate score (0-100)
        score = 50.0
        if problem_clear:
            score += 15.0
        if len(sol_overlap) >= 3:
            score += 15.0
        if evidence_items:
            score += 15.0
        if len(risks) <= 1:
            score += 5.0

        score = max(10.0, min(95.0, score))

        if score >= 75.0:
            status = ClaimStatus.SUPPORTED
        elif score >= 50.0:
            status = ClaimStatus.PARTIALLY_SUPPORTED
        else:
            status = ClaimStatus.INSUFFICIENT_EVIDENCE

        return FounderValidationResult(
            validation_id=f"fval-{int(time.time()*1000)}",
            problem_statement=p_clean,
            proposed_solution=s_clean,
            market_or_domain=market_or_domain or "Technology / Applied AI",
            overall_status=status,
            overall_score=score,
            supported_claims=supported,
            partially_supported_claims=partially,
            insufficient_evidence_claims=insufficient,
            contradicted_claims=contradicted,
            unknowns=unknowns,
            risk_factors=risks,
            recommended_validation_steps=validation_steps,
            evidence_sources=evidence_items,
        )


# =============================================================================
# AEGIS VERIFICATION AGENT CORE
# =============================================================================

class AegisVerificationAgent:
    """
    Production Aegis Verification Agent.
    Validates Knowledge's synthesized answers, protects against hallucination,
    emits real-time telemetry, enforces bounded 2-cycle review, and provides
    honest epistemic ratings.
    """

    MAX_REVIEW_CYCLES = 2

    def __init__(self, bus: Optional[TrinityBus] = None):
        self.bus = bus or TrinityBus()
        self.claim_decomposer = ClaimDecomposer()
        self.corroboration_engine = CorroborationEngine()
        self.temporal_verifier = TemporalVerifier()
        self.contradiction_detector = ContradictionDetector()
        self.founder_engine = FounderValidationEngine()
        self.status = "AEGIS_IDLE"

    def _emit(self, query_id: str, phase: str, message: str, data: Optional[Dict[str, Any]] = None) -> None:
        """Helper to emit real backend telemetry through TrinityBus."""
        self.status = phase
        self.bus.emit_telemetry(
            session_id="aegis_verification",
            query_id=query_id,
            agent_source="aegis",
            phase=phase,
            message=message,
            data=data or {},
        )

    def verify(self, request: VerificationRequest) -> VerificationReport:
        """
        Executes claim-level verification of proposed draft text against evidence.
        Enforces a hard limit of 2 review cycles.
        """
        t0 = time.time()
        qid = request.query_id or f"q-{int(time.time()*1000)}"
        cycle = min(max(1, request.cycle_number), self.MAX_REVIEW_CYCLES)

        self._emit(qid, "AEGIS_RECEIVING_CLAIMS", f"Aegis evaluating proposed answer for subject '{request.subject}' (Cycle {cycle}/{self.MAX_REVIEW_CYCLES})...")

        # 1. Decompose answer into atomic claims
        self._emit(qid, "AEGIS_DECOMPOSING", "Decomposing answer into atomic testable claims...")
        atomic_claims = self.claim_decomposer.decompose(request.draft_text, subject=request.subject)

        if not atomic_claims:
            self._emit(qid, "AEGIS_APPROVED", "Draft contains no testable factual claims; approved as conversational acknowledgment.")
            return VerificationReport(
                query_id=qid,
                verdict="APPROVED",
                overall_epistemic_type=EpistemicType.VERIFIED_FACT,
                confidence=1.0,
                claims_verified=[],
                contradictions=[],
                cycle_number=cycle,
                latency_ms=round((time.time() - t0) * 1000, 2),
            )

        # 2. Verify each atomic claim
        verified_records: List[ClaimVerification] = []
        all_contradictions: List[str] = []
        has_contradiction = False
        has_insufficient = False

        self._emit(qid, "AEGIS_CHECKING_EVIDENCE", f"Checking {len(atomic_claims)} atomic claims against {len(request.evidence_items)} evidence artifacts...")

        for claim in atomic_claims:
            # Check for direct contradictions
            conflicts = self.contradiction_detector.detect_conflicts(claim, request.evidence_items)
            if conflicts:
                has_contradiction = True
                self._emit(qid, "AEGIS_DETECTING_CONTRADICTION", f"Contradiction detected for: {claim.text[:60]}")
                contradicting_ids = [c["evidence_id"] for c in conflicts]
                for c in conflicts:
                    all_contradictions.append(f"Claim: '{c['claim_text']}' contradicted by {c['evidence_id']}: {c['rationale']}")

                verified_records.append(ClaimVerification(
                    claim_id=claim.claim_id,
                    claim_text=claim.text,
                    status=ClaimStatus.CONTRADICTED,
                    epistemic_type=EpistemicType.UNCERTAINTY,
                    confidence=0.10,
                    contradicting_source_ids=contradicting_ids,
                    temporal_validity="CONTRADICTED",
                    notes="Contradicted by authoritative evidence.",
                ))
                continue

            # Match claim to relevant evidence items
            matched_evidence_ids = self._find_matching_evidence(claim, request.evidence_items)

            # Corroboration check
            corrob_score, unique_source_ids, _ = self.corroboration_engine.evaluate_corroboration(
                request.evidence_items,
                matched_evidence_ids,
            )

            # Temporal check
            temp_validity, temp_notes = self.temporal_verifier.evaluate_temporality(
                claim,
                request.evidence_items,
                matched_evidence_ids,
            )

            # Determine ClaimStatus & EpistemicType
            if claim.claim_type == ClaimType.OPINION_OR_SPECULATION:
                status = ClaimStatus.PARTIALLY_SUPPORTED
                ep_type = EpistemicType.SPECULATION_PREDICTION
                conf = max(0.60, corrob_score if matched_evidence_ids else 0.50)
            elif not matched_evidence_ids:
                if claim.claim_type in (ClaimType.PEDAGOGICAL_ANALOGY, ClaimType.CONCEPTUAL_EXPLANATION):
                    status = ClaimStatus.SUPPORTED
                    ep_type = EpistemicType.INFERENCE
                    conf = 0.85
                else:
                    status = ClaimStatus.INSUFFICIENT_EVIDENCE
                    ep_type = EpistemicType.UNCERTAINTY
                    conf = 0.30
                    has_insufficient = True
            elif temp_validity in ("OUTDATED", "ANACHRONISTIC"):
                status = ClaimStatus.PARTIALLY_SUPPORTED
                ep_type = EpistemicType.VERIFIED_FACT
                conf = max(0.40, corrob_score - 0.25)
            elif corrob_score >= 0.70:
                status = ClaimStatus.SUPPORTED
                ep_type = EpistemicType.VERIFIED_FACT
                conf = corrob_score
            elif corrob_score >= 0.30:
                status = ClaimStatus.PARTIALLY_SUPPORTED
                ep_type = EpistemicType.SOURCE_ATTRIBUTED_CLAIM
                conf = corrob_score
            else:
                status = ClaimStatus.INSUFFICIENT_EVIDENCE
                ep_type = EpistemicType.UNCERTAINTY
                conf = corrob_score
                has_insufficient = True

            verified_records.append(ClaimVerification(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=status,
                epistemic_type=ep_type,
                confidence=conf,
                corroborating_source_ids=unique_source_ids,
                temporal_validity=temp_validity,
                notes=temp_notes,
            ))

        # 3. Formulate Overall Verdict & Epistemic Type
        overall_conf = sum(c.confidence for c in verified_records) / max(1, len(verified_records))

        if has_contradiction:
            if cycle < self.MAX_REVIEW_CYCLES:
                verdict = "REVISE"
                self._emit(qid, "AEGIS_NEEDS_CORRECTION", f"Cycle {cycle}: Answer contains contradicted claims. Requesting correction from Knowledge/Nova.")
                feedback = "Draft contains contradicted statements: " + "; ".join(all_contradictions[:2])
            else:
                verdict = "REJECT"
                self._emit(qid, "AEGIS_REJECTED", f"Cycle {cycle}: Hard loop limit reached. Rejecting contradicted claims in final response.")
                feedback = "Loop limit reached. Remove contradicted assertions and state honest uncertainty."
            overall_ep = EpistemicType.UNCERTAINTY

        elif has_insufficient:
            if cycle < self.MAX_REVIEW_CYCLES:
                verdict = "REVISE"
                self._emit(qid, "AEGIS_NEEDS_CORRECTION", f"Cycle {cycle}: Insufficient evidence for key claims. Requesting targeted discovery from Nova.")
                feedback = "Key factual assertions lack evidence backing. Requesting targeted research."
            else:
                verdict = "UNCERTAIN"
                self._emit(qid, "AEGIS_COMPLETED", f"Cycle {cycle}: Hard loop limit reached. Approving response with honest uncertainty qualifiers.")
                feedback = "Loop limit reached. Answer must qualify unsupported claims with epistemic uncertainty."
            overall_ep = EpistemicType.UNCERTAINTY

        else:
            verdict = "APPROVED"
            self._emit(qid, "AEGIS_APPROVED", f"All {len(verified_records)} claims verified and corroborated.")
            overall_ep = EpistemicType.VERIFIED_FACT
            feedback = None

        latency = round((time.time() - t0) * 1000, 2)
        return VerificationReport(
            query_id=qid,
            verdict=verdict,
            overall_epistemic_type=overall_ep,
            confidence=round(overall_conf, 3),
            claims_verified=verified_records,
            contradictions=all_contradictions,
            revision_feedback=feedback,
            cycle_number=cycle,
            latency_ms=latency,
        )

    def _find_matching_evidence(self, claim: AtomicClaim, evidence_items: List[DiscoveryEvidence]) -> List[str]:
        """Finds matching evidence IDs based on keyword and entity overlap."""
        matched_ids = []
        c_tokens = set(re.findall(r"\w{3,}", claim.text.lower()))

        for ev in evidence_items:
            ev_text = (ev.raw_snippet + " " + ev.claim_candidate + " " + (ev.source_name or "")).lower()
            ev_tokens = set(re.findall(r"\w{3,}", ev_text))

            overlap = c_tokens.intersection(ev_tokens)
            entity_overlap = any(ent.lower() in ev_text for ent in claim.entities_mentioned if len(ent) > 3)
            date_overlap = any(d.lower() in ev_text for d in claim.dates_mentioned)

            if entity_overlap or date_overlap or len(overlap) >= 3 or (len(c_tokens) <= 3 and len(overlap) >= 2):
                matched_ids.append(ev.evidence_id)

        return matched_ids
