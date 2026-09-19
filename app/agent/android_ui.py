"""
NR-AI Android UI Perception & Device Intelligence Foundation (Step 6 Phase 5).

Provides safe, bounded, deterministic perception and interaction for authorized
Android devices:
1. Device state & foreground app inspection
2. Safe screen capture with memory-bounded handling
3. Bounded UI hierarchy distillation via uiautomator dump
4. Deterministic sequential target IDs (android.target.001)
5. 15-second target TTL & stale-target rejection
6. Safe coordinate resolution strictly from verified targets
7. Allowlisted device operations (tap, back, home, press_key, swipe, scroll)
8. Advisory model prompt bounding & JSON action intent parsing
9. Comprehensive audit logging with sensitive data redaction
"""

from dataclasses import asdict, dataclass, field
import json
import logging
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union
import uuid
import xml.etree.ElementTree as ET

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
from app.agent.android_tools import SafeAdbClient
from app.memory.audit_logger import AuditLogger

logger = logging.getLogger("NRAI.AndroidUI")


# -----------------------------------------------------------------------------
# Data Models: Targets and Snapshots
# -----------------------------------------------------------------------------

@dataclass
class AndroidTarget:
    """Deterministic, bounded UI target extracted from device accessibility tree."""
    target_id: str
    semantic_type: str
    text: str
    content_desc: str
    resource_id: str
    class_name: str
    package_name: str
    bounds: Tuple[int, int, int, int]  # (left, top, right, bottom)
    center: Tuple[int, int]            # (cx, cy)
    clickable: bool
    enabled: bool
    selected: bool
    focused: bool
    device_serial: str
    screen_width: int
    screen_height: int
    timestamp: float = field(default_factory=time.time)
    ttl_seconds: float = DEFAULT_TARGET_TTL_SECONDS

    def is_stale(self, ttl: Optional[float] = None) -> bool:
        """Returns True if the target has exceeded its TTL."""
        limit = ttl if ttl is not None else self.ttl_seconds
        return (time.time() - self.timestamp) > limit

    def to_dict(self) -> Dict[str, Any]:
        """Returns a sanitized dictionary representation of the target."""
        return {
            "target_id": self.target_id,
            "semantic_type": self.semantic_type,
            "text": self.text,
            "content_desc": self.content_desc,
            "resource_id": self.resource_id,
            "class_name": self.class_name,
            "bounds": list(self.bounds),
            "center": list(self.center),
            "clickable": self.clickable,
            "enabled": self.enabled,
            "selected": self.selected,
            "device_serial": self.device_serial,
            "screen_width": self.screen_width,
            "screen_height": self.screen_height,
            "timestamp": self.timestamp,
            "age_s": round(time.time() - self.timestamp, 2),
            "is_stale": self.is_stale(),
        }


@dataclass
class AndroidUISnapshot:
    """Immutable state snapshot capturing UI state and actionable targets at a moment in time."""
    snapshot_id: str
    device_serial: str
    foreground_app: Dict[str, str] = field(default_factory=dict)
    screen_dimensions: Tuple[int, int] = (1080, 2400)
    timestamp: float = field(default_factory=time.time)
    targets: List[AndroidTarget] = field(default_factory=list)
    target_map: Dict[str, AndroidTarget] = field(default_factory=dict)
    visible_text_items: List[str] = field(default_factory=list)
    raw_xml_length: int = 0

    def __post_init__(self):
        if not self.target_map and self.targets:
            self.target_map = {t.target_id: t for t in self.targets}

    def get_target(self, target_id: str) -> Optional[AndroidTarget]:
        return self.target_map.get(target_id)

    def find_targets_by_text(self, query: str, exact: bool = False) -> List[AndroidTarget]:
        q = query.strip().lower()
        if not q:
            return []
        matches = []
        for t in self.targets:
            t_text = (t.text or "").lower()
            t_desc = (t.content_desc or "").lower()
            if exact:
                if q == t_text or q == t_desc:
                    matches.append(t)
            else:
                if q in t_text or q in t_desc:
                    matches.append(t)
        return matches

    def find_targets_by_resource_id(self, res_id: str) -> List[AndroidTarget]:
        q = res_id.strip().lower()
        if not q:
            return []
        return [t for t in self.targets if q in (t.resource_id or "").lower()]

    def get_bounded_summary(self, max_targets: int = 50) -> Dict[str, Any]:
        """Provides a safe, bounded summary of screen state for advisory models."""
        slice_targets = self.targets[:max_targets]
        return {
            "snapshot_id": self.snapshot_id,
            "device_serial": self.device_serial,
            "foreground_app": self.foreground_app,
            "screen_dimensions": list(self.screen_dimensions),
            "timestamp": self.timestamp,
            "total_targets": len(self.targets),
            "visible_texts": self.visible_text_items[:50],
            "targets": [
                {
                    "target_id": t.target_id,
                    "type": t.semantic_type,
                    "text": t.text,
                    "content_desc": t.content_desc,
                    "clickable": t.clickable,
                    "enabled": t.enabled,
                }
                for t in slice_targets
            ],
        }


