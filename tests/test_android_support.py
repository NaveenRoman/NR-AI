import os
import shutil
import sys
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.action_dispatcher import ActionDispatcher
from app.agent.android_toolchain import (
    AndroidErrorAnalyzer,
    AndroidProjectDetector,
    AndroidProjectInspector,
    AndroidToolchain,
)
from app.agent.task_planner import TaskPlanner
from app.brain.brain import NRBrain
from app.memory.context_memory import ProjectContextMemory


class TestAndroidSupport(unittest.TestCase):
    """
    Android Studio & Android Toolchain Support Test Suite.

    Validates:
    1. Android Project Detection (Compose vs XML)
    2. Android Project Inspection (Modules, Dependencies, SDKs, Resources)
    3. Toolchain Scaffolding (Architecture, Manifest, Kotlin, Gradle KTS)
    4. Screen & Component Addition (LoginScreen.kt)
    5. Dependency Management (Firebase Auth Injection)
    6. UI Theme Customization (Button Color Modification)
    7. Android & Gradle Error Diagnostics
    8. Natural Language & Voice Command Routing
    """

    def setUp(self):
        self.test_dir = PROJECT_ROOT / "data" / "test_android_workspace"
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.toolchain = AndroidToolchain(workspace=str(self.test_dir))
        self.memory = ProjectContextMemory(workspace=str(self.test_dir))
        self.planner = TaskPlanner(memory=self.memory)
        self.dispatcher = ActionDispatcher(memory=self.memory)
        self.brain = NRBrain(workspace=str(self.test_dir))

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_01_android_project_detector_compose(self):
        """Verify detector identifies Jetpack Compose Android app with Kotlin DSL."""
        scaffold_res = self.toolchain.scaffold_project(
            project_name="MyComposeApp",
            package_name="com.test.compose",
            target_dir="my_compose_app",
            use_compose=True,
        )
        self.assertTrue(scaffold_res["success"])

        app_dir = Path(scaffold_res["project_path"])
        detection = AndroidProjectDetector.detect(app_dir)
        self.assertTrue(detection["is_android"])
        self.assertEqual(detection["build_language"], "kotlin_dsl")
        self.assertEqual(detection["source_language"], "kotlin")
        self.assertEqual(detection["ui_framework"], "jetpack_compose")

    def test_02_android_project_detector_xml(self):
        """Verify detector identifies XML Layout-based Android app."""
        xml_app = self.test_dir / "xml_app"
        xml_app.mkdir(parents=True, exist_ok=True)
        (xml_app / "settings.gradle").write_text('include(":app")\n', encoding="utf-8")
        (xml_app / "build.gradle").write_text('// Root build\n', encoding="utf-8")
        app_mod = xml_app / "app"
        app_mod.mkdir(parents=True, exist_ok=True)
        (app_mod / "build.gradle").write_text('apply plugin: "com.android.application"\n', encoding="utf-8")
        res_layout = app_mod / "src" / "main" / "res" / "layout"
        res_layout.mkdir(parents=True, exist_ok=True)
        (res_layout / "activity_main.xml").write_text('<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android"/>\n', encoding="utf-8")

        detection = AndroidProjectDetector.detect(xml_app)
        self.assertTrue(detection["is_android"])
        self.assertEqual(detection["build_language"], "groovy_dsl")
        self.assertEqual(detection["ui_framework"], "xml_layouts")

    def test_03_android_project_inspector(self):
        """Verify inspector extracts SDK versions, namespace, modules, and dependencies."""
        scaffold_res = self.toolchain.scaffold_project(
            project_name="InspectApp",
            package_name="com.test.inspect",
            target_dir="inspect_app",
        )
        inspector = AndroidProjectInspector(scaffold_res["project_path"])
        info = inspector.inspect()

        self.assertTrue(info["is_android"])
        self.assertIn("app", info["modules"])
        self.assertEqual(info["app_module"]["namespace"], "com.test.inspect")
        self.assertEqual(info["app_module"]["compile_sdk"], 34)
        self.assertGreaterEqual(info["app_module"]["dependencies_count"], 5)
        self.assertGreaterEqual(info["source_files_count"], 1)

    def test_04_android_toolchain_scaffolding(self):
        """Verify complete modern Android project is scaffolded with all essential files."""
        res = self.toolchain.scaffold_project(
            project_name="ShopApp",
            package_name="com.shop.android",
            target_dir="shop_app",
        )
        self.assertTrue(res["success"])
        p = Path(res["project_path"])
        self.assertTrue((p / "settings.gradle.kts").exists())
        self.assertTrue((p / "build.gradle.kts").exists())
        self.assertTrue((p / "app" / "build.gradle.kts").exists())
        self.assertTrue((p / "app" / "src" / "main" / "AndroidManifest.xml").exists())
        self.assertTrue((p / "app" / "src" / "main" / "java" / "com" / "shop" / "android" / "MainActivity.kt").exists())
        self.assertTrue((p / "app" / "src" / "main" / "res" / "values" / "strings.xml").exists())

    def test_05_android_add_login_screen(self):
        """Verify adding a Jetpack Compose LoginScreen component."""
        scaffold_res = self.toolchain.scaffold_project(
            project_name="AuthApp",
            package_name="com.auth.app",
            target_dir="auth_app",
        )
        login_res = self.toolchain.add_login_screen(
            project_dir=scaffold_res["project_path"],
            package_name="com.auth.app",
        )
        self.assertTrue(login_res["success"])
        login_file = Path(login_res["file"])
        self.assertTrue(login_file.exists())
        content = login_file.read_text(encoding="utf-8")
        self.assertIn("@Composable", content)
        self.assertIn("fun LoginScreen", content)
        self.assertIn("PasswordVisualTransformation", content)

    def test_06_android_add_firebase_auth(self):
        """Verify injecting Firebase Auth dependency and helper."""
        scaffold_res = self.toolchain.scaffold_project(
            project_name="FirebaseApp",
            package_name="com.fb.app",
            target_dir="fb_app",
        )
        fb_res = self.toolchain.add_firebase_auth(
            project_dir=scaffold_res["project_path"],
            package_name="com.fb.app",
        )
        self.assertTrue(fb_res["success"])
        build_kts = Path(scaffold_res["project_path"]) / "app" / "build.gradle.kts"
        self.assertIn("firebase-auth-ktx", build_kts.read_text(encoding="utf-8"))

    def test_07_android_change_button_color(self):
        """Verify modifying button color in Color.kt."""
        scaffold_res = self.toolchain.scaffold_project(
            project_name="ColorApp",
            package_name="com.color.app",
            target_dir="color_app",
        )
        color_res = self.toolchain.change_button_color(
            project_dir=scaffold_res["project_path"],
            color_hex="0xFF00E676",
        )
        self.assertTrue(color_res["success"])
        color_file = Path(color_res["file"])
        self.assertIn("0xFF00E676", color_file.read_text(encoding="utf-8"))

    def test_08_android_error_analyzer_kotlin(self):
        """Verify analyzer parses Kotlin compiler error trace."""
        mock_log = (
            "e: C:/NR-AI/app/src/main/java/MainActivity.kt:25:12 Unresolved reference: FirebaseAuth\n"
        )
        diag = AndroidErrorAnalyzer.analyze(mock_log)
        self.assertEqual(diag["category"], "kotlin_compiler")
        self.assertEqual(diag["line"], 25)
        self.assertEqual(diag["error"], "Unresolved reference: FirebaseAuth")
        self.assertIn("build.gradle.kts", diag["suggestion"])

    def test_09_android_error_analyzer_gradle(self):
        """Verify analyzer parses Gradle dependency resolution failure."""
        mock_log = (
            "FAILURE: Build failed with an exception.\n"
            "Could not resolve com.example.missing:artifact:1.0.0\n"
        )
        diag = AndroidErrorAnalyzer.analyze(mock_log)
        self.assertEqual(diag["category"], "gradle_dependency")
        self.assertIn("repository declarations", diag["suggestion"])

    def test_10_android_error_analyzer_resource(self):
        """Verify analyzer parses AAPT missing resource errors."""
        mock_log = (
            "AAPT: error: resource color/missing_color not found.\n"
        )
        diag = AndroidErrorAnalyzer.analyze(mock_log)
        self.assertEqual(diag["category"], "android_resource")

    def test_11_android_voice_command_routing(self):
        """Verify natural language Android commands decompose and dispatch correctly."""
        commands = [
            ("Create a new Android app", "android_create"),
            ("Add a login screen", "android_add_login"),
            ("Add Firebase authentication", "android_add_firebase"),
            ("Change the button color", "android_change_color"),
            ("Fix the Gradle error", "modify_code"),
        ]

        for text, expected_type in commands:
            plan = self.planner.plan(text)
            self.assertTrue(plan["success"], f"Failed to plan: {text}")
            self.assertEqual(
                plan["actions"][0]["type"],
                expected_type,
                f"Expected {expected_type} for '{text}', got {plan['actions'][0]['type']}",
            )


if __name__ == "__main__":
    unittest.main()
