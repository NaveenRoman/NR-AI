import json
import os
import shutil
import sys
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.action_dispatcher import ActionDispatcher
from app.agent.docker_toolchain import DockerProjectDetector, DockerToolchain
from app.agent.node_react_toolchain import (
    NodeReactErrorAnalyzer,
    NodeReactProjectDetector,
    NodeReactProjectInspector,
    NodeReactToolchain,
)
from app.agent.spring_boot_toolchain import (
    SpringBootErrorAnalyzer,
    SpringBootProjectDetector,
    SpringBootProjectInspector,
    SpringBootToolchain,
)
from app.agent.task_planner import TaskPlanner
from app.agent.unity_toolchain import (
    UnityErrorAnalyzer,
    UnityProjectDetector,
    UnityProjectInspector,
    UnityToolchain,
)
from app.agent.universal_project_engine import (
    Ecosystem,
    UniversalProjectDetector,
    UniversalProjectEngine,
)
from app.brain.brain import NRBrain
from app.memory.context_memory import ProjectContextMemory


class TestUniversalProjectEngine(unittest.TestCase):
    """
    Universal Project Engine & Multi-Ecosystem Router Test Suite.

    Validates:
    1. Automatic technology & ecosystem identification from natural language prompts.
    2. Dependency-aware multi-tier blueprint generation.
    3. Unity C# Game Toolchain (Scaffolding, Inspection, C# Error Analysis).
    4. Spring Boot Java Microservice Toolchain (Scaffolding, Inspection, Java Error Analysis).
    5. Node / React / Next.js Toolchain (Scaffolding, Inspection, JS Error Analysis).
    6. Docker & Container Orchestration Toolchain (Dockerfile, Docker Compose).
    7. Multi-Tier Full-Stack SaaS Composite Project Creation.
    8. Universal Natural Language & Voice Command Routing.
    """

    def setUp(self):
        self.test_dir = PROJECT_ROOT / "data" / "test_universal_workspace"
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.memory = ProjectContextMemory(workspace=str(self.test_dir))
        self.planner = TaskPlanner(memory=self.memory)
        self.dispatcher = ActionDispatcher(memory=self.memory)
        self.engine = UniversalProjectEngine(workspace=str(self.test_dir))

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_01_universal_detector_prompt_classification(self):
        """Verify prompt classification routes to the correct primary and composite ecosystems."""
        cases = [
            ("Build an Android shopping app", Ecosystem.ANDROID.value),
            ("Build a Flutter food delivery app", Ecosystem.FLUTTER.value),
            ("Build a Unity multiplayer game", Ecosystem.UNITY.value),
            ("Build a Spring Boot banking backend", Ecosystem.SPRING_BOOT.value),
            ("Build a React dashboard with Python API", Ecosystem.REACT.value),
            ("Build a full-stack SaaS application", Ecosystem.FULLSTACK_SAAS.value),
        ]

        for prompt, expected_eco in cases:
            res = UniversalProjectDetector.identify_from_prompt(prompt)
            self.assertEqual(
                res["primary_ecosystem"],
                expected_eco,
                f"Failed for prompt '{prompt}': got {res['primary_ecosystem']}",
            )

    def test_02_universal_detector_directory_inspection(self):
        """Verify detector identifies ecosystem from on-disk directory contents."""
        unity_dir = self.test_dir / "unity_proj"
        self.engine.unity.scaffold_project("UnityGame", target_dir="unity_proj")
        res = UniversalProjectDetector.identify_from_directory(unity_dir)
        self.assertEqual(res["primary_ecosystem"], Ecosystem.UNITY.value)

    def test_03_dependency_aware_blueprint_generation(self):
        """Verify blueprint generator produces dependency-ordered multi-tier execution steps."""
        blueprint = self.engine.generate_blueprint("Build a full-stack SaaS application", "data/saas_proj")
        self.assertTrue(blueprint["success"])
        self.assertEqual(blueprint["primary_ecosystem"], Ecosystem.FULLSTACK_SAAS.value)
        self.assertGreaterEqual(len(blueprint["tiers"]), 3)
        self.assertEqual(blueprint["execution_order"][0], "database")
        self.assertIn("frontend", blueprint["execution_order"])
        self.assertIn("container", blueprint["execution_order"])

    def test_04_unity_scaffolding_and_inspection(self):
        """Verify Unity project scaffolding and deep asset/script inspection."""
        scaffold_res = self.engine.unity.scaffold_project("ActionGame", target_dir="unity_app")
        self.assertTrue(scaffold_res["success"])

        app_dir = Path(scaffold_res["project_path"])
        self.assertTrue((app_dir / "ProjectSettings" / "ProjectVersion.txt").exists())
        self.assertTrue((app_dir / "Packages" / "manifest.json").exists())
        self.assertTrue((app_dir / "Assets" / "Scripts" / "GameManager.cs").exists())
        self.assertTrue((app_dir / "Assets" / "Scripts" / "PlayerController.cs").exists())

        inspector = UnityProjectInspector(app_dir)
        info = inspector.inspect()
        self.assertTrue(info["success"])
        self.assertTrue(info["is_unity"])
        self.assertGreaterEqual(info["total_scripts"], 3)
        self.assertGreaterEqual(info["packages_count"], 1)

    def test_05_unity_error_analyzer(self):
        """Verify Unity error analyzer parses C# compiler errors."""
        mock_log = (
            "Assets/Scripts/PlayerController.cs(24,15): error CS0103: The name 'moveSpeed' does not exist in the current context\n"
        )
        diag = UnityErrorAnalyzer.analyze(mock_log)
        self.assertEqual(diag["category"], "csharp_compiler")
        self.assertEqual(diag["file"], "Assets/Scripts/PlayerController.cs")
        self.assertEqual(diag["line"], 24)
        self.assertEqual(diag["error_code"], "CS0103")

    def test_06_spring_boot_scaffolding_and_inspection(self):
        """Verify Spring Boot microservice scaffolding and component inspection."""
        scaffold_res = self.engine.spring_boot.scaffold_project("BankingApp", target_dir="spring_app", domain="banking")
        self.assertTrue(scaffold_res["success"])

        app_dir = Path(scaffold_res["project_path"])
        self.assertTrue((app_dir / "pom.xml").exists())
        self.assertTrue((app_dir / "src" / "main" / "resources" / "application.properties").exists())
        self.assertTrue((app_dir / "src" / "main" / "java" / "com" / "example" / "nrai" / "Application.java").exists())
        self.assertTrue((app_dir / "src" / "main" / "java" / "com" / "example" / "nrai" / "controller" / "AccountController.java").exists())

        inspector = SpringBootProjectInspector(app_dir)
        info = inspector.inspect()
        self.assertTrue(info["success"])
        self.assertTrue(info["is_spring_boot"])
        self.assertGreaterEqual(len(info["controllers"]), 1)
        self.assertGreaterEqual(len(info["dependencies"]), 2)

    def test_07_spring_boot_error_analyzer(self):
        """Verify Spring Boot error analyzer parses Java compiler errors."""
        mock_log = (
            "src/main/java/com/example/nrai/controller/AccountController.java:18: error: cannot find symbol\n"
        )
        diag = SpringBootErrorAnalyzer.analyze(mock_log)
        self.assertEqual(diag["category"], "java_compiler")
        self.assertEqual(diag["file"], "src/main/java/com/example/nrai/controller/AccountController.java")
        self.assertEqual(diag["line"], 18)

    def test_08_node_react_scaffolding_and_inspection(self):
        """Verify React Vite project scaffolding and component inspection."""
        scaffold_res = self.engine.node_react.scaffold_react_app("react_app", target_dir="react_app")
        self.assertTrue(scaffold_res["success"])

        app_dir = Path(scaffold_res["project_path"])
        self.assertTrue((app_dir / "package.json").exists())
        self.assertTrue((app_dir / "vite.config.js").exists())
        self.assertTrue((app_dir / "src" / "App.jsx").exists())
        self.assertTrue((app_dir / "src" / "api.js").exists())

        inspector = NodeReactProjectInspector(app_dir)
        info = inspector.inspect()
        self.assertTrue(info["success"])
        self.assertTrue(info["is_react"])
        self.assertGreaterEqual(len(info["components"]), 1)

    def test_09_node_react_error_analyzer(self):
        """Verify Node/React error analyzer parses JavaScript/JSX syntax and missing module errors."""
        mock_log = "SyntaxError: Unexpected token '<' (src/App.jsx:15:8)\n"
        diag = NodeReactErrorAnalyzer.analyze(mock_log)
        self.assertEqual(diag["category"], "js_syntax_error")
        self.assertEqual(diag["file"], "src/App.jsx")
        self.assertEqual(diag["line"], 15)

    def test_10_docker_scaffolding(self):
        """Verify Docker toolchain generates multi-container compose and Dockerfile."""
        dock_dir = self.test_dir / "docker_app"
        dock_res = self.engine.docker.scaffold_saas_docker(dock_dir, app_name="saas_test", include_db=True)
        self.assertTrue(dock_res["success"])
        self.assertTrue((dock_dir / "Dockerfile").exists())
        self.assertTrue((dock_dir / "docker-compose.yml").exists())

        detection = DockerProjectDetector.detect(dock_dir)
        self.assertTrue(detection["is_docker"])
        self.assertIn("backend", detection["services"])
        self.assertIn("postgres", detection["services"])

    def test_11_universal_project_engine_scaffold_saas(self):
        """Verify universal engine executes full multi-tier SaaS platform creation."""
        res = self.engine.scaffold_universal_project(
            "Build a full-stack SaaS application",
            target_dir="fullstack_saas_platform",
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["blueprint"]["primary_ecosystem"], Ecosystem.FULLSTACK_SAAS.value)
        self.assertIn("frontend", res["scaffold_results"])
        self.assertIn("docker", res["scaffold_results"])

    def test_12_natural_language_intent_routing(self):
        """Verify TaskPlanner and ActionDispatcher route universal ecosystem commands."""
        commands = [
            ("Build a Spring Boot banking backend", "spring_create"),
            ("Build a Unity multiplayer game", "unity_create"),
            ("Build a React dashboard with Python API", "react_create"),
            ("Build a full-stack SaaS application", "universal_create_project"),
        ]

        for text, expected_type in commands:
            plan = self.planner.plan(text)
            self.assertTrue(plan["success"], f"Failed to plan: {text}")
            self.assertEqual(
                plan["actions"][0]["type"],
                expected_type,
                f"Expected {expected_type} for '{text}', got {plan['actions'][0]['type']}",
            )

            # Test execution through ActionDispatcher
            exec_res = self.dispatcher.execute(plan["actions"][0])
            self.assertTrue(exec_res.get("success", False), f"Action execution failed for: {expected_type}")


if __name__ == "__main__":
    unittest.main()
