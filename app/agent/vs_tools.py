"""
NR-AI Visual Studio Safe Tool Execution Engine (Step 7).

Implements the bounded allowlist of 12 deterministic Visual Studio development tools.
Strictly forbids arbitrary shell execution, isolates processes with shell=False,
and validates all inputs via VSSafetyGate.
"""

from dataclasses import dataclass, field
import logging
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional, Union

from app.agent.vs_safety import (
    VSSafetyGate,
    VSErrorCode,
    VSSafetyError,
    ALLOWED_VS_TOOLS,
    BUILD_TIMEOUT_SECONDS,
    TEST_TIMEOUT_SECONDS,
    RUNTIME_TIMEOUT_SECONDS,
    MAX_BUILD_OUTPUT_BYTES,
    MAX_BUILD_OUTPUT_LINES,
    MAX_RUNTIME_OUTPUT_BYTES,
    MAX_RUNTIME_OUTPUT_LINES,
    GLOBAL_WORKSPACE_ROOT,
    redact_sensitive_data,
)
from app.agent.vs_environment import VSEnvironmentDetector, VSEnvironmentInfo
from app.agent.vs_project import VSProjectInspector, VSProjectMetadata, VSSolutionMetadata
from app.agent.vs_error_analyzer import VSErrorAnalyzer, VSBuildError, VSErrorCategory
from app.agent.vs_debugger import (
    VSIdeInspector,
    SafeVSDebugger,
    VSDebuggerState,
    VSIdeState,
    VSBreakpoint,
    VSDebugEvidence,
    VSDebugLocation,
    VSDebugVariable,
    VSDebugStackFrame,
)
from app.memory.audit_logger import AuditLogger
from app.agent.vs_diagnostics import (
    VSTestIntelligence,
    VSPerformanceMonitor,
    VSDiagnosticsEngine,
    VSFailureType,
    VSDiagnosticContext,
    VSTestConfiguration,
    VSTestProjectMetadata,
    VSTestCaseResult,
    VSTestFramework,
)

logger = logging.getLogger("NRAI.VSTools")



@dataclass
class VSToolResult:
    """Structured, verifiable result returned by all vs.* tools."""
    tool: str = ""
    success: bool = False
    data: Dict[str, Any] = field(default_factory=dict)
    message: str = ""
    error: Optional[str] = None
    error_code: Optional[str] = None
    output: str = ""
    verified: bool = False
    duration_s: float = 0.0
    timestamp: float = field(default_factory=time.time)
    tool_name: Optional[str] = None
    error_message: Optional[str] = None

    def __post_init__(self):
        if self.tool_name and not self.tool:
            self.tool = self.tool_name
        elif self.tool and not self.tool_name:
            self.tool_name = self.tool
        if self.error_message and not self.error:
            self.error = self.error_message
        elif self.error and not self.error_message:
            self.error_message = self.error

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool": self.tool,
            "success": self.success,
            "data": self.data,
            "message": self.message,
            "error": self.error,
            "error_code": self.error_code,
            "output": self.output,
            "verified": self.verified,
            "duration_s": round(self.duration_s, 2),
            "timestamp": self.timestamp,
        }


