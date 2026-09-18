"""
Tests for NR-AI Knowledge Trinity Architecture: Phase 1.
NOVA (Discovery) + KNOWLEDGE (Coordination) + AEGIS (Verification)

Tests:
- Data schemas (DiscoveryEvidence, MediaItem, ClaimVerification, FounderValidationResult)
- Protocol and TrinityBus pub/sub telemetry dispatcher
- Multi-turn pronoun coreference resolution (he, she, his, her, it, this)
- Pedagogical adaptation and contextual follow-up intent classification
"""

import time
import unittest

from app.knowledge.query_understanding import (
    FreshnessRequirement,
    QueryIntent,
    QueryUnderstandingEngine,
    TimeScope,
)
from app.knowledge.taxonomy import EpistemicType
from app.knowledge.trinity import (
    ClaimStatus,
    ClaimVerification,
    DiscoveryEvidence,
    DiscoveryRequest,
    DiscoveryResponse,
    FounderValidationResult,
    MediaItem,
    MediaType,
    SourceAuthorityTier,
    TrinityBus,
    TrinityMessageType,
    TrinityTelemetryEvent,
    VerificationReport,
    VerificationRequest,
)


class TestKnowledgeTrinitySchemas(unittest.TestCase):
    """Verifies dataclasses, enums, serialization, and deserialization for Trinity schemas."""

    def test_media_item_serialization(self):
        item = MediaItem(
            media_id="media-001",
            media_type=MediaType.IMAGE_DIAGRAM,
            url="https://arxiv.org/html/2205.14135/flash_attn_diagram.png",
            title="FlashAttention GPU SRAM Tiling Architecture",
            description="Comparison of standard attention HBM memory access vs FlashAttention tiled SRAM computation.",
            publisher="arXiv cs.LG",
            license="CC-BY-4.0",
            verified=True,
        )
        d = item.to_dict()
        self.assertEqual(d["media_id"], "media-001")
        self.assertEqual(d["media_type"], "IMAGE_DIAGRAM")
        self.assertTrue(d["verified"])

        restored = MediaItem.from_dict(d)
        self.assertEqual(restored.media_id, item.media_id)
        self.assertEqual(restored.media_type, MediaType.IMAGE_DIAGRAM)
        self.assertEqual(restored.url, item.url)
        self.assertTrue(restored.verified)

    def test_discovery_evidence_serialization(self):
        media = MediaItem(
            media_id="med-1",
            media_type=MediaType.OFFICIAL_REPO,
            url="https://github.com/Dao-AILab/flash-attention",
            title="Official FlashAttention Repository",
        )
        ev = DiscoveryEvidence(
            evidence_id="ev-101",
            query_id="q-101",
            claim_candidate="FlashAttention was created by Tri Dao and collaborators at Stanford University.",
            source_name="NeurIPS 2022 Proceedings / arXiv:2205.14135",
            source_url="https://arxiv.org/abs/2205.14135",
            publisher="Neural Information Processing Systems (NeurIPS)",
            publication_timestamp=1667260800.0,
            authority_tier=SourceAuthorityTier.PRIMARY_CANONICAL,
            reliability_weight=0.99,
            freshness_score=0.95,
            raw_snippet="FlashAttention: Fast and Memory-Efficient Exact Attention with IO-Awareness. Tri Dao, Daniel Y. Fu, Stefano Ermon, Atri Rudra, Christopher Ré.",
            media_items=[media],
        )
        d = ev.to_dict()
        self.assertEqual(d["evidence_id"], "ev-101")
        self.assertEqual(d["authority_tier"], "PRIMARY_CANONICAL")
        self.assertEqual(len(d["media_items"]), 1)

        restored = DiscoveryEvidence.from_dict(d)
        self.assertEqual(restored.evidence_id, "ev-101")
        self.assertEqual(restored.authority_tier, SourceAuthorityTier.PRIMARY_CANONICAL)
        self.assertEqual(len(restored.media_items), 1)
        self.assertEqual(restored.media_items[0].media_type, MediaType.OFFICIAL_REPO)

    def test_claim_verification_serialization(self):
        cv = ClaimVerification(
            claim_id="cl-001",
            claim_text="FlashAttention reduces memory accesses between GPU HBM and SRAM.",
            status=ClaimStatus.SUPPORTED,
            epistemic_type=EpistemicType.VERIFIED_FACT,
            confidence=0.98,
            corroborating_source_ids=["ev-101", "ev-102"],
            contradicting_source_ids=[],
            temporal_validity="CURRENT",
            bias_rating=0.05,
            notes="Empirically validated across benchmarks.",
        )
        d = cv.to_dict()
        self.assertEqual(d["status"], "SUPPORTED")
        self.assertEqual(d["epistemic_type"], "VERIFIED_FACT")

        restored = ClaimVerification.from_dict(d)
        self.assertEqual(restored.claim_id, "cl-001")
        self.assertEqual(restored.status, ClaimStatus.SUPPORTED)
        self.assertEqual(restored.confidence, 0.98)

    def test_founder_validation_result(self):
        f_res = FounderValidationResult(
            validation_id="fval-01",
            problem_statement="Legacy COBOL mainframes in banks suffer from high maintenance costs and talent shortages.",
            proposed_solution="AI-powered automated AST transpilation to modern Java/Kotlin microservices.",
            market_or_domain="Enterprise Banking Tech",
            overall_status=ClaimStatus.PARTIALLY_SUPPORTED,
            overall_score=72.5,
            supported_claims=["COBOL talent shortage is documented by Gartner and IBM."],
            partially_supported_claims=["Automated transpilation reduces costs, but manual verification remains mandatory."],
            insufficient_evidence_claims=["Banks will deploy autonomous transpilers without human certification."],
            contradicted_claims=["Zero-risk drop-in replacement is disproven by core banking regulatory standards."],
            unknowns=["Specific liability terms for transpiler hallucination in financial ledgers."],
            risk_factors=["Severe regulatory audits", "Strict fault-tolerance requirements"],
            recommended_validation_steps=["Pilot on non-clearing test harness", "Interview 5 bank enterprise architects"],
        )
        d = f_res.to_dict()
        self.assertEqual(d["overall_status"], "PARTIALLY_SUPPORTED")
        self.assertEqual(len(d["supported_claims"]), 1)
        self.assertEqual(len(d["contradicted_claims"]), 1)
        self.assertEqual(d["overall_score"], 72.5)


