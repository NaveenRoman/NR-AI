"""
Tests for Galaxy Droid Hierarchy Invariants & Agent Information Context.
"""

import unittest
from app.ui.galaxy_engine import GalaxyEngine


class TestGalaxyDroidHierarchy(unittest.TestCase):

    def setUp(self):
        self.engine = GalaxyEngine()

    def test_child_agents_registered(self):
        state = self.engine.get_galaxy_state()
        nodes = {n["agent_id"]: n for n in state["nodes"]}
        self.assertIn("android_unified_agent", nodes)
        self.assertIn("droid_scout", nodes)
        self.assertIn("droid_guardian", nodes)

        scout = nodes["droid_scout"]
        self.assertEqual(scout["friendly_name"], "Droid Scout")
        self.assertEqual(scout["role"], "Android Studio Watch & Development Assistant")
        self.assertEqual(scout["parent_agent"], "android_unified_agent")

        guardian = nodes["droid_guardian"]
        self.assertEqual(guardian["friendly_name"], "Droid Guardian")
        self.assertEqual(guardian["role"], "Android Build & Verification Guardian")
        self.assertEqual(guardian["parent_agent"], "android_unified_agent")

    def test_strict_hierarchy_invariants_no_direct_central_connections(self):
        state = self.engine.get_galaxy_state()
        conns = state["connections"]

        # 1. Zero connections from nr_ai_central_intelligence to child specialists
        central_to_children = [
            c for c in conns 
            if c["from"] == "nr_ai_central_intelligence" and c["to"] in ("droid_scout", "droid_guardian")
        ]
        self.assertEqual(len(central_to_children), 0, "Droid Scout and Guardian must NOT connect directly to NR-AI central core!")

        # 2. Exactly 2 connections from android_unified_agent to child specialists
        droid_to_children = [
            c for c in conns
            if c["from"] == "android_unified_agent" and c["to"] in ("droid_scout", "droid_guardian")
        ]
        self.assertEqual(len(droid_to_children), 2, "Droid must have exactly 2 satellite connections to Scout and Guardian!")

    def test_agent_context_parent_and_children(self):
        droid_ctx = self.engine.get_agent_context("android_unified_agent")
        self.assertEqual(droid_ctx["parent_agent"], "nr_ai_central_intelligence")
        self.assertIn("droid_scout", droid_ctx["child_agents"])
        self.assertIn("droid_guardian", droid_ctx["child_agents"])
        self.assertEqual(droid_ctx["safety_state"], "ModelIsolationGate (Deterministic)")

        scout_ctx = self.engine.get_agent_context("droid_scout")
        self.assertEqual(scout_ctx["parent_agent"], "android_unified_agent")
        self.assertEqual(len(scout_ctx["child_agents"]), 0)


if __name__ == "__main__":
    unittest.main()