# -----------------------------------------------------------------------------
# UI Distiller: Hierarchy XML Parser
# -----------------------------------------------------------------------------

class AndroidUIDistiller:
    """
    Parses uiautomator dump XML into structured, sanitized AndroidTarget objects.
    Enforces deterministic sequential target identifiers and filters noisy layout containers.
    """

    @staticmethod
    def _map_semantic_type(class_name: str) -> str:
        cn = class_name.lower()
        if "button" in cn:
            return "button"
        if "edittext" in cn:
            return "edit_text"
        if "checkbox" in cn or "radiobutton" in cn or "switch" in cn or "toggle" in cn:
            return "toggle"
        if "textview" in cn:
            return "text_view"
        if "imageview" in cn:
            return "image"
        if "viewgroup" in cn or "layout" in cn:
            return "container"
        return "element"

    @classmethod
    def parse_hierarchy_xml(
        cls,
        xml_content: str,
        device_serial: str,
        screen_width: int,
        screen_height: int,
        timestamp: Optional[float] = None,
        foreground_app: Optional[Dict[str, str]] = None,
    ) -> AndroidUISnapshot:
        """Parses UI hierarchy XML and returns a clean AndroidUISnapshot."""
        snap_id = f"snap_{uuid.uuid4().hex[:8]}"
        t_now = timestamp if timestamp is not None else time.time()
        fg = foreground_app or {"package": "unknown", "activity": "unknown"}

        if not xml_content or not xml_content.strip():
            return AndroidUISnapshot(
                snapshot_id=snap_id,
                device_serial=device_serial,
                foreground_app=fg,
                screen_dimensions=(screen_width, screen_height),
                timestamp=t_now,
                targets=[],
                visible_text_items=[],
                raw_xml_length=0,
            )

        try:
            root = ET.fromstring(xml_content.strip())
        except Exception as e:
            logger.warning(f"Failed to parse UI hierarchy XML: {e}")
            raise AndroidSafetyError(
                AndroidErrorCode.UI_EXTRACTION_FAILED,
                f"Malformed UI hierarchy XML: {e}",
            )

        targets: List[AndroidTarget] = []
        visible_texts: List[str] = []
        target_idx = 1

        bounds_regex = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")

        for elem in root.iter():
            if elem.tag != "node":
                continue

            text = elem.attrib.get("text", "").strip()
            content_desc = elem.attrib.get("content-desc", "").strip()
            resource_id = elem.attrib.get("resource-id", "").strip()
            class_name = elem.attrib.get("class", "").strip()
            package_name = elem.attrib.get("package", "").strip()
            clickable = elem.attrib.get("clickable", "false").lower() == "true"
            enabled = elem.attrib.get("enabled", "true").lower() == "true"
            selected = elem.attrib.get("selected", "false").lower() == "true"
            focused = elem.attrib.get("focused", "false").lower() == "true"
            bounds_str = elem.attrib.get("bounds", "")

            m = bounds_regex.match(bounds_str)
            if not m:
                continue

            x1, y1, x2, y2 = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
            w = x2 - x1
            h = y2 - y1

            # Skip zero-size or off-screen elements
            if w <= 0 or h <= 0 or x1 >= screen_width or y1 >= screen_height:
                continue

            # Record visible text
            if text and text not in visible_texts:
                visible_texts.append(text)
            if content_desc and content_desc not in visible_texts:
                visible_texts.append(content_desc)

            # Filter out non-interactive layout containers with no text
            sem_type = cls._map_semantic_type(class_name)
            is_informative = bool(text or content_desc or resource_id)
            if not clickable and not is_informative and sem_type == "container":
                continue

            target_id = f"android.target.{target_idx:03d}"
            target_idx += 1

            cx = (x1 + x2) // 2
            cy = (y1 + y2) // 2

            target = AndroidTarget(
                target_id=target_id,
                semantic_type=sem_type,
                text=text,
                content_desc=content_desc,
                resource_id=resource_id,
                class_name=class_name,
                package_name=package_name,
                bounds=(x1, y1, x2, y2),
                center=(cx, cy),
                clickable=clickable,
                enabled=enabled,
                selected=selected,
                focused=focused,
                device_serial=device_serial,
                screen_width=screen_width,
                screen_height=screen_height,
                timestamp=t_now,
            )
            targets.append(target)

        return AndroidUISnapshot(
            snapshot_id=snap_id,
            device_serial=device_serial,
            foreground_app=fg,
            screen_dimensions=(screen_width, screen_height),
            timestamp=t_now,
            targets=targets,
            visible_text_items=visible_texts,
            raw_xml_length=len(xml_content),
        )


