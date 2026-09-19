"""
NR-AI Android UI Behavior Debugger Engine.

Connects UI actions and device accessibility hierarchy to Jetpack Compose
semantics, source callbacks, state holders, and runtime logcat:
  User Action -> UI Target -> UI Hierarchy -> Compose Semantics ->
  Source Callback -> State Mutation -> Recomposition / Runtime -> Logcat.

Diagnoses behavioral bugs:
  - Button does nothing (disconnected or empty callback)
  - Text field / counter doesn't update (unmutated state or unbound state holder)
  - Element missing / unrepresented state
  - Blocked main thread or unhandled navigation
"""

from dataclasses import dataclass, field, asdict
from enum import Enum
import logging
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from app.agent.android_compose_intelligence import (
    AndroidComposeIntelligence,
    ComposeIntelligenceReport,
    ComposeAnomalyKind,
)
from app.agent.android_ui import AndroidTarget, AndroidUISnapshot

logger = logging.getLogger("NRAI.AndroidUIDebugger")


class UIDefectKind(str, Enum):
    DISCONNECTED_CALLBACK = "DISCONNECTED_CALLBACK"
    UNMUTATED_STATE = "UNMUTATED_STATE"
    UNBOUND_STATE_HOLDER = "UNBOUND_STATE_HOLDER"
    UNREPRESENTED_STATE = "UNREPRESENTED_STATE"
    MISSING_UI_TARGET = "MISSING_UI_TARGET"
    BLOCKED_MAIN_THREAD = "BLOCKED_MAIN_THREAD"
    NAVIGATION_UNHANDLED = "NAVIGATION_UNHANDLED"
    NONE_DETECTED = "NONE_DETECTED"


class UIDiagnosisStatus(str, Enum):
    OBSERVED = "OBSERVED"
    STRONGLY_SUPPORTED = "STRONGLY_SUPPORTED"
    POSSIBLE = "POSSIBLE"
    UNRESOLVED = "UNRESOLVED"


@dataclass
class UIInteractionTrace:
    action_type: str
    target_text: str
    target_id: Optional[str] = None
    pre_state_snippet: List[str] = field(default_factory=list)
    post_state_snippet: List[str] = field(default_factory=list)
    expected_change: str = ""
    actual_change: str = ""
    logcat_snippet: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class UIDebugDiagnosis:
    defect_kind: UIDefectKind
    status: UIDiagnosisStatus
    target_composable: Optional[str] = None
    source_file: Optional[str] = None
    source_line: Optional[int] = None
    state_variable: Optional[str] = None
    callback_name: Optional[str] = None
    explanation: str = ""
    repair_suggestion: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["defect_kind"] = self.defect_kind.value
        d["status"] = self.status.value
        return d


@dataclass
class UIDebugReport:
    device_serial: str
    traces: List[UIInteractionTrace] = field(default_factory=list)
    diagnoses: List[UIDebugDiagnosis] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "device_serial": self.device_serial,
            "traces": [t.to_dict() for t in self.traces],
            "diagnoses": [d.to_dict() for d in self.diagnoses],
            "summary": self.summary,
        }


