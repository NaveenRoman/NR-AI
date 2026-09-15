"""
NR-AI Unity Agent Tool Registry & Dispatcher (Step 8 Phase 1).

Central registry and dispatcher for all approved Unity developer tools.
Enforces strict allowlists, parameter validation, rate limiting, and audit logging.
"""

from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import time
from typing import Any, Callable, Dict, List, Optional, Set

from app.memory.audit_logger import AuditLogger
from app.agent.unity_safety import (
    ALLOWED_UNITY_TOOLS,
    UnityErrorCode,
    UnitySafetyError,
    UnitySafetyGate,
    DEFAULT_UNITY_SAFETY_GATE,
    redact_sensitive_data,
)
from app.agent.unity_environment import (
    UnityEnvironmentDetector,
    DEFAULT_UNITY_ENV_DETECTOR,
)
from app.agent.unity_project import (
    UnityProjectInspector,
    DEFAULT_UNITY_PROJECT_INSPECTOR,
)

logger = logging.getLogger("NRAI.UnityTools")


@dataclass
class UnityToolResult:
    """Structured result returned by every Unity tool call."""
    tool: str
    success: bool
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    error_code: Optional[str] = None
    message: str = ""
    verified: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool": self.tool,
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "error_code": self.error_code,
            "message": self.message,
            "verified": self.verified,
        }


