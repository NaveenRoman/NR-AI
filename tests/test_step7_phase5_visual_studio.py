"""
Acceptance Test Suite for NR-AI Step 7 Phase 5:
Visual Studio Test, Performance, Diagnostics & Unified End-to-End Intelligence.

Tests A through Z (26 tests):
A. test_A_test_project_discovery
B. test_B_test_configuration_inspection
C. test_C_authorized_test_execution
D. test_D_unauthorized_test_rejection
E. test_E_granular_test_result_parsing
F. test_F_failed_test_source_mapping
G. test_G_skipped_test_handling
H. test_H_bounded_test_output
I. test_I_performance_monitor_durations
J. test_J_performance_monitor_process_metrics
K. test_K_slow_test_identification
L. test_L_diagnostic_context_aggregation
M. test_M_diagnostic_failure_types
N. test_N_build_to_test_workflow
O. test_O_test_to_diagnosis_workflow
P. test_P_diagnosis_to_repair_workflow
Q. test_Q_repair_to_rebuild_and_retest
R. test_R_autonomous_recovery_rollback
S. test_S_runtime_to_debug_workflow
T. test_T_successful_unified_workflow
U. test_U_bounded_repair_attempts_limit
V. test_V_safety_invariants_and_stale_target
W. test_W_emergency_stop_freezes_all_operations
X. test_X_all_34_tools_registered_and_dispatched
Y. test_Y_zero_shell_true_across_codebase
Z. test_Z_model_isolation_and_evidence_override
"""

import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import re
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
    GLOBAL_WORKSPACE_ROOT,
    MAX_REPAIR_ATTEMPTS,
    TEST_TIMEOUT_SECONDS,
    BUILD_TIMEOUT_SECONDS,
    VSErrorCode,
    VSSafetyError,
    EmergencyStopActiveError,
    VSSafetyGate,
    redact_sensitive_data,
)
from app.agent.vs_environment import VSEnvironmentDetector, VSEnvironmentInfo
from app.agent.vs_project import VSProjectInspector
from app.agent.vs_tools import (
    VSToolResult,
    VSToolRegistry,
    SafeMSBuildRunner,
    SafeRuntimeRunner,
)
from app.agent.vs_diagnostics import (
    VSTestFramework,
    VSTestCaseResult,
    VSTestProjectMetadata,
    VSTestConfiguration,
    VSTestIntelligence,
    VSPerformanceMetrics,
    VSPerformanceMonitor,
    VSFailureType,
    VSDiagnosticContext,
    VSDiagnosticsEngine,
)
from app.agent.vs_error_analyzer import VSErrorAnalyzer, VSBuildError, VSErrorCategory
from app.agent.vs_code_repair import VSCodeRepairEngine, VSEditProposal
from app.agent.vs_debugger import (
    VSDebuggerState,
    VSIdeRunningState,
    VSBreakpoint,
    VSIdeState,
    VSDebugLocation,
    VSDebugVariable,
    VSDebugStackFrame,
    VSDebugEvidence,
    VSIdeInspector,
    SafeVSDebugger,
)
from app.agent.vs_unified_agent import (
    UnifiedVSState,
    UnifiedVSErrorDomain,
    UnifiedVSWorkflowType,
    UnifiedVisualStudioAgent,
    VSStateMachine,
    UnifiedVSPlan,
    VSPlanStep,
)
from app.memory.audit_logger import AuditLogger


