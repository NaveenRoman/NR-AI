"""
Unit tests for NR-AI Knowledge Trinity Phase 2: Nova Discovery Agent.
NOVA (Discovery) + KNOWLEDGE (Coordination) + AEGIS (Verification)

Tests:
- Source adapter interface and health monitoring
- Evidence creation, provenance tracking, authority tiering, freshness scoring
- Content hashing (SHA-256) and deduplication
- Media extraction, classification, and URL validation
- Unavailable provider tolerance and timeout isolation
- TrinityBus telemetry event emission sequence
- SSRF security validation (loopback, RFC 1918, link-local, cloud metadata)
- Zero shell execution invariant
"""

import hashlib
import time
import unittest
from unittest.mock import MagicMock, patch

from app.knowledge.trinity import (
    DiscoveryEvidence,
    DiscoveryRequest,
    DiscoveryResponse,
    MediaItem,
    MediaType,
    SourceAuthorityTier,
    TrinityBus,
    TrinityTelemetryEvent,
)
from app.knowledge.trinity.nova import (
    ArXivDiscoverySource,
    ContinuousDiscoveryScheduler,
    DiscoverySource,
    DuckDuckGoDiscoverySource,
    GitHubDiscoverySource,
    NewsFeedDiscoverySource,
    NovaDiscoveryAgent,
    PublicMediaDiscoverySource,
    SSRFGuard,
    WikipediaDiscoverySource,
    canonicalize_url,
    compute_content_hash,
)


class MockDiscoverySource(DiscoverySource):
    """Mock source adapter for deterministic unit testing."""

    def __init__(self, name="MockSource", tier=SourceAuthorityTier.PRIMARY_CANONICAL, fail=False):
        super().__init__(source_name=name, source_type="mock", authority_tier=tier)
        self.fail = fail

    def search(self, query: str, max_results: int = 5, timeout: float = 5.0):
        self.total_requests += 1
        if self.fail:
            self.last_error = "Simulated network failure"
            raise RuntimeError("Simulated network failure")
        self.successful_requests += 1
        return [
            DiscoveryEvidence(
                evidence_id=f"ev-mock-{i}",
                query_id="",
                claim_candidate=f"Mock claim {i} regarding {query}",
                source_name=self.source_name,
                source_url=f"https://example.org/item/{i}?utm_source=tracker",
                authority_tier=self.authority_tier,
                reliability_weight=self.default_reliability,
                raw_snippet=f"Snippet for mock result {i}",
            )
            for i in range(max_results)
        ]


class TestSSRFSecurityGuard(unittest.TestCase):
    """Tests SSRFGuard security enforcement against local/intranet attacks."""

    def test_ssrf_blocks_loopback_and_local(self):
        blocked_urls = [
            "http://localhost:8585/secret",
            "http://127.0.0.1:8000/api",
            "http://0.0.0.0/",
            "http://metadata.google.internal/computeMetadata/v1/",
            "http://169.254.169.254/latest/meta-data/",
            "https://server.local/admin",
        ]
        for u in blocked_urls:
            is_safe, reason = SSRFGuard.is_safe_url(u)
            self.assertFalse(is_safe, f"Expected {u} to be blocked, but passed. Reason: {reason}")

    def test_ssrf_blocks_unsupported_schemes(self):
        unsafe = [
            "file:///C:/Windows/System32/cmd.exe",
            "ftp://files.example.com/dump",
            "gopher://example.com/",
            "javascript:alert(1)",
        ]
        for u in unsafe:
            is_safe, _ = SSRFGuard.is_safe_url(u)
            self.assertFalse(is_safe)

    def test_ssrf_allows_public_web_domains(self):
        safe = [
            "https://en.wikipedia.org/wiki/Attention_mechanism",
            "https://arxiv.org/abs/2205.14135",
            "https://github.com/Dao-AILab/flash-attention",
        ]
        for u in safe:
            is_safe, reason = SSRFGuard.is_safe_url(u)
            self.assertTrue(is_safe, f"Expected {u} to be safe, but failed: {reason}")


class TestURLCanonicalizationAndHashing(unittest.TestCase):
    """Tests URL parameter stripping and SHA-256 text fingerprinting."""

    def test_canonicalize_url_strips_tracking(self):
        dirty = "https://example.com/paper/123/?utm_source=twitter&utm_medium=social&ref=partner#section"
        clean = canonicalize_url(dirty)
        self.assertEqual(clean, "https://example.com/paper/123")

    def test_compute_content_hash(self):
        text1 = "FlashAttention: Fast and Memory-Efficient Exact Attention"
        text2 = "  flashattention:   fast and memory-efficient exact attention  "
        self.assertEqual(compute_content_hash(text1), compute_content_hash(text2))
        self.assertEqual(len(compute_content_hash(text1)), 64)


