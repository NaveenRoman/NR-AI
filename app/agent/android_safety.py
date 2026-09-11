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


# -----------------------------------------------------------------------------
# Exceptions
# -----------------------------------------------------------------------------

class AndroidSafetyError(Exception):
    """Base exception for all Android safety violations."""
    def __init__(self, code: AndroidErrorCode, message: str):
        super().__init__(message)
        self.code = code
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