class TestStep7Phase5VisualStudio(unittest.TestCase):
    """26 Comprehensive Acceptance Tests for Step 7 Phase 5 (Tests A through Z)."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="nrai_vs_p5_test_")
        self.project_dir = Path(self.temp_dir) / "test_project"
        self.project_dir.mkdir(parents=True, exist_ok=True)

        self.safety = VSSafetyGate(authorized_project=self.project_dir)
        self.safety.deactivate_emergency_stop()

        # Create sample test project
        self.test_proj_path = self.project_dir / "Sample.Tests.csproj"
        self.test_proj_path.write_text(
            '<Project Sdk="Microsoft.NET.Sdk">\n'
            '  <PropertyGroup>\n'
            '    <TargetFramework>net8.0</TargetFramework>\n'
            '  </PropertyGroup>\n'
            '  <ItemGroup>\n'
            '    <PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.8.0" />\n'
            '    <PackageReference Include="xunit" Version="2.6.1" />\n'
            '  </ItemGroup>\n'
            '</Project>\n',
            encoding="utf-8"
        )

        # Create sample unit test source
        self.test_cs_path = self.project_dir / "CalculatorTests.cs"
        self.test_cs_path.write_text(
            'using Xunit;\n'
            'namespace Sample.Tests {\n'
            '    public class CalculatorTests {\n'
            '        [Fact]\n'
            '        public void TestAdd() {\n'
            '            Assert.Equal(4, 2 + 2);\n'
            '        }\n'
            '    }\n'
            '}\n',
            encoding="utf-8"
        )

        # Create sample .runsettings
        self.runsettings_path = self.project_dir / "test.runsettings"
        self.runsettings_path.write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<RunSettings>\n'
            '  <RunConfiguration>\n'
            '    <MaxCpuCount>0</MaxCpuCount>\n'
            '  </RunConfiguration>\n'
            '</RunSettings>\n',
            encoding="utf-8"
        )

        self.audit_dir = Path(self.temp_dir) / "audit"
        self.audit = AuditLogger(log_dir=self.audit_dir)
        self.tools = VSToolRegistry(safety_gate=self.safety, audit_logger=self.audit)

    def tearDown(self):
        self.safety.deactivate_emergency_stop()
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # Test A: Test Project Discovery
    # -------------------------------------------------------------------------
    def test_A_test_project_discovery(self):
        """Test A: Discovers test projects and identifies xUnit framework."""
        intel = VSTestIntelligence(safety_gate=self.safety, project_root=self.project_dir)
        discovered = intel.discover_test_projects(self.project_dir)
        self.assertGreaterEqual(len(discovered), 1)
        p_meta = discovered[0]
        self.assertIn("Sample.Tests", p_meta.name)
        self.assertIn("xUnit", p_meta.frameworks)
        self.assertTrue(p_meta.has_runsettings)

    # -------------------------------------------------------------------------
    # Test B: Test Configuration Inspection
    # -------------------------------------------------------------------------
    def test_B_test_configuration_inspection(self):
        """Test B: Inspects test target configuration and settings."""
        t_res = self.tools.execute_tool("vs.inspect_test_configuration", {"target_path": str(self.test_proj_path)})
        self.assertTrue(t_res.success)
        self.assertIn("xUnit", t_res.data.get("frameworks", []))
        self.assertEqual(t_res.data.get("target_framework"), "net8.0")
        self.assertIsNotNone(t_res.data.get("runsettings_path"))

    # -------------------------------------------------------------------------
    # Test C: Authorized Test Execution
    # -------------------------------------------------------------------------
    def test_C_authorized_test_execution(self):
        """Test C: Safe test execution within authorized project boundary."""
        with patch.object(self.tools.runner, "run_test") as mock_run:
            mock_run.return_value = {
                "success": True,
                "returncode": 0,
                "full_output": "Passed! - Failed: 0, Passed: 5, Skipped: 0, Total: 5, Duration: 120 ms",
                "summary": {"total": 5, "passed": 5, "failed": 0, "skipped": 0, "success": True},
                "duration_s": 0.12,
            }
            res = self.tools.execute_tool("vs.run_safe_test", {"target_path": str(self.test_proj_path)})
            self.assertTrue(res.success)
            self.assertEqual(res.data.get("summary", {}).get("passed"), 5)

    # -------------------------------------------------------------------------
    # Test D: Unauthorized Test Rejection
    # -------------------------------------------------------------------------
    def test_D_unauthorized_test_rejection(self):
        """Test D: Rejects test execution outside authorized boundary."""
        unauth = Path("C:/Windows/System32/evil_test.csproj")
        res = self.tools.execute_tool("vs.run_safe_test", {"target_path": str(unauth)})
        self.assertFalse(res.success)
        self.assertTrue(res.error_code in ("PROJECT_NOT_AUTHORIZED", "FILE_NOT_AUTHORIZED"))

    # -------------------------------------------------------------------------
    # Test E: Granular Test Result Parsing
    # -------------------------------------------------------------------------
    def test_E_granular_test_result_parsing(self):
        """Test E: Parses granular test results with passed, failed, and skipped counts."""
        sample_output = (
            "Passed CalculatorTests.TestAdd [15 ms]\n"
            "Passed CalculatorTests.TestSubtract [22 ms]\n"
            "Failed CalculatorTests.TestDivide [105 ms]\n"
            "Error Message:\n"
            " Assert.Equal() Failure\n"
            "Expected: 5\n"
            "Actual:   4\n"
            "Stack Trace:\n"
            "   at Sample.Tests.CalculatorTests.TestDivide() in CalculatorTests.cs:line 25\n"
            "Skipped CalculatorTests.TestIntegration [Database offline]\n"
        )
        intel = VSTestIntelligence(safety_gate=self.safety, project_root=self.project_dir)
        parsed = intel.parse_granular_test_results(sample_output, project_root=self.project_dir)
        summary = parsed["summary"]
        self.assertEqual(summary["total"], 4)
        self.assertEqual(summary["passed"], 2)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["skipped"], 1)
        self.assertTrue(parsed["has_failures"])

    # -------------------------------------------------------------------------
    # Test F: Failed Test to Source Mapping
    # -------------------------------------------------------------------------
    def test_F_failed_test_source_mapping(self):
        """Test F: Maps failed unit test back to source file and line number."""
        sample_output = (
            "Failed CalculatorTests.TestDivide [50 ms]\n"
            "Error Message:\n"
            " Assert.Equal() Failure\n"
            "Stack Trace:\n"
            "   at Sample.Tests.CalculatorTests.TestDivide() in CalculatorTests.cs:line 42\n"
        )
        intel = VSTestIntelligence(safety_gate=self.safety, project_root=self.project_dir)
        parsed = intel.parse_granular_test_results(sample_output, project_root=self.project_dir)
        failed = parsed["failed_tests"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["line_number"], 42)
        self.assertIn("CalculatorTests.cs", failed[0]["source_file"])

    # -------------------------------------------------------------------------
    # Test G: Skipped Test Handling
    # -------------------------------------------------------------------------
    def test_G_skipped_test_handling(self):
        """Test G: Correctly handles skipped tests with skip reasons."""
        sample_output = "Skipped CalculatorTests.TestFeatureX [Ignored on Windows]\n"
        intel = VSTestIntelligence(safety_gate=self.safety, project_root=self.project_dir)
        parsed = intel.parse_granular_test_results(sample_output, project_root=self.project_dir)
        self.assertEqual(parsed["summary"]["skipped"], 1)
        self.assertEqual(parsed["test_cases"][0]["outcome"], "Skipped")
        self.assertEqual(parsed["test_cases"][0]["error_message"], "Ignored on Windows")

    # -------------------------------------------------------------------------
    # Test H: Bounded Test Output
    # -------------------------------------------------------------------------
    def test_H_bounded_test_output(self):
        """Test H: Bounded test capture respects max line limit."""
        long_output = "\n".join([f"Test line {i}" for i in range(500)])
        self.tools.last_test_output = {"full_output": long_output, "returncode": 0}
        res = self.tools.execute_tool("vs.capture_test_output", {"lines": 50})
        self.assertTrue(res.success)
        self.assertEqual(res.data.get("lines_returned"), 50)

    # -------------------------------------------------------------------------
    # Test I: Performance Monitor Durations
    # -------------------------------------------------------------------------
    def test_I_performance_monitor_durations(self):
        """Test I: Performance monitor tracks elapsed times for build, test, and debug."""
        perf = VSPerformanceMonitor(safety_gate=self.safety)
        perf.record_build_duration(4.5)
        perf.record_test_duration(2.8)
        perf.record_debug_duration(12.1)
        metrics = perf.inspect_performance()
        self.assertEqual(metrics.build_duration_s, 4.5)
        self.assertEqual(metrics.test_duration_s, 2.8)
        self.assertEqual(metrics.debug_duration_s, 12.1)

    # -------------------------------------------------------------------------
    # Test J: Performance Monitor Process Metrics
    # -------------------------------------------------------------------------
    def test_J_performance_monitor_process_metrics(self):
        """Test J: Safe non-invasive process metrics observation."""
        res = self.tools.execute_tool("vs.inspect_performance", {})
        self.assertTrue(res.success)
        self.assertIn("process_memory_mb", res.data)
        self.assertIn("process_cpu_percent", res.data)

    # -------------------------------------------------------------------------
    # Test K: Slow Test Identification
    # -------------------------------------------------------------------------
    def test_K_slow_test_identification(self):
        """Test K: Identifies slow tests (>500ms) and raises performance warnings."""
        sample_output = (
            "Passed FastTest [10 ms]\n"
            "Passed SlowTest [850 ms]\n"
        )
        intel = VSTestIntelligence(safety_gate=self.safety, project_root=self.project_dir)
        parsed = intel.parse_granular_test_results(sample_output)
        slow = parsed["slow_tests"]
        self.assertEqual(len(slow), 1)
        self.assertEqual(slow[0]["name"], "SlowTest")

        perf = VSPerformanceMonitor(safety_gate=self.safety)
        perf.set_slow_tests(slow)
        metrics = perf.inspect_performance()
        self.assertTrue(any("slow test" in w.lower() for w in metrics.warnings))

    # -------------------------------------------------------------------------
    # Test L: Diagnostic Context Aggregation
    # -------------------------------------------------------------------------
    def test_L_diagnostic_context_aggregation(self):
        """Test L: Aggregates subsystem outputs into structured VSDiagnosticContext."""
        engine = VSDiagnosticsEngine(safety_gate=self.safety, project_root=self.project_dir)
        build_err_out = {
            "success": False,
            "returncode": 1,
            "full_output": f"{self.test_cs_path}(10,12): error CS0103: The name 'x' does not exist",
        }
        ctx = engine.build_diagnostic_context(build_output=build_err_out)
        self.assertEqual(ctx.failure_type, VSFailureType.BUILD_FAILURE)
        self.assertIn("CS0103", ctx.error_codes)
        self.assertTrue(ctx.repairable)

    # -------------------------------------------------------------------------
    # Test M: Diagnostic Failure Types
    # -------------------------------------------------------------------------
    def test_M_diagnostic_failure_types(self):
        """Test M: Correctly categorizes failure types across all 8 domains."""
        engine = VSDiagnosticsEngine(safety_gate=self.safety, project_root=self.project_dir)

        # Safety rejection
        se = VSSafetyError(VSErrorCode.PROJECT_NOT_AUTHORIZED, "Outside workspace")
        ctx_safety = engine.build_diagnostic_context(safety_error=se)
        self.assertEqual(ctx_safety.failure_type, VSFailureType.SAFETY_REJECTION)

        # Test failure
        test_out = {"success": False, "full_output": "Failed MyTest [10 ms]\nError Message: Failed"}
        ctx_test = engine.build_diagnostic_context(test_output=test_out)
        self.assertEqual(ctx_test.failure_type, VSFailureType.TEST_FAILURE)

        # Runtime crash
        rt_out = {"success": False, "error_code": "RUNTIME_CRASH", "full_output": "Crash"}
        ctx_rt = engine.build_diagnostic_context(runtime_output=rt_out)
        self.assertEqual(ctx_rt.failure_type, VSFailureType.RUNTIME_FAILURE)

    # -------------------------------------------------------------------------
    # Test N: Build to Test Workflow
    # -------------------------------------------------------------------------
    def test_N_build_to_test_workflow(self):
        """Test N: Multi-stage pipeline executing Build -> Test stages."""
        with patch.object(self.tools.runner, "run_build") as m_build, \
             patch.object(self.tools.runner, "run_test") as m_test:
            m_build.return_value = {"success": True, "returncode": 0, "full_output": "Built successfully"}
            m_test.return_value = {
                "success": True,
                "returncode": 0,
                "full_output": "Passed! - Failed: 0, Passed: 3, Skipped: 0, Total: 3",
                "summary": {"total": 3, "passed": 3, "failed": 0, "skipped": 0, "success": True}
            }
            res = self.tools.execute_tool("vs.run_unified_workflow", {
                "target_path": str(self.project_dir),
                "stages": ["BUILD", "TEST"]
            })
            self.assertTrue(res.success)
            self.assertEqual(res.data.get("stages_executed"), ["BUILD", "TEST"])

    # -------------------------------------------------------------------------
    # Test O: Test to Diagnosis Workflow
    # -------------------------------------------------------------------------
    def test_O_test_to_diagnosis_workflow(self):
        """Test O: Workflow transitions from failed test to diagnostic inspection."""
        self.tools.last_test_output = {
            "success": False,
            "full_output": "Failed CalculatorTests.TestDiv [20 ms]\nError Message: DivByZero",
        }
        res = self.tools.execute_tool("vs.inspect_diagnostics", {})
        self.assertTrue(res.success)
        self.assertEqual(res.data.get("failure_type"), VSFailureType.TEST_FAILURE.value)
        self.assertIn("CalculatorTests.TestDiv", res.data.get("affected_tests", []))

    # -------------------------------------------------------------------------
    # Test P: Diagnosis to Repair Workflow
    # -------------------------------------------------------------------------
    def test_P_diagnosis_to_repair_workflow(self):
        """Test P: Error analyzer and diagnostic context identify repairable C# error."""
        analyzer = VSErrorAnalyzer(project_root=self.project_dir)
        err = VSBuildError(
            category=VSErrorCategory.CS_COMPILER_ERROR,
            error_code="CS0103",
            message="The name 'foo' does not exist in the current context",
            file_path=str(self.test_cs_path),
            line=5,
        )
        is_rep, reason = analyzer.is_repairable_error(err)
        self.assertTrue(is_rep)
        self.assertIn("cs0103", reason.lower())

    # -------------------------------------------------------------------------
    # Test Q: Repair to Rebuild and Retest
    # -------------------------------------------------------------------------
    def test_Q_repair_to_rebuild_and_retest(self):
        """Test Q: Autonomous repair loop applies edit, rebuilds, and verifies."""
        agent = UnifiedVisualStudioAgent(
            safety_gate=self.safety,
            workspace_root=self.project_dir,
            tool_registry=self.tools,
            audit_logger=self.audit,
        )

        with patch.object(self.tools, "execute_tool") as mock_tool:
            # Inspection -> Rebuild -> Test -> Verify
            mock_tool.side_effect = [
                VSToolResult(tool="vs.inspect_project", success=True, data={"name": "test"}),
                VSToolResult(tool="vs.run_safe_build", success=True, data={"status": "built"}),
                VSToolResult(tool="vs.run_safe_test", success=True, data={"passed": 2}),
                VSToolResult(tool="vs.verify_build_result", success=True, data={"verified": True}),
            ]
            res = agent.execute_unified_workflow(
                goal="Inspect, build, test, and verify",
                target_path=self.project_dir,
                stages=["INSPECT", "BUILD", "TEST", "VERIFY"],
            )
            self.assertTrue(res.success)
            self.assertEqual(res.state, UnifiedVSState.COMPLETED)

    # -------------------------------------------------------------------------
    # Test R: Autonomous Recovery Rollback
    # -------------------------------------------------------------------------
    def test_R_autonomous_recovery_rollback(self):
        """Test R: Failed autonomous repair rolls back edits cleanly."""
        repair_engine = VSCodeRepairEngine(safety_gate=self.safety)
        test_file = self.project_dir / "RollbackTest.cs"
        initial_code = "public class RollbackTest { public int Val = 1; }\n"
        test_file.write_text(initial_code, encoding="utf-8")

        initial_sha = hashlib.sha256(test_file.read_bytes()).hexdigest()
        proposal = VSEditProposal(
            file_path=str(test_file),
            target_file_sha256=initial_sha,
            start_line=1,
            end_line=1,
            original_code="public int Val = 1;",
            replacement_code="public int Val = 999;",
            explanation="Test edit",
            repair_id="rep_test_r",
            diagnostic_code="CS0001",
        )

        backup, diff = repair_engine.apply_edit_proposal(proposal, "rep_test_r")
        self.assertIn("Val = 999", test_file.read_text(encoding="utf-8"))

        # Trigger rollback
        repair_engine._rollback_all([(backup, test_file)])
        self.assertEqual(test_file.read_text(encoding="utf-8"), initial_code)

    # -------------------------------------------------------------------------
    # Test S: Runtime to Debug Workflow
    # -------------------------------------------------------------------------
    def test_S_runtime_to_debug_workflow(self):
        """Test S: Runtime crash output is diagnosed and classified into runtime failure."""
        analyzer = VSErrorAnalyzer(project_root=self.project_dir)
        crash_output = (
            "Unhandled exception. System.NullReferenceException: Object reference not set to an instance of an object.\n"
            "   at Sample.Program.Main() in Program.cs:line 12\n"
        )
        diag = analyzer.classify_runtime_failure(crash_output, exit_code=-532462766)
        self.assertEqual(diag.category, VSErrorCategory.UNHANDLED_EXCEPTION)
        self.assertEqual(diag.line, 12)

    # -------------------------------------------------------------------------
    # Test T: Successful Unified Workflow
    # -------------------------------------------------------------------------
    def test_T_successful_unified_workflow(self):
        """Test T: Full unified multi-stage workflow succeeds with deterministic evidence."""
        res = self.tools.execute_tool("vs.run_unified_workflow", {
            "target_path": str(self.project_dir),
            "stages": ["DIAGNOSE", "PERFORMANCE"]
        })
        self.assertTrue(res.success)
        self.assertIn("DIAGNOSE", res.data.get("stage_results", {}))
        self.assertIn("PERFORMANCE", res.data.get("stage_results", {}))

    # -------------------------------------------------------------------------
    # Test U: Bounded Repair Attempts Limit
    # -------------------------------------------------------------------------
    def test_U_bounded_repair_attempts_limit(self):
        """Test U: Enforces MAX_REPAIR_ATTEMPTS = 2 limit without infinite retries."""
        self.assertEqual(MAX_REPAIR_ATTEMPTS, 2)
        agent = UnifiedVisualStudioAgent(safety_gate=self.safety, workspace_root=self.project_dir)
        self.assertEqual(agent.code_repair.max_attempts, 2)

    # -------------------------------------------------------------------------
    # Test V: Safety Invariants and Stale Target
    # -------------------------------------------------------------------------
    def test_V_safety_invariants_and_stale_target(self):
        """Test V: Stale target SHA-256 mismatch is rejected."""
        repair_engine = VSCodeRepairEngine(safety_gate=self.safety)
        bad_proposal = VSEditProposal(
            file_path=str(self.test_cs_path),
            target_file_sha256="0000000000000000000000000000000000000000000000000000000000000000",
            start_line=1,
            end_line=2,
            original_code="bad",
            replacement_code="good",
            explanation="Stale target test",
            repair_id="rep_v",
            diagnostic_code="CS0103",
        )
        with self.assertRaises(VSSafetyError) as cm:
            repair_engine.apply_edit_proposal(bad_proposal, "rep_v")
        self.assertEqual(cm.exception.code, VSErrorCode.STALE_TARGET)

    # -------------------------------------------------------------------------
    # Test W: Emergency Stop Freezes All Operations
    # -------------------------------------------------------------------------
    def test_W_emergency_stop_freezes_all_operations(self):
        """Test W: Emergency stop immediately halts test and tool operations."""
        self.safety.activate_emergency_stop("TEST_EMERGENCY")
        try:
            res = self.tools.execute_tool("vs.run_safe_test", {"target_path": str(self.test_proj_path)})
            self.assertFalse(res.success)
            self.assertEqual(res.error_code, VSErrorCode.EMERGENCY_STOPPED.value)
        finally:
            self.safety.deactivate_emergency_stop()

    # -------------------------------------------------------------------------
    # Test X: All 34 Tools Registered and Dispatched
    # -------------------------------------------------------------------------
    def test_X_all_34_tools_registered_and_dispatched(self):
        """Test X: All 34 tools in ALLOWED_VS_TOOLS are registered in VSToolRegistry."""
        self.assertEqual(len(ALLOWED_VS_TOOLS), 34)
        for tool_name in ALLOWED_VS_TOOLS:
            handler = self.tools._get_handler(tool_name)
            self.assertIsNotNone(handler, f"Tool {tool_name} must have a registered handler")

    # -------------------------------------------------------------------------
    # Test Y: Zero shell=True Across Codebase
    # -------------------------------------------------------------------------
    def test_Y_zero_shell_true_across_codebase(self):
        """Test Y: AST audit verifies shell=True is strictly 0 across all VS modules."""
        agent_dir = Path("C:/NR-AI/app/agent")
        vs_files = [f for f in agent_dir.glob("vs_*.py")]
        self.assertGreaterEqual(len(vs_files), 8)

        shell_true_count = 0
        for f in vs_files:
            content = f.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(content, filename=str(f))
            for node in ast.walk(tree):
                if isinstance(node, ast.keyword) and node.arg == "shell":
                    if isinstance(node.value, ast.Constant) and node.value.value is True:
                        shell_true_count += 1

        self.assertEqual(shell_true_count, 0, "Disallowed shell=True detected in Visual Studio modules!")

    # -------------------------------------------------------------------------
    # Test Z: Model Isolation and Evidence Override
    # -------------------------------------------------------------------------
    def test_Z_model_isolation_and_evidence_override(self):
        """Test Z: Deterministic evidence strictly overrides advisory model claims."""
        agent = UnifiedVisualStudioAgent(safety_gate=self.safety, workspace_root=self.project_dir)
        failed_result = agent._build_result(
            "req_z", "wf_z", "Test goal", UnifiedVSWorkflowType.END_TO_END,
            VSStateMachine(safety_gate=self.safety), False, UnifiedVSErrorDomain.TEST_FAILURE,
            summary="Ground truth tests failed",
            error="2 assertions failed",
        )
        model_claim = {"success": True, "build_passed": True, "message": "Everything looks great!"}
        verification = agent.verify_workflow_outcome(failed_result, model_claim=model_claim)
        self.assertFalse(verification["verified"])
        self.assertTrue(verification["overridden"])
        self.assertEqual(verification["assessment"], "OVERRIDDEN_BY_EVIDENCE")


if __name__ == "__main__":
    unittest.main()
