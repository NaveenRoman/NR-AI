"""
NR-AI Remote Action Deterministic Safety Gate.
Step 10 Phase 4 — Scoped Remote Actions & Computer Control Safety.

Provides multi-layer deterministic safety validation for remote computer actions:
- Prohibited command scanning (shell, PowerShell, CMD, exec, eval, ADB, filesystem, registry)
- Target freshness and coordinate validation (15-second TTL)
- Model isolation enforcement
- Deterministic risk assessment and two-step confirmation gating
- Emergency stop integration
- Secret and credential redaction
"""

from dataclasses import dataclass
from enum import Enum
import logging
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from app.remote.config import (
    MAX_FRAME_HEIGHT,
    MAX_FRAME_WIDTH,
    REMOTE_CONFIRMATION_REQUIRED,
    REMOTE_SAFETY_REJECTED,
    REMOTE_STOPPED,
    REMOTE_TARGET_INVALID,
    REMOTE_TARGET_STALE,
    REMOTE_TARGET_TTL_SECONDS,
)
from app.remote.emergency import EmergencyStopController
from app.remote.permissions import (
    PROHIBITED_ACTIONS,
    PROHIBITED_COMMAND_PATTERNS,
)
from app.remote.remote_actions import (
    REMOTE_ACTION_ALLOWLIST,
    RemoteActionRequest,
    RemoteActionRiskLevel,
    RemoteActionType,
)

logger = logging.getLogger("NRAI.RemoteActionSafety")

# Enhanced prohibited command patterns for remote execution
REMOTE_PROHIBITED_PATTERNS = [
    # Shells and script interpreters
    re.compile(r"\b(?:powershell(?:\.exe)?|cmd(?:\.exe)?|bash(?:\.exe)?|sh(?:\.exe)?|wscript(?:\.exe)?|cscript(?:\.exe)?)\b", re.IGNORECASE),
    re.compile(r"\b(?:adb(?:\.exe)?\s+shell|adb(?:\.exe)?\s+root|adb(?:\.exe)?\s+push|adb(?:\.exe)?\s+pull)\b", re.IGNORECASE),
    # Code execution primitives
    re.compile(r"\b(?:exec|eval|os\.system)\s*\(", re.IGNORECASE),
    re.compile(r"\bsubprocess\.(?:Popen|run|call|check_output)\b", re.IGNORECASE),
    # Destructive filesystem commands
    re.compile(r"\b(?:rmdir\s+/[sq]|del\s+/[fqs]|erase\s+/[fqs])\b", re.IGNORECASE),
    re.compile(r"\bformat\s+[a-z]:", re.IGNORECASE),
    re.compile(r"\brm\s+-rf\b", re.IGNORECASE),
    re.compile(r"\b(?:dd\s+if=|mkfs)\b", re.IGNORECASE),
    # Registry and system modification
    re.compile(r"\b(?:reg(?:\.exe)?\s+(?:add|delete|copy|save|restore)|regedit(?:\.exe)?)\b", re.IGNORECASE),
    re.compile(r"\b(?:vssadmin(?:\.exe)?|bcdedit(?:\.exe)?|diskpart(?:\.exe)?)\b", re.IGNORECASE),
    # Database destruction
    re.compile(r"\b(?:drop\s+database|drop\s+table|truncate\s+table|delete\s+from)\b", re.IGNORECASE),
    # Dangerous system shutdown/reboot
    re.compile(r"\b(?:shutdown\s+/[srft]|reboot|init\s+0|halt)\b", re.IGNORECASE),
]

# Sensitive credentials and high-risk keywords
HIGH_RISK_KEYWORDS = [
    "delete all", "wipe disk", "factory reset", "format drive",
    "kill process tree", "uninstall system", "buy now", "purchase",
    "checkout", "pay now", "transfer money", "submit payment",
    "credit card", "bank account", "wire transfer",
    "password", "secret_key", "private_key", "token", "api_key",
    "passwd", "credentials",
]

SENSITIVE_KEY_NAMES = (
    "password", "secret", "token", "key", "credential", "auth", "api_key"
)


@dataclass
class SafetyEvaluationResult:
    """Result of deterministic safety evaluation for a proposed remote action."""
    passed: bool
    risk_level: RemoteActionRiskLevel
    requires_confirmation: bool
    decision_code: str
    reason: str
    redacted_parameters: Dict[str, Any]