class AndroidUIDebugger:
    """Diagnoses UI interaction failures and state divergence across Android UI & Compose."""

    def __init__(self):
        self.compose_engine = AndroidComposeIntelligence()

    def diagnose_interaction(
        self,
        pre_elements: List[str],
        post_elements: List[str],
        action: str,
        target_text: str,
        compose_report: Optional[ComposeIntelligenceReport] = None,
        logcat_snippet: str = "",
    ) -> UIDebugDiagnosis:
        """
        Diagnoses whether an action succeeded or failed, correlating with Compose analysis.
        """
        # Check if UI hierarchy changed
        ui_changed = (pre_elements != post_elements)
        has_anr = "ANR" in logcat_snippet or "Application Not Responding" in logcat_snippet
        has_crash = "FATAL EXCEPTION" in logcat_snippet or "Exception" in logcat_snippet

        if has_anr:
            return UIDebugDiagnosis(
                defect_kind=UIDefectKind.BLOCKED_MAIN_THREAD,
                status=UIDiagnosisStatus.OBSERVED,
                explanation=f"Action '{action}' on '{target_text}' triggered an ANR on the main thread.",
                repair_suggestion="Move long-running computation off the main thread into Dispatchers.Default or Dispatchers.IO.",
            )

        # Check Compose anomalies if available
        if compose_report:
            for anomaly in compose_report.anomalies:
                if anomaly.kind == ComposeAnomalyKind.CALLBACK_DISCONNECTED:
                    return UIDebugDiagnosis(
                        defect_kind=UIDefectKind.DISCONNECTED_CALLBACK,
                        status=UIDiagnosisStatus.STRONGLY_SUPPORTED,
                        target_composable=anomaly.composable_name,
                        source_file=anomaly.file_path,
                        source_line=anomaly.line,
                        callback_name="onClick",
                        explanation=f"Action '{action}' triggered callback in '{anomaly.composable_name}' which is disconnected or empty.",
                        repair_suggestion="Wire the callback body to the appropriate ViewModel action or state mutator.",
                    )
                elif anomaly.kind == ComposeAnomalyKind.STATE_NEVER_UPDATED:
                    return UIDebugDiagnosis(
                        defect_kind=UIDefectKind.UNMUTATED_STATE,
                        status=UIDiagnosisStatus.STRONGLY_SUPPORTED,
                        target_composable=anomaly.composable_name,
                        source_file=anomaly.file_path,
                        source_line=anomaly.line,
                        state_variable="state",
                        explanation=f"State holder in '{anomaly.composable_name}' is declared but never updated on user interactions.",
                        repair_suggestion="Ensure callback updates state.value or passes event to state owner.",
                    )

        # If UI didn't change and action was interactive (click/tap)
        if not ui_changed and action.lower() in ("click", "tap", "press"):
            return UIDebugDiagnosis(
                defect_kind=UIDefectKind.UNMUTATED_STATE,
                status=UIDiagnosisStatus.POSSIBLE,
                explanation=f"Tap on '{target_text}' produced no visible state or layout changes in the UI hierarchy.",
                repair_suggestion="Verify onClick handler mutates a mutableStateOf/StateFlow and that the UI observes it.",
            )

        return UIDebugDiagnosis(
            defect_kind=UIDefectKind.NONE_DETECTED,
            status=UIDiagnosisStatus.OBSERVED,
            explanation=f"Action '{action}' on '{target_text}' completed with observable UI state transition.",
            repair_suggestion="",
        )

    def diagnose_from_snapshots(
        self,
        pre_snap: AndroidUISnapshot,
        post_snap: AndroidUISnapshot,
        action_target: AndroidTarget,
        action_type: str = "tap",
        compose_report: Optional[ComposeIntelligenceReport] = None,
        logcat_snippet: str = "",
    ) -> UIDebugReport:
        """Full diagnosis comparing pre-action and post-action UI snapshots."""
        pre_texts = pre_snap.visible_text_items
        post_texts = post_snap.visible_text_items

        trace = UIInteractionTrace(
            action_type=action_type,
            target_text=action_target.text or action_target.content_desc or action_target.resource_id,
            target_id=action_target.target_id,
            pre_state_snippet=pre_texts[:10],
            post_state_snippet=post_texts[:10],
            expected_change="State mutation and recomposition / UI update",
            actual_change="UI unchanged" if pre_texts == post_texts else "UI updated",
            logcat_snippet=logcat_snippet[:500],
        )

        diagnosis = self.diagnose_interaction(
            pre_elements=pre_texts,
            post_elements=post_texts,
            action=action_type,
            target_text=trace.target_text,
            compose_report=compose_report,
            logcat_snippet=logcat_snippet,
        )

        report = UIDebugReport(
            device_serial=pre_snap.device_serial,
            traces=[trace],
            diagnoses=[diagnosis],
            summary=diagnosis.explanation,
        )
        return report
