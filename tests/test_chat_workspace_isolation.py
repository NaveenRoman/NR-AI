import json
import os
import unittest
from unittest.mock import MagicMock, patch

from app.agent.engineering_intent import canonicalize_agent_id
from app.brain.companion import NRCompanion, CommandCategory
from app.voice.listener import VoiceConfig, DummyVoiceListener
from app.voice.speaker import VoiceSpeaker


class TestChatWorkspaceIsolation(unittest.TestCase):
    """Verifies strict per-agent chat workspace isolation and central intelligence."""

    @classmethod
    def setUpClass(cls):
        config = VoiceConfig.from_env()
        config.silent_mode = True
        config.tts_enabled = False
        listener = DummyVoiceListener(config=config)
        speaker = VoiceSpeaker(config=config)
        cls.companion = NRCompanion(
            voice_config=config,
            voice_listener=listener,
            voice_speaker=speaker,
        )

    def setUp(self):
        # Clear histories before each test
        for aid in ("nr_ai", "droid", "droid_scout", "droid_guardian", "studio", "unity", "unreal", "knowledge"):
            self.companion.clear_agent_chat_history(aid)
        self.companion.active_conversation_agent = None
        self.companion.active_conversation_agent_name = None

    def test_01_canonicalize_agent_id(self):
        """canonicalize_agent_id maps aliases to stable canonical identifiers."""
        self.assertEqual(canonicalize_agent_id("nr_ai"), "nr_ai")
        self.assertEqual(canonicalize_agent_id("NR-AI"), "nr_ai")
        self.assertEqual(canonicalize_agent_id("nr_ai_central_intelligence"), "nr_ai")
        self.assertEqual(canonicalize_agent_id("central"), "nr_ai")
        self.assertEqual(canonicalize_agent_id("android_unified_agent"), "droid")
        self.assertEqual(canonicalize_agent_id("droid"), "droid")
        self.assertEqual(canonicalize_agent_id("Droid Scout"), "droid_scout")
        self.assertEqual(canonicalize_agent_id("vs_unified_agent"), "studio")
        self.assertEqual(canonicalize_agent_id("visual_studio"), "studio")
        self.assertEqual(canonicalize_agent_id("unity_autonomous_agent"), "unity")
        self.assertEqual(canonicalize_agent_id("unreal_autonomous_agent"), "unreal")
        self.assertEqual(canonicalize_agent_id("universal_knowledge_engine"), "knowledge")
        self.assertEqual(canonicalize_agent_id("security_agent"), "skyshield")
        self.assertEqual(canonicalize_agent_id("skyshield"), "skyshield")

    def test_02_independent_chat_recording_and_isolation(self):
        """Messages added to Agent A do NOT leak to Agent B."""
        self.companion.add_agent_chat_message("nr_ai", role="user", text="Hello NR-AI")
        self.companion.add_agent_chat_message("nr_ai", role="assistant", text="Hello Boss!")

        self.companion.add_agent_chat_message("droid", role="user", text="inspect project")
        self.companion.add_agent_chat_message("droid", role="assistant", text="Droid: Project OK")

        nrai_hist = self.companion.get_agent_chat_history("nr_ai")
        droid_hist = self.companion.get_agent_chat_history("droid")
        unity_hist = self.companion.get_agent_chat_history("unity")

        self.assertEqual(len(nrai_hist), 2)
        self.assertEqual(len(droid_hist), 2)
        self.assertEqual(len(unity_hist), 0)

        # Content isolation check
        nrai_contents = [m["content"] for m in nrai_hist]
        droid_contents = [m["content"] for m in droid_hist]

        self.assertIn("Hello NR-AI", nrai_contents)
        self.assertIn("Hello Boss!", nrai_contents)
        self.assertNotIn("inspect project", nrai_contents)
        self.assertNotIn("Droid: Project OK", nrai_contents)

        self.assertIn("inspect project", droid_contents)
        self.assertIn("Droid: Project OK", droid_contents)
        self.assertNotIn("Hello NR-AI", droid_contents)
        self.assertNotIn("Hello Boss!", droid_contents)

    def test_03_message_schema_and_immutability(self):
        """Message items follow required schema: { message_id, agent_id, role, content, timestamp }."""
        self.companion.add_agent_chat_message("studio", role="user", text="compile solution")
        hist = self.companion.get_agent_chat_history("studio")
        self.assertEqual(len(hist), 1)
        msg = hist[0]

        self.assertIn("message_id", msg)
        self.assertEqual(msg["agent_id"], "studio")
        self.assertEqual(msg["role"], "user")
        self.assertEqual(msg["content"], "compile solution")
        self.assertIn("timestamp", msg)

    def test_04_clear_chat_isolation(self):
        """Clearing Agent A chat does not touch Agent B chat."""
        self.companion.add_agent_chat_message("nr_ai", role="user", text="Msg 1")
        self.companion.add_agent_chat_message("droid", role="user", text="Msg 2")

        self.companion.clear_agent_chat_history("nr_ai")

        self.assertEqual(len(self.companion.get_agent_chat_history("nr_ai")), 0)
        self.assertEqual(len(self.companion.get_agent_chat_history("droid")), 1)

    def test_05_central_intelligence_conceptual_answers(self):
        """Central NR-AI provides verified knowledge answers without raw echo fallbacks."""
        test_queries = [
            ("What is Kotlin?", ["kotlin", "jetbrains"]),
            ("How does Android Studio build an Android app?", ["gradle", "build", "aapt2"]),
            ("Explain Python decorators", ["decorator", "function", "wrapper"]),
            ("What is Unreal Engine?", ["unreal", "engine", "3d"]),
            ("How does Unity work?", ["unity", "engine", "monobehaviour"]),
            ("What is machine learning?", ["machine learning", "learning", "data"]),
            ("Explain quantum computing", ["quantum", "qubit"]),
        ]

        for query, expected_keywords in test_queries:
            resp = self.companion.interact(query, target_agent="nr_ai")
            text = resp.text
            self.assertTrue(text, f"Empty response for: {query}")
            self.assertFalse(text.startswith("I received your request:"), f"Raw echo returned for: {query}")
            self.assertNotIn("No active target", text, f"No active target error for: {query}")
            has_kw = any(kw in text.lower() for kw in expected_keywords)
            self.assertTrue(has_kw, f"Expected keywords {expected_keywords} in answer for '{query}':\n{text}")

    def test_06_specialist_coordination_no_hijack(self):
        """Central coordination logs to NR-AI and Droid without merging or hijacking."""
        coord_cmd = "Ask Droid to open Android Studio"
        resp = self.companion.interact(coord_cmd, target_agent="nr_ai")

        self.assertIn("Droid", resp.text)
        self.assertIn("android studio", resp.text.lower())

        nrai_hist = self.companion.get_agent_chat_history("nr_ai")
        droid_hist = self.companion.get_agent_chat_history("droid")

        # NR-AI history must contain the user coordination request and NR-AI reply
        nrai_user_msgs = [m["content"] for m in nrai_hist if m["role"] == "user"]
        self.assertIn(coord_cmd, nrai_user_msgs)

        # Droid history must contain Droid execution log
        self.assertGreaterEqual(len(droid_hist), 1)
        self.assertIn("Android Studio", droid_hist[-1]["content"])

        # NR-AI active conversation agent must NOT be hijacked
        self.assertIsNone(self.companion.active_conversation_agent)


if __name__ == "__main__":
    unittest.main()
