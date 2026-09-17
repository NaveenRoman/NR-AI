"""
NR-AI Scoped Remote Actions & Computer Control Safety Schema.
Step 10 Phase 4 — Scoped Remote Actions & Computer Control Safety.

Defines strict structured schemas, allowlisted computer control actions,
risk classifications, validation rules, and result contracts for remote execution.
"""

from dataclasses import asdict, dataclass, field
from enum import Enum
import json
import logging
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from app.remote.config import (
    MAX_PAYLOAD_BYTES,
    MAX_REMOTE_ACTION_PAYLOAD_BYTES,
    MAX_REMOTE_ACTION_TIMEOUT_SECONDS,
    NONCE_TIMESTAMP_TOLERANCE_SECONDS,
    REMOTE_ACTION_MALFORMED,
    REMOTE_ACTION_NOT_ALLOWED,
    REMOTE_ACTION_EXPIRED,
)
from app.remote.permissions import PhonePermissionScope

logger = logging.getLogger("NRAI.RemoteActions")


class RemoteActionRiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class RemoteActionType(str, Enum):
    OPEN_APP = "computer.open_app"
    LIST_WINDOWS = "computer.list_windows"
    FOCUS_WINDOW = "computer.focus_window"
    INSPECT_SCREEN = "computer.inspect_screen"
    FIND_TEXT = "computer.find_text"
    CLICK_TARGET = "computer.click_target"
    DOUBLE_CLICK = "computer.double_click"
    TYPE_TEXT = "computer.type_text"
    PRESS_KEY = "computer.press_key"
    HOTKEY = "computer.hotkey"
    SCROLL = "computer.scroll"
    VERIFY = "computer.verify"
    RUN_WORKFLOW = "computer.run_workflow"


@dataclass
class RemoteActionDefinition:
    """Formal definition and constraints for an allowlisted remote computer action."""
    action_type: RemoteActionType
    description: str
    required_params: List[str]
    allowed_params: Set[str]
    default_risk: RemoteActionRiskLevel
    requires_confirmation: bool
    timeout_seconds: float
    audit_classification: str
    required_scope: PhonePermissionScope = PhonePermissionScope.APPROVED_COMPUTER_ACTION


