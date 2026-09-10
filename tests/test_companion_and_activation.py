"""
Tests for NR-AI Activation, Companion Mode, News Agent, and Avatar State.
"""

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from app.agent.news_agent import (
    NewsAgent,
    NewsItem,
    NewsVerificationReport,
    VerificationStatus,
)
from app.brain.companion import CommandCategory, CompanionResponse, NRCompanion
from app.config.voice_config import VoiceConfig
from app.ui.avatar_state import (
    AvatarEmotion,
    AvatarFrame,
    AvatarMode,
    AvatarStateManager,
    EyeDirection,
)
from app.ui.dashboard import CompanionDashboard
from app.voice.listener import (
    ListeningState,
    MockSpeechRecognizer,
    VoiceListener,
)
from app.voice.speaker import AssistantState, MemoryTTS, VoiceSpeaker


class TestVoiceActivation(unittest.TestCase):
    def setUp(self):
        self.config = VoiceConfig(silent_mode=True, tts_enabled=False, wake_word_enabled=True)
        self.mock_backend = MockSpeechRecognizer()
        self.listener = VoiceListener(config=self.config, recognizer_backend=self.mock_backend)
        self.memory_tts = MemoryTTS()
        self.speaker = VoiceSpeaker(config=VoiceConfig(tts_enabled=True), tts_backend=self.memory_tts)

    def test_microphone_hardware_detection(self):
        probe = self.listener.probe_microphone()
        self.assertIn("available", probe)
        self.assertIn("device_count", probe)
        self.assertTrue(probe["push_to_talk_fallback"])
        # If running on this real Windows machine, devices are detected
        if probe["available"]:
            self.assertGreater(probe["device_count"], 0)
            self.assertIsNotNone(probe["active_device"])

    def test_wake_word_detection(self):
        # Test "Hey NR" wake word
        is_wake, cmd = self.listener.detect_wake_word("Hey NR what is happening in AI")
        self.assertTrue(is_wake)
        self.assertEqual(cmd, "what is happening in AI")

        # Test "Hello NR" wake word
        is_wake, cmd = self.listener.detect_wake_word("Hello NR, check the unity project")
        self.assertTrue(is_wake)
        self.assertEqual(cmd, "check the unity project")

        # Test non-wake word
        is_wake, cmd = self.listener.detect_wake_word("What time is it right now")
        self.assertFalse(is_wake)

    def test_push_to_talk_fallback_when_wake_disabled(self):
        self.listener.config.wake_word_enabled = False
        self.mock_backend.queue_text("Direct push-to-talk command")
        success, cmd = self.listener.listen_for_wake_word()
        self.assertTrue(success)
        self.assertEqual(cmd, "Direct push-to-talk command")

    def test_voice_output_and_state_transitions(self):
        states_seen = []
        self.speaker.add_state_callback(lambda s: states_seen.append(s))

        self.speaker.speak("Testing NR AI Voice Synthesis.")
        self.assertTrue(len(self.memory_tts.spoken_history) > 0)
        self.assertIn("Testing NR AI Voice Synthesis.", self.memory_tts.spoken_history[-1])

        # Verify states transitioned to SPEAKING then back to IDLE
        self.assertIn(AssistantState.SPEAKING, states_seen)
        self.assertEqual(self.speaker.state, AssistantState.IDLE)


class TestNewsAgent(unittest.TestCase):
    def setUp(self):
        self.news_agent = NewsAgent(cache_ttl_seconds=60.0)

    def test_news_categories_supported(self):
        categories = ["World", "India", "Technology", "AI", "Business", "Science", "Gaming"]
        for cat in categories:
            self.assertIn(cat, self.news_agent.FEED_REGISTRY)

    def test_real_news_fetch_and_metadata(self):
        # Fetch World news from live RSS
        items = self.news_agent.fetch_category("World", limit=3)
        if items:
            it = items[0]
            self.assertTrue(bool(it.headline))
            self.assertTrue(bool(it.source))
            self.assertTrue(bool(it.url))
            self.assertEqual(it.topic, "World")

    def test_real_ai_news_fetch(self):
        # Fetch AI news from ArXiv / Google News
        items = self.news_agent.fetch_category("AI", limit=3)
        if items:
            self.assertTrue(len(items) > 0)
            self.assertEqual(items[0].topic, "AI")

    def test_multi_source_verification_logic(self):
        report = self.news_agent.verify_headline("Artificial Intelligence deep learning developments", "AI")
        self.assertIsInstance(report, NewsVerificationReport)
        self.assertIn(report.status, [
            VerificationStatus.VERIFIED,
            VerificationStatus.SINGLE_SOURCE,
            VerificationStatus.CONFLICTING,
            VerificationStatus.UNREACHABLE,
        ])


