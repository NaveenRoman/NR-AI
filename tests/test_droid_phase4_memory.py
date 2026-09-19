"""
Tests for Droid Phase 4 Component 10: Persistent Project Engineering Memory.
"""
import unittest
from app.agent.android_project_memory import AndroidProjectMemoryStore, ProjectMemoryRecord

class TestDroidPhase4Memory(unittest.TestCase):
    def setUp(self):
        self.store = AndroidProjectMemoryStore(db_path=":memory:")

    def test_save_and_load_memory(self):
        rec = ProjectMemoryRecord(
            project_id="test_proj_1",
            modules=[":app", ":core"],
            important_symbols=["MainActivity", "AppRepository"],
        )
        self.assertTrue(self.store.save_memory(rec))
        loaded = self.store.load_memory("test_proj_1")
        self.assertEqual(loaded.project_id, "test_proj_1")
        self.assertEqual(loaded.modules, [":app", ":core"])
        self.assertEqual(loaded.important_symbols, ["MainActivity", "AppRepository"])

    def test_secret_redaction_in_memory(self):
        rec = ProjectMemoryRecord(
            project_id="secret_proj",
            known_build_issues=[{"error": "API_KEY=AIzaSySecret1234567890123456789012345"}],
        )
        self.store.save_memory(rec)
        loaded = self.store.load_memory("secret_proj")
        json_repr = str(loaded.known_build_issues)
        self.assertNotIn("AIzaSySecret1234567890123456789012345", json_repr)

    def test_repair_pattern_recording(self):
        self.store.record_successful_repair(
            project_id="repair_proj",
            defect_type="ARITHMETIC_EXCEPTION",
            file_path="MainActivity.kt",
            patch_summary="Added zero check guard",
        )
        patterns = self.store.get_relevant_repair_patterns("repair_proj", "ARITHMETIC_EXCEPTION")
        self.assertEqual(len(patterns), 1)
        self.assertEqual(patterns[0]["file"], "MainActivity.kt")