class SafeMSBuildRunner:
    """
    Deterministic build and test runner for Visual Studio & .NET.
    Executes dotnet or msbuild strictly with shell=False and parameterized arguments.
    """

    def __init__(
        self,
        safety_gate: Optional[VSSafetyGate] = None,
        env_detector: Optional[VSEnvironmentDetector] = None,
        audit_logger: Optional[Any] = None,
    ):
        self.safety = safety_gate or VSSafetyGate()
        self.env = env_detector or VSEnvironmentDetector()
        self.audit = audit_logger
        self.perf_monitor: Optional[VSPerformanceMonitor] = None

    def run_build(
        self,
        target_path: Path,
        action: str = "BUILD",
        configuration: str = "Debug",
        platform: Optional[str] = None,
        properties: Optional[Dict[str, str]] = None,
        target_framework: Optional[str] = None,
        max_lines: int = MAX_BUILD_OUTPUT_LINES,
        max_bytes: int = MAX_BUILD_OUTPUT_BYTES,
        timeout: float = BUILD_TIMEOUT_SECONDS,
    ) -> Dict[str, Any]:
        """Executes an authorized build action on a project or solution file with bounded capture."""
        self.safety.check_emergency_stop()
        validated_action = self.safety.validate_build_action(action)
        validated_target = self.safety.validate_file_path(target_path, check_writable=False)
        val_timeout = self.safety.validate_timeout(timeout, max_timeout=BUILD_TIMEOUT_SECONDS)

        env_info = self.env.detect()
        dotnet = env_info.dotnet_path
        msbuild = env_info.msbuild_path

        if not dotnet and not msbuild:
            return {
                "success": False,
                "returncode": -1,
                "error_code": VSErrorCode.MSBUILD_NOT_FOUND.value,
                "output_sample": "Neither .NET SDK (dotnet) nor MSBuild was found on the system.",
                "full_output": "MSBuild or dotnet missing.",
                "duration_s": 0.0,
            }

        cmd: List[str] = []
        if dotnet:
            cmd.append(dotnet)
            if validated_action == "CLEAN":
                cmd.extend(["clean", str(validated_target), "-c", configuration])
            elif validated_action == "REBUILD":
                cmd.extend(["build", str(validated_target), "-c", configuration, "--no-incremental"])
            elif validated_action == "RESTORE":
                cmd.extend(["restore", str(validated_target)])
            else:
                cmd.extend(["build", str(validated_target), "-c", configuration])

            if platform:
                cmd.extend(["-p:Platform=" + platform])
            if target_framework:
                cmd.extend(["-f", target_framework])
            if properties:
                for k, v in properties.items():
                    if re.match(r'^[A-Za-z0-9_]+$', str(k)) and not any(bad in str(v) for bad in ("|", "&", ";", ">", "<")):
                        cmd.append(f"-p:{k}={v}")
        else:
            cmd.append(msbuild)
            target_switch = f"/t:{'Rebuild' if validated_action == 'REBUILD' else ('Clean' if validated_action == 'CLEAN' else 'Build')}"
            cmd.extend([str(validated_target), target_switch, f"/p:Configuration={configuration}", "/v:m"])
            if platform:
                cmd.append(f"/p:Platform={platform}")
            if target_framework:
                cmd.append(f"/p:TargetFramework={target_framework}")
            if properties:
                for k, v in properties.items():
                    if re.match(r'^[A-Za-z0-9_]+$', str(k)) and not any(bad in str(v) for bad in ("|", "&", ";", ">", "<")):
                        cmd.append(f"/p:{k}={v}")

        start_time = time.time()
        try:
            res = subprocess.run(
                cmd,
                cwd=str(validated_target.parent),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=False,
                timeout=val_timeout,
            )
            duration = time.time() - start_time
            if getattr(self, "perf_monitor", None):
                try:
                    self.perf_monitor.record_build_duration(duration)
                except Exception:
                    pass
            raw_stdout = redact_sensitive_data(res.stdout or "")
            raw_stderr = redact_sensitive_data(res.stderr or "")
            raw = raw_stdout + ("\n" + raw_stderr if raw_stderr else "")

            # Bounded output line & byte limit
            lines = raw.splitlines()
            if len(lines) > max_lines:
                lines = lines[:max_lines] + ["[TRUNCATED: MAX LINES REACHED]"]
            bounded_text = "\n".join(lines)
            if len(bounded_text.encode("utf-8")) > max_bytes:
                bounded_text = bounded_text[:max_bytes] + "\n[TRUNCATED: MAX BYTES REACHED]"

            success = (res.returncode == 0)

            return {
                "success": success,
                "returncode": res.returncode,
                "command": cmd,
                "stdout": raw_stdout[:2000],
                "stderr": raw_stderr[:2000],
                "output_sample": bounded_text[:2000],
                "full_output": bounded_text,
                "duration_s": round(duration, 2),
                "action": validated_action,
                "configuration": configuration,
                "platform": platform,
                "target": str(validated_target),
            }
        except subprocess.TimeoutExpired:
            duration = time.time() - start_time
            return {
                "success": False,
                "returncode": -1,
                "error_code": VSErrorCode.BUILD_TIMEOUT.value,
                "stdout": "",
                "stderr": f"Build timed out after {val_timeout}s.",
                "output_sample": f"Build timed out after {val_timeout}s.",
                "full_output": "Build timed out.",
                "duration_s": round(duration, 2),
                "action": validated_action,
                "configuration": configuration,
                "target": str(validated_target),
            }
        except Exception as e:
            duration = time.time() - start_time
            return {
                "success": False,
                "returncode": -1,
                "error_code": VSErrorCode.BUILD_FAILED.value,
                "stdout": "",
                "stderr": str(e),
                "output_sample": str(e),
                "full_output": str(e),
                "duration_s": round(duration, 2),
                "action": validated_action,
                "configuration": configuration,
                "target": str(validated_target),
            }

    def run_test(
        self,
        target_path: Path,
        filter_expr: Optional[str] = None,
        configuration: str = "Debug",
        max_lines: int = MAX_BUILD_OUTPUT_LINES,
        max_bytes: int = MAX_BUILD_OUTPUT_BYTES,
        timeout: float = TEST_TIMEOUT_SECONDS,
    ) -> Dict[str, Any]:
        """Executes tests safely via dotnet test or vstest.console."""
        self.safety.check_emergency_stop()
        validated_target = self.safety.validate_file_path(target_path, check_writable=False)
        val_timeout = self.safety.validate_timeout(timeout, max_timeout=TEST_TIMEOUT_SECONDS)

        env_info = self.env.detect()
        dotnet = env_info.dotnet_path

        if not dotnet and not env_info.vstest_path:
            return {
                "success": False,
                "returncode": -1,
                "error_code": VSErrorCode.SDK_NOT_FOUND.value,
                "output_sample": "No test runner (dotnet test or vstest.console) found.",
                "full_output": "Test runner missing.",
                "duration_s": 0.0,
            }

        cmd: List[str] = []
        if dotnet:
            cmd.extend([dotnet, "test", str(validated_target), "-c", configuration, "--verbosity", "normal"])
            if filter_expr:
                cmd.extend(["--filter", filter_expr])
        else:
            cmd.extend([env_info.vstest_path, str(validated_target)])
            if filter_expr:
                cmd.append(f"/TestCaseFilter:{filter_expr}")

        start_time = time.time()
        try:
            res = subprocess.run(
                cmd,
                cwd=str(validated_target.parent),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=False,
                timeout=val_timeout,
            )
            duration = time.time() - start_time
            if getattr(self, "perf_monitor", None):
                try:
                    self.perf_monitor.record_test_duration(duration)
                except Exception:
                    pass
            raw_stdout = redact_sensitive_data(res.stdout or "")
            raw_stderr = redact_sensitive_data(res.stderr or "")
            raw = raw_stdout + ("\n" + raw_stderr if raw_stderr else "")

            # Bounded output line & byte limit
            lines = raw.splitlines()
            if len(lines) > max_lines:
                lines = lines[:max_lines] + ["[TRUNCATED: MAX LINES REACHED]"]
            bounded_text = "\n".join(lines)
            if len(bounded_text.encode("utf-8")) > max_bytes:
                bounded_text = bounded_text[:max_bytes] + "\n[TRUNCATED: MAX BYTES REACHED]"

            summary = VSErrorAnalyzer().parse_test_summary(bounded_text)
            success = (res.returncode == 0 and summary.get("failed", 0) == 0)

            return {
                "success": success,
                "returncode": res.returncode,
                "stdout": raw_stdout[:2000],
                "stderr": raw_stderr[:2000],
                "output_sample": bounded_text[:2000],
                "full_output": bounded_text,
                "summary": summary,
                "duration_s": round(duration, 2),
                "target": str(validated_target),
                "command": cmd,
            }
        except subprocess.TimeoutExpired:
            duration = time.time() - start_time
            return {
                "success": False,
                "returncode": -1,
                "error_code": VSErrorCode.TEST_TIMEOUT.value,
                "stdout": "",
                "stderr": f"Test execution timed out after {val_timeout}s.",
                "output_sample": f"Test execution timed out after {val_timeout}s.",
                "full_output": "Test execution timed out.",
                "summary": {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "success": False},
                "duration_s": round(duration, 2),
                "target": str(validated_target),
            }
        except Exception as e:
            duration = time.time() - start_time
            return {
                "success": False,
                "returncode": -1,
                "error_code": VSErrorCode.TEST_FAILED.value,
                "stdout": "",
                "stderr": str(e),
                "output_sample": str(e),
                "full_output": str(e),
                "summary": {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "success": False},
                "duration_s": round(duration, 2),
                "target": str(validated_target),
            }


