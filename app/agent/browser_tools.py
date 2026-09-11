"""
NR-AI Browser Tool Registry & Deterministic Safety Contracts (Step 5 Phase 2).

Defines strongly validated, approved tools for web interaction:
1. browser.open
2. browser.navigate
3. browser.back
4. browser.forward
5. browser.refresh
6. browser.list_tabs
7. browser.switch_tab
8. browser.inspect_page
9. browser.find_text
10. browser.find_element
11. browser.click
12. browser.type
13. browser.scroll
14. browser.verify
15. browser.screenshot
16. browser.close

Every tool enforces:
- Strict parameter schemas and type checking
- Deterministic safety gates (URL whitelist, SSRF, download quarantine)
- Sensitive field detection & audit log masking
- Untrusted webpage data encapsulation
- Rate limiting (max 20 actions/min) and step budgeting
- Emergency stop integration
- No arbitrary JavaScript execution or raw page.evaluate() exposed to models
"""

from dataclasses import dataclass, field
from enum import Enum
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from app.agent.browser_driver import BrowserDriver
from app.agent.browser_safety import (
    BrowserRateLimiter,
    BrowserSafetyError,
    BrowserSafetyGate,
    BrowserWorkflowBounds,
    EmergencyStopActiveError,
    RateLimitExceededError,
    StaleTargetError,
    UntrustedWebData,
)
from app.memory.audit_logger import AuditLogger

logger = logging.getLogger("NRAI.BrowserTools")


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


HIGH_RISK_ACTION_PATTERNS = [
    r"\bpurchase\b",
    r"\bbuy\b",
    r"\border\b",
    r"\bcheckout\b",
    r"\bpay\b",
    r"\bpayment\b",
    r"\btransfer\b",
    r"\bdelete\s+account\b",
    r"\bcancel\s+subscription\b",
    r"\bterminate\b",
    r"\bpurge\b",
    r"\bchange\s+password\b",
]


@dataclass
class BrowserToolResult:
    """Structured result returned by every browser tool execution."""
    success: bool
    tool_name: str
    output: Any
    risk_level: RiskLevel
    error: Optional[str] = None
    confirmation_required: bool = False
    confirmation_message: Optional[str] = None
    audit_logged: bool = False
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "tool_name": self.tool_name,
            "output": self.output,
            "risk_level": self.risk_level.value,
            "error": self.error,
            "confirmation_required": self.confirmation_required,
            "confirmation_message": self.confirmation_message,
            "audit_logged": self.audit_logged,
            "timestamp": self.timestamp,
        }


@dataclass
class BrowserToolDefinition:
    """Metadata specification and handler for an approved browser tool."""
    name: str
    description: str
    required_params: List[str]
    optional_params: List[str]
    default_risk: RiskLevel
    handler: Callable[..., BrowserToolResult]


