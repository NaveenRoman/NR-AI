import unittest
from pathlib import Path

from app.agent.android_unified_agent import UnifiedAndroidAgent
from app.brain.companion import NRCompanion, CommandCategory


class TestDroidPhase5SpecialistIntegration(unittest.TestCase):

    def setUp(self):
        self.agent = UnifiedAndroidAgent()
        self.comp = NRCompanion()

    def test_phase5_facade_methods(self):
        self.assertTrue(hasattr(self.agent, "inspect_studio_workspace"))
        self.assertTrue(hasattr(self.agent, "audit_manifest_merge"))
        self.assertTrue(hasattr(self.agent, "audit_accessibility"))
        self.assertTrue(hasattr(self.agent, "diagnose_jank"))
        self.assertTrue(hasattr(self.agent, "audit_readiness"))
        self.assertTrue(hasattr(self.agent, "switch_active_project"))

    def test_phase5_companion_commands(self):
        commands = [
            "audit android readiness",
            "inspect studio workspace",
            "audit manifest merge",
            "audit android accessibility",
            "diagnose android jank",
            "list android projects",
        ]
        for cmd in commands:
            res = self.comp.handle_command(cmd)
            self.assertIn("category", res)
            self.assertEqual(res["category"], CommandCategory.ANDROID_STUDIO.value)


if __name__ == "__main__":
    unittest.main()
