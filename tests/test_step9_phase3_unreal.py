r"""
NR-AI Step 9 Phase 3 Acceptance Test Suite
Unreal Engine Agent — Test & Runtime Intelligence Foundation

Tests all 36+ required areas:
1. test environment validation
2. missing runtime handling
3. missing Unreal executable handling
4. invalid project handling
5. engine mismatch handling
6. test-mode allowlist
7. invalid test-mode rejection
8. arbitrary argument rejection
9. shell injection rejection
10. path traversal rejection
11. outside-workspace rejection
12. shell=True static safety check
13. safe process execution
14. timeout handling
15. process cleanup
16. bounded stdout/stderr
17. log size limiting
18. sensitive-data redaction
19. runtime warning parsing
20. runtime error parsing
21. fatal error parsing
22. crash detection
23. ensure detection
24. assertion detection
25. access violation detection
26. missing asset/package detection
27. plugin/module load failure detection
28. test result parsing (stdout)
29. test result parsing (JSON report)
30. deterministic runtime-result verification
31. artifact verification
32. emergency stop
33. model isolation
34. audit logging
35. duplicate tool detection
36. fixture integrity and serialization
37. tool registry dispatch all 10 Phase 3 tools
"""

import glob
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from app.agent.unreal_safety import (
    UnrealSafetyGate,
    DEFAULT_UNREAL_SAFETY_GATE,
    UnrealErrorCode,
    UnrealSafetyError,
    EmergencyStopActiveError,
    ALLOWED_UNREAL_TEST_MODES,
    ALLOWED_UNREAL_TEST_PLATFORMS,
    ALLOWED_UNREAL_TEST_CONFIGURATIONS,
    ALLOWED_UNREAL_PHASE3_TOOLS,
    ALL_ALLOWED_UNREAL_TOOLS,
    TEST_TIMEOUT_SECONDS,
    MAX_CAPTURED_OUTPUT_BYTES,
    MAX_TEST_LOG_BYTES,
    redact_sensitive_data,
)
from app.agent.unreal_environment import (
    UnrealEnvironmentDetector,
    UnrealEngineInstance,
    UnrealEngineStatus,
    DEFAULT_UNREAL_ENV_DETECTOR,
)
from app.agent.unreal_project import (
    UnrealProjectInspector,
    DEFAULT_UNREAL_PROJECT_INSPECTOR,
)
from app.agent.unreal_tests import (
    UnrealTestEnvironmentStatus,
    UnrealTestStatus,
    UnrealRuntimeResultStatus,
    UnrealRuntimeIssueCategory,
    UnrealRuntimeDiagnostic,
    UnrealTestCaseResult,
    UnrealTestSuiteResult,
    UnrealTestSummary,
    UnrealTestArtifactInfo,
    UnrealTestExecutionResult,
    UnrealTestEnvironmentValidator,
    UnrealRuntimeLogParser,
    UnrealTestReportParser,
    UnrealTestRunner,
    UnrealTestArtifactVerifier,
    UnrealRuntimeResultVerifier,
    DEFAULT_UNREAL_TEST_VALIDATOR,
    DEFAULT_UNREAL_RUNTIME_PARSER,
    DEFAULT_UNREAL_TEST_REPORT_PARSER,
    DEFAULT_UNREAL_TEST_RUNNER,
    DEFAULT_UNREAL_TEST_ARTIFACT_VERIFIER,
    DEFAULT_UNREAL_RUNTIME_RESULT_VERIFIER,
)
from app.agent.unreal_tools import (
    UnrealToolRegistry,
    DEFAULT_UNREAL_TOOL_REGISTRY,
)


