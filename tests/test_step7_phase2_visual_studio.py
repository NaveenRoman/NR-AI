"""
Acceptance Test Suite for NR-AI Step 7 Phase 2:
Visual Studio Build, Debug, Runtime & Test Intelligence.

Tests A through Z (26 tests):
A. test_A_build_configuration_inspection
B. test_B_target_framework_detection
C. test_C_dependency_inspection
D. test_D_safe_solution_build
E. test_E_safe_project_build
F. test_F_build_output_capture
G. test_G_exit_code_verification
H. test_H_cs_compiler_error_parsing
I. test_I_vb_compiler_error_parsing
J. test_J_fs_compiler_error_parsing
K. test_K_msbuild_error_parsing
L. test_L_nuget_error_parsing
M. test_M_test_execution
N. test_N_test_failure_parsing
O. test_O_test_success_verification
P. test_P_runtime_failure_parsing
Q. test_Q_launch_configuration_inspection
R. test_R_timeout_enforcement
S. test_S_output_size_limits
T. test_T_unauthorized_executable_rejection
U. test_U_unauthorized_path_rejection
V. test_V_path_traversal_rejection
W. test_W_emergency_stop
X. test_X_model_isolation
Y. test_Y_deterministic_evidence_precedence
Z. test_Z_audit_logging
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from app.agent.vs_safety import (
    ALLOWED_VS_TOOLS,
    ALLOWED_VS_EXTENSIONS,
    PROTECTED_VS_EXTENSIONS,
    PROTECTED_VS_FILENAMES,
    PROTECTED_VS_DIRECTORIES,
    MAX_BUILD_OUTPUT_BYTES,
    MAX_BUILD_OUTPUT_LINES,
    RUNTIME_TIMEOUT_SECONDS,
    MAX_RUNTIME_OUTPUT_BYTES,
    MAX_RUNTIME_OUTPUT_LINES,
    BUILD_TIMEOUT_SECONDS,
    TEST_TIMEOUT_SECONDS,
    VSErrorCode,
    VSSafetyError,
    EmergencyStopActiveError,
    VSSafetyGate,
    redact_sensitive_data,
    redact_sensitive_vs_data,
)
from app.agent.vs_environment import (
    VSInstance,
    VSEnvironmentInfo,
    VSEnvironmentDetector,
)
from app.agent.vs_project import (
    VSProjectMetadata,
    VSSolutionMetadata,
    VSLaunchProfile,
    VSLaunchSettingsMetadata,
    VSProjectInspector,
)
from app.agent.vs_tools import (
    VSToolResult,
    SafeMSBuildRunner,
    SafeRuntimeRunner,
    VSToolRegistry,
)
from app.agent.vs_error_analyzer import (
    VSErrorCategory,
    VSBuildError,
    VSErrorAnalyzer,
)
from app.agent.vs_code_repair import (
    VSEditProposal,
    VSEditBatch,
    VSRepairResult,
    VSCodeRepairEngine,
)
from app.agent.vs_unified_agent import (
    UnifiedVSState,
    UnifiedVSErrorDomain,
    UnifiedVSWorkflowType,
    VSPlanStep,
    VSPlan,
    VSExecutionResult,
    UnifiedVSResult,
    VSWorkflowReport,
    VSStateMachine,
    UnifiedVSPlanner,
    classify_vs_error,
    UnifiedVisualStudioAgent,
    verify_workflow_outcome,
)
from app.memory.audit_logger import AuditLogger


class TestStep7Phase2VisualStudioAgent(unittest.TestCase):
    """Authoritative acceptance test suite for Step 7 Phase 2."""

    def setUp(self):
        VSSafetyGate.deactivate_emergency_stop()
        self.scratch_root = Path("C:/NR-AI/scratch/vs_phase2_tmp").resolve()
        self.scratch_root.mkdir(parents=True, exist_ok=True)
        self.test_dir = Path(tempfile.mkdtemp(dir=self.scratch_root))
        self.audit = AuditLogger(log_dir=str(self.test_dir / "audit"))
        self.gate = VSSafetyGate(authorized_project=self.test_dir)
        self.fixture_dir = Path("C:/NR-AI/nr_vs_test").resolve()

    def tearDown(self):
        VSSafetyGate.deactivate_emergency_stop()
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # Test A: Build Configuration Inspection
    # -------------------------------------------------------------------------
    def test_A_build_configuration_inspection(self):
        """Test inspection of build configurations, platforms, and properties."""
        inspector = VSProjectInspector(safety_gate=self.gate)

        # 1. Project level inspection
        proj_file = self.fixture_dir / "SampleApp.csproj"
        meta = inspector.inspect_project(proj_file)
        self.assertEqual(meta.target_framework, "net8.0")
        self.assertEqual(meta.platform_target, "AnyCPU")
        self.assertEqual(meta.runtime_identifier, "win-x64")
        self.assertTrue(meta.treat_warnings_as_errors)
        self.assertEqual(meta.lang_version, "12.0")

        # 2. Tool handler execution
        registry = VSToolRegistry(workspace_root=self.fixture_dir, audit_logger=self.audit)
        res = registry.execute("vs.inspect_build_configuration", {"target_path": str(proj_file)})
        self.assertTrue(res.success)
        self.assertIn("target_framework", res.data)
        self.assertEqual(res.data["target_framework"], "net8.0")
        self.assertEqual(res.data["platform_target"], "AnyCPU")

    # -------------------------------------------------------------------------
    # Test B: Target Framework Detection
    # -------------------------------------------------------------------------
    def test_B_target_framework_detection(self):
        """Test detecting single and multi-targeting frameworks."""
        inspector = VSProjectInspector(safety_gate=self.gate)

        # Single target framework
        tfms = inspector.inspect_target_frameworks(self.fixture_dir / "SampleApp.csproj")
        self.assertEqual(tfms, ["net8.0"])

        # Multi-target project
        multi_proj = self.test_dir / "MultiTarget.csproj"
        multi_proj.write_text("""<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFrameworks>net8.0;net481;net6.0</TargetFrameworks>
  </PropertyGroup>
