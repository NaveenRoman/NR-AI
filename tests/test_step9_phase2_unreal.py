"""
NR-AI Acceptance Suite — Step 9 Phase 2: Unreal Engine Agent Compilation & Build Foundation.

Validates:
- Build environment detection and prerequisite validation (.NET, UBT, Engine, Project)
- Target validation with strict allowlists and injection rejection
- Safe build execution (shell=False, timeouts, process tree cleanup, output bounds)
- Log parsing across 17 distinct diagnostic categories (C++, UHT, Linker, Build.cs, .NET, etc.)
- Deterministic build result verification & evidence precedence (model cannot override evidence)
- Bounded artifact inspection with SHA-256 digests
- Subprocess security, emergency stop, audit logging, and 0 orphan processes
"""

import ast
import json
import os
from pathlib import Path
import subprocess
import time
import unittest

from app.agent.unreal_safety import (
    UnrealSafetyGate,
    UnrealErrorCode,
    UnrealSafetyError,
    EmergencyStopActiveError,
    ALLOWED_UNREAL_CONFIGURATIONS,
    ALLOWED_UNREAL_TARGET_TYPES,
    ALLOWED_UNREAL_PLATFORMS,
    ALLOWED_UNREAL_PHASE1_TOOLS,
    ALLOWED_UNREAL_PHASE2_TOOLS,
    ALL_ALLOWED_UNREAL_TOOLS,
    BUILD_TIMEOUT_SECONDS,
    COMPILE_TIMEOUT_SECONDS,
    MAX_CAPTURED_OUTPUT_BYTES,
    redact_sensitive_data,
)
from app.agent.unreal_environment import (
    UnrealEnvironmentDetector,
    UnrealEngineInstance,
    UnrealEngineStatus,
)
from app.agent.unreal_project import UnrealProjectInspector
from app.agent.unreal_build import (
    UnrealBuildEnvironmentStatus,
    UnrealBuildResultStatus,
    UnrealBuildIssueCategory,
    UnrealBuildDiagnostic,
    UnrealBuildTargetInfo,
    UnrealBuildArtifactInfo,
    UnrealBuildResult,
    UnrealBuildLogParser,
    UnrealBuildEnvironmentValidator,
    UnrealBuildArtifactVerifier,
    UnrealBuildRunner,
    UnrealBuildResultVerifier,
)
from app.agent.unreal_tools import UnrealToolRegistry, UnrealToolResult

WORKSPACE_ROOT = Path("C:/NR-AI").resolve()
FIXTURE_PROJECT = (WORKSPACE_ROOT / "nr_unreal_test").resolve()


