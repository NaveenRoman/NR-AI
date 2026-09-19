"""
Tests for Droid Phase 4 Component 5: XML Resource Graph & Cross-Reference.
"""
import unittest
from pathlib import Path
from app.agent.android_resource_graph import AndroidResourceGraph, ResourceAnomalyKind

class TestDroidPhase4ResourceGraph(unittest.TestCase):
    def setUp(self):
        self.engine = AndroidResourceGraph()
        self.project_path = Path(r"C:\NR-AI\nr_android_test")

    def test_index_definitions_and_references(self):
        report = self.engine.build_graph(self.project_path)
        self.assertGreater(report.total_resources, 0)
        self.assertIn("string/app_name", report.definitions)
        self.assertEqual(report.definitions["string/app_name"][0].value, "NR-AI Companion")

    def test_detect_anomalies(self):
        report = self.engine.build_graph(self.project_path)
        self.assertIsInstance(report.anomalies, list)

    def test_correlate_runtime_resource_error(self):
        err = "android.content.res.Resources$NotFoundException: String resource ID #0x7f100001"
        res = self.engine.correlate_runtime_error(err)
        self.assertEqual(res.get("category"), "RESOURCE_NOT_FOUND")
