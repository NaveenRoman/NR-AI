"""
NR-AI Jetpack Compose State & Interaction Intelligence Engine.

Performs static analysis of @Composable functions, state holding primitives, event
callbacks, recomposition triggers, and ViewModel interactions. Detects common Compose
anti-patterns and event flow anomalies.

Classifications strictly adhere to:
OBSERVED, STRONGLY_SUPPORTED, POSSIBLE, UNKNOWN.
(Never classifies as confirmed bug without runtime/test evidence).
"""

from dataclasses import dataclass, field, asdict
from enum import Enum
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

logger = logging.getLogger("NRAI.AndroidComposeIntelligence")


class ComposePatternStatus(str, Enum):
    OBSERVED = "OBSERVED"
    STRONGLY_SUPPORTED = "STRONGLY_SUPPORTED"
    POSSIBLE = "POSSIBLE"
    UNKNOWN = "UNKNOWN"


class ComposeAnomalyKind(str, Enum):
    STATE_NEVER_UPDATED = "STATE_NEVER_UPDATED"
    CALLBACK_DISCONNECTED = "CALLBACK_DISCONNECTED"
    STALE_STATE = "STALE_STATE"
    MISSING_STATE_OBSERVATION = "MISSING_STATE_OBSERVATION"
    STATE_MUTATION_OUTSIDE_EXPECTED_FLOW = "STATE_MUTATION_OUTSIDE_EXPECTED_FLOW"
    UNREACHABLE_INTERACTION_HANDLER = "UNREACHABLE_INTERACTION_HANDLER"
    MISSING_LIFECYCLE_AWARE_COLLECTION = "MISSING_LIFECYCLE_AWARE_COLLECTION"


@dataclass
class ComposableFunction:
    name: str
    file_path: str
    line: int
    parameters: List[Dict[str, str]] = field(default_factory=list)
    state_holders: List[Dict[str, Any]] = field(default_factory=list)
    side_effects: List[str] = field(default_factory=list)
    callbacks: List[str] = field(default_factory=list)
    viewmodel_refs: List[str] = field(default_factory=list)
    is_hoisted: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ComposeEventFlow:
    composable_name: str
    trigger_event: str
    callback_target: str
    state_mutated: Optional[str]
    causes_recomposition: bool
    flow_pattern: str
    status: ComposePatternStatus

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class ComposeStateAnomaly:
    kind: ComposeAnomalyKind
    status: ComposePatternStatus
    composable_name: str
    state_or_handler_name: str
    description: str
    file_path: str
    line_number: int
    suggested_fix: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value
        d["status"] = self.status.value
        return d


@dataclass
class ComposeIntelligenceReport:
    composables: List[ComposableFunction] = field(default_factory=list)
    event_flows: List[ComposeEventFlow] = field(default_factory=list)
    anomalies: List[ComposeStateAnomaly] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)

    @property
    def total_composables(self) -> int:
        return len(self.composables)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_composables": self.total_composables,
            "composables": [c.to_dict() for c in self.composables],
            "event_flows": [ef.to_dict() for ef in self.event_flows],
            "anomalies": [a.to_dict() for a in self.anomalies],
            "summary": self.summary,
        }


