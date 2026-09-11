"""
NR-AI Android Safety Engine & Deterministic Contracts (Step 6 Phase 1).

Enforces strict deterministic boundaries:
1. Authorized Project Boundary: ONLY C:\\NR-AI\\nr_android_test (package com.nrai.test)
2. Authorized Device Allowlist: emulator-5554, 15930545720012G
3. Authorized AVDs: Pixel_6_API_34 (Pixel_6_API_35 explicitly blocked)
4. Authorized Build Actions: DEBUG_ASSEMBLE, CLEAN, CHECK
5. Tool Allowlist: 15 strictly enumerated android.* tools
6. APK Safety: Authorized project build outputs only, verified package identity
7. Emergency Stop: Immediate freeze of all Android operations
8. No arbitrary shell, ADB, or Gradle passthrough
"""

from dataclasses import dataclass, field
from enum import Enum
import logging
import os
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union

logger = logging.getLogger("NRAI.AndroidSafety")

# -----------------------------------------------------------------------------
# Structured Error Constants
# -----------------------------------------------------------------------------

class AndroidErrorCode(str, Enum):
    PROJECT_NOT_AUTHORIZED = "PROJECT_NOT_AUTHORIZED"
    DEVICE_NOT_AUTHORIZED = "DEVICE_NOT_AUTHORIZED"
    APK_NOT_AUTHORIZED = "APK_NOT_AUTHORIZED"
    PACKAGE_MISMATCH = "PACKAGE_MISMATCH"
    BUILD_NOT_ALLOWED = "BUILD_NOT_ALLOWED"
    ANDROID_STUDIO_NOT_FOUND = "ANDROID_STUDIO_NOT_FOUND"
    DEVICE_NOT_FOUND = "DEVICE_NOT_FOUND"
    APP_NOT_FOUND = "APP_NOT_FOUND"
    ACTION_NOT_ALLOWED = "ACTION_NOT_ALLOWED"
    EMERGENCY_STOPPED = "EMERGENCY_STOPPED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    INSTALL_CONFIRMATION_REQUIRED = "INSTALL_CONFIRMATION_REQUIRED"
    BUILD_FAILED = "BUILD_FAILED"
    INSTALL_FAILED = "INSTALL_FAILED"
    LAUNCH_FAILED = "LAUNCH_FAILED"
    DEVICE_DISCONNECTED = "DEVICE_DISCONNECTED"
    APK_NOT_FOUND = "APK_NOT_FOUND"
    FILE_NOT_AUTHORIZED = "FILE_NOT_AUTHORIZED"
    PROTECTED_FILE_REJECTED = "PROTECTED_FILE_REJECTED"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    PATCH_TOO_LARGE = "PATCH_TOO_LARGE"
    TOO_MANY_FILES_CHANGED = "TOO_MANY_FILES_CHANGED"
    STALE_TARGET = "STALE_TARGET"
    EDIT_VALIDATION_FAILED = "EDIT_VALIDATION_FAILED"
    REPAIR_FAILED = "REPAIR_FAILED"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"


# -----------------------------------------------------------------------------
# Exceptions
# -----------------------------------------------------------------------------

class AndroidSafetyError(Exception):
    """Base exception for all Android safety violations."""
    def __init__(self, code: AndroidErrorCode, message: str):
        super().__init__(message)
        self.code = code
        self.error_code = code
        self.message = message


class EmergencyStopActiveError(AndroidSafetyError):
    """Raised when an operation is attempted while emergency stop is active."""
    def __init__(self, message: str = "EMERGENCY STOP is active. All Android operations are frozen."):
        super().__init__(AndroidErrorCode.EMERGENCY_STOPPED, message)


class RateLimitExceededError(AndroidSafetyError):
    """Raised when action execution rate exceeds safety limits."""
    def __init__(self, message: str = "Android action rate limit exceeded."):
        super().__init__(AndroidErrorCode.ACTION_NOT_ALLOWED, message)


