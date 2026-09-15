"""
NR-AI Unity Project & Asset Intelligence Engine (Step 8).

Safe, deterministic inspection of Unity projects, Packages/manifest.json,
Assets hierarchy, C# scripts, assembly definitions (.asmdef), and build scenes.
"""

from dataclasses import dataclass, field
import hashlib
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from app.agent.unity_safety import (
    UnitySafetyGate,
    UnityErrorCode,
    UnitySafetyError,
    ALLOWED_UNITY_EXTENSIONS,
    MAX_READ_LINES,
    redact_sensitive_data,
)

logger = logging.getLogger("NRAI.UnityProject")


@dataclass
class UnityAssetInfo:
    """Represents an asset in a Unity project."""
    name: str
    relative_path: str
    absolute_path: str
    asset_type: str  # Script, Scene, Prefab, Material, AsmDef, Asset, Metadata, Other
    size_bytes: int
    has_meta: bool = False
    guid: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "relative_path": self.relative_path,
            "absolute_path": self.absolute_path,
            "asset_type": self.asset_type,
            "size_bytes": self.size_bytes,
            "has_meta": self.has_meta,
            "guid": self.guid,
        }


@dataclass
class UnityProjectMetadata:
    """Comprehensive inspection report for a Unity project."""
    name: str
    path: str
    unity_version: str
    render_pipeline: str = "Built-in"
    packages: List[Dict[str, str]] = field(default_factory=list)
    scenes_in_build: List[Dict[str, Any]] = field(default_factory=list)
    asmdef_files: List[str] = field(default_factory=list)
    csharp_scripts_count: int = 0
    scenes_count: int = 0
    prefabs_count: int = 0
    assets_count: int = 0
    is_valid: bool = True

    @property
    def packages_count(self) -> int:
        return len(self.packages)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "unity_version": self.unity_version,
            "render_pipeline": self.render_pipeline,
            "packages_count": len(self.packages),
            "packages": self.packages,
            "scenes_in_build": self.scenes_in_build,
            "asmdef_files": self.asmdef_files,
            "csharp_scripts_count": self.csharp_scripts_count,
            "scenes_count": self.scenes_count,
            "prefabs_count": self.prefabs_count,
            "assets_count": self.assets_count,
            "is_valid": self.is_valid,
        }