class TestKnowledgeTrinityProtocol(unittest.TestCase):
    """Verifies message protocol and TrinityBus event dispatching."""

    def test_trinity_bus_telemetry(self):
        bus = TrinityBus()
        received_events = []

        def on_event(evt: TrinityTelemetryEvent):
            received_events.append(evt)

        bus.subscribe_telemetry(on_event)

        bus.emit_telemetry(
            session_id="sess-001",
            query_id="q-001",
            agent_source="nova",
            phase="NOVA_DISCOVERY",
            message="Searching arXiv API for 'FlashAttention'...",
            data={"target": "arXiv"},
        )

        self.assertEqual(len(received_events), 1)
        evt = received_events[0]
        self.assertEqual(evt.agent_source, "nova")
        self.assertEqual(evt.phase, "NOVA_DISCOVERY")
        self.assertEqual(evt.data.get("target"), "arXiv")

        # Check bus history
        hist = bus.get_recent_history()
        self.assertGreaterEqual(len(hist), 1)

        # Unsubscribe
        bus.unsubscribe_telemetry(on_event)
        bus.emit_telemetry(
            session_id="sess-001",
            query_id="q-002",
            agent_source="aegis",
            phase="AEGIS_VERIFYING",
            message="Verifying claims...",
        )
        # Should not have received second event
        self.assertEqual(len(received_events), 1)

    def test_discovery_and_verification_protocol_types(self):
        req = DiscoveryRequest(
            session_id="s1",
            query_id="q1",
            primary_subject="Transformer Architecture",
            target_attributes=["inventor", "date"],
            decomposed_queries=["who invented transformer architecture", "attention is all you need paper authors"],
            freshness_required=False,
        )
        d_req = req.to_dict()
        self.assertEqual(d_req["primary_subject"], "Transformer Architecture")
        self.assertEqual(len(d_req["decomposed_queries"]), 2)

        v_req = VerificationRequest(
            query_id="q1",
            draft_text="The Transformer was introduced in 2017 by Vaswani et al.",
            subject="Transformer",
            cycle_number=1,
        )
        d_v_req = v_req.to_dict()
        self.assertEqual(d_v_req["cycle_number"], 1)

        v_rep = VerificationReport(
            query_id="q1",
            verdict="APPROVED",
            overall_epistemic_type=EpistemicType.VERIFIED_FACT,
            confidence=1.0,
            cycle_number=1,
            latency_ms=12.4,
        )
        d_rep = v_rep.to_dict()
        self.assertEqual(d_rep["verdict"], "APPROVED")


