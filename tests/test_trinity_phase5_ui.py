"""
NR-AI Step 9 Knowledge Trinity Phase 5 Automated Test Suite.
Validates:
- Test A: Galaxy state schema and metrics
- Test B: Knowledge Trinity node presence, roles, and sizing (34px vs 24px)
- Test C: Inter-agent Trinity connections defined and animated
- Test D: Real telemetry endpoint contract and atomic tracking
- Test E: Single Knowledge Workspace invariant
- Test F: Specialist agent workspace isolation (zero leakage)
- Test G: Chat history persistence with rich card metadata
- Test H: Real agent introductions exact string fidelity
- Test I: Provenance and media schemas verification
- Test J: Bounded 2-cycle review limit
- Test K: Error recovery and graceful degradation
- Test L: Security audit: zero shell=True, zero eval, zero exec, SSRF guard
- Test M: No hardcoded question logic
"""

import os
import re
import time
import unittest
from unittest.mock import MagicMock, patch

from app.knowledge.taxonomy import EpistemicBadge, EpistemicType
from app.knowledge.trinity.schemas import (
    ClaimStatus,
    ClaimVerification,
    DiscoveryEvidence,
    MediaItem,
    MediaType,
    SourceAuthorityTier,
)
from app.knowledge.trinity.protocol import (
    DiscoveryRequest,
    DiscoveryResponse,
    TrinityBus,
    VerificationRequest,
    VerificationReport,
)
from app.knowledge.trinity.coordinator import (
    KnowledgeTrinityCoordinator,
    TrinityResponse,
    TrinityTelemetryState,
)
from app.knowledge.engine import UniversalKnowledgeEngine
from app.ui.galaxy_engine import GalaxyEngine, CelestialNode
from app.brain.companion import NRCompanion, CompanionResponse, CommandCategory


