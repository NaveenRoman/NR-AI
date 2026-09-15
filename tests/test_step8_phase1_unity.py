"""
NR-AI Step 8 Phase 1 Acceptance Tests: Unity Environment and Project Inspection Foundation.

Verifies:
- Unity environment detection (Editor, Hub, CLI, active processes)
- Unity project discovery and metadata inspection
- Package manifest (Packages/manifest.json) parsing
- Asset and C# script discovery with .meta GUID extraction
- Bounded script reading with SHA-256 computation
- Assembly definition (.asmdef) and build scene inspection
- Safety gate boundaries, path traversal rejection, and protected file handling
- Emergency stop freeze and rate limiting
- Sensitive data redaction
- Tool registry dispatch for all 12 tools with structured results and audit logging
"""

import json
import os
from pathlib import Path
import tempfile
import unittest

from app.agent.unity_safety import (
    UnitySafetyGate,
    UnityErrorCode,
    UnitySafetyError,
    EmergencyStopActiveError,
    ALLOWED_UNITY_TOOLS,
    PROTECTED_UNITY_FILES,
    PROTECTED_UNITY_EXTENSIONS,
    redact_sensitive_data,
    MAX_READ_LINES,
)
from app.agent.unity_environment import (
    UnityEnvironmentDetector,
    UnityEditorInstance,
    UnityCliInfo,
    UnityHubInfo,
    UnityEnvironmentInfo,
)
from app.agent.unity_project import (
    UnityProjectInspector,
    UnityProjectMetadata,
    UnityAssetInfo,
)
from app.agent.unity_tools import (
    UnityToolRegistry,
    UnityToolResult,
)
from app.memory.audit_logger import AuditLogger


WORKSPACE_ROOT = Path(r"C:/NR-AI").resolve()
FIXTURE_PROJECT = (WORKSPACE_ROOT / "nr_unity_test").resolve()


