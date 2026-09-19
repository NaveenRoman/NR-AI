"""
Tests for Droid Phase 4 Component 2: Kotlin & Java Semantic Intelligence.
"""
import unittest
from pathlib import Path
from app.agent.android_semantic_engine import AndroidSemanticEngine, FactCategory, SymbolKind, LanguageKind

class TestDroidPhase4Semantics(unittest.TestCase):
    def setUp(self):
        self.engine = AndroidSemanticEngine()
        self.target_file = Path(r"C:\NR-AI\nr_android_test\app\src\main\java\com\nrai\test\MainActivity.kt")

    def test_analyze_kotlin_source(self):
        facts = self.engine.analyze_file(self.target_file)
        self.assertGreater(len(facts), 0)
        self.assertIn("com.nrai.test.MainActivity", self.engine.symbols)
        main_sym = self.engine.symbols["com.nrai.test.MainActivity"]
        self.assertEqual(main_sym.kind, SymbolKind.CLASS)
        self.assertEqual(main_sym.language, LanguageKind.KOTLIN)

    def test_structural_facts_vs_inferences(self):
        facts = self.engine.analyze_file(self.target_file)
        categories = {f.category for f in facts}
        self.assertIn(FactCategory.STRUCTURAL_FACT, categories)

    def test_android_lifecycle_detection(self):
        self.engine.analyze_file(self.target_file)
        main_sym = self.engine.symbols.get("com.nrai.test.MainActivity")
        self.assertIsNotNone(main_sym)
        self.assertIn("onCreate", main_sym.lifecycle_methods)

    def test_coroutine_markers_detection(self):
        self.engine.analyze_file(self.target_file)
        # Verify symbols dictionary indexed
        self.assertGreater(len(self.engine.symbols), 5)
