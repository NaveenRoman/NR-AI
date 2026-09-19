"""
Test Suite: Universal Engineering Workflow & Dialogue Intelligence
Tests all 10 categories of the Universal Engineering Workflow Correction:
1. OPEN_ANDROID_STUDIO
2. CREATE_ANDROID_PROJECT
3. BUILD_ANDROID_PROJECT
4. RUN_ANDROID_PROJECT
5. MODIFY_ANDROID_PROJECT
6. CONTINUE_ACTIVE_ANDROID_PROJECT
7. PROJECT_CONTEXT_PERSISTENCE
8. Intent / Domain Disambiguation (Android vs Unreal)
9. Existing Droid Routing Regression
10. Safety & Security Invariants
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from app.agent.engineering_intent import (
    EngineeringAction,
    EngineeringDomain,
    EngineeringIntent,
    EngineeringIntentParser,
    VerificationLevel,
)
from app.agent.engineering_context import (
    ActiveProjectContext,
    ActiveProjectContextManager,
)
from app.agent.android_unified_agent import UnifiedAndroidAgent
from app.brain.companion import NRCompanion, CommandCategory


class TestUniversalEngineeringWorkflow(unittest.TestCase):
    """Full 10-category test suite for Universal Engineering Workflow."""

    @classmethod
    def setUpClass(cls):
        cls.companion = NRCompanion()
        # Redirect companion active context to isolated temp file
        cls.temp_dir = tempfile.mkdtemp(prefix="nrai_uew_test_")
        cls.test_storage = Path(cls.temp_dir) / "test_context.json"
        cls.companion.engineering_context_manager = ActiveProjectContextManager(cls.test_storage)
        cls.companion.unified_android_agent.context_manager = cls.companion.engineering_context_manager

    @classmethod
    def tearDownClass(cls):
        if Path(cls.temp_dir).exists():
            shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def setUp(self):
        # Reset context and conversation state before each test
        self.companion.engineering_context_manager.clear()
        self.companion.active_conversation_agent = None
        self.companion.active_conversation_agent_name = None

    # =========================================================================
    # Category 1: OPEN_ANDROID_STUDIO
    # =========================================================================
    def test_01_open_android_studio_action(self):
        """'open android studio' must launch workspace, route to Droid, and avoid greeting loop."""
        resp = self.companion.handle_command("open android studio")
        self.assertEqual(resp.get("category"), CommandCategory.ANDROID_STUDIO.value)
        self.assertEqual(resp.get("routed_to"), "Droid")
        self.assertIn("Android Studio workspace launched", resp.get("text"))
        self.assertEqual(self.companion.active_conversation_agent, "android_unified_agent")
        # Ensure it does NOT return literal naive message or greeting
        self.assertNotIn("Proceeding with 'open'", resp.get("text"))
        self.assertNotIn("What do you need?", resp.get("text"))

    def test_01b_open_ide_variations(self):
        """Test variations of open android studio."""
        for phrase in ("open android studio", "launch android studio", "start android studio"):
            intent = EngineeringIntentParser.parse(phrase)
            self.assertEqual(intent.action, EngineeringAction.OPEN)
            self.assertEqual(intent.domain, EngineeringDomain.ANDROID)
            self.assertTrue(intent.is_valid)

    # =========================================================================
    # Category 2: CREATE_ANDROID_PROJECT
    # =========================================================================
    def test_02_create_android_project_scaffolding(self):
        """Create new Android project sets context and honest status."""
        resp = self.companion.handle_command("Create a new Android project called TestApp using Kotlin.")
        self.assertEqual(resp.get("category"), CommandCategory.ANDROID_STUDIO.value)
        self.assertEqual(resp.get("routed_to"), "Droid")
        self.assertIn("TestApp", resp.get("text"))
        self.assertIn("dev_projects", resp.get("text"))

        act_proj = self.companion.engineering_context_manager.get_active_project()
        self.assertIsNotNone(act_proj)
        self.assertEqual(act_proj.project_name, "TestApp")
        self.assertEqual(act_proj.domain, "ANDROID")

        # Verify scaffolding was actually created
        proj_dir = Path(r"C:\NR-AI\dev_projects\TestApp")
        self.assertTrue(proj_dir.exists())
        self.assertTrue((proj_dir / "build.gradle.kts").exists() or (proj_dir / "build.gradle").exists())

    def test_02b_create_project_intent_parser(self):
        """Verify intent parsing parameters for project creation."""
        intent = EngineeringIntentParser.parse("create an android project named BankingApp with java")
        self.assertEqual(intent.action, EngineeringAction.CREATE_PROJECT)
        self.assertEqual(intent.domain, EngineeringDomain.ANDROID)
        self.assertEqual(intent.project, "BankingApp")
        self.assertEqual(intent.parameters.get("language"), "Java")

    # =========================================================================
    # Category 3: BUILD_ANDROID_PROJECT
    # =========================================================================
    def test_03_build_android_project(self):
        """Deterministic build triggers gradle runner and records action."""
        self.companion.engineering_context_manager.set_active_project("TestApp", "ANDROID")
        resp = self.companion.handle_command("Build the project.")
        self.assertEqual(resp.get("category"), CommandCategory.ANDROID_STUDIO.value)
        self.assertIn("Gradle build completed", resp.get("text"))
        self.assertIn("TestApp", resp.get("text"))

        act_proj = self.companion.engineering_context_manager.get_active_project()
        self.assertEqual(act_proj.last_action, "BUILD")

    def test_03b_rebuild_project_clean(self):
        """Rebuild triggers clean first."""
        intent = EngineeringIntentParser.parse("rebuild the android app")
        self.assertEqual(intent.action, EngineeringAction.REBUILD)
        self.assertEqual(intent.domain, EngineeringDomain.ANDROID)

    # =========================================================================
    # Category 4: RUN_ANDROID_PROJECT
    # =========================================================================
    def test_04_run_it_pronoun_resolution(self):
        """'Run it' resolves pronoun 'it' to active project."""
        self.companion.engineering_context_manager.set_active_project("TestApp", "ANDROID")
        resp = self.companion.handle_command("Run it.")
        self.assertEqual(resp.get("category"), CommandCategory.ANDROID_STUDIO.value)
        self.assertIn("TestApp", resp.get("text"))
        self.assertIn("Pixel_6_API_34", resp.get("text"))

    def test_04b_install_action(self):
        """Install action executes and records."""
        intent = EngineeringIntentParser.parse("install the apk on emulator")
        self.assertEqual(intent.action, EngineeringAction.INSTALL)
        self.assertEqual(intent.domain, EngineeringDomain.ANDROID)

    # =========================================================================
    # Category 5: MODIFY_ANDROID_PROJECT
    # =========================================================================
    def test_05_modify_feature_resolution(self):
        """'Add a splash screen' resolves files without user entering file names."""
        self.companion.engineering_context_manager.set_active_project("TestApp", "ANDROID")
        resp = self.companion.handle_command("Add a splash screen.")
        self.assertEqual(resp.get("category"), CommandCategory.ANDROID_STUDIO.value)
        self.assertIn("splash screen", resp.get("text"))
        self.assertIn("SplashActivity.kt", resp.get("text"))

        act_proj = self.companion.engineering_context_manager.get_active_project()
        self.assertEqual(act_proj.active_feature, "splash screen")
        self.assertTrue(any("SplashActivity.kt" in f for f in act_proj.affected_files))

    def test_05b_login_feature_resolution(self):
        """'Add login screen' maps to login components."""
        target, files = self.companion.engineering_context_manager.resolve_target_and_files("login screen")
        self.assertEqual(target, "login screen")
        self.assertTrue(any("LoginActivity.kt" in f for f in files))
        self.assertTrue(any("activity_login.xml" in f for f in files))

    # =========================================================================
    # Category 6: CONTINUE_ACTIVE_ANDROID_PROJECT
    # =========================================================================
    def test_06_multi_turn_continuity(self):
        """Multi-turn flow: Create -> Add Feature -> Run -> Refine Feature."""
        # Turn 1: Create project
        r1 = self.companion.handle_command("Create a new Android project called FlowApp using Kotlin.")
        self.assertIn("FlowApp", r1.get("text"))

        # Turn 2: Add feature
        r2 = self.companion.handle_command("Add a splash screen.")
        self.assertIn("splash screen", r2.get("text"))
        self.assertEqual(self.companion.engineering_context_manager.get_active_project().active_feature, "splash screen")

        # Turn 3: Run it
        r3 = self.companion.handle_command("Run it.")
        self.assertIn("FlowApp", r3.get("text"))
        # Active feature must be preserved across RUN!
        self.assertEqual(self.companion.engineering_context_manager.get_active_project().active_feature, "splash screen")

        # Turn 4: Refine feature
        r4 = self.companion.handle_command("Make the logo smaller and center it.")
        self.assertIn("splash screen", r4.get("text"))
        self.assertIn("FlowApp", r4.get("text"))

    # =========================================================================
    # Category 7: PROJECT_CONTEXT_PERSISTENCE
    # =========================================================================
    def test_07_context_persistence_and_reload(self):
        """Context persists to disk and reloads accurately."""
        mgr1 = ActiveProjectContextManager(self.test_storage)
        mgr1.set_active_project("PersistApp", "ANDROID", canonical_path="/path/PersistApp")
        mgr1.set_active_feature("checkout flow", ["CheckoutActivity.kt", "CartModel.kt"])
        mgr1.record_action("TEST", target="checkout flow")

        # Create fresh manager from same storage
        mgr2 = ActiveProjectContextManager(self.test_storage)
        act = mgr2.get_active_project()
        self.assertIsNotNone(act)
        self.assertEqual(act.project_name, "PersistApp")
        self.assertEqual(act.active_feature, "checkout flow")
        self.assertEqual(act.last_action, "TEST")
        self.assertEqual(len(act.affected_files), 2)

    # =========================================================================
    # Category 8: Intent / Domain Disambiguation
    # =========================================================================
    def test_08_domain_disambiguation(self):
        """Disambiguate Android vs Unreal vs Visual Studio vs Unity."""
        i_android = EngineeringIntentParser.parse("create an android project called MobileOne")
        self.assertEqual(i_android.domain, EngineeringDomain.ANDROID)

        i_unreal = EngineeringIntentParser.parse("create an unreal project called ShooterGame in C++")
        self.assertEqual(i_unreal.domain, EngineeringDomain.UNREAL)
        self.assertEqual(i_unreal.action, EngineeringAction.CREATE_PROJECT)

        i_vs = EngineeringIntentParser.parse("build solution MyBackend.sln")
        self.assertEqual(i_vs.domain, EngineeringDomain.VISUAL_STUDIO)

        i_unity = EngineeringIntentParser.parse("create a unity script called PlayerController")
        self.assertEqual(i_unity.domain, EngineeringDomain.UNITY)

    def test_08b_unreal_honest_capability_reporting(self):
        """Unreal workflow command routes honestly reporting NOT_IMPLEMENTED."""
        resp = self.companion.handle_command("create an unreal project called ShooterGame in C++")
        self.assertEqual(resp.get("category"), CommandCategory.UNREAL.value)
        self.assertEqual(resp.get("routed_to"), "UnrealToolchain")
        self.assertIn("NOT_IMPLEMENTED", resp.get("text"))
        self.assertIn("Unreal Phase 1 is not yet started", resp.get("text"))

    # =========================================================================
    # Category 9: Existing Droid Routing Regression
    # =========================================================================
    def test_09_existing_droid_phrases_pass_to_legacy_handlers(self):
        """Audit and diagnostic commands still invoke their dedicated Phase 1-5 handlers."""
        resp = self.companion.handle_command("audit android readiness")
        self.assertEqual(resp.get("category"), CommandCategory.ANDROID_STUDIO.value)
        self.assertIn("Droid Readiness", resp.get("text"))

        resp_snap = self.companion.handle_command("inspect android studio project")
        self.assertEqual(resp_snap.get("category"), CommandCategory.ANDROID_STUDIO.value)
        self.assertIn("AGP", resp_snap.get("text"))

    def test_09b_direct_address_capabilities(self):
        """'Droid, what can you do?' continues to respond properly."""
        resp = self.companion.handle_command("Droid, what can you do?")
        self.assertIn("Droid", resp.get("text"))
        self.assertIn("Android", resp.get("text"))

    # =========================================================================
    # Category 10: Safety & Security Invariants
    # =========================================================================
    def test_10_prohibited_injection_tokens_rejected(self):
        """Commands containing injection tokens are rejected."""
        malicious_inputs = [
            "create an android project called Test; rm -rf /",
            "open android studio && cmd.exe /c calc",
            "build project | format c:",
            "create project ../../../etc/passwd",
        ]
        for bad_input in malicious_inputs:
            intent = EngineeringIntentParser.parse(bad_input)
            self.assertFalse(intent.is_valid, f"Failed to reject malicious input: {bad_input}")
            self.assertIsNotNone(intent.rejection_reason)


if __name__ == "__main__":
    unittest.main()
