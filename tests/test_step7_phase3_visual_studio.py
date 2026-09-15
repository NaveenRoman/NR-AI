"""
Acceptance Test Suite for NR-AI Step 7 Phase 3:
Visual Studio Autonomous Repair & End-to-End Workflows.

Tests A through Z (26 tests):
A. test_A_repairable_compiler_error
B. test_B_successful_autonomous_repair
C. test_C_failed_repair_rollback
D. test_D_stale_file_hash_rejection
E. test_E_malformed_model_proposal_rejection
F. test_F_unauthorized_path_rejection
G. test_G_path_traversal_rejection
H. test_H_protected_file_rejection
I. test_I_secret_prohibited_token_rejection
J. test_J_oversized_patch_rejection
K. test_K_too_many_files_rejection
L. test_L_too_many_repair_attempts_rejection
M. test_M_emergency_stop
N. test_N_model_tool_execution_attempt_rejection
O. test_O_model_filesystem_mutation_attempt_rejection
P. test_P_deterministic_evidence_precedence
Q. test_Q_build_failure_to_diagnosis
R. test_R_diagnosis_to_repair_proposal
S. test_S_repair_to_rebuild
T. test_T_rebuild_to_test
U. test_U_test_failure_bounded_recovery
V. test_V_successful_end_to_end_workflow
W. test_W_failed_end_to_end_workflow
X. test_X_rollback_integrity_byte_for_byte
Y. test_Y_audit_logging
Z. test_Z_no_shell_true
"""

import copy
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
    MAX_FILES_CHANGED,
    MAX_PATCH_SIZE_BYTES,
    MAX_LINES_CHANGED,
    MAX_REPAIR_ATTEMPTS,
    VSErrorCode,
    VSSafetyError,
    EmergencyStopActiveError,
    VSSafetyGate,
    redact_sensitive_data,
    redact_sensitive_vs_data,
)
from app.agent.vs_error_analyzer import (
    VSErrorCategory,
    VSBuildError,
    VSErrorAnalyzer,
)
from app.agent.vs_code_repair import (
    DEFAULT_CHECKPOINT_DIR,
    VSEditProposal,
    VSEditBatch,
    VSRepairResult,
    VSCodeRepairEngine,
    extract_bounded_context,
    build_model_repair_prompt,
    parse_model_repair_response,
)
from app.agent.vs_tools import (
    VSToolResult,
    SafeMSBuildRunner,
    SafeRuntimeRunner,
    VSToolRegistry,
)
from app.agent.vs_unified_agent import (
    UnifiedVSState,
    UnifiedVSErrorDomain,
    UnifiedVSWorkflowType,
    VSPlanStep,
    UnifiedVSPlan,
    UnifiedVSResult,
    VSStateMachine,
    UnifiedVisualStudioAgent,
    classify_vs_error,
)
from app.memory.audit_logger import AuditLogger


