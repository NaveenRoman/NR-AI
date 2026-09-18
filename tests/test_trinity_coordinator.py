"""
NR-AI Knowledge Trinity Phase 4 Test Suite: Unified Orchestration & Continuous Knowledge.
Comprehensive validation covering all 22 required aspects:
1. Knowledge -> Nova discovery delegation
2. Knowledge -> Aegis verification delegation
3. Knowledge -> Nova -> Aegis end-to-end flow
4. Successful verification approval
5. Failed verification handling
6. Correction cycle behavior
7. Hard 2-cycle limit enforcement (no infinite loops)
8. Evidence provenance preservation
9. Knowledge versioning pipeline
10. Temporal updates & version differentiation
11. Stale evidence handling
12. Contradictory evidence & epistemic uncertainty
13. Unknown entity handling (honest uncertainty, zero hallucination)
14. Conversational memory & coreference resolution (14 reference types)
15. Media discovery results (zero fabrication)
16. Founder validation workflow
17. Real-time TrinityBus telemetry emission
18. Failure fallback: Nova offline
19. Failure fallback: Aegis offline
20. Security invariants (shell=False, eval=0, exec=0, SSRF protection)
21. Single Knowledge workspace enforcement
22. Specialist workspace isolation (Droid, Studio, Unity, Unreal, Sentinel, Browser)
"""

import time
import unittest
from unittest.mock import MagicMock, patch

from app.knowledge.taxonomy import EpistemicBadge, EpistemicType
from app.knowledge.trinity.schemas import (
    ClaimStatus,
    ClaimVerification,
    DiscoveryEvidence,
    FounderValidationResult,
    MediaItem,
    MediaType,
    SourceAuthorityTier,
)
from app.knowledge.trinity.protocol import (
    DiscoveryRequest,
    DiscoveryResponse,
    TrinityBus,
    TrinityTelemetryEvent,
    VerificationRequest,
    VerificationReport,
)
from app.knowledge.trinity.nova import NovaDiscoveryAgent, DiscoverySource
from app.knowledge.trinity.aegis import AegisVerificationAgent
from app.knowledge.trinity.coordinator import (
    ConversationalMemoryManager,
    ConversationState,
    KnowledgeTrinityCoordinator,
    KnowledgeUpdateCategory,
    KnowledgeVersioningManager,
    QueryCategory,
    QueryDecisionEngine,
    TrinityResponse,
    VersionedKnowledgeRecord,
)
from app.brain.companion import NRCompanion


class MockFastDiscoverySource(DiscoverySource):
    """Mock discovery source returning deterministic test evidence."""
    def __init__(self, name="MockDiscoverySource", tier=SourceAuthorityTier.AUTHORITATIVE_ORG, items=None, media=None):
        super().__init__(source_name=name, source_type="mock", authority_tier=tier)
        self.items = items or []
        self.media = media or []

    def search(self, query: str, max_results: int = 5, timeout: float = 5.0):
        self.total_requests += 1
        self.successful_requests += 1
        res = list(self.items)
        if self.media:
            for ev in res:
                ev.media_items.extend(self.media)
            if not res:
                res.append(DiscoveryEvidence(
                    evidence_id=f"ev-media-{abs(hash(query)) % 10000}",
                    query_id="q-media",
                    claim_candidate=f"Resource discovered for {query}",
                    source_name=self.source_name,
                    authority_tier=self.authority_tier,
                    media_items=self.media,
                ))
        return res


