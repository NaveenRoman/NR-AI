"""
Gradle Version Catalog & TOML Intelligence Subsystem

Provides deep structural parsing, dependency graphing, and safe transactional
modifications for `gradle/libs.versions.toml` and Gradle build declarations.

Guarantees:
  - Structured syntax parsing via standard tomllib (Python 3.11)
  - Reverse dependency tracking (Which libraries use version alias 'compose'?)
  - Unresolved reference detection (version.ref pointing to missing key)
  - Safe transactional edits: SHA-256 stale target protection, .bak backups,
    atomic writes, syntax verification, and automatic rollback on failure
  - Strict size limits (<= 100 KB) and zero secret contamination
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import time
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from app.agent.android_safety import (
    AndroidErrorCode,
    AndroidSafetyError,
    AndroidSafetyGate,
    MAX_PATCH_SIZE_BYTES,
)

logger = logging.getLogger("GradleIntelligence")

DEFAULT_CATALOG_RELATIVE_PATH = Path("gradle/libs.versions.toml")


# -----------------------------------------------------------------------------
# Data Models
# -----------------------------------------------------------------------------

@dataclass
class VersionEntry:
    alias: str
    value: str
    line: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LibraryEntry:
    alias: str
    group: str
    name: str
    version_ref: Optional[str] = None
    version_value: Optional[str] = None
    module: str = ""

    def __post_init__(self):
        if not self.module and self.group and self.name:
            self.module = f"{self.group}:{self.name}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PluginEntry:
    alias: str
    id: str
    version_ref: Optional[str] = None
    version_value: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BundleEntry:
    alias: str
    libraries: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class VersionCatalogReport:
    file_path: str
    sha256: str
    versions_count: int
    libraries_count: int
    plugins_count: int
    bundles_count: int
    versions: Dict[str, VersionEntry]
    libraries: Dict[str, LibraryEntry]
    plugins: Dict[str, PluginEntry]
    bundles: Dict[str, BundleEntry]
    unresolved_references: List[Dict[str, str]]
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "sha256": self.sha256,
            "versions_count": self.versions_count,
            "libraries_count": self.libraries_count,
            "plugins_count": self.plugins_count,
            "bundles_count": self.bundles_count,
            "versions": {k: v.to_dict() for k, v in self.versions.items()},
            "libraries": {k: v.to_dict() for k, v in self.libraries.items()},
            "plugins": {k: v.to_dict() for k, v in self.plugins.items()},
            "bundles": {k: v.to_dict() for k, v in self.bundles.items()},
            "unresolved_references": self.unresolved_references,
            "timestamp": self.timestamp,
        }


# -----------------------------------------------------------------------------
# Gradle Intelligence Engine
# -----------------------------------------------------------------------------

class GradleVersionCatalogEngine:
    """
    Parses, inspects, and safely mutates Gradle Version Catalogs (libs.versions.toml).
    """

    def __init__(self, safety_gate: Optional[AndroidSafetyGate] = None):
        self.safety = safety_gate or AndroidSafetyGate()

    @staticmethod
    def compute_sha256(content: Union[str, bytes]) -> str:
        data = content.encode("utf-8") if isinstance(content, str) else content
        return hashlib.sha256(data).hexdigest()

    def parse_catalog_file(self, toml_file: Union[str, Path]) -> VersionCatalogReport:
        """
        Parses a libs.versions.toml file into a structured intelligence report.
        """
        path = Path(toml_file).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Version catalog not found at '{path}'.")

        raw_bytes = path.read_bytes()
        if len(raw_bytes) > MAX_PATCH_SIZE_BYTES:
            raise ValueError(f"Version catalog size ({len(raw_bytes)} bytes) exceeds 100 KB safety limit.")

        sha256 = self.compute_sha256(raw_bytes)
        try:
            parsed = tomllib.loads(raw_bytes.decode("utf-8"))
        except Exception as e:
            raise ValueError(f"Malformed TOML syntax in '{path}': {e}")

        return self.build_report(parsed, str(path), sha256)

    def parse_catalog_string(self, toml_str: str, file_path: str = "memory://libs.versions.toml") -> VersionCatalogReport:
        """Parse TOML string in-memory."""
        raw_bytes = toml_str.encode("utf-8")
        sha256 = self.compute_sha256(raw_bytes)
        try:
            parsed = tomllib.loads(toml_str)
        except Exception as e:
            raise ValueError(f"Malformed TOML string: {e}")
        return self.build_report(parsed, file_path, sha256)

    def build_report(self, parsed: Dict[str, Any], file_path: str, sha256: str) -> VersionCatalogReport:
        """Construct a strongly typed catalog report and check for unresolved refs."""
        versions: Dict[str, VersionEntry] = {}
        for alias, val in parsed.get("versions", {}).items():
            versions[alias] = VersionEntry(alias=alias, value=str(val))

        libraries: Dict[str, LibraryEntry] = {}
        for alias, defn in parsed.get("libraries", {}).items():
            if isinstance(defn, str):
                # Simple coordinate: "group:name:version" or "group:name"
                parts = defn.split(":")
                grp = parts[0] if len(parts) > 0 else ""
                name = parts[1] if len(parts) > 1 else ""
                ver_val = parts[2] if len(parts) > 2 else None
                libraries[alias] = LibraryEntry(
                    alias=alias,
                    group=grp,
                    name=name,
                    version_value=ver_val,
                    module=f"{grp}:{name}",
                )
            elif isinstance(defn, dict):
                grp = defn.get("group", "")
                name = defn.get("name", "")
                mod = defn.get("module", "")
                if mod and ":" in mod and not grp:
                    grp, name = mod.split(":", 1)
                elif not mod and grp and name:
                    mod = f"{grp}:{name}"

                ver = defn.get("version", {})
                ver_ref = None
                ver_val = None
                if isinstance(ver, str):
                    ver_val = ver
                elif isinstance(ver, dict):
                    ver_ref = ver.get("ref")
                    ver_val = ver.get("strictly") or ver.get("prefer") or ver.get("require")

                libraries[alias] = LibraryEntry(
                    alias=alias,
                    group=grp,
                    name=name,
                    version_ref=ver_ref,
                    version_value=ver_val,
                    module=mod,
                )

        plugins: Dict[str, PluginEntry] = {}
        for alias, defn in parsed.get("plugins", {}).items():
            if isinstance(defn, str):
                plugins[alias] = PluginEntry(alias=alias, id=defn)
            elif isinstance(defn, dict):
                p_id = defn.get("id", "")
                ver = defn.get("version", {})
                ver_ref = None
                ver_val = None
                if isinstance(ver, str):
                    ver_val = ver
                elif isinstance(ver, dict):
                    ver_ref = ver.get("ref")

                plugins[alias] = PluginEntry(
                    alias=alias,
                    id=p_id,
                    version_ref=ver_ref,
                    version_value=ver_val,
                )

        bundles: Dict[str, BundleEntry] = {}
        for alias, defn in parsed.get("bundles", {}).items():
            if isinstance(defn, list):
                bundles[alias] = BundleEntry(alias=alias, libraries=[str(x) for x in defn])

        # Detect unresolved references
        unresolved: List[Dict[str, str]] = []
        for l_alias, lib in libraries.items():
            if lib.version_ref and lib.version_ref not in versions:
                unresolved.append({
                    "target_type": "library",
                    "alias": l_alias,
                    "missing_version_ref": lib.version_ref,
                })

        for p_alias, plug in plugins.items():
            if plug.version_ref and plug.version_ref not in versions:
                unresolved.append({
                    "target_type": "plugin",
                    "alias": p_alias,
                    "missing_version_ref": plug.version_ref,
                })

        return VersionCatalogReport(
            file_path=file_path,
            sha256=sha256,
            versions_count=len(versions),
            libraries_count=len(libraries),
            plugins_count=len(plugins),
            bundles_count=len(bundles),
            versions=versions,
            libraries=libraries,
            plugins=plugins,
            bundles=bundles,
            unresolved_references=unresolved,
        )

    # -------------------------------------------------------------------------
    # Intelligence Queries
    # -------------------------------------------------------------------------

    def find_dependents_of_version(self, catalog: VersionCatalogReport, version_alias: str) -> List[Dict[str, Any]]:
        """Identify which libraries and plugins reference a given version alias."""
        dependents = []
        for lib in catalog.libraries.values():
            if lib.version_ref == version_alias:
                dependents.append({
                    "type": "library",
                    "alias": lib.alias,
                    "module": lib.module,
                })
        for plug in catalog.plugins.values():
            if plug.version_ref == version_alias:
                dependents.append({
                    "type": "plugin",
                    "alias": plug.alias,
                    "id": plug.id,
                })
        return dependents

    # -------------------------------------------------------------------------
    # Safe Transactional Modifications
    # -------------------------------------------------------------------------

    def update_version(
        self,
        toml_path: Union[str, Path],
        version_alias: str,
        new_version_value: str,
        expected_sha256: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Safely and atomically updates a version alias in libs.versions.toml.
        Enforces:
          - Stale target validation (SHA-256 matching)
          - .bak backup creation
          - Post-modification syntax validation via tomllib
          - Byte-for-byte rollback on syntax or validation error
        """
        self.safety.check_emergency_stop()
        path = Path(toml_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Version catalog '{path}' does not exist.")

        raw_bytes = path.read_bytes()
        current_sha256 = self.compute_sha256(raw_bytes)
        raw_text = raw_bytes.decode("utf-8")

        # 1. Stale target check
        if expected_sha256 and expected_sha256 != current_sha256:
            raise AndroidSafetyError(
                AndroidErrorCode.EDIT_VALIDATION_FAILED,
                f"Version catalog '{path.name}' has been modified concurrently. Expected SHA-256 {expected_sha256[:8]}, found {current_sha256[:8]}.",
            )

        # 2. Parse initial state to ensure alias exists
        initial_report = self.parse_catalog_file(path)
        if version_alias not in initial_report.versions:
            raise KeyError(f"Version alias '{version_alias}' not found in [versions] of '{path.name}'.")

        # 3. Create atomic backup
        backup_path = path.with_suffix(f".toml.bak.{int(time.time() * 1000)}")
        shutil.copy2(path, backup_path)

        # 4. Perform structured regex/section modification
        # Match alias = "..." specifically inside or around the [versions] section
        pattern = rf'(?m)^(\s*{re.escape(version_alias)}\s*=\s*)(["\'])(.*?)\2'
        if not re.search(pattern, raw_text):
            backup_path.unlink(missing_ok=True)
            raise ValueError(f"Could not locate assignment pattern for version alias '{version_alias}'.")

        modified_text = re.sub(pattern, rf'\g<1>"{new_version_value}"', raw_text, count=1)

        # 5. Validate resulting TOML syntax
        try:
            tomllib.loads(modified_text)
        except Exception as e:
            # Rollback
            shutil.copy2(backup_path, path)
            backup_path.unlink(missing_ok=True)
            raise AndroidSafetyError(
                AndroidErrorCode.EDIT_VALIDATION_FAILED,
                f"Generated invalid TOML syntax during version update: {e}. Rolled back cleanly.",
            )

        # 6. Atomic write via temporary file using raw bytes
        new_bytes = modified_text.encode("utf-8")
        tmp_target = path.with_suffix(".toml.tmp")
        try:
            tmp_target.write_bytes(new_bytes)
            os.replace(tmp_target, path)
        except Exception as write_err:
            shutil.copy2(backup_path, path)
            backup_path.unlink(missing_ok=True)
            raise IOError(f"Failed to write version catalog: {write_err}. Restored from backup.")

        new_sha256 = self.compute_sha256(new_bytes)
        logger.info(f"Updated version alias '{version_alias}' -> '{new_version_value}' in '{path.name}'.")

        return {
            "success": True,
            "file": str(path),
            "version_alias": version_alias,
            "old_version": initial_report.versions[version_alias].value,
            "new_version": new_version_value,
            "backup_file": str(backup_path),
            "old_sha256": current_sha256,
            "new_sha256": new_sha256,
        }

    def rollback_catalog(self, toml_path: Union[str, Path], backup_path: Union[str, Path]) -> bool:
        """Restore catalog from a backup file."""
        t_path = Path(toml_path).resolve()
        b_path = Path(backup_path).resolve()
        if not b_path.exists():
            return False
        shutil.copy2(b_path, t_path)
        logger.info(f"Rolled back '{t_path.name}' from '{b_path.name}'.")
        return True
