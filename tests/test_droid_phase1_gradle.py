"""
Tests for Droid Phase 1: Gradle Version Catalog & TOML Intelligence Subsystem
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from app.agent.android_gradle_intelligence import (
    BundleEntry,
    GradleVersionCatalogEngine,
    LibraryEntry,
    PluginEntry,
    VersionCatalogReport,
    VersionEntry,
)
from app.agent.android_safety import AndroidSafetyError, AndroidSafetyGate

SAMPLE_TOML = """
[versions]
agp = "8.2.0"
kotlin = "1.9.20"
compose = "1.5.4"
lifecycle = "2.6.2"

[libraries]
core-ktx = { group = "androidx.core", name = "core-ktx", version = "1.12.0" }
compose-ui = { group = "androidx.compose.ui", name = "ui", version.ref = "compose" }
compose-material = { module = "androidx.compose.material3:material3", version.ref = "compose" }
compose-tooling = "androidx.compose.ui:ui-tooling:1.5.4"
broken-lib = { group = "com.example", name = "broken", version.ref = "missing-version" }

[plugins]
android-application = { id = "com.android.application", version.ref = "agp" }
kotlin-android = { id = "org.jetbrains.kotlin.android", version.ref = "kotlin" }
broken-plugin = { id = "com.example.plugin", version.ref = "nonexistent-version" }

[bundles]
compose = ["compose-ui", "compose-material"]
"""


class TestDroidPhase1GradleIntelligence(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.safety = AndroidSafetyGate()
        self.engine = GradleVersionCatalogEngine(safety_gate=self.safety)
        self.toml_file = Path(self.tmp_dir) / "libs.versions.toml"
        self.toml_file.write_text(SAMPLE_TOML, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_parse_catalog_structure(self):
        """Verify complete TOML catalog parsing with strong typing."""
        report = self.engine.parse_catalog_file(self.toml_file)
        self.assertIsInstance(report, VersionCatalogReport)
        self.assertEqual(report.versions_count, 4)
        self.assertEqual(report.libraries_count, 5)
        self.assertEqual(report.plugins_count, 3)
        self.assertEqual(report.bundles_count, 1)

        self.assertIn("compose", report.versions)
        self.assertEqual(report.versions["compose"].value, "1.5.4")

        self.assertIn("compose-ui", report.libraries)
        self.assertEqual(report.libraries["compose-ui"].version_ref, "compose")
        self.assertEqual(report.libraries["compose-ui"].module, "androidx.compose.ui:ui")

        self.assertIn("android-application", report.plugins)
        self.assertEqual(report.plugins["android-application"].version_ref, "agp")

        self.assertIn("compose", report.bundles)
        self.assertEqual(report.bundles["compose"].libraries, ["compose-ui", "compose-material"])

    def test_02_unresolved_reference_detection(self):
        """Verify broken version references are caught and reported."""
        report = self.engine.parse_catalog_file(self.toml_file)
        unresolved = report.unresolved_references
        self.assertEqual(len(unresolved), 2)

        unresolved_aliases = {u["alias"] for u in unresolved}
        self.assertIn("broken-lib", unresolved_aliases)
        self.assertIn("broken-plugin", unresolved_aliases)

    def test_03_find_dependents_of_version(self):
        """Verify reverse dependency queries for a given version alias."""
        report = self.engine.parse_catalog_file(self.toml_file)
        dependents = self.engine.find_dependents_of_version(report, "compose")
        self.assertEqual(len(dependents), 2)
        aliases = [d["alias"] for d in dependents]
        self.assertIn("compose-ui", aliases)
        self.assertIn("compose-material", aliases)

        agp_dependents = self.engine.find_dependents_of_version(report, "agp")
        self.assertEqual(len(agp_dependents), 1)
        self.assertEqual(agp_dependents[0]["alias"], "android-application")

    def test_04_atomic_version_update(self):
        """Verify safe transactional update of a version value with backup and hash update."""
        report_before = self.engine.parse_catalog_file(self.toml_file)
        initial_sha = report_before.sha256

        result = self.engine.update_version(
            toml_path=self.toml_file,
            version_alias="compose",
            new_version_value="1.6.0",
            expected_sha256=initial_sha,
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["old_version"], "1.5.4")
        self.assertEqual(result["new_version"], "1.6.0")
        self.assertTrue(Path(result["backup_file"]).exists())

        # Verify new file content
        report_after = self.engine.parse_catalog_file(self.toml_file)
        self.assertEqual(report_after.versions["compose"].value, "1.6.0")
        self.assertEqual(report_after.sha256, result["new_sha256"])

    def test_05_stale_target_protection(self):
        """Verify update is blocked if the file hash doesn't match expected hash."""
        with self.assertRaises(AndroidSafetyError):
            self.engine.update_version(
                toml_path=self.toml_file,
                version_alias="compose",
                new_version_value="1.6.0",
                expected_sha256="wrong_hash_1234567890",
            )

    def test_06_rollback_functionality(self):
        """Verify catalog can be restored cleanly from backup."""
        report_before = self.engine.parse_catalog_file(self.toml_file)
        result = self.engine.update_version(
            toml_path=self.toml_file,
            version_alias="agp",
            new_version_value="8.3.0",
        )

        report_modified = self.engine.parse_catalog_file(self.toml_file)
        self.assertEqual(report_modified.versions["agp"].value, "8.3.0")

        # Rollback
        restored = self.engine.rollback_catalog(self.toml_file, result["backup_file"])
        self.assertTrue(restored)

        report_restored = self.engine.parse_catalog_file(self.toml_file)
        self.assertEqual(report_restored.versions["agp"].value, "8.2.0")
        self.assertEqual(report_restored.sha256, report_before.sha256)

    def test_07_size_limit_enforcement(self):
        """Verify oversized version catalogs are rejected."""
        oversized = Path(self.tmp_dir) / "large.toml"
        # 100 KB limit = 102400 bytes
        oversized.write_bytes(b"# " + b"x" * 105000)
        with self.assertRaises(ValueError) as ctx:
            self.engine.parse_catalog_file(oversized)
        self.assertIn("exceeds 100 KB", str(ctx.exception))

    def test_08_invalid_toml_syntax_handling(self):
        """Verify malformed TOML raises clean syntax errors."""
        bad_toml = Path(self.tmp_dir) / "broken.toml"
        bad_toml.write_text("[versions\nbad_syntax = ", encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            self.engine.parse_catalog_file(bad_toml)
        self.assertIn("Malformed TOML", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