class TestStep7Phase3VisualStudio(unittest.TestCase):
    """26 Rigorous Acceptance Tests for Step 7 Phase 3 (Tests A through Z)."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="nrai_vs_p3_test_")
        self.project_dir = Path(self.temp_dir) / "test_project"
        self.project_dir.mkdir(parents=True, exist_ok=True)

        # Copy fixture project files into isolated test directory
        fixture_src = Path(r"C:\NR-AI\nr_vs_test")
        if fixture_src.exists():
            for item in fixture_src.iterdir():
                if item.name in ("bin", "obj", ".vs"):
                    continue
                if item.is_file():
                    shutil.copy2(item, self.project_dir / item.name)
                elif item.is_dir():
                    shutil.copytree(item, self.project_dir / item.name)
        else:
            # Create minimal fixture if nr_vs_test is absent
            (self.project_dir / "Program.cs").write_text(
                'using System;\nnamespace TestApp { class Program { static void Main() { Console.WriteLine("OK"); } } }\n',
                encoding="utf-8",
            )
            (self.project_dir / "SampleApp.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>',
                encoding="utf-8",
            )

        self.safety_gate = VSSafetyGate(authorized_project=self.project_dir)
        self.error_analyzer = VSErrorAnalyzer(project_root=self.project_dir)
        self.audit_logger = AuditLogger(log_dir=Path(self.temp_dir) / "audit")
        self.repair_engine = VSCodeRepairEngine(
            safety_gate=self.safety_gate,
            error_analyzer=self.error_analyzer,
            audit_logger=self.audit_logger,
            checkpoint_dir=Path(self.temp_dir) / "checkpoints",
        )

    def tearDown(self):
        self.safety_gate.deactivate_emergency_stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # Test A: Repairable compiler error
    # -------------------------------------------------------------------------
    def test_A_repairable_compiler_error(self):
        prog_cs = self.project_dir / "Program.cs"
        cs_err = VSBuildError(
            category=VSErrorCategory.CS_COMPILER_ERROR,
            error_code="CS1002",
            message="; expected",
            file_path=str(prog_cs),
            line=9,
            column=15,
            diagnosis="Missing semicolon after statement.",
        )
        is_rep, reason = self.error_analyzer.is_repairable_error(cs_err)
        self.assertTrue(is_rep, f"CS1002 should be repairable, got: {reason}")
        self.assertIn("Eligible for autonomous repair", reason)

        # Verify non-repairable categories
        sdk_err = VSBuildError(
            category=VSErrorCategory.MISSING_SDK,
            error_code="MSB4236",
            message="The SDK 'Microsoft.NET.Sdk' specified could not be found.",
            file_path=str(self.project_dir / "SampleApp.csproj"),
        )
        is_rep_sdk, reason_sdk = self.error_analyzer.is_repairable_error(sdk_err)
        self.assertFalse(is_rep_sdk)
        self.assertIn("MISSING_SDK", reason_sdk)

        # Verify permission failure
        perm_err = VSBuildError(
            category=VSErrorCategory.PERMISSION_FAILURE,
            error_code="ACCESS_DENIED",
            message="Access to the path is denied.",
            file_path=str(prog_cs),
        )
        is_rep_perm, _ = self.error_analyzer.is_repairable_error(perm_err)
        self.assertFalse(is_rep_perm)

        # Verify protected file diagnostic
        sec_err = VSBuildError(
            category=VSErrorCategory.CS_COMPILER_ERROR,
            error_code="CS1002",
            message="; expected",
            file_path=str(self.project_dir / "secrets.json"),
        )
        is_rep_sec, reason_sec = self.error_analyzer.is_repairable_error(sec_err)
        self.assertFalse(is_rep_sec)
        self.assertIn("protected security asset", reason_sec)

    # -------------------------------------------------------------------------
    # Test B: Successful autonomous repair
    # -------------------------------------------------------------------------
    def test_B_successful_autonomous_repair(self):
        prog_cs = self.project_dir / "Program.cs"
        broken_code = """using System;

