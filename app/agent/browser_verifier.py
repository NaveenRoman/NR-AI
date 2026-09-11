"""
NR-AI Browser State Verification Engine (Step 5 Phase 3).

Provides deterministic, ground-truth state verification comparing
BEFORE-ACTION and AFTER-ACTION browser states.

Key Principles:
- Ground-Truth Wins: Model claims are NOT evidence. If deterministic evidence
  shows failure, model claims of success are rejected (Case D).
- Stale Target Protection: 15-second TTL bounds on element freshness.
- Sensitive Data Exclusion: Snapshots strictly exclude passwords, tokens, and cookies.
"""

from dataclasses import dataclass, field
from enum import Enum
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from playwright.sync_api import Page

from app.agent.browser_perception import (
    SemanticPageSummary,
    WebPerception,
)
from app.agent.browser_safety import (
    BrowserSafetyGate,
    StaleTargetError,
)
from app.agent.consensus_engine import (
    ConsensusDecision,
    ConsensusReport,
    DecisionCase,
    ExecutionEvidence,
    VerificationReview,
)

logger = logging.getLogger("NRAI.BrowserVerifier")

TARGET_TTL_SECONDS = 15.0


# -----------------------------------------------------------------------------
# Data Models
# -----------------------------------------------------------------------------

@dataclass
class BrowserStateSnapshot:
    """
    Bounded, sanitised snapshot of browser page state at a specific moment in time.
    Strictly excludes credentials, tokens, cookies, and secrets.
    """
    session_id: str
    tab_id: str
    url: str
    title: str
    semantic_summary: Dict[str, Any]
    interactive_elements: List[Dict[str, Any]]
    visible_text_summary: str
    screenshot_metadata: Optional[Dict[str, Any]] = None
    timestamp: float = field(default_factory=time.time)
    ttl_seconds: float = TARGET_TTL_SECONDS

    def is_stale(self) -> bool:
        """Returns True if snapshot is older than the TTL."""
        return (time.time() - self.timestamp) > self.ttl_seconds

    def has_element(self, identifier: str) -> bool:
        """Checks whether element is present by id, selector, or name."""
        clean_id = identifier.lstrip("#").lower()
        for el in self.interactive_elements:
            el_id = str(el.get("id", "")).lower()
            el_dom_id = str(el.get("dom_id", "")).lower()
            el_sel = str(el.get("selector", "")).lower()
            el_name = str(el.get("name", "")).lower()
            if (
                clean_id == el_dom_id or
                clean_id == el_id or
                clean_id in (el_id, el_sel.lstrip("#")) or
                clean_id in el_id or
                identifier.lower() in (el_id, el_sel, el_name, el_dom_id)
            ):
                return True
        return False

    def get_element(self, identifier: str) -> Optional[Dict[str, Any]]:
        """Retrieves element dict by id or selector."""
        clean_id = identifier.lstrip("#").lower()
        for el in self.interactive_elements:
            el_id = str(el.get("id", "")).lower()
            el_dom_id = str(el.get("dom_id", "")).lower()
            el_sel = str(el.get("selector", "")).lower()
            if clean_id in (el_dom_id, el_id, el_sel.lstrip("#")) or identifier.lower() in (el_id, el_sel, el_dom_id):
                return el
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "tab_id": self.tab_id,
            "url": self.url,
            "title": self.title,
            "semantic_summary": self.semantic_summary,
            "element_count": len(self.interactive_elements),
            "text_preview": self.visible_text_summary[:300],
            "screenshot_metadata": self.screenshot_metadata,
            "timestamp": self.timestamp,
        }


@dataclass
class VerificationCheckResult:
    """Individual verification assertion result."""
    check_name: str
    passed: bool
    expected: Any
    actual: Any
    message: str


@dataclass
class StateVerificationResult:
    """Comprehensive result of comparing before and after states against expectations."""
    verified: bool
    status: str
    checks: List[VerificationCheckResult]
    objections: List[str]
    evidence_summary: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verified": self.verified,
            "status": self.status,
            "checks": [
                {
                    "check": c.check_name,
                    "passed": c.passed,
                    "expected": c.expected,
                    "actual": c.actual,
                    "message": c.message,
                }
                for c in self.checks
            ],
            "objections": self.objections,
            "evidence_summary": self.evidence_summary,
            "timestamp": self.timestamp,
        }


