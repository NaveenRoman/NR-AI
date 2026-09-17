"""
Test Suite for NR-AI Universal Knowledge Store & Epistemic Taxonomy.
Verifies data models, SQLite FTS5 BM25 search, TTL pruning, bulk import,
and contradiction logging.
"""

import time
import unittest

from app.knowledge.taxonomy import (
    EpistemicBadge,
    EpistemicType,
    KnowledgeDomain,
    KnowledgeNode,
    KnowledgeSource,
    ResearchReport,
)
from app.knowledge.store import HybridKnowledgeStore, sanitize_fts5_query


class TestEpistemicTaxonomy(unittest.TestCase):
    """Verifies the core epistemic classification and data structures."""

    def test_epistemic_types_enumeration(self):
        expected_types = {
            "VERIFIED_FACT",
            "CURRENT_INFORMATION",
            "SOURCE_ATTRIBUTED_CLAIM",
            "INFERENCE",
            "UNCERTAINTY",
            "SPECULATION_PREDICTION",
        }
        actual_types = {e.value for e in EpistemicType}
        self.assertEqual(expected_types, actual_types)

    def test_epistemic_badge_formatting(self):
        b1 = EpistemicBadge.from_type(EpistemicType.VERIFIED_FACT, confidence=0.98)
        self.assertEqual(b1.label, "VERIFIED FACT")
        self.assertIn("[VERIFIED FACT | 98%]", b1.format_tag())
        self.assertEqual(b1.color, "#10B981")

        b2 = EpistemicBadge.from_type(EpistemicType.SPECULATION_PREDICTION, confidence=0.65)
        self.assertEqual(b2.label, "SPECULATION / PREDICTION")
        self.assertIn("[SPECULATION / PREDICTION | 65%]", b2.format_tag())

        # Test dictionary roundtrip
        d = b1.to_dict()
        b1_rec = EpistemicBadge.from_dict(d)
        self.assertEqual(b1.badge_type, b1_rec.badge_type)
        self.assertEqual(b1.confidence, b1_rec.confidence)

    def test_knowledge_node_expiration(self):
        # Node with no TTL never expires
        node_perm = KnowledgeNode(
            node_id="fact-1",
            domain="history",
            topic="1800s",
            title="Battle of Waterloo",
            content="Fought on 18 June 1815 near Waterloo.",
            epistemic_type=EpistemicType.VERIFIED_FACT,
            ttl_seconds=None,
        )
        self.assertFalse(node_perm.is_expired())

        # Node with TTL of 2 seconds
        node_temp = KnowledgeNode(
            node_id="curr-1",
            domain="current_events",
            topic="prices",
            title="Stock Price",
            content="Price is $100.",
            epistemic_type=EpistemicType.CURRENT_INFORMATION,
            ttl_seconds=2,
            updated_at=time.time() - 3,
        )
        self.assertTrue(node_temp.is_expired())

    def test_research_report_formatting(self):
        src = KnowledgeSource(
            name="Nature Physics",
            url="https://nature.com/articles/s41567",
            publisher="Nature Publishing Group",
            reliability_weight=0.95,
            source_type="academic",
        )
        report = ResearchReport(
            query="What is quantum coherence?",
            primary_answer="Quantum coherence describes the correlation between physical quantities of a wave.",
            epistemic_type=EpistemicType.VERIFIED_FACT,
            confidence=0.99,
            sources=[src],
        )

        speech = report.format_speech()
        self.assertIn("Quantum coherence describes", speech)

        md = report.format_markdown()
        self.assertIn("[VERIFIED FACT | 99%]", md)
        self.assertIn("Nature Physics", md)

        # Dictionary roundtrip
        rep_dict = report.to_dict()
        rep_rec = ResearchReport.from_dict(rep_dict)
        self.assertEqual(report.query, rep_rec.query)
        self.assertEqual(report.epistemic_type, rep_rec.epistemic_type)