# -----------------------------------------------------------------------------
# System Whitelists & Configuration
# -----------------------------------------------------------------------------

AUTHORIZED_PROJECT_PATH = Path(r"C:\NR-AI\nr_android_test").resolve()
AUTHORIZED_PACKAGE_NAME = "com.nrai.test"

AUTHORIZED_DEVICE_SERIALS: Set[str] = {
    "emulator-5554",
    "15930545720012G",
}

AUTHORIZED_AVDS: Set[str] = {
    "Pixel_6_API_34",
}

BLOCKED_AVDS: Set[str] = {
    "Pixel_6_API_35",  # Broken/unused AVD per user instructions
}

AUTHORIZED_BUILD_ACTIONS: Dict[str, List[str]] = {
    "DEBUG_ASSEMBLE": ["assembleDebug"],
    "CLEAN": ["clean"],
    "CHECK": ["check"],
}

ALLOWED_ANDROID_TOOLS: Set[str] = {
    "android.list_devices",
    "android.get_device_status",
    "android.launch_studio",
    "android.focus_studio",
    "android.open_project",
    "android.inspect_project",
    "android.build_project",
    "android.get_build_status",
    "android.list_emulators",
    "android.start_emulator",
    "android.stop_emulator",
    "android.install_test_apk",
    "android.launch_app",
    "android.capture_log",
    "android.verify_app",
}