# -----------------------------------------------------------------------------
# Target Registry: In-Memory TTL Cache
# -----------------------------------------------------------------------------

class AndroidTargetRegistry:
    """Manages cached AndroidUISnapshots and enforces 15-second TTL on targets."""

    def __init__(self, safety_gate: Optional[AndroidSafetyGate] = None):
        self.safety = safety_gate or AndroidSafetyGate()
        self._snapshots: Dict[str, AndroidUISnapshot] = {}

    def store_snapshot(self, snapshot: AndroidUISnapshot) -> None:
        self._snapshots[snapshot.device_serial] = snapshot

    def get_snapshot(self, serial: str) -> Optional[AndroidUISnapshot]:
        return self._snapshots.get(serial)

    def get_target(self, serial: str, target_id: str) -> AndroidTarget:
        """
        Retrieves a target by ID, strictly verifying its existence and freshness.
        Raises TARGET_NOT_FOUND or STALE_TARGET on failure.
        """
        self.safety.validate_device_serial(serial)
        self.safety.check_emergency_stop()

        snapshot = self._snapshots.get(serial)
        if not snapshot:
            raise AndroidSafetyError(
                AndroidErrorCode.TARGET_NOT_FOUND,
                f"No active UI snapshot available for device '{serial}'. Please inspect UI first.",
            )

        target = snapshot.get_target(target_id)
        if not target:
            raise AndroidSafetyError(
                AndroidErrorCode.TARGET_NOT_FOUND,
                f"Target '{target_id}' not found on device '{serial}'.",
            )

        # Enforce 15-second target TTL
        self.safety.validate_target_freshness(target.timestamp, ttl_seconds=target.ttl_seconds)

        return target

    def clear(self, serial: Optional[str] = None) -> None:
        if serial:
            self._snapshots.pop(serial, None)
        else:
            self._snapshots.clear()


# -----------------------------------------------------------------------------
# UI Controller: High-Level Safe Orchestrator
# -----------------------------------------------------------------------------

