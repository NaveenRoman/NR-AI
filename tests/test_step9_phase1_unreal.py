"""
NR-AI Step 9 Phase 1 Acceptance Tests: Unreal Engine Environment & Project Inspection Foundation.

Verifies:
- Unreal environment detection (host, custom roots, missing fallback, incomplete installations)
- Multiple-version handling and status categorization (DETECTED, INCOMPLETE, etc.)
- Project path validation, workspace confinement (C:/NR-AI), traversal rejection, protected file rules
- Valid and malformed .uproject parsing
- Valid and malformed .uplugin parsing
- Project structure inspection (Source, Content, Config, Plugins)
- Module declarations, Build.cs, and Target.cs inspection
- C++ class declarations and UCLASS/UPROPERTY reflection macro extraction
- Config file (.ini) inspection
- Content asset (.uasset, .umap) inventory and SHA-256 computation
- Engine association validation (matched with host vs custom)
- Emergency stop freeze halts all tool operations
- Rate limiting enforcement
- Sensitive data redaction
- Tool registry dispatch for all 12 tools with structured results and audit logging
- Subprocess security: 0 shell=True across all Unreal modules
- Model isolation: zero direct execution or filesystem authority
- Duplicate tool name detection (0 duplicates)
"""

import json
import os
from pathlib import Path
import re
import tempfile
import unittest

from app.agent.unreal_safety import (
    UnrealSafetyGate,
    UnrealErrorCode,
    UnrealSafetyError,
    EmergencyStopActiveError,
    ALLOWED_UNREAL_TOOLS,
    ALL_ALLOWED_UNREAL_TOOLS,
    PROTECTED_UNREAL_FILES,
    PROTECTED_UNREAL_EXTENSIONS,
    PROTECTED_UNREAL_DIRECTORIES,
    redact_sensitive_data,
    MAX_READ_LINES,
)
from app.agent.unreal_environment import (
    UnrealEnvironmentDetector,
    UnrealEngineInstance,
    UnrealEngineStatus,
    UnrealEnvironmentInfo,
)
from app.agent.unreal_project import (
    UnrealProjectInspector,
    UnrealProjectMetadata,
    UnrealModuleInfo,
    UnrealPluginInfo,
    UnrealClassInfo,
    UnrealAssetInfo,
)
from app.agent.unreal_tools import (
    UnrealToolRegistry,
    UnrealToolResult,
)
from app.memory.audit_logger import AuditLogger


WORKSPACE_ROOT = Path(r"C:/NR-AI").resolve()
FIXTURE_PROJECT = (WORKSPACE_ROOT / "nr_unreal_test").resolve()