# Explicit allowlist of approved remote computer actions.
# Actions outside this registry CANNOT be executed remotely.
REMOTE_ACTION_ALLOWLIST: Dict[str, RemoteActionDefinition] = {
    RemoteActionType.OPEN_APP.value: RemoteActionDefinition(
        action_type=RemoteActionType.OPEN_APP,
        description="Launch an authorized application on the PC desktop.",
        required_params=["app_name"],
        allowed_params={"app_name", "args"},
        default_risk=RemoteActionRiskLevel.LOW,
        requires_confirmation=False,
        timeout_seconds=15.0,
        audit_classification="APP_LAUNCH",
    ),
    RemoteActionType.LIST_WINDOWS.value: RemoteActionDefinition(
        action_type=RemoteActionType.LIST_WINDOWS,
        description="Enumerate visible top-level windows on the interactive PC desktop.",
        required_params=[],
        allowed_params={"include_minimized"},
        default_risk=RemoteActionRiskLevel.LOW,
        requires_confirmation=False,
        timeout_seconds=5.0,
        audit_classification="WINDOW_ENUMERATION",
    ),
    RemoteActionType.FOCUS_WINDOW.value: RemoteActionDefinition(
        action_type=RemoteActionType.FOCUS_WINDOW,
        description="Bring a target window to foreground focus.",
        required_params=["target"],
        allowed_params={"target"},
        default_risk=RemoteActionRiskLevel.LOW,
        requires_confirmation=False,
        timeout_seconds=5.0,
        audit_classification="WINDOW_FOCUS",
    ),
    RemoteActionType.INSPECT_SCREEN.value: RemoteActionDefinition(
        action_type=RemoteActionType.INSPECT_SCREEN,
        description="Read visible screen/window text content via local OCR.",
        required_params=[],
        allowed_params={"target_window"},
        default_risk=RemoteActionRiskLevel.LOW,
        requires_confirmation=False,
        timeout_seconds=10.0,
        audit_classification="SCREEN_INSPECT",
    ),
    RemoteActionType.FIND_TEXT.value: RemoteActionDefinition(
        action_type=RemoteActionType.FIND_TEXT,
        description="Locate target text and return screen coordinates.",
        required_params=["query"],
        allowed_params={"query", "target_window"},
        default_risk=RemoteActionRiskLevel.LOW,
        requires_confirmation=False,
        timeout_seconds=10.0,
        audit_classification="TEXT_SEARCH",
    ),
    RemoteActionType.CLICK_TARGET.value: RemoteActionDefinition(
        action_type=RemoteActionType.CLICK_TARGET,
        description="Safely click a visual target or verified screen location.",
        required_params=[],
        allowed_params={"target_name", "coordinates", "target_window", "verify_expected"},
        default_risk=RemoteActionRiskLevel.LOW,
        requires_confirmation=False,
        timeout_seconds=10.0,
        audit_classification="MOUSE_CLICK",
    ),
    RemoteActionType.DOUBLE_CLICK.value: RemoteActionDefinition(
        action_type=RemoteActionType.DOUBLE_CLICK,
        description="Safely double-click a visual target or verified location.",
        required_params=[],
        allowed_params={"target_name", "coordinates", "target_window", "verify_expected"},
        default_risk=RemoteActionRiskLevel.LOW,
        requires_confirmation=False,
        timeout_seconds=10.0,
        audit_classification="MOUSE_DOUBLE_CLICK",
    ),
    RemoteActionType.TYPE_TEXT.value: RemoteActionDefinition(
        action_type=RemoteActionType.TYPE_TEXT,
        description="Type filtered text into the focused window.",
        required_params=["text"],
        allowed_params={"text", "interval", "is_sensitive", "verify_expected"},
        default_risk=RemoteActionRiskLevel.LOW,
        requires_confirmation=False,
        timeout_seconds=15.0,
        audit_classification="KEYBOARD_INPUT",
    ),
    RemoteActionType.PRESS_KEY.value: RemoteActionDefinition(
        action_type=RemoteActionType.PRESS_KEY,
        description="Press an approved keyboard key from the safety whitelist.",
        required_params=["key"],
        allowed_params={"key", "verify_expected"},
        default_risk=RemoteActionRiskLevel.LOW,
        requires_confirmation=False,
        timeout_seconds=5.0,
        audit_classification="KEY_PRESS",
    ),
    RemoteActionType.HOTKEY.value: RemoteActionDefinition(
        action_type=RemoteActionType.HOTKEY,
        description="Execute an approved key combination.",
        required_params=["keys"],
        allowed_params={"keys", "verify_expected"},
        default_risk=RemoteActionRiskLevel.LOW,
        requires_confirmation=False,
        timeout_seconds=5.0,
        audit_classification="HOTKEY",
    ),
    RemoteActionType.SCROLL.value: RemoteActionDefinition(
        action_type=RemoteActionType.SCROLL,
        description="Scroll active window content up or down safely.",
        required_params=["direction"],
        allowed_params={"direction", "clicks"},
        default_risk=RemoteActionRiskLevel.LOW,
        requires_confirmation=False,
        timeout_seconds=5.0,
        audit_classification="SCROLL",
    ),
    RemoteActionType.VERIFY.value: RemoteActionDefinition(
        action_type=RemoteActionType.VERIFY,
        description="Verify expected text or UI state is visible on screen.",
        required_params=["expected_text"],
        allowed_params={"expected_text", "wait_time"},
        default_risk=RemoteActionRiskLevel.LOW,
        requires_confirmation=False,
        timeout_seconds=10.0,
        audit_classification="STATE_VERIFY",
    ),
    RemoteActionType.RUN_WORKFLOW.value: RemoteActionDefinition(
        action_type=RemoteActionType.RUN_WORKFLOW,
        description="Execute an autonomous, bounded multi-step computer workflow.",
        required_params=["goal"],
        allowed_params={"goal", "max_steps"},
        default_risk=RemoteActionRiskLevel.MEDIUM,
        requires_confirmation=False,
        timeout_seconds=30.0,
        audit_classification="AUTONOMOUS_WORKFLOW",
    ),
}

