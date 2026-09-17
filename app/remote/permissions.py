"""
NR-AI Scoped Authorization and Model Isolation Gate.
Step 10 Phase 1 — Secure Phone <-> PC Communication Foundation.
"""

from dataclasses import dataclass
from enum import Enum
import re
from typing import Dict, List, Optional, Set, Tuple


class PhonePermissionScope(str, Enum):
    READ_STATUS = "READ_STATUS"
    READ_DEVICE_INFO = "READ_DEVICE_INFO"
    SEND_COMMAND = "SEND_COMMAND"
    VOICE_COMMAND = "VOICE_COMMAND"
    APPROVED_COMPUTER_ACTION = "APPROVED_COMPUTER_ACTION"
    EMERGENCY_STOP = "EMERGENCY_STOP"
    READ_TELEMETRY = "READ_TELEMETRY"
    READ_SCREEN_STREAM = "READ_SCREEN_STREAM"


# Default scopes assigned to a newly paired companion device
DEFAULT_COMPANION_SCOPES: Set[PhonePermissionScope] = {
    PhonePermissionScope.READ_STATUS,
    PhonePermissionScope.READ_DEVICE_INFO,
    PhonePermissionScope.SEND_COMMAND,
    PhonePermissionScope.VOICE_COMMAND,
    PhonePermissionScope.EMERGENCY_STOP,
    PhonePermissionScope.READ_TELEMETRY,
}

# Action to required scope mapping
ACTION_SCOPE_MAP: Dict[str, PhonePermissionScope] = {
    "status.read": PhonePermissionScope.READ_STATUS,
    "device.info": PhonePermissionScope.READ_DEVICE_INFO,
    "command.send": PhonePermissionScope.SEND_COMMAND,
    "voice.command": PhonePermissionScope.VOICE_COMMAND,
    "action.approved_execute": PhonePermissionScope.APPROVED_COMPUTER_ACTION,
    "emergency.stop": PhonePermissionScope.EMERGENCY_STOP,
    "emergency.status": PhonePermissionScope.READ_STATUS,
    "telemetry.read": PhonePermissionScope.READ_TELEMETRY,
    "telemetry.subscribe": PhonePermissionScope.READ_TELEMETRY,
    "stream.start": PhonePermissionScope.READ_SCREEN_STREAM,
    "stream.stop": PhonePermissionScope.READ_SCREEN_STREAM,
    "stream.pause": PhonePermissionScope.READ_SCREEN_STREAM,
    "stream.resume": PhonePermissionScope.READ_SCREEN_STREAM,
    "stream.frame": PhonePermissionScope.READ_SCREEN_STREAM,
    "stream.status": PhonePermissionScope.READ_TELEMETRY,
}

# Strictly prohibited actions / command patterns
PROHIBITED_ACTIONS: Set[str] = {
    "shell",
    "powershell",
    "cmd",
    "cmd.exe",
    "exec",
    "eval",
    "system",
    "system.exec",
    "adb_shell",
    "adb.exec",
    "terminal",
    "bash",
    "sh",
    "filesystem_read",
    "filesystem_write",
    "filesystem_delete",
    "disk.format",
    "reg.edit",
    # Step 10 Phase 2: Prohibited remote computer control in observation layer
    "remote.click",
    "remote.type",
    "remote.mouse_move",
    "remote.key_press",
    "remote.input",
    "mouse_click",
    "key_event",
    "desktop_control",
}

# Substring patterns prohibited within command texts or payloads
PROHIBITED_COMMAND_PATTERNS = [
    re.compile(r"\b(rmdir\s+/s|format\s+[a-z]:|del\s+/[fqs])\b", re.IGNORECASE),
    re.compile(r"\b(powershell\.exe|cmd\.exe|bash\.exe|wscript\.exe|cscript\.exe)\b", re.IGNORECASE),
    re.compile(r"\b(adb\s+shell|adb\s+push|adb\s+pull|adb\s+root)\b", re.IGNORECASE),
]


@dataclass
class AuthorizationResult:
    allowed: bool
    decision_code: str
    reason: str
    required_scope: Optional[str] = None


