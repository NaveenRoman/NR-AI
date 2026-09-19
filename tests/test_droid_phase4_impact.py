"""
Tests for Droid Phase 4 Component 11: Impact Analysis & Blast Radius.
"""
import unittest
from pathlib import Path
from app.agent.android_project_graph import AndroidProjectGraphEngine
from app.agent.android_impact import AndroidImpactAnalyzer, ImpactLevel

class TestDroidPhase4Impact(unittest.TestCase):
    def setUp(self):
        self.analyzer = AndroidImpactAnalyzer()
        self.kg_engine = AndroidProjectGraphEngine()
        self.project_path = Path(r"C:\NR-AI\nr_android_test")
        self.kg = self.kg_engine.build_knowledge_graph(self.project_path)

    def test_source_change_impact(self):
        target = self.project_path / "app" / "src" / "main" / "java" / "com" / "nrai" / "test" / "MainActivity.kt"
        report = self.analyzer.analyze_impact([target], kg=self.kg)
        self.assertIn(":app", report.affected_modules)
        self.assertGreater(len(report.minimum_sufficient_tests), 0)
        self.assertLessEqual(report.blast_radius_score, 50.0)

    def test_build_script_change_impact(self):
        build_file = self.project_path / "app" / "build.gradle.kts"
        report = self.analyzer.analyze_impact([build_file], kg=self.kg)
        self.assertTrue(report.requires_full_regression)
        self.assertIn("Build configuration file modified", str(report.reasons))