class TestHybridKnowledgeStore(unittest.TestCase):
    """Verifies SQLite3 FTS5 Hybrid Knowledge Store operations."""

    def setUp(self):
        # Use in-memory SQLite store for fast, hermetic test execution
        self.store = HybridKnowledgeStore(db_path=":memory:")

    def test_sanitize_query(self):
        self.assertEqual(sanitize_fts5_query(""), "")
        self.assertEqual(sanitize_fts5_query("   "), "")
        q = sanitize_fts5_query('Quantum "Computing": [2026]!')
        self.assertIn('"Quantum"*', q)
        self.assertIn('"Computing"*', q)

    def test_upsert_and_retrieve_node(self):
        src = KnowledgeSource(name="Principia", publisher="Isaac Newton", reliability_weight=1.0)
        node = KnowledgeNode(
            node_id="stem-newton-1",
            domain=KnowledgeDomain.STEM.value,
            topic="physics",
            title="Newton's First Law of Motion",
            content="An object at rest stays at rest and an object in motion stays in motion unless acted upon by a net external force.",
            epistemic_type=EpistemicType.VERIFIED_FACT,
            confidence=1.0,
            sources=[src],
            tags=["physics", "mechanics", "newton"],
            temporal_anchor="1687",
            metadata={"chapter": "Axioms"},
        )

        ok = self.store.upsert_node(node)
        self.assertTrue(ok)

        retrieved = self.store.get_node("stem-newton-1")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.title, "Newton's First Law of Motion")
        self.assertEqual(retrieved.epistemic_type, EpistemicType.VERIFIED_FACT)
        self.assertEqual(len(retrieved.sources), 1)
        self.assertEqual(retrieved.sources[0].name, "Principia")
        self.assertEqual(retrieved.tags, ["physics", "mechanics", "newton"])
        self.assertEqual(retrieved.metadata["chapter"], "Axioms")

    def test_delete_node(self):
        node = KnowledgeNode(
            node_id="del-1",
            domain="test",
            topic="test",
            title="Temporary Fact",
            content="This will be deleted.",
            epistemic_type=EpistemicType.VERIFIED_FACT,
        )
        self.store.upsert_node(node)
        self.assertIsNotNone(self.store.get_node("del-1"))

        deleted = self.store.delete_node("del-1")
        self.assertTrue(deleted)
        self.assertIsNone(self.store.get_node("del-1"))

        # Verify FTS index is also cleared
        results = self.store.search_bm25("Temporary Fact")
        self.assertEqual(len(results), 0)

    def test_fts5_bm25_search_and_ranking(self):
        nodes = [
            KnowledgeNode(
                node_id="hist-1",
                domain=KnowledgeDomain.HISTORY.value,
                topic="industrial_revolution",
                title="First Industrial Revolution",
                content="The Industrial Revolution was the transition to new manufacturing processes in Great Britain, continental Europe, and the United States, that occurred during the period from around 1760 to about 1840.",
                epistemic_type=EpistemicType.VERIFIED_FACT,
                tags=["industry", "steam", "britain"],
            ),
            KnowledgeNode(
                node_id="hist-2",
                domain=KnowledgeDomain.HISTORY.value,
                topic="industrial_revolution",
                title="Second Industrial Revolution",
                content="The Second Industrial Revolution, also known as the Technological Revolution, was a phase of rapid scientific discovery, standardization, mass production, and industrialization from the late 19th century into the early 20th century.",
                epistemic_type=EpistemicType.VERIFIED_FACT,
                tags=["electricity", "steel", "assembly_line"],
            ),
            KnowledgeNode(
                node_id="cs-1",
                domain=KnowledgeDomain.COMPUTER_SCIENCE.value,
                topic="architecture",
                title="Von Neumann Architecture",
                content="A theoretical design architecture for an electronic digital computer with subsystems consisting of a processing unit, a control unit, memory, external mass storage, and input and output mechanisms.",
                epistemic_type=EpistemicType.VERIFIED_FACT,
                tags=["hardware", "cpu", "memory"],
            ),
        ]

        self.store.bulk_import(nodes)
        self.assertEqual(self.store.count_nodes(), 3)

        # 1. Search for Industrial Revolution
        res = self.store.search_bm25("Industrial Revolution")
        self.assertGreaterEqual(len(res), 2)
        titles = [r[0].title for r in res]
        self.assertIn("First Industrial Revolution", titles)
        self.assertIn("Second Industrial Revolution", titles)

        # 2. Search with domain filter
        res_cs = self.store.search_bm25("Architecture", domain=KnowledgeDomain.COMPUTER_SCIENCE.value)
        self.assertEqual(len(res_cs), 1)
        self.assertEqual(res_cs[0][0].title, "Von Neumann Architecture")

        # 3. Search with non-matching domain returns empty
        res_empty = self.store.search_bm25("Architecture", domain=KnowledgeDomain.HISTORY.value)
        self.assertEqual(len(res_empty), 0)

    def test_ttl_pruning(self):
        n_perm = KnowledgeNode(
            node_id="perm-1",
            domain="history",
            topic="wars",
            title="American Civil War",
            content="Fought from 1861 to 1865.",
            epistemic_type=EpistemicType.VERIFIED_FACT,
            ttl_seconds=None,
        )
        n_expired = KnowledgeNode(
            node_id="exp-1",
            domain="current_events",
            topic="weather",
            title="Flash Weather",
            content="Raining right now.",
            epistemic_type=EpistemicType.CURRENT_INFORMATION,
            ttl_seconds=1,
            updated_at=time.time() - 5,  # 5 seconds ago, TTL is 1s
        )

        self.store.upsert_node(n_perm)
        self.store.upsert_node(n_expired)
        self.assertEqual(self.store.count_nodes(), 2)

        pruned = self.store.prune_expired()
        self.assertEqual(pruned, 1)
        self.assertEqual(self.store.count_nodes(), 1)
        self.assertIsNotNone(self.store.get_node("perm-1"))
        self.assertIsNone(self.store.get_node("exp-1"))

    def test_contradiction_logging(self):
        cid = self.store.record_contradiction(
            topic="Superconductor Transition Temp",
            claim_a="Transition occurs at 15 Kelvin",
            source_a="Lab Alpha (2025)",
            claim_b="Transition occurs at 93 Kelvin",
            source_b="Lab Beta (2026)",
            difference_description="78 Kelvin discrepancy under atmospheric pressure",
        )
        self.assertGreater(cid, 0)

        disputes = self.store.get_contradictions(topic="Superconductor Transition Temp")
        self.assertEqual(len(disputes), 1)
        self.assertEqual(disputes[0]["claim_a"], "Transition occurs at 15 Kelvin")
        self.assertEqual(disputes[0]["claim_b"], "Transition occurs at 93 Kelvin")

    def test_domain_and_topic_listing(self):
        nodes = [
            KnowledgeNode(node_id="1", domain="history", topic="rome", title="Caesar", content="Rome", epistemic_type=EpistemicType.VERIFIED_FACT),
            KnowledgeNode(node_id="2", domain="history", topic="greece", title="Athens", content="Greece", epistemic_type=EpistemicType.VERIFIED_FACT),
            KnowledgeNode(node_id="3", domain="stem", topic="math", title="Euler", content="Identity", epistemic_type=EpistemicType.VERIFIED_FACT),
        ]
        self.store.bulk_import(nodes)

        domains = self.store.list_domains()
        self.assertEqual(len(domains), 2)
        domain_names = {d["domain"] for d in domains}
        self.assertEqual(domain_names, {"history", "stem"})

        topics_history = self.store.list_topics(domain="history")
        self.assertEqual(set(topics_history), {"rome", "greece"})


if __name__ == "__main__":
    unittest.main()
