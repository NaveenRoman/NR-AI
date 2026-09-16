r"""
NR-AI Unreal Engine Agent Safety Gate & Policy Enforcement (Step 9 Phase 1).

Strict deterministic safety enforcement for Unreal Engine projects:
- Explicit tool allowlist (ALLOWED_UNREAL_TOOLS)
- Strict workspace boundary confinement (C:/NR-AI and authorized projects)
- Path traversal & protected file/directory protection
- Thread-safe emergency stop freeze
- Bounded rates, edit bounds, and file size limits
- Sensitive data redaction (API keys, secrets, tokens, credentials)
- Zero advisory model tool authority & 100% shell=False subprocess isolation
"""

from dataclasses import dataclass, field
from enum import Enum
import logging
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union

logger = logging.getLogger("NRAI.UnrealSafety")


# -----------------------------------------------------------------------------
# Error Codes & Exceptions
# -----------------------------------------------------------------------------

class UnrealErrorCode(str, Enum):
    PROJECT_NOT_AUTHORIZED = "PROJECT_NOT_AUTHORIZED"
    FILE_NOT_AUTHORIZED = "FILE_NOT_AUTHORIZED"
    PATH_TRAVERSAL_DETECTED = "PATH_TRAVERSAL_DETECTED"
    PROTECTED_FILE_REJECTED = "PROTECTED_FILE_REJECTED"
    PROTECTED_DIRECTORY_REJECTED = "PROTECTED_DIRECTORY_REJECTED"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    PATCH_TOO_LARGE = "PATCH_TOO_LARGE"
    TOO_MANY_FILES_CHANGED = "TOO_MANY_FILES_CHANGED"
    TOO_MANY_LINES_CHANGED = "TOO_MANY_LINES_CHANGED"
    TOOL_NOT_ALLOWED = "TOOL_NOT_ALLOWED"
    ACTION_NOT_ALLOWED = "ACTION_NOT_ALLOWED"
    EMERGENCY_STOPPED = "EMERGENCY_STOPPED"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    UNREAL_NOT_FOUND = "UNREAL_NOT_FOUND"
    EDITOR_NOT_RUNNING = "EDITOR_NOT_RUNNING"
    PROJECT_INVALID = "PROJECT_INVALID"
    INVALID_UPROJECT = "INVALID_UPROJECT"
    INVALID_UPLUGIN = "INVALID_UPLUGIN"
    ASSET_NOT_FOUND = "ASSET_NOT_FOUND"
    SOURCE_NOT_FOUND = "SOURCE_NOT_FOUND"
    BUILD_FAILED = "BUILD_FAILED"
    COMPILATION_FAILED = "COMPILATION_FAILED"
    TEST_FAILED = "TEST_FAILED"
    BUILD_TIMEOUT = "BUILD_TIMEOUT"
    TEST_TIMEOUT = "TEST_TIMEOUT"
    STALE_TARGET = "STALE_TARGET"
    INVALID_BUILD_TARGET = "INVALID_BUILD_TARGET"
    INVALID_BUILD_CONFIGURATION = "INVALID_BUILD_CONFIGURATION"
    INVALID_BUILD_PLATFORM = "INVALID_BUILD_PLATFORM"
    INVALID_BUILD_OUTPUT = "INVALID_BUILD_OUTPUT"
    BUILD_ARTIFACT_MISSING = "BUILD_ARTIFACT_MISSING"
    DOTNET_RUNTIME_MISSING = "DOTNET_RUNTIME_MISSING"
    BUILD_ENVIRONMENT_FAILURE = "BUILD_ENVIRONMENT_FAILURE"
    INVALID_TEST_MODE = "INVALID_TEST_MODE"
    INVALID_TEST_FILTER = "INVALID_TEST_FILTER"
    INVALID_TEST_OUTPUT = "INVALID_TEST_OUTPUT"
    TEST_ARTIFACT_MISSING = "TEST_ARTIFACT_MISSING"
    TEST_RUNNER_ERROR = "TEST_RUNNER_ERROR"
    TEST_PARSER_ERROR = "TEST_PARSER_ERROR"
    EDIT_VALIDATION_FAILED = "EDIT_VALIDATION_FAILED"
    REPAIR_FAILED = "REPAIR_FAILED"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"
    REPAIR_UNSUPPORTED = "REPAIR_UNSUPPORTED"
    MALFORMED_PROPOSAL = "MALFORMED_PROPOSAL"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    AST_INTEGRITY_FAILED = "AST_INTEGRITY_FAILED"
    REPAIR_ATTEMPTS_EXCEEDED = "REPAIR_ATTEMPTS_EXCEEDED"
    UNREPAIRABLE_FAILURE = "UNREPAIRABLE_FAILURE"
    REPAIR_LOOP_EXHAUSTED = "REPAIR_LOOP_EXHAUSTED"
    EVIDENCE_VERIFICATION_FAILED = "EVIDENCE_VERIFICATION_FAILED"
    WORKFLOW_CANCELLED = "WORKFLOW_CANCELLED"
    INVALID_PARAMETER = "INVALID_PARAMETER"
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    SYMBOL_NOT_FOUND = "SYMBOL_NOT_FOUND"
    AMBIGUOUS_TARGET = "AMBIGUOUS_TARGET"
    INVALID_OPERATION = "INVALID_OPERATION"
    INVALID_PROPOSAL = "INVALID_PROPOSAL"
    PROHIBITED_TOKEN = "PROHIBITED_TOKEN"
    SENSITIVE_CONTENT = "SENSITIVE_CONTENT"
    STRUCTURE_VALIDATION_FAILED = "STRUCTURE_VALIDATION_FAILED"
    WRITE_FAILED = "WRITE_FAILED"
    ATOMIC_REPLACE_FAILED = "ATOMIC_REPLACE_FAILED"
    HASH_VERIFICATION_FAILED = "HASH_VERIFICATION_FAILED"
    MODEL_PROPOSAL_REJECTED = "MODEL_PROPOSAL_REJECTED"
    WORKFLOW_STEP_LIMIT_EXCEEDED = "WORKFLOW_STEP_LIMIT_EXCEEDED"
    INVALID_WORKFLOW = "INVALID_WORKFLOW"
    WORKFLOW_NOT_FOUND = "WORKFLOW_NOT_FOUND"
    WORKFLOW_FAILED = "WORKFLOW_FAILED"
    WORKFLOW_TIMEOUT = "WORKFLOW_TIMEOUT"
    PLAN_VALIDATION_FAILED = "PLAN_VALIDATION_FAILED"