</Project>""", encoding="utf-8")

        tfms_multi = inspector.inspect_target_frameworks(multi_proj)
        self.assertEqual(tfms_multi, ["net8.0", "net481", "net6.0"])

        # Via tool registry
        registry = VSToolRegistry(workspace_root=self.test_dir, audit_logger=self.audit)
        res = registry.execute("vs.inspect_target_frameworks", {"target_path": str(multi_proj)})
        self.assertTrue(res.success)
        self.assertEqual(res.data["target_frameworks"], ["net8.0", "net481", "net6.0"])

    # -------------------------------------------------------------------------
    # Test C: Dependency Inspection
    # -------------------------------------------------------------------------
    def test_C_dependency_inspection(self):
        """Test inspection of package references, project references, and CPM."""
        registry = VSToolRegistry(workspace_root=self.fixture_dir, audit_logger=self.audit)

        # Project with package references
        res1 = registry.execute("vs.inspect_dependencies", {"project_path": str(self.fixture_dir / "SampleApp.csproj")})
        self.assertTrue(res1.success)
        pkg_names = [p["name"] for p in res1.data.get("packages", [])]
        self.assertIn("Newtonsoft.Json", pkg_names)

        # Test project with project reference
        res2 = registry.execute("vs.inspect_dependencies", {"project_path": str(self.fixture_dir / "SampleApp.Tests.csproj")})
        self.assertTrue(res2.success)
        proj_refs = res2.data.get("project_references", [])
        self.assertTrue(any("SampleApp.csproj" in r for r in proj_refs))

    # -------------------------------------------------------------------------
    # Test D: Safe Solution Build
    # -------------------------------------------------------------------------
    def test_D_safe_solution_build(self):
        """Test running safe solution build with bounded output and shell=False."""
        runner = SafeMSBuildRunner(safety_gate=self.gate, audit_logger=self.audit)

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "Build succeeded. 0 Warning(s) 0 Error(s)"
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc) as mock_sub:
            res = runner.run_build(
                self.fixture_dir / "SampleApp.sln",
                action="BUILD",
                configuration="Release",
                platform="x64",
            )

        self.assertTrue(res["success"])
        self.assertEqual(res["returncode"], 0)

        # Verify command arguments and shell=False (dotnet or msbuild)
        cmd_args = mock_sub.call_args[0][0]
        self.assertTrue(any(x in ("build", "/t:Build") for x in cmd_args))
        self.assertTrue(any("Release" in x for x in cmd_args))
        self.assertTrue(any("x64" in x for x in cmd_args))
        self.assertFalse(mock_sub.call_args[1].get("shell", False))

    # -------------------------------------------------------------------------
    # Test E: Safe Project Build
    # -------------------------------------------------------------------------
    def test_E_safe_project_build(self):
        """Test running safe project build with properties, target framework, and shell=False."""
        runner = SafeMSBuildRunner(safety_gate=self.gate, audit_logger=self.audit)

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "SampleApp -> bin\\Debug\\net8.0\\SampleApp.dll\nBuild succeeded."
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc) as mock_sub:
            res = runner.run_build(
                self.fixture_dir / "SampleApp.csproj",
                action="REBUILD",
                configuration="Debug",
                target_framework="net8.0",
                properties={"WarningLevel": "4", "Nullable": "enable"},
            )

        self.assertTrue(res["success"])
        cmd_args = mock_sub.call_args[0][0]
        self.assertTrue(any(x in ("--no-incremental", "/t:Rebuild") for x in cmd_args))
        self.assertTrue(any("net8.0" in x for x in cmd_args))
        self.assertTrue(any("WarningLevel" in x for x in cmd_args))
        self.assertFalse(mock_sub.call_args[1].get("shell", False))

    # -------------------------------------------------------------------------
    # Test F: Build Output Capture
    # -------------------------------------------------------------------------
    def test_F_build_output_capture(self):
        """Test bounded capture_build_output tool handler."""
        registry = VSToolRegistry(workspace_root=self.fixture_dir, audit_logger=self.audit)

        # Populate last_build_output with 50 lines
        lines = [f"MSBuild Line {i}: Output progress details" for i in range(50)]
        registry.last_build_output = {
            "success": True,
            "returncode": 0,
            "stdout": "\n".join(lines),
            "stderr": "",
            "full_output": "\n".join(lines),
        }

        res = registry.execute("vs.capture_build_output", {"lines": 15})
        self.assertTrue(res.success)
        self.assertEqual(res.data["lines_returned"], 15)
        self.assertEqual(res.data["total_lines"], 50)
        self.assertIn("MSBuild Line 49", res.output)
        self.assertNotIn("MSBuild Line 10", res.output)

    # -------------------------------------------------------------------------
    # Test G: Exit Code Verification
    # -------------------------------------------------------------------------
    def test_G_exit_code_verification(self):
        """Test exit code and output binary verification."""
        registry = VSToolRegistry(workspace_root=self.fixture_dir, audit_logger=self.audit)

        # Successful build output with mock bin
        bin_dir = self.fixture_dir / "bin" / "Debug" / "net8.0"
        bin_dir.mkdir(parents=True, exist_ok=True)
        sample_dll = bin_dir / "SampleApp.dll"
        sample_dll.write_text("fake binary", encoding="utf-8")

        try:
            registry.last_build_output = {"success": True, "returncode": 0, "full_output": "Success"}
            res = registry.execute("vs.verify_build_result", {"target_path": str(self.fixture_dir / "SampleApp.csproj")})
            self.assertTrue(res.success)
            self.assertTrue(res.data["verified"])
            self.assertEqual(res.data["returncode"], 0)

            # Non-zero exit code
            registry.last_build_output = {"success": False, "returncode": 1, "full_output": "Compilation failed"}
            res_fail = registry.execute("vs.verify_build_result", {"target_path": str(self.fixture_dir / "SampleApp.csproj")})
            self.assertFalse(res_fail.success)
            self.assertFalse(res_fail.data["verified"])
            self.assertEqual(res_fail.data["returncode"], 1)
        finally:
            if sample_dll.exists():
                sample_dll.unlink()

    # -------------------------------------------------------------------------
    # Test H: C# Compiler Error Parsing
    # -------------------------------------------------------------------------
    def test_H_cs_compiler_error_parsing(self):
        """Test parsing CS compiler errors with line, column, and diagnosis."""
        output = (
            "C:\\NR-AI\\nr_vs_test\\Program.cs(12,15): error CS0103: The name 'unresolvedVar' does not exist in the current context [C:\\NR-AI\\nr_vs_test\\SampleApp.csproj]\n"
            "C:\\NR-AI\\nr_vs_test\\Program.cs(15,20): error CS0246: The type or namespace name 'MissingType' could not be found (are you missing a using directive?) [C:\\NR-AI\\nr_vs_test\\SampleApp.csproj]\n"
        )
        analyzer = VSErrorAnalyzer()
        errors = analyzer.parse_build_output(output)
        self.assertEqual(len(errors), 2)

        self.assertEqual(errors[0].category, VSErrorCategory.CS_COMPILER_ERROR)
        self.assertEqual(errors[0].code, "CS0103")
        self.assertEqual(errors[0].line, 12)
        self.assertEqual(errors[0].column, 15)

        self.assertEqual(errors[1].category, VSErrorCategory.MISSING_REFERENCE)
        self.assertEqual(errors[1].code, "CS0246")

    # -------------------------------------------------------------------------
    # Test I: VB Compiler Error Parsing
    # -------------------------------------------------------------------------
    def test_I_vb_compiler_error_parsing(self):
        """Test parsing BC compiler errors for Visual Basic."""
        output = (
            "C:\\NR-AI\\nr_vs_test\\Module1.vb(10,5): error BC30451: 'totalSum' is not declared. It may be inaccessible due to its protection level. [C:\\NR-AI\\nr_vs_test\\VbApp.vbproj]\n"
        )
        analyzer = VSErrorAnalyzer()
        errors = analyzer.parse_build_output(output)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].category, VSErrorCategory.VB_COMPILER_ERROR)
        self.assertEqual(errors[0].code, "BC30451")
        self.assertEqual(errors[0].line, 10)

    # -------------------------------------------------------------------------
    # Test J: F# Compiler Error Parsing
    # -------------------------------------------------------------------------
    def test_J_fs_compiler_error_parsing(self):
        """Test parsing FS compiler errors for F#."""
        output = (
            "C:\\NR-AI\\nr_vs_test\\Program.fs(18,9): error FS0039: The value or constructor 'calculateSum' is not defined. [C:\\NR-AI\\nr_vs_test\\FsApp.fsproj]\n"
        )
        analyzer = VSErrorAnalyzer()
        errors = analyzer.parse_build_output(output)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].category, VSErrorCategory.FS_COMPILER_ERROR)
        self.assertEqual(errors[0].code, "FS0039")
        self.assertEqual(errors[0].line, 18)

    # -------------------------------------------------------------------------
    # Test K: MSBuild Error Parsing
    # -------------------------------------------------------------------------
    def test_K_msbuild_error_parsing(self):
        """Test parsing MSBuild errors including missing SDK and missing targets."""
        output = (
            "C:\\NR-AI\\nr_vs_test\\SampleApp.csproj(1,1): error MSB4236: The SDK 'Microsoft.NET.Sdk.Web' specified could not be found.\n"
            "C:\\NR-AI\\nr_vs_test\\SampleApp.csproj(20,5): error MSB4019: The imported project \"C:\\Custom.targets\" was not found. Confirm that the expression in the Import declaration is correct.\n"
        )
        analyzer = VSErrorAnalyzer()
        errors = analyzer.parse_build_output(output)
        self.assertEqual(len(errors), 2)

        self.assertEqual(errors[0].category, VSErrorCategory.MISSING_SDK)
        self.assertEqual(errors[0].code, "MSB4236")

        self.assertEqual(errors[1].category, VSErrorCategory.MSBUILD_ERROR)
        self.assertEqual(errors[1].code, "MSB4019")

    # -------------------------------------------------------------------------
    # Test L: NuGet Error Parsing
    # -------------------------------------------------------------------------
    def test_L_nuget_error_parsing(self):
        """Test parsing NuGet restore errors and version conflicts."""
        output = (
            "C:\\NR-AI\\nr_vs_test\\SampleApp.csproj : error NU1605: Detected package downgrade: System.Text.Json from 8.0.1 to 8.0.0. Reference the package directly from the project to select a different version.\n"
            "C:\\NR-AI\\nr_vs_test\\SampleApp.csproj : error NU1101: Unable to find package Contoso.Analytics. No packages exist with this id in source(s): nuget.org\n"
        )
        analyzer = VSErrorAnalyzer()
        errors = analyzer.parse_build_output(output)
        self.assertEqual(len(errors), 2)

        self.assertEqual(errors[0].category, VSErrorCategory.PACKAGE_VERSION_CONFLICT)
        self.assertEqual(errors[0].code, "NU1605")

        self.assertEqual(errors[1].category, VSErrorCategory.NUGET_ERROR)
        self.assertEqual(errors[1].code, "NU1101")

    # -------------------------------------------------------------------------
    # Test M: Test Execution
    # -------------------------------------------------------------------------
    def test_M_test_execution(self):
        """Test safe test execution with filter, configuration, and shell=False."""
        runner = SafeMSBuildRunner(safety_gate=self.gate, audit_logger=self.audit)

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "Passed!  - Failed:     0, Passed:     8, Skipped:     0, Total:     8, Duration: 120 ms"
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc) as mock_sub:
            res = runner.run_test(
                self.fixture_dir / "SampleApp.Tests.csproj",
                filter_expr="Category=Fast",
                configuration="Debug",
            )

        self.assertTrue(res["success"])
        self.assertEqual(res["returncode"], 0)
        self.assertEqual(res["summary"]["passed"], 8)
        self.assertEqual(res["summary"]["failed"], 0)

        cmd_args = mock_sub.call_args[0][0]
        self.assertTrue(any("Fast" in x for x in cmd_args))
        self.assertFalse(mock_sub.call_args[1].get("shell", False))

    # -------------------------------------------------------------------------
    # Test N: Test Failure Parsing
    # -------------------------------------------------------------------------
    def test_N_test_failure_parsing(self):
        """Test parsing unit test failures and test summary."""
        output = (
            "  Failed SampleApp.Tests.CalculatorTests.TestDivisionByZero [45 ms]\n"
            "  Error Message:\n"
            "   System.DivideByZeroException: Attempted to divide by zero.\n"
            "  Stack Trace:\n"
            "     at SampleApp.Calculator.Divide(Int32 a, Int32 b) in C:\\NR-AI\\nr_vs_test\\Calculator.cs:line 30\n"
            "     at SampleApp.Tests.CalculatorTests.TestDivisionByZero() in C:\\NR-AI\\nr_vs_test\\CalculatorTests.cs:line 15\n\n"
            "Failed!  - Failed:     1, Passed:     4, Skipped:     0, Total:     5, Duration: 350 ms - SampleApp.Tests.dll (net8.0)\n"
        )
        analyzer = VSErrorAnalyzer()
        errors = analyzer.parse_build_output(output)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].category, VSErrorCategory.TEST_RUNNER_ERROR)
        self.assertIn("TestDivisionByZero", errors[0].message)
        self.assertEqual(errors[0].line, 30)

        summary = analyzer.parse_test_summary(output)
        self.assertEqual(summary["total"], 5)
        self.assertEqual(summary["passed"], 4)
        self.assertEqual(summary["failed"], 1)

    # -------------------------------------------------------------------------
    # Test O: Test Success Verification
    # -------------------------------------------------------------------------
    def test_O_test_success_verification(self):
        """Test verify_test_result tool handler for passed vs failed runs."""
        registry = VSToolRegistry(workspace_root=self.fixture_dir, audit_logger=self.audit)

        # Successful run
        registry.last_test_output = {
            "success": True,
            "returncode": 0,
            "summary": {"total": 10, "passed": 10, "failed": 0},
        }
        res_pass = registry.execute("vs.verify_test_result", {})
        self.assertTrue(res_pass.success)
        self.assertTrue(res_pass.data["verified"])

        # Failed run
        registry.last_test_output = {
            "success": False,
            "returncode": 1,
            "summary": {"total": 10, "passed": 9, "failed": 1},
        }
        res_fail = registry.execute("vs.verify_test_result", {})
        self.assertFalse(res_fail.success)
        self.assertFalse(res_fail.data["verified"])

    # -------------------------------------------------------------------------
    # Test P: Runtime Failure Parsing
    # -------------------------------------------------------------------------
    def test_P_runtime_failure_parsing(self):
        """Test classifying unhandled exceptions, access violations, port conflicts, and missing runtimes."""
        analyzer = VSErrorAnalyzer()

        # 1. Unhandled exception
        out1 = (
            "Unhandled exception. System.NullReferenceException: Object reference not set to an instance of an object.\n"
            "   at SampleApp.Program.ProcessData() in C:\\NR-AI\\nr_vs_test\\Program.cs:line 25\n"
            "   at SampleApp.Program.Main(String[] args) in C:\\NR-AI\\nr_vs_test\\Program.cs:line 10\n"
        )
        err1 = analyzer.classify_runtime_failure(out1, exit_code=1)
        self.assertEqual(err1.category, VSErrorCategory.UNHANDLED_EXCEPTION)
        self.assertIn("NullReferenceException", err1.message)
        self.assertEqual(err1.line, 25)

        # 2. Access violation crash
        err2 = analyzer.classify_runtime_failure("Fatal error. Process terminated.", exit_code=-1073741819)
        self.assertEqual(err2.category, VSErrorCategory.RUNTIME_CRASH)
        self.assertIn("0xC0000005", err2.message)

        # 3. Port bind conflict
        out3 = "System.IO.IOException: Failed to bind to address http://127.0.0.1:5000: address already in use."
        err3 = analyzer.classify_runtime_failure(out3, exit_code=1)
        self.assertEqual(err3.category, VSErrorCategory.PORT_BIND_FAILURE)

        # 4. Missing runtime
        out4 = "You must install or update .NET to run this application. App: C:\\App.dll Architecture: x64 Framework: 'Microsoft.NETCore.App', version '9.0.0'"
        err4 = analyzer.classify_runtime_failure(out4, exit_code=1)
        self.assertEqual(err4.category, VSErrorCategory.MISSING_RUNTIME)

    # -------------------------------------------------------------------------
    # Test Q: Launch Configuration Inspection
    # -------------------------------------------------------------------------
    def test_Q_launch_configuration_inspection(self):
        """Test inspecting launch profiles with sensitive data redaction."""
        inspector = VSProjectInspector(safety_gate=self.gate)
        launch_file = self.fixture_dir / "Properties" / "launchSettings.json"

        meta = inspector.inspect_launch_configuration(launch_file)
        self.assertIn("SampleApp", meta.profiles)
        profile = meta.profiles["SampleApp"]

        self.assertEqual(profile.command_name, "Project")
        self.assertEqual(profile.command_line_args, "--verbose --mode test")
        self.assertEqual(profile.environment_variables.get("ASPNETCORE_ENVIRONMENT"), "Development")

        # Verify sensitive redaction
        secret_val = profile.environment_variables.get("API_SECRET_KEY", "")
        db_url = profile.environment_variables.get("DATABASE_URL", "")
        self.assertNotIn("sk-secret-key-12345", secret_val)
        self.assertNotIn("myPassword", db_url)
        self.assertIn("[REDACTED", secret_val)
        self.assertIn("[REDACTED", db_url)

    # -------------------------------------------------------------------------
    # Test R: Timeout Enforcement
    # -------------------------------------------------------------------------
    def test_R_timeout_enforcement(self):
        """Test enforcing runtime and build timeouts with graceful termination."""
        runner = SafeRuntimeRunner(safety_gate=self.gate, audit_logger=self.audit)

        fake_exe = self.test_dir / "HangingApp.exe"
        fake_exe.write_text("fake", encoding="utf-8")

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["fake"], timeout=1.0)):
            res = runner.run_executable(fake_exe, timeout=1.0)

        self.assertFalse(res["success"])
        self.assertTrue(res["timed_out"])
        self.assertEqual(res["returncode"], -1)

        # Test safety gate timeout validation
        with self.assertRaises(VSSafetyError):
            self.gate.validate_timeout(-5.0)
        with self.assertRaises(VSSafetyError):
            self.gate.validate_timeout(500.0)

    # -------------------------------------------------------------------------
    # Test S: Output Size Limits
    # -------------------------------------------------------------------------
    def test_S_output_size_limits(self):
        """Test validating and bounding build and runtime output limits."""
        # 1. Exact limits
        self.gate.validate_output_size(100, 10, "build")
        self.gate.validate_output_size(100, 10, "runtime")

        # 2. Exceeding bytes
        with self.assertRaises(VSSafetyError):
            self.gate.validate_output_size(MAX_BUILD_OUTPUT_BYTES + 100, 10, "build")
        with self.assertRaises(VSSafetyError):
            self.gate.validate_output_size(MAX_RUNTIME_OUTPUT_BYTES + 100, 10, "runtime")

        # 3. Exceeding lines
        with self.assertRaises(VSSafetyError):
            self.gate.validate_output_size(100, MAX_BUILD_OUTPUT_LINES + 100, "build")
        with self.assertRaises(VSSafetyError):
            self.gate.validate_output_size(100, MAX_RUNTIME_OUTPUT_LINES + 100, "runtime")

    # -------------------------------------------------------------------------
    # Test T: Unauthorized Executable Rejection
    # -------------------------------------------------------------------------
    def test_T_unauthorized_executable_rejection(self):
        """Test rejection of unauthorized executables and system shells."""
        gate = VSSafetyGate(authorized_project=self.test_dir)

        # Reject system executables
        with self.assertRaises(VSSafetyError) as ctx1:
            gate.validate_executable_path("C:/Windows/System32/cmd.exe")
        self.assertEqual(ctx1.exception.code, VSErrorCode.UNAUTHORIZED_EXECUTABLE)

        # Reject random arbitrary path
        with self.assertRaises(VSSafetyError) as ctx2:
            gate.validate_executable_path("C:/Temp/malicious.exe")
        self.assertEqual(ctx2.exception.code, VSErrorCode.UNAUTHORIZED_EXECUTABLE)

        # Allow authorized binary within workspace
        valid_bin = self.test_dir / "bin" / "Debug" / "net8.0" / "App.exe"
        valid_bin.parent.mkdir(parents=True, exist_ok=True)
        valid_bin.write_text("binary", encoding="utf-8")
        val = gate.validate_executable_path(valid_bin)
        self.assertEqual(val, valid_bin)

    # -------------------------------------------------------------------------
    # Test U: Unauthorized Path Rejection
    # -------------------------------------------------------------------------
    def test_U_unauthorized_path_rejection(self):
        """Test rejection of paths outside authorized workspace."""
        gate = VSSafetyGate(authorized_project=self.test_dir)

        with self.assertRaises(VSSafetyError) as ctx:
            gate.validate_project_path("C:/Windows/System32")
        self.assertEqual(ctx.exception.code, VSErrorCode.PROJECT_NOT_AUTHORIZED)

        with self.assertRaises(VSSafetyError) as ctx2:
            gate.validate_project_path("C:/Users/OtherUser/SecretProject")
        self.assertEqual(ctx2.exception.code, VSErrorCode.PROJECT_NOT_AUTHORIZED)

    # -------------------------------------------------------------------------
    # Test V: Path Traversal Rejection
    # -------------------------------------------------------------------------
    def test_V_path_traversal_rejection(self):
        """Test rejection of directory traversal attempts."""
        gate = VSSafetyGate(authorized_project=self.test_dir)

        traversal_path = self.test_dir / ".." / ".." / "Windows" / "System32"
        with self.assertRaises(VSSafetyError) as ctx:
            gate.validate_project_path(traversal_path)
        self.assertEqual(ctx.exception.code, VSErrorCode.PATH_TRAVERSAL_DETECTED)

    # -------------------------------------------------------------------------
    # Test W: Emergency Stop
    # -------------------------------------------------------------------------
    def test_W_emergency_stop(self):
        """Test emergency stop freezes runner execution immediately without executing subprocess."""
        gate = VSSafetyGate(authorized_project=self.test_dir)
        gate.activate_emergency_stop()

        runner = SafeRuntimeRunner(safety_gate=gate, audit_logger=self.audit)
        fake_exe = self.test_dir / "App.exe"
        fake_exe.write_text("fake", encoding="utf-8")

        with patch("subprocess.run") as mock_sub:
            with self.assertRaises(EmergencyStopActiveError):
                runner.run_executable(fake_exe)
            mock_sub.assert_not_called()

        gate.deactivate_emergency_stop()

    # -------------------------------------------------------------------------
    # Test X: Model Isolation
    # -------------------------------------------------------------------------
    def test_X_model_isolation(self):
        """Test that advisory model responses cannot directly execute runtime or build tools."""
        agent = UnifiedVisualStudioAgent(workspace_root=self.fixture_dir, audit_logger=self.audit)

        # Advisory model output attempting to run arbitrary command
        malicious_claim = {
            "success": True,
            "tool_call": "vs.capture_runtime_output",
            "command": "powershell.exe -Command Remove-Item C:\\NR-AI -Recurse",
        }

        # Model claim cannot trigger execution
        res = agent.verify_workflow_outcome(
            result=UnifiedVSResult(
                request_id="req-1",
                workflow_id="wf-1",
                goal="run app",
                workflow_type=UnifiedVSWorkflowType.RUN_PROJECT,
                state=UnifiedVSState.FAILED,
                success=False,
                error_domain=UnifiedVSErrorDomain.RUNTIME_FAILURE,
            ),
            model_claim=malicious_claim,
        )
        self.assertFalse(res["verified"])
        self.assertTrue(res["overridden"])

    # -------------------------------------------------------------------------
    # Test Y: Deterministic Evidence Precedence
    # -------------------------------------------------------------------------
    def test_Y_deterministic_evidence_precedence(self):
        """Test that deterministic ground-truth overrides false model claims."""
        ground_truth = UnifiedVSResult(
            request_id="req-gt",
            workflow_id="wf-gt",
            goal="run tests and app",
            workflow_type=UnifiedVSWorkflowType.RUN_PROJECT,
            state=UnifiedVSState.FAILED,
            success=False,
            error_domain=UnifiedVSErrorDomain.RUNTIME_FAILURE,
            error_message="Runtime crashed with code -1073741819",
            evidence={"exit_code": -1073741819, "runtime_crash": True},
        )

        model_claim = {
            "success": True,
            "claim": "All tests passed and application executed without issue.",
        }

        verdict = verify_workflow_outcome(ground_truth, model_claim=model_claim)
        self.assertFalse(verdict["verified"])
        self.assertTrue(verdict["overridden"])
        self.assertIn("OVERRIDE", verdict["override_reason"])
        self.assertEqual(verdict["deterministic_evidence"]["exit_code"], -1073741819)

    # -------------------------------------------------------------------------
    # Test Z: Audit Logging
    # -------------------------------------------------------------------------
    def test_Z_audit_logging(self):
        """Test structured audit trail logging for all tool executions and outcomes."""
        audit_dir = self.test_dir / "audit_test_phase2"
        audit = AuditLogger(log_dir=str(audit_dir))
        registry = VSToolRegistry(workspace_root=self.fixture_dir, audit_logger=audit)

        # 1. Target frameworks inspection
        res_tfm = registry.execute("vs.inspect_target_frameworks", {"target_path": str(self.fixture_dir / "SampleApp.csproj")})
        self.assertTrue(res_tfm.success)

        # 2. Launch configuration inspection
        res_launch = registry.execute("vs.inspect_launch_configuration", {"target_path": str(self.fixture_dir / "Properties" / "launchSettings.json")})
        self.assertTrue(res_launch.success)

        entries = audit.get_entries()
        self.assertGreaterEqual(len(entries), 2)
        event_names = [e.get("event_type") for e in entries if isinstance(e, dict)]
        self.assertIn("VS_TOOL_CALL", event_names)


if __name__ == "__main__":
    unittest.main()
