import json
import os
import shutil
import unittest
from pathlib import Path

from app.agent.action_dispatcher import ActionDispatcher
from app.agent.universal_project_engine import Ecosystem, UniversalProjectDetector, UniversalProjectEngine
from app.agent.unreal_toolchain import (
    UnrealEnvironmentDetector,
    UnrealErrorAnalyzer,
    UnrealProjectDetector,
    UnrealProjectInspector,
    UnrealToolchain,
)


class TestUnrealSupport(unittest.TestCase):

    def setUp(self):
        self.test_dir = Path("data/test_unreal_workspace").resolve()
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.toolchain = UnrealToolchain(workspace=str(self.test_dir))

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_01_environment_detection_transparency(self):
        """Verifies environment detection returns valid status without faking availability."""
        env = UnrealEnvironmentDetector.detect_environment()
        self.assertIn("is_available", env)
        self.assertIn("status", env)
        self.assertIn("winsdk_available", env)
        self.assertIn(env["status"], ["AVAILABLE", "UNAVAILABLE"])
        # Verify status summary is transparent
        if not env["is_available"]:
            self.assertEqual(env["summary"], "UNREAL ENGINE: UNAVAILABLE")

    def test_02_project_scaffolding_and_detection(self):
        """Verifies scaffolding of complete Unreal C++ project structure."""
        scaffold = self.toolchain.scaffold_project(
            project_name="ShadowQuest",
            target_dir="shadow_quest",
            engine_version="5.3",
        )
        self.assertTrue(scaffold["success"])
        project_path = Path(scaffold["project_path"])

        # Check uproject
        uproject_file = project_path / "ShadowQuest.uproject"
        self.assertTrue(uproject_file.exists())
        data = json.loads(uproject_file.read_text(encoding="utf-8"))
        self.assertEqual(data["EngineAssociation"], "5.3")
        self.assertEqual(data["Modules"][0]["Name"], "ShadowQuest")

        # Check Target & Build files
        self.assertTrue((project_path / "Source" / "ShadowQuestTarget.cs").exists())
        self.assertTrue((project_path / "Source" / "ShadowQuestEditorTarget.cs").exists())
        self.assertTrue((project_path / "Source" / "ShadowQuest" / "ShadowQuest.Build.cs").exists())

        # Check C++ gameplay classes
        self.assertTrue((project_path / "Source" / "ShadowQuest" / "GameMode" / "ShadowQuestGameMode.h").exists())
        self.assertTrue((project_path / "Source" / "ShadowQuest" / "Character" / "ShadowQuestCharacter.h").exists())
        self.assertTrue((project_path / "Source" / "ShadowQuest" / "Components" / "HealthComponent.h").exists())

        # Check detection
        detection = UnrealProjectDetector.detect(project_path)
        self.assertTrue(detection["is_unreal"])
        self.assertTrue(detection["is_cpp"])
        self.assertEqual(detection["project_name"], "ShadowQuest")
        self.assertEqual(detection["engine_association"], "5.3")

    def test_03_project_inspection_and_macros(self):
        """Verifies inspector finds classes and reflection macros."""
        scaffold = self.toolchain.scaffold_project(
            project_name="ArenaCombat",
            target_dir="arena_combat",
        )
        inspector = UnrealProjectInspector(scaffold["project_path"])
        info = inspector.inspect()

        self.assertEqual(info["project_name"], "ArenaCombat")
        self.assertTrue(info["is_cpp"])
        class_names = [c["name"] for c in info["classes"]]
        self.assertIn("AArenaCombatGameMode", class_names)
        self.assertIn("AArenaCombatCharacter", class_names)
        self.assertIn("UHealthComponent", class_names)

        # Verify macros found
        self.assertIn("UCLASS", info["macros_used"])
        self.assertIn("GENERATED_BODY", info["macros_used"])
        self.assertIn("UPROPERTY", info["macros_used"])
        self.assertIn("UFUNCTION", info["macros_used"])

    def test_04_multi_turn_health_system_modification(self):
        """Verifies multi-turn modification of HealthComponent."""
        scaffold = self.toolchain.scaffold_project(
            project_name="CyberRacer",
            target_dir="cyber_racer",
        )
        project_path = Path(scaffold["project_path"])

        # Modify health system
        mod = self.toolchain.modify_health_system(project_path, max_health=250.0)
        self.assertTrue(mod["success"])
        self.assertEqual(mod["new_max_health"], 250.0)

        health_cpp = (project_path / "Source" / "CyberRacer" / "Components" / "HealthComponent.cpp").read_text(encoding="utf-8")
        self.assertIn("MaxHealth = 250.0f;", health_cpp)

    def test_05_error_analyzer_uht_missing_generated_body(self):
        """Verifies UnrealErrorAnalyzer diagnoses missing GENERATED_BODY()."""
        err_log = "Character.h:12: error: Class declaration 'ASoldier' missing GENERATED_BODY() macro"
        diag = UnrealErrorAnalyzer.analyze(err_log, filepath="Character.h")
        self.assertEqual(diag["error_type"], "UnrealHeaderToolError")
        self.assertEqual(diag["error_category"], "reflection")
        self.assertIn("GENERATED_BODY", diag["suggested_fix"])

    def test_06_error_analyzer_include_order(self):
        """Verifies UnrealErrorAnalyzer diagnoses .generated.h include order error."""
        err_log = "Soldier.h:5: fatal error: #include 'Soldier.generated.h' must be the last include in a Unreal header"
        diag = UnrealErrorAnalyzer.analyze(err_log, filepath="Soldier.h")
        self.assertEqual(diag["error_type"], "UnrealIncludeOrderError")
        self.assertEqual(diag["error_category"], "header_include")
        self.assertIn("very last #include", diag["suggested_fix"])

    def test_07_error_analyzer_c2065_undeclared_identifier(self):
        """Verifies UnrealErrorAnalyzer diagnoses C2065 undeclared identifier."""
        err_log = "HealthComponent.cpp(22,15): error C2065: 'MaxArmor': undeclared identifier"
        diag = UnrealErrorAnalyzer.analyze(err_log, filepath="HealthComponent.cpp")
        self.assertEqual(diag["error_type"], "UnrealCppCompilerError")
        self.assertEqual(diag["line"], 22)
        self.assertIn("MaxArmor", diag["message"])

    def test_08_error_analyzer_lnk2019_unresolved_external(self):
        """Verifies UnrealErrorAnalyzer diagnoses linker unresolved external symbol."""
        err_log = "error LNK2019: unresolved external symbol \"__declspec(dllimport) public: void __cdecl UUserWidget::AddToViewport(int)\""
        diag = UnrealErrorAnalyzer.analyze(err_log)
        self.assertEqual(diag["error_type"], "UnrealLinkerError")
        self.assertEqual(diag["error_category"], "linker")
        self.assertIn("PublicDependencyModuleNames", diag["suggested_fix"])

    def test_09_universal_routing_unreal(self):
        """Verifies prompt classification for Unreal Engine requests."""
        p1 = UniversalProjectDetector.identify_from_prompt("Create an Unreal Engine third-person game")
        self.assertEqual(p1["primary_ecosystem"], Ecosystem.UNREAL.value)

        p2 = UniversalProjectDetector.identify_from_prompt("Create an Unreal FPS prototype in C++")
        self.assertEqual(p2["primary_ecosystem"], Ecosystem.UNREAL.value)

        p3 = UniversalProjectDetector.identify_from_prompt("Build an Unreal multiplayer game with health system")
        self.assertEqual(p3["primary_ecosystem"], Ecosystem.UNREAL.value)

    def test_10_action_dispatcher_unreal_integration(self):
        """Verifies ActionDispatcher dispatches unreal_create action."""
        dispatcher = ActionDispatcher()
        res = dispatcher.execute({
            "type": "unreal_create",
            "name": "BattleRoyale",
            "target_dir": str(self.test_dir / "battle_royale"),
        })
        self.assertTrue(res["success"])
        self.assertEqual(res["project_name"], "BattleRoyale")
        self.assertTrue((self.test_dir / "battle_royale" / "BattleRoyale.uproject").exists())


if __name__ == "__main__":
    unittest.main()
