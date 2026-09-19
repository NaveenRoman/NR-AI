"""
Tests for Droid Phase 2: Jetpack Compose Visual Preview Subsystem.
"""

import tempfile
import unittest
from pathlib import Path
from app.agent.android_compose_preview import (
    ComposePreviewEngine,
    PreviewRenderStatus,
    PreviewArtifactStore,
)


SAMPLE_COMPOSE_PREVIEW = """
package com.nrai.test.ui

import androidx.compose.runtime.Composable
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.material3.Text
import androidx.compose.material3.Button
import androidx.compose.foundation.layout.Column

@Preview(name = "Main Screen Light", widthDp = 360, heightDp = 640, showBackground = true)
@Composable
fun MainScreenPreview() {
    Column {
        Text("Welcome to NR-AI Android Studio Agent")
        Button(onClick = { }) {
            Text("Launch")
        }
    }
}

@Preview(name = "Dark Mode", uiMode = "Configuration.UI_MODE_NIGHT_YES", fontScale = 1.2)
@Composable
fun DarkModePreview() {
    Text("Dark Mode Preview")
}
"""


class TestDroidPhase2ComposePreview(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name).resolve()

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_static_preview_parameter_extraction(self):
        """Verify @Preview parameters (name, dimensions, showBackground, etc.) are extracted."""
        engine = ComposePreviewEngine()
        report = engine.analyze_source(SAMPLE_COMPOSE_PREVIEW, file_path="memory://Sample.kt")

        self.assertEqual(report.total_previews, 2)
        p1 = report.previews[0]
        self.assertEqual(p1.function_name, "MainScreenPreview")
        self.assertEqual(p1.parameters.name, "Main Screen Light")
        self.assertEqual(p1.parameters.width_dp, 360)
        self.assertEqual(p1.parameters.height_dp, 640)
        self.assertTrue(p1.parameters.show_background)
        self.assertIn("Column", p1.layout_containers)
        self.assertIn("Button", p1.components_previewed)
        self.assertIn("Text", p1.components_previewed)

        p2 = report.previews[1]
        self.assertEqual(p2.function_name, "DarkModePreview")
        self.assertEqual(p2.parameters.name, "Dark Mode")
        self.assertEqual(p2.parameters.ui_mode, "Configuration.UI_MODE_NIGHT_YES")
        self.assertEqual(p2.parameters.font_scale, 1.2)

    def test_live_preview_render_truthful_unavailable(self):
        """Verify live rendering authoritatively reports PREVIEW_UNAVAILABLE when layoutlib is unconfigured."""
        engine = ComposePreviewEngine()
        res = engine.render_preview("MainScreenPreview", "memory://Sample.kt")

        self.assertEqual(res.status, PreviewRenderStatus.PREVIEW_UNAVAILABLE)
        self.assertIn("Headless LayoutLib preview renderer is not configured", res.diagnostic_reason)
        self.assertIsNone(res.artifact_path)

    def test_preview_artifact_store_retention(self):
        """Verify artifact store retains at most max_items files via FIFO rotation."""
        store = PreviewArtifactStore(root_dir=self.tmp_path, max_items=5)
        engine = ComposePreviewEngine(artifact_store=store)

        for i in range(10):
            src = f'@Preview(name = "P{i}")\n@Composable\nfun P{i}() {{ Text("{i}") }}'
            engine.analyze_source(src, file_path=f"memory://P{i}.kt")

        artifacts = store.list_artifacts()
        self.assertLessEqual(len(artifacts), 5)


if __name__ == "__main__":
    unittest.main()
