"""
NR-AI Knowledge Trinity: Internal Message Protocol & Telemetry Bus.
NOVA (Discovery) + KNOWLEDGE (Coordination) + AEGIS (Verification)

Provides a strongly-typed, thread-safe asynchronous message bus for:
- Discovery requests and responses between Knowledge and Nova
- Verification requests and reports between Knowledge and Aegis
- Real-time telemetry events emitted directly to the UI (zero fake animation)
"""

from dataclasses import asdict, dataclass, field
from enum import Enum
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from app.knowledge.taxonomy import EpistemicBadge, EpistemicType
from app.knowledge.trinity.schemas import (
    ClaimVerification,
    DiscoveryEvidence,
    MediaItem,
)

logger = logging.getLogger("NRAI.KnowledgeTrinity.Protocol")


class TrinityMessageType(str, Enum):
    """Enumeration of message types flowing through the Trinity Bus."""
    DISCOVERY_REQUEST = "DISCOVERY_REQUEST"
    DISCOVERY_RESPONSE = "DISCOVERY_RESPONSE"
    VERIFICATION_REQUEST = "VERIFICATION_REQUEST"
    VERIFICATION_REPORT = "VERIFICATION_REPORT"
    TELEMETRY_EVENT = "TELEMETRY_EVENT"


@dataclass
class DiscoveryRequest:
    """Dispatched by Knowledge to task Nova with multi-source evidence gathering."""
    session_id: str
    query_id: str
    primary_subject: str
    target_attributes: List[str] = field(default_factory=list)
    decomposed_queries: List[str] = field(default_factory=list)
    freshness_required: bool = False
    include_media: bool = True
    max_sources: int = 5
    timeout_seconds: float = 6.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "query_id": self.query_id,
            "primary_subject": self.primary_subject,
            "target_attributes": self.target_attributes,
            "decomposed_queries": self.decomposed_queries,
            "freshness_required": self.freshness_required,
            "include_media": self.include_media,
            "max_sources": self.max_sources,
            "timeout_seconds": self.timeout_seconds,
            "timestamp": self.timestamp,
        }


@dataclass
class DiscoveryResponse:
    """Produced by Nova containing gathered evidence, sources, and discovered media."""
    query_id: str
    status: str  # SUCCESS, PARTIAL, NO_DATA, TIMEOUT, ERROR
    evidence_items: List[DiscoveryEvidence] = field(default_factory=list)
    discovered_media: List[MediaItem] = field(default_factory=list)
    latency_ms: float = 0.0
    message: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query_id": self.query_id,
            "status": self.status,
            "evidence_items": [e.to_dict() for e in self.evidence_items],
            "discovered_media": [m.to_dict() for m in self.discovered_media],
            "latency_ms": self.latency_ms,
            "message": self.message,
            "timestamp": self.timestamp,
        }


@dataclass
class VerificationRequest:
    """Dispatched by Knowledge to task Aegis with validating claims and draft answers."""
    query_id: str
    draft_text: str
    subject: str
    evidence_items: List[DiscoveryEvidence] = field(default_factory=list)
    cycle_number: int = 1  # Strictly 1 or 2 (maximum 2 review cycles allowed)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query_id": self.query_id,
            "draft_text": self.draft_text,
            "subject": self.subject,
            "evidence_items": [e.to_dict() for e in self.evidence_items],
            "cycle_number": self.cycle_number,
            "timestamp": self.timestamp,
        }


@dataclass
class VerificationReport:
    """Produced by Aegis providing claim-level certifications, verdicts, and loop bounds."""
    query_id: str
    verdict: str  # APPROVED, REVISE, REJECT, UNCERTAIN
    overall_epistemic_type: EpistemicType = EpistemicType.VERIFIED_FACT
    confidence: float = 1.0
    claims_verified: List[ClaimVerification] = field(default_factory=list)
    contradictions: List[str] = field(default_factory=list)
    revision_feedback: Optional[str] = None
    cycle_number: int = 1
    latency_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query_id": self.query_id,
            "verdict": self.verdict,
            "overall_epistemic_type": self.overall_epistemic_type.value,
            "confidence": self.confidence,
            "claims_verified": [c.to_dict() for c in self.claims_verified],
            "contradictions": self.contradictions,
            "revision_feedback": self.revision_feedback,
            "cycle_number": self.cycle_number,
            "latency_ms": self.latency_ms,
            "timestamp": self.timestamp,
        }


@dataclass
class TrinityTelemetryEvent:
    """Real-time telemetry event emitted during active computation."""
    event_id: str
    session_id: str
    query_id: str
    agent_source: str  # "nova", "aegis", "knowledge"
    phase: str         # "NOVA_DISCOVERY", "AEGIS_VERIFYING", "KNOWLEDGE_SYNTHESIS", "COMPLETED"
    message: str       # e.g., "Searching arXiv API...", "Verifying 4 claims...", "Drafting response..."
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "query_id": self.query_id,
            "agent_source": self.agent_source,
            "phase": self.phase,
            "message": self.message,
            "data": self.data,
            "timestamp": self.timestamp,
        }


class TrinityBus:
    """
    In-process, thread-safe asynchronous message bus and telemetry dispatcher
    coordinating Knowledge, Nova, and Aegis.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._telemetry_listeners: List[Callable[[TrinityTelemetryEvent], None]] = []
        self._history: List[Dict[str, Any]] = []
        self._max_history = 200

    def subscribe_telemetry(self, callback: Callable[[TrinityTelemetryEvent], None]) -> None:
        """Registers a callback to receive real-time telemetry events."""
        with self._lock:
            if callback not in self._telemetry_listeners:
                self._telemetry_listeners.append(callback)

    def unsubscribe_telemetry(self, callback: Callable[[TrinityTelemetryEvent], None]) -> None:
        """Unregisters a telemetry callback."""
        with self._lock:
            if callback in self._telemetry_listeners:
                self._telemetry_listeners.remove(callback)

    def emit_telemetry(
        self,
        session_id: str,
        query_id: str,
        agent_source: str,
        phase: str,
        message: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> TrinityTelemetryEvent:
        """
        Emits a real-time telemetry event to all registered listeners.
        Enforces: NO fake events, only actual execution state changes.
        """
        evt = TrinityTelemetryEvent(
            event_id=f"evt-{int(time.time()*1000)}-{abs(hash(message)) % 10000}",
            session_id=session_id,
            query_id=query_id,
            agent_source=agent_source,
            phase=phase,
            message=message,
            data=data or {},
            timestamp=time.time(),
        )

        with self._lock:
            listeners = list(self._telemetry_listeners)
            self._history.append({
                "type": TrinityMessageType.TELEMETRY_EVENT.value,
                "event": evt.to_dict(),
            })
            if len(self._history) > self._max_history:
                self._history.pop(0)

        for listener in listeners:
            try:
                listener(evt)
            except Exception as e:
                logger.warning(f"Telemetry listener error: {e}")

        return evt

    def get_recent_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Returns recent bus activity for auditing and UI display."""
        with self._lock:
            return list(self._history[-limit:])

    def clear_history(self) -> None:
        """Clears execution history."""
        with self._lock:
            self._history.clear()