class TestStep9Phase1Unreal(unittest.TestCase):
    """Acceptance test suite for Step 9 Phase 1: Unreal Engine Environment & Project Inspection Foundation."""

    def setUp(self):
        self.safety = UnrealSafetyGate(
            workspace_root=WORKSPACE_ROOT,
            authorized_project=FIXTURE_PROJECT,
            authorized_projects=[FIXTURE_PROJECT],
            rate_limit_calls_per_minute=200,
        )
        self.env = UnrealEnvironmentDetector()
        self.inspector = UnrealProjectInspector(safety_gate=self.safety, env_detector=self.env)
        self.temp_audit_dir = tempfile.mkdtemp()
        self.audit = AuditLogger(log_dir=self.temp_audit_dir)
        self.registry = UnrealToolRegistry(
            safety_gate=self.safety,
            env_detector=self.env,
            inspector=self.inspector,
            audit_logger=self.audit,
            workspace_root=WORKSPACE_ROOT,
        )

    # -------------------------------------------------------------------------
    # 1. Environment Detection Tests
    # -------------------------------------------------------------------------

    def test_01_environment_detection_host(self):
        """Test 1: Detecting Unreal Engine environment on the current system without crashing."""
        info = self.env.detect_environment()
        self.assertIsInstance(info, UnrealEnvironmentInfo)
        self.assertIsInstance(info.is_available, bool)
        self.assertIsInstance(info.engines, list)
        self.assertIn("NOT VERIFIED", info.execution_verification_status)

    def test_02_environment_detection_missing_fallback(self):
        """Test 2: Environment detector when no Unreal installation is present in search paths."""
        custom_detector = UnrealEnvironmentDetector(
            custom_roots=[Path(r"C:/NonExistent/EpicGames")],
            custom_manifest_dir=Path(r"C:/NonExistent/Manifests"),
        )
        info = custom_detector.detect_environment()
        self.assertFalse(info.is_available)
        self.assertEqual(len(info.engines), 0)
        self.assertIsNone(info.preferred_engine)
        self.assertEqual(info.status, "NOT_INSTALLED")

    def test_03_multiple_version_handling(self):
        """Test 3: Handling multiple installed Unreal Engine versions and sorting descending."""
        temp_dir = Path(tempfile.mkdtemp())
        try:
            ue53 = temp_dir / "UE_5.3"
            ue58 = temp_dir / "UE_5.8"
            for u in [ue53, ue58]:
                (u / "Engine" / "Binaries" / "Win64").mkdir(parents=True)
                (u / "Engine" / "Build").mkdir(parents=True)
                (u / "Engine" / "Binaries" / "Win64" / "UnrealEditor.exe").write_text("mock", encoding="utf-8")
                ver_major = 5
                ver_minor = 8 if "5.8" in u.name else 3
                v_data = {"MajorVersion": ver_major, "MinorVersion": ver_minor, "PatchVersion": 0}
                (u / "Engine" / "Build" / "Build.version").write_text(json.dumps(v_data), encoding="utf-8")

            detector = UnrealEnvironmentDetector(custom_roots=[temp_dir], custom_manifest_dir=Path(tempfile.mkdtemp()))
            engines = detector.detect_engines()
            self.assertEqual(len(engines), 2)
            # Preferred/first should be 5.8 (highest version)
            self.assertEqual(engines[0].version_minor, 8)
            self.assertEqual(engines[1].version_minor, 3)
        finally:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_04_incomplete_installation_detection(self):
        """Test 4: Classifying an engine directory missing binaries as INCOMPLETE."""
        temp_dir = Path(tempfile.mkdtemp())
        try:
            ue_bad = temp_dir / "UE_5.4"
            (ue_bad / "Engine").mkdir(parents=True)
            detector = UnrealEnvironmentDetector(custom_roots=[temp_dir], custom_manifest_dir=Path(tempfile.mkdtemp()))
            engines = detector.detect_engines()
            self.assertEqual(len(engines), 1)
            self.assertEqual(engines[0].status, UnrealEngineStatus.INCOMPLETE)
            self.assertFalse(engines[0].is_executable)
        finally:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_05_executable_detection(self):
        """Test 5: Detecting presence of UnrealEditor executable flag."""
        engines = self.env.detect_engines()
        if engines:
            eng = engines[0]
            if eng.editor_executable and os.path.exists(eng.editor_executable):
                self.assertTrue(eng.is_executable)
                self.assertTrue(Path(eng.editor_executable).is_file())

    # -------------------------------------------------------------------------
    # 2. Project Path Validation & Safety Boundaries
    # -------------------------------------------------------------------------

    def test_06_project_path_validation_valid(self):
        """Test 6: Validating authorized project path succeeds."""
        validated = self.safety.validate_project_path(FIXTURE_PROJECT)
        self.assertEqual(validated, FIXTURE_PROJECT)

    def test_07_workspace_boundary_rejection_outside(self):
        """Test 7: Rejecting project paths outside authorized workspace."""
        outside = Path(r"C:/External/UnrealProject").resolve()
        with self.assertRaises(UnrealSafetyError) as ctx:
            self.safety.validate_project_path(outside)
        self.assertEqual(ctx.exception.code, UnrealErrorCode.PROJECT_NOT_AUTHORIZED)

    def test_08_path_traversal_rejection(self):
        """Test 8: Rejecting path traversal sequences in project and file paths."""
        traversal = WORKSPACE_ROOT / "nr_unreal_test" / ".." / ".." / "Windows"
        with self.assertRaises(UnrealSafetyError) as ctx:
            self.safety.validate_project_path(str(traversal))
        self.assertEqual(ctx.exception.code, UnrealErrorCode.PATH_TRAVERSAL_DETECTED)

        traversal_file = FIXTURE_PROJECT / "Source" / ".." / ".." / "secret.env"
        with self.assertRaises(UnrealSafetyError) as ctx:
            self.safety.validate_file_path(str(traversal_file), project_root=FIXTURE_PROJECT)
        self.assertEqual(ctx.exception.code, UnrealErrorCode.PATH_TRAVERSAL_DETECTED)

    def test_09_unc_path_rejection(self):
        """Test 9: Rejecting UNC paths as prohibited network locations."""
        unc_path = r"\\NetworkShare\UnrealProject"
        with self.assertRaises(UnrealSafetyError) as ctx:
            self.safety.validate_project_path(unc_path)
        self.assertEqual(ctx.exception.code, UnrealErrorCode.PATH_TRAVERSAL_DETECTED)

    def test_10_protected_file_rejection(self):
        """Test 10: Rejecting access to protected files and extensions."""
        protected_env = FIXTURE_PROJECT / ".env"
        with self.assertRaises(UnrealSafetyError) as ctx:
            self.safety.validate_file_path(protected_env, project_root=FIXTURE_PROJECT)
        self.assertEqual(ctx.exception.code, UnrealErrorCode.PROTECTED_FILE_REJECTED)

        protected_dll = FIXTURE_PROJECT / "Binaries" / "UnrealPlugin.dll"
        with self.assertRaises(UnrealSafetyError) as ctx:
            self.safety.validate_file_path(protected_dll, project_root=FIXTURE_PROJECT)
        self.assertIn(ctx.exception.code, [UnrealErrorCode.PROTECTED_FILE_REJECTED, UnrealErrorCode.PROTECTED_DIRECTORY_REJECTED])

    # -------------------------------------------------------------------------
    # 3. Project Structure & Metadata Parsing
    # -------------------------------------------------------------------------

    def test_11_valid_uproject_parsing(self):
        """Test 11: Parsing valid .uproject JSON returns structured dictionary."""
        uproj_file = FIXTURE_PROJECT / "nr_unreal_test.uproject"
        data = self.inspector.parse_uproject(uproj_file)
        self.assertTrue(data.get("is_valid"))
        self.assertEqual(data.get("EngineAssociation"), "5.8")
        self.assertEqual(len(data.get("Modules", [])), 1)
        self.assertEqual(data.get("Modules")[0]["Name"], "nr_unreal_test")

    def test_12_malformed_uproject_handling(self):
        """Test 12: Malformed .uproject produces clean error diagnostic without crashing."""
        temp_dir = Path(tempfile.mkdtemp())
        try:
            bad_uproj = temp_dir / "Broken.uproject"
            bad_uproj.write_text("{ \"FileVersion\": 3, broken json here... ", encoding="utf-8")
            data = self.inspector.parse_uproject(bad_uproj)
            self.assertFalse(data.get("is_valid"))
            self.assertIn("error", data)
        finally:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_13_valid_uplugin_parsing(self):
        """Test 13: Parsing valid .uplugin JSON returns structured dictionary."""
        uplugin_file = FIXTURE_PROJECT / "Plugins" / "TestPlugin" / "TestPlugin.uplugin"
        data = self.inspector.parse_uplugin(uplugin_file)
        self.assertTrue(data.get("is_valid"))
        self.assertEqual(data.get("FriendlyName"), "TestPlugin")
        self.assertEqual(len(data.get("Modules", [])), 1)

    def test_14_malformed_uplugin_handling(self):
        """Test 14: Malformed .uplugin produces clean error diagnostic without crashing."""
        temp_dir = Path(tempfile.mkdtemp())
        try:
            bad_uplugin = temp_dir / "Broken.uplugin"
            bad_uplugin.write_text("invalid plugin json content", encoding="utf-8")
            data = self.inspector.parse_uplugin(bad_uplugin)
            self.assertFalse(data.get("is_valid"))
            self.assertIn("error", data)
        finally:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_15_project_structure_inspection(self):
        """Test 15: Inspecting full project structure produces comprehensive metadata."""
        meta = self.inspector.inspect_project(FIXTURE_PROJECT)
        self.assertIsInstance(meta, UnrealProjectMetadata)
        self.assertTrue(meta.is_valid)
        self.assertEqual(meta.name, "nr_unreal_test")
        self.assertEqual(meta.engine_association, "5.8")
        self.assertTrue(meta.is_cpp)
        self.assertFalse(meta.is_blueprint)
        self.assertGreaterEqual(meta.source_files_count, 2)
        self.assertGreaterEqual(meta.assets_count, 1)

    def test_16_engine_association_validation(self):
        """Test 16: Validating EngineAssociation against installed engines."""
        res = self.inspector.validate_engine_association(FIXTURE_PROJECT)
        self.assertTrue(res.get("is_valid"))
        self.assertEqual(res.get("engine_association"), "5.8")
        self.assertIn("status", res)

    def test_17_module_and_build_cs_inspection(self):
        """Test 17: Discovering and inspecting modules and Build.cs files."""
        meta = self.inspector.inspect_project(FIXTURE_PROJECT)
        self.assertGreaterEqual(len(meta.modules), 1)
        mod = meta.modules[0]
        self.assertEqual(mod.name, "nr_unreal_test")
        self.assertEqual(mod.type, "Runtime")
        self.assertTrue(any("Build.cs" in b for b in meta.build_cs_files))
        self.assertTrue(any("Target.cs" in t for t in meta.target_cs_files))

    def test_18_cpp_class_and_reflection_macro_extraction(self):
        """Test 18: Extracting C++ class names and reflection macros from headers."""
        meta = self.inspector.inspect_project(FIXTURE_PROJECT)
        class_names = [c.name for c in meta.classes]
        self.assertIn("AMyActor", class_names)
        my_actor = next(c for c in meta.classes if c.name == "AMyActor")
        self.assertEqual(my_actor.parent, "AActor")
        self.assertIn("UCLASS", my_actor.macros)
        self.assertIn("UPROPERTY", my_actor.macros)
        self.assertIn("UFUNCTION", my_actor.macros)
        self.assertIn("GENERATED_BODY", my_actor.macros)

    def test_19_config_file_inspection(self):
        """Test 19: Inspecting Config/*.ini files in the project."""
        res = self.registry.dispatch("unreal.inspect_config", project_path=str(FIXTURE_PROJECT))
        self.assertTrue(res.success)
        cfg_files = res.data.get("config_files", {})
        self.assertIn("DefaultEngine.ini", cfg_files)
        self.assertIn("DefaultGame.ini", cfg_files)

    def test_20_asset_inventory_and_sha256(self):
        """Test 20: Enumerating Content/ assets and verifying SHA-256 digests."""
        assets = self.inspector.list_assets(FIXTURE_PROJECT)
        self.assertGreaterEqual(len(assets), 2)
        asset_names = [a.name for a in assets]
        self.assertIn("TestMap", asset_names)
        self.assertIn("TestAsset", asset_names)
        for a in assets:
            self.assertIsNotNone(a.sha256)
            self.assertEqual(len(a.sha256), 64)

    def test_21_bounded_source_file_reading(self):
        """Test 21: Bounded reading of C++ source files with line limits and SHA-256."""
        actor_header = FIXTURE_PROJECT / "Source" / "nr_unreal_test" / "MyActor.h"
        res = self.inspector.read_source_file(actor_header, project_root=FIXTURE_PROJECT, max_lines=10)
        self.assertEqual(res["lines_read"], 10)
        self.assertIn("UCLASS", res["content"])
        self.assertIsNotNone(res["sha256"])

    # -------------------------------------------------------------------------
    # 4. Security, Audit Logging & Emergency Controls
    # -------------------------------------------------------------------------

    def test_22_sensitive_data_redaction(self):
        """Test 22: Redaction of secret tokens, API keys, and passwords."""
        raw_text = "api_key: 'unreal_secret_token_12345' and Bearer 9876543210abcdef123456"
        redacted = redact_sensitive_data(raw_text)
        self.assertNotIn("unreal_secret_token_12345", redacted)
        self.assertNotIn("9876543210abcdef123456", redacted)
        self.assertIn("***REDACTED***", redacted)

    def test_23_emergency_stop_freezes_operations(self):
        """Test 23: Thread-safe emergency stop immediately freezes all Unreal operations."""
        self.safety.trigger_emergency_stop("Operator manual stop test")
        self.assertTrue(self.safety.is_emergency_stopped())

        # Direct call raises EmergencyStopActiveError
        with self.assertRaises(EmergencyStopActiveError):
            self.safety.validate_project_path(FIXTURE_PROJECT)

        # Dispatch returns clean error result
        res = self.registry.dispatch("unreal.inspect_project", project_path=str(FIXTURE_PROJECT))
        self.assertFalse(res.success)
        self.assertEqual(res.error_code, UnrealErrorCode.EMERGENCY_STOPPED.value)

        # Reset resumes operations
        self.safety.reset_emergency_stop()
        self.assertFalse(self.safety.is_emergency_stopped())
        res_after = self.registry.dispatch("unreal.inspect_project", project_path=str(FIXTURE_PROJECT))
        self.assertTrue(res_after.success)

    def test_24_rate_limiting_enforcement(self):
        """Test 24: Rate limiting enforcement when call volume exceeds threshold."""
        strict_safety = UnrealSafetyGate(
            workspace_root=WORKSPACE_ROOT,
            authorized_project=FIXTURE_PROJECT,
            rate_limit_calls_per_minute=5,
        )
        for _ in range(5):
            strict_safety.check_rate_limit()
        with self.assertRaises(UnrealSafetyError) as ctx:
            strict_safety.check_rate_limit()
        self.assertEqual(ctx.exception.code, UnrealErrorCode.RATE_LIMIT_EXCEEDED)

    def test_25_tool_registry_dispatch_all_phase1_tools(self):
        """Test 25: All 12 approved Phase 1 tools dispatch successfully via registry with audit logging."""
        tools = self.registry.get_registered_tools()
        self.assertEqual(len(tools), 12)
        self.assertEqual(len(tools), len(set(tools)))  # 0 duplicates

        test_calls = [
            ("unreal.detect_environment", {}),
            ("unreal.list_installations", {}),
            ("unreal.inspect_project", {"project_path": str(FIXTURE_PROJECT)}),
            ("unreal.validate_project_path", {"project_path": str(FIXTURE_PROJECT)}),
            ("unreal.parse_uproject", {"project_path": str(FIXTURE_PROJECT)}),
            ("unreal.inspect_modules", {"project_path": str(FIXTURE_PROJECT)}),
            ("unreal.inspect_plugins", {"project_path": str(FIXTURE_PROJECT)}),
            ("unreal.inspect_project_structure", {"project_path": str(FIXTURE_PROJECT)}),
            ("unreal.validate_engine_association", {"project_path": str(FIXTURE_PROJECT)}),
            ("unreal.inspect_config", {"project_path": str(FIXTURE_PROJECT)}),
            ("unreal.list_assets", {"project_path": str(FIXTURE_PROJECT)}),
            ("unreal.read_source_file", {"file_path": str(FIXTURE_PROJECT / "Source" / "nr_unreal_test" / "MyActor.h"), "project_path": str(FIXTURE_PROJECT)}),
        ]

        for tool_name, kwargs in test_calls:
            res = self.registry.dispatch(tool_name, **kwargs)
            self.assertIsInstance(res, UnrealToolResult)
            self.assertTrue(res.success, f"Tool '{tool_name}' dispatch failed: {res.error}")

    def test_26_subprocess_security_no_shell(self):
        """Test 26: Verify that shell=True is never used across all Unreal agent modules."""
        import glob
        unreal_modules = glob.glob(str(Path("app/agent/unreal_*.py").resolve()))
        self.assertGreaterEqual(len(unreal_modules), 3)

        shell_true_pattern = re.compile(r"shell\s*=\s*True")
        for mod_path in unreal_modules:
            txt = Path(mod_path).read_text(encoding="utf-8", errors="ignore")
            matches = shell_true_pattern.findall(txt)
            self.assertEqual(len(matches), 0, f"Found shell=True in {mod_path}")

    def test_27_model_isolation_and_evidence_precedence(self):
        """Test 27: Advisory model has zero direct write/shell authority; deterministic evidence prevails."""
        # Unapproved tool cannot be executed
        res = self.registry.dispatch("unreal.execute_arbitrary_shell", command="rm -rf /")
        self.assertFalse(res.success)
        self.assertEqual(res.error_code, UnrealErrorCode.TOOL_NOT_ALLOWED.value)

        # Non-existent project cannot claim success
        res_fake = self.registry.dispatch("unreal.inspect_project", project_path=str(WORKSPACE_ROOT / "non_existent"))
        self.assertFalse(res_fake.success)

    def test_28_duplicate_tool_name_detection(self):
        """Test 28: Verify that no duplicate tool names exist in the Unreal tool registry."""
        tools = self.registry.get_registered_tools()
        self.assertEqual(len(tools), 12)
        unique_tools = set(tools)
        self.assertEqual(len(tools), len(unique_tools), f"Duplicate tools found: {len(tools) - len(unique_tools)}")
        for t in tools:
            self.assertTrue(t.startswith("unreal."))

    def test_29_fixture_integrity_and_serialization(self):
        """Test 29: Verify test fixture integrity and JSON serialization of all metadata models."""
        meta = self.inspector.inspect_project(FIXTURE_PROJECT)
        serialized = json.dumps(meta.to_dict(), indent=2)
        self.assertIsInstance(serialized, str)
        deserialized = json.loads(serialized)
        self.assertEqual(deserialized["name"], "nr_unreal_test")
        self.assertTrue(deserialized["is_valid"])
        self.assertEqual(len(deserialized["modules"]), 1)
        self.assertGreaterEqual(len(deserialized["classes"]), 1)


if __name__ == "__main__":
    unittest.main()
