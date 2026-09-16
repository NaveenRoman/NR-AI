r"""
NR-AI Unreal Engine Project & Asset Intelligence Engine (Step 9 Phase 1).

Safe, bounded, deterministic inspection of Unreal Engine projects (.uproject),
plugins (.uplugin), Source tree (C++, Build.cs, Target.cs, UCLASS macros),
Config files (.ini), and Content assets (.uasset, .umap).
"""

from dataclasses import dataclass, field
import hashlib
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from app.agent.unreal_safety import (
    UnrealSafetyGate,
    UnrealErrorCode,
    UnrealSafetyError,
    ALLOWED_UNREAL_EXTENSIONS,
    MAX_READ_LINES,
    MAX_READ_BYTES,
    redact_sensitive_data,
    DEFAULT_UNREAL_SAFETY_GATE,
)
from app.agent.unreal_environment import (
    UnrealEnvironmentDetector,
    UnrealEngineInstance,
    DEFAULT_UNREAL_ENV_DETECTOR,
)

logger = logging.getLogger("NRAI.UnrealProject")


# -----------------------------------------------------------------------------
# Data Models
# -----------------------------------------------------------------------------

@dataclass
class UnrealModuleInfo:
    """Represents a module declared in a .uproject or .uplugin."""
    name: str
    type: str = "Runtime"
    loading_phase: str = "Default"
    build_cs_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "loading_phase": self.loading_phase,
            "build_cs_path": self.build_cs_path,
        }


@dataclass
class UnrealPluginInfo:
    """Represents a plugin declared in a project or found in Plugins/."""
    name: str
    enabled: bool = True
    marketplace_url: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    plugin_file_path: Optional[str] = None
    modules: List[UnrealModuleInfo] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "marketplace_url": self.marketplace_url,
            "description": self.description,
            "category": self.category,
            "plugin_file_path": self.plugin_file_path,
            "modules": [m.to_dict() for m in self.modules],
        }


@dataclass
class UnrealClassInfo:
    """Represents a C++ class identified in an Unreal project Source tree."""
    name: str
    parent: Optional[str] = None
    header_file: str = ""
    macros: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "parent": self.parent,
            "header_file": self.header_file,
            "macros": self.macros,
        }


@dataclass
class UnrealAssetInfo:
    """Represents an asset in Content/ or subdirectories."""
    name: str
    relative_path: str
    absolute_path: str
    asset_type: str  # Map, Asset, Other
    size_bytes: int
    sha256: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "relative_path": self.relative_path,
            "absolute_path": self.absolute_path,
            "asset_type": self.asset_type,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


@dataclass
class UnrealProjectMetadata:
    """Comprehensive inspection report for an Unreal project."""
    name: str
    path: str
    uproject_path: Optional[str] = None
    engine_association: str = ""
    is_cpp: bool = False
    is_blueprint: bool = True
    modules: List[UnrealModuleInfo] = field(default_factory=list)
    plugins: List[UnrealPluginInfo] = field(default_factory=list)
    classes: List[UnrealClassInfo] = field(default_factory=list)
    target_cs_files: List[str] = field(default_factory=list)
    build_cs_files: List[str] = field(default_factory=list)
    source_files_count: int = 0
    header_files_count: int = 0
    assets_count: int = 0
    maps_count: int = 0
    config_files: List[str] = field(default_factory=list)
    is_valid: bool = True
    validation_errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "uproject_path": self.uproject_path,
            "engine_association": self.engine_association,
            "is_cpp": self.is_cpp,
            "is_blueprint": self.is_blueprint,
            "modules": [m.to_dict() for m in self.modules],
            "plugins": [p.to_dict() for p in self.plugins],
            "classes": [c.to_dict() for c in self.classes],
            "target_cs_files": self.target_cs_files,
            "build_cs_files": self.build_cs_files,
            "source_files_count": self.source_files_count,
            "header_files_count": self.header_files_count,
            "assets_count": self.assets_count,
            "maps_count": self.maps_count,
            "config_files": self.config_files,
            "is_valid": self.is_valid,
            "validation_errors": self.validation_errors,
        }


# -----------------------------------------------------------------------------
# Inspector Class
# -----------------------------------------------------------------------------