class SafeRuntimeRunner:
    """
    Safely executes compiled .NET binaries and console projects.
    Enforces strict boundaries:
    - shell=False
    - executable or assembly must be inside authorized workspace
    - bounded runtime execution timeout (default 10s)
    - bounded output capture (max lines & bytes)
    - secret redaction
    """

    def __init__(
        self,
        safety_gate: Optional[VSSafetyGate] = None,
        env_detector: Optional[VSEnvironmentDetector] = None,
        audit_logger: Optional[Any] = None,
    ):
        self.safety = safety_gate or VSSafetyGate()
        self.env = env_detector or VSEnvironmentDetector()
        self.audit = audit_logger

    def run_executable(
        self,
        target_path: Union[str, Path],
        args: Optional[List[str]] = None,
        timeout: float = RUNTIME_TIMEOUT_SECONDS,
        max_lines: int = MAX_RUNTIME_OUTPUT_LINES,
        max_bytes: int = MAX_RUNTIME_OUTPUT_BYTES,
    ) -> Dict[str, Any]:
        """Runs a project executable (.exe or dotnet .dll) under strict safety bounds."""
        self.safety.check_emergency_stop()
        val_path = self.safety.validate_executable_path(target_path)
        val_timeout = self.safety.validate_timeout(timeout, max_timeout=60.0)

        cmd: List[str] = []
        if val_path.suffix.lower() == ".dll":
            env_info = self.env.detect()
            dotnet = env_info.dotnet_path or "dotnet"
            cmd.extend([dotnet, str(val_path)])
        else:
            cmd.append(str(val_path))

        if args:
            for a in args:
                # Sanitize arguments: no pipes, no shell redirects
                if any(bad in str(a) for bad in ("|", "&", ";", ">", "<")):
                    raise VSSafetyError(VSErrorCode.ACTION_NOT_ALLOWED, f"Disallowed character in argument: {a}")
                cmd.append(str(a))

        start_time = time.time()
        try:
            res = subprocess.run(
                cmd,
                cwd=str(val_path.parent),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=False,
                timeout=val_timeout,
            )
            duration = time.time() - start_time
            raw_stdout = redact_sensitive_data(res.stdout or "")
            raw_stderr = redact_sensitive_data(res.stderr or "")
            full = raw_stdout + ("\n" + raw_stderr if raw_stderr else "")

            # Truncate to bounds
            lines = full.splitlines()
            if len(lines) > max_lines:
                lines = lines[:max_lines] + ["[TRUNCATED: MAX LINES REACHED]"]
            bounded_text = "\n".join(lines)
            if len(bounded_text.encode("utf-8")) > max_bytes:
                bounded_text = bounded_text[:max_bytes] + "\n[TRUNCATED: MAX BYTES REACHED]"

            return {
                "success": (res.returncode == 0),
                "returncode": res.returncode,
                "stdout": raw_stdout[:2000],
                "stderr": raw_stderr[:2000],
                "full_output": bounded_text,
                "output_sample": bounded_text[:2000],
                "duration_s": round(duration, 2),
                "command": cmd,
                "timed_out": False,
            }
        except subprocess.TimeoutExpired:
            duration = time.time() - start_time
            return {
                "success": False,
                "returncode": -1,
                "error_code": VSErrorCode.RUNTIME_TIMEOUT.value,
                "stdout": "",
                "stderr": f"Process timed out after {val_timeout}s.",
                "full_output": f"Process timed out after {val_timeout}s.",
                "output_sample": f"Process timed out after {val_timeout}s.",
                "duration_s": round(duration, 2),
                "command": cmd,
                "timed_out": True,
            }
        except Exception as e:
            duration = time.time() - start_time
            return {
                "success": False,
                "returncode": -1,
                "error_code": VSErrorCode.RUNTIME_LAUNCH_FAILURE.value,
                "stdout": "",
                "stderr": str(e),
                "full_output": str(e),
                "output_sample": str(e),
                "duration_s": round(duration, 2),
                "command": cmd,
                "timed_out": False,
            }