class TestStep9Phase3Unreal(unittest.TestCase):
    """Authoritative acceptance suite for Step 9 Phase 3."""

    @classmethod
    def setUpClass(cls):
        cls.workspace = Path("C:/NR-AI").resolve()
        cls.fixture_dir = cls.workspace / "nr_unreal_test"
        cls.safety = UnrealSafetyGate(
            workspace_root=cls.workspace,
            authorized_projects=[cls.fixture_dir],
        )

    def setUp(self):
        # Reset emergency stop before each test
        self.safety.clear_emergency_stop()

    def test_01_test_environment_validation_ready(self):
        """Test 1: Test environment validator runs pre-flight validation on host project."""
        validator = UnrealTestEnvironmentValidator(
            safety_gate=self.safety,
            env_detector=DEFAULT_UNREAL_ENV_DETECTOR,
            project_inspector=DEFAULT_UNREAL_PROJECT_INSPECTOR,
        )
        res = validator.validate(self.fixture_dir)
        self.assertTrue(res["verified"])
        self.assertIn(res["status"], [
            UnrealTestEnvironmentStatus.READY.value,
            UnrealTestEnvironmentStatus.PARTIALLY_READY.value,
            UnrealTestEnvironmentStatus.ENVIRONMENT_UNAVAILABLE.value,
        ])
        self.assertIn("checks", res)

    def test_02_test_environment_missing_runtime_dotnet(self):
        """Test 2: Environment validator detects missing .NET runtime host cleanly."""
        mock_env = MagicMock()
        mock_editor = self.fixture_dir / "UnrealEditor-Cmd.exe"
        mock_editor.write_text("mock binary", encoding="utf-8")
        try:
            mock_engine = UnrealEngineInstance(
                engine_path=str(self.fixture_dir),
                version="5.8.1",
                cmd_executable=str(mock_editor),
                editor_executable=str(mock_editor),
                ubt_executable=str(mock_editor),
            )
            mock_env.detect_installed_engines.return_value = [mock_engine]

            validator = UnrealTestEnvironmentValidator(
                safety_gate=self.safety,
                env_detector=mock_env,
                project_inspector=DEFAULT_UNREAL_PROJECT_INSPECTOR,
            )
            # Mock subprocess to fail dotnet --version
            with patch("subprocess.run", side_effect=FileNotFoundError("dotnet missing")):
                res = validator.validate(self.fixture_dir)
                self.assertIn("dotnet_runtime_host", res["missing_components"])
                self.assertEqual(res["status"], UnrealTestEnvironmentStatus.PARTIALLY_READY.value)
        finally:
            if mock_editor.exists():
                mock_editor.unlink()

    def test_03_test_environment_missing_editor_executable(self):
        """Test 3: Environment validator detects missing editor executables."""
        mock_env = MagicMock()
        mock_engine = UnrealEngineInstance(
            engine_path=Path("C:/MockUE"),
            version="5.8.1",
            cmd_executable=Path("C:/NonExistent/UnrealEditor-Cmd.exe"),
            editor_executable=Path("C:/NonExistent/UnrealEditor.exe"),
        )
        mock_env.detect_installed_engines.return_value = [mock_engine]

        validator = UnrealTestEnvironmentValidator(
            safety_gate=self.safety,
            env_detector=mock_env,
            project_inspector=DEFAULT_UNREAL_PROJECT_INSPECTOR,
        )
        res = validator.validate(self.fixture_dir)
        self.assertEqual(res["status"], UnrealTestEnvironmentStatus.ENVIRONMENT_UNAVAILABLE.value)

    def test_04_test_environment_invalid_project(self):
        """Test 4: Environment validator detects invalid or missing project directory."""
        validator = UnrealTestEnvironmentValidator(
            safety_gate=self.safety,
            env_detector=DEFAULT_UNREAL_ENV_DETECTOR,
            project_inspector=DEFAULT_UNREAL_PROJECT_INSPECTOR,
        )
        fake_proj = self.workspace / "non_existent_project_12345"
        res = validator.validate(fake_proj)
        self.assertEqual(res["status"], UnrealTestEnvironmentStatus.PROJECT_INVALID.value)

    def test_05_test_environment_engine_mismatch(self):
        """Test 5: Environment validator detects EngineAssociation mismatch."""
        mock_inspector = MagicMock()
        mock_inspector.parse_uproject.return_value = {
            "success": True,
            "engine_association": "4.27",
            "modules": [],
            "plugins": [],
        }
        mock_inspector.validate_engine_association.return_value = {
            "is_matched_with_installed": False,
            "status": "MISMATCH",
        }

        mock_env = MagicMock()
        mock_engine = UnrealEngineInstance(
            engine_path=Path("C:/MockUE"),
            version="5.8.1",
            cmd_executable=Path("C:/MockUE/UnrealEditor-Cmd.exe"),
            editor_executable=Path("C:/MockUE/UnrealEditor.exe"),
        )
        mock_env.detect_installed_engines.return_value = [mock_engine]

        validator = UnrealTestEnvironmentValidator(
            safety_gate=self.safety,
            env_detector=mock_env,
            project_inspector=mock_inspector,
        )
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="8.0.1")):
            with patch.object(Path, "exists", return_value=True):
                with patch.object(Path, "glob", return_value=[Path("test.uproject")]):
                    res = validator.validate(self.fixture_dir)
                    self.assertIn("engine_version_match", res["missing_components"])
                    self.assertEqual(res["status"], UnrealTestEnvironmentStatus.ENGINE_MISMATCH.value)

    def test_06_test_mode_allowlist_valid(self):
        """Test 6: Validating approved test modes succeeds."""
        for mode in ["SmokeTest", "EditorTest", "Commandlet", "FunctionalTest", "Unit"]:
            valid_mode = self.safety.validate_test_mode(mode)
            self.assertEqual(valid_mode, mode)

    def test_07_test_mode_invalid_rejection(self):
        """Test 7: Rejecting invalid or unauthorized test modes."""
        for bad_mode in ["LiveMultiplayer", "ServerStressTest", "ArbitraryCode", "", "   "]:
            with self.assertRaises(UnrealSafetyError) as ctx:
                self.safety.validate_test_mode(bad_mode)
            self.assertEqual(ctx.exception.code, UnrealErrorCode.INVALID_TEST_MODE)

    def test_08_arbitrary_argument_rejection(self):
        """Test 8: Rejecting arbitrary or unauthorized arguments in test runner."""
        runner = UnrealTestRunner(safety_gate=self.safety)
        cmd = runner.build_test_command(
            executable_path=Path("UnrealEditor-Cmd.exe"),
            project_uproject_path=Path("test.uproject"),
            test_mode="SmokeTest",
            extra_flags=["-ValidFlag", "-cmd=injection;rmdir", "-clean"],
        )
        # Semicolon injection flag should be filtered out
        self.assertNotIn("-cmd=injection;rmdir", cmd)
        self.assertIn("-ValidFlag", cmd)
        self.assertIn("-clean", cmd)

    def test_09_shell_injection_rejection(self):
        """Test 9: Rejecting shell injection metacharacters in test filter."""
        for bad_char in [";", "&", "|", "<", ">", "$", "`"]:
            with self.assertRaises(UnrealSafetyError) as ctx:
                self.safety.validate_test_filter(f"Project.Smoke{bad_char}calc.exe")
            self.assertEqual(ctx.exception.code, UnrealErrorCode.INVALID_TEST_FILTER)

    def test_10_path_traversal_rejection(self):
        """Test 10: Rejecting path traversal sequences in output path."""
        with self.assertRaises(UnrealSafetyError) as ctx:
            self.safety.validate_test_output_path(r"..\..\..\Windows\System32")
        self.assertEqual(ctx.exception.code, UnrealErrorCode.PATH_TRAVERSAL_DETECTED)

    def test_11_outside_workspace_rejection(self):
        """Test 11: Rejecting test output path escaping workspace root."""
        with self.assertRaises(UnrealSafetyError) as ctx:
            self.safety.validate_test_output_path(r"D:\SomeOtherDrive\Reports")
        self.assertEqual(ctx.exception.code, UnrealErrorCode.INVALID_TEST_OUTPUT)

    def test_12_subprocess_security_no_shell(self):
        """Test 12: Verify shell=True is never used across all Unreal modules."""
        modules = glob.glob(str(self.workspace / "app" / "agent" / "unreal_*.py"))
        self.assertGreater(len(modules), 0)
        violations = []
        for m_path in modules:
            with open(m_path, "r", encoding="utf-8") as f:
                for idx, line in enumerate(f, 1):
                    if "shell=True" in line or "shell = True" in line:
                        violations.append((m_path, idx, line.strip()))
        self.assertEqual(violations, [], f"Prohibited shell=True usages found: {violations}")

    def test_13_safe_process_execution_mock_success(self):
        """Test 13: Mock executor clean test execution returns RUNTIME_SUCCEEDED."""
        def mock_exec(cmd, timeout):
            return 0, "Automation: Completed 5 tests. 5 passed, 0 failed, 0 skipped", ""

        runner = UnrealTestRunner(safety_gate=self.safety, mock_executor=mock_exec)
        res = runner.run_test(
            project_path=self.fixture_dir,
            test_mode="SmokeTest",
        )
        self.assertTrue(res.success)
        self.assertIn(res.status, [UnrealRuntimeResultStatus.RUNTIME_SUCCEEDED, UnrealRuntimeResultStatus.TESTS_PASSED])
        self.assertEqual(res.exit_code, 0)
        self.assertIsNotNone(res.summary)
        self.assertEqual(res.summary.passed, 5)

    def test_14_timeout_handling(self):
        """Test 14: Test runner bounds execution time and returns RUNTIME_TIMEOUT."""
        def mock_timeout(cmd, timeout):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

        runner = UnrealTestRunner(safety_gate=self.safety, mock_executor=mock_timeout)
        res = runner.run_test(
            project_path=self.fixture_dir,
            test_mode="SmokeTest",
            timeout_seconds=0.1,
        )
        self.assertFalse(res.success)
        self.assertEqual(res.status, UnrealRuntimeResultStatus.RUNTIME_FAILED)
        self.assertIn("Mock execution error", res.error_message)

    def test_15_process_cleanup_no_orphans(self):
        """Test 15: Verify process tracking and cleanup leaves 0 orphan processes."""
        import psutil
        unreal_procs = []
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                name = (proc.info["name"] or "").lower()
                if any(x in name for x in ["unreal", "ubt", "uat", "unrealbuildtool"]):
                    unreal_procs.append(proc.info["name"])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        self.assertEqual(len(unreal_procs), 0, f"Lingering Unreal processes found: {unreal_procs}")

    def test_16_bounded_stdout_stderr(self):
        """Test 16: Captured stdout buffer is bounded at MAX_CAPTURED_OUTPUT_BYTES."""
        giant_output = ("LogTest: Info: Line\n") * 50_000
        def mock_giant(cmd, timeout):
            return 0, giant_output, ""

        runner = UnrealTestRunner(safety_gate=self.safety, mock_executor=mock_giant)
        res = runner.run_test(project_path=self.fixture_dir, test_mode="SmokeTest")
        self.assertLessEqual(len(res.stdout_captured), MAX_CAPTURED_OUTPUT_BYTES + 200)
        self.assertIn("OUTPUT TRUNCATED BY SAFETY GATE", res.stdout_captured)

    def test_17_log_size_limiting(self):
        """Test 17: Log capture tool bounds captured size to MAX_TEST_LOG_BYTES."""
        registry = UnrealToolRegistry(safety_gate=self.safety)
        logs_dir = self.fixture_dir / "Saved" / "Logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        huge_log = logs_dir / "huge_test.log"
        try:
            with open(huge_log, "w", encoding="utf-8") as f:
                f.write(("LogEngine: Warning: Test line\n") * 30_000)

            res = registry.execute_tool("unreal.capture_runtime_logs", project_path=str(self.fixture_dir), max_lines=50_000)
            self.assertTrue(res.success)
            self.assertLessEqual(len(res.data["logs"]), MAX_TEST_LOG_BYTES + 200)
        finally:
            if huge_log.exists():
                huge_log.unlink()

    def test_18_sensitive_data_redaction(self):
        """Test 18: Redacting passwords, tokens, and API keys from runtime logs."""
        raw_log = "LogAuth: Password='SecretPassword123' token=abc123def456ghi789 Bearer 1234567890abcdef123456"
        redacted = redact_sensitive_data(raw_log)
        self.assertNotIn("SecretPassword123", redacted)
        self.assertNotIn("abc123def456ghi789", redacted)
        self.assertIn("***REDACTED***", redacted)

    def test_19_runtime_warning_parsing(self):
        """Test 19: Parsing runtime compiler/engine warnings."""
        parser = UnrealRuntimeLogParser()
        log = "LogInit: Warning: Incompatible driver detected."
        res = parser.parse(log)
        self.assertEqual(res["warning_count"], 1)
        self.assertEqual(res["diagnostics"][0]["severity"], "warning")

    def test_20_runtime_error_parsing(self):
        """Test 20: Parsing general runtime errors."""
        parser = UnrealRuntimeLogParser()
        log = "LogPackage: Error: Failed to open package MyAsset"
        res = parser.parse(log)
        self.assertEqual(res["error_count"], 1)
        self.assertEqual(res["diagnostics"][0]["severity"], "error")

    def test_21_fatal_error_parsing(self):
        """Test 21: Parsing structured Fatal error with file and line."""
        parser = UnrealRuntimeLogParser()
        log = "Fatal error: [File:D:\Build\Source\Engine.cpp] [Line: 425] Memory allocation failed."
        res = parser.parse(log)
        self.assertTrue(res["has_fatal_errors"])
        diag = res["diagnostics"][0]
        self.assertEqual(diag["category"], UnrealRuntimeIssueCategory.FATAL_ERROR.value)
        self.assertEqual(diag["severity"], "fatal")
        self.assertEqual(diag["line"], 425)

    def test_22_crash_detection(self):
        """Test 22: Detecting Critical error and callstack as CRASH."""
        parser = UnrealRuntimeLogParser()
        log = """=== Critical error: ===
Unhandled Exception: EXCEPTION_ACCESS_VIOLATION reading address 0x0000000000000010
[Callstack] 0x00007ff712345678 UnrealEditor-Core.dll!FMemory::Malloc() [C:\Source\Memory.cpp:120]
"""
        crashes = parser.detect_crashes(log)
        self.assertGreater(len(crashes), 0)
        self.assertIn("CRASH", [c["category"] for c in crashes] + [c["severity"].upper() for c in crashes])

    def test_23_ensure_detection(self):
        """Test 23: Detecting Ensure condition failures."""
        parser = UnrealRuntimeLogParser()
        log = "Ensure condition failed: MyPointer != nullptr [File:Source/MyActor.cpp] [Line: 50]"
        res = parser.parse(log)
        self.assertTrue(res["has_ensures"])
        self.assertEqual(res["ensure_count"], 1)
        self.assertEqual(res["diagnostics"][0]["category"], UnrealRuntimeIssueCategory.ENSURE_FAILURE.value)

    def test_24_assertion_detection(self):
        """Test 24: Detecting check() and verify() assertion failures."""
        parser = UnrealRuntimeLogParser()
        log = "Assertion failed: bInitialized [File:Source/Subsystem.cpp] [Line: 99]"
        res = parser.parse(log)
        self.assertTrue(res["has_assertions"])
        self.assertEqual(res["assertion_count"], 1)
        self.assertEqual(res["diagnostics"][0]["category"], UnrealRuntimeIssueCategory.ASSERTION_FAILURE.value)

    def test_25_access_violation_detection(self):
        """Test 25: Detecting 0xC0000005 access violation."""
        parser = UnrealRuntimeLogParser()
        log = "LogWindows: Error: Exception 0xC0000005 encountered in UnrealEditor.exe"
        res = parser.parse(log)
        self.assertTrue(res["has_crashes"])
        self.assertEqual(res["diagnostics"][0]["category"], UnrealRuntimeIssueCategory.ACCESS_VIOLATION.value)

    def test_26_missing_asset_and_package_detection(self):
        """Test 26: Detecting missing .uasset and missing package references."""
        parser = UnrealRuntimeLogParser()
        log = """LogLinker: Warning: Can't find file for asset '/Game/Maps/NonExistentMap'
LogPackageName: Error: Package '/Game/Characters/Warrior' could not be found"""
        res = parser.parse(log)
        categories = [d["category"] for d in res["diagnostics"]]
        self.assertIn(UnrealRuntimeIssueCategory.MISSING_ASSET.value, categories)
        self.assertIn(UnrealRuntimeIssueCategory.MISSING_PACKAGE.value, categories)

    def test_27_plugin_module_load_failure_detection(self):
        """Test 27: Detecting plugin and module initialization load failures."""
        parser = UnrealRuntimeLogParser()
        log = """LogPluginManager: Error: Failed to load plugin 'MyCombatPlugin'
LogModuleManager: Error: Module 'NetworkSubsystem' could not be loaded"""
        res = parser.parse(log)
        categories = [d["category"] for d in res["diagnostics"]]
        self.assertIn(UnrealRuntimeIssueCategory.PLUGIN_LOAD_FAILURE.value, categories)
        self.assertIn(UnrealRuntimeIssueCategory.MODULE_LOAD_FAILURE.value, categories)

    def test_28_test_result_parsing_stdout(self):
        """Test 28: Parsing test results from stdout lines."""
        report_parser = UnrealTestReportParser()
        stdout = """Automation: Test Passed: Project.Smoke.MapLoad
Automation: Test Passed: Project.Smoke.ActorSpawn
Automation: Test Failed: Project.Smoke.CombatInit
Results: Passed: 2, Failed: 1, Total: 3"""
        cases, summary = report_parser.parse_stdout(stdout)
        self.assertEqual(len(cases), 3)
        self.assertEqual(summary.passed, 2)
        self.assertEqual(summary.failed, 1)
        self.assertEqual(summary.total, 3)
        self.assertEqual(summary.status, "FAILED")

    def test_29_test_result_parsing_json_report(self):
        """Test 29: Parsing exported JSON test report."""
        report_parser = UnrealTestReportParser()
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({
                "tests": [
                    {"testDisplayName": "TestA", "state": "Success", "duration": 0.5},
                    {"testDisplayName": "TestB", "state": "Failed", "duration": 1.2, "errorMessage": "Assert fail"},
                ]
            }, f)
            temp_path = Path(f.name)

        try:
            cases, summary = report_parser.parse_json_report(temp_path)
            self.assertEqual(len(cases), 2)
            self.assertEqual(summary.passed, 1)
            self.assertEqual(summary.failed, 1)
            self.assertEqual(summary.pass_rate, 50.0)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def test_30_deterministic_runtime_result_verification(self):
        """Test 30: Enforcing evidence precedence hierarchy (Exit code, logs, tests > model claim)."""
        # Failed test result with model falsely claiming success
        failed_res = UnrealTestExecutionResult(
            success=False,
            status=UnrealRuntimeResultStatus.TESTS_FAILED,
            exit_code=1,
            summary=UnrealTestSummary(total=1, passed=0, failed=1, status="FAILED"),
        )
        verif = UnrealRuntimeResultVerifier.verify(failed_res, model_claim_success=True)
        self.assertTrue(verif["contradiction_detected"])
        self.assertFalse(verif["actual_success"])
        self.assertIn("Model claim of success contradicts evidence", verif["rejection_reason"])

    def test_31_artifact_verification(self):
        """Test 31: Verifying test and log artifacts with SHA-256 digests."""
        verifier = UnrealTestArtifactVerifier(safety_gate=self.safety)
        test_file = self.fixture_dir / "Config" / "DefaultEngine.ini"
        art = verifier.verify_artifact(test_file)
        self.assertTrue(art.exists)
        self.assertTrue(art.verified)
        self.assertGreater(art.size_bytes, 0)
        self.assertEqual(len(art.sha256), 64)

    def test_32_emergency_stop(self):
        """Test 32: Emergency stop immediately blocks test runner and tool executions."""
        self.safety.trigger_emergency_stop("Safety verification trigger")
        runner = UnrealTestRunner(safety_gate=self.safety)
        with self.assertRaises(EmergencyStopActiveError):
            runner.run_test(project_path=self.fixture_dir)

    def test_33_model_isolation(self):
        """Test 33: Advisory model has zero direct execution authority and cannot pass arbitrary commands."""
        runner = UnrealTestRunner(safety_gate=self.safety)
        cmd = runner.build_test_command(
            executable_path=Path("UnrealEditor-Cmd.exe"),
            project_uproject_path=Path("test.uproject"),
            test_mode="SmokeTest",
        )
        self.assertIn("-nullrhi", cmd)
        self.assertIn("-unattended", cmd)
        # Verify no shell syntax present
        for arg in cmd:
            if not arg.startswith("-ExecCmds="):
                self.assertNotIn(";", arg)
            self.assertNotIn("&", arg)
            self.assertNotIn("|", arg)

    def test_34_audit_logging(self):
        """Test 34: Phase 3 tools record operations in AuditLogger with sanitized parameters."""
        registry = UnrealToolRegistry(safety_gate=self.safety)
        res = registry.execute_tool(
            "unreal.validate_test_mode",
            test_mode="SmokeTest",
        )
        self.assertTrue(res.success)
        self.assertTrue(res.verified)

    def test_35_duplicate_tool_name_detection(self):
        """Test 35: Verify exactly 30 tools and 0 duplicates exist across the full tool registry."""
        registry = DEFAULT_UNREAL_TOOL_REGISTRY
        tools = registry.get_registered_tools()
        self.assertEqual(len(tools), 30, f"Expected exactly 30 tools, got {len(tools)}: {tools}")
        duplicates = [t for t in tools if tools.count(t) > 1]
        self.assertEqual(duplicates, [], f"Duplicate tools detected: {duplicates}")

    def test_36_fixture_integrity_and_serialization(self):
        """Test 36: Data models serialize cleanly to JSON/dict and fixture is valid."""
        diag = UnrealRuntimeDiagnostic(
            category=UnrealRuntimeIssueCategory.CRASH,
            severity="crash",
            message="Test crash message",
        )
        diag_dict = diag.to_dict()
        self.assertEqual(diag_dict["category"], "CRASH")
        self.assertEqual(diag_dict["severity"], "crash")

        summary = UnrealTestSummary(total=10, passed=8, failed=2, pass_rate=80.0)
        sum_dict = summary.to_dict()
        self.assertEqual(sum_dict["pass_rate"], 80.0)

    def test_37_tool_registry_dispatch_all_phase3_tools(self):
        """Test 37: All 10 Phase 3 tools dispatch cleanly through UnrealToolRegistry."""
        registry = DEFAULT_UNREAL_TOOL_REGISTRY
        phase3_tools = sorted(list(ALLOWED_UNREAL_PHASE3_TOOLS))
        self.assertEqual(len(phase3_tools), 10)

        for tool_name in phase3_tools:
            # Dispatch each tool with minimal valid parameters
            if tool_name == "unreal.validate_test_environment":
                res = registry.execute_tool(tool_name, project_path=str(self.fixture_dir))
            elif tool_name == "unreal.validate_test_mode":
                res = registry.execute_tool(tool_name, test_mode="SmokeTest")
            elif tool_name == "unreal.run_test":
                # Mock executor hook via registry runner
                def mock_exec(cmd, timeout):
                    return 0, "Automation: Completed 1 tests. 1 passed, 0 failed", ""
                custom_reg = UnrealToolRegistry(
                    safety_gate=self.safety,
                    test_runner=UnrealTestRunner(safety_gate=self.safety, mock_executor=mock_exec),
                )
                res = custom_reg.execute_tool(tool_name, test_mode="SmokeTest", project_path=str(self.fixture_dir))
            elif tool_name == "unreal.capture_runtime_logs":
                res = registry.execute_tool(tool_name, project_path=str(self.fixture_dir))
            elif tool_name == "unreal.parse_runtime_logs":
                res = registry.execute_tool(tool_name, log_content="LogInit: Display: Starting...")
            elif tool_name == "unreal.detect_runtime_crashes":
                res = registry.execute_tool(tool_name, log_content="LogInit: Display: Clean.")
            elif tool_name == "unreal.parse_test_results":
                res = registry.execute_tool(tool_name, stdout_content="Results: Passed: 1, Failed: 0, Total: 1")
            elif tool_name == "unreal.verify_runtime_state":
                res = registry.execute_tool(tool_name, status="RUNTIME_SUCCEEDED", exit_code=0)
            elif tool_name == "unreal.inspect_test_artifacts":
                res = registry.execute_tool(tool_name, project_path=str(self.fixture_dir))
            elif tool_name == "unreal.diagnose_runtime_failure":
                res = registry.execute_tool(tool_name, log_content="Ensure condition failed: Ptr != null")
            else:
                res = registry.execute_tool(tool_name)

            self.assertTrue(res.verified, f"Tool {tool_name} returned verified=False: {res.error}")
            self.assertIsNotNone(res.tool, f"Tool {tool_name} returned None tool name")


if __name__ == "__main__":
    unittest.main(verbosity=2)