class TestKnowledgeTrinityCoreferenceRepairs(unittest.TestCase):
    """Verifies that QueryUnderstandingEngine resolves pronoun coreferences and pedagogical intent."""

    def setUp(self):
        self.engine = QueryUnderstandingEngine()

    def test_male_pronoun_resolution_albert_einstein(self):
        # Query 1 established context
        ctx = {"last_subject": "Albert Einstein"}

        # Query 2 uses "he" / "his"
        q1 = self.engine.understand("Where was he born?", session_context=ctx)
        self.assertEqual(q1.primary_subject, "Albert Einstein")
        self.assertTrue(q1.conversational_coreference)
        self.assertIn("albert einstein", q1.repaired_query.lower())
        self.assertEqual(q1.target_attribute, "birthplace")

        q2 = self.engine.understand("When was he born?", session_context=ctx)
        self.assertEqual(q2.primary_subject, "Albert Einstein")
        self.assertEqual(q2.target_attribute, "date")

        q3 = self.engine.understand("Who was he?", session_context=ctx)
        self.assertEqual(q3.primary_subject, "Albert Einstein")

    def test_female_pronoun_resolution_marie_curie(self):
        ctx = {"last_subject": "Marie Curie"}

        q1 = self.engine.understand("Where was she born?", session_context=ctx)
        self.assertEqual(q1.primary_subject, "Marie Curie")
        self.assertTrue(q1.conversational_coreference)
        self.assertIn("marie curie", q1.repaired_query.lower())

        q2 = self.engine.understand("When did she win the Nobel Prize?", session_context=ctx)
        self.assertEqual(q2.primary_subject, "Marie Curie")

    def test_inanimate_pronoun_resolution_flashattention(self):
        ctx = {"last_subject": "FlashAttention"}

        q1 = self.engine.understand("Who created it?", session_context=ctx)
        self.assertEqual(q1.primary_subject, "FlashAttention")
        self.assertEqual(q1.target_attribute, "creator")
        self.assertTrue(q1.conversational_coreference)

        q2 = self.engine.understand("How does it work?", session_context=ctx)
        self.assertEqual(q2.primary_subject, "FlashAttention")
        self.assertEqual(q2.intent, QueryIntent.MECHANISM)

    def test_pedagogical_adaptation_intent(self):
        ctx = {"last_subject": "Transformer Architecture"}

        q1 = self.engine.understand("Explain it like I am a beginner", session_context=ctx)
        self.assertEqual(q1.primary_subject, "Transformer Architecture")
        self.assertEqual(q1.intent, QueryIntent.PEDAGOGICAL_ADAPTATION)
        self.assertTrue(q1.conversational_coreference)

        q2 = self.engine.understand("Explain like I'm 5", session_context=ctx)
        self.assertEqual(q2.primary_subject, "Transformer Architecture")
        self.assertEqual(q2.intent, QueryIntent.PEDAGOGICAL_ADAPTATION)

        q3 = self.engine.understand("Give me a real-world example", session_context=ctx)
        self.assertEqual(q3.primary_subject, "Transformer Architecture")
        self.assertEqual(q3.intent, QueryIntent.PEDAGOGICAL_ADAPTATION)

        q4 = self.engine.understand("In simple terms", session_context=ctx)
        self.assertEqual(q4.primary_subject, "Transformer Architecture")
        self.assertEqual(q4.intent, QueryIntent.PEDAGOGICAL_ADAPTATION)


if __name__ == "__main__":
    unittest.main()
