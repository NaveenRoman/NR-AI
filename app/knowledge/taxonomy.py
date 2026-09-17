"""
NR-AI Epistemic Taxonomy & Universal Knowledge Data Models.

Defines the epistemic classification system, knowledge nodes, source attributions,
and structured research report contracts for the Universal Knowledge Brain.
Ensures zero hallucinations by tagging every response with verified epistemic metadata:
- VERIFIED FACT
- CURRENT INFORMATION
- SOURCE-ATTRIBUTED CLAIM
- INFERENCE
- UNCERTAINTY
- SPECULATION/PREDICTION
"""

from dataclasses import dataclass, field
from enum import Enum
import json
import time
from typing import Any, Dict, List, Optional, Set


# =============================================================================
# EPISTEMIC CLASSIFICATION TAXONOMY
# =============================================================================

class EpistemicType(str, Enum):
    """
    Core Epistemic Classifications for NR-AI Knowledge.
    Distinguishes established truth, real-time states, attributed claims,
    inferences, known uncertainties, and forward-looking speculations.
    """
    VERIFIED_FACT = "VERIFIED_FACT"
    CURRENT_INFORMATION = "CURRENT_INFORMATION"
    SOURCE_ATTRIBUTED_CLAIM = "SOURCE_ATTRIBUTED_CLAIM"
    INFERENCE = "INFERENCE"
    UNCERTAINTY = "UNCERTAINTY"
    SPECULATION_PREDICTION = "SPECULATION_PREDICTION"


class KnowledgeDomain(str, Enum):
    """Supported Knowledge Domains in NR-AI Universal Knowledge Brain."""
    HISTORY = "history"
    STEM = "stem"
    COMPUTER_SCIENCE = "computer_science"
    AI_ML = "ai_ml"
    ROBOTICS = "robotics"
    CYBERSECURITY = "cybersecurity"
    NETWORKING = "networking"
    DATABASES = "databases"
    CLOUD = "cloud"
    BUSINESS = "business"
    EDUCATION = "education"
    SPACE = "space"
    ELECTRONICS = "electronics"
    HARDWARE = "hardware"
    CURRENT_EVENTS = "current_events"
    FUTURE_TECH = "future_tech"
    GENERAL = "general"


# Human-readable labels and badge styling
EPISTEMIC_METADATA: Dict[EpistemicType, Dict[str, str]] = {
    EpistemicType.VERIFIED_FACT: {
        "label": "VERIFIED FACT",
        "badge": "[VERIFIED FACT]",
        "color": "#10B981",  # Emerald Green
        "description": "Empirically validated, mathematically sound, or established historical consensus.",
    },
    EpistemicType.CURRENT_INFORMATION: {
        "label": "CURRENT INFO",
        "badge": "[CURRENT INFORMATION]",
        "color": "#3B82F6",  # Royal Blue
        "description": "Real-time state or recent development subject to change (e.g. today's market, latest release).",
    },
    EpistemicType.SOURCE_ATTRIBUTED_CLAIM: {
        "label": "ATTRIBUTED CLAIM",
        "badge": "[SOURCE-ATTRIBUTED CLAIM]",
        "color": "#F59E0B",  # Amber
        "description": "Statement or metric directly attributed to a specific creator, institution, or publisher.",
    },
    EpistemicType.INFERENCE: {
        "label": "INFERENCE",
        "badge": "[INFERENCE]",
        "color": "#8B5CF6",  # Purple
        "description": "Logical deduction or synthesis derived from established facts.",
    },
    EpistemicType.UNCERTAINTY: {
        "label": "UNCERTAINTY",
        "badge": "[UNCERTAINTY]",
        "color": "#6B7280",  # Slate Gray
        "description": "Inconclusive data, competing hypotheses, or known knowledge gaps.",
    },
    EpistemicType.SPECULATION_PREDICTION: {
        "label": "SPECULATION / PREDICTION",
        "badge": "[SPECULATION/PREDICTION]",
        "color": "#EC4899",  # Pink / Magenta
        "description": "Forward-looking forecast, AGI/ASI theory, or speculative scenario based on present trends.",
    },
}


