"""
Tests for Droid Phase 1: Full Integration Suite

Validates end-to-end orchestration across:
  - AndroidProjectRegistry
  - GradleVersionCatalogEngine
  - AndroidASTEngine
  - AndroidResourceGraphEngine
  - JetpackComposeIntelligenceEngine
  - AndroidTestResultParser
  - DroidTaskStateStore
  - UnifiedAndroidAgent
  - AndroidSafetyGate
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from app.agent.android_ast import AndroidASTEngine
from app.agent.android_code_repair import AndroidBuildError, AndroidErrorCategory
from app.agent.android_compose import JetpackComposeIntelligenceEngine
from app.agent.android_gradle_intelligence import GradleVersionCatalogEngine
from app.agent.android_project_registry import AndroidProjectRegistry
from app.agent.android_resource_graph import AndroidResourceGraphEngine
from app.agent.android_safety import AndroidSafetyError, AndroidSafetyGate
from app.agent.android_test_results import AndroidTestResultParser
from app.agent.android_unified_agent import UnifiedAndroidAgent
from app.agent.droid_task_state import DroidTaskStateStore, TaskState, TaskStatus

SAMPLE_TOML = """[versions]
kotlin = "1.9.20"
compose = "1.5.4"

[libraries]
compose-ui = { group = "androidx.compose.ui", name = "ui", version.ref = "compose" }
"""

SAMPLE_KOTLIN = """package com.example.droid

import androidx.appcompat.app.AppCompatActivity
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.runtime.mutableStateOf
import androidx.compose.material3.Text

class MainActivity : AppCompatActivity() {
    fun getMessage(): String {
        return "Hello Droid"
    }
}

@Composable
fun DroidGreeting(name: String) {
    val count = remember { mutableStateOf(0) }
    Text(text = "Hello $name, count: ${count.value}")
}
"""

SAMPLE_STRINGS_XML = """<?xml version="1.0" encoding="utf-8"?>
<resources>
    <string name="app_name">DroidPhase1App</string>
</resources>
"""

SAMPLE_LAYOUT_XML = """<?xml version="1.0" encoding="utf-8"?>
<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android"
    android:layout_width="match_parent"
    android:layout_height="match_parent">
    <TextView
        android:id="@+id/tv_greeting"
        android:layout_width="wrap_content"
        android:layout_height="wrap_content"
        android:text="@string/app_name" />
</LinearLayout>
"""

SAMPLE_JUNIT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="com.example.droid.MainActivityTest" tests="2" failures="1" errors="0" skipped="0" time="0.05">
  <testcase name="testSuccess" classname="com.example.droid.MainActivityTest" time="0.01"/>
  <testcase name="testMessage" classname="com.example.droid.MainActivityTest" time="0.02">
    <failure message="Expected Hello Droid but got null" type="java.lang.AssertionError">
\tat org.junit.Assert.fail(Assert.java:89)
\tat com.example.droid.MainActivityTest.testMessage(MainActivityTest.kt:25)
    </failure>
  </testcase>
</testsuite>
"""

SAMPLE_LINT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<issues format="6">
    <issue
        id="UnusedResources"
        severity="Warning"
        message="The resource R.string.unused appears to be unused"
        category="Performance"
        priority="3"
        summary="Unused resources"
        explanation="Unused resources make applications larger and slow down builds.">
        <location file="app/src/main/res/values/strings.xml" line="10"/>
    </issue>