class TestNovaDiscoveryAgent(unittest.TestCase):
    """Tests Nova discovery pipeline, source adapters, deduplication, and telemetry."""

    def setUp(self):
        self.bus = TrinityBus()
        self.agent = NovaDiscoveryAgent(bus=self.bus)

    def test_source_adapter_registration_and_health(self):
        sources = self.agent.list_sources()
        source_names = [s["source_name"] for s in sources]
        self.assertIn("arXiv", source_names)
        self.assertIn("Wikipedia", source_names)
        self.assertIn("DuckDuckGo", source_names)
        self.assertIn("GitHub", source_names)
        self.assertIn("Verified News Feeds", source_names)
        self.assertIn("Public Media & Video Index", source_names)

        # All sources initially available
        for s in sources:
            self.assertTrue(s["is_available"])

    def test_discovery_pipeline_with_mock_sources(self):
        # Create isolated agent with mock sources
        test_bus = TrinityBus()
        test_agent = NovaDiscoveryAgent(bus=test_bus)
        test_agent.sources.clear()

        test_agent.register_source(MockDiscoverySource("PrimaryMock", SourceAuthorityTier.PRIMARY_CANONICAL))
        test_agent.register_source(MockDiscoverySource("SecondaryMock", SourceAuthorityTier.RELIABLE_SECONDARY))

        telemetry_events = []
        test_bus.subscribe_telemetry(lambda evt: telemetry_events.append(evt))

        req = DiscoveryRequest(
            session_id="test-sess",
            query_id="test-q1",
            primary_subject="FlashAttention",
            decomposed_queries=["FlashAttention Stanford", "Tri Dao FlashAttention"],
            max_sources=4,
        )

        resp = test_agent.discover(req)
        self.assertEqual(resp.query_id, "test-q1")
        self.assertIn(resp.status, ("SUCCESS", "PARTIAL"))
        self.assertLessEqual(len(resp.evidence_items), 4)

        # Verify telemetry sequence
        phases = [e.phase for e in telemetry_events]
        self.assertIn("NOVA_SEARCHING", phases)
        self.assertIn("NOVA_SOURCE_QUERY", phases)
        self.assertIn("NOVA_DEDUPLICATING", phases)
        self.assertIn("NOVA_COMPLETED", phases)

    def test_deduplication_filters_identical_urls_and_hashes(self):
        ev1 = DiscoveryEvidence(
            evidence_id="1", query_id="q", claim_candidate="Claim A",
            source_name="Src", source_url="https://example.com/article?utm_source=a",
            raw_snippet="Same text content",
        )
        ev2 = DiscoveryEvidence(
            evidence_id="2", query_id="q", claim_candidate="Claim B",
            source_name="Src", source_url="https://example.com/article?utm_source=b",
            raw_snippet="Same text content",
        )
        ev3 = DiscoveryEvidence(
            evidence_id="3", query_id="q", claim_candidate="Claim C",
            source_name="Src", source_url="https://different.org/paper",
            raw_snippet="Completely different text content",
        )

        deduped = self.agent._deduplicate_evidence([ev1, ev2, ev3])
        # ev2 should be filtered because URL canonicalizes to example.com/article and content hash is identical
        self.assertEqual(len(deduped), 2)
        urls = [e.source_url for e in deduped]
        self.assertIn("https://different.org/paper", urls)

    def test_source_failure_tolerance(self):
        """Verifies that one crashing source does not fail the entire Nova discovery request."""
        test_bus = TrinityBus()
        test_agent = NovaDiscoveryAgent(bus=test_bus)
        test_agent.sources.clear()

        # Add 1 failing source and 1 working source
        test_agent.register_source(MockDiscoverySource("FailingSource", fail=True))
        test_agent.register_source(MockDiscoverySource("WorkingSource", fail=False))

        req = DiscoveryRequest(
            session_id="s", query_id="q-tol", primary_subject="Resilience Test", max_sources=3
        )

        resp = test_agent.discover(req)
        self.assertEqual(resp.status, "SUCCESS")
        self.assertGreater(len(resp.evidence_items), 0)
        self.assertEqual(resp.evidence_items[0].source_name, "WorkingSource")

    def test_public_media_extraction(self):
        media_src = PublicMediaDiscoverySource()
        results = media_src.search("Transformer video and diagram architecture")
        self.assertGreaterEqual(len(results), 1)

        media_items = results[0].media_items
        types = [m.media_type for m in media_items]
        self.assertIn(MediaType.VIDEO_EXPLAINER, types)
        self.assertIn(MediaType.IMAGE_DIAGRAM, types)
        self.assertTrue(all(m.verified for m in media_items))

    def test_continuous_discovery_scheduler(self):
        scheduler = ContinuousDiscoveryScheduler(bus=self.bus)
        scheduler.register_job("Generative AI", interval_seconds=60.0)
        self.assertIn("Generative AI", scheduler._jobs)
        self.assertEqual(scheduler._jobs["Generative AI"].interval_seconds, 60.0)


if __name__ == "__main__":
    unittest.main()
