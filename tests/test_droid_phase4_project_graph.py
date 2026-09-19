"""
Tests for Droid Phase 4 Component 6: Multi-Module Project Reasoning & Knowledge Graph.
"""
import unittest
from pathlib import Path
from app.agent.android_project_graph import AndroidProjectGraphEngine, NodeType, EdgeType

class TestDroidPhase4ProjectGraph(unittest.TestCase):
    def setUp(self):
        self.engine = AndroidProjectGraphEngine()
        self.project_path = Path(r"C:\NR-AI\nr_android_test")

    def test_build_knowledge_graph(self):
        kg = self.engine.build_knowledge_graph(self.project_path)
        self.assertGreater(len(kg.nodes), 10)
        self.assertGreater(len(kg.edges), 10)
        self.assertIn(":app", kg.modules)

    def test_which_module_caused_failure(self):
        kg = self.engine.build_knowledge_graph(self.project_path)
        trace = "java.lang.ArithmeticException at com.nrai.test.MainActivity.onCreate(MainActivity.kt:38)"
        mod = self.engine.which_module_caused_failure(kg, trace)
        self.assertEqual(mod, ":app")

    def test_affected_modules(self):
        kg = self.engine.build_knowledge_graph(self.project_path)
        target = self.project_path / "app" / "src" / "main" / "java" / "com" / "nrai" / "test" / "MainActivity.kt"
        affected = self.engine.affected_modules(kg, target)
        self.assertIn(":app", affected)

    def test_which_dependency_introduced_class(self):
        kg = self.engine.build_knowledge_graph(self.project_path)
        deps = self.engine.which_dependency_introduced_class(kg, "androidx.compose.material3.Text")
        self.assertTrue(any("material3" in d for d in deps))
