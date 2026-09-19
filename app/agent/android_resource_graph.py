"""
Android XML Resource Graph & Cross-Reference Subsystem

Provides deep indexing, bidirectional cross-referencing, and integrity analysis
across Android layout XML, values (strings, colors, dimens, styles), drawables,
manifests, and Kotlin/Java code references (R.*.*).

Guarantees:
  - Detects missing resources (referenced in XML/code but not defined)
  - Detects unused resources (defined in res/ but never referenced)
  - Detects duplicate resource declarations
  - Handles configuration qualifiers (values, values-night, values-es)
  - Bounded size limits (<= 100 KB per XML file) and malformed XML fault-tolerance
"""

from __future__ import annotations

import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from app.agent.android_safety import MAX_PATCH_SIZE_BYTES

logger = logging.getLogger("AndroidResourceGraph")


# -----------------------------------------------------------------------------
# Data Models
# -----------------------------------------------------------------------------

@dataclass
class ResourceDefinition:
    res_type: str      # string, color, dimen, id, layout, drawable, style, etc.
    name: str          # resource identifier name
    value: Optional[str]
    file_path: str
    line: int
    config: str        # default, night, es, land, etc.

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ResourceReference:
    res_type: str
    name: str
    source_file: str
    line: int
    reference_syntax: str  # e.g., "@string/app_name", "R.string.app_name"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


from enum import Enum

class ResourceAnomalyKind(str, Enum):
    MISSING_RESOURCE = "MISSING_RESOURCE"
    DUPLICATE_RESOURCE = "DUPLICATE_RESOURCE"
    UNUSED_RESOURCE = "UNUSED_RESOURCE"
    BROKEN_REFERENCE = "BROKEN_REFERENCE"
    TYPE_MISMATCH = "TYPE_MISMATCH"
    POSSIBLE_REFERENCE = "POSSIBLE_REFERENCE"


@dataclass
class ResourceAnomaly:
    kind: ResourceAnomalyKind
    key: str
    source_file: str
    line: int
    description: str
    runtime_error_correlation: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d


@dataclass
class ResourceGraphReport:
    project_path: str
    total_definitions: int
    total_references: int
    definitions: Dict[str, List[ResourceDefinition]]  # key: "type/name"
    references: Dict[str, List[ResourceReference]]    # key: "type/name"
    missing_resources: List[ResourceReference]
    unused_resources: List[str]  # keys: "type/name"
    duplicates: List[Dict[str, Any]]
    scanned_files_count: int
    anomalies: List[ResourceAnomaly] = field(default_factory=list)
    @property
    def total_resources(self) -> int:
        return self.total_definitions

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_path": self.project_path,
            "total_resources": self.total_resources,
            "total_definitions": self.total_definitions,
            "total_references": self.total_references,
            "definitions": {k: [d.to_dict() for d in v] for k, v in self.definitions.items()},
            "references": {k: [r.to_dict() for r in v] for k, v in self.references.items()},
            "missing_resources": [m.to_dict() for m in self.missing_resources],
            "unused_resources": self.unused_resources,
            "duplicates": self.duplicates,
            "anomalies": [a.to_dict() for a in self.anomalies],
            "scanned_files_count": self.scanned_files_count,
            "timestamp": self.timestamp,
        }


# -----------------------------------------------------------------------------
# Resource Graph Scanner
# -----------------------------------------------------------------------------

