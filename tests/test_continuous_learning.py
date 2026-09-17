"""
Test Suite for NR-AI Continuous Learning & Contradiction Detection.
Verifies conflict detection (temporal, negation) and background scheduler lifecycle.
"""

import time
from unittest.mock import MagicMock, patch
import unittest

from app.agent.news_agent import NewsItem
from app.knowledge.contradiction import ContradictionDetector
from app.knowledge.scheduler import ContinuousLearningScheduler
from app.knowledge.store import HybridKnowledgeStore
from app.knowledge.taxonomy import (
    EpistemicType,
    KnowledgeDomain,
    KnowledgeNode,
    KnowledgeSource,
)


class TestContradictionDetector(unittest.TestCase):
    """Verifies automated detection of factual, temporal, and polarity disputes."""

    def setUp(self):
        self.store = HybridKnowledgeStore(db_path=":memory:")
        self.detector = ContradictionDetector(store=self.store)

    def test_temporal_conflict_detection(self):
        node_a = KnowledgeNode(
            node_id="moon-a",
            domain="history",
            topic="space_exploration",
            title="First Crewed Lunar Landing",
            content="Neil Armstrong landed on the Moon in the year 1969.",
            epistemic_type=EpistemicType.VERIFIED_FACT,
            sources=[KnowledgeSource(name="NASA Archives")],
        )

        node_b = KnowledgeNode(
            node_id="moon-b",
            domain="history",
            topic="space_exploration",
            title="First Crewed Lunar Landing",
            content="Neil Armstrong landed on the Moon in the year 1974.",
            epistemic_type=EpistemicType.SOURCE_ATTRIBUTED_CLAIM,
            sources=[KnowledgeSource(name="Conflicting Blog")],
        )

        conflicts = self.detector.detect_conflicts(node_b, [node_a])
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].conflict_type, "temporal")
        self.assertIn("1969", conflicts[0].claim_a + conflicts[0].claim_b)
        self.assertIn("1974", conflicts[0].claim_a + conflicts[0].claim_b)

        # Verify recorded into store
        logged = self.store.get_contradictions(topic="space_exploration")
        self.assertEqual(len(logged), 1)

    def test_polarity_negation_detection(self):
        node_a = KnowledgeNode(
            node_id="superconductor-a",
            domain="stem",
            topic="room_temperature_superconductor",
            title="LK-99 Ambient Superconductivity",
            content="The ambient superconductivity claims were confirmed by independent measurements.",
            epistemic_type=EpistemicType.SOURCE_ATTRIBUTED_CLAIM,
            sources=[KnowledgeSource(name="Lab Report 1")],
        )

        node_b = KnowledgeNode(
            node_id="superconductor-b",
            domain="stem",
            topic="room_temperature_superconductor",
            title="LK-99 Ambient Superconductivity",
            content="The ambient superconductivity claims were refuted by international synthesis teams.",
            epistemic_type=EpistemicType.VERIFIED_FACT,
            sources=[KnowledgeSource(name="Nature Journal")],
        )

        conflicts = self.detector.detect_conflicts(node_b, [node_a])
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].conflict_type, "negation")


class TestContinuousLearningScheduler(unittest.TestCase):
    """Verifies background synchronization and lifecycle."""

    def setUp(self):
        self.store = HybridKnowledgeStore(db_path=":memory:")
        self.news_mock = MagicMock()
        self.scheduler = ContinuousLearningScheduler(
            store=self.store,
            news_agent=self.news_mock,
            sync_interval_seconds=10.0,
        )

    def test_sync_now_ingestion(self):
        item = NewsItem(
            headline="OpenAI Announces New Open-Weight Model Architecture",
            source="TechCrunch",
            publication_time="2026-09-17T12:00:00Z",
            url="https://techcrunch.com/article1",
            topic="AI",
            publisher="TechCrunch",
            summary="A new open-weights model family was released for developers.",
        )
        self.news_mock.fetch_category.return_value = [item]

        res = self.scheduler.sync_now()
        self.assertTrue(res["success"])
        self.assertGreaterEqual(res["ingested"], 1)

        # Verify ingested node exists in store
        hits = self.store.search_bm25("OpenAI Announces New Open-Weight Model Architecture")
        self.assertGreaterEqual(len(hits), 1)
        self.assertEqual(hits[0][0].epistemic_type, EpistemicType.CURRENT_INFORMATION)
        self.assertEqual(hits[0][0].ttl_seconds, 86400 * 3)

        # Check stats
        stats = self.scheduler.get_stats()
        self.assertEqual(stats["total_syncs"], 1)
        self.assertGreaterEqual(stats["nodes_ingested"], 1)

    def test_scheduler_lifecycle(self):
        self.assertFalse(self.scheduler.is_running)
        self.scheduler.start()
        self.assertTrue(self.scheduler.is_running)

        time.sleep(0.2)
        self.scheduler.stop(timeout=1.0)
        self.assertFalse(self.scheduler.is_running)


if __name__ == "__main__":
    unittest.main()