@dataclass
class EpistemicBadge:
    """Badge representation for display on UI, mobile companion, and terminal."""

    badge_type: EpistemicType
    confidence: float = 1.0
    label: str = ""
    color: str = ""
    description: str = ""

    def __post_init__(self):
        meta = EPISTEMIC_METADATA.get(self.badge_type, {})
        if not self.label:
            self.label = meta.get("label", self.badge_type.value)
        if not self.color:
            self.color = meta.get("color", "#6B7280")
        if not self.description:
            self.description = meta.get("description", "")

    @classmethod
    def from_type(cls, epistemic_type: EpistemicType, confidence: float = 1.0) -> "EpistemicBadge":
        return cls(badge_type=epistemic_type, confidence=max(0.0, min(1.0, confidence)))

    def format_tag(self) -> str:
        conf_pct = int(self.confidence * 100)
        return f"[{self.label} | {conf_pct}%]"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.badge_type.value,
            "label": self.label,
            "confidence": round(self.confidence, 3),
            "color": self.color,
            "description": self.description,
            "tag": self.format_tag(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EpistemicBadge":
        raw_type = data.get("type", EpistemicType.VERIFIED_FACT.value)
        try:
            b_type = EpistemicType(raw_type)
        except ValueError:
            b_type = EpistemicType.VERIFIED_FACT
        return cls(
            badge_type=b_type,
            confidence=float(data.get("confidence", 1.0)),
            label=data.get("label", ""),
            color=data.get("color", ""),
            description=data.get("description", ""),
        )


# =============================================================================
# KNOWLEDGE SOURCE
# =============================================================================

@dataclass
class KnowledgeSource:
    """Attribution metadata for a specific knowledge source."""

    name: str
    url: Optional[str] = None
    publisher: str = "Unknown"
    published_date: Optional[str] = None
    reliability_weight: float = 1.0  # 0.0 to 1.0
    source_type: str = "primary"     # primary, academic, news, documentation, seed, web

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url,
            "publisher": self.publisher,
            "published_date": self.published_date,
            "reliability_weight": round(self.reliability_weight, 3),
            "source_type": self.source_type,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "KnowledgeSource":
        return cls(
            name=data.get("name", "Unknown Source"),
            url=data.get("url"),
            publisher=data.get("publisher", "Unknown"),
            published_date=data.get("published_date"),
            reliability_weight=float(data.get("reliability_weight", 1.0)),
            source_type=data.get("source_type", "primary"),
        )


# =============================================================================
# KNOWLEDGE NODE (ATOMIC STORED FACT)
# =============================================================================

@dataclass
class KnowledgeNode:
    """
    Atomic unit of knowledge stored in the Hybrid Knowledge Store.
    Supports full-text indexing, domain filtering, and epistemic verification.
    """

    node_id: str
    domain: str
    topic: str
    title: str
    content: str
    epistemic_type: EpistemicType = EpistemicType.VERIFIED_FACT
    confidence: float = 1.0
    sources: List[KnowledgeSource] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    temporal_anchor: Optional[str] = None  # e.g. "1873", "2026-09", "1969-07-20"
    ttl_seconds: Optional[int] = None      # None = permanent; integer = expires after ttl
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_expired(self, current_time: Optional[float] = None) -> bool:
        """Returns True if the node has an active TTL that has elapsed."""
        if self.ttl_seconds is None:
            return False
        now = current_time or time.time()
        return (now - self.updated_at) > self.ttl_seconds

    def format_card(self) -> str:
        """Formats a human-readable epistemic card representation."""
        badge = EpistemicBadge.from_type(self.epistemic_type, self.confidence)
        lines = [
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"{badge.format_tag()} {self.title}",
            f"Domain: {self.domain} | Topic: {self.topic}",
        ]
        if self.temporal_anchor:
            lines.append(f"Temporal Anchor: {self.temporal_anchor}")
        lines.append(f"Content: {self.content}")
        if self.sources:
            src_names = [s.name for s in self.sources]
            lines.append(f"Sources: {', '.join(src_names)}")
        lines.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "domain": self.domain,
            "topic": self.topic,
            "title": self.title,
            "content": self.content,
            "epistemic_type": self.epistemic_type.value,
            "confidence": round(self.confidence, 3),
            "sources": [s.to_dict() for s in self.sources],
            "tags": self.tags,
            "temporal_anchor": self.temporal_anchor,
            "ttl_seconds": self.ttl_seconds,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "KnowledgeNode":
        raw_type = data.get("epistemic_type", EpistemicType.VERIFIED_FACT.value)
        try:
            e_type = EpistemicType(raw_type)
        except ValueError:
            e_type = EpistemicType.VERIFIED_FACT

        raw_sources = data.get("sources", [])
        sources = [KnowledgeSource.from_dict(s) if isinstance(s, dict) else KnowledgeSource(name=str(s)) for s in raw_sources]

        return cls(
            node_id=str(data.get("node_id", "")),
            domain=str(data.get("domain", "general")),
            topic=str(data.get("topic", "general")),
            title=str(data.get("title", "")),
            content=str(data.get("content", "")),
            epistemic_type=e_type,
            confidence=float(data.get("confidence", 1.0)),
            sources=sources,
            tags=list(data.get("tags", [])),
            temporal_anchor=data.get("temporal_anchor"),
            ttl_seconds=data.get("ttl_seconds"),
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
            metadata=dict(data.get("metadata", {})),
        )


# =============================================================================
# STRUCTURED RESEARCH REPORT
# =============================================================================

@dataclass
class ResearchReport:
    """
    Structured outcome of a research or knowledge retrieval query.
    Delivered to the user, the mobile companion, and TTS voice synthesis.
    """

    query: str
    primary_answer: str
    epistemic_type: EpistemicType
    confidence: float
    badges: List[EpistemicBadge] = field(default_factory=list)
    nodes_consulted: List[str] = field(default_factory=list)
    sources: List[KnowledgeSource] = field(default_factory=list)
    contradictions_found: List[str] = field(default_factory=list)
    retrieval_tier: str = "local_store"  # local_store, web_research, multi_model_consensus
    latency_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self):
        if not self.badges:
            self.badges = [EpistemicBadge.from_type(self.epistemic_type, self.confidence)]

    def format_speech(self) -> str:
        """
        Creates a clean, conversational text-to-speech snippet.
        Omits heavy markup and URLs while preserving epistemic nuance.
        """
        prefix = ""
        if self.epistemic_type == EpistemicType.CURRENT_INFORMATION:
            prefix = "Based on current information: "
        elif self.epistemic_type == EpistemicType.SOURCE_ATTRIBUTED_CLAIM:
            if self.sources:
                prefix = f"According to {self.sources[0].name}: "
            else:
                prefix = "According to reported claims: "
        elif self.epistemic_type == EpistemicType.INFERENCE:
            prefix = "Based on available data, the likely conclusion is: "
        elif self.epistemic_type == EpistemicType.UNCERTAINTY:
            prefix = "There is uncertainty on this topic: "
        elif self.epistemic_type == EpistemicType.SPECULATION_PREDICTION:
            prefix = "As a projection or speculation: "

        # Clean citations like [1] or (http...)
        import re
        clean = re.sub(r"\[\d+\]", "", self.primary_answer)
        clean = re.sub(r"https?://\S+", "", clean).strip()
        return f"{prefix}{clean}"

    def format_markdown(self) -> str:
        """Creates a formatted markdown block with epistemic badges and citations."""
        badge = self.badges[0] if self.badges else EpistemicBadge.from_type(self.epistemic_type, self.confidence)
        lines = [
            f"### {badge.format_tag()}",
            f"",
            self.primary_answer,
            f"",
        ]
        if self.contradictions_found:
            lines.append("> [!WARNING]")
            lines.append("> **Conflicting Sources Detected**:")
            for c in self.contradictions_found:
                lines.append(f"> - {c}")
            lines.append("")

        if self.sources:
            lines.append("#### Sources & Evidence:")
            for idx, s in enumerate(self.sources, 1):
                if s.url:
                    lines.append(f"{idx}. [{s.name}]({s.url}) ({s.publisher})")
                else:
                    lines.append(f"{idx}. **{s.name}** ({s.publisher})")

        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "primary_answer": self.primary_answer,
            "epistemic_type": self.epistemic_type.value,
            "confidence": round(self.confidence, 3),
            "badges": [b.to_dict() for b in self.badges],
            "nodes_consulted": self.nodes_consulted,
            "sources": [s.to_dict() for s in self.sources],
            "contradictions_found": self.contradictions_found,
            "retrieval_tier": self.retrieval_tier,
            "latency_ms": round(self.latency_ms, 2),
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ResearchReport":
        raw_type = data.get("epistemic_type", EpistemicType.VERIFIED_FACT.value)
        try:
            e_type = EpistemicType(raw_type)
        except ValueError:
            e_type = EpistemicType.VERIFIED_FACT

        badges = [EpistemicBadge.from_dict(b) for b in data.get("badges", [])]
        sources = [KnowledgeSource.from_dict(s) for s in data.get("sources", [])]

        return cls(
            query=str(data.get("query", "")),
            primary_answer=str(data.get("primary_answer", "")),
            epistemic_type=e_type,
            confidence=float(data.get("confidence", 1.0)),
            badges=badges,
            nodes_consulted=list(data.get("nodes_consulted", [])),
            sources=sources,
            contradictions_found=list(data.get("contradictions_found", [])),
            retrieval_tier=str(data.get("retrieval_tier", "local_store")),
            latency_ms=float(data.get("latency_ms", 0.0)),
            timestamp=float(data.get("timestamp", time.time())),
        )
