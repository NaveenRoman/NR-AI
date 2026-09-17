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

logger = logging.getLogger("NRAI.UniversalKnowledgeEngine")


class UniversalKnowledgeEngine:
    """
    Central Cognitive Brain for Universal Knowledge and Continuous Learning.
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
    ) -> ResearchReport:
        """
        Executes an epistemic knowledge lookup or multi-hop research inquiry.
        """
        return self.research_engine.research(
            query=text,
            domain=domain,
            allow_web=allow_web,
        )

    def query_speech(self, text: str) -> str:
        """Returns a concise, voice-synthesizable text response with epistemic context."""
        report = self.query(text)
        return report.format_speech()

    def query_companion_card(self, text: str) -> Dict[str, Any]:
        """
        Generates a structured payload optimized for the mobile companion app,
        desktop avatar, and WebSocket telemetry streams.
        """
        report = self.query(text)
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
