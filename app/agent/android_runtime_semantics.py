"""
NR-AI Runtime Compose Semantics Correlation Subsystem (Droid Phase 2).

Bridges source-level Jetpack Compose structural AST intelligence with live
device UI hierarchy (from UIAutomator accessibility tree):
1. Multi-signal node matching:
   - testTag / Modifier.semantics
   - contentDescription
   - Text literal values
   - Component type & accessibility class mapping (Button -> Button, etc.)
   - Spatial layout & vertical order correlation
2. Deterministic evidence classification:
   - CORRELATED: Strong, unambiguous match (exact testTag, exact unique text, exact contentDesc)
   - PARTIAL_CORRELATION: Moderate match (text substring, type + order heuristic)
   - UNMATCHED: No corresponding runtime element found for source component (or vice versa)
   - AMBIGUOUS: Multiple identical candidates without distinguishing signals
3. Semantic node enrichment:
   - Correlates runtime screen bounds, center, clickability, enablement with
     source file, composable function, line number, and AST metadata.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import logging
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from app.agent.android_ast import AndroidASTEngine
from app.agent.android_compose import (
    COMMON_COMPONENTS,
    ComposeComponentEntry,
    ComposeIntelligenceReport,
    ComposableFunctionReport,
    JetpackComposeIntelligenceEngine,
)
from app.agent.android_ui import AndroidTarget, AndroidUISnapshot

logger = logging.getLogger("NRAI.RuntimeSemantics")


# -----------------------------------------------------------------------------
# Data Models
# -----------------------------------------------------------------------------

class CorrelationEvidence(str, Enum):
    CORRELATED = "CORRELATED"
    PARTIAL_CORRELATION = "PARTIAL_CORRELATION"
    UNMATCHED = "UNMATCHED"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass
class CorrelatedSemanticsNode:
    """Enriched correlation between a source Compose component and a runtime UI target."""
    component_type: str
    source_file: str
    composable_name: str
    line_number: int
    target_id: Optional[str] = None
    bounds: Optional[Tuple[int, int, int, int]] = None
    center: Optional[Tuple[int, int]] = None
    clickable: bool = False
    enabled: bool = True
    text: Optional[str] = None
    content_desc: Optional[str] = None
    test_tag: Optional[str] = None
    evidence: CorrelationEvidence = CorrelationEvidence.UNMATCHED
    confidence: float = 0.0
    matched_signals: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "component_type": self.component_type,
            "source_file": self.source_file,
            "composable_name": self.composable_name,
            "line_number": self.line_number,
            "target_id": self.target_id,
            "bounds": list(self.bounds) if self.bounds else None,
            "center": list(self.center) if self.center else None,
            "clickable": self.clickable,
            "enabled": self.enabled,
            "text": self.text,
            "content_desc": self.content_desc,
            "test_tag": self.test_tag,
            "evidence": self.evidence.value if isinstance(self.evidence, CorrelationEvidence) else str(self.evidence),
            "confidence": round(self.confidence, 2),
            "matched_signals": self.matched_signals,
        }


@dataclass
class CorrelatedSemanticsReport:
    """Master correlation summary correlating Compose source with live runtime UI."""
    file_path: str
    device_serial: Optional[str] = None
    total_runtime_targets: int = 0
    total_source_components: int = 0
    correlated_count: int = 0
    partial_count: int = 0
    unmatched_count: int = 0
    ambiguous_count: int = 0
    correlation_ratio: float = 0.0
    nodes: List[CorrelatedSemanticsNode] = field(default_factory=list)
    unmatched_targets: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "device_serial": self.device_serial,
            "total_runtime_targets": self.total_runtime_targets,
            "total_source_components": self.total_source_components,
            "correlated_count": self.correlated_count,
            "partial_count": self.partial_count,
            "unmatched_count": self.unmatched_count,
            "ambiguous_count": self.ambiguous_count,
            "correlation_ratio": round(self.correlation_ratio, 3),
            "nodes": [n.to_dict() for n in self.nodes],
            "unmatched_targets": self.unmatched_targets,
            "timestamp": self.timestamp,
        }


# -----------------------------------------------------------------------------
# Runtime Compose Semantics Correlator
# -----------------------------------------------------------------------------

class RuntimeComposeSemanticsCorrelator:
    """
    Correlates source code Compose definitions with live accessibility trees.
    Deterministic, explainable, and multi-signal.
    """

    def __init__(self, compose_intelligence: Optional[JetpackComposeIntelligenceEngine] = None):
        self.compose = compose_intelligence or JetpackComposeIntelligenceEngine()

    def correlate(
        self,
        compose_report: ComposeIntelligenceReport,
        ui_snapshot: AndroidUISnapshot,
    ) -> CorrelatedSemanticsReport:
        """
        Executes multi-signal correlation between ComposeIntelligenceReport
        and AndroidUISnapshot.
        """
        runtime_targets = list(ui_snapshot.targets)
        matched_target_ids: Set[str] = set()
        nodes: List[CorrelatedSemanticsNode] = []

        # Flatten all components from all composables
        source_items: List[Tuple[str, ComposableFunctionReport, ComposeComponentEntry]] = []
        for fn_name, fn_report in compose_report.composables.items():
            for comp in fn_report.components:
                source_items.append((fn_name, fn_report, comp))

        for fn_name, fn_report, comp in source_items:
            # Extract expected signals from source snippet
            test_tag = self._extract_test_tag(comp.raw_snippet)
            expected_text = self._extract_text_literal(comp.raw_snippet)
            expected_desc = self._extract_content_desc(comp.raw_snippet)
            comp_type = comp.component_type

            best_target: Optional[AndroidTarget] = None
            best_evidence: CorrelationEvidence = CorrelationEvidence.UNMATCHED
            best_confidence: float = 0.0
            matched_signals: List[str] = []
            competing_candidates: List[AndroidTarget] = []

            # 1. Signal 1: TestTag / Resource ID Match (Strongest)
            if test_tag:
                for t in runtime_targets:
                    res_id = t.resource_id or ""
                    desc = t.content_desc or ""
                    if test_tag in res_id or test_tag == desc:
                        competing_candidates.append(t)

                if len(competing_candidates) == 1:
                    best_target = competing_candidates[0]
                    best_evidence = CorrelationEvidence.CORRELATED
                    best_confidence = 1.0
                    matched_signals.append(f"test_tag:{test_tag}")
                elif len(competing_candidates) > 1:
                    best_target = competing_candidates[0]
                    best_evidence = CorrelationEvidence.AMBIGUOUS
                    best_confidence = 0.5
                    matched_signals.append(f"test_tag_ambiguous:{test_tag}")

            # 2. Signal 2: Exact Text Match
            if not best_target and expected_text:
                for t in runtime_targets:
                    t_text = (t.text or "").strip()
                    if t_text and expected_text.lower() == t_text.lower():
                        competing_candidates.append(t)

                if len(competing_candidates) == 1:
                    best_target = competing_candidates[0]
                    best_evidence = CorrelationEvidence.CORRELATED
                    best_confidence = 0.95
                    matched_signals.append(f"text_exact:'{expected_text}'")
                elif len(competing_candidates) > 1:
                    best_target = competing_candidates[0]
                    best_evidence = CorrelationEvidence.AMBIGUOUS
                    best_confidence = 0.6
                    matched_signals.append(f"text_ambiguous:'{expected_text}'")

            # 3. Signal 3: Exact Content Description Match
            if not best_target and expected_desc:
                for t in runtime_targets:
                    t_desc = (t.content_desc or "").strip()
                    if t_desc and expected_desc.lower() == t_desc.lower():
                        competing_candidates.append(t)

                if len(competing_candidates) == 1:
                    best_target = competing_candidates[0]
                    best_evidence = CorrelationEvidence.CORRELATED
                    best_confidence = 0.9
                    matched_signals.append(f"content_desc:'{expected_desc}'")
                elif len(competing_candidates) > 1:
                    best_target = competing_candidates[0]
                    best_evidence = CorrelationEvidence.AMBIGUOUS
                    best_confidence = 0.55
                    matched_signals.append(f"content_desc_ambiguous:'{expected_desc}'")

            # 4. Signal 4: Substring Text Match (Partial)
            if not best_target and expected_text and len(expected_text) >= 3:
                for t in runtime_targets:
                    t_text = (t.text or "").strip()
                    if t_text and (expected_text.lower() in t_text.lower() or t_text.lower() in expected_text.lower()):
                        competing_candidates.append(t)

                if len(competing_candidates) == 1:
                    best_target = competing_candidates[0]
                    best_evidence = CorrelationEvidence.PARTIAL_CORRELATION
                    best_confidence = 0.75
                    matched_signals.append(f"text_substring:'{expected_text}'")

            # 5. Signal 5: Component Type & Interaction Match
            if not best_target:
                for t in runtime_targets:
                    if t.target_id in matched_target_ids:
                        continue
                    type_match = self._matches_component_type(comp_type, t)
                    if type_match:
                        competing_candidates.append(t)

                if len(competing_candidates) == 1:
                    best_target = competing_candidates[0]
                    best_evidence = CorrelationEvidence.PARTIAL_CORRELATION
                    best_confidence = 0.65
                    matched_signals.append(f"type_inferred:{comp_type}")
                elif len(competing_candidates) > 1:
                    # Pick first available matching type
                    best_target = competing_candidates[0]
                    best_evidence = CorrelationEvidence.PARTIAL_CORRELATION
                    best_confidence = 0.5
                    matched_signals.append(f"type_heuristic:{comp_type}")

            # Record node
            if best_target:
                matched_target_ids.add(best_target.target_id)
                nodes.append(CorrelatedSemanticsNode(
                    component_type=comp_type,
                    source_file=compose_report.file_path,
                    composable_name=fn_name,
                    line_number=comp.line,
                    target_id=best_target.target_id,
                    bounds=best_target.bounds,
                    center=best_target.center,
                    clickable=best_target.clickable,
                    enabled=best_target.enabled,
                    text=best_target.text or expected_text,
                    content_desc=best_target.content_desc or expected_desc,
                    test_tag=test_tag,
                    evidence=best_evidence,
                    confidence=best_confidence,
                    matched_signals=matched_signals,
                ))
            else:
                nodes.append(CorrelatedSemanticsNode(
                    component_type=comp_type,
                    source_file=compose_report.file_path,
                    composable_name=fn_name,
                    line_number=comp.line,
                    target_id=None,
                    bounds=None,
                    center=None,
                    clickable=comp.has_click,
                    enabled=True,
                    text=expected_text,
                    content_desc=expected_desc,
                    test_tag=test_tag,
                    evidence=CorrelationEvidence.UNMATCHED,
                    confidence=0.0,
                    matched_signals=[],
                ))

        # Identify unmatched runtime targets
        unmatched_targets = [
            t.target_id for t in runtime_targets if t.target_id not in matched_target_ids
        ]

        corr_count = sum(1 for n in nodes if n.evidence == CorrelationEvidence.CORRELATED)
        partial_count = sum(1 for n in nodes if n.evidence == CorrelationEvidence.PARTIAL_CORRELATION)
        unmatched_count = sum(1 for n in nodes if n.evidence == CorrelationEvidence.UNMATCHED)
        ambiguous_count = sum(1 for n in nodes if n.evidence == CorrelationEvidence.AMBIGUOUS)

        total_source = len(nodes)
        ratio = ((corr_count + 0.5 * partial_count) / total_source) if total_source > 0 else 0.0

        return CorrelatedSemanticsReport(
            file_path=compose_report.file_path,
            device_serial=ui_snapshot.device_serial,
            total_runtime_targets=len(runtime_targets),
            total_source_components=total_source,
            correlated_count=corr_count,
            partial_count=partial_count,
            unmatched_count=unmatched_count,
            ambiguous_count=ambiguous_count,
            correlation_ratio=ratio,
            nodes=nodes,
            unmatched_targets=unmatched_targets,
        )

    def correlate_from_source(
        self,
        source_code: str,
        ui_snapshot: AndroidUISnapshot,
        file_path: str = "memory://Composable.kt",
    ) -> CorrelatedSemanticsReport:
        """Helper to run correlation directly from Kotlin source text."""
        compose_rep = self.compose.analyze_source(source_code, file_path=file_path)
        return self.correlate(compose_rep, ui_snapshot)

    # -------------------------------------------------------------------------
    # Signal Extraction Helpers
    # -------------------------------------------------------------------------

    def _extract_test_tag(self, snippet: str) -> Optional[str]:
        """Extracts testTag parameter from Modifier.testTag("...") or testTag = "..."."""
        m = re.search(r'(?:testTag\s*=\s*|Modifier\.testTag\(\s*)"([^"]+)"', snippet)
        return m.group(1) if m else None

    def _extract_text_literal(self, snippet: str) -> Optional[str]:
        """Extracts text string literal from Text("...") or Button { Text("...") }."""
        m = re.search(r'Text\(\s*(?:text\s*=\s*)?"([^"]+)"', snippet)
        if not m:
            m = re.search(r'"([^"]{2,})"', snippet)
        return m.group(1) if m else None

    def _extract_content_desc(self, snippet: str) -> Optional[str]:
        """Extracts contentDescription attribute."""
        m = re.search(r'contentDescription\s*=\s*"([^"]+)"', snippet)
        return m.group(1) if m else None

    def _matches_component_type(self, comp_type: str, target: AndroidTarget) -> bool:
        """Determines if a Compose component type corresponds to a runtime target."""
        cls_name = (target.class_name or "").lower()
        sem_type = (target.semantic_type or "").lower()

        if comp_type in ("Button", "OutlinedButton", "ElevatedButton", "TextButton", "IconButton"):
            return "button" in cls_name or sem_type == "button" or (target.clickable and bool(target.text))
        elif comp_type == "Text":
            return "textview" in cls_name or sem_type in ("text", "label") or bool(target.text)
        elif comp_type in ("Image", "Icon"):
            return "imageview" in cls_name or sem_type in ("image", "icon")
        elif comp_type in ("TextField", "OutlinedTextField"):
            return "edittext" in cls_name or sem_type in ("input", "field")
        elif comp_type == "Checkbox":
            return "checkbox" in cls_name or sem_type == "checkbox"
        elif comp_type == "Switch":
            return "switch" in cls_name or sem_type == "switch"
        elif comp_type == "RadioButton":
            return "radiobutton" in cls_name or sem_type == "radio"
        return False