# Mapping of convenient shorthand names to full RemoteActionType strings
ACTION_ALIASES: Dict[str, str] = {
    "open_app": RemoteActionType.OPEN_APP.value,
    "list_windows": RemoteActionType.LIST_WINDOWS.value,
    "focus_window": RemoteActionType.FOCUS_WINDOW.value,
    "inspect_screen": RemoteActionType.INSPECT_SCREEN.value,
    "find_text": RemoteActionType.FIND_TEXT.value,
    "click_target": RemoteActionType.CLICK_TARGET.value,
    "double_click": RemoteActionType.DOUBLE_CLICK.value,
    "type_text": RemoteActionType.TYPE_TEXT.value,
    "press_key": RemoteActionType.PRESS_KEY.value,
    "hotkey": RemoteActionType.HOTKEY.value,
    "scroll": RemoteActionType.SCROLL.value,
    "verify": RemoteActionType.VERIFY.value,
    "run_workflow": RemoteActionType.RUN_WORKFLOW.value,
    "workflow": RemoteActionType.RUN_WORKFLOW.value,
}


@dataclass
class RemoteActionRequest:
    """Strict structured request for remote computer action execution."""
    action_id: str
    session_id: str
    device_id: str
    action_type: str
    target: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    expected_state: Optional[Any] = None
    request_timestamp: float = field(default_factory=time.time)
    request_nonce: str = ""
    confirmation_token: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_id": self.action_id,
            "session_id": self.session_id,
            "device_id": self.device_id,
            "action_type": self.action_type,
            "target": self.target,
            "parameters": self.parameters,
            "expected_state": self.expected_state,
            "request_timestamp": self.request_timestamp,
            "request_nonce": self.request_nonce,
            "confirmation_token": self.confirmation_token,
        }

    def to_safe_dict(self) -> Dict[str, Any]:
        """Returns request representation with secret/sensitive values redacted."""
        safe_params = {}
        for k, v in self.parameters.items():
            k_low = str(k).lower()
            if any(s in k_low for s in ("pass", "secret", "token", "cred", "auth", "key")):
                safe_params[k] = "[REDACTED]"
            elif k == "text" and (self.parameters.get("is_sensitive") or any(s in str(v).lower() for s in ("pass", "secret", "token", "key"))):
                safe_params[k] = "[REDACTED]"
            else:
                safe_params[k] = v

        d = self.to_dict()
        d["parameters"] = safe_params
        if self.confirmation_token:
            d["confirmation_token"] = "[PRESENT]"
        return d


@dataclass
class RemoteActionResult:
    """Standardized structured result returned for every remote action request."""
    action_id: str
    session_id: str
    device_id: str
    action_type: str
    status: str  # SUCCESS, AWAITING_CONFIRMATION, DENIED, FAILED, STOPPED, CANCELLED
    success: bool
    verified: bool = False
    message: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    risk_level: str = RemoteActionRiskLevel.LOW.value
    requires_confirmation: bool = False
    confirmation_token: Optional[str] = None
    duration_s: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_id": self.action_id,
            "session_id": self.session_id,
            "device_id": self.device_id,
            "action_type": self.action_type,
            "status": self.status,
            "success": self.success,
            "verified": self.verified,
            "message": self.message,
            "data": self.data,
            "error": self.error,
            "risk_level": self.risk_level,
            "requires_confirmation": self.requires_confirmation,
            "confirmation_token": self.confirmation_token,
            "duration_s": round(self.duration_s, 3),
            "timestamp": self.timestamp,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


# Approved schema top-level fields
ALLOWED_REQUEST_FIELDS: Set[str] = {
    "action_id",
    "session_id",
    "device_id",
    "action_type",
    "action",  # alias for action_type
    "target",
    "parameters",
    "params",  # alias for parameters
    "expected_state",
    "request_timestamp",
    "timestamp",  # alias
    "request_nonce",
    "nonce",  # alias
    "confirmation_token",
    "token",  # alias
}


def normalize_action_type(raw_type: str) -> str:
    """Resolves action alias or returns normalized full action type."""
    cleaned = (raw_type or "").strip().lower()
    if cleaned in ACTION_ALIASES:
        return ACTION_ALIASES[cleaned]
    return cleaned


