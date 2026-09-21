import unittest
from app.brain.companion import NRCompanion, canonicalize_agent_id
from app.agent.engineering_intent import EngineeringIntentParser, EngineeringDomain, EngineeringAction


class TestDroidRoutingAndExecution(unittest.TestCase):
    """
    Authoritative test suite verifying:
    1. Droid is the canonical primary Android engineering agent.
    2. Droid Guardian is an internal subsystem and never hijacks Droid or emits dummy safety strings.
    3. Actionable Android requests execute real workflows and return real execution evidence.
    4. Active chat remains Droid for all Android operations.
    """

    def setUp(self):
        self.companion = NRCompanion()
        # Reset state to clean Central NR-AI
        self.companion.active_conversation_agent = None
        self.companion.active_conversation_agent_name = None

    def test_canonical_droid_resolution(self):
        """Requirement 1: 'Droid', 'Android', 'Android Agent', 'hey hi droid' resolve to Droid."""
        # 1. Canonical ID mapping
        self.assertEqual(canonicalize_agent_id("droid"), "droid")
        self.assertEqual(canonicalize_agent_id("android"), "droid")
        self.assertEqual(canonicalize_agent_id("android_unified_agent"), "droid")
        self.assertEqual(canonicalize_agent_id("android agent"), "droid")

        # 2. Address resolution
        aid, name, rem = self.companion.resolve_addressed_agent("droid")
        self.assertEqual(aid, "android_unified_agent")
        self.assertEqual(name, "Droid")

        aid, name, rem = self.companion.resolve_addressed_agent("hey hi droid")
        self.assertEqual(aid, "android_unified_agent")
        self.assertEqual(name, "Droid")

        aid, name, rem = self.companion.resolve_addressed_agent("hey droid, open our project")
        self.assertEqual(aid, "android_unified_agent")
        self.assertEqual(name, "Droid")
        self.assertEqual(rem, "open our project")

        aid, name, rem = self.companion.resolve_addressed_agent("android agent: build the app")
        self.assertEqual(aid, "android_unified_agent")
        self.assertEqual(name, "Droid")
        self.assertEqual(rem, "build the app")

    def test_droid_greeting_sets_active_chat_to_droid(self):
        """User saying 'hey hi droid' activates Droid session, author is Droid, active chat is droid."""
        resp = self.companion.interact("hey hi droid")
        self.assertEqual(resp.routed_to, "Droid")
        self.assertEqual(self.companion.active_conversation_agent, "droid")
        self.assertEqual(self.companion.active_conversation_agent_name, "Droid")
        self.assertEqual(resp.data.get("active_conversation_agent"), "droid")
        self.assertTrue("listening" in resp.text.lower() or "droid" in resp.text.lower())

    def test_actionable_android_command_executes_via_droid_with_evidence(self):
        """Requirement 3: 'Open our NR-AI project in Android Studio and make Gradle run' executes via Droid."""
        resp = self.companion.interact("Open our NR-AI project in Android Studio and make Gradle run")
        
        # 1. Routed to Droid
        self.assertEqual(resp.routed_to, "Droid")
        self.assertEqual(self.companion.active_conversation_agent, "droid")
        self.assertEqual(resp.data.get("active_conversation_agent"), "droid")
        
        # 2. Author is Droid, NOT Droid Guardian
        self.assertFalse(resp.text.startswith("Droid Guardian: Verified state"))
        self.assertFalse(resp.text.startswith("Droid Guardian: Safety gate passed"))
        
        # 3. Contains real execution evidence
        self.assertEqual(resp.data.get("action"), "OPEN")
        self.assertEqual(resp.data.get("project"), "NR-AI")
        self.assertIn("studio_pid", resp.data)
        self.assertIn("window_verified", resp.data)
        self.assertIn("LIVE VERIFIED", resp.text)
        
        # 4. If Gradle run was requested, verified execution evidence is present
        self.assertTrue(resp.data.get("gradle_run"))
        self.assertIn("gradle_exit_code", resp.data)
        self.assertEqual(resp.data.get("gradle_exit_code"), 0)

    def test_droid_guardian_yields_actionable_commands_to_droid(self):
        """Requirement 2: Even if Droid Guardian was the active agent or target, actionable commands transfer to Droid."""
        # Force active agent to droid_guardian
        self.companion.active_conversation_agent = "droid_guardian"
        self.companion.active_conversation_agent_name = "Droid Guardian"

        # Send actionable engineering command with target_agent="droid_guardian"
        resp = self.companion.interact(
            "can you open our nr-ai project in android studio",
            target_agent="droid_guardian",
        )

        # 1. Droid Guardian must NOT hijack the interaction
        self.assertEqual(resp.routed_to, "Droid")
        self.assertEqual(self.companion.active_conversation_agent, "droid")
        self.assertEqual(self.companion.active_conversation_agent_name, "Droid")
        self.assertEqual(resp.data.get("active_conversation_agent"), "droid")

        # 2. Must NOT emit dummy safety gate text
        self.assertNotIn("Droid Guardian: Verified state for", resp.text)
        self.assertNotIn("Safety gate passed.", resp.text)

        # 3. Real execution result
        self.assertEqual(resp.data.get("action"), "OPEN")
        self.assertTrue("LIVE VERIFIED" in resp.text or resp.data.get("success") is True)

    def test_open_android_studio_executes_instead_of_dummy_placeholder(self):
        """Requirement 3: 'open android studio' executes real workflow and does not return dummy string."""
        self.companion.active_conversation_agent = None
        resp = self.companion.interact("open android studio")
        
        self.assertNotEqual(resp.text, "Sure, I'll open Android Studio.")
        self.assertEqual(resp.routed_to, "Droid")
        self.assertEqual(self.companion.active_conversation_agent, "droid")
        self.assertEqual(resp.data.get("action"), "OPEN")

    def test_explicit_droid_guardian_status_query(self):
        """Requirement 4: Pure status / verification query explicitly to Droid Guardian stays with Guardian."""
        resp = self.companion.interact("Droid Guardian, what is your status?")
        self.assertEqual(resp.routed_to, "Droid Guardian")
        self.assertIn("Droid Guardian", resp.text)
        self.assertTrue("Verification gate active" in resp.text or "monitoring gradle" in resp.text.lower())


if __name__ == "__main__":
    unittest.main()