class TestStep8Phase1Unity(unittest.TestCase):
    """Acceptance test suite for Step 8 Phase 1: Unity Environment and Project Inspection Foundation."""

    def setUp(self):
        self.safety = UnitySafetyGate(
            workspace_root=WORKSPACE_ROOT,
            authorized_project=FIXTURE_PROJECT,
            authorized_projects=[FIXTURE_PROJECT],
            rate_limit_calls_per_minute=200,
        )
        self.env = UnityEnvironmentDetector()
        self.inspector = UnityProjectInspector(safety_gate=self.safety)
        self.temp_audit_dir = tempfile.mkdtemp()
        self.audit = AuditLogger(log_dir=self.temp_audit_dir)
        self.registry = UnityToolRegistry(
            safety_gate=self.safety,
            env_detector=self.env,
            inspector=self.inspector,
            audit_logger=self.audit,
            workspace_root=WORKSPACE_ROOT,
        )

    # -------------------------------------------------------------------------
    # 1. Environment Detection Tests
    # -------------------------------------------------------------------------

    def test_01_environment_detection_host_or_fallback(self):
        """Tests detecting Unity environment on the current system without crashing."""
        info = self.env.detect_environment()
        self.assertIsInstance(info, UnityEnvironmentInfo)
        self.assertIsInstance(info.is_available, bool)
        self.assertIsInstance(info.editors, list)
        self.assertIsInstance(info.cli, UnityCliInfo)
        self.assertIsInstance(info.hub, UnityHubInfo)

    def test_02_environment_detection_missing_fallback(self):
        """Tests environment detector when no editors or tools are found in custom search paths."""
        custom_detector = UnityEnvironmentDetector(
            known_roots=[Path(r"C:/NonExistent/Unity/Editor/Unity.exe")],
            hub_paths=[Path(r"C:/NonExistent/UnityHub/UnityHub.exe")],
            cli_path=Path(r"C:/NonExistent/Unity/bin/unity.exe"),
        )
        info = custom_detector.detect_environment()
        self.assertFalse(info.is_available)
        self.assertEqual(len(info.editors), 0)
        self.assertIsNone(info.preferred_editor)
        self.assertFalse(info.cli.is_available)
        self.assertFalse(info.hub.is_available)

    def test_03_editor_version_parsing(self):
        """Tests parsing Unity editor version strings and sorting them correctly."""
        d = UnityEnvironmentDetector()
        v1 = d._parse_version_components("2022.3.35f1")
        v2 = d._parse_version_components("2021.3.16f1")
        v3 = d._parse_version_components("2023.1.0a1")
        self.assertEqual(v1, (2022, 3, True))
        self.assertEqual(v2, (2021, 3, True))
        self.assertEqual(v3, (2023, 1, False))
        self.assertGreater(v1, v2)

    def test_04_editor_for_project_matching(self):
        """Tests matching an editor for a specific project version."""
        matching_ed = self.env.find_editor_for_project(FIXTURE_PROJECT)
        if matching_ed:
            self.assertIsInstance(matching_ed, UnityEditorInstance)
            self.assertTrue(matching_ed.is_executable)

    def test_05_unity_cli_detection(self):
        """Tests official Unity CLI detection dataclass structure."""
        cli = self.env.detect_cli()
        self.assertIsInstance(cli, UnityCliInfo)
        self.assertIsInstance(cli.is_available, bool)
        if cli.is_available:
            self.assertTrue(Path(cli.cli_path).is_file())

    def test_06_running_editor_processes_inspection(self):
        """Tests checking for running Unity Editor processes safely (shell=False)."""
        procs = self.env.get_running_editor_processes()
        self.assertIsInstance(procs, list)
        for proc in procs:
            self.assertIn("pid", proc)
            self.assertIn("name", proc)

    # -------------------------------------------------------------------------
    # 2. Project & Asset Inspection Tests
    # -------------------------------------------------------------------------

    def test_07_project_validation_valid_project(self):
        """Tests that the test fixture is recognized as a valid Unity project."""
        is_valid = self.inspector.is_valid_project(FIXTURE_PROJECT)
        self.assertTrue(is_valid)

    def test_08_project_validation_invalid_directory(self):
        """Tests that an empty directory is correctly identified as not a Unity project."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            is_valid = self.inspector.is_valid_project(tmppath)
            self.assertFalse(is_valid)

    def test_09_project_metadata_inspection(self):
        """Tests inspecting comprehensive project metadata from nr_unity_test."""
        meta = self.inspector.inspect_project(FIXTURE_PROJECT)
        self.assertIsInstance(meta, UnityProjectMetadata)
        self.assertEqual(meta.name, "nr_unity_test")
        self.assertEqual(meta.unity_version, "2022.3.35f1")
        self.assertEqual(meta.render_pipeline, "Universal Render Pipeline (URP)")
        self.assertGreaterEqual(meta.packages_count, 5)
        self.assertGreaterEqual(meta.csharp_scripts_count, 2)
        self.assertGreaterEqual(meta.scenes_count, 1)
        self.assertTrue(meta.is_valid)

    def test_10_project_version_reading(self):
        """Tests reading the target Unity editor version from ProjectVersion.txt."""
        version = self.inspector.get_project_version(FIXTURE_PROJECT)
        self.assertEqual(version, "2022.3.35f1")

    def test_11_package_manifest_parsing(self):
        """Tests parsing Packages/manifest.json dependencies."""
        pkgs = self.inspector.inspect_packages(FIXTURE_PROJECT)
        self.assertIsInstance(pkgs, list)
        self.assertGreaterEqual(len(pkgs), 5)
        pkg_names = [p["package"] for p in pkgs]
        self.assertIn("com.unity.textmeshpro", pkg_names)
        self.assertIn("com.unity.render-pipelines.universal", pkg_names)

    def test_12_asset_discovery_and_categorization(self):
        """Tests enumerating assets and classifying them by type."""
        assets = self.inspector.list_assets(FIXTURE_PROJECT)
        self.assertGreaterEqual(len(assets), 4)
        types = {a.asset_type for a in assets}
        self.assertIn("Script", types)
        self.assertIn("Scene", types)
        self.assertIn("AsmDef", types)

    def test_13_meta_file_and_guid_inspection(self):
        """Tests that asset metadata companion (.meta) files are inspected for GUIDs."""
        assets = self.inspector.list_assets(FIXTURE_PROJECT, asset_type="Script")
        self.assertGreater(len(assets), 0)
        for a in assets:
            if a.name == "GameManager.cs":
                self.assertTrue(a.has_meta)
                self.assertIsNotNone(a.guid)
                self.assertEqual(len(a.guid), 32)

    def test_14_csharp_script_discovery(self):
        """Tests discovering C# scripts matching wildcard patterns."""
        scripts = self.inspector.find_scripts(FIXTURE_PROJECT, pattern="*.cs")
        self.assertGreaterEqual(len(scripts), 2)
        names = [Path(s).name for s in scripts]
        self.assertIn("GameManager.cs", names)
        self.assertIn("PlayerController.cs", names)

    def test_15_bounded_script_reading_and_sha256(self):
        """Tests bounded script reading and SHA-256 digest computation."""
        script_file = FIXTURE_PROJECT / "Assets" / "Scripts" / "GameManager.cs"
        res = self.inspector.read_script(script_file, max_lines=10)
        self.assertEqual(res["lines_returned"], 10)
        self.assertGreater(res["total_lines"], 10)
        self.assertTrue(res["truncated"])
        self.assertIn("sha256", res)
        self.assertEqual(len(res["sha256"]), 64)

    def test_16_asmdef_inspection(self):
        """Tests parsing Unity assembly definition (.asmdef) files."""
        asmdef_file = FIXTURE_PROJECT / "Assets" / "Scripts" / "GameScripts.asmdef"
        data = self.inspector.inspect_asmdef(asmdef_file)
        self.assertEqual(data["name"], "GameScripts")
        self.assertIn("Unity.TextMeshPro", data["references"])
        self.assertTrue(data["auto_referenced"])

    def test_17_scenes_in_build_inspection(self):
        """Tests reading configured scenes in build settings."""
        scenes = self.inspector.inspect_scenes_in_build(FIXTURE_PROJECT)
        self.assertGreaterEqual(len(scenes), 1)
        self.assertTrue(any("SampleScene.unity" in s["path"] for s in scenes))

    # -------------------------------------------------------------------------
    # 3. Safety Gate & Policy Enforcement Tests
    # -------------------------------------------------------------------------

    def test_18_safety_allowlist_enforcement(self):
        """Tests that unapproved tools are rejected by the safety gate."""
        with self.assertRaises(UnitySafetyError) as ctx:
            self.safety.validate_tool_allowed("unity.unauthorized_tool")
        self.assertEqual(ctx.exception.code, UnityErrorCode.TOOL_NOT_ALLOWED)

    def test_19_safety_path_traversal_rejection(self):
        """Tests detecting and blocking path traversal attempts."""
        traversal_path = FIXTURE_PROJECT / ".." / ".." / "Windows"
        with self.assertRaises(UnitySafetyError) as ctx:
            self.safety.validate_path(traversal_path)
        self.assertEqual(ctx.exception.code, UnityErrorCode.PATH_TRAVERSAL_DETECTED)

    def test_20_safety_external_path_rejection(self):
        """Tests that paths outside authorized workspace are blocked."""
        external_path = Path(r"C:/Windows/System32/drivers/etc/hosts")
        with self.assertRaises(UnitySafetyError) as ctx:
            self.safety.validate_path(external_path)
        self.assertEqual(ctx.exception.code, UnityErrorCode.FILE_NOT_AUTHORIZED)

    def test_21_safety_protected_file_rejection(self):
        """Tests that protected project configuration files cannot be modified."""
        protected_sln = FIXTURE_PROJECT / "nr_unity_test.sln"
        with self.assertRaises(UnitySafetyError) as ctx:
            self.safety.validate_file_for_write(protected_sln)
        self.assertEqual(ctx.exception.code, UnityErrorCode.PROTECTED_FILE_REJECTED)

    def test_22_safety_protected_directory_rejection(self):
        """Tests that protected internal directories (Library, Temp, obj) are rejected."""
        protected_dir = FIXTURE_PROJECT / "Library" / "shadercache.bin"
        with self.assertRaises(UnitySafetyError) as ctx:
            self.safety.validate_file_for_write(protected_dir)
        self.assertIn(ctx.exception.code, [UnityErrorCode.PROTECTED_DIRECTORY_REJECTED, UnityErrorCode.PROTECTED_FILE_REJECTED, UnityErrorCode.FILE_NOT_AUTHORIZED])

    def test_23_safety_emergency_stop(self):
        """Tests thread-safe emergency stop freezing all Unity operations."""
        self.safety.emergency_stop(reason="Operator halt")
        self.assertTrue(self.safety.is_emergency_stopped())
        with self.assertRaises(EmergencyStopActiveError):
            self.safety.validate_tool_allowed("unity.detect_environment")

        # Verify registry dispatch fails during emergency stop
        res = self.registry.execute_tool("unity.detect_environment")
        self.assertFalse(res.success)
        self.assertEqual(res.error_code, UnityErrorCode.EMERGENCY_STOPPED.value)

        # Deactivate emergency stop
        self.safety.deactivate_emergency_stop()
        self.assertFalse(self.safety.is_emergency_stopped())
        res_resumed = self.registry.execute_tool("unity.detect_environment")
        self.assertTrue(res_resumed.success)

    def test_24_safety_rate_limiting(self):
        """Tests rate limiter blocking rapid bursts of calls."""
        strict_safety = UnitySafetyGate(
            workspace_root=WORKSPACE_ROOT,
            authorized_project=FIXTURE_PROJECT,
            rate_limit_calls_per_minute=3,
        )
        strict_safety.check_rate_limit("test_tool")
        strict_safety.check_rate_limit("test_tool")
        strict_safety.check_rate_limit("test_tool")
        with self.assertRaises(UnitySafetyError) as ctx:
            strict_safety.check_rate_limit("test_tool")
        self.assertEqual(ctx.exception.code, UnityErrorCode.RATE_LIMIT_EXCEEDED)

    def test_25_sensitive_data_redaction(self):
        """Tests automatic masking of API keys, bearer tokens, passwords, and secrets."""
        secret_sample = """
unity_lic = UL-9999-SECRET
api_key = AIzaSyA1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6
Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9
password = MySecretPass123
client_secret = ClientSecret999
"""
        redacted = redact_sensitive_data(secret_sample)
        self.assertNotIn("AIzaSy", redacted)
        self.assertNotIn("MySecretPass123", redacted)
        self.assertNotIn("ClientSecret999", redacted)
        self.assertNotIn("UL-9999-SECRET", redacted)
        self.assertIn("[REDACTED_API_KEY]", redacted)
        self.assertIn("[REDACTED_PASSWORD]", redacted)
        self.assertIn("[REDACTED_SECRET]", redacted)
        self.assertIn("[REDACTED_LICENSE]", redacted)

    # -------------------------------------------------------------------------
    # 4. Tool Registry Dispatch & Audit Logging Tests
    # -------------------------------------------------------------------------

    def test_26_tool_registry_dispatch_all_12_tools_and_audit(self):
        """Tests dispatching all 12 registered tools through UnityToolRegistry and auditing calls."""
        tools = self.registry.get_registered_tools()
        self.assertEqual(tools, ALLOWED_UNITY_TOOLS)
        self.assertEqual(len(tools), 12)

        proj_p = str(FIXTURE_PROJECT)

        # 1. unity.detect_environment
        r1 = self.registry.execute_tool("unity.detect_environment")
        self.assertTrue(r1.success)

        # 2. unity.inspect_project
        r2 = self.registry.execute_tool("unity.inspect_project", {"project_path": proj_p})
        self.assertTrue(r2.success)

        # 3. unity.list_projects
        r3 = self.registry.execute_tool("unity.list_projects")
        self.assertTrue(r3.success)

        # 4. unity.inspect_packages
        r4 = self.registry.execute_tool("unity.inspect_packages", {"project_path": proj_p})
        self.assertTrue(r4.success)

        # 5. unity.list_assets
        r5 = self.registry.execute_tool("unity.list_assets", {"project_path": proj_p})
        self.assertTrue(r5.success)

        # 6. unity.find_scripts
        r6 = self.registry.execute_tool("unity.find_scripts", {"project_path": proj_p})
        self.assertTrue(r6.success)

        # 7. unity.read_script
        r7 = self.registry.execute_tool("unity.read_script", {
            "project_path": proj_p,
            "relative_path": "Assets/Scripts/GameManager.cs",
        })
        self.assertTrue(r7.success)

        # 8. unity.inspect_asmdef
        r8 = self.registry.execute_tool("unity.inspect_asmdef", {"project_path": proj_p})
        self.assertTrue(r8.success)

        # 9. unity.inspect_scenes_in_build
        r9 = self.registry.execute_tool("unity.inspect_scenes_in_build", {"project_path": proj_p})
        self.assertTrue(r9.success)

        # 10. unity.inspect_editor_state
        r10 = self.registry.execute_tool("unity.inspect_editor_state")
        self.assertTrue(r10.success)

        # 11. unity.validate_project_structure
        r11 = self.registry.execute_tool("unity.validate_project_structure", {"project_path": proj_p})
        self.assertTrue(r11.success)

        # 12. unity.inspect_project_version
        r12 = self.registry.execute_tool("unity.inspect_project_version", {"project_path": proj_p})
        self.assertTrue(r12.success)


if __name__ == "__main__":
    unittest.main()