class VSToolRegistry:
    """
    Tool execution registry mapping all 12 allowlisted Visual Studio tools
    to their safe implementation methods.
    """

    def __init__(
        self,
        safety_gate: Optional[VSSafetyGate] = None,
        env_detector: Optional[VSEnvironmentDetector] = None,
        project_inspector: Optional[VSProjectInspector] = None,
        msbuild_runner: Optional[SafeMSBuildRunner] = None,
        workspace_root: Optional[Union[str, Path]] = None,
        audit_logger: Optional[AuditLogger] = None,
        ide_inspector: Optional[VSIdeInspector] = None,
        debugger: Optional[SafeVSDebugger] = None,
    ):
        if safety_gate is None and workspace_root is not None:
            self.safety = VSSafetyGate(authorized_project=Path(workspace_root).resolve())
        else:
            self.safety = safety_gate or VSSafetyGate()
        self.env = env_detector or VSEnvironmentDetector()
        self.inspector = project_inspector or VSProjectInspector(safety_gate=self.safety)
        self.runner = msbuild_runner or SafeMSBuildRunner(safety_gate=self.safety, env_detector=self.env)
        self.analyzer = VSErrorAnalyzer(project_root=self.safety.authorized_project)
        self.runtime_runner = SafeRuntimeRunner(safety_gate=self.safety, env_detector=self.env)
        self.audit = audit_logger
        self.ide = ide_inspector or VSIdeInspector(safety_gate=self.safety, env_detector=self.env, project_inspector=self.inspector)
        self.debugger = debugger or SafeVSDebugger(safety_gate=self.safety, ide_inspector=self.ide, audit_logger=self.audit)
        self.test_intel = VSTestIntelligence(safety_gate=self.safety, project_root=self.safety.authorized_project)
        self.perf_monitor = VSPerformanceMonitor(safety_gate=self.safety)
        self.diagnostics_engine = VSDiagnosticsEngine(safety_gate=self.safety, project_root=self.safety.authorized_project)
        self.runner.perf_monitor = self.perf_monitor
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None
        self.last_build_output: Dict[str, Any] = {}
        self.last_test_output: Dict[str, Any] = {}
        self.last_runtime_output: Dict[str, Any] = {}

    def execute(self, tool_name: str, params: Optional[Dict[str, Any]] = None) -> VSToolResult:
        """Alias for execute_tool."""
        return self.execute_tool(tool_name, params)

    def execute_tool(self, tool_name: str, params: Optional[Dict[str, Any]] = None) -> VSToolResult:
        """Dispatches an authorized tool call with safety validation."""
        start_time = time.time()
        p = params or {}

        try:
            validated_tool = self.safety.validate_tool_name(tool_name)
            handler = self._get_handler(validated_tool)
            res = handler(p)
            res.duration_s = time.time() - start_time
            if self.audit:
                try:
                    self.audit.log_event("VS_TOOL_CALL", {"tool": tool_name, "params": redact_sensitive_data(str(p))}, status="success" if res.success else "failure")
                except Exception:
                    pass
            return res
        except VSSafetyError as se:
            if self.audit:
                try:
                    self.audit.log_event("VS_TOOL_CALL", {"tool": tool_name, "error": se.message}, status="failure")
                except Exception:
                    pass
            return VSToolResult(
                tool=tool_name,
                success=False,
                error=se.message,
                error_code=se.code.value,
                duration_s=time.time() - start_time,
            )
        except Exception as e:
            if self.audit:
                try:
                    self.audit.log_event("VS_TOOL_CALL", {"tool": tool_name, "error": str(e)}, status="failure")
                except Exception:
                    pass
            return VSToolResult(
                tool=tool_name,
                success=False,
                error=str(e),
                error_code=VSErrorCode.BUILD_FAILED.value,
                duration_s=time.time() - start_time,
            )

    def run_safe_build(self, *args, **kwargs) -> VSToolResult:
        """Runs safe MSBuild or dotnet build."""
        params = dict(kwargs)
        if args and isinstance(args[0], dict):
            params = dict(args[0], **kwargs)
        elif args and isinstance(args[0], (str, Path)):
            params["target_path"] = str(args[0])
            if len(args) > 1:
                params["action"] = args[1]
        return self._tool_run_safe_build(params)

    def _get_handler(self, tool_name: str) -> Callable[[Dict[str, Any]], VSToolResult]:
        handlers: Dict[str, Callable[[Dict[str, Any]], VSToolResult]] = {
            "vs.inspect_solution": self._tool_inspect_solution,
            "vs.inspect_project": self._tool_inspect_project,
            "vs.list_projects": self._tool_list_projects,
            "vs.inspect_project_files": self._tool_inspect_project_files,
            "vs.find_code": self._tool_find_code,
            "vs.read_code": self._tool_read_code,
            "vs.inspect_build_configuration": self._tool_inspect_build_configuration,
            "vs.inspect_target_frameworks": self._tool_inspect_target_frameworks,
            "vs.inspect_dependencies": self._tool_inspect_dependencies,
            "vs.run_safe_build": self.run_safe_build,
            "vs.capture_build_output": self._tool_capture_build_output,
            "vs.verify_build_result": self._tool_verify_build_result,
            "vs.run_safe_test": self._tool_run_safe_test,
            "vs.capture_test_output": self._tool_capture_test_output,
            "vs.verify_test_result": self._tool_verify_test_result,
            "vs.inspect_launch_configuration": self._tool_inspect_launch_configuration,
            "vs.capture_runtime_output": self._tool_capture_runtime_output,
            "vs.verify_runtime_result": self._tool_verify_runtime_result,
            # Step 7 Phase 4: IDE State & Controlled Debugging
            "vs.inspect_ide_state": self._tool_inspect_ide_state,
            "vs.inspect_active_document": self._tool_inspect_active_document,
            "vs.inspect_debugger_state": self._tool_inspect_debugger_state,
            "vs.set_breakpoint": self._tool_set_breakpoint,
            "vs.remove_breakpoint": self._tool_remove_breakpoint,
            "vs.start_debug_session": self._tool_start_debug_session,
            "vs.stop_debug_session": self._tool_stop_debug_session,
            "vs.continue_debug": self._tool_continue_debug,
            "vs.pause_debug": self._tool_pause_debug,
            "vs.inspect_debug_location": self._tool_inspect_debug_location,
            "vs.inspect_debug_locals": self._tool_inspect_debug_locals,
            # Step 7 Phase 5: Test, Performance, Diagnostics & Unified Intelligence
            "vs.discover_test_projects": self._tool_discover_test_projects,
            "vs.inspect_test_configuration": self._tool_inspect_test_configuration,
            "vs.inspect_diagnostics": self._tool_inspect_diagnostics,
            "vs.inspect_performance": self._tool_inspect_performance,
            "vs.run_unified_workflow": self._tool_run_unified_workflow,
        }
        handler = handlers.get(tool_name)
        if not handler:
            raise VSSafetyError(VSErrorCode.TOOL_NOT_ALLOWED, f"Handler for '{tool_name}' not implemented.")
        return handler

    # -------------------------------------------------------------------------
    # Tool Handlers
    # -------------------------------------------------------------------------

    def _tool_inspect_solution(self, params: Dict[str, Any]) -> VSToolResult:
        sol_path = params.get("solution_path") or params.get("path")
        if not sol_path:
            # Look for solution in authorized project
            projs = self.inspector.list_projects(self.safety.authorized_project)
            sol = next((p for p in projs if p["type"] == "solution"), None)
            if sol:
                sol_path = sol["path"]
            else:
                return VSToolResult(
                    tool="vs.inspect_solution",
                    success=False,
                    error="No solution path provided and none found in project root.",
                    error_code=VSErrorCode.CONFIGURATION_NOT_FOUND.value,
                )

        meta = self.inspector.inspect_solution(sol_path)
        return VSToolResult(
            tool="vs.inspect_solution",
            success=True,
            data=meta.to_dict(),
            message=f"Inspected solution '{meta.name}' with {len(meta.projects)} projects.",
            verified=True,
        )

    def _tool_inspect_project(self, params: Dict[str, Any]) -> VSToolResult:
        proj_path = params.get("project_path") or params.get("path")
        if not proj_path:
            projs = self.inspector.list_projects(self.safety.authorized_project)
            proj = next((p for p in projs if p["type"] == "project"), None)
            if proj:
                proj_path = proj["path"]
            else:
                return VSToolResult(
                    tool="vs.inspect_project",
                    success=False,
                    error="No project path provided and none found in project root.",
                    error_code=VSErrorCode.CONFIGURATION_NOT_FOUND.value,
                )

        meta = self.inspector.inspect_project(proj_path)
        return VSToolResult(
            tool="vs.inspect_project",
            success=True,
            data=meta.to_dict(),
            message=f"Inspected project '{meta.name}' ({meta.project_type}, {meta.target_frameworks}).",
            verified=True,
        )

    def _tool_list_projects(self, params: Dict[str, Any]) -> VSToolResult:
        root = params.get("root_path") or self.safety.authorized_project
        projects = self.inspector.list_projects(root)
        return VSToolResult(
            tool="vs.list_projects",
            success=True,
            data={"projects": projects, "count": len(projects)},
            message=f"Found {len(projects)} solutions/projects.",
            verified=True,
        )

    def _tool_inspect_project_files(self, params: Dict[str, Any]) -> VSToolResult:
        proj_path = params.get("project_path") or self.safety.authorized_project
        validated_dir = self.safety.validate_project_path(proj_path)
        if validated_dir.is_file():
            validated_dir = validated_dir.parent

        files_list: List[Dict[str, Any]] = []
        for r, dirs, files in os.walk(validated_dir):
            dirs[:] = [d for d in dirs if d.lower() not in ("bin", "obj", ".vs", ".git", "packages")]
            for f in files:
                f_path = Path(r) / f
                ext = f_path.suffix.lower()
                files_list.append({
                    "name": f,
                    "relative_path": str(f_path.relative_to(validated_dir)),
                    "extension": ext,
                    "size_bytes": f_path.stat().st_size,
                })

        return VSToolResult(
            tool="vs.inspect_project_files",
            success=True,
            data={"files": files_list, "total_count": len(files_list)},
            message=f"Enumerated {len(files_list)} source and resource files.",
            verified=True,
        )

    def _tool_find_code(self, params: Dict[str, Any]) -> VSToolResult:
        pattern = params.get("pattern", "")
        if not pattern:
            return VSToolResult(tool="vs.find_code", success=False, error="Pattern is required.")
        proj_path = params.get("project_path") or self.safety.authorized_project
        ext = params.get("extension")
        matches = self.inspector.find_code(proj_path, pattern, extension=ext)
        return VSToolResult(
            tool="vs.find_code",
            success=True,
            data={"matches": matches, "count": len(matches)},
            message=f"Found {len(matches)} matches for pattern '{pattern}'.",
            verified=True,
        )

    def _tool_read_code(self, params: Dict[str, Any]) -> VSToolResult:
        f_path = params.get("file_path")
        if not f_path:
            return VSToolResult(tool="vs.read_code", success=False, error="file_path is required.")
        start = int(params.get("start_line", 1))
        end = int(params.get("end_line", 100))
        read_data = self.inspector.read_code(f_path, start_line=start, end_line=end)
        return VSToolResult(
            tool="vs.read_code",
            success=True,
            data=read_data,
            output=read_data["content"],
            message=f"Read lines {read_data['start_line']}..{read_data['end_line']} from '{Path(f_path).name}'.",
            verified=True,
        )

    def _tool_inspect_build_configuration(self, params: Dict[str, Any]) -> VSToolResult:
        target = params.get("target_path") or self.safety.authorized_project
        val_path = self.safety.validate_file_path(target, check_writable=False)
        configs: List[str] = ["Debug", "Release"]
        data: Dict[str, Any] = {"target": str(val_path)}

        if val_path.suffix.lower() in (".sln", ".slnx"):
            meta_sol = self.inspector.inspect_solution(val_path)
            configs = meta_sol.configurations or configs
            data["configurations"] = configs
        elif val_path.suffix.lower() in (".csproj", ".vbproj", ".fsproj"):
            meta = self.inspector.inspect_project(val_path)
            configs = [f"{c}|Any CPU" for c in configs]
            data.update({
                "configurations": configs,
                "target_framework": meta.target_framework,
                "target_frameworks": meta.target_frameworks,
                "platform_target": meta.platform_target,
                "runtime_identifier": meta.runtime_identifier,
                "treat_warnings_as_errors": meta.treat_warnings_as_errors,
                "lang_version": meta.lang_version,
                "nullable": meta.nullable,
            })
        else:
            data["configurations"] = configs

        return VSToolResult(
            tool="vs.inspect_build_configuration",
            success=True,
            data=data,
            message=f"Configurations for '{val_path.name}': {configs}",
            verified=True,
        )

    def _tool_inspect_dependencies(self, params: Dict[str, Any]) -> VSToolResult:
        proj_path = params.get("project_path") or self.safety.authorized_project
        val_path = self.safety.validate_file_path(proj_path, check_writable=False)

        if val_path.is_dir():
            # Discover first project in dir
            projs = self.inspector.list_projects(val_path)
            p_cand = next((p for p in projs if p["type"] == "project"), None)
            if p_cand:
                val_path = Path(p_cand["path"])

        meta = self.inspector.inspect_project(val_path)
        return VSToolResult(
            tool="vs.inspect_dependencies",
            success=True,
            data={
                "project": meta.name,
                "packages": meta.package_references,
                "project_references": meta.project_references,
            },
            message=f"Dependencies: {len(meta.package_references)} packages, {len(meta.project_references)} project references.",
            verified=True,
        )

    def _tool_run_safe_build(self, params: Dict[str, Any]) -> VSToolResult:
        target = params.get("target_path") or self.safety.authorized_project
        val_target = Path(target)
        if val_target.is_dir():
            projs = self.inspector.list_projects(val_target)
            sol = next((p for p in projs if p["type"] == "solution"), None)
            if sol:
                val_target = Path(sol["path"])
            else:
                proj = next((p for p in projs if p["type"] == "project"), None)
                if proj:
                    val_target = Path(proj["path"])

        action = params.get("action", "BUILD")
        cfg = params.get("configuration", "Debug")
        platform = params.get("platform")
        props = params.get("properties")
        tfm = params.get("target_framework")
        timeout = float(params.get("timeout", BUILD_TIMEOUT_SECONDS))

        build_res = self.runner.run_build(
            val_target,
            action=action,
            configuration=cfg,
            platform=platform,
            properties=props,
            target_framework=tfm,
            timeout=timeout,
        )
        self.last_build_output = build_res
        success = build_res.get("success", False)

        return VSToolResult(
            tool="vs.run_safe_build",
            success=success,
            data=build_res,
            output=build_res.get("full_output", ""),
            message=f"MSBuild '{action}' {'succeeded' if success else 'failed'}.",
            error=None if success else f"Build failed: {build_res.get('output_sample', '')[:200]}",
            error_code=None if success else (build_res.get("error_code") or VSErrorCode.BUILD_FAILED.value),
            verified=success,
        )

    def _tool_run_safe_test(self, params: Dict[str, Any]) -> VSToolResult:
        target = params.get("target_path") or self.safety.authorized_project
        val_target = Path(target)
        if val_target.is_dir():
            projs = self.inspector.list_projects(val_target)
            t_proj = next((p for p in projs if "test" in p["name"].lower()), None)
            if t_proj:
                val_target = Path(t_proj["path"])
            elif projs:
                val_target = Path(projs[0]["path"])

        filter_exp = params.get("filter")
        cfg = params.get("configuration", "Debug")
        timeout = float(params.get("timeout", TEST_TIMEOUT_SECONDS))
        test_res = self.runner.run_test(val_target, filter_expr=filter_exp, configuration=cfg, timeout=timeout)
        if test_res.get("full_output"):
            try:
                granular = self.test_intel.parse_granular_test_results(test_res["full_output"])
                test_res["granular_results"] = granular
                if getattr(self, "perf_monitor", None) and granular.get("slow_tests"):
                    self.perf_monitor.set_slow_tests(granular["slow_tests"])
            except Exception:
                pass
        self.last_test_output = test_res
        success = test_res.get("success", False)

        return VSToolResult(
            tool="vs.run_safe_test",
            success=success,
            data=test_res,
            output=test_res.get("full_output", ""),
            message=f"Tests {'passed' if success else 'failed'}.",
            error=None if success else f"Test run failed: {test_res.get('output_sample', '')[:200]}",
            error_code=None if success else (test_res.get("error_code") or VSErrorCode.TEST_FAILED.value),
            verified=success,
        )

    def _tool_capture_build_output(self, params: Dict[str, Any]) -> VSToolResult:
        max_lines = int(params.get("lines", 100))
        full = self.last_build_output.get("full_output", "No recent build output recorded.")
        lines = full.splitlines()
        tail = lines[-max_lines:] if len(lines) > max_lines else lines

        return VSToolResult(
            tool="vs.capture_build_output",
            success=True,
            data={"lines_returned": len(tail), "total_lines": len(lines)},
            output="\n".join(tail),
            message=f"Captured {len(tail)} lines of recent build output.",
            verified=True,
        )

    def _tool_verify_build_result(self, params: Dict[str, Any]) -> VSToolResult:
        target = params.get("target_path") or self.safety.authorized_project
        val_target = Path(target)
        if val_target.is_file():
            p_dir = val_target.parent
        else:
            p_dir = val_target

        bin_dir = p_dir / "bin"
        has_bin = bin_dir.exists() and bin_dir.is_dir()
        binaries: List[str] = []

        if has_bin:
            for r, dirs, files in os.walk(bin_dir):
                for f in files:
                    if f.endswith((".dll", ".exe")):
                        binaries.append(str(Path(r) / f))

        rc = self.last_build_output.get("returncode", 0) if self.last_build_output else 0
        verified = bool(binaries) and (rc == 0)
        return VSToolResult(
            tool="vs.verify_build_result",
            success=verified,
            data={
                "has_bin_dir": has_bin,
                "binaries_found": binaries,
                "count": len(binaries),
                "verified": verified,
                "returncode": rc,
            },
            message=f"Build result verification: {len(binaries)} build artifact(s) found (exit {rc}).",
            verified=verified,
        )

    def _tool_inspect_target_frameworks(self, params: Dict[str, Any]) -> VSToolResult:
        target = params.get("target_path") or params.get("path") or self.safety.authorized_project
        tfms = self.inspector.inspect_target_frameworks(target)
        return VSToolResult(
            tool="vs.inspect_target_frameworks",
            success=True,
            data={"target": str(target), "target_frameworks": tfms},
            message=f"Target frameworks for '{Path(target).name}': {tfms}",
            verified=True,
        )

    def _tool_capture_test_output(self, params: Dict[str, Any]) -> VSToolResult:
        max_lines = int(params.get("lines", 100))
        full = self.last_test_output.get("full_output", "No recent test output recorded.")
        lines = full.splitlines()
        tail = lines[-max_lines:] if len(lines) > max_lines else lines
        summary = self.last_test_output.get("summary", {})

        return VSToolResult(
            tool="vs.capture_test_output",
            success=True,
            data={
                "lines_returned": len(tail),
                "total_lines": len(lines),
                "summary": summary,
                "returncode": self.last_test_output.get("returncode", 0),
            },
            output="\n".join(tail),
            message=f"Captured {len(tail)} lines of recent test output.",
            verified=True,
        )

    def _tool_verify_test_result(self, params: Dict[str, Any]) -> VSToolResult:
        summary = self.last_test_output.get("summary", {})
        returncode = self.last_test_output.get("returncode", -1)
        success = self.last_test_output.get("success", False)
        failed_count = summary.get("failed", 0)

        verified = (success and returncode == 0 and failed_count == 0)
        return VSToolResult(
            tool="vs.verify_test_result",
            success=verified,
            data={
                "verified": verified,
                "returncode": returncode,
                "summary": summary,
            },
            message=f"Test verification: {'PASSED' if verified else 'FAILED'} (failed tests: {failed_count}).",
            verified=verified,
        )

    def _tool_inspect_launch_configuration(self, params: Dict[str, Any]) -> VSToolResult:
        target = params.get("target_path") or params.get("project_path") or self.safety.authorized_project
        meta = self.inspector.inspect_launch_configuration(target)
        return VSToolResult(
            tool="vs.inspect_launch_configuration",
            success=True,
            data=meta.to_dict(),
            message=f"Inspected {len(meta.profiles)} launch profile(s) from '{Path(meta.path).name}'.",
            verified=True,
        )

    def _tool_capture_runtime_output(self, params: Dict[str, Any]) -> VSToolResult:
        target = params.get("target_path") or params.get("executable_path")
        if not target:
            target = self._find_project_executable(self.safety.authorized_project)
        if not target:
            return VSToolResult(
                tool="vs.capture_runtime_output",
                success=False,
                error="No executable or output binary specified or found.",
                error_code=VSErrorCode.CONFIGURATION_NOT_FOUND.value,
            )

        args = params.get("args", [])
        timeout = float(params.get("timeout", RUNTIME_TIMEOUT_SECONDS))
        max_lines = int(params.get("max_lines", MAX_RUNTIME_OUTPUT_LINES))

        res = self.runtime_runner.run_executable(target, args=args, timeout=timeout, max_lines=max_lines)
        self.last_runtime_output = res
        success = res.get("success", False)
        returncode = res.get("returncode", -1)

        diag = None
        if not success:
            err_item = self.analyzer.classify_runtime_failure(res.get("full_output", ""), exit_code=returncode)
            diag = err_item.to_dict()

        return VSToolResult(
            tool="vs.capture_runtime_output",
            success=success,
            data={
                "runtime_result": res,
                "diagnosis": diag,
            },
            output=res.get("full_output", ""),
            message=f"Runtime execution {'succeeded' if success else 'terminated with non-zero exit code'}.",
            error=None if success else f"Runtime failed: {res.get('output_sample', '')[:200]}",
            error_code=None if success else (diag.get("error_code") if diag else VSErrorCode.RUNTIME_CRASH.value),
            verified=success,
        )

    def _tool_verify_runtime_result(self, params: Dict[str, Any]) -> VSToolResult:
        res = self.last_runtime_output
        returncode = res.get("returncode", -1)
        success = res.get("success", False)
        timed_out = res.get("timed_out", False)

        verified = (success and returncode == 0 and not timed_out)
        return VSToolResult(
            tool="vs.verify_runtime_result",
            success=verified,
            data={
                "verified": verified,
                "returncode": returncode,
                "timed_out": timed_out,
            },
            message=f"Runtime verification: {'PASSED (exit 0)' if verified else f'FAILED (exit {returncode})'}.",
            verified=verified,
        )

    def _find_project_executable(self, root: Path) -> Optional[Path]:
        """Locates compiled .exe or .dll in bin folder under authorized root."""
        val_root = self.safety.validate_project_path(root)
        if val_root.is_file():
            val_root = val_root.parent

        bin_dir = val_root / "bin"
        if not bin_dir.exists():
            return None

        dll_cand = None
        for r, dirs, files in os.walk(bin_dir):
            for f in files:
                f_lower = f.lower()
                if f_lower.endswith(".exe"):
                    return Path(r) / f
                elif f_lower.endswith(".dll") and not f_lower.endswith(".views.dll") and dll_cand is None:
                    dll_cand = Path(r) / f
        return dll_cand

    # -------------------------------------------------------------------------
    # Step 7 Phase 4: IDE State & Controlled Debugging Tool Handlers
    # -------------------------------------------------------------------------

    def _tool_inspect_ide_state(self, params: Dict[str, Any]) -> VSToolResult:
        """Inspects current Visual Studio IDE execution and active solution/project state."""
        ide_state = self.ide.inspect_ide()
        return VSToolResult(
            tool="vs.inspect_ide_state",
            success=True,
            data=ide_state.to_dict(),
            message=f"IDE State: {'Running' if ide_state.is_running else 'Not Running'}.",
            verified=True,
        )

    def _tool_inspect_active_document(self, params: Dict[str, Any]) -> VSToolResult:
        """Inspects active or requested document within authorized workspace."""
        path = params.get("path") or params.get("file_path") or params.get("document_path")
        cursor_line = params.get("cursor_line")
        if cursor_line is not None:
            try:
                cursor_line = int(cursor_line)
            except (ValueError, TypeError):
                cursor_line = None
        doc_info = self.ide.inspect_active_document(file_path=path, cursor_line=cursor_line)
        return VSToolResult(
            tool="vs.inspect_active_document",
            success=True,
            data=doc_info,
            message=f"Active document: '{doc_info.get('file_name', '')}' ({doc_info.get('total_lines', 0)} lines).",
            verified=True,
        )

    def _tool_inspect_debugger_state(self, params: Dict[str, Any]) -> VSToolResult:
        """Inspects current debugger state and active breakpoints."""
        bps = [bp.to_dict() for bp in self.debugger.list_breakpoints()]
        data = {
            "state": self.debugger.state.value,
            "session_id": self.debugger.session_id,
            "breakpoint_count": len(bps),
            "breakpoints": bps,
        }
        return VSToolResult(
            tool="vs.inspect_debugger_state",
            success=True,
            data=data,
            message=f"Debugger state: {self.debugger.state.value} ({len(bps)} breakpoints).",
            verified=True,
        )

    def _tool_set_breakpoint(self, params: Dict[str, Any]) -> VSToolResult:
        """Sets a validated breakpoint in an authorized source file."""
        file_path = params.get("file_path") or params.get("target_file") or params.get("path")
        line = params.get("line_number") or params.get("line")
        if not file_path or line is None:
            return VSToolResult(
                tool="vs.set_breakpoint",
                success=False,
                error="Parameters 'file_path' and 'line_number' are required.",
                error_code=VSErrorCode.BREAKPOINT_INVALID.value,
            )
        try:
            line_num = int(line)
        except (ValueError, TypeError):
            return VSToolResult(
                tool="vs.set_breakpoint",
                success=False,
                error=f"Invalid line number: {line}",
                error_code=VSErrorCode.BREAKPOINT_INVALID.value,
            )

        condition = params.get("condition")
        expected_hash = params.get("expected_hash") or params.get("expected_file_hash")

        bp = self.debugger.set_breakpoint(
            file_path=file_path,
            line_number=line_num,
            condition=condition,
            expected_hash=expected_hash,
        )
        return VSToolResult(
            tool="vs.set_breakpoint",
            success=True,
            data=bp.to_dict(),
            message=f"Breakpoint '{bp.id}' set at {Path(bp.file_path).name}:{bp.line_number}.",
            verified=True,
        )

    def _tool_remove_breakpoint(self, params: Dict[str, Any]) -> VSToolResult:
        """Removes an active breakpoint by identifier."""
        bp_id = params.get("breakpoint_id") or params.get("id")
        if not bp_id:
            return VSToolResult(
                tool="vs.remove_breakpoint",
                success=False,
                error="Parameter 'breakpoint_id' is required.",
                error_code=VSErrorCode.BREAKPOINT_NOT_FOUND.value,
            )

        self.debugger.remove_breakpoint(str(bp_id))
        return VSToolResult(
            tool="vs.remove_breakpoint",
            success=True,
            data={"breakpoint_id": bp_id, "removed": True},
            message=f"Breakpoint '{bp_id}' removed.",
            verified=True,
        )

    def _tool_start_debug_session(self, params: Dict[str, Any]) -> VSToolResult:
        """Starts a controlled debug session on an authorized target."""
        target = params.get("target_path") or params.get("path") or params.get("project_path")
        if not target:
            # Fallback to authorized project
            projs = self.inspector.list_projects(self.safety.authorized_project)
            csproj = next((p["path"] for p in projs if p["type"] == "csharp"), None)
            target = csproj or str(self.safety.authorized_project)

        configuration = params.get("configuration", "Debug")
        platform = params.get("platform", "Any CPU")
        timeout = float(params.get("timeout", 30.0))

        evidence = self.debugger.start_session(
            target_path=target,
            configuration=configuration,
            platform=platform,
            timeout=timeout,
        )
        return VSToolResult(
            tool="vs.start_debug_session",
            success=True,
            data=evidence.to_dict(),
            message=f"Debug session '{evidence.session_id}' started in state '{evidence.state}'.",
            verified=True,
        )

    def _tool_stop_debug_session(self, params: Dict[str, Any]) -> VSToolResult:
        """Stops the active debugging session."""
        evidence = self.debugger.stop_session()
        return VSToolResult(
            tool="vs.stop_debug_session",
            success=True,
            data=evidence.to_dict(),
            message="Debug session stopped.",
            verified=True,
        )

    def _tool_continue_debug(self, params: Dict[str, Any]) -> VSToolResult:
        """Resumes execution from paused state."""
        until_bp = bool(params.get("until_breakpoint", True))
        evidence = self.debugger.continue_session(until_breakpoint=until_bp)
        return VSToolResult(
            tool="vs.continue_debug",
            success=True,
            data=evidence.to_dict(),
            message=f"Debug execution resumed (state: {evidence.state}).",
            verified=True,
        )

    def _tool_pause_debug(self, params: Dict[str, Any]) -> VSToolResult:
        """Pauses a running debug session."""
        evidence = self.debugger.pause_session()
        return VSToolResult(
            tool="vs.pause_debug",
            success=True,
            data=evidence.to_dict(),
            message=f"Debug session paused at location: {evidence.location.file_path if evidence.location else 'unknown'}.",
            verified=True,
        )

    def _tool_inspect_debug_location(self, params: Dict[str, Any]) -> VSToolResult:
        """Inspects current source execution location during a paused debug session."""
        loc = self.debugger.get_current_location()
        loc_dict = loc.to_dict() if loc else {}
        data = {"location": loc_dict if loc else None, **loc_dict}
        return VSToolResult(
            tool="vs.inspect_debug_location",
            success=True,
            data=data,
            message=f"Debug location: {Path(loc.file_path).name}:{loc.line_number}" if loc else "Debugger not paused at source line.",
            verified=True,
        )

    def _tool_inspect_debug_locals(self, params: Dict[str, Any]) -> VSToolResult:
        """Inspects bounded local variables with sensitive data redacted."""
        max_c = int(params.get("max_count", 50))
        vars_list = self.debugger.inspect_locals(max_count=max_c)
        serialized = [v.to_dict() for v in vars_list]
        return VSToolResult(
            tool="vs.inspect_debug_locals",
            success=True,
            data={
                "locals": serialized,
                "variables": serialized,
                "count": len(vars_list),
            },
            message=f"Inspected {len(vars_list)} local variables (sanitized).",
            verified=True,
        )

    # -------------------------------------------------------------------------
    # Step 7 Phase 5: Test, Performance, Diagnostics & Unified Intelligence Handlers
    # -------------------------------------------------------------------------

    def _tool_discover_test_projects(self, params: Dict[str, Any]) -> VSToolResult:
        """Discovers test projects and test frameworks in authorized workspace."""
        root = params.get("root_path") or params.get("path") or self.safety.authorized_project
        projs = self.test_intel.discover_test_projects(root)
        serialized = [p.to_dict() for p in projs]
        return VSToolResult(
            tool="vs.discover_test_projects",
            success=True,
            data={"test_projects": serialized, "count": len(serialized)},
            message=f"Discovered {len(serialized)} test project(s).",
            verified=True,
        )

    def _tool_inspect_test_configuration(self, params: Dict[str, Any]) -> VSToolResult:
        """Inspects test configuration details including frameworks, runners, and .runsettings."""
        target = params.get("target_path") or params.get("project_path") or self.safety.authorized_project
        val_path = Path(target)
        if val_path.is_dir():
            projs = self.test_intel.discover_test_projects(val_path)
            if projs:
                target = projs[0].path
            else:
                p_list = self.inspector.list_projects(val_path)
                if p_list:
                    target = p_list[0]["path"]
        cfg = self.test_intel.inspect_test_configuration(target)
        return VSToolResult(
            tool="vs.inspect_test_configuration",
            success=True,
            data=cfg.to_dict(),
            message=f"Inspected test configuration for '{Path(target).name}'.",
            verified=True,
        )

    def _tool_inspect_diagnostics(self, params: Dict[str, Any]) -> VSToolResult:
        """Unifies build, test, runtime, debugger, and IDE state into structured diagnostic context."""
        ide_st = None
        try:
            if hasattr(self.ide, 'get_ide_state'):
                ide_st = self.ide.get_ide_state()
        except Exception:
            pass

        ctx = self.diagnostics_engine.build_diagnostic_context(
            build_output=self.last_build_output,
            test_output=self.last_test_output,
            runtime_output=self.last_runtime_output,
            debugger_evidence=getattr(self.debugger, 'last_evidence', None),
            ide_state=ide_st,
        )
        return VSToolResult(
            tool="vs.inspect_diagnostics",
            success=True,
            data=ctx.to_dict(),
            message=f"Diagnostics [{ctx.failure_type.value}]: {ctx.summary[:100]}",
            verified=True,
        )

    def _tool_inspect_performance(self, params: Dict[str, Any]) -> VSToolResult:
        """Inspects non-invasive performance metrics (build/test/debug durations, process stats)."""
        proc_name = params.get("process_name")
        metrics = self.perf_monitor.inspect_performance(target_process_name=proc_name)
        return VSToolResult(
            tool="vs.inspect_performance",
            success=True,
            data=metrics.to_dict(),
            message=f"Performance metrics: build={metrics.build_duration_s}s, test={metrics.test_duration_s}s, memory={metrics.process_memory_mb}MB.",
            verified=True,
        )

    def _tool_run_unified_workflow(self, params: Dict[str, Any]) -> VSToolResult:
        """Executes a bounded multi-stage workflow (INSPECT -> BUILD -> TEST -> DIAGNOSE -> VERIFY)."""
        goal = params.get("goal", "Inspect, build, test, and verify project")
        target = params.get("target_path") or self.safety.authorized_project
        stages = params.get("stages") or ["INSPECT", "BUILD", "TEST", "VERIFY"]

        stage_results = {}
        success = True
        stage_log = []

        for stg in stages:
            s_up = str(stg).upper().strip()
            stage_log.append(s_up)

            if s_up == "INSPECT":
                r = self.execute_tool("vs.inspect_project", {"project_path": str(target)})
                stage_results["INSPECT"] = r.to_dict()
                if not r.success:
                    success = False
                    break
            elif s_up == "BUILD":
                r = self.execute_tool("vs.run_safe_build", {"target_path": str(target)})
                stage_results["BUILD"] = r.to_dict()
                if not r.success:
                    success = False
                    break
            elif s_up == "TEST":
                r = self.execute_tool("vs.run_safe_test", {"target_path": str(target)})
                stage_results["TEST"] = r.to_dict()
                if not r.success:
                    success = False
                    break
            elif s_up == "DIAGNOSE":
                r = self.execute_tool("vs.inspect_diagnostics", {})
                stage_results["DIAGNOSE"] = r.to_dict()
            elif s_up == "PERFORMANCE":
                r = self.execute_tool("vs.inspect_performance", {})
                stage_results["PERFORMANCE"] = r.to_dict()
            elif s_up == "VERIFY":
                r = self.execute_tool("vs.verify_build_result", {"target_path": str(target)})
                stage_results["VERIFY"] = r.to_dict()
                if not r.success:
                    success = False
                    break

        return VSToolResult(
            tool="vs.run_unified_workflow",
            success=success,
            data={
                "goal": goal,
                "stages_executed": stage_log,
                "stage_results": stage_results,
                "success": success,
            },
            message=f"Unified workflow {'succeeded' if success else 'failed'} across stages: {' -> '.join(stage_log)}.",
            verified=success,
        )