class UnityProjectInspector:
    """
    Provides safe, bounded, deterministic inspection of Unity project structures,
    package manifests, C# scripts, and assets.
    """

    def __init__(self, safety_gate: Optional[UnitySafetyGate] = None):
        self.safety = safety_gate or UnitySafetyGate()

    def is_valid_project(self, project_path: Path) -> bool:
        """Verifies directory is a valid Unity project (has Assets/ and ProjectSettings or Packages)."""
        p = Path(project_path).resolve()
        if not p.is_dir():
            return False
        has_assets = (p / "Assets").is_dir()
        has_settings = (p / "ProjectSettings" / "ProjectVersion.txt").is_file()
        has_packages = (p / "Packages" / "manifest.json").is_file()
        return has_assets and (has_settings or has_packages)

    def get_project_version(self, project_path: Path) -> str:
        """Reads target Unity editor version from ProjectSettings/ProjectVersion.txt."""
        version_file = Path(project_path).resolve() / "ProjectSettings" / "ProjectVersion.txt"
        if not version_file.is_file():
            return "Unknown"
        try:
            content = version_file.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"m_EditorVersion:\s*([^\r\n]+)", content)
            if m:
                return m.group(1).strip()
        except Exception:
            pass
        return "Unknown"

    def inspect_packages(self, project_path: Path) -> List[Dict[str, str]]:
        """Parses Packages/manifest.json dependencies."""
        manifest_file = Path(project_path).resolve() / "Packages" / "manifest.json"
        if not manifest_file.is_file():
            return []
        try:
            data = json.loads(manifest_file.read_text(encoding="utf-8", errors="replace"))
            deps = data.get("dependencies", {})
            return [{"package": k, "version": v} for k, v in deps.items()]
        except Exception:
            return []

    def _detect_render_pipeline(self, packages: List[Dict[str, str]]) -> str:
        """Infers render pipeline from package dependencies."""
        pkg_names = {p["package"].lower() for p in packages}
        if any("universal" in name for name in pkg_names):
            return "Universal Render Pipeline (URP)"
        if any("high-definition" in name or "hdrp" in name for name in pkg_names):
            return "High Definition Render Pipeline (HDRP)"
        return "Built-in Render Pipeline"

    def _classify_asset_type(self, ext: str) -> str:
        """Maps file extension to Unity asset type."""
        e = ext.lower()
        if e == ".cs":
            return "Script"
        elif e == ".unity":
            return "Scene"
        elif e == ".prefab":
            return "Prefab"
        elif e == ".mat":
            return "Material"
        elif e in (".asmdef", ".asmref"):
            return "AsmDef"
        elif e == ".meta":
            return "Metadata"
        elif e in (".asset", ".preset"):
            return "Asset"
        return "Other"

    def _extract_meta_guid(self, meta_file: Path) -> Optional[str]:
        """Extracts GUID from .meta file."""
        if not meta_file.is_file():
            return None
        try:
            content = meta_file.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"guid:\s*([a-fA-F0-9]{32})", content)
            if m:
                return m.group(1)
        except Exception:
            pass
        return None

    def list_assets(
        self,
        project_path: Path,
        asset_type: Optional[str] = None,
        subpath: str = "Assets",
    ) -> List[UnityAssetInfo]:
        """Lists assets under subpath within authorized project."""
        proj = self.safety.validate_path(Path(project_path))
        target_dir = (proj / subpath).resolve()
        self.safety.validate_path(target_dir)

        results: List[UnityAssetInfo] = []
        if not target_dir.is_dir():
            return results

        for root, _, files in os.walk(target_dir):
            r_path = Path(root)
            for f in files:
                f_path = r_path / f
                ext = f_path.suffix.lower()
                if ext == ".meta":
                    continue  # We process meta alongside the main asset

                atype = self._classify_asset_type(ext)
                if asset_type and atype.lower() != asset_type.lower():
                    continue

                meta_path = Path(str(f_path) + ".meta")
                has_m = meta_path.is_file()
                guid = self._extract_meta_guid(meta_path) if has_m else None
                try:
                    rel_p = str(f_path.relative_to(proj)).replace("\\", "/")
                    size = f_path.stat().st_size
                except Exception:
                    rel_p = f
                    size = 0

                results.append(
                    UnityAssetInfo(
                        name=f,
                        relative_path=rel_p,
                        absolute_path=str(f_path),
                        asset_type=atype,
                        size_bytes=size,
                        has_meta=has_m,
                        guid=guid,
                    )
                )

        return results

    def find_scripts(self, project_path: Path, pattern: str = "*.cs") -> List[str]:
        """Finds all C# scripts matching pattern in Assets directory."""
        proj = self.safety.validate_path(Path(project_path))
        assets_dir = proj / "Assets"
        if not assets_dir.is_dir():
            return []

        matched = []
        for p in assets_dir.rglob(pattern):
            if p.is_file() and p.suffix.lower() == ".cs":
                try:
                    rel = str(p.relative_to(proj)).replace("\\", "/")
                    matched.append(rel)
                except Exception:
                    matched.append(p.name)
        return sorted(matched)

    def read_script(self, file_path: Path, max_lines: int = MAX_READ_LINES) -> Dict[str, Any]:
        """
        Reads a C# script safely with line bounding and SHA-256 computation.
        Extracts namespace, class names, and method summaries.
        """
        val_path = self.safety.validate_path(Path(file_path))
        if not val_path.is_file():
            raise UnitySafetyError(
                UnityErrorCode.SCRIPT_NOT_FOUND,
                f"C# script file '{val_path}' does not exist.",
            )

        content = val_path.read_text(encoding="utf-8", errors="replace")
        sha256_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        lines = content.splitlines()
        total_lines = len(lines)
        truncated = total_lines > max_lines
        preview_lines = lines[:max_lines]

        # Extract basic C# structure
        classes = re.findall(r"\b(?:public|internal|private)?\s*(?:class|struct|interface|enum)\s+([A-Za-z0-9_]+)", content)
        namespaces = re.findall(r"\bnamespace\s+([A-Za-z0-9_\.]+)", content)

        return {
            "file_name": val_path.name,
            "path": str(val_path),
            "sha256": sha256_hash,
            "total_lines": total_lines,
            "lines_returned": len(preview_lines),
            "truncated": truncated,
            "content": "\n".join(preview_lines),
            "namespaces": namespaces,
            "classes": classes,
        }

    def inspect_asmdef(self, asmdef_path: Path) -> Dict[str, Any]:
        """Parses a Unity assembly definition (.asmdef) file."""
        val_path = self.safety.validate_path(Path(asmdef_path))
        if not val_path.is_file():
            raise UnitySafetyError(
                UnityErrorCode.FILE_NOT_AUTHORIZED,
                f"AsmDef file '{val_path}' not found.",
            )
        try:
            data = json.loads(val_path.read_text(encoding="utf-8", errors="replace"))
            return {
                "name": data.get("name", val_path.stem),
                "path": str(val_path),
                "references": data.get("references", []),
                "includePlatforms": data.get("includePlatforms", []),
                "excludePlatforms": data.get("excludePlatforms", []),
                "allowUnsafeCode": data.get("allowUnsafeCode", False),
                "autoReferenced": data.get("autoReferenced", True),
                "auto_referenced": data.get("autoReferenced", True),
                "noEngineReferences": data.get("noEngineReferences", False),
            }
        except Exception as e:
            raise UnitySafetyError(
                UnityErrorCode.FILE_NOT_AUTHORIZED,
                f"Failed to parse asmdef JSON '{val_path}': {e}",
            )

    def inspect_scenes_in_build(self, project_path: Path) -> List[Dict[str, Any]]:
        """Parses EditorBuildSettings.asset to find registered scenes, or enumerates Assets/Scenes."""
        proj = self.safety.validate_path(Path(project_path))
        build_settings = proj / "ProjectSettings" / "EditorBuildSettings.asset"

        scenes: List[Dict[str, Any]] = []
        if build_settings.is_file():
            try:
                txt = build_settings.read_text(encoding="utf-8", errors="replace")
                # Pattern: - enabled: 1
                #          path: Assets/Scenes/SampleScene.unity
                matches = re.findall(r"enabled:\s*(\d+)\s*[\r\n]+\s*path:\s*([^\r\n]+)", txt)
                for en_str, s_path in matches:
                    scenes.append({
                        "path": s_path.strip(),
                        "enabled": bool(int(en_str)),
                    })
            except Exception:
                pass

        if not scenes:
            # Fallback: scan Assets for .unity files
            for scene_file in proj.glob("Assets/**/*.unity"):
                try:
                    rel = str(scene_file.relative_to(proj)).replace("\\", "/")
                    scenes.append({"path": rel, "enabled": True})
                except Exception:
                    pass

        return scenes

    def inspect_project(self, project_path: Path) -> UnityProjectMetadata:
        """Comprehensive metadata report for a Unity project."""
        proj = self.safety.validate_path(Path(project_path))
        is_valid = self.is_valid_project(proj)
        ver = self.get_project_version(proj)
        packages = self.inspect_packages(proj)
        pipeline = self._detect_render_pipeline(packages)
        scenes = self.inspect_scenes_in_build(proj)

        # Count assets
        all_assets = self.list_assets(proj)
        scripts_count = sum(1 for a in all_assets if a.asset_type == "Script")
        scenes_count = sum(1 for a in all_assets if a.asset_type == "Scene")
        prefabs_count = sum(1 for a in all_assets if a.asset_type == "Prefab")

        asmdefs = [a.relative_path for a in all_assets if a.asset_type == "AsmDef"]

        return UnityProjectMetadata(
            name=proj.name,
            path=str(proj),
            unity_version=ver,
            render_pipeline=pipeline,
            packages=packages,
            scenes_in_build=scenes,
            asmdef_files=asmdefs,
            csharp_scripts_count=scripts_count,
            scenes_count=scenes_count,
            prefabs_count=prefabs_count,
            assets_count=len(all_assets),
            is_valid=is_valid,
        )


DEFAULT_UNITY_PROJECT_INSPECTOR = UnityProjectInspector()
