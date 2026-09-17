"""
End-to-End Test Suite for NR-AI Universal Knowledge & Continuous Learning Brain.

Verifies:
1. Universal Knowledge Engine queries across History, STEM, CS, AI, and Future Tech.
2. Companion Orchestrator text routing for knowledge queries.
3. Phone Companion voice intent parsing and dispatch.
4. Desktop NRCompanion brain integration with avatar and speech.
5. Honest reality status regarding GPT-6 Astra (Configured vs Live API Verified).
"""

import time
import unittest
from unittest.mock import MagicMock, patch

from app.agent.model_provider import OpenAIProvider
from app.brain.companion import CommandCategory, NRCompanion
from app.config.model_config import GPT_6_ASTRA, ModelConfig
from app.knowledge.engine import UniversalKnowledgeEngine
from app.knowledge.taxonomy import EpistemicType
from app.remote.auth import SessionManager
from app.remote.identity import PairingManager
from app.remote.permissions import DEFAULT_COMPANION_SCOPES
from app.remote.companion_orchestrator import (
    CompanionOrchestrator,
    CompanionOrchestratorState,
)
from app.remote.voice_intent import VoiceIntentParser, VoiceIntentType


class TestUniversalKnowledgeE2E(unittest.TestCase):
    """Full end-to-end integration tests for the Universal Knowledge Brain."""

    @classmethod
    def setUpClass(cls):
        cls.engine = UniversalKnowledgeEngine(auto_seed=True)

    def test_domain_queries_and_epistemic_badges(self):
        """Verify queries across domains return expected facts and epistemic types."""
        # 1. History
        rep_hist = self.engine.query("When was the Meiji Restoration in Japan?")
        self.assertEqual(rep_hist.retrieval_tier, "local_store")
        self.assertEqual(rep_hist.epistemic_type, EpistemicType.VERIFIED_FACT)
        self.assertIn("1868", rep_hist.primary_answer)
        self.assertIn("Meiji", rep_hist.primary_answer)
        self.assertGreater(len(rep_hist.sources), 0)

        # 2. STEM / Physics
        rep_stem = self.engine.query("What are Maxwell's Equations?")
        self.assertEqual(rep_stem.retrieval_tier, "local_store")
        self.assertEqual(rep_stem.epistemic_type, EpistemicType.VERIFIED_FACT)
        self.assertIn("magnetic", rep_stem.primary_answer.lower())
        self.assertIn("electric", rep_stem.primary_answer.lower())

        # 3. Computer Science
        rep_cs = self.engine.query("Explain Coffman deadlock conditions")
        self.assertEqual(rep_cs.retrieval_tier, "local_store")
        self.assertEqual(rep_cs.epistemic_type, EpistemicType.VERIFIED_FACT)
        self.assertIn("Mutual Exclusion", rep_cs.primary_answer)
        self.assertIn("Circular Wait", rep_cs.primary_answer)

        # 4. AI & Deep Learning
        rep_ai = self.engine.query("How does FlashAttention optimize GPU memory?")
        self.assertEqual(rep_ai.retrieval_tier, "local_store")
        self.assertEqual(rep_ai.epistemic_type, EpistemicType.VERIFIED_FACT)
        self.assertIn("SRAM", rep_ai.primary_answer)

        # 5. Future Tech / AGI Projections
        rep_future = self.engine.query("What are the predictions for Artificial Superintelligence (ASI)?")
        self.assertEqual(rep_future.retrieval_tier, "local_store")
        self.assertEqual(rep_future.epistemic_type, EpistemicType.SPECULATION_PREDICTION)
        self.assertIn("[SPECULATION / PREDICTION", rep_future.badges[0].format_tag())

    def test_speech_and_card_formatting(self):
        """Verify speech synthesis and companion card payloads."""
        card = self.engine.query_companion_card("What is the Fundamental Theorem of Calculus?")
        self.assertEqual(card["epistemic_type"], "VERIFIED_FACT")
        self.assertIn("calculus", card["display_text"].lower())
        self.assertIn("fundamental theorem", card["speech_text"].lower())
        self.assertIn("badge", card)
        self.assertEqual(card["badge"]["label"], "VERIFIED FACT")
        self.assertGreater(card["confidence"], 0.9)

    def test_companion_orchestrator_text_knowledge_routing(self):
        """Verify text knowledge query on CompanionOrchestrator returns [SUCCESS | KNOWLEDGE]."""
        pairing_mgr = PairingManager()
        session_mgr = SessionManager(pairing_manager=pairing_mgr)
        device_id = "DEV-E2E-PHONE-001"
        code = pairing_mgr.initiate_pairing(device_id, "Android Phone")
        pairing_mgr.confirm_pairing(device_id, code, "Android Phone")
        s_ok, _, sess = session_mgr.create_session(device_id=device_id, scopes=DEFAULT_COMPANION_SCOPES)
        self.assertTrue(s_ok)
        orchestrator = CompanionOrchestrator(session_manager=session_mgr)

        res = orchestrator.process_command(
            session_id=sess.session_id,
            device_id=sess.device_id,
            command_text="What is the Von Neumann Architecture?",
        )

        self.assertTrue(res.success)
        self.assertEqual(res.status, "SUCCESS")
        self.assertEqual(res.command_type, "KNOWLEDGE")
        self.assertIn("[VERIFIED FACT", res.message)
        self.assertIn("Von Neumann", res.message)
        self.assertIsNotNone(res.tts_response)
        self.assertIn("Von Neumann", res.tts_response.text)
        self.assertIn("retrieval_tier", res.data)

    def test_voice_intent_and_routing(self):
        """Verify VoiceIntentParser detects KNOWLEDGE_QUERY and orchestrator routes it."""
        parser = VoiceIntentParser()
        intent = parser.parse("explain the second industrial revolution")
        self.assertEqual(intent.intent_type, VoiceIntentType.KNOWLEDGE_QUERY)
        self.assertGreaterEqual(intent.confidence, 0.9)

        # Route through orchestrator
        pairing_mgr = PairingManager()
        session_mgr = SessionManager(pairing_manager=pairing_mgr)
        device_id = "DEV-E2E-VOICE-001"
        code = pairing_mgr.initiate_pairing(device_id, "Android Phone")
        pairing_mgr.confirm_pairing(device_id, code, "Android Phone")
        s_ok, _, sess = session_mgr.create_session(device_id=device_id, scopes=DEFAULT_COMPANION_SCOPES)
        self.assertTrue(s_ok)
        orchestrator = CompanionOrchestrator(session_manager=session_mgr)
        orchestrator.transition_state(device_id, CompanionOrchestratorState.AUTHENTICATED)
        orchestrator.transition_state(device_id, CompanionOrchestratorState.IDLE_CONNECTED)
        orchestrator.transition_state(device_id, CompanionOrchestratorState.EVALUATING_ACTION)

        start_t = time.time()
        res = orchestrator._route_intent_or_query(
            sess=sess,
            transcript="explain the second industrial revolution",
            intent_type=intent.intent_type,
            extra_data={},
            cached_target=None,
            start_t=start_t,
            now=start_t,
        )

        self.assertTrue(res.success)
        self.assertEqual(res.command_type, "KNOWLEDGE")
        self.assertIn("Second Industrial Revolution", res.message)
        self.assertIn("[VERIFIED FACT", res.message)

    def test_desktop_nr_companion_brain_interaction(self):
        """Verify Desktop NRCompanion classifies and handles knowledge requests."""
        companion = NRCompanion()
        resp = companion.interact("Tell me about the Battle of Waterloo", speak_output=False)

        self.assertEqual(resp.category, CommandCategory.KNOWLEDGE)
        self.assertEqual(resp.routed_to, "UniversalKnowledgeEngine")
        self.assertIn("Battle of Waterloo", resp.text)
        self.assertIn("[VERIFIED FACT", resp.text)
        self.assertIn("badge", resp.data)
        self.assertEqual(resp.data["badge"]["label"], "VERIFIED FACT")

    def test_gpt6_astra_honest_reality_verification(self):
        """Verify GPT-6 Astra status is strictly reported without synthetic claims."""
        config = ModelConfig.from_env()
        provider = OpenAIProvider(config=config)

        status_report = provider.verify_live_api(GPT_6_ASTRA)
        self.assertEqual(status_report["model"], GPT_6_ASTRA)
        self.assertTrue(status_report["configured"])
        # Crucial invariant: live_api_verified must NOT be True unless an authentic API responded live
        if not config.has_credentials():
            self.assertFalse(status_report["live_api_verified"])
            self.assertEqual(status_report["status"], "NO_CREDENTIALS")
        else:
            # If credentials exist but account is unpaid / rate-limited or model unreleased:
            self.assertIn(
                status_report["status"],
                ["MODEL_NOT_FOUND", "CREDENTIALS_EXHAUSTED_OR_RATE_LIMITED", "API_ERROR"],
            )
            self.assertFalse(status_report["live_api_verified"])


if __name__ == "__main__":
    unittest.main()
