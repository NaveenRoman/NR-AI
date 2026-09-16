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

ALLOWED_UNITY_PHASE1_TOOLS: Set[str] = {
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

ALLOWED_UNITY_TOOLS: Set[str] = ALLOWED_UNITY_PHASE1_TOOLS

ALLOWED_UNITY_BUILD_TOOLS: Set[str] = {
    # Step 8 Phase 2: Compilation & Build Foundation
    "unity.validate_build_target",
    "unity.validate_build_output_path",
    "unity.compile_project",
    "unity.build_player",
    "unity.verify_build_artifact",
    "unity.parse_build_log",
    "unity.get_build_configuration",
    "unity.clean_build_target",
}

ALLOWED_UNITY_TEST_TOOLS: Set[str] = {
    # Step 8 Phase 3: Unity Test Runner & PlayMode Foundation
    "unity.validate_test_mode",
    "unity.validate_test_filter",
    "unity.run_editmode_tests",
    "unity.run_playmode_tests",
    "unity.parse_test_results",
    "unity.verify_test_artifact",
    "unity.get_test_summary",
    "unity.diagnose_test_failure",
}

ALLOWED_UNITY_AST_TOOLS: Set[str] = {
    # Step 8 Phase 4: Unity C# Script Analysis & AST Modification
    "unity.parse_script_ast",
    "unity.analyze_script",
    "unity.propose_ast_modification",
    "unity.apply_ast_modification",
    "unity.verify_ast_integrity",
    "unity.rollback_script_modification",
    "unity.get_script_symbols",
    "unity.validate_script_repair",
}

ALL_ALLOWED_UNITY_TOOLS: Set[str] = (
    ALLOWED_UNITY_TOOLS | ALLOWED_UNITY_BUILD_TOOLS | ALLOWED_UNITY_TEST_TOOLS | ALLOWED_UNITY_AST_TOOLS
)

ALLOWED_TEST_MODES: Set[str] = {
    "EditMode",
    "PlayMode",
}

ALLOWED_BUILD_TARGETS: Set[str] = {
    "StandaloneWindows64",
    "StandaloneWindows",
    "Android",
    "WebGL",
    "Linux64",
    "StandaloneOSX",
}

PROTECTED_BUILD_OUTPUT_DIRS: Set[str] = {
    "Assets",
    "ProjectSettings",
    "Packages",
    "Library",
    "Temp",
    "obj",
    "Logs",
    ".git",
    ".vs",
}

PROTECTED_TEST_OUTPUT_DIRS: Set[str] = {
    "Assets",
    "ProjectSettings",
    "Packages",
    "Library",
    "Temp",
    "obj",
    "Logs",
    ".git",
    ".vs",
}

DISALLOWED_BUILD_OUTPUT_EXTENSIONS: Set[str] = {
    ".bat",
    ".cmd",
    ".ps1",
    ".sh",
    ".bash",
    ".vbs",
    ".js",
    ".py",
    ".cs",
}

DISALLOWED_TEST_OUTPUT_EXTENSIONS: Set[str] = {
    ".bat",
    ".cmd",
    ".ps1",
    ".sh",
    ".bash",
    ".vbs",
    ".js",
    ".py",
    ".cs",
    ".exe",
    ".dll",
}

MAX_TEST_RESULT_FILE_SIZE_BYTES: int = 5_000_000  # 5 MB limit for XML results

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

# Step 8 Phase 4 AST Modification limits
MAX_AST_FILES_PER_OP: int = 5
MAX_AST_PATCH_BYTES: int = 100_000    # 100 KB total patch limit
MAX_AST_CHANGED_LINES: int = 500      # 500 changed lines limit
MAX_AST_REPAIR_ATTEMPTS: int = 2      # 2 repair attempts limit


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
        """Verifies tool_name is present in approved Unity tool allowlists."""
        self.assert_not_stopped()
        if tool_name not in ALL_ALLOWED_UNITY_TOOLS:
            raise UnitySafetyError(
                UnityErrorCode.TOOL_NOT_ALLOWED,
                f"Tool '{tool_name}' is not in approved Unity tools allowlist.",
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

    def validate_build_target(self, target: str) -> str:
        """Validates that target is one of the allowed build targets."""
        self.assert_not_stopped()
        if not target or target not in ALLOWED_BUILD_TARGETS:
            raise UnitySafetyError(
                UnityErrorCode.INVALID_BUILD_TARGET,
                f"Build target '{target}' is not in ALLOWED_BUILD_TARGETS: {sorted(list(ALLOWED_BUILD_TARGETS))}.",
                {"target": target, "allowed": sorted(list(ALLOWED_BUILD_TARGETS))},
            )
        return target

    def validate_build_output_path(self, target_path: Path | str, project_root: Optional[Path] = None) -> Path:
        """
        Validates build output destination path.
        Must be confined within project directory (e.g. Builds/) or workspace build folder.
        Rejects traversal, system roots, and writing directly into Assets/ProjectSettings.
        """
        self.assert_not_stopped()
        raw_str = str(target_path)
        if ".." in raw_str.replace("\\", "/").split("/"):
            raise UnitySafetyError(
                UnityErrorCode.PATH_TRAVERSAL_DETECTED,
                f"Path traversal sequence '..' detected in build output path: '{target_path}'.",
                {"path": str(target_path)},
            )

        try:
            resolved = Path(target_path).resolve()
        except Exception as e:
            raise UnitySafetyError(
                UnityErrorCode.INVALID_BUILD_OUTPUT,
                f"Invalid build output filesystem path '{target_path}': {e}",
            )

        # Check confinement to workspace or authorized project
        in_project = False
        target_project = (project_root or self.authorized_project).resolve()
        for auth_p in self.authorized_projects + [target_project]:
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

        if not (in_project or in_workspace):
            raise UnitySafetyError(
                UnityErrorCode.FILE_NOT_AUTHORIZED,
                f"Build output path '{resolved}' is outside authorized project/workspace boundaries.",
                {"path": str(resolved)},
            )

        # Check that output path does not target protected project directories (like Assets, ProjectSettings, Library)
        rel_to_proj = None
        if in_project:
            try:
                rel_to_proj = resolved.relative_to(target_project)
            except ValueError:
                pass
        if rel_to_proj:
            parts = rel_to_proj.parts
            if parts:
                top_part = parts[0]
                if top_part in PROTECTED_BUILD_OUTPUT_DIRS:
                    raise UnitySafetyError(
                        UnityErrorCode.PROTECTED_DIRECTORY_REJECTED,
                        f"Build output cannot be written directly into protected directory '{top_part}'. Use 'Builds/' instead.",
                        {"path": str(resolved), "directory": top_part},
                    )

        suffix = resolved.suffix.lower()
        if suffix in DISALLOWED_BUILD_OUTPUT_EXTENSIONS:
            raise UnitySafetyError(
                UnityErrorCode.INVALID_BUILD_OUTPUT,
                f"Disallowed script/executable extension '{suffix}' for build output.",
                {"path": str(resolved), "suffix": suffix},
            )

        return resolved

    def validate_test_mode(self, mode: str) -> str:
        """Validates that test mode is one of the allowed test modes (EditMode, PlayMode)."""
        self.assert_not_stopped()
        if not mode or mode not in ALLOWED_TEST_MODES:
            raise UnitySafetyError(
                UnityErrorCode.INVALID_TEST_MODE,
                f"Test mode '{mode}' is invalid. Allowed modes: {sorted(list(ALLOWED_TEST_MODES))}.",
                {"mode": mode, "allowed": sorted(list(ALLOWED_TEST_MODES))},
            )
        return mode

    def validate_test_filter(self, filter_str: Optional[str], filter_type: str = "filter") -> Optional[str]:
        """
        Validates test filter, category, class, or method strings.
        Rejects shell metacharacters, control characters, quotes, or dangerous constructs.
        Returns the validated filter string, or None if filter_str is None/empty.
        """
        self.assert_not_stopped()
        if filter_str is None:
            return None
        clean = str(filter_str).strip()
        if not clean:
            return None

        # Disallow shell metacharacters, control chars, quotes, redirects, backticks
        dangerous_chars = set('&|;$`><"\'\n\r\t\x00\\')
        for ch in clean:
            if ch in dangerous_chars or ord(ch) < 32 or ord(ch) > 126:
                raise UnitySafetyError(
                    UnityErrorCode.INVALID_TEST_FILTER,
                    f"Test {filter_type} '{clean}' contains dangerous or invalid character '{ch}'.",
                    {"filter": clean, "invalid_char": ch, "type": filter_type},
                )

        if len(clean) > 256:
            raise UnitySafetyError(
                UnityErrorCode.INVALID_TEST_FILTER,
                f"Test {filter_type} string exceeds maximum length of 256 characters ({len(clean)}).",
                {"filter": clean[:50] + "...", "length": len(clean)},
            )

        # Disallow path traversal sequences
        if ".." in clean:
            raise UnitySafetyError(
                UnityErrorCode.INVALID_TEST_FILTER,
                f"Path traversal sequence '..' detected in test {filter_type}: '{clean}'.",
                {"filter": clean},
            )

        return clean

    def validate_test_output_path(self, target_path: Path | str, project_root: Optional[Path] = None) -> Path:
        """
        Validates test results XML output path.
        Must be confined within project directory (e.g. TestResults/, Builds/) or workspace scratch folder.
        Rejects traversal, system roots, protected project directories (Assets, ProjectSettings, Library),
        and dangerous file extensions.
        """
        self.assert_not_stopped()
        raw_str = str(target_path)
        if ".." in raw_str.replace("\\", "/").split("/"):
            raise UnitySafetyError(
                UnityErrorCode.PATH_TRAVERSAL_DETECTED,
                f"Path traversal sequence '..' detected in test output path: '{target_path}'.",
                {"path": str(target_path)},
            )

        try:
            resolved = Path(target_path).resolve()
        except Exception as e:
            raise UnitySafetyError(
                UnityErrorCode.INVALID_TEST_OUTPUT,
                f"Invalid test output filesystem path '{target_path}': {e}",
            )

        # Confinement check
        in_project = False
        target_project = (project_root or self.authorized_project).resolve()
        for auth_p in self.authorized_projects + [target_project]:
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

        if not (in_project or in_workspace):
            raise UnitySafetyError(
                UnityErrorCode.FILE_NOT_AUTHORIZED,
                f"Test output path '{resolved}' is outside authorized project/workspace boundaries.",
                {"path": str(resolved)},
            )

        # Check that output path does not target protected project directories (like Assets, ProjectSettings, Library)
        rel_to_proj = None
        if in_project:
            try:
                rel_to_proj = resolved.relative_to(target_project)
            except ValueError:
                pass
        if rel_to_proj:
            parts = rel_to_proj.parts
            if parts:
                top_part = parts[0]
                if top_part in PROTECTED_TEST_OUTPUT_DIRS:
                    raise UnitySafetyError(
                        UnityErrorCode.PROTECTED_DIRECTORY_REJECTED,
                        f"Test output cannot be written directly into protected directory '{top_part}'. Use 'TestResults/' instead.",
                        {"path": str(resolved), "directory": top_part},
                    )

        suffix = resolved.suffix.lower()
        if suffix in DISALLOWED_TEST_OUTPUT_EXTENSIONS:
            raise UnitySafetyError(
                UnityErrorCode.INVALID_TEST_OUTPUT,
                f"Disallowed script/executable extension '{suffix}' for test output.",
                {"path": str(resolved), "suffix": suffix},
            )

        return resolved

    def validate_ast_limits(
        self,
        num_files: int = 1,
        patch_size_bytes: int = 0,
        lines_changed: int = 0,
        attempts: int = 1,
    ) -> None:
        """
        Enforces Phase 4 AST modification limits:
        - Max 5 files per operation
        - Max 100 KB total patch
        - Max 500 changed lines
        - Max 2 repair attempts
        """
        self.assert_not_stopped()
        if num_files > MAX_AST_FILES_PER_OP:
            raise UnitySafetyError(
                UnityErrorCode.TOO_MANY_FILES_CHANGED,
                f"Requested modification affects {num_files} files, exceeding limit of {MAX_AST_FILES_PER_OP}.",
                {"num_files": num_files, "limit": MAX_AST_FILES_PER_OP},
            )
        if patch_size_bytes > MAX_AST_PATCH_BYTES:
            raise UnitySafetyError(
                UnityErrorCode.PATCH_TOO_LARGE,
                f"Requested patch size {patch_size_bytes} bytes exceeds maximum limit of {MAX_AST_PATCH_BYTES} bytes (100 KB).",
                {"patch_size_bytes": patch_size_bytes, "limit": MAX_AST_PATCH_BYTES},
            )
        if lines_changed > MAX_AST_CHANGED_LINES:
            raise UnitySafetyError(
                UnityErrorCode.TOO_MANY_LINES_CHANGED,
                f"Requested modification changes {lines_changed} lines, exceeding limit of {MAX_AST_CHANGED_LINES}.",
                {"lines_changed": lines_changed, "limit": MAX_AST_CHANGED_LINES},
            )
        if attempts > MAX_AST_REPAIR_ATTEMPTS:
            raise UnitySafetyError(
                UnityErrorCode.REPAIR_ATTEMPTS_EXCEEDED,
                f"Modification attempt count {attempts} exceeds maximum allowed attempts of {MAX_AST_REPAIR_ATTEMPTS}.",
                {"attempts": attempts, "limit": MAX_AST_REPAIR_ATTEMPTS},
            )

    def validate_script_file_target(
        self,
        file_path: Path | str,
        check_exists: bool = True,
    ) -> Path:
        """
        Validates that target script file is authorized for AST analysis or modification.
        Enforces:
        - Emergency stop check
        - No path traversal
        - Workspace/authorized project confinement
        - Must have .cs extension
        - Not in protected files (ProjectVersion.txt, manifest.json)
        - Not in protected directories (Library, Temp, obj, Logs, .git, .vs)
        - File existence check if check_exists is True
        """
        self.assert_not_stopped()
        raw_str = str(file_path)
        if ".." in raw_str.replace("\\", "/").split("/"):
            raise UnitySafetyError(
                UnityErrorCode.PATH_TRAVERSAL_DETECTED,
                f"Path traversal sequence '..' detected in script target: '{file_path}'.",
                {"path": str(file_path)},
            )

        try:
            resolved = Path(file_path).resolve()
        except Exception as e:
            raise UnitySafetyError(
                UnityErrorCode.FILE_NOT_AUTHORIZED,
                f"Invalid script filesystem path '{file_path}': {e}",
            )

        # Confinement check
        self.validate_path(resolved, allow_read_only_workspace=True)

        # Extension check
        suffix = resolved.suffix.lower()
        if suffix != ".cs":
            raise UnitySafetyError(
                UnityErrorCode.FILE_NOT_AUTHORIZED,
                f"AST modification/analysis only applies to C# (.cs) files, received '{suffix}': {resolved.name}.",
                {"path": str(resolved), "suffix": suffix},
            )

        # Protected file check
        if resolved.name in PROTECTED_UNITY_FILES:
            raise UnitySafetyError(
                UnityErrorCode.PROTECTED_FILE_REJECTED,
                f"Cannot modify or analyze protected project file '{resolved.name}'.",
                {"path": str(resolved)},
            )

        # Protected directory check
        for part in resolved.parts:
            if part in PROTECTED_UNITY_DIRECTORIES:
                raise UnitySafetyError(
                    UnityErrorCode.PROTECTED_DIRECTORY_REJECTED,
                    f"Script path '{resolved}' is inside protected directory '{part}'.",
                    {"path": str(resolved), "directory": part},
                )

        if check_exists and not resolved.exists():
            raise UnitySafetyError(
                UnityErrorCode.SCRIPT_NOT_FOUND,
                f"Target C# script file does not exist: '{resolved}'.",
                {"path": str(resolved)},
            )

        return resolved

    def validate_target_sha256(self, file_path: Path | str, expected_sha256: str) -> bool:
        """
        Verifies that the target file's current SHA-256 matches expected_sha256.
        Raises UnitySafetyError(UnityErrorCode.STALE_TARGET) on mismatch.
        """
        resolved = Path(file_path).resolve()
        if not resolved.exists():
            raise UnitySafetyError(
                UnityErrorCode.SCRIPT_NOT_FOUND,
                f"Target file for hash verification not found: '{resolved}'.",
            )
        import hashlib
        current_hash = hashlib.sha256(resolved.read_bytes()).hexdigest().lower()
        exp = str(expected_sha256 or "").strip().lower()
        if current_hash != exp:
            raise UnitySafetyError(
                UnityErrorCode.STALE_TARGET,
                f"File SHA-256 mismatch for '{resolved.name}'. Current: {current_hash}, Expected: {exp}.",
                {"path": str(resolved), "current_sha256": current_hash, "expected_sha256": exp},
            )
        return True


# Global default instance
DEFAULT_UNITY_SAFETY_GATE = UnitySafetyGate()
