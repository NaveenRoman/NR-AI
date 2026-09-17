"""
Test Suite for NR-AI Autonomous Web Research Engine.
Verifies SSRF protection, HTML content extraction, query decomposition,
and multi-tier research flow.
"""

from unittest.mock import MagicMock, patch
import unittest

from app.knowledge.research import HTMLTextExtractor, ResearchEngine, SSRFGuard
from app.knowledge.store import HybridKnowledgeStore
from app.knowledge.taxonomy import (
    EpistemicType,
    KnowledgeDomain,
    KnowledgeNode,
    KnowledgeSource,
)


class TestSSRFGuard(unittest.TestCase):
    """Verifies SSRF protection blocking local, private, and reserved network targets."""

    def test_safe_public_urls(self):
        safe_urls = [
            "https://en.wikipedia.org/wiki/Artificial_intelligence",
            "http://export.arxiv.org/api/query?search_query=all:transformer",
            "https://nature.com/articles",
        ]
        for url in safe_urls:
            is_safe, reason = SSRFGuard.is_safe_url(url)
            self.assertTrue(is_safe, f"Expected {url} to be safe, got: {reason}")

    def test_blocked_loopback_and_schemes(self):
        blocked_urls = [
            ("http://127.0.0.1:8585/api/status", "loopback IP"),
            ("http://localhost:8080", "loopback"),
            ("file:///C:/Windows/System32/drivers/etc/hosts", "scheme"),
            ("ftp://ftp.example.com", "scheme"),
            ("", "empty"),
        ]
        for url, expected_reason_fragment in blocked_urls:
            is_safe, reason = SSRFGuard.is_safe_url(url)
            self.assertFalse(is_safe, f"Expected {url} to be blocked")


class TestHTMLTextExtractor(unittest.TestCase):
    """Verifies HTML cleaning and boilerplate removal."""

    def test_clean_html_extraction(self):
        sample_html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Test Page</title>
            <script>console.log('Ignore this secret script');</script>
            <style>body { color: red; }</style>
        </head>
        <body>
            <nav><a href="/home">Home</a> <a href="/login">Login</a></nav>
            <h1>Quantum Teleportation</h1>
            <p>Quantum teleportation is a technique for transferring quantum information from a sender to a receiver.</p>
            <footer>Copyright 2026 Example Corp</footer>
        </body>
        </html>
        """
        parser = HTMLTextExtractor()
        parser.feed(sample_html)
        clean = parser.get_clean_text()

        self.assertIn("Quantum Teleportation", clean)
        self.assertIn("technique for transferring quantum information", clean)
        self.assertNotIn("secret script", clean)
        self.assertNotIn("color: red", clean)
        self.assertNotIn("Copyright 2026", clean)


class TestResearchEngine(unittest.TestCase):
    """Verifies multi-hop query decomposition and tier execution."""

    def setUp(self):
        self.store = HybridKnowledgeStore(db_path=":memory:")
        # Seed one node into the test store
        node = KnowledgeNode(
            node_id="test-waterloo",
            domain=KnowledgeDomain.HISTORY.value,
            topic="wars",
            title="Battle of Waterloo",
            content="Fought on June 18, 1815, Waterloo marked the final defeat of Napoleon Bonaparte.",
            epistemic_type=EpistemicType.VERIFIED_FACT,
            confidence=1.0,
            sources=[KnowledgeSource(name="Oxford History", publisher="OUP")],
        )
        self.store.upsert_node(node)
        self.engine = ResearchEngine(store=self.store)

    def test_decompose_query(self):
        q = "Compare SFT and RLHF and when was DPO introduced?"
        terms = self.engine.decompose_query(q)
        self.assertGreaterEqual(len(terms), 2)
        # Should include cleaned components
        joined = " ".join(terms)
        self.assertIn("SFT", joined)
        self.assertIn("RLHF", joined)

    def test_tier1_local_store_hit(self):
        report = self.engine.research("What happened at the Battle of Waterloo?")
        self.assertEqual(report.retrieval_tier, "local_store")
        self.assertEqual(report.epistemic_type, EpistemicType.VERIFIED_FACT)
        self.assertIn("Napoleon Bonaparte", report.primary_answer)
        self.assertEqual(len(report.sources), 1)
        self.assertEqual(report.sources[0].name, "Oxford History")
        self.assertLess(report.latency_ms, 50.0)

    def test_tier2_web_research_mocked(self):
        mock_wiki_item = {
            "title": "Quantum Decoherence",
            "snippet": "Quantum decoherence is the loss of quantum coherence where a quantum system behaves classically.",
            "url": "https://en.wikipedia.org/wiki/Quantum_decoherence",
            "source": "Wikipedia",
            "publisher": "Wikimedia Foundation",
            "reliability_weight": 0.98,
        }

        with patch.object(self.engine.wiki, "search", return_value=[mock_wiki_item]), \
             patch.object(self.engine.arxiv, "search", return_value=[]), \
             patch.object(self.engine.ddg, "search", return_value=[]):
            report = self.engine.research("Explain quantum decoherence in superconductor qubits")
            self.assertEqual(report.retrieval_tier, "web_research")
            self.assertIn("Quantum Decoherence", report.primary_answer)
            self.assertEqual(len(report.sources), 1)
            self.assertEqual(report.sources[0].name, "Quantum Decoherence")
            self.assertEqual(report.epistemic_type, EpistemicType.VERIFIED_FACT)

            # Check that it was cached into the local knowledge store
            cached = self.store.search_bm25("Quantum Decoherence")
            self.assertGreaterEqual(len(cached), 1)


if __name__ == "__main__":
    unittest.main()
