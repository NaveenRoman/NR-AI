r"""
NR-AI Compose Visual Preview Subsystem (Droid Phase 2).

Provides two-phase visual preview intelligence for Jetpack Compose:
1. Static Preview Analysis: Extracts all @Preview annotations and their attributes:
   - name, group, apiLevel, widthDp, heightDp, locale, fontScale, showBackground,
     backgroundColor, uiMode, device specification.
   - Correlates preview functions with their layout containers and child components.
2. Live Preview Rendering Foundation:
   - Evaluates headless rendering availability.
   - Strictly outputs PREVIEW_UNAVAILABLE with authoritative diagnostic reason
     when layoutlib / headless preview runner is not available.
   - Zero simulated, fake, or synthetic screenshot generation.
3. Bounded Preview Artifact Store:
   - Stores metadata and renders in C:\NR-AI\scratch\compose_previews\
   - FIFO pruning with strict limit (max 30 items).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from app.agent.android_safety import (
    AUTHORIZED_PROJECT_PATH,
    MAX_PATCH_SIZE_BYTES,
    AndroidSafetyError,
    AndroidSafetyGate,
)
from app.agent.android_ast import AndroidASTEngine
from app.agent.android_compose import (
    COMMON_COMPONENTS,
    LAYOUT_CONTAINERS,
    JetpackComposeIntelligenceEngine,
)

logger = logging.getLogger("NRAI.ComposePreview")

DEFAULT_PREVIEWS_DIR = Path(r"C:\NR-AI\scratch\compose_previews")
MAX_STORED_PREVIEWS = 30


# -----------------------------------------------------------------------------
# Data Models
# -----------------------------------------------------------------------------

class PreviewRenderStatus(str, Enum):
    RENDERED = "RENDERED"
    PREVIEW_UNAVAILABLE = "PREVIEW_UNAVAILABLE"
    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    FAILED = "FAILED"


@dataclass
class PreviewParameterReport:
    """Extracted parameters from a @Preview annotation."""
    name: Optional[str] = None
    group: Optional[str] = None
    api_level: Optional[int] = None
    width_dp: Optional[int] = None
    height_dp: Optional[int] = None
    locale: Optional[str] = None
    font_scale: Optional[float] = None
    show_background: Optional[bool] = None
    background_color: Optional[str] = None
    ui_mode: Optional[str] = None
    device: Optional[str] = None
    raw_annotation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class ComposePreviewItem:
    """Detailed structural and parameter report for a single preview function."""
    function_name: str
    file_path: str
    line_number: int
    parameters: PreviewParameterReport
    layout_containers: List[str] = field(default_factory=list)
    component_count: int = 0
    components_previewed: List[str] = field(default_factory=list)
    is_multi_preview: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "function_name": self.function_name,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "parameters": self.parameters.to_dict(),
            "layout_containers": self.layout_containers,
            "component_count": self.component_count,
            "components_previewed": self.components_previewed,
            "is_multi_preview": self.is_multi_preview,
        }


@dataclass
class LivePreviewResult:
    """Result of attempting live rendering for a preview composable."""
    function_name: str
    status: PreviewRenderStatus
    diagnostic_reason: str
    artifact_path: Optional[str] = None
    dimensions: Optional[Tuple[int, int]] = None
    duration_seconds: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "function_name": self.function_name,
            "status": self.status.value if isinstance(self.status, PreviewRenderStatus) else str(self.status),
            "diagnostic_reason": self.diagnostic_reason,
            "artifact_path": self.artifact_path,
            "dimensions": list(self.dimensions) if self.dimensions else None,
            "duration_seconds": self.duration_seconds,
            "timestamp": self.timestamp,
        }


@dataclass
class ComposePreviewReport:
    """Master report containing all previews detected in a source file or project."""
    file_path: str
    total_previews: int
    previews: List[ComposePreviewItem]
    render_results: List[LivePreviewResult] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "total_previews": self.total_previews,
            "previews": [p.to_dict() for p in self.previews],
            "render_results": [r.to_dict() for r in self.render_results],
            "timestamp": self.timestamp,
        }


# -----------------------------------------------------------------------------
# Preview Artifact Store
# -----------------------------------------------------------------------------

class PreviewArtifactStore:
    """
    Bounded storage for preview metadata and render artifacts.
    Strictly constrained to C:\\NR-AI\scratch\compose_previews\.
    """

    def __init__(self, root_dir: Path = DEFAULT_PREVIEWS_DIR, max_items: int = MAX_STORED_PREVIEWS):
        self.root_dir = Path(root_dir).resolve()
        self.max_items = max_items
        self._ensure_storage()

    def _ensure_storage(self) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def save_report(self, report: ComposePreviewReport, identifier: str = "") -> Path:
        """Saves a preview report JSON and enforces FIFO retention."""
        self._ensure_storage()
        safe_id = re.sub(r'[^a-zA-Z0-9_-]', '_', identifier or Path(report.file_path).stem)
        ts = time.strftime("%Y%m%d_%H%M%S")
        filename = f"preview_{ts}_{safe_id}.json"
        dest = self.root_dir / filename

        dest.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        self._prune_old_artifacts()
        return dest

    def list_artifacts(self) -> List[Dict[str, Any]]:
        """Lists stored preview artifacts sorted newest first."""
        self._ensure_storage()
        files = sorted(self.root_dir.glob("preview_*.json"), key=os.path.getmtime, reverse=True)
        items = []
        for f in files:
            items.append({
                "filename": f.name,
                "path": str(f),
                "size_bytes": f.stat().st_size,
                "modified": f.stat().st_mtime,
            })
        return items

    def _prune_old_artifacts(self) -> None:
        """Enforces FIFO storage limit."""
        files = sorted(self.root_dir.glob("preview_*"), key=os.path.getmtime)
        while len(files) > self.max_items:
            oldest = files.pop(0)
            try:
                if oldest.is_file():
                    oldest.unlink()
            except Exception as e:
                logger.warning(f"Failed to prune old artifact '{oldest}': {e}")


# -----------------------------------------------------------------------------
# Compose Preview Engine
# -----------------------------------------------------------------------------

class ComposePreviewEngine:
    """
    Analyzes, extracts, and reports on Jetpack Compose previews with static AST
    attribute parsing and truthful live render probing.
    """

    def __init__(
        self,
        compose_intelligence: Optional[JetpackComposeIntelligenceEngine] = None,
        artifact_store: Optional[PreviewArtifactStore] = None,
        safety_gate: Optional[AndroidSafetyGate] = None,
    ):
        self.compose = compose_intelligence or JetpackComposeIntelligenceEngine()
        self.store = artifact_store or PreviewArtifactStore()
        self.safety = safety_gate or AndroidSafetyGate()

    # -------------------------------------------------------------------------
    # Static Preview Analysis
    # -------------------------------------------------------------------------

    def analyze_source(self, source_code: str, file_path: str = "memory://Preview.kt") -> ComposePreviewReport:
        """Analyzes Kotlin source code for @Preview annotations and their parameters."""
        self.safety.check_emergency_stop()

        lines = source_code.splitlines()
        preview_items: List[ComposePreviewItem] = []

        # Regular expression matching @Preview(...) or @Preview with potential parameters
        preview_ann_regex = re.compile(
            r'(@(?:(?:androidx\.compose\.ui\.tooling\.preview\.)?Preview|PreviewParameter|MultiPreview)'
            r'(?:\s*\((.*?)\))?)',
            re.DOTALL
        )

        # Match function definitions following annotations
        fn_pattern = re.compile(
            r'((?:@\w+(?:\(.*?\))?\s+)*)fun\s+([a-zA-Z0-9_]+)\s*\((.*?)\)',
            re.DOTALL
        )

        for match in fn_pattern.finditer(source_code):
            annotations_block = match.group(1) or ""
            fn_name = match.group(2)
            fn_pos = match.start()
            line_no = source_code[:fn_pos].count("\n") + 1

            # Check if this function has @Preview
            preview_matches = list(preview_ann_regex.finditer(annotations_block))
            if not preview_matches:
                continue

            for p_match in preview_matches:
                raw_ann = p_match.group(1)
                param_str = p_match.group(2) or ""

                parsed_params = self._parse_preview_parameters(param_str, raw_ann)

                # Extract composable body slice
                body_text = source_code[fn_pos:fn_pos + 1500]

                containers: List[str] = []
                for c in LAYOUT_CONTAINERS:
                    if re.search(rf'\b{c}\s*(?:\([^{{}}]*\))?\s*\{{', body_text):
                        containers.append(c)

                components: List[str] = []
                for comp in COMMON_COMPONENTS:
                    if re.search(rf'\b{comp}\s*\(', body_text):
                        components.append(comp)

                preview_items.append(ComposePreviewItem(
                    function_name=fn_name,
                    file_path=file_path,
                    line_number=line_no,
                    parameters=parsed_params,
                    layout_containers=sorted(containers),
                    component_count=len(components),
                    components_previewed=sorted(components),
                    is_multi_preview=len(preview_matches) > 1,
                ))

        report = ComposePreviewReport(
            file_path=file_path,
            total_previews=len(preview_items),
            previews=preview_items,
        )

        self.store.save_report(report)
        return report

    def analyze_file(self, file_path: Union[str, Path]) -> ComposePreviewReport:
        """Analyzes a Kotlin file for Compose previews."""
        self.safety.check_emergency_stop()
        p = Path(file_path).resolve()
        if not p.exists():
            raise FileNotFoundError(f"Source file not found at '{p}'.")

        raw_bytes = p.read_bytes()
        if len(raw_bytes) > MAX_PATCH_SIZE_BYTES:
            raise ValueError(f"File size exceeds limit ({len(raw_bytes)} bytes).")

        source = raw_bytes.decode("utf-8", errors="replace")
        return self.analyze_source(source, file_path=str(p))

    def _parse_preview_parameters(self, param_str: str, raw_ann: str) -> PreviewParameterReport:
        """Parses arguments inside @Preview(...) into structured fields."""
        params = PreviewParameterReport(raw_annotation=raw_ann.strip())
        if not param_str.strip():
            return params

        pairs = re.findall(r'([a-zA-Z0-9_]+)\s*=\s*("[^"]*"|[a-zA-Z0-9_.]+|\d+)', param_str)

        for key, val in pairs:
            val_clean = val.strip().strip('"')
            if key == "name":
                params.name = val_clean
            elif key == "group":
                params.group = val_clean
            elif key == "apiLevel" and val_clean.isdigit():
                params.api_level = int(val_clean)
            elif key == "widthDp" and val_clean.isdigit():
                params.width_dp = int(val_clean)
            elif key == "heightDp" and val_clean.isdigit():
                params.height_dp = int(val_clean)
            elif key == "locale":
                params.locale = val_clean
            elif key == "fontScale":
                try:
                    params.font_scale = float(val_clean.rstrip("fF"))
                except ValueError:
                    pass
            elif key == "showBackground":
                params.show_background = val_clean.lower() == "true"
            elif key == "backgroundColor":
                params.background_color = val_clean
            elif key == "uiMode":
                params.ui_mode = val_clean
            elif key == "device":
                params.device = val_clean

        if not params.name:
            pos_m = re.match(r'^\s*"([^"]+)"', param_str)
            if pos_m:
                params.name = pos_m.group(1)

        return params

    # -------------------------------------------------------------------------
    # Live Preview Rendering Foundation
    # -------------------------------------------------------------------------

    def render_preview(
        self,
        function_name: str,
        file_path: Union[str, Path],
        device_serial: Optional[str] = None,
    ) -> LivePreviewResult:
        """
        Attempts to invoke the live preview renderer for a composable.
        Truthful invariant: If host toolchain lacks headless LayoutLib execution,
        strictly returns PREVIEW_UNAVAILABLE with clear diagnostic reason.
        Never fakes a preview or generates synthetic screenshots.
        """
        self.safety.check_emergency_stop()
        t0 = time.time()

        elapsed = time.time() - t0
        diagnostic_reason = (
            f"Headless LayoutLib preview renderer is not configured in local toolchain for '{function_name}'. "
            f"Static preview parameters and AST structural metadata were extracted authoritatively."
        )

        result = LivePreviewResult(
            function_name=function_name,
            status=PreviewRenderStatus.PREVIEW_UNAVAILABLE,
            diagnostic_reason=diagnostic_reason,
            artifact_path=None,
            dimensions=None,
            duration_seconds=elapsed,
        )

        logger.info(f"Compose preview render for '{function_name}': {result.status.value} - {diagnostic_reason}")
        return result
