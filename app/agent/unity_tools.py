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
import uuid

from app.memory.audit_logger import AuditLogger
from app.agent.unity_safety import (
    ALLOWED_UNITY_TOOLS,
    ALLOWED_UNITY_BUILD_TOOLS,
    ALLOWED_UNITY_TEST_TOOLS,
    ALLOWED_UNITY_AST_TOOLS,
    ALL_ALLOWED_UNITY_TOOLS,
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
from app.agent.unity_build import (
    UnityBuildManager,
    DEFAULT_UNITY_BUILD_MANAGER,
    UnityLogParser,
)
from app.agent.unity_tests import (
    UnityTestManager,
    DEFAULT_UNITY_TEST_MANAGER,
    DEFAULT_TEST_TIMEOUT,
)
from app.agent.unity_ast import (
    UnityScriptManager,
    DEFAULT_UNITY_SCRIPT_MANAGER,
    ASTModificationProposal,
    ASTModificationType,
    CSharpParser,
    UnityScriptAnalyzer,
    UnityASTModifier,
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
        build_manager: Optional[UnityBuildManager] = None,
        test_manager: Optional[UnityTestManager] = None,
        ast_manager: Optional[UnityScriptManager] = None,
        audit_logger: Optional[AuditLogger] = None,
        workspace_root: Optional[Path] = None,
        include_build_tools: Optional[bool] = None,
        include_test_tools: Optional[bool] = None,
        include_ast_tools: Optional[bool] = None,
    ):
        self.safety = safety_gate or DEFAULT_UNITY_SAFETY_GATE
        self.env = env_detector or DEFAULT_UNITY_ENV_DETECTOR
        self.inspector = inspector or DEFAULT_UNITY_PROJECT_INSPECTOR
        self.build = build_manager or DEFAULT_UNITY_BUILD_MANAGER
        self.tests = test_manager or DEFAULT_UNITY_TEST_MANAGER
        self.ast = ast_manager or DEFAULT_UNITY_SCRIPT_MANAGER
        self.audit = audit_logger or AuditLogger()
        self.workspace_root = workspace_root or self.safety.workspace_root

        if include_build_tools is None:
            include_build_tools = (build_manager is not None) or (inspector is None and env_detector is None)

        if include_test_tools is None:
            include_test_tools = (test_manager is not None) or (
                inspector is None and env_detector is None and build_manager is None
            )

        if include_ast_tools is None:
            include_ast_tools = (ast_manager is not None) or (
                inspector is None and env_detector is None and build_manager is None and test_manager is None
            )

        self._handlers: Dict[str, Callable[[Dict[str, Any]], UnityToolResult]] = {
            # Step 8 Phase 1: Environment & Project Inspection Foundation
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

        if include_build_tools:
            # Step 8 Phase 2: Compilation & Build Foundation
            self._handlers.update({
                "unity.validate_build_target": self._tool_validate_build_target,
                "unity.validate_build_output_path": self._tool_validate_build_output_path,
                "unity.compile_project": self._tool_compile_project,
                "unity.build_player": self._tool_build_player,
                "unity.verify_build_artifact": self._tool_verify_build_artifact,
                "unity.parse_build_log": self._tool_parse_build_log,
                "unity.get_build_configuration": self._tool_get_build_configuration,
                "unity.clean_build_target": self._tool_clean_build_target,
            })

        if include_test_tools:
            # Step 8 Phase 3: Unity Test Runner & PlayMode Foundation
            self._handlers.update({
                "unity.validate_test_mode": self._tool_validate_test_mode,
                "unity.validate_test_filter": self._tool_validate_test_filter,
                "unity.run_editmode_tests": self._tool_run_editmode_tests,
                "unity.run_playmode_tests": self._tool_run_playmode_tests,
                "unity.parse_test_results": self._tool_parse_test_results,
                "unity.verify_test_artifact": self._tool_verify_test_artifact,
                "unity.get_test_summary": self._tool_get_test_summary,
                "unity.diagnose_test_failure": self._tool_diagnose_test_failure,
            })

        if include_ast_tools:
            # Step 8 Phase 4: Unity C# Script Analysis & AST Modification
            self._handlers.update({
                "unity.parse_script_ast": self._tool_parse_script_ast,
                "unity.analyze_script": self._tool_analyze_script,
                "unity.propose_ast_modification": self._tool_propose_ast_modification,
                "unity.apply_ast_modification": self._tool_apply_ast_modification,
                "unity.verify_ast_integrity": self._tool_verify_ast_integrity,
                "unity.rollback_script_modification": self._tool_rollback_script_modification,
                "unity.get_script_symbols": self._tool_get_script_symbols,
                "unity.validate_script_repair": self._tool_validate_script_repair,
            })

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

    # -------------------------------------------------------------------------
    # Step 8 Phase 2: Compilation & Build Foundation Tools
    # -------------------------------------------------------------------------

    def _tool_validate_build_target(self, params: Dict[str, Any]) -> UnityToolResult:
        """Validates that requested build target is supported and allowed."""
        target = params.get("target") or params.get("build_target")
        if not target:
            raise UnitySafetyError(UnityErrorCode.INVALID_BUILD_TARGET, "target is required.")
        valid = self.build.validate_build_target(str(target))
        return UnityToolResult(
            tool="unity.validate_build_target",
            success=True,
            data={"target": valid, "valid": True},
            message=f"Build target '{valid}' is valid and supported.",
            verified=True,
        )

    def _tool_validate_build_output_path(self, params: Dict[str, Any]) -> UnityToolResult:
        """Validates build output destination path."""
        out_p = params.get("output_path") or params.get("path")
        if not out_p:
            raise UnitySafetyError(UnityErrorCode.INVALID_BUILD_OUTPUT, "output_path is required.")
        proj_p = Path(params.get("project_path")) if params.get("project_path") else None
        resolved = self.build.validate_build_output_path(out_p, project_root=proj_p)
        return UnityToolResult(
            tool="unity.validate_build_output_path",
            success=True,
            data={"output_path": str(resolved), "valid": True},
            message=f"Build output path '{resolved}' is valid and confined.",
            verified=True,
        )

    def _tool_compile_project(self, params: Dict[str, Any]) -> UnityToolResult:
        """Executes batchmode compilation of Unity project scripts."""
        proj_p = params.get("project_path") or self.safety.authorized_project
        editor_p = params.get("editor_path")
        timeout = float(params.get("timeout_seconds") or 120.0)
        res = self.build.compile_project(proj_p, editor_path=editor_p, timeout_seconds=timeout)
        return UnityToolResult(
            tool="unity.compile_project",
            success=res.success,
            data=res.to_dict(),
            error=res.error_summary if not res.success else None,
            error_code=UnityErrorCode.COMPILATION_FAILED.value if not res.success else None,
            message=f"Compilation {'succeeded' if res.success else 'failed'}: {res.error_summary or 'All scripts compiled cleanly.'}",
            verified=res.verified,
        )

    def _tool_build_player(self, params: Dict[str, Any]) -> UnityToolResult:
        """Executes controlled player build for target platform."""
        proj_p = params.get("project_path") or self.safety.authorized_project
        target = params.get("build_target") or "StandaloneWindows64"
        out_p = params.get("output_path")
        editor_p = params.get("editor_path")
        scenes = params.get("scenes")
        timeout = float(params.get("timeout_seconds") or 300.0)
        res = self.build.build_player(
            proj_p,
            build_target=target,
            output_path=out_p,
            scenes=scenes,
            editor_path=editor_p,
            timeout_seconds=timeout,
        )
        return UnityToolResult(
            tool="unity.build_player",
            success=res.success,
            data=res.to_dict(),
            error=res.error_summary if not res.success else None,
            error_code=UnityErrorCode.BUILD_FAILED.value if not res.success else None,
            message=f"Player build for {target} {'succeeded' if res.success else 'failed'}: {res.error_summary or 'Artifact verified.'}",
            verified=res.verified,
        )

    def _tool_verify_build_artifact(self, params: Dict[str, Any]) -> UnityToolResult:
        """Verifies build artifact existence, size, and SHA-256 hash."""
        out_p = params.get("output_path") or params.get("path")
        if not out_p:
            raise UnitySafetyError(UnityErrorCode.INVALID_BUILD_OUTPUT, "output_path is required.")
        data = self.build.verify_build_artifact(out_p)
        return UnityToolResult(
            tool="unity.verify_build_artifact",
            success=data.get("verified", False),
            data=data,
            error=data.get("error"),
            error_code=UnityErrorCode.BUILD_ARTIFACT_MISSING.value if not data.get("verified") else None,
            message=f"Artifact '{out_p}' verified: {data.get('verified', False)}.",
            verified=data.get("verified", False),
        )

    def _tool_parse_build_log(self, params: Dict[str, Any]) -> UnityToolResult:
        """Parses Unity build/compilation log text or file."""
        log_text = params.get("log_text")
        log_file = params.get("log_file") or params.get("log_path")
        if not log_text and log_file:
            p = self.safety.validate_path(Path(log_file))
            if p.exists():
                log_text = p.read_text(encoding="utf-8", errors="replace")
        if log_text is None:
            raise UnitySafetyError(UnityErrorCode.FILE_NOT_AUTHORIZED, "log_text or valid log_file is required.")
        parsed = UnityLogParser.parse_log(log_text)
        return UnityToolResult(
            tool="unity.parse_build_log",
            success=True,
            data={
                "errors": [e.to_dict() for e in parsed["errors"]],
                "warnings": [w.to_dict() for w in parsed["warnings"]],
                "error_count": parsed["error_count"],
                "warning_count": parsed["warning_count"],
                "summary": parsed["summary"],
            },
            message=parsed["summary"],
            verified=True,
        )

    def _tool_get_build_configuration(self, params: Dict[str, Any]) -> UnityToolResult:
        """Inspects build configuration including scenes in build and allowed targets."""
        proj_p = params.get("project_path") or self.safety.authorized_project
        cfg = self.build.get_build_configuration(proj_p)
        return UnityToolResult(
            tool="unity.get_build_configuration",
            success=True,
            data=cfg,
            message=f"Retrieved build configuration for '{cfg['project_name']}'.",
            verified=True,
        )

    def _tool_clean_build_target(self, params: Dict[str, Any]) -> UnityToolResult:
        """Safely cleans stale build artifacts."""
        out_p = params.get("output_path") or params.get("path")
        if not out_p:
            raise UnitySafetyError(UnityErrorCode.INVALID_BUILD_OUTPUT, "output_path is required.")
        proj_p = Path(params.get("project_path")) if params.get("project_path") else None
        cleaned = self.build.clean_build_target(out_p, project_root=proj_p)
        return UnityToolResult(
            tool="unity.clean_build_target",
            success=cleaned,
            data={"cleaned": cleaned, "path": str(out_p)},
            message=f"Cleaned build target '{out_p}'.",
            verified=cleaned,
        )

    # -------------------------------------------------------------------------
    # Step 8 Phase 3: Test Runner & PlayMode Tool Handlers
    # -------------------------------------------------------------------------

    def _tool_validate_test_mode(self, params: Dict[str, Any]) -> UnityToolResult:
        """Validates that test mode is in ALLOWED_TEST_MODES (EditMode, PlayMode)."""
        mode = params.get("mode") or params.get("test_mode")
        if not mode:
            raise UnitySafetyError(UnityErrorCode.INVALID_TEST_MODE, "mode parameter is required.")
        val_mode = self.tests.validate_test_mode(str(mode))
        return UnityToolResult(
            tool="unity.validate_test_mode",
            success=True,
            data={"test_mode": val_mode, "allowed": True},
            message=f"Test mode '{val_mode}' is valid and approved.",
            verified=True,
        )

    def _tool_validate_test_filter(self, params: Dict[str, Any]) -> UnityToolResult:
        """Validates test filter strings against shell injection and traversal."""
        filt = params.get("filter") or params.get("test_filter")
        f_type = params.get("filter_type", "filter")
        val_filter = self.tests.validate_test_filter(filt, filter_type=f_type)
        return UnityToolResult(
            tool="unity.validate_test_filter",
            success=True,
            data={"filter": val_filter, "filter_type": f_type, "valid": True},
            message=f"Test {f_type} '{val_filter}' is valid and safe.",
            verified=True,
        )

    def _tool_run_editmode_tests(self, params: Dict[str, Any]) -> UnityToolResult:
        """Safely executes Unity EditMode tests in batchmode with bounded timeout."""
        proj_p = params.get("project_path")
        editor_p = params.get("editor_path")
        filt = params.get("test_filter") or params.get("filter")
        assemblies = params.get("assembly_names") or params.get("assemblies")
        categories = params.get("category_names") or params.get("categories")
        xml_p = params.get("result_xml_path") or params.get("output_path")
        timeout = float(params.get("timeout_seconds", DEFAULT_TEST_TIMEOUT))

        res = self.tests.run_editmode_tests(
            project_path=proj_p,
            editor_path=editor_p,
            test_filter=filt,
            assembly_names=assemblies,
            category_names=categories,
            result_xml_path=xml_p,
            timeout_seconds=timeout,
        )
        return UnityToolResult(
            tool="unity.run_editmode_tests",
            success=res.success,
            data=res.to_dict(),
            error=res.error_summary if not res.success else None,
            error_code=UnityErrorCode.TEST_FAILED.value if not res.success else None,
            message=f"EditMode tests {'passed' if res.success else 'failed'}: {res.error_summary or 'All tests passed.'}",
            verified=res.verified,
        )

    def _tool_run_playmode_tests(self, params: Dict[str, Any]) -> UnityToolResult:
        """Safely executes Unity PlayMode tests in batchmode with bounded timeout."""
        proj_p = params.get("project_path")
        editor_p = params.get("editor_path")
        filt = params.get("test_filter") or params.get("filter")
        assemblies = params.get("assembly_names") or params.get("assemblies")
        categories = params.get("category_names") or params.get("categories")
        xml_p = params.get("result_xml_path") or params.get("output_path")
        timeout = float(params.get("timeout_seconds", DEFAULT_TEST_TIMEOUT))

        res = self.tests.run_playmode_tests(
            project_path=proj_p,
            editor_path=editor_p,
            test_filter=filt,
            assembly_names=assemblies,
            category_names=categories,
            result_xml_path=xml_p,
            timeout_seconds=timeout,
        )
        return UnityToolResult(
            tool="unity.run_playmode_tests",
            success=res.success,
            data=res.to_dict(),
            error=res.error_summary if not res.success else None,
            error_code=UnityErrorCode.TEST_FAILED.value if not res.success else None,
            message=f"PlayMode tests {'passed' if res.success else 'failed'}: {res.error_summary or 'All tests passed.'}",
            verified=res.verified,
        )

    def _tool_parse_test_results(self, params: Dict[str, Any]) -> UnityToolResult:
        """Parses NUnit3 / Unity XML test results into structured cases and failures."""
        xml_content = params.get("xml_content")
        xml_path = params.get("xml_path") or params.get("path")
        mode = params.get("test_mode", "EditMode")
        data = self.tests.parse_test_results(xml_content=xml_content, xml_path=xml_path, test_mode=mode)
        return UnityToolResult(
            tool="unity.parse_test_results",
            success=True,
            data=data,
            message=f"Parsed {data['cases_count']} test case(s) with {data['failures_count']} failure(s).",
            verified=True,
        )

    def _tool_verify_test_artifact(self, params: Dict[str, Any]) -> UnityToolResult:
        """Verifies test results XML artifact existence, size, valid XML, and SHA-256."""
        out_p = params.get("output_path") or params.get("path")
        if not out_p:
            raise UnitySafetyError(UnityErrorCode.INVALID_TEST_OUTPUT, "output_path is required.")
        data = self.tests.verify_test_artifact(out_p)
        return UnityToolResult(
            tool="unity.verify_test_artifact",
            success=data.get("verified", False),
            data=data,
            error=data.get("error"),
            error_code=UnityErrorCode.TEST_ARTIFACT_MISSING.value if not data.get("verified") else None,
            message=f"Test artifact '{out_p}' verified: {data.get('verified', False)}.",
            verified=data.get("verified", False),
        )

    def _tool_get_test_summary(self, params: Dict[str, Any]) -> UnityToolResult:
        """Extracts high-level summary and pass/fail metrics from test results."""
        xml_content = params.get("xml_content")
        xml_path = params.get("xml_path") or params.get("path")
        mode = params.get("test_mode", "EditMode")
        data = self.tests.get_test_summary(xml_content=xml_content, xml_path=xml_path, test_mode=mode)
        return UnityToolResult(
            tool="unity.get_test_summary",
            success=True,
            data=data,
            message=f"Test summary: {data['passed']}/{data['total']} passed ({data['pass_rate']:.1f}%), status: {data['status']}.",
            verified=True,
        )

    def _tool_diagnose_test_failure(self, params: Dict[str, Any]) -> UnityToolResult:
        """Diagnoses failure cause and classifies into deterministic category."""
        msg = params.get("message", "")
        st = params.get("stack_trace", "")
        log = params.get("log_text", "")
        code = int(params.get("exit_code", 0))
        mode = params.get("test_mode", "EditMode")
        data = self.tests.diagnose_test_failure(
            message=msg,
            stack_trace=st,
            log_text=log,
            exit_code=code,
            test_mode=mode,
        )
        return UnityToolResult(
            tool="unity.diagnose_test_failure",
            success=True,
            data=data,
            message=data["diagnostics"],
            verified=True,
        )

    # -------------------------------------------------------------------------
    # Step 8 Phase 4: Script Analysis & AST Modification Tool Handlers
    # -------------------------------------------------------------------------

    def _tool_parse_script_ast(self, params: Dict[str, Any]) -> UnityToolResult:
        """Parses C# script into structured AST syntax tree."""
        target_file = params.get("script_path") or params.get("path") or params.get("target_file")
        if not target_file:
            raise UnitySafetyError(UnityErrorCode.FILE_NOT_AUTHORIZED, "script_path or target_file is required.")
        tree = self.ast.parse_script(target_file)
        tree_dict = tree.to_dict()
        types_count = len(tree.get_all_types())
        return UnityToolResult(
            tool="unity.parse_script_ast",
            success=tree.is_valid,
            data=tree_dict,
            message=f"Parsed C# AST for '{Path(target_file).name}': {types_count} type(s), valid={tree.is_valid}.",
            verified=tree.is_valid,
        )

    def _tool_analyze_script(self, params: Dict[str, Any]) -> UnityToolResult:
        """Analyzes C# script for syntax defects, compilation errors, and code smells."""
        target_file = params.get("script_path") or params.get("path") or params.get("target_file")
        if not target_file:
            raise UnitySafetyError(UnityErrorCode.FILE_NOT_AUTHORIZED, "script_path or target_file is required.")
        analysis = self.ast.analyze_script(target_file)
        return UnityToolResult(
            tool="unity.analyze_script",
            success=True,
            data=analysis,
            message=f"Analyzed '{Path(target_file).name}': {analysis['error_count']} error(s), {analysis['warning_count']} warning(s).",
            verified=True,
        )

    def _tool_propose_ast_modification(self, params: Dict[str, Any]) -> UnityToolResult:
        """Creates and validates a structured AST modification proposal."""
        target_file = params.get("target_file") or params.get("script_path") or params.get("path")
        exp_hash = params.get("expected_sha256") or params.get("target_sha256") or ""
        mod_type = params.get("modification_type") or params.get("type")
        target_type = params.get("target_type")
        target_member = params.get("target_member")
        new_content = params.get("new_node_content") or params.get("content") or params.get("replacement")
        p_params = params.get("parameters") or {}
        rationale = params.get("rationale") or params.get("reason") or ""
        diagnostics = params.get("diagnostics_addressed") or []
        attempts = int(params.get("attempt_count", 1))

        if not target_file:
            raise UnitySafetyError(UnityErrorCode.FILE_NOT_AUTHORIZED, "target_file is required.")
        if not exp_hash:
            raise UnitySafetyError(UnityErrorCode.MALFORMED_PROPOSAL, "expected_sha256 is required.")
        if not mod_type:
            raise UnitySafetyError(UnityErrorCode.MALFORMED_PROPOSAL, "modification_type is required.")

        proposal = self.ast.propose_modification(
            target_file=target_file,
            expected_sha256=exp_hash,
            modification_type=mod_type,
            target_type=target_type,
            target_member=target_member,
            new_node_content=new_content,
            parameters=p_params,
            rationale=rationale,
            diagnostics_addressed=diagnostics,
            attempt_count=attempts,
        )
        return UnityToolResult(
            tool="unity.propose_ast_modification",
            success=True,
            data=proposal.to_dict(),
            message=f"Created AST proposal '{proposal.proposal_id}' ({proposal.modification_type.value}) for '{Path(target_file).name}'.",
            verified=True,
        )

    def _tool_apply_ast_modification(self, params: Dict[str, Any]) -> UnityToolResult:
        """Applies a validated AST modification proposal with atomic backup and rollback."""
        if "proposal" in params and isinstance(params["proposal"], dict):
            p_dict = params["proposal"]
            proposal = ASTModificationProposal(
                proposal_id=p_dict.get("proposal_id", f"prop_{uuid.uuid4().hex[:8]}"),
                target_file=p_dict["target_file"],
                expected_sha256=p_dict["expected_sha256"],
                modification_type=ASTModificationType(p_dict["modification_type"]),
                target_type=p_dict.get("target_type"),
                target_member=p_dict.get("target_member"),
                new_node_content=p_dict.get("new_node_content"),
                parameters=p_dict.get("parameters", {}),
                rationale=p_dict.get("rationale", ""),
                diagnostics_addressed=p_dict.get("diagnostics_addressed", []),
                attempt_count=int(p_dict.get("attempt_count", 1)),
            )
        else:
            target_file = params.get("target_file") or params.get("script_path") or params.get("path")
            exp_hash = params.get("expected_sha256") or params.get("target_sha256") or ""
            mod_type = params.get("modification_type") or params.get("type")
            target_type = params.get("target_type")
            target_member = params.get("target_member")
            new_content = params.get("new_node_content") or params.get("content") or params.get("replacement")
            p_params = params.get("parameters") or {}
            rationale = params.get("rationale") or params.get("reason") or ""
            diagnostics = params.get("diagnostics_addressed") or []
            attempts = int(params.get("attempt_count", 1))

            proposal = self.ast.propose_modification(
                target_file=target_file,
                expected_sha256=exp_hash,
                modification_type=mod_type,
                target_type=target_type,
                target_member=target_member,
                new_node_content=new_content,
                parameters=p_params,
                rationale=rationale,
                diagnostics_addressed=diagnostics,
                attempt_count=attempts,
            )

        validate_compile = params.get("validate_compile", True)
        res = self.ast.apply_modification(proposal, validate_compile=validate_compile)
        return UnityToolResult(
            tool="unity.apply_ast_modification",
            success=res.success,
            data=res.to_dict(),
            error=res.error,
            error_code=res.error_code,
            message=(
                f"Successfully applied AST modification to '{Path(res.target_file).name}'."
                if res.success
                else f"AST modification failed and was rolled back: {res.error}"
            ),
            verified=res.success and res.ast_valid,
        )

    def _tool_verify_ast_integrity(self, params: Dict[str, Any]) -> UnityToolResult:
        """Verifies post-transformation AST syntax tree validity and structural integrity."""
        target_file = params.get("script_path") or params.get("path") or params.get("target_file")
        if not target_file:
            raise UnitySafetyError(UnityErrorCode.FILE_NOT_AUTHORIZED, "target_file is required.")
        exp_hash = params.get("expected_sha256")
        if exp_hash:
            self.safety.validate_target_sha256(target_file, exp_hash)

        tree = self.ast.parse_script(target_file)
        return UnityToolResult(
            tool="unity.verify_ast_integrity",
            success=tree.is_valid,
            data={
                "target_file": str(target_file),
                "sha256": tree.sha256,
                "is_valid": tree.is_valid,
                "diagnostics": [d.to_dict() for d in tree.diagnostics],
            },
            error="AST contains syntax errors" if not tree.is_valid else None,
            error_code=UnityErrorCode.AST_INTEGRITY_FAILED.value if not tree.is_valid else None,
            message=f"AST integrity for '{Path(target_file).name}': valid={tree.is_valid}.",
            verified=tree.is_valid,
        )

    def _tool_rollback_script_modification(self, params: Dict[str, Any]) -> UnityToolResult:
        """Restores a target file from its backup checkpoint and verifies post-rollback hash."""
        target_file = params.get("target_file") or params.get("script_path")
        checkpoint_path = params.get("checkpoint_path") or params.get("backup_path")
        expected_sha = params.get("expected_original_sha256") or params.get("original_sha256")

        if not target_file or not checkpoint_path or not expected_sha:
            raise UnitySafetyError(
                UnityErrorCode.MALFORMED_PROPOSAL,
                "target_file, checkpoint_path, and expected_original_sha256 are required for rollback.",
            )

        ok = self.ast.rollback_modification(
            target_file=target_file,
            checkpoint_path=checkpoint_path,
            expected_original_sha256=expected_sha,
        )
        return UnityToolResult(
            tool="unity.rollback_script_modification",
            success=ok,
            data={"target_file": str(target_file), "restored_sha256": expected_sha},
            message=f"Successfully rolled back '{Path(target_file).name}' to checkpoint.",
            verified=ok,
        )

    def _tool_get_script_symbols(self, params: Dict[str, Any]) -> UnityToolResult:
        """Discovers C# symbols across an entire Unity project or a single script."""
        script_path = params.get("script_path") or params.get("path")
        project_path = params.get("project_path")

        if script_path:
            tree = self.ast.parse_script(script_path)
            symbols = tree.get_all_symbols()
            return UnityToolResult(
                tool="unity.get_script_symbols",
                success=True,
                data={"script_path": str(script_path), "symbols": symbols, "count": len(symbols)},
                message=f"Found {len(symbols)} symbol(s) in '{Path(script_path).name}'.",
                verified=True,
            )
        elif project_path:
            data = self.ast.get_project_symbols(project_path)
            return UnityToolResult(
                tool="unity.get_script_symbols",
                success=True,
                data=data,
                message=f"Found symbols across {data['script_count']} script(s) in '{Path(project_path).name}'.",
                verified=True,
            )
        else:
            data = self.ast.get_project_symbols(self.safety.authorized_project)
            return UnityToolResult(
                tool="unity.get_script_symbols",
                success=True,
                data=data,
                message=f"Found symbols across {data['script_count']} script(s) in authorized project.",
                verified=True,
            )

    def _tool_validate_script_repair(self, params: Dict[str, Any]) -> UnityToolResult:
        """Validates whether an applied repair resolved target diagnostics."""
        target_file = params.get("target_file") or params.get("script_path")
        if not target_file:
            raise UnitySafetyError(UnityErrorCode.FILE_NOT_AUTHORIZED, "target_file is required.")
        diagnostics = params.get("diagnostics_to_check") or params.get("diagnostics") or []
        res = self.ast.validate_script_repair(target_file, diagnostics)
        return UnityToolResult(
            tool="unity.validate_script_repair",
            success=res["is_valid"],
            data=res,
            message=f"Script repair validation for '{Path(target_file).name}': valid={res['is_valid']}, resolved={len(res['diagnostics_resolved'])}.",
            verified=res["is_valid"],
        )


DEFAULT_UNITY_TOOL_REGISTRY = UnityToolRegistry()