class BrowserToolRegistry:
    """
    Central Registry for Approved Browser Interaction Tools.
    Enforces deterministic safety validation before any operation executes.
    """

    def __init__(
        self,
        driver: Optional[BrowserDriver] = None,
        safety_gate: Optional[BrowserSafetyGate] = None,
        audit_logger: Optional[AuditLogger] = None,
        rate_limiter: Optional[BrowserRateLimiter] = None,
        workflow_bounds: Optional[BrowserWorkflowBounds] = None,
    ):
        self.safety_gate = safety_gate or BrowserSafetyGate()
        self.driver = driver or BrowserDriver(safety_gate=self.safety_gate)
        self.audit = audit_logger or AuditLogger()
        self.rate_limiter = rate_limiter or BrowserRateLimiter(max_actions_per_minute=20)
        self.bounds = workflow_bounds or BrowserWorkflowBounds(max_steps=25, max_retries=2)

        self._emergency_stopped = False
        self._target_cache: Dict[str, Dict[str, Any]] = {}

        self._tools: Dict[str, BrowserToolDefinition] = {}
        self._register_all_tools()

    # -------------------------------------------------------------------------
    # Tool Registration
    # -------------------------------------------------------------------------

    def _register_all_tools(self) -> None:
        self.register(BrowserToolDefinition(
            name="browser.open",
            description="Open and initialize a controlled, isolated browser session.",
            required_params=[],
            optional_params=["headless"],
            default_risk=RiskLevel.LOW,
            handler=self._tool_open,
        ))
        self.register(BrowserToolDefinition(
            name="browser.navigate",
            description="Safely navigate active tab to an approved HTTPS or local URL.",
            required_params=["url"],
            optional_params=["timeout_ms"],
            default_risk=RiskLevel.MEDIUM,
            handler=self._tool_navigate,
        ))
        self.register(BrowserToolDefinition(
            name="browser.back",
            description="Navigate back in browser history.",
            required_params=[],
            optional_params=[],
            default_risk=RiskLevel.LOW,
            handler=self._tool_back,
        ))
        self.register(BrowserToolDefinition(
            name="browser.forward",
            description="Navigate forward in browser history.",
            required_params=[],
            optional_params=[],
            default_risk=RiskLevel.LOW,
            handler=self._tool_forward,
        ))
        self.register(BrowserToolDefinition(
            name="browser.refresh",
            description="Reload the currently active page.",
            required_params=[],
            optional_params=[],
            default_risk=RiskLevel.LOW,
            handler=self._tool_refresh,
        ))
        self.register(BrowserToolDefinition(
            name="browser.list_tabs",
            description="List all currently open browser tabs and identify active tab.",
            required_params=[],
            optional_params=[],
            default_risk=RiskLevel.LOW,
            handler=self._tool_list_tabs,
        ))
        self.register(BrowserToolDefinition(
            name="browser.switch_tab",
            description="Switch active focus to a specific tab by ID or index.",
            required_params=["tab_id"],
            optional_params=[],
            default_risk=RiskLevel.LOW,
            handler=self._tool_switch_tab,
        ))
        self.register(BrowserToolDefinition(
            name="browser.inspect_page",
            description="Extract interactive elements and text from page as untrusted data.",
            required_params=[],
            optional_params=["max_elements"],
            default_risk=RiskLevel.LOW,
            handler=self._tool_inspect_page,
        ))
        self.register(BrowserToolDefinition(
            name="browser.find_text",
            description="Search visible text content on the active webpage.",
            required_params=["query"],
            optional_params=["case_sensitive"],
            default_risk=RiskLevel.LOW,
            handler=self._tool_find_text,
        ))
        self.register(BrowserToolDefinition(
            name="browser.find_element",
            description="Locate an interactive element by role, text, or CSS selector.",
            required_params=[],
            optional_params=["selector", "role", "name", "text"],
            default_risk=RiskLevel.LOW,
            handler=self._tool_find_element,
        ))
        self.register(BrowserToolDefinition(
            name="browser.click",
            description="Click a verified interactive element on the active page.",
            required_params=["selector"],
            optional_params=["target_ref", "force"],
            default_risk=RiskLevel.MEDIUM,
            handler=self._tool_click,
        ))
        self.register(BrowserToolDefinition(
            name="browser.type",
            description="Type text into an input element. Masks sensitive credentials in logs.",
            required_params=["selector", "text"],
            optional_params=["clear_first", "press_enter"],
            default_risk=RiskLevel.MEDIUM,
            handler=self._tool_type,
        ))
        self.register(BrowserToolDefinition(
            name="browser.scroll",
            description="Scroll the active page viewport up or down.",
            required_params=[],
            optional_params=["direction", "pixels"],
            default_risk=RiskLevel.LOW,
            handler=self._tool_scroll,
        ))
        self.register(BrowserToolDefinition(
            name="browser.verify",
            description="Deterministically verify page URL, text, or element presence.",
            required_params=[],
            optional_params=["expected_url_pattern", "expected_text", "expected_selector"],
            default_risk=RiskLevel.LOW,
            handler=self._tool_verify,
        ))
        self.register(BrowserToolDefinition(
            name="browser.screenshot",
            description="Capture viewport or element screenshot to a safe local file.",
            required_params=[],
            optional_params=["path", "selector"],
            default_risk=RiskLevel.LOW,
            handler=self._tool_screenshot,
        ))
        self.register(BrowserToolDefinition(
            name="browser.close",
            description="Safely close a specific tab or shut down the entire browser.",
            required_params=[],
            optional_params=["tab_id", "close_all"],
            default_risk=RiskLevel.LOW,
            handler=self._tool_close,
        ))

    def register(self, tool_def: BrowserToolDefinition) -> None:
        self._tools[tool_def.name] = tool_def

    def get_tool(self, name: str) -> Optional[BrowserToolDefinition]:
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
    # Emergency Stop
    # -------------------------------------------------------------------------

    def activate_emergency_stop(self) -> None:
        """Immediately halts all browser actions and freezes execution."""
        logger.warning("[BrowserSafety] EMERGENCY STOP ACTIVATED. All browser operations frozen.")
        self._emergency_stopped = True

    def reset_emergency_stop(self) -> None:
        """Resets emergency stop status."""
        logger.info("[BrowserSafety] Emergency stop cleared.")
        self._emergency_stopped = False

    def is_emergency_stopped(self) -> bool:
        return self._emergency_stopped

    # -------------------------------------------------------------------------
    # Deterministic Risk Assessment
    # -------------------------------------------------------------------------

    def assess_risk(self, tool_name: str, params: Dict[str, Any]) -> Tuple[RiskLevel, Optional[str]]:
        """
        Determines the risk classification of a proposed browser action.
        High-risk actions require explicit user confirmation.
        """
        tool_def = self.get_tool(tool_name)
        if not tool_def:
            return RiskLevel.HIGH, f"Unknown tool: '{tool_name}'"

        # Check for high-risk action keywords (purchases, payments, deletions)
        text_content = str(
            params.get("text", "") or
            params.get("selector", "") or
            params.get("url", "")
        ).lower()

        for pat in HIGH_RISK_ACTION_PATTERNS:
            if re.search(pat, text_content):
                return RiskLevel.HIGH, f"Action matches high-risk pattern: '{pat}'"

        # Check for typing into password or sensitive credential fields
        if tool_name == "browser.type":
            selector = str(params.get("selector", "")).lower()
            if self.safety_gate.is_sensitive_field(name=selector, id_attr=selector, placeholder=selector):
                return RiskLevel.MEDIUM, "Action targets sensitive input field (redaction active)."

        return tool_def.default_risk, None

    # -------------------------------------------------------------------------
    # Core Execution Dispatcher
    # -------------------------------------------------------------------------

    def execute_tool(
        self,
        tool_name: str,
        params: Optional[Dict[str, Any]] = None,
        confirmed: bool = False,
    ) -> BrowserToolResult:
        """
        Validates safety contracts and executes approved browser tool.
        """
        params = params or {}

        # 1. Emergency Stop Check
        if self._emergency_stopped:
            return BrowserToolResult(
                success=False,
                tool_name=tool_name,
                output=None,
                risk_level=RiskLevel.HIGH,
                error="Operation rejected: Emergency Stop is currently ACTIVE.",
            )

        # 2. Tool Definition Check
        tool_def = self.get_tool(tool_name)
        if not tool_def:
            return BrowserToolResult(
                success=False,
                tool_name=tool_name,
                output=None,
                risk_level=RiskLevel.HIGH,
                error=f"Unauthorized browser tool: '{tool_name}'",
            )

        # 3. Parameter Validation
        missing = [p for p in tool_def.required_params if p not in params]
        if missing:
            return BrowserToolResult(
                success=False,
                tool_name=tool_name,
                output=None,
                risk_level=tool_def.default_risk,
                error=f"Missing required parameter(s): {missing}",
            )

        # 4. Rate Limiting Check
        try:
            self.rate_limiter.check_and_record()
        except RateLimitExceededError as e:
            return BrowserToolResult(
                success=False,
                tool_name=tool_name,
                output=None,
                risk_level=RiskLevel.HIGH,
                error=str(e),
            )

        # 5. Workflow Step Budget Check
        try:
            self.bounds.record_step()
        except BrowserSafetyError as e:
            return BrowserToolResult(
                success=False,
                tool_name=tool_name,
                output=None,
                risk_level=RiskLevel.HIGH,
                error=str(e),
            )

        # 6. Risk Assessment & Confirmation Boundary
        risk, reason = self.assess_risk(tool_name, params)
        if risk == RiskLevel.HIGH and not confirmed:
            return BrowserToolResult(
                success=False,
                tool_name=tool_name,
                output=None,
                risk_level=RiskLevel.HIGH,
                confirmation_required=True,
                confirmation_message=f"CONFIRMATION_REQUIRED: High-risk action detected ({reason}). Confirm to proceed.",
            )

        # 7. Execute Handler
        t0 = time.time()
        try:
            result = tool_def.handler(params)
        except BrowserSafetyError as e:
            result = BrowserToolResult(
                success=False,
                tool_name=tool_name,
                output=None,
                risk_level=risk,
                error=str(e),
            )
        except Exception as e:
            logger.exception(f"Unhandled error during tool '{tool_name}': {e}")
            result = BrowserToolResult(
                success=False,
                tool_name=tool_name,
                output=None,
                risk_level=risk,
                error=f"Execution error: {str(e)}",
            )

        # 8. Audit Logging with Sensitive Data Sanitization
        sanitized_params = self.safety_gate.sanitize_audit_payload(params)
        audit_entry = {
            "timestamp": time.time(),
            "tool": tool_name,
            "params": sanitized_params,
            "success": result.success,
            "error": result.error,
            "duration_ms": (time.time() - t0) * 1000.0,
        }
        try:
            self.audit.log_event(
                event_type="browser_tool_execution",
                details=audit_entry,
                status="success" if result.success else "failure",
            )
            result.audit_logged = True
        except Exception as e:
            logger.warning(f"Failed to record audit log: {e}")

        return result

    # -------------------------------------------------------------------------
    # Tool Handlers
    # -------------------------------------------------------------------------

    def _tool_open(self, params: Dict[str, Any]) -> BrowserToolResult:
        headless = params.get("headless", self.driver.headless)
        self.driver.headless = headless
        self.driver.launch()
        return BrowserToolResult(
            success=True,
            tool_name="browser.open",
            output={
                "session_id": self.driver.session_id,
                "headless": headless,
                "running": self.driver.is_running(),
            },
            risk_level=RiskLevel.LOW,
        )

    def _tool_navigate(self, params: Dict[str, Any]) -> BrowserToolResult:
        url = params["url"]
        timeout_ms = params.get("timeout_ms")
        nav_result = self.driver.navigate(url=url, timeout_ms=timeout_ms)
        return BrowserToolResult(
            success=True,
            tool_name="browser.navigate",
            output=nav_result,
            risk_level=RiskLevel.MEDIUM,
        )

    def _tool_back(self, params: Dict[str, Any]) -> BrowserToolResult:
        res = self.driver.back()
        return BrowserToolResult(success=True, tool_name="browser.back", output=res, risk_level=RiskLevel.LOW)

    def _tool_forward(self, params: Dict[str, Any]) -> BrowserToolResult:
        res = self.driver.forward()
        return BrowserToolResult(success=True, tool_name="browser.forward", output=res, risk_level=RiskLevel.LOW)

    def _tool_refresh(self, params: Dict[str, Any]) -> BrowserToolResult:
        res = self.driver.refresh()
        return BrowserToolResult(success=True, tool_name="browser.refresh", output=res, risk_level=RiskLevel.LOW)

    def _tool_list_tabs(self, params: Dict[str, Any]) -> BrowserToolResult:
        tabs = self.driver.list_tabs()
        return BrowserToolResult(
            success=True,
            tool_name="browser.list_tabs",
            output=[{"tab_id": t.tab_id, "title": t.title, "url": t.url, "active": t.active} for t in tabs],
            risk_level=RiskLevel.LOW,
        )

    def _tool_switch_tab(self, params: Dict[str, Any]) -> BrowserToolResult:
        res = self.driver.switch_tab(params["tab_id"])
        return BrowserToolResult(success=True, tool_name="browser.switch_tab", output=res, risk_level=RiskLevel.LOW)

    def _tool_inspect_page(self, params: Dict[str, Any]) -> BrowserToolResult:
        """
        Extracts visible interactive elements and text from page.
        Wraps content in UntrustedWebData to prevent prompt injection.
        """
        page = self.driver.get_active_page()
        max_elements = params.get("max_elements", 50)

        # Extract semantic elements safely without arbitrary JS execution
        elements_data = []
        locators = page.locator("a, button, input, select, textarea, [role=button], h1, h2, h3, p")
        count = min(locators.count(), max_elements)

        for i in range(count):
            loc = locators.nth(i)
            try:
                if not loc.is_visible():
                    continue
                tag = loc.evaluate("el => el.tagName.toLowerCase()")
                text = (loc.inner_text() or "").strip()[:80]
                box = loc.bounding_box() or {}
                selector = f"{tag}:nth-of-type({i+1})"
                elements_data.append({
                    "ref": i + 1,
                    "tag": tag,
                    "text": text,
                    "bounds": box,
                    "timestamp": time.time(),
                })
            except Exception:
                continue

        # Cache targets for freshness checking
        now = time.time()
        for el in elements_data:
            self._target_cache[str(el["ref"])] = el

        raw_text = page.inner_text("body")[:2000] if page.locator("body").count() > 0 else ""
        untrusted = UntrustedWebData(
            raw_content=raw_text,
            source_url=page.url,
            title=page.title(),
        )

        return BrowserToolResult(
            success=True,
            tool_name="browser.inspect_page",
            output={
                "url": page.url,
                "title": page.title(),
                "element_count": len(elements_data),
                "elements": elements_data,
                "untrusted_context": untrusted.to_safe_context(),
                "injection_detected": untrusted.injection_detected,
            },
            risk_level=RiskLevel.LOW,
        )

    def _tool_find_text(self, params: Dict[str, Any]) -> BrowserToolResult:
        query = params["query"]
        case_sensitive = params.get("case_sensitive", False)
        page = self.driver.get_active_page()

        loc = page.get_by_text(query, exact=case_sensitive)
        count = loc.count()
        matches = []
        for i in range(min(count, 10)):
            item = loc.nth(i)
            try:
                matches.append({
                    "text": item.inner_text()[:100],
                    "bounds": item.bounding_box(),
                })
            except Exception:
                continue

        return BrowserToolResult(
            success=True,
            tool_name="browser.find_text",
            output={"query": query, "found": count > 0, "match_count": count, "matches": matches},
            risk_level=RiskLevel.LOW,
        )

    def _tool_find_element(self, params: Dict[str, Any]) -> BrowserToolResult:
        page = self.driver.get_active_page()
        selector = params.get("selector")
        role = params.get("role")
        name = params.get("name")
        text = params.get("text")

        if role:
            loc = page.get_by_role(role, name=name)
        elif text:
            loc = page.get_by_text(text)
        elif selector:
            loc = page.locator(selector)
        else:
            return BrowserToolResult(
                success=False,
                tool_name="browser.find_element",
                output=None,
                risk_level=RiskLevel.LOW,
                error="Must provide selector, role, or text to find_element.",
            )

        count = loc.count()
        found = count > 0
        box = loc.first.bounding_box() if found else None

        return BrowserToolResult(
            success=True,
            tool_name="browser.find_element",
            output={"found": found, "count": count, "bounds": box},
            risk_level=RiskLevel.LOW,
        )

    def _tool_click(self, params: Dict[str, Any]) -> BrowserToolResult:
        selector = params["selector"]
        target_ref = params.get("target_ref")

        # Stale target validation if target_ref provided
        if target_ref:
            ref_str = str(target_ref)
            if ref_str in self._target_cache:
                cached = self._target_cache[ref_str]
                if time.time() - cached["timestamp"] > self.safety_gate.target_ttl_seconds:
                    raise StaleTargetError(
                        f"Target element reference '{target_ref}' is stale "
                        f"(exceeded {self.safety_gate.target_ttl_seconds}s TTL). Re-inspect page required."
                    )

        page = self.driver.get_active_page()
        loc = page.locator(selector).first
        if not loc.is_visible():
            return BrowserToolResult(
                success=False,
                tool_name="browser.click",
                output=None,
                risk_level=RiskLevel.MEDIUM,
                error=f"Element '{selector}' is not visible.",
            )

        loc.click(timeout=self.driver.default_timeout_ms)
        return BrowserToolResult(
            success=True,
            tool_name="browser.click",
            output={"clicked": True, "selector": selector},
            risk_level=RiskLevel.MEDIUM,
        )

    def _tool_type(self, params: Dict[str, Any]) -> BrowserToolResult:
        selector = params["selector"]
        text = params["text"]
        clear_first = params.get("clear_first", True)
        press_enter = params.get("press_enter", False)

        page = self.driver.get_active_page()
        loc = page.locator(selector).first

        if clear_first:
            loc.fill("")
        loc.type(text, timeout=self.driver.default_timeout_ms)

        if press_enter:
            loc.press("Enter")

        # Mask sensitive text in result payload
        is_sensitive = self.safety_gate.is_sensitive_field(name=selector, id_attr=selector)
        reported_text = self.safety_gate.redact_sensitive_value(text) if is_sensitive else text

        return BrowserToolResult(
            success=True,
            tool_name="browser.type",
            output={"selector": selector, "typed_length": len(text), "value": reported_text},
            risk_level=RiskLevel.MEDIUM,
        )

    def _tool_scroll(self, params: Dict[str, Any]) -> BrowserToolResult:
        direction = params.get("direction", "down").lower()
        pixels = params.get("pixels", 400)
        page = self.driver.get_active_page()

        dy = pixels if direction == "down" else -pixels
        page.mouse.wheel(0, dy)
        return BrowserToolResult(
            success=True,
            tool_name="browser.scroll",
            output={"scrolled": True, "direction": direction, "pixels": pixels},
            risk_level=RiskLevel.LOW,
        )

    def _tool_verify(self, params: Dict[str, Any]) -> BrowserToolResult:
        page = self.driver.get_active_page()
        expected_url = params.get("expected_url_pattern")
        expected_text = params.get("expected_text")
        expected_selector = params.get("expected_selector")

        evidence: Dict[str, Any] = {"verified": True, "details": []}

        if expected_url:
            matched = bool(re.search(expected_url, page.url))
            evidence["details"].append({"check": "url", "expected": expected_url, "actual": page.url, "matched": matched})
            if not matched:
                evidence["verified"] = False

        if expected_text:
            content = page.inner_text("body") if page.locator("body").count() > 0 else ""
            matched = expected_text in content
            evidence["details"].append({"check": "text", "expected": expected_text, "matched": matched})
            if not matched:
                evidence["verified"] = False

        if expected_selector:
            count = page.locator(expected_selector).count()
            matched = count > 0
            evidence["details"].append({"check": "selector", "expected": expected_selector, "matched": matched, "count": count})
            if not matched:
                evidence["verified"] = False

        return BrowserToolResult(
            success=evidence["verified"],
            tool_name="browser.verify",
            output=evidence,
            risk_level=RiskLevel.LOW,
            error=None if evidence["verified"] else "Verification assertions failed.",
        )

    def _tool_screenshot(self, params: Dict[str, Any]) -> BrowserToolResult:
        path = params.get("path")
        selector = params.get("selector")
        saved_path = self.driver.screenshot(path=path, selector=selector)
        return BrowserToolResult(
            success=True,
            tool_name="browser.screenshot",
            output={"path": saved_path},
            risk_level=RiskLevel.LOW,
        )

    def _tool_close(self, params: Dict[str, Any]) -> BrowserToolResult:
        tab_id = params.get("tab_id")
        close_all = params.get("close_all", False)

        if close_all:
            self.driver.close()
            return BrowserToolResult(
                success=True,
                tool_name="browser.close",
                output={"status": "ALL_CLOSED"},
                risk_level=RiskLevel.LOW,
            )
        else:
            res = self.driver.close_tab(tab_id=tab_id)
            return BrowserToolResult(
                success=True,
                tool_name="browser.close",
                output=res,
                risk_level=RiskLevel.LOW,
            )
