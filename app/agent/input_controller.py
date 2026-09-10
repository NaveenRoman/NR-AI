"""
NR-AI Controlled Input Controller & Safety Foundation (Step 4D).

Provides safe, audited, rate-limited, and verified computer-interaction capabilities:
1. Move mouse to validated target
2. Click validated target
3. Double-click validated target
4. Scroll
5. Type text safely (with sensitive redaction)
6. Press approved keyboard keys
7. Execute approved hotkeys
8. Emergency stop mechanism (thread-safe)
9. Rate limiting & action pacing
10. Target window validation & coordinate bounds enforcement
11. State & action verification
"""

from dataclasses import dataclass, field
import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    import pyautogui
    # Configure safety features of PyAutoGUI
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05
except Exception as _err:
    pyautogui = None

from app.agent.state_verifier import StateVerifier
from app.agent.action_verifier import ActionVerifier
from app.agent.window_manager import WindowManager
from app.memory.audit_logger import AuditLogger
from app.vision.vision_service import ScreenVisionService

logger = logging.getLogger("NRAI.InputController")


# Explicit Approved Keys Whitelist
APPROVED_KEYS = {
    # Enter / Return / Navigation
    "enter", "return", "tab", "space", "backspace", "delete", "del",
    "up", "down", "left", "right",
    "home", "end", "pageup", "pgup", "pagedown", "pgdn",
    "escape", "esc", "insert",
    # Modifiers
    "shift", "ctrl", "control", "alt", "win", "windows",
    # Function keys
    "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
}

KEY_NORMALIZATION = {
    "return": "enter",
    "del": "delete",
    "control": "ctrl",
    "esc": "escape",
    "pgup": "pageup",
    "pgdn": "pagedown",
    "windows": "win",
}

# Approved Safe Hotkey Combinations
APPROVED_HOTKEYS = {
    ("ctrl", "c"),
    ("ctrl", "v"),
    ("ctrl", "x"),
    ("ctrl", "z"),
    ("ctrl", "s"),
    ("ctrl", "a"),
    ("ctrl", "f"),
    ("ctrl", "r"),
    ("ctrl", "t"),
    ("ctrl", "w"),
    ("ctrl", "y"),
    ("ctrl", "shift", "t"),
    ("ctrl", "shift", "n"),
    ("alt", "tab"),
    ("alt", "f4"),
    ("win", "d"),
    ("win", "r"),
}

# Dangerous Keywords & Patterns (Blocked from typing or clicking without policy confirmation)
DANGEROUS_PATTERNS = [
    r"\bformat\s+[a-z]:",
    r"\brmdir\s+/[sq]",
    r"\bdel\s+/[fq]",
    r"\bshutdown\b",
    r"\brestart\b",
    r"\breboot\b",
    r"\bregedit\b",
    r"\bdiskpart\b",
    r"\bdrop\s+database\b",
    r"\bdrop\s+table\b",
    r"\btruncate\s+table\b",
]

SENSITIVE_KEYWORDS = [
    "password", "passwd", "pwd", "secret", "token",
    "api_key", "apikey", "pin", "cvv", "credit_card", "ssn"
]

TARGET_TTL_SECONDS = 15.0
DEFAULT_MAX_ACTIONS = 20
DEFAULT_MIN_DELAY = 0.15


@dataclass
class InputActionResult:
    """Structured result of a controlled computer interaction action."""
    success: bool
    action: str
    target: Optional[str] = None
    coordinates: Optional[Tuple[int, int]] = None
    window: Optional[str] = None
    message: str = ""
    error: Optional[str] = None
    verified: bool = False
    verification_details: Dict[str, Any] = field(default_factory=dict)
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "action": self.action,
            "target": self.target,
            "coordinates": list(self.coordinates) if self.coordinates else None,
            "window": self.window,
            "message": self.message,
            "error": self.error,
            "verified": self.verified,
            "verification_details": self.verification_details,
            "data": self.data,
            "timestamp": self.timestamp,
        }