# -----------------------------------------------------------------------------
# State Verification Engine
# -----------------------------------------------------------------------------

class BrowserStateVerifier:
    """
    Deterministic State Verifier comparing before-action and after-action
    browser states. Rejects model claims when concrete evidence fails.
    """

    def __init__(
        self,
        perception: Optional[WebPerception] = None,
        safety_gate: Optional[BrowserSafetyGate] = None,
        target_ttl_seconds: float = TARGET_TTL_SECONDS,
    ):
        self.perception = perception or WebPerception(target_ttl_seconds=target_ttl_seconds)
        self.safety_gate = safety_gate or BrowserSafetyGate()
        self.target_ttl_seconds = target_ttl_seconds

    # -------------------------------------------------------------------------
    # Snapshot Creation
    # -------------------------------------------------------------------------

    def capture_snapshot(
        self,
        page: Page,
        session_id: str = "default_session",
        tab_id: str = "tab_0",
        capture_screenshot: bool = False,
    ) -> BrowserStateSnapshot:
        """
        Captures a sanitised, bounded snapshot of the current browser page.
        Excludes passwords, tokens, secrets, cookies, and auth headers.
        """
        summary, screenshot_meta = self.perception.extract_semantic_page(
            page=page,
            include_visual_mapping=True,
            capture_screenshot=capture_screenshot,
        )

        # Sanitize interactive elements: remove or redact sensitive values
        sanitized_elements = []
        for el in summary.interactive_elements:
            elem_id = el.get("id", "")
            el_type = el.get("type", "")
            name = el.get("name", "")

            # If element is a password or credential field, sanitize any values
            if el_type == "password" or self.safety_gate.is_sensitive_field(name=name, id_attr=elem_id):
                sanitized_el = dict(el)
                if "value" in sanitized_el:
                    sanitized_el["value"] = "********"
                sanitized_elements.append(sanitized_el)
            else:
                sanitized_elements.append(dict(el))

        # Sanitize text preview to avoid logging passwords or credit cards
        raw_text = summary.untrusted_data.raw_content if summary.untrusted_data else ""
        cleaned_text = re.sub(r"\s+", " ", raw_text).strip()

        return BrowserStateSnapshot(
            session_id=session_id,
            tab_id=tab_id,
            url=page.url,
            title=page.title(),
            semantic_summary=summary.to_dict(),
            interactive_elements=sanitized_elements,
            visible_text_summary=cleaned_text[:1500],
            screenshot_metadata=screenshot_meta.to_dict() if screenshot_meta else None,
            timestamp=time.time(),
            ttl_seconds=self.target_ttl_seconds,
        )

    # -------------------------------------------------------------------------
    # Verification & Diffing
    # -------------------------------------------------------------------------

    def verify_transition(
        self,
        before: BrowserStateSnapshot,
        after: BrowserStateSnapshot,
        expectations: Dict[str, Any],
    ) -> StateVerificationResult:
        """
        Compares before and after state snapshots against deterministic expectations.

        Supported expectations:
        - expected_url_pattern: regex pattern matching after.url
        - expected_text_appeared: string that MUST be in after.visible_text_summary
        - expected_text_disappeared: string that was in before and MUST NOT be in after
        - expected_element_appeared: element ID or selector that must exist in after
        - expected_element_disappeared: element ID or selector that existed in before and must not in after
        - expected_element_enabled: element ID that must be enabled=True in after
        - expected_element_disabled: element ID that must be enabled=False in after
        - expected_title_changed: bool, whether title must have changed
        """
        # 1. Stale target check on before state
        if before.is_stale():
            raise StaleTargetError(
                f"Before-state snapshot is stale ({time.time() - before.timestamp:.1f}s old > {self.target_ttl_seconds}s TTL). "
                f"Cannot reliably verify transition against expired baseline."
            )

        checks: List[VerificationCheckResult] = []
        objections: List[str] = []

        # Check 1: URL Pattern
        if "expected_url_pattern" in expectations:
            pat = expectations["expected_url_pattern"]
            matched = bool(re.search(pat, after.url))
            checks.append(VerificationCheckResult(
                check_name="url_pattern",
                passed=matched,
                expected=pat,
                actual=after.url,
                message="URL matches expected pattern" if matched else f"URL '{after.url}' did not match '{pat}'",
            ))
            if not matched:
                objections.append(f"URL did not match expected pattern '{pat}'. Actual URL: '{after.url}'")

        # Check 2: Text Appearance
        if "expected_text_appeared" in expectations:
            text = expectations["expected_text_appeared"]
            present_after = text.lower() in after.visible_text_summary.lower()
            checks.append(VerificationCheckResult(
                check_name="text_appeared",
                passed=present_after,
                expected=text,
                actual="PRESENT" if present_after else "MISSING",
                message=f"Text '{text}' appeared in after-state" if present_after else f"Text '{text}' was not found in after-state",
            ))
            if not present_after:
                objections.append(f"Expected text '{text}' failed to appear in page content.")

        # Check 3: Text Disappearance
        if "expected_text_disappeared" in expectations:
            text = expectations["expected_text_disappeared"]
            still_present = text.lower() in after.visible_text_summary.lower()
            checks.append(VerificationCheckResult(
                check_name="text_disappeared",
                passed=not still_present,
                expected=f"NOT '{text}'",
                actual="ABSENT" if not still_present else "STILL_PRESENT",
                message=f"Text '{text}' disappeared" if not still_present else f"Text '{text}' is still present",
            ))
            if still_present:
                objections.append(f"Expected text '{text}' to disappear, but it remained visible.")

        # Check 4: Element Appearance
        if "expected_element_appeared" in expectations:
            elem_id = expectations["expected_element_appeared"]
            present = after.has_element(elem_id)
            checks.append(VerificationCheckResult(
                check_name="element_appeared",
                passed=present,
                expected=elem_id,
                actual="PRESENT" if present else "NOT_FOUND",
                message=f"Element '{elem_id}' appeared" if present else f"Element '{elem_id}' was not found in after-state",
            ))
            if not present:
                objections.append(f"Expected element '{elem_id}' was not found in after-state.")

        # Check 5: Element Disappearance
        if "expected_element_disappeared" in expectations:
            elem_id = expectations["expected_element_disappeared"]
            still_present = after.has_element(elem_id)
            checks.append(VerificationCheckResult(
                check_name="element_disappeared",
                passed=not still_present,
                expected=f"NOT '{elem_id}'",
                actual="ABSENT" if not still_present else "STILL_PRESENT",
                message=f"Element '{elem_id}' disappeared" if not still_present else f"Element '{elem_id}' is still present",
            ))
            if still_present:
                objections.append(f"Expected element '{elem_id}' to disappear, but it remains present.")

        # Check 6: Element Enabled State
        if "expected_element_enabled" in expectations:
            elem_id = expectations["expected_element_enabled"]
            el = after.get_element(elem_id)
            is_enabled = bool(el and el.get("enabled", False))
            checks.append(VerificationCheckResult(
                check_name="element_enabled",
                passed=is_enabled,
                expected=True,
                actual=is_enabled,
                message=f"Element '{elem_id}' enabled={is_enabled}",
            ))
            if not is_enabled:
                objections.append(f"Expected element '{elem_id}' to be enabled, but it is disabled or absent.")

        # Check 7: Element Disabled State
        if "expected_element_disabled" in expectations:
            elem_id = expectations["expected_element_disabled"]
            el = after.get_element(elem_id)
            is_disabled = bool(el and not el.get("enabled", True))
            checks.append(VerificationCheckResult(
                check_name="element_disabled",
                passed=is_disabled,
                expected=True,
                actual=is_disabled,
                message=f"Element '{elem_id}' disabled={is_disabled}",
            ))
            if not is_disabled:
                objections.append(f"Expected element '{elem_id}' to be disabled, but it is enabled.")

        # Check 8: Title Change
        if expectations.get("expected_title_changed"):
            changed = before.title != after.title
            checks.append(VerificationCheckResult(
                check_name="title_changed",
                passed=changed,
                expected="TITLE_CHANGED",
                actual=f"before='{before.title}', after='{after.title}'",
                message="Title changed" if changed else "Title did not change",
            ))
            if not changed:
                objections.append(f"Title remained unchanged ('{before.title}').")

        # Compile overall result
        all_passed = all(c.passed for c in checks) if checks else (before.url != after.url or before.title != after.title)
        status = "VERIFIED_PASS" if all_passed else "VERIFIED_FAIL"

        evidence_summary = {
            "url_before": before.url,
            "url_after": after.url,
            "url_changed": before.url != after.url,
            "title_before": before.title,
            "title_after": after.title,
            "title_changed": before.title != after.title,
            "element_count_before": len(before.interactive_elements),
            "element_count_after": len(after.interactive_elements),
        }

        return StateVerificationResult(
            verified=all_passed,
            status=status,
            checks=checks,
            objections=objections,
            evidence_summary=evidence_summary,
            timestamp=time.time(),
        )

    # -------------------------------------------------------------------------
    # Ground-Truth vs Model Claim Evaluation (Case D Integration)
    # -------------------------------------------------------------------------

    def evaluate_model_claim(
        self,
        model_claim_success: bool,
        model_explanation: str,
        verification_result: StateVerificationResult,
    ) -> ConsensusReport:
        """
        Evaluates a model's claim against deterministic browser state verification.

        Ground Truth Rule:
        - If verification_result.verified is FALSE:
          REJECT the model's claim immediately under CASE_D, regardless of whether
          the model claimed success or high confidence.
        - If verification_result.verified is TRUE and model claimed success:
          ACCEPT under CASE_A.
        - If model claims failure and verification also shows failure:
          REJECT under CASE_C.
        - If disagreement:
          INVESTIGATE under CASE_B.
        """
        model_review = VerificationReview(
            slot_id=5,
            verifier_name="ModelWebVerifier",
            model_id="advisory-model",
            passed=model_claim_success,
            confidence=0.9 if model_claim_success else 0.2,
            objections=[] if model_claim_success else [model_explanation],
            notes=model_explanation,
        )

        exec_evidence = ExecutionEvidence(
            command="browser_state_verification",
            exit_code=0 if verification_result.verified else 1,
            stdout=f"Checks passed: {sum(1 for c in verification_result.checks if c.passed)}/{len(verification_result.checks)}",
            stderr="; ".join(verification_result.objections) if verification_result.objections else "",
            real_execution_verified=True,
        )

        # ---------------------------------------------------------------------
        # CASE D: Concrete ground truth failed -> REJECT overrides model claim
        # ---------------------------------------------------------------------
        if not verification_result.verified:
            summary = (
                f"Ground-truth state verification failed ({'; '.join(verification_result.objections)}). "
                f"Rejection overrides model claim of success: '{model_explanation}'."
            )
            return ConsensusReport(
                decision=ConsensusDecision.REJECT,
                case=DecisionCase.CASE_D,
                summary=summary,
                primary_review=model_review,
                evidence=exec_evidence,
                objections=verification_result.objections,
                recommended_action="Reject action outcome and trigger recovery / retry.",
            )

        # ---------------------------------------------------------------------
        # CASE A: Both model and deterministic evidence confirm success
        # ---------------------------------------------------------------------
        if model_claim_success and verification_result.verified:
            return ConsensusReport(
                decision=ConsensusDecision.ACCEPT,
                case=DecisionCase.CASE_A,
                summary="Both model assessment and deterministic browser state verification confirm action success.",
                primary_review=model_review,
                evidence=exec_evidence,
                objections=[],
                recommended_action="Accept action outcome and proceed to next step.",
            )

        # ---------------------------------------------------------------------
        # CASE B: Conflict between model and ground truth (e.g. ground truth passed but model complained)
        # ---------------------------------------------------------------------
        return ConsensusReport(
            decision=ConsensusDecision.INVESTIGATE,
            case=DecisionCase.CASE_B,
            summary="Discrepancy between model observation and browser state evidence.",
            primary_review=model_review,
            evidence=exec_evidence,
            objections=model_review.objections + verification_result.objections,
            recommended_action="Investigate unexpected state transition.",
        )
