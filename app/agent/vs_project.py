"""
NR-AI Visual Studio Project & Solution Intelligence Engine (Step 7).

Provides safe, deterministic inspection of Visual Studio solutions (.sln, .slnx),
project files (.csproj, .vbproj, .fsproj), central build files (Directory.Build.props/targets),
and NuGet configurations without arbitrary code execution.
"""

from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union
import xml.etree.ElementTree as ET

from app.agent.vs_safety import (
    VSSafetyGate,
    VSErrorCode,
    VSSafetyError,
    ALLOWED_VS_EXTENSIONS,
    redact_sensitive_data,
)

logger = logging.getLogger("NRAI.VSProject")


@dataclass
class VSProjectMetadata:
    """Structured inspection data for a .NET project."""
    name: str
    path: str
    project_type: str  # CSharp, VisualBasic, FSharp, Unknown
    is_sdk_style: bool = True
    target_frameworks: List[str] = field(default_factory=list)
    output_type: str = "Library"
    root_namespace: Optional[str] = None
    nullable: Optional[str] = None
    lang_version: Optional[str] = None
    package_references: List[Dict[str, str]] = field(default_factory=list)
    project_references: List[str] = field(default_factory=list)
    properties: Dict[str, str] = field(default_factory=dict)
    source_files_count: int = 0
    central_package_management: bool = False

    @property
    def target_framework(self) -> Optional[str]:
        return self.target_frameworks[0] if self.target_frameworks else None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "project_type": self.project_type,
            "is_sdk_style": self.is_sdk_style,
            "target_frameworks": self.target_frameworks,
            "target_framework": self.target_framework,
            "output_type": self.output_type,
            "root_namespace": self.root_namespace,
            "nullable": self.nullable,
            "lang_version": self.lang_version,
            "package_references": self.package_references,
            "project_references": self.project_references,
            "properties": self.properties,
            "source_files_count": self.source_files_count,
            "central_package_management": self.central_package_management,
        }


@dataclass
class VSSolutionMetadata:
    """Structured inspection data for a Visual Studio solution."""
    name: str
    path: str
    format_version: str
    projects: List[Dict[str, str]] = field(default_factory=list)
    configurations: List[str] = field(default_factory=list)
    central_build_props: Optional[str] = None
    central_build_targets: Optional[str] = None
    nuget_config: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "format_version": self.format_version,
            "project_count": len(self.projects),
            "projects": self.projects,
            "configurations": self.configurations,
            "has_central_build_props": bool(self.central_build_props),
            "has_central_build_targets": bool(self.central_build_targets),
            "has_nuget_config": bool(self.nuget_config),
        }