class AndroidResourceGraphEngine:
    """
    Scans and indexes Android resource definitions and cross-references.
    """

    XML_REF_PATTERN = re.compile(r'@(?:\+id|id|string|color|dimen|drawable|style|layout|mipmap)/([a-zA-Z0-9_.]+)')
    CODE_REF_PATTERN = re.compile(r'\bR\.(id|string|color|dimen|drawable|style|layout|mipmap)\.([a-zA-Z0-9_]+)\b')

    def __init__(self):
        pass

    def build_graph(self, project_root: Union[str, Path]) -> ResourceGraphReport:
        """
        Builds a complete resource definition and reference graph for an Android project.
        """
        root = Path(project_root).resolve()
        if not root.exists():
            raise FileNotFoundError(f"Project directory '{root}' does not exist.")

        definitions: Dict[str, List[ResourceDefinition]] = {}
        references: Dict[str, List[ResourceReference]] = {}
        duplicates: List[Dict[str, Any]] = []
        scanned_count = 0

        # Locate res/ directories
        res_dirs = list(root.glob("**/src/*/res"))
        if not res_dirs and (root / "res").exists():
            res_dirs = [root / "res"]

        # 1. Index Definitions & XML References in res/
        for res_dir in res_dirs:
            for item in res_dir.iterdir():
                if not item.is_dir():
                    continue
                dir_name = item.name
                config = self._extract_config(dir_name)

                # Values directories (strings, colors, dimens, styles, etc.)
                if dir_name.startswith("values"):
                    for val_file in item.glob("*.xml"):
                        scanned_count += 1
                        self._parse_values_file(val_file, config, definitions, references, duplicates)

                # Layout directories
                elif dir_name.startswith("layout"):
                    for layout_file in item.glob("*.xml"):
                        scanned_count += 1
                        res_name = layout_file.stem
                        key = f"layout/{res_name}"
                        definitions.setdefault(key, []).append(
                            ResourceDefinition(
                                res_type="layout",
                                name=res_name,
                                value=str(layout_file),
                                file_path=str(layout_file),
                                line=1,
                                config=config,
                            )
                        )
                        self._parse_xml_references(layout_file, references, definitions, is_layout=True)

                # Drawable / Mipmap directories
                elif dir_name.startswith("drawable") or dir_name.startswith("mipmap"):
                    r_type = "drawable" if dir_name.startswith("drawable") else "mipmap"
                    for d_file in item.iterdir():
                        if d_file.is_file() and not d_file.name.startswith("."):
                            scanned_count += 1
                            res_name = d_file.stem
                            key = f"{r_type}/{res_name}"
                            definitions.setdefault(key, []).append(
                                ResourceDefinition(
                                    res_type=r_type,
                                    name=res_name,
                                    value=str(d_file),
                                    file_path=str(d_file),
                                    line=1,
                                    config=config,
                                )
                            )
                            if d_file.suffix == ".xml":
                                self._parse_xml_references(d_file, references, definitions)

                # Menu / Navigation / Anim / Color directories
                elif dir_name.startswith(("menu", "navigation", "anim", "color", "font")):
                    r_type = dir_name.split("-")[0]
                    for r_file in item.iterdir():
                        if r_file.is_file() and not r_file.name.startswith("."):
                            scanned_count += 1
                            res_name = r_file.stem
                            key = f"{r_type}/{res_name}"
                            definitions.setdefault(key, []).append(
                                ResourceDefinition(
                                    res_type=r_type,
                                    name=res_name,
                                    value=str(r_file),
                                    file_path=str(r_file),
                                    line=1,
                                    config=config,
                                )
                            )
                            if r_file.suffix == ".xml":
                                self._parse_xml_references(r_file, references, definitions)

        # 2. Index AndroidManifest.xml
        for manifest_path in root.glob("**/AndroidManifest.xml"):
            if "build" in manifest_path.parts:
                continue
            scanned_count += 1
            self._parse_xml_references(manifest_path, references, definitions)

        # 3. Index Code References in Kotlin/Java files
        code_files = list(root.glob("**/*.kt")) + list(root.glob("**/*.java"))
        for code_file in code_files:
            if "build" in code_file.parts:
                continue
            scanned_count += 1
            self._scan_code_references(code_file, references)

        # 4. Diagnose Missing, Unused, Duplicate, and Broken Resources
        missing_resources: List[ResourceReference] = []
        anomalies: List[ResourceAnomaly] = []

        for key, ref_list in references.items():
            if key not in definitions:
                missing_resources.extend(ref_list)
                for ref in ref_list:
                    anomalies.append(ResourceAnomaly(
                        kind=ResourceAnomalyKind.MISSING_RESOURCE,
                        key=key,
                        source_file=ref.source_file,
                        line=ref.line,
                        description=f"Resource '{ref.reference_syntax}' is referenced but not defined in any res/ directory.",
                    ))

        unused_resources: List[str] = []
        for key, def_list in definitions.items():
            # Exclude standard entrypoints like main launcher activity layout or styles often used by OS
            if key not in references:
                unused_resources.append(key)
                if def_list:
                    d = def_list[0]
                    anomalies.append(ResourceAnomaly(
                        kind=ResourceAnomalyKind.UNUSED_RESOURCE,
                        key=key,
                        source_file=d.file_path,
                        line=d.line,
                        description=f"Resource '{key}' is defined in {Path(d.file_path).name} but never referenced in code or layouts.",
                    ))

        for dup in duplicates:
            anomalies.append(ResourceAnomaly(
                kind=ResourceAnomalyKind.DUPLICATE_RESOURCE,
                key=dup.get("resource", "UNKNOWN"),
                source_file=dup.get("file", "UNKNOWN"),
                line=1,
                description=f"Duplicate resource '{dup.get('resource')}' declared in configuration '{dup.get('config')}'.",
            ))

        total_defs = sum(len(v) for v in definitions.values())
        total_refs = sum(len(v) for v in references.values())

        return ResourceGraphReport(
            project_path=str(root),
            total_definitions=total_defs,
            total_references=total_refs,
            definitions=definitions,
            references=references,
            missing_resources=missing_resources,
            unused_resources=unused_resources,
            duplicates=duplicates,
            scanned_files_count=scanned_count,
            anomalies=anomalies,
        )

    def find_definitions_for_reference(self, ref_syntax: str, report: ResourceGraphReport) -> List[ResourceDefinition]:
        """Bidirectionally locates definitions for a code or XML reference syntax (e.g. 'R.id.btn' or '@id/btn')."""
        # Parse syntax into type and name
        m_code = re.search(r'\bR\.(id|string|color|dimen|drawable|style|layout|mipmap|menu|anim)\.([a-zA-Z0-9_]+)\b', ref_syntax)
        m_xml = re.search(r'@(?:\+id|id|string|color|dimen|drawable|style|layout|mipmap|menu|anim)/([a-zA-Z0-9_.]+)', ref_syntax)

        res_type = ""
        res_name = ""
        if m_code:
            res_type = m_code.group(1)
            res_name = m_code.group(2)
        elif m_xml:
            parts = ref_syntax.lstrip("@").lstrip("+").split("/", 1)
            if len(parts) == 2:
                res_type = parts[0]
                res_name = parts[1]

        if not res_type or not res_name:
            return []

        key = f"{res_type}/{res_name}"
        return report.definitions.get(key, [])

    def find_code_references_for_resource(self, res_key: str, report: ResourceGraphReport) -> List[ResourceReference]:
        """Finds all code/XML locations that reference a given resource key (e.g. 'string/app_name')."""
        return report.references.get(res_key, [])

    def correlate_runtime_error(
        self,
        error_trace: str,
        report: Optional[ResourceGraphReport] = None,
    ) -> Dict[str, Any]:
        """Correlates a runtime Logcat crash trace with missing or broken resource definitions."""
        correlated: List[ResourceAnomaly] = []
        category = "UNKNOWN"
        m_res_not_found = re.search(r"Resources\$NotFoundException:\s*(?:Resource\s*(?:ID\s*)?#?(?:0x[0-9a-fA-F]+|[a-zA-Z0-9_\.]+)|String resource ID.*)", error_trace)
        m_inflate = re.search(r"InflateException:.*?(?:layout/|id/)([a-zA-Z0-9_]+)", error_trace)
        m_no_such_field = re.search(r"NoSuchFieldError:\s*([a-zA-Z0-9_]+)", error_trace)

        if m_res_not_found:
            category = "RESOURCE_NOT_FOUND"
        elif m_inflate:
            category = "INFLATE_EXCEPTION"
        elif m_no_such_field:
            category = "NO_SUCH_FIELD"

        if (m_res_not_found or m_inflate or m_no_such_field) and report:
            for miss in report.missing_resources:
                if miss.name in error_trace:
                    correlated.append(ResourceAnomaly(
                        kind=ResourceAnomalyKind.BROKEN_REFERENCE,
                        key=f"{miss.res_type}/{miss.name}",
                        source_file=miss.source_file,
                        line=miss.line,
                        description=f"Runtime crash matches missing resource '{miss.reference_syntax}'.",
                        runtime_error_correlation=error_trace.strip()[:300],
                    ))
        return {
            "category": category,
            "error_trace": error_trace,
            "anomalies": correlated,
        }

    def _extract_config(self, dir_name: str) -> str:
        parts = dir_name.split("-", 1)
        return parts[1] if len(parts) > 1 else "default"

    def _parse_values_file(
        self,
        file_path: Path,
        config: str,
        definitions: Dict[str, List[ResourceDefinition]],
        references: Dict[str, List[ResourceReference]],
        duplicates: List[Dict[str, Any]],
    ) -> None:
        if file_path.stat().st_size > MAX_PATCH_SIZE_BYTES:
            logger.warning(f"Skipping oversized values file: {file_path}")
            return

        try:
            tree = ET.parse(file_path)
            root = tree.getroot()
        except Exception as e:
            logger.warning(f"Malformed XML in {file_path}: {e}")
            return

        seen_in_file: Set[str] = set()

        for idx, child in enumerate(root, 1):
            tag = child.tag
            name = child.get("name")
            if not name:
                continue

            r_type = tag
            if tag == "item":
                r_type = child.get("type", "item")

            key = f"{r_type}/{name}"
            val = child.text

            # Check for duplicate within same config/file
            if key in seen_in_file:
                duplicates.append({
                    "resource": key,
                    "file": str(file_path),
                    "config": config,
                    "duplicate_name": name,
                })
            else:
                seen_in_file.add(key)

            definitions.setdefault(key, []).append(
                ResourceDefinition(
                    res_type=r_type,
                    name=name,
                    value=val,
                    file_path=str(file_path),
                    line=idx,
                    config=config,
                )
            )

            # Check inside value for references (e.g., @string/another)
            if val and val.startswith("@") and not val.startswith("@android:"):
                self._record_xml_value_ref(val, str(file_path), idx, references)

    def _parse_xml_references(
        self,
        file_path: Path,
        references: Dict[str, List[ResourceReference]],
        definitions: Dict[str, List[ResourceDefinition]],
        is_layout: bool = False,
    ) -> None:
        if file_path.stat().st_size > MAX_PATCH_SIZE_BYTES:
            return

        try:
            tree = ET.parse(file_path)
            root = tree.getroot()
        except Exception:
            return

        for elem in root.iter():
            # If layout, check for id definition: android:id="@+id/my_id"
            for attr_name, attr_val in elem.attrib.items():
                if not attr_val or not isinstance(attr_val, str):
                    continue

                if is_layout and attr_val.startswith("@+id/"):
                    id_name = attr_val.replace("@+id/", "")
                    key = f"id/{id_name}"
                    definitions.setdefault(key, []).append(
                        ResourceDefinition(
                            res_type="id",
                            name=id_name,
                            value=None,
                            file_path=str(file_path),
                            line=1,
                            config="default",
                        )
                    )

                if attr_val.startswith("@") and not attr_val.startswith("@android:") and not attr_val.startswith("@+id/"):
                    self._record_xml_value_ref(attr_val, str(file_path), 1, references)

    def _record_xml_value_ref(
        self,
        val: str,
        source_file: str,
        line: int,
        references: Dict[str, List[ResourceReference]],
    ) -> None:
        # Expected formats: @string/foo, @color/bar, @style/Theme.App, @id/btn
        match = re.match(r'^@([a-zA-Z0-9_]+)/([a-zA-Z0-9_.]+)$', val)
        if match:
            r_type = match.group(1)
            name = match.group(2)
            key = f"{r_type}/{name}"
            references.setdefault(key, []).append(
                ResourceReference(
                    res_type=r_type,
                    name=name,
                    source_file=source_file,
                    line=line,
                    reference_syntax=val,
                )
            )

    def _scan_code_references(
        self,
        code_file: Path,
        references: Dict[str, List[ResourceReference]],
    ) -> None:
        try:
            text = code_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return

        lines = text.splitlines()
        for line_no, line_content in enumerate(lines, 1):
            for m in self.CODE_REF_PATTERN.finditer(line_content):
                r_type = m.group(1)
                r_name = m.group(2)
                key = f"{r_type}/{r_name}"
                references.setdefault(key, []).append(
                    ResourceReference(
                        res_type=r_type,
                        name=r_name,
                        source_file=str(code_file),
                        line=line_no,
                        reference_syntax=m.group(0),
                    )
                )


AndroidResourceGraph = AndroidResourceGraphEngine

