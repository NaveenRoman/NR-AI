"""
NR-AI Knowledge Trinity: Core Data Schemas.
NOVA (Discovery) + KNOWLEDGE (Coordination) + AEGIS (Verification)

Defines strongly-typed, verifiable, immutable data structures for:
- Evidence collection with authority tiering and provenance
- Structured multimedia discovery (diagrams, videos, repositories, docs)
- Atomic claim verification with epistemic classification
- Founder problem-solution validation matrices
"""

from dataclasses import asdict, dataclass, field
from enum import Enum
import time
from typing import Any, Dict, List, Optional

from app.knowledge.taxonomy import EpistemicBadge, EpistemicType


class SourceAuthorityTier(str, Enum):
    """Hierarchical reliability grading of information sources."""
    PRIMARY_CANONICAL = "PRIMARY_CANONICAL"      # Peer-reviewed paper, RFC, official standard, legal registry, release note
    AUTHORITATIVE_ORG = "AUTHORITATIVE_ORG"      # Stanford, MIT, OpenAI official blog, W3C, ISO, government databases
    RELIABLE_SECONDARY = "RELIABLE_SECONDARY"    # Wikipedia with citations, Reuters, AP, Bloomberg, BBC, official news feeds
    TECHNICAL_COMMUNITY = "TECHNICAL_COMMUNITY"  # High-reputation developer communities, GitHub README, StackOverflow
    UNVERIFIED_WEB = "UNVERIFIED_WEB"            # Personal blogs, marketing PR, social media, unvetted forums


class MediaType(str, Enum):
    """Types of rich multimedia assets discovered by Nova."""
    IMAGE_DIAGRAM = "IMAGE_DIAGRAM"
    VIDEO_EXPLAINER = "VIDEO_EXPLAINER"
    OFFICIAL_REPO = "OFFICIAL_REPO"
    DOCUMENTATION = "DOCUMENTATION"
    SCHOLARLY_PDF = "SCHOLARLY_PDF"
    ARTICLE = "ARTICLE"


@dataclass
class MediaItem:
    """Structured multimedia asset with validation and licensing metadata."""
    media_id: str
    media_type: MediaType
    url: str
    title: str
    description: str = ""
    thumbnail_url: Optional[str] = None
    publisher: str = ""
    published_date: Optional[str] = None
    license: str = "Public/Referential"
    verified: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "media_id": self.media_id,
            "media_type": self.media_type.value,
            "url": self.url,
            "title": self.title,
            "description": self.description,
            "thumbnail_url": self.thumbnail_url,
            "publisher": self.publisher,
            "published_date": self.published_date,
            "license": self.license,
            "verified": self.verified,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MediaItem":
        m_type = data.get("media_type", MediaType.ARTICLE)
        if isinstance(m_type, str):
            try:
                m_type = MediaType(m_type)
            except ValueError:
                m_type = MediaType.ARTICLE
        return cls(
            media_id=data.get("media_id", ""),
            media_type=m_type,
            url=data.get("url", ""),
            title=data.get("title", ""),
            description=data.get("description", ""),
            thumbnail_url=data.get("thumbnail_url"),
            publisher=data.get("publisher", ""),
            published_date=data.get("published_date"),
            license=data.get("license", "Public/Referential"),
            verified=bool(data.get("verified", False)),
        )


@dataclass
class DiscoveryEvidence:
    """Atomic evidence artifact discovered by Nova with full provenance."""
    evidence_id: str
    query_id: str
    claim_candidate: str
    source_name: str
    source_url: Optional[str] = None
    publisher: str = ""
    publication_timestamp: Optional[float] = None
    publication_date: Optional[str] = None
    retrieved_timestamp: float = field(default_factory=time.time)
    authority_tier: SourceAuthorityTier = SourceAuthorityTier.RELIABLE_SECONDARY
    reliability_weight: float = 0.90
    freshness_score: float = 1.0
    raw_snippet: str = ""
    media_items: List[MediaItem] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "query_id": self.query_id,
            "claim_candidate": self.claim_candidate,
            "source_name": self.source_name,
            "source_url": self.source_url,
            "publisher": self.publisher,
            "publication_timestamp": self.publication_timestamp,
            "publication_date": self.publication_date,
            "retrieved_timestamp": self.retrieved_timestamp,
            "authority_tier": self.authority_tier.value,
            "reliability_weight": self.reliability_weight,
            "freshness_score": self.freshness_score,
            "raw_snippet": self.raw_snippet,
            "media_items": [m.to_dict() for m in self.media_items],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DiscoveryEvidence":
        a_tier = data.get("authority_tier", SourceAuthorityTier.RELIABLE_SECONDARY)
        if isinstance(a_tier, str):
            try:
                a_tier = SourceAuthorityTier(a_tier)
            except ValueError:
                a_tier = SourceAuthorityTier.RELIABLE_SECONDARY

        media = [MediaItem.from_dict(m) for m in data.get("media_items", [])]

        return cls(
            evidence_id=data.get("evidence_id", ""),
            query_id=data.get("query_id", ""),
            claim_candidate=data.get("claim_candidate", ""),
            source_name=data.get("source_name", ""),
            source_url=data.get("source_url"),
            publisher=data.get("publisher", ""),
            publication_timestamp=data.get("publication_timestamp"),
            publication_date=data.get("publication_date"),
            retrieved_timestamp=data.get("retrieved_timestamp", time.time()),
            authority_tier=a_tier,
            reliability_weight=float(data.get("reliability_weight", 0.90)),
            freshness_score=float(data.get("freshness_score", 1.0)),
            raw_snippet=data.get("raw_snippet", ""),
            media_items=media,
        )


