"""
Jetpack Compose Intelligence Subsystem

Provides deep structural analysis, component hierarchy extraction, state tracking,
and semantic anti-pattern inference for modern Jetpack Compose code.

Guarantees:
  - Distinguishes STRUCTURAL_DETECTION (exact syntax) from SEMANTIC_INFERENCE (heuristics)
  - Identifies composables, previews, layout containers, UI components, modifiers
  - Tracks state management: remember, rememberSaveable, mutableStateOf, collectAsState
  - Detects common anti-patterns: unremembered mutableStateOf, missing contentDescription,
    hardcoded strings instead of stringResource
  - Bounded size limits (<= 100 KB) and non-destructive analysis
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from app.agent.android_ast import AndroidASTEngine, MethodEntry, SourceASTReport
from app.agent.android_safety import MAX_PATCH_SIZE_BYTES

logger = logging.getLogger("AndroidCompose")

LAYOUT_CONTAINERS = {
    "Column", "Row", "Box", "Scaffold", "LazyColumn", "LazyRow",
    "LazyVerticalGrid", "LazyHorizontalGrid", "Surface", "Card",
}

COMMON_COMPONENTS = {
    "Text", "Button", "OutlinedButton", "ElevatedButton", "TextButton",
    "IconButton", "FloatingActionButton", "TextField", "OutlinedTextField",
    "Image", "Icon", "Spacer", "Divider", "HorizontalDivider", "VerticalDivider",
    "CircularProgressIndicator", "LinearProgressIndicator", "Checkbox", "Switch",
    "RadioButton", "Slider", "TopAppBar", "BottomAppBar", "NavigationBar",
}


# -----------------------------------------------------------------------------
# Data Models
# -----------------------------------------------------------------------------

@dataclass
class ComposeStateEntry:
    name: str
    state_type: str  # mutableStateOf, derivedStateOf, collectAsState, etc.
    has_remember: bool
    is_saveable: bool
    line: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ComposeComponentEntry:
    component_type: str
    raw_snippet: str
    has_modifier: bool
    modifier_calls: List[str]
    has_click: bool
    line: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ComposableFunctionReport:
    name: str
    parameters: List[str]
    annotations: List[str]
    is_preview: bool
    layout_containers: List[str]
    components: List[ComposeComponentEntry]
    state_entries: List[ComposeStateEntry]
    structural_detections: List[str]
    semantic_inferences: List[Dict[str, Any]]
    start_line: int
    end_line: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "parameters": self.parameters,
            "annotations": self.annotations,
            "is_preview": self.is_preview,
            "layout_containers": self.layout_containers,
            "components": [c.to_dict() for c in self.components],
            "state_entries": [s.to_dict() for s in self.state_entries],
            "structural_detections": self.structural_detections,
            "semantic_inferences": self.semantic_inferences,
            "start_line": self.start_line,
            "end_line": self.end_line,
        }


@dataclass
class ComposeIntelligenceReport:
    file_path: str
    total_composables: int
    total_previews: int
    composables: Dict[str, ComposableFunctionReport]
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "total_composables": self.total_composables,
            "total_previews": self.total_previews,
            "composables": {k: v.to_dict() for k, v in self.composables.items()},
            "timestamp": self.timestamp,
        }


# -----------------------------------------------------------------------------
# Compose Intelligence Engine
# -----------------------------------------------------------------------------

class JetpackComposeIntelligenceEngine:
    """
    Analyzes Kotlin source files for Jetpack Compose structures and semantics.
    """

    def __init__(self, ast_engine: Optional[AndroidASTEngine] = None):
        self.ast = ast_engine or AndroidASTEngine()

    def analyze_file(self, file_path: Union[str, Path]) -> ComposeIntelligenceReport:
        path = Path(file_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Source file '{path}' does not exist.")

        raw_bytes = path.read_bytes()
        if len(raw_bytes) > MAX_PATCH_SIZE_BYTES:
            raise ValueError(f"File size ({len(raw_bytes)} bytes) exceeds 100 KB limit.")

        source = raw_bytes.decode("utf-8", errors="replace")
        return self.analyze_source(source, str(path))

    def analyze_source(self, source: str, file_path: str = "memory://Composable.kt") -> ComposeIntelligenceReport:
        ast_report = self.ast.parse_source(source, file_path)
        lines = source.splitlines()

        composables: Dict[str, ComposableFunctionReport] = {}
        total_previews = 0

        # Collect all composable functions: top level + inside classes
        candidates: List[MethodEntry] = [m for m in ast_report.top_level_methods.values() if m.is_composable]
        for cls in ast_report.classes.values():
            for m in cls.methods.values():
                if m.is_composable:
                    candidates.append(m)

        for method in candidates:
            # Extract function body slice
            start_idx = max(0, method.start_line - 1)
            end_idx = min(len(lines), method.end_line)
            fn_lines = lines[start_idx:end_idx]
            fn_text = "\n".join(fn_lines)

            comp_report = self._analyze_composable_body(method, fn_lines, fn_text, method.start_line)
            composables[method.name] = comp_report
            if comp_report.is_preview:
                total_previews += 1

        return ComposeIntelligenceReport(
            file_path=file_path,
            total_composables=len(composables),
            total_previews=total_previews,
            composables=composables,
        )

    def _analyze_composable_body(
        self,
        method: MethodEntry,
        fn_lines: List[str],
        fn_text: str,
        base_line: int,
    ) -> ComposableFunctionReport:
        structural_detections: List[str] = ["@Composable detected"]
        semantic_inferences: List[Dict[str, Any]] = []

        # 1. Preview check
        is_preview = any("@Preview" in a for a in method.annotations)
        if is_preview:
            structural_detections.append("@Preview annotation detected")

        # 2. Containers
        containers_found: Set[str] = set()
        for container in LAYOUT_CONTAINERS:
            # Match Container(...) or Container { handling nested parens like Modifier.fillMaxSize()
            pattern = rf'\b{container}\s*(?:\([^{{}}]*\))?\s*\{{'
            if re.search(pattern, fn_text):
                containers_found.add(container)
                structural_detections.append(f"Layout container: {container}")

        # 3. State Analysis
        state_entries: List[ComposeStateEntry] = []
        state_pattern = re.compile(
            r'val\s+([a-zA-Z0-9_]+)\s*(?:by|=)\s*'
            r'(?:(remember|rememberSaveable)\s*\{)?\s*'
            r'(mutableStateOf|derivedStateOf|collectAsState|collectAsStateWithLifecycle)',
            re.MULTILINE
        )

        for m in state_pattern.finditer(fn_text):
            var_name = m.group(1)
            remember_wrapper = m.group(2)
            state_call = m.group(3)

            has_remember = remember_wrapper is not None
            is_saveable = remember_wrapper == "rememberSaveable"
            line_no = base_line + fn_text[:m.start()].count("\n")

            state_entries.append(ComposeStateEntry(
                name=var_name,
                state_type=state_call,
                has_remember=has_remember,
                is_saveable=is_saveable,
                line=line_no,
            ))

            structural_detections.append(
                f"State holder '{var_name}': {state_call} (remembered={has_remember})"
            )

            # Check anti-pattern: mutableStateOf without remember
            if state_call == "mutableStateOf" and not has_remember:
                semantic_inferences.append({
                    "type": "ANTI_PATTERN_UNREMEMBERED_STATE",
                    "severity": "WARNING",
                    "variable": var_name,
                    "line": line_no,
                    "message": f"State variable '{var_name}' initialized with mutableStateOf() without remember { ... }. It will re-initialize on every recomposition.",
                })

        # 4. Component Analysis
        components: List[ComposeComponentEntry] = []
        for idx, line in enumerate(fn_lines, 1):
            line_no = base_line + idx - 1
            for comp_name in COMMON_COMPONENTS:
                comp_match = re.search(rf'\b{comp_name}\s*\(', line)
                if comp_match:
                    has_modifier = "Modifier" in line or "modifier =" in line
                    has_click = "onClick" in line or ".clickable" in line

                    # Extract modifier chains if present
                    mod_calls = []
                    mod_match = re.search(r'Modifier\.([a-zA-Z0-9_()]+)', line)
                    if mod_match:
                        mod_calls.append(mod_match.group(1))

                    components.append(ComposeComponentEntry(
                        component_type=comp_name,
                        raw_snippet=line.strip(),
                        has_modifier=has_modifier,
                        modifier_calls=mod_calls,
                        has_click=has_click,
                        line=line_no,
                    ))

            # Accessibility Check on Image
            if "Image(" in line:
                if "contentDescription = null" not in line and "contentDescription" not in line:
                    semantic_inferences.append({
                        "type": "ACCESSIBILITY_MISSING_CONTENT_DESCRIPTION",
                        "severity": "WARNING",
                        "line": line_no,
                        "message": "Image composable missing contentDescription attribute.",
                    })

            # Hardcoded String Check on Text
            if "Text(" in line:
                hardcoded_match = re.search(r'Text\(\s*(?:text\s*=\s*)?"([^"]+)"', line)
                if hardcoded_match:
                    literal = hardcoded_match.group(1)
                    semantic_inferences.append({
                        "type": "I18N_HARDCODED_STRING",
                        "severity": "INFO",
                        "line": line_no,
                        "literal": literal,
                        "message": f"Hardcoded string \"{literal}\" in Text(). Consider using stringResource(R.string.xxx).",
                    })

        return ComposableFunctionReport(
            name=method.name,
            parameters=method.parameters,
            annotations=method.annotations,
            is_preview=is_preview,
            layout_containers=sorted(list(containers_found)),
            components=components,
            state_entries=state_entries,
            structural_detections=structural_detections,
            semantic_inferences=semantic_inferences,
            start_line=method.start_line,
            end_line=method.end_line,
        )
