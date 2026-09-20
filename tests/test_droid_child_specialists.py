"""
Tests for Droid Child Specialists: DroidContext, DroidScoutAgent, DroidGuardianAgent.
Verifies:
- Hierarchy invariants (parent is Droid; no direct link to central intelligence).
- Model isolation: strictly reasoning/observation/verification without arbitrary mutations.
- Closed feedback loop: Build -> Guardian Verify -> Failure -> Diagnosis -> Repair -> Verify -> Success.
"""

import unittest
from app.agent.droid_child_agents import DroidContext, DroidScoutAgent, DroidGuardianAgent
from app.agent.android_unified_agent import UnifiedAndroidAgent


class TestDroidChildSpecialists(unittest.TestCase):

    def setUp(self):
        self.context = DroidContext()
        self.scout = DroidScoutAgent(self.context)
        self.guardian = DroidGuardianAgent(self.context)

    def test_hierarchy_invariants(self):
        self.assertEqual(self.scout.parent_agent_id, "android_unified_agent")
        self.assertEqual(self.guardian.parent_agent_id, "android_unified_agent")
        self.assertNotEqual(self.scout.parent_agent_id, "nr_ai_central_intelligence")
        self.assertNotEqual(self.guardian.parent_agent_id, "nr_ai_central_intelligence")

    def test_scout_observe_and_advise(self):
        obs = self.scout.observe()
        self.assertIn("project_path", obs)
        self.assertIn("status", obs)
        self.assertEqual(obs["status"], "ONLINE")

        analysis = self.scout.analyze()
        self.assertIn("healthy", analysis)

        advice = self.scout.advise()
        self.assertEqual(advice["advisor"], "Droid Scout")
        self.assertEqual(advice["parent"], "Droid")
        self.assertIn("suggested_action", advice)

    def test_guardian_monitor_and_diagnose_failure(self):
        fail_output = r"""
> Task :app:compileDebugJavaWithJavac FAILED
C:\NR-AI\dev_projects\NR-AI\app\src\main\java\com\nrai\nrai\MainActivity.java:18: error: cannot find symbol
        TextView tv = findViewById(R.id.missing_text_view);
                                       ^
  symbol:   variable missing_text_view
  location: class id
1 error
"""
        res = self.guardian.monitor_gradle_build(fail_output, exit_code=1)
        self.assertFalse(res["is_success"])
        self.assertEqual(res["verdict"], "FAILED")
        self.assertEqual(res["compiler_errors_count"], 1)

        diag = self.guardian.diagnose_failure(res)
        self.assertEqual(diag["diagnostic_type"], "UNRESOLVED_SYMBOL")
        self.assertIn("missing_text_view", diag["root_cause"])
        self.assertIn("MainActivity.java", diag["defect_target_file"])

    def test_guardian_verify_repair_closed_loop(self):
        fail_res = {
            "is_success": False,
            "exit_code": 1,
            "compiler_errors_count": 1,
        }
        success_res = {
            "is_success": True,
            "exit_code": 0,
            "compiler_errors_count": 0,
        }
        rep = self.guardian.verify_repair(fail_res, success_res)
        self.assertTrue(rep["repair_successful"])
        self.assertEqual(rep["verdict"], "VERIFIED_REPAIRED")
        self.assertEqual(len(self.context.repair_history), 1)

    def test_unified_agent_child_integration(self):
        droid = UnifiedAndroidAgent()
        self.assertIsNotNone(droid.scout)
        self.assertIsNotNone(droid.guardian)
        self.assertEqual(droid.scout.name, "Droid Scout")
        self.assertEqual(droid.guardian.name, "Droid Guardian")

        obs = droid.scout_observe()
        self.assertEqual(obs["status"], "ONLINE")


if __name__ == "__main__":
    unittest.main()
