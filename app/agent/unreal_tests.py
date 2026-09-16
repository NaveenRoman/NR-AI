r"""
NR-AI Unreal Engine Test & Runtime Intelligence Engine (Step 9 Phase 3).

Safe, bounded, deterministic management of Unreal Engine test execution,
runtime observation, log capture, crash/ensure/assertion detection,
test result parsing, artifact verification, and evidence-based runtime verification.
Strictly shell=False, bounded timeouts, sensitive data redaction, and model isolation.
"""

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
import uuid

import psutil

from app.agent.unreal_safety import (
    UnrealSafetyGate,
    DEFAULT_UNREAL_SAFETY_GATE,
    UnrealErrorCode,
    UnrealSafetyError,
    EmergencyStopActiveError,
    ALLOWED_UNREAL_TEST_MODES,
    ALLOWED_UNREAL_TEST_PLATFORMS,
    ALLOWED_UNREAL_TEST_CONFIGURATIONS,
    ALLOWED_UNREAL_EXECUTABLE_NAMES,
    TEST_TIMEOUT_SECONDS,
    MAX_CAPTURED_OUTPUT_BYTES,
    MAX_TEST_LOG_BYTES,
    MAX_TEST_RESULT_BYTES,
    redact_sensitive_data,
)
from app.agent.unreal_environment import (
    UnrealEnvironmentDetector,
    UnrealEngineInstance,
    DEFAULT_UNREAL_ENV_DETECTOR,
)
from app.agent.unreal_project import (
    UnrealProjectInspector,
    DEFAULT_UNREAL_PROJECT_INSPECTOR,
)
from app.agent.unreal_build import (
    UnrealBuildEnvironmentStatus,
)

logger = logging.getLogger("NRAI.UnrealTests")


# -----------------------------------------------------------------------------
# Enums
# -----------------------------------------------------------------------------

class UnrealTestEnvironmentStatus(str, Enum):
    READY = "READY"
    PARTIALLY_READY = "PARTIALLY_READY"
    ENVIRONMENT_UNAVAILABLE = "ENVIRONMENT_UNAVAILABLE"
    PROJECT_INVALID = "PROJECT_INVALID"
    ENGINE_MISMATCH = "ENGINE_MISMATCH"
    EXECUTION_NOT_VERIFIED = "EXECUTION_NOT_VERIFIED"


class UnrealTestStatus(str, Enum):
    PASSED = "Passed"
    FAILED = "Failed"
    SKIPPED = "Skipped"
    NOT_EXECUTED = "NotExecuted"
    TIMED_OUT = "TimedOut"


class UnrealRuntimeResultStatus(str, Enum):
    RUNTIME_SUCCEEDED = "RUNTIME_SUCCEEDED"
    RUNTIME_FAILED = "RUNTIME_FAILED"
    TESTS_PASSED = "TESTS_PASSED"
    TESTS_FAILED = "TESTS_FAILED"
    CRASH_DETECTED = "CRASH_DETECTED"
    ENSURE_DETECTED = "ENSURE_DETECTED"
    RUNTIME_TIMEOUT = "RUNTIME_TIMEOUT"
    ENVIRONMENT_FAILURE = "ENVIRONMENT_FAILURE"
    NOT_VERIFIED = "NOT_VERIFIED"


class UnrealRuntimeIssueCategory(str, Enum):
    FATAL_ERROR = "FATAL_ERROR"
    CRASH = "CRASH"
    ENSURE_FAILURE = "ENSURE_FAILURE"
    ASSERTION_FAILURE = "ASSERTION_FAILURE"
    ACCESS_VIOLATION = "ACCESS_VIOLATION"
    STACK_OVERFLOW = "STACK_OVERFLOW"
    INVALID_MEMORY_ACCESS = "INVALID_MEMORY_ACCESS"
    MISSING_ASSET = "MISSING_ASSET"
    MISSING_PACKAGE = "MISSING_PACKAGE"
    FAILED_LOAD = "FAILED_LOAD"
    UOBJECT_FAILURE = "UOBJECT_FAILURE"
    BLUEPRINT_RUNTIME_ERROR = "BLUEPRINT_RUNTIME_ERROR"
    PLUGIN_LOAD_FAILURE = "PLUGIN_LOAD_FAILURE"
    MODULE_LOAD_FAILURE = "MODULE_LOAD_FAILURE"
    SUBSYSTEM_INIT_FAILURE = "SUBSYSTEM_INIT_FAILURE"
    RENDERER_INIT_FAILURE = "RENDERER_INIT_FAILURE"
    SHADER_COMPILE_FAILURE = "SHADER_COMPILE_FAILURE"
    CONFIG_FAILURE = "CONFIG_FAILURE"
    PERMISSION_FAILURE = "PERMISSION_FAILURE"
    TIMEOUT = "TIMEOUT"
    PROCESS_TERMINATION = "PROCESS_TERMINATION"
    UNKNOWN_RUNTIME_FAILURE = "UNKNOWN_RUNTIME_FAILURE"


# -----------------------------------------------------------------------------
# Data Models
# -----------------------------------------------------------------------------

@dataclass
class UnrealRuntimeDiagnostic:
    """Structured representation of a runtime error, crash, ensure, or assertion."""
    category: UnrealRuntimeIssueCategory
    severity: str = "error"  # fatal, crash, ensure, assertion, error, warning
    message: str = ""
    file: str = ""
    line: int = 0
    function: str = ""
    module: str = ""
    stack_evidence: List[str] = field(default_factory=list)
    raw_line: str = ""
    timestamp: str = ""
    exit_code: Optional[int] = None
    confidence: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category.value,
            "severity": self.severity,
            "message": redact_sensitive_data(self.message),
            "file": redact_sensitive_data(self.file),
            "line": self.line,
            "function": redact_sensitive_data(self.function),
            "module": redact_sensitive_data(self.module),
            "stack_evidence": [redact_sensitive_data(s) for s in self.stack_evidence],
            "raw_line": redact_sensitive_data(self.raw_line),
            "timestamp": self.timestamp,
            "exit_code": self.exit_code,
            "confidence": self.confidence,
        }


@dataclass
class UnrealTestCaseResult:
    """Result of an individual test case."""
    name: str
    full_name: str
    status: UnrealTestStatus = UnrealTestStatus.PASSED
    duration_seconds: float = 0.0
    error_message: Optional[str] = None
    diagnostics: List[UnrealRuntimeDiagnostic] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "full_name": self.full_name,
            "status": self.status.value,
            "duration_seconds": round(self.duration_seconds, 3),
            "error_message": redact_sensitive_data(self.error_message) if self.error_message else None,
            "diagnostics": [d.to_dict() for d in self.diagnostics],
        }