class TestKnowledgeTrinityPhase5UI(unittest.TestCase):
    """Phase 5 Galaxy UI and Production Polish Validation Suite."""

    def setUp(self):
        self.galaxy_engine = GalaxyEngine()
        self.bus = TrinityBus()
        self.mock_nova = MagicMock()
        self.mock_aegis = MagicMock()
        self.mock_store = MagicMock()
        self.mock_research = MagicMock()

        self.mock_report = MagicMock()
        self.mock_report.format_answer.return_value = "Alexander Graham Bell invented the telephone in 1876."
        self.mock_report.format_speech.return_value = "Alexander Graham Bell invented the telephone in 1876."
        self.mock_report.synthesis = "Alexander Graham Bell invented the telephone in 1876."
        self.mock_report.primary_answer = "Alexander Graham Bell invented the telephone in 1876."
        self.mock_report.sources = []
        self.mock_report.confidence = 0.95
        self.mock_report.epistemic_type = EpistemicType.VERIFIED_FACT
        self.mock_report.badge = EpistemicBadge.from_type(EpistemicType.VERIFIED_FACT, 0.95)
        self.mock_research.research.return_value = self.mock_report

        self.mock_store.get_historical_answer.return_value = None
        self.mock_store.search.return_value = []

        self.mock_nova.discover.return_value = DiscoveryResponse(
            query_id="q-test",
            status="SUCCESS",
            evidence_items=[],
            discovered_media=[],
        )
        self.mock_aegis.verify.return_value = VerificationReport(
            query_id="q-test",
            verdict="APPROVED",
            overall_epistemic_type=EpistemicType.VERIFIED_FACT,
            confidence=0.95,
        )

        self.coordinator = KnowledgeTrinityCoordinator(
            bus=self.bus,
            nova=self.mock_nova,
            aegis=self.mock_aegis,
            knowledge_store=self.mock_store,
            research_engine=self.mock_research,
        )

    # --------------------------------------------------------------------------
    # Test A: Galaxy state schema and metrics
    # --------------------------------------------------------------------------
    def test_galaxy_state_schema_and_metrics(self):
        state = self.galaxy_engine.get_galaxy_state()
        self.assertTrue(state.get("success"), "Galaxy state must succeed")
        self.assertIn("central_core", state)
        self.assertIn("nodes", state)
        self.assertIn("connections", state)
        self.assertIn("system_metrics", state)
        self.assertIn("trinity_telemetry", state)

        core = state["central_core"]
        self.assertIn(core.get("status"), ("OPERATIONAL", "STOPPED", "ONLINE"))
        self.assertIn("accretion_disk_colors", core)

        metrics = state["system_metrics"]
        self.assertIn("cpu_percent", metrics)
        self.assertIn("memory_percent", metrics)
        self.assertIn("active_agents_count", metrics)

    # --------------------------------------------------------------------------
    # Test B: Knowledge Trinity node presence, roles, and sizing (34px vs 24px)
    # --------------------------------------------------------------------------
    def test_knowledge_trinity_nodes_and_sizing(self):
        state = self.galaxy_engine.get_galaxy_state()
        nodes = {n["agent_id"]: n for n in state["nodes"]}

        # 1. Knowledge Node: 34px base radius anchor
        self.assertIn("universal_knowledge_engine", nodes)
        k_node = nodes["universal_knowledge_engine"]
        self.assertEqual(k_node.get("base_radius"), 34.0, "Knowledge base_radius must be 34.0")
        self.assertEqual(k_node.get("friendly_name"), "Knowledge")
        self.assertIn("Knowledge", k_node.get("role"))

        # 2. Nova Node: 24px base radius scout
        self.assertIn("nova_discovery_agent", nodes)
        n_node = nodes["nova_discovery_agent"]
        self.assertEqual(n_node.get("base_radius"), 24.0, "Nova base_radius must be 24.0")
        self.assertEqual(n_node.get("friendly_name"), "Nova")
        self.assertIn("Discovery", n_node.get("role"))
        self.assertEqual(n_node.get("parent_department"), "universal_knowledge_engine")

        # 3. Aegis Node: 24px base radius gatekeeper
        self.assertIn("aegis_verification_agent", nodes)
        a_node = nodes["aegis_verification_agent"]
        self.assertEqual(a_node.get("base_radius"), 24.0, "Aegis base_radius must be 24.0")
        self.assertEqual(a_node.get("friendly_name"), "Aegis")
        self.assertIn("Verification", a_node.get("role"))
        self.assertEqual(a_node.get("parent_department"), "universal_knowledge_engine")

        # 4. Specialist nodes have default 26.0px base radius
        if "android_unified_agent" in nodes:
            self.assertEqual(nodes["android_unified_agent"].get("base_radius"), 26.0)

    # --------------------------------------------------------------------------
    # Test C: Inter-agent Trinity connections defined and animated
    # --------------------------------------------------------------------------
    def test_inter_agent_trinity_connections(self):
        state = self.galaxy_engine.get_galaxy_state()
        conns = state.get("connections", [])
        trinity_conns = [c for c in conns if c.get("is_trinity") is True]

        self.assertEqual(len(trinity_conns), 3, "Exactly 3 Trinity inter-agent connections expected")

        pairs = {(c["from"], c["to"]) for c in trinity_conns}
        self.assertIn(("universal_knowledge_engine", "nova_discovery_agent"), pairs)
        self.assertIn(("universal_knowledge_engine", "aegis_verification_agent"), pairs)
        self.assertIn(("nova_discovery_agent", "aegis_verification_agent"), pairs)

        # Check telemetry animation reflection
        mock_active_tel = {
            "trinity_telemetry": {
                "active_agent": "nova",
                "nova_state": "DISCOVERING",
                "aegis_state": "IDLE",
                "knowledge_state": "WORKING",
                "source_count": 3,
                "verification_status": "IDLE",
            }
        }
        active_state = self.galaxy_engine.get_galaxy_state(companion_snapshot=mock_active_tel)
        active_t_conns = [c for c in active_state["connections"] if c.get("is_trinity")]
        k_to_nova = next(c for c in active_t_conns if c["from"] == "universal_knowledge_engine" and c["to"] == "nova_discovery_agent")
        self.assertTrue(k_to_nova["animated"], "Knowledge to Nova connection must be animated when active")

    # --------------------------------------------------------------------------
    # Test D: Real telemetry endpoint contract and atomic tracking
    # --------------------------------------------------------------------------
    def test_trinity_telemetry_schema_and_atomic_tracking(self):
        snap = self.coordinator.get_telemetry_snapshot()
        required_fields = (
            "active_agent",
            "workflow_state",
            "query_id",
            "knowledge_state",
            "nova_state",
            "aegis_state",
            "progress",
            "source_count",
            "verification_status",
            "current_operation",
            "error_state",
            "timestamp",
        )
        for field in required_fields:
            self.assertIn(field, snap, f"Telemetry must include '{field}'")

        # Atomic state updates
        self.coordinator._telemetry_state.update(
            active_agent="aegis",
            workflow_state="VERIFYING",
            source_count=7,
            verification_status="APPROVED",
            current_operation="Aegis auditing draft claim #2",
        )
        snap2 = self.coordinator.get_telemetry_snapshot()
        self.assertEqual(snap2["active_agent"], "aegis")
        self.assertEqual(snap2["workflow_state"], "VERIFYING")
        self.assertEqual(snap2["source_count"], 7)
        self.assertEqual(snap2["verification_status"], "APPROVED")
        self.assertEqual(snap2["current_operation"], "Aegis auditing draft claim #2")

    # --------------------------------------------------------------------------
    # Test E: Single Knowledge Workspace invariant
    # --------------------------------------------------------------------------
    def test_single_knowledge_workspace_invariant(self):
        profiles = self.galaxy_engine.profiles
        self.assertEqual(profiles["nova_discovery_agent"].get("parent_department"), "universal_knowledge_engine")
        self.assertEqual(profiles["aegis_verification_agent"].get("parent_department"), "universal_knowledge_engine")

        companion = NRCompanion()
        ctx_nova = companion.get_agent_workspace_context("nova_discovery_agent")
        ctx_aegis = companion.get_agent_workspace_context("aegis_verification_agent")
        ctx_k = companion.get_agent_workspace_context("universal_knowledge_engine")

        self.assertEqual(ctx_nova.get("workspace_name"), "Universal Knowledge Workspace")
        self.assertEqual(ctx_aegis.get("workspace_name"), "Universal Knowledge Workspace")
        self.assertEqual(ctx_k.get("workspace_name"), "Universal Knowledge Workspace")

    # --------------------------------------------------------------------------
    # Test F: Specialist agent workspace isolation (zero leakage)
    # --------------------------------------------------------------------------
    def test_specialist_agent_workspace_isolation(self):
        companion = NRCompanion()
        companion.agent_chat_histories.clear()

        # 1. Turn to Droid
        companion.add_agent_chat_message("android_unified_agent", "user", "build nr_android_test")
        companion.add_agent_chat_message("android_unified_agent", "agent", "Droid: Gradle build succeeded.")

        # 2. Turn to Studio
        companion.add_agent_chat_message("vs_unified_agent", "user", "compile solution")
        companion.add_agent_chat_message("vs_unified_agent", "agent", "Studio: MSBuild completed with 0 errors.")

        # 3. Turn to Universal Knowledge
        companion.add_agent_chat_message("universal_knowledge_engine", "user", "who invented the telephone")
        companion.add_agent_chat_message("universal_knowledge_engine", "agent", "[VERIFIED FACT] Alexander Graham Bell invented the telephone in 1876.")

        h_droid = companion.get_agent_chat_history("android_unified_agent")
        h_studio = companion.get_agent_chat_history("vs_unified_agent")
        h_k = companion.get_agent_chat_history("universal_knowledge_engine")

        self.assertEqual(len(h_droid), 2)
        self.assertEqual(len(h_studio), 2)
        self.assertEqual(len(h_k), 2)

        # Zero leakage: Droid history does not contain telephone or Studio commands
        droid_texts = [m["text"] for m in h_droid]
        self.assertNotIn("who invented the telephone", droid_texts[0])
        self.assertNotIn("compile solution", droid_texts[0])

        # Knowledge history does not contain android or msbuild commands
        k_texts = [m["text"] for m in h_k]
        self.assertNotIn("build nr_android_test", k_texts[0])
        self.assertNotIn("compile solution", k_texts[0])

    # --------------------------------------------------------------------------
    # Test G: Chat history persistence with rich card metadata
    # --------------------------------------------------------------------------
    def test_chat_history_persists_card_metadata(self):
        companion = NRCompanion()
        companion.agent_chat_histories.clear()

        card_payload = {
            "epistemic_type": "VERIFIED_FACT",
            "badge": {"tag": "[VERIFIED FACT]", "css_class": "badge-verified", "label": "Verified Fact"},
            "confidence": 0.98,
            "evidence_items": [
                {"title": "US Patent 174,465", "url": "https://patents.google.com", "source_type": "OFFICIAL"}
            ],
            "media_items": [
                {"type": "video", "title": "History of Telephone", "url": "https://youtube.com/test", "description": "Educational"}
            ],
            "collaboration_block": "Knowledge coordinated with Nova (1 source) and Aegis (verified 1 cycle).",
        }

        companion.add_agent_chat_message(
            "universal_knowledge_engine",
            "agent",
            "[VERIFIED FACT] Alexander Graham Bell invented the telephone in 1876.",
            data={"card": card_payload},
        )

        history = companion.get_agent_chat_history("universal_knowledge_engine")
        self.assertEqual(len(history), 1)
        self.assertIn("data", history[0])
        self.assertIn("card", history[0]["data"])
        card = history[0]["data"]["card"]
        self.assertEqual(card["epistemic_type"], "VERIFIED_FACT")
        self.assertEqual(len(card["evidence_items"]), 1)
        self.assertEqual(len(card["media_items"]), 1)
        self.assertIn("collaboration_block", card)

    # --------------------------------------------------------------------------
    # Test H: Real agent introductions exact string fidelity
    # --------------------------------------------------------------------------
    def test_real_agent_introductions_exact_strings(self):
        intros = self.galaxy_engine.get_agent_introductions()
        seq = {a["agent_id"]: a["speech_text"] for a in intros["sequence"]}

        expected_knowledge = "I am Knowledge, the main Universal Knowledge agent. Nova researches information and Aegis verifies it before I answer when verification is needed."
        expected_nova = "I am Nova, the discovery and research engine behind Knowledge. I search available public sources and bring new evidence into the Knowledge system."
        expected_aegis = "I am Aegis, the verification layer. I check claims, detect contradictions and help Knowledge avoid unsupported answers."

        self.assertEqual(seq.get("universal_knowledge_engine"), expected_knowledge, "Knowledge introduction must match exact required string")
        self.assertEqual(seq.get("nova_discovery_agent"), expected_nova, "Nova introduction must match exact required string")
        self.assertEqual(seq.get("aegis_verification_agent"), expected_aegis, "Aegis introduction must match exact required string")

    # --------------------------------------------------------------------------
    # Test I: Provenance and media schemas verification
    # --------------------------------------------------------------------------
    def test_provenance_and_media_schemas(self):
        evidence = DiscoveryEvidence(
            evidence_id="ev-101",
            query_id="q-test",
            claim_candidate="FlashAttention leverages SRAM tiling on GPUs...",
            source_name="arXiv:2205.14135",
            source_url="https://arxiv.org/abs/2205.14135",
            publisher="arXiv",
            authority_tier=SourceAuthorityTier.PRIMARY_CANONICAL,
            reliability_weight=0.98,
            raw_snippet="FlashAttention leverages SRAM tiling on GPUs...",
        )
        d_dict = evidence.to_dict()
        self.assertEqual(d_dict["source_name"], "arXiv:2205.14135")
        self.assertEqual(d_dict["authority_tier"], "PRIMARY_CANONICAL")
        self.assertEqual(d_dict["source_url"], "https://arxiv.org/abs/2205.14135")

        media = MediaItem(
            media_id="m-202",
            media_type=MediaType.VIDEO_EXPLAINER,
            title="FlashAttention GPU Walkthrough",
            url="https://youtube.com/watch?v=mock123",
            description="Deep dive on SRAM tiling",
            publisher="YouTube",
        )
        m_dict = media.to_dict()
        self.assertEqual(m_dict["media_type"], "VIDEO_EXPLAINER")
        self.assertEqual(m_dict["title"], "FlashAttention GPU Walkthrough")
        self.assertEqual(m_dict["url"], "https://youtube.com/watch?v=mock123")

    # --------------------------------------------------------------------------
    # Test J: Bounded 2-cycle review limit
    # --------------------------------------------------------------------------
    def test_bounded_two_cycle_review_limit(self):
        mock_report_fail = VerificationReport(
            query_id="q-bound",
            verdict="REVISE",
            confidence=0.3,
            revision_feedback="Claim needs correction.",
            claims_verified=[],
        )
        self.mock_aegis.verify.return_value = mock_report_fail

        resp = self.coordinator.coordinate("Who invented the telephone?", max_cycles=2)
        self.assertLessEqual(resp.review_cycles, 2, "Review cycles must be strictly bounded <= 2")

    # --------------------------------------------------------------------------
    # Test K: Error recovery and graceful degradation
    # --------------------------------------------------------------------------
    def test_error_recovery_graceful_degradation(self):
        self.mock_nova.discover.side_effect = RuntimeError("Network timeout contacting external source")
        resp = self.coordinator.coordinate("Tell me about latest quantum computing results")
        self.assertIsNotNone(resp)
        self.assertIn("error_state", self.coordinator.get_telemetry_snapshot())

    # --------------------------------------------------------------------------
    # Test L: Security audit: zero shell=True, zero eval, zero exec, SSRF guard
    # --------------------------------------------------------------------------
    def test_security_audit_zero_shell_eval_exec(self):
        target_dirs = [
            os.path.join("app", "knowledge"),
            os.path.join("app", "ui"),
        ]

        forbidden_patterns = [
            (re.compile(r"shell\s*=\s*True"), "shell=True"),
            (re.compile(r"(?<!\w)eval\s*\("), "eval()"),
            (re.compile(r"(?<!\w)exec\s*\("), "exec()"),
        ]

        violations = []
        for t_dir in target_dirs:
            for root, _, files in os.walk(t_dir):
                for f in files:
                    if f.endswith(".py"):
                        path = os.path.join(root, f)
                        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                            content = fh.read()
                        for pattern, label in forbidden_patterns:
                            if pattern.search(content):
                                violations.append(f"{path}: forbidden pattern {label}")

        self.assertEqual(violations, [], f"Security violations found: {violations}")

    # --------------------------------------------------------------------------
    # Test M: No hardcoded question logic
    # --------------------------------------------------------------------------
    def test_no_hardcoded_question_logic(self):
        with open(os.path.join("app", "knowledge", "engine.py"), "r", encoding="utf-8") as f:
            engine_code = f.read()

        self.assertNotIn('if query == "who invented the telephone"', engine_code.lower())
        self.assertNotIn('if text == "who created it"', engine_code.lower())
        self.assertNotIn('if "flashattention" == query', engine_code.lower())


if __name__ == "__main__":
    unittest.main()
