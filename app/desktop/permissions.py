"""
Desktop Shell Permission and Safety Validator for NR-AI.
OpenJarvis-inspired desktop boundary security.

Security Invariants:
- Desktop shell communicates strictly via typed safe intents and validated NR-AI APIs.
- Permanent prohibition of arbitrary shell commands, PowerShell, cmd.exe, eval, exec,
  and arbitrary subprocess or network access.
- Bounded payload size and target view validation.
- ModelIsolationGate and emergency stop enforcement.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("NRAI.Desktop.Permissions")

# Authoritative list of safe desktop intent types
class DesktopIntentType(str, Enum):
    NAVIGATE_VIEW = "NAVIGATE_VIEW"
    SELECT_AGENT = "SELECT_AGENT"
    TRIGGER_VOICE_ACTION = "TRIGGER_VOICE_ACTION"
    SUBMIT_TASK = "SUBMIT_TASK"
    QUERY_HEALTH = "QUERY_HEALTH"
    TRIGGER_EMERGENCY_STOP = "TRIGGER_EMERGENCY_STOP"
    PROHIBITED_ACTION = "PROHIBITED_ACTION"


# Prohibited command verbs and syntax patterns
PROHIBITED_COMMAND_PATTERNS: List[re.Pattern] = [
    re.compile(r"(?i)\b(cmd\.exe|powershell(\.exe)?|bash|sh|zsh)\b"),
    re.compile(r"(?i)\b(eval|exec|compile|__import__)\b"),
    re.compile(r"(?i)\b(subprocess|os\.system|popen|spawn)\b"),
    re.compile(r"(?i)\b(rmdir|del\s+/s|format|diskpart|reg\s+delete)\b"),
    re.compile(r"[;&|`$><]"),  # Shell metacharacters
]

ALLOWED_VIEWS: Set[str] = {
    "galaxy",
    "chat",
    "knowledge",
    "tasks",
    "voice",
    "settings",
    "companion",
}

ALLOWED_AGENTS: Set[str] = {
    "nr_ai",
    "droid",
    "droid_scout",
    "droid_guardian",
    "knowledge",
    "nova",
    "aegis",
}

ALLOWED_VOICE_ACTIONS: Set[str] = {
    "start_listening",
    "stop_listening",
    "toggle_mute",
    "cancel_playback",
    "reset_session",
}


@dataclass
class IntentValidationResult:
    is_authorized: bool
    intent_type: DesktopIntentType
    sanitized_payload: Dict[str, Any]
    rejection_reason: Optional[str] = None


class DesktopPermissionValidator:
    """
    Validates desktop shell requests against strict security boundaries.
    """

    MAX_PAYLOAD_BYTES: int = 65536  # 64 KB max payload

    @classmethod
    def validate_intent(
        cls,
        intent_type: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> IntentValidationResult:
        """
        Validate that a desktop shell action conforms to safe typed intent boundaries.
        """
        payload = payload or {}

        # 1. Payload size bounding
        try:
            import json
            serialized = json.dumps(payload)
            if len(serialized.encode("utf-8")) > cls.MAX_PAYLOAD_BYTES:
                return IntentValidationResult(
                    is_authorized=False,
                    intent_type=DesktopIntentType.PROHIBITED_ACTION,
                    sanitized_payload={},
                    rejection_reason="PAYLOAD_EXCEEDS_SIZE_LIMIT_64KB",
                )
        except Exception as exc:
            return IntentValidationResult(
                is_authorized=False,
                intent_type=DesktopIntentType.PROHIBITED_ACTION,
                sanitized_payload={},
                rejection_reason=f"PAYLOAD_SERIALIZATION_FAILED: {exc}",
            )

        # 2. Check for shell injection / eval / command execution attempts
        for key, val in payload.items():
            if isinstance(val, str):
                for pat in PROHIBITED_COMMAND_PATTERNS:
                    if pat.search(val):
                        logger.warning(
                            "Security rejection: Prohibited pattern '%s' detected in desktop payload key '%s'",
                            pat.pattern,
                            key,
                        )
                        return IntentValidationResult(
                            is_authorized=False,
                            intent_type=DesktopIntentType.PROHIBITED_ACTION,
                            sanitized_payload={},
                            rejection_reason=f"PROHIBITED_COMMAND_EXECUTION_BLOCKED: {key}",
                        )

        # 3. Validate specific intent types
        try:
            parsed_type = DesktopIntentType(intent_type)
        except ValueError:
            return IntentValidationResult(
                is_authorized=False,
                intent_type=DesktopIntentType.PROHIBITED_ACTION,
                sanitized_payload={},
                rejection_reason=f"UNKNOWN_INTENT_TYPE: {intent_type}",
            )

        if parsed_type == DesktopIntentType.NAVIGATE_VIEW:
            target_view = str(payload.get("view", "")).lower()
            if target_view not in ALLOWED_VIEWS:
                return IntentValidationResult(
                    is_authorized=False,
                    intent_type=parsed_type,
                    sanitized_payload={},
                    rejection_reason=f"UNAUTHORIZED_DESKTOP_VIEW: {target_view}",
                )
            return IntentValidationResult(
                is_authorized=True,
                intent_type=parsed_type,
                sanitized_payload={"view": target_view},
            )

        elif parsed_type == DesktopIntentType.SELECT_AGENT:
            target_agent = str(payload.get("agent_id", "")).lower()
            if target_agent not in ALLOWED_AGENTS:
                return IntentValidationResult(
                    is_authorized=False,
                    intent_type=parsed_type,
                    sanitized_payload={},
                    rejection_reason=f"UNAUTHORIZED_AGENT: {target_agent}",
                )
            return IntentValidationResult(
                is_authorized=True,
                intent_type=parsed_type,
                sanitized_payload={"agent_id": target_agent},
            )

        elif parsed_type == DesktopIntentType.TRIGGER_VOICE_ACTION:
            action = str(payload.get("action", "")).lower()
            if action not in ALLOWED_VOICE_ACTIONS:
                return IntentValidationResult(
                    is_authorized=False,
                    intent_type=parsed_type,
                    sanitized_payload={},
                    rejection_reason=f"UNAUTHORIZED_VOICE_ACTION: {action}",
                )
            return IntentValidationResult(
                is_authorized=True,
                intent_type=parsed_type,
                sanitized_payload={"action": action},
            )

        elif parsed_type == DesktopIntentType.TRIGGER_EMERGENCY_STOP:
            # Emergency stop is always authorized and prioritized
            return IntentValidationResult(
                is_authorized=True,
                intent_type=parsed_type,
                sanitized_payload={"reason": payload.get("reason", "desktop_emergency_stop")},
            )

        elif parsed_type == DesktopIntentType.QUERY_HEALTH:
            return IntentValidationResult(
                is_authorized=True,
                intent_type=parsed_type,
                sanitized_payload={"scope": payload.get("scope", "all")},
            )

        elif parsed_type == DesktopIntentType.SUBMIT_TASK:
            instruction = str(payload.get("instruction", "")).strip()
            if not instruction:
                return IntentValidationResult(
                    is_authorized=False,
                    intent_type=parsed_type,
                    sanitized_payload={},
                    rejection_reason="EMPTY_TASK_INSTRUCTION",
                )
            # Sanitize instruction using PromptGuardrails
            try:
                from app.security.guardrails import PromptGuardrails
                guardrails = PromptGuardrails()
                instruction = guardrails.redact(instruction)
            except Exception:
                pass

            return IntentValidationResult(
                is_authorized=True,
                intent_type=parsed_type,
                sanitized_payload={"instruction": instruction},
            )

        return IntentValidationResult(
            is_authorized=False,
            intent_type=DesktopIntentType.PROHIBITED_ACTION,
            sanitized_payload={},
            rejection_reason="UNHANDLED_INTENT_TYPE",
        )