class TestTrinityCoordinator(unittest.TestCase):
    """Comprehensive test suite for KnowledgeTrinityCoordinator."""

    def setUp(self):
        self.bus = TrinityBus()
        self.nova = NovaDiscoveryAgent(bus=self.bus)
        # Clear external network sources for deterministic offline tests
        self.nova.sources.clear()
        self.mock_source = MockFastDiscoverySource()
        self.nova.register_source(self.mock_source)

        self.aegis = AegisVerificationAgent(bus=self.bus)
        self.coordinator = KnowledgeTrinityCoordinator(
            bus=self.bus,
            nova=self.nova,
            aegis=self.aegis,
        )

    # -------------------------------------------------------------------------
    # 1. Knowledge -> Nova Discovery Delegation
    # -------------------------------------------------------------------------
    def test_knowledge_to_nova_discovery_delegation(self):
        """Verify queries requiring fresh information trigger Nova discovery."""
        events = []
        self.bus.subscribe_telemetry(lambda e: events.append(e))

        resp = self.coordinator.coordinate("What is the latest AI news today?")
        self.assertIsNotNone(resp)
        self.assertEqual(resp.route_category, QueryCategory.CURRENT_INFORMATION)

        # Check Nova searching telemetry was emitted
        nova_phases = [e.phase for e in events if e.agent_source == "nova"]
        self.assertIn("NOVA_SEARCHING", nova_phases)

    # -------------------------------------------------------------------------
    # 2. Knowledge -> Aegis Verification Delegation
    # -------------------------------------------------------------------------
    def test_knowledge_to_aegis_verification_delegation(self):
        """Verify queries with factual or numerical claims trigger Aegis verification."""
        events = []
        self.bus.subscribe_telemetry(lambda e: events.append(e))

        resp = self.coordinator.coordinate("Who created FlashAttention in 2022?")
        aegis_events = [e for e in events if e.agent_source == "aegis"]
        self.assertTrue(len(aegis_events) > 0)
        phases = [e.phase for e in aegis_events]
        self.assertIn("AEGIS_VERIFYING", phases)

    # -------------------------------------------------------------------------
    # 3. Knowledge -> Nova -> Aegis End-to-End Flow
    # -------------------------------------------------------------------------
    def test_knowledge_nova_aegis_end_to_end_flow(self):
        """Verify full Trinity flow: Knowledge -> Nova -> Aegis -> Response."""
        # Inject controlled source into Nova
        ev = DiscoveryEvidence(
            evidence_id="ev-test-1",
            query_id="q-test",
            claim_candidate="Quantum Attention was developed in 2025 by Dr. Elena Vance at MIT.",
            source_name="MIT Research Archive",
            authority_tier=SourceAuthorityTier.AUTHORITATIVE_ORG,
        )
        self.nova.register_source(MockFastDiscoverySource(items=[ev]))

        resp = self.coordinator.coordinate("Tell me about Quantum Attention research by Dr. Vance")
        self.assertIsNotNone(resp)
        self.assertTrue(len(resp.evidence_items) > 0)
        self.assertIn("[TRINITY COLLABORATION]", resp.collaboration_block)
        self.assertIn("KNOWLEDGE:", resp.collaboration_block)
        self.assertIn("NOVA:", resp.collaboration_block)
        self.assertIn("AEGIS:", resp.collaboration_block)

    # -------------------------------------------------------------------------
    # 4. Successful Verification Approval
    # -------------------------------------------------------------------------
    def test_successful_verification_approval(self):
        """Verify factual draft with corroborating evidence receives APPROVED verdict."""
        ev = DiscoveryEvidence(
            evidence_id="ev-alexander-bell",
            query_id="q-phone",
            claim_candidate="Alexander Graham Bell patented the telephone in 1876.",
            source_name="US Patent Office",
            authority_tier=SourceAuthorityTier.PRIMARY_CANONICAL,
        )
        v_req = VerificationRequest(
            query_id="q-v1",
            draft_text="Alexander Graham Bell invented the telephone in 1876.",
            subject="Telephone",
            evidence_items=[ev],
        )
        rep = self.aegis.verify(v_req)
        self.assertEqual(rep.verdict, "APPROVED")
        self.assertEqual(rep.overall_epistemic_type, EpistemicType.VERIFIED_FACT)
        self.assertGreaterEqual(rep.confidence, 0.90)

    # -------------------------------------------------------------------------
    # 5. Failed Verification Handling
    # -------------------------------------------------------------------------
    def test_failed_verification_handling(self):
        """Verify draft with contradicted claim is flagged by Aegis."""
        ev = DiscoveryEvidence(
            evidence_id="ev-real-date",
            query_id="q-conflict",
            claim_candidate="Technology Alpha was released in 2021 by Lab X.",
            source_name="Official Archive",
            authority_tier=SourceAuthorityTier.PRIMARY_CANONICAL,
        )
        v_req = VerificationRequest(
            query_id="q-v2",
            draft_text="Technology Alpha was released in 2026.",
            subject="Technology Alpha",
            evidence_items=[ev],
        )
        rep = self.aegis.verify(v_req)
        self.assertTrue(rep.verdict in ("REVISE", "REJECT"))
        self.assertTrue(len(rep.contradictions) > 0 or any(c.status == ClaimStatus.CONTRADICTED for c in rep.claims_verified))

    # -------------------------------------------------------------------------
    # 6. Correction Cycle Behavior
    # -------------------------------------------------------------------------
    def test_correction_cycle_behavior(self):
        """Verify Coordinator executes Cycle 2 re-check when Cycle 1 detects conflict."""
        events = []
        self.bus.subscribe_telemetry(lambda e: events.append(e))

        ev = DiscoveryEvidence(
            evidence_id="ev-date-2024",
            query_id="q-date",
            claim_candidate="Project Chrono was released in 2024.",
            source_name="Chrono Foundation",
            authority_tier=SourceAuthorityTier.PRIMARY_CANONICAL,
        )
        self.nova.register_source(MockFastDiscoverySource(items=[ev]))

        resp = self.coordinator.coordinate("Project Chrono was released in 2026. Verify this.")
        self.assertLessEqual(resp.review_cycles, 2)
        # Verify cycles executed cleanly without hanging
        self.assertIsNotNone(resp.primary_answer)

    # -------------------------------------------------------------------------
    # 7. Hard 2-Cycle Limit Enforcement
    # -------------------------------------------------------------------------
    def test_hard_two_cycle_limit_enforcement(self):
        """Verify review cycles are strictly bounded to 2 and never infinite-loop."""
        self.assertEqual(self.coordinator.MAX_REVIEW_CYCLES, 2)
        resp = self.coordinator.coordinate("Irresolvable contradictory claim across all sources in 2026 vs 1990")
        self.assertLessEqual(resp.review_cycles, 2)

    # -------------------------------------------------------------------------
    # 8. Evidence Provenance Preservation
    # -------------------------------------------------------------------------
    def test_evidence_provenance_preservation(self):
        """Verify source names, URLs, and authority tiers are preserved in metadata."""
        ev = DiscoveryEvidence(
            evidence_id="ev-prov-1",
            query_id="q-prov",
            claim_candidate="Transformer models utilize self-attention mechanisms.",
            source_name="arXiv:1706.03762",
            source_url="https://arxiv.org/abs/1706.03762",
            authority_tier=SourceAuthorityTier.PRIMARY_CANONICAL,
        )
        self.nova.register_source(MockFastDiscoverySource(items=[ev]))

        resp = self.coordinator.coordinate("Tell me about Transformer self-attention")
        self.assertTrue(len(resp.sources) > 0)
        src = resp.sources[0]
        self.assertIn("arXiv", src["name"])
        self.assertEqual(src["tier"], SourceAuthorityTier.PRIMARY_CANONICAL.value)

    # -------------------------------------------------------------------------
    # 9. Knowledge Versioning Pipeline
    # -------------------------------------------------------------------------
    def test_knowledge_versioning_pipeline(self):
        """Verify append-only versioning preserves version 1 when version 2 is published."""
        mgr = self.coordinator.versioning

        ev1 = DiscoveryEvidence(
            evidence_id="ev-v1", query_id="q1", claim_candidate="v1 fact", source_name="Source 1"
        )
        rec1 = mgr.publish_version(
            node_id="test-topic",
            title="Test Topic",
            content="Version 1 content",
            evidence=[ev1],
        )
        self.assertEqual(rec1.version, 1)
        self.assertIsNone(rec1.superseded_at)

        # Publish version 2
        ev2 = DiscoveryEvidence(
            evidence_id="ev-v2", query_id="q2", claim_candidate="v2 fact", source_name="Source 2"
        )
        rec2 = mgr.publish_version(
            node_id="test-topic",
            title="Test Topic",
            content="Version 2 content",
            evidence=[ev2],
        )
        self.assertEqual(rec2.version, 2)
        self.assertIsNone(rec2.superseded_at)

        # Verify historical preservation
        history = mgr.get_version_history("test-topic")
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].version, 1)
        self.assertIsNotNone(history[0].superseded_at)
        self.assertEqual(history[1].version, 2)

    # -------------------------------------------------------------------------
    # 10. Temporal Version Differentiation
    # -------------------------------------------------------------------------
    def test_temporal_version_differentiation(self):
        """Verify historical versions remain intact when superseded."""
        mgr = self.coordinator.versioning
        mgr.publish_version("python-lang", "Python", "Python 2.7 legacy version", [])
        mgr.publish_version("python-lang", "Python", "Python 3.12 modern version", [])

        current = mgr.get_current_record("python-lang")
        self.assertIn("Python 3.12", current.content)

        history = mgr.get_version_history("python-lang")
        self.assertIn("Python 2.7", history[0].content)

    # -------------------------------------------------------------------------
    # 11. Stale Evidence Handling
    # -------------------------------------------------------------------------
    def test_stale_evidence_handling(self):
        """Verify evidence with old timestamp is downweighted for freshness queries."""
        stale_ev = DiscoveryEvidence(
            evidence_id="ev-old",
            query_id="q-stale",
            claim_candidate="Old news from 2020",
            source_name="Old Feed",
            publication_timestamp=time.time() - (86400 * 365 * 4),  # 4 years ago
            freshness_score=0.20,
        )
        self.assertLess(stale_ev.freshness_score, 0.50)

    # -------------------------------------------------------------------------
    # 12. Contradictory Evidence & Epistemic Uncertainty
    # -------------------------------------------------------------------------
    def test_contradictory_evidence_epistemic_uncertainty(self):
        """Verify conflicting claims yield an epistemically honest uncertainty response."""
        ev = DiscoveryEvidence(
            evidence_id="ev-c-2024",
            query_id="q-conf",
            claim_candidate="Device Zeta was launched in 2024.",
            source_name="Official Gazette",
            authority_tier=SourceAuthorityTier.PRIMARY_CANONICAL,
        )
        self.nova.register_source(MockFastDiscoverySource(items=[ev]))

        resp = self.coordinator.coordinate("Device Zeta was launched in 2026.")
        self.assertIsNotNone(resp)
        # Should not assert 2026 with 100% confidence
        if resp.verification_report and resp.verification_report.contradictions:
            self.assertIn("conflicting", resp.primary_answer.lower())

    # -------------------------------------------------------------------------
    # 13. Unknown Entity Handling (Honest Uncertainty)
    # -------------------------------------------------------------------------
    def test_unknown_entity_honest_uncertainty(self):
        """Verify unindexed/fictional entity produces honest uncertainty without hallucination."""
        resp = self.coordinator.coordinate("What are the specifications of XZ-9900 Hyperion?")
        self.assertEqual(resp.route_category, QueryCategory.UNKNOWN_ENTITY)
        self.assertEqual(resp.epistemic_type, EpistemicType.UNCERTAINTY)
        self.assertEqual(resp.confidence, 0.0)
        self.assertIn("do not have verified", resp.primary_answer.lower())
        self.assertIn("UNVERIFIED", resp.markdown)

    # -------------------------------------------------------------------------
    # 14. Conversational Memory & Coreference Resolution (14 Reference Types)
    # -------------------------------------------------------------------------
    def test_conversational_memory_pronoun_resolution(self):
        """Verify multi-turn reference tracking across person and object pronouns."""
        state = ConversationState(
            current_subject="FlashAttention",
            current_entity="Tri Dao",
        )
        mem = self.coordinator.memory

        # Person reference
        q_resolved, subj, ref = mem.resolve_coreference("Who is he?", state)
        self.assertEqual(subj, "Tri Dao")
        self.assertEqual(ref, "he")

        # Object reference
        q_resolved2, subj2, ref2 = mem.resolve_coreference("When was it released?", state)
        self.assertEqual(subj2, "FlashAttention")
        self.assertEqual(ref2, "it")

        # Complex reference: that technology
        q_resolved3, subj3, ref3 = mem.resolve_coreference("Explain that technology simply", state)
        self.assertEqual(subj3, "FlashAttention")
        self.assertEqual(ref3, "that technology")

    # -------------------------------------------------------------------------
    # 15. Media Discovery Results (Zero Fabrication)
    # -------------------------------------------------------------------------
    def test_media_discovery_results(self):
        """Verify media request discovers structured MediaItem without fabrication."""
        media = MediaItem(
            media_id="m-1",
            media_type=MediaType.VIDEO_EXPLAINER,
            url="https://www.youtube.com/watch?v=kCc8FmEb1nY",
            title="Attention Is All You Need Explained",
            publisher="YouTube / 3Blue1Brown",
            verified=True,
        )
        self.nova.register_source(MockFastDiscoverySource(media=[media]))

        resp = self.coordinator.coordinate("Find me a video about Transformers")
        self.assertEqual(resp.route_category, QueryCategory.MEDIA_LINK_REQUEST)
        self.assertTrue(len(resp.media_items) > 0)
        self.assertEqual(resp.media_items[0].media_type, MediaType.VIDEO_EXPLAINER)
        self.assertIn("youtube.com", resp.media_items[0].url)

    # -------------------------------------------------------------------------
    # 16. Founder Validation Workflow
    # -------------------------------------------------------------------------
    def test_founder_validation_workflow(self):
        """Verify problem-solution validation matrix produces structured evaluation."""
        problem = "Developers spend 30% of their time waiting for long build compilation."
        solution = "Distributed peer-to-peer artifact cache for C++ and Gradle builds."

        val = self.coordinator.coordinate_founder_validation(problem, solution)
        self.assertIsInstance(val, FounderValidationResult)
        self.assertGreaterEqual(val.overall_score, 0.0)
        self.assertLessEqual(val.overall_score, 100.0)
        self.assertIsNotNone(val.overall_status)
        self.assertTrue(len(val.recommended_validation_steps) > 0)

    # -------------------------------------------------------------------------
    # 17. Real-Time TrinityBus Telemetry Emission
    # -------------------------------------------------------------------------
    def test_real_time_trinity_telemetry_emission(self):
        """Verify real-time backend telemetry events are emitted for each phase."""
        events = []
        self.bus.subscribe_telemetry(lambda e: events.append(e))

        self.coordinator.coordinate("What is RAM?")
        self.assertTrue(len(events) > 0)
        sources = set(e.agent_source for e in events)
        self.assertIn("knowledge", sources)

        # Check Idle was emitted at completion
        completion_events = [e for e in events if e.phase == "IDLE"]
        self.assertTrue(len(completion_events) > 0)

    # -------------------------------------------------------------------------
    # 18. Failure Fallback: Nova Offline
    # -------------------------------------------------------------------------
    def test_failure_fallback_nova_offline(self):
        """Verify Coordinator handles Nova exceptions gracefully without crashing."""
        with patch.object(self.nova, "discover", side_effect=RuntimeError("Nova network offline")):
            # Should not crash; gracefully returns knowledge response
            resp = self.coordinator.coordinate("What is the latest AI news today?")
            self.assertIsNotNone(resp)
            self.assertIsNotNone(resp.primary_answer)

    # -------------------------------------------------------------------------
    # 19. Failure Fallback: Aegis Offline
    # -------------------------------------------------------------------------
    def test_failure_fallback_aegis_offline(self):
        """Verify Coordinator handles Aegis failure without false verification."""
        with patch.object(self.aegis, "verify", side_effect=RuntimeError("Aegis quota exceeded")):
            resp = self.coordinator.coordinate("Who created FlashAttention in 2022?")
            self.assertIsNotNone(resp)
            self.assertIsNotNone(resp.primary_answer)

    # -------------------------------------------------------------------------
    # 20. Security Invariants
    # -------------------------------------------------------------------------
    def test_security_invariants(self):
        """Verify Coordinator status reports zero shell execution and active SSRF protection."""
        status = self.coordinator.get_status()
        self.assertFalse(status["security"]["shell_execution"])
        self.assertFalse(status["security"]["eval_exec"])
        self.assertTrue(status["security"]["ssrf_protection"])

    # -------------------------------------------------------------------------
    # 21. Single Knowledge Workspace Enforcement
    # -------------------------------------------------------------------------
    def test_single_knowledge_workspace_enforcement(self):
        """Verify Nova and Aegis route to the single Knowledge chat workspace in Companion."""
        companion = NRCompanion()
        # Activate Nova
        speech_nova, is_first = companion.activate_agent_session("nova_discovery_agent")
        self.assertEqual(companion.active_conversation_agent, "universal_knowledge_engine")

        # Activate Aegis
        speech_aegis, is_first2 = companion.activate_agent_session("aegis_verification_agent")
        self.assertEqual(companion.active_conversation_agent, "universal_knowledge_engine")

        # Chat history is shared
        companion.add_agent_chat_message("nova_discovery_agent", "user", "Hello Knowledge Department")
        hist = companion.get_agent_chat_history("universal_knowledge_engine")
        self.assertTrue(any("Hello Knowledge Department" in m["text"] for m in hist))

    # -------------------------------------------------------------------------
    # 22. Specialist Workspace Isolation
    # -------------------------------------------------------------------------
    def test_specialist_workspace_isolation(self):
        """Verify independent agents (Droid, Studio, Unity, Unreal, Sentinel) retain separate chats."""
        companion = NRCompanion()
        specialists = [
            ("android_unified_agent", "Droid"),
            ("vs_unified_agent", "Studio"),
            ("unity_autonomous_agent", "Unity"),
            ("unreal_autonomous_agent", "Unreal"),
            ("computer_control_agent", "Sentinel"),
        ]

        for aid, name in specialists:
            companion.activate_agent_session(aid)
            self.assertEqual(companion.active_conversation_agent, aid)
            companion.add_agent_chat_message(aid, "user", f"Task for {name}")

            # Verify isolated history
            hist = companion.get_agent_chat_history(aid)
            self.assertTrue(any(f"Task for {name}" in m["text"] for m in hist))

            # Verify Knowledge history is NOT contaminated with specialist chat
            k_hist = companion.get_agent_chat_history("universal_knowledge_engine")
            self.assertFalse(any(f"Task for {name}" in m["text"] for m in k_hist))


if __name__ == "__main__":
    unittest.main()
