"""
Acceptance Test Suite for NR-AI Step 7: Visual Studio Agent Foundation.

Tests A through Z (26 tests):
A. test_A_environment_detection
B. test_B_missing_vs_handling
C. test_C_solution_discovery
D. test_D_project_discovery
E. test_E_safe_file_inspection
F. test_F_protected_file_rejection
G. test_G_unauthorized_path_rejection
H. test_H_patch_size_limit
I. test_I_line_count_limit
J. test_J_excessive_files_limit
K. test_K_stale_target_rejection
L. test_L_atomic_edit_behavior
M. test_M_rollback_behavior
N. test_N_cs_compiler_error_parsing
O. test_O_msbuild_error_parsing
P. test_P_nuget_error_parsing
Q. test_Q_test_failure_parsing
R. test_R_model_proposal_schema_validation
S. test_S_model_cannot_execute_tools
T. test_T_sensitive_data_redaction
U. test_U_emergency_stop
V. test_V_state_machine_transitions
W. test_W_two_repair_attempt_bound
X. test_X_deterministic_evidence_precedence
Y. test_Y_audit_trail_logging
Z. test_Z_companion_routing
"""

import json
import os
from pathlib import Path
import shutil
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
    MAX_PATCH_BYTES,
    MAX_EDIT_LINES,
    MAX_FILES_PER_REPAIR,
    MAX_READ_LINES,
    MAX_SEARCH_RESULTS,
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
    VSProjectInspector,
)
from app.agent.vs_tools import (
    VSToolResult,
    SafeMSBuildRunner,
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
from app.brain.companion import CommandCategory, NRCompanion
from app.memory.audit_logger import AuditLogger


class TestStep7VisualStudioAgent(unittest.TestCase):
    """Authoritative test suite for Step 7 Visual Studio Agent."""

    def setUp(self):
        VSSafetyGate.deactivate_emergency_stop()
        self.scratch_root = Path("C:/NR-AI/scratch/vs_test_tmp").resolve()
        self.scratch_root.mkdir(parents=True, exist_ok=True)
        self.test_dir = Path(tempfile.mkdtemp(dir=self.scratch_root))
        self.audit = AuditLogger(log_dir=str(self.test_dir / "audit"))
        self.gate = VSSafetyGate(authorized_project=self.test_dir)

    def tearDown(self):
        VSSafetyGate.deactivate_emergency_stop()
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # Test A: Environment Detection
    # -------------------------------------------------------------------------
    def test_A_environment_detection(self):
        """Test environment detector identifies VS instances and tools safely."""
        sample_vswhere_output = json.dumps([
            {
                "instanceId": "vs2022comm",
                "installDate": "2023-01-01T00:00:00Z",
                "installationName": "VisualStudio/17.8.0+34309.116",
                "installationPath": r"C:\Program Files\Microsoft Visual Studio\2022\Community",
                "installationVersion": "17.8.34309.116",
                "productId": "Microsoft.VisualStudio.Product.Community",
                "productPath": r"C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\IDE\devenv.exe",
                "isPrerelease": False,
                "displayName": "Visual Studio Community 2022",
                "description": "Visual Studio Community",
            }
        ])

        detector = VSEnvironmentDetector()
        with patch.object(detector, "_run_vswhere", return_value=sample_vswhere_output):
            with patch("shutil.which", side_effect=lambda x: r"C:\Program Files\dotnet\dotnet.exe" if x == "dotnet" else None):
                with patch("subprocess.run") as mock_sub:
                    mock_sub.return_value = MagicMock(returncode=0, stdout="8.0.100\n", stderr="")
                    info = detector.detect(force_refresh=True)

        self.assertIsInstance(info, VSEnvironmentInfo)
        self.assertEqual(len(info.instances), 1)
        self.assertEqual(info.instances[0].display_name, "Visual Studio Community 2022")
        self.assertEqual(info.instances[0].version, "17.8.34309.116")
        self.assertEqual(info.dotnet_version, "8.0.100")
        self.assertTrue(info.is_ready)

    # -------------------------------------------------------------------------
    # Test B: Missing Visual Studio Handling
    # -------------------------------------------------------------------------
    def test_B_missing_vs_handling(self):
        """Test graceful handling when Visual Studio is not installed."""
        detector = VSEnvironmentDetector()
        with patch.object(detector, "_run_vswhere", return_value="[]"):
            with patch("shutil.which", return_value=None):
                with patch.object(detector, "_find_vswhere", return_value=None):
                    with patch("pathlib.Path.exists", return_value=False):
                        info = detector.detect(force_refresh=True)

        self.assertIsInstance(info, VSEnvironmentInfo)
        self.assertEqual(len(info.instances), 0)
        self.assertFalse(info.is_ready)
        self.assertTrue(len(info.warnings) > 0)

    # -------------------------------------------------------------------------
    # Test C: Solution Discovery
    # -------------------------------------------------------------------------
    def test_C_solution_discovery(self):
        """Test discovering and parsing .sln and .slnx files."""
        sln_content = """
Microsoft Visual Studio Solution File, Format Version 12.00
# Visual Studio Version 17
VisualStudioVersion = 17.8.34309.116
MinimumVisualStudioVersion = 10.0.40219.1
Project("{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}") = "SampleApp", "src\\SampleApp\\SampleApp.csproj", "{8B5E4DF8-19D4-4C5D-9132-7F015F3C2809}"
EndProject
Project("{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}") = "SampleApp.Tests", "tests\\SampleApp.Tests\\SampleApp.Tests.csproj", "{49A6242D-6F47-4C7F-8515-3B8563C9E27E}"
EndProject
Global
	GlobalSection(SolutionConfigurationPlatforms) = preSolution
		Debug|Any CPU = Debug|Any CPU
		Release|Any CPU = Release|Any CPU
	EndGlobalSection
EndGlobal
"""
        sln_path = self.test_dir / "SampleApp.sln"
        sln_path.write_text(sln_content, encoding="utf-8")

        inspector = VSProjectInspector(safety_gate=self.gate)
        meta = inspector.inspect_solution(sln_path)

        self.assertIsInstance(meta, VSSolutionMetadata)
        self.assertEqual(meta.name, "SampleApp.sln")
        self.assertEqual(len(meta.projects), 2)
        proj_names = [p["name"] for p in meta.projects]
        self.assertIn("SampleApp", proj_names)
        self.assertIn("SampleApp.Tests", proj_names)
        self.assertIn("Debug|Any CPU", meta.configurations)

        # Test .slnx XML format
        slnx_content = """<Solution>
  <Project Path="src/SampleApp/SampleApp.csproj" Type="Classic C#" />
</Solution>"""
        slnx_path = self.test_dir / "SampleApp.slnx"
        slnx_path.write_text(slnx_content, encoding="utf-8")
        meta_slnx = inspector.inspect_solution(slnx_path)
        self.assertEqual(meta_slnx.name, "SampleApp.slnx")
        self.assertEqual(len(meta_slnx.projects), 1)

    # -------------------------------------------------------------------------
    # Test D: Project Discovery
    # -------------------------------------------------------------------------
    def test_D_project_discovery(self):
        """Test discovering and parsing .csproj, central package management, and props."""
        csproj_content = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net8.0</TargetFramework>
    <Nullable>enable</Nullable>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Newtonsoft.Json" Version="13.0.3" />
  </ItemGroup>
  <ItemGroup>
    <ProjectReference Include="..\\Core\\Core.csproj" />
  </ItemGroup>
</Project>"""
        csproj_path = self.test_dir / "App.csproj"
        csproj_path.write_text(csproj_content, encoding="utf-8")

        props_content = """<Project>
  <PropertyGroup>
    <ManagePackageVersionsCentrally>true</ManagePackageVersionsCentrally>
  </PropertyGroup>
</Project>"""
        (self.test_dir / "Directory.Build.props").write_text(props_content, encoding="utf-8")

        inspector = VSProjectInspector(safety_gate=self.gate)
        proj_meta = inspector.inspect_project(csproj_path)

        self.assertIsInstance(proj_meta, VSProjectMetadata)
        self.assertEqual(proj_meta.name, "App.csproj")
        self.assertEqual(proj_meta.target_framework, "net8.0")
        self.assertEqual(proj_meta.output_type, "Exe")
        self.assertTrue(proj_meta.central_package_management)
        self.assertIn("Newtonsoft.Json", [p["name"] for p in proj_meta.package_references])
        self.assertIn("Core.csproj", [Path(p).name for p in proj_meta.project_references])

    # -------------------------------------------------------------------------
    # Test E: Safe File Inspection & Bounded Read/Find
    # -------------------------------------------------------------------------
    def test_E_safe_file_inspection(self):
        """Test bounded read_code and find_code return truncated content."""
        code_lines = [f"// Line {i}: int x_{i} = {i};" for i in range(600)]
        code_lines.insert(250, "public class MarkerSymbolTarget { }")
        code_path = self.test_dir / "LargeFile.cs"
        code_path.write_text("\n".join(code_lines), encoding="utf-8")

        inspector = VSProjectInspector(safety_gate=self.gate)

        # Read with max_lines=100
        read_res = inspector.read_code(code_path, start_line=1, max_lines=100)
        self.assertIn("lines", read_res)
        self.assertEqual(read_res["lines_read"], 100)
        self.assertTrue(read_res["truncated"])
        self.assertIn("[TRUNCATED: MAX LINES REACHED]", read_res["content"])

        # Find code across directory
        find_res = inspector.find_code(self.test_dir, "MarkerSymbolTarget")
        self.assertEqual(len(find_res), 1)
        self.assertEqual(find_res[0]["line_number"], 251)
        self.assertIn("MarkerSymbolTarget", find_res[0]["line_content"])

    # -------------------------------------------------------------------------
    # Test F: Protected File Rejection
    # -------------------------------------------------------------------------
    def test_F_protected_file_rejection(self):
        """Test rejection of modifications to protected files."""
        protected_samples = [
            self.test_dir / "key.pfx",
            self.test_dir / "strongname.snk",
            self.test_dir / "secrets.json",
            self.test_dir / "appsettings.production.json",
            self.test_dir / "test.suo",
            self.test_dir / "test.csproj.user",
            self.test_dir / "bin" / "output.dll",
            self.test_dir / ".git" / "config",
        ]

        for p in protected_samples:
            with self.assertRaises(VSSafetyError) as ctx:
                self.gate.validate_file_path(p, check_writable=True)
            self.assertIn(
                ctx.exception.code,
                (
                    VSErrorCode.PROTECTED_FILE_REJECTED,
                    VSErrorCode.PROTECTED_DIRECTORY_REJECTED,
                    VSErrorCode.FILE_NOT_AUTHORIZED,
                ),
            )

    # -------------------------------------------------------------------------
    # Test G: Unauthorized Path Rejection
    # -------------------------------------------------------------------------
    def test_G_unauthorized_path_rejection(self):
        """Test rejection of path traversal and paths outside authorized workspace."""
        traversal_path = r"C:\NR-AI\nr_vs_test\..\..\Windows\System32\cmd.exe"
        with self.assertRaises(VSSafetyError) as ctx:
            self.gate.validate_file_path(traversal_path)
        self.assertIn(ctx.exception.code, (VSErrorCode.PATH_TRAVERSAL_DETECTED, VSErrorCode.FILE_NOT_AUTHORIZED))

        outside_path = r"D:\OtherProjects\Solution.sln"
        with self.assertRaises(VSSafetyError) as ctx:
            self.gate.validate_project_path(outside_path)
        self.assertIn(ctx.exception.code, (VSErrorCode.PROJECT_NOT_AUTHORIZED, VSErrorCode.FILE_NOT_AUTHORIZED))

    # -------------------------------------------------------------------------
    # Test H: Patch Size Limit
    # -------------------------------------------------------------------------
    def test_H_patch_size_limit(self):
        """Test rejection of patches exceeding MAX_PATCH_BYTES."""
        huge_patch = "x = 1;\n" * 15000  # > 65,536 bytes
        self.assertGreater(len(huge_patch.encode("utf-8")), MAX_PATCH_BYTES)

        with self.assertRaises(VSSafetyError) as ctx:
            self.gate.validate_patch_content(huge_patch)
        self.assertEqual(ctx.exception.code, VSErrorCode.PATCH_TOO_LARGE)

    # -------------------------------------------------------------------------
    # Test I: Line Count Limit
    # -------------------------------------------------------------------------
    def test_I_line_count_limit(self):
        """Test rejection of edits exceeding MAX_EDIT_LINES."""
        many_lines = "\n".join([f"int val_{i} = {i};" for i in range(250)])

        with self.assertRaises(VSSafetyError) as ctx:
            self.gate.validate_patch_content(many_lines)
        self.assertEqual(ctx.exception.code, VSErrorCode.TOO_MANY_LINES_CHANGED)

    # -------------------------------------------------------------------------
    # Test J: Excessive Files Limit
    # -------------------------------------------------------------------------
    def test_J_excessive_files_limit(self):
        """Test rejection of proposals modifying more than MAX_FILES_PER_REPAIR."""
        proposals = [
            VSEditProposal(file_path=self.test_dir / f"File{i}.cs", target_sha256="abc", new_content="class A {}")
            for i in range(4)
        ]
        batch = VSEditBatch(proposals=proposals)

        with self.assertRaises(VSSafetyError) as ctx:
            self.gate.validate_edit_batch(batch)
        self.assertEqual(ctx.exception.code, VSErrorCode.TOO_MANY_FILES_CHANGED)

    # -------------------------------------------------------------------------
    # Test K: Stale Target Rejection
    # -------------------------------------------------------------------------
    def test_K_stale_target_rejection(self):
        """Test rejection when target file SHA-256 does not match target_sha256."""
        f_path = self.test_dir / "Target.cs"
        f_path.write_text("class Original {}", encoding="utf-8")
        orig_hash = VSSafetyGate.compute_sha256(f_path)

        # Externally modify file
        f_path.write_text("class ModifiedExternally {}", encoding="utf-8")

        engine = VSCodeRepairEngine(safety_gate=self.gate, audit_logger=self.audit)
        batch = VSEditBatch(proposals=[
            VSEditProposal(
                file_path=f_path,
                target_sha256=orig_hash,
                new_content="class Repaired {}",
            )
        ])

        res = engine.apply_repair(batch)
        self.assertFalse(res.success)
        self.assertTrue(res.stale_target_detected)
        self.assertEqual(f_path.read_text(encoding="utf-8"), "class ModifiedExternally {}")

    # -------------------------------------------------------------------------
    # Test L: Atomic Edit Behavior
    # -------------------------------------------------------------------------
    def test_L_atomic_edit_behavior(self):
        """Test edits are applied atomically with pre-mutation checkpoints."""
        f_path = self.test_dir / "AtomicTest.cs"
        f_path.write_text("public class OldClass { }", encoding="utf-8")
        h = VSSafetyGate.compute_sha256(f_path)

        engine = VSCodeRepairEngine(safety_gate=self.gate, audit_logger=self.audit)
        batch = VSEditBatch(proposals=[
            VSEditProposal(
                file_path=f_path,
                target_sha256=h,
                new_content="public class NewClass { int Val = 42; }",
            )
        ])

        res = engine.apply_repair(batch)
        self.assertTrue(res.success)
        self.assertEqual(f_path.read_text(encoding="utf-8"), "public class NewClass { int Val = 42; }")
        self.assertTrue(res.checkpoint_id)

        # Verify checkpoint file exists
        ckpt_file = engine.checkpoint_dir / f"{res.checkpoint_id}.json"
        self.assertTrue(ckpt_file.exists())

    # -------------------------------------------------------------------------
    # Test M: Rollback Behavior
    # -------------------------------------------------------------------------
    def test_M_rollback_behavior(self):
        """Test rollback restores original files byte-for-byte."""
        f_path = self.test_dir / "RollbackTest.cs"
        orig_text = "namespace OriginalNamespace {\n    class Alpha { }\n}"
        f_path.write_text(orig_text, encoding="utf-8")
        orig_hash = VSSafetyGate.compute_sha256(f_path)

        engine = VSCodeRepairEngine(safety_gate=self.gate, audit_logger=self.audit)
        batch = VSEditBatch(proposals=[
            VSEditProposal(
                file_path=f_path,
                target_sha256=orig_hash,
                new_content="namespace BadMutation { }",
            )
        ])

        res = engine.apply_repair(batch)
        self.assertTrue(res.success)
        self.assertNotEqual(VSSafetyGate.compute_sha256(f_path), orig_hash)

        # Rollback
        rb_ok = engine.rollback_repair(res.checkpoint_id)
        self.assertTrue(rb_ok)
        self.assertEqual(VSSafetyGate.compute_sha256(f_path), orig_hash)
        self.assertEqual(f_path.read_text(encoding="utf-8"), orig_text)

    # -------------------------------------------------------------------------
    # Test N: C# Compiler Error Parsing
    # -------------------------------------------------------------------------
    def test_N_cs_compiler_error_parsing(self):
        """Test analyzer extracts CS compiler errors with line, column, and code."""
        output = """
Build FAILED.
Program.cs(14,21): error CS0103: The name 'CalculateTax' does not exist in the current context [C:\\NR-AI\\App.csproj]
    0 Warning(s)
    1 Error(s)
"""
        analyzer = VSErrorAnalyzer()
        errors = analyzer.parse_build_output(output)

        self.assertEqual(len(errors), 1)
        e = errors[0]
        self.assertEqual(e.category, VSErrorCategory.CS_COMPILER_ERROR)
        self.assertEqual(e.code, "CS0103")
        self.assertEqual(e.line, 14)
        self.assertEqual(e.column, 21)
        self.assertIn("CalculateTax", e.message)
        self.assertEqual(e.file_path, "Program.cs")

    # -------------------------------------------------------------------------
    # Test O: MSBuild Error Parsing
    # -------------------------------------------------------------------------
    def test_O_msbuild_error_parsing(self):
        """Test analyzer extracts MSBuild errors."""
        output = """
C:\\NR-AI\\App.csproj(20,5): error MSB4019: The imported project "C:\\Custom.targets" was not found. Confirm that the expression in the Import declaration is correct.
"""
        analyzer = VSErrorAnalyzer()
        errors = analyzer.parse_build_output(output)

        self.assertEqual(len(errors), 1)
        e = errors[0]
        self.assertEqual(e.category, VSErrorCategory.MSBUILD_ERROR)
        self.assertEqual(e.code, "MSB4019")
        self.assertIn("Custom.targets", e.message)

    # -------------------------------------------------------------------------
    # Test P: NuGet Error Parsing
    # -------------------------------------------------------------------------
    def test_P_nuget_error_parsing(self):
        """Test analyzer extracts NuGet restore errors."""
        output = """
C:\\NR-AI\\App.csproj : error NU1101: Unable to find package Newtonsoft.Json. No packages exist with this id in source(s): nuget.org
"""
        analyzer = VSErrorAnalyzer()
        errors = analyzer.parse_build_output(output)

        self.assertEqual(len(errors), 1)
        e = errors[0]
        self.assertEqual(e.category, VSErrorCategory.NUGET_ERROR)
        self.assertEqual(e.code, "NU1101")
        self.assertIn("Newtonsoft.Json", e.message)

    # -------------------------------------------------------------------------
    # Test Q: Test Failure Parsing
    # -------------------------------------------------------------------------
    def test_Q_test_failure_parsing(self):
        """Test analyzer extracts test runner failures."""
        output = """
  Failed TestProject.CalculatorTests.TestAdd [15 ms]
  Error Message:
   Assert.Equal() Failure
   Expected: 5
   Actual:   4
  Stack Trace:
     at TestProject.CalculatorTests.TestAdd() in C:\\NR-AI\\CalculatorTests.cs:line 18
"""
        analyzer = VSErrorAnalyzer()
        errors = analyzer.parse_build_output(output)

        self.assertEqual(len(errors), 1)
        e = errors[0]
        self.assertEqual(e.category, VSErrorCategory.TEST_RUNNER_ERROR)
        self.assertIn("TestAdd", e.message)
        self.assertEqual(e.line, 18)

    # -------------------------------------------------------------------------
    # Test R: Model Proposal Schema Validation
    # -------------------------------------------------------------------------
    def test_R_model_proposal_schema_validation(self):
        """Test validation and rejection of malformed model repair proposals."""
        engine = VSCodeRepairEngine(safety_gate=self.gate, audit_logger=self.audit)

        # Non-JSON
        self.assertIsNone(engine.validate_model_proposal("Not JSON at all"))

        # Missing required fields
        self.assertIsNone(engine.validate_model_proposal('{"proposals": [{"new_content": "foo"}]}'))

        # Valid format
        valid_json = json.dumps({
            "rationale": "Add missing helper method",
            "proposals": [
                {
                    "file_path": str(self.test_dir / "Valid.cs"),
                    "target_sha256": "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
                    "new_content": "class Valid { void Helper() {} }",
                    "explanation": "Added Helper method",
                }
            ]
        })
        batch = engine.validate_model_proposal(valid_json)
        self.assertIsNotNone(batch)
        self.assertEqual(len(batch.proposals), 1)
        self.assertEqual(batch.proposals[0].explanation, "Added Helper method")

    # -------------------------------------------------------------------------
    # Test S: Model Cannot Execute Tools
    # -------------------------------------------------------------------------
    def test_S_model_cannot_execute_tools(self):
        """Test that advisory models have 0 tool authority and cannot directly mutate."""
        engine = VSCodeRepairEngine(safety_gate=self.gate, audit_logger=self.audit)

        # Model text attempting to execute tool
        malicious_model_output = """
```json
{
  "proposals": [],
  "tool_call": "vs.run_safe_build",
  "command": "cmd.exe /c del *.*"
}
```
"""
        batch = engine.validate_model_proposal(malicious_model_output)
        # Empty proposals list or rejected
        self.assertIsNone(batch)

        # Verify prompt explicit isolation text
        prompt = engine.create_repair_prompt(
            errors=[VSBuildError(category=VSErrorCategory.CS_COMPILER_ERROR, code="CS0103", message="Missing", file_path="A.cs", line=1, column=1)],
            project_context={"files": ["A.cs"]},
        )
        self.assertIn("ADVISORY ONLY", prompt)
        self.assertIn("NO DIRECT TOOL AUTHORITY", prompt)

    # -------------------------------------------------------------------------
    # Test T: Sensitive Data Redaction
    # -------------------------------------------------------------------------
    def test_T_sensitive_data_redaction(self):
        """Test that connection strings, secrets, and credentials are redacted."""
        raw_text = (
            "Connecting with Server=db.prod.com;Database=finance;User ID=sa;Password=SecretProdPassword123!; "
            "Key: AIzaSyB4J09exampleFakeApiKey12345678, "
            "Token: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.fake_token_value_abc"
        )
        redacted = redact_sensitive_vs_data(raw_text)

        self.assertNotIn("SecretProdPassword123!", redacted)
        self.assertNotIn("AIzaSyB4J09exampleFakeApiKey12345678", redacted)
        self.assertIn("[REDACTED_API_KEY]", redacted)
        self.assertIn("[REDACTED]", redacted)

    # -------------------------------------------------------------------------
    # Test U: Emergency Stop
    # -------------------------------------------------------------------------
    def test_U_emergency_stop(self):
        """Test emergency stop freezes all operations immediately."""
        self.gate.trigger_emergency_stop()
        self.assertTrue(self.gate.is_emergency_stopped())

        with self.assertRaises(EmergencyStopActiveError):
            self.gate.validate_tool_name("vs.run_safe_build")

        with self.assertRaises(EmergencyStopActiveError):
            self.gate.validate_file_path(self.test_dir / "App.cs")

        # State machine transition to STOPPED
        sm = VSStateMachine()
        sm.transition(UnifiedVSState.STOPPED, reason="Emergency stop test")
        self.assertEqual(sm.current_state, UnifiedVSState.STOPPED)

        # Deactivate
        self.gate.deactivate_emergency_stop()
        self.assertFalse(self.gate.is_emergency_stopped())

    # -------------------------------------------------------------------------
    # Test V: State Machine Transitions
    # -------------------------------------------------------------------------
    def test_V_state_machine_transitions(self):
        """Test deterministic state transitions and rejection of invalid jumps."""
        sm = VSStateMachine()
        self.assertEqual(sm.current_state, UnifiedVSState.IDLE)

        # Valid progression: IDLE -> PLANNING -> INSPECTING -> BUILDING -> COMPLETED
        sm.transition(UnifiedVSState.PLANNING)
        self.assertEqual(sm.current_state, UnifiedVSState.PLANNING)

        sm.transition(UnifiedVSState.INSPECTING)
        self.assertEqual(sm.current_state, UnifiedVSState.INSPECTING)

        sm.transition(UnifiedVSState.BUILDING)
        self.assertEqual(sm.current_state, UnifiedVSState.BUILDING)

        sm.transition(UnifiedVSState.COMPLETED)
        self.assertEqual(sm.current_state, UnifiedVSState.COMPLETED)

        # Invalid transition: COMPLETED -> BUILDING
        with self.assertRaises(VSSafetyError):
            sm.transition(UnifiedVSState.BUILDING)

    # -------------------------------------------------------------------------
    # Test W: Two Repair Attempt Bound
    # -------------------------------------------------------------------------
    def test_W_two_repair_attempt_bound(self):
        """Test that autonomous repair halts after at most 2 failed attempts."""
        agent = UnifiedVisualStudioAgent(
            workspace_root=self.test_dir,
            audit_logger=self.audit,
        )

        # Mock repair engine to fail build on both attempts
        call_count = 0
        def fake_attempt(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return VSRepairResult(
                success=False,
                error_message=f"Attempt {call_count} failed to fix build",
                attempt_number=call_count,
            )

        with patch.object(agent.repair_engine, "attempt_repair", side_effect=fake_attempt):
            with patch.object(agent.tools, "run_safe_build", return_value=VSToolResult(
                success=False,
                tool_name="vs.run_safe_build",
                error_code="BUILD_FAILED",
                error_message="Compilation error CS0103",
                output="error CS0103",
            )):
                report = agent.execute_workflow("repair vs build")

        self.assertFalse(report.success)
        self.assertLessEqual(report.repair_attempts, 2)
        self.assertIn("Halted after maximum 2 repair attempts", report.summary)

    # -------------------------------------------------------------------------
    # Test X: Deterministic Evidence Precedence
    # -------------------------------------------------------------------------
    def test_X_deterministic_evidence_precedence(self):
        """Test that deterministic ground-truth evidence overrides model claims."""
        failed_result = UnifiedVSResult(
            request_id="req-123",
            workflow_id="wf-123",
            goal="build solution",
            workflow_type=UnifiedVSWorkflowType.BUILD_PROJECT,
            state=UnifiedVSState.FAILED,
            success=False,
            error_domain=UnifiedVSErrorDomain.BUILD_ERROR,
            error_message="error CS0103: The name 'xyz' does not exist",
            evidence={"exit_code": 1, "errors_count": 1},
        )

        # Model hallucinating or claiming build succeeded
        model_claim = {
            "success": True,
            "build_success": True,
            "claim": "I have successfully compiled the solution with 0 errors.",
        }

        verdict = verify_workflow_outcome(failed_result, model_claim=model_claim)
        self.assertFalse(verdict["verified"])
        self.assertTrue(verdict["overridden"])
        self.assertIn("OVERRIDE", verdict["override_reason"])
        self.assertEqual(verdict["deterministic_evidence"]["exit_code"], 1)

    # -------------------------------------------------------------------------
    # Test Y: Audit Trail Logging
    # -------------------------------------------------------------------------
    def test_Y_audit_trail_logging(self):
        """Test comprehensive audit logging for tool executions and repairs."""
        audit_dir = self.test_dir / "audit_test"
        audit = AuditLogger(log_dir=str(audit_dir))

        registry = VSToolRegistry(workspace_root=self.test_dir, audit_logger=audit)
        f_sample = self.test_dir / "AuditSample.cs"
        f_sample.write_text("class AuditSample { }", encoding="utf-8")

        res = registry.execute("vs.read_code", {"file_path": str(f_sample), "max_lines": 10})
        self.assertTrue(res.success)

        # Read audit log
        entries = audit.get_entries()
        self.assertGreaterEqual(len(entries), 1)
        self.assertTrue(any("vs.read_code" in str(e) for e in entries))

    # -------------------------------------------------------------------------
    # Test Z: Companion Routing
    # -------------------------------------------------------------------------
    def test_Z_companion_routing(self):
        """Test NRCompanion classifies and routes Visual Studio commands."""
        companion = NRCompanion(workspace=str(self.test_dir))

        vs_queries = [
            "vs: build solution",
            "inspect solution App.sln",
            "dotnet build",
            "fix vs build error",
            "run vs test",
            "inspect vs project",
        ]

        for q in vs_queries:
            cat = companion.classify_command(q)
            self.assertEqual(cat, CommandCategory.VISUAL_STUDIO, f"Query '{q}' failed to route to VISUAL_STUDIO")

        # Verify route execution mock
        with patch.object(companion.vs_agent, "execute_workflow") as mock_exec:
            mock_exec.return_value = UnifiedVSResult(
                request_id="test",
                workflow_id="wf-test",
                goal="build solution",
                workflow_type=UnifiedVSWorkflowType.BUILD_PROJECT,
                state=UnifiedVSState.COMPLETED,
                success=True,
                summary="Build succeeded.",
            )
            resp = companion.route_command("vs: build solution")
            self.assertEqual(resp.category, CommandCategory.VISUAL_STUDIO)
            self.assertEqual(resp.routed_to, "UnifiedVisualStudioAgent")
            self.assertIn("Build succeeded", resp.text)


if __name__ == "__main__":
    unittest.main()
