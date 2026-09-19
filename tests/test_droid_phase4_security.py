"""
Tests for Droid Phase 4 Component 14: Safety Bounds & 10 Companion Commands.
"""
import unittest
from app.brain.companion import NRCompanion, CommandCategory
from app.agent.android_safety import MAX_REPAIR_ATTEMPTS

class TestDroidPhase4Security(unittest.TestCase):
    def setUp(self):
        self.comp = NRCompanion()

    def test_10_companion_commands_routing(self):
        phrases = [
            "inspect android studio project",
            "build gradle knowledge graph",
            "analyze kotlin semantics",
            "check jetpack compose state flow",
            "audit android xml resources",
            "diagnose test failure",
            "debug android ui behavior",
            "measure android startup performance",
            "calculate android blast radius",
            "run advanced android engineering loop",
        ]
        for phrase in phrases:
            cat = self.comp.classify_command(phrase)
            self.assertEqual(cat, CommandCategory.ANDROID_STUDIO, f"Phrase '{phrase}' failed to classify as ANDROID_STUDIO")

    def test_hard_repair_bounds_invariants(self):
        self.assertEqual(MAX_REPAIR_ATTEMPTS, 2)
