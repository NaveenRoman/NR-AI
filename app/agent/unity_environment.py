"""
NR-AI Unity Environment Detection & Inspection Engine (Step 8).

Safely discovers and inspects installed Unity Editors, the official Unity CLI,
and active editor processes using bounded, non-arbitrary subprocess calls (shell=False).
"""

from dataclasses import dataclass, field
import glob
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("NRAI.UnityEnvironment")

# Known installation roots for Unity Editor on Windows
STANDARD_UNITY_ROOTS = [
    Path(r"C:\Program Files\Unity 2022.3.35f1\Editor\Unity.exe"),
    Path(r"C:\Program Files\Unity 2022.3.76f1\Editor\Unity.exe"),
    Path(r"C:\Program Files\Unity\Editor\Unity.exe"),
]

STANDARD_UNITY_HUB_PATHS = [
    Path(r"C:\Program Files\Unity Hub\Unity Hub.exe"),
    Path(r"C:\Program Files (x86)\Unity Hub\Unity Hub.exe"),
]

DEFAULT_UNITY_CLI_PATH = Path(os.path.expandvars(r"%LOCALAPPDATA%\Unity\bin\unity.exe"))


@dataclass
class UnityEditorInstance:
    """Represents an installed Unity Editor instance."""
    editor_path: str
    version: str
    version_major: int = 0
    version_minor: int = 0
    is_lts: bool = False
    is_running: bool = False
    process_id: Optional[int] = None
    has_standalone_support: bool = False

    @property
    def is_executable(self) -> bool:
        return Path(self.editor_path).is_file()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "editor_path": self.editor_path,
            "version": self.version,
            "version_major": self.version_major,
            "version_minor": self.version_minor,
            "is_lts": self.is_lts,
            "is_running": self.is_running,
            "process_id": self.process_id,
            "has_standalone_support": self.has_standalone_support,
        }


@dataclass
class UnityCliInfo:
    """Represents the official Unity CLI tool."""
    cli_path: Optional[str] = None
    is_installed: bool = False
    version: Optional[str] = None

    @property
    def is_available(self) -> bool:
        return self.is_installed

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cli_path": self.cli_path,
            "is_installed": self.is_installed,
            "version": self.version,
        }


@dataclass
class UnityHubInfo:
    """Represents the Unity Hub installation."""
    hub_path: Optional[str] = None
    is_installed: bool = False
    version: Optional[str] = None

    @property
    def is_available(self) -> bool:
        return self.is_installed

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hub_path": self.hub_path,
            "is_installed": self.is_installed,
            "version": self.version,
        }


@dataclass
class UnityEnvironmentInfo:
    """Structured report of the host machine Unity environment."""
    is_available: bool = False
    editors: List[UnityEditorInstance] = field(default_factory=list)
    preferred_editor: Optional[UnityEditorInstance] = None
    cli: UnityCliInfo = field(default_factory=UnityCliInfo)
    hub: UnityHubInfo = field(default_factory=UnityHubInfo)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_available": self.is_available,
            "editors": [e.to_dict() for e in self.editors],
            "preferred_editor": self.preferred_editor.to_dict() if self.preferred_editor else None,
            "cli": self.cli.to_dict(),
            "hub": self.hub.to_dict(),
        }


