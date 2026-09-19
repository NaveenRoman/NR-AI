"""
Tests for Droid Phase 4 Component 4: Gradle Build & Dependency Graph.
"""
import unittest
from pathlib import Path
from app.agent.android_build_graph import AndroidBuildGraphEngine, DependencyScope, GradleIssueKind

class TestDroidPhase4GradleGraph(unittest.TestCase):
    def setUp(self):
        self.engine = AndroidBuildGraphEngine()
        self.project_path = Path(r"C:\NR-AI\nr_android_test")

    def test_build_graph_modules(self):
        bg = self.engine.build_graph(self.project_path)
        self.assertEqual(bg.project_name, "nr_android_test")
        self.assertIn(":app", bg.modules)
        app_mod = bg.modules[":app"]
        self.assertFalse(app_mod.is_library)
        self.assertIn("com.android.application", app_mod.plugins)

    def test_version_catalog_parsing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            gradle_dir = td_path / "gradle"
            gradle_dir.mkdir(parents=True, exist_ok=True)
            (td_path / "settings.gradle").write_text("rootProject.name = 'temp_proj'\n", encoding="utf-8")
            (gradle_dir / "libs.versions.toml").write_text(
                '[versions]\ncoreKtx = "1.12.0"\n\n[libraries]\nandroidx-core-ktx = { group = "androidx.core", name = "core-ktx", version.ref = "coreKtx" }\n',
                encoding="utf-8"
            )
            bg = self.engine.build_graph(td_path)
            self.assertIn("coreKtx", bg.version_catalog_versions)
            self.assertIn("androidx-core-ktx", bg.version_catalog_libraries)

    def test_dependency_scopes(self):
        bg = self.engine.build_graph(self.project_path)
        app_mod = bg.modules[":app"]
        scopes = {d.scope for d in app_mod.dependencies}
        self.assertIn(DependencyScope.TEST_IMPLEMENTATION, scopes)

    def test_graph_serialization(self):
        bg = self.engine.build_graph(self.project_path)
        d = bg.to_dict()
        self.assertEqual(d["project_name"], "nr_android_test")
        self.assertGreater(d["total_dependencies"], 0)