class UnityToolRegistry:
    """
    Manages and safely dispatches all allowed Unity developer tools.
    Enforces the ALLOWED_UNITY_TOOLS allowlist, validates boundaries,
    and logs all operations to the audit trail.
    """

    def __init__(
        self,
        safety_gate: Optional[UnitySafetyGate] = None,
        env_detector: Optional[UnityEnvironmentDetector] = None,
        inspector: Optional[UnityProjectInspector] = None,
        audit_logger: Optional[AuditLogger] = None,
        workspace_root: Optional[Path] = None,
    ):
        self.safety = safety_gate or DEFAULT_UNITY_SAFETY_GATE
        self.env = env_detector or DEFAULT_UNITY_ENV_DETECTOR
        self.inspector = inspector or DEFAULT_UNITY_PROJECT_INSPECTOR
        self.audit = audit_logger or AuditLogger()
        self.workspace_root = workspace_root or self.safety.workspace_root
        self._handlers: Dict[str, Callable[[Dict[str, Any]], UnityToolResult]] = {
            "unity.detect_environment": self._tool_detect_environment,
            "unity.inspect_project": self._tool_inspect_project,
            "unity.list_projects": self._tool_list_projects,
            "unity.inspect_packages": self._tool_inspect_packages,
            "unity.list_assets": self._tool_list_assets,
            "unity.find_scripts": self._tool_find_scripts,
            "unity.read_script": self._tool_read_script,
            "unity.inspect_asmdef": self._tool_inspect_asmdef,
            "unity.inspect_scenes_in_build": self._tool_inspect_scenes_in_build,
            "unity.inspect_editor_state": self._tool_inspect_editor_state,
            "unity.validate_project_structure": self._tool_validate_project_structure,
            "unity.inspect_project_version": self._tool_inspect_project_version,
        }

    def get_registered_tools(self) -> Set[str]:
        """Returns the set of registered tool names."""
        return set(self._handlers.keys())

    def _get_handler(self, tool_name: str) -> Optional[Callable[[Dict[str, Any]], UnityToolResult]]:
        """Returns the registered method for a tool name."""
        return self._handlers.get(tool_name)

    def _log_audit(self, event_type: str, details: Dict[str, Any], status: str = "success") -> None:
        """Safely dispatches audit logs to AuditLogger instance."""
        if not self.audit:
            return
        try:
            if hasattr(self.audit, "log_event"):
                self.audit.log_event(event_type=event_type, details=details, status=status)
            elif hasattr(self.audit, "log"):
                self.audit.log(event_type=event_type, action=details.get("tool", "unity_tool"), status=status.upper(), details=details)
        except Exception as log_err:
            logger.warning(f"Audit log failed: {log_err}")

    def execute_tool(self, tool_name: str, params: Optional[Dict[str, Any]] = None) -> UnityToolResult:
        """
        Safely dispatches a tool call through the safety gate with audit logging.
        """
        params = params or {}
        t0 = time.time()

        try:
            # 1. Safety check allowlist
            self.safety.validate_tool_allowed(tool_name)
            # 2. Rate limit check
            self.safety.check_rate_limit(tool_name)

            # 3. Retrieve handler
            handler = self._get_handler(tool_name)
            if not handler:
                raise UnitySafetyError(
                    UnityErrorCode.TOOL_NOT_ALLOWED,
                    f"Tool '{tool_name}' has no registered execution handler.",
                )

            # 4. Execute tool
            res = handler(params)

            dur = time.time() - t0
            self._log_audit(
                event_type="unity_tool_execution",
                details={
                    "tool": tool_name,
                    "success": res.success,
                    "duration_s": dur,
                    "message": redact_sensitive_data(res.message),
                },
                status="success" if res.success else "failure",
            )
            return res

        except UnitySafetyError as e:
            dur = time.time() - t0
            self._log_audit(
                event_type="unity_safety_rejection",
                details={"tool": tool_name, "error": str(e), "code": e.code.value, "duration_s": dur},
                status="rejected",
            )
            return UnityToolResult(
                tool=tool_name,
                success=False,
                error=str(e),
                error_code=e.code.value,
                message=f"Safety rejection: {e}",
                verified=False,
            )
        except Exception as e:
            dur = time.time() - t0
            self._log_audit(
                event_type="unity_tool_error",
                details={"tool": tool_name, "error": str(e), "duration_s": dur},
                status="error",
            )
            return UnityToolResult(
                tool=tool_name,
                success=False,
                error=str(e),
                error_code="INTERNAL_ERROR",
                message=f"Tool execution failed: {e}",
                verified=False,
            )

    # -------------------------------------------------------------------------
    # Tool Handlers
    # -------------------------------------------------------------------------

    def _tool_detect_environment(self, params: Dict[str, Any]) -> UnityToolResult:
        """Discovers Unity editors, Unity Hub, and official Unity CLI."""
        info = self.env.detect_environment()
        return UnityToolResult(
            tool="unity.detect_environment",
            success=True,
            data=info.to_dict(),
            message=f"Detected {len(info.editors)} Unity editor(s). CLI installed: {info.cli.is_installed}.",
            verified=True,
        )

    def _tool_inspect_project(self, params: Dict[str, Any]) -> UnityToolResult:
        """Inspects target Unity project metadata, scripts, scenes, and packages."""
        proj_p = params.get("project_path") or params.get("path") or self.safety.authorized_project
        meta = self.inspector.inspect_project(Path(proj_p))
        return UnityToolResult(
            tool="unity.inspect_project",
            success=True,
            data=meta.to_dict(),
            message=f"Inspected Unity project '{meta.name}' (version {meta.unity_version}).",
            verified=True,
        )

    def _tool_list_projects(self, params: Dict[str, Any]) -> UnityToolResult:
        """Lists authorized Unity projects under the workspace root."""
        root = Path(params.get("root_path") or self.safety.workspace_root)
        self.safety.validate_path(root)

        projects = []
        # Check standard authorized project
        if self.inspector.is_valid_project(self.safety.authorized_project):
            projects.append(str(self.safety.authorized_project))

        # Check subdirectories of workspace
        for item in root.iterdir():
            if item.is_dir() and item != self.safety.authorized_project:
                if self.inspector.is_valid_project(item):
                    projects.append(str(item))

        return UnityToolResult(
            tool="unity.list_projects",
            success=True,
            data={"projects": projects, "count": len(projects)},
            message=f"Found {len(projects)} valid Unity project(s).",
            verified=True,
        )

    def _tool_inspect_packages(self, params: Dict[str, Any]) -> UnityToolResult:
        """Parses Packages/manifest.json dependencies."""
        proj_p = params.get("project_path") or self.safety.authorized_project
        pkgs = self.inspector.inspect_packages(Path(proj_p))
        return UnityToolResult(
            tool="unity.inspect_packages",
            success=True,
            data={"packages": pkgs, "count": len(pkgs)},
            message=f"Found {len(pkgs)} package dependency/ies.",
            verified=True,
        )

    def _tool_list_assets(self, params: Dict[str, Any]) -> UnityToolResult:
        """Lists assets with optional type filter (Script, Scene, Prefab, Material, AsmDef)."""
        proj_p = params.get("project_path") or self.safety.authorized_project
        atype = params.get("asset_type")
        subpath = params.get("subpath", "Assets")
        assets = self.inspector.list_assets(Path(proj_p), asset_type=atype, subpath=subpath)
        serialized = [a.to_dict() for a in assets]
        return UnityToolResult(
            tool="unity.list_assets",
            success=True,
            data={"assets": serialized, "count": len(serialized)},
            message=f"Listed {len(serialized)} asset(s) (filter: {atype or 'all'}).",
            verified=True,
        )

    def _tool_find_scripts(self, params: Dict[str, Any]) -> UnityToolResult:
        """Finds C# scripts matching pattern."""
        proj_p = params.get("project_path") or self.safety.authorized_project
        pattern = params.get("pattern", "*.cs")
        scripts = self.inspector.find_scripts(Path(proj_p), pattern=pattern)
        return UnityToolResult(
            tool="unity.find_scripts",
            success=True,
            data={"scripts": scripts, "count": len(scripts)},
            message=f"Found {len(scripts)} C# script(s) matching '{pattern}'.",
            verified=True,
        )

    def _tool_read_script(self, params: Dict[str, Any]) -> UnityToolResult:
        """Reads a C# script with bounded lines and SHA-256 computation."""
        f_path = params.get("file_path") or params.get("path")
        if not f_path and params.get("project_path") and params.get("relative_path"):
            f_path = Path(params["project_path"]) / params["relative_path"]
        if not f_path:
            raise UnitySafetyError(UnityErrorCode.FILE_NOT_AUTHORIZED, "file_path is required.")
        max_l = int(params.get("max_lines", 500))
        data = self.inspector.read_script(Path(f_path), max_lines=max_l)
        return UnityToolResult(
            tool="unity.read_script",
            success=True,
            data=data,
            message=f"Read {data['lines_returned']}/{data['total_lines']} lines from '{Path(f_path).name}'.",
            verified=True,
        )

    def _tool_inspect_asmdef(self, params: Dict[str, Any]) -> UnityToolResult:
        """Parses a Unity assembly definition (.asmdef) file."""
        asmdef_p = params.get("asmdef_path") or params.get("path")
        if not asmdef_p and params.get("project_path"):
            asmdefs = self.inspector.list_assets(Path(params["project_path"]), asset_type="AsmDef")
            if asmdefs:
                all_asmdefs = [self.inspector.inspect_asmdef(Path(a.absolute_path)) for a in asmdefs]
                return UnityToolResult(
                    tool="unity.inspect_asmdef",
                    success=True,
                    data={"asmdefs": all_asmdefs, "count": len(all_asmdefs)},
                    message=f"Found {len(all_asmdefs)} asmdef file(s).",
                    verified=True,
                )
        if not asmdef_p:
            raise UnitySafetyError(UnityErrorCode.FILE_NOT_AUTHORIZED, "asmdef_path is required.")
        data = self.inspector.inspect_asmdef(Path(asmdef_p))
        return UnityToolResult(
            tool="unity.inspect_asmdef",
            success=True,
            data=data,
            message=f"Parsed asmdef '{data['name']}' with {len(data['references'])} references.",
            verified=True,
        )

    def _tool_inspect_scenes_in_build(self, params: Dict[str, Any]) -> UnityToolResult:
        """Inspects build scene configuration."""
        proj_p = params.get("project_path") or self.safety.authorized_project
        scenes = self.inspector.inspect_scenes_in_build(Path(proj_p))
        return UnityToolResult(
            tool="unity.inspect_scenes_in_build",
            success=True,
            data={"scenes": scenes, "count": len(scenes)},
            message=f"Found {len(scenes)} scene(s) in build settings.",
            verified=True,
        )

    def _tool_inspect_editor_state(self, params: Dict[str, Any]) -> UnityToolResult:
        """Inspects active Unity Editor processes and running states."""
        procs = self.env.get_running_editor_processes()
        is_running = len(procs) > 0
        return UnityToolResult(
            tool="unity.inspect_editor_state",
            success=True,
            data={"is_running": is_running, "running_processes": procs, "count": len(procs)},
            message=f"Unity Editor running: {is_running} ({len(procs)} process(es)).",
            verified=True,
        )

    def _tool_validate_project_structure(self, params: Dict[str, Any]) -> UnityToolResult:
        """Validates that target directory meets Unity project requirements."""
        proj_p = params.get("project_path") or self.safety.authorized_project
        is_valid = self.inspector.is_valid_project(Path(proj_p))
        return UnityToolResult(
            tool="unity.validate_project_structure",
            success=is_valid,
            data={"is_valid": is_valid, "project_path": str(proj_p)},
            message=f"Project structure is {'valid' if is_valid else 'invalid'}.",
            verified=True,
        )

    def _tool_inspect_project_version(self, params: Dict[str, Any]) -> UnityToolResult:
        """Reads target editor version from ProjectSettings/ProjectVersion.txt."""
        proj_p = params.get("project_path") or self.safety.authorized_project
        v = self.inspector.get_project_version(Path(proj_p))
        return UnityToolResult(
            tool="unity.inspect_project_version",
            success=(v != "Unknown"),
            data={"version": v, "project_path": str(proj_p)},
            message=f"Target Unity Editor version: {v}.",
            verified=True,
        )


DEFAULT_UNITY_TOOL_REGISTRY = UnityToolRegistry()
