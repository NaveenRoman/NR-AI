"""
Jarvis Central Assistant Data Models for NR-AI.
Defines epistemic classes, source domains, provenance schemas, request/response structures.

Invariants:
- Explicit epistemic classification on every response.
- Compact provenance tracking for all retrieved evidence.
- Zero secret retention in models.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class EpistemicClass(str, Enum):
    VERIFIED_FACT = "VERIFIED_FACT"
    CURRENT_INFORMATION = "CURRENT_INFORMATION"
    SOURCE_ATTRIBUTED_CLAIM = "SOURCE_ATTRIBUTED_CLAIM"
    INFERENCE = "INFERENCE"
    UNCERTAINTY = "UNCERTAINTY"
    SPECULATION_PREDICTION = "SPECULATION_PREDICTION"


class SourceDomain(str, Enum):
    KNOWLEDGE = "KNOWLEDGE"
    GITHUB = "GITHUB"
    REPORT = "REPORT"
    PROJECT = "PROJECT"
    TASK = "TASK"
    CONVERSATION = "CONVERSATION"
    LIVE = "LIVE"
    MODEL_INFERENCE = "MODEL_INFERENCE"


@dataclass
class SourceProvenance:
    source_domain: SourceDomain
    title: str
    ref: str  # e.g. "Report 38", "Commit 9b43692", "KnowledgeTrinity:core"
    snippet: str = ""
    timestamp: Optional[float] = None
    confidence: float = 1.0
    access_scope: str = "global"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_domain": self.source_domain.value if isinstance(self.source_domain, SourceDomain) else str(self.source_domain),
            "title": self.title,
            "ref": self.ref,
            "snippet": self.snippet[:200] if self.snippet else "",
            "timestamp": self.timestamp or time.time(),
            "confidence": round(self.confidence, 3),
            "access_scope": self.access_scope,
        }

    @property
    def badge_label(self) -> str:
        """Returns compact badge string for UI display."""
        if self.source_domain == SourceDomain.GITHUB:
            return f"[Git {self.ref}]"
        elif self.source_domain == SourceDomain.REPORT:
            return f"[{self.title.upper()}]"
        elif self.source_domain == SourceDomain.KNOWLEDGE:
            return f"[KNOWLEDGE: {self.ref.upper()}]"
        elif self.source_domain == SourceDomain.LIVE:
            return "[LIVE TELEMETRY]"
        elif self.source_domain == SourceDomain.PROJECT:
            return f"[PROJECT: {self.ref}]"
        elif self.source_domain == SourceDomain.TASK:
            return f"[TASK: {self.ref}]"
        return f"[{self.source_domain.value}]"


@dataclass
class JarvisRequest:
    message: str
    session_id: str = "default_jarvis_session"
    workspace_scope: str = "global"
    include_live_state: bool = True
    context_budget_chars: int = 4000
    delegation_allowed: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message": self.message,
            "session_id": self.session_id,
            "workspace_scope": self.workspace_scope,
            "include_live_state": self.include_live_state,
            "context_budget_chars": self.context_budget_chars,
            "delegation_allowed": self.delegation_allowed,
        }


@dataclass
class JarvisResponse:
    reply: str
    sources: List[SourceProvenance] = field(default_factory=list)
    epistemic_class: EpistemicClass = EpistemicClass.SOURCE_ATTRIBUTED_CLAIM
    delegated_to: Optional[str] = None
    live_state: Dict[str, Any] = field(default_factory=dict)
    response_id: str = field(default_factory=lambda: f"jarvis_resp_{uuid.uuid4().hex[:8]}")
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "response_id": self.response_id,
            "reply": self.reply,
            "sources": [s.to_dict() for s in self.sources],
            "badges": [s.badge_label for s in self.sources],
            "epistemic_class": self.epistemic_class.value if isinstance(self.epistemic_class, EpistemicClass) else str(self.epistemic_class),
            "delegated_to": self.delegated_to,
            "live_state": self.live_state,
            "timestamp": self.timestamp,
        }