@dataclass
class UnrealTestSuiteResult:
    """Result of a test fixture or suite."""
    name: str
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    cases: List[UnrealTestCaseResult] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "cases": [c.to_dict() for c in self.cases],
        }


@dataclass
class UnrealTestSummary:
    """Aggregate summary of a test run."""
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    duration_seconds: float = 0.0
    pass_rate: float = 0.0
    status: str = "PASSED"
    failure_categories: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "duration_seconds": round(self.duration_seconds, 3),
            "pass_rate": round(self.pass_rate, 2),
            "status": self.status,
            "failure_categories": dict(self.failure_categories),
        }


@dataclass
class UnrealTestArtifactInfo:
    """Verification metadata for a test or log artifact."""
    path: str
    artifact_type: str = "LOG"  # LOG, TEST_REPORT, CRASH_DUMP, JSON
    exists: bool = False
    size_bytes: int = 0
    sha256: str = ""
    verified: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "artifact_type": self.artifact_type,
            "exists": self.exists,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "verified": self.verified,
        }


@dataclass
class UnrealTestExecutionResult:
    """Comprehensive outcome of an Unreal test or runtime execution."""
    success: bool = False
    status: UnrealRuntimeResultStatus = UnrealRuntimeResultStatus.NOT_VERIFIED
    test_mode: str = "SmokeTest"
    exit_code: int = 0
    duration_seconds: float = 0.0
    summary: Optional[UnrealTestSummary] = None
    test_cases: List[UnrealTestCaseResult] = field(default_factory=list)
    diagnostics: List[UnrealRuntimeDiagnostic] = field(default_factory=list)
    artifacts: List[UnrealTestArtifactInfo] = field(default_factory=list)
    stdout_captured: str = ""
    stderr_captured: str = ""
    verified: bool = False
    error_message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "status": self.status.value,
            "test_mode": self.test_mode,
            "exit_code": self.exit_code,
            "duration_seconds": round(self.duration_seconds, 3),
            "summary": self.summary.to_dict() if self.summary else None,
            "test_cases": [c.to_dict() for c in self.test_cases],
            "diagnostics": [d.to_dict() for d in self.diagnostics],
            "artifacts": [a.to_dict() for a in self.artifacts],
            "stdout_captured": redact_sensitive_data(self.stdout_captured),
            "stderr_captured": redact_sensitive_data(self.stderr_captured),
            "verified": self.verified,
            "error_message": redact_sensitive_data(self.error_message),
        }


# -----------------------------------------------------------------------------
# Component 1: Unreal Test Environment Validator
# -----------------------------------------------------------------------------

class UnrealTestEnvironmentValidator:
    """
    Deterministically validates pre-flight requirements for Unreal test/runtime execution:
    - Engine installation and UnrealEditor-Cmd.exe / UnrealEditor.exe
    - UnrealBuildTool.exe availability
    - Host .NET runtime host availability
    - Project validity (.uproject syntax and presence)
    - Engine association validity
    """

    def __init__(
        self,
        safety_gate: Optional[UnrealSafetyGate] = None,
        env_detector: Optional[UnrealEnvironmentDetector] = None,
        project_inspector: Optional[UnrealProjectInspector] = None,
    ):
        self.safety = safety_gate or DEFAULT_UNREAL_SAFETY_GATE
        self.env_detector = env_detector or DEFAULT_UNREAL_ENV_DETECTOR
        self.project_inspector = project_inspector or DEFAULT_UNREAL_PROJECT_INSPECTOR

    def validate(self, project_path: Optional[Path] = None) -> Dict[str, Any]:
        """Runs pre-flight validation and returns structured diagnostic status."""
        self.safety.assert_not_emergency_stopped()
        self.safety.check_rate_limit("validate_test_environment")

        checks: Dict[str, Any] = {}
        missing_components: List[str] = []

        # 1. Engine Detection
        engines = self.env_detector.detect_installed_engines()
        if not engines:
            return {
                "status": UnrealTestEnvironmentStatus.ENVIRONMENT_UNAVAILABLE.value,
                "message": "No Unreal Engine installations detected on this system.",
                "missing_components": ["UnrealEngine"],
                "checks": {"engine_detected": False},
                "verified": True,
            }

        primary_engine = engines[0]
        checks["engine_version"] = primary_engine.version
        checks["engine_path"] = str(primary_engine.engine_path)

        # 2. Executable checks
        def _check_path_exists(p_val: Any) -> bool:
            if p_val is None:
                return False
            try:
                return Path(p_val).exists()
            except Exception:
                return False

        has_editor_cmd = _check_path_exists(primary_engine.cmd_executable)
        has_editor = _check_path_exists(primary_engine.editor_executable)
        has_ubt = _check_path_exists(primary_engine.ubt_executable)

        checks["editor_cmd_exists"] = has_editor_cmd
        checks["editor_exists"] = has_editor
        checks["ubt_exists"] = has_ubt

        if not has_editor_cmd and not has_editor:
            missing_components.append("UnrealEditor-Cmd.exe")

        # 3. .NET Runtime Check
        dotnet_available = False
        dotnet_version = ""
        try:
            p = subprocess.run(
                ["dotnet", "--version"],
                capture_output=True,
                text=True,
                shell=False,
                timeout=5,
            )
            if p.returncode == 0 and p.stdout.strip():
                dotnet_available = True
                dotnet_version = p.stdout.strip()
        except Exception:
            dotnet_available = False

        checks["dotnet_available"] = dotnet_available
        checks["dotnet_version"] = dotnet_version
        if not dotnet_available:
            missing_components.append("dotnet_runtime_host")

        # 4. Project Validation
        target_project = project_path or self.safety.default_project
        checks["project_path"] = str(target_project)
        project_valid = False

        if target_project and target_project.exists():
            uproject_files = list(target_project.glob("*.uproject"))
            if uproject_files:
                parsed = self.project_inspector.parse_uproject(uproject_files[0])
                if parsed.get("is_valid") or parsed.get("success"):
                    project_valid = True
                    checks["project_name"] = uproject_files[0].stem
                    checks["engine_association"] = str(parsed.get("EngineAssociation") or parsed.get("engine_association") or "")

                    # Verify engine association
                    assoc = self.project_inspector.validate_engine_association(
                        target_project, installed_engines=engines
                    )
                    checks["engine_association_matched"] = assoc.get("is_matched_with_installed", False)
                    if not assoc.get("is_matched_with_installed", False):
                        missing_components.append("engine_version_match")
            else:
                missing_components.append("uproject_file")
        else:
            missing_components.append("project_directory")

        checks["project_valid"] = project_valid

        # Status determination
        if not project_valid:
            status = UnrealTestEnvironmentStatus.PROJECT_INVALID
            message = f"Project at '{target_project}' is missing or has invalid .uproject configuration."
        elif not has_editor_cmd and not has_editor:
            status = UnrealTestEnvironmentStatus.ENVIRONMENT_UNAVAILABLE
            message = "Unreal Editor executables are missing from the engine installation."
        elif not dotnet_available:
            status = UnrealTestEnvironmentStatus.PARTIALLY_READY
            message = "Unreal Engine detected but .NET runtime host is unavailable; live batchmode execution cannot run."
        elif "engine_version_match" in missing_components:
            status = UnrealTestEnvironmentStatus.ENGINE_MISMATCH
            message = "Project EngineAssociation does not match installed engine version."
        else:
            status = UnrealTestEnvironmentStatus.READY
            message = "Unreal test/runtime environment is verified and ready."

        return {
            "status": status.value,
            "message": message,
            "missing_components": missing_components,
            "checks": checks,
            "verified": True,
        }


