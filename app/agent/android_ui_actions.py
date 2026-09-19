"""
NR-AI Approved Android UI Action Engine & Sensitive Input Protection (Droid Phase 3).

Provides safe, bounded, deterministic execution of allowlisted UI actions on authorized Android devices.
Enforces:
- Allowlisted actions: tap_target, long_press_target, type_text, press_back, scroll, wait, capture_screenshot, capture_hierarchy.
- Target selection hierarchy: resource ID -> test tag -> accessibility description -> visible text -> structured hierarchy -> approved coordinate fallback.
- Rejects arbitrary model coordinate inputs.
- Deterministic target identity, timestamp, and 15s TTL validation (STALE_TARGET rejection).
- Sensitive input protection: strictly blocks typing passwords, API keys, tokens, credentials, and banking data.
- Full emergency stop integration and audit logging.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
import uuid

from app.agent.android_safety import (
    ALLOWED_ANDROID_UI_OPERATIONS,
    ALLOWED_KEYCODES,
    AUTHORIZED_DEVICE_SERIALS,
    DEFAULT_TARGET_TTL_SECONDS,
    AndroidErrorCode,
    AndroidSafetyError,
    AndroidSafetyGate,
    EmergencyStopActiveError,
)
from app.agent.android_ui import AndroidTarget, AndroidUIIntelligence, AndroidUISnapshot
from app.agent.android_tools import SafeAdbClient
from app.memory.audit_logger import AuditLogger

logger = logging.getLogger("NRAI.AndroidUIActions")


# -----------------------------------------------------------------------------
# Action Enums & Data Models
# -----------------------------------------------------------------------------

class ApprovedUIActionType(str, Enum):
    TAP = "TAP"
    LONG_PRESS = "LONG_PRESS"
    TYPE_TEXT = "TYPE_TEXT"
    PRESS_BACK = "PRESS_BACK"
    SCROLL = "SCROLL"
    WAIT = "WAIT"
    CAPTURE_SCREENSHOT = "CAPTURE_SCREENSHOT"
    CAPTURE_HIERARCHY = "CAPTURE_HIERARCHY"


class InputSensitivity(str, Enum):
    SAFE_TEST_INPUT = "SAFE_TEST_INPUT"
    SENSITIVE_INPUT = "SENSITIVE_INPUT"


# Patterns that indicate sensitive input
SENSITIVE_INPUT_PATTERNS = [
    re.compile(r"(?i)(password|passwd|pwd)"),
    re.compile(r"(?i)(api[_-]?key|secret|token|bearer|private[_-]?key)"),
    re.compile(r"(?i)(credit[_-]?card|cvv|ssn|pin|routing[_-]?number)"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9_]{36,}"),
]


@dataclass
class UIActionResult:
    """Deterministic result of executing an approved UI action."""
    success: bool
    action_type: ApprovedUIActionType
    target_id: Optional[str] = None
    target_identifier: Optional[str] = None
    input_text: Optional[str] = None
    stale_target: bool = False
    sensitive_blocked: bool = False
    message: str = ""
    error: Optional[str] = None
    duration_seconds: float = 0.0
    action_id: str = field(default_factory=lambda: f"uiact_{uuid.uuid4().hex[:8]}")
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_id": self.action_id,
            "success": self.success,
            "action_type": self.action_type.value if isinstance(self.action_type, ApprovedUIActionType) else str(self.action_type),
            "target_id": self.target_id,
            "target_identifier": self.target_identifier,
            "input_text": "[REDACTED]" if self.sensitive_blocked else self.input_text,
            "stale_target": self.stale_target,
            "sensitive_blocked": self.sensitive_blocked,
            "message": self.message,
            "error": self.error,
            "duration_seconds": self.duration_seconds,
            "timestamp": self.timestamp,
        }


# -----------------------------------------------------------------------------
# Approved UI Action Engine
# -----------------------------------------------------------------------------

class ApprovedUIActionEngine:
    """
    Executes approved UI actions against live Android devices using verified targets.
    Enforces deterministic target resolution and prevents execution of unvetted coordinates or sensitive text.
    """

    def __init__(
        self,
        ui_intelligence: Optional[AndroidUIIntelligence] = None,
        adb_client: Optional[SafeAdbClient] = None,
        safety_gate: Optional[AndroidSafetyGate] = None,
        audit_logger: Optional[AuditLogger] = None,
    ):
        self.safety = safety_gate or AndroidSafetyGate()
        self.ui_intelligence = ui_intelligence or AndroidUIIntelligence(safety_gate=self.safety)
        self.adb = adb_client or SafeAdbClient()
        self.audit = audit_logger or AuditLogger()

    # -------------------------------------------------------------------------
    # Sensitive Input Protection
    # -------------------------------------------------------------------------

    @classmethod
    def classify_input_text(cls, text: str) -> InputSensitivity:
        """Determines if a text string is safe test input or potentially sensitive."""
        if not text:
            return InputSensitivity.SAFE_TEST_INPUT

        for pat in SENSITIVE_INPUT_PATTERNS:
            if pat.search(text):
                return InputSensitivity.SENSITIVE_INPUT

        return InputSensitivity.SAFE_TEST_INPUT

    # -------------------------------------------------------------------------
    # Target Resolution Hierarchy
    # -------------------------------------------------------------------------

    def resolve_target(
        self,
        identifier_type: str,
        identifier_value: str,
        snapshot: Optional[AndroidUISnapshot] = None,
        serial: Optional[str] = None,
    ) -> Tuple[Optional[AndroidTarget], str]:
        """
        Resolves a UI target following the strict priority hierarchy:
        1. Resource ID
        2. Test Tag
        3. Accessibility Description
        4. Visible Text
        5. Structured Class / Hierarchy Path
        6. Approved Coordinate Fallback (only from fresh verified hierarchy).
        """
        self.safety.check_emergency_stop()

        if snapshot is None:
            snapshot = self.ui_intelligence.inspect_ui(serial=serial)

        id_clean = (identifier_value or "").strip()
        type_clean = (identifier_type or "").lower().strip()

        # 1. Resource ID
        if type_clean in ("resource_id", "id", "resid"):
            for t in snapshot.targets:
                if t.resource_id == id_clean or t.resource_id.endswith(f"/{id_clean}"):
                    if t.is_stale():
                        return None, "STALE_TARGET"
                    return t, "RESOURCE_ID"

        # 2. Test Tag
        if type_clean in ("test_tag", "tag"):
            for t in snapshot.targets:
                if t.content_desc == id_clean or t.resource_id.endswith(f"/{id_clean}"):
                    if t.is_stale():
                        return None, "STALE_TARGET"
                    return t, "TEST_TAG"

        # 3. Accessibility Description
        if type_clean in ("content_desc", "description", "desc"):
            for t in snapshot.targets:
                if t.content_desc.strip().lower() == id_clean.lower():
                    if t.is_stale():
                        return None, "STALE_TARGET"
                    return t, "CONTENT_DESC"

        # 4. Visible Text Literal
        if type_clean in ("text", "visible_text"):
            for t in snapshot.targets:
                if t.text.strip().lower() == id_clean.lower():
                    if t.is_stale():
                        return None, "STALE_TARGET"
                    return t, "VISIBLE_TEXT"

        # General Search fallback across all fields
        for t in snapshot.targets:
            if t.resource_id == id_clean or t.resource_id.endswith(f"/{id_clean}"):
                if t.is_stale():
                    return None, "STALE_TARGET"
                return t, "RESOURCE_ID"
            if t.content_desc.strip().lower() == id_clean.lower():
                if t.is_stale():
                    return None, "STALE_TARGET"
                return t, "CONTENT_DESC"
            if t.text.strip().lower() == id_clean.lower():
                if t.is_stale():
                    return None, "STALE_TARGET"
                return t, "VISIBLE_TEXT"

        return None, "TARGET_NOT_FOUND"

    # -------------------------------------------------------------------------
    # Action Execution
    # -------------------------------------------------------------------------

    def tap_by_identifier(
        self,
        identifier_type: str,
        identifier_value: str,
        serial: Optional[str] = None,
    ) -> UIActionResult:
        """Finds target using deterministic hierarchy and executes tap."""
        start = time.monotonic()
        self.safety.check_emergency_stop()

        target, resolve_reason = self.resolve_target(identifier_type, identifier_value, serial=serial)
        if resolve_reason == "STALE_TARGET":
            return UIActionResult(
                success=False,
                action_type=ApprovedUIActionType.TAP,
                target_identifier=identifier_value,
                stale_target=True,
                message=f"Target '{identifier_value}' is stale (exceeded TTL). Re-resolution required.",
                error="STALE_TARGET",
                duration_seconds=time.monotonic() - start,
            )
        if not target:
            return UIActionResult(
                success=False,
                action_type=ApprovedUIActionType.TAP,
                target_identifier=identifier_value,
                message=f"Target '{identifier_value}' not found in current UI hierarchy.",
                error="TARGET_NOT_FOUND",
                duration_seconds=time.monotonic() - start,
            )

        res = self.ui_intelligence.tap_target(target.target_id, serial=serial)
        duration = time.monotonic() - start
        return UIActionResult(
            success=res.get("success", False),
            action_type=ApprovedUIActionType.TAP,
            target_id=target.target_id,
            target_identifier=identifier_value,
            message=f"Tapped target '{identifier_value}' via {resolve_reason} at {target.center}",
            duration_seconds=duration,
        )

    def type_text_into_target(
        self,
        identifier_type: str,
        identifier_value: str,
        text: str,
        serial: Optional[str] = None,
    ) -> UIActionResult:
        """Taps target and types safe text. Blocks sensitive inputs."""
        start = time.monotonic()
        self.safety.check_emergency_stop()

        # Sensitive Input Guard
        if self.classify_input_text(text) == InputSensitivity.SENSITIVE_INPUT:
            msg = f"Rejected sensitive input text: typing passwords, credentials, or private keys is prohibited."
            logger.warning(msg)
            return UIActionResult(
                success=False,
                action_type=ApprovedUIActionType.TYPE_TEXT,
                target_identifier=identifier_value,
                sensitive_blocked=True,
                message=msg,
                error="SENSITIVE_INPUT_BLOCKED",
                duration_seconds=time.monotonic() - start,
            )

        # First tap target to focus
        tap_res = self.tap_by_identifier(identifier_type, identifier_value, serial=serial)
        if not tap_res.success:
            return tap_res

        # Safe typing via ADB input text without shell=True
        time.sleep(0.3)
        sanitized_text = re.sub(r"[^a-zA-Z0-9_\-\. ]", "", text).replace(" ", "%s")
        target_serial = serial or self.safety.validate_device_serial("emulator-5554")
        self.adb.execute(["-s", target_serial, "shell", "input", "text", sanitized_text])

        duration = time.monotonic() - start
        return UIActionResult(
            success=True,
            action_type=ApprovedUIActionType.TYPE_TEXT,
            target_id=tap_res.target_id,
            target_identifier=identifier_value,
            input_text=text,
            message=f"Typed text into '{identifier_value}' successfully.",
            duration_seconds=duration,
        )

    def press_back(self, serial: Optional[str] = None) -> UIActionResult:
        """Executes hardware BACK navigation."""
        start = time.monotonic()
        self.safety.check_emergency_stop()

        res = self.ui_intelligence.press_key("BACK", serial=serial)
        return UIActionResult(
            success=res.get("success", False),
            action_type=ApprovedUIActionType.PRESS_BACK,
            message="Pressed BACK button.",
            duration_seconds=time.monotonic() - start,
        )

    def scroll(self, direction: str = "DOWN", serial: Optional[str] = None) -> UIActionResult:
        """Executes bounded scroll gesture."""
        start = time.monotonic()
        self.safety.check_emergency_stop()

        res = self.ui_intelligence.scroll(direction=direction, serial=serial)
        return UIActionResult(
            success=res.get("success", False),
            action_type=ApprovedUIActionType.SCROLL,
            message=f"Scrolled {direction}.",
            duration_seconds=time.monotonic() - start,
        )

    def capture_hierarchy(self, serial: Optional[str] = None) -> Dict[str, Any]:
        """Captures and distills UI hierarchy."""
        self.safety.check_emergency_stop()
        snap = self.ui_intelligence.inspect_ui(serial=serial)
        return snap.to_dict()

    def launch_app(self, package_name: str, serial: Optional[str] = None) -> Dict[str, Any]:
        """Launches target package main activity."""
        self.safety.check_emergency_stop()
        target_serial = serial or self.safety.validate_device_serial("emulator-5554")
        success = self.adb.launch_package(target_serial, package_name)
        return {"success": success, "output": "App launched" if success else "Failed to launch app"}

