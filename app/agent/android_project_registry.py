"""
Android Project Registry Subsystem

Enables safe, dynamic registration of Android projects while enforcing:
  - Strict path canonicalization and traversal prevention
  - Protected system directory rejection (Windows, Program Files, system roots)
  - Explicit user authorization verification
  - Multi-project isolation
  - Suspension and revocation lifecycle states
  - Default authorization for C:\\NR-AI\\nr_android_test
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

logger = logging.getLogger("AndroidProjectRegistry")

DEFAULT_REGISTRY_PATH = Path(r"C:\NR-AI\data\android_projects.json")
DEFAULT_AUTHORIZED_PROJECT = Path(r"C:\NR-AI\nr_android_test").resolve()

# System drive roots and protected system directories
SYSTEM_ROOTS: Set[Path] = {
    Path(r"C:\\").resolve(),
    Path(r"D:\\").resolve() if Path(r"D:\\").exists() else Path(r"C:\\").resolve(),
}

FORBIDDEN_SYSTEM_DIRS: Set[Path] = {
    Path(os.environ.get("SystemRoot", r"C:\Windows")).resolve(),
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")).resolve(),
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")).resolve(),
    Path(r"C:\ProgramData").resolve(),
    Path(r"C:\Windows").resolve(),
}


class ProjectStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"


class ProjectType(str, Enum):
    APPLICATION = "APPLICATION"
    LIBRARY = "LIBRARY"
    MULTI_MODULE = "MULTI_MODULE"
    UNKNOWN = "UNKNOWN"


@dataclass
class AndroidProjectRecord:
    project_id: str
    project_name: str
    canonical_path: str
    registration_time: float = field(default_factory=time.time)
    project_type: str = ProjectType.APPLICATION.value
    package_id: Optional[str] = None
    gradle_root: Optional[str] = None
    modules: List[str] = field(default_factory=list)
    status: str = ProjectStatus.ACTIVE.value
    authorized_by_user: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AndroidProjectRecord:
        return cls(**data)


class AndroidProjectRegistry:
    """
    Manages safe multi-project allowlisting for Android engineering workflows.
    """

    def __init__(self, registry_file: Optional[Union[str, Path]] = None):
        self.registry_file = Path(registry_file or DEFAULT_REGISTRY_PATH).resolve()
        self._projects: Dict[str, AndroidProjectRecord] = {}

        if str(self.registry_file) != ":memory:":
            self.registry_file.parent.mkdir(parents=True, exist_ok=True)
            self._load()
        else:
            self._seed_default()

    def _seed_default(self) -> None:
        """Seed default authorized project if it exists on disk."""
        if DEFAULT_AUTHORIZED_PROJECT.exists():
            default_id = "nr_android_test"
            if default_id not in self._projects:
                rec = AndroidProjectRecord(
                    project_id=default_id,
                    project_name="NR-AI Android Test Fixture",
                    canonical_path=str(DEFAULT_AUTHORIZED_PROJECT),
                    registration_time=time.time(),
                    project_type=ProjectType.APPLICATION.value,
                    package_id="com.nrai.test",
                    gradle_root=str(DEFAULT_AUTHORIZED_PROJECT),
                    modules=["app"],
                    status=ProjectStatus.ACTIVE.value,
                    authorized_by_user=True,
                )
                self._projects[default_id] = rec

    def _load(self) -> None:
        """Load registry from JSON disk store."""
        if self.registry_file.exists():
            try:
                with open(self.registry_file, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                    for pid, pdata in raw.get("projects", {}).items():
                        self._projects[pid] = AndroidProjectRecord.from_dict(pdata)
            except Exception as e:
                logger.error(f"Failed to load project registry from {self.registry_file}: {e}")

        # Always ensure nr_android_test is present
        self._seed_default()
        self._save()

    def _save(self) -> None:
        """Atomically persist registry to JSON disk store."""
        if ":memory:" in str(self.registry_file):
            return

        tmp_file = self.registry_file.with_suffix(".tmp")
        payload = {
            "version": "1.0",
            "updated_at": time.time(),
            "projects": {pid: p.to_dict() for pid, p in self._projects.items()},
        }
        try:
            with open(tmp_file, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            os.replace(tmp_file, self.registry_file)
        except Exception as e:
            logger.error(f"Failed to save project registry: {e}")
            if tmp_file.exists():
                tmp_file.unlink(missing_ok=True)

    def validate_candidate_path(self, path: Union[str, Path]) -> Tuple[bool, str, Path]:
        """
        Validates whether a directory path is legally registrable.
        Rejects traversal, non-existent paths, root filesystems, and system directories.
        """
        try:
            raw_path = Path(path)
            canonical = raw_path.resolve()
        except Exception as e:
            return False, f"Invalid path syntax: {e}", raw_path

        if not canonical.exists():
            return False, f"Path does not exist: {canonical}", canonical

        if not canonical.is_dir():
            return False, f"Project path must be a directory: {canonical}", canonical

        # Check drive roots
        for root_dir in SYSTEM_ROOTS:
            if canonical == root_dir:
                return False, f"Cannot register root or system directory: {canonical}", canonical

        # Check protected system directories
        for sys_dir in FORBIDDEN_SYSTEM_DIRS:
            if canonical == sys_dir:
                return False, f"Cannot register root or system directory: {canonical}", canonical
            try:
                if canonical.is_relative_to(sys_dir) and not str(canonical).startswith(r"C:\NR-AI"):
                    return False, f"Path is located within protected system directory: {canonical}", canonical
            except AttributeError:
                if str(canonical).startswith(str(sys_dir)) and not str(canonical).startswith(r"C:\NR-AI"):
                    return False, f"Path is located within protected system directory: {canonical}", canonical

        # Verify Android project artifacts
        has_gradle = (canonical / "build.gradle").exists() or (canonical / "build.gradle.kts").exists()
        has_settings = (canonical / "settings.gradle").exists() or (canonical / "settings.gradle.kts").exists()
        has_manifest = any(canonical.glob("**/AndroidManifest.xml"))

        if not (has_gradle or has_settings or has_manifest):
            return False, f"Directory does not appear to be an Android project (missing build.gradle or AndroidManifest.xml): {canonical}", canonical

        return True, "Valid Android project directory", canonical

    def register_project(
        self,
        project_path: Union[str, Path],
        project_name: Optional[str] = None,
        authorized_by_user: bool = True,
        package_id: Optional[str] = None,
    ) -> AndroidProjectRecord:
        """
        Registers a new Android project into the system.
        """
        ok, msg, canonical = self.validate_candidate_path(project_path)
        if not ok:
            raise ValueError(f"Project registration failed: {msg}")

        # Check for existing registration with same canonical path
        for existing in self._projects.values():
            if Path(existing.canonical_path).resolve() == canonical:
                if existing.status == ProjectStatus.REVOKED.value:
                    # Reactivate
                    existing.status = ProjectStatus.ACTIVE.value
                    existing.authorized_by_user = authorized_by_user
                    existing.registration_time = time.time()
                    self._save()
                    return existing
                return existing

        p_id = f"proj_{uuid.uuid4().hex[:8]}"
        p_name = project_name or canonical.name

        # Detect modules
        modules = []
        for child in canonical.iterdir():
            if child.is_dir() and ((child / "build.gradle").exists() or (child / "build.gradle.kts").exists()):
                modules.append(child.name)
        if not modules:
            modules = ["app"] if (canonical / "app").exists() else ["."]

        # Detect package ID if not provided
        if not package_id:
            for manifest in canonical.glob("**/AndroidManifest.xml"):
                try:
                    txt = manifest.read_text(encoding="utf-8", errors="ignore")
                    import re
                    m = re.search(r'package\s*=\s*["\']([^"\']+)["\']', txt)
                    if m:
                        package_id = m.group(1)
                        break
                except Exception:
                    pass

        record = AndroidProjectRecord(
            project_id=p_id,
            project_name=p_name,
            canonical_path=str(canonical),
            registration_time=time.time(),
            project_type=ProjectType.MULTI_MODULE.value if len(modules) > 1 else ProjectType.APPLICATION.value,
            package_id=package_id,
            gradle_root=str(canonical),
            modules=modules,
            status=ProjectStatus.ACTIVE.value,
            authorized_by_user=authorized_by_user,
        )

        self._projects[p_id] = record
        self._save()
        logger.info(f"Registered Android project '{p_name}' ({p_id}) at {canonical}")
        return record

    def get_project(self, project_id: str) -> Optional[AndroidProjectRecord]:
        """Fetch project record by project_id."""
        return self._projects.get(project_id)

    def get_project_by_path(self, path: Union[str, Path]) -> Optional[AndroidProjectRecord]:
        """Fetch project record matching the canonical directory path."""
        try:
            target = Path(path).resolve()
        except Exception:
            return None

        for p in self._projects.values():
            if Path(p.canonical_path).resolve() == target:
                return p
        return None

    def list_projects(self, status: Optional[Union[str, ProjectStatus]] = None) -> List[AndroidProjectRecord]:
        """List registered projects with optional status filter."""
        stat_val = status.value if isinstance(status, ProjectStatus) else (str(status) if status else None)
        if stat_val:
            return [p for p in self._projects.values() if p.status == stat_val]
        return list(self._projects.values())

    def suspend_project(self, project_id: str, reason: Optional[str] = None) -> AndroidProjectRecord:
        """Suspend a project (temporarily disable build/repair/deployment operations)."""
        p = self.get_project(project_id)
        if not p:
            raise KeyError(f"Project '{project_id}' not found.")
        p.status = ProjectStatus.SUSPENDED.value
        if reason:
            p.metadata["suspension_reason"] = reason
        self._save()
        logger.info(f"Suspended project '{project_id}'.")
        return p

    def revoke_project(self, project_id: str, reason: Optional[str] = None) -> AndroidProjectRecord:
        """Revoke a project (forbid all access)."""
        p = self.get_project(project_id)
        if not p:
            raise KeyError(f"Project '{project_id}' not found.")
        p.status = ProjectStatus.REVOKED.value
        p.authorized_by_user = False
        if reason:
            p.metadata["revocation_reason"] = reason
        self._save()
        logger.info(f"Revoked project '{project_id}'.")
        return p

    def is_path_authorized(self, target_path: Union[str, Path]) -> Tuple[bool, Optional[str], Optional[AndroidProjectRecord]]:
        """
        Validates whether a given file or directory is inside an ACTIVE, authorized project.
        Returns: (is_authorized, reason_or_error, matched_project)
        """
        try:
            target = Path(target_path).resolve()
        except Exception as e:
            return False, f"Invalid target path syntax: {e}", None

        # Check all registered active projects
        for p in self._projects.values():
            if p.status == ProjectStatus.ACTIVE.value and p.authorized_by_user:
                proj_root = Path(p.canonical_path).resolve()
                try:
                    if target == proj_root or target.is_relative_to(proj_root):
                        return True, "Path authorized within active project boundary", p
                except AttributeError:
                    if str(target) == str(proj_root) or str(target).startswith(str(proj_root) + os.sep):
                        return True, "Path authorized within active project boundary", p

        # Check if it was in a suspended or revoked project
        for p in self._projects.values():
            proj_root = Path(p.canonical_path).resolve()
            try:
                if target == proj_root or target.is_relative_to(proj_root):
                    return False, f"Path belongs to {p.status} project '{p.project_name}' ({p.project_id}). Operations rejected.", p
            except AttributeError:
                if str(target) == str(proj_root) or str(target).startswith(str(proj_root) + os.sep):
                    return False, f"Path belongs to {p.status} project '{p.project_name}' ({p.project_id}). Operations rejected.", p

        return False, f"Path '{target}' is not within any registered and authorized Android project.", None