DEFAULT_STUDIO_PATH = Path(r"C:\Program Files\Android\Android Studio1\bin\studio64.exe")
DEFAULT_JDK_PATH = Path(r"C:\Program Files\Android\Android Studio1\jbr")
DEFAULT_SDK_PATH = Path(os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk"))
DEFAULT_ADB_PATH = Path(os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"))
DEFAULT_EMULATOR_PATH = Path(os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\emulator\emulator.exe"))

# -----------------------------------------------------------------------------
# File Policy & Code Modification Constants (Step 6 Phase 3)
# -----------------------------------------------------------------------------

ALLOWED_ANDROID_EXTENSIONS: Set[str] = {
    ".java",
    ".kt",
    ".xml",
    ".gradle",
    ".gradle.kts",
    ".properties",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".md",
}

FORBIDDEN_ANDROID_EXTENSIONS: Set[str] = {
    ".exe",
    ".bat",
    ".cmd",
    ".ps1",
    ".py",
    ".dll",
    ".so",
    ".apk",
    ".aab",
    ".class",
    ".jar",
}

PROTECTED_ANDROID_FILES: Set[str] = {
    "local.properties",
    "google-services.json",
}

MAX_EDITABLE_FILE_SIZE_BYTES: int = 1024 * 1024  # 1 MB
MAX_PATCH_SIZE_BYTES: int = 100 * 1024  # 100 KB
MAX_FILES_PER_REPAIR: int = 5
MAX_LINES_PER_EDIT: int = 500
MAX_REPAIR_ATTEMPTS: int = 2


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


TOOL_RISK_MAP: Dict[str, RiskLevel] = {
    "android.list_devices": RiskLevel.LOW,
    "android.get_device_status": RiskLevel.LOW,
    "android.launch_studio": RiskLevel.MEDIUM,
    "android.focus_studio": RiskLevel.LOW,
    "android.open_project": RiskLevel.MEDIUM,
    "android.inspect_project": RiskLevel.LOW,
    "android.build_project": RiskLevel.MEDIUM,
    "android.get_build_status": RiskLevel.LOW,
    "android.list_emulators": RiskLevel.LOW,
    "android.start_emulator": RiskLevel.MEDIUM,
    "android.stop_emulator": RiskLevel.MEDIUM,
    "android.install_test_apk": RiskLevel.HIGH,
    "android.launch_app": RiskLevel.MEDIUM,
    "android.capture_log": RiskLevel.LOW,
    "android.verify_app": RiskLevel.LOW,
}


# -----------------------------------------------------------------------------
# Rate Limiting & Safety Gate
# -----------------------------------------------------------------------------

class AndroidRateLimiter:
    """Sliding-window rate limiter preventing excessive rapid Android tool invocations."""

    def __init__(self, max_actions_per_minute: int = 20):
        self.max_actions = max_actions_per_minute
        self.timestamps: List[float] = []

    def check_and_record(self) -> None:
        now = time.time()
        cutoff = now - 60.0
        self.timestamps = [t for t in self.timestamps if t > cutoff]
        if len(self.timestamps) >= self.max_actions:
            raise RateLimitExceededError(
                f"Rate limit exceeded: maximum {self.max_actions} actions per minute allowed."
            )
        self.timestamps.append(now)

    def reset(self) -> None:
        self.timestamps.clear()


class AndroidSafetyGate:
    """
    Central safety validator for Android Studio and device operations.
    Authoritatively checks boundaries, allowlists, risks, and emergency stop.
    """

    _emergency_stop: bool = False

    def __init__(
        self,
        authorized_project: Path = AUTHORIZED_PROJECT_PATH,
        authorized_package: str = AUTHORIZED_PACKAGE_NAME,
        rate_limiter: Optional[AndroidRateLimiter] = None,
    ):
        self.authorized_project = authorized_project.resolve()
        self.authorized_package = authorized_package
        self.rate_limiter = rate_limiter or AndroidRateLimiter(max_actions_per_minute=20)

    # -------------------------------------------------------------------------
    # Emergency Stop
    # -------------------------------------------------------------------------

    @classmethod
    def activate_emergency_stop(cls) -> None:
        cls._emergency_stop = True
        logger.warning("[AndroidSafety] EMERGENCY STOP ACTIVATED. All Android operations frozen.")

    @classmethod
    def deactivate_emergency_stop(cls) -> None:
        cls._emergency_stop = False
        logger.info("[AndroidSafety] Emergency stop cleared.")

    @classmethod
    def is_emergency_stop_active(cls) -> bool:
        return cls._emergency_stop

    def check_emergency_stop(self) -> None:
        if self.is_emergency_stop_active():
            raise EmergencyStopActiveError()

    # -------------------------------------------------------------------------
    # Validation Methods
    # -------------------------------------------------------------------------

    def validate_tool_name(self, tool_name: str) -> None:
        """Ensures tool is in the approved 15-tool allowlist."""
        self.check_emergency_stop()
        if not tool_name or tool_name not in ALLOWED_ANDROID_TOOLS:
            raise AndroidSafetyError(
                AndroidErrorCode.ACTION_NOT_ALLOWED,
                f"Tool '{tool_name}' is not in the authorized Android tool allowlist.",
            )

    def validate_project_path(self, project_path: Optional[Union[str, Path]]) -> Path:
        """
        Validates that the project directory resolves strictly inside or to
        the authorized project path C:\\NR-AI\\nr_android_test.
        """
        self.check_emergency_stop()
        if not project_path:
            return self.authorized_project

        try:
            resolved = Path(project_path).resolve()
        except Exception as e:
            raise AndroidSafetyError(
                AndroidErrorCode.PROJECT_NOT_AUTHORIZED,
                f"Invalid project path specification: {e}",
            )

        if resolved != self.authorized_project:
            raise AndroidSafetyError(
                AndroidErrorCode.PROJECT_NOT_AUTHORIZED,
                f"Project path '{resolved}' is not authorized. Only '{self.authorized_project}' is authorized.",
            )

        if not resolved.exists() or not resolved.is_dir():
            raise AndroidSafetyError(
                AndroidErrorCode.PROJECT_NOT_AUTHORIZED,
                f"Authorized project directory '{resolved}' does not exist.",
            )

        return resolved

    def validate_device_serial(self, serial: str) -> str:
        """Ensures device serial is in the authorized test device allowlist."""
        self.check_emergency_stop()
        clean_serial = str(serial or "").strip()
        if not clean_serial or clean_serial not in AUTHORIZED_DEVICE_SERIALS:
            raise AndroidSafetyError(
                AndroidErrorCode.DEVICE_NOT_AUTHORIZED,
                f"Device '{clean_serial}' is not in the authorized device allowlist {sorted(AUTHORIZED_DEVICE_SERIALS)}.",
            )
        return clean_serial

    def validate_avd_name(self, avd_name: str) -> str:
        """Ensures AVD is in the authorized AVD allowlist and not blocked."""
        self.check_emergency_stop()
        clean_avd = str(avd_name or "").strip()
        if clean_avd in BLOCKED_AVDS:
            raise AndroidSafetyError(
                AndroidErrorCode.ACTION_NOT_ALLOWED,
                f"AVD '{clean_avd}' is explicitly blocked (broken/unused).",
            )
        if clean_avd not in AUTHORIZED_AVDS:
            raise AndroidSafetyError(
                AndroidErrorCode.ACTION_NOT_ALLOWED,
                f"AVD '{clean_avd}' is not in the authorized AVD allowlist {sorted(AUTHORIZED_AVDS)}.",
            )
        return clean_avd

    def validate_build_action(self, action_name: str) -> List[str]:
        """Ensures build action is in the authorized fixed build actions."""
        self.check_emergency_stop()
        clean_action = str(action_name or "").strip().upper()
        if clean_action not in AUTHORIZED_BUILD_ACTIONS:
            raise AndroidSafetyError(
                AndroidErrorCode.BUILD_NOT_ALLOWED,
                f"Build action '{action_name}' is not allowed. Must be one of {list(AUTHORIZED_BUILD_ACTIONS.keys())}.",
            )
        return AUTHORIZED_BUILD_ACTIONS[clean_action]

    def validate_apk_path(self, apk_path: Union[str, Path]) -> Path:
        """
        Validates that the APK belongs to the authorized project's build output boundary
        and is named or structured for com.nrai.test.
        """
        self.check_emergency_stop()
        try:
            resolved = Path(apk_path).resolve()
        except Exception as e:
            raise AndroidSafetyError(
                AndroidErrorCode.APK_NOT_AUTHORIZED,
                f"Invalid APK path specification: {e}",
            )

        # Must reside strictly within authorized project build output directory
        expected_build_dir = (self.authorized_project / "app" / "build").resolve()
        try:
            resolved.relative_to(expected_build_dir)
        except ValueError:
            raise AndroidSafetyError(
                AndroidErrorCode.APK_NOT_AUTHORIZED,
                f"APK path '{resolved}' is outside the authorized project build directory '{expected_build_dir}'.",
            )

        if not resolved.name.endswith(".apk"):
            raise AndroidSafetyError(
                AndroidErrorCode.APK_NOT_AUTHORIZED,
                f"Target file '{resolved.name}' is not an APK file.",
            )

        if not resolved.exists():
            raise AndroidSafetyError(
                AndroidErrorCode.APK_NOT_AUTHORIZED,
                f"Target APK '{resolved}' does not exist on disk.",
            )

        return resolved

    def revalidate_apk_integrity(
        self,
        apk_path: Union[str, Path],
        expected_mtime: Optional[float] = None,
        expected_size: Optional[int] = None,
    ) -> Path:
        """
        Revalidates APK immediately prior to deployment:
        - Confirms path validity and existence
        - Verifies APK size > 0
        - Optionally verifies mtime / size have not unexpectedly changed
        """
        self.check_emergency_stop()
        resolved = self.validate_apk_path(apk_path)
        stat = resolved.stat()
        if stat.st_size <= 0:
            raise AndroidSafetyError(
                AndroidErrorCode.APK_NOT_AUTHORIZED,
                f"APK '{resolved.name}' has invalid zero byte size.",
            )
        if expected_size is not None and stat.st_size != expected_size:
            raise AndroidSafetyError(
                AndroidErrorCode.APK_NOT_AUTHORIZED,
                f"APK '{resolved.name}' size mismatch: expected {expected_size}, got {stat.st_size}.",
            )
        if expected_mtime is not None and abs(stat.st_mtime - expected_mtime) > 1.0:
            raise AndroidSafetyError(
                AndroidErrorCode.APK_NOT_AUTHORIZED,
                f"APK '{resolved.name}' timestamp changed unexpectedly.",
            )
        return resolved

    def validate_package_name(self, package_name: str) -> str:
        """Ensures package name matches com.nrai.test."""
        self.check_emergency_stop()
        clean_pkg = str(package_name or "").strip()
        if clean_pkg != self.authorized_package:
            raise AndroidSafetyError(
                AndroidErrorCode.PACKAGE_MISMATCH,
                f"Package '{clean_pkg}' does not match authorized package '{self.authorized_package}'.",
            )
        return clean_pkg

    def assess_risk(self, tool_name: str, params: Dict[str, Any]) -> Tuple[RiskLevel, bool]:
        """
        Determines risk level and whether explicit user confirmation is required.
        HIGH risk tools (e.g. android.install_test_apk) require confirmation.
        """
        risk = TOOL_RISK_MAP.get(tool_name, RiskLevel.MEDIUM)
        requires_confirmation = (risk == RiskLevel.HIGH)
        return risk, requires_confirmation

    # -------------------------------------------------------------------------
    # Code Editing & File Boundary Methods (Step 6 Phase 3)
    # -------------------------------------------------------------------------

    def validate_editable_file(
        self,
        file_path: Union[str, Path],
        is_creation: bool = False,
    ) -> Path:
        """
        Validates that a file is authorized for code inspection or editing:
        - Resolves canonical path strictly inside C:\\NR-AI\\nr_android_test
        - Rejects path traversal (.., \\\\, UNC, drive switching)
        - Rejects protected files (local.properties, *.jks, *.keystore, google-services.json)
        - Rejects unauthorized or dangerous extensions
        - Verifies existence (or non-existence if is_creation=True)
        - Verifies file size <= 1 MB
        """
        self.check_emergency_stop()
        raw_str = str(file_path or "").strip()
        if not raw_str:
            raise AndroidSafetyError(
                AndroidErrorCode.FILE_NOT_AUTHORIZED,
                "File path cannot be empty.",
            )

        # 1. Traversal and UNC checks
        if ".." in raw_str or raw_str.startswith(r"\\") or raw_str.startswith("//"):
            raise AndroidSafetyError(
                AndroidErrorCode.FILE_NOT_AUTHORIZED,
                f"Path traversal or UNC path rejected: '{raw_str}'.",
            )

        try:
            target = Path(raw_str)
            if not target.is_absolute():
                resolved = (self.authorized_project / target).resolve()
            else:
                resolved = target.resolve()
        except Exception as e:
            raise AndroidSafetyError(
                AndroidErrorCode.FILE_NOT_AUTHORIZED,
                f"Invalid file path resolution '{raw_str}': {e}",
            )

        # 2. Strict Project Boundary Check
        try:
            resolved.relative_to(self.authorized_project)
        except ValueError:
            raise AndroidSafetyError(
                AndroidErrorCode.FILE_NOT_AUTHORIZED,
                f"Path '{resolved}' is outside authorized project '{self.authorized_project}'.",
            )

        # 3. Protected Android Files Check
        name_lower = resolved.name.lower()
        if (
            name_lower in PROTECTED_ANDROID_FILES
            or name_lower == ".env"
            or name_lower.startswith(".env")
            or name_lower.endswith(".jks")
            or name_lower.endswith(".keystore")
        ):
            raise AndroidSafetyError(
                AndroidErrorCode.PROTECTED_FILE_REJECTED,
                f"File '{resolved.name}' is a protected configuration or credential file and cannot be modified.",
            )

        # Additional sensitive keyword check in non-source files
        ext = resolved.suffix.lower()
        if resolved.name.lower().endswith(".gradle.kts"):
            ext = ".gradle.kts"

        for sensitive in ("keystore", "credential", "secret", "token", "password", "env"):
            if sensitive in name_lower and ext not in (".kt", ".java"):
                raise AndroidSafetyError(
                    AndroidErrorCode.PROTECTED_FILE_REJECTED,
                    f"File '{resolved.name}' contains protected sensitive keyword '{sensitive}'.",
                )

        # 4. File Extension Allowlist Check
        if ext in FORBIDDEN_ANDROID_EXTENSIONS or ext not in ALLOWED_ANDROID_EXTENSIONS:
            raise AndroidSafetyError(
                AndroidErrorCode.FILE_NOT_AUTHORIZED,
                f"File extension '{ext}' for '{resolved.name}' is not authorized. Allowed: {sorted(ALLOWED_ANDROID_EXTENSIONS)}.",
            )

        # 5. Existence Check
        if not is_creation and not resolved.exists():
            raise AndroidSafetyError(
                AndroidErrorCode.FILE_NOT_AUTHORIZED,
                f"Target file '{resolved}' does not exist inside authorized project.",
            )

        if is_creation and resolved.exists():
            raise AndroidSafetyError(
                AndroidErrorCode.EDIT_VALIDATION_FAILED,
                f"Target file '{resolved}' already exists; cannot recreate existing file.",
            )

        # 6. File Size Limit Check (<= 1 MB)
        if resolved.exists():
            size = resolved.stat().st_size
            if size > MAX_EDITABLE_FILE_SIZE_BYTES:
                raise AndroidSafetyError(
                    AndroidErrorCode.FILE_TOO_LARGE,
                    f"File '{resolved.name}' size ({size} bytes) exceeds limit ({MAX_EDITABLE_FILE_SIZE_BYTES} bytes).",
                )

        return resolved

    def validate_patch_size(self, patch_data: Union[int, str, bytes]) -> bool:
        """Ensures proposed patch does not exceed maximum patch size (100 KB)."""
        self.check_emergency_stop()
        if isinstance(patch_data, str):
            num_bytes = len(patch_data.encode("utf-8"))
        elif isinstance(patch_data, (bytes, bytearray)):
            num_bytes = len(patch_data)
        else:
            num_bytes = int(patch_data)

        if num_bytes > MAX_PATCH_SIZE_BYTES:
            raise AndroidSafetyError(
                AndroidErrorCode.PATCH_TOO_LARGE,
                f"Proposed patch size ({num_bytes} bytes) exceeds limit ({MAX_PATCH_SIZE_BYTES} bytes).",
            )
        return True

    @property
    def authorized_devices(self) -> Set[str]:
        return AUTHORIZED_DEVICE_SERIALS

    def validate_prohibited_content(self, content: str) -> None:
        """Validates that replacement text does not contain prohibited shell execution or plain credentials."""
        self.check_emergency_stop()
        prohibited_patterns = [
            (r"Runtime\.getRuntime\(\)\.exec", "Runtime.getRuntime().exec"),
            (r"ProcessBuilder", "ProcessBuilder"),
            (r"System\.exit", "System.exit"),
            (r"subprocess", "subprocess"),
            (r"os\.system", "os.system"),
            (r"os\.popen", "os.popen"),
            (r"powershell", "powershell"),
            (r"cmd\.exe", "cmd.exe"),
            (r"adb\s+shell", "adb shell"),
            (r"shell\s*=\s*True", "shell=True"),
            (r"eval\(", "eval()"),
            (r"exec\(", "exec()"),
            (r"AIzaSy[A-Za-z0-9_\-]{20,}", "Plain Google API Key"),
            (r"sk-proj-[A-Za-z0-9_\-]{20,}", "Plain OpenAI API Key"),
        ]
        for pat, desc in prohibited_patterns:
            if re.search(pat, content, re.IGNORECASE):
                raise AndroidSafetyError(
                    AndroidErrorCode.EDIT_VALIDATION_FAILED,
                    f"Proposed change contains prohibited construct: '{desc}'.",
                )