class TestAvatarState(unittest.TestCase):
    def setUp(self):
        self.avatar = AvatarStateManager()

    def test_state_transitions(self):
        frames = []
        self.avatar.subscribe(lambda f: frames.append(f))

        f_listen = self.avatar.set_listening("Listening")
        self.assertEqual(f_listen.mode, AvatarMode.LISTENING)
        self.assertEqual(f_listen.emotion, AvatarEmotion.ATTENTIVE)

        f_think = self.avatar.set_thinking("Thinking")
        self.assertEqual(f_think.mode, AvatarMode.THINKING)
        self.assertEqual(f_think.eye_direction, EyeDirection.UP)

        f_speak = self.avatar.set_speaking("Hello")
        self.assertEqual(f_speak.mode, AvatarMode.SPEAKING)
        self.assertGreater(f_speak.mouth_open_ratio, 0.0)

        f_idle = self.avatar.set_idle("Done")
        self.assertEqual(f_idle.mode, AvatarMode.IDLE)
        self.assertEqual(f_idle.mouth_open_ratio, 0.0)

        # Check export adapters
        unity_bs = f_speak.to_unity_blendshapes()
        self.assertIn("JawOpen", unity_bs)
        unreal_ll = f_speak.to_unreal_livelink()
        self.assertIn("Curves", unreal_ll)


class TestCompanionAndRouting(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="nrai_companion_test_")
        self.companion = NRCompanion(workspace=self.temp_dir)

    def tearDown(self):
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    def test_command_classification(self):
        self.assertEqual(
            self.companion.classify_command("what's happening in AI?"),
            CommandCategory.NEWS_AI,
        )
        self.assertEqual(
            self.companion.classify_command("give me today's news"),
            CommandCategory.NEWS_GENERAL,
        )
        self.assertEqual(
            self.companion.classify_command("check the Unity project"),
            CommandCategory.UNITY,
        )
        self.assertEqual(
            self.companion.classify_command("work on Android"),
            CommandCategory.ANDROID,
        )
        self.assertEqual(
            self.companion.classify_command("prepare for Unreal Engine"),
            CommandCategory.UNREAL,
        )
        self.assertEqual(
            self.companion.classify_command("what are the agents doing?"),
            CommandCategory.AGENTS,
        )
        self.assertEqual(
            self.companion.classify_command("How are you today?"),
            CommandCategory.CONVERSATION,
        )
        self.assertEqual(
            self.companion.classify_command("create a Python program that prints test"),
            CommandCategory.COMPLEX_TASK,
        )

    def test_companion_natural_conversation(self):
        resp = self.companion.interact("Hello NR")
        self.assertEqual(resp.category, CommandCategory.CONVERSATION)
        self.assertIn("NR AI", resp.text)
        self.assertEqual(self.companion.avatar.current_frame.mode, AvatarMode.IDLE)

    def test_unity_routing(self):
        resp = self.companion.interact("check the Unity project")
        self.assertEqual(resp.category, CommandCategory.UNITY)
        self.assertIn("Unity", resp.text)
        self.assertEqual(resp.routed_to, "UnityToolchain")

    def test_android_routing(self):
        resp = self.companion.interact("work on Android")
        self.assertEqual(resp.category, CommandCategory.ANDROID)
        self.assertIn("Android", resp.text)
        self.assertEqual(resp.routed_to, "AndroidToolchain")

    def test_agents_status_routing(self):
        resp = self.companion.interact("what are the agents doing?")
        self.assertEqual(resp.category, CommandCategory.AGENTS)
        self.assertIn("10-Agent System Status", resp.text)
        self.assertEqual(resp.routed_to, "MultiAgentOrchestrator")

    def test_dashboard_integration(self):
        dashboard = CompanionDashboard(companion=self.companion)
        snapshot = dashboard.get_status_snapshot()

        self.assertIn("assistant_status", snapshot)
        self.assertIn("microphone", snapshot)
        self.assertIn("active_model", snapshot)
        self.assertIn("orchestrator_metrics", snapshot)
        self.assertEqual(snapshot["orchestrator_metrics"]["total_slots"], 10)

        terminal_view = dashboard.render_terminal_view()
        self.assertIn("NR-AI COMPANION DASHBOARD", terminal_view)


if __name__ == "__main__":
    unittest.main()
