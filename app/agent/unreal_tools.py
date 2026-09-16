r"""
NR-AI Unreal Engine Agent Tool Registry & Dispatcher (Step 9 Phase 1).

Central registry and dispatcher for all approved Unreal Engine developer tools.
Enforces strict allowlists, parameter validation, rate limiting, and audit logging.
"""

from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import time
from typing import Any, Callable, Dict, List, Optional, Set
import uuid

from app.memory.audit_logger import AuditLogger
from app.agent.unreal_safety import (
    ALLOWED_UNREAL_TOOLS,
    ALL_ALLOWED_UNREAL_TOOLS,
    UnrealErrorCode,
    UnrealSafetyError,
    UnrealSafetyGate,
    DEFAULT_UNREAL_SAFETY_GATE,
    redact_sensitive_data,
)
from app.agent.unreal_environment import (
    UnrealEnvironmentDetector,
    DEFAULT_UNREAL_ENV_DETECTOR,
)
from app.agent.unreal_project import (
    UnrealProjectInspector,
    DEFAULT_UNREAL_PROJECT_INSPECTOR,
)

logger = logging.getLogger("NRAI.UnrealTools")


@dataclass
class UnrealToolResult:
    """Structured result returned by every Unreal developer tool call."""
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


class UnrealToolRegistry:
    """
    Manages and safely dispatches all allowed Unreal developer tools.
    Enforces the ALLOWED_UNREAL_TOOLS allowlist, validates boundaries,
    and logs all operations to the audit trail.
    """

    def __init__(
        self,
        safety_gate: Optional[UnrealSafetyGate] = None,
        env_detector: Optional[UnrealEnvironmentDetector] = None,
        inspector: Optional[UnrealProjectInspector] = None,
        audit_logger: Optional[AuditLogger] = None,
        workspace_root: Optional[Path] = None,
    ):
        self.safety = safety_gate or DEFAULT_UNREAL_SAFETY_GATE
        self.env = env_detector or DEFAULT_UNREAL_ENV_DETECTOR
        self.inspector = inspector or DEFAULT_UNREAL_PROJECT_INSPECTOR
        self.audit = audit_logger or AuditLogger()
        self.workspace_root = workspace_root or self.safety.workspace_root

        # Handler dispatch map
        self._handlers: Dict[str, Callable[..., UnrealToolResult]] = {
            # Step 9 Phase 1: Environment & Project Inspection Foundation
            "unreal.detect_environment": self._tool_detect_environment,
            "unreal.list_installations": self._tool_list_installations,
            "unreal.inspect_project": self._tool_inspect_project,
            "unreal.validate_project_path": self._tool_validate_project_path,
            "unreal.parse_uproject": self._tool_parse_uproject,
            "unreal.inspect_modules": self._tool_inspect_modules,
            "unreal.inspect_plugins": self._tool_inspect_plugins,
            "unreal.inspect_project_structure": self._tool_inspect_project_structure,
            "unreal.validate_engine_association": self._tool_validate_engine_association,
            "unreal.inspect_config": self._tool_inspect_config,
            "unreal.list_assets": self._tool_list_assets,
            "unreal.read_source_file": self._tool_read_source_file,
        }

    def get_registered_tools(self) -> List[str]:
        """Returns sorted list of all approved, registered Unreal developer tools."""
        return sorted(list(self._handlers.keys()))

    def dispatch(self, tool_name: str, **kwargs) -> UnrealToolResult:
        """Alias for execute_tool."""
        return self.execute_tool(tool_name, **kwargs)

    def execute_tool(self, tool_name: str, **kwargs) -> UnrealToolResult:
        """
        Executes an approved Unreal developer tool through safety gating and audit logging.
        """
        t0 = time.time()
        audit_details = {
            "tool": tool_name,
            "params": {k: redact_sensitive_data(str(v)) for k, v in kwargs.items()},
        }

        # 1. Assert tool is allowed
        try:
            self.safety.validate_tool_allowed(tool_name)
        except UnrealSafetyError as se:
            self._log_audit(tool_name, False, se.message, se.code.value, time.time() - t0, audit_details)
            return UnrealToolResult(
                tool=tool_name,
                success=False,
                error=se.message,
                error_code=se.code.value,
                message=f"Safety check rejected tool: {se.message}",
            )

        # 2. Check emergency stop
        try:
            self.safety.assert_not_emergency_stopped()
        except UnrealSafetyError as se:
            self._log_audit(tool_name, False, se.message, se.code.value, time.time() - t0, audit_details)
            return UnrealToolResult(
                tool=tool_name,
                success=False,
                error=se.message,
                error_code=se.code.value,
                message=f"Emergency stop frozen: {se.message}",
            )

        # 3. Check rate limiting
        try:
            self.safety.check_rate_limit()
        except UnrealSafetyError as se:
            self._log_audit(tool_name, False, se.message, se.code.value, time.time() - t0, audit_details)
            return UnrealToolResult(
                tool=tool_name,
                success=False,
                error=se.message,
                error_code=se.code.value,
                message=f"Rate limit exceeded: {se.message}",
            )

        # 4. Dispatch handler
        handler = self._handlers.get(tool_name)
        if not handler:
            err_msg = f"No handler registered for tool '{tool_name}'"
            self._log_audit(tool_name, False, err_msg, UnrealErrorCode.TOOL_NOT_ALLOWED.value, time.time() - t0, audit_details)
            return UnrealToolResult(
                tool=tool_name,
                success=False,
                error=err_msg,
                error_code=UnrealErrorCode.TOOL_NOT_ALLOWED.value,
            )

        try:
            result = handler(**kwargs)
            duration = time.time() - t0
            self._log_audit(
                tool_name,
                result.success,
                result.message or result.error or "Success",
                result.error_code,
                duration,
                audit_details,
            )
            return result
        except UnrealSafetyError as se:
            duration = time.time() - t0
            self._log_audit(tool_name, False, se.message, se.code.value, duration, audit_details)
            return UnrealToolResult(
                tool=tool_name,
                success=False,
                error=se.message,
                error_code=se.code.value,
                message=f"Safety policy rejected operation: {se.message}",
            )
        except Exception as e:
            duration = time.time() - t0
            err_msg = f"Unexpected execution failure in {tool_name}: {e}"
            logger.exception(err_msg)
            self._log_audit(tool_name, False, err_msg, "INTERNAL_ERROR", duration, audit_details)
            return UnrealToolResult(
                tool=tool_name,
                success=False,
                error=err_msg,
                error_code="INTERNAL_ERROR",
                message=err_msg,
            )

    def _log_audit(
        self,
        tool: str,
        success: bool,
        message: str,
        error_code: Optional[str],
        duration: float,
        details: Dict[str, Any],
    ) -> None:
        """Emits a structured audit log entry."""
        try:
            status_str = "success" if success else "failure"
            self.audit.log_event(
                event_type="unreal_tool_dispatch",
                action=tool,
                status=status_str,
                details={
                    "tool": tool,
                    "success": success,
                    "message": redact_sensitive_data(message),
                    "error_code": error_code,
                    "duration_seconds": round(duration, 4),
                    "audit": details,
                },
            )
        except Exception as e:
            logger.debug(f"Failed to record audit event: {e}")

    # -------------------------------------------------------------------------
    # Tool Handlers (Phase 1)
    # -------------------------------------------------------------------------

    def _tool_detect_environment(self, **kwargs) -> UnrealToolResult:
        """unreal.detect_environment: Detects host Unreal Engine installations and environment."""
        info = self.env.detect_environment()
        return UnrealToolResult(
            tool="unreal.detect_environment",
            success=True,
            data=info.to_dict(),
            verified=True,
            message=f"Unreal environment detected: {len(info.engines)} installation(s) found.",
        )

    def _tool_list_installations(self, **kwargs) -> UnrealToolResult:
        """unreal.list_installations: Lists all discovered Unreal Engine installations."""
        engines = self.env.detect_engines()
        return UnrealToolResult(
            tool="unreal.list_installations",
            success=True,
            data={"engines": [e.to_dict() for e in engines], "count": len(engines)},
            verified=True,
            message=f"Discovered {len(engines)} Unreal Engine installation(s).",
        )

    def _tool_inspect_project(self, project_path: str = "", **kwargs) -> UnrealToolResult:
        """unreal.inspect_project: Full structured inspection of an Unreal project."""
        target = project_path or str(self.safety.authorized_projects[0])
        metadata = self.inspector.inspect_project(target)
        return UnrealToolResult(
            tool="unreal.inspect_project",
            success=metadata.is_valid,
            data=metadata.to_dict(),
            verified=metadata.is_valid,
            error=None if metadata.is_valid else "; ".join(metadata.validation_errors),
            error_code=None if metadata.is_valid else UnrealErrorCode.PROJECT_INVALID.value,
            message=f"Project inspection {'succeeded' if metadata.is_valid else 'failed'} for {metadata.name}.",
        )

    def _tool_validate_project_path(self, project_path: str = "", **kwargs) -> UnrealToolResult:
        """unreal.validate_project_path: Validates that a project path is authorized and within workspace."""
        if not project_path:
            return UnrealToolResult(
                tool="unreal.validate_project_path",
                success=False,
                error="Project path parameter is required.",
                error_code=UnrealErrorCode.INVALID_PARAMETER.value,
            )
        resolved = self.safety.validate_project_path(project_path)
        return UnrealToolResult(
            tool="unreal.validate_project_path",
            success=True,
            data={"validated_path": str(resolved), "workspace_root": str(self.workspace_root)},
            verified=True,
            message=f"Project path '{resolved}' is valid and authorized.",
        )

    def _tool_parse_uproject(self, uproject_path: str = "", project_path: str = "", **kwargs) -> UnrealToolResult:
        """unreal.parse_uproject: Parses and validates a .uproject file."""
        target_path: Optional[Path] = None
        if uproject_path:
            target_path = Path(uproject_path)
        elif project_path:
            proj = self.safety.validate_project_path(project_path)
            uprojects = list(proj.glob("*.uproject"))
            if uprojects:
                target_path = uprojects[0]

        if not target_path:
            return UnrealToolResult(
                tool="unreal.parse_uproject",
                success=False,
                error="Target .uproject path could not be resolved.",
                error_code=UnrealErrorCode.INVALID_UPROJECT.value,
            )

        validated_file = self.safety.validate_file_path(target_path)
        res = self.inspector.parse_uproject(validated_file)
        success = res.get("is_valid", False)
        return UnrealToolResult(
            tool="unreal.parse_uproject",
            success=success,
            data=res,
            verified=success,
            error=None if success else res.get("error"),
            error_code=None if success else UnrealErrorCode.INVALID_UPROJECT.value,
            message=f".uproject parsing {'succeeded' if success else 'failed'}.",
        )

    def _tool_inspect_modules(self, project_path: str = "", **kwargs) -> UnrealToolResult:
        """unreal.inspect_modules: Discovers and inspects modules and Build.cs files."""
        target = project_path or str(self.safety.authorized_projects[0])
        meta = self.inspector.inspect_project(target)
        return UnrealToolResult(
            tool="unreal.inspect_modules",
            success=True,
            data={
                "modules": [m.to_dict() for m in meta.modules],
                "build_cs_files": meta.build_cs_files,
                "target_cs_files": meta.target_cs_files,
                "count": len(meta.modules),
            },
            verified=True,
            message=f"Found {len(meta.modules)} declared module(s) and {len(meta.build_cs_files)} Build.cs file(s).",
        )

    def _tool_inspect_plugins(self, project_path: str = "", **kwargs) -> UnrealToolResult:
        """unreal.inspect_plugins: Discovers and inspects declared and local plugins."""
        target = project_path or str(self.safety.authorized_projects[0])
        meta = self.inspector.inspect_project(target)
        return UnrealToolResult(
            tool="unreal.inspect_plugins",
            success=True,
            data={"plugins": [p.to_dict() for p in meta.plugins], "count": len(meta.plugins)},
            verified=True,
            message=f"Found {len(meta.plugins)} plugin(s).",
        )

    def _tool_inspect_project_structure(self, project_path: str = "", **kwargs) -> UnrealToolResult:
        """unreal.inspect_project_structure: Analyzes directories, source files, and asset distribution."""
        target = project_path or str(self.safety.authorized_projects[0])
        meta = self.inspector.inspect_project(target)
        return UnrealToolResult(
            tool="unreal.inspect_project_structure",
            success=True,
            data={
                "name": meta.name,
                "is_cpp": meta.is_cpp,
                "is_blueprint": meta.is_blueprint,
                "source_files_count": meta.source_files_count,
                "header_files_count": meta.header_files_count,
                "assets_count": meta.assets_count,
                "maps_count": meta.maps_count,
                "config_files": meta.config_files,
            },
            verified=True,
            message=f"Project structure analyzed for {meta.name}: {meta.source_files_count} cpp files, {meta.assets_count} assets.",
        )

    def _tool_validate_engine_association(self, project_path: str = "", **kwargs) -> UnrealToolResult:
        """unreal.validate_engine_association: Verifies project's EngineAssociation against installed engines."""
        target = project_path or str(self.safety.authorized_projects[0])
        res = self.inspector.validate_engine_association(target)
        success = res.get("is_valid", False)
        return UnrealToolResult(
            tool="unreal.validate_engine_association",
            success=success,
            data=res,
            verified=success and res.get("is_matched_with_installed", False),
            error=None if success else res.get("message"),
            error_code=None if success else UnrealErrorCode.PROJECT_INVALID.value,
            message=res.get("message", ""),
        )

    def _tool_inspect_config(self, project_path: str = "", **kwargs) -> UnrealToolResult:
        """unreal.inspect_config: Discovers and inspects project Config/*.ini files."""
        target = project_path or str(self.safety.authorized_projects[0])
        proj = self.safety.validate_project_path(target)
        config_dir = proj / "Config"
        config_data: Dict[str, Any] = {}
        if config_dir.is_dir():
            for ini_file in config_dir.glob("*.ini"):
                rel_name = ini_file.name
                try:
                    lines = ini_file.read_text(encoding="utf-8", errors="ignore").splitlines()
                    config_data[rel_name] = {
                        "path": str(ini_file.relative_to(proj)).replace("\\", "/"),
                        "lines_count": len(lines),
                        "sections": [line.strip() for line in lines if line.strip().startswith("[") and line.strip().endswith("]")],
                    }
                except Exception as e:
                    config_data[rel_name] = {"error": str(e)}

        return UnrealToolResult(
            tool="unreal.inspect_config",
            success=True,
            data={"config_files": config_data, "count": len(config_data)},
            verified=True,
            message=f"Found {len(config_data)} configuration file(s) in Config/.",
        )

    def _tool_list_assets(self, project_path: str = "", **kwargs) -> UnrealToolResult:
        """unreal.list_assets: Lists all .uasset and .umap assets in Content/."""
        target = project_path or str(self.safety.authorized_projects[0])
        assets = self.inspector.list_assets(target)
        return UnrealToolResult(
            tool="unreal.list_assets",
            success=True,
            data={"assets": [a.to_dict() for a in assets], "count": len(assets)},
            verified=True,
            message=f"Discovered {len(assets)} asset(s) in Content/.",
        )

    def _tool_read_source_file(
        self,
        file_path: str = "",
        project_path: str = "",
        max_lines: int = 1000,
        **kwargs,
    ) -> UnrealToolResult:
        """unreal.read_source_file: Bounded safe reading of an Unreal C++, C#, or ini source file."""
        if not file_path:
            return UnrealToolResult(
                tool="unreal.read_source_file",
                success=False,
                error="file_path parameter is required.",
                error_code=UnrealErrorCode.INVALID_PARAMETER.value,
            )

        proj_root = Path(project_path).resolve() if project_path else None
        read_res = self.inspector.read_source_file(file_path, project_root=proj_root, max_lines=max_lines)
        return UnrealToolResult(
            tool="unreal.read_source_file",
            success=True,
            data=read_res,
            verified=True,
            message=f"Successfully read {read_res['lines_read']} lines from {read_res['relative_path']}.",
        )


# Global default tool registry
DEFAULT_UNREAL_TOOL_REGISTRY = UnrealToolRegistry()
