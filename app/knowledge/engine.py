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

    def query_companion_card(self, text: str, session_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Generates a structured payload optimized for the mobile companion app,
        desktop avatar, and WebSocket telemetry streams.
        """
        report = self.query(text, session_context=session_context)
        badge = report.badges[0] if report.badges else EpistemicBadge.from_type(report.epistemic_type, report.confidence)

        return {
            "query": report.query,
            "speech_text": report.format_speech(),
            "display_text": report.primary_answer,
            "markdown": report.format_markdown(),
            "epistemic_type": report.epistemic_type.value,
            "badge": badge.to_dict(),
            "confidence": report.confidence,
            "sources": [s.to_dict() for s in report.sources],
            "retrieval_tier": report.retrieval_tier,
            "latency_ms": report.latency_ms,
            "nodes_consulted": report.nodes_consulted,
            "timestamp": report.timestamp,
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