</issues>
"""


class TestDroidPhase1Integration(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.project_dir = Path(self.tmp_dir) / "TestAndroidApp"
        self.project_dir.mkdir(parents=True, exist_ok=True)

        # Structure project
        (self.project_dir / "gradle").mkdir(parents=True, exist_ok=True)
        (self.project_dir / "build.gradle.kts").write_text("// build file\n", encoding="utf-8")
        (self.project_dir / "settings.gradle.kts").write_text("// settings file\n", encoding="utf-8")
        (self.project_dir / "gradle" / "libs.versions.toml").write_text(SAMPLE_TOML, encoding="utf-8")

        # Source code
        src_dir = self.project_dir / "app" / "src" / "main" / "java" / "com" / "example" / "droid"
        src_dir.mkdir(parents=True, exist_ok=True)
        self.kt_file = src_dir / "MainActivity.kt"
        self.kt_file.write_text(SAMPLE_KOTLIN, encoding="utf-8")

        # Resources
        res_dir = self.project_dir / "app" / "src" / "main" / "res"
        (res_dir / "values").mkdir(parents=True, exist_ok=True)
        (res_dir / "layout").mkdir(parents=True, exist_ok=True)
        (res_dir / "values" / "strings.xml").write_text(SAMPLE_STRINGS_XML, encoding="utf-8")
        (res_dir / "layout" / "activity_main.xml").write_text(SAMPLE_LAYOUT_XML, encoding="utf-8")

        # Manifest with valid XML namespace
        manifest_dir = self.project_dir / "app" / "src" / "main"
        (manifest_dir / "AndroidManifest.xml").write_text(
            '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.example.droid">'
            '<application android:label="@string/app_name"/></manifest>',
            encoding="utf-8",
        )

        # Reports
        reports_dir = self.project_dir / "app" / "build" / "test-results" / "testDebug"
        reports_dir.mkdir(parents=True, exist_ok=True)
        (reports_dir / "TEST-com.example.droid.MainActivityTest.xml").write_text(SAMPLE_JUNIT_XML, encoding="utf-8")

        lint_dir = self.project_dir / "app" / "build" / "reports"
        lint_dir.mkdir(parents=True, exist_ok=True)
        (lint_dir / "lint-results.xml").write_text(SAMPLE_LINT_XML, encoding="utf-8")

        # State database
        self.db_path = Path(self.tmp_dir) / "task_state.db"
        self.task_store = DroidTaskStateStore(db_path=self.db_path)

        # Project registry
        self.registry_file = Path(self.tmp_dir) / "projects.json"
        self.project_registry = AndroidProjectRegistry(registry_file=self.registry_file)

        # Safety gate
        self.safety_gate = AndroidSafetyGate(
            authorized_project=self.project_dir,
            project_registry=self.project_registry,
        )

        # Unified Agent
        self.agent = UnifiedAndroidAgent(
            safety_gate=self.safety_gate,
            project_registry=self.project_registry,
            task_state_store=self.task_store,
        )

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_end_to_end_project_registration_and_safety_gate(self):
        """Verify registering project dynamically allows file access through safety gate."""
        record = self.project_registry.register_project(
            project_path=self.project_dir,
            project_name="Droid Test App",
        )
        self.assertEqual(record.status, "ACTIVE")
        self.assertEqual(record.package_id, "com.example.droid")

        # Verify safety gate accepts files within this project
        self.safety_gate.validate_editable_file(self.kt_file)
        self.assertTrue(self.project_registry.is_path_authorized(self.kt_file))

    def test_02_task_state_persistence_and_lifecycle(self):
        """Verify task state creation, transitions, and checkpointing."""
        task = self.task_store.create_task(
            project_id=str(self.project_dir),
            workflow="droid_phase1_flow",
            initial_steps=["inspect", "build", "verify"],
            metadata={"phase": "Phase 1"},
        )
        self.assertEqual(task.status, TaskStatus.PENDING.value)
        self.assertEqual(task.current_state, TaskState.INITIALIZED.value)

        # Transition state
        updated = self.task_store.checkpoint_task(
            task_id=task.task_id,
            checkpoint_data={"step": "ast_and_resources"},
            completed_step="inspect",
            current_state=TaskState.INSPECTING.value,
        )
        self.assertEqual(updated.current_state, TaskState.INSPECTING.value)

        # Checkpoint retrieval
        latest = self.task_store.get_task(task.task_id)
        self.assertEqual(latest.current_state, TaskState.INSPECTING.value)
        self.assertEqual(latest.checkpoint.get("step"), "ast_and_resources")

    def test_03_unified_agent_version_catalog_inspection_and_mutation(self):
        """Verify agent inspects and transactionally modifies libs.versions.toml."""
        toml_path = self.project_dir / "gradle" / "libs.versions.toml"
        report = self.agent.inspect_version_catalog(toml_path)
        self.assertEqual(report.versions["compose"].value, "1.5.4")

        # Mutate compose version
        res = self.agent.gradle_intelligence.update_version(
            toml_path=toml_path,
            version_alias="compose",
            new_version_value="1.6.0",
        )
        self.assertTrue(res["success"])

        # Re-inspect
        report_after = self.agent.inspect_version_catalog(toml_path)
        self.assertEqual(report_after.versions["compose"].value, "1.6.0")

    def test_04_unified_agent_ast_and_compose_inspection(self):
        """Verify agent parses Kotlin AST and extracts Compose intelligence."""
        ast_rep = self.agent.inspect_source_ast(self.kt_file)
        self.assertEqual(ast_rep.package_name, "com.example.droid")
        self.assertIn("MainActivity", ast_rep.classes)
        self.assertIn("DroidGreeting", ast_rep.top_level_methods)

        # Compose intelligence
        compose_rep = self.agent.inspect_compose(self.kt_file)
        self.assertEqual(compose_rep.total_composables, 1)
        self.assertIn("DroidGreeting", compose_rep.composables)

        greeting = compose_rep.composables["DroidGreeting"]
        self.assertEqual(len(greeting.state_entries), 1)
        self.assertEqual(greeting.state_entries[0].name, "count")
        self.assertTrue(greeting.state_entries[0].has_remember)

    def test_05_unified_agent_resource_graph_inspection(self):
        """Verify agent builds bidirectional resource definitions and references."""
        res_rep = self.agent.inspect_resource_graph(self.project_dir)
        self.assertIn("string/app_name", res_rep.definitions)
        self.assertIn("layout/activity_main", res_rep.definitions)
        self.assertIn("id/tv_greeting", res_rep.definitions)

        # @string/app_name referenced in layout and manifest
        self.assertIn("string/app_name", res_rep.references)
        self.assertGreaterEqual(len(res_rep.references["string/app_name"]), 2)

    def test_06_unified_agent_test_and_lint_parsing_to_repair_targets(self):
        """Verify JUnit and Lint reports parse and convert to actionable repair errors."""
        summary = self.agent.test_parser.scan_project_results(self.project_dir)
        self.assertEqual(summary["total_test_suites"], 1)
        self.assertEqual(summary["total_test_failures"], 1)
        self.assertEqual(summary["total_lint_issues"], 1)

        junit_rep = self.agent.test_parser.parse_junit_xml(
            self.project_dir / "app" / "build" / "test-results" / "testDebug" / "TEST-com.example.droid.MainActivityTest.xml"
        )
        build_errors = self.agent.test_parser.to_build_errors(junit_report=junit_rep)
        self.assertEqual(len(build_errors), 1)
        self.assertEqual(build_errors[0].line, 25)
        self.assertIn("testMessage", build_errors[0].message)


if __name__ == "__main__":
    unittest.main()