class VSProjectInspector:
    """
    Inspects Visual Studio solutions and project trees safely.
    All read operations are validated through VSSafetyGate.
    """

    def __init__(self, safety_gate: Optional[VSSafetyGate] = None):
        self.safety = safety_gate or VSSafetyGate()

    # -------------------------------------------------------------------------
    # Solution Inspection (.sln, .slnx)
    # -------------------------------------------------------------------------

    def inspect_solution(self, solution_path: Union[str, Path]) -> VSSolutionMetadata:
        """Parses a .sln or .slnx solution file into structured metadata."""
        validated_path = self.safety.validate_file_path(solution_path, check_writable=False)
        if not validated_path.exists():
            raise VSSafetyError(VSErrorCode.FILE_NOT_AUTHORIZED, f"Solution file not found: '{validated_path}'")

        content = validated_path.read_text(encoding="utf-8", errors="replace")

        if validated_path.suffix.lower() == ".slnx":
            return self._parse_slnx(validated_path, content)
        return self._parse_sln(validated_path, content)

    def _parse_sln(self, path: Path, content: str) -> VSSolutionMetadata:
        """Standard .sln line parser."""
        format_ver = "Unknown"
        projects: List[Dict[str, str]] = []
        configurations: Set[str] = set()

        for line in content.splitlines():
            line_str = line.strip()
            if line_str.startswith("Microsoft Visual Studio Solution File"):
                format_ver = line_str
            elif line_str.startswith("Project("):
                # Format: Project("{GUID}") = "Name", "RelativePath", "{GUID}"
                m = re.search(r'Project\("([^"]+)"\)\s*=\s*"([^"]+)",\s*"([^"]+)",\s*"([^"]+)"', line_str)
                if m:
                    type_guid = m.group(1)
                    p_name = m.group(2)
                    p_rel = m.group(3).replace("\\", os.sep)
                    p_guid = m.group(4)
                    full_p = (path.parent / p_rel).resolve()
                    projects.append({
                        "name": p_name,
                        "relative_path": p_rel,
                        "full_path": str(full_p),
                        "project_guid": p_guid,
                        "type_guid": type_guid,
                    })
            elif "Release|" in line_str or "Debug|" in line_str:
                m_cfg = re.search(r'(Debug|Release)\|[A-Za-z0-9\s]+', line_str)
                if m_cfg:
                    configurations.add(m_cfg.group(0).strip())

        # Check directory conventions
        props = path.parent / "Directory.Build.props"
        targets = path.parent / "Directory.Build.targets"
        nuget = path.parent / "nuget.config"

        return VSSolutionMetadata(
            name=path.name,
            path=str(path),
            format_version=format_ver,
            projects=projects,
            configurations=sorted(list(configurations)),
            central_build_props=str(props) if props.exists() else None,
            central_build_targets=str(targets) if targets.exists() else None,
            nuget_config=str(nuget) if nuget.exists() else None,
        )

    def _parse_slnx(self, path: Path, content: str) -> VSSolutionMetadata:
        """Parses modern XML-based .slnx format."""
        projects: List[Dict[str, str]] = []
        configurations: List[str] = ["Debug|Any CPU", "Release|Any CPU"]

        try:
            root = ET.fromstring(content)
            for p_elem in root.findall(".//Project"):
                p_path = p_elem.attrib.get("Path", "")
                if p_path:
                    p_name = Path(p_path).stem
                    full_p = (path.parent / p_path.replace("\\", os.sep)).resolve()
                    projects.append({
                        "name": p_name,
                        "relative_path": p_path,
                        "full_path": str(full_p),
                    })
        except Exception as e:
            logger.warning(f"Error parsing .slnx XML: {e}")

        props = path.parent / "Directory.Build.props"
        targets = path.parent / "Directory.Build.targets"
        nuget = path.parent / "nuget.config"

        return VSSolutionMetadata(
            name=path.name,
            path=str(path),
            format_version="Visual Studio 2022+ .slnx",
            projects=projects,
            configurations=configurations,
            central_build_props=str(props) if props.exists() else None,
            central_build_targets=str(targets) if targets.exists() else None,
            nuget_config=str(nuget) if nuget.exists() else None,
        )

    # -------------------------------------------------------------------------
    # Project File Inspection (.csproj, .vbproj, .fsproj)
    # -------------------------------------------------------------------------

    def inspect_project(self, project_path: Union[str, Path]) -> VSProjectMetadata:
        """Parses project XML file, extracting properties, frameworks, and references."""
        validated = self.safety.validate_file_path(project_path, check_writable=False)
        if not validated.exists():
            raise VSSafetyError(VSErrorCode.FILE_NOT_AUTHORIZED, f"Project file not found: '{validated}'")

        content = validated.read_text(encoding="utf-8", errors="replace")
        ext = validated.suffix.lower()

        type_map = {
            ".csproj": "CSharp",
            ".vbproj": "VisualBasic",
            ".fsproj": "FSharp",
        }
        proj_type = type_map.get(ext, "Unknown")

        try:
            root = ET.fromstring(content)
        except Exception as e:
            raise VSSafetyError(VSErrorCode.CONFIGURATION_NOT_FOUND, f"Malformed project XML in '{validated.name}': {e}")

        # Check SDK style
        is_sdk = "Sdk" in root.attrib or bool(root.findall(".//Sdk"))

        # Extract TargetFrameworks
        tfms: List[str] = []
        output_type = "Library"
        root_ns = None
        nullable = None
        lang_ver = None
        props: Dict[str, str] = {}

        # Search in PropertyGroups
        for pg in root.findall(".//PropertyGroup"):
            for child in pg:
                tag = child.tag.split("}")[-1]  # Strip XML namespaces if any
                text = (child.text or "").strip()
                props[tag] = text

                if tag == "TargetFramework":
                    tfms.append(text)
                elif tag == "TargetFrameworks":
                    tfms.extend([t.strip() for t in text.split(";") if t.strip()])
                elif tag == "OutputType":
                    output_type = text
                elif tag == "RootNamespace":
                    root_ns = text
                elif tag == "Nullable":
                    nullable = text
                elif tag == "LangVersion":
                    lang_ver = text

        # Extract PackageReferences
        pkg_refs: List[Dict[str, str]] = []
        for pkg in root.findall(".//PackageReference"):
            name = pkg.attrib.get("Include") or pkg.attrib.get("Update", "")
            ver = pkg.attrib.get("Version", "")
            if not ver:
                ver_elem = pkg.find("Version")
                if ver_elem is not None:
                    ver = (ver_elem.text or "").strip()
            if name:
                pkg_refs.append({"name": name, "package": name, "version": ver})

        # Extract ProjectReferences
        proj_refs: List[str] = []
        for pref in root.findall(".//ProjectReference"):
            inc = pref.attrib.get("Include", "")
            if inc:
                proj_refs.append(inc.replace("\\", os.sep))

        # Check Central Package Management in parent hierarchy
        cpm = False
        curr = validated.parent
        for _ in range(5):
            props_file = curr / "Directory.Build.props"
            pkgs_file = curr / "Directory.Packages.props"
            for f_cand in (props_file, pkgs_file):
                if f_cand.exists():
                    try:
                        txt = f_cand.read_text(encoding="utf-8", errors="replace")
                        if "ManagePackageVersionsCentrally" in txt and "true" in txt.lower():
                            cpm = True
                            break
                    except Exception:
                        pass
            if cpm or curr == curr.parent or curr == GLOBAL_WORKSPACE_ROOT:
                break
            curr = curr.parent

        # Count source files in project folder
        source_exts = {".cs"} if proj_type == "CSharp" else ({".vb"} if proj_type == "VisualBasic" else {".fs"})
        source_count = 0
        p_dir = validated.parent
        for root_d, dirs, files in os.walk(p_dir):
            # Prune protected directories
            dirs[:] = [d for d in dirs if d.lower() not in ("bin", "obj", ".vs", ".git", "packages")]
            for f in files:
                if Path(f).suffix.lower() in source_exts:
                    source_count += 1

        return VSProjectMetadata(
            name=validated.name,
            path=str(validated),
            project_type=proj_type,
            is_sdk_style=is_sdk,
            target_frameworks=tfms,
            output_type=output_type,
            root_namespace=root_ns,
            nullable=nullable,
            lang_version=lang_ver,
            package_references=pkg_refs,
            project_references=proj_refs,
            properties=props,
            source_files_count=source_count,
            central_package_management=cpm,
        )

    # -------------------------------------------------------------------------
    # Discovery & Search Tools
    # -------------------------------------------------------------------------

    def list_projects(self, root_path: Optional[Union[str, Path]] = None) -> List[Dict[str, Any]]:
        """Finds all solutions and projects inside an authorized directory."""
        validated_root = self.safety.validate_project_path(root_path or self.safety.authorized_project)
        results: List[Dict[str, Any]] = []

        for r, dirs, files in os.walk(validated_root):
            dirs[:] = [d for d in dirs if d.lower() not in ("bin", "obj", ".vs", ".git", "packages", "node_modules")]
            for f in files:
                ext = Path(f).suffix.lower()
                if ext in (".sln", ".slnx", ".csproj", ".vbproj", ".fsproj"):
                    full_p = Path(r) / f
                    results.append({
                        "name": Path(f).stem,
                        "filename": f,
                        "path": str(full_p),
                        "type": "solution" if "sln" in ext else "project",
                        "extension": ext,
                    })

        return sorted(results, key=lambda x: x["path"])

    def find_code(
        self,
        project_path: Union[str, Path],
        pattern: str,
        extension: Optional[str] = None,
        max_results: int = 50,
    ) -> List[Dict[str, Any]]:
        """Searches for regex/literal pattern across source files in a project."""
        validated_dir = self.safety.validate_project_path(project_path)
        if validated_dir.is_file():
            validated_dir = validated_dir.parent

        regex = re.compile(pattern, re.IGNORECASE)
        matches: List[Dict[str, Any]] = []

        allowed_exts = {extension.lower()} if extension else {".cs", ".vb", ".fs", ".xaml", ".xml", ".config", ".json"}

        for r, dirs, files in os.walk(validated_dir):
            dirs[:] = [d for d in dirs if d.lower() not in ("bin", "obj", ".vs", ".git", "packages")]
            for f in files:
                f_path = Path(r) / f
                if f_path.suffix.lower() in allowed_exts:
                    try:
                        content = f_path.read_text(encoding="utf-8", errors="replace")
                        for idx, line in enumerate(content.splitlines(), start=1):
                            if regex.search(line):
                                clean_line = redact_sensitive_data(line.strip())
                                matches.append({
                                    "file": str(f_path),
                                    "line": idx,
                                    "line_number": idx,
                                    "content": clean_line[:300],
                                    "line_content": clean_line[:300],
                                })
                                if len(matches) >= max_results:
                                    return matches
                    except Exception:
                        continue

        return matches

    def read_code(
        self,
        file_path: Union[str, Path],
        start_line: int = 1,
        end_line: Optional[int] = None,
        max_lines: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Reads a bounded range of lines from an authorized file with redaction."""
        validated_file = self.safety.validate_file_path(file_path, check_writable=False)
        if not validated_file.exists():
            raise VSSafetyError(VSErrorCode.FILE_NOT_AUTHORIZED, f"File does not exist: '{validated_file}'")

        lines = validated_file.read_text(encoding="utf-8", errors="replace").splitlines()
        total_lines = len(lines)

        s = max(1, start_line)
        if max_lines is not None:
            e = min(total_lines, s + max_lines - 1)
        elif end_line is not None:
            e = min(total_lines, max(s, end_line))
        else:
            e = min(total_lines, s + 99)

        bounded_lines = lines[s - 1:e]
        truncated = total_lines > e

        annotated = [
            f"{line_num:4d} | {redact_sensitive_data(line)}"
            for line_num, line in enumerate(bounded_lines, start=s)
        ]
        content_str = "\n".join(annotated)
        if truncated:
            content_str += "\n[TRUNCATED: MAX LINES REACHED]"

        return {
            "file_path": str(validated_file),
            "start_line": s,
            "end_line": e,
            "lines_read": len(bounded_lines),
            "total_lines": total_lines,
            "truncated": truncated,
            "lines": bounded_lines,
            "content": content_str,
        }
