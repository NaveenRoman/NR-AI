r"""
NR-AI Unity Agent Safety Gate & Policy Enforcement (Step 8).

Strict deterministic safety enforcement for Unity projects:
- Explicit tool allowlist (ALLOWED_UNITY_TOOLS)
- Strict workspace boundary confinement (C:/NR-AI and authorized projects)
- Path traversal & protected file protection
- Thread-safe emergency stop freeze
- Bounded rates, edit bounds, and file size limits
- Sensitive data redaction (API keys, secrets, tokens)
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

logger = logging.getLogger("NRAI.UnitySafety")


# -----------------------------------------------------------------------------
# Error Codes & Exceptions
# -----------------------------------------------------------------------------

class UnityErrorCode(str, Enum):
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
    UNITY_NOT_FOUND = "UNITY_NOT_FOUND"
    EDITOR_NOT_RUNNING = "EDITOR_NOT_RUNNING"
    PROJECT_INVALID = "PROJECT_INVALID"
    ASSET_NOT_FOUND = "ASSET_NOT_FOUND"
    SCRIPT_NOT_FOUND = "SCRIPT_NOT_FOUND"
    BUILD_FAILED = "BUILD_FAILED"
    COMPILATION_FAILED = "COMPILATION_FAILED"
    TEST_FAILED = "TEST_FAILED"
    BUILD_TIMEOUT = "BUILD_TIMEOUT"
    TEST_TIMEOUT = "TEST_TIMEOUT"
    STALE_TARGET = "STALE_TARGET"
    EDIT_VALIDATION_FAILED = "EDIT_VALIDATION_FAILED"
    REPAIR_FAILED = "REPAIR_FAILED"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"
    REPAIR_UNSUPPORTED = "REPAIR_UNSUPPORTED"
    MALFORMED_PROPOSAL = "MALFORMED_PROPOSAL"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"


class UnitySafetyError(Exception):
    """Raised when a Unity safety boundary or policy is violated."""
    def __init__(self, code: UnityErrorCode, message: str, details: Optional[Dict[str, Any]] = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(f"[{code.value}] {message}")


class EmergencyStopActiveError(UnitySafetyError):
    """Raised when an operation is attempted while emergency stop is active."""
    def __init__(self, message: str = "EMERGENCY STOP is active. All Unity operations frozen."):
        super().__init__(UnityErrorCode.EMERGENCY_STOPPED, message)


# -----------------------------------------------------------------------------
# Allowlist Constants & Boundaries
# -----------------------------------------------------------------------------

GLOBAL_WORKSPACE_ROOT = Path("C:/NR-AI").resolve()
DEFAULT_AUTHORIZED_PROJECT = (GLOBAL_WORKSPACE_ROOT / "nr_unity_test").resolve()

ALLOWED_UNITY_EXTENSIONS: Set[str] = {
    ".cs",
    ".unity",
    ".prefab",
    ".mat",
    ".asset",
    ".meta",
    ".json",
    ".asmdef",
    ".asmref",
    ".txt",
    ".md",
    ".xml",
    ".shader",
    ".compute",
    ".cginc",
    ".hlsl",
}

PROTECTED_UNITY_EXTENSIONS: Set[str] = {
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
    ".csproj",
}

PROTECTED_UNITY_FILES: Set[str] = {
    "ProjectVersion.txt",
    "manifest.json",
}

PROTECTED_UNITY_DIRECTORIES: Set[str] = {
    "Library",
    "Temp",
    "obj",
    "Logs",
    "Build",
    "Builds",
    ".vs",
    ".git",
}

ALLOWED_UNITY_TOOLS: Set[str] = {
    # Step 8 Phase 1: Environment & Project Inspection Foundation
    "unity.detect_environment",
    "unity.inspect_project",
    "unity.list_projects",
    "unity.inspect_packages",
    "unity.list_assets",
    "unity.find_scripts",
    "unity.read_script",
    "unity.inspect_asmdef",
    "unity.inspect_scenes_in_build",
    "unity.inspect_editor_state",
    "unity.validate_project_structure",
    "unity.inspect_project_version",
}

# Operational limits
MAX_FILES_CHANGED: int = 3
MAX_PATCH_SIZE_BYTES: int = 65_536    # 64 KB limit
MAX_FILE_SIZE_BYTES: int = 1_000_000  # 1 MB
MAX_LINES_CHANGED: int = 200          # 200 lines limit
MAX_REPAIR_ATTEMPTS: int = 2
BUILD_TIMEOUT_SECONDS: float = 300.0
TEST_TIMEOUT_SECONDS: float = 180.0
MAX_READ_LINES: int = 500
MAX_SEARCH_RESULTS: int = 50


# -----------------------------------------------------------------------------
# Sensitive Data Redaction
# -----------------------------------------------------------------------------

REDACTION_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r'(?i)(AIza[0-9A-Za-z\-_]{20,40})'), "[REDACTED_API_KEY]"),
    (re.compile(r'(?i)(Bearer\s+)[A-Za-z0-9_\-\.]{20,}'), r"\1[REDACTED_TOKEN]"),
    (re.compile(r'(?i)(ghp_[0-9A-Za-z]{36}|gho_[0-9A-Za-z]{36})'), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r'(?i)(sk-[A-Za-z0-9\-_]{10,})'), "[REDACTED_API_KEY]"),
    (re.compile(r'(?i)(password\s*[:=]\s*)[^\s,;]+'), r"\1[REDACTED_PASSWORD]"),
    (re.compile(r'(?i)(pwd\s*[:=]\s*)[^\s,;]+'), r"\1[REDACTED_PASSWORD]"),
    (re.compile(r'(?i)(client_secret\s*[:=]\s*)[^\s,;]+'), r"\1[REDACTED_SECRET]"),
    (re.compile(r'(?i)(secret\s*[:=]\s*)[^\s,;]+'), r"\1[REDACTED_SECRET]"),
    (re.compile(r'(?i)(unity_lic\s*[:=]\s*)[^\s,;]+'), r"\1[REDACTED_LICENSE]"),
]


def redact_sensitive_data(text: str) -> str:
    """Masks secrets, passwords, and tokens with [REDACTED_...] placeholders."""
    if not text:
        return ""
    result = str(text)
    for pattern, repl in REDACTION_PATTERNS:
        result = pattern.sub(repl, result)
    return result


# -----------------------------------------------------------------------------
# Unity Safety Gate
# -----------------------------------------------------------------------------

class UnitySafetyGate:
    """
    Central deterministic safety gate for the Unity Agent.
    Enforces path boundaries, file protections, allowlists, rate limits,
    and emergency stop state.
    """

    def __init__(
        self,
        authorized_project: Optional[Path] = None,
        workspace_root: Optional[Path] = None,
        rate_limit_calls_per_minute: int = 120,
        authorized_projects: Optional[List[Path]] = None,
    ):
        self.workspace_root = (workspace_root or GLOBAL_WORKSPACE_ROOT).resolve()
        if authorized_projects:
            self.authorized_projects = [Path(p).resolve() for p in authorized_projects]
        elif authorized_project:
            self.authorized_projects = [Path(authorized_project).resolve()]
        else:
            self.authorized_projects = [DEFAULT_AUTHORIZED_PROJECT]
        self.authorized_project = self.authorized_projects[0]
        self.rate_limit_cpm = rate_limit_calls_per_minute

        self._lock = threading.Lock()
        self._emergency_stop_active = False
        self._emergency_stop_reason: Optional[str] = None
        self._call_timestamps: Dict[str, List[float]] = {}

    def emergency_stop(self, reason: str = "Emergency stop activated by operator") -> None:
        """Immediately halts all Unity Agent operations across threads."""
        with self._lock:
            self._emergency_stop_active = True
            self._emergency_stop_reason = reason
            logger.critical(f"[UnitySafety] EMERGENCY STOP ACTIVATED. Reason: {reason}")
            print(f"[UnitySafety] EMERGENCY STOP ACTIVATED. [Reason: {reason}] All Unity operations frozen.")

    def activate_emergency_stop(self, reason: str = "Emergency stop activated") -> None:
        """Alias for emergency_stop."""
        self.emergency_stop(reason)

    def deactivate_emergency_stop(self) -> None:
        """Resets emergency stop state (operator manual override)."""
        with self._lock:
            self._emergency_stop_active = False
            self._emergency_stop_reason = None
            logger.info("[UnitySafety] Emergency stop deactivated.")

    def is_emergency_stopped(self) -> bool:
        """Checks if emergency stop is active."""
        with self._lock:
            return self._emergency_stop_active

    def assert_not_stopped(self) -> None:
        """Raises EmergencyStopActiveError if emergency stop is currently active."""
        with self._lock:
            if self._emergency_stop_active:
                raise EmergencyStopActiveError(
                    f"EMERGENCY STOP is active [{self._emergency_stop_reason}]. All Unity operations frozen."
                )

    def validate_tool_allowed(self, tool_name: str) -> None:
        """Verifies tool_name is present in ALLOWED_UNITY_TOOLS."""
        self.assert_not_stopped()
        if tool_name not in ALLOWED_UNITY_TOOLS:
            raise UnitySafetyError(
                UnityErrorCode.TOOL_NOT_ALLOWED,
                f"Tool '{tool_name}' is not in ALLOWED_UNITY_TOOLS allowlist.",
                {"tool": tool_name},
            )

    def validate_path(self, target_path: Path, allow_read_only_workspace: bool = True) -> Path:
        """
        Validates target_path resides strictly within the authorized project
        or workspace boundary. Rejects path traversal and external roots.
        """
        self.assert_not_stopped()
        raw_str = str(target_path)
        if ".." in raw_str.replace("\\", "/").split("/"):
            raise UnitySafetyError(
                UnityErrorCode.PATH_TRAVERSAL_DETECTED,
                f"Path traversal sequence '..' detected in path: '{target_path}'.",
                {"path": str(target_path)},
            )

        try:
            resolved = target_path.resolve()
        except Exception as e:
            raise UnitySafetyError(
                UnityErrorCode.FILE_NOT_AUTHORIZED,
                f"Invalid filesystem path '{target_path}': {e}",
            )

        # Check confinement
        in_project = False
        for auth_p in self.authorized_projects:
            try:
                resolved.relative_to(auth_p)
                in_project = True
                break
            except ValueError:
                pass

        in_workspace = False
        try:
            resolved.relative_to(self.workspace_root)
            in_workspace = True
        except ValueError:
            pass

        if not (in_project or (allow_read_only_workspace and in_workspace)):
            raise UnitySafetyError(
                UnityErrorCode.FILE_NOT_AUTHORIZED,
                f"Path '{resolved}' is outside authorized boundaries '{self.authorized_project}' and '{self.workspace_root}'.",
                {"path": str(resolved), "authorized_project": str(self.authorized_project)},
            )

        # Check protected extension
        suffix = resolved.suffix.lower()
        if suffix in PROTECTED_UNITY_EXTENSIONS:
            raise UnitySafetyError(
                UnityErrorCode.PROTECTED_FILE_REJECTED,
                f"File '{resolved.name}' has protected extension '{suffix}'. Access blocked.",
                {"path": str(resolved), "suffix": suffix},
            )

        return resolved

    def validate_file_for_write(self, target_path: Path) -> Path:
        """Validates path for modification (strictly inside authorized project and not protected)."""
        resolved = self.validate_path(target_path, allow_read_only_workspace=False)
        rel_parts = set(resolved.parts)
        for d in PROTECTED_UNITY_DIRECTORIES:
            if d in rel_parts:
                raise UnitySafetyError(
                    UnityErrorCode.PROTECTED_DIRECTORY_REJECTED,
                    f"Directory component '{d}' in path '{resolved}' is protected. Write blocked.",
                    {"path": str(resolved), "directory": d},
                )

        suffix = resolved.suffix.lower()
        if suffix in PROTECTED_UNITY_EXTENSIONS:
            raise UnitySafetyError(
                UnityErrorCode.PROTECTED_FILE_REJECTED,
                f"File '{resolved.name}' has protected extension '{suffix}'. Direct write blocked.",
                {"path": str(resolved), "suffix": suffix},
            )

        if resolved.name in PROTECTED_UNITY_FILES:
            raise UnitySafetyError(
                UnityErrorCode.PROTECTED_FILE_REJECTED,
                f"File '{resolved.name}' is a protected Unity project configuration file. Direct write blocked.",
                {"path": str(resolved)},
            )
        return resolved

    def check_rate_limit(self, tool_name: str) -> None:
        """Enforces sliding-window rate limits per tool."""
        now = time.time()
        window_start = now - 60.0
        with self._lock:
            ts_list = self._call_timestamps.setdefault(tool_name, [])
            ts_list = [t for t in ts_list if t > window_start]
            self._call_timestamps[tool_name] = ts_list
            if len(ts_list) >= self.rate_limit_cpm:
                raise UnitySafetyError(
                    UnityErrorCode.RATE_LIMIT_EXCEEDED,
                    f"Rate limit of {self.rate_limit_cpm} calls/min exceeded for tool '{tool_name}'.",
                    {"tool": tool_name, "count": len(ts_list)},
                )
            ts_list.append(now)


# Global default instance
DEFAULT_UNITY_SAFETY_GATE = UnitySafetyGate()
