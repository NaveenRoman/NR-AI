"""
NR-AI Computer Tool Registry & Deterministic Safety Contracts (Step 4E).

Defines strongly validated, approved tools for computer use:
1. computer.open_app
2. computer.list_windows
3. computer.focus_window
4. computer.inspect_screen
5. computer.find_text
6. computer.click_target
7. computer.double_click
8. computer.type_text
9. computer.press_key
10. computer.hotkey
11. computer.scroll
12. computer.verify

Every tool specifies:
- Strict parameter schema and types
- Deterministic validation
- Risk classification (LOW, MEDIUM, HIGH)
- Execution dispatch wrapping 4A-4D foundations
- Structured result and audit logging
"""

from dataclasses import dataclass, field
from enum import Enum
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from app.agent.input_controller import InputController
from app.agent.state_verifier import StateVerifier
from app.agent.window_manager import WindowManager
from app.commands.app_launcher import AppLauncher
from app.memory.audit_logger import AuditLogger
from app.vision.vision_service import ScreenVisionService

logger = logging.getLogger("NRAI.ComputerTools")


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


HIGH_RISK_PATTERNS = [
    r"\bformat\b",
    r"\brmdir\b",
    r"\bdel\b",
    r"\bdelete\b",
    r"\bshutdown\b",
    r"\brestart\b",
    r"\breboot\b",
    r"\bregedit\b",
    r"\bdiskpart\b",
    r"\bdrop\s+database\b",
    r"\bdrop\s+table\b",
    r"\btruncate\s+table\b",
    r"\bdelete\s+from\b",
    r"\bvssadmin\b",
    r"\bbcdedit\b",
    r"\breg\s+(?:add|delete)\b",
]

HIGH_RISK_KEYWORDS = [
    "delete all", "wipe disk", "factory reset", "format drive",
    "kill process tree", "uninstall system", "buy now", "purchase",
    "checkout", "pay now", "transfer money", "submit payment",
    "credit card", "bank account", "wire transfer",
    "password", "secret_key", "private_key", "token", "api_key",
    "passwd", "credentials"
]


@dataclass
class ToolExecutionResult:
    """Standardized structured result returned by every computer tool."""
    success: bool
    tool: str
    data: Dict[str, Any] = field(default_factory=dict)
    message: str = ""
    error: Optional[str] = None
    risk_level: RiskLevel = RiskLevel.LOW
    requires_confirmation: bool = False
    verified: bool = False
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "tool": self.tool,
            "data": self.data,
            "message": self.message,
            "error": self.error,
            "risk_level": self.risk_level.value,
            "requires_confirmation": self.requires_confirmation,
            "verified": self.verified,
            "timestamp": self.timestamp,
        }


@dataclass
class ToolDefinition:
    """Formal definition and validation schema of an approved computer tool."""
    name: str
    description: str
    required_params: List[str]
    optional_params: List[str]
    default_risk: RiskLevel
    handler: Callable[..., ToolExecutionResult]


