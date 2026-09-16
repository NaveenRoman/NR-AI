r"""
NR-AI Unreal Engine Agent Tool Registry & Dispatcher (Step 9 Phase 1).

Central registry and dispatcher for all approved Unreal Engine developer tools.
Enforces strict allowlists, parameter validation, rate limiting, and audit logging.
"""

from dataclasses import dataclass, field
import hashlib
import json
import os
import logging
from pathlib import Path
import time
from typing import Any, Callable, Dict, List, Optional, Set
import uuid

from app.memory.audit_logger import AuditLogger
from app.agent.unreal_safety import (
    ALLOWED_UNREAL_TOOLS,
    ALL_ALLOWED_UNREAL_TOOLS,
    ALLOWED_UNREAL_CONFIGURATIONS,
    ALLOWED_UNREAL_TARGET_TYPES,
    ALLOWED_UNREAL_PLATFORMS,
    ALLOWED_UNREAL_TEST_MODES,
    ALLOWED_UNREAL_TEST_PLATFORMS,
    ALLOWED_UNREAL_TEST_CONFIGURATIONS,
    TEST_TIMEOUT_SECONDS,
    MAX_TEST_LOG_BYTES,
    UnrealErrorCode,
    UnrealSafetyError,
    UnrealSafetyGate,
    DEFAULT_UNREAL_SAFETY_GATE,
    DEFAULT_AUTHORIZED_PROJECT,
    MAX_REPAIR_ATTEMPTS,
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
from app.agent.unreal_build import (
    UnrealBuildEnvironmentValidator,
    UnrealBuildArtifactVerifier,
    UnrealBuildRunner,
    UnrealBuildResultVerifier,
    UnrealBuildLogParser,
    UnrealBuildResult,
    UnrealBuildTargetInfo,
    UnrealBuildDiagnostic,
    UnrealBuildIssueCategory,
    UnrealBuildEnvironmentStatus,
    UnrealBuildResultStatus,
    DEFAULT_UNREAL_BUILD_VALIDATOR,
    DEFAULT_UNREAL_ARTIFACT_VERIFIER,
    DEFAULT_UNREAL_BUILD_RUNNER,
)
from app.agent.unreal_source import (
    UnrealCppAnalyzer,
    UnrealBlueprintInspector,
    UnrealSourceModifier,
    UnrealModificationProposal,
    UnrealModificationBackup,
    UnrealModificationResult,
    UnrealCppFileAnalysis,
    UnrealClassSymbol,
    UnrealMethodSymbol,
    DEFAULT_UNREAL_CPP_ANALYZER,
    DEFAULT_UNREAL_BP_INSPECTOR,
    DEFAULT_UNREAL_SOURCE_MODIFIER,
)
from app.agent.unreal_tests import (
    UnrealTestEnvironmentValidator,
    UnrealRuntimeLogParser,
    UnrealTestReportParser,
    UnrealTestRunner,
    UnrealTestArtifactVerifier,
    UnrealRuntimeResultVerifier,
    UnrealTestExecutionResult,
    UnrealRuntimeDiagnostic,
    UnrealTestSummary,
    UnrealTestCaseResult,
    UnrealRuntimeIssueCategory,
    UnrealRuntimeResultStatus,
    DEFAULT_UNREAL_TEST_VALIDATOR,
    DEFAULT_UNREAL_RUNTIME_PARSER,
    DEFAULT_UNREAL_TEST_REPORT_PARSER,
    DEFAULT_UNREAL_TEST_RUNNER,
    DEFAULT_UNREAL_TEST_ARTIFACT_VERIFIER,
    DEFAULT_UNREAL_RUNTIME_RESULT_VERIFIER,
)
from app.agent.unreal_agent import (
    UnrealAutonomousAgent,
    DEFAULT_UNREAL_AGENT,
    UnrealWorkflowState,
    UnrealFailureDomain,
    UnrealWorkflowType,
    UnrealWorkflowPlan,
    UnrealWorkflowReport,
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
        build_runner: Optional[UnrealBuildRunner] = None,
        build_validator: Optional[UnrealBuildEnvironmentValidator] = None,
        artifact_verifier: Optional[UnrealBuildArtifactVerifier] = None,
        include_build_tools: Optional[bool] = None,
        test_runner: Optional[UnrealTestRunner] = None,
        test_validator: Optional[UnrealTestEnvironmentValidator] = None,
        test_artifact_verifier: Optional[UnrealTestArtifactVerifier] = None,
        runtime_parser: Optional[UnrealRuntimeLogParser] = None,
        report_parser: Optional[UnrealTestReportParser] = None,
        runtime_verifier: Optional[UnrealRuntimeResultVerifier] = None,
        include_test_tools: Optional[bool] = None,
        cpp_analyzer: Optional[UnrealCppAnalyzer] = None,
        bp_inspector: Optional[UnrealBlueprintInspector] = None,
        source_modifier: Optional[UnrealSourceModifier] = None,
        include_source_tools: Optional[bool] = None,
        agent: Optional[UnrealAutonomousAgent] = None,
        include_workflow_tools: Optional[bool] = None,
    ):
        self.safety = safety_gate or DEFAULT_UNREAL_SAFETY_GATE
        self.env = env_detector or DEFAULT_UNREAL_ENV_DETECTOR
        self.inspector = inspector or DEFAULT_UNREAL_PROJECT_INSPECTOR
        self.audit = audit_logger or AuditLogger()
        self.workspace_root = workspace_root or self.safety.workspace_root
        self.build_validator = build_validator or DEFAULT_UNREAL_BUILD_VALIDATOR
        self.artifact_verifier = artifact_verifier or DEFAULT_UNREAL_ARTIFACT_VERIFIER
        self.runner = build_runner or DEFAULT_UNREAL_BUILD_RUNNER

        self.test_validator = test_validator or DEFAULT_UNREAL_TEST_VALIDATOR
        self.test_runner = test_runner or DEFAULT_UNREAL_TEST_RUNNER
        self.test_artifact_verifier = test_artifact_verifier or DEFAULT_UNREAL_TEST_ARTIFACT_VERIFIER
        self.runtime_parser = runtime_parser or DEFAULT_UNREAL_RUNTIME_PARSER
        self.report_parser = report_parser or DEFAULT_UNREAL_TEST_REPORT_PARSER
        self.runtime_verifier = runtime_verifier or DEFAULT_UNREAL_RUNTIME_RESULT_VERIFIER

        if include_build_tools is None:
            include_build_tools = (
                (build_runner is not None or build_validator is not None or artifact_verifier is not None)
                or (inspector is None and env_detector is None)
            )

        self.cpp_analyzer = cpp_analyzer or DEFAULT_UNREAL_CPP_ANALYZER
        self.bp_inspector = bp_inspector or DEFAULT_UNREAL_BP_INSPECTOR
        self.source_modifier = source_modifier or DEFAULT_UNREAL_SOURCE_MODIFIER

        if include_test_tools is None:
            include_test_tools = (
                (test_runner is not None or test_validator is not None or test_artifact_verifier is not None)
                or (inspector is None and env_detector is None)
            )

        if include_source_tools is None:
            include_source_tools = (
                (cpp_analyzer is not None or bp_inspector is not None or source_modifier is not None)
                or (inspector is None and env_detector is None)
            )

        self.agent = agent or DEFAULT_UNREAL_AGENT
        if include_workflow_tools is None:
            include_workflow_tools = (
                agent is not None
                or (
                    inspector is None
                    and env_detector is None
                    and cpp_analyzer is None
                    and bp_inspector is None
                    and source_modifier is None
                    and build_runner is None
                    and test_runner is None
                    and include_source_tools is not False
                    and include_test_tools is not False
                    and include_build_tools is not False
                )
            )

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

        if include_build_tools:
            # Step 9 Phase 2: Compilation & Build Foundation
            self._handlers.update({
                "unreal.validate_build_environment": self._tool_validate_build_environment,
                "unreal.validate_build_target": self._tool_validate_build_target,
                "unreal.build_project": self._tool_build_project,
                "unreal.parse_build_diagnostics": self._tool_parse_build_diagnostics,
                "unreal.verify_build_result": self._tool_verify_build_result,
                "unreal.inspect_build_artifacts": self._tool_inspect_build_artifacts,
                "unreal.get_supported_targets": self._tool_get_supported_targets,
                "unreal.diagnose_build_failure": self._tool_diagnose_build_failure,
            })

        if include_test_tools:
            # Step 9 Phase 3: Test & Runtime Intelligence Foundation
            self._handlers.update({
                "unreal.validate_test_environment": self._tool_validate_test_environment,
                "unreal.validate_test_mode": self._tool_validate_test_mode,
                "unreal.run_test": self._tool_run_test,
                "unreal.capture_runtime_logs": self._tool_capture_runtime_logs,
                "unreal.parse_runtime_logs": self._tool_parse_runtime_logs,
                "unreal.detect_runtime_crashes": self._tool_detect_runtime_crashes,
                "unreal.parse_test_results": self._tool_parse_test_results,
                "unreal.verify_runtime_state": self._tool_verify_runtime_state,
                "unreal.inspect_test_artifacts": self._tool_inspect_test_artifacts,
                "unreal.diagnose_runtime_failure": self._tool_diagnose_runtime_failure,
            })

        if include_source_tools:
            # Step 9 Phase 4: C++ / Blueprint Intelligence & Safe Modification Foundation
            self._handlers.update({
                "unreal.inspect_cpp_source": self._tool_inspect_cpp_source,
                "unreal.list_cpp_symbols": self._tool_list_cpp_symbols,
                "unreal.inspect_cpp_class": self._tool_inspect_cpp_class,
                "unreal.inspect_cpp_method": self._tool_inspect_cpp_method,
                "unreal.find_cpp_symbol": self._tool_find_cpp_symbol,
                "unreal.inspect_reflection_metadata": self._tool_inspect_reflection_metadata,
                "unreal.inspect_inheritance": self._tool_inspect_inheritance,
                "unreal.list_blueprint_assets": self._tool_list_blueprint_assets,
                "unreal.inspect_blueprint_metadata": self._tool_inspect_blueprint_metadata,
                "unreal.validate_cpp_change": self._tool_validate_cpp_change,
                "unreal.propose_cpp_change": self._tool_propose_cpp_change,
                "unreal.apply_cpp_change": self._tool_apply_cpp_change,
                "unreal.rollback_cpp_change": self._tool_rollback_cpp_change,
                "unreal.verify_source_change": self._tool_verify_source_change,
            })

        if include_workflow_tools:
            # Step 9 Phase 5: Autonomous Repair & End-to-End Workflows
            self._handlers.update({
                "unreal.create_repair_workflow": self._tool_create_repair_workflow,
                "unreal.inspect_failure": self._tool_inspect_failure,
                "unreal.diagnose_failure": self._tool_diagnose_failure,
                "unreal.plan_repair": self._tool_plan_repair,
                "unreal.validate_repair_plan": self._tool_validate_repair_plan,
                "unreal.execute_repair_workflow": self._tool_execute_repair_workflow,
                "unreal.verify_repair_workflow": self._tool_verify_repair_workflow,
                "unreal.rollback_repair_workflow": self._tool_rollback_repair_workflow,
                "unreal.stop_repair_workflow": self._tool_stop_repair_workflow,
                "unreal.get_workflow_status": self._tool_get_workflow_status,
            })

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

    # -------------------------------------------------------------------------
    # Tool Handlers (Phase 2: Compilation & Build Foundation)
    # -------------------------------------------------------------------------

    def _tool_validate_build_environment(self, project_path: str = "", **kwargs) -> UnrealToolResult:
        """unreal.validate_build_environment: Validates host engine, UBT, .NET runtime, and project prerequisites."""
        target = project_path or str(self.safety.authorized_projects[0])
        res = self.build_validator.validate_build_environment(target)
        return UnrealToolResult(
            tool="unreal.validate_build_environment",
            success=True,
            data=res,
            verified=True,
            message=f"Build environment status: {res['status']}. {res['summary']}",
        )

    def _tool_validate_build_target(
        self,
        project_path: str = "",
        target_name: str = "",
        target_type: str = "Editor",
        configuration: str = "Development",
        platform: str = "Win64",
        **kwargs,
    ) -> UnrealToolResult:
        """unreal.validate_build_target: Validates build target name, configuration, and platform allowlists."""
        target = project_path or str(self.safety.authorized_projects[0])
        t_name, t_type, config, plat = self.safety.validate_build_target(
            target, target_name, target_type, configuration, platform
        )
        target_info = UnrealBuildTargetInfo(target_name=t_name, target_type=t_type, configuration=config, platform=plat)
        return UnrealToolResult(
            tool="unreal.validate_build_target",
            success=True,
            data=target_info.to_dict(),
            verified=True,
            message=f"Target '{t_name}' ({t_type}, {config}, {plat}) is valid and approved.",
        )

    def _tool_build_project(
        self,
        project_path: str = "",
        target_name: Optional[str] = None,
        target_type: str = "Editor",
        configuration: str = "Development",
        platform: str = "Win64",
        timeout_seconds: float = 300.0,
        **kwargs,
    ) -> UnrealToolResult:
        """unreal.build_project: Safe, bounded execution of Unreal project compilation via UnrealBuildTool."""
        target = project_path or str(self.safety.authorized_projects[0])
        build_res = self.runner.build_project(
            project_path=target,
            target_name=target_name,
            target_type=target_type,
            configuration=configuration,
            platform=platform,
            timeout_seconds=timeout_seconds,
        )
        return UnrealToolResult(
            tool="unreal.build_project",
            success=build_res.success,
            data=build_res.to_dict(),
            error=build_res.error_summary if not build_res.success else None,
            error_code=build_res.status.value if not build_res.success else None,
            verified=build_res.verified,
            message=f"Build finished with status {build_res.status.value} (exit_code={build_res.exit_code}, errors={build_res.error_count}, warnings={build_res.warning_count}).",
        )

    def _tool_parse_build_diagnostics(self, log_content: str = "", **kwargs) -> UnrealToolResult:
        """unreal.parse_build_diagnostics: Parses raw build log into structured diagnostics across 17 categories."""
        redacted = redact_sensitive_data(log_content)
        parsed = UnrealBuildLogParser.parse_log(redacted)
        return UnrealToolResult(
            tool="unreal.parse_build_diagnostics",
            success=True,
            data={
                "errors": [e.to_dict() for e in parsed["errors"]],
                "warnings": [w.to_dict() for w in parsed["warnings"]],
                "error_count": parsed["error_count"],
                "warning_count": parsed["warning_count"],
                "summary": parsed["summary"],
            },
            verified=True,
            message=f"Parsed {parsed['error_count']} error(s) and {parsed['warning_count']} warning(s).",
        )

    def _tool_verify_build_result(
        self,
        build_result: Optional[Dict[str, Any]] = None,
        model_claim_success: Optional[bool] = None,
        **kwargs,
    ) -> UnrealToolResult:
        """unreal.verify_build_result: Applies strict evidence precedence to verify build outcomes against model claims."""
        if not build_result:
            return UnrealToolResult(
                tool="unreal.verify_build_result",
                success=False,
                error="build_result dictionary is required.",
                error_code=UnrealErrorCode.INVALID_PARAMETER.value,
            )

        status_str = build_result.get("status", UnrealBuildResultStatus.BUILD_FAILED.value)
        try:
            status_enum = UnrealBuildResultStatus(status_str)
        except ValueError:
            status_enum = UnrealBuildResultStatus.BUILD_FAILED

        errors_reconstructed = []
        for e in build_result.get("errors", []):
            cat_str = e.get("category", UnrealBuildIssueCategory.UNKNOWN_BUILD_FAILURE.value)
            try:
                cat_enum = UnrealBuildIssueCategory(cat_str)
            except ValueError:
                cat_enum = UnrealBuildIssueCategory.UNKNOWN_BUILD_FAILURE
            errors_reconstructed.append(
                UnrealBuildDiagnostic(
                    category=cat_enum,
                    severity=e.get("severity", "error"),
                    file=e.get("file", ""),
                    line=e.get("line", 1),
                    column=e.get("column", 1),
                    code=e.get("code", ""),
                    message=e.get("message", ""),
                    source_evidence=e.get("source_evidence", ""),
                    raw_line=e.get("raw_line", ""),
                    confidence=e.get("confidence", 1.0),
                )
            )

        artifacts_reconstructed = [
            UnrealBuildArtifactInfo(
                name=a.get("name", ""),
                path=a.get("path", ""),
                size_bytes=a.get("size_bytes", 0),
                sha256=a.get("sha256", ""),
                is_file=a.get("is_file", True),
                exists=a.get("exists", True),
            )
            for a in build_result.get("artifacts", [])
        ]

        res_obj = UnrealBuildResult(
            success=build_result.get("success", False),
            status=status_enum,
            exit_code=build_result.get("exit_code", -1),
            duration_seconds=build_result.get("duration_seconds", 0.0),
            target_name=build_result.get("target_name", ""),
            configuration=build_result.get("configuration", "Development"),
            platform=build_result.get("platform", "Win64"),
            errors=errors_reconstructed,
            warnings=[],
            artifacts=artifacts_reconstructed,
            verified=build_result.get("verified", False),
        )

        verification = UnrealBuildResultVerifier.verify(res_obj, model_claim_success=model_claim_success)
        return UnrealToolResult(
            tool="unreal.verify_build_result",
            success=True,
            data=verification,
            verified=True,
            message=f"Verified build status: {verification['status']} (evidence precedence enforced).",
        )

    def _tool_inspect_build_artifacts(
        self,
        project_path: str = "",
        target_name: str = "",
        configuration: str = "Development",
        platform: str = "Win64",
        **kwargs,
    ) -> UnrealToolResult:
        """unreal.inspect_build_artifacts: Bounded discovery and verification of artifacts in Binaries/Win64 with SHA-256."""
        target = project_path or str(self.safety.authorized_projects[0])
        t_name = target_name or Path(target).name
        artifacts = self.artifact_verifier.verify_artifacts(target, t_name, configuration, platform)
        return UnrealToolResult(
            tool="unreal.inspect_build_artifacts",
            success=True,
            data={"artifacts": [a.to_dict() for a in artifacts], "count": len(artifacts)},
            verified=True,
            message=f"Found {len(artifacts)} build artifact(s) in Binaries/{platform}.",
        )

    def _tool_get_supported_targets(self, project_path: str = "", **kwargs) -> UnrealToolResult:
        """unreal.get_supported_targets: Lists project targets, allowed configurations, and platforms."""
        target = project_path or str(self.safety.authorized_projects[0])
        proj_dir = self.safety.validate_project_path(target)
        targets_found: List[Dict[str, Any]] = []
        source_dir = proj_dir / "Source"
        if source_dir.is_dir():
            for t_file in source_dir.glob("*.Target.cs"):
                name = t_file.name.replace(".Target.cs", "")
                t_type = "Editor" if "Editor" in name else "Game"
                targets_found.append({
                    "name": name,
                    "type": t_type,
                    "target_file": str(t_file),
                })
        return UnrealToolResult(
            tool="unreal.get_supported_targets",
            success=True,
            data={
                "targets": targets_found,
                "allowed_configurations": sorted(list(ALLOWED_UNREAL_CONFIGURATIONS)),
                "allowed_target_types": sorted(list(ALLOWED_UNREAL_TARGET_TYPES)),
                "allowed_platforms": sorted(list(ALLOWED_UNREAL_PLATFORMS)),
            },
            verified=True,
            message=f"Discovered {len(targets_found)} target(s) for project.",
        )

    def _tool_diagnose_build_failure(self, log_content: str = "", **kwargs) -> UnrealToolResult:
        """unreal.diagnose_build_failure: Provides diagnostic categorization and root-cause analysis for build errors."""
        redacted = redact_sensitive_data(log_content)
        parsed = UnrealBuildLogParser.parse_log(redacted)
        diagnoses = []
        for err in parsed["errors"]:
            rec = "Check compiler logs and file syntax."
            if err.category == UnrealBuildIssueCategory.DOTNET_RUNTIME_MISSING:
                rec = "Install the required host .NET desktop runtime (10.0.7) to allow UnrealBuildTool to execute."
            elif err.category == UnrealBuildIssueCategory.MISSING_HEADER:
                rec = f"Verify header file '{err.source_evidence}' exists and is added to PublicIncludePaths in .Build.cs."
            elif err.category == UnrealBuildIssueCategory.CPP_COMPILE_ERROR:
                rec = f"Resolve C++ compilation error {err.code} at line {err.line} in {err.file}."
            elif err.category == UnrealBuildIssueCategory.LINKER_ERROR:
                rec = f"Resolve linker symbol error {err.code}. Check module dependencies in .Build.cs."
            elif err.category == UnrealBuildIssueCategory.UHT_ERROR:
                rec = f"Fix UHT reflection macro usage in {err.file}. Ensure GENERATED_BODY() is present and macros are well-formed."
            elif err.category == UnrealBuildIssueCategory.BUILD_CS_ERROR:
                rec = f"Fix C# syntax/reference error in build rules file {err.file}."
            elif err.category == UnrealBuildIssueCategory.TARGET_CS_ERROR:
                rec = f"Fix C# syntax/reference error in target rules file {err.file}."
            diagnoses.append({
                "category": err.category.value,
                "file": err.file,
                "line": err.line,
                "code": err.code,
                "message": err.message,
                "recommended_action": rec,
            })
        return UnrealToolResult(
            tool="unreal.diagnose_build_failure",
            success=True,
            data={
                "diagnoses": diagnoses,
                "count": len(diagnoses),
                "primary_category": diagnoses[0]["category"] if diagnoses else "NO_ERRORS",
                "summary": parsed["summary"],
            },
            verified=True,
            message=f"Diagnosed {len(diagnoses)} build issue(s).",
        )

    # -------------------------------------------------------------------------
    # Step 9 Phase 3: Test & Runtime Intelligence Tool Handlers
    # -------------------------------------------------------------------------

    def _tool_validate_test_environment(self, project_path: str = "", **kwargs) -> UnrealToolResult:
        """unreal.validate_test_environment: Deterministically validates pre-flight test environment prerequisites."""
        target = project_path or str(self.safety.authorized_projects[0])
        proj_dir = self.safety.validate_project_path(target)
        val_result = self.test_validator.validate(proj_dir)
        return UnrealToolResult(
            tool="unreal.validate_test_environment",
            success=val_result["status"] in ("READY", "PARTIALLY_READY"),
            data=val_result,
            verified=True,
            message=val_result["message"],
        )

    def _tool_validate_test_mode(self, test_mode: str = "SmokeTest", **kwargs) -> UnrealToolResult:
        """unreal.validate_test_mode: Validates whether a test mode is in the allowed test modes set."""
        try:
            valid_mode = self.safety.validate_test_mode(test_mode)
            return UnrealToolResult(
                tool="unreal.validate_test_mode",
                success=True,
                data={
                    "test_mode": valid_mode,
                    "allowed_modes": sorted(list(ALLOWED_UNREAL_TEST_MODES)),
                },
                verified=True,
                message=f"Test mode '{valid_mode}' is valid and approved.",
            )
        except UnrealSafetyError as se:
            return UnrealToolResult(
                tool="unreal.validate_test_mode",
                success=False,
                error=se.message,
                error_code=se.code.value,
                data={"allowed_modes": sorted(list(ALLOWED_UNREAL_TEST_MODES))},
                message=f"Invalid test mode: {se.message}",
            )

    def _tool_run_test(
        self,
        test_mode: str = "SmokeTest",
        test_filter: str = "",
        project_path: str = "",
        output_path: str = "",
        configuration: str = "Development",
        platform: str = "Win64",
        timeout_seconds: float = 180.0,
        extra_flags: Optional[List[str]] = None,
        **kwargs,
    ) -> UnrealToolResult:
        """unreal.run_test: Safely executes an Unreal test/runtime session using strictly shell=False."""
        target = project_path or str(self.safety.authorized_projects[0])
        proj_dir = self.safety.validate_project_path(target)
        res = self.test_runner.run_test(
            project_path=proj_dir,
            test_mode=test_mode,
            test_filter=test_filter if test_filter else None,
            output_path=output_path if output_path else None,
            configuration=configuration,
            platform=platform,
            timeout_seconds=timeout_seconds,
            extra_flags=extra_flags,
        )
        return UnrealToolResult(
            tool="unreal.run_test",
            success=res.success,
            data=res.to_dict(),
            verified=res.verified,
            error=res.error_message if not res.success else None,
            message=f"Test execution status: {res.status.value}. Exit code: {res.exit_code}.",
        )

    def _tool_capture_runtime_logs(
        self,
        project_path: str = "",
        max_lines: int = 1000,
        **kwargs,
    ) -> UnrealToolResult:
        """unreal.capture_runtime_logs: Bounded capture of the latest Unreal runtime logs from Saved/Logs."""
        target = project_path or str(self.safety.authorized_projects[0])
        proj_dir = self.safety.validate_project_path(target)
        logs_dir = proj_dir / "Saved" / "Logs"

        if not logs_dir.exists():
            return UnrealToolResult(
                tool="unreal.capture_runtime_logs",
                success=True,
                data={"logs": "", "lines_count": 0, "log_file": None},
                verified=True,
                message=f"No Saved/Logs directory found in '{proj_dir}'.",
            )

        log_files = sorted(logs_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not log_files:
            return UnrealToolResult(
                tool="unreal.capture_runtime_logs",
                success=True,
                data={"logs": "", "lines_count": 0, "log_file": None},
                verified=True,
                message="No log files found in Saved/Logs.",
            )

        latest_log = log_files[0]
        try:
            with open(latest_log, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            if len(lines) > max_lines:
                lines = lines[-max_lines:]
            content = "".join(lines)
            if len(content) > MAX_TEST_LOG_BYTES:
                content = content[:MAX_TEST_LOG_BYTES] + "\n... [LOG TRUNCATED BY SAFETY GATE]"
            sanitized = redact_sensitive_data(content)
            return UnrealToolResult(
                tool="unreal.capture_runtime_logs",
                success=True,
                data={
                    "logs": sanitized,
                    "lines_count": len(lines),
                    "log_file": str(latest_log),
                },
                verified=True,
                message=f"Captured {len(lines)} line(s) from latest log '{latest_log.name}'.",
            )
        except Exception as e:
            return UnrealToolResult(
                tool="unreal.capture_runtime_logs",
                success=False,
                error=str(e),
                message=f"Failed to read log file: {e}",
            )

    def _tool_parse_runtime_logs(self, log_content: str = "", **kwargs) -> UnrealToolResult:
        """unreal.parse_runtime_logs: Parses runtime log text across 22 categories into structured diagnostics."""
        redacted = redact_sensitive_data(log_content)
        parsed = self.runtime_parser.parse(redacted)
        return UnrealToolResult(
            tool="unreal.parse_runtime_logs",
            success=True,
            data=parsed,
            verified=True,
            message=parsed["summary"],
        )

    def _tool_detect_runtime_crashes(self, log_content: str = "", **kwargs) -> UnrealToolResult:
        """unreal.detect_runtime_crashes: Extracts crashes, ensure failures, and assertion diagnostics."""
        redacted = redact_sensitive_data(log_content)
        crashes = self.runtime_parser.detect_crashes(redacted)
        has_fatal = any(d.get("severity") in ("crash", "fatal") for d in crashes)
        return UnrealToolResult(
            tool="unreal.detect_runtime_crashes",
            success=True,
            data={
                "crashes": crashes,
                "count": len(crashes),
                "has_fatal_crashes": has_fatal,
            },
            verified=True,
            message=f"Detected {len(crashes)} crash/ensure/assertion event(s).",
        )

    def _tool_parse_test_results(
        self,
        stdout_content: str = "",
        report_file: str = "",
        **kwargs,
    ) -> UnrealToolResult:
        """unreal.parse_test_results: Parses test results from stdout or exported JSON reports."""
        if report_file:
            report_p = Path(report_file).resolve()
            self.safety.validate_test_output_path(report_p)
            cases, summary = self.report_parser.parse_json_report(report_p)
        else:
            cases, summary = self.report_parser.parse_stdout(stdout_content)

        return UnrealToolResult(
            tool="unreal.parse_test_results",
            success=True,
            data={
                "summary": summary.to_dict(),
                "cases": [c.to_dict() for c in cases],
                "count": len(cases),
            },
            verified=True,
            message=f"Parsed {len(cases)} test cases: {summary.passed} passed, {summary.failed} failed.",
        )

    def _tool_verify_runtime_state(
        self,
        status: str = "NOT_VERIFIED",
        exit_code: int = 0,
        tests_failed: int = 0,
        has_crashes: bool = False,
        model_claim_success: Optional[bool] = None,
        **kwargs,
    ) -> UnrealToolResult:
        """unreal.verify_runtime_state: Evaluates runtime state against evidence precedence hierarchy."""
        actual_success = (
            not has_crashes
            and exit_code == 0
            and tests_failed == 0
            and status in ("RUNTIME_SUCCEEDED", "TESTS_PASSED")
        )

        contradiction = False
        rejection_reason = ""
        if model_claim_success is not None:
            if model_claim_success and not actual_success:
                contradiction = True
                rejection_reason = f"Model claim of success contradicts evidence: status={status}, exit_code={exit_code}, failed_tests={tests_failed}"
            elif not model_claim_success and actual_success:
                contradiction = True
                rejection_reason = "Model claim of failure contradicts confirmed successful execution."

        return UnrealToolResult(
            tool="unreal.verify_runtime_state",
            success=not contradiction,
            data={
                "actual_success": actual_success,
                "status": status,
                "contradiction_detected": contradiction,
                "rejection_reason": rejection_reason,
            },
            verified=True,
            message="Evidence verification complete: " + ("Evidence consistent." if not contradiction else rejection_reason),
        )

    def _tool_inspect_test_artifacts(
        self,
        project_path: str = "",
        artifact_path: str = "",
        **kwargs,
    ) -> UnrealToolResult:
        """unreal.inspect_test_artifacts: Inspects and SHA-256 hashes generated test/log artifacts."""
        if artifact_path:
            p = Path(artifact_path).resolve()
            self.safety.validate_test_output_path(p)
            art = self.test_artifact_verifier.verify_artifact(p)
            artifacts = [art.to_dict()]
        else:
            target = project_path or str(self.safety.authorized_projects[0])
            proj_dir = self.safety.validate_project_path(target)
            scanned = self.test_artifact_verifier.scan_project_artifacts(proj_dir)
            artifacts = [a.to_dict() for a in scanned]

        return UnrealToolResult(
            tool="unreal.inspect_test_artifacts",
            success=True,
            data={"artifacts": artifacts, "count": len(artifacts)},
            verified=True,
            message=f"Verified {len(artifacts)} test/log artifact(s).",
        )

    def _tool_diagnose_runtime_failure(self, log_content: str = "", **kwargs) -> UnrealToolResult:
        """unreal.diagnose_runtime_failure: Formulates actionable root-cause recommendations for runtime errors."""
        redacted = redact_sensitive_data(log_content)
        parsed = self.runtime_parser.parse(redacted)
        diagnoses = []
        for diag in parsed["diagnostics"]:
            cat = diag["category"]
            rec = "Inspect runtime logs and check component initialization."
            if cat == UnrealRuntimeIssueCategory.CRASH.value:
                rec = "Address fatal crash or unhandled exception. Inspect callstack."
            elif cat == UnrealRuntimeIssueCategory.ACCESS_VIOLATION.value:
                rec = "Resolve null pointer dereference or invalid memory access (0xC0000005)."
            elif cat == UnrealRuntimeIssueCategory.ENSURE_FAILURE.value:
                rec = f"Investigate ensure condition failure: {diag['message']}."
            elif cat == UnrealRuntimeIssueCategory.ASSERTION_FAILURE.value:
                rec = f"Fix failing assertion check(): {diag['message']}."
            elif cat == UnrealRuntimeIssueCategory.MISSING_ASSET.value:
                rec = f"Verify asset file exists at referenced Content path: {diag['message']}."
            elif cat == UnrealRuntimeIssueCategory.BLUEPRINT_RUNTIME_ERROR.value:
                rec = f"Fix Blueprint runtime error: {diag['message']}. Check for Accessed None."
            elif cat == UnrealRuntimeIssueCategory.PLUGIN_LOAD_FAILURE.value:
                rec = f"Check plugin dependencies and descriptor syntax in Plugins/: {diag['message']}."
            elif cat == UnrealRuntimeIssueCategory.RENDERER_INIT_FAILURE.value:
                rec = "Use -nullrhi flag for headless execution or check GPU drivers."
            elif cat == UnrealRuntimeIssueCategory.TIMEOUT.value:
                rec = "Increase timeout threshold or investigate deadlocks."

            diagnoses.append({
                "category": cat,
                "severity": diag["severity"],
                "message": diag["message"],
                "recommended_action": rec,
            })

        return UnrealToolResult(
            tool="unreal.diagnose_runtime_failure",
            success=True,
            data={
                "diagnoses": diagnoses,
                "count": len(diagnoses),
                "summary": parsed["summary"],
            },
            verified=True,
            message=f"Diagnosed {len(diagnoses)} runtime issue(s).",
        )



    # =========================================================================
    # Step 9 Phase 4 Tool Handlers: C++ / Blueprint Intelligence & Safe Modification
    # =========================================================================

    def _tool_inspect_cpp_source(
        self,
        file_path: str = "",
        project_path: Optional[str] = None,
        **kwargs,
    ) -> UnrealToolResult:
        """unreal.inspect_cpp_source: Analyzes C++ header/source structure and extracts symbols."""
        if not file_path:
            return UnrealToolResult(
                tool="unreal.inspect_cpp_source",
                success=False,
                error="file_path parameter is required",
                error_code=UnrealErrorCode.INVALID_PARAMETER.value,
                message="file_path parameter is required.",
            )
        try:
            fp = self.safety.validate_source_file_path(file_path, project_path)
            analysis = self.cpp_analyzer.analyze_file(fp)
            return UnrealToolResult(
                tool="unreal.inspect_cpp_source",
                success=True,
                data=analysis.to_dict(),
                verified=True,
                message=f"Analyzed {fp.name}: {len(analysis.classes)} classes, {len(analysis.methods)} methods, {len(analysis.properties)} properties.",
            )
        except UnrealSafetyError as se:
            return UnrealToolResult(
                tool="unreal.inspect_cpp_source",
                success=False,
                error=se.message,
                error_code=se.code.value,
                message=f"Safety check rejected source inspection: {se.message}",
            )
        except Exception as e:
            return UnrealToolResult(
                tool="unreal.inspect_cpp_source",
                success=False,
                error=str(e),
                error_code=UnrealErrorCode.INVALID_PARAMETER.value,
                message=f"Failed to inspect C++ source: {e}",
            )

    def _tool_list_cpp_symbols(
        self,
        file_path: str = "",
        symbol_type: Optional[str] = None,
        project_path: Optional[str] = None,
        **kwargs,
    ) -> UnrealToolResult:
        """unreal.list_cpp_symbols: Lists symbols from a C++ source file with optional type filtering."""
        if not file_path:
            return UnrealToolResult(
                tool="unreal.list_cpp_symbols",
                success=False,
                error="file_path parameter is required",
                error_code=UnrealErrorCode.INVALID_PARAMETER.value,
                message="file_path parameter is required.",
            )
        try:
            fp = self.safety.validate_source_file_path(file_path, project_path)
            analysis = self.cpp_analyzer.analyze_file(fp)
            symbols = []
            st = (symbol_type or "").lower().strip()
            if not st or st in ("class", "classes"):
                for c in analysis.classes:
                    symbols.append({"type": "class", "name": c.name, "line": c.location.line, "declaration": c.declaration_signature})
            if not st or st in ("method", "methods", "function", "functions"):
                for m in analysis.methods:
                    symbols.append({"type": "method", "name": m.name, "class": m.class_name, "line": m.location.line, "signature": m.signature})
            if not st or st in ("property", "properties", "field", "fields"):
                for p in analysis.properties:
                    symbols.append({"type": "property", "name": p.name, "class": p.class_name, "line": p.location.line, "type_name": p.type_name})
            if not st or st in ("enum", "enums"):
                for e in analysis.enums:
                    symbols.append({"type": "enum", "name": e.name, "line": e.location.line, "entries": e.entries})
            if not st or st in ("macro", "macros", "reflection"):
                for r in analysis.reflection_macros:
                    symbols.append({"type": "reflection_macro", "macro_type": r.macro_type, "target": r.target_name, "line": r.location.line})

            return UnrealToolResult(
                tool="unreal.list_cpp_symbols",
                success=True,
                data={"file_path": str(fp), "symbols": symbols, "count": len(symbols), "filter": symbol_type},
                verified=True,
                message=f"Extracted {len(symbols)} symbol(s) from {fp.name}.",
            )
        except UnrealSafetyError as se:
            return UnrealToolResult(
                tool="unreal.list_cpp_symbols",
                success=False,
                error=se.message,
                error_code=se.code.value,
                message=f"Safety check rejected listing symbols: {se.message}",
            )
        except Exception as e:
            return UnrealToolResult(
                tool="unreal.list_cpp_symbols",
                success=False,
                error=str(e),
                error_code=UnrealErrorCode.INVALID_PARAMETER.value,
                message=f"Failed to list C++ symbols: {e}",
            )

    def _tool_inspect_cpp_class(
        self,
        file_path: str = "",
        class_name: str = "",
        project_path: Optional[str] = None,
        **kwargs,
    ) -> UnrealToolResult:
        """unreal.inspect_cpp_class: Inspects a specific C++ class and its methods and properties."""
        if not file_path or not class_name:
            return UnrealToolResult(
                tool="unreal.inspect_cpp_class",
                success=False,
                error="file_path and class_name parameters are required",
                error_code=UnrealErrorCode.INVALID_PARAMETER.value,
                message="file_path and class_name parameters are required.",
            )
        try:
            fp = self.safety.validate_source_file_path(file_path, project_path)
            analysis = self.cpp_analyzer.analyze_file(fp)
            target_cls = None
            for c in analysis.classes:
                if c.name == class_name:
                    target_cls = c
                    break
            if not target_cls:
                return UnrealToolResult(
                    tool="unreal.inspect_cpp_class",
                    success=False,
                    error=f"Class '{class_name}' not found in {fp.name}",
                    error_code=UnrealErrorCode.SYMBOL_NOT_FOUND.value,
                    message=f"Class '{class_name}' was not found in {fp.name}.",
                )
            c_dict = target_cls.to_dict()
            c_dict["methods"] = [m.to_dict() for m in analysis.methods if m.class_name == class_name]
            c_dict["properties"] = [p.to_dict() for p in analysis.properties if p.class_name == class_name]
            return UnrealToolResult(
                tool="unreal.inspect_cpp_class",
                success=True,
                data=c_dict,
                verified=True,
                message=f"Class {class_name} inspected ({len(c_dict['methods'])} methods, {len(c_dict['properties'])} properties).",
            )
        except UnrealSafetyError as se:
            return UnrealToolResult(
                tool="unreal.inspect_cpp_class",
                success=False,
                error=se.message,
                error_code=se.code.value,
                message=f"Safety check rejected class inspection: {se.message}",
            )
        except Exception as e:
            return UnrealToolResult(
                tool="unreal.inspect_cpp_class",
                success=False,
                error=str(e),
                error_code=UnrealErrorCode.INVALID_PARAMETER.value,
                message=f"Failed to inspect C++ class: {e}",
            )

    def _tool_inspect_cpp_method(
        self,
        file_path: str = "",
        class_name: str = "",
        method_name: str = "",
        project_path: Optional[str] = None,
        **kwargs,
    ) -> UnrealToolResult:
        """unreal.inspect_cpp_method: Inspects a specific method within a C++ class or file."""
        if not file_path or not method_name:
            return UnrealToolResult(
                tool="unreal.inspect_cpp_method",
                success=False,
                error="file_path and method_name parameters are required",
                error_code=UnrealErrorCode.INVALID_PARAMETER.value,
                message="file_path and method_name parameters are required.",
            )
        try:
            fp = self.safety.validate_source_file_path(file_path, project_path)
            analysis = self.cpp_analyzer.analyze_file(fp)
            target_m = None
            for m in analysis.methods:
                if (not class_name or m.class_name == class_name) and m.name == method_name:
                    target_m = m
                    break
            if not target_m:
                return UnrealToolResult(
                    tool="unreal.inspect_cpp_method",
                    success=False,
                    error=f"Method '{method_name}' not found in {class_name or fp.name}",
                    error_code=UnrealErrorCode.SYMBOL_NOT_FOUND.value,
                    message=f"Method '{method_name}' was not found.",
                )
            return UnrealToolResult(
                tool="unreal.inspect_cpp_method",
                success=True,
                data=target_m.to_dict(),
                verified=True,
                message=f"Method {target_m.class_name}::{target_m.name} inspected.",
            )
        except UnrealSafetyError as se:
            return UnrealToolResult(
                tool="unreal.inspect_cpp_method",
                success=False,
                error=se.message,
                error_code=se.code.value,
                message=f"Safety check rejected method inspection: {se.message}",
            )
        except Exception as e:
            return UnrealToolResult(
                tool="unreal.inspect_cpp_method",
                success=False,
                error=str(e),
                error_code=UnrealErrorCode.INVALID_PARAMETER.value,
                message=f"Failed to inspect C++ method: {e}",
            )

    def _tool_find_cpp_symbol(
        self,
        symbol_name: str = "",
        project_path: Optional[str] = None,
        search_headers: bool = True,
        search_sources: bool = True,
        **kwargs,
    ) -> UnrealToolResult:
        """unreal.find_cpp_symbol: Searches for a symbol name across all C++ files in project Source/."""
        if not symbol_name:
            return UnrealToolResult(
                tool="unreal.find_cpp_symbol",
                success=False,
                error="symbol_name parameter is required",
                error_code=UnrealErrorCode.INVALID_PARAMETER.value,
                message="symbol_name parameter is required.",
            )
        try:
            target_proj = project_path or str(self.safety.authorized_projects[0])
            proj_dir = self.safety.validate_project_path(target_proj)
            source_dir = proj_dir / "Source"
            matches = []
            if source_dir.is_dir():
                exts = []
                if search_headers:
                    exts.extend([".h", ".hpp"])
                if search_sources:
                    exts.extend([".cpp", ".inl"])
                for root, _, files in os.walk(source_dir):
                    for f in sorted(files):
                        p = Path(root) / f
                        if p.suffix.lower() in exts:
                            try:
                                an = self.cpp_analyzer.analyze_file(p)
                                rel_path = str(p.relative_to(proj_dir))
                                for c in an.classes:
                                    if symbol_name.lower() in c.name.lower():
                                        matches.append({"type": "class", "name": c.name, "file": rel_path, "line": c.location.line})
                                for m in an.methods:
                                    if symbol_name.lower() in m.name.lower():
                                        matches.append({"type": "method", "name": f"{m.class_name}::{m.name}", "file": rel_path, "line": m.location.line})
                                for pr in an.properties:
                                    if symbol_name.lower() in pr.name.lower():
                                        matches.append({"type": "property", "name": f"{pr.class_name}::{pr.name}", "file": rel_path, "line": pr.location.line})
                                for en in an.enums:
                                    if symbol_name.lower() in en.name.lower():
                                        matches.append({"type": "enum", "name": en.name, "file": rel_path, "line": en.location.line})
                            except Exception:
                                continue
            return UnrealToolResult(
                tool="unreal.find_cpp_symbol",
                success=True,
                data={"symbol_name": symbol_name, "matches": matches, "count": len(matches)},
                verified=True,
                message=f"Found {len(matches)} match(es) for '{symbol_name}'.",
            )
        except UnrealSafetyError as se:
            return UnrealToolResult(
                tool="unreal.find_cpp_symbol",
                success=False,
                error=se.message,
                error_code=se.code.value,
                message=f"Safety check rejected symbol search: {se.message}",
            )
        except Exception as e:
            return UnrealToolResult(
                tool="unreal.find_cpp_symbol",
                success=False,
                error=str(e),
                error_code=UnrealErrorCode.INVALID_PARAMETER.value,
                message=f"Failed to find C++ symbol: {e}",
            )

    def _tool_inspect_reflection_metadata(
        self,
        file_path: str = "",
        target_name: Optional[str] = None,
        project_path: Optional[str] = None,
        **kwargs,
    ) -> UnrealToolResult:
        """unreal.inspect_reflection_metadata: Extracts Unreal reflection macros (UCLASS, UFUNCTION, etc.)."""
        if not file_path:
            return UnrealToolResult(
                tool="unreal.inspect_reflection_metadata",
                success=False,
                error="file_path parameter is required",
                error_code=UnrealErrorCode.INVALID_PARAMETER.value,
                message="file_path parameter is required.",
            )
        try:
            fp = self.safety.validate_source_file_path(file_path, project_path)
            analysis = self.cpp_analyzer.analyze_file(fp)
            macros = [
                m.to_dict() for m in analysis.reflection_macros
                if not target_name or (m.target_name and m.target_name.lower() == target_name.lower())
            ]
            return UnrealToolResult(
                tool="unreal.inspect_reflection_metadata",
                success=True,
                data={"file_path": str(fp), "reflection_macros": macros, "count": len(macros), "target_filter": target_name},
                verified=True,
                message=f"Extracted {len(macros)} reflection macro(s) from {fp.name}.",
            )
        except UnrealSafetyError as se:
            return UnrealToolResult(
                tool="unreal.inspect_reflection_metadata",
                success=False,
                error=se.message,
                error_code=se.code.value,
                message=f"Safety check rejected reflection inspection: {se.message}",
            )
        except Exception as e:
            return UnrealToolResult(
                tool="unreal.inspect_reflection_metadata",
                success=False,
                error=str(e),
                error_code=UnrealErrorCode.INVALID_PARAMETER.value,
                message=f"Failed to inspect reflection metadata: {e}",
            )

    def _tool_inspect_inheritance(
        self,
        file_path: str = "",
        class_name: Optional[str] = None,
        project_path: Optional[str] = None,
        **kwargs,
    ) -> UnrealToolResult:
        """unreal.inspect_inheritance: Inspects class inheritance and checks for UObject/AActor bases."""
        if not file_path:
            return UnrealToolResult(
                tool="unreal.inspect_inheritance",
                success=False,
                error="file_path parameter is required",
                error_code=UnrealErrorCode.INVALID_PARAMETER.value,
                message="file_path parameter is required.",
            )
        try:
            fp = self.safety.validate_source_file_path(file_path, project_path)
            analysis = self.cpp_analyzer.analyze_file(fp)
            hierarchies = []
            for c in analysis.classes:
                if not class_name or c.name == class_name:
                    is_uobj = any("UObject" in b or "AActor" in b or "APawn" in b or "ACharacter" in b for b in c.base_classes)
                    is_act = any("AActor" in b or "APawn" in b or "ACharacter" in b for b in c.base_classes)
                    hierarchies.append({
                        "class_name": c.name,
                        "base_classes": c.base_classes,
                        "is_uobject": is_uobj,
                        "is_actor": is_act,
                        "declaration": c.declaration_signature,
                        "file_path": str(fp),
                    })
            return UnrealToolResult(
                tool="unreal.inspect_inheritance",
                success=True,
                data={"hierarchies": hierarchies, "count": len(hierarchies), "file_path": str(fp)},
                verified=True,
                message=f"Inspected inheritance for {len(hierarchies)} class(es).",
            )
        except UnrealSafetyError as se:
            return UnrealToolResult(
                tool="unreal.inspect_inheritance",
                success=False,
                error=se.message,
                error_code=se.code.value,
                message=f"Safety check rejected inheritance inspection: {se.message}",
            )
        except Exception as e:
            return UnrealToolResult(
                tool="unreal.inspect_inheritance",
                success=False,
                error=str(e),
                error_code=UnrealErrorCode.INVALID_PARAMETER.value,
                message=f"Failed to inspect inheritance: {e}",
            )

    def _tool_list_blueprint_assets(
        self,
        project_path: Optional[str] = None,
        subfolder: Optional[str] = None,
        limit: int = 100,
        **kwargs,
    ) -> UnrealToolResult:
        """unreal.list_blueprint_assets: Discovers Blueprint assets in project Content/ directory."""
        try:
            target_proj = project_path or str(self.safety.authorized_projects[0])
            assets = self.bp_inspector.list_blueprint_assets(target_proj, subfolder=subfolder, limit=limit)
            return UnrealToolResult(
                tool="unreal.list_blueprint_assets",
                success=True,
                data={"assets": [a.to_dict() for a in assets], "count": len(assets), "limit": limit},
                verified=True,
                message=f"Discovered {len(assets)} Blueprint asset(s).",
            )
        except UnrealSafetyError as se:
            return UnrealToolResult(
                tool="unreal.list_blueprint_assets",
                success=False,
                error=se.message,
                error_code=se.code.value,
                message=f"Safety check rejected Blueprint asset listing: {se.message}",
            )
        except Exception as e:
            return UnrealToolResult(
                tool="unreal.list_blueprint_assets",
                success=False,
                error=str(e),
                error_code=UnrealErrorCode.INVALID_PARAMETER.value,
                message=f"Failed to list Blueprint assets: {e}",
            )

    def _tool_inspect_blueprint_metadata(
        self,
        asset_path: str = "",
        project_path: Optional[str] = None,
        **kwargs,
    ) -> UnrealToolResult:
        """unreal.inspect_blueprint_metadata: Inspects safe metadata of a Blueprint .uasset file."""
        if not asset_path:
            return UnrealToolResult(
                tool="unreal.inspect_blueprint_metadata",
                success=False,
                error="asset_path parameter is required",
                error_code=UnrealErrorCode.INVALID_PARAMETER.value,
                message="asset_path parameter is required.",
            )
        try:
            target_proj = project_path or str(self.safety.authorized_projects[0])
            info = self.bp_inspector.inspect_blueprint(asset_path, target_proj)
            return UnrealToolResult(
                tool="unreal.inspect_blueprint_metadata",
                success=True,
                data=info.to_dict(),
                verified=True,
                message=f"Inspected metadata for Blueprint asset {info.asset_name}.",
            )
        except UnrealSafetyError as se:
            return UnrealToolResult(
                tool="unreal.inspect_blueprint_metadata",
                success=False,
                error=se.message,
                error_code=se.code.value,
                message=f"Safety check rejected Blueprint metadata inspection: {se.message}",
            )
        except Exception as e:
            return UnrealToolResult(
                tool="unreal.inspect_blueprint_metadata",
                success=False,
                error=str(e),
                error_code=UnrealErrorCode.INVALID_PARAMETER.value,
                message=f"Failed to inspect Blueprint metadata: {e}",
            )

    def _tool_validate_cpp_change(
        self,
        file_path: str = "",
        operation: str = "",
        content: str = "",
        target_symbol: str = "",
        expected_sha256: str = "",
        start_line: int = 0,
        end_line: int = 0,
        project_path: Optional[str] = None,
        **kwargs,
    ) -> UnrealToolResult:
        """unreal.validate_cpp_change: Validates a proposed C++ modification against safety policies."""
        payload = {
            "file_path": file_path,
            "operation": operation,
            "content": content,
            "target_symbol": target_symbol,
            "expected_sha256": expected_sha256,
            "start_line": start_line,
            "end_line": end_line,
        }
        try:
            self.safety.validate_proposal_payload(payload, project_path)
            prop = UnrealModificationProposal.from_dict(payload)
            val = self.source_modifier.validate_proposal(prop)
            return UnrealToolResult(
                tool="unreal.validate_cpp_change",
                success=val["valid"],
                data=val,
                error=val.get("error"),
                error_code=val.get("error_code"),
                verified=True,
                message=val.get("message") or (val.get("error") if not val["valid"] else "Proposal is valid."),
            )
        except UnrealSafetyError as se:
            return UnrealToolResult(
                tool="unreal.validate_cpp_change",
                success=False,
                error=se.message,
                error_code=se.code.value,
                message=f"Safety check rejected proposal: {se.message}",
            )
        except Exception as e:
            return UnrealToolResult(
                tool="unreal.validate_cpp_change",
                success=False,
                error=str(e),
                error_code=UnrealErrorCode.INVALID_PROPOSAL.value,
                message=f"Failed to validate C++ change: {e}",
            )

    def _tool_propose_cpp_change(
        self,
        file_path: str = "",
        operation: str = "",
        content: str = "",
        target_symbol: str = "",
        expected_sha256: str = "",
        start_line: int = 0,
        end_line: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
        project_path: Optional[str] = None,
        **kwargs,
    ) -> UnrealToolResult:
        """unreal.propose_cpp_change: Constructs and validates a bounded C++ modification proposal."""
        payload = {
            "file_path": file_path,
            "operation": operation,
            "content": content,
            "target_symbol": target_symbol,
            "expected_sha256": expected_sha256,
            "start_line": start_line,
            "end_line": end_line,
            "metadata": metadata or {},
        }
        try:
            self.safety.validate_proposal_payload(payload, project_path)
            prop = UnrealModificationProposal.from_dict(payload)
            val = self.source_modifier.validate_proposal(prop)
            if not val["valid"]:
                return UnrealToolResult(
                    tool="unreal.propose_cpp_change",
                    success=False,
                    error=val.get("error"),
                    error_code=val.get("error_code") or UnrealErrorCode.INVALID_PROPOSAL.value,
                    data=val,
                    message=f"Proposal rejected: {val.get('error')}",
                )
            return UnrealToolResult(
                tool="unreal.propose_cpp_change",
                success=True,
                data=prop.to_dict(),
                verified=True,
                message=f"Created proposal {prop.proposal_id} ({prop.operation.value}).",
            )
        except UnrealSafetyError as se:
            return UnrealToolResult(
                tool="unreal.propose_cpp_change",
                success=False,
                error=se.message,
                error_code=se.code.value,
                message=f"Safety check rejected proposal creation: {se.message}",
            )
        except Exception as e:
            return UnrealToolResult(
                tool="unreal.propose_cpp_change",
                success=False,
                error=str(e),
                error_code=UnrealErrorCode.INVALID_PROPOSAL.value,
                message=f"Failed to propose C++ change: {e}",
            )

    def _tool_apply_cpp_change(
        self,
        proposal: Optional[Dict[str, Any]] = None,
        project_path: Optional[str] = None,
        **kwargs,
    ) -> UnrealToolResult:
        """unreal.apply_cpp_change: Applies a verified, atomic modification to a C++ file with backup."""
        payload = dict(proposal) if proposal else dict(kwargs)
        try:
            self.safety.validate_proposal_payload(payload, project_path)
            prop = UnrealModificationProposal.from_dict(payload)
            res = self.source_modifier.apply_modification(prop)
            return UnrealToolResult(
                tool="unreal.apply_cpp_change",
                success=res.success,
                data=res.to_dict(),
                error=res.error_message,
                error_code=res.error_code,
                verified=res.success,
                message=f"Applied modification to {res.target_path} (backup: {res.backup_id})." if res.success else f"Failed to apply: {res.error_message}",
            )
        except UnrealSafetyError as se:
            return UnrealToolResult(
                tool="unreal.apply_cpp_change",
                success=False,
                error=se.message,
                error_code=se.code.value,
                message=f"Safety check rejected applying modification: {se.message}",
            )
        except Exception as e:
            return UnrealToolResult(
                tool="unreal.apply_cpp_change",
                success=False,
                error=str(e),
                error_code=UnrealErrorCode.WRITE_FAILED.value,
                message=f"Failed to apply C++ modification: {e}",
            )

    def _tool_rollback_cpp_change(
        self,
        backup_id: Optional[str] = None,
        backup_path: Optional[str] = None,
        target_path: Optional[str] = None,
        **kwargs,
    ) -> UnrealToolResult:
        """unreal.rollback_cpp_change: Restores a C++ source file from a verified backup."""
        try:
            bp = Path(backup_path) if backup_path else None
            tp = Path(target_path) if target_path else None
            res = self.source_modifier.rollback_modification(backup_id=backup_id, backup_path=bp, target_path=tp)
            return UnrealToolResult(
                tool="unreal.rollback_cpp_change",
                success=res.success,
                data=res.to_dict(),
                error=res.error_message,
                error_code=res.error_code,
                verified=res.success,
                message=f"Rolled back {res.target_path} to backup {res.backup_id}." if res.success else f"Rollback failed: {res.error_message}",
            )
        except UnrealSafetyError as se:
            return UnrealToolResult(
                tool="unreal.rollback_cpp_change",
                success=False,
                error=se.message,
                error_code=se.code.value,
                message=f"Safety check rejected rollback: {se.message}",
            )
        except Exception as e:
            return UnrealToolResult(
                tool="unreal.rollback_cpp_change",
                success=False,
                error=str(e),
                error_code=UnrealErrorCode.ROLLBACK_FAILED.value,
                message=f"Failed to rollback C++ modification: {e}",
            )

    def _tool_verify_source_change(
        self,
        file_path: str = "",
        expected_sha256: Optional[str] = None,
        check_syntax: bool = True,
        project_path: Optional[str] = None,
        **kwargs,
    ) -> UnrealToolResult:
        """unreal.verify_source_change: Verifies SHA-256 integrity and balanced syntax after modifications."""
        if not file_path:
            return UnrealToolResult(
                tool="unreal.verify_source_change",
                success=False,
                error="file_path parameter is required",
                error_code=UnrealErrorCode.INVALID_PARAMETER.value,
                message="file_path parameter is required.",
            )
        try:
            fp = self.safety.validate_source_file_path(file_path, project_path)
            if not fp.exists():
                return UnrealToolResult(
                    tool="unreal.verify_source_change",
                    success=False,
                    error=f"Source file {fp} does not exist",
                    error_code=UnrealErrorCode.FILE_NOT_FOUND.value,
                    message=f"File {fp.name} does not exist.",
                )
            data = fp.read_bytes()
            actual_sha = hashlib.sha256(data).hexdigest()
            sha_match = True
            if expected_sha256:
                sha_match = (actual_sha.lower() == expected_sha256.lower())
            syntax_valid = True
            syntax_err = None
            if check_syntax:
                text = data.decode("utf-8", errors="replace")
                syntax_valid, syntax_err = self.cpp_analyzer.verify_balanced_syntax(text)
            success = sha_match and syntax_valid
            err_msg = None
            if not sha_match:
                err_msg = f"SHA-256 mismatch: expected {expected_sha256}, got {actual_sha}"
            elif not syntax_valid:
                err_msg = f"Syntax check failed: {syntax_err}"
            return UnrealToolResult(
                tool="unreal.verify_source_change",
                success=success,
                data={
                    "file_path": str(fp),
                    "sha256": actual_sha,
                    "expected_sha256": expected_sha256,
                    "sha_matches": sha_match,
                    "syntax_valid": syntax_valid,
                    "syntax_error": syntax_err,
                },
                error=err_msg,
                error_code=UnrealErrorCode.VERIFICATION_FAILED.value if not success else None,
                verified=True,
                message="Source verification passed." if success else f"Source verification failed: {err_msg}",
            )
        except UnrealSafetyError as se:
            return UnrealToolResult(
                tool="unreal.verify_source_change",
                success=False,
                error=se.message,
                error_code=se.code.value,
                message=f"Safety check rejected source verification: {se.message}",
            )
        except Exception as e:
            return UnrealToolResult(
                tool="unreal.verify_source_change",
                success=False,
                error=str(e),
                error_code=UnrealErrorCode.VERIFICATION_FAILED.value,
                message=f"Failed to verify source change: {e}",
            )

    # -------------------------------------------------------------------------
    # Step 9 Phase 5 Tool Handlers: Autonomous Repair & End-to-End Workflows
    # -------------------------------------------------------------------------

    def _tool_create_repair_workflow(
        self,
        project_path: str = "",
        workflow_type: str = "WORKFLOW_BUILD_FAILURE_REPAIR",
        goal: str = "",
        custom_steps: Optional[List[Dict[str, Any]]] = None,
        **kwargs,
    ) -> UnrealToolResult:
        """unreal.create_repair_workflow: Creates and validates a structured workflow plan."""
        try:
            proj = project_path or str(DEFAULT_AUTHORIZED_PROJECT)
            try:
                wf_type = UnrealWorkflowType(workflow_type)
            except ValueError:
                wf_type = UnrealWorkflowType.CUSTOM

            max_repair_attempts = kwargs.get("max_repair_attempts", MAX_REPAIR_ATTEMPTS)
            plan = self.agent.create_workflow_plan(
                workflow_type=wf_type,
                target_project=proj,
                goal=goal or f"Autonomous workflow for {wf_type.value}",
                custom_steps=custom_steps,
                max_repair_attempts=max_repair_attempts,
            )
            return UnrealToolResult(
                tool="unreal.create_repair_workflow",
                success=True,
                data=plan.to_dict(),
                verified=True,
                message=f"Created workflow plan {plan.workflow_id} with {len(plan.steps)} steps.",
            )
        except UnrealSafetyError as se:
            return UnrealToolResult(
                tool="unreal.create_repair_workflow",
                success=False,
                error=se.message,
                error_code=se.code.value,
                message=f"Safety policy rejected workflow creation: {se.message}",
            )
        except Exception as e:
            return UnrealToolResult(
                tool="unreal.create_repair_workflow",
                success=False,
                error=str(e),
                error_code=UnrealErrorCode.INVALID_OPERATION.value,
                message=f"Failed to create repair workflow: {e}",
            )

    def _tool_inspect_failure(
        self,
        project_path: str = "",
        build_log: Optional[str] = None,
        test_log: Optional[str] = None,
        runtime_log: Optional[str] = None,
        **kwargs,
    ) -> UnrealToolResult:
        """unreal.inspect_failure: Deterministically inspects failure evidence across build/runtime logs."""
        try:
            proj = project_path or str(DEFAULT_AUTHORIZED_PROJECT)
            diag_res = self.agent.inspect_and_diagnose(
                project_path=proj,
                build_log=build_log,
                test_log=test_log,
                runtime_log=runtime_log,
            )
            return UnrealToolResult(
                tool="unreal.inspect_failure",
                success=True,
                data=diag_res,
                verified=True,
                message=f"Failure inspection classified domain: {diag_res.get('failure_domain')}",
            )
        except UnrealSafetyError as se:
            return UnrealToolResult(
                tool="unreal.inspect_failure",
                success=False,
                error=se.message,
                error_code=se.code.value,
                message=f"Safety check rejected failure inspection: {se.message}",
            )
        except Exception as e:
            return UnrealToolResult(
                tool="unreal.inspect_failure",
                success=False,
                error=str(e),
                error_code=UnrealErrorCode.INVALID_OPERATION.value,
                message=f"Failed to inspect failure: {e}",
            )

    def _tool_diagnose_failure(
        self,
        project_path: str = "",
        build_log: Optional[str] = None,
        test_log: Optional[str] = None,
        runtime_log: Optional[str] = None,
        source_file: Optional[str] = None,
        **kwargs,
    ) -> UnrealToolResult:
        """unreal.diagnose_failure: Executes deterministic failure diagnosis enforcing EVIDENCE > MODEL CLAIMS."""
        try:
            proj = project_path or str(DEFAULT_AUTHORIZED_PROJECT)
            diag_res = self.agent.inspect_and_diagnose(
                project_path=proj,
                build_log=build_log,
                test_log=test_log,
                runtime_log=runtime_log,
                source_file=source_file,
            )
            return UnrealToolResult(
                tool="unreal.diagnose_failure",
                success=True,
                data=diag_res,
                verified=True,
                message=f"Diagnosis completed: domain={diag_res.get('failure_domain')}, root_cause={diag_res.get('root_cause')}",
            )
        except UnrealSafetyError as se:
            return UnrealToolResult(
                tool="unreal.diagnose_failure",
                success=False,
                error=se.message,
                error_code=se.code.value,
                message=f"Safety check rejected failure diagnosis: {se.message}",
            )
        except Exception as e:
            return UnrealToolResult(
                tool="unreal.diagnose_failure",
                success=False,
                error=str(e),
                error_code=UnrealErrorCode.INVALID_OPERATION.value,
                message=f"Failed to diagnose failure: {e}",
            )

    def _tool_plan_repair(
        self,
        project_path: str = "",
        workflow_type: str = "WORKFLOW_BUILD_FAILURE_REPAIR",
        goal: str = "",
        custom_steps: Optional[List[Dict[str, Any]]] = None,
        **kwargs,
    ) -> UnrealToolResult:
        """unreal.plan_repair: Creates a structured repair plan bounded to max 25 steps."""
        return self._tool_create_repair_workflow(
            project_path=project_path,
            workflow_type=workflow_type,
            goal=goal,
            custom_steps=custom_steps,
            **kwargs,
        )

    def _tool_validate_repair_plan(
        self,
        plan: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> UnrealToolResult:
        """unreal.validate_repair_plan: Validates plan against step bounds and prohibited execution tokens."""
        if not plan or not isinstance(plan, dict):
            return UnrealToolResult(
                tool="unreal.validate_repair_plan",
                success=False,
                error="plan parameter is required and must be a dict object",
                error_code=UnrealErrorCode.INVALID_PARAMETER.value,
                message="Plan dict is required.",
            )
        try:
            wf_type_str = plan.get("workflow_type", "CUSTOM")
            try:
                wf_type = UnrealWorkflowType(wf_type_str)
            except ValueError:
                wf_type = UnrealWorkflowType.CUSTOM

            max_att = plan.get("max_repair_attempts", MAX_REPAIR_ATTEMPTS)
            plan_obj = UnrealWorkflowPlan(
                workflow_id=plan.get("workflow_id", "wf_val"),
                goal=plan.get("goal", ""),
                workflow_type=wf_type,
                target_project=plan.get("target_project", str(DEFAULT_AUTHORIZED_PROJECT)),
                steps=plan.get("steps", []),
                max_repair_attempts=max_att,
            )
            is_valid = self.agent.validate_workflow_plan(plan_obj)
            return UnrealToolResult(
                tool="unreal.validate_repair_plan",
                success=is_valid,
                data={"valid": is_valid, "step_count": len(plan_obj.steps)},
                verified=True,
                message=f"Plan validation passed with {len(plan_obj.steps)} steps.",
            )
        except UnrealSafetyError as se:
            return UnrealToolResult(
                tool="unreal.validate_repair_plan",
                success=False,
                error=se.message,
                error_code=se.code.value,
                message=f"Safety check rejected repair plan: {se.message}",
            )
        except Exception as e:
            return UnrealToolResult(
                tool="unreal.validate_repair_plan",
                success=False,
                error=str(e),
                error_code=UnrealErrorCode.PLAN_VALIDATION_FAILED.value,
                message=f"Failed to validate repair plan: {e}",
            )

    def _tool_execute_repair_workflow(
        self,
        project_path: str = "",
        workflow_type: str = "WORKFLOW_BUILD_FAILURE_REPAIR",
        goal: str = "",
        target_file: Optional[str] = None,
        operation: Optional[str] = None,
        replacement: Optional[str] = None,
        expected_sha256: Optional[str] = None,
        build_log: Optional[str] = None,
        test_log: Optional[str] = None,
        runtime_log: Optional[str] = None,
        **kwargs,
    ) -> UnrealToolResult:
        """unreal.execute_repair_workflow: Executes end-to-end bounded repair workflow."""
        try:
            proj = project_path or str(DEFAULT_AUTHORIZED_PROJECT)
            try:
                wf_type = UnrealWorkflowType(workflow_type)
            except ValueError:
                wf_type = UnrealWorkflowType.CUSTOM

            report = self.agent.execute_workflow(
                workflow_type=wf_type,
                target_project=proj,
                goal=goal,
                build_log=build_log,
                test_log=test_log,
                runtime_log=runtime_log,
                target_file=target_file,
                operation=operation,
                replacement=replacement,
                expected_sha256=expected_sha256,
            )
            return UnrealToolResult(
                tool="unreal.execute_repair_workflow",
                success=report.success,
                data=report.to_dict(),
                error=report.error,
                error_code=report.failure_domain.value if not report.success else None,
                verified=report.success,
                message=f"Workflow {report.workflow_id} [{report.workflow_type.value}] finished with state {report.final_state.value}.",
            )
        except UnrealSafetyError as se:
            return UnrealToolResult(
                tool="unreal.execute_repair_workflow",
                success=False,
                error=se.message,
                error_code=se.code.value,
                message=f"Safety check rejected workflow execution: {se.message}",
            )
        except Exception as e:
            return UnrealToolResult(
                tool="unreal.execute_repair_workflow",
                success=False,
                error=str(e),
                error_code=UnrealErrorCode.INVALID_OPERATION.value,
                message=f"Failed to execute repair workflow: {e}",
            )

    def _tool_verify_repair_workflow(
        self,
        file_path: str = "",
        expected_sha256: Optional[str] = None,
        check_syntax: bool = True,
        project_path: Optional[str] = None,
        **kwargs,
    ) -> UnrealToolResult:
        """unreal.verify_repair_workflow: Verifies syntax and SHA-256 integrity of modified files."""
        res = self._tool_verify_source_change(
            file_path=file_path,
            expected_sha256=expected_sha256,
            check_syntax=check_syntax,
            project_path=project_path,
            **kwargs,
        )
        return UnrealToolResult(
            tool="unreal.verify_repair_workflow",
            success=res.success,
            data=res.data,
            error=res.error,
            error_code=res.error_code,
            verified=res.verified,
            message=res.message,
        )

    def _tool_rollback_repair_workflow(
        self,
        backup_id: Optional[str] = None,
        backup_path: Optional[str] = None,
        target_path: Optional[str] = None,
        **kwargs,
    ) -> UnrealToolResult:
        """unreal.rollback_repair_workflow: Rolls back modifications byte-for-byte using backup checkpoint."""
        res = self._tool_rollback_cpp_change(
            backup_id=backup_id,
            backup_path=backup_path,
            target_path=target_path,
            **kwargs,
        )
        return UnrealToolResult(
            tool="unreal.rollback_repair_workflow",
            success=res.success,
            data=res.data,
            error=res.error,
            error_code=res.error_code,
            verified=res.verified,
            message=res.message,
        )

    def _tool_stop_repair_workflow(
        self,
        workflow_id: str = "",
        reason: str = "User requested stop",
        **kwargs,
    ) -> UnrealToolResult:
        """unreal.stop_repair_workflow: Halts an active workflow and cleans up all owned processes."""
        try:
            success = self.agent.stop_workflow(workflow_id, reason)
            return UnrealToolResult(
                tool="unreal.stop_repair_workflow",
                success=success,
                data={"workflow_id": workflow_id, "stopped": True, "reason": reason},
                verified=True,
                message=f"Workflow {workflow_id} stopped: {reason}",
            )
        except Exception as e:
            return UnrealToolResult(
                tool="unreal.stop_repair_workflow",
                success=False,
                error=str(e),
                error_code=UnrealErrorCode.INVALID_OPERATION.value,
                message=f"Failed to stop repair workflow: {e}",
            )

    def _tool_get_workflow_status(
        self,
        workflow_id: Optional[str] = None,
        **kwargs,
    ) -> UnrealToolResult:
        """unreal.get_workflow_status: Returns active workflow report or current agent state."""
        try:
            if workflow_id and workflow_id in self.agent._active_workflows:
                report = self.agent._active_workflows[workflow_id]
                return UnrealToolResult(
                    tool="unreal.get_workflow_status",
                    success=True,
                    data=report.to_dict(),
                    verified=True,
                    message=f"Workflow {workflow_id} state: {report.final_state.value}",
                )
            else:
                return UnrealToolResult(
                    tool="unreal.get_workflow_status",
                    success=True,
                    data={
                        "current_state": self.agent.current_state.value,
                        "active_workflows_count": len(self.agent._active_workflows),
                        "owned_processes_count": len(self.agent._owned_processes),
                    },
                    verified=True,
                    message=f"Unreal Agent state: {self.agent.current_state.value}",
                )
        except Exception as e:
            return UnrealToolResult(
                tool="unreal.get_workflow_status",
                success=False,
                error=str(e),
                error_code=UnrealErrorCode.INVALID_OPERATION.value,
                message=f"Failed to get workflow status: {e}",
            )


# Global default tool registry
DEFAULT_UNREAL_TOOL_REGISTRY = UnrealToolRegistry()