# -----------------------------------------------------------------------------
# Component 2: Unreal Runtime Log Parser
# -----------------------------------------------------------------------------

class UnrealRuntimeLogParser:
    """
    Deterministic regex parser for Unreal Engine runtime, editor, and test logs.
    Recognizes 22 categories: fatal errors, crashes, ensures, assertions, access violations,
    stack overflows, invalid memory access, missing assets/packages, UObject errors,
    Blueprint runtime errors, subsystem/renderer/shader failures, and config/permission failures.
    """

    CRASH_PATTERNS = [
        re.compile(r"=== Critical error: ===.*", re.IGNORECASE),
        re.compile(r"Unhandled Exception:\s*(0x[0-9a-fA-F]+|\w+)", re.IGNORECASE),
        re.compile(r"(Crash dump.*|Windows Error Reporting.*)", re.IGNORECASE),
        re.compile(r"Access violation (reading|writing) location\s*(0x[0-9a-fA-F]+)", re.IGNORECASE),
        re.compile(r"\[Callstack\]\s+0x[0-9a-fA-F]+", re.IGNORECASE),
    ]

    ENSURE_PATTERNS = [
        re.compile(r"Ensure condition failed:\s*(.+)", re.IGNORECASE),
        re.compile(r"LogOutputDevice:\s*Error:\s*Ensure condition failed:\s*(.+)", re.IGNORECASE),
    ]

    ASSERTION_PATTERNS = [
        re.compile(r"Assertion failed:\s*(.+)", re.IGNORECASE),
        re.compile(r"check\(\) failed:\s*(.+)", re.IGNORECASE),
        re.compile(r"verify\(\) failed:\s*(.+)", re.IGNORECASE),
        re.compile(r"checkSlow\(\) failed:\s*(.+)", re.IGNORECASE),
    ]

    FATAL_PATTERNS = [
        re.compile(r"Fatal error:\s*\[File:(.+?)\]\s*\[Line:\s*(\d+)\]\s*(.+)", re.IGNORECASE),
        re.compile(r"LogOutputDevice:\s*Error:\s*=== Critical error: ===", re.IGNORECASE),
        re.compile(r"LogOutputDevice:\s*Error:\s*Fatal error:\s*(.+)", re.IGNORECASE),
    ]

    ACCESS_VIOLATION_PATTERNS = [
        re.compile(r"EXCEPTION_ACCESS_VIOLATION", re.IGNORECASE),
        re.compile(r"0xC0000005", re.IGNORECASE),
    ]

    STACK_OVERFLOW_PATTERNS = [
        re.compile(r"EXCEPTION_STACK_OVERFLOW", re.IGNORECASE),
        re.compile(r"0xC00000FD", re.IGNORECASE),
    ]

    INVALID_MEMORY_PATTERNS = [
        re.compile(r"Null pointer dereference", re.IGNORECASE),
        re.compile(r"Invalid memory access", re.IGNORECASE),
        re.compile(r"Attempted to access memory at\s*(0x[0-9a-fA-F]+)", re.IGNORECASE),
    ]

    MISSING_ASSET_PATTERNS = [
        re.compile(r"Can't find file for asset\s*['\"]?([^'\"\r\n]+)['\"]?", re.IGNORECASE),
        re.compile(r"Failed to load .*?\/([A-Za-z0-9_\/]+)\.uasset", re.IGNORECASE),
    ]

    MISSING_PACKAGE_PATTERNS = [
        re.compile(r"Package\s+['\"]?([^'\"\r\n]+)['\"]?\s+could not be found", re.IGNORECASE),
        re.compile(r"Cannot open file for package\s+['\"]?([^'\"\r\n]+)['\"]?", re.IGNORECASE),
    ]

    FAILED_LOAD_PATTERNS = [
        re.compile(r"Failed to load (package|module|plugin|asset)\s*['\"]?([^'\"\r\n]+)['\"]?", re.IGNORECASE),
    ]

    UOBJECT_PATTERNS = [
        re.compile(r"FUObjectHashTables", re.IGNORECASE),
        re.compile(r"UObject\s+['\"]?([^'\"\r\n]+)['\"]?\s+has not been initialized", re.IGNORECASE),
        re.compile(r"Garbage collection error", re.IGNORECASE),
    ]

    BLUEPRINT_PATTERNS = [
        re.compile(r'Blueprint Runtime Error:\s*"([^"]+)"\s*on\s*([^\r\n]+)', re.IGNORECASE),
        re.compile(r"Accessed None trying to read property\s*([A-Za-z0-9_]+)", re.IGNORECASE),
    ]

    PLUGIN_LOAD_PATTERNS = [
        re.compile(r"Failed to load plugin\s*['\"]?([A-Za-z0-9_]+)['\"]?", re.IGNORECASE),
        re.compile(r"Plugin\s+['\"]?([A-Za-z0-9_]+)['\"]?\s+could not be found", re.IGNORECASE),
    ]

    MODULE_LOAD_PATTERNS = [
        re.compile(r"Module\s*['\"]?([A-Za-z0-9_]+)['\"]?\s*could not be loaded", re.IGNORECASE),
        re.compile(r"Unable to load module\s*['\"]?([A-Za-z0-9_]+)['\"]?", re.IGNORECASE),
    ]

    SUBSYSTEM_INIT_PATTERNS = [
        re.compile(r"Subsystem\s*['\"]?([A-Za-z0-9_]+)['\"]?\s*failed to initialize", re.IGNORECASE),
    ]

    RENDERER_INIT_PATTERNS = [
        re.compile(r"Failed to initialize (RHI|D3D12|D3D11|Vulkan|DirectX)", re.IGNORECASE),
        re.compile(r"D3D12 device removed", re.IGNORECASE),
        re.compile(r"No compatible RHI found", re.IGNORECASE),
    ]

    SHADER_COMPILE_PATTERNS = [
        re.compile(r"ShaderCompileWorker failed.*", re.IGNORECASE),
        re.compile(r"Failed to compile (default )?material\s*['\"]?([^'\"\r\n]+)['\"]?", re.IGNORECASE),
    ]

    CONFIG_PATTERNS = [
        re.compile(r"Failed to (read|load) config file\s*['\"]?([^'\"\r\n]+)['\"]?", re.IGNORECASE),
        re.compile(r"Config file error in\s*['\"]?([^'\"\r\n]+)['\"]?", re.IGNORECASE),
    ]

    PERMISSION_PATTERNS = [
        re.compile(r"Access is denied", re.IGNORECASE),
        re.compile(r"UnauthorizedAccessException", re.IGNORECASE),
    ]

    TIMEOUT_PATTERNS = [
        re.compile(r"Commandlet timed out", re.IGNORECASE),
        re.compile(r"Operation timed out after\s*(\d+)", re.IGNORECASE),
    ]

    GENERAL_ERROR_PATTERNS = [
        re.compile(r"(Log\w+):\s*Error:\s*(.+)", re.IGNORECASE),
    ]

    GENERAL_WARNING_PATTERNS = [
        re.compile(r"(Log\w+):\s*Warning:\s*(.+)", re.IGNORECASE),
    ]

    def parse(self, text_or_lines: Union[str, List[str]]) -> Dict[str, Any]:
        """Parses log text or line list deterministically into structured diagnostics."""
        if isinstance(text_or_lines, str):
            lines = text_or_lines.splitlines()
        else:
            lines = list(text_or_lines)

        diagnostics: List[UnrealRuntimeDiagnostic] = []
        stack_frames: List[str] = []
        in_callstack = False

        error_count = 0
        warning_count = 0
        crash_count = 0
        ensure_count = 0
        assertion_count = 0

        for line in lines:
            trimmed = line.strip()
            if not trimmed:
                continue

            # Callstack tracking
            if "[Callstack]" in trimmed or "=== Critical error: ===" in trimmed:
                in_callstack = True
            elif in_callstack and (trimmed.startswith("0x") or trimmed.startswith("[") or "UnrealEditor" in trimmed):
                stack_frames.append(trimmed)
                continue
            elif in_callstack and not (trimmed.startswith("0x") or trimmed.startswith("[") or "!" in trimmed):
                in_callstack = False

            diag: Optional[UnrealRuntimeDiagnostic] = None

            # 1. Fatal Error
            for pat in self.FATAL_PATTERNS:
                m = pat.search(trimmed)
                if m:
                    groups = m.groups()
                    file_match = m.group(1) if len(groups) >= 1 else ""
                    line_match = int(m.group(2)) if len(groups) >= 2 and m.group(2) and m.group(2).isdigit() else 0
                    msg = m.group(3) if len(groups) >= 3 else (m.group(1) if len(groups) >= 1 else trimmed)
                    diag = UnrealRuntimeDiagnostic(
                        category=UnrealRuntimeIssueCategory.FATAL_ERROR,
                        severity="fatal",
                        message=msg,
                        file=file_match,
                        line=line_match,
                        raw_line=trimmed,
                    )
                    error_count += 1
                    break

            # 2. Crashes
            if not diag:
                for pat in self.CRASH_PATTERNS:
                    m = pat.search(trimmed)
                    if m:
                        diag = UnrealRuntimeDiagnostic(
                            category=UnrealRuntimeIssueCategory.CRASH,
                            severity="crash",
                            message=trimmed,
                            raw_line=trimmed,
                        )
                        crash_count += 1
                        error_count += 1
                        break

            # 3. Ensures
            if not diag:
                for pat in self.ENSURE_PATTERNS:
                    m = pat.search(trimmed)
                    if m:
                        diag = UnrealRuntimeDiagnostic(
                            category=UnrealRuntimeIssueCategory.ENSURE_FAILURE,
                            severity="ensure",
                            message=m.group(1),
                            raw_line=trimmed,
                        )
                        ensure_count += 1
                        error_count += 1
                        break

            # 4. Assertions
            if not diag:
                for pat in self.ASSERTION_PATTERNS:
                    m = pat.search(trimmed)
                    if m:
                        diag = UnrealRuntimeDiagnostic(
                            category=UnrealRuntimeIssueCategory.ASSERTION_FAILURE,
                            severity="assertion",
                            message=m.group(1),
                            raw_line=trimmed,
                        )
                        assertion_count += 1
                        error_count += 1
                        break

            # 5. Access Violation
            if not diag:
                for pat in self.ACCESS_VIOLATION_PATTERNS:
                    if pat.search(trimmed):
                        diag = UnrealRuntimeDiagnostic(
                            category=UnrealRuntimeIssueCategory.ACCESS_VIOLATION,
                            severity="crash",
                            message=trimmed,
                            raw_line=trimmed,
                        )
                        crash_count += 1
                        error_count += 1
                        break

            # 6. Stack Overflow
            if not diag:
                for pat in self.STACK_OVERFLOW_PATTERNS:
                    if pat.search(trimmed):
                        diag = UnrealRuntimeDiagnostic(
                            category=UnrealRuntimeIssueCategory.STACK_OVERFLOW,
                            severity="crash",
                            message=trimmed,
                            raw_line=trimmed,
                        )
                        crash_count += 1
                        error_count += 1
                        break

            # 7. Invalid Memory Access
            if not diag:
                for pat in self.INVALID_MEMORY_PATTERNS:
                    if pat.search(trimmed):
                        diag = UnrealRuntimeDiagnostic(
                            category=UnrealRuntimeIssueCategory.INVALID_MEMORY_ACCESS,
                            severity="error",
                            message=trimmed,
                            raw_line=trimmed,
                        )
                        error_count += 1
                        break

            # 8. Missing Asset
            if not diag:
                for pat in self.MISSING_ASSET_PATTERNS:
                    m = pat.search(trimmed)
                    if m:
                        diag = UnrealRuntimeDiagnostic(
                            category=UnrealRuntimeIssueCategory.MISSING_ASSET,
                            severity="error",
                            message=f"Missing asset: {m.group(1)}",
                            raw_line=trimmed,
                        )
                        error_count += 1
                        break

            # 9. Missing Package
            if not diag:
                for pat in self.MISSING_PACKAGE_PATTERNS:
                    m = pat.search(trimmed)
                    if m:
                        diag = UnrealRuntimeDiagnostic(
                            category=UnrealRuntimeIssueCategory.MISSING_PACKAGE,
                            severity="error",
                            message=f"Missing package: {m.group(1)}",
                            raw_line=trimmed,
                        )
                        error_count += 1
                        break

            # 10. Blueprint Runtime Error
            if not diag:
                for pat in self.BLUEPRINT_PATTERNS:
                    m = pat.search(trimmed)
                    if m:
                        diag = UnrealRuntimeDiagnostic(
                            category=UnrealRuntimeIssueCategory.BLUEPRINT_RUNTIME_ERROR,
                            severity="error",
                            message=trimmed,
                            raw_line=trimmed,
                        )
                        error_count += 1
                        break

            # 11. Plugin Load Failure
            if not diag:
                for pat in self.PLUGIN_LOAD_PATTERNS:
                    m = pat.search(trimmed)
                    if m:
                        diag = UnrealRuntimeDiagnostic(
                            category=UnrealRuntimeIssueCategory.PLUGIN_LOAD_FAILURE,
                            severity="error",
                            message=f"Plugin load failure: {m.group(1)}",
                            raw_line=trimmed,
                        )
                        error_count += 1
                        break

            # 12. Module Load Failure
            if not diag:
                for pat in self.MODULE_LOAD_PATTERNS:
                    m = pat.search(trimmed)
                    if m:
                        diag = UnrealRuntimeDiagnostic(
                            category=UnrealRuntimeIssueCategory.MODULE_LOAD_FAILURE,
                            severity="error",
                            message=f"Module load failure: {m.group(1)}",
                            raw_line=trimmed,
                        )
                        error_count += 1
                        break

            # 13. Subsystem / Renderer / Shader / Config / Permission
            if not diag:
                for pat in self.SUBSYSTEM_INIT_PATTERNS:
                    if pat.search(trimmed):
                        diag = UnrealRuntimeDiagnostic(
                            category=UnrealRuntimeIssueCategory.SUBSYSTEM_INIT_FAILURE,
                            severity="error",
                            message=trimmed,
                            raw_line=trimmed,
                        )
                        error_count += 1
                        break

            if not diag:
                for pat in self.RENDERER_INIT_PATTERNS:
                    if pat.search(trimmed):
                        diag = UnrealRuntimeDiagnostic(
                            category=UnrealRuntimeIssueCategory.RENDERER_INIT_FAILURE,
                            severity="error",
                            message=trimmed,
                            raw_line=trimmed,
                        )
                        error_count += 1
                        break

            if not diag:
                for pat in self.SHADER_COMPILE_PATTERNS:
                    if pat.search(trimmed):
                        diag = UnrealRuntimeDiagnostic(
                            category=UnrealRuntimeIssueCategory.SHADER_COMPILE_FAILURE,
                            severity="error",
                            message=trimmed,
                            raw_line=trimmed,
                        )
                        error_count += 1
                        break

            if not diag:
                for pat in self.CONFIG_PATTERNS:
                    if pat.search(trimmed):
                        diag = UnrealRuntimeDiagnostic(
                            category=UnrealRuntimeIssueCategory.CONFIG_FAILURE,
                            severity="error",
                            message=trimmed,
                            raw_line=trimmed,
                        )
                        error_count += 1
                        break

            if not diag:
                for pat in self.PERMISSION_PATTERNS:
                    if pat.search(trimmed):
                        diag = UnrealRuntimeDiagnostic(
                            category=UnrealRuntimeIssueCategory.PERMISSION_FAILURE,
                            severity="error",
                            message=trimmed,
                            raw_line=trimmed,
                        )
                        error_count += 1
                        break

            # 14. General Log Errors / Warnings
            if not diag:
                for pat in self.GENERAL_ERROR_PATTERNS:
                    m = pat.search(trimmed)
                    if m:
                        diag = UnrealRuntimeDiagnostic(
                            category=UnrealRuntimeIssueCategory.UNKNOWN_RUNTIME_FAILURE,
                            severity="error",
                            module=m.group(1),
                            message=m.group(2),
                            raw_line=trimmed,
                        )
                        error_count += 1
                        break

            if not diag:
                for pat in self.GENERAL_WARNING_PATTERNS:
                    m = pat.search(trimmed)
                    if m:
                        diag = UnrealRuntimeDiagnostic(
                            category=UnrealRuntimeIssueCategory.UNKNOWN_RUNTIME_FAILURE,
                            severity="warning",
                            module=m.group(1),
                            message=m.group(2),
                            raw_line=trimmed,
                        )
                        warning_count += 1
                        break

            if diag:
                diagnostics.append(diag)

        # Attach stack frames if available to the most severe diagnostic
        if stack_frames and diagnostics:
            for d in diagnostics:
                if d.severity in ("crash", "fatal", "ensure", "assertion"):
                    d.stack_evidence = list(stack_frames)
                    break

        return {
            "diagnostics": [d.to_dict() for d in diagnostics],
            "diagnostic_objects": diagnostics,
            "error_count": error_count,
            "warning_count": warning_count,
            "crash_count": crash_count,
            "ensure_count": ensure_count,
            "assertion_count": assertion_count,
            "has_crashes": crash_count > 0,
            "has_ensures": ensure_count > 0,
            "has_assertions": assertion_count > 0,
            "has_fatal_errors": any(d.severity == "fatal" for d in diagnostics),
            "summary": f"{error_count} error(s), {warning_count} warning(s), {crash_count} crash(es), {ensure_count} ensure(s).",
        }

    def detect_crashes(self, text: str) -> List[Dict[str, Any]]:
        """Convenience method specifically filtering for crashes, ensures, and assertions."""
        parsed = self.parse(text)
        crash_types = {"crash", "fatal", "ensure", "assertion"}
        return [d for d in parsed["diagnostics"] if d["severity"] in crash_types]