class RemoteActionSafetyGate:
    """
    Deterministic safety gate enforcing multi-layer authorization and safety boundaries
    for all remote computer actions before execution.
    """

    def __init__(self, emergency_controller: Optional[EmergencyStopController] = None):
        self.emergency_controller = emergency_controller or EmergencyStopController()

    def evaluate_request(
        self,
        request: RemoteActionRequest,
        cached_target: Optional[Dict[str, Any]] = None,
        current_time: Optional[float] = None,
    ) -> SafetyEvaluationResult:
        """
        Deterministically evaluates a remote action request.
        Checks emergency stop, prohibited actions/commands, target freshness, text safety,
        and high-risk confirmation requirements.
        """
        now = current_time if current_time is not None else time.time()

        # 1. Emergency Stop Check
        if self.emergency_controller.is_active():
            return SafetyEvaluationResult(
                passed=False,
                risk_level=RemoteActionRiskLevel.HIGH,
                requires_confirmation=False,
                decision_code=REMOTE_STOPPED,
                reason="Computer control rejected: Emergency stop is active.",
                redacted_parameters=self.redact_parameters(request.parameters),
            )

        # 2. Prohibited Action Check
        action_clean = request.action_type.strip().lower()
        if action_clean in PROHIBITED_ACTIONS:
            return SafetyEvaluationResult(
                passed=False,
                risk_level=RemoteActionRiskLevel.HIGH,
                requires_confirmation=False,
                decision_code=REMOTE_SAFETY_REJECTED,
                reason=f"Action '{request.action_type}' is strictly prohibited by security policy.",
                redacted_parameters=self.redact_parameters(request.parameters),
            )

        # 3. Parameter and Content Prohibited Pattern Scan
        content_to_scan = self._extract_text_content(request)
        for pat in REMOTE_PROHIBITED_PATTERNS:
            if pat.search(content_to_scan):
                matched = pat.pattern
                return SafetyEvaluationResult(
                    passed=False,
                    risk_level=RemoteActionRiskLevel.HIGH,
                    requires_confirmation=False,
                    decision_code=REMOTE_SAFETY_REJECTED,
                    reason=f"Prohibited command or script pattern detected: '{matched}'.",
                    redacted_parameters=self.redact_parameters(request.parameters),
                )

        # 4. Target Freshness and Validity for UI Actions
        if request.action_type in (
            RemoteActionType.CLICK_TARGET.value,
            RemoteActionType.DOUBLE_CLICK.value,
            RemoteActionType.FIND_TEXT.value,
        ):
            target_valid, target_code, target_msg = self._validate_target(request, cached_target, now)
            if not target_valid:
                return SafetyEvaluationResult(
                    passed=False,
                    risk_level=RemoteActionRiskLevel.LOW,
                    requires_confirmation=False,
                    decision_code=target_code,
                    reason=target_msg,
                    redacted_parameters=self.redact_parameters(request.parameters),
                )

        # 5. Coordinate Boundary Validation
        coords = request.parameters.get("coordinates")
        if coords:
            if not self._validate_coordinates(coords):
                return SafetyEvaluationResult(
                    passed=False,
                    risk_level=RemoteActionRiskLevel.LOW,
                    requires_confirmation=False,
                    decision_code=REMOTE_TARGET_INVALID,
                    reason="Target coordinates are invalid or out of screen bounds.",
                    redacted_parameters=self.redact_parameters(request.parameters),
                )

        # 6. Risk Level & Confirmation Determination
        risk_level, requires_conf, conf_reason = self._assess_risk_and_confirmation(request, content_to_scan)

        return SafetyEvaluationResult(
            passed=True,
            risk_level=risk_level,
            requires_confirmation=requires_conf,
            decision_code=REMOTE_CONFIRMATION_REQUIRED if requires_conf else "ALLOWED",
            reason=conf_reason or "Action passed deterministic safety gate.",
            redacted_parameters=self.redact_parameters(request.parameters),
        )

    def _extract_text_content(self, request: RemoteActionRequest) -> str:
        """Collects all text-like fields from request for security pattern scanning."""
        parts = [
            request.action_type,
            request.target or "",
            str(request.parameters.get("text", "")),
            str(request.parameters.get("app_name", "")),
            str(request.parameters.get("target_name", "")),
            str(request.parameters.get("query", "")),
            str(request.parameters.get("goal", "")),
        ]
        # Include keys in hotkeys
        keys = request.parameters.get("keys")
        if isinstance(keys, list):
            parts.extend(str(k) for k in keys)
        elif isinstance(keys, str):
            parts.append(keys)

        return " ".join(parts).lower()

    def _validate_target(
        self,
        request: RemoteActionRequest,
        cached_target: Optional[Dict[str, Any]],
        now: float,
    ) -> Tuple[bool, str, str]:
        """Validates target freshness, TTL, and parameter structure."""
        target_name = request.parameters.get("target_name") or request.target
        coords = request.parameters.get("coordinates")

        # Must have either target name or coordinates
        if not target_name and not coords:
            return False, REMOTE_TARGET_INVALID, "Action requires either a target_name or validated coordinates."

        # If cached target is provided with timestamp, enforce 15s TTL
        if cached_target:
            ts = cached_target.get("timestamp")
            if ts is not None:
                age = now - float(ts)
                if age > REMOTE_TARGET_TTL_SECONDS:
                    return False, REMOTE_TARGET_STALE, f"Visual target '{target_name}' is stale ({age:.1f}s > {REMOTE_TARGET_TTL_SECONDS}s TTL). Fresh perception required."

        # Target name sanity check (prevent injection / control characters)
        if target_name and any(c in target_name for c in ("\x00", "\n", "\r")):
            return False, REMOTE_TARGET_INVALID, "Target name contains illegal control characters."

        return True, "TARGET_VALID", "Target is valid."

    def _validate_coordinates(self, coords: Any) -> bool:
        """Validates coordinate structure and screen bounds."""
        if isinstance(coords, (list, tuple)) and len(coords) == 2:
            try:
                x, y = int(coords[0]), int(coords[1])
                return 0 <= x <= MAX_FRAME_WIDTH and 0 <= y <= MAX_FRAME_HEIGHT
            except (ValueError, TypeError):
                return False
        return False

    def _assess_risk_and_confirmation(
        self,
        request: RemoteActionRequest,
        content_to_scan: str,
    ) -> Tuple[RemoteActionRiskLevel, bool, Optional[str]]:
        """
        Determines the risk classification and whether two-step confirmation is required.
        Destructive keywords or system chords deterministically trigger confirmation.
        """
        # 1. Action definition default risk
        action_def = REMOTE_ACTION_ALLOWLIST.get(request.action_type)
        default_risk = action_def.default_risk if action_def else RemoteActionRiskLevel.LOW
        req_confirmation = action_def.requires_confirmation if action_def else False

        # 2. Check for high-risk keywords in payload
        for kw in HIGH_RISK_KEYWORDS:
            if kw in content_to_scan:
                return (
                    RemoteActionRiskLevel.HIGH,
                    True,
                    f"Action requires confirmation: High-risk keyword '{kw}' detected.",
                )

        # 3. Check for destructive hotkeys (e.g. Alt+F4 closing window)
        if request.action_type == RemoteActionType.HOTKEY.value:
            keys_raw = request.parameters.get("keys", [])
            if isinstance(keys_raw, str):
                keys = [k.lower().strip() for k in keys_raw.split("+")]
            else:
                keys = [str(k).lower().strip() for k in keys_raw]

            if "alt" in keys and "f4" in keys:
                return (
                    RemoteActionRiskLevel.HIGH,
                    True,
                    "Action requires confirmation: Window termination chord (Alt+F4).",
                )

        # 4. Check for workflow goals containing sensitive terms
        if request.action_type == RemoteActionType.RUN_WORKFLOW.value:
            goal = str(request.parameters.get("goal", "")).lower()
            if any(term in goal for term in ("delete", "remove", "close", "restart", "shutdown", "uninstall")):
                return (
                    RemoteActionRiskLevel.HIGH,
                    True,
                    "Workflow requires confirmation: Potentially destructive goal.",
                )

        return default_risk, req_confirmation, None

    def redact_parameters(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Recursively redacts passwords, tokens, API keys, and sensitive text."""
        redacted = {}
        for k, v in params.items():
            k_low = str(k).lower()
            if any(s in k_low for s in SENSITIVE_KEY_NAMES):
                redacted[k] = "[REDACTED]"
            elif k == "text" and (params.get("is_sensitive") or any(s in str(v).lower() for s in SENSITIVE_KEY_NAMES)):
                redacted[k] = "[REDACTED]"
            elif isinstance(v, dict):
                redacted[k] = self.redact_parameters(v)
            else:
                redacted[k] = v
        return redacted

    @classmethod
    def is_model_proposal_safe(cls, proposal: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Validates an AI model proposed computer action.
        Guarantees that raw LLM output cannot directly invoke OS commands, shells, or arbitrary coordinates.
        """
        if not isinstance(proposal, dict):
            return False, "INVALID_PROPOSAL_TYPE", {}

        action = str(proposal.get("action", "")).strip().lower()
        if not action:
            return False, "MISSING_ACTION", {}

        if action in PROHIBITED_ACTIONS:
            return False, f"Prohibited action in model proposal: '{action}'", {}

        # Scan for shell keywords
        for kw in ("shell", "powershell", "cmd", "exec", "eval", "system", "adb"):
            if kw in action:
                return False, f"Prohibited keyword '{kw}' in model proposal", {}

        # Strip unapproved keys injected by model
        safe_proposal = {
            "action": action,
            "params": proposal.get("params", {}),
            "advisory_notes": str(proposal.get("notes", "")),
        }
        return True, "PROPOSAL_ACCEPTED_FOR_EVALUATION", safe_proposal
