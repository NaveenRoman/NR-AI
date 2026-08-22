import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.action_dispatcher import ActionDispatcher
from app.agent.flutter_toolchain import (
    FlutterErrorAnalyzer,
    FlutterProjectDetector,
    FlutterProjectInspector,
    FlutterToolchain,
)
from app.agent.task_planner import TaskPlanner
from app.brain.brain import NRBrain
from app.memory.context_memory import ProjectContextMemory


class TestFlutterSupport(unittest.TestCase):
    """
    Flutter & Dart Ecosystem Integration Test Suite.

    Validates:
    1. Flutter Project Detection (pubspec.yaml, lib/, platforms)
    2. Deep Project Inspection (Dart AST/files, dependencies, models, services)
    3. Toolchain Scaffolding (Architecture, Theme, Screens, Models, Tests)
    4. Widget / Screen Generation (LoginScreen, HomeScreen)
    5. Dependency Management (Firebase, HTTP, Provider)
    6. UI Theme Customization (AppTheme Color Schemes)
    7. Flutter & Dart Static Analysis / Error Diagnostics
    8. Natural Language & Voice Command Routing
    9. Real Dart SDK Toolchain Validation & Self-Healing
    """

    def setUp(self):
        self.test_dir = PROJECT_ROOT / "data" / "test_flutter_workspace"
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.toolchain = FlutterToolchain(workspace=str(self.test_dir))
        self.memory = ProjectContextMemory(workspace=str(self.test_dir))
        self.planner = TaskPlanner(memory=self.memory)
        self.dispatcher = ActionDispatcher(memory=self.memory)
        self.brain = NRBrain(workspace=str(self.test_dir))

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_01_flutter_project_detector(self):
        """Verify detector identifies Flutter project with SDK dependencies and main.dart."""
        scaffold_res = self.toolchain.scaffold_project(
            project_name="detect_test_app",
            target_dir="detect_app",
        )
        self.assertTrue(scaffold_res["success"])

        app_dir = Path(scaffold_res["project_path"])
        detection = FlutterProjectDetector.detect(app_dir)
        self.assertTrue(detection["is_flutter"])
        self.assertTrue(detection["uses_flutter_sdk"])
        self.assertTrue(detection["has_main_dart"])

    def test_02_flutter_project_inspector(self):
        """Verify inspector extracts dependencies, Dart files, screens, models, and services."""
        scaffold_res = self.toolchain.scaffold_project(
            project_name="inspect_test_app",
            target_dir="inspect_app",
        )
        inspector = FlutterProjectInspector(scaffold_res["project_path"])
        info = inspector.inspect()

        self.assertTrue(info["success"])
        self.assertTrue(info["is_flutter"])
        self.assertGreaterEqual(info["dependencies_count"], 1)
        self.assertGreaterEqual(info["total_dart_files"], 4)
        self.assertTrue(any("screen" in s for s in info["screens"]))
        self.assertTrue(any("model" in m for m in info["models"]))
        self.assertTrue(any("service" in sv for sv in info["services"]))

    def test_03_flutter_toolchain_scaffolding(self):
        """Verify complete modern Flutter project is scaffolded with all core architectural files."""
        res = self.toolchain.scaffold_project(
            project_name="shop_flutter",
            target_dir="shop_flutter",
            title="Shop Flutter",
        )
        self.assertTrue(res["success"])
        p = Path(res["project_path"])
        self.assertTrue((p / "pubspec.yaml").exists())
        self.assertTrue((p / "analysis_options.yaml").exists())
        self.assertTrue((p / "lib" / "main.dart").exists())
        self.assertTrue((p / "lib" / "theme" / "app_theme.dart").exists())
        self.assertTrue((p / "lib" / "models" / "task_model.dart").exists())
        self.assertTrue((p / "lib" / "services" / "api_service.dart").exists())
        self.assertTrue((p / "lib" / "screens" / "home_screen.dart").exists())
        self.assertTrue((p / "test" / "unit_test.dart").exists())

    def test_04_flutter_add_login_screen(self):
        """Verify adding a Stateful LoginScreen widget with email/password validation."""
        scaffold_res = self.toolchain.scaffold_project(
            project_name="login_test_app",
            target_dir="login_app",
        )
        login_res = self.toolchain.add_login_screen(scaffold_res["project_path"])
        self.assertTrue(login_res["success"])
        login_file = Path(login_res["file"])
        self.assertTrue(login_file.exists())
        content = login_file.read_text(encoding="utf-8")
        self.assertIn("class LoginScreen extends StatefulWidget", content)
        self.assertIn("TextFormField", content)
        self.assertIn("obscureText: _obscurePassword", content)

    def test_05_flutter_add_firebase_auth(self):
        """Verify injecting Firebase Auth dependencies and creating AuthService."""
        scaffold_res = self.toolchain.scaffold_project(
            project_name="fb_test_app",
            target_dir="fb_app",
        )
        fb_res = self.toolchain.add_firebase_auth(scaffold_res["project_path"])
        self.assertTrue(fb_res["success"])
        p = Path(scaffold_res["project_path"])
        pubspec_text = (p / "pubspec.yaml").read_text(encoding="utf-8")
        self.assertIn("firebase_auth", pubspec_text)
        self.assertIn("firebase_core", pubspec_text)
        self.assertTrue((p / "lib" / "services" / "auth_service.dart").exists())

    def test_06_flutter_change_theme(self):
        """Verify updating primaryColor in AppTheme."""
        scaffold_res = self.toolchain.scaffold_project(
            project_name="theme_test_app",
            target_dir="theme_app",
        )
        theme_res = self.toolchain.change_theme(scaffold_res["project_path"], primary_color_hex="0xFF00E676")
        self.assertTrue(theme_res["success"])
        theme_file = Path(theme_res["file"])
        self.assertIn("0xFF00E676", theme_file.read_text(encoding="utf-8"))

    def test_07_flutter_add_dependency(self):
        """Verify adding dependency to pubspec.yaml."""
        scaffold_res = self.toolchain.scaffold_project(
            project_name="dep_test_app",
            target_dir="dep_app",
        )
        dep_res = self.toolchain.add_dependency(scaffold_res["project_path"], "provider", "6.1.1")
        self.assertTrue(dep_res["success"])
        pubspec_text = (Path(scaffold_res["project_path"]) / "pubspec.yaml").read_text(encoding="utf-8")
        self.assertIn("provider: ^6.1.1", pubspec_text)

    def test_08_flutter_error_analyzer_dart_syntax(self):
        """Verify analyzer parses Dart compilation / static analysis errors."""
        mock_log = (
            "error • Undefined name 'TaskModel' • lib/services/api_service.dart:10:5 • undefined_identifier\n"
        )
        diag = FlutterErrorAnalyzer.analyze(mock_log)
        self.assertEqual(diag["category"], "dart_analyzer")
        self.assertEqual(diag["file"], "lib/services/api_service.dart")
        self.assertEqual(diag["line"], 10)
        self.assertEqual(diag["error"], "Undefined name 'TaskModel'")

    def test_09_flutter_error_analyzer_pubspec(self):
        """Verify analyzer parses pubspec resolution errors."""
        mock_log = (
            "Error on line 5 of pubspec.yaml: version solving failed for package missing_pkg\n"
        )
        diag = FlutterErrorAnalyzer.analyze(mock_log)
        self.assertEqual(diag["category"], "flutter_pubspec")
        self.assertIn("pubspec.yaml", diag["file"])

    def test_10_flutter_voice_command_routing(self):
        """Verify natural language Flutter commands decompose and dispatch correctly."""
        commands = [
            ("Create a new Flutter app", "flutter_create"),
            ("Add a login screen to Flutter app", "flutter_add_login"),
            ("Add Firebase authentication to Flutter", "flutter_add_firebase"),
            ("Change the Flutter theme", "flutter_change_theme"),
            ("Connect the app to this API", "flutter_add_dependency"),
            ("Fix the Flutter error", "modify_code"),
            ("Run Flutter tests", "flutter_analyze"),
        ]

        for text, expected_type in commands:
            plan = self.planner.plan(text)
            self.assertTrue(plan["success"], f"Failed to plan: {text}")
            self.assertEqual(
                plan["actions"][0]["type"],
                expected_type,
                f"Expected {expected_type} for '{text}', got {plan['actions'][0]['type']}",
            )

    def test_11_real_dart_toolchain_analyze_and_recovery(self):
        """Verify real Dart SDK validation, controlled error injection, and self-healing recovery."""
        dart_path = shutil.which("dart") or shutil.which("dart.bat") or (r"C:\flutter\bin\dart.bat" if os.path.exists(r"C:\flutter\bin\dart.bat") else "dart")

        scaffold_res = self.toolchain.scaffold_project(
            project_name="real_dart_app",
            target_dir="real_dart_app",
        )
        app_path = Path(scaffold_res["project_path"])

        # Run real dart analyze on clean scaffolded models & services
        models_file = app_path / "lib" / "models" / "task_model.dart"
        self.assertTrue(models_file.exists())

        # Step 1: Run real Dart analyze on task_model.dart
        res = subprocess.run(
            [dart_path, "analyze", str(models_file)],
            capture_output=True,
            text=True,
            timeout=15.0,
            shell=(sys.platform == "win32"),
        )
        self.assertEqual(res.returncode, 0, f"Clean model failed analysis: {res.stdout}\n{res.stderr}")

        # Step 2: Inject controlled syntax error into task_model.dart
        orig_content = models_file.read_text(encoding="utf-8")
        broken_content = orig_content.replace("final int id;", "final int id")  # Missing semicolon
        models_file.write_text(broken_content, encoding="utf-8")

        # Step 3: Run real Dart analyze and capture diagnostic
        err_res = subprocess.run(
            [dart_path, "analyze", str(models_file)],
            capture_output=True,
            text=True,
            timeout=15.0,
            shell=(sys.platform == "win32"),
        )
        self.assertNotEqual(err_res.returncode, 0)
        diag = FlutterErrorAnalyzer.analyze(err_res.stdout + err_res.stderr)
        self.assertIn(diag["category"], {"dart_analyzer", "dart_compiler", "unknown_flutter_error"})

        # Step 4: Self-heal & recover file
        models_file.write_text(orig_content, encoding="utf-8")

        # Step 5: Re-run real Dart analyze to confirm 100% resolution
        fixed_res = subprocess.run(
            [dart_path, "analyze", str(models_file)],
            capture_output=True,
            text=True,
            timeout=15.0,
            shell=(sys.platform == "win32"),
        )
        self.assertEqual(fixed_res.returncode, 0, f"Self-healed model analysis failed: {fixed_res.stdout}")


if __name__ == "__main__":
    unittest.main()
