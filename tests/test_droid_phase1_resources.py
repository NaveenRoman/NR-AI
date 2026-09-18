"""
Tests for Droid Phase 1: Android XML Resource Graph Subsystem
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from app.agent.android_resource_graph import (
    AndroidResourceGraphEngine,
    ResourceDefinition,
    ResourceGraphReport,
    ResourceReference,
)

STRINGS_XML = """<?xml version="1.0" encoding="utf-8"?>
<resources>
    <string name="app_name">DroidApp</string>
    <string name="unused_string">Never Used Anywhere</string>
    <string name="duplicate_string">First Value</string>
    <string name="duplicate_string">Second Value</string>
</resources>
"""

COLORS_XML = """<?xml version="1.0" encoding="utf-8"?>
<resources>
    <color name="primary">#FF0000</color>
</resources>
"""

COLORS_NIGHT_XML = """<?xml version="1.0" encoding="utf-8"?>
<resources>
    <color name="primary">#880000</color>
</resources>
"""

ACTIVITY_MAIN_XML = """<?xml version="1.0" encoding="utf-8"?>
<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android"
    android:layout_width="match_parent"
    android:layout_height="match_parent">

    <TextView
        android:id="@+id/tv_title"
        android:layout_width="wrap_content"
        android:layout_height="wrap_content"
        android:text="@string/app_name" />

    <Button
        android:id="@+id/btn_submit"
        android:layout_width="wrap_content"
        android:layout_height="wrap_content"
        android:text="@string/nonexistent_submit" />

</LinearLayout>
"""

MANIFEST_XML = """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.example.app">
    <application
        android:label="@string/app_name"
        android:theme="@style/Theme.App">
        <activity android:name=".MainActivity" />
    </application>
</manifest>
"""

MAIN_ACTIVITY_KT = """package com.example.app

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity

class MainActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        val btn = findViewById(R.id.btn_submit)
        val missingStr = getString(R.string.code_missing_string)
    }
}
"""


class TestDroidPhase1Resources(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.root = Path(self.tmp_dir)

        # Setup directory structure: app/src/main/res/values, layout, values-night
        self.res_dir = self.root / "app" / "src" / "main" / "res"
        (self.res_dir / "values").mkdir(parents=True, exist_ok=True)
        (self.res_dir / "values-night").mkdir(parents=True, exist_ok=True)
        (self.res_dir / "layout").mkdir(parents=True, exist_ok=True)

        (self.res_dir / "values" / "strings.xml").write_text(STRINGS_XML, encoding="utf-8")
        (self.res_dir / "values" / "colors.xml").write_text(COLORS_XML, encoding="utf-8")
        (self.res_dir / "values-night" / "colors.xml").write_text(COLORS_NIGHT_XML, encoding="utf-8")
        (self.res_dir / "layout" / "activity_main.xml").write_text(ACTIVITY_MAIN_XML, encoding="utf-8")

        manifest_dir = self.root / "app" / "src" / "main"
        (manifest_dir / "AndroidManifest.xml").write_text(MANIFEST_XML, encoding="utf-8")

        java_dir = self.root / "app" / "src" / "main" / "java" / "com" / "example" / "app"
        java_dir.mkdir(parents=True, exist_ok=True)
        (java_dir / "MainActivity.kt").write_text(MAIN_ACTIVITY_KT, encoding="utf-8")

        self.engine = AndroidResourceGraphEngine()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_build_graph_definitions(self):
        """Verify indexing of strings, colors, layouts, and ids."""
        report = self.engine.build_graph(self.root)
        self.assertIsInstance(report, ResourceGraphReport)

        self.assertIn("string/app_name", report.definitions)
        self.assertIn("color/primary", report.definitions)
        # Check night configuration recorded
        color_defs = report.definitions["color/primary"]
        self.assertEqual(len(color_defs), 2)
        configs = {d.config for d in color_defs}
        self.assertIn("default", configs)
        self.assertIn("night", configs)

        # Layout and IDs
        self.assertIn("layout/activity_main", report.definitions)
        self.assertIn("id/tv_title", report.definitions)
        self.assertIn("id/btn_submit", report.definitions)

    def test_02_detect_references_from_xml_and_code(self):
        """Verify references are discovered across layout XML, manifest, and Kotlin code."""
        report = self.engine.build_graph(self.root)

        # @string/app_name is referenced in layout XML and manifest
        self.assertIn("string/app_name", report.references)
        app_name_refs = report.references["string/app_name"]
        self.assertGreaterEqual(len(app_name_refs), 2)

        # R.layout.activity_main referenced in MainActivity.kt
        self.assertIn("layout/activity_main", report.references)

        # R.id.btn_submit referenced in MainActivity.kt
        self.assertIn("id/btn_submit", report.references)

    def test_03_detect_missing_resources(self):
        """Verify references to undefined resources are flagged."""
        report = self.engine.build_graph(self.root)
        missing_keys = {f"{m.res_type}/{m.name}" for m in report.missing_resources}

        # @string/nonexistent_submit from layout
        self.assertIn("string/nonexistent_submit", missing_keys)

        # R.string.code_missing_string from Kotlin
        self.assertIn("string/code_missing_string", missing_keys)

        # @style/Theme.App from manifest
        self.assertIn("style/Theme.App", missing_keys)

    def test_04_detect_unused_resources(self):
        """Verify defined resources that are never referenced are reported."""
        report = self.engine.build_graph(self.root)
        self.assertIn("string/unused_string", report.unused_resources)
        # string/app_name should NOT be in unused
        self.assertNotIn("string/app_name", report.unused_resources)

    def test_05_detect_duplicate_definitions(self):
        """Verify duplicate keys within the same values file are identified."""
        report = self.engine.build_graph(self.root)
        self.assertEqual(len(report.duplicates), 1)
        dup = report.duplicates[0]
        self.assertEqual(dup["resource"], "string/duplicate_string")
        self.assertEqual(dup["duplicate_name"], "duplicate_string")

    def test_06_malformed_xml_resilience(self):
        """Verify corrupted XML files do not crash the engine."""
        corrupt_xml = self.res_dir / "values" / "corrupted.xml"
        corrupt_xml.write_text("<resources><unclosed_tag", encoding="utf-8")

        report = self.engine.build_graph(self.root)
        self.assertIsInstance(report, ResourceGraphReport)
        self.assertIn("string/app_name", report.definitions)


if __name__ == "__main__":
    unittest.main()
