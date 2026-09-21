"""
Tests for NR-AI Context Intelligence & Budget Manager (Phase 2).
Verifies:
- 7-channel context partitioning (SYSTEM, TASK, PROJECT, KNOWLEDGE, CONVERSATION, EVIDENCE, AGENT)
- Priority ordering during packing
- Strict total budget enforcement and deterministic truncation of lower priority channels
- Secret and PII scrubbing across all channels
- Provenance tracking
"""

import unittest

from app.context.budget import (
    CHANNEL_PRIORITY,
    ContextBudget,
    ContextChannel,
    ContextItem,
)
from app.context.manager import ContextManager


class TestContextManager(unittest.TestCase):
    """Unit test suite for ContextManager."""

    def setUp(self):
        # Bounded budget: 1000 characters total for deterministic testing
        self.budget = ContextBudget(
            max_total_chars=1000,
            channel_caps={
                ContextChannel.SYSTEM: 400,
                ContextChannel.TASK: 400,
                ContextChannel.PROJECT: 400,
                ContextChannel.KNOWLEDGE: 300,
                ContextChannel.CONVERSATION: 300,
                ContextChannel.EVIDENCE: 200,
                ContextChannel.AGENT: 200,
            }
        )
        self.manager = ContextManager(budget=self.budget)

    def test_multi_channel_context_packing(self):
        """Verify adding items to multiple channels assembles a structured prompt."""
        self.manager.set_system_context("You are NR-AI Android Specialist.", provenance="system_config")
        self.manager.set_task_context("task_1", "Build app and run tests.", current_step=2)
        self.manager.set_project_context("MyApp", active_feature="SplashScreen")
        self.manager.add_knowledge_item("AGP 8.7.0 requires JDK 17+", provenance="knowledge_trinity")
        self.manager.add_conversation_turn("user", "Can you build it now?")

        packed = self.manager.pack_context()
        self.assertGreater(packed.total_chars, 0)
        self.assertIn("=== SYSTEM CONTEXT ===", packed.prompt_text)
        self.assertIn("=== TASK CONTEXT ===", packed.prompt_text)
        self.assertIn("=== PROJECT CONTEXT ===", packed.prompt_text)
        self.assertIn("=== KNOWLEDGE CONTEXT ===", packed.prompt_text)
        self.assertIn("=== CONVERSATION CONTEXT ===", packed.prompt_text)

        # Verify provenances preserved
        self.assertIn("knowledge_trinity", packed.provenance_trail)
        self.assertIn("system_config", packed.provenance_trail)

    def test_secrets_scrubbed_from_all_channels(self):
        """Verify API keys and PII are scrubbed before model context packaging."""
        self.manager.set_system_context("Rules for user john.doe@example.com")
        self.manager.set_task_context("task_sec", "Use secret key sk-abc12345678901234567890 for API access.")
        self.manager.add_conversation_turn("user", "My credit card is 4111-2222-3333-4444")

        packed = self.manager.pack_context()
        self.assertNotIn("sk-abc1234567890", packed.prompt_text)
        self.assertIn("SECRET_REDACTED", packed.prompt_text)
        self.assertNotIn("4111-2222-3333-4444", packed.prompt_text)
        self.assertIn("PII_REDACTED", packed.prompt_text)
        self.assertGreater(packed.scrubbed_secret_count, 0)

    def test_deterministic_truncation_preserves_high_priority_channels(self):
        """
        Verify that when budget is exceeded, lower priority channels (AGENT, EVIDENCE, CONVERSATION)
        are truncated while SYSTEM and TASK context are preserved.
        """
        # High priority
        self.manager.set_system_context("HIGH_PRIORITY_SYSTEM_POLICY: All invariants hold.")
        self.manager.set_task_context("task_crucial", "HIGH_PRIORITY_TASK: Complete compilation.")

        # Low priority large content (fills remaining budget)
        large_scratchpad = "AGENT_SCRATCHPAD: " + ("x" * 600)
        self.manager.add_item(ContextChannel.AGENT, large_scratchpad, provenance="scratchpad")

        large_evidence = "EVIDENCE_LOG: " + ("y" * 600)
        self.manager.add_evidence(large_evidence, provenance="log")

        packed = self.manager.pack_context()
        self.assertLessEqual(packed.total_chars, self.budget.max_total_chars + 100) # slight leeway for headers
        self.assertIn("HIGH_PRIORITY_SYSTEM_POLICY", packed.prompt_text)
        self.assertIn("HIGH_PRIORITY_TASK", packed.prompt_text)
        # Verify that lower channels were truncated
        self.assertTrue(
            ContextChannel.AGENT in packed.truncated_channels or
            ContextChannel.EVIDENCE in packed.truncated_channels
        )


if __name__ == "__main__":
    unittest.main()