class ClaimStatus(str, Enum):
    """Validation status assigned to an individual claim by Aegis."""
    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    CONTRADICTED = "CONTRADICTED"
    UNKNOWN = "UNKNOWN"


@dataclass
class ClaimVerification:
    """Claim-level verification assessment produced by Aegis."""
    claim_id: str
    claim_text: str
    status: ClaimStatus
    epistemic_type: EpistemicType
    confidence: float
    corroborating_source_ids: List[str] = field(default_factory=list)
    contradicting_source_ids: List[str] = field(default_factory=list)
    temporal_validity: str = "CURRENT"  # CURRENT, HISTORICAL_ONLY, OUTDATED, ANACHRONISTIC
    bias_rating: float = 0.0             # 0.0 (Neutral/Factual) to 1.0 (Marketing/PR bias)
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "claim_text": self.claim_text,
            "status": self.status.value,
            "epistemic_type": self.epistemic_type.value,
            "confidence": self.confidence,
            "corroborating_source_ids": self.corroborating_source_ids,
            "contradicting_source_ids": self.contradicting_source_ids,
            "temporal_validity": self.temporal_validity,
            "bias_rating": self.bias_rating,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ClaimVerification":
        st = data.get("status", ClaimStatus.UNKNOWN)
        if isinstance(st, str):
            try:
                st = ClaimStatus(st)
            except ValueError:
                st = ClaimStatus.UNKNOWN

        ep = data.get("epistemic_type", EpistemicType.UNCERTAINTY)
        if isinstance(ep, str):
            try:
                ep = EpistemicType(ep)
            except ValueError:
                ep = EpistemicType.UNCERTAINTY

        return cls(
            claim_id=data.get("claim_id", ""),
            claim_text=data.get("claim_text", ""),
            status=st,
            epistemic_type=ep,
            confidence=float(data.get("confidence", 0.0)),
            corroborating_source_ids=data.get("corroborating_source_ids", []),
            contradicting_source_ids=data.get("contradicting_source_ids", []),
            temporal_validity=data.get("temporal_validity", "CURRENT"),
            bias_rating=float(data.get("bias_rating", 0.0)),
            notes=data.get("notes", ""),
        )


@dataclass
class FounderValidationResult:
    """Structured problem-solution validation matrix for founders and architects."""
    validation_id: str
    problem_statement: str
    proposed_solution: str
    market_or_domain: str
    overall_status: ClaimStatus
    overall_score: float  # 0.0 to 100.0
    supported_claims: List[str] = field(default_factory=list)
    partially_supported_claims: List[str] = field(default_factory=list)
    insufficient_evidence_claims: List[str] = field(default_factory=list)
    contradicted_claims: List[str] = field(default_factory=list)
    unknowns: List[str] = field(default_factory=list)
    risk_factors: List[str] = field(default_factory=list)
    recommended_validation_steps: List[str] = field(default_factory=list)
    evidence_sources: List[DiscoveryEvidence] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "validation_id": self.validation_id,
            "problem_statement": self.problem_statement,
            "proposed_solution": self.proposed_solution,
            "market_or_domain": self.market_or_domain,
            "overall_status": self.overall_status.value,
            "overall_score": self.overall_score,
            "supported_claims": self.supported_claims,
            "partially_supported_claims": self.partially_supported_claims,
            "insufficient_evidence_claims": self.insufficient_evidence_claims,
            "contradicted_claims": self.contradicted_claims,
            "unknowns": self.unknowns,
            "risk_factors": self.risk_factors,
            "recommended_validation_steps": self.recommended_validation_steps,
            "evidence_sources": [e.to_dict() for e in self.evidence_sources],
            "timestamp": self.timestamp,
        }
