r"""
NR-AI Unreal Engine Environment Detection & Inspection Engine (Step 9 Phase 1).

Safely discovers and inspects installed Unreal Engine versions, editor binaries,
UnrealBuildTool (UBT), AutomationTool (UAT), Build.version metadata, and running processes
using bounded, non-arbitrary operations (strictly shell=False).
"""

from dataclasses import dataclass, field
from enum import Enum
import glob
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import psutil

logger = logging.getLogger("NRAI.UnrealEnvironment")


# -----------------------------------------------------------------------------
# Status & Enums
# -----------------------------------------------------------------------------

class UnrealEngineStatus(str, Enum):
    NOT_INSTALLED = "NOT_INSTALLED"
    DETECTED = "DETECTED"
    INCOMPLETE = "INCOMPLETE"
    UNUSABLE = "UNUSABLE"
    USABLE = "USABLE"
    EXECUTION_NOT_VERIFIED = "EXECUTION_NOT_VERIFIED"


# Standard search locations for Unreal Engine on Windows
STANDARD_UNREAL_ROOTS: List[Path] = [
    Path(r"C:\Program Files\Epic Games"),
    Path(r"C:\Program Files (x86)\Epic Games"),
    Path(r"D:\Epic Games"),
    Path(r"E:\Epic Games"),
    Path(r"C:\Epic Games"),
]

STANDARD_MANIFEST_DIR = Path(r"C:\ProgramData\Epic\EpicGamesLauncher\Data\Manifests")


# -----------------------------------------------------------------------------
# Data Models
# -----------------------------------------------------------------------------

@dataclass
class UnrealEngineInstance:
    """Represents an installed Unreal Engine version."""
    engine_path: str
    version: str
    version_major: int = 0
    version_minor: int = 0
    version_patch: int = 0
    changelist: int = 0
    branch_name: str = ""
    is_ue5: bool = False
    status: UnrealEngineStatus = UnrealEngineStatus.DETECTED
    status_reason: str = ""
    editor_executable: Optional[str] = None
    cmd_executable: Optional[str] = None
    ubt_executable: Optional[str] = None
    uat_batch: Optional[str] = None
    build_version_path: Optional[str] = None
    is_running: bool = False
    process_id: Optional[int] = None

    @property
    def is_executable(self) -> bool:
        if self.editor_executable and Path(self.editor_executable).is_file():
            return True
        if self.cmd_executable and Path(self.cmd_executable).is_file():
            return True
        return False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "engine_path": self.engine_path,
            "version": self.version,
            "version_major": self.version_major,
            "version_minor": self.version_minor,
            "version_patch": self.version_patch,
            "changelist": self.changelist,
            "branch_name": self.branch_name,
            "is_ue5": self.is_ue5,
            "status": self.status.value if isinstance(self.status, UnrealEngineStatus) else str(self.status),
            "status_reason": self.status_reason,
            "editor_executable": self.editor_executable,
            "cmd_executable": self.cmd_executable,
            "ubt_executable": self.ubt_executable,
            "uat_batch": self.uat_batch,
            "build_version_path": self.build_version_path,
            "is_running": self.is_running,
            "process_id": self.process_id,
        }


@dataclass
class UnrealEnvironmentInfo:
    """Structured report of host machine Unreal Engine environment."""
    is_available: bool = False
    status: str = "NOT_INSTALLED"
    engines: List[UnrealEngineInstance] = field(default_factory=list)
    preferred_engine: Optional[UnrealEngineInstance] = None
    active_processes: List[Dict[str, Any]] = field(default_factory=list)
    msvc_available: bool = False
    vs_path: Optional[str] = None
    winsdk_available: bool = False
    winsdk_version: Optional[str] = None
    execution_verification_status: str = "LIVE_UNREAL_EXECUTION = NOT VERIFIED"
    execution_verification_reason: str = "Headless execution requires project context and license activation; UBT requires host .NET runtime"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_available": self.is_available,
            "status": self.status,
            "engines": [e.to_dict() for e in self.engines],
            "preferred_engine": self.preferred_engine.to_dict() if self.preferred_engine else None,
            "active_processes": self.active_processes,
            "msvc_available": self.msvc_available,
            "vs_path": self.vs_path,
            "winsdk_available": self.winsdk_available,
            "winsdk_version": self.winsdk_version,
            "execution_verification_status": self.execution_verification_status,
            "execution_verification_reason": self.execution_verification_reason,
        }


# -----------------------------------------------------------------------------
# Detector Class
# -----------------------------------------------------------------------------

