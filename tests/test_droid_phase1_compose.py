"""
Tests for Droid Phase 1: Jetpack Compose Intelligence Subsystem
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from app.agent.android_compose import (
    ComposeComponentEntry,
    ComposeIntelligenceReport,
    ComposeStateEntry,
    JetpackComposeIntelligenceEngine,
)

SAMPLE_COMPOSE_KT = """package com.example.app.ui

import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberSaveable
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Box
import androidx.compose.material3.Text
import androidx.compose.material3.Button
import androidx.compose.foundation.Image
import androidx.compose.ui.Modifier
import androidx.compose.foundation.layout.fillMaxSize

@Composable
fun CounterScreen(initialCount: Int = 0) {
    val count = rememberSaveable { mutableStateOf(initialCount) }
    val buggyState = mutableStateOf("Buggy Unremembered")

    Column(modifier = Modifier.fillMaxSize()) {
        Text("Current Count: ${count.value}")
        Button(onClick = { count.value++ }) {
            Text(text = "Increment")
        }
        Image(modifier = Modifier.fillMaxSize())
    }
}

@Preview
@Composable
fun CounterScreenPreview() {
    CounterScreen()
}
"""


class TestDroidPhase1Compose(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.kt_file = Path(self.tmp_dir) / "CounterScreen.kt"
        self.kt_file.write_text(SAMPLE_COMPOSE_KT, encoding="utf-8")
        self.engine = JetpackComposeIntelligenceEngine()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_detect_composables_and_previews(self):
        """Verify detection of @Composable functions and @Preview annotations."""
        report = self.engine.analyze_file(self.kt_file)
        self.assertIsInstance(report, ComposeIntelligenceReport)
        self.assertEqual(report.total_composables, 2)
        self.assertEqual(report.total_previews, 1)

        self.assertIn("CounterScreen", report.composables)
        self.assertIn("CounterScreenPreview", report.composables)

        self.assertFalse(report.composables["CounterScreen"].is_preview)
        self.assertTrue(report.composables["CounterScreenPreview"].is_preview)

    def test_02_detect_layout_containers(self):
        """Verify layout containers (Column) are extracted."""
        report = self.engine.analyze_file(self.kt_file)
        screen = report.composables["CounterScreen"]
        self.assertIn("Column", screen.layout_containers)

    def test_03_detect_components_and_modifiers(self):
        """Verify UI components (Text, Button, Image) and click handlers are found."""
        report = self.engine.analyze_file(self.kt_file)
        screen = report.composables["CounterScreen"]

        comp_types = [c.component_type for c in screen.components]
        self.assertIn("Text", comp_types)
        self.assertIn("Button", comp_types)
        self.assertIn("Image", comp_types)

        btn = next(c for c in screen.components if c.component_type == "Button")
        self.assertTrue(btn.has_click)

    def test_04_detect_state_holders(self):
        """Verify state management declarations are indexed."""
        report = self.engine.analyze_file(self.kt_file)
        screen = report.composables["CounterScreen"]

        state_names = {s.name: s for s in screen.state_entries}
        self.assertIn("count", state_names)
        self.assertIn("buggyState", state_names)

        self.assertTrue(state_names["count"].has_remember)
        self.assertTrue(state_names["count"].is_saveable)

        self.assertFalse(state_names["buggyState"].has_remember)

    def test_05_anti_pattern_semantic_inference(self):
        """Verify unremembered mutableStateOf triggers a semantic warning."""
        report = self.engine.analyze_file(self.kt_file)
        screen = report.composables["CounterScreen"]

        inf_types = [i["type"] for i in screen.semantic_inferences]
        self.assertIn("ANTI_PATTERN_UNREMEMBERED_STATE", inf_types)

        unrem_inf = next(i for i in screen.semantic_inferences if i["type"] == "ANTI_PATTERN_UNREMEMBERED_STATE")
        self.assertEqual(unrem_inf["variable"], "buggyState")
        self.assertEqual(unrem_inf["severity"], "WARNING")

    def test_06_accessibility_semantic_inference(self):
        """Verify Image without contentDescription triggers accessibility warning."""
        report = self.engine.analyze_file(self.kt_file)
        screen = report.composables["CounterScreen"]

        inf_types = [i["type"] for i in screen.semantic_inferences]
        self.assertIn("ACCESSIBILITY_MISSING_CONTENT_DESCRIPTION", inf_types)

    def test_07_distinguish_structural_vs_semantic(self):
        """Verify structural detections and semantic inferences are explicitly partitioned."""
        report = self.engine.analyze_file(self.kt_file)
        screen = report.composables["CounterScreen"]

        self.assertGreater(len(screen.structural_detections), 0)
        self.assertGreater(len(screen.semantic_inferences), 0)

        for detection in screen.structural_detections:
            self.assertIsInstance(detection, str)

        for inference in screen.semantic_inferences:
            self.assertIsInstance(inference, dict)
            self.assertIn("type", inference)
            self.assertIn("severity", inference)
            self.assertIn("message", inference)


if __name__ == "__main__":
    unittest.main()