class UnrealProjectInspector:
    """
    Provides safe, bounded, deterministic inspection of Unreal Engine projects.
    Never executes arbitrary project binaries or code.
    """

    def __init__(
        self,
        safety_gate: Optional[UnrealSafetyGate] = None,
        env_detector: Optional[UnrealEnvironmentDetector] = None,
    ):
        self.safety = safety_gate or DEFAULT_UNREAL_SAFETY_GATE
        self.env = env_detector or DEFAULT_UNREAL_ENV_DETECTOR

    def inspect_project(self, project_path: str | Path) -> UnrealProjectMetadata:
        """
        Performs a full structured inspection of an authorized Unreal Engine project.
        """
        proj = self.safety.validate_project_path(project_path)

        if not proj.is_dir():
            raise UnrealSafetyError(
                UnrealErrorCode.PROJECT_INVALID,
                f"Unreal project path '{proj}' is not a valid directory.",
                {"path": str(proj)},
            )

        uproject_files = list(proj.glob("*.uproject"))
        if not uproject_files:
            return UnrealProjectMetadata(
                name=proj.name,
                path=str(proj),
                is_valid=False,
                validation_errors=["No .uproject file found in project root."],
            )

        uproject_file = uproject_files[0]
        project_name = uproject_file.stem

        # Parse .uproject
        uproj_result = self.parse_uproject(uproject_file)
        if not uproj_result.get("is_valid", False):
            return UnrealProjectMetadata(
                name=project_name,
                path=str(proj),
                uproject_path=str(uproject_file),
                is_valid=False,
                validation_errors=[uproj_result.get("error", "Malformed .uproject JSON")],
            )

        engine_assoc = uproj_result.get("EngineAssociation", "")
        declared_modules_data = uproj_result.get("Modules", [])
        declared_plugins_data = uproj_result.get("Plugins", [])

        # Inspect Source directory
        source_dir = proj / "Source"
        has_source = source_dir.is_dir()
        target_cs_files: List[str] = []
        build_cs_files: List[str] = []
        classes_found: List[UnrealClassInfo] = []
        cpp_count = 0
        h_count = 0

        if has_source:
            for f in source_dir.rglob("*"):
                if f.is_file():
                    name_lower = f.name.lower()
                    rel_str = str(f.relative_to(proj)).replace("\\", "/")
                    if name_lower.endswith(".target.cs"):
                        target_cs_files.append(rel_str)
                    elif name_lower.endswith(".build.cs"):
                        build_cs_files.append(rel_str)
                    elif name_lower.endswith(".cpp") or name_lower.endswith(".c"):
                        cpp_count += 1
                    elif name_lower.endswith(".h") or name_lower.endswith(".hpp"):
                        h_count += 1
                        # Extract classes and reflection macros
                        cls_list = self._extract_classes_from_header(f, proj)
                        classes_found.extend(cls_list)

        is_cpp = has_source and (cpp_count > 0 or len(build_cs_files) > 0)
        is_blueprint = not is_cpp

        # Build module models
        modules: List[UnrealModuleInfo] = []
        for mod_dict in declared_modules_data:
            if isinstance(mod_dict, dict) and "Name" in mod_dict:
                mod_name = mod_dict["Name"]
                # Match corresponding Build.cs
                matched_build_cs = next(
                    (b for b in build_cs_files if Path(b).stem.lower() == f"{mod_name.lower()}.build"),
                    None
                )
                modules.append(UnrealModuleInfo(
                    name=mod_name,
                    type=mod_dict.get("Type", "Runtime"),
                    loading_phase=mod_dict.get("LoadingPhase", "Default"),
                    build_cs_path=matched_build_cs,
                ))

        # Inspect Plugins directory (.uplugin discovery)
        plugins: List[UnrealPluginInfo] = []
        plugins_dir = proj / "Plugins"
        if plugins_dir.is_dir():
            for uplugin_file in plugins_dir.rglob("*.uplugin"):
                p_res = self.parse_uplugin(uplugin_file)
                if p_res.get("is_valid", False):
                    p_modules: List[UnrealModuleInfo] = [
                        UnrealModuleInfo(
                            name=m.get("Name", ""),
                            type=m.get("Type", "Runtime"),
                            loading_phase=m.get("LoadingPhase", "Default"),
                        )
                        for m in p_res.get("Modules", [])
                        if isinstance(m, dict) and "Name" in m
                    ]
                    plugins.append(UnrealPluginInfo(
                        name=p_res.get("FriendlyName") or uplugin_file.stem,
                        enabled=True,
                        marketplace_url=p_res.get("MarketplaceURL"),
                        description=p_res.get("Description"),
                        category=p_res.get("Category"),
                        plugin_file_path=str(uplugin_file.relative_to(proj)).replace("\\", "/"),
                        modules=p_modules,
                    ))

        # Add declared plugins from uproject that might not have local source
        for pl_decl in declared_plugins_data:
            if isinstance(pl_decl, dict) and "Name" in pl_decl:
                pl_name = pl_decl["Name"]
                if not any(p.name.lower() == pl_name.lower() for p in plugins):
                    plugins.append(UnrealPluginInfo(
                        name=pl_name,
                        enabled=pl_decl.get("Enabled", True),
                        marketplace_url=pl_decl.get("MarketplaceURL"),
                    ))

        # Inspect Config directory
        config_files: List[str] = []
        config_dir = proj / "Config"
        if config_dir.is_dir():
            for f in config_dir.glob("*.ini"):
                config_files.append(str(f.relative_to(proj)).replace("\\", "/"))

        # Inspect Content directory (Assets)
        assets = self.list_assets(proj)
        maps_count = sum(1 for a in assets if a.asset_type == "Map")

        return UnrealProjectMetadata(
            name=project_name,
            path=str(proj),
            uproject_path=str(uproject_file.relative_to(proj)).replace("\\", "/"),
            engine_association=str(engine_assoc),
            is_cpp=is_cpp,
            is_blueprint=is_blueprint,
            modules=modules,
            plugins=plugins,
            classes=classes_found,
            target_cs_files=target_cs_files,
            build_cs_files=build_cs_files,
            source_files_count=cpp_count,
            header_files_count=h_count,
            assets_count=len(assets),
            maps_count=maps_count,
            config_files=config_files,
            is_valid=True,
            validation_errors=[],
        )

    def parse_uproject(self, uproject_path: str | Path) -> Dict[str, Any]:
        """
        Safely parses a .uproject file. Returns parsed dictionary with 'is_valid' flag.
        """
        p = Path(uproject_path).resolve()
        if not p.is_file():
            return {"is_valid": False, "error": f".uproject file not found at '{p}'"}

        try:
            txt = p.read_text(encoding="utf-8", errors="ignore")
            data = json.loads(txt)
            if not isinstance(data, dict):
                return {"is_valid": False, "error": ".uproject root must be a JSON object"}
            data["is_valid"] = True
            return data
        except Exception as e:
            return {"is_valid": False, "error": f"JSON syntax error in .uproject: {e}"}

    def parse_uplugin(self, uplugin_path: str | Path) -> Dict[str, Any]:
        """
        Safely parses a .uplugin file. Returns parsed dictionary with 'is_valid' flag.
        """
        p = Path(uplugin_path).resolve()
        if not p.is_file():
            return {"is_valid": False, "error": f".uplugin file not found at '{p}'"}

        try:
            txt = p.read_text(encoding="utf-8", errors="ignore")
            data = json.loads(txt)
            if not isinstance(data, dict):
                return {"is_valid": False, "error": ".uplugin root must be a JSON object"}
            data["is_valid"] = True
            return data
        except Exception as e:
            return {"is_valid": False, "error": f"JSON syntax error in .uplugin: {e}"}

    def _extract_classes_from_header(self, header_file: Path, project_root: Path) -> List[UnrealClassInfo]:
        """Extracts C++ class declarations, parent classes, and reflection macros from a header."""
        results: List[UnrealClassInfo] = []
        try:
            txt = header_file.read_text(encoding="utf-8", errors="ignore")
            rel_header = str(header_file.relative_to(project_root)).replace("\\", "/")

            # Reflection macros present in file
            found_macros = []
            for macro in ["UCLASS", "USTRUCT", "UENUM", "UFUNCTION", "UPROPERTY", "GENERATED_BODY"]:
                if macro in txt:
                    found_macros.append(macro)

            # Match class declarations: class [API_MACRO] ClassName : public ParentClass
            pattern = re.compile(
                r"class\s+(?:[A-Z0-9_]+_API\s+)?([A-Za-z0-9_]+)(?:\s*:\s*(?:public|protected|private)\s+([A-Za-z0-9_]+))?",
                re.MULTILINE,
            )
            for m in pattern.finditer(txt):
                cls_name = m.group(1)
                parent_name = m.group(2)
                # Ignore forward declarations (e.g. class FSomething;)
                end_pos = m.end()
                next_char = txt[end_pos:].strip()
                if next_char and next_char[0] == ";":
                    continue
                results.append(UnrealClassInfo(
                    name=cls_name,
                    parent=parent_name,
                    header_file=rel_header,
                    macros=found_macros,
                ))
        except Exception as e:
            logger.debug(f"Failed to extract classes from {header_file}: {e}")
        return results

    def list_assets(self, project_path: str | Path) -> List[UnrealAssetInfo]:
        """
        Discovers all assets in the project's Content/ directory and computes digests.
        """
        proj = self.safety.validate_project_path(project_path)
        content_dir = proj / "Content"
        if not content_dir.is_dir():
            return []

        assets: List[UnrealAssetInfo] = []
        for f in content_dir.rglob("*"):
            if f.is_file():
                name_lower = f.name.lower()
                if name_lower.endswith(".uasset") or name_lower.endswith(".umap"):
                    asset_type = "Map" if name_lower.endswith(".umap") else "Asset"
                    rel_path = str(f.relative_to(proj)).replace("\\", "/")
                    size = f.stat().st_size
                    # Compute SHA-256
                    try:
                        h = hashlib.sha256()
                        with open(f, "rb") as af:
                            for chunk in iter(lambda: af.read(65536), b""):
                                h.update(chunk)
                        digest = h.hexdigest()
                    except Exception:
                        digest = None

                    assets.append(UnrealAssetInfo(
                        name=f.stem,
                        relative_path=rel_path,
                        absolute_path=str(f.resolve()),
                        asset_type=asset_type,
                        size_bytes=size,
                        sha256=digest,
                    ))

        return sorted(assets, key=lambda a: a.relative_path)

    def read_source_file(
        self,
        file_path: str | Path,
        project_root: Optional[Path] = None,
        max_lines: int = MAX_READ_LINES,
    ) -> Dict[str, Any]:
        """
        Safely and boundedly reads an Unreal C++, C#, ini, or metadata file with SHA-256 digest.
        """
        f = self.safety.validate_file_path(file_path, project_root=project_root)

        if not f.is_file():
            raise UnrealSafetyError(
                UnrealErrorCode.SOURCE_NOT_FOUND,
                f"Target source file '{f}' does not exist.",
                {"file": str(f)},
            )

        size = f.stat().st_size
        if size > MAX_READ_BYTES:
            raise UnrealSafetyError(
                UnrealErrorCode.FILE_TOO_LARGE,
                f"File '{f.name}' size ({size} bytes) exceeds limit ({MAX_READ_BYTES} bytes).",
                {"file": str(f), "size": size, "limit": MAX_READ_BYTES},
            )

        try:
            lines = []
            h = hashlib.sha256()
            with open(f, "r", encoding="utf-8", errors="replace") as sf:
                for idx, line in enumerate(sf):
                    h.update(line.encode("utf-8"))
                    if idx < max_lines:
                        lines.append(line)

            content = "".join(lines)
            redacted_content = redact_sensitive_data(content)
            sha256_hash = h.hexdigest()

            return {
                "file_path": str(f),
                "relative_path": str(f.relative_to(project_root)).replace("\\", "/") if project_root else f.name,
                "lines_read": len(lines),
                "total_size_bytes": size,
                "sha256": sha256_hash,
                "content": redacted_content,
            }
        except Exception as e:
            if isinstance(e, UnrealSafetyError):
                raise e
            raise UnrealSafetyError(
                UnrealErrorCode.SOURCE_NOT_FOUND,
                f"Failed to read file '{f}': {e}",
                {"file": str(f), "error": str(e)},
            )

    def validate_engine_association(
        self,
        project_path: str | Path,
        installed_engines: Optional[List[UnrealEngineInstance]] = None,
    ) -> Dict[str, Any]:
        """
        Validates the project's EngineAssociation against installed Unreal Engine versions.
        """
        proj = self.safety.validate_project_path(project_path)
        uproject_files = list(proj.glob("*.uproject"))
        if not uproject_files:
            return {
                "is_valid": False,
                "engine_association": None,
                "status": "MISSING_UPROJECT",
                "message": "No .uproject file found in project.",
            }

        uproj_data = self.parse_uproject(uproject_files[0])
        if not uproj_data.get("is_valid"):
            return {
                "is_valid": False,
                "engine_association": None,
                "status": "MALFORMED_UPROJECT",
                "message": uproj_data.get("error", "Malformed .uproject"),
            }

        assoc = str(uproj_data.get("EngineAssociation", "")).strip()

        engines = installed_engines
        if engines is None:
            engines = self.env.detect_engines()

        matched_engine = None
        for eng in engines:
            # Match major.minor (e.g. 5.8 or 5.8.1 matches 5.8)
            if assoc == eng.version or assoc == f"{eng.version_major}.{eng.version_minor}":
                matched_engine = eng
                break

        is_matched = matched_engine is not None

        return {
            "is_valid": True,
            "engine_association": assoc,
            "is_matched_with_installed": is_matched,
            "matched_engine_path": matched_engine.engine_path if matched_engine else None,
            "matched_engine_version": matched_engine.version if matched_engine else None,
            "status": "MATCHED" if is_matched else "UNMATCHED_OR_CUSTOM",
            "message": (
                f"EngineAssociation '{assoc}' matches installed Unreal Engine at {matched_engine.engine_path}"
                if is_matched
                else f"EngineAssociation '{assoc}' does not directly match any detected installed engine"
            ),
        }


# Global default inspector
DEFAULT_UNREAL_PROJECT_INSPECTOR = UnrealProjectInspector()
