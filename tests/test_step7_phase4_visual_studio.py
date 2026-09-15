"""
Acceptance Test Suite for NR-AI Step 7 Phase 4:
Visual Studio IDE Intelligence & Controlled Debugging.

Tests A through Z (26 tests):
A. test_A_ide_running_detection
B. test_B_ide_not_running_detection
C. test_C_ide_instances_inspection
D. test_D_active_document_inspection
E. test_E_active_document_out_of_workspace_rejection
F. test_F_active_document_protected_file_rejection
G. test_G_breakpoint_set_and_query
H. test_H_breakpoint_duplicate_idempotent
I. test_I_breakpoint_remove
J. test_J_breakpoint_invalid_line_rejection
K. test_K_breakpoint_invalid_extension_rejection
L. test_L_breakpoint_out_of_workspace_rejection
M. test_M_breakpoint_stale_hash_rejection
N. test_N_breakpoint_capacity_limit
O. test_O_debug_target_validation
P. test_P_debug_session_lifecycle
Q. test_Q_debug_session_invalid_transitions
R. test_R_debug_locals_inspection
S. test_S_debug_locals_redaction
T. test_T_debug_location_inspection
U. test_U_emergency_stop_debugger
V. test_V_model_isolation_and_determinism
W. test_W_all_29_tools_registered_and_dispatched
X. test_X_no_shell_true
Y. test_Y_audit_logging_debug_events
Z. test_Z_unified_agent_debug_workflow
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
    MAX_BREAKPOINTS,
    MAX_DEBUG_LOCALS,
    DEBUG_TIMEOUT_SECONDS,
    VSErrorCode,
    VSSafetyError,
    EmergencyStopActiveError,
    VSSafetyGate,
    redact_sensitive_data,
    redact_sensitive_vs_data,
)
from app.agent.vs_environment import VSEnvironmentDetector, VSEnvironmentInfo
from app.agent.vs_project import VSProjectInspector
from app.agent.vs_tools import (
    VSToolResult,
    VSToolRegistry,
    SafeMSBuildRunner,
    SafeRuntimeRunner,
)
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
)
from app.memory.audit_logger import AuditLogger


class TestStep7Phase4VisualStudio(unittest.TestCase):
    """26 Comprehensive Acceptance Tests for Step 7 Phase 4 (Tests A through Z)."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="nrai_vs_p4_test_")
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
                'using System;\nnamespace TestApp {\n    class Program {\n        static void Main() {\n            Console.WriteLine("OK");\n        }\n    }\n}\n',
                encoding="utf-8",
            )
            (self.project_dir / "SampleApp.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>',
                encoding="utf-8",
            )

        # Ensure Program.cs has at least 15 lines for breakpoint tests
        prog_path = self.project_dir / "Program.cs"
        if prog_path.exists():
            content = prog_path.read_text(encoding="utf-8")
            lines = content.splitlines()
            if len(lines) < 15:
                padding = [f"            // Line {i}" for i in range(len(lines), 20)]
                padded = lines[:-2] + padding + lines[-2:]
                prog_path.write_text("\n".join(padded) + "\n", encoding="utf-8")

        self.audit_dir = Path(self.temp_dir) / "audit"
        self.audit_logger = AuditLogger(log_dir=self.audit_dir)
        self.safety_gate = VSSafetyGate(authorized_project=self.project_dir)
        self.ide_inspector = VSIdeInspector(safety_gate=self.safety_gate)
        self.debugger = SafeVSDebugger(
            safety_gate=self.safety_gate,
            ide_inspector=self.ide_inspector,
            audit_logger=self.audit_logger,
        )
        self.debugger.set_mock_backend(True)

        self.registry = VSToolRegistry(
            safety_gate=self.safety_gate,
            workspace_root=self.project_dir,
            audit_logger=self.audit_logger,
            ide_inspector=self.ide_inspector,
            debugger=self.debugger,
        )

        self.unified_agent = UnifiedVisualStudioAgent(
            workspace_root=self.project_dir,
            safety_gate=self.safety_gate,
            audit_logger=self.audit_logger,
            ide_inspector=self.ide_inspector,
            debugger=self.debugger,
            tool_registry=self.registry,
        )

    def tearDown(self):
        self.safety_gate.deactivate_emergency_stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # Test A: IDE running detection
    # -------------------------------------------------------------------------
    def test_A_ide_running_detection(self):
        # Test mock state
        mock_state = VSIdeState(
            is_running=True,
            running_state=VSIdeRunningState.RUNNING,
            instance_id="devenv_1234",
            version="17.11.0",
        )
        self.ide_inspector.set_mock_state(mock_state)
        state = self.ide_inspector.inspect_ide()
        self.assertTrue(state.is_running)
        self.assertEqual(state.running_state, VSIdeRunningState.RUNNING)
        self.assertEqual(state.instance_id, "devenv_1234")

        # Test tool dispatch
        res = self.registry.execute_tool("vs.inspect_ide_state")
        self.assertTrue(res.success)
        self.assertTrue(res.data["is_running"])
        self.ide_inspector.set_mock_state(None)

    # -------------------------------------------------------------------------
    # Test B: IDE not running detection
    # -------------------------------------------------------------------------
    def test_B_ide_not_running_detection(self):
        # Force non-running mock state
        mock_state = VSIdeState(
            is_running=False,
            running_state=VSIdeRunningState.NOT_DETECTED,
            instance_id=None,
        )
        self.ide_inspector.set_mock_state(mock_state)
        state = self.ide_inspector.inspect_ide()
        self.assertFalse(state.is_running)
        self.assertEqual(state.running_state, VSIdeRunningState.NOT_DETECTED)
        self.assertIsNone(state.instance_id)

        res = self.registry.execute_tool("vs.inspect_ide_state")
        self.assertTrue(res.success)
        self.assertFalse(res.data["is_running"])
        self.ide_inspector.set_mock_state(None)

    # -------------------------------------------------------------------------
    # Test C: IDE instances inspection
    # -------------------------------------------------------------------------
    def test_C_ide_instances_inspection(self):
        # Without mock state, inspect_ide should detect solutions/projects in workspace
        state = self.ide_inspector.inspect_ide()
        self.assertIsNotNone(state)
        self.assertIn(state.running_state, [VSIdeRunningState.RUNNING, VSIdeRunningState.NOT_DETECTED])
        # Project should be detected in test directory
        if (self.project_dir / "SampleApp.csproj").exists():
            self.assertIn(state.active_project, ["SampleApp.csproj", "SampleApp.Tests.csproj"])
        self.assertEqual(state.active_configuration, "Debug")

    # -------------------------------------------------------------------------
    # Test D: Active document inspection
    # -------------------------------------------------------------------------
    def test_D_active_document_inspection(self):
        prog_path = self.project_dir / "Program.cs"
        res = self.ide_inspector.inspect_active_document(file_path=str(prog_path), cursor_line=5)
        self.assertTrue(res["success"])
        self.assertEqual(res["file_name"], "Program.cs")
        self.assertEqual(res["cursor_line"], 5)
        self.assertGreater(res["total_lines"], 0)
        self.assertIn("using System", res["preview"])

        # Via tool registry
        tool_res = self.registry.execute_tool(
            "vs.inspect_active_document",
            {"file_path": str(prog_path), "cursor_line": 3},
        )
        self.assertTrue(tool_res.success)
        self.assertEqual(tool_res.data["cursor_line"], 3)

    # -------------------------------------------------------------------------
    # Test E: Active document out-of-workspace rejection
    # -------------------------------------------------------------------------
    def test_E_active_document_out_of_workspace_rejection(self):
        outside_file = Path(tempfile.gettempdir()) / "outside.cs"
        outside_file.write_text("class Outside {}", encoding="utf-8")
        try:
            with self.assertRaises(VSSafetyError) as ctx:
                self.ide_inspector.inspect_active_document(file_path=str(outside_file))
            self.assertEqual(ctx.exception.code, VSErrorCode.FILE_NOT_AUTHORIZED)

            # Via tool registry
            tool_res = self.registry.execute_tool(
                "vs.inspect_active_document",
                {"file_path": str(outside_file)},
            )
            self.assertFalse(tool_res.success)
            self.assertEqual(tool_res.error_code, VSErrorCode.FILE_NOT_AUTHORIZED.value)
        finally:
            if outside_file.exists():
                outside_file.unlink()

    # -------------------------------------------------------------------------
    # Test F: Active document protected file rejection
    # -------------------------------------------------------------------------
    def test_F_active_document_protected_file_rejection(self):
        secret_file = self.project_dir / "secrets.json"
        secret_file.write_text('{"ApiKey": "SECRET123"}', encoding="utf-8")
        with self.assertRaises(VSSafetyError) as ctx:
            self.ide_inspector.inspect_active_document(file_path=str(secret_file))
        self.assertEqual(ctx.exception.code, VSErrorCode.PROTECTED_FILE_REJECTED)

        # Via tool registry
        tool_res = self.registry.execute_tool(
            "vs.inspect_active_document",
            {"file_path": str(secret_file)},
        )
        self.assertFalse(tool_res.success)
        self.assertEqual(tool_res.error_code, VSErrorCode.PROTECTED_FILE_REJECTED.value)

    # -------------------------------------------------------------------------
    # Test G: Breakpoint set and query
    # -------------------------------------------------------------------------
    def test_G_breakpoint_set_and_query(self):
        prog_path = self.project_dir / "Program.cs"
        bp = self.debugger.set_breakpoint(str(prog_path), 5, condition="x > 0")
        self.assertIsNotNone(bp.id)
        self.assertEqual(bp.line_number, 5)
        self.assertEqual(bp.condition, "x > 0")
        self.assertTrue(bp.enabled)

        bps = self.debugger.list_breakpoints()
        self.assertEqual(len(bps), 1)
        self.assertEqual(bps[0].id, bp.id)

        # Via tool registry
        tool_res = self.registry.execute_tool(
            "vs.set_breakpoint",
            {"file_path": str(prog_path), "line_number": 7},
        )
        self.assertTrue(tool_res.success)
        self.assertEqual(tool_res.data["line_number"], 7)

    # -------------------------------------------------------------------------
    # Test H: Breakpoint duplicate idempotent
    # -------------------------------------------------------------------------
    def test_H_breakpoint_duplicate_idempotent(self):
        prog_path = self.project_dir / "Program.cs"
        bp1 = self.debugger.set_breakpoint(str(prog_path), 5, condition=None)
        bp2 = self.debugger.set_breakpoint(str(prog_path), 5, condition="count == 10")
        self.assertEqual(bp1.id, bp2.id)
        self.assertEqual(bp2.condition, "count == 10")
        self.assertEqual(len(self.debugger.list_breakpoints()), 1)

    # -------------------------------------------------------------------------
    # Test I: Breakpoint remove
    # -------------------------------------------------------------------------
    def test_I_breakpoint_remove(self):
        prog_path = self.project_dir / "Program.cs"
        bp = self.debugger.set_breakpoint(str(prog_path), 5)
        self.assertEqual(len(self.debugger.list_breakpoints()), 1)

        res = self.debugger.remove_breakpoint(bp.id)
        self.assertTrue(res)
        self.assertEqual(len(self.debugger.list_breakpoints()), 0)

        # Non-existent breakpoint removal fails
        with self.assertRaises(VSSafetyError) as ctx:
            self.debugger.remove_breakpoint("bp_999")
        self.assertEqual(ctx.exception.code, VSErrorCode.BREAKPOINT_NOT_FOUND)

        # Via tool registry
        tool_res = self.registry.execute_tool("vs.remove_breakpoint", {"breakpoint_id": "bp_999"})
        self.assertFalse(tool_res.success)
        self.assertEqual(tool_res.error_code, VSErrorCode.BREAKPOINT_NOT_FOUND.value)

    # -------------------------------------------------------------------------
    # Test J: Breakpoint invalid line rejection
    # -------------------------------------------------------------------------
    def test_J_breakpoint_invalid_line_rejection(self):
        prog_path = self.project_dir / "Program.cs"
        # Line 0 or negative
        with self.assertRaises(VSSafetyError) as ctx0:
            self.debugger.set_breakpoint(str(prog_path), 0)
        self.assertEqual(ctx0.exception.code, VSErrorCode.BREAKPOINT_INVALID)

        with self.assertRaises(VSSafetyError) as ctx_neg:
            self.debugger.set_breakpoint(str(prog_path), -5)
        self.assertEqual(ctx_neg.exception.code, VSErrorCode.BREAKPOINT_INVALID)

        # Line exceeding total lines
        with self.assertRaises(VSSafetyError) as ctx_large:
            self.debugger.set_breakpoint(str(prog_path), 99999)
        self.assertEqual(ctx_large.exception.code, VSErrorCode.BREAKPOINT_INVALID)

    # -------------------------------------------------------------------------
    # Test K: Breakpoint invalid extension rejection
    # -------------------------------------------------------------------------
    def test_K_breakpoint_invalid_extension_rejection(self):
        non_cs = self.project_dir / "SampleApp.csproj"
        with self.assertRaises(VSSafetyError) as ctx:
            self.debugger.set_breakpoint(str(non_cs), 1)
        self.assertEqual(ctx.exception.code, VSErrorCode.BREAKPOINT_INVALID)

    # -------------------------------------------------------------------------
    # Test L: Breakpoint out of workspace rejection
    # -------------------------------------------------------------------------
    def test_L_breakpoint_out_of_workspace_rejection(self):
        outside_file = Path(tempfile.gettempdir()) / "outside.cs"
        outside_file.write_text("class Outside { int x; }", encoding="utf-8")
        try:
            with self.assertRaises(VSSafetyError) as ctx:
                self.debugger.set_breakpoint(str(outside_file), 1)
            self.assertEqual(ctx.exception.code, VSErrorCode.FILE_NOT_AUTHORIZED)
        finally:
            if outside_file.exists():
                outside_file.unlink()

        # Path traversal
        traversal = f"{self.project_dir}/../../Windows/System32/calc.cs"
        with self.assertRaises(VSSafetyError) as ctx_trav:
            self.debugger.set_breakpoint(traversal, 1)
        self.assertIn(ctx_trav.exception.code, [VSErrorCode.FILE_NOT_AUTHORIZED, VSErrorCode.PATH_TRAVERSAL_DETECTED])

    # -------------------------------------------------------------------------
    # Test M: Breakpoint stale hash rejection
    # -------------------------------------------------------------------------
    def test_M_breakpoint_stale_hash_rejection(self):
        prog_path = self.project_dir / "Program.cs"
        bad_hash = "0" * 64
        with self.assertRaises(VSSafetyError) as ctx:
            self.debugger.set_breakpoint(str(prog_path), 3, expected_hash=bad_hash)
        self.assertEqual(ctx.exception.code, VSErrorCode.STALE_TARGET)

    # -------------------------------------------------------------------------
    # Test N: Breakpoint capacity limit
    # -------------------------------------------------------------------------
    def test_N_breakpoint_capacity_limit(self):
        prog_path = self.project_dir / "Program.cs"
        # Fill up breakpoints to MAX_BREAKPOINTS (50)
        # Note: We create distinct line numbers or simulate capacity
        self.debugger._breakpoints = {
            f"bp_{i:03d}": VSBreakpoint(id=f"bp_{i:03d}", file_path=str(prog_path), line_number=i)
            for i in range(1, MAX_BREAKPOINTS + 1)
        }
        self.assertEqual(len(self.debugger.list_breakpoints()), MAX_BREAKPOINTS)

        # 51st breakpoint should be rejected
        with self.assertRaises(VSSafetyError) as ctx:
            self.debugger.set_breakpoint(str(prog_path), 1)
        self.assertEqual(ctx.exception.code, VSErrorCode.TOO_MANY_BREAKPOINTS)

    # -------------------------------------------------------------------------
    # Test O: Debug target validation
    # -------------------------------------------------------------------------
    def test_O_debug_target_validation(self):
        proj_path = self.project_dir / "SampleApp.csproj"
        val = self.safety_gate.validate_debug_target(proj_path)
        self.assertEqual(val, proj_path.resolve())

        # Non-existent target
        with self.assertRaises(VSSafetyError) as ctx_non:
            self.safety_gate.validate_debug_target(self.project_dir / "NonExistent.csproj")
        self.assertEqual(ctx_non.exception.code, VSErrorCode.DEBUG_TARGET_UNAUTHORIZED)

        # Unauthorized extension (.bat)
        bad_ext = self.project_dir / "run.bat"
        bad_ext.write_text("@echo off", encoding="utf-8")
        with self.assertRaises(VSSafetyError) as ctx_ext:
            self.safety_gate.validate_debug_target(bad_ext)
        self.assertEqual(ctx_ext.exception.code, VSErrorCode.DEBUG_TARGET_UNAUTHORIZED)

    # -------------------------------------------------------------------------
    # Test P: Debug session lifecycle
    # -------------------------------------------------------------------------
    def test_P_debug_session_lifecycle(self):
        proj_path = self.project_dir / "SampleApp.csproj"
        prog_path = self.project_dir / "Program.cs"

        # Set a breakpoint
        self.debugger.set_breakpoint(str(prog_path), 5)

        # Start session
        ev_start = self.debugger.start_session(proj_path)
        self.assertIsNotNone(ev_start.session_id)
        self.assertEqual(self.debugger.state, VSDebuggerState.PAUSED)
        self.assertIsNotNone(ev_start.location)
        self.assertEqual(ev_start.hit_breakpoint_id, "bp_001")

        # Continue session
        ev_cont = self.debugger.continue_session()
        self.assertEqual(self.debugger.state, VSDebuggerState.RUNNING)

        # Pause session
        ev_pause = self.debugger.pause_session()
        self.assertEqual(self.debugger.state, VSDebuggerState.PAUSED)

        # Stop session
        ev_stop = self.debugger.stop_session()
        self.assertEqual(self.debugger.state, VSDebuggerState.INACTIVE)

    # -------------------------------------------------------------------------
    # Test Q: Debug session invalid transitions
    # -------------------------------------------------------------------------
    def test_Q_debug_session_invalid_transitions(self):
        # Cannot pause when inactive
        with self.assertRaises(VSSafetyError) as ctx_pause:
            self.debugger.pause_session()
        self.assertEqual(ctx_pause.exception.code, VSErrorCode.NO_ACTIVE_DEBUG_SESSION)

        # Cannot continue when inactive
        with self.assertRaises(VSSafetyError) as ctx_cont:
            self.debugger.continue_session()
        self.assertEqual(ctx_cont.exception.code, VSErrorCode.NO_ACTIVE_DEBUG_SESSION)

        # Start session
        proj_path = self.project_dir / "SampleApp.csproj"
        self.debugger.start_session(proj_path)

        # Cannot start again when active
        with self.assertRaises(VSSafetyError) as ctx_start:
            self.debugger.start_session(proj_path)
        self.assertEqual(ctx_start.exception.code, VSErrorCode.DEBUG_SESSION_ACTIVE)

        self.debugger.stop_session()

    # -------------------------------------------------------------------------
    # Test R: Debug locals inspection
    # -------------------------------------------------------------------------
    def test_R_debug_locals_inspection(self):
        # Cannot inspect locals when inactive
        with self.assertRaises(VSSafetyError) as ctx:
            self.debugger.inspect_locals()
        self.assertEqual(ctx.exception.code, VSErrorCode.NO_ACTIVE_DEBUG_SESSION)

        # Start session with breakpoint so it pauses
        proj_path = self.project_dir / "SampleApp.csproj"
        prog_path = self.project_dir / "Program.cs"
        self.debugger.set_breakpoint(str(prog_path), 5)
        self.debugger.start_session(proj_path)

        # Inspect locals while paused
        locals_list = self.debugger.inspect_locals()
        self.assertIsInstance(locals_list, list)
        self.assertGreater(len(locals_list), 0)

        # Via tool registry
        tool_res = self.registry.execute_tool("vs.inspect_debug_locals")
        self.assertTrue(tool_res.success)
        self.assertIn("variables", tool_res.data)

        self.debugger.stop_session()

    # -------------------------------------------------------------------------
    # Test S: Debug locals redaction
    # -------------------------------------------------------------------------
    def test_S_debug_locals_redaction(self):
        # Inject evidence containing sensitive variables
        evidence = VSDebugEvidence(
            session_id="dbg_secret_test",
            state=VSDebuggerState.PAUSED.value,
            variables=[
                VSDebugVariable(name="password", type_name="string", value="password = SuperSecretPwd123"),
                VSDebugVariable(name="token", type_name="string", value="Bearer ghp_123456789012345678901234567890123456"),
                VSDebugVariable(name="normalVar", type_name="int", value="42"),
            ],
        )
        self.debugger.inject_mock_evidence(evidence)

        sanitized = self.debugger.inspect_locals()
        self.assertEqual(len(sanitized), 3)

        pwd_var = next(v for v in sanitized if v.name == "password")
        self.assertTrue(pwd_var.is_redacted)
        self.assertNotIn("SuperSecretPwd123", pwd_var.value)
        self.assertIn("[REDACTED_PASSWORD]", pwd_var.value)

        tok_var = next(v for v in sanitized if v.name == "token")
        self.assertTrue(tok_var.is_redacted)
        self.assertNotIn("ghp_", tok_var.value)

        normal_var = next(v for v in sanitized if v.name == "normalVar")
        self.assertFalse(normal_var.is_redacted)
        self.assertEqual(normal_var.value, "42")

        self.debugger.stop_session()

    # -------------------------------------------------------------------------
    # Test T: Debug location inspection
    # -------------------------------------------------------------------------
    def test_T_debug_location_inspection(self):
        # When inactive, location is None
        self.assertIsNone(self.debugger.get_current_location())

        # Start session and hit breakpoint
        proj_path = self.project_dir / "SampleApp.csproj"
        prog_path = self.project_dir / "Program.cs"
        self.debugger.set_breakpoint(str(prog_path), 5)
        self.debugger.start_session(proj_path)

        loc = self.debugger.get_current_location()
        self.assertIsNotNone(loc)
        self.assertEqual(loc.file_path, str(prog_path.resolve()))
        self.assertEqual(loc.line_number, 5)

        # Via tool registry
        tool_res = self.registry.execute_tool("vs.inspect_debug_location")
        self.assertTrue(tool_res.success)
        self.assertEqual(tool_res.data["line_number"], 5)

        self.debugger.stop_session()

    # -------------------------------------------------------------------------
    # Test U: Emergency stop debugger
    # -------------------------------------------------------------------------
    def test_U_emergency_stop_debugger(self):
        self.safety_gate.activate_emergency_stop("TEST_SAFETY_TRIP")

        with self.assertRaises(EmergencyStopActiveError):
            self.debugger.set_breakpoint(str(self.project_dir / "Program.cs"), 5)

        with self.assertRaises(EmergencyStopActiveError):
            self.debugger.start_session(self.project_dir / "SampleApp.csproj")

        with self.assertRaises(EmergencyStopActiveError):
            self.ide_inspector.inspect_ide()

        # Tool registry should gracefully return error
        res = self.registry.execute_tool("vs.inspect_ide_state")
        self.assertFalse(res.success)
        self.assertEqual(res.error_code, VSErrorCode.EMERGENCY_STOPPED.value)

        self.safety_gate.deactivate_emergency_stop()

    # -------------------------------------------------------------------------
    # Test V: Model isolation and determinism
    # -------------------------------------------------------------------------
    def test_V_model_isolation_and_determinism(self):
        # AI Model proposal cannot directly manipulate breakpoints or debug sessions
        # All changes must pass through VSSafetyGate and SafeVSDebugger validation.
        # Deterministic debugger state overrides any hallucinations:
        evidence = VSDebugEvidence(
            session_id="dbg_det",
            state=VSDebuggerState.PAUSED.value,
            variables=[VSDebugVariable(name="realVal", type_name="int", value="100")],
        )
        self.debugger.inject_mock_evidence(evidence)

        # Model claims value is 9999, but inspect_locals returns real value 100
        locals_list = self.debugger.inspect_locals()
        self.assertEqual(locals_list[0].value, "100")
        self.debugger.stop_session()

    # -------------------------------------------------------------------------
    # Test W: All 29 tools registered and dispatched
    # -------------------------------------------------------------------------
    def test_W_all_29_tools_registered_and_dispatched(self):
        # At least 29 tools in allowlist (expanded in Phase 5)
        self.assertGreaterEqual(len(ALLOWED_VS_TOOLS), 29)

        # The 11 new tools
        new_tools = [
            "vs.inspect_ide_state",
            "vs.inspect_active_document",
            "vs.inspect_debugger_state",
            "vs.set_breakpoint",
            "vs.remove_breakpoint",
            "vs.start_debug_session",
            "vs.stop_debug_session",
            "vs.continue_debug",
            "vs.pause_debug",
            "vs.inspect_debug_location",
            "vs.inspect_debug_locals",
        ]
        for t in new_tools:
            self.assertIn(t, ALLOWED_VS_TOOLS)

        # Unauthorized tool rejected
        res = self.registry.execute_tool("vs.unauthorized_debugger_action")
        self.assertFalse(res.success)
        self.assertEqual(res.error_code, VSErrorCode.TOOL_NOT_ALLOWED.value)

    # -------------------------------------------------------------------------
    # Test X: No shell=True
    # -------------------------------------------------------------------------
    def test_X_no_shell_true(self):
        vs_files = [
            Path(r"C:\NR-AI\app\agent\vs_safety.py"),
            Path(r"C:\NR-AI\app\agent\vs_environment.py"),
            Path(r"C:\NR-AI\app\agent\vs_project.py"),
            Path(r"C:\NR-AI\app\agent\vs_tools.py"),
            Path(r"C:\NR-AI\app\agent\vs_error_analyzer.py"),
            Path(r"C:\NR-AI\app\agent\vs_code_repair.py"),
            Path(r"C:\NR-AI\app\agent\vs_unified_agent.py"),
            Path(r"C:\NR-AI\app\agent\vs_debugger.py"),
        ]

        for p in vs_files:
            if not p.exists():
                continue
            tree = ast.parse(p.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    for kw in node.keywords:
                        if kw.arg == "shell":
                            if isinstance(kw.value, ast.Constant):
                                self.assertFalse(
                                    kw.value.value,
                                    f"shell=True found in {p.name} at line {node.lineno}!",
                                )

    # -------------------------------------------------------------------------
    # Test Y: Audit logging debug events
    # -------------------------------------------------------------------------
    def test_Y_audit_logging_debug_events(self):
        prog_path = self.project_dir / "Program.cs"
        proj_path = self.project_dir / "SampleApp.csproj"

        self.debugger.set_breakpoint(str(prog_path), 5)
        self.debugger.start_session(proj_path)
        self.debugger.continue_session()
        self.debugger.pause_session()
        self.debugger.stop_session()

        # Check audit log entries
        all_entries = list(self.audit_logger.entries)
        if not all_entries:
            log_files = list(self.audit_dir.glob("*.json"))
            for lf in log_files:
                try:
                    data = json.loads(lf.read_text(encoding="utf-8"))
                    if isinstance(data, list):
                        all_entries.extend(data)
                    elif isinstance(data, dict):
                        all_entries.append(data)
                except Exception:
                    pass

        self.assertGreater(len(all_entries), 0)
        event_names = [e.get("event_type") for e in all_entries]
        self.assertIn("VS_BREAKPOINT_SET", event_names)
        self.assertIn("VS_DEBUG_SESSION_STARTED", event_names)
        self.assertIn("VS_DEBUG_SESSION_PAUSED", event_names)
        self.assertIn("VS_DEBUG_SESSION_STOPPED", event_names)

    # -------------------------------------------------------------------------
    # Test Z: Unified agent debug workflow
    # -------------------------------------------------------------------------
    def test_Z_unified_agent_debug_workflow(self):
        proj_path = self.project_dir / "SampleApp.csproj"
        prog_path = self.project_dir / "Program.cs"

        # End-to-end debug workflow
        result = self.unified_agent.execute_debug_workflow(
            target_path=str(proj_path),
            breakpoints=[{"file_path": str(prog_path), "line_number": 5}],
            inspect_locals=True,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.workflow_type, UnifiedVSWorkflowType.CONTROLLED_DEBUG.value)
        self.assertIn("debug_evidence", result.artifacts)
        ev_data = result.artifacts["debug_evidence"]
        self.assertIsNotNone(ev_data)

        # End-to-end IDE inspect workflow
        res_ide = self.unified_agent.execute_inspect_ide_workflow()
        self.assertTrue(res_ide.success)
        self.assertEqual(res_ide.workflow_type, UnifiedVSWorkflowType.INSPECT_IDE.value)
        self.assertIn("ide_state", res_ide.artifacts)


if __name__ == "__main__":
    unittest.main()