def validate_remote_action_request(
    data: Dict[str, Any],
    max_payload_bytes: int = MAX_REMOTE_ACTION_PAYLOAD_BYTES,
    tolerance_seconds: float = NONCE_TIMESTAMP_TOLERANCE_SECONDS,
    current_time: Optional[float] = None,
) -> Tuple[bool, Optional[str], Optional[RemoteActionRequest]]:
    """
    Deterministically validates an incoming remote computer action request against schema,
    payload size boundaries, timestamp drift, and parameter constraints.
    Returns (valid, error_code, validated_request_object).
    """
    if not isinstance(data, dict):
        return False, REMOTE_ACTION_MALFORMED, None

    # 1. Payload size check
    try:
        payload_size = len(json.dumps(data).encode("utf-8"))
        if payload_size > max_payload_bytes:
            return False, REMOTE_ACTION_MALFORMED, None
    except Exception:
        return False, REMOTE_ACTION_MALFORMED, None

    # 2. Strict field verification (reject unknown top-level keys)
    for k in data.keys():
        if k not in ALLOWED_REQUEST_FIELDS:
            return False, REMOTE_ACTION_MALFORMED, None

    # 3. Required top-level fields
    action_id = str(data.get("action_id", "")).strip()
    session_id = str(data.get("session_id", "")).strip()
    device_id = str(data.get("device_id", "")).strip()
    raw_action = str(data.get("action_type") or data.get("action", "")).strip()
    nonce = str(data.get("request_nonce") or data.get("nonce", "")).strip()

    if not action_id or not session_id or not device_id or not raw_action:
        return False, REMOTE_ACTION_MALFORMED, None

    # 4. Action allowlist check
    action_type = normalize_action_type(raw_action)
    action_def = REMOTE_ACTION_ALLOWLIST.get(action_type)
    if not action_def:
        return False, REMOTE_ACTION_NOT_ALLOWED, None

    # 5. Timestamp drift / expiry check
    now = current_time if current_time is not None else time.time()
    raw_ts = data.get("request_timestamp") or data.get("timestamp")
    try:
        ts = float(raw_ts) if raw_ts is not None else now
    except (ValueError, TypeError):
        return False, REMOTE_ACTION_MALFORMED, None

    if abs(now - ts) > tolerance_seconds:
        return False, REMOTE_ACTION_EXPIRED, None

    # 6. Parameters structure and parameter allowlist check
    raw_params = data.get("parameters") or data.get("params") or {}
    if not isinstance(raw_params, dict):
        return False, REMOTE_ACTION_MALFORMED, None

    params = dict(raw_params)

    # Propagate top-level target into parameters if applicable
    target = data.get("target")
    if target is not None and "target" not in params and "app_name" not in params and "target_name" not in params:
        if action_type == RemoteActionType.OPEN_APP.value:
            params["app_name"] = str(target)
        elif action_type == RemoteActionType.FOCUS_WINDOW.value:
            params["target"] = str(target)
        elif action_type in (RemoteActionType.CLICK_TARGET.value, RemoteActionType.DOUBLE_CLICK.value):
            params["target_name"] = str(target)
        elif action_type == RemoteActionType.FIND_TEXT.value:
            params["query"] = str(target)

    # Check required parameters for action
    for req in action_def.required_params:
        if req not in params or params[req] is None or (isinstance(params[req], str) and not params[req].strip()):
            return False, REMOTE_ACTION_MALFORMED, None

    # Reject parameters not in allowed_params
    for p_key in params.keys():
        if p_key not in action_def.allowed_params:
            return False, REMOTE_ACTION_MALFORMED, None

    # Special check for click_target / double_click: must have at least target_name or coordinates
    if action_type in (RemoteActionType.CLICK_TARGET.value, RemoteActionType.DOUBLE_CLICK.value):
        if not params.get("target_name") and not params.get("coordinates"):
            return False, REMOTE_ACTION_MALFORMED, None

    req_obj = RemoteActionRequest(
        action_id=action_id,
        session_id=session_id,
        device_id=device_id,
        action_type=action_type,
        target=str(target) if target is not None else None,
        parameters=params,
        expected_state=data.get("expected_state"),
        request_timestamp=ts,
        request_nonce=nonce or action_id,
        confirmation_token=data.get("confirmation_token") or data.get("token"),
    )

    return True, None, req_obj