class UnrealEnvironmentDetector:
    """
    Safely detects installed Unreal Engine instances, binaries, compilers,
    and active processes using bounded, deterministic checks.
    """

    def __init__(
        self,
        custom_roots: Optional[List[Path]] = None,
        custom_manifest_dir: Optional[Path] = None,
        custom_engine_paths: Optional[List[Path]] = None,
    ):
        self.custom_roots = custom_roots
        self.known_roots = custom_roots if custom_roots is not None else STANDARD_UNREAL_ROOTS
        self.manifest_dir = custom_manifest_dir if custom_manifest_dir is not None else STANDARD_MANIFEST_DIR
        self.custom_engine_paths = custom_engine_paths or []

    def _parse_build_version(self, version_file: Path) -> Dict[str, Any]:
        """Safely parses an Engine/Build/Build.version JSON file."""
        if not version_file.is_file():
            return {}
        try:
            txt = version_file.read_text(encoding="utf-8", errors="ignore")
            return json.loads(txt)
        except Exception as e:
            logger.warning(f"Failed to parse {version_file}: {e}")
            return {}

    def _inspect_engine_dir(self, engine_dir: Path) -> Optional[UnrealEngineInstance]:
        """Inspects a directory to determine if it is a valid Unreal Engine installation."""
        if not engine_dir.is_dir():
            return None

        engine_sub = engine_dir / "Engine"
        if not engine_sub.is_dir():
            return None

        # Binary candidate paths
        win64_bin = engine_sub / "Binaries" / "Win64"
        editor_exe = win64_bin / "UnrealEditor.exe"
        if not editor_exe.is_file():
            editor_exe = win64_bin / "UE4Editor.exe"
        editor_str = str(editor_exe) if editor_exe.is_file() else None

        cmd_exe = win64_bin / "UnrealEditor-Cmd.exe"
        if not cmd_exe.is_file():
            cmd_exe = win64_bin / "UE4Editor-Cmd.exe"
        cmd_str = str(cmd_exe) if cmd_exe.is_file() else None

        ubt_candidates = [
            engine_sub / "Binaries" / "DotNET" / "UnrealBuildTool" / "UnrealBuildTool.exe",
            engine_sub / "Binaries" / "DotNET" / "UnrealBuildTool.exe",
        ]
        ubt_exe = next((c for c in ubt_candidates if c.is_file()), None)
        ubt_str = str(ubt_exe) if ubt_exe else None

        uat_bat = engine_sub / "Build" / "BatchFiles" / "RunUAT.bat"
        uat_str = str(uat_bat) if uat_bat.is_file() else None

        version_file = engine_sub / "Build" / "Build.version"
        version_data = self._parse_build_version(version_file)

        major = version_data.get("MajorVersion", 0)
        minor = version_data.get("MinorVersion", 0)
        patch = version_data.get("PatchVersion", 0)
        changelist = version_data.get("Changelist", 0)
        branch = version_data.get("BranchName", "")

        if major > 0:
            version_str = f"{major}.{minor}.{patch}"
        else:
            # Fallback to directory name parsing (e.g. UE_5.8 -> 5.8)
            m = re.search(r"(?:UE[_-]?)?(\d+\.\d+)", engine_dir.name)
            if m:
                version_str = m.group(1)
                parts = version_str.split(".")
                major = int(parts[0])
                minor = int(parts[1]) if len(parts) > 1 else 0
            else:
                version_str = "Unknown"

        is_ue5 = major >= 5 or "UE_5" in engine_dir.name

        # Determine status
        status: UnrealEngineStatus
        status_reason: str

        if not editor_str and not cmd_str and not ubt_str:
            status = UnrealEngineStatus.INCOMPLETE
            status_reason = "Missing critical Unreal binaries (Editor, Cmd, UBT)"
        elif not editor_str and not cmd_str:
            status = UnrealEngineStatus.INCOMPLETE
            status_reason = "Missing UnrealEditor executable"
        else:
            # Installation is physically detected and populated
            status = UnrealEngineStatus.DETECTED
            status_reason = "Engine files and binaries verified on disk; live execution not verified"

        return UnrealEngineInstance(
            engine_path=str(engine_dir.resolve()),
            version=version_str,
            version_major=major,
            version_minor=minor,
            version_patch=patch,
            changelist=changelist,
            branch_name=branch,
            is_ue5=is_ue5,
            status=status,
            status_reason=status_reason,
            editor_executable=editor_str,
            cmd_executable=cmd_str,
            ubt_executable=ubt_str,
            uat_batch=uat_str,
            build_version_path=str(version_file.resolve()) if version_file.is_file() else None,
        )

    def detect_engines(self) -> List[UnrealEngineInstance]:
        """Discovers all installed Unreal Engine instances on the system."""
        found_dirs: Set[Path] = set()

        # 1. Check custom explicit engine paths
        for cp in self.custom_engine_paths:
            if cp and cp.exists() and cp.is_dir():
                found_dirs.add(cp.resolve())

        # 2. Check environment variables
        env_vars = ["UNREAL_ENGINE_PATH", "UE_ENGINE_DIR", "UE_ROOT"]
        for ev in env_vars:
            val = os.environ.get(ev)
            if val and os.path.exists(val):
                found_dirs.add(Path(val).resolve())

        # 3. Check Epic Games launcher manifests
        if self.manifest_dir and self.manifest_dir.exists():
            try:
                for manifest_file in self.manifest_dir.glob("*.item"):
                    try:
                        data = json.loads(manifest_file.read_text(encoding="utf-8", errors="ignore"))
                        app_name = data.get("AppName", "")
                        loc = data.get("InstallLocation", "")
                        if ("UE_" in app_name or "Unreal" in app_name) and loc and os.path.isdir(loc):
                            found_dirs.add(Path(loc).resolve())
                    except Exception:
                        pass
            except Exception:
                pass

        # 4. Check known installation roots
        for root in self.known_roots:
            if root and root.exists() and root.is_dir():
                try:
                    for entry in root.iterdir():
                        if entry.is_dir() and (entry.name.startswith("UE_") or "Unreal" in entry.name):
                            found_dirs.add(entry.resolve())
                except Exception:
                    pass

        # 5. Check Windows registry (only when scanning default system roots)
        if self.custom_roots is None:
            self._scan_registry_for_engines(found_dirs)

        # Inspect all found directories
        instances: List[UnrealEngineInstance] = []
        running_procs = self.get_running_processes()

        for d in sorted(found_dirs):
            inst = self._inspect_engine_dir(d)
            if inst:
                # Check if running
                for proc in running_procs:
                    proc_path = proc.get("exe_path", "").lower()
                    if inst.engine_path.lower() in proc_path:
                        inst.is_running = True
                        inst.process_id = proc.get("pid")
                        break
                instances.append(inst)

        # Sort instances descending by version (preferred first)
        instances.sort(key=lambda x: (x.version_major, x.version_minor, x.version_patch), reverse=True)
        return instances

    def _scan_registry_for_engines(self, found_dirs: Set[Path]) -> None:
        """Queries Windows registry for registered Unreal Engine installations."""
        try:
            import winreg
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                for subkey in (
                    r"SOFTWARE\EpicGames\Unreal Engine",
                    r"SOFTWARE\Epic Games\Unreal Engine",
                    r"SOFTWARE\Epic Games\Unreal Engine\Builds",
                ):
                    try:
                        with winreg.OpenKey(hive, subkey) as k:
                            num_subkeys, num_values, _ = winreg.QueryInfoKey(k)
                            for i in range(num_values):
                                vname, vdata, _ = winreg.EnumValue(k, i)
                                if isinstance(vdata, str) and os.path.isdir(vdata):
                                    found_dirs.add(Path(vdata).resolve())
                    except Exception:
                        pass
        except Exception:
            pass

    def get_running_processes(self) -> List[Dict[str, Any]]:
        """Non-invasively discovers active Unreal Engine editor or compiler processes."""
        procs: List[Dict[str, Any]] = []
        target_names = {"unrealeditor.exe", "unrealeditor-cmd.exe", "ue4editor.exe", "ue4editor-cmd.exe", "unrealbuildtool.exe"}
        try:
            for p in psutil.process_iter(["pid", "name", "exe"]):
                name = (p.info["name"] or "").lower()
                if name in target_names:
                    procs.append({
                        "pid": p.info["pid"],
                        "name": p.info["name"],
                        "exe_path": p.info.get("exe") or "",
                    })
        except Exception as e:
            logger.debug(f"Failed to query running processes: {e}")
        return procs

    def detect_environment(self) -> UnrealEnvironmentInfo:
        """Produces a comprehensive Unreal environment summary report."""
        engines = self.detect_engines()
        is_avail = len(engines) > 0
        preferred = engines[0] if engines else None

        active_procs = self.get_running_processes()

        # Toolchain / MSVC checks
        cl_path = shutil.which("cl") or shutil.which("cl.exe")
        msvc_avail = bool(cl_path)
        vs_path: Optional[str] = None
        winsdk_avail = False
        winsdk_ver: Optional[str] = None

        winsdk_dir = Path(r"C:\Program Files (x86)\Windows Kits\10")
        if winsdk_dir.exists():
            inc = winsdk_dir / "Include"
            if inc.exists():
                try:
                    versions = [v.name for v in inc.iterdir() if v.name.startswith("10.")]
                    if versions:
                        winsdk_avail = True
                        winsdk_ver = sorted(versions)[-1]
                except Exception:
                    pass

        status_str = "AVAILABLE" if is_avail else "NOT_INSTALLED"

        return UnrealEnvironmentInfo(
            is_available=is_avail,
            status=status_str,
            engines=engines,
            preferred_engine=preferred,
            active_processes=active_procs,
            msvc_available=msvc_avail,
            vs_path=vs_path,
            winsdk_available=winsdk_avail,
            winsdk_version=winsdk_ver,
            execution_verification_status="LIVE_UNREAL_EXECUTION = NOT VERIFIED",
            execution_verification_reason=(
                "Headless invocation of UnrealEditor-Cmd times out without display/project; "
                "UnrealBuildTool requires host .NET desktop runtime"
            ),
        )


# Global default detector
DEFAULT_UNREAL_ENV_DETECTOR = UnrealEnvironmentDetector()
