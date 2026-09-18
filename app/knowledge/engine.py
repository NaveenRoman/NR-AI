"""
NR-AI Universal Knowledge & Continuous Learning Brain Engine.

Central facade coordinating:
- Epistemic classification and badges.
- Local Hybrid Knowledge Store (SQLite3 FTS5 BM25 search).
- Autonomous multi-hop web & scholarly research (ArXiv, Wikipedia, DuckDuckGo).
- Contradiction and conflict detection.
- Background continuous learning and news ingestion scheduler.
- Integration into Desktop Companion and Mobile Companion endpoints.
"""

import logging
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional

from app.agent.news_agent import NewsAgent
from app.knowledge.contradiction import ContradictionDetector
from app.knowledge.graph import KnowledgeGraph
from app.knowledge.grounding import AnswerGroundingGate
from app.knowledge.query_understanding import QueryUnderstandingEngine
from app.knowledge.research import ResearchEngine
from app.knowledge.scheduler import ContinuousLearningScheduler
from app.knowledge.seeds import populate_knowledge_store
from app.knowledge.store import HybridKnowledgeStore
from app.knowledge.taxonomy import (
    EpistemicBadge,
    EpistemicType,
    KnowledgeDomain,
    KnowledgeNode,
    KnowledgeSource,
    ResearchReport,
)
from app.knowledge.timeline import KnowledgeTimelineEngine, TimelineEvent

logger = logging.getLogger("NRAI.UniversalKnowledgeEngine")