def authorize_action(
    action: str,
    granted_scopes: Set[PhonePermissionScope],
    is_emergency_active: bool = False,
    command_text: Optional[str] = None,
) -> AuthorizationResult:
    """
    Deterministically authorize a requested phone action against granted scopes,
    emergency stop state, and prohibited action boundaries.
    """
    action_clean = (action or "").strip().lower()

    # 1. Prohibited action check
    if action_clean in PROHIBITED_ACTIONS:
        return AuthorizationResult(
            allowed=False,
            decision_code="PROHIBITED_ACTION",
            reason=f"Action '{action}' is strictly prohibited by security policy.",
        )

    # 2. Emergency stop freeze
    if is_emergency_active:
        # Only emergency status and emergency stop itself are allowed during emergency freeze
        if action_clean not in ("emergency.status", "emergency.stop"):
            return AuthorizationResult(
                allowed=False,
                decision_code="EMERGENCY_STOP_ACTIVE",
                reason="System is in EMERGENCY_STOP state. All phone commands frozen.",
            )

    # 3. Known action check
    required_scope = ACTION_SCOPE_MAP.get(action_clean)
    if not required_scope:
        return AuthorizationResult(
            allowed=False,
            decision_code="UNKNOWN_ACTION",
            reason=f"Action '{action}' is unknown and cannot be authorized.",
        )

    # 4. Scope authorization
    if required_scope not in granted_scopes:
        return AuthorizationResult(
            allowed=False,
            decision_code="SCOPE_DENIED",
            reason=f"Action '{action}' requires scope '{required_scope.value}' which is not granted.",
            required_scope=required_scope.value,
        )

    # 5. Content inspection if command text is provided
    if command_text:
        for pattern in PROHIBITED_COMMAND_PATTERNS:
            if pattern.search(command_text):
                return AuthorizationResult(
                    allowed=False,
                    decision_code="PROHIBITED_PAYLOAD",
                    reason="Command payload contains prohibited shell or administrative patterns.",
                )

    return AuthorizationResult(
        allowed=True,
        decision_code="AUTHORIZED",
        reason="Action successfully authorized.",
        required_scope=required_scope.value,
    )


class ModelIsolationGate:
    """
    Ensures that Large Language Models (LLMs) cannot execute actions, shell commands,
    network calls, or ADB instructions directly. Models are strictly advisory.
    """

    @classmethod
    def is_model_authorized(cls, action_or_resource: str) -> Tuple[bool, str]:
        """
        Check whether an LLM model is authorized to directly access or execute
        an action or resource. Models are strictly advisory and cannot directly
        capture screens, start/stop streams, execute shell, or access sockets/queues.
        """
        clean = (action_or_resource or "").strip().lower()
        if not clean:
            return False, "EMPTY_ACTION_OR_RESOURCE"

        # Check prohibited action list
        if clean in PROHIBITED_ACTIONS:
            return False, f"Prohibited action: '{clean}' cannot be executed directly by model."

        # Model is blocked from direct screen capture, stream, socket, queue, shell, adb
        blocked_keywords = (
            "capture", "stream", "socket", "queue", "shell", "powershell",
            "cmd", "adb", "pixel", "network", "bind", "listen"
        )
        for kw in blocked_keywords:
            if kw in clean:
                return False, f"Prohibited action: model cannot access '{kw}' directly."

        return True, "AUTHORIZED_ADVISORY_ONLY"

    @staticmethod
    def sanitize_model_proposal(proposal: Dict[str, any]) -> Tuple[bool, str, Dict[str, any]]:
        """
        Validates an LLM-proposed action before it can enter any execution queue.
        Rejects proposals that attempt raw execution or socket access.
        """
        if not isinstance(proposal, dict):
            return False, "INVALID_PROPOSAL_TYPE", {}

        action = str(proposal.get("action", "")).strip().lower()
        if not action:
            return False, "MISSING_ACTION", {}

        if action in PROHIBITED_ACTIONS:
            return False, "PROHIBITED_ACTION_PROPOSED", {}

        # LLMs cannot propose direct socket binds or raw filesystem access
        if "socket" in action or "network" in action or "bind" in action or "listen" in action:
            return False, "PROHIBITED_NETWORK_PROPOSAL", {}

        if "powershell" in action or "shell" in action or "cmd" in action:
            return False, "PROHIBITED_SHELL_PROPOSAL", {}

        # Strip any unapproved keys injected by model
        safe_proposal = {
            "action": action,
            "params": proposal.get("params", {}),
            "advisory_notes": str(proposal.get("notes", "")),
        }
        return True, "PROPOSAL_ACCEPTED_FOR_EVALUATION", safe_proposal
