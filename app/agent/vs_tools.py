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
    redact_sensitive_data,
)
from app.agent.vs_environment import VSEnvironmentDetector, VSEnvironmentInfo
from app.agent.vs_project import VSProjectInspector, VSProjectMetadata, VSSolutionMetadata
from app.memory.audit_logger import AuditLogger

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
    ):
        self.safety = safety_gate or VSSafetyGate()
        self.env = env_detector or VSEnvironmentDetector()

    def run_build(
        self,
        target_path: Path,
        action: str = "BUILD",
        configuration: str = "Debug",
        timeout: float = BUILD_TIMEOUT_SECONDS,
    ) -> Dict[str, Any]:
        """Executes an authorized build action on a project or solution file."""
        self.safety.check_emergency_stop()
        validated_action = self.safety.validate_build_action(action)
        validated_target = self.safety.validate_file_path(target_path, check_writable=False)

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
        else:
            cmd.append(msbuild)
            target_switch = f"/t:{'Rebuild' if validated_action == 'REBUILD' else ('Clean' if validated_action == 'CLEAN' else 'Build')}"
            cmd.extend([str(validated_target), target_switch, f"/p:Configuration={configuration}", "/v:m"])

        start_time = time.time()
        try:
            res = subprocess.run(
                cmd,
                cwd=str(validated_target.parent),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=False,
                timeout=timeout,
            )
            duration = time.time() - start_time
            raw = (res.stdout or "") + ("\n" + res.stderr if res.stderr else "")
            sanitized = redact_sensitive_data(raw)
            success = (res.returncode == 0)

            return {
                "success": success,
                "returncode": res.returncode,
                "command": [cmd[0]] + cmd[1:],
                "output_sample": sanitized[:2000],
                "full_output": sanitized,
                "duration_s": round(duration, 2),
            }
        except subprocess.TimeoutExpired:
            duration = time.time() - start_time
            return {
                "success": False,
                "returncode": -1,
                "error_code": VSErrorCode.BUILD_TIMEOUT.value,
                "output_sample": f"Build timed out after {timeout}s.",
                "full_output": "Build timed out.",
                "duration_s": round(duration, 2),
            }
        except Exception as e:
            duration = time.time() - start_time
            return {
                "success": False,
                "returncode": -1,
                "error_code": VSErrorCode.BUILD_FAILED.value,
                "output_sample": str(e),
                "full_output": str(e),
                "duration_s": round(duration, 2),
            }

    def run_test(
        self,
        target_path: Path,
        filter_expr: Optional[str] = None,
        timeout: float = TEST_TIMEOUT_SECONDS,
    ) -> Dict[str, Any]:
        """Executes tests safely via dotnet test or vstest.console."""
        self.safety.check_emergency_stop()
        validated_target = self.safety.validate_file_path(target_path, check_writable=False)

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
            cmd.extend([dotnet, "test", str(validated_target), "--verbosity", "normal"])
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
                timeout=timeout,
            )
            duration = time.time() - start_time
            raw = (res.stdout or "") + ("\n" + res.stderr if res.stderr else "")
            sanitized = redact_sensitive_data(raw)
            success = (res.returncode == 0)

            return {
                "success": success,
                "returncode": res.returncode,
                "output_sample": sanitized[:2000],
                "full_output": sanitized,
                "duration_s": round(duration, 2),
            }
        except subprocess.TimeoutExpired:
            duration = time.time() - start_time
            return {
                "success": False,
                "returncode": -1,
                "error_code": VSErrorCode.TEST_TIMEOUT.value,
                "output_sample": f"Test execution timed out after {timeout}s.",
                "full_output": "Test execution timed out.",
                "duration_s": round(duration, 2),
            }
        except Exception as e:
            duration = time.time() - start_time
            return {
                "success": False,
                "returncode": -1,
                "error_code": VSErrorCode.TEST_FAILED.value,
                "output_sample": str(e),
                "full_output": str(e),
                "duration_s": round(duration, 2),
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
    ):
        if safety_gate is None and workspace_root is not None:
            self.safety = VSSafetyGate(authorized_project=Path(workspace_root).resolve())
        else:
            self.safety = safety_gate or VSSafetyGate()
        self.env = env_detector or VSEnvironmentDetector()
        self.inspector = project_inspector or VSProjectInspector(safety_gate=self.safety)
        self.runner = msbuild_runner or SafeMSBuildRunner(safety_gate=self.safety, env_detector=self.env)
        self.audit = audit_logger
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None
        self.last_build_output: Dict[str, Any] = {}

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
            "vs.inspect_dependencies": self._tool_inspect_dependencies,
            "vs.run_safe_build": self.run_safe_build,
            "vs.run_safe_test": self._tool_run_safe_test,
            "vs.capture_build_output": self._tool_capture_build_output,
            "vs.verify_build_result": self._tool_verify_build_result,
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

        if val_path.suffix.lower() in (".sln", ".slnx"):
            meta = self.inspector.inspect_solution(val_path)
            configs = meta.configurations or configs
        elif val_path.suffix.lower() in (".csproj", ".vbproj", ".fsproj"):
            meta = self.inspector.inspect_project(val_path)
            configs = [f"{c}|Any CPU" for c in configs]

        return VSToolResult(
            tool="vs.inspect_build_configuration",
            success=True,
            data={"target": str(val_path), "configurations": configs},
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

        build_res = self.runner.run_build(val_target, action=action, configuration=cfg)
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
        test_res = self.runner.run_test(val_target, filter_expr=filter_exp)
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

        verified = bool(binaries)
        return VSToolResult(
            tool="vs.verify_build_result",
            success=verified,
            data={"has_bin_dir": has_bin, "binaries_found": binaries, "count": len(binaries)},
            message=f"Build result verification: {len(binaries)} build artifact(s) found.",
            verified=verified,
        )