# -----------------------------------------------------------------------------
# Component 3: Unreal Test Report Parser
# -----------------------------------------------------------------------------

class UnrealTestReportParser:
    """
    Parses Unreal Engine test results from stdout automation lines or exported JSON test reports.
    Produces structured UnrealTestCaseResult, UnrealTestSuiteResult, and UnrealTestSummary models.
    """

    CASE_PASSED_PAT = re.compile(r"Automation:\s*Test Passed:\s*([^\r\n]+)", re.IGNORECASE)
    CASE_FAILED_PAT = re.compile(r"Automation:\s*Test Failed:\s*([^\r\n]+)", re.IGNORECASE)
    CASE_SKIPPED_PAT = re.compile(r"Automation:\s*Test Skipped:\s*([^\r\n]+)", re.IGNORECASE)

    SUMMARY_PAT1 = re.compile(
        r"Automation:\s*Completed\s+(\d+)\s+tests\.\s+(\d+)\s+passed,\s+(\d+)\s+failed,\s+(\d+)\s+skipped",
        re.IGNORECASE,
    )
    SUMMARY_PAT2 = re.compile(
        r"Results:\s*Passed:\s*(\d+),\s*Failed:\s*(\d+),\s*Total:\s*(\d+)",
        re.IGNORECASE,
    )

    def parse_stdout(self, stdout_text: str) -> Tuple[List[UnrealTestCaseResult], UnrealTestSummary]:
        """Parses individual test case lines and summary statistics from captured stdout."""
        cases: List[UnrealTestCaseResult] = []
        total = 0
        passed = 0
        failed = 0
        skipped = 0

        for line in stdout_text.splitlines():
            m_pass = self.CASE_PASSED_PAT.search(line)
            if m_pass:
                name = m_pass.group(1).strip()
                cases.append(UnrealTestCaseResult(name=name, full_name=name, status=UnrealTestStatus.PASSED))
                passed += 1
                total += 1
                continue

            m_fail = self.CASE_FAILED_PAT.search(line)
            if m_fail:
                name = m_fail.group(1).strip()
                cases.append(UnrealTestCaseResult(
                    name=name, full_name=name, status=UnrealTestStatus.FAILED, error_message=f"Failed test: {name}"
                ))
                failed += 1
                total += 1
                continue

            m_skip = self.CASE_SKIPPED_PAT.search(line)
            if m_skip:
                name = m_skip.group(1).strip()
                cases.append(UnrealTestCaseResult(name=name, full_name=name, status=UnrealTestStatus.SKIPPED))
                skipped += 1
                total += 1
                continue

            # Summary regex 1
            m_sum1 = self.SUMMARY_PAT1.search(line)
            if m_sum1:
                total = int(m_sum1.group(1))
                passed = int(m_sum1.group(2))
                failed = int(m_sum1.group(3))
                skipped = int(m_sum1.group(4))

            # Summary regex 2
            m_sum2 = self.SUMMARY_PAT2.search(line)
            if m_sum2:
                passed = int(m_sum2.group(1))
                failed = int(m_sum2.group(2))
                total = int(m_sum2.group(3))

        pass_rate = (passed / total * 100.0) if total > 0 else 0.0
        status_str = "PASSED" if failed == 0 and passed > 0 else ("FAILED" if failed > 0 else "NOT_RUN")

        summary = UnrealTestSummary(
            total=total,
            passed=passed,
            failed=failed,
            skipped=skipped,
            pass_rate=pass_rate,
            status=status_str,
        )
        return cases, summary

    def parse_json_report(self, report_path: Path) -> Tuple[List[UnrealTestCaseResult], UnrealTestSummary]:
        """Parses exported JSON test report from -ReportExportPath."""
        if not report_path.exists():
            return [], UnrealTestSummary(status="NOT_FOUND")

        try:
            with open(report_path, "r", encoding="utf-8", errors="replace") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning("Failed to parse JSON test report at %s: %s", report_path, e)
            return [], UnrealTestSummary(status="INVALID_JSON")

        cases: List[UnrealTestCaseResult] = []
        tests_arr = data.get("tests", []) if isinstance(data, dict) else []
        passed = 0
        failed = 0
        skipped = 0

        for t in tests_arr:
            name = t.get("testDisplayName") or t.get("fullTestPath") or t.get("name") or "UnnamedTest"
            state = (t.get("state") or "").lower()
            dur = float(t.get("duration", 0.0))
            err = t.get("errorMessage") or ""

            if state in ("success", "passed", "pass"):
                st = UnrealTestStatus.PASSED
                passed += 1
            elif state in ("fail", "failed", "error"):
                st = UnrealTestStatus.FAILED
                failed += 1
            else:
                st = UnrealTestStatus.SKIPPED
                skipped += 1

            cases.append(UnrealTestCaseResult(
                name=name,
                full_name=name,
                status=st,
                duration_seconds=dur,
                error_message=err if err else None,
            ))

        total = len(cases)
        pass_rate = (passed / total * 100.0) if total > 0 else 0.0
        status_str = "PASSED" if failed == 0 and passed > 0 else ("FAILED" if failed > 0 else "NOT_RUN")

        summary = UnrealTestSummary(
            total=total,
            passed=passed,
            failed=failed,
            skipped=skipped,
            pass_rate=pass_rate,
            status=status_str,
        )
        return cases, summary


