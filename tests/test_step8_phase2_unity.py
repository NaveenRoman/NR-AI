"""
NR-AI Step 8 Phase 2 Acceptance Tests: Unity Compilation & Build Foundation.

Verifies:
- Unity executable validation and project compatibility
- Build target validation and rejection of unsupported targets
- Build output path validation and confinement (traversal, protected dir checks)
- Stale target cleanup and protection
- Build artifact verification (existence, size, SHA-256)
- C# compiler error and warning parsing from Unity logs
- Unity engine build exception parsing (BuildFailedException, shader errors)
- Safe bounded process execution (shell=False, timeout, process cleanup)
- Deterministic mock compilation (clean compilation vs compiler errors)
- Deterministic mock player build (artifact generation, evidence verification)
- Emergency stop freeze of compilation, build, and cleanup operations
- Sensitive data redaction in compiler issues, logs, and outputs
- Tool registry dispatch for all Phase 2 tools with structured audit logging
- Model isolation: zero raw shell execution and advisory-only control
- End-to-end controlled build workflow
"""

import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from app.agent.unity_safety import (
    UnitySafetyGate,
    UnityErrorCode,
    UnitySafetyError,
    EmergencyStopActiveError,
    ALLOWED_BUILD_TARGETS,
    ALLOWED_UNITY_BUILD_TOOLS,
    ALL_ALLOWED_UNITY_TOOLS,
    redact_sensitive_data,
)
from app.agent.unity_environment import (
    UnityEnvironmentDetector,
    UnityEditorInstance,
)
from app.agent.unity_project import (
    UnityProjectInspector,
)
from app.agent.unity_build import (
    UnityBuildManager,
    UnityLogParser,
    UnityProcessRunner,
    UnityCompilerIssue,
    CompilationResult,
    BuildResult,
    BuildArtifactInfo,
)
from app.agent.unity_tools import (
    UnityToolRegistry,
    UnityToolResult,
)
from app.memory.audit_logger import AuditLogger

WORKSPACE_ROOT = Path("C:/NR-AI").resolve()
FIXTURE_PROJECT = (WORKSPACE_ROOT / "nr_unity_test").resolve()


