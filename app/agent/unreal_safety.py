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
from typing import Any, Dict, List, Optional, Set, Tuple

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
    INVALID_BUILD_OUTPUT = "INVALID_BUILD_OUTPUT"
    BUILD_ARTIFACT_MISSING = "BUILD_ARTIFACT_MISSING"
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

ALLOWED_UNREAL_TOOLS: Set[str] = ALLOWED_UNREAL_PHASE1_TOOLS
ALL_ALLOWED_UNREAL_TOOLS: Set[str] = ALLOWED_UNREAL_PHASE1_TOOLS

# File size & operational limits
MAX_READ_LINES: int = 1000
MAX_READ_BYTES: int = 500_000
MAX_CAPTURED_OUTPUT_BYTES: int = 100_000
MAX_FILE_SIZE_BYTES: int = 50_000_000

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

    def check_rate_limit(self) -> None:
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

    @staticmethod
    def _is_subpath(child: Path, parent: Path) -> bool:
        try:
            child.relative_to(parent)
            return True
        except ValueError:
            return False


# Global default instance
DEFAULT_UNREAL_SAFETY_GATE = UnrealSafetyGate()