class UnityEnvironmentDetector:
    """
    Detects Unity Editors, Unity Hub, and the Unity CLI on the system safely.
    Uses bounded subprocess calls strictly with shell=False.
    """

    def __init__(
        self,
        custom_editor_paths: Optional[List[Path]] = None,
        known_roots: Optional[List[Path]] = None,
        hub_paths: Optional[List[Path]] = None,
        cli_path: Optional[Path] = None,
    ):
        self.custom_editor_paths = custom_editor_paths or []
        self._custom_known_roots = known_roots
        self.known_roots = known_roots if known_roots is not None else STANDARD_UNITY_ROOTS
        self.hub_paths = hub_paths if hub_paths is not None else STANDARD_UNITY_HUB_PATHS
        self.cli_path = cli_path if cli_path is not None else DEFAULT_UNITY_CLI_PATH

    def _parse_version_components(self, version_str: str) -> Tuple[int, int, bool]:
        """Parses major, minor, and LTS flag from version string e.g. 2022.3.35f1."""
        m = re.match(r"^(\d+)\.(\d+)", version_str)
        if m:
            major = int(m.group(1))
            minor = int(m.group(2))
            is_lts = (major >= 2021 and minor == 3) or (major >= 2023 and minor == 2)
            return major, minor, is_lts
        return 0, 0, False

    def _get_editor_version(self, editor_path: Path) -> str:
        """Extracts version from path or by querying Unity.exe -version safely."""
        # Fast path: check if version is in directory path (e.g. Unity 2022.3.35f1)
        m = re.search(r"Unity\s+([\d\.]+f\d+)", str(editor_path), re.IGNORECASE)
        if m:
            return m.group(1)

        try:
            res = subprocess.run(
                [str(editor_path), "-version"],
                capture_output=True,
                text=True,
                timeout=5.0,
                shell=False,
            )
            v = res.stdout.strip() or res.stderr.strip()
            if v:
                return v
        except Exception:
            pass

        # Fallback: check sibling or parent files
        return "Unknown"

    def detect_editors(self) -> List[UnityEditorInstance]:
        """Finds all installed Unity Editors."""
        candidate_paths: List[Path] = list(self.known_roots) + self.custom_editor_paths

        # Add Hub installed editors if any (only when scanning default system roots)
        if self._custom_known_roots is None:
            hub_editor_globs = [
                r"C:\Program Files\Unity\Hub\Editor\*\Editor\Unity.exe",
                r"C:\Program Files (x86)\Unity\Hub\Editor\*\Editor\Unity.exe",
                r"C:\Program Files\Unity 20*\Editor\Unity.exe",
            ]
            for pat in hub_editor_globs:
                for p_str in glob.glob(pat):
                    candidate_paths.append(Path(p_str))

        running_procs = self.get_running_editor_processes()

        detected: List[UnityEditorInstance] = []
        seen_paths = set()

        for cand in candidate_paths:
            resolved = cand.resolve()
            if resolved in seen_paths:
                continue
            if resolved.is_file() and resolved.name.lower() == "unity.exe":
                seen_paths.add(resolved)
                version_str = self._get_editor_version(resolved)
                major, minor, is_lts = self._parse_version_components(version_str)

                # Check windows standalone playback engine
                playback_dir = resolved.parent / "Data" / "PlaybackEngines" / "windowsstandalonesupport"
                has_standalone = playback_dir.exists()

                # Check if running
                is_running = False
                pid = None
                for proc in running_procs:
                    r_path = proc["name"] if isinstance(proc, dict) else proc[0]
                    r_pid = proc["pid"] if isinstance(proc, dict) else proc[1]
                    if str(resolved).lower() == str(r_path).lower() or r_path.lower() == "unity.exe":
                        is_running = True
                        pid = r_pid
                        break

                detected.append(
                    UnityEditorInstance(
                        editor_path=str(resolved),
                        version=version_str,
                        version_major=major,
                        version_minor=minor,
                        is_lts=is_lts,
                        is_running=is_running,
                        process_id=pid,
                        has_standalone_support=has_standalone,
                    )
                )

        # Sort by version descending
        detected.sort(key=lambda e: (e.version_major, e.version_minor, e.version), reverse=True)
        return detected

    def detect_cli(self) -> UnityCliInfo:
        """Inspects the official Unity CLI if installed."""
        cli_path = None
        if self.cli_path and self.cli_path.exists():
            cli_path = self.cli_path
        elif self._custom_known_roots is None:
            wh = shutil.which("unity")
            if wh:
                cli_path = Path(wh)

        if cli_path and cli_path.exists():
            try:
                res = subprocess.run(
                    [str(cli_path), "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5.0,
                    shell=False,
                )
                v = res.stdout.strip()
                return UnityCliInfo(cli_path=str(cli_path), is_installed=True, version=v)
            except Exception:
                return UnityCliInfo(cli_path=str(cli_path), is_installed=True, version="Detected")

        return UnityCliInfo(is_installed=False)

    def detect_hub(self) -> UnityHubInfo:
        """Inspects Unity Hub installation."""
        for cand in self.hub_paths:
            if cand.exists():
                return UnityHubInfo(hub_path=str(cand), is_installed=True)
        if self._custom_known_roots is None:
            wh = shutil.which("unityhub")
            if wh:
                return UnityHubInfo(hub_path=str(Path(wh)), is_installed=True)
        return UnityHubInfo(is_installed=False)

    def get_running_editor_processes(self) -> List[Dict[str, Any]]:
        """Returns list of running Unity.exe process dicts with name, pid, and process_path."""
        running: List[Dict[str, Any]] = []
        try:
            res = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq Unity.exe", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=5.0,
                shell=False,
            )
            for line in res.stdout.strip().splitlines():
                if not line or "INFO: No tasks" in line:
                    continue
                parts = [p.strip(' "') for p in line.split(",")]
                if len(parts) >= 2 and parts[0].lower() == "unity.exe":
                    try:
                        pid = int(parts[1])
                        running.append({"name": parts[0], "pid": pid, "process_path": parts[0]})
                    except ValueError:
                        pass
        except Exception:
            pass
        return running

    def detect_environment(self) -> UnityEnvironmentInfo:
        """Comprehensive environment audit report."""
        editors = self.detect_editors()
        cli = self.detect_cli()
        hub = self.detect_hub()

        is_avail = (len(editors) > 0) or cli.is_installed
        preferred = editors[0] if editors else None

        return UnityEnvironmentInfo(
            is_available=is_avail,
            editors=editors,
            preferred_editor=preferred,
            cli=cli,
            hub=hub,
        )

    def find_editor_for_project(self, project_path: Path) -> Optional[UnityEditorInstance]:
        """Finds matching installed editor for project based on ProjectVersion.txt."""
        version_file = project_path / "ProjectSettings" / "ProjectVersion.txt"
        req_ver = None
        if version_file.exists():
            try:
                content = version_file.read_text(encoding="utf-8")
                m = re.search(r"m_EditorVersion:\s*([^\r\n]+)", content)
                if m:
                    req_ver = m.group(1).strip()
            except Exception:
                pass

        editors = self.detect_editors()
        if not editors:
            return None

        if req_ver:
            # Exact match
            for ed in editors:
                if ed.version == req_ver:
                    return ed
            # Prefix match
            for ed in editors:
                if ed.version.startswith(req_ver.split(".")[0]):
                    return ed

        return editors[0]


DEFAULT_UNITY_ENV_DETECTOR = UnityEnvironmentDetector()