class AndroidComposeIntelligence:
    """
    Analyzes Kotlin Compose files for state holding, event handling, and flow anomalies.
    """

    STATE_PRIMITIVES = {
        "remember": "remember",
        "rememberSaveable": "rememberSaveable",
        "mutableStateOf": "mutableStateOf",
        "derivedStateOf": "derivedStateOf",
        "collectAsState": "collectAsState",
        "collectAsStateWithLifecycle": "collectAsStateWithLifecycle",
    }

    SIDE_EFFECTS = {
        "LaunchedEffect",
        "DisposableEffect",
        "SideEffect",
    }

    def analyze_code(self, code: str, file_path: str = "") -> ComposeIntelligenceReport:
        """Analyzes a Kotlin source string containing Compose functions."""
        composables: List[ComposableFunction] = []
        event_flows: List[ComposeEventFlow] = []
        anomalies: List[ComposeStateAnomaly] = []

        lines = code.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            trimmed = line.strip()

            if "@Composable" in trimmed and not trimmed.startswith("//"):
                # Find the function header
                fn_line_idx = i + 1
                while fn_line_idx < len(lines) and "fun " not in lines[fn_line_idx]:
                    fn_line_idx += 1

                if fn_line_idx < len(lines):
                    fn_line = lines[fn_line_idx].strip()
                    m_fun = re.search(r"fun\s+([a-zA-Z0-9_]+)\s*\(([^)]*)\)", fn_line)
                    if m_fun:
                        fn_name = m_fun.group(1)
                        params_raw = m_fun.group(2)
                        parsed_params = self._parse_parameters(params_raw)

                        # Extract body of Composable
                        body_lines, end_idx = self._extract_block(lines, fn_line_idx)
                        comp = self._analyze_composable_body(
                            fn_name=fn_name,
                            file_path=file_path,
                            start_line=fn_line_idx + 1,
                            params=parsed_params,
                            body_lines=body_lines,
                        )
                        composables.append(comp)

                        # Detect Flows & Anomalies in this Composable
                        self._analyze_flows_and_anomalies(comp, body_lines, event_flows, anomalies)

                        i = end_idx
                        continue
            i += 1

        summary = {
            "total_composables": len(composables),
            "total_event_flows": len(event_flows),
            "total_anomalies": len(anomalies),
            "anomaly_breakdown": {k.value: sum(1 for a in anomalies if a.kind == k) for k in ComposeAnomalyKind},
        }

        return ComposeIntelligenceReport(
            composables=composables,
            event_flows=event_flows,
            anomalies=anomalies,
            summary=summary,
        )

    def analyze_file(self, file_path: Union[str, Path]) -> ComposeIntelligenceReport:
        path = Path(file_path).resolve()
        if not path.exists() or path.suffix != ".kt":
            return ComposeIntelligenceReport()
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
            return self.analyze_code(content, file_path=str(path))
        except Exception as e:
            logger.debug(f"Failed analyzing Compose file {path}: {e}")
            return ComposeIntelligenceReport()

    def analyze_project(self, project_path: Union[str, Path]) -> ComposeIntelligenceReport:
        p_path = Path(project_path).resolve()
        all_comp: List[ComposableFunction] = []
        all_flows: List[ComposeEventFlow] = []
        all_anomalies: List[ComposeStateAnomaly] = []

        for kt in p_path.rglob("*.kt"):
            if "build" in kt.parts or ".gradle" in kt.parts:
                continue
            rep = self.analyze_file(kt)
            all_comp.extend(rep.composables)
            all_flows.extend(rep.event_flows)
            all_anomalies.extend(rep.anomalies)

        summary = {
            "total_composables": len(all_comp),
            "total_event_flows": len(all_flows),
            "total_anomalies": len(all_anomalies),
            "anomaly_breakdown": {k.value: sum(1 for a in all_anomalies if a.kind == k) for k in ComposeAnomalyKind},
        }

        return ComposeIntelligenceReport(
            composables=all_comp,
            event_flows=all_flows,
            anomalies=all_anomalies,
            summary=summary,
        )

    # -------------------------------------------------------------------------
    # Internal Parsers
    # -------------------------------------------------------------------------

    def _parse_parameters(self, raw: str) -> List[Dict[str, str]]:
        params = []
        if not raw.strip():
            return params
        for item in raw.split(","):
            parts = item.split(":")
            if len(parts) >= 2:
                name = parts[0].strip()
                t_parts = parts[1].split("=")
                t_name = t_parts[0].strip()
                default_val = t_parts[1].strip() if len(t_parts) > 1 else ""
                params.append({"name": name, "type": t_name, "default": default_val})
        return params

    def _extract_block(self, lines: List[str], start_line_idx: int) -> Tuple[List[str], int]:
        block: List[str] = []
        depth = 0
        found_open = False

        for idx in range(start_line_idx, len(lines)):
            line = lines[idx]
            block.append(line)
            depth += line.count("{") - line.count("}")
            if "{" in line:
                found_open = True
            if found_open and depth <= 0:
                return block, idx

        return block, len(lines)

    def _analyze_composable_body(
        self,
        fn_name: str,
        file_path: str,
        start_line: int,
        params: List[Dict[str, str]],
        body_lines: List[str],
    ) -> ComposableFunction:
        state_holders: List[Dict[str, Any]] = []
        side_effects: List[str] = []
        callbacks: List[str] = []
        viewmodel_refs: List[str] = []

        # Check parameter state hoisting (e.g. onXChange: () -> Unit)
        is_hoisted = any("->" in p.get("type", "") for p in params)

        full_body = "\n".join(body_lines)

        # Detect State holding variables
        for line_no, line in enumerate(body_lines, start=start_line):
            t = line.strip()
            # var x by remember { mutableStateOf(...) } or val x = rememberSaveable { ... }
            m_state = re.search(r"\b(val|var)\s+([a-zA-Z0-9_]+)\s*(?:by|=)\s*(remember|rememberSaveable)\s*\{?\s*(mutableStateOf|derivedStateOf)?", t)
            if m_state:
                mut = m_state.group(1)
                s_name = m_state.group(2)
                r_kind = m_state.group(3)
                prim = m_state.group(4) or r_kind
                state_holders.append({
                    "name": s_name,
                    "kind": prim,
                    "wrapper": r_kind,
                    "mutable": (mut == "var" or prim == "mutableStateOf"),
                    "line": line_no,
                })

            # collectAsState / collectAsStateWithLifecycle
            m_flow = re.search(r"\b([a-zA-Z0-9_]+)\s*(?:by|=)\s*([a-zA-Z0-9_\.]+)\.(collectAsState|collectAsStateWithLifecycle)\(", t)
            if m_flow:
                s_name = m_flow.group(1)
                prim = m_flow.group(3)
                state_holders.append({
                    "name": s_name,
                    "kind": prim,
                    "wrapper": "flow",
                    "mutable": False,
                    "line": line_no,
                })

            # Side effects
            for se in self.SIDE_EFFECTS:
                if re.search(rf"\b{se}\b", t):
                    if se not in side_effects:
                        side_effects.append(se)

            # ViewModel references
            m_vm = re.search(r"\b([a-zA-Z0-9_]+ViewModel)\b", t)
            if m_vm:
                vm_name = m_vm.group(1)
                if vm_name not in viewmodel_refs:
                    viewmodel_refs.append(vm_name)

            # Callbacks (onClick = { ... }, onValueChange = ...)
            m_cb = re.search(r"\b(on[A-Z][a-zA-Z0-9_]*)\s*=\s*\{([^}]*)\}", t)
            if m_cb:
                cb_name = m_cb.group(1)
                callbacks.append(cb_name)

        return ComposableFunction(
            name=fn_name,
            file_path=file_path,
            line=start_line,
            parameters=params,
            state_holders=state_holders,
            side_effects=side_effects,
            callbacks=callbacks,
            viewmodel_refs=viewmodel_refs,
            is_hoisted=is_hoisted,
        )

    def _analyze_flows_and_anomalies(
        self,
        comp: ComposableFunction,
        body_lines: List[str],
        event_flows: List[ComposeEventFlow],
        anomalies: List[ComposeStateAnomaly],
    ) -> None:
        full_text = "\n".join(body_lines)

        # 1. Check for STATE_NEVER_UPDATED
        for sh in comp.state_holders:
            s_name = sh["name"]
            if sh["mutable"]:
                # Search for mutations like `s_name = ` or `s_name.value = `
                mutations = re.findall(rf"\b{s_name}(?:\.value)?\s*=", full_text)
                # Filter out the initial declaration
                if len(mutations) <= 1:
                    anomalies.append(ComposeStateAnomaly(
                        kind=ComposeAnomalyKind.STATE_NEVER_UPDATED,
                        status=ComposePatternStatus.STRONGLY_SUPPORTED,
                        composable_name=comp.name,
                        state_or_handler_name=s_name,
                        description=f"State variable '{s_name}' is declared mutable via {sh['kind']} but is never mutated in '{comp.name}'.",
                        file_path=comp.file_path,
                        line_number=sh["line"],
                        suggested_fix=f"Mutate '{s_name}' inside an event callback or use val/immutable state.",
                    ))

        # 2. Check for CALLBACK_DISCONNECTED (empty `{}` or commented out handler)
        for line_no, line in enumerate(body_lines, start=comp.line):
            # onClick = {} or onClick = { /* TODO */ }
            m_empty_cb = re.search(r"\b(onClick|onValueChange|onConfirm)\s*=\s*\{\s*(?://.*|/\*.*?\*/)?\s*\}", line)
            if m_empty_cb:
                cb = m_empty_cb.group(1)
                anomalies.append(ComposeStateAnomaly(
                    kind=ComposeAnomalyKind.CALLBACK_DISCONNECTED,
                    status=ComposePatternStatus.OBSERVED,
                    composable_name=comp.name,
                    state_or_handler_name=cb,
                    description=f"Interaction handler '{cb}' in '{comp.name}' is empty or disconnected.",
                    file_path=comp.file_path,
                    line_number=line_no,
                    suggested_fix=f"Wire '{cb}' to an action handler, state mutation, or ViewModel call.",
                ))

        # 3. Check for MISSING_LIFECYCLE_AWARE_COLLECTION
        for sh in comp.state_holders:
            if sh["kind"] == "collectAsState":
                anomalies.append(ComposeStateAnomaly(
                    kind=ComposeAnomalyKind.MISSING_LIFECYCLE_AWARE_COLLECTION,
                    status=ComposePatternStatus.STRONGLY_SUPPORTED,
                    composable_name=comp.name,
                    state_or_handler_name=sh["name"],
                    description=f"Flow state '{sh['name']}' collected with 'collectAsState()' instead of lifecycle-aware 'collectAsStateWithLifecycle()'.",
                    file_path=comp.file_path,
                    line_number=sh["line"],
                    suggested_fix=f"Replace '{sh['name']}.collectAsState()' with '{sh['name']}.collectAsStateWithLifecycle()'.",
                ))

        # 4. Pattern Detection: UI event -> callback -> state mutation -> recomposition
        for sh in comp.state_holders:
            s_name = sh["name"]
            # Look for button/click with mutation of this state
            for line in body_lines:
                if re.search(rf"\b(Button|IconButton|TextButton|clickable)\b", line):
                    if re.search(rf"\b{s_name}\b", full_text):
                        event_flows.append(ComposeEventFlow(
                            composable_name=comp.name,
                            trigger_event="UI Event (Click/Input)",
                            callback_target=f"{comp.name}.onClick",
                            state_mutated=s_name,
                            causes_recomposition=True,
                            flow_pattern="UI_EVENT -> CALLBACK -> STATE_MUTATION -> RECOMPOSITION",
                            status=ComposePatternStatus.OBSERVED,
                        ))
                        break

        # Pattern Detection: UI event -> callback -> ViewModel -> state -> UI
        if comp.viewmodel_refs:
            for vm in comp.viewmodel_refs:
                event_flows.append(ComposeEventFlow(
                    composable_name=comp.name,
                    trigger_event="UI Event (User Action)",
                    callback_target=f"{vm}.onAction",
                    state_mutated="ViewModel StateFlow",
                    causes_recomposition=True,
                    flow_pattern="UI_EVENT -> CALLBACK -> VIEWMODEL -> STATE -> UI",
                    status=ComposePatternStatus.OBSERVED,
                ))
