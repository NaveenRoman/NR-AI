"""
NR-AI Visual Studio Safety Engine & Deterministic Contracts (Step 7).

Enforces strict deterministic boundaries:
1. Authorized Project Boundary: Only projects within C:\\NR-AI (default C:\\NR-AI\\nr_vs_test)
2. Tool Allowlist: 12 strictly enumerated vs.* tools
3. Allowed File Extensions: Explicit allowlist (.cs, .vb, .fs, .sln, .csproj, etc.)
4. Protected Files: Reject certificates (.pfx, .snk), keys, secrets, bin/obj, .suo, .user
5. Operational Bounds: max 5 files changed, max 100 KB patch, max 500 lines changed
6. Repair Bounds: max 2 repair attempts, SHA-256 target validation, atomic rollback
7. Emergency Stop: Immediate freeze of all Visual Studio operations
8. No arbitrary shell, MSBuild, or executable passthrough
"""

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import logging
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union

logger = logging.getLogger("NRAI.VSSafety")

# -----------------------------------------------------------------------------
# Structured Error Constants
# -----------------------------------------------------------------------------

class VSErrorCode(str, Enum):
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
    VS_NOT_FOUND = "VS_NOT_FOUND"
    MSBUILD_NOT_FOUND = "MSBUILD_NOT_FOUND"
    SDK_NOT_FOUND = "SDK_NOT_FOUND"
    CONFIGURATION_NOT_FOUND = "CONFIGURATION_NOT_FOUND"
    BUILD_FAILED = "BUILD_FAILED"
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


class VSSafetyError(Exception):
    """Raised when a Visual Studio safety boundary is violated."""
    def __init__(self, code: VSErrorCode, message: str, details: Optional[Dict[str, Any]] = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(f"[{code.value}] {message}")


class EmergencyStopActiveError(VSSafetyError):
    """Raised when an operation is attempted while emergency stop is active."""
    def __init__(self, message: str = "EMERGENCY STOP is active. All Visual Studio operations frozen."):
        super().__init__(VSErrorCode.EMERGENCY_STOPPED, message)


# -----------------------------------------------------------------------------
# Allowlist Constants & Boundaries
# -----------------------------------------------------------------------------

GLOBAL_WORKSPACE_ROOT = Path("C:/NR-AI").resolve()
DEFAULT_AUTHORIZED_PROJECT = (GLOBAL_WORKSPACE_ROOT / "nr_vs_test").resolve()

ALLOWED_VS_EXTENSIONS: Set[str] = {
    ".sln",
    ".slnx",
    ".csproj",
    ".vbproj",
    ".fsproj",
    ".props",
    ".targets",
    ".cs",
    ".vb",
    ".fs",
    ".config",
    ".json",
    ".xml",
    ".xaml",
    ".resx",
    ".editorconfig",
    ".txt",
    ".md",
    ".ini",
    ".runsettings",
}

PROTECTED_VS_EXTENSIONS: Set[str] = {
    ".suo",
    ".user",
    ".userosscache",
    ".pfx",
    ".snk",
    ".p12",
    ".key",
    ".pem",
    ".env",
    ".exe",
    ".dll",
    ".pdb",
    ".obj",
    ".bin",
    ".cache",
    ".nupkg",
    ".snupkg",
    ".dylib",
    ".so",
}

PROTECTED_VS_DIRECTORIES: Set[str] = {
    "bin",
    "obj",
    "packages",
    ".vs",
    ".git",
    ".nuget",
    "testresults",
    "artifacts",
}

PROTECTED_VS_FILENAMES: Set[str] = {
    "secrets.json",
    "appsettings.production.json",
    "launchsettings.json",
    ".env",
}

ALLOWED_VS_TOOLS: Set[str] = {
    "vs.inspect_solution",
    "vs.inspect_project",
    "vs.list_projects",
    "vs.inspect_project_files",
    "vs.find_code",
    "vs.read_code",
    "vs.inspect_build_configuration",
    "vs.inspect_dependencies",
    "vs.run_safe_build",
    "vs.run_safe_test",
    "vs.capture_build_output",
    "vs.verify_build_result",
}

ALLOWED_BUILD_ACTIONS: Set[str] = {
    "BUILD",
    "REBUILD",
    "CLEAN",
    "TEST",
    "RESTORE",
}

# Operational limits
MAX_FILES_CHANGED: int = 3
MAX_PATCH_SIZE_BYTES: int = 65_536    # 64 KB limit
MAX_FILE_SIZE_BYTES: int = 1_000_000  # 1 MB
MAX_LINES_CHANGED: int = 200          # 200 lines limit
MAX_REPAIR_ATTEMPTS: int = 2
BUILD_TIMEOUT_SECONDS: float = 180.0
TEST_TIMEOUT_SECONDS: float = 180.0

# Aliases for operational limits
MAX_PATCH_BYTES: int = MAX_PATCH_SIZE_BYTES
MAX_EDIT_LINES: int = MAX_LINES_CHANGED
MAX_FILES_PER_REPAIR: int = MAX_FILES_CHANGED
MAX_READ_LINES: int = 500
MAX_SEARCH_RESULTS: int = 50


# -----------------------------------------------------------------------------
# Sensitive Data Redaction
# -----------------------------------------------------------------------------

REDACTION_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r'(?i)(AIza[0-9A-Za-z\-_]{20,40})'), "[REDACTED_API_KEY]"),
    (re.compile(r'(?i)(Bearer\s+)[A-Za-z0-9_\-\.]{20,}'), r"\1[REDACTED_TOKEN]"),
    (re.compile(r'(?i)(ghp_[0-9A-Za-z]{36}|gho_[0-9A-Za-z]{36})'), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r'(?i)(sk-[A-Za-z0-9]{32,})'), "[REDACTED_OPENAI_KEY]"),
    (re.compile(r'(?i)(password\s*[:=]\s*)["\']?[^\s",;]+["\']?'), r"\1[REDACTED_PASSWORD]"),
    (re.compile(r'(?i)(pwd\s*[:=]\s*)["\']?[^\s",;]+["\']?'), r"\1[REDACTED_PASSWORD]"),
    (re.compile(r'(?i)(client_secret\s*[:=]\s*)["\']?[^\s",;]+["\']?'), r"\1[REDACTED_SECRET]"),
    (re.compile(r'(?i)(User\s*ID\s*=[^;]+;\s*Password\s*=[^;]+)', re.IGNORECASE), "User ID=[REDACTED];Password=[REDACTED]"),
    (re.compile(r'(?i)(Server\s*=[^;]+;\s*Database\s*=[^;]+;\s*Uid\s*=[^;]+;\s*Pwd\s*=[^;]+)', re.IGNORECASE), "Server=[REDACTED];Database=[REDACTED];Uid=[REDACTED];Pwd=[REDACTED]"),
]