class UniversalKnowledgeEngine:
    """
    Central Cognitive Brain for Universal Knowledge and Continuous Learning.
    Coordinates Query Understanding, Grounding Gate, Timeline (1880–2026),
    Knowledge Graph, Hybrid Store, and Multi-Source Research.
    """

    def __init__(
        self,
        workspace: Optional[str] = None,
        db_path: Optional[str] = None,
        auto_seed: bool = True,
        auto_start_scheduler: bool = False,
    ):
        self.workspace = Path(workspace or os.getcwd()).resolve()
        self.store = HybridKnowledgeStore(db_path=db_path, workspace=str(self.workspace))
        self.news_agent = NewsAgent()
        self.research_engine = ResearchEngine(store=self.store)
        self.query_understanding = self.research_engine.query_understanding
        self.timeline = self.research_engine.timeline
        self.graph = self.research_engine.graph
        self.grounding_gate = AnswerGroundingGate
        self.contradiction_detector = ContradictionDetector(store=self.store)
        self.scheduler = ContinuousLearningScheduler(
            store=self.store,
            news_agent=self.news_agent,
        )

        from app.knowledge.trinity.aegis import AegisVerificationAgent
        from app.knowledge.trinity.nova import NovaDiscoveryAgent
        from app.knowledge.trinity.protocol import TrinityBus
        from app.knowledge.trinity.coordinator import KnowledgeTrinityCoordinator
        self.trinity_bus = TrinityBus()
        self.aegis = AegisVerificationAgent(bus=self.trinity_bus)
        self.nova = NovaDiscoveryAgent(bus=self.trinity_bus)
        self.coordinator = KnowledgeTrinityCoordinator(
            bus=self.trinity_bus,
            nova=self.nova,
            aegis=self.aegis,
            knowledge_store=self.store,
            research_engine=self.research_engine,
        )

        if auto_seed:
            populate_knowledge_store(self.store, force=False)

        if auto_start_scheduler:
            self.scheduler.start()

    def query(
        self,
        text: str,
        domain: Optional[str] = None,
        allow_web: bool = True,
        session_context: Optional[Dict[str, Any]] = None,
    ) -> ResearchReport:
        """
        Executes an epistemic knowledge lookup or multi-hop research inquiry.
        """
        return self.research_engine.research(
            query=text,
            domain=domain,
            allow_web=allow_web,
            session_context=session_context,
        )

    def query_speech(self, text: str, session_context: Optional[Dict[str, Any]] = None) -> str:
        """Returns a concise, voice-synthesizable text response with epistemic context."""
        report = self.query(text, session_context=session_context)
        return report.format_speech()

    def should_verify(self, text: str, draft_text: str = "") -> bool:
        """
        Determines deterministically if a proposed answer warrants Aegis verification.
        High-stakes triggers: numbers, dates, technology specifications,
        current/latest queries, research papers, people, or unknown entities.
        """
        q_low = text.strip().lower()
        greetings = {
            "hello", "hi", "hey", "good morning", "good afternoon",
            "good evening", "how are you", "what's up", "thanks", "thank you",
            "bye", "goodbye", "help", "who are you"
        }
        if q_low in greetings or (len(q_low.split()) <= 2 and any(g in q_low for g in ("hello", "hi", "hey"))):
            return False

        triggers = (
            "latest", "currently", "specifications", "specs",
            "release", "version", "invented", "created", "born", "died",
            "paper", "research", "quantum", "gpt", "model", "percent", "%",
            "who is", "who was", "who invented", "who created", "founded",
        )
        combined = (text + " " + draft_text).lower()
        if any(t in combined for t in triggers):
            return True

        if any(w in q_low for w in ("today", "yesterday", "tomorrow", "this year", "now")):
            return True

        return False

    def verify_answer(
        self,
        query: str,
        draft_text: str,
        subject: str = "",
        evidence_items: Optional[List[Any]] = None,
        cycle: int = 1,
    ) -> Any:
        """Runs proposed answer through Aegis verification gate."""
        from app.knowledge.trinity.protocol import VerificationRequest
        req = VerificationRequest(
            query_id=f"q-{int(time.time()*1000)}",
            draft_text=draft_text,
            subject=subject or query,
            evidence_items=evidence_items or [],
            cycle_number=cycle,
        )
        return self.aegis.verify(req)

    def query_companion_card(self, text: str, session_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Generates a structured payload optimized for the mobile companion app,
        desktop avatar, and WebSocket telemetry streams via KnowledgeTrinityCoordinator.
        """
        t_resp = self.coordinator.coordinate(text, session_context=session_context)
        badge_dict = t_resp.badge.to_dict() if t_resp.badge else {}

        return {
            "query": t_resp.query,
            "speech_text": t_resp.speech_text,
            "display_text": t_resp.primary_answer,
            "markdown": t_resp.markdown,
            "epistemic_type": t_resp.epistemic_type.value,
            "badge": badge_dict,
            "confidence": t_resp.confidence,
            "sources": t_resp.sources,
            "evidence_items": [e.to_dict() for e in t_resp.evidence_items],
            "media_items": [m.to_dict() for m in t_resp.media_items],
            "verification_report": t_resp.verification_report.to_dict() if t_resp.verification_report else None,
            "collaboration_block": t_resp.collaboration_block,
            "review_cycles": t_resp.review_cycles,
            "retrieval_tier": t_resp.route_category.value,
            "latency_ms": t_resp.latency_ms,
            "nodes_consulted": len(t_resp.sources),
            "timestamp": time.time(),
        }

    def query_timeline_year(self, year: int) -> List[TimelineEvent]:
        """Queries historical continuum events for a given year."""
        return self.timeline.lookup_year(year)

    def query_evolution(self, topic: str, start_year: int = 1880, end_year: int = 2026) -> str:
        """Synthesizes chronological technological or historical evolution for a topic."""
        return self.timeline.synthesize_evolution(topic, start_year, end_year)

    def query_graph(self, subject: str, attribute_or_relation: str) -> List[Dict[str, Any]]:
        """Queries the entity-relationship knowledge graph."""
        return self.graph.query_attribute(subject, attribute_or_relation)

    def start_scheduler(self) -> None:
        """Starts background continuous learning worker."""
        self.scheduler.start()

    def stop_scheduler(self) -> None:
        """Stops background continuous learning worker."""
        self.scheduler.stop()

    def get_status(self) -> Dict[str, Any]:
        """Returns runtime diagnostics across knowledge subsystems."""
        return {
            "engine": "UniversalKnowledgeEngine",
            "total_nodes": self.store.count_nodes(),
            "domains": self.store.list_domains(),
            "scheduler_stats": self.scheduler.get_stats(),
            "db_path": self.store.db_path,
        }