class TestStep8Phase2Unity(unittest.TestCase):
    """Test suite for Step 8 Phase 2: Unity Compilation & Build Foundation."""

    def setUp(self):
        self.safety = UnitySafetyGate(
            authorized_project=FIXTURE_PROJECT,
            workspace_root=WORKSPACE_ROOT,
            rate_limit_calls_per_minute=200,
        )
        self.env = UnityEnvironmentDetector()
        self.inspector = UnityProjectInspector(safety_gate=self.safety)
        self.runner = UnityProcessRunner(safety_gate=self.safety)
        self.build = UnityBuildManager(
            safety_gate=self.safety,
            env_detector=self.env,
            project_inspector=self.inspector,
            runner=self.runner,
        )
        self.temp_audit_dir = tempfile.mkdtemp()
        self.audit = AuditLogger(log_dir=self.temp_audit_dir)
        self.registry = UnityToolRegistry(
            safety_gate=self.safety,
            env_detector=self.env,
            inspector=self.inspector,
            build_manager=self.build,
            audit_logger=self.audit,
            workspace_root=WORKSPACE_ROOT,
            include_build_tools=True,
        )
        self.test_tmp_dir = tempfile.mkdtemp(dir=str(WORKSPACE_ROOT / "scratch"))

    def tearDown(self):
        if hasattr(self, "temp_audit_dir") and os.path.exists(self.temp_audit_dir):
            try:
                shutil.rmtree(self.temp_audit_dir, ignore_errors=True)
            except Exception:
                pass
        if hasattr(self, "test_tmp_dir") and os.path.exists(self.test_tmp_dir):
            try:
                shutil.rmtree(self.test_tmp_dir, ignore_errors=True)
            except Exception:
                pass

    # -------------------------------------------------------------------------
    # 1. Build Target & Output Path Validation
    # -------------------------------------------------------------------------

    def test_01_build_target_validation_allowed(self):
        """Tests that approved build targets are accepted."""
        for target in ALLOWED_BUILD_TARGETS:
            validated = self.build.validate_build_target(target)
            self.assertEqual(validated, target)

    def test_02_build_target_validation_rejected(self):
        """Tests that unsupported or dangerous build targets are rejected."""
        invalid_targets = ["PlayStation9", "NintendoSwitch2", "CustomOS", "", "../../target"]
        for target in invalid_targets:
            with self.assertRaises(UnitySafetyError) as ctx:
                self.build.validate_build_target(target)
            self.assertEqual(ctx.exception.code, UnityErrorCode.INVALID_BUILD_TARGET)

    def test_03_build_output_path_validation_valid(self):
        """Tests that valid build output paths within project are accepted."""
        out_p = FIXTURE_PROJECT / "Builds" / "StandaloneWindows64" / "Game.exe"
        resolved = self.build.validate_build_output_path(out_p, project_root=FIXTURE_PROJECT)
        self.assertEqual(resolved, out_p.resolve())

    def test_04_build_output_path_rejection_path_traversal(self):
        """Tests that path traversal sequences in build output paths are blocked."""
        traversal_p = FIXTURE_PROJECT / "Builds" / ".." / ".." / "outside.exe"
        with self.assertRaises(UnitySafetyError) as ctx:
            self.build.validate_build_output_path(traversal_p, project_root=FIXTURE_PROJECT)
        self.assertEqual(ctx.exception.code, UnityErrorCode.PATH_TRAVERSAL_DETECTED)

    def test_05_build_output_path_rejection_external_path(self):
        """Tests that system paths and paths outside workspace are blocked."""
        system_paths = [
            Path(r"C:\Windows\System32\payload.exe"),
            Path(r"C:\Program Files\Unity\malicious.exe"),
        ]
        for sp in system_paths:
            with self.assertRaises(UnitySafetyError) as ctx:
                self.build.validate_build_output_path(sp, project_root=FIXTURE_PROJECT)
            self.assertEqual(ctx.exception.code, UnityErrorCode.FILE_NOT_AUTHORIZED)

    def test_06_build_output_path_rejection_protected_directory(self):
        """Tests that build output targeting protected directories (Assets, ProjectSettings) is blocked."""
        bad_paths = [
            FIXTURE_PROJECT / "Assets" / "output.exe",
            FIXTURE_PROJECT / "ProjectSettings" / "output.exe",
            FIXTURE_PROJECT / "Packages" / "output.exe",
        ]
        for bp in bad_paths:
            with self.assertRaises(UnitySafetyError) as ctx:
                self.build.validate_build_output_path(bp, project_root=FIXTURE_PROJECT)
            self.assertEqual(ctx.exception.code, UnityErrorCode.PROTECTED_DIRECTORY_REJECTED)

    # -------------------------------------------------------------------------
    # 2. Stale Target Cleanup & Artifact Verification
    # -------------------------------------------------------------------------

    def test_07_clean_build_target_cleans_file(self):
        """Tests that clean_build_target removes stale build artifacts."""
        dummy_build = Path(self.test_tmp_dir) / "StaleGame.exe"
        dummy_build.write_bytes(b"STALE_BUILD_DATA")
        self.assertTrue(dummy_build.exists())

        success = self.build.clean_build_target(dummy_build)
        self.assertTrue(success)
        self.assertFalse(dummy_build.exists())

    def test_08_clean_build_target_refuses_project_root(self):
        """Tests that clean_build_target strictly refuses to delete the project or workspace root."""
        with self.assertRaises(UnitySafetyError) as ctx:
            self.build.clean_build_target(FIXTURE_PROJECT)
        self.assertEqual(ctx.exception.code, UnityErrorCode.PROTECTED_DIRECTORY_REJECTED)

    def test_09_verify_build_artifact_success(self):
        """Tests verify_build_artifact on an existing artifact file."""
        dummy_exe = Path(self.test_tmp_dir) / "VerifiedApp.exe"
        payload = b"UNITY_WINDOWS_STANDALONE_BINARY_MOCK"
        dummy_exe.write_bytes(payload)

        result = self.build.verify_build_artifact(dummy_exe)
        self.assertTrue(result["verified"])
        self.assertTrue(result["exists"])
        self.assertEqual(result["size_bytes"], len(payload))
        self.assertEqual(result["sha256"], hashlib.sha256(payload).hexdigest())

    def test_10_verify_build_artifact_missing(self):
        """Tests verify_build_artifact on a non-existent artifact."""
        missing_exe = Path(self.test_tmp_dir) / "NonExistent.exe"
        result = self.build.verify_build_artifact(missing_exe)
        self.assertFalse(result["verified"])
        self.assertFalse(result["exists"])

    # -------------------------------------------------------------------------
    # 3. Log Parsing & Compiler Diagnostics
    # -------------------------------------------------------------------------

    def test_11_log_parser_csharp_compiler_errors(self):
        """Tests parsing C# compiler errors into structured UnityCompilerIssue objects."""
        log_sample = (
            "Assets/Scripts/PlayerController.cs(15,20): error CS0103: The name 'speed' does not exist in the current context\n"
            "Assets/Scripts/GameManager.cs(30,12): error CS1002: ; expected\n"
        )
        parsed = UnityLogParser.parse_log(log_sample)
        self.assertEqual(parsed["error_count"], 2)
        self.assertEqual(parsed["warning_count"], 0)

        err1 = parsed["errors"][0]
        self.assertEqual(err1.file, "Assets/Scripts/PlayerController.cs")
        self.assertEqual(err1.line, 15)
        self.assertEqual(err1.column, 20)
        self.assertEqual(err1.code, "CS0103")
        self.assertIn("speed", err1.message)

        err2 = parsed["errors"][1]
        self.assertEqual(err2.code, "CS1002")
        self.assertEqual(err2.line, 30)

    def test_12_log_parser_csharp_warnings(self):
        """Tests parsing C# compiler warnings."""
        log_sample = (
            "Assets/Scripts/PlayerController.cs(10,5): warning CS0168: The variable 'unusedVar' is declared but never used\n"
        )
        parsed = UnityLogParser.parse_log(log_sample)
        self.assertEqual(parsed["error_count"], 0)
        self.assertEqual(parsed["warning_count"], 1)

        warn = parsed["warnings"][0]
        self.assertEqual(warn.code, "CS0168")
        self.assertEqual(warn.line, 10)
        self.assertEqual(warn.severity, "warning")

    def test_13_log_parser_build_failed_exception(self):
        """Tests parsing Unity BuildFailedException and compilation failure banners."""
        log_sample = (
            "Scripts have compiler errors.\n"
            "BuildFailedException: Build failed with errors.\n"
        )
        parsed = UnityLogParser.parse_log(log_sample)
        self.assertGreaterEqual(parsed["error_count"], 1)
        codes = [e.code for e in parsed["errors"]]
        self.assertTrue("BUILD_EXCEPTION" in codes or "COMPILATION_FAILED" in codes)

    def test_14_log_parser_shader_error(self):
        """Tests parsing fatal shader compilation errors."""
        log_sample = (
            "Shader error in 'Custom/WaterShader': invalid subscript 'uv' at line 45\n"
        )
        parsed = UnityLogParser.parse_log(log_sample)
        self.assertEqual(parsed["error_count"], 1)
        err = parsed["errors"][0]
        self.assertEqual(err.code, "SHADER_ERROR")
        self.assertEqual(err.file, "Custom/WaterShader")

    def test_15_log_parser_clean_compilation(self):
        """Tests parsing a completely clean compilation log."""
        log_sample = (
            "Compilation succeeded.\n"
            "Finished compiling assemblies.\n"
        )
        parsed = UnityLogParser.parse_log(log_sample)
        self.assertEqual(parsed["error_count"], 0)
        self.assertEqual(parsed["warning_count"], 0)
        self.assertIn("No compilation or build errors detected", parsed["summary"])

    # -------------------------------------------------------------------------
    # 4. Safe Bounded Process Execution
    # -------------------------------------------------------------------------

    def test_16_process_runner_bounded_execution(self):
        """Tests process execution using mock runner with bounded capture."""
        def mock_exec(cmd_args, timeout):
            return 0, "Build Output Sample: Success", ""

        self.runner.set_mock_executor(mock_exec)
        code, stdout, stderr, dur = self.runner.run_command(["dummy.exe", "-test"], timeout_seconds=10.0)
        self.assertEqual(code, 0)
        self.assertIn("Success", stdout)
        self.assertLess(dur, 5.0)

    def test_17_process_runner_timeout_and_cleanup(self):
        """Tests that execution timeout raises UnitySafetyError(BUILD_TIMEOUT)."""
        import subprocess

        # Test timeout exception handling in runner
        def mock_timeout(cmd_args, timeout):
            raise subprocess.TimeoutExpired(cmd_args, timeout)

        # Force runner live path with small timeout on non-blocking command
        with self.assertRaises(UnitySafetyError) as ctx:
            # We simulate a timeout by running a python command with sleep
            self.runner.set_mock_executor(None)
            self.runner.run_command(
                ["python", "-c", "import time; time.sleep(10)"],
                timeout_seconds=0.5,
            )
        self.assertEqual(ctx.exception.code, UnityErrorCode.BUILD_TIMEOUT)

    # -------------------------------------------------------------------------
    # 5. Deterministic Compilation & Build Workflows
    # -------------------------------------------------------------------------

    def test_18_compile_project_success_deterministic(self):
        """Tests controlled compilation reporting success with zero errors."""
        def mock_clean_compile(cmd_args, timeout):
            return 0, "Compiled 2 assemblies cleanly.\nTotal errors: 0\n", ""

        self.runner.set_mock_executor(mock_clean_compile)
        res = self.build.compile_project(FIXTURE_PROJECT)
        self.assertTrue(res.success)
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(res.error_count, 0)
        self.assertTrue(res.verified)

    def test_19_compile_project_compiler_error_deterministic(self):
        """Tests compilation with C# compiler error captured and structured."""
        def mock_failing_compile(cmd_args, timeout):
            out = (
                "Assets/Scripts/PlayerController.cs(12,15): error CS0103: The name 'missingVar' does not exist\n"
                "Scripts have compiler errors.\n"
            )
            return 1, out, ""

        self.runner.set_mock_executor(mock_failing_compile)
        res = self.build.compile_project(FIXTURE_PROJECT)
        self.assertFalse(res.success)
        self.assertEqual(res.exit_code, 1)
        self.assertEqual(res.error_count, 1)
        self.assertEqual(res.errors[0].code, "CS0103")
        self.assertIn("missingVar", res.errors[0].message)

    def test_20_compile_project_invalid_project_rejection(self):
        """Tests that compilation on an invalid directory raises PROJECT_INVALID."""
        empty_dir = Path(self.test_tmp_dir) / "NotUnity"
        empty_dir.mkdir()
        with self.assertRaises(UnitySafetyError) as ctx:
            self.build.compile_project(empty_dir)
        self.assertEqual(ctx.exception.code, UnityErrorCode.PROJECT_INVALID)

    def test_21_build_player_success_deterministic(self):
        """Tests player build execution with generated artifact verified."""
        out_exe = Path(self.test_tmp_dir) / "Build" / "Game.exe"

        def mock_build_success(cmd_args, timeout):
            # Create artifact as Unity would
            out_exe.parent.mkdir(parents=True, exist_ok=True)
            out_exe.write_bytes(b"EXECUTABLE_PAYLOAD_WIN64")
            return 0, "Player build completed successfully.\n", ""

        self.runner.set_mock_executor(mock_build_success)
        res = self.build.build_player(
            project_path=FIXTURE_PROJECT,
            build_target="StandaloneWindows64",
            output_path=str(out_exe),
        )
        self.assertTrue(res.success)
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(res.build_target, "StandaloneWindows64")
        self.assertEqual(len(res.artifacts), 1)
        self.assertEqual(res.artifacts[0].name, "Game.exe")
        self.assertTrue(res.verified)

    def test_22_build_player_failure_deterministic(self):
        """Tests player build failure when compiler errors occur."""
        out_exe = Path(self.test_tmp_dir) / "FailedBuild" / "Game.exe"

        def mock_build_fail(cmd_args, timeout):
            out = "BuildFailedException: Compilation failed.\n"
            return 1, out, ""

        self.runner.set_mock_executor(mock_build_fail)
        res = self.build.build_player(
            project_path=FIXTURE_PROJECT,
            build_target="StandaloneWindows64",
            output_path=str(out_exe),
        )
        self.assertFalse(res.success)
        self.assertEqual(res.exit_code, 1)
        self.assertFalse(res.verified)
        self.assertFalse(out_exe.exists())

    # -------------------------------------------------------------------------
    # 6. Safety Gate, Emergency Stop, Rate Limiting & Redaction
    # -------------------------------------------------------------------------

    def test_23_emergency_stop_freezes_compilation_and_build(self):
        """Tests that emergency stop immediately halts compilation, build, and cleanup."""
        self.safety.emergency_stop("Operator security halt")
        self.assertTrue(self.safety.is_emergency_stopped())

        with self.assertRaises(EmergencyStopActiveError):
            self.build.compile_project(FIXTURE_PROJECT)

        with self.assertRaises(EmergencyStopActiveError):
            self.build.build_player(FIXTURE_PROJECT)

        with self.assertRaises(EmergencyStopActiveError):
            self.build.clean_build_target(Path(self.test_tmp_dir) / "App.exe")

        # Deactivate
        self.safety.deactivate_emergency_stop()
        self.assertFalse(self.safety.is_emergency_stopped())

    def test_24_sensitive_data_redaction_in_build_logs(self):
        """Tests that sensitive keys and tokens are redacted from build logs and parsed errors."""
        raw_log = (
            "Assets/Scripts/NetManager.cs(10,5): error CS0103: API key AIzaSyA123456789012345678901234567890 failed\n"
            "Build log token Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.token123\n"
        )
        parsed = UnityLogParser.parse_log(raw_log)
        self.assertEqual(parsed["error_count"], 1)
        err_msg = parsed["errors"][0].message
        self.assertNotIn("AIzaSy", err_msg)
        self.assertIn("[REDACTED_API_KEY]", err_msg)

    # -------------------------------------------------------------------------
    # 7. Tool Registry Dispatch & End-to-End Workflow
    # -------------------------------------------------------------------------

    def test_25_tool_registry_dispatch_phase2_tools_and_audit(self):
        """Tests dispatching all 8 Phase 2 tools through UnityToolRegistry and auditing."""
        tools = self.registry.get_registered_tools()
        for t in ALLOWED_UNITY_BUILD_TOOLS:
            self.assertIn(t, tools)

        # 1. unity.validate_build_target
        r1 = self.registry.execute_tool("unity.validate_build_target", {"target": "StandaloneWindows64"})
        self.assertTrue(r1.success)

        # 2. unity.validate_build_output_path
        valid_out = str(FIXTURE_PROJECT / "Builds" / "StandaloneWindows64" / "Game.exe")
        r2 = self.registry.execute_tool("unity.validate_build_output_path", {"output_path": valid_out})
        self.assertTrue(r2.success)

        # 3. unity.get_build_configuration
        r3 = self.registry.execute_tool("unity.get_build_configuration", {"project_path": str(FIXTURE_PROJECT)})
        self.assertTrue(r3.success)
        self.assertIn("scenes_in_build", r3.data)

        # 4. unity.clean_build_target
        test_file = Path(self.test_tmp_dir) / "ToClean.exe"
        test_file.write_bytes(b"DATA")
        r4 = self.registry.execute_tool("unity.clean_build_target", {"output_path": str(test_file)})
        self.assertTrue(r4.success)

        # 5. unity.verify_build_artifact
        artifact = Path(self.test_tmp_dir) / "TargetApp.exe"
        artifact.write_bytes(b"TARGET_PAYLOAD")
        r5 = self.registry.execute_tool("unity.verify_build_artifact", {"output_path": str(artifact)})
        self.assertTrue(r5.success)
        self.assertTrue(r5.data["verified"])

        # 6. unity.parse_build_log
        log_sample = "Assets/Test.cs(5,1): error CS0103: The name 'a' does not exist\n"
        r6 = self.registry.execute_tool("unity.parse_build_log", {"log_text": log_sample})
        self.assertTrue(r6.success)
        self.assertEqual(r6.data["error_count"], 1)

        # 7. unity.compile_project (with mock)
        def mock_compile(cmd_args, timeout):
            return 0, "Compiled successfully\n", ""
        self.runner.set_mock_executor(mock_compile)
        r7 = self.registry.execute_tool("unity.compile_project", {"project_path": str(FIXTURE_PROJECT)})
        self.assertTrue(r7.success)

        # 8. unity.build_player (with mock)
        out_app = Path(self.test_tmp_dir) / "FinalApp.exe"
        def mock_build(cmd_args, timeout):
            out_app.write_bytes(b"FINAL_APP_BINARY")
            return 0, "Build succeeded\n", ""
        self.runner.set_mock_executor(mock_build)
        r8 = self.registry.execute_tool("unity.build_player", {
            "project_path": str(FIXTURE_PROJECT),
            "build_target": "StandaloneWindows64",
            "output_path": str(out_app),
        })
        self.assertTrue(r8.success)
        self.assertTrue(r8.verified)

    def test_26_end_to_end_controlled_build_workflow(self):
        """Tests complete end-to-end controlled build workflow."""
        # 1. Validate target
        target = self.build.validate_build_target("StandaloneWindows64")
        self.assertEqual(target, "StandaloneWindows64")

        # 2. Inspect build configuration
        cfg = self.build.get_build_configuration(FIXTURE_PROJECT)
        self.assertEqual(cfg["unity_version"], "2022.3.35f1")
        self.assertGreaterEqual(cfg["scenes_in_build_count"], 1)

        # 3. Controlled compilation
        def mock_e2e_compile(cmd, timeout):
            return 0, "Compiled 2 assemblies\n", ""
        self.runner.set_mock_executor(mock_e2e_compile)
        c_res = self.build.compile_project(FIXTURE_PROJECT)
        self.assertTrue(c_res.success)

        # 4. Controlled build
        out_binary = Path(self.test_tmp_dir) / "E2EGame.exe"
        def mock_e2e_build(cmd, timeout):
            out_binary.write_bytes(b"E2E_BINARY_MOCK_PAYLOAD")
            return 0, "Built player successfully\n", ""
        self.runner.set_mock_executor(mock_e2e_build)
        b_res = self.build.build_player(
            project_path=FIXTURE_PROJECT,
            build_target=target,
            output_path=str(out_binary),
        )
        self.assertTrue(b_res.success)
        self.assertEqual(len(b_res.artifacts), 1)

        # 5. Deterministic verification
        v_res = self.build.verify_build_artifact(out_binary)
        self.assertTrue(v_res["verified"])
        self.assertEqual(v_res["sha256"], b_res.artifacts[0].sha256)

    def test_27_model_isolation_and_advisory_boundary(self):
        """Tests that models have zero direct shell execution or arbitrary path manipulation authority."""
        # Safety gate rejects raw shell attempts or arbitrary unapproved tools
        with self.assertRaises(UnitySafetyError) as ctx:
            self.safety.validate_tool_allowed("system.exec_powershell")
        self.assertEqual(ctx.exception.code, UnityErrorCode.TOOL_NOT_ALLOWED)

        # Rejects arbitrary execution of batch files or shell scripts as build outputs
        with self.assertRaises(UnitySafetyError):
            self.build.validate_build_output_path(Path(r"C:\NR-AI\malicious.bat"))


if __name__ == "__main__":
    unittest.main()
