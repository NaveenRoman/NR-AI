r"""
NR-AI Visual Verification Engine & Safe Screenshot Manager (Droid Phase 2).

Provides deterministic visual assertion evaluation and bounded screenshot management:
1. Structured Visual & UI Assertions:
   - ELEMENT_VISIBLE: Element is present on screen with non-zero dimensions.
   - ELEMENT_ENABLED: Element is enabled for user interaction.
   - ELEMENT_CLICKABLE: Element responds to click / tap events.
   - ELEMENT_TEXT_EQUALS: Element text exactly matches expected value.
   - ELEMENT_TEXT_CONTAINS: Element text contains expected substring.
   - SCREEN_NAVIGATED: Foreground activity / package matches expected screen.
   - SEMANTIC_NODE_PRESENT: Correlated Compose semantic node exists.
   - SCREEN_NOT_EMPTY: Accessibility tree contains active interactive elements.
2. Comprehensive Verification Status:
   - PASS: All mandatory assertions passed.
   - FAIL: One or more mandatory assertions failed.
   - PARTIAL: Mandatory passed, but optional assertions failed.
   - NOT_VERIFIED: Verification could not be executed (e.g. device offline).
3. Safe Screenshot Manager:
   - Uses SafeAdbClient.capture_screen() with shell=False.
   - Strictly bounded storage in C:\NR-AI\scratch\screenshots\ (max 30).
   - Automatic FIFO rotation.
   - Sensitive credential and secret redaction from metadata and filenames.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import json
import logging
import os
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from app.agent.android_safety import (
    AUTHORIZED_PACKAGE_NAME,
    AndroidErrorCode,
    AndroidSafetyError,
    AndroidSafetyGate,
)
from app.agent.android_tools import SafeAdbClient
from app.agent.android_ui import AndroidTarget, AndroidUISnapshot
from app.agent.android_runtime_semantics import CorrelatedSemanticsNode, CorrelatedSemanticsReport, CorrelationEvidence
from app.memory.audit_logger import AuditLogger

logger = logging.getLogger("NRAI.VisualVerifier")

DEFAULT_SCREENSHOTS_DIR = Path(r"C:\NR-AI\scratch\screenshots")
MAX_STORED_SCREENSHOTS = 30


# -----------------------------------------------------------------------------
# Data Models
# -----------------------------------------------------------------------------

class AssertionType(str, Enum):
    ELEMENT_VISIBLE = "ELEMENT_VISIBLE"
    ELEMENT_ENABLED = "ELEMENT_ENABLED"
    ELEMENT_CLICKABLE = "ELEMENT_CLICKABLE"
    ELEMENT_TEXT_EQUALS = "ELEMENT_TEXT_EQUALS"
    ELEMENT_TEXT_CONTAINS = "ELEMENT_TEXT_CONTAINS"
    SCREEN_NAVIGATED = "SCREEN_NAVIGATED"
    SEMANTIC_NODE_PRESENT = "SEMANTIC_NODE_PRESENT"
    SCREEN_NOT_EMPTY = "SCREEN_NOT_EMPTY"


class VisualVerificationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    PARTIAL = "PARTIAL"
    NOT_VERIFIED = "NOT_VERIFIED"


@dataclass
class VisualAssertion:
    """Specification of an expected UI or visual state."""
    assertion_type: AssertionType
    query: str
    expected_value: Optional[Any] = None
    optional: bool = False
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "assertion_type": self.assertion_type.value if isinstance(self.assertion_type, AssertionType) else str(self.assertion_type),
            "query": self.query,
            "expected_value": self.expected_value,
            "optional": self.optional,
            "description": self.description,
        }


@dataclass
class AssertionResult:
    """Outcome of evaluating a single visual assertion."""
    assertion: VisualAssertion
    passed: bool
    actual_value: Optional[Any] = None
    matched_target: Optional[Dict[str, Any]] = None
    matched_node: Optional[Dict[str, Any]] = None
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "assertion": self.assertion.to_dict(),
            "passed": self.passed,
            "actual_value": self.actual_value,
            "matched_target": self.matched_target,
            "matched_node": self.matched_node,
            "message": self.message,
        }


@dataclass
class VisualVerificationReport:
    """Comprehensive outcome report of visual verification."""
    status: VisualVerificationStatus
    total_assertions: int
    passed_count: int
    failed_count: int
    optional_failed_count: int
    results: List[AssertionResult]
    screenshot_path: Optional[str] = None
    device_serial: Optional[str] = None
    duration_seconds: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value if isinstance(self.status, VisualVerificationStatus) else str(self.status),
            "total_assertions": self.total_assertions,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "optional_failed_count": self.optional_failed_count,
            "results": [r.to_dict() for r in self.results],
            "screenshot_path": self.screenshot_path,
            "device_serial": self.device_serial,
            "duration_seconds": round(self.duration_seconds, 3),
            "timestamp": self.timestamp,
        }


# -----------------------------------------------------------------------------
# Safe Screenshot Manager
# -----------------------------------------------------------------------------

class SafeScreenshotManager:
    """
    Manages safe, memory-bounded, credential-redacted screenshot capture
    and disk persistence with automatic FIFO rotation.
    """

    def __init__(
        self,
        adb_client: Optional[SafeAdbClient] = None,
        safety_gate: Optional[AndroidSafetyGate] = None,
        root_dir: Path = DEFAULT_SCREENSHOTS_DIR,
        max_items: int = MAX_STORED_SCREENSHOTS,
    ):
        self.adb = adb_client or SafeAdbClient()
        self.safety = safety_gate or AndroidSafetyGate()
        self.root_dir = Path(root_dir).resolve()
        self.max_items = max_items
        self._ensure_storage()

    def _ensure_storage(self) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def capture_screenshot(
        self,
        serial: str,
        label: str = "capture",
    ) -> Path:
        """
        Captures screenshot from authorized device and writes to bounded directory.
        Redacts any sensitive characters from label.
        """
        self.safety.check_emergency_stop()
        validated_serial = self.safety.validate_device_serial(serial)
        self._ensure_storage()

        # Sanitize and redact sensitive tokens from label
        clean_label = re.sub(r'(?i)(token|secret|password|passwd|api[_-]?key|ghp_[A-Za-z0-9_]*)', 'redacted', label)
        clean_label = re.sub(r'[^a-zA-Z0-9_-]', '_', clean_label)
        from app.agent.android_code_repair import redact_sensitive_content
        clean_label = redact_sensitive_content(clean_label)[:30]

        ts = time.strftime("%Y%m%d_%H%M%S")
        filename = f"screencap_{ts}_{clean_label}.png"
        dest_path = self.root_dir / filename

        # Capture via SafeAdbClient
        self.adb.capture_screen(validated_serial, dest_path=dest_path)
        self._prune_old_screenshots()
        return dest_path

    def list_screenshots(self) -> List[Dict[str, Any]]:
        """Lists captured screenshots sorted newest first."""
        self._ensure_storage()
        files = sorted(self.root_dir.glob("screencap_*.png"), key=os.path.getmtime, reverse=True)
        items = []
        for f in files:
            items.append({
                "filename": f.name,
                "path": str(f),
                "size_bytes": f.stat().st_size,
                "timestamp": f.stat().st_mtime,
            })
        return items

    def _prune_old_screenshots(self) -> None:
        """Enforces maximum stored screenshot count."""
        files = sorted(self.root_dir.glob("screencap_*.png"), key=os.path.getmtime)
        while len(files) > self.max_items:
            oldest = files.pop(0)
            try:
                if oldest.is_file():
                    oldest.unlink()
            except Exception as e:
                logger.warning(f"Failed to prune old screenshot '{oldest}': {e}")


# -----------------------------------------------------------------------------
# Visual Verification Engine
# -----------------------------------------------------------------------------

class VisualVerificationEngine:
    """
    Evaluates visual and runtime assertions against UI snapshots and correlated semantics.
    """

    def __init__(
        self,
        screenshot_manager: Optional[SafeScreenshotManager] = None,
        audit_logger: Optional[AuditLogger] = None,
        safety_gate: Optional[AndroidSafetyGate] = None,
    ):
        self.screenshots = screenshot_manager or SafeScreenshotManager()
        self.audit = audit_logger or AuditLogger()
        self.safety = safety_gate or AndroidSafetyGate()

    def verify(
        self,
        assertions: List[VisualAssertion],
        ui_snapshot: AndroidUISnapshot,
        semantics_report: Optional[CorrelatedSemanticsReport] = None,
        screenshot_path: Optional[Union[str, Path]] = None,
    ) -> VisualVerificationReport:
        """
        Evaluates list of VisualAssertions deterministically against snapshot.
        """
        self.safety.check_emergency_stop()
        t0 = time.time()

        results: List[AssertionResult] = []
        passed_count = 0
        failed_count = 0
        optional_failed_count = 0

        for a in assertions:
            res = self._evaluate_assertion(a, ui_snapshot, semantics_report)
            results.append(res)

            if res.passed:
                passed_count += 1
            else:
                if a.optional:
                    optional_failed_count += 1
                else:
                    failed_count += 1

        # Determine overall status
        if not assertions:
            status = VisualVerificationStatus.NOT_VERIFIED
        elif failed_count == 0 and optional_failed_count == 0:
            status = VisualVerificationStatus.PASS
        elif failed_count == 0 and optional_failed_count > 0:
            status = VisualVerificationStatus.PARTIAL
        else:
            status = VisualVerificationStatus.FAIL

        elapsed = time.time() - t0
        report = VisualVerificationReport(
            status=status,
            total_assertions=len(assertions),
            passed_count=passed_count,
            failed_count=failed_count,
            optional_failed_count=optional_failed_count,
            results=results,
            screenshot_path=str(screenshot_path) if screenshot_path else None,
            device_serial=ui_snapshot.device_serial,
            duration_seconds=elapsed,
        )

        self.audit.log(
            event_type="android_visual_verification",
            actor="VisualVerificationEngine",
            details={
                "status": status.value,
                "total": len(assertions),
                "passed": passed_count,
                "failed": failed_count,
                "duration_s": elapsed,
            },
        )
        return report

    def _evaluate_assertion(
        self,
        assertion: VisualAssertion,
        ui_snapshot: AndroidUISnapshot,
        semantics_report: Optional[CorrelatedSemanticsReport],
    ) -> AssertionResult:
        """Evaluates a single assertion."""
        atype = assertion.assertion_type
        q = assertion.query.strip()
        exp = assertion.expected_value

        # 1. SCREEN_NOT_EMPTY
        if atype == AssertionType.SCREEN_NOT_EMPTY:
            min_count = int(exp) if exp is not None and str(exp).isdigit() else 1
            actual = len(ui_snapshot.targets)
            passed = actual >= min_count
            return AssertionResult(
                assertion=assertion,
                passed=passed,
                actual_value=actual,
                message=f"Screen contains {actual} targets (expected >= {min_count}).",
            )

        # 2. SCREEN_NAVIGATED
        if atype == AssertionType.SCREEN_NAVIGATED:
            fg = ui_snapshot.foreground_app
            pkg = fg.get("package", "")
            act = fg.get("activity", "")
            actual_str = f"{pkg}/{act}" if act else pkg

            target_q = (str(exp) if exp is not None else q).lower()
            passed = target_q in actual_str.lower() or target_q in act.lower() or target_q in pkg.lower()
            return AssertionResult(
                assertion=assertion,
                passed=passed,
                actual_value=actual_str,
                message=f"Foreground window is '{actual_str}', match='{target_q}'." if passed else f"Expected foreground matching '{target_q}', got '{actual_str}'.",
            )

        # 3. Target-based assertions (find matching target)
        matched_target = self._find_target(ui_snapshot, q)

        if atype == AssertionType.ELEMENT_VISIBLE:
            passed = False
            actual_val = None
            if matched_target:
                w = matched_target.bounds[2] - matched_target.bounds[0]
                h = matched_target.bounds[3] - matched_target.bounds[1]
                passed = w > 0 and h > 0
                actual_val = f"dimensions: {w}x{h}"
            return AssertionResult(
                assertion=assertion,
                passed=passed,
                actual_value=actual_val,
                matched_target=matched_target.to_dict() if matched_target else None,
                message=f"Element '{q}' is visible with {actual_val}." if passed else f"Element '{q}' was not found or has 0 dimensions.",
            )

        if atype == AssertionType.ELEMENT_ENABLED:
            passed = bool(matched_target and matched_target.enabled)
            actual_val = matched_target.enabled if matched_target else None
            return AssertionResult(
                assertion=assertion,
                passed=passed,
                actual_value=actual_val,
                matched_target=matched_target.to_dict() if matched_target else None,
                message=f"Element '{q}' enabled={passed}." if matched_target else f"Element '{q}' not found.",
            )

        if atype == AssertionType.ELEMENT_CLICKABLE:
            passed = bool(matched_target and matched_target.clickable)
            actual_val = matched_target.clickable if matched_target else None
            return AssertionResult(
                assertion=assertion,
                passed=passed,
                actual_value=actual_val,
                matched_target=matched_target.to_dict() if matched_target else None,
                message=f"Element '{q}' clickable={passed}." if matched_target else f"Element '{q}' not found.",
            )

        if atype == AssertionType.ELEMENT_TEXT_EQUALS:
            target_text = matched_target.text if matched_target else ""
            expected_text = str(exp if exp is not None else q)
            passed = bool(matched_target and target_text == expected_text)
            return AssertionResult(
                assertion=assertion,
                passed=passed,
                actual_value=target_text,
                matched_target=matched_target.to_dict() if matched_target else None,
                message=f"Element text matches exactly '{expected_text}'." if passed else f"Expected text '{expected_text}', got '{target_text}'.",
            )

        if atype == AssertionType.ELEMENT_TEXT_CONTAINS:
            target_text = matched_target.text if matched_target else ""
            expected_sub = str(exp if exp is not None else q)
            passed = bool(matched_target and expected_sub.lower() in target_text.lower())
            return AssertionResult(
                assertion=assertion,
                passed=passed,
                actual_value=target_text,
                matched_target=matched_target.to_dict() if matched_target else None,
                message=f"Element text '{target_text}' contains '{expected_sub}'." if passed else f"Text '{target_text}' does not contain '{expected_sub}'.",
            )

        # 4. SEMANTIC_NODE_PRESENT
        if atype == AssertionType.SEMANTIC_NODE_PRESENT:
            matched_node: Optional[CorrelatedSemanticsNode] = None
            if semantics_report:
                for n in semantics_report.nodes:
                    if (
                        q.lower() == n.component_type.lower()
                        or q.lower() == (n.test_tag or "").lower()
                        or q.lower() in (n.text or "").lower()
                    ):
                        if n.evidence in (CorrelationEvidence.CORRELATED, CorrelationEvidence.PARTIAL_CORRELATION):
                            matched_node = n
                            break

            passed = matched_node is not None
            return AssertionResult(
                assertion=assertion,
                passed=passed,
                actual_value=matched_node.evidence.value if matched_node else None,
                matched_node=matched_node.to_dict() if matched_node else None,
                message=f"Semantic node for '{q}' correlated ({matched_node.evidence.value})." if passed else f"No correlated semantic node found for '{q}'.",
            )

        return AssertionResult(
            assertion=assertion,
            passed=False,
            message=f"Unknown assertion type '{atype}'.",
        )

    def _find_target(self, snapshot: AndroidUISnapshot, query: str) -> Optional[AndroidTarget]:
        """Finds a target in the snapshot by target_id, exact text, content_desc, or resource_id."""
        # 1. Exact target_id match
        t = snapshot.get_target(query)
        if t:
            return t

        # 2. Match by text
        text_matches = snapshot.find_targets_by_text(query, exact=True)
        if text_matches:
            return text_matches[0]

        # 3. Match by content_desc or text substring
        sub_matches = snapshot.find_targets_by_text(query, exact=False)
        if sub_matches:
            return sub_matches[0]

        # 4. Match by resource_id
        res_matches = snapshot.find_targets_by_resource_id(query)
        if res_matches:
            return res_matches[0]

        return None

# Phase 3 compatibility alias
AndroidVisualVerifier = VisualVerificationEngine