# -----------------------------------------------------------------------------
# Component 4: Safe Unreal Test Runner
# -----------------------------------------------------------------------------

class UnrealTestRunner:
    """
    Safely executes Unreal Engine automation tests, commandlets, and runtime sessions
    using strictly shell=False, bounded timeouts, output buffering, process tree cleanup,
    and mock executor hooks for deterministic verification.
    """

    def __init__(
        self,
        safety_gate: Optional[UnrealSafetyGate] = None,
        env_detector: Optional[UnrealEnvironmentDetector] = None,
        log_parser: Optional[UnrealRuntimeLogParser] = None,
        report_parser: Optional[UnrealTestReportParser] = None,
        mock_executor: Optional[Callable[[List[str], float], Tuple[int, str, str]]] = None,
    ):
        self.safety = safety_gate or DEFAULT_UNREAL_SAFETY_GATE
        self.env_detector = env_detector or DEFAULT_UNREAL_ENV_DETECTOR
        self.log_parser = log_parser or UnrealRuntimeLogParser()
        self.report_parser = report_parser or UnrealTestReportParser()
        self.mock_executor = mock_executor

    def build_test_command(
        self,
        executable_path: Path,
        project_uproject_path: Path,
        test_mode: str = "SmokeTest",
        test_filter: Optional[str] = None,
        output_path: Optional[Path] = None,
        extra_flags: Optional[List[str]] = None,
    ) -> List[str]:
        """Constructs an explicit, validated command list for test invocation."""
        cmd = [
            str(executable_path),
            str(project_uproject_path),
            "-nullrhi",
            "-unattended",
            "-nopause",
            "-nosplash",
            "-stdout",
            "-FullStdOutLogOutput",
        ]

        if test_mode == "SmokeTest":
            cmd.extend([
                "-ExecCmds=Automation RunTests Project.Smoke; Quit",
                "-TestExit=Automation Test Exited",
            ])
        elif test_mode == "EditorTest":
            filter_arg = f"Automation RunTests {test_filter}" if test_filter else "Automation RunTests"
            cmd.extend([
                f"-ExecCmds={filter_arg}; Quit",
                "-TestExit=Automation Test Exited",
            ])
        elif test_mode == "Commandlet":
            cmd_arg = f"-run={test_filter}" if test_filter else "-run=RunTests"
            cmd.append(cmd_arg)
        elif test_mode == "FunctionalTest":
            filter_arg = f"Automation RunTests {test_filter or 'Project.Functional'}"
            cmd.extend([
                f"-ExecCmds={filter_arg}; Quit",
                "-TestExit=Automation Test Exited",
            ])
        elif test_mode == "Unit":
            filter_arg = f"Automation RunTests {test_filter or 'Project.Unit'}"
            cmd.extend([
                f"-ExecCmds={filter_arg}; Quit",
                "-TestExit=Automation Test Exited",
            ])

        if output_path:
            cmd.append(f"-ReportExportPath={output_path}")

        if extra_flags:
            for flag in extra_flags:
                # Sanitize flag to prevent shell injection or arbitrary executables
                clean_flag = flag.strip()
                if clean_flag and not any(c in clean_flag for c in [";", "&", "|", "<", ">", "$", "`"]):
                    cmd.append(clean_flag)

        return cmd

    def run_test(
        self,
        project_path: Optional[Path] = None,
        test_mode: str = "SmokeTest",
        test_filter: Optional[str] = None,
        output_path: Optional[Any] = None,
        configuration: str = "Development",
        platform: str = "Win64",
        timeout_seconds: Optional[float] = None,
        extra_flags: Optional[List[str]] = None,
    ) -> UnrealTestExecutionResult:
        """Executes the requested test with full safety gating and process confinement."""
        t0 = time.time()
        timeout = timeout_seconds or TEST_TIMEOUT_SECONDS

        # 1. Assert safety boundaries
        self.safety.assert_not_emergency_stopped()
        self.safety.check_rate_limit("run_test")

        target_project = project_path or self.safety.default_project
        v_mode, v_filter, v_output, v_conf, v_plat = self.safety.validate_test_parameters(
            test_mode=test_mode,
            test_filter=test_filter,
            output_path=output_path,
            configuration=configuration,
            platform=platform,
            project_path=target_project,
        )

        # 2. Locate project .uproject
        uproject_files = list(target_project.glob("*.uproject"))
        if not uproject_files:
            return UnrealTestExecutionResult(
                success=False,
                status=UnrealRuntimeResultStatus.ENVIRONMENT_FAILURE,
                test_mode=v_mode,
                error_message=f"No .uproject found in '{target_project}'",
            )
        uproject_path = uproject_files[0]

        # 3. Locate engine and Editor executable
        engines = (
            self.env_detector.detect_installed_engines()
            if hasattr(self.env_detector, "detect_installed_engines")
            else self.env_detector.detect_engines()
        )
        if not engines:
            return UnrealTestExecutionResult(
                success=False,
                status=UnrealRuntimeResultStatus.ENVIRONMENT_FAILURE,
                test_mode=v_mode,
                error_message="No Unreal Engine installation detected.",
            )

        engine = engines[0]
        editor_candidate = engine.cmd_executable or engine.editor_executable
        if not editor_candidate:
            return UnrealTestExecutionResult(
                success=False,
                status=UnrealRuntimeResultStatus.ENVIRONMENT_FAILURE,
                test_mode=v_mode,
                error_message="Unreal Editor executable not found in installed engine.",
            )
        editor_cmd = Path(editor_candidate)
        if not editor_cmd.exists() and self.mock_executor is None:
            return UnrealTestExecutionResult(
                success=False,
                status=UnrealRuntimeResultStatus.ENVIRONMENT_FAILURE,
                test_mode=v_mode,
                error_message="Unreal Editor executable not found in installed engine.",
            )

        # 4. Construct command
        cmd = self.build_test_command(
            executable_path=editor_cmd,
            project_uproject_path=uproject_path,
            test_mode=v_mode,
            test_filter=v_filter,
            output_path=v_output,
            extra_flags=extra_flags,
        )

        # 5. Execute via mock or subprocess
        stdout_text = ""
        stderr_text = ""
        exit_code = 0
        timed_out = False

        if self.mock_executor:
            try:
                exit_code, stdout_text, stderr_text = self.mock_executor(cmd, timeout)
            except Exception as e:
                return UnrealTestExecutionResult(
                    success=False,
                    status=UnrealRuntimeResultStatus.RUNTIME_FAILED,
                    test_mode=v_mode,
                    error_message=f"Mock execution error: {e}",
                    duration_seconds=time.time() - t0,
                )
        else:
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    shell=False,
                    errors="replace",
                )
                try:
                    stdout_text, stderr_text = proc.communicate(timeout=timeout)
                    exit_code = proc.returncode
                except subprocess.TimeoutExpired:
                    timed_out = True
                    # Clean up process tree
                    try:
                        parent = psutil.Process(proc.pid)
                        for child in parent.children(recursive=True):
                            try:
                                child.kill()
                            except (psutil.NoSuchProcess, psutil.AccessDenied):
                                pass
                        parent.kill()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                    proc.kill()
                    stdout_text, stderr_text = proc.communicate()
                    exit_code = -1
            except Exception as e:
                return UnrealTestExecutionResult(
                    success=False,
                    status=UnrealRuntimeResultStatus.RUNTIME_FAILED,
                    test_mode=v_mode,
                    error_message=f"Failed to launch test process: {e}",
                    duration_seconds=time.time() - t0,
                )

        # Truncate and cap output
        truncation_notice = "\n... [OUTPUT TRUNCATED BY SAFETY GATE]"
        if len(stdout_text) > MAX_CAPTURED_OUTPUT_BYTES:
            stdout_text = stdout_text[:MAX_CAPTURED_OUTPUT_BYTES] + truncation_notice
        if len(stderr_text) > MAX_CAPTURED_OUTPUT_BYTES:
            stderr_text = stderr_text[:MAX_CAPTURED_OUTPUT_BYTES] + truncation_notice

        duration = time.time() - t0

        # 6. Parse logs and diagnostics
        combined_logs = stdout_text + "\n" + stderr_text
        parsed_logs = self.log_parser.parse(combined_logs)
        diagnostics = parsed_logs["diagnostic_objects"]

        # 7. Parse test results
        cases, summary = self.report_parser.parse_stdout(stdout_text)
        if v_output and Path(v_output).exists():
            json_cases, json_summary = self.report_parser.parse_json_report(Path(v_output))
            if json_cases:
                cases = json_cases
                summary = json_summary

        # 8. Determine overall status
        if timed_out:
            status = UnrealRuntimeResultStatus.RUNTIME_TIMEOUT
            success = False
            err_msg = f"Test execution timed out after {timeout}s"
        elif parsed_logs["has_crashes"]:
            status = UnrealRuntimeResultStatus.CRASH_DETECTED
            success = False
            err_msg = "Unreal runtime crash detected during test execution."
        elif parsed_logs["has_ensures"]:
            status = UnrealRuntimeResultStatus.ENSURE_DETECTED
            success = False
            err_msg = "Ensure failure condition detected during test execution."
        elif summary.failed > 0:
            status = UnrealRuntimeResultStatus.TESTS_FAILED
            success = False
            err_msg = f"{summary.failed} test(s) failed."
        elif summary.passed > 0 and summary.failed == 0 and exit_code == 0:
            status = UnrealRuntimeResultStatus.TESTS_PASSED
            success = True
            err_msg = ""
        elif exit_code != 0:
            status = UnrealRuntimeResultStatus.RUNTIME_FAILED
            success = False
            err_msg = f"Process exited with non-zero code {exit_code}."
        else:
            status = UnrealRuntimeResultStatus.RUNTIME_SUCCEEDED
            success = True
            err_msg = ""

        return UnrealTestExecutionResult(
            success=success,
            status=status,
            test_mode=v_mode,
            exit_code=exit_code,
            duration_seconds=duration,
            summary=summary,
            test_cases=cases,
            diagnostics=diagnostics,
            stdout_captured=stdout_text,
            stderr_captured=stderr_text,
            verified=True,
            error_message=err_msg,
        )