namespace SampleApp
{
    internal class Program
    {
        static void Main(string[] args)
        {
            Console.WriteLine("Broken Hello")
        }
    }
}
"""
        prog_cs.write_text(broken_code, encoding="utf-8")
        current_hash = VSSafetyGate.compute_sha256(prog_cs)

        fixed_line = '            Console.WriteLine("Broken Hello");'
        valid_proposal = {
            "proposal_version": "1.0",
            "proposal_id": "vs_prop_test_b",
            "target_file": str(prog_cs),
            "expected_file_hash": current_hash,
            "start_line": 9,
            "end_line": 9,
            "replacement_text": fixed_line,
            "reason": "Add missing semicolon",
            "diagnostics_addressed": ["CS1002"],
        }

        # Mock runner that fails build initially, then succeeds after repair
        mock_runner = MagicMock(spec=SafeMSBuildRunner)
        mock_runner.run_build.side_effect = [
            {"success": False, "full_output": f"{prog_cs}(9,47): error CS1002: ; expected", "exit_code": 1},
            {"success": True, "full_output": "Build succeeded. 0 Error(s)", "exit_code": 0},
        ]
        engine = VSCodeRepairEngine(
            safety_gate=self.safety_gate,
            msbuild_runner=mock_runner,
            error_analyzer=self.error_analyzer,
            audit_logger=self.audit_logger,
            checkpoint_dir=Path(self.temp_dir) / "checkpoints",
        )

        res = engine.repair_build(
            target_path=self.project_dir / "SampleApp.csproj",
            repair_proposal_provider=lambda err, att, ctx: valid_proposal,
        )

        self.assertTrue(res.success)
        self.assertEqual(res.attempts, 1)
        self.assertIn(str(prog_cs), res.applied_files)
        self.assertIn("Console.WriteLine(\"Broken Hello\");", prog_cs.read_text(encoding="utf-8"))

    # -------------------------------------------------------------------------
    # Test C: Failed repair rollback
    # -------------------------------------------------------------------------
    def test_C_failed_repair_rollback(self):
        prog_cs = self.project_dir / "Program.cs"
        initial_code = prog_cs.read_text(encoding="utf-8")
        initial_hash = VSSafetyGate.compute_sha256(prog_cs)

        # Propose an edit that fails rebuild
        mock_runner = MagicMock(spec=SafeMSBuildRunner)
        mock_runner.run_build.side_effect = [
            {"success": False, "full_output": f"{prog_cs}(9,10): error CS1002: ; expected", "exit_code": 1},
            {"success": False, "full_output": f"{prog_cs}(9,10): error CS1002: ; expected again", "exit_code": 1},
            {"success": False, "full_output": f"{prog_cs}(9,10): error CS1002: ; expected again", "exit_code": 1},
        ]

        def failing_proposal(err, attempt, ctx):
            return {
                "target_file": str(prog_cs),
                "expected_file_hash": VSSafetyGate.compute_sha256(prog_cs),
                "start_line": 9,
                "end_line": 9,
                "replacement_text": "bad code replacement;",
                "reason": "Attempting fix",
            }

        engine = VSCodeRepairEngine(
            safety_gate=self.safety_gate,
            msbuild_runner=mock_runner,
            error_analyzer=self.error_analyzer,
            audit_logger=self.audit_logger,
            checkpoint_dir=Path(self.temp_dir) / "checkpoints",
        )

        res = engine.repair_build(
            target_path=self.project_dir / "SampleApp.csproj",
            max_attempts=2,
            repair_proposal_provider=failing_proposal,
        )

        self.assertFalse(res.success)
        self.assertTrue(res.rolled_back)
        # Verify byte-for-byte fidelity restored
        final_hash = VSSafetyGate.compute_sha256(prog_cs)
        self.assertEqual(initial_hash, final_hash)
        self.assertEqual(initial_code, prog_cs.read_text(encoding="utf-8"))

    # -------------------------------------------------------------------------
    # Test D: Stale file hash rejection
    # -------------------------------------------------------------------------
    def test_D_stale_file_hash_rejection(self):
        prog_cs = self.project_dir / "Program.cs"
        stale_hash = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"

        proposal = {
            "target_file": str(prog_cs),
            "expected_file_hash": stale_hash,
            "start_line": 1,
            "end_line": 1,
            "replacement_text": "// modified line",
            "reason": "Update header",
        }

        # Validate proposal schema succeeds
        parsed = self.repair_engine.validate_proposal_schema(proposal)
        # Applying proposal must detect stale target and reject
        with self.assertRaises(VSSafetyError) as cm:
            self.repair_engine.apply_edit_proposal(parsed, repair_id="test_d")
        self.assertEqual(cm.exception.code, VSErrorCode.STALE_TARGET)

    # -------------------------------------------------------------------------
    # Test E: Malformed model proposal rejection
    # -------------------------------------------------------------------------
    def test_E_malformed_model_proposal_rejection(self):
        # Empty string
        with self.assertRaises(VSSafetyError) as cm:
            parse_model_repair_response("")
        self.assertEqual(cm.exception.code, VSErrorCode.MALFORMED_PROPOSAL)

        # Invalid JSON
        with self.assertRaises(VSSafetyError) as cm:
            parse_model_repair_response("{not valid json")
        self.assertEqual(cm.exception.code, VSErrorCode.MALFORMED_PROPOSAL)

        # JSON array instead of object
        with self.assertRaises(VSSafetyError) as cm:
            parse_model_repair_response("[1, 2, 3]")
        self.assertEqual(cm.exception.code, VSErrorCode.MALFORMED_PROPOSAL)

        # Missing target_file
        with self.assertRaises(VSSafetyError) as cm:
            self.repair_engine.validate_proposal_schema({
                "expected_file_hash": "abc",
                "replacement_text": "x",
            })
        self.assertEqual(cm.exception.code, VSErrorCode.MALFORMED_PROPOSAL)

        # Missing expected_file_hash
        with self.assertRaises(VSSafetyError) as cm:
            self.repair_engine.validate_proposal_schema({
                "target_file": str(self.project_dir / "Program.cs"),
                "replacement_text": "x",
            })
        self.assertEqual(cm.exception.code, VSErrorCode.MALFORMED_PROPOSAL)

        # Invalid line range (end_line < start_line)
        with self.assertRaises(VSSafetyError) as cm:
            self.repair_engine.validate_proposal_schema({
                "target_file": str(self.project_dir / "Program.cs"),
                "expected_file_hash": "abc",
                "start_line": 10,
                "end_line": 5,
                "replacement_text": "x",
            })
        self.assertEqual(cm.exception.code, VSErrorCode.EDIT_VALIDATION_FAILED)

    # -------------------------------------------------------------------------
    # Test F: Unauthorized path rejection
    # -------------------------------------------------------------------------
    def test_F_unauthorized_path_rejection(self):
        outside_dir = Path(tempfile.mkdtemp(prefix="nrai_outside_"))
        outside_file = outside_dir / "outside_unauthorized.cs"
        outside_file.write_text("// outside file", encoding="utf-8")

        proposal = {
            "target_file": str(outside_file),
            "expected_file_hash": "abc",
            "start_line": 1,
            "end_line": 1,
            "replacement_text": "// modified",
        }

        try:
            with self.assertRaises(VSSafetyError) as cm:
                self.repair_engine.validate_proposal_schema(proposal)
            self.assertIn(cm.exception.code, (VSErrorCode.FILE_NOT_AUTHORIZED, VSErrorCode.PATH_TRAVERSAL_DETECTED))
        finally:
            shutil.rmtree(outside_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # Test G: Path traversal rejection
    # -------------------------------------------------------------------------
    def test_G_path_traversal_rejection(self):
        traversal_path = str(self.project_dir / ".." / ".." / "outside.cs")
        proposal = {
            "target_file": traversal_path,
            "expected_file_hash": "abc",
            "start_line": 1,
            "end_line": 1,
            "replacement_text": "// modified",
        }

        with self.assertRaises(VSSafetyError) as cm:
            self.repair_engine.validate_proposal_schema(proposal)
        self.assertIn(cm.exception.code, (VSErrorCode.PATH_TRAVERSAL_DETECTED, VSErrorCode.FILE_NOT_AUTHORIZED))

    # -------------------------------------------------------------------------
    # Test H: Protected file rejection
    # -------------------------------------------------------------------------
    def test_H_protected_file_rejection(self):
        protected_names = [
            "secrets.json",
            ".env",
            "appsettings.production.json",
            "key.snk",
            "certificate.pfx",
        ]

        for name in protected_names:
            prot_file = self.project_dir / name
            prot_file.write_text("protected content", encoding="utf-8")
            proposal = {
                "target_file": str(prot_file),
                "expected_file_hash": "abc",
                "start_line": 1,
                "end_line": 1,
                "replacement_text": "new content",
            }
            with self.assertRaises(VSSafetyError) as cm:
                self.repair_engine.validate_proposal_schema(proposal)
            self.assertEqual(cm.exception.code, VSErrorCode.PROTECTED_FILE_REJECTED)

    # -------------------------------------------------------------------------
    # Test I: Secret/prohibited-token rejection
    # -------------------------------------------------------------------------
    def test_I_secret_prohibited_token_rejection(self):
        prog_cs = self.project_dir / "Program.cs"
        current_hash = VSSafetyGate.compute_sha256(prog_cs)

        prohibited_replacements = [
            'string key = "AIzaSyB1234567890abcdef1234567890abc";',
            'string pem = "-----BEGIN RSA PRIVATE KEY-----";',
            'string pass = "password=SuperSecretPassword123!";',
            'string clientSecret = "client_secret = \'xyz1234567890\'";',
        ]

        for repl in prohibited_replacements:
            proposal = {
                "target_file": str(prog_cs),
                "expected_file_hash": current_hash,
                "start_line": 1,
                "end_line": 1,
                "replacement_text": repl,
            }
            with self.assertRaises(VSSafetyError) as cm:
                self.repair_engine.validate_proposal_schema(proposal)
            self.assertEqual(cm.exception.code, VSErrorCode.EDIT_VALIDATION_FAILED)

    # -------------------------------------------------------------------------
    # Test J: Oversized patch rejection
    # -------------------------------------------------------------------------
    def test_J_oversized_patch_rejection(self):
        prog_cs = self.project_dir / "Program.cs"
        current_hash = VSSafetyGate.compute_sha256(prog_cs)

        # 105 KB string exceeds 100 KB limit (102,400 bytes)
        huge_text = "// " + ("x" * 105_000)

        proposal = {
            "target_file": str(prog_cs),
            "expected_file_hash": current_hash,
            "start_line": 1,
            "end_line": 1,
            "replacement_text": huge_text,
        }

        with self.assertRaises(VSSafetyError) as cm:
            self.repair_engine.validate_proposal_schema(proposal)
        self.assertEqual(cm.exception.code, VSErrorCode.PATCH_TOO_LARGE)

    # -------------------------------------------------------------------------
    # Test K: Too many files rejection
    # -------------------------------------------------------------------------
    def test_K_too_many_files_rejection(self):
        # Create 6 files (limit is 5)
        props = []
        for i in range(6):
            f = self.project_dir / f"ExtraFile{i}.cs"
            f.write_text(f"// file {i}\n", encoding="utf-8")
            h = VSSafetyGate.compute_sha256(f)
            props.append(VSEditProposal(
                file_path=str(f),
                expected_sha256=h,
                start_line=1,
                end_line=1,
                replacement_text=f"// updated {i}",
            ))

        batch = VSEditBatch(proposals=props)
        with self.assertRaises(VSSafetyError) as cm:
            self.repair_engine.validate_batch(batch)
        self.assertEqual(cm.exception.code, VSErrorCode.TOO_MANY_FILES_CHANGED)

    # -------------------------------------------------------------------------
    # Test L: Too many repair attempts rejection (max 2)
    # -------------------------------------------------------------------------
    def test_L_too_many_repair_attempts_rejection(self):
        prog_cs = self.project_dir / "Program.cs"

        mock_runner = MagicMock(spec=SafeMSBuildRunner)
        # Always fail builds
        mock_runner.run_build.return_value = {
            "success": False,
            "full_output": f"{prog_cs}(9,10): error CS1002: ; expected",
            "exit_code": 1,
        }

        engine = VSCodeRepairEngine(
            safety_gate=self.safety_gate,
            msbuild_runner=mock_runner,
            error_analyzer=self.error_analyzer,
            audit_logger=self.audit_logger,
            checkpoint_dir=Path(self.temp_dir) / "checkpoints",
        )

        attempts_recorded = []

        def counting_provider(err, attempt, ctx):
            attempts_recorded.append(attempt)
            return {
                "target_file": str(prog_cs),
                "expected_file_hash": VSSafetyGate.compute_sha256(prog_cs),
                "start_line": 9,
                "end_line": 9,
                "replacement_text": f"// attempt {attempt};\n",
            }

        # Request 10 attempts, engine must strictly clamp to 2
        res = engine.repair_build(
            target_path=self.project_dir / "SampleApp.csproj",
            max_attempts=10,
            repair_proposal_provider=counting_provider,
        )

        self.assertFalse(res.success)
        self.assertEqual(res.attempts, 2)
        self.assertEqual(attempts_recorded, [1, 2])
        self.assertTrue(res.rolled_back)

    # -------------------------------------------------------------------------
    # Test M: Emergency stop
    # -------------------------------------------------------------------------
    def test_M_emergency_stop(self):
        self.safety_gate.trigger_emergency_stop("Operator halted test")
        self.assertTrue(self.safety_gate.is_emergency_stop_active())

        # repair_build must raise EmergencyStopActiveError
        with self.assertRaises(EmergencyStopActiveError):
            self.repair_engine.repair_build(target_path=self.project_dir / "SampleApp.csproj")

        # UnifiedVS agent execute_workflow must stop immediately
        agent = UnifiedVisualStudioAgent(
            safety_gate=self.safety_gate,
            error_analyzer=self.error_analyzer,
            audit_logger=self.audit_logger,
            workspace_root=self.project_dir,
        )
        wf_res = agent.execute_workflow("Build project")
        self.assertEqual(wf_res.state, UnifiedVSState.STOPPED)
        self.assertEqual(wf_res.error_code, VSErrorCode.EMERGENCY_STOPPED.value)
        self.safety_gate.deactivate_emergency_stop()

    # -------------------------------------------------------------------------
    # Test N: Model tool-execution attempt rejection
    # -------------------------------------------------------------------------
    def test_N_model_tool_execution_attempt_rejection(self):
        forbidden_payloads = [
            '{"tool_call": "vs.run_safe_build", "params": {}}',
            '{"command": "dotnet build -c Release"}',
            '{"tool": "shell_exec", "args": ["rm", "-rf"]}',
            '{"execute": "cmd.exe /c dir"}',
            '{"shell": "powershell Get-Process"}',
            '{"exec": "whoami"}',
        ]

        for payload in forbidden_payloads:
            with self.assertRaises(VSSafetyError) as cm:
                parse_model_repair_response(payload)
            self.assertEqual(cm.exception.code, VSErrorCode.MALFORMED_PROPOSAL)

    # -------------------------------------------------------------------------
    # Test O: Model filesystem-mutation attempt rejection
    # -------------------------------------------------------------------------
    def test_O_model_filesystem_mutation_attempt_rejection(self):
        # Even if wrapped inside a valid-looking proposal object, presence of tool/shell keys is rejected
        prog_cs = self.project_dir / "Program.cs"
        dirty_proposal = {
            "target_file": str(prog_cs),
            "expected_file_hash": VSSafetyGate.compute_sha256(prog_cs),
            "start_line": 1,
            "end_line": 1,
            "replacement_text": "// test",
            "shell": "rm -rf /",
        }

        with self.assertRaises(VSSafetyError) as cm:
            self.repair_engine.validate_proposal_schema(dirty_proposal)
        self.assertEqual(cm.exception.code, VSErrorCode.MALFORMED_PROPOSAL)

    # -------------------------------------------------------------------------
    # Test P: Deterministic evidence precedence
    # -------------------------------------------------------------------------
    def test_P_deterministic_evidence_precedence(self):
        agent = UnifiedVisualStudioAgent(
            safety_gate=self.safety_gate,
            workspace_root=self.project_dir,
        )

        failed_result = UnifiedVSResult(
            request_id="req_1",
            workflow_id="wf_1",
            goal="Build project",
            workflow_type=UnifiedVSWorkflowType.BUILD_PROJECT,
            state=UnifiedVSState.FAILED,
            success=False,
            error_domain=UnifiedVSErrorDomain.BUILD_ERROR,
            error_code="CS1002",
            error_message="Compilation error CS1002: ; expected",
        )

        # Model hallucinates that the build succeeded
        hallucinated_claim = {
            "claim": "I have successfully fixed and verified the build. Zero errors.",
            "status": "success",
            "build_passed": True,
        }

        outcome = agent.verify_workflow_outcome(failed_result, hallucinated_claim)
        self.assertFalse(outcome["verified"])
        self.assertTrue(outcome["overridden"])
        self.assertEqual(outcome["assessment"], "OVERRIDDEN_BY_EVIDENCE")

    # -------------------------------------------------------------------------
    # Test Q: Build failure -> diagnosis
    # -------------------------------------------------------------------------
    def test_Q_build_failure_to_diagnosis(self):
        prog_cs = self.project_dir / "Program.cs"
        raw_output = f"{prog_cs}(10,13): error CS1002: ; expected [{self.project_dir / 'SampleApp.csproj'}]"

        errors = self.error_analyzer.analyze(raw_output)
        self.assertEqual(len(errors), 1)
        err = errors[0]
        self.assertEqual(err.category, VSErrorCategory.CS_COMPILER_ERROR)
        self.assertEqual(err.error_code, "CS1002")
        self.assertEqual(err.line, 10)
        self.assertEqual(err.column, 13)
        self.assertIn("CS1002", err.diagnosis)

    # -------------------------------------------------------------------------
    # Test R: Diagnosis -> repair proposal
    # -------------------------------------------------------------------------
    def test_R_diagnosis_to_repair_proposal(self):
        prog_cs = self.project_dir / "Program.cs"
        err = VSBuildError(
            category=VSErrorCategory.CS_COMPILER_ERROR,
            error_code="CS1002",
            message="; expected",
            file_path=str(prog_cs),
            line=9,
            diagnosis="Missing semicolon.",
        )

        ctx = extract_bounded_context(err, self.project_dir, max_lines_context=5)
        self.assertIn("context_code", ctx)
        self.assertEqual(ctx["file_sha256"], VSSafetyGate.compute_sha256(prog_cs))
        self.assertLessEqual(ctx["end_line"] - ctx["start_line"] + 1, 11)

        sys_prompt, user_prompt = build_model_repair_prompt(err, ctx)
        self.assertIn("proposal_version", sys_prompt)
        self.assertIn("expected_file_hash", sys_prompt)
        self.assertIn(ctx["file_sha256"], user_prompt)

    # -------------------------------------------------------------------------
    # Test S: Repair -> rebuild
    # -------------------------------------------------------------------------
    def test_S_repair_to_rebuild(self):
        prog_cs = self.project_dir / "Program.cs"
        orig_hash = VSSafetyGate.compute_sha256(prog_cs)

        proposal = VSEditProposal(
            file_path=str(prog_cs),
            expected_sha256=orig_hash,
            start_line=9,
            end_line=9,
            replacement_text='            Console.WriteLine("Rebuild Test");',
        )

        # Applying edit proposal creates checkpoint and updates file
        bk, diff = self.repair_engine.apply_edit_proposal(proposal, repair_id="test_s")
        self.assertTrue(bk.exists())
        self.assertTrue(len(diff) > 0)
        self.assertIn("Rebuild Test", prog_cs.read_text(encoding="utf-8"))

        # State transition check
        sm = VSStateMachine(safety_gate=self.safety_gate)
        sm.current_state = UnifiedVSState.APPLYING_REPAIR
        sm.transition(UnifiedVSState.REBUILDING, "Triggering rebuild after repair")
        self.assertEqual(sm.current_state, UnifiedVSState.REBUILDING)

    # -------------------------------------------------------------------------
    # Test T: Rebuild -> test
    # -------------------------------------------------------------------------
    def test_T_rebuild_to_test(self):
        sm = VSStateMachine(safety_gate=self.safety_gate)
        sm.current_state = UnifiedVSState.REBUILDING
        sm.transition(UnifiedVSState.TESTING, "Rebuild passed, executing tests")
        self.assertEqual(sm.current_state, UnifiedVSState.TESTING)

        sm.transition(UnifiedVSState.VERIFYING, "Tests passed, verifying outcomes")
        self.assertEqual(sm.current_state, UnifiedVSState.VERIFYING)

        sm.transition(UnifiedVSState.COMPLETED, "Workflow complete")
        self.assertEqual(sm.current_state, UnifiedVSState.COMPLETED)

    # -------------------------------------------------------------------------
    # Test U: Test failure -> bounded recovery
    # -------------------------------------------------------------------------
    def test_U_test_failure_bounded_recovery(self):
        prog_cs = self.project_dir / "Program.cs"
        unit_test_cs = self.project_dir / "UnitTest1.cs"
        if not unit_test_cs.exists():
            unit_test_cs.write_text("using System;\nnamespace SampleApp.Tests { public class UnitTest1 { public void TestPass() {} } }\n", encoding="utf-8")

        test_fail_output = f"Failed TestPass [1 ms]\nError Message: Assertion failed\nStack Trace:\n  at SampleApp.Tests.UnitTest1.TestPass() in {unit_test_cs}:line 10"

        # Mock runner where:
        # Build 1 fails -> proposal 1 applied -> Rebuild 1 passes -> Test 1 fails ->
        # Diagnostic extracted -> proposal 2 applied -> Rebuild 2 passes -> Test 2 passes!
        mock_runner = MagicMock(spec=SafeMSBuildRunner)
        mock_runner.run_build.side_effect = [
            {"success": False, "full_output": f"{prog_cs}(9,10): error CS1002: ; expected", "exit_code": 1},
            {"success": True, "full_output": "Build succeeded", "exit_code": 0},
            {"success": True, "full_output": "Build succeeded", "exit_code": 0},
        ]
        mock_runner.run_test.side_effect = [
            {"success": False, "full_output": test_fail_output, "exit_code": 1},
            {"success": True, "full_output": "Passed! - Failed: 0, Passed: 1, Skipped: 0", "exit_code": 0},
        ]

        def dynamic_provider(err, attempt, ctx):
            target = prog_cs if attempt == 1 else unit_test_cs
            return {
                "target_file": str(target),
                "expected_file_hash": VSSafetyGate.compute_sha256(target),
                "start_line": 1,
                "end_line": 1,
                "replacement_text": f'// Fixed on attempt {attempt}\n',
                "reason": f"Repair attempt {attempt}",
            }

        engine = VSCodeRepairEngine(
            safety_gate=self.safety_gate,
            msbuild_runner=mock_runner,
            error_analyzer=self.error_analyzer,
            audit_logger=self.audit_logger,
            checkpoint_dir=Path(self.temp_dir) / "checkpoints",
        )

        res = engine.repair_build(
            target_path=self.project_dir / "SampleApp.csproj",
            test_target=self.project_dir / "SampleApp.Tests.csproj",
            run_tests=True,
            repair_proposal_provider=dynamic_provider,
        )

        self.assertTrue(res.success)
        self.assertEqual(res.attempts, 2)

    # -------------------------------------------------------------------------
    # Test V: Successful end-to-end workflow
    # -------------------------------------------------------------------------
    def test_V_successful_end_to_end_workflow(self):
        # Mock tools registry to simulate clean execution
        agent = UnifiedVisualStudioAgent(
            safety_gate=self.safety_gate,
            workspace_root=self.project_dir,
            audit_logger=self.audit_logger,
        )

        agent.tools.execute_tool = MagicMock()
        agent.tools.execute_tool.side_effect = lambda name, params: VSToolResult(
            tool_name=name,
            success=True,
            data={"status": "OK"},
            message="Tool executed successfully",
        )

        res = agent.execute_workflow(
            goal="Inspect, build and test solution",
            target_path=self.project_dir / "SampleApp.csproj",
            allow_repair=True,
        )

        self.assertTrue(res.success)
        self.assertEqual(res.state, UnifiedVSState.COMPLETED)
        self.assertEqual(res.error_domain, UnifiedVSErrorDomain.NONE)

    # -------------------------------------------------------------------------
    # Test W: Failed end-to-end workflow
    # -------------------------------------------------------------------------
    def test_W_failed_end_to_end_workflow(self):
        agent = UnifiedVisualStudioAgent(
            safety_gate=self.safety_gate,
            workspace_root=self.project_dir,
            audit_logger=self.audit_logger,
        )

        # Simulate unrepairable MSB4236 error
        agent.tools.execute_tool = MagicMock()
        agent.tools.execute_tool.side_effect = [
            # vs.inspect_project
            VSToolResult(tool_name="vs.inspect_project", success=True, data={}),
            # vs.run_safe_build
            VSToolResult(
                tool_name="vs.run_safe_build",
                success=False,
                error="The SDK Microsoft.NET.Sdk could not be found",
                output="error MSB4236: The SDK 'Microsoft.NET.Sdk' specified could not be found.",
                error_code="MSB4236",
            ),
            # vs.capture_build_output
            VSToolResult(
                tool_name="vs.capture_build_output",
                success=True,
                output="error MSB4236: The SDK 'Microsoft.NET.Sdk' specified could not be found.",
            ),
        ]

        res = agent.execute_workflow(
            goal="Build and verify application",
            target_path=self.project_dir / "SampleApp.csproj",
            allow_repair=True,
        )

        self.assertFalse(res.success)
        self.assertEqual(res.state, UnifiedVSState.FAILED)
        self.assertEqual(res.error_domain, UnifiedVSErrorDomain.REPAIR_FAILURE)
        self.assertIn("cannot safely be repaired automatically", res.summary)

    # -------------------------------------------------------------------------
    # Test X: Rollback integrity (byte-for-byte fidelity)
    # -------------------------------------------------------------------------
    def test_X_rollback_integrity_byte_for_byte(self):
        prog_cs = self.project_dir / "Program.cs"
        initial_bytes = prog_cs.read_bytes()
        initial_sha256 = VSSafetyGate.compute_sha256(prog_cs)

        # Create checkpoint
        bk = self.repair_engine.create_checkpoint(prog_cs, repair_id="test_x")
        self.assertTrue(bk.exists())
        self.assertEqual(bk.read_bytes(), initial_bytes)

        # Mutate the file
        prog_cs.write_bytes(b"MUTATED BYTE STREAM CONTENT")
        self.assertNotEqual(prog_cs.read_bytes(), initial_bytes)

        # Restore checkpoint
        self.repair_engine.restore_checkpoint(bk, prog_cs)
        restored_bytes = prog_cs.read_bytes()
        restored_sha256 = VSSafetyGate.compute_sha256(prog_cs)

        # Byte-for-byte equality verification
        self.assertEqual(initial_bytes, restored_bytes)
        self.assertEqual(initial_sha256, restored_sha256)

    # -------------------------------------------------------------------------
    # Test Y: Audit logging
    # -------------------------------------------------------------------------
    def test_Y_audit_logging(self):
        logger = AuditLogger(log_dir=Path(self.temp_dir) / "audit")

        logger.log_event("VS_REPAIR_SUCCESS", {
            "repair_id": "vs_rep_test_y",
            "file": "Program.cs",
            "diff": "+ fixed line",
        }, status="success")

        logger.log_event("VS_ROLLBACK", {
            "repair_id": "vs_rep_test_y",
            "reason": "Rebuild failed",
        }, status="warning")

        self.assertTrue(logger.log_file.exists())
        data = json.loads(logger.log_file.read_text(encoding="utf-8"))
        self.assertEqual(data["total_events"], 2)
        events = data["events"]
        self.assertEqual(events[0]["event_type"], "VS_REPAIR_SUCCESS")
        self.assertEqual(events[0]["status"], "success")
        self.assertEqual(events[1]["event_type"], "VS_ROLLBACK")
        self.assertEqual(events[1]["status"], "warning")

    # -------------------------------------------------------------------------
    # Test Z: No shell=True
    # -------------------------------------------------------------------------
    def test_Z_no_shell_true(self):
        vs_module_dir = Path(r"C:\NR-AI\app\agent")
        vs_files = list(vs_module_dir.glob("vs_*.py"))
        self.assertGreaterEqual(len(vs_files), 5, "Expected at least 5 vs_*.py modules")

        violations = []
        for vf in vs_files:
            content = vf.read_text(encoding="utf-8", errors="ignore")
            for idx, line in enumerate(content.splitlines(), start=1):
                clean = line.strip()
                if clean.startswith("#"):
                    continue
                if "shell=True" in clean or "shell = True" in clean:
                    violations.append(f"{vf.name}:{idx}: {clean}")

        self.assertEqual(
            len(violations), 0,
            f"Found forbidden shell=True in Visual Studio modules: {violations}"
        )


if __name__ == "__main__":
    unittest.main()