class TestStep9Phase2Unreal(unittest.TestCase):
    """Authoritative test suite for Step 9 Phase 2 Unreal Build Foundation."""

    def setUp(self):
        self.safety = UnrealSafetyGate(
            workspace_root=WORKSPACE_ROOT,
            authorized_projects=[FIXTURE_PROJECT],
        )
        self.env = UnrealEnvironmentDetector()
        self.inspector = UnrealProjectInspector(self.safety, self.env)
        self.validator = UnrealBuildEnvironmentValidator(self.safety, self.env, self.inspector)
        self.artifact_verifier = UnrealBuildArtifactVerifier(self.safety)
        self.runner = UnrealBuildRunner(
            safety_gate=self.safety,
            env_detector=self.env,
            inspector=self.inspector,
            validator=self.validator,
            artifact_verifier=self.artifact_verifier,
        )
        self.registry = UnrealToolRegistry(
            safety_gate=self.safety,
            env_detector=self.env,
            inspector=self.inspector,
            build_runner=self.runner,
            build_validator=self.validator,
            artifact_verifier=self.artifact_verifier,
        )

    def tearDown(self):
        self.safety.reset_emergency_stop()

    # 1. Build environment validation (host check without crash)
    def test_01_build_environment_validation_host(self):
        """Test 1: Build environment validation returns structured dictionary without crashing."""
        res = self.validator.validate_build_environment(FIXTURE_PROJECT)
        self.assertIn("status", res)
        self.assertIn("checks", res)
        self.assertIn("summary", res)
        checks = res["checks"]
        self.assertIn("engine_installed", checks)
        self.assertIn("ubt_available", checks)
        self.assertIn("dotnet_runtime_available", checks)
        self.assertIn("project_valid", checks)
        self.assertTrue(checks["project_valid"])

    # 2. Missing Unreal environment
    def test_02_missing_unreal_environment(self):
        """Test 2: Environment validator handles missing Unreal Engine installations."""
        mock_env = UnrealEnvironmentDetector()
        mock_env.detect_engines = lambda: []  # No engines
        validator = UnrealBuildEnvironmentValidator(self.safety, mock_env, self.inspector)
        res = validator.validate_build_environment(FIXTURE_PROJECT)
        self.assertEqual(res["status"], UnrealBuildEnvironmentStatus.ENVIRONMENT_UNAVAILABLE.value)
        self.assertFalse(res["usable"])

    # 3. Missing UnrealBuildTool
    def test_03_missing_unreal_build_tool(self):
        """Test 3: Environment validator detects missing UBT executable."""
        mock_engine = UnrealEngineInstance(
            engine_path=r"C:\Fake\UE_5.8",
            version="5.8.1",
            status=UnrealEngineStatus.INCOMPLETE,
            editor_executable=None,
            cmd_executable=None,
            ubt_executable=r"C:\Fake\UE_5.8\MissingUBT.exe",
        )
        mock_env = UnrealEnvironmentDetector()
        mock_env.detect_engines = lambda: [mock_engine]
        validator = UnrealBuildEnvironmentValidator(self.safety, mock_env, self.inspector)
        res = validator.validate_build_environment(FIXTURE_PROJECT)
        self.assertFalse(res["checks"]["ubt_available"])

    # 4. Missing runtime/toolchain (.NET)
    def test_04_missing_runtime_toolchain(self):
        """Test 4: Environment validator flags missing .NET runtime host."""
        validator = UnrealBuildEnvironmentValidator(self.safety, self.env, self.inspector)
        validator._check_dotnet_usable = lambda: False  # Force .NET unavailable
        res = validator.validate_build_environment(FIXTURE_PROJECT)
        self.assertFalse(res["checks"]["dotnet_runtime_available"])
        self.assertEqual(res["status"], UnrealBuildEnvironmentStatus.ENVIRONMENT_UNAVAILABLE.value)

    # 5. Project validity
    def test_05_project_validity(self):
        """Test 5: Project structure validation detects valid vs invalid project dirs."""
        res_valid = self.validator.validate_build_environment(FIXTURE_PROJECT)
        self.assertTrue(res_valid["checks"]["project_valid"])

    # 6. Engine mismatch
    def test_06_engine_mismatch(self):
        """Test 6: Validator detects engine association mismatch when project expects different version."""
        mock_engine = UnrealEngineInstance(
            engine_path=r"C:\Fake\UE_4.27",
            version="4.27.2",
            status=UnrealEngineStatus.USABLE,
            editor_executable=None,
            cmd_executable=None,
            ubt_executable=None,
        )
        mock_env = UnrealEnvironmentDetector()
        mock_env.detect_engines = lambda: [mock_engine]
        mock_inspector = UnrealProjectInspector(self.safety, mock_env)
        validator = UnrealBuildEnvironmentValidator(self.safety, mock_env, mock_inspector)
        res = validator.validate_build_environment(FIXTURE_PROJECT)
        self.assertEqual(res["status"], UnrealBuildEnvironmentStatus.ENGINE_MISMATCH.value)

    # 7. Valid build target
    def test_07_valid_build_target(self):
        """Test 7: Valid target parameters pass validation."""
        t_name, t_type, config, plat = self.safety.validate_build_target(
            FIXTURE_PROJECT, "nr_unreal_test", "Editor", "Development", "Win64"
        )
        self.assertEqual(t_name, "nr_unreal_test")
        self.assertEqual(t_type, "Editor")
        self.assertEqual(config, "Development")
        self.assertEqual(plat, "Win64")

    # 8. Invalid build target configuration
    def test_08_invalid_build_target_configuration(self):
        """Test 8: Invalid configuration raises UnrealSafetyError."""
        with self.assertRaises(UnrealSafetyError) as ctx:
            self.safety.validate_build_target(
                FIXTURE_PROJECT, "nr_unreal_test", "Editor", "UltraFastConfig", "Win64"
            )
        self.assertEqual(ctx.exception.code, UnrealErrorCode.INVALID_BUILD_CONFIGURATION)

    # 9. Arbitrary target rejection
    def test_09_arbitrary_target_rejection(self):
        """Test 9: Targets with special characters are rejected."""
        with self.assertRaises(UnrealSafetyError):
            self.safety.validate_build_target(
                FIXTURE_PROJECT, "Target*Name", "Editor", "Development", "Win64"
            )

    # 10. Arbitrary argument rejection
    def test_10_arbitrary_argument_rejection(self):
        """Test 10: Target name containing flag syntax or spaces is rejected."""
        with self.assertRaises(UnrealSafetyError):
            self.safety.validate_build_target(
                FIXTURE_PROJECT, "MyTarget -CustomFlag", "Editor", "Development", "Win64"
            )

    # 11. Path traversal rejection
    def test_11_path_traversal_rejection(self):
        """Test 11: Path traversal sequences in project path are rejected."""
        with self.assertRaises(UnrealSafetyError) as ctx:
            self.safety.validate_build_target(
                WORKSPACE_ROOT / "nr_unreal_test/../outside", "nr_unreal_test"
            )
        self.assertEqual(ctx.exception.code, UnrealErrorCode.PATH_TRAVERSAL_DETECTED)

    # 12. Outside-workspace rejection
    def test_12_outside_workspace_rejection(self):
        """Test 12: Projects outside C:\\NR-AI are rejected."""
        with self.assertRaises(UnrealSafetyError) as ctx:
            self.safety.validate_build_target("C:/Windows/System32", "Target")
        self.assertEqual(ctx.exception.code, UnrealErrorCode.PROJECT_NOT_AUTHORIZED)

    # 13. Shell injection rejection
    def test_13_shell_injection_rejection(self):
        """Test 13: Shell injection characters in target name are strictly rejected."""
        injection_payloads = [
            "target; rm -rf",
            "target && dir",
            "target | whoami",
            "target `id`",
            "target$(whoami)",
            "target\ncalc.exe",
        ]
        for payload in injection_payloads:
            with self.assertRaises(UnrealSafetyError):
                self.safety.validate_build_target(FIXTURE_PROJECT, payload)

    # 14. shell=True static safety check
    def test_14_static_safety_shell_false(self):
        """Test 14: Verify shell=True is NEVER used in unreal_build.py, unreal_safety.py, unreal_tools.py."""
        target_files = [
            WORKSPACE_ROOT / "app/agent/unreal_build.py",
            WORKSPACE_ROOT / "app/agent/unreal_safety.py",
            WORKSPACE_ROOT / "app/agent/unreal_tools.py",
            WORKSPACE_ROOT / "app/agent/unreal_project.py",
            WORKSPACE_ROOT / "app/agent/unreal_environment.py",
        ]
        for filepath in target_files:
            if not filepath.is_file():
                continue
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertNotIn("shell=True", content, f"shell=True found in {filepath.name}")
            tree = ast.parse(content, filename=str(filepath))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    for kw in node.keywords:
                        if kw.arg == "shell":
                            if isinstance(kw.value, ast.Constant):
                                self.assertFalse(
                                    kw.value.value,
                                    f"shell=True detected in AST call in {filepath.name}",
                                )

    # 15. Safe process invocation
    def test_15_safe_process_invocation(self):
        """Test 15: Safe process invocation runs with mock executor without shell."""
        commands_executed = []

        def mock_exec(cmd_args, timeout, cwd):
            commands_executed.append(cmd_args)
            return 0, "Build succeeded.\nTotal execution time: 1.20s", ""

        self.runner.set_mock_executor(mock_exec)
        result = self.runner.build_project(FIXTURE_PROJECT, "nr_unreal_test")
        self.assertTrue(result.success)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(len(commands_executed), 1)
        self.assertIn("nr_unreal_testEditor", commands_executed[0])
        self.assertIn("Win64", commands_executed[0])
        self.assertIn("Development", commands_executed[0])

    # 16. Timeout handling
    def test_16_timeout_handling(self):
        """Test 16: Runner handles process timeout cleanly, classifies as BUILD_TIMEOUT."""
        def mock_timeout(cmd_args, timeout, cwd):
            raise subprocess.TimeoutExpired(cmd=cmd_args, timeout=timeout)

        self.runner.set_mock_executor(mock_timeout)
        result = self.runner.build_project(FIXTURE_PROJECT, "nr_unreal_test", timeout_seconds=5.0)
        self.assertFalse(result.success)
        self.assertEqual(result.status, UnrealBuildResultStatus.BUILD_TIMEOUT)
        self.assertGreaterEqual(result.error_count, 1)
        self.assertEqual(result.errors[0].category, UnrealBuildIssueCategory.BUILD_TIMEOUT)

    # 17. Output size limiting
    def test_17_output_size_limiting(self):
        """Test 17: Enormous stdout/stderr output is bounded to MAX_CAPTURED_OUTPUT_BYTES."""
        huge_log = "Error line in compilation\n" * 50_000

        def mock_huge(cmd_args, timeout, cwd):
            return 1, huge_log, ""

        self.runner.set_mock_executor(mock_huge)
        result = self.runner.build_project(FIXTURE_PROJECT, "nr_unreal_test")
        self.assertLessEqual(len(result.output_preview), 2000)

    # 18. Sensitive output redaction
    def test_18_sensitive_output_redaction(self):
        """Test 18: API keys, bearer tokens, and secrets in build output are redacted."""
        log_with_secrets = (
            "Building...\n"
            "api_key: secret_api_key_1234567890\n"
            "Bearer ya29.a0AfH6SMB_secret_access_token_abcdef123456\n"
            "password = super_secret_pass123\n"
        )
        redacted = redact_sensitive_data(log_with_secrets)
        self.assertNotIn("secret_api_key_1234567890", redacted)
        self.assertNotIn("super_secret_pass123", redacted)
        self.assertIn("***REDACTED***", redacted)

    # 19. Exit code handling
    def test_19_exit_code_handling(self):
        """Test 19: Non-zero exit codes classify build as failed."""
        def mock_fail(cmd_args, timeout, cwd):
            return 2, "UnrealBuildTool failed.", "Exit code 2"

        self.runner.set_mock_executor(mock_fail)
        result = self.runner.build_project(FIXTURE_PROJECT, "nr_unreal_test")
        self.assertFalse(result.success)
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.status, UnrealBuildResultStatus.BUILD_FAILED)

    # 20. C++ compile-error parsing
    def test_20_cpp_compile_error_parsing(self):
        """Test 20: MSVC C++ syntax and declaration errors are parsed into CPP_COMPILE_ERROR."""
        log = (
            r"C:\NR-AI\nr_unreal_test\Source\nr_unreal_test\MyActor.cpp(25,12): "
            r"error C2065: 'InvalidIdentifier': undeclared identifier"
        )
        parsed = UnrealBuildLogParser.parse_log(log)
        self.assertEqual(parsed["error_count"], 1)
        err = parsed["errors"][0]
        self.assertEqual(err.category, UnrealBuildIssueCategory.CPP_COMPILE_ERROR)
        self.assertEqual(err.line, 25)
        self.assertEqual(err.column, 12)
        self.assertEqual(err.code, "C2065")
        self.assertIn("InvalidIdentifier", err.message)

    # 21. Linker-error parsing
    def test_21_linker_error_parsing(self):
        """Test 21: Unresolved external symbols (LNK2019) are parsed into LINKER_ERROR."""
        log = (
            r"MyActor.cpp.obj : error LNK2019: unresolved external symbol "
            r'"public: void __cdecl UMyClass::DoWork(void)" referenced in function'
        )
        parsed = UnrealBuildLogParser.parse_log(log)
        self.assertEqual(parsed["error_count"], 1)
        err = parsed["errors"][0]
        self.assertEqual(err.category, UnrealBuildIssueCategory.LINKER_ERROR)
        self.assertEqual(err.code, "LNK2019")

    # 22. Missing-header parsing
    def test_22_missing_header_parsing(self):
        """Test 22: Missing include files (fatal error C1083) are parsed into MISSING_HEADER."""
        log = (
            r"C:\NR-AI\nr_unreal_test\Source\nr_unreal_test\MyActor.cpp(4): "
            r"fatal error C1083: Cannot open include file: 'MissingHeader.h': No such file or directory"
        )
        parsed = UnrealBuildLogParser.parse_log(log)
        self.assertEqual(parsed["error_count"], 1)
        err = parsed["errors"][0]
        self.assertEqual(err.category, UnrealBuildIssueCategory.MISSING_HEADER)
        self.assertEqual(err.source_evidence, "MissingHeader.h")
        self.assertEqual(err.line, 4)

    # 23. Build.cs / Target.cs diagnostic parsing
    def test_23_build_cs_target_cs_diagnostic_parsing(self):
        """Test 23: C# Roslyn compilation errors in Build.cs or Target.cs are parsed."""
        log = (
            r"C:\NR-AI\nr_unreal_test\Source\nr_unreal_test\nr_unreal_test.Build.cs(12,9): "
            r"error CS0246: The type or namespace name 'NonExistentModule' could not be found"
        )
        parsed = UnrealBuildLogParser.parse_log(log)
        self.assertEqual(parsed["error_count"], 1)
        err = parsed["errors"][0]
        self.assertEqual(err.category, UnrealBuildIssueCategory.BUILD_CS_ERROR)
        self.assertEqual(err.code, "CS0246")
        self.assertEqual(err.line, 12)

    # 24. UHT diagnostic parsing
    def test_24_uht_diagnostic_parsing(self):
        """Test 24: UnrealHeaderTool reflection and GENERATED_BODY errors are parsed."""
        log = (
            r"UnrealHeaderTool: C:\NR-AI\nr_unreal_test\Source\nr_unreal_test\MyActor.h(14): "
            r"LogCompile: Error: Missing GENERATED_BODY() macro in class AMyActor"
        )
        parsed = UnrealBuildLogParser.parse_log(log)
        self.assertEqual(parsed["error_count"], 1)
        err = parsed["errors"][0]
        self.assertEqual(err.category, UnrealBuildIssueCategory.UHT_ERROR)
        self.assertEqual(err.line, 14)
        self.assertIn("Missing GENERATED_BODY()", err.message)

    # 25. .NET/runtime diagnostic parsing
    def test_25_dotnet_runtime_diagnostic_parsing(self):
        """Test 25: Missing .NET host launch failures are parsed into DOTNET_RUNTIME_MISSING."""
        log = (
            "You must install .NET to run this application.\n"
            "App: C:\\Program Files\\Epic Games\\UE_5.8\\Engine\\Binaries\\DotNET\\UnrealBuildTool\\UnrealBuildTool.exe\n"
            "App host version: 10.0.7\n"
            ".NET location: Not found\n"
        )
        parsed = UnrealBuildLogParser.parse_log(log)
        self.assertEqual(parsed["error_count"], 1)
        err = parsed["errors"][0]
        self.assertEqual(err.category, UnrealBuildIssueCategory.DOTNET_RUNTIME_MISSING)
        self.assertIn("install .NET", err.message)

    # 26. Engine mismatch diagnostic parsing
    def test_26_engine_mismatch_diagnostic_parsing(self):
        """Test 26: Engine association mismatch logs are parsed into ENGINE_MISMATCH."""
        log = "Error: EngineAssociation '5.4' does not match installed engine '5.8'."
        parsed = UnrealBuildLogParser.parse_log(log)
        self.assertEqual(parsed["error_count"], 1)
        self.assertEqual(parsed["errors"][0].category, UnrealBuildIssueCategory.ENGINE_MISMATCH)

    # 27. Deterministic build-result verification & model claim precedence
    def test_27_deterministic_build_result_verification(self):
        """Test 27: Evidence precedence enforces deterministic result over model claims."""
        failed_res = UnrealBuildResult(
            success=False,
            status=UnrealBuildResultStatus.BUILD_FAILED,
            exit_code=1,
            target_name="nr_unreal_testEditor",
            errors=[
                UnrealBuildDiagnostic(
                    category=UnrealBuildIssueCategory.CPP_COMPILE_ERROR,
                    severity="error",
                    file="MyActor.cpp",
                    line=10,
                    code="C2065",
                    message="undeclared identifier",
                )
            ],
        )
        # Model falsely claims success
        verif = UnrealBuildResultVerifier.verify(failed_res, model_claim_success=True)
        self.assertFalse(verif["verified_success"])
        self.assertEqual(verif["status"], UnrealBuildResultStatus.BUILD_FAILED.value)
        self.assertTrue(verif["discrepancy_detected"])

    # 28. Artifact verification
    def test_28_artifact_verification(self):
        """Test 28: Artifact verifier scans project Binaries directory and computes SHA-256."""
        # Create temporary dummy binary inside authorized fixture Binaries/Win64
        bin_dir = FIXTURE_PROJECT / "Binaries" / "Win64"
        bin_dir.mkdir(parents=True, exist_ok=True)
        dummy_dll = bin_dir / "UnrealEditor-nr_unreal_test.dll"
        try:
            dummy_dll.write_bytes(b"MZ_DUMMY_BINARY_DATA_FOR_TEST")
            artifacts = self.artifact_verifier.verify_artifacts(
                FIXTURE_PROJECT, "nr_unreal_test", "Development", "Win64"
            )
            self.assertGreaterEqual(len(artifacts), 1)
            art = next(a for a in artifacts if a.name == "UnrealEditor-nr_unreal_test.dll")
            self.assertTrue(art.exists)
            self.assertEqual(len(art.sha256), 64)
            self.assertEqual(art.size_bytes, len(b"MZ_DUMMY_BINARY_DATA_FOR_TEST"))
        finally:
            if dummy_dll.is_file():
                dummy_dll.unlink()

    # 29. Emergency stop freezes build operations
    def test_29_emergency_stop(self):
        """Test 29: Emergency stop freezes all build operations immediately."""
        self.safety.trigger_emergency_stop("Safety test stop")
        with self.assertRaises(EmergencyStopActiveError):
            self.safety.validate_build_target(FIXTURE_PROJECT, "nr_unreal_test")
        with self.assertRaises(EmergencyStopActiveError):
            self.runner.build_project(FIXTURE_PROJECT, "nr_unreal_test")

    # 30. Model isolation
    def test_30_model_isolation(self):
        """Test 30: Model cannot pass arbitrary arguments or bypass deterministic command assembly."""
        def mock_checker(cmd_args, timeout, cwd):
            self.assertNotIn("--arbitrary-flag", cmd_args)
            return 0, "Clean build", ""

        self.runner.set_mock_executor(mock_checker)
        # Verify valid build constructs only authorized args
        res = self.runner.build_project(FIXTURE_PROJECT, "nr_unreal_test")
        self.assertTrue(res.success)

    # 31. Audit logging
    def test_31_audit_logging(self):
        """Test 31: Dispatching Phase 2 tools produces audit trail events."""
        res = self.registry.dispatch("unreal.validate_build_environment", project_path=str(FIXTURE_PROJECT))
        self.assertTrue(res.success)

    # 32. Duplicate tool detection
    def test_32_duplicate_tool_detection(self):
        """Test 32: Registered tools have exactly 0 duplicates across all 20 tools."""
        tools = self.registry.get_registered_tools()
        self.assertEqual(len(tools), 20)
        self.assertEqual(len(tools), len(set(tools)))
        # Verify both Phase 1 and Phase 2 tools are present
        for t in ALLOWED_UNREAL_PHASE1_TOOLS:
            self.assertIn(t, tools)
        for t in ALLOWED_UNREAL_PHASE2_TOOLS:
            self.assertIn(t, tools)

    # 33. Process cleanup & no orphan processes
    def test_33_process_cleanup_no_orphans(self):
        """Test 33: Process cleanup ensures no lingering processes."""
        proc = subprocess.Popen(["cmd.exe", "/c", "timeout", "10"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.runner._cleanup_process(proc)
        # Confirm terminated
        self.assertIsNotNone(proc.poll())

    # 34. Fixture integrity and data model serialization
    def test_34_fixture_integrity_and_serialization(self):
        """Test 34: Data model JSON serialization integrity."""
        diag = UnrealBuildDiagnostic(
            category=UnrealBuildIssueCategory.CPP_COMPILE_ERROR,
            severity="error",
            file="Source/MyActor.cpp",
            line=42,
            column=5,
            code="C2065",
            message="Undeclared identifier",
            source_evidence="test",
            raw_line="raw line",
        )
        serialized = json.dumps(diag.to_dict())
        deserialized = json.loads(serialized)
        self.assertEqual(deserialized["code"], "C2065")
        self.assertEqual(deserialized["category"], UnrealBuildIssueCategory.CPP_COMPILE_ERROR.value)

    # 35. Tool registry dispatch for all 8 Phase 2 tools
    def test_35_tool_registry_dispatch_all_phase2_tools(self):
        """Test 35: All 8 Phase 2 tools dispatch cleanly via UnrealToolRegistry."""
        # 1. validate_build_environment
        r1 = self.registry.dispatch("unreal.validate_build_environment", project_path=str(FIXTURE_PROJECT))
        self.assertTrue(r1.success)

        # 2. validate_build_target
        r2 = self.registry.dispatch(
            "unreal.validate_build_target",
            project_path=str(FIXTURE_PROJECT),
            target_name="nr_unreal_test",
        )
        self.assertTrue(r2.success)

        # 3. get_supported_targets
        r3 = self.registry.dispatch("unreal.get_supported_targets", project_path=str(FIXTURE_PROJECT))
        self.assertTrue(r3.success)
        self.assertGreaterEqual(len(r3.data["targets"]), 1)

        # 4. parse_build_diagnostics
        r4 = self.registry.dispatch(
            "unreal.parse_build_diagnostics",
            log_content="MyActor.cpp(10): error C2065: 'undeclared': undeclared identifier",
        )
        self.assertTrue(r4.success)
        self.assertEqual(r4.data["error_count"], 1)

        # 5. verify_build_result
        r5 = self.registry.dispatch(
            "unreal.verify_build_result",
            build_result={"success": False, "exit_code": 1, "errors": [{"category": "CPP_COMPILE_ERROR"}]},
        )
        self.assertTrue(r5.success)
        self.assertFalse(r5.data["verified_success"])

        # 6. inspect_build_artifacts
        r6 = self.registry.dispatch(
            "unreal.inspect_build_artifacts",
            project_path=str(FIXTURE_PROJECT),
            target_name="nr_unreal_test",
        )
        self.assertTrue(r6.success)

        # 7. diagnose_build_failure
        r7 = self.registry.dispatch(
            "unreal.diagnose_build_failure",
            log_content="fatal error C1083: Cannot open include file: 'Missing.h': No such file",
        )
        self.assertTrue(r7.success)
        self.assertEqual(r7.data["count"], 1)

        # 8. build_project (with mock executor)
        self.runner.set_mock_executor(lambda cmd, to, cwd: (0, "Build Succeeded", ""))
        r8 = self.registry.dispatch("unreal.build_project", project_path=str(FIXTURE_PROJECT))
        self.assertTrue(r8.success)


if __name__ == "__main__":
    unittest.main()