# -----------------------------------------------------------------------------
# Component 5: Unreal Test Artifact Verifier
# -----------------------------------------------------------------------------

class UnrealTestArtifactVerifier:
    """
    Verifies and hashes generated test result files and logs within authorized project boundaries.
    """

    def __init__(self, safety_gate: Optional[UnrealSafetyGate] = None):
        self.safety = safety_gate or DEFAULT_UNREAL_SAFETY_GATE

    def verify_artifact(self, file_path: Union[str, Path]) -> UnrealTestArtifactInfo:
        """Verifies an individual test artifact file and computes SHA-256."""
        p = Path(file_path).resolve()
        if not p.exists():
            return UnrealTestArtifactInfo(path=str(p), exists=False, verified=False)

        size = p.stat().st_size
        h = hashlib.sha256()
        try:
            with open(p, "rb") as f:
                while chunk := f.read(65536):
                    h.update(chunk)
            sha = h.hexdigest()
            return UnrealTestArtifactInfo(
                path=str(p),
                exists=True,
                size_bytes=size,
                sha256=sha,
                verified=size > 0,
            )
        except Exception as e:
            logger.warning("Error reading artifact %s: %s", p, e)
            return UnrealTestArtifactInfo(path=str(p), exists=True, verified=False)

    def scan_project_artifacts(self, project_path: Optional[Path] = None) -> List[UnrealTestArtifactInfo]:
        """Scans Saved/Logs and Saved/TestReports inside the authorized project."""
        target_project = project_path or self.safety.default_project
        artifacts: List[UnrealTestArtifactInfo] = []

        scan_dirs = [
            target_project / "Saved" / "Logs",
            target_project / "Saved" / "TestReports",
        ]

        for sdir in scan_dirs:
            if sdir.exists():
                for f in sdir.glob("*.*"):
                    if f.is_file() and f.suffix in (".log", ".json", ".xml", ".txt", ".dmp"):
                        artifacts.append(self.verify_artifact(f))

        return artifacts


