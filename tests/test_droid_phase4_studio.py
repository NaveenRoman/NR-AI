"""
Tests for Droid Phase 4 Component 1: Android Studio Deep Integration.
"""
import unittest
from pathlib import Path
from app.agent.android_studio_intelligence import AndroidStudioIntelligence, AndroidStudioProjectSnapshot

class TestDroidPhase4Studio(unittest.TestCase):
    def setUp(self):
        self.engine = AndroidStudioIntelligence()
        self.project_path = Path(r"C:\NR-AI\nr_android_test")

    def test_discover_studio_installation(self):
        install = self.engine.discover_studio_installation()
        self.assertIsNotNone(install, "Android Studio must be discovered on host.")
        self.assertTrue(Path(install.studio_home).exists())
        self.assertTrue(Path(install.jbr_home).exists())
        self.assertTrue(Path(install.java_executable).exists())
        self.assertTrue(install.version_string.startswith("AI-"))

    def test_inspect_project(self):
        snap = self.engine.inspect_project(self.project_path)
        self.assertIsInstance(snap, AndroidStudioProjectSnapshot)
        self.assertEqual(snap.project_name, "nr_android_test")
        self.assertIn(":app", snap.modules)
        self.assertEqual(snap.agp_version, "8.7.0")
        self.assertEqual(snap.gradle_version, "8.10.2")
        self.assertEqual(snap.kotlin_version, "1.9.24")
        self.assertTrue(snap.has_wrapper)

    def test_snapshot_serialization(self):
        snap = self.engine.inspect_project(self.project_path)
        d = snap.to_dict()
        self.assertEqual(d["project_name"], "nr_android_test")
        self.assertIn("modules", d)
        self.assertIn("agp_version", d)
