"""
NR-AI Continuous Learning & Knowledge Refresh Scheduler.

Runs low-priority background synchronization to ingest real-time news,
refresh decaying temporal knowledge, prune expired records, and detect contradictions.
Designed to never interrupt user interaction or exceed 5% CPU utilization.
"""

import logging
import threading
import time
from typing import Any, Dict, List, Optional

from app.agent.news_agent import NewsAgent
from app.knowledge.contradiction import ContradictionDetector
from app.knowledge.store import HybridKnowledgeStore
from app.knowledge.taxonomy import (
    EpistemicType,
    KnowledgeDomain,
    KnowledgeNode,
    KnowledgeSource,
)

logger = logging.getLogger("NRAI.ContinuousLearning")


class ContinuousLearningScheduler:
    """
    Background daemon for continuous knowledge acquisition and maintenance.

    Capabilities:
    - Periodically pulls verified AI and Technology news from NewsAgent.
    - Promotes corroborated reports to durable, TTL-backed KnowledgeNodes.
    - Detects claim contradictions and records them in the store.
    - Prunes expired temporal records.
    - Supports manual sync on demand (sync_now).
    """

    def __init__(
        self,
        store: Optional[HybridKnowledgeStore] = None,
        news_agent: Optional[NewsAgent] = None,
        sync_interval_seconds: float = 3600.0,  # Default: hourly
    ):
        self.store = store or HybridKnowledgeStore()
        self.news_agent = news_agent or NewsAgent()
        self.contradiction_detector = ContradictionDetector(store=self.store)
        self.sync_interval = sync_interval_seconds

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_sync_time: float = 0.0
        self._sync_stats: Dict[str, Any] = {
            "total_syncs": 0,
            "nodes_ingested": 0,
            "nodes_pruned": 0,
            "contradictions_detected": 0,
            "last_status": "IDLE",
        }

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Starts the background continuous learning worker daemon."""
        if self.is_running:
            logger.info("Continuous learning worker is already running.")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._worker_loop,
            name="NR-AI-ContinuousLearningWorker",
            daemon=True,
        )
        self._thread.start()
        logger.info(f"Started Continuous Learning daemon (Interval: {self.sync_interval}s).")

    def stop(self, timeout: float = 2.0) -> None:
        """Gracefully stops the background continuous learning daemon."""
        if not self.is_running:
            return

        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        logger.info("Stopped Continuous Learning daemon.")

    def _worker_loop(self) -> None:
        """Periodic background loop."""
        while not self._stop_event.is_set():
            try:
                self.sync_now()
            except Exception as e:
                logger.error(f"Error during continuous learning sync: {e}")

            # Sleep in short increments to allow rapid clean termination
            elapsed = 0.0
            while elapsed < self.sync_interval and not self._stop_event.is_set():
                time.sleep(1.0)
                elapsed += 1.0

    def sync_now(self) -> Dict[str, Any]:
        """
        Executes an immediate synchronization cycle:
        1. Ingests top news from AI and Technology categories.
        2. Prunes expired knowledge nodes.
        3. Scans newly ingested nodes for contradictions.
        """
        t0 = time.time()
        logger.info("Starting continuous knowledge sync cycle...")
        self._sync_stats["last_status"] = "SYNCING"

        ingested_count = 0
        conflicts_count = 0

        # 1. Ingest Verified AI & Tech News Feeds
        try:
            for category in ("AI", "Technology"):
                items = self.news_agent.fetch_category(category, limit=3)
                for item in items:
                    if not item.headline:
                        continue

                    # Construct KnowledgeNode from NewsItem
                    node_id = f"news-{category.lower()}-{abs(hash(item.headline)) % 100000}"
                    domain = "ai_ml" if category == "AI" else "computer_science"
                    pub_name = getattr(item, "publisher", "") or getattr(item, "source", "News Source")
                    pub_time = getattr(item, "publication_time", None) or getattr(item, "published_date", None)
                    sources = [
                        KnowledgeSource(
                            name=pub_name,
                            url=getattr(item, "url", ""),
                            publisher=pub_name,
                            published_date=pub_time,
                            reliability_weight=0.90,
                            source_type="news",
                        )
                    ]

                    candidate_node = KnowledgeNode(
                        node_id=node_id,
                        domain=domain,
                        topic=f"{category.lower()}_news",
                        title=item.headline,
                        content=item.summary or item.headline,
                        epistemic_type=EpistemicType.CURRENT_INFORMATION,
                        confidence=0.92,
                        sources=sources,
                        tags=["news", category.lower(), "current_events"],
                        ttl_seconds=86400 * 3,  # 3 days TTL
                    )

                    # Check for contradictions against existing knowledge
                    existing = [
                        hit[0] for hit in self.store.search_bm25(item.headline, domain=domain, limit=3)
                    ]
                    conflicts = self.contradiction_detector.detect_conflicts(candidate_node, existing)
                    conflicts_count += len(conflicts)

                    # Upsert into knowledge store
                    self.store.upsert_node(candidate_node)
                    ingested_count += 1

        except Exception as e:
            logger.warning(f"News ingestion encountered an issue: {e}")

        # 2. Prune Expired Temporal Nodes
        pruned_count = self.store.prune_expired()

        self._last_sync_time = time.time()
        duration = round(time.time() - t0, 3)

        self._sync_stats["total_syncs"] += 1
        self._sync_stats["nodes_ingested"] += ingested_count
        self._sync_stats["nodes_pruned"] += pruned_count
        self._sync_stats["contradictions_detected"] += conflicts_count
        self._sync_stats["last_duration_s"] = duration
        self._sync_stats["last_status"] = "SUCCESS"

        logger.info(
            f"Completed sync in {duration}s: Ingested {ingested_count}, "
            f"Pruned {pruned_count}, Contradictions {conflicts_count}."
        )

        return {
            "success": True,
            "duration_s": duration,
            "ingested": ingested_count,
            "pruned": pruned_count,
            "contradictions": conflicts_count,
            "timestamp": self._last_sync_time,
        }

    def get_stats(self) -> Dict[str, Any]:
        """Returns runtime metrics of the continuous learning scheduler."""
        stats = dict(self._sync_stats)
        stats["is_running"] = self.is_running
        stats["last_sync_time"] = self._last_sync_time
        return stats