class AndroidUIController:
    """
    Coordinates safe device perception, bounded interaction, coordinate resolution,
    and ground-truth state verification.
    """

    def __init__(
        self,
        safety_gate: Optional[AndroidSafetyGate] = None,
        adb_client: Optional[SafeAdbClient] = None,
        target_registry: Optional[AndroidTargetRegistry] = None,
        audit_logger: Optional[AuditLogger] = None,
    ):
        self.safety = safety_gate or AndroidSafetyGate()
        self.adb = adb_client or SafeAdbClient()
        self.registry = target_registry or AndroidTargetRegistry(safety_gate=self.safety)
        self.audit = audit_logger or AuditLogger()

    def get_device_state(self, serial: str) -> str:
        self.safety.validate_device_serial(serial)
        self.safety.check_emergency_stop()
        return self.adb.get_device_state(serial)

    def get_foreground_app(self, serial: str) -> Dict[str, str]:
        self.safety.validate_device_serial(serial)
        self.safety.check_emergency_stop()
        return self.adb.get_foreground_app(serial)

    def get_screen_size(self, serial: str) -> Tuple[int, int]:
        self.safety.validate_device_serial(serial)
        self.safety.check_emergency_stop()
        return self.adb.get_screen_size(serial)

    def capture_screen(self, serial: str, dest_path: Optional[Path] = None) -> bytes:
        """Captures bounded screen bytes from authorized device."""
        self.safety.validate_device_serial(serial)
        self.safety.check_emergency_stop()
        raw = self.adb.capture_screen(serial, dest_path=dest_path)
        if dest_path and raw:
            p = Path(dest_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(raw)
        self.audit.log_event(
            event_type="android.capture_screen",
            status="SUCCESS",
            details={"serial": serial, "bytes_captured": len(raw), "dest_path": str(dest_path) if dest_path else None},
        )
        return raw

    def inspect_ui(self, serial: str) -> AndroidUISnapshot:
        """
        Inspects the authorized device screen, dumps the UI hierarchy,
        distills bounded targets, and caches the snapshot in the registry.
        """
        self.safety.validate_device_serial(serial)
        self.safety.check_emergency_stop()

        fg = self.adb.get_foreground_app(serial)
        width, height = self.adb.get_screen_size(serial)
        xml = self.adb.dump_ui_hierarchy(serial)

        snapshot = AndroidUIDistiller.parse_hierarchy_xml(
            xml_content=xml,
            device_serial=serial,
            screen_width=width,
            screen_height=height,
            foreground_app=fg,
        )
        self.registry.store_snapshot(snapshot)

        self.audit.log_event(
            event_type="android.inspect_ui",
            status="SUCCESS",
            details={
                "serial": serial,
                "snapshot_id": snapshot.snapshot_id,
                "targets_count": len(snapshot.targets),
                "foreground_app": fg,
            },
        )
        return snapshot

    def capture_and_inspect(self, serial: str) -> Dict[str, Any]:
        """Convenience method returning UI inspection dictionary."""
        try:
            snap = self.inspect_ui(serial)
            d = snap.to_dict()
            d["success"] = True
            d["target_count"] = len(snap.targets)
            return d
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "target_count": 0,
                "targets": [],
            }


    def find_ui_text(self, serial: str, query: str) -> List[Dict[str, Any]]:
        self.safety.validate_device_serial(serial)
        self.safety.check_emergency_stop()
        snap = self.registry.get_snapshot(serial) or self.inspect_ui(serial)
        matches = snap.find_targets_by_text(query)
        return [m.to_dict() for m in matches]

    def find_ui_element(self, serial: str, query: str) -> List[Dict[str, Any]]:
        self.safety.validate_device_serial(serial)
        self.safety.check_emergency_stop()
        snap = self.registry.get_snapshot(serial) or self.inspect_ui(serial)
        by_res = snap.find_targets_by_resource_id(query)
        if by_res:
            return [m.to_dict() for m in by_res]
        by_text = snap.find_targets_by_text(query)
        return [m.to_dict() for m in by_text]

    def tap_target(self, serial: str, target_id: str) -> Dict[str, Any]:
        """
        Taps an actionable UI target by resolving coordinates deterministically.
        Validates target freshness, screen boundaries, and dimension consistency.
        """
        self.safety.validate_device_serial(serial)
        self.safety.check_emergency_stop()

        # Resolve target and check freshness (15s TTL)
        target = self.registry.get_target(serial, target_id)

        # Validate screen dimension consistency
        curr_w, curr_h = self.adb.get_screen_size(serial)
        self.safety.validate_screen_dimensions(target.screen_width, target.screen_height, curr_w, curr_h)

        # Validate target coordinates within screen bounds
        cx, cy = self.safety.validate_target_coordinates(target.center[0], target.center[1], curr_w, curr_h)

        # Execute deterministic tap
        success = self.adb.tap(serial, cx, cy)

        # Allow UI to react
        time.sleep(0.3)

        # Capture post-action state
        new_fg = self.adb.get_foreground_app(serial)

        res = {
            "success": success,
            "action": "tap_target",
            "target_id": target_id,
            "coordinates": [cx, cy],
            "serial": serial,
            "foreground_app": new_fg,
        }

        self.audit.log_event(
            event_type="android.tap_target",
            status="SUCCESS" if success else "FAILED",
            details=res,
        )
        return res

    def back(self, serial: str) -> Dict[str, Any]:
        self.safety.validate_device_serial(serial)
        self.safety.check_emergency_stop()
        success = self.adb.back(serial)
        time.sleep(0.3)
        fg = self.adb.get_foreground_app(serial)
        res = {"success": success, "action": "back", "serial": serial, "foreground_app": fg}
        self.audit.log_event(event_type="android.back", status="SUCCESS" if success else "FAILED", details=res)
        return res

    def home(self, serial: str) -> Dict[str, Any]:
        self.safety.validate_device_serial(serial)
        self.safety.check_emergency_stop()
        success = self.adb.home(serial)
        time.sleep(0.3)
        fg = self.adb.get_foreground_app(serial)
        res = {"success": success, "action": "home", "serial": serial, "foreground_app": fg}
        self.audit.log_event(event_type="android.home", status="SUCCESS" if success else "FAILED", details=res)
        return res

    def press_key(self, serial: str, keycode: int) -> Dict[str, Any]:
        self.safety.validate_device_serial(serial)
        self.safety.check_emergency_stop()
        valid_key = self.safety.validate_keycode(keycode)
        success = self.adb.press_key(serial, valid_key)
        res = {"success": success, "action": "press_key", "keycode": valid_key, "serial": serial}
        self.audit.log_event(event_type="android.press_key", status="SUCCESS" if success else "FAILED", details=res)
        return res

    def swipe(self, serial: str, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> Dict[str, Any]:
        self.safety.validate_device_serial(serial)
        self.safety.check_emergency_stop()
        w, h = self.adb.get_screen_size(serial)
        vx1, vy1 = self.safety.validate_target_coordinates(x1, y1, w, h)
        vx2, vy2 = self.safety.validate_target_coordinates(x2, y2, w, h)
        success = self.adb.swipe(serial, vx1, vy1, vx2, vy2, duration_ms=duration_ms)
        res = {"success": success, "action": "swipe", "from": [vx1, vy1], "to": [vx2, vy2], "serial": serial}
        self.audit.log_event(event_type="android.swipe", status="SUCCESS" if success else "FAILED", details=res)
        return res

    def scroll(self, serial: str, direction: str = "down") -> Dict[str, Any]:
        self.safety.validate_device_serial(serial)
        self.safety.check_emergency_stop()
        d_lower = direction.lower()
        if d_lower not in ("down", "up", "left", "right"):
            raise AndroidSafetyError(
                AndroidErrorCode.ACTION_NOT_ALLOWED,
                f"Invalid scroll direction '{direction}'. Must be up, down, left, or right.",
            )
        success = self.adb.scroll(serial, direction=d_lower)
        res = {"success": success, "action": "scroll", "direction": d_lower, "serial": serial}
        self.audit.log_event(event_type="android.scroll", status="SUCCESS" if success else "FAILED", details=res)
        return res

    def verify_ui_state(
        self,
        serial: str,
        expected_package: Optional[str] = None,
        expected_text: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Verifies ground-truth device state against expectations."""
        self.safety.validate_device_serial(serial)
        self.safety.check_emergency_stop()

        fg = self.adb.get_foreground_app(serial)
        snap = self.inspect_ui(serial)

        pkg_ok = (expected_package is None or fg.get("package") == expected_package)
        text_ok = (expected_text is None or bool(snap.find_targets_by_text(expected_text)))

        overall = pkg_ok and text_ok
        res = {
            "success": overall,
            "serial": serial,
            "foreground_app": fg,
            "package_verified": pkg_ok,
            "text_verified": text_ok,
            "targets_count": len(snap.targets),
        }
        self.audit.log_event(
            event_type="android.verify_ui_state",
            status="SUCCESS" if overall else "FAILED",
            details=res,
        )
        return res

    def execute_action(
        self,
        serial: str,
        action: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Central dispatch entry point for allowlisted UI operations.
        Passes through device validation, emergency stop, and high-risk action guards.
        """
        self.safety.validate_device_serial(serial)
        self.safety.check_emergency_stop()
        self.safety.validate_safe_ui_action(action, params)

        p = params or {}
        if action == "get_device_state":
            state = self.get_device_state(serial)
            return {"success": True, "device_state": state, "serial": serial}
        elif action == "get_foreground_app":
            fg = self.get_foreground_app(serial)
            return {"success": True, "foreground_app": fg, "serial": serial}
        elif action == "get_screen_size":
            w, h = self.get_screen_size(serial)
            return {"success": True, "screen_size": [w, h], "serial": serial}
        elif action == "capture_screen":
            dest = Path(p["dest_path"]) if "dest_path" in p else None
            raw = self.capture_screen(serial, dest_path=dest)
            return {"success": True, "bytes_length": len(raw), "serial": serial}
        elif action == "inspect_ui":
            snap = self.inspect_ui(serial)
            return {"success": True, "snapshot": snap.get_bounded_summary(), "serial": serial}
        elif action == "find_ui_text":
            text_query = p.get("query", "")
            matches = self.find_ui_text(serial, text_query)
            return {"success": True, "matches": matches, "serial": serial}
        elif action == "find_ui_element":
            elem_query = p.get("query", "")
            matches = self.find_ui_element(serial, elem_query)
            return {"success": True, "matches": matches, "serial": serial}
        elif action == "tap_target":
            tid = p.get("target_id", "")
            if not tid:
                raise AndroidSafetyError(
                    AndroidErrorCode.MALFORMED_ACTION_SCHEMA,
                    "Missing 'target_id' for tap_target action.",
                )
            return self.tap_target(serial, tid)
        elif action == "back":
            return self.back(serial)
        elif action == "home":
            return self.home(serial)
        elif action == "press_key":
            code = int(p.get("keycode", 0))
            return self.press_key(serial, code)
        elif action == "swipe":
            x1 = int(p.get("x1", 0))
            y1 = int(p.get("y1", 0))
            x2 = int(p.get("x2", 0))
            y2 = int(p.get("y2", 0))
            dur = int(p.get("duration_ms", 300))
            return self.swipe(serial, x1, y1, x2, y2, duration_ms=dur)
        elif action == "scroll":
            direction = p.get("direction", "down")
            return self.scroll(serial, direction=direction)
        elif action == "verify_ui_state":
            exp_pkg = p.get("expected_package")
            exp_txt = p.get("expected_text")
            return self.verify_ui_state(serial, expected_package=exp_pkg, expected_text=exp_txt)
        else:
            raise AndroidSafetyError(
                AndroidErrorCode.ACTION_NOT_ALLOWED,
                f"Unknown action '{action}'.",
            )


# -----------------------------------------------------------------------------
# Model Advisory Bounding & Schema Parsing
# -----------------------------------------------------------------------------

def build_model_ui_prompt(snapshot: AndroidUISnapshot, user_goal: str) -> Tuple[str, str]:
    """
    Constructs a strictly bounded, advisory prompt for an LLM reasoning about Android UI.
    Never includes passwords, raw XML dumps, or credentials.
    """
    sys_prompt = (
        "You are a STRICTLY ADVISORY assistant for Android UI analysis.\n"
        "You do NOT have execution authority. You cannot access ADB or the device directly.\n"
        "Given the current visible UI targets, select the best safe action to achieve the goal.\n"
        "Respond ONLY with valid JSON matching this schema:\n"
        "{\n"
        '  "action": "tap_target" | "back" | "home" | "scroll",\n'
        '  "target_id": "android.target.XXX",  // required if action is tap_target\n'
        '  "direction": "down" | "up",         // required if action is scroll\n'
        '  "reason": "Clear justification"\n'
        "}\n"
    )

    summary = snapshot.get_bounded_summary(max_targets=40)
    user_prompt = (
        f"Goal: {user_goal}\n"
        f"Foreground App: {summary['foreground_app'].get('package')}/{summary['foreground_app'].get('activity')}\n"
        f"Screen Size: {summary['screen_dimensions'][0]}x{summary['screen_dimensions'][1]}\n"
        f"Visible Elements:\n"
        f"{json.dumps(summary['targets'], indent=2)}\n\n"
        "Propose the next single safe action."
    )
    return sys_prompt, user_prompt


def parse_model_ui_action(raw_text: str) -> Dict[str, Any]:
    """
    Strictly validates and parses JSON action proposal from advisory model.
    Rejects malformed JSON, missing fields, or unauthorized actions.
    """
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        data = json.loads(cleaned)
    except Exception as e:
        raise AndroidSafetyError(
            AndroidErrorCode.MALFORMED_ACTION_SCHEMA,
            f"Advisory model output is not valid JSON: {e}",
        )

    if not isinstance(data, dict):
        raise AndroidSafetyError(
            AndroidErrorCode.MALFORMED_ACTION_SCHEMA,
            "Model action proposal must be a JSON object.",
        )

    action = data.get("action")
    if not action or not isinstance(action, str):
        raise AndroidSafetyError(
            AndroidErrorCode.MALFORMED_ACTION_SCHEMA,
            "Model action proposal missing required 'action' field.",
        )

    if action not in ALLOWED_ANDROID_UI_OPERATIONS:
        raise AndroidSafetyError(
            AndroidErrorCode.ACTION_NOT_ALLOWED,
            f"Action '{action}' proposed by model is not in the allowlist.",
        )

    # If action is tap_target, target_id must match format
    if action == "tap_target":
        tid = data.get("target_id")
        if not tid or not isinstance(tid, str) or not tid.startswith("android.target."):
            raise AndroidSafetyError(
                AndroidErrorCode.MALFORMED_ACTION_SCHEMA,
                f"Invalid or missing target_id '{tid}' in proposal.",
            )

    return data

# Phase 3 compatibility alias
AndroidUIIntelligence = AndroidUIController