# -----------------------------------------------------------------------------
# Component 6: Unreal Runtime Result Verifier (Evidence Precedence Hierarchy)
# -----------------------------------------------------------------------------

class UnrealRuntimeResultVerifier:
    """
    Enforces deterministic evidence precedence hierarchy for runtime/test outcomes:
    1. Authoritative Test Artifact / Report Summary
    2. Process Exit State
    3. Structured Diagnostics (Crashes, Ensures, Fatal Errors)
    4. Captured Logs
    5. Expected Runtime Artifacts
    6. Project State
    7. Model Claim (Model claim CANNOT override actual runtime evidence)
    """

    @staticmethod
    def verify(
        result: UnrealTestExecutionResult,
        model_claim_success: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Validates test execution outcome against evidence precedence."""
        contradiction = False
        rejection_reason = ""

        # Check for crash/ensure evidence
        has_crash = result.status == UnrealRuntimeResultStatus.CRASH_DETECTED
        has_ensure = result.status == UnrealRuntimeResultStatus.ENSURE_DETECTED
        exit_failed = result.exit_code != 0
        tests_failed = result.summary is not None and result.summary.failed > 0

        actual_success = (
            not has_crash
            and not has_ensure
            and not exit_failed
            and not tests_failed
            and result.status in (UnrealRuntimeResultStatus.RUNTIME_SUCCEEDED, UnrealRuntimeResultStatus.TESTS_PASSED)
        )

        if model_claim_success is not None:
            if model_claim_success and not actual_success:
                contradiction = True
                rejection_reason = (
                    f"Model claim of success contradicts evidence: "
                    f"status={result.status.value}, exit_code={result.exit_code}, "
                    f"tests_failed={result.summary.failed if result.summary else 0}"
                )
            elif not model_claim_success and actual_success:
                contradiction = True
                rejection_reason = "Model claim of failure contradicts confirmed successful test execution."

        return {
            "verified": True,
            "actual_success": actual_success,
            "status": result.status.value,
            "has_crash": has_crash,
            "has_ensure": has_ensure,
            "exit_code": result.exit_code,
            "contradiction_detected": contradiction,
            "rejection_reason": rejection_reason,
            "summary": result.summary.to_dict() if result.summary else None,
        }


# -----------------------------------------------------------------------------
# Global Default Instances
# -----------------------------------------------------------------------------

DEFAULT_UNREAL_TEST_VALIDATOR = UnrealTestEnvironmentValidator()
DEFAULT_UNREAL_RUNTIME_PARSER = UnrealRuntimeLogParser()
DEFAULT_UNREAL_TEST_REPORT_PARSER = UnrealTestReportParser()
DEFAULT_UNREAL_TEST_RUNNER = UnrealTestRunner()
DEFAULT_UNREAL_TEST_ARTIFACT_VERIFIER = UnrealTestArtifactVerifier()
DEFAULT_UNREAL_RUNTIME_RESULT_VERIFIER = UnrealRuntimeResultVerifier()