class ComputerToolRegistry:
    """
    Central Registry for Approved Computer Interaction Tools.
    Enforces deterministic safety validation before any operation executes.
    """

    def __init__(
        self,
        app_launcher: Optional[AppLauncher] = None,
        window_manager: Optional[WindowManager] = None,
        vision_service: Optional[ScreenVisionService] = None,
        input_controller: Optional[InputController] = None,
        state_verifier: Optional[StateVerifier] = None,
        audit_logger: Optional[AuditLogger] = None,
    ):
        self.app_launcher = app_launcher or AppLauncher()
        self.window_manager = window_manager or WindowManager()
        self.vision_service = vision_service or ScreenVisionService(window_manager=self.window_manager)
        self.input_controller = input_controller or InputController(
            window_manager=self.window_manager,
            vision_service=self.vision_service,
        )
        self.state_verifier = state_verifier or StateVerifier()
        self.audit = audit_logger or AuditLogger()

        self._tools: Dict[str, ToolDefinition] = {}
        self._register_all_tools()

    # -------------------------------------------------------------------------
    # Tool Registration & Inspection
    # -------------------------------------------------------------------------

    def _register_all_tools(self) -> None:
        self.register(ToolDefinition(
            name="computer.open_app",
            description="Launch an authorized application from the whitelist.",
            required_params=["app_name"],
            optional_params=["args"],
            default_risk=RiskLevel.LOW,
            handler=self._tool_open_app,
        ))
        self.register(ToolDefinition(
            name="computer.list_windows",
            description="Enumerate visible top-level windows on the interactive desktop.",
            required_params=[],
            optional_params=["include_minimized"],
            default_risk=RiskLevel.LOW,
            handler=self._tool_list_windows,
        ))
        self.register(ToolDefinition(
            name="computer.focus_window",
            description="Bring an existing application window to foreground focus.",
            required_params=["target"],
            optional_params=[],
            default_risk=RiskLevel.LOW,
            handler=self._tool_focus_window,
        ))
        self.register(ToolDefinition(
            name="computer.inspect_screen",
            description="Read visible screen/window content using offline RapidOCR.",
            required_params=[],
            optional_params=["target_window"],
            default_risk=RiskLevel.LOW,
            handler=self._tool_inspect_screen,
        ))
        self.register(ToolDefinition(
            name="computer.find_text",
            description="Locate visible text and return screen-absolute coordinates.",
            required_params=["query"],
            optional_params=["target_window"],
            default_risk=RiskLevel.LOW,
            handler=self._tool_find_text,
        ))
        self.register(ToolDefinition(
            name="computer.click_target",
            description="Safely click a visual target or validated screen coordinate.",
            required_params=[],
            optional_params=["target_name", "coordinates", "target_window", "verify_expected"],
            default_risk=RiskLevel.LOW,
            handler=self._tool_click_target,
        ))
        self.register(ToolDefinition(
            name="computer.double_click",
            description="Safely double-click a visual target or validated coordinate.",
            required_params=[],
            optional_params=["target_name", "coordinates", "target_window", "verify_expected"],
            default_risk=RiskLevel.LOW,
            handler=self._tool_double_click,
        ))
        self.register(ToolDefinition(
            name="computer.type_text",
            description="Type text into the active focused window with safety filtering.",
            required_params=["text"],
            optional_params=["interval", "is_sensitive", "verify_expected"],
            default_risk=RiskLevel.LOW,
            handler=self._tool_type_text,
        ))
        self.register(ToolDefinition(
            name="computer.press_key",
            description="Press an approved keyboard key from the safety whitelist.",
            required_params=["key"],
            optional_params=["verify_expected"],
            default_risk=RiskLevel.LOW,
            handler=self._tool_press_key,
        ))
        self.register(ToolDefinition(
            name="computer.hotkey",
            description="Execute an approved keyboard shortcut combination.",
            required_params=["keys"],
            optional_params=["verify_expected"],
            default_risk=RiskLevel.LOW,
            handler=self._tool_hotkey,
        ))
        self.register(ToolDefinition(
            name="computer.scroll",
            description="Scroll active window content up or down safely.",
            required_params=["direction"],
            optional_params=["clicks"],
            default_risk=RiskLevel.LOW,
            handler=self._tool_scroll,
        ))
        self.register(ToolDefinition(
            name="computer.verify",
            description="Verify expected text or UI state is visible on screen.",
            required_params=["expected_text"],
            optional_params=["wait_time"],
            default_risk=RiskLevel.LOW,
            handler=self._tool_verify,
        ))

    def register(self, tool_def: ToolDefinition) -> None:
        self._tools[tool_def.name] = tool_def

    def get_tool(self, name: str) -> Optional[ToolDefinition]:
        return self._tools.get(name)

    def list_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "required_params": t.required_params,
                "optional_params": t.optional_params,
                "default_risk": t.default_risk.value,
            }
            for t in self._tools.values()
        ]

    # -------------------------------------------------------------------------
    # Deterministic Risk & Policy Assessment
    # -------------------------------------------------------------------------

    def assess_risk(self, tool_name: str, params: Dict[str, Any]) -> Tuple[RiskLevel, Optional[str]]:
        """
        Determines the risk classification of a proposed tool call.
        High-risk actions require explicit user confirmation.
        """
        tool_def = self.get_tool(tool_name)
        if not tool_def:
            return RiskLevel.HIGH, f"Unknown tool: '{tool_name}'"

        # 1. Parameter content scanning
        text_content = str(params.get("text", "") or params.get("target_name", "") or params.get("query", "")).lower()

        for pat in HIGH_RISK_PATTERNS:
            if re.search(pat, text_content):
                return RiskLevel.HIGH, f"Destructive command pattern detected: '{pat}'"

        for kw in HIGH_RISK_KEYWORDS:
            if kw in text_content:
                return RiskLevel.HIGH, f"High-risk keyword detected: '{kw}'"

        # 2. Key combination risks (e.g. closing apps with potential unsaved data)
        if tool_name == "computer.hotkey":
            keys_raw = params.get("keys", [])
            if isinstance(keys_raw, str):
                keys = [k.lower().strip() for k in keys_raw.split("+")]
            else:
                keys = [str(k).lower().strip() for k in keys_raw]
            if "f4" in keys and "alt" in keys:
                return RiskLevel.MEDIUM, "Closing window (Alt+F4) may lose unsaved work."

        return tool_def.default_risk, None

    def _redact_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Redacts sensitive credentials, tokens, and passwords before logging."""
        redacted = {}
        for k, v in params.items():
            k_low = k.lower()
            if any(s in k_low for s in ("pass", "secret", "token", "cred", "auth", "key")):
                redacted[k] = "[REDACTED]"
            elif k == "text" and (params.get("is_sensitive") or any(s in str(v).lower() for s in ("password", "secret", "token", "apikey"))):
                redacted[k] = "[REDACTED]"
            else:
                redacted[k] = v
        return redacted

    # -------------------------------------------------------------------------
    # Tool Execution Gateway
    # -------------------------------------------------------------------------

    def execute_tool(
        self,
        tool_name: str,
        params: Dict[str, Any],
        user_confirmed: bool = False,
    ) -> ToolExecutionResult:
        """
        Safely validates parameters, enforces risk policies, checks emergency stop,
        dispatches to tool handler, and logs the execution.
        """
        # 1. Emergency stop check
        if self.input_controller.is_emergency_stopped():
            return ToolExecutionResult(
                success=False,
                tool=tool_name,
                error="EMERGENCY_STOP_ACTIVE",
                message="Execution rejected: Emergency stop is active.",
            )

        # 2. Tool existence
        tool_def = self.get_tool(tool_name)
        if not tool_def:
            return ToolExecutionResult(
                success=False,
                tool=tool_name,
                error="INVALID_TOOL",
                message=f"Tool '{tool_name}' is not in the approved Computer Tool Registry.",
            )

        # 3. Parameter validation
        for req in tool_def.required_params:
            if req not in params or params[req] is None or (isinstance(params[req], str) and not params[req].strip()):
                return ToolExecutionResult(
                    success=False,
                    tool=tool_name,
                    error="MISSING_PARAMETER",
                    message=f"Tool '{tool_name}' is missing required parameter: '{req}'.",
                )

        # 4. Risk assessment
        risk, reason = self.assess_risk(tool_name, params)
        if risk == RiskLevel.HIGH and not user_confirmed:
            return ToolExecutionResult(
                success=False,
                tool=tool_name,
                risk_level=RiskLevel.HIGH,
                requires_confirmation=True,
                error="CONFIRMATION_REQUIRED",
                message=f"High-risk action blocked: {reason}. Explicit user confirmation is required before proceeding.",
            )

        # 5. Execute handler
        try:
            result = tool_def.handler(params)
            result.risk_level = risk

            # Audit logging with redaction
            self.audit.log_event(
                event_type="computer_tool_execution",
                details={
                    "tool": tool_name,
                    "params": self._redact_params(params),
                    "success": result.success,
                    "verified": result.verified,
                    "risk_level": risk.value,
                    "error": result.error,
                },
                status="success" if result.success else "failure",
            )
            return result
        except Exception as e:
            logger.error(f"[ComputerTools] Unhandled exception in '{tool_name}': {e}", exc_info=True)
            return ToolExecutionResult(
                success=False,
                tool=tool_name,
                error="EXECUTION_EXCEPTION",
                message=f"Tool '{tool_name}' encountered an execution error: {e}",
            )

    # Alias for ergonomics
    execute = execute_tool

    # -------------------------------------------------------------------------
    # Individual Tool Handlers (Deterministic Wrappers)
    # -------------------------------------------------------------------------

    def _tool_open_app(self, p: Dict[str, Any]) -> ToolExecutionResult:
        app_name = str(p["app_name"]).strip()
        args = p.get("args")

        # Strict authorization check against whitelist
        name_lower = app_name.lower()
        normalized = self.app_launcher.discovery.normalize_name(name_lower)
        if name_lower not in self.app_launcher.approved_applications and normalized not in self.app_launcher.approved_applications:
            return ToolExecutionResult(
                success=False,
                tool="computer.open_app",
                error="UNAUTHORIZED_APPLICATION",
                message=f"I am not authorized to launch '{app_name}'.",
                verified=False,
            )

        res = self.app_launcher.launch_detailed(app_name)
        success = res.get("success", False)
        return ToolExecutionResult(
            success=success,
            tool="computer.open_app",
            data=res,
            message=res.get("message", f"Application '{app_name}' launch {'succeeded' if success else 'failed'}."),
            verified=success,
            error=res.get("error"),
        )

    def _tool_list_windows(self, p: Dict[str, Any]) -> ToolExecutionResult:
        inc_min = bool(p.get("include_minimized", False))
        windows = self.window_manager.get_windows(include_cloaked=inc_min)
        return ToolExecutionResult(
            success=True,
            tool="computer.list_windows",
            data={"windows": windows, "count": len(windows)},
            message=f"Discovered {len(windows)} desktop window(s).",
            verified=True,
        )

    def _tool_focus_window(self, p: Dict[str, Any]) -> ToolExecutionResult:
        target = p["target"]
        timeout = float(p.get("timeout", 3.0))
        start_t = time.time()
        ok, msg = False, ""
        while True:
            ok, msg = self.window_manager.activate_window(target)
            if ok:
                break
            if "ambiguous" in msg.lower():
                break
            if (time.time() - start_t) >= timeout:
                break
            time.sleep(0.3)

        return ToolExecutionResult(
            success=ok,
            tool="computer.focus_window",
            data={"target": target, "message": msg},
            message=msg,
            verified=ok,
            error=None if ok else "WINDOW_NOT_FOUND",
        )

    def _tool_inspect_screen(self, p: Dict[str, Any]) -> ToolExecutionResult:
        target_win = p.get("target_window")
        res = self.vision_service.inspect_screen(target=target_win)
        success = res.get("success", False)
        return ToolExecutionResult(
            success=success,
            tool="computer.inspect_screen",
            data=res,
            message=res.get("summary", "Screen inspection complete."),
            verified=success,
        )

    def _tool_find_text(self, p: Dict[str, Any]) -> ToolExecutionResult:
        query = p["query"]
        target_win = p.get("target_window")
        res = self.vision_service.find_text(query, target=target_win)
        found = res.get("found", False)
        match_data = res.get("match")
        return ToolExecutionResult(
            success=found,
            tool="computer.find_text",
            data=res,
            message=f"Text '{query}' was {'found' if found else 'not found'} on screen.",
            verified=found,
        )

    def _tool_click_target(self, p: Dict[str, Any]) -> ToolExecutionResult:
        return self._execute_click(p, clicks=1, tool_name="computer.click_target")

    def _tool_double_click(self, p: Dict[str, Any]) -> ToolExecutionResult:
        return self._execute_click(p, clicks=2, tool_name="computer.double_click")

    def _execute_click(self, p: Dict[str, Any], clicks: int, tool_name: str) -> ToolExecutionResult:
        target_name = p.get("target_name")
        target_win = p.get("target_window")
        verify_expected = p.get("verify_expected")
        coords = p.get("coordinates")

        # 1. Parameter presence check
        if not coords and not target_name:
            return ToolExecutionResult(
                success=False,
                tool=tool_name,
                error="MISSING_TARGET",
                message="Either 'coordinates' or 'target_name' must be provided to click.",
            )

        # 2. Window validation before click
        if target_win:
            w_valid, w_msg = self.input_controller.validate_target_window(target_win)
            if not w_valid:
                return ToolExecutionResult(
                    success=False,
                    tool=tool_name,
                    error="WINDOW_DISAPPEARED",
                    message=f"Window safety abort: {w_msg}",
                )

        # 3. Resolve coordinates from vision if not supplied
        if not coords:
            find_res = self.vision_service.find_text(target_name, target=target_win)
            if not find_res.get("found"):
                return ToolExecutionResult(
                    success=False,
                    tool=tool_name,
                    error="TARGET_NOT_FOUND",
                    message=f"Could not locate visual target '{target_name}' on screen.",
                )
            coords = find_res["match"]["center"]
            if not target_win and find_res.get("target"):
                target_win = find_res["target"].get("title")

        if not isinstance(coords, (list, tuple)) or len(coords) < 2:
            return ToolExecutionResult(
                success=False,
                tool=tool_name,
                error="INVALID_COORDINATES",
                message=f"Invalid coordinates format: {coords}",
            )

        cx, cy = int(coords[0]), int(coords[1])

        # 4. Coordinate bounds validation
        val_ok, val_err, _ = self.input_controller.validate_coordinates(cx, cy)
        if not val_ok:
            return ToolExecutionResult(
                success=False,
                tool=tool_name,
                error="INVALID_COORDINATES",
                message=val_err,
            )

        # 5. InputController click execution
        act_res = self.input_controller.click_target(
            x=cx,
            y=cy,
            clicks=clicks,
            target_name=target_name,
            target_window=target_win,
            verify_expected=verify_expected,
        )

        return ToolExecutionResult(
            success=act_res.success,
            tool=tool_name,
            data=act_res.to_dict(),
            message=act_res.message,
            verified=act_res.verified,
            error=act_res.error,
        )

    def _tool_type_text(self, p: Dict[str, Any]) -> ToolExecutionResult:
        text = str(p["text"])
        interval = float(p.get("interval", 0.02))
        is_sensitive = bool(p.get("is_sensitive", False))
        verify_expected = p.get("verify_expected")

        act_res = self.input_controller.type_text(
            text=text,
            interval=interval,
            is_sensitive=is_sensitive,
            verify_expected=verify_expected,
        )
        return ToolExecutionResult(
            success=act_res.success,
            tool="computer.type_text",
            data=act_res.to_dict(),
            message=act_res.message,
            verified=act_res.verified,
            error=act_res.error,
        )

    def _tool_press_key(self, p: Dict[str, Any]) -> ToolExecutionResult:
        key = str(p["key"]).lower().strip()
        verify_expected = p.get("verify_expected")
        act_res = self.input_controller.press_key(key, verify_expected=verify_expected)
        return ToolExecutionResult(
            success=act_res.success,
            tool="computer.press_key",
            data=act_res.to_dict(),
            message=act_res.message,
            verified=act_res.verified,
            error=act_res.error,
        )

    def _tool_hotkey(self, p: Dict[str, Any]) -> ToolExecutionResult:
        keys = p["keys"]
        if isinstance(keys, str):
            keys = [k.strip() for k in keys.split("+")]
        verify_expected = p.get("verify_expected")
        act_res = self.input_controller.hotkey(*keys, verify_expected=verify_expected)
        return ToolExecutionResult(
            success=act_res.success,
            tool="computer.hotkey",
            data=act_res.to_dict(),
            message=act_res.message,
            verified=act_res.verified,
            error=act_res.error,
        )

    def _tool_scroll(self, p: Dict[str, Any]) -> ToolExecutionResult:
        direction = str(p["direction"]).lower().strip()
        if direction not in ("up", "down"):
            return ToolExecutionResult(
                success=False,
                tool="computer.scroll",
                error="INVALID_PARAMETER",
                message=f"Invalid scroll direction '{direction}'. Must be 'up' or 'down'.",
            )
        clicks = int(p.get("clicks", 500))
        if clicks <= 0:
            return ToolExecutionResult(
                success=False,
                tool="computer.scroll",
                error="INVALID_PARAMETER",
                message="Scroll clicks must be a positive integer.",
            )
        act_clicks = clicks if direction == "up" else -clicks
        act_res = self.input_controller.scroll(act_clicks)
        return ToolExecutionResult(
            success=act_res.success,
            tool="computer.scroll",
            data=act_res.to_dict(),
            message=act_res.message,
            verified=act_res.verified,
            error=act_res.error,
        )

    def _tool_verify(self, p: Dict[str, Any]) -> ToolExecutionResult:
        expected = str(p["expected_text"]).strip()
        wait_time = float(p.get("wait_time", 1.0))
        res = self.state_verifier.verify(expected, wait_time=wait_time)
        success = res.get("success", False)
        return ToolExecutionResult(
            success=success,
            tool="computer.verify",
            data=res,
            message=res.get("message", f"State verification {'passed' if success else 'failed'}."),
            verified=success,
            error=None if success else "VERIFICATION_FAILED",
        )