def redact_sensitive_data(text: str) -> str:
    """Sanitizes text removing connection strings, secrets, and credentials."""
    if not text:
        return ""
    redacted = str(text)
    for pattern, repl in REDACTION_PATTERNS:
        redacted = pattern.sub(repl, redacted)
    return redacted


redact_sensitive_vs_data = redact_sensitive_data


# -----------------------------------------------------------------------------
# Visual Studio Safety Gate
# -----------------------------------------------------------------------------

class VSSafetyGate:
    """
    Deterministic safety validator for all Visual Studio Agent operations.
    Guarantees boundary containment within C:\\NR-AI, tool allowlists,
    file protection, and thread-safe emergency stop.
    """

    _emergency_stop: bool = False
    _emergency_lock = threading.Lock()

    def __init__(self, authorized_project: Optional[Path] = None):
        self.authorized_project = Path(authorized_project or DEFAULT_AUTHORIZED_PROJECT).resolve()

    # -------------------------------------------------------------------------
    # Emergency Stop Controls
    # -------------------------------------------------------------------------

    @classmethod
    def activate_emergency_stop(cls) -> None:
        with cls._emergency_lock:
            cls._emergency_stop = True
            logger.critical("[VSSafety] EMERGENCY STOP ACTIVATED. All Visual Studio operations frozen.")

    @classmethod
    def deactivate_emergency_stop(cls) -> None:
        with cls._emergency_lock:
            cls._emergency_stop = False
            logger.info("[VSSafety] Emergency stop deactivated.")

    @classmethod
    def is_emergency_stop_active(cls) -> bool:
        with cls._emergency_lock:
            return cls._emergency_stop

    @classmethod
    def trigger_emergency_stop(cls) -> None:
        cls.activate_emergency_stop()

    @classmethod
    def is_emergency_stopped(cls) -> bool:
        return cls.is_emergency_stop_active()

    def check_emergency_stop(self) -> None:
        if self.is_emergency_stop_active():
            raise EmergencyStopActiveError()

    # -------------------------------------------------------------------------
    # Project & Path Validation
    # -------------------------------------------------------------------------

    def validate_project_path(self, raw_path: Union[str, Path]) -> Path:
        """Validates that project path is strictly within the authorized boundary C:\\NR-AI."""
        self.check_emergency_stop()
        if not raw_path:
            return self.authorized_project

        p_str = str(raw_path).strip()
        # Reject traversal tokens explicitly
        if ".." in p_str.replace("\\", "/").split("/"):
            raise VSSafetyError(
                VSErrorCode.PATH_TRAVERSAL_DETECTED,
                f"Path traversal ('..') detected in path: '{raw_path}'",
            )

        try:
            resolved = Path(raw_path).resolve()
        except Exception as e:
            raise VSSafetyError(VSErrorCode.PROJECT_NOT_AUTHORIZED, f"Invalid project path: {e}")

        # Check containment within global workspace root
        try:
            resolved.relative_to(GLOBAL_WORKSPACE_ROOT)
        except ValueError:
            raise VSSafetyError(
                VSErrorCode.PROJECT_NOT_AUTHORIZED,
                f"Project path '{resolved}' is outside authorized workspace root '{GLOBAL_WORKSPACE_ROOT}'.",
            )

        return resolved

    def validate_file_path(self, raw_path: Union[str, Path], check_writable: bool = True) -> Path:
        """Validates that a file path is authorized, within boundary, and not protected."""
        self.check_emergency_stop()
        p_str = str(raw_path).strip()

        # Reject path traversal
        if ".." in p_str.replace("\\", "/").split("/"):
            raise VSSafetyError(
                VSErrorCode.PATH_TRAVERSAL_DETECTED,
                f"Path traversal detected in file path: '{raw_path}'",
            )

        try:
            resolved = Path(raw_path).resolve()
        except Exception as e:
            raise VSSafetyError(VSErrorCode.FILE_NOT_AUTHORIZED, f"Invalid file path: {e}")

        # Must be inside global workspace root
        try:
            resolved.relative_to(GLOBAL_WORKSPACE_ROOT)
        except ValueError:
            raise VSSafetyError(
                VSErrorCode.FILE_NOT_AUTHORIZED,
                f"File path '{resolved}' is outside authorized workspace root '{GLOBAL_WORKSPACE_ROOT}'.",
            )

        # Check protected directory names in path parts
        parts_lower = [part.lower() for part in resolved.parts]
        for prot_dir in PROTECTED_VS_DIRECTORIES:
            if prot_dir.lower() in parts_lower:
                raise VSSafetyError(
                    VSErrorCode.PROTECTED_DIRECTORY_REJECTED,
                    f"Access to protected directory '{prot_dir}' in path '{resolved}' is forbidden.",
                )

        # Check protected filenames
        if resolved.name.lower() in PROTECTED_VS_FILENAMES:
            raise VSSafetyError(
                VSErrorCode.PROTECTED_FILE_REJECTED,
                f"Access to sensitive protected file '{resolved.name}' is forbidden.",
            )

        # Check protected extensions
        ext = resolved.suffix.lower()
        if ext in PROTECTED_VS_EXTENSIONS:
            raise VSSafetyError(
                VSErrorCode.PROTECTED_FILE_REJECTED,
                f"File extension '{ext}' is protected and cannot be inspected or modified.",
            )

        # For writable operations, enforce strict allowlist
        if check_writable:
            if ext not in ALLOWED_VS_EXTENSIONS:
                raise VSSafetyError(
                    VSErrorCode.FILE_NOT_AUTHORIZED,
                    f"File extension '{ext}' is not in the allowed Visual Studio extensions allowlist.",
                )

        return resolved

    # -------------------------------------------------------------------------
    # Tool and Action Validation
    # -------------------------------------------------------------------------

    def validate_tool_name(self, tool_name: str) -> str:
        """Validates that the requested tool is in the strictly approved 12-tool allowlist."""
        self.check_emergency_stop()
        clean = (tool_name or "").strip()
        if clean not in ALLOWED_VS_TOOLS:
            raise VSSafetyError(
                VSErrorCode.TOOL_NOT_ALLOWED,
                f"Tool '{clean}' is not in the approved Visual Studio tool allowlist. Allowed tools: {sorted(list(ALLOWED_VS_TOOLS))}",
            )
        return clean

    def validate_build_action(self, action: str) -> str:
        """Validates build action against allowlist."""
        self.check_emergency_stop()
        clean = (action or "").strip().upper()
        if clean not in ALLOWED_BUILD_ACTIONS:
            raise VSSafetyError(
                VSErrorCode.ACTION_NOT_ALLOWED,
                f"Build action '{action}' is not authorized. Allowed actions: {sorted(list(ALLOWED_BUILD_ACTIONS))}",
            )
        return clean

    # -------------------------------------------------------------------------
    # Code Editing Limits & Hash Validation
    # -------------------------------------------------------------------------

    def validate_patch_size(self, patch_bytes: int) -> None:
        """Enforces patch size ceiling."""
        if patch_bytes > MAX_PATCH_SIZE_BYTES:
            raise VSSafetyError(
                VSErrorCode.PATCH_TOO_LARGE,
                f"Patch size ({patch_bytes} bytes) exceeds maximum limit ({MAX_PATCH_SIZE_BYTES} bytes).",
            )

    def validate_file_size(self, file_path: Path) -> None:
        """Enforces target file size limit."""
        if file_path.exists() and file_path.stat().st_size > MAX_FILE_SIZE_BYTES:
            raise VSSafetyError(
                VSErrorCode.FILE_TOO_LARGE,
                f"Target file '{file_path.name}' ({file_path.stat().st_size} bytes) exceeds limit ({MAX_FILE_SIZE_BYTES} bytes).",
            )

    def validate_batch_file_count(self, file_count: int) -> None:
        """Enforces maximum file modification count per edit transaction."""
        if file_count > MAX_FILES_CHANGED:
            raise VSSafetyError(
                VSErrorCode.TOO_MANY_FILES_CHANGED,
                f"Requested modification of {file_count} files exceeds limit of {MAX_FILES_CHANGED} files per batch.",
            )

    def validate_lines_changed(self, lines_count: int) -> None:
        """Enforces maximum lines changed per single edit."""
        if lines_count > MAX_LINES_CHANGED:
            raise VSSafetyError(
                VSErrorCode.TOO_MANY_LINES_CHANGED,
                f"Proposed change of {lines_count} lines exceeds limit of {MAX_LINES_CHANGED} lines.",
            )

    def validate_patch_content(self, content: str) -> None:
        """Enforces patch size and line count bounds."""
        if not content:
            return
        b_len = len(content.encode("utf-8"))
        self.validate_patch_size(b_len)
        lines = len(content.splitlines())
        self.validate_lines_changed(lines)

    def validate_edit_batch(self, batch: Any) -> None:
        """Validates an edit batch against file count and individual patch limits."""
        proposals = getattr(batch, "proposals", None) or getattr(batch, "files", None) or []
        self.validate_batch_file_count(len(proposals))
        for p in proposals:
            f_path = getattr(p, "file_path", None)
            if f_path:
                self.validate_file_path(f_path, check_writable=True)
            p_content = getattr(p, "new_content", None) or getattr(p, "patch", None) or ""
            if p_content:
                self.validate_patch_content(p_content)

    @staticmethod
    def compute_sha256(file_path: Path) -> str:
        """Calculates SHA-256 hash of a file on disk."""
        if not file_path.exists():
            return ""
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        return hasher.hexdigest()

    def verify_target_hash(self, file_path: Path, expected_sha256: str) -> bool:
        """Verifies that the target file on disk matches the expected SHA-256 hash."""
        if not file_path.exists():
            return False
        current = self.compute_sha256(file_path)
        if current.lower() != expected_sha256.strip().lower():
            raise VSSafetyError(
                VSErrorCode.STALE_TARGET,
                f"Target file '{file_path.name}' hash mismatch: expected {expected_sha256[:8]}..., current is {current[:8]}... File was modified externally.",
            )
        return True
