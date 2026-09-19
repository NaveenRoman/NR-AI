"""
Tests for Droid Phase 4 Component 3: Jetpack Compose State & Interaction Intelligence.
"""
import unittest
from pathlib import Path
from app.agent.android_compose_intelligence import (
    AndroidComposeIntelligence,
    ComposePatternStatus,
    ComposeAnomalyKind,
)

class TestDroidPhase4Compose(unittest.TestCase):
    def setUp(self):
        self.engine = AndroidComposeIntelligence()
        self.target_file = Path(r"C:\NR-AI\nr_android_test\app\src\main\java\com\nrai\test\ComposeStateBugFixture.kt")
        self.project_path = Path(r"C:\NR-AI\nr_android_test")

    def test_analyze_composable_functions(self):
        report = self.engine.analyze_file(self.target_file)
        self.assertGreater(len(report.composables), 0)
        names = [c.name for c in report.composables]
        self.assertIn("MainScreen", names)

    def test_state_holder_extraction(self):
        report = self.engine.analyze_file(self.target_file)
        main_screen = next((c for c in report.composables if c.name == "MainScreen"), None)
        self.assertIsNotNone(main_screen)
        state_names = [s.get("name") or s.get("variable_name") for s in main_screen.state_holders]
        self.assertIn("counter", state_names)

    def test_project_wide_analysis(self):
        report = self.engine.analyze_project(self.project_path)
        self.assertGreater(report.total_composables, 0)
        d = report.to_dict()
        self.assertIn("total_composables", d)