class UnrealSafetyError(Exception):
    """Raised when an Unreal safety boundary or policy is violated."""
    def __init__(self, code: UnrealErrorCode, message: str, details: Optional[Dict[str, Any]] = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(f"[{code.value}] {message}")

    @property
    def error_code(self) -> UnrealErrorCode:
        return self.code


class EmergencyStopActiveError(UnrealSafetyError):
    """Raised when an operation is attempted while emergency stop is active."""
    def __init__(self, message: str = "EMERGENCY STOP is active. All Unreal operations frozen."):
        super().__init__(UnrealErrorCode.EMERGENCY_STOPPED, message)


# -----------------------------------------------------------------------------
# Allowlist Constants & Boundaries
# -----------------------------------------------------------------------------

GLOBAL_WORKSPACE_ROOT = Path("C:/NR-AI").resolve()
DEFAULT_AUTHORIZED_PROJECT = (GLOBAL_WORKSPACE_ROOT / "nr_unreal_test").resolve()

ALLOWED_UNREAL_EXTENSIONS: Set[str] = {
    ".uproject",
    ".uplugin",
    ".cpp",
    ".h",
    ".hpp",
    ".c",
    ".cs",
    ".ini",
    ".json",
    ".txt",
    ".md",
    ".xml",
    ".uasset",
    ".umap",
    ".target.cs",
    ".build.cs",
}

PROTECTED_UNREAL_EXTENSIONS: Set[str] = {
    ".key",
    ".keystore",
    ".env",
    ".exe",
    ".dll",
    ".pdb",
    ".so",
    ".dylib",
    ".pfx",
    ".snk",
    ".p12",
    ".sln",
    ".userprefs",
    ".vcxproj",
}

PROTECTED_UNREAL_FILES: Set[str] = {
    "Build.version",
}

PROTECTED_UNREAL_DIRECTORIES: Set[str] = {
    "Binaries",
    "Intermediate",
    "Saved",
    "DerivedDataCache",
    "Build",
    ".vs",
    ".git",
}

ALLOWED_UNREAL_PHASE1_TOOLS: Set[str] = {
    "unreal.detect_environment",
    "unreal.list_installations",
    "unreal.inspect_project",
    "unreal.validate_project_path",
    "unreal.parse_uproject",
    "unreal.inspect_modules",
    "unreal.inspect_plugins",
    "unreal.inspect_project_structure",
    "unreal.validate_engine_association",
    "unreal.inspect_config",
    "unreal.list_assets",
    "unreal.read_source_file",
}

ALLOWED_UNREAL_PHASE2_TOOLS: Set[str] = {
    "unreal.validate_build_environment",
    "unreal.validate_build_target",
    "unreal.build_project",
    "unreal.parse_build_diagnostics",
    "unreal.verify_build_result",
    "unreal.inspect_build_artifacts",
    "unreal.get_supported_targets",
    "unreal.diagnose_build_failure",
}

ALLOWED_UNREAL_PHASE3_TOOLS: Set[str] = {
    "unreal.validate_test_environment",
    "unreal.validate_test_mode",
    "unreal.run_test",
    "unreal.capture_runtime_logs",
    "unreal.parse_runtime_logs",
    "unreal.detect_runtime_crashes",
    "unreal.parse_test_results",
    "unreal.verify_runtime_state",
    "unreal.inspect_test_artifacts",
    "unreal.diagnose_runtime_failure",
}

ALLOWED_UNREAL_PHASE4_TOOLS: Set[str] = {
    "unreal.inspect_cpp_source",
    "unreal.list_cpp_symbols",
    "unreal.inspect_cpp_class",
    "unreal.inspect_cpp_method",
    "unreal.find_cpp_symbol",
    "unreal.inspect_reflection_metadata",
    "unreal.inspect_inheritance",
    "unreal.list_blueprint_assets",
    "unreal.inspect_blueprint_metadata",
    "unreal.validate_cpp_change",
    "unreal.propose_cpp_change",
    "unreal.apply_cpp_change",
    "unreal.rollback_cpp_change",
    "unreal.verify_source_change",
}

ALLOWED_UNREAL_PHASE5_TOOLS: Set[str] = {
    "unreal.create_repair_workflow",
    "unreal.inspect_failure",
    "unreal.diagnose_failure",
    "unreal.plan_repair",
    "unreal.validate_repair_plan",
    "unreal.execute_repair_workflow",
    "unreal.verify_repair_workflow",
    "unreal.rollback_repair_workflow",
    "unreal.stop_repair_workflow",
    "unreal.get_workflow_status",
}

ALLOWED_UNREAL_PHASE4_ALL_TOOLS: Set[str] = (
    ALLOWED_UNREAL_PHASE1_TOOLS
    | ALLOWED_UNREAL_PHASE2_TOOLS
    | ALLOWED_UNREAL_PHASE3_TOOLS
    | ALLOWED_UNREAL_PHASE4_TOOLS
)

ALL_ALLOWED_UNREAL_TOOLS: Set[str] = (
    ALLOWED_UNREAL_PHASE4_ALL_TOOLS
    | ALLOWED_UNREAL_PHASE5_TOOLS
)
ALLOWED_UNREAL_TOOLS: Set[str] = ALL_ALLOWED_UNREAL_TOOLS

# Supported Unreal Source Extensions & Modification Limits
ALLOWED_UNREAL_SOURCE_EXTENSIONS: Set[str] = {".h", ".hpp", ".cpp", ".inl"}
ALLOWED_UNREAL_MODIFICATION_OPERATIONS: Set[str] = {
    "ADD_INCLUDE",
    "REMOVE_INCLUDE",
    "ADD_METHOD",
    "REPLACE_METHOD_BODY",
    "REPLACE_SOURCE_RANGE",
    "ADD_MEMBER_PROPERTY",
    "ADD_ENUM_ENTRY",
}
MAX_SOURCE_FILE_BYTES: int = 1_000_000
MAX_PATCH_BYTES: int = 100_000
MAX_CHANGED_LINES: int = 500
MAX_FILES_PER_OPERATION: int = 5
MAX_REPAIR_ATTEMPTS: int = 2

# Prohibited tokens in source proposals to prevent code execution injection
PROHIBITED_SOURCE_TOKENS: Set[str] = {
    "cmd.exe",
    "powershell",
    "pwsh",
    "bash",
    "sh",
    "/bin/sh",
    "subprocess",
    "os.system",
    "eval(",
    "exec(",
    "__import__",
    "Invoke-Expression",
    "rundll32",
    "certutil",
    "nc.exe",
    "curl",
    "wget",
}

# Supported Unreal Build Targets & Platforms
ALLOWED_UNREAL_CONFIGURATIONS: Set[str] = {"Development", "DebugGame", "Shipping"}
ALLOWED_UNREAL_TARGET_TYPES: Set[str] = {"Editor", "Game"}
ALLOWED_UNREAL_PLATFORMS: Set[str] = {"Win64"}

# Supported Unreal Test Modes & Executable Classes
ALLOWED_UNREAL_TEST_MODES: Set[str] = {"SmokeTest", "EditorTest", "Commandlet", "FunctionalTest", "Unit"}
ALLOWED_UNREAL_TEST_PLATFORMS: Set[str] = {"Win64"}
ALLOWED_UNREAL_TEST_CONFIGURATIONS: Set[str] = {"Development", "DebugGame", "Shipping"}
ALLOWED_UNREAL_EXECUTABLE_NAMES: Set[str] = {
    "UnrealEditor-Cmd.exe",
    "UnrealEditor.exe",
    "UnrealBuildTool.exe",
    "RunUAT.bat",
}

# Timeout and operational limits
BUILD_TIMEOUT_SECONDS: float = 300.0
COMPILE_TIMEOUT_SECONDS: float = 120.0
TEST_TIMEOUT_SECONDS: float = 180.0
MAX_READ_LINES: int = 1000
MAX_READ_BYTES: int = 500_000
MAX_CAPTURED_OUTPUT_BYTES: int = 500_000
MAX_FILE_SIZE_BYTES: int = 50_000_000
MAX_TEST_LOG_BYTES: int = 500_000
MAX_TEST_RESULT_BYTES: int = 10_000_000
WORKFLOW_TIMEOUT_SECONDS: float = 300.0
MAX_WORKFLOW_STEPS: int = 25

# Sensitive token patterns for automatic masking
SENSITIVE_PATTERNS = [
    re.compile(r"(api[_-]?key\s*[:=]\s*['\"]?)([A-Za-z0-9_\-]{8,})(['\"]?)", re.IGNORECASE),
    re.compile(r"(bearer\s+)([A-Za-z0-9_\-\.]{16,})", re.IGNORECASE),
    re.compile(r"(password\s*[:=]\s*['\"]?)([^\s'\"]{4,})(['\"]?)", re.IGNORECASE),
    re.compile(r"(secret\s*[:=]\s*['\"]?)([A-Za-z0-9_\-]{8,})(['\"]?)", re.IGNORECASE),
    re.compile(r"(token\s*[:=]\s*['\"]?)([A-Za-z0-9_\-]{8,})(['\"]?)", re.IGNORECASE),
    re.compile(r"(epic[_-]?account\s*[:=]\s*['\"]?)([^\s'\"]+)(['\"]?)", re.IGNORECASE),
]


def redact_sensitive_data(text: str) -> str:
    """Masks secrets, tokens, passwords, and sensitive keys from log/error output."""
    if not text:
        return text
    sanitized = text
    for pat in SENSITIVE_PATTERNS:
        sanitized = pat.sub(r"\1***REDACTED***\3" if pat.groups >= 3 else r"\1***REDACTED***", sanitized)
    return sanitized


# -----------------------------------------------------------------------------
# Unreal Safety Gate
# -----------------------------------------------------------------------------

class UnrealSafetyGate:
    """
    Enforces security boundaries, path authorization, rate limiting,
    and emergency stop freezes for all Unreal Agent operations.
    """

    def __init__(
        self,
        workspace_root: Optional[Path] = None,
        authorized_project: Optional[Path] = None,
        authorized_projects: Optional[List[Path]] = None,
        rate_limit_calls_per_minute: int = 240,
    ):
        self.workspace_root = (workspace_root or GLOBAL_WORKSPACE_ROOT).resolve()
        self.authorized_projects: List[Path] = [
            (p if isinstance(p, Path) else Path(p)).resolve()
            for p in (authorized_projects or [authorized_project or DEFAULT_AUTHORIZED_PROJECT])
        ]
        self.rate_limit_calls_per_minute = rate_limit_calls_per_minute
        self._call_timestamps: List[float] = []
        self._rate_lock = threading.Lock()
        self._emergency_stopped = False
        self._stop_lock = threading.Lock()

    # -------------------------------------------------------------------------
    # Emergency Stop Controls
    # -------------------------------------------------------------------------

    def trigger_emergency_stop(self, reason: str = "Operator or safety trigger") -> None:
        """Immediately halts all active Unreal operations and freezes the agent."""
        with self._stop_lock:
            self._emergency_stopped = True
            logger.critical(f"[UnrealSafety] EMERGENCY STOP ACTIVATED. Reason: {reason}")
            print(f"[UnrealSafety] EMERGENCY STOP ACTIVATED. [Reason: {reason}] All Unreal operations frozen.")

    def reset_emergency_stop(self) -> None:
        """Resets the emergency stop condition."""
        with self._stop_lock:
            self._emergency_stopped = False
            logger.info("[UnrealSafety] Emergency stop reset. Normal operations resumed.")

    def clear_emergency_stop(self) -> None:
        """Alias for reset_emergency_stop for API consistency."""
        self.reset_emergency_stop()

    def activate_emergency_stop(self, reason: str = "Operator or safety trigger") -> None:
        """Alias for trigger_emergency_stop for API consistency."""
        self.trigger_emergency_stop(reason)

    def is_emergency_stopped(self) -> bool:
        with self._stop_lock:
            return self._emergency_stopped

    def assert_not_emergency_stopped(self) -> None:
        """Raises EmergencyStopActiveError if emergency stop is currently active."""
        if self.is_emergency_stopped():
            raise EmergencyStopActiveError()

    # -------------------------------------------------------------------------
    # Rate Limiting
    # -------------------------------------------------------------------------

    def check_rate_limit(self, action: str = "") -> None:
        """Enforces sliding-window rate limiting on tool calls."""
        now = time.time()
        window_start = now - 60.0
        with self._rate_lock:
            self._call_timestamps = [t for t in self._call_timestamps if t > window_start]
            if len(self._call_timestamps) >= self.rate_limit_calls_per_minute:
                raise UnrealSafetyError(
                    UnrealErrorCode.RATE_LIMIT_EXCEEDED,
                    f"Rate limit exceeded: max {self.rate_limit_calls_per_minute} calls per minute.",
                    {"current_calls": len(self._call_timestamps), "window_seconds": 60.0},
                )
            self._call_timestamps.append(now)

    # -------------------------------------------------------------------------
    # Boundary & Path Validation
    # -------------------------------------------------------------------------

    def validate_tool_allowed(self, tool_name: str) -> None:
        """Asserts that the tool is explicitly approved in ALLOWED_UNREAL_TOOLS."""
        self.assert_not_emergency_stopped()
        if tool_name not in ALL_ALLOWED_UNREAL_TOOLS:
            raise UnrealSafetyError(
                UnrealErrorCode.TOOL_NOT_ALLOWED,
                f"Tool '{tool_name}' is not in the approved Unreal tool allowlist.",
                {"tool": tool_name, "allowed_tools": sorted(list(ALL_ALLOWED_UNREAL_TOOLS))},
            )

    def validate_project_path(self, project_path: str | Path) -> Path:
        """
        Validates that an Unreal project directory is authorized and within workspace bounds.
        Rejects traversal sequences, UNC paths, and unauthorized external paths.
        """
        self.assert_not_emergency_stopped()
        path_str = str(project_path)

        # Detect raw traversal tokens
        if ".." in path_str.replace("\\", "/").split("/"):
            raise UnrealSafetyError(
                UnrealErrorCode.PATH_TRAVERSAL_DETECTED,
                f"Path traversal sequence '..' detected in project path: {path_str}",
                {"path": path_str},
            )

        if path_str.startswith("\\\\") or path_str.startswith("//"):
            raise UnrealSafetyError(
                UnrealErrorCode.PATH_TRAVERSAL_DETECTED,
                f"UNC paths are prohibited: {path_str}",
                {"path": path_str},
            )

        resolved = Path(project_path).resolve()

        # Confinement to workspace root
        try:
            resolved.relative_to(self.workspace_root)
        except ValueError:
            raise UnrealSafetyError(
                UnrealErrorCode.PROJECT_NOT_AUTHORIZED,
                f"Project path '{resolved}' is outside the authorized workspace '{self.workspace_root}'.",
                {"path": str(resolved), "workspace_root": str(self.workspace_root)},
            )

        # Authorized project check
        is_authorized = any(
            resolved == auth or self._is_subpath(resolved, auth)
            for auth in self.authorized_projects
        )

        if not is_authorized:
            raise UnrealSafetyError(
                UnrealErrorCode.PROJECT_NOT_AUTHORIZED,
                f"Project '{resolved}' is not in the authorized project allowlist: {[str(p) for p in self.authorized_projects]}.",
                {"path": str(resolved), "authorized_projects": [str(p) for p in self.authorized_projects]},
            )

        return resolved

    def validate_file_path(self, file_path: str | Path, project_root: Optional[Path] = None) -> Path:
        """
        Validates that a file target is within an authorized project and permitted by safety rules.
        """
        self.assert_not_emergency_stopped()
        path_str = str(file_path)

        if ".." in path_str.replace("\\", "/").split("/"):
            raise UnrealSafetyError(
                UnrealErrorCode.PATH_TRAVERSAL_DETECTED,
                f"Path traversal sequence '..' detected in file path: {path_str}",
                {"path": path_str},
            )

        if path_str.startswith("\\\\") or path_str.startswith("//"):
            raise UnrealSafetyError(
                UnrealErrorCode.PATH_TRAVERSAL_DETECTED,
                f"UNC paths are prohibited: {path_str}",
                {"path": path_str},
            )

        resolved = Path(file_path).resolve()

        # Check against project root if provided, else check workspace root
        if project_root:
            proj_resolved = self.validate_project_path(project_root)
            try:
                resolved.relative_to(proj_resolved)
            except ValueError:
                raise UnrealSafetyError(
                    UnrealErrorCode.FILE_NOT_AUTHORIZED,
                    f"File '{resolved}' is outside authorized project '{proj_resolved}'.",
                    {"file": str(resolved), "project": str(proj_resolved)},
                )
        else:
            try:
                resolved.relative_to(self.workspace_root)
            except ValueError:
                raise UnrealSafetyError(
                    UnrealErrorCode.FILE_NOT_AUTHORIZED,
                    f"File '{resolved}' is outside authorized workspace '{self.workspace_root}'.",
                    {"file": str(resolved), "workspace_root": str(self.workspace_root)},
                )

        # Protected file check
        if resolved.name in PROTECTED_UNREAL_FILES:
            raise UnrealSafetyError(
                UnrealErrorCode.PROTECTED_FILE_REJECTED,
                f"Access to protected file '{resolved.name}' is prohibited.",
                {"file": str(resolved)},
            )

        # Protected extension check
        lower_name = resolved.name.lower()
        for ext in PROTECTED_UNREAL_EXTENSIONS:
            if lower_name.endswith(ext):
                raise UnrealSafetyError(
                    UnrealErrorCode.PROTECTED_FILE_REJECTED,
                    f"File '{resolved.name}' has prohibited extension '{ext}'.",
                    {"file": str(resolved), "prohibited_extension": ext},
                )

        # Protected directory check
        parts = resolved.parts
        for pdir in PROTECTED_UNREAL_DIRECTORIES:
            if pdir in parts:
                raise UnrealSafetyError(
                    UnrealErrorCode.PROTECTED_DIRECTORY_REJECTED,
                    f"File '{resolved}' is located inside protected directory '{pdir}'.",
                    {"file": str(resolved), "protected_directory": pdir},
                )

        return resolved

    def validate_build_target(
        self,
        project_path: str | Path,
        target_name: str,
        target_type: str = "Editor",
        configuration: str = "Development",
        platform: str = "Win64",
    ) -> Tuple[str, str, str, str]:
        """
        Validates that requested build parameters are safe, conform to allowlists,
        and do not contain command injection or path traversal tokens.
        Returns normalized (target_name, target_type, configuration, platform).
        """
        self.assert_not_emergency_stopped()
        self.validate_project_path(project_path)

        if not target_name or not isinstance(target_name, str):
            raise UnrealSafetyError(
                UnrealErrorCode.INVALID_BUILD_TARGET,
                "Build target name must be a non-empty string.",
                {"target_name": target_name},
            )

        # Prohibit shell injection, command chaining, and path traversal characters
        prohibited_chars = [";", "&", "|", "<", ">", "$", "`", "\n", "\r", "\\", "/", "..", '"', "'"]
        for char in prohibited_chars:
            if char in target_name:
                raise UnrealSafetyError(
                    UnrealErrorCode.INVALID_BUILD_TARGET,
                    f"Illegal character '{char}' in build target name: {target_name}",
                    {"target_name": target_name, "prohibited_character": char},
                )

        # Target name alphanumeric/underscore validation
        if not re.match(r"^[A-Za-z0-9_]+$", target_name):
            raise UnrealSafetyError(
                UnrealErrorCode.INVALID_BUILD_TARGET,
                f"Target name '{target_name}' contains invalid characters. Must be alphanumeric or underscore.",
                {"target_name": target_name},
            )

        if target_type not in ALLOWED_UNREAL_TARGET_TYPES:
            raise UnrealSafetyError(
                UnrealErrorCode.INVALID_BUILD_TARGET,
                f"Target type '{target_type}' is not allowed. Must be one of {sorted(list(ALLOWED_UNREAL_TARGET_TYPES))}",
                {"target_type": target_type, "allowed": sorted(list(ALLOWED_UNREAL_TARGET_TYPES))},
            )

        if configuration not in ALLOWED_UNREAL_CONFIGURATIONS:
            raise UnrealSafetyError(
                UnrealErrorCode.INVALID_BUILD_CONFIGURATION,
                f"Build configuration '{configuration}' is not allowed. Must be one of {sorted(list(ALLOWED_UNREAL_CONFIGURATIONS))}",
                {"configuration": configuration, "allowed": sorted(list(ALLOWED_UNREAL_CONFIGURATIONS))},
            )

        if platform not in ALLOWED_UNREAL_PLATFORMS:
            raise UnrealSafetyError(
                UnrealErrorCode.INVALID_BUILD_PLATFORM,
                f"Build platform '{platform}' is not allowed. Must be one of {sorted(list(ALLOWED_UNREAL_PLATFORMS))}",
                {"platform": platform, "allowed": sorted(list(ALLOWED_UNREAL_PLATFORMS))},
            )

        return target_name, target_type, configuration, platform

    def validate_test_mode(self, test_mode: str) -> str:
        """Validates that the test mode is explicitly allowed."""
        if not test_mode or not isinstance(test_mode, str):
            raise UnrealSafetyError(
                UnrealErrorCode.INVALID_TEST_MODE,
                "Test mode must be a non-empty string.",
                {"test_mode": test_mode},
            )
        if test_mode not in ALLOWED_UNREAL_TEST_MODES:
            raise UnrealSafetyError(
                UnrealErrorCode.INVALID_TEST_MODE,
                f"Test mode '{test_mode}' is not allowed. Must be one of {sorted(list(ALLOWED_UNREAL_TEST_MODES))}",
                {"test_mode": test_mode, "allowed": sorted(list(ALLOWED_UNREAL_TEST_MODES))},
            )
        return test_mode

    def validate_test_filter(self, test_filter: Optional[str]) -> Optional[str]:
        """Validates test filter string rejecting shell metacharacters and path traversal."""
        if not test_filter:
            return None
        if not isinstance(test_filter, str):
            raise UnrealSafetyError(
                UnrealErrorCode.INVALID_TEST_FILTER,
                "Test filter must be a string.",
                {"test_filter": test_filter},
            )
        # Reject shell injection, command chaining, and file traversal characters
        prohibited_chars = [";", "&", "|", "<", ">", "$", "`", "\n", "\r", "\\", "..", '"', "'"]
        for char in prohibited_chars:
            if char in test_filter:
                raise UnrealSafetyError(
                    UnrealErrorCode.INVALID_TEST_FILTER,
                    f"Illegal character '{char}' in test filter: {test_filter}",
                    {"test_filter": test_filter, "prohibited_character": char},
                )
        if not re.match(r"^[A-Za-z0-9_\.\*\+\-\:\/]+$", test_filter):
            raise UnrealSafetyError(
                UnrealErrorCode.INVALID_TEST_FILTER,
                f"Test filter '{test_filter}' contains invalid characters. Must match safe identifier syntax.",
                {"test_filter": test_filter},
            )
        return test_filter

    def validate_test_output_path(self, output_path: Optional[Any], project_path: Optional[Path] = None) -> Optional[Path]:
        """Validates that test output path is strictly within authorized workspace / project boundaries."""
        if not output_path:
            return None
        out_p = Path(output_path).resolve() if not isinstance(output_path, Path) else output_path.resolve()

        # Check path traversal in original string representation
        if ".." in str(output_path):
            raise UnrealSafetyError(
                UnrealErrorCode.PATH_TRAVERSAL_DETECTED,
                f"Path traversal ('..') detected in output path: {output_path}",
                {"output_path": str(output_path)},
            )

        # Check workspace confinement
        base_allowed = project_path.resolve() if project_path else self.workspace_root
        if not (self._is_subpath(out_p, self.workspace_root) or (project_path and self._is_subpath(out_p, project_path))):
            raise UnrealSafetyError(
                UnrealErrorCode.INVALID_TEST_OUTPUT,
                f"Output path '{out_p}' escapes authorized workspace boundary '{self.workspace_root}'",
                {"output_path": str(out_p), "workspace_root": str(self.workspace_root)},
            )
        return out_p

    def validate_test_parameters(
        self,
        test_mode: str = "SmokeTest",
        test_filter: Optional[str] = None,
        output_path: Optional[Any] = None,
        configuration: str = "Development",
        platform: str = "Win64",
        project_path: Optional[Path] = None,
    ) -> Tuple[str, Optional[str], Optional[Path], str, str]:
        """Validates all test execution parameters against strict allowlists and boundaries."""
        self.assert_not_emergency_stopped()
        self.check_rate_limit("validate_test_parameters")

        valid_mode = self.validate_test_mode(test_mode)
        valid_filter = self.validate_test_filter(test_filter)
        valid_out = self.validate_test_output_path(output_path, project_path=project_path)

        if configuration not in ALLOWED_UNREAL_TEST_CONFIGURATIONS:
            raise UnrealSafetyError(
                UnrealErrorCode.INVALID_BUILD_CONFIGURATION,
                f"Test configuration '{configuration}' is not allowed. Must be one of {sorted(list(ALLOWED_UNREAL_TEST_CONFIGURATIONS))}",
                {"configuration": configuration, "allowed": sorted(list(ALLOWED_UNREAL_TEST_CONFIGURATIONS))},
            )

        if platform not in ALLOWED_UNREAL_TEST_PLATFORMS:
            raise UnrealSafetyError(
                UnrealErrorCode.INVALID_BUILD_PLATFORM,
                f"Test platform '{platform}' is not allowed. Must be one of {sorted(list(ALLOWED_UNREAL_TEST_PLATFORMS))}",
                {"platform": platform, "allowed": sorted(list(ALLOWED_UNREAL_TEST_PLATFORMS))},
            )

        return valid_mode, valid_filter, valid_out, configuration, platform

    @property
    def default_project(self) -> Path:
        return self.authorized_projects[0] if self.authorized_projects else self.workspace_root

    def validate_source_file_path(
        self,
        path: Union[str, Path],
        project_path: Optional[Union[str, Path]] = None,
        must_exist: bool = False,
        project_root: Optional[Union[str, Path]] = None,
    ) -> Path:
        """
        Validates that a C++ source/header file path is authorized and within bounds.
        """
        if project_path is None and project_root is not None:
            project_path = project_root
        self.assert_not_emergency_stopped()
        self.check_rate_limit("validate_source_file_path")

        if not path:
            raise UnrealSafetyError(
                UnrealErrorCode.INVALID_PARAMETER,
                "Source file path cannot be empty.",
            )

        raw_str = str(path).replace("\\", "/")

        # Check path traversal
        if ".." in raw_str:
            raise UnrealSafetyError(
                UnrealErrorCode.PATH_TRAVERSAL_DETECTED,
                f"Path traversal detected in source path: {path}",
                {"path": raw_str},
            )

        if raw_str.startswith("//") or raw_str.startswith("\\\\"):
            raise UnrealSafetyError(
                UnrealErrorCode.UNC_PATH_REJECTED,
                f"UNC paths are prohibited: {path}",
                {"path": raw_str},
            )

        p = Path(path).resolve()

        # Check workspace confinement
        allowed_bases = [Path(project_path).resolve()] if project_path else self.authorized_projects
        is_in_ws = self._is_subpath(p, self.workspace_root)
        is_in_proj = any(self._is_subpath(p, b) for b in allowed_bases)
        if not (is_in_ws or is_in_proj):
            raise UnrealSafetyError(
                UnrealErrorCode.FILE_NOT_AUTHORIZED,
                f"Source path '{p}' is outside authorized boundaries.",
                {"path": str(p), "workspace_root": str(self.workspace_root)},
            )

        # Check protected files/directories before extension check
        if p.name in PROTECTED_UNREAL_FILES or p.suffix.lower() in {".uproject", ".uplugin"}:
            raise UnrealSafetyError(
                UnrealErrorCode.PROTECTED_FILE_REJECTED,
                f"File '{p.name}' is a protected file and cannot be modified or accessed.",
                {"file": p.name},
            )

        for part in p.parts:
            if part in PROTECTED_UNREAL_DIRECTORIES:
                raise UnrealSafetyError(
                    UnrealErrorCode.PROTECTED_FILE_REJECTED,
                    f"Directory '{part}' is protected.",
                    {"part": part, "path": str(p)},
                )

        # Check extension
        ext = p.suffix.lower()
        if ext not in ALLOWED_UNREAL_SOURCE_EXTENSIONS:
            raise UnrealSafetyError(
                UnrealErrorCode.FILE_NOT_AUTHORIZED,
                f"File extension '{ext}' is not an authorized C++ source extension. Allowed: {sorted(list(ALLOWED_UNREAL_SOURCE_EXTENSIONS))}",
                {"extension": ext, "allowed": sorted(list(ALLOWED_UNREAL_SOURCE_EXTENSIONS))},
            )

        # Check file existence and size limits
        if p.is_file():
            if p.stat().st_size > MAX_SOURCE_FILE_BYTES:
                raise UnrealSafetyError(
                    UnrealErrorCode.FILE_TOO_LARGE,
                    f"Source file size ({p.stat().st_size} bytes) exceeds maximum limit of {MAX_SOURCE_FILE_BYTES} bytes.",
                    {"file_size": p.stat().st_size, "max_bytes": MAX_SOURCE_FILE_BYTES},
                )
        elif must_exist:
            raise UnrealSafetyError(
                UnrealErrorCode.FILE_NOT_FOUND,
                f"Target source file does not exist: {p}",
                {"path": str(p)},
            )

        return p

    def validate_patch_content(self, patch_content: str) -> None:
        """
        Validates patch content bounds, prohibited tokens, and structural safety.
        """
        self.assert_not_emergency_stopped()
        self.check_rate_limit("validate_patch_content")

        if patch_content is None:
            raise UnrealSafetyError(
                UnrealErrorCode.INVALID_PARAMETER,
                "Patch content cannot be None.",
            )

        byte_size = len(patch_content.encode("utf-8"))
        if byte_size > MAX_PATCH_BYTES:
            raise UnrealSafetyError(
                UnrealErrorCode.PATCH_TOO_LARGE,
                f"Patch size ({byte_size} bytes) exceeds maximum allowable patch limit ({MAX_PATCH_BYTES} bytes).",
                {"byte_size": byte_size, "max_bytes": MAX_PATCH_BYTES},
            )

        line_count = len(patch_content.splitlines())
        if line_count > MAX_CHANGED_LINES:
            raise UnrealSafetyError(
                UnrealErrorCode.TOO_MANY_LINES_CHANGED,
                f"Patch line count ({line_count} lines) exceeds maximum limit of {MAX_CHANGED_LINES} lines.",
                {"line_count": line_count, "max_lines": MAX_CHANGED_LINES},
            )

        # Prohibited tokens check
        lower_content = patch_content.lower()
        for token in PROHIBITED_SOURCE_TOKENS:
            if token.lower() in lower_content:
                raise UnrealSafetyError(
                    UnrealErrorCode.PROHIBITED_TOKEN,
                    f"Prohibited execution token detected in modification content: '{token}'",
                    {"prohibited_token": token},
                )

    def validate_proposal_payload(
        self,
        proposal: Dict[str, Any],
        project_path: Optional[Union[str, Path]] = None,
    ) -> None:
        """
        Validates a structured modification proposal against the schema.
        """
        self.assert_not_emergency_stopped()
        self.check_rate_limit("validate_proposal_payload")

        if not isinstance(proposal, dict):
            raise UnrealSafetyError(
                UnrealErrorCode.INVALID_PROPOSAL,
                "Proposal must be a JSON/dict object.",
            )

        version = proposal.get("proposal_version")
        if version is not None and version != "1.0":
            raise UnrealSafetyError(
                UnrealErrorCode.INVALID_PROPOSAL,
                f"Unsupported proposal_version '{version}'. Only version '1.0' is supported.",
                {"proposal_version": version},
            )

        # Operation check
        op = str(proposal.get("operation", ""))
        if not op:
            raise UnrealSafetyError(
                UnrealErrorCode.INVALID_PROPOSAL,
                "Missing required proposal field: 'operation'",
                {"missing_field": "operation"},
            )
        if op not in ALLOWED_UNREAL_MODIFICATION_OPERATIONS:
            raise UnrealSafetyError(
                UnrealErrorCode.INVALID_OPERATION,
                f"Unsupported modification operation '{op}'. Allowed: {sorted(list(ALLOWED_UNREAL_MODIFICATION_OPERATIONS))}",
                {"operation": op, "allowed": sorted(list(ALLOWED_UNREAL_MODIFICATION_OPERATIONS))},
            )

        # Target file check
        target_f = proposal.get("target_file") or proposal.get("file_path")
        if not target_f:
            raise UnrealSafetyError(
                UnrealErrorCode.INVALID_PROPOSAL,
                "Missing required proposal field: 'target_file'",
                {"missing_field": "target_file"},
            )

        # Expected SHA check
        if "expected_sha256" not in proposal or proposal["expected_sha256"] is None:
            raise UnrealSafetyError(
                UnrealErrorCode.INVALID_PROPOSAL,
                "Missing required proposal field: 'expected_sha256'",
                {"missing_field": "expected_sha256"},
            )

        # Validate target file path
        self.validate_source_file_path(target_f, project_path=project_path, must_exist=False)

        # Validate replacement content
        rep = str(proposal.get("replacement") if proposal.get("replacement") is not None else proposal.get("content", ""))
        self.validate_patch_content(rep)

        # Check for command/shell injection words in metadata fields
        for fld in ["operation", "rationale"]:
            val = str(proposal.get(fld, "")).lower()
            for dangerous in ["cmd.exe", "powershell", "subprocess", "os.system", "/bin/sh", "bash -c"]:
                if dangerous in val:
                    raise UnrealSafetyError(
                        UnrealErrorCode.PROHIBITED_TOKEN,
                        f"Prohibited command token '{dangerous}' in proposal field '{fld}'",
                        {"field": fld, "token": dangerous},
                    )

    def validate_workflow_step_count(self, step_count: int) -> None:
        """Validates that workflow step count does not exceed MAX_WORKFLOW_STEPS."""
        self.assert_not_emergency_stopped()
        if step_count > MAX_WORKFLOW_STEPS:
            raise UnrealSafetyError(
                UnrealErrorCode.WORKFLOW_STEP_LIMIT_EXCEEDED,
                f"Workflow step count ({step_count}) exceeds maximum allowed limit ({MAX_WORKFLOW_STEPS}).",
                {"step_count": step_count, "max_steps": MAX_WORKFLOW_STEPS},
            )

    def validate_repair_attempts(self, attempts: int) -> int:
        """
        Validates that repair attempts do not exceed MAX_REPAIR_ATTEMPTS (strictly 2).
        Attempts 1 and 2 are allowed.
        Attempts 3 or more are deterministically rejected with UnrealSafetyError(REPAIR_ATTEMPTS_EXCEEDED).
        """
        self.assert_not_emergency_stopped()
        if not isinstance(attempts, int) or attempts < 1:
            raise UnrealSafetyError(
                UnrealErrorCode.INVALID_PARAMETER,
                f"Repair attempts must be a positive integer (1 or 2), got {attempts}.",
                {"attempts": attempts, "min_attempts": 1, "max_attempts": MAX_REPAIR_ATTEMPTS},
            )
        if attempts > MAX_REPAIR_ATTEMPTS:
            raise UnrealSafetyError(
                UnrealErrorCode.REPAIR_ATTEMPTS_EXCEEDED,
                f"Repair attempts ({attempts}) exceeds maximum allowed safety limit of {MAX_REPAIR_ATTEMPTS}.",
                {"attempts": attempts, "max_attempts": MAX_REPAIR_ATTEMPTS},
            )
        return attempts

    @staticmethod
    def _is_subpath(child: Path, parent: Path) -> bool:
        try:
            child.relative_to(parent)
            return True
        except ValueError:
            return False


# Global default instance
DEFAULT_UNREAL_SAFETY_GATE = UnrealSafetyGate()