class InputController:
    """
    Controlled, safe execution engine for keyboard and mouse interactions.
    Enforces target validation, bounds checking, emergency stop, rate limiting,
    and post-action verification.
    """

    def __init__(
        self,
        window_manager: Optional[WindowManager] = None,
        vision_service: Optional[ScreenVisionService] = None,
        audit_logger: Optional[AuditLogger] = None,
        max_actions_per_sequence: int = DEFAULT_MAX_ACTIONS,
        min_action_delay: float = DEFAULT_MIN_DELAY,
        target_ttl: float = TARGET_TTL_SECONDS,
    ):
        self.window_manager = window_manager or WindowManager()
        self.vision_service = vision_service or ScreenVisionService(window_manager=self.window_manager)
        self.audit = audit_logger or AuditLogger()
        self.state_verifier = StateVerifier()
        self.action_verifier = ActionVerifier()

        # Configuration & Rate Limiting
        self.max_actions_per_sequence = max_actions_per_sequence
        self.min_action_delay = min_action_delay
        self.target_ttl = target_ttl

        self._action_count = 0
        self._last_action_time = 0.0
        self._sequence_lock = threading.Lock()

        # Emergency Stop Mechanism
        self._emergency_stop_event = threading.Event()

    # -------------------------------------------------------------------------
    # Emergency Stop
    # -------------------------------------------------------------------------

    def emergency_stop(self) -> Dict[str, Any]:
        """
        Immediately halts further input actions and rejects future queued actions.
        Thread-safe.
        """
        self._emergency_stop_event.set()
        logger.warning("[InputController] EMERGENCY STOP ACTIVATED. All input operations frozen.")
        self.audit.log_event(
            "emergency_stop",
            {"status": "activated", "reason": "User or safety trigger"},
            status="warning",
        )
        return {
            "success": True,
            "emergency_stop": True,
            "message": "Emergency stop activated. All computer interactions are halted.",
        }

    def reset_emergency_stop(self) -> Dict[str, Any]:
        """
        Resets the emergency stop, allowing safe actions to resume.
        Thread-safe.
        """
        self._emergency_stop_event.clear()
        with self._sequence_lock:
            self._action_count = 0
        logger.info("[InputController] Emergency stop reset. Operations restored.")
        self.audit.log_event(
            "emergency_stop",
            {"status": "reset", "reason": "Explicit operator reset"},
            status="success",
        )
        return {
            "success": True,
            "emergency_stop": False,
            "message": "Emergency stop has been reset. Computer input control resumed.",
        }

    def is_emergency_stopped(self) -> bool:
        """Returns True if emergency stop is currently engaged."""
        return self._emergency_stop_event.is_set()

    # -------------------------------------------------------------------------
    # Screen Bounds & Coordinate Validation
    # -------------------------------------------------------------------------

    def get_screen_bounds(self) -> Tuple[int, int]:
        """Returns (width, height) of the current desktop display."""
        if pyautogui:
            size = pyautogui.size()
            return int(size.width), int(size.height)
        return 1920, 1080

    def validate_coordinates(self, x: Any, y: Any) -> Tuple[bool, Optional[str], Optional[Tuple[int, int]]]:
        """
        Strictly validates that coordinates are numeric and lie within current screen bounds.
        """
        try:
            ix = int(x)
            iy = int(y)
        except (ValueError, TypeError):
            return False, f"Coordinates must be numeric integers, received: ({x}, {y})", None

        w, h = self.get_screen_bounds()
        if ix < 0 or iy < 0:
            return False, f"Coordinates cannot be negative: ({ix}, {iy})", None
        if ix >= w or iy >= h:
            return False, f"Coordinates ({ix}, {iy}) exceed screen bounds ({w}x{h})", None

        return True, None, (ix, iy)

    def validate_target(
        self,
        target_info: Dict[str, Any],
        require_window: bool = False,
    ) -> Tuple[bool, Optional[str], Optional[Tuple[int, int]]]:
        """
        Validates target dictionary for freshness (TTL), bounds, and window presence.
        """
        if not isinstance(target_info, dict):
            return False, "Target must be a structured dictionary.", None

        # 1. TTL Check
        timestamp = target_info.get("timestamp")
        if timestamp:
            age = time.time() - float(timestamp)
            if age > self.target_ttl:
                return False, f"Target data is stale ({age:.1f}s > {self.target_ttl}s limit). Please re-locate target.", None

        # 2. Coordinates
        center = target_info.get("center") or target_info.get("coordinates")
        if not center or len(center) != 2:
            return False, "Target lacks valid center coordinates [x, y].", None

        valid_coord, coord_err, clean_coords = self.validate_coordinates(center[0], center[1])
        if not valid_coord:
            return False, coord_err, None

        # 3. Target Window Validation
        if require_window:
            target_win = target_info.get("window") or target_info.get("target_window")
            if target_win:
                win_valid, win_msg = self.validate_target_window(target_win)
                if not win_valid:
                    return False, win_msg, None

        return True, None, clean_coords

    def validate_target_window(self, target_window_spec: Union[str, int]) -> Tuple[bool, str]:
        """
        Validates whether the target window is still present and matches the active foreground window.
        """
        active_win = self.window_manager.get_active_window()
        if not active_win:
            return False, "No active desktop window found."

        spec_str = str(target_window_spec).lower().strip()
        active_title = active_win.get("title", "").lower()
        active_proc = active_win.get("process_name", "").lower()
        active_hwnd = str(active_win.get("hwnd", ""))

        if spec_str in active_title or spec_str in active_proc or spec_str == active_hwnd:
            return True, f"Target window matches active window '{active_win.get('title')}'."

        # Check if window exists on desktop and activate it if needed
        found = self.window_manager.find_window_deterministic(str(target_window_spec))
        if found:
            # Safely bring to front
            self.window_manager.activate_window(str(target_window_spec))
            time.sleep(0.2)
            new_active = self.window_manager.get_active_window()
            if new_active and (spec_str in new_active.get("title", "").lower() or spec_str in new_active.get("process_name", "").lower()):
                return True, f"Activated target window '{new_active.get('title')}'."

        return False, f"Target window '{target_window_spec}' is not currently available or active."

    # -------------------------------------------------------------------------
    # Dangerous Action & Safety Filtering
    # -------------------------------------------------------------------------

    def is_dangerous_action(
        self,
        action: str,
        target_or_text: str = "",
    ) -> Tuple[bool, Optional[str]]:
        """
        Checks whether the action or text contains forbidden or destructive operations.
        """
        c = (target_or_text or "").lower()

        # Check destructive command patterns
        for pat in DANGEROUS_PATTERNS:
            if re.search(pat, c):
                return True, f"Destructive command detected and blocked by safety policy: '{pat}'"

        # Check dangerous target keywords
        dangerous_targets = ["format", "wipe disk", "delete system", "factory reset", "shut down", "restart pc", "reboot"]
        if any(dt in c for dt in dangerous_targets):
            return True, f"Dangerous target '{target_or_text}' blocked by safety policy."

        return False, None

    def is_sensitive_text(self, text: str) -> bool:
        """Determines if text looks like a password, secret, or sensitive credential."""
        t_lower = text.lower()
        return any(k in t_lower for k in SENSITIVE_KEYWORDS)

    # -------------------------------------------------------------------------
    # Rate Limiting & Sequence Protection
    # -------------------------------------------------------------------------

    def _check_rate_limit(self) -> Optional[str]:
        """Enforces inter-action delay and maximum sequence limits."""
        with self._sequence_lock:
            now = time.time()
            elapsed = now - self._last_action_time
            if elapsed < self.min_action_delay:
                time.sleep(self.min_action_delay - elapsed)

            if self._action_count >= self.max_actions_per_sequence:
                return (
                    f"Rate limit exceeded: Maximum consecutive action limit ({self.max_actions_per_sequence}) reached. "
                    "Reset sequence before continuing."
                )

            self._action_count += 1
            self._last_action_time = time.time()
            return None

    def reset_sequence_counter(self) -> None:
        """Resets the consecutive action counter."""
        with self._sequence_lock:
            self._action_count = 0

    # -------------------------------------------------------------------------
    # Mouse Operations
    # -------------------------------------------------------------------------

    def move_to_target(
        self,
        x: int,
        y: int,
        duration: float = 0.2,
        target_window: Optional[str] = None,
    ) -> InputActionResult:
        """
        Safely moves mouse cursor to validated coordinates.
        """
        if self.is_emergency_stopped():
            return InputActionResult(
                success=False,
                action="move_to",
                coordinates=(x, y),
                error="EMERGENCY_STOP_ACTIVE",
                message="Action rejected: Emergency stop is active.",
            )

        rate_err = self._check_rate_limit()
        if rate_err:
            return InputActionResult(success=False, action="move_to", error="RATE_LIMIT", message=rate_err)

        valid_coord, err_msg, clean_coords = self.validate_coordinates(x, y)
        if not valid_coord:
            return InputActionResult(success=False, action="move_to", error="INVALID_COORDINATES", message=err_msg)

        cx, cy = clean_coords

        if target_window:
            w_valid, w_err = self.validate_target_window(target_window)
            if not w_valid:
                return InputActionResult(success=False, action="move_to", coordinates=(cx, cy), error="WINDOW_UNAVAILABLE", message=w_err)

        try:
            pyautogui.moveTo(cx, cy, duration=max(0.1, min(duration, 1.0)))
            active_win = self.window_manager.get_active_window()
            win_title = active_win.get("title") if active_win else None

            self.audit.log_event("mouse_move", {
                "coordinates": [cx, cy],
                "duration": duration,
                "window": win_title,
            })

            return InputActionResult(
                success=True,
                action="move_to",
                coordinates=(cx, cy),
                window=win_title,
                message=f"Mouse moved safely to ({cx}, {cy}).",
                verified=True,
            )
        except Exception as e:
            return InputActionResult(success=False, action="move_to", coordinates=(cx, cy), error=str(e), message=f"Failed to move mouse: {e}")

    def click_target(
        self,
        x: int,
        y: int,
        clicks: int = 1,
        button: str = "left",
        target_window: Optional[str] = None,
        target_name: Optional[str] = None,
        verify_expected: Optional[str] = None,
    ) -> InputActionResult:
        """
        Safely moves to and clicks a validated target coordinate.
        """
        if self.is_emergency_stopped():
            return InputActionResult(
                success=False,
                action="click",
                target=target_name,
                coordinates=(x, y),
                error="EMERGENCY_STOP_ACTIVE",
                message="Action rejected: Emergency stop is active.",
            )

        is_dang, dang_msg = self.is_dangerous_action("click", target_name or "")
        if is_dang:
            return InputActionResult(
                success=False,
                action="click",
                target=target_name,
                coordinates=(x, y),
                error="DANGEROUS_ACTION_BLOCKED",
                message=dang_msg,
            )

        rate_err = self._check_rate_limit()
        if rate_err:
            return InputActionResult(success=False, action="click", target=target_name, error="RATE_LIMIT", message=rate_err)

        valid_coord, err_msg, clean_coords = self.validate_coordinates(x, y)
        if not valid_coord:
            return InputActionResult(success=False, action="click", target=target_name, error="INVALID_COORDINATES", message=err_msg)

        cx, cy = clean_coords

        if target_window:
            w_valid, w_err = self.validate_target_window(target_window)
            if not w_valid:
                return InputActionResult(success=False, action="click", target=target_name, coordinates=(cx, cy), error="WINDOW_UNAVAILABLE", message=w_err)

        try:
            # Controlled movement followed by click
            pyautogui.moveTo(cx, cy, duration=0.2)
            time.sleep(0.05)
            pyautogui.click(cx, cy, clicks=clicks, interval=0.1, button=button)

            active_win = self.window_manager.get_active_window()
            win_title = active_win.get("title") if active_win else None

            # Verification stage
            verified = False
            verif_details = {}
            if verify_expected:
                time.sleep(0.5)
                v_res = self.state_verifier.verify(verify_expected, wait_time=0.5)
                verified = v_res.get("success", False)
                verif_details = v_res
            else:
                # Default verification: action executed within bounds on active desktop
                verified = True

            act_name = "double_click" if clicks == 2 else "click"
            self.audit.log_event("mouse_click", {
                "action": act_name,
                "coordinates": [cx, cy],
                "button": button,
                "target": target_name,
                "window": win_title,
                "verified": verified,
            })

            target_display = f" '{target_name}'" if target_name else ""
            msg = f"Clicked{target_display} at coordinates ({cx}, {cy})."
            if verify_expected and not verified:
                msg += " (Note: Expected post-action state could not be verified)."

            return InputActionResult(
                success=True,
                action=act_name,
                target=target_name,
                coordinates=(cx, cy),
                window=win_title,
                message=msg,
                verified=verified,
                verification_details=verif_details,
            )
        except Exception as e:
            return InputActionResult(
                success=False,
                action="click",
                target=target_name,
                coordinates=(cx, cy),
                error=str(e),
                message=f"Click execution failed: {e}",
            )

    def double_click_target(
        self,
        x: int,
        y: int,
        target_window: Optional[str] = None,
        target_name: Optional[str] = None,
        verify_expected: Optional[str] = None,
    ) -> InputActionResult:
        """Safely executes a double-click on validated coordinates."""
        return self.click_target(
            x=x,
            y=y,
            clicks=2,
            button="left",
            target_window=target_window,
            target_name=target_name,
            verify_expected=verify_expected,
        )

    def scroll(self, clicks: int, x: Optional[int] = None, y: Optional[int] = None) -> InputActionResult:
        """Safely scrolls the active window or target coordinates."""
        if self.is_emergency_stopped():
            return InputActionResult(success=False, action="scroll", error="EMERGENCY_STOP_ACTIVE", message="Action rejected: Emergency stop is active.")

        rate_err = self._check_rate_limit()
        if rate_err:
            return InputActionResult(success=False, action="scroll", error="RATE_LIMIT", message=rate_err)

        if x is not None and y is not None:
            valid_coord, err_msg, clean_coords = self.validate_coordinates(x, y)
            if not valid_coord:
                return InputActionResult(success=False, action="scroll", error="INVALID_COORDINATES", message=err_msg)
            cx, cy = clean_coords
            pyautogui.moveTo(cx, cy, duration=0.15)
        else:
            clean_coords = None

        try:
            # Bound scroll magnitude to prevent wild jumps
            bounded_clicks = max(-5000, min(int(clicks), 5000))
            pyautogui.scroll(bounded_clicks)

            direction = "up" if bounded_clicks > 0 else "down"
            self.audit.log_event("mouse_scroll", {
                "clicks": bounded_clicks,
                "direction": direction,
                "coordinates": clean_coords,
            })

            return InputActionResult(
                success=True,
                action="scroll",
                coordinates=clean_coords,
                message=f"Scrolled {direction} ({abs(bounded_clicks)} clicks).",
                verified=True,
            )
        except Exception as e:
            return InputActionResult(success=False, action="scroll", error=str(e), message=f"Scroll failed: {e}")

    # -------------------------------------------------------------------------
    # Keyboard Operations
    # -------------------------------------------------------------------------

    def type_text(
        self,
        text: str,
        interval: float = 0.02,
        is_sensitive: bool = False,
        verify_expected: Optional[str] = None,
    ) -> InputActionResult:
        """
        Safely types text into the active focused window using PyAutoGUI.
        Never executes strings as shell commands. Redacts sensitive strings in logs.
        """
        if self.is_emergency_stopped():
            return InputActionResult(success=False, action="type_text", error="EMERGENCY_STOP_ACTIVE", message="Action rejected: Emergency stop is active.")

        if not text:
            return InputActionResult(success=False, action="type_text", error="EMPTY_TEXT", message="No text provided to type.")

        # Check dangerous actions
        is_dang, dang_msg = self.is_dangerous_action("type_text", text)
        if is_dang:
            return InputActionResult(success=False, action="type_text", error="DANGEROUS_ACTION_BLOCKED", message=dang_msg)

        rate_err = self._check_rate_limit()
        if rate_err:
            return InputActionResult(success=False, action="type_text", error="RATE_LIMIT", message=rate_err)

        # Sensitive text detection
        sensitive = is_sensitive or self.is_sensitive_text(text)

        try:
            # PyAutoGUI typewrite
            safe_interval = max(0.01, min(interval, 0.2))
            pyautogui.write(text, interval=safe_interval)

            active_win = self.window_manager.get_active_window()
            win_title = active_win.get("title") if active_win else None

            # Verification
            verified = False
            verif_details = {}
            if verify_expected:
                time.sleep(0.5)
                v_res = self.state_verifier.verify(verify_expected, wait_time=0.5)
                verified = v_res.get("success", False)
                verif_details = v_res
            else:
                verified = True

            # Audit logging with redaction
            logged_text = "[REDACTED SENSITIVE INPUT]" if sensitive else text
            self.audit.log_event("keyboard_type", {
                "length": len(text),
                "text_logged": logged_text,
                "is_sensitive": sensitive,
                "window": win_title,
                "verified": verified,
            })

            resp_display = "sensitive input (redacted)" if sensitive else f"'{text}'"
            msg = f"Typed {resp_display} into active window."
            if verify_expected and not verified:
                msg += " (Note: Typed content could not be visually confirmed via OCR)."

            return InputActionResult(
                success=True,
                action="type_text",
                target=resp_display,
                window=win_title,
                message=msg,
                verified=verified,
                verification_details=verif_details,
            )
        except Exception as e:
            return InputActionResult(success=False, action="type_text", error=str(e), message=f"Failed to type text: {e}")

    def press_key(self, key: str, verify_expected: Optional[str] = None) -> InputActionResult:
        """
        Presses an explicitly approved keyboard key. Rejects unknown keys.
        """
        if self.is_emergency_stopped():
            return InputActionResult(success=False, action="press_key", error="EMERGENCY_STOP_ACTIVE", message="Action rejected: Emergency stop is active.")

        norm_key = key.lower().strip()
        norm_key = KEY_NORMALIZATION.get(norm_key, norm_key)

        # Whitelist check
        if norm_key not in APPROVED_KEYS:
            return InputActionResult(
                success=False,
                action="press_key",
                target=key,
                error="UNAPPROVED_KEY",
                message=f"Key '{key}' is not in the approved safety whitelist.",
            )

        rate_err = self._check_rate_limit()
        if rate_err:
            return InputActionResult(success=False, action="press_key", target=norm_key, error="RATE_LIMIT", message=rate_err)

        try:
            pyautogui.press(norm_key)

            active_win = self.window_manager.get_active_window()
            win_title = active_win.get("title") if active_win else None

            verified = True
            verif_details = {}
            if verify_expected:
                time.sleep(0.5)
                v_res = self.state_verifier.verify(verify_expected, wait_time=0.5)
                verified = v_res.get("success", False)
                verif_details = v_res

            self.audit.log_event("key_press", {
                "key": norm_key,
                "window": win_title,
                "verified": verified,
            })

            return InputActionResult(
                success=True,
                action="press_key",
                target=norm_key,
                window=win_title,
                message=f"Pressed key '{norm_key}'.",
                verified=verified,
                verification_details=verif_details,
            )
        except Exception as e:
            return InputActionResult(success=False, action="press_key", target=norm_key, error=str(e), message=f"Failed to press key: {e}")

    def hotkey(self, *keys: str, verify_expected: Optional[str] = None) -> InputActionResult:
        """
        Executes an approved keyboard shortcut combo (e.g. Ctrl+C, Alt+Tab).
        """
        if self.is_emergency_stopped():
            return InputActionResult(success=False, action="hotkey", error="EMERGENCY_STOP_ACTIVE", message="Action rejected: Emergency stop is active.")

        if not keys:
            return InputActionResult(success=False, action="hotkey", error="EMPTY_HOTKEY", message="No keys provided for hotkey.")

        norm_keys = tuple(KEY_NORMALIZATION.get(k.lower().strip(), k.lower().strip()) for k in keys)

        # Check against approved hotkey set
        if norm_keys not in APPROVED_HOTKEYS:
            # Check individual keys
            for k in norm_keys:
                if k not in APPROVED_KEYS:
                    return InputActionResult(
                        success=False,
                        action="hotkey",
                        target="+".join(keys),
                        error="UNAPPROVED_HOTKEY",
                        message=f"Hotkey combination '{'+'.join(keys)}' contains unapproved key '{k}'.",
                    )

        rate_err = self._check_rate_limit()
        if rate_err:
            return InputActionResult(success=False, action="hotkey", target="+".join(norm_keys), error="RATE_LIMIT", message=rate_err)

        try:
            pyautogui.hotkey(*norm_keys)

            active_win = self.window_manager.get_active_window()
            win_title = active_win.get("title") if active_win else None

            verified = True
            verif_details = {}
            if verify_expected:
                time.sleep(0.5)
                v_res = self.state_verifier.verify(verify_expected, wait_time=0.5)
                verified = v_res.get("success", False)
                verif_details = v_res

            combo_name = "+".join(norm_keys)
            self.audit.log_event("hotkey_press", {
                "combo": combo_name,
                "window": win_title,
                "verified": verified,
            })

            return InputActionResult(
                success=True,
                action="hotkey",
                target=combo_name,
                window=win_title,
                message=f"Executed hotkey '{combo_name}'.",
                verified=verified,
                verification_details=verif_details,
            )
        except Exception as e:
            return InputActionResult(success=False, action="hotkey", target="+".join(keys), error=str(e), message=f"Failed to execute hotkey: {e}")


# Export alias for architectural consistency
ControlledInputService = InputController
