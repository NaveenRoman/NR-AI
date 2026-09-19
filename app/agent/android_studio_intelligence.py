"""
NR-AI Android Studio Deep Integration & Project Snapshot Engine.

Discovers host Android Studio installations, bundled JBR/JDK, Android SDK,
Gradle, AGP, Kotlin versions, module structure, build variants, and project state
via structured metadata without brittle GUI automation.

Adheres strictly to the NR-AI Safety Invariant:
Never fabricate missing values; use "UNKNOWN" when unavailable.
"""

from dataclasses import dataclass, field, asdict
import json
import logging
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger("NRAI.AndroidStudioIntelligence")

UNKNOWN = "UNKNOWN"


@dataclass
class AndroidStudioProjectSnapshot:
    """Structured, immutable snapshot of the Android Studio & project environment."""
    studio_version: str = UNKNOWN
    jdk_version: str = UNKNOWN
    sdk_path: str = UNKNOWN
    project_path: str = UNKNOWN
    modules: List[str] = field(default_factory=list)
    variants: List[str] = field(default_factory=list)
    gradle_version: str = UNKNOWN
    agp_version: str = UNKNOWN
    kotlin_version: str = UNKNOWN
    sync_state: str = UNKNOWN
    build_state: str = UNKNOWN
    known_errors: List[str] = field(default_factory=list)
    studio_install_path: str = UNKNOWN
    jbr_path: str = UNKNOWN
    run_configurations: List[Dict[str, Any]] = field(default_factory=list)
    has_wrapper: bool = False

    @property
    def project_name(self) -> str:
        if self.project_path and self.project_path != UNKNOWN:
            return Path(self.project_path).name
        return UNKNOWN

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["project_name"] = self.project_name
        return d


class AndroidStudioIntelligence:
    """
    Inspects host Android Studio environments and project structures.
    Uses non-destructive, read-only metadata probing.
    """

    DEFAULT_STUDIO_LOCATIONS = [
        Path(r"C:\Program Files\Android\Android Studio1"),
        Path(r"C:\Program Files\Android\Android Studio"),
        Path(r"C:\Program Files\Google\Android Studio"),
        Path(os.path.expandvars(r"%LOCALAPPDATA%\Programs\Android Studio")),
    ]

    def __init__(self, custom_studio_path: Optional[Union[str, Path]] = None):
        self.custom_studio_path = Path(custom_studio_path) if custom_studio_path else None

    # -------------------------------------------------------------------------
    # Host Environment Discovery
    # -------------------------------------------------------------------------

    def find_studio_installations(self) -> List[Dict[str, Any]]:
        """Scans filesystem for Android Studio installations."""
        installations = []
        candidates = []
        if self.custom_studio_path:
            candidates.append(self.custom_studio_path)
        candidates.extend(self.DEFAULT_STUDIO_LOCATIONS)

        # Dynamic search in Program Files
        prog_files = Path(r"C:\Program Files\Android")
        if prog_files.exists() and prog_files.is_dir():
            for child in prog_files.iterdir():
                if child.is_dir() and "studio" in child.name.lower():
                    if child not in candidates:
                        candidates.append(child)

        seen_paths = set()
        for cand in candidates:
            resolved = cand.resolve() if cand.exists() else cand
            if str(resolved) in seen_paths:
                continue
            seen_paths.add(str(resolved))

            if not cand.exists() or not cand.is_dir():
                continue

            info = self._inspect_studio_dir(cand)
            if info:
                installations.append(info)

        return installations

    def _inspect_studio_dir(self, path: Path) -> Optional[Dict[str, Any]]:
        """Reads product-info.json or build.txt to extract Studio metadata."""
        product_info = path / "product-info.json"
        build_txt = path / "build.txt"
        version = UNKNOWN
        build_number = UNKNOWN
        name = "Android Studio"

        if product_info.exists():
            try:
                data = json.loads(product_info.read_text(encoding="utf-8", errors="ignore"))
                name = data.get("name", "Android Studio")
                version = data.get("version", UNKNOWN)
                build_number = data.get("buildNumber", UNKNOWN)
            except Exception as e:
                logger.debug(f"Failed parsing product-info.json in {path}: {e}")

        if build_txt.exists():
            try:
                bt_val = build_txt.read_text(encoding="utf-8", errors="ignore").strip()
                if bt_val.startswith("AI-") or build_number == UNKNOWN:
                    build_number = bt_val
                if version == UNKNOWN:
                    version = bt_val
            except Exception:
                pass

        jbr_dir = path / "jbr"
        has_jbr = jbr_dir.exists() and jbr_dir.is_dir()

        # If it has product info or jbr or bin/studio64.exe, it's a studio install
        bin_dir = path / "bin"
        if not (product_info.exists() or has_jbr or bin_dir.exists()):
            return None

        return {
            "path": str(path),
            "name": name,
            "version": version,
            "build_number": build_number,
            "jbr_path": str(jbr_dir) if has_jbr else UNKNOWN,
        }

    def detect_bundled_jdk(self, studio_path: Path) -> Tuple[str, str]:
        """Probes the bundled JBR/JDK version safely with a 5s bounded timeout."""
        jbr_dir = studio_path / "jbr"
        if not jbr_dir.exists():
            return UNKNOWN, UNKNOWN

        java_exe = jbr_dir / "bin" / ("java.exe" if os.name == "nt" else "java")
        if not java_exe.exists():
            return UNKNOWN, str(jbr_dir)

        try:
            res = subprocess.run(
                [str(java_exe), "-version"],
                capture_output=True,
                text=True,
                timeout=5.0,
                shell=False,
            )
            raw = res.stderr.strip() or res.stdout.strip()
            # Match openjdk version "21.0.3" or java version "..."
            match = re.search(r'version "([^"]+)"', raw)
            if match:
                return match.group(1), str(jbr_dir)
            first_line = raw.splitlines()[0] if raw else UNKNOWN
            return first_line, str(jbr_dir)
        except Exception as e:
            logger.debug(f"Failed detecting JBR java version: {e}")
            return UNKNOWN, str(jbr_dir)

    def detect_sdk_path(self, project_path: Optional[Path] = None) -> str:
        """Determines the Android SDK path from local.properties, env, or standard locations."""
        if project_path:
            local_props = project_path / "local.properties"
            if local_props.exists():
                try:
                    for line in local_props.read_text(encoding="utf-8", errors="ignore").splitlines():
                        if line.strip().startswith("sdk.dir"):
                            parts = line.split("=", 1)
                            if len(parts) == 2:
                                val = parts[1].strip().replace(r"\:", ":").replace(r"\\", "\\")
                                if Path(val).exists():
                                    return val
                except Exception:
                    pass

        # Environment variables
        for env_var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
            val = os.environ.get(env_var)
            if val and Path(val).exists():
                return val

        # Common Windows path
        default_win_sdk = Path(os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk"))
        if default_win_sdk.exists():
            return str(default_win_sdk)

        return UNKNOWN

    # -------------------------------------------------------------------------
    # Project Structure Inspection
    # -------------------------------------------------------------------------

    def discover_studio_installation(self) -> Optional[Any]:
        """Returns the primary discovered Android Studio installation as an object."""
        installs = self.find_studio_installations()
        if not installs:
            return None
        from dataclasses import make_dataclass
        primary = installs[0]
        jbr = primary.get("jbr_path") or primary.get("bundled_jdk_path", "")
        StudioInstall = make_dataclass("StudioInstall", [
            ("studio_home", str),
            ("jbr_home", str),
            ("java_executable", str),
            ("version_string", str),
        ])
        return StudioInstall(
            studio_home=primary.get("path", ""),
            jbr_home=jbr,
            java_executable=str(Path(jbr) / "bin" / ("java.exe" if os.name == "nt" else "java")),
            version_string=primary.get("build_number", primary.get("version", "")),
        )

    def snapshot_project(
        self,
        project_path: Union[str, Path],
    ) -> AndroidStudioProjectSnapshot:
        """Builds a comprehensive AndroidStudioProjectSnapshot for an authorized project."""
        p_path = Path(project_path).resolve()
        if not p_path.exists() or not p_path.is_dir():
            return AndroidStudioProjectSnapshot(
                project_path=str(p_path),
                known_errors=[f"Project path does not exist: {p_path}"],
            )

        # 1. Studio & JBR
        installs = self.find_studio_installations()
        studio_ver = UNKNOWN
        studio_path_str = UNKNOWN
        jdk_ver = UNKNOWN
        jbr_path_str = UNKNOWN

        if installs:
            best = installs[0]
            studio_ver = best.get("version", UNKNOWN)
            studio_path_str = best.get("path", UNKNOWN)
            j_ver, j_path = self.detect_bundled_jdk(Path(studio_path_str))
            jdk_ver = j_ver
            jbr_path_str = j_path
        else:
            # Fallback to system java
            try:
                res = subprocess.run(["java", "-version"], capture_output=True, text=True, timeout=3.0, shell=False)
                raw = res.stderr.strip() or res.stdout.strip()
                match = re.search(r'version "([^"]+)"', raw)
                if match:
                    jdk_ver = match.group(1)
            except Exception:
                pass

        # 2. SDK Path
        sdk_path = self.detect_sdk_path(p_path)

        # 3. Modules & Gradle
        modules = self._discover_modules(p_path)
        gradle_ver = self._detect_gradle_version(p_path)
        agp_ver = self._detect_agp_version(p_path)
        kotlin_ver = self._detect_kotlin_version(p_path)

        # 4. Variants & Run Configurations
        variants = self._discover_build_variants(p_path, modules)
        run_configs = self._discover_run_configurations(p_path)

        # 5. Sync & Build State
        sync_state, build_state, errors = self._determine_sync_and_build_state(p_path)
        has_wrapper = (p_path / "gradlew").exists() or (p_path / "gradlew.bat").exists()

        return AndroidStudioProjectSnapshot(
            studio_version=studio_ver,
            jdk_version=jdk_ver,
            sdk_path=sdk_path,
            project_path=str(p_path),
            modules=modules,
            variants=variants,
            gradle_version=gradle_ver,
            agp_version=agp_ver,
            kotlin_version=kotlin_ver,
            sync_state=sync_state,
            build_state=build_state,
            known_errors=errors,
            studio_install_path=studio_path_str,
            jbr_path=jbr_path_str,
            run_configurations=run_configs,
            has_wrapper=has_wrapper,
        )

    inspect_project = snapshot_project

    def _discover_modules(self, project_path: Path) -> List[str]:
        """Discovers modules from settings.gradle or settings.gradle.kts."""
        modules = []
        for name in ("settings.gradle", "settings.gradle.kts"):
            settings_file = project_path / name
            if settings_file.exists():
                try:
                    content = settings_file.read_text(encoding="utf-8", errors="ignore")
                    for line in content.splitlines():
                        line_s = line.strip()
                        if line_s.startswith("include ") or line_s.startswith("include("):
                            # Exclude includeGroupByRegex or other methods
                            if "includeGroupByRegex" in line_s or "includeFlat" in line_s:
                                continue
                            for mod in re.findall(r"['\"]([^'\"]+)['\"]", line_s):
                                if mod not in modules:
                                    modules.append(mod)
                except Exception:
                    pass

        if not modules:
            # Fallback: check subdirectories with build.gradle
            for child in project_path.iterdir():
                if child.is_dir() and not child.name.startswith("."):
                    if (child / "build.gradle").exists() or (child / "build.gradle.kts").exists():
                        modules.append(f":{child.name}")

        return sorted(modules)

    def _detect_gradle_version(self, project_path: Path) -> str:
        """Reads distributionUrl in gradle-wrapper.properties."""
        props = project_path / "gradle" / "wrapper" / "gradle-wrapper.properties"
        if props.exists():
            try:
                for line in props.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if line.strip().startswith("distributionUrl"):
                        match = re.search(r"gradle-([0-9\.]+)-(bin|all)\.zip", line)
                        if match:
                            return match.group(1)
            except Exception:
                pass
        return UNKNOWN

    def _detect_agp_version(self, project_path: Path) -> str:
        """Finds Android Gradle Plugin version from version catalogs or build scripts."""
        # 1. Version Catalog libs.versions.toml
        toml_file = project_path / "gradle" / "libs.versions.toml"
        if toml_file.exists():
            try:
                content = toml_file.read_text(encoding="utf-8", errors="ignore")
                match = re.search(r'(?:agp|androidGradlePlugin)\s*=\s*["\']([^"\']+)["\']', content)
                if match:
                    return match.group(1)
            except Exception:
                pass

        # 2. Root or module build.gradle
        for path in sorted(project_path.rglob("build.gradle*")):
            if path.is_file():
                try:
                    content = path.read_text(encoding="utf-8", errors="ignore")
                    m = re.search(r"com\.android\.tools\.build:gradle:([0-9\.]+[a-zA-Z0-9\.\-]*)", content)
                    if m:
                        return m.group(1)
                    m2 = re.search(r"com\.android\.(?:application|library)['\"]?\s+version\s+['\"]([^'\"]+)['\"]", content)
                    if m2:
                        return m2.group(1)
                except Exception:
                    pass

        return UNKNOWN

    def _detect_kotlin_version(self, project_path: Path) -> str:
        """Finds Kotlin version from version catalogs or build scripts."""
        toml_file = project_path / "gradle" / "libs.versions.toml"
        if toml_file.exists():
            try:
                content = toml_file.read_text(encoding="utf-8", errors="ignore")
                match = re.search(r'kotlin\s*=\s*["\']([^"\']+)["\']', content)
                if match:
                    return match.group(1)
            except Exception:
                pass

        for path in sorted(project_path.rglob("build.gradle*")):
            if path.is_file():
                try:
                    content = path.read_text(encoding="utf-8", errors="ignore")
                    m = re.search(r"(?:org\.jetbrains\.kotlin.*|kotlin-android)['\"]?\s+version\s+['\"]([^'\"]+)['\"]", content)
                    if m:
                        return m.group(1)
                    m2 = re.search(r"org\.jetbrains\.kotlin.*:([0-9\.]+[a-zA-Z0-9\.\-]*)", content)
                    if m2:
                        return m2.group(1)
                except Exception:
                    pass

        return UNKNOWN

    def _discover_build_variants(self, project_path: Path, modules: List[str]) -> List[str]:
        """Discovers build variants (defaulting to standard debug / release)."""
        variants = ["debug", "release"]
        for mod in modules:
            mod_name = mod.lstrip(":")
            mod_dir = project_path / mod_name
            build_gradle = mod_dir / "build.gradle"
            if not build_gradle.exists():
                build_gradle = mod_dir / "build.gradle.kts"
            if build_gradle.exists():
                try:
                    content = build_gradle.read_text(encoding="utf-8", errors="ignore")
                    # Look for productFlavors
                    if "productFlavors" in content:
                        flavors = re.findall(r"create\([\"']([^\"']+)[\"']\)|([a-zA-Z0-9]+)\s*\{", content)
                        # Keep standard debug/release if custom flavors aren't trivially parsed
                except Exception:
                    pass
        return variants

    def _discover_run_configurations(self, project_path: Path) -> List[Dict[str, Any]]:
        """Reads .idea/runConfigurations if safely available."""
        configs = []
        rc_dir = project_path / ".idea" / "runConfigurations"
        if rc_dir.exists() and rc_dir.is_dir():
            for xml_file in rc_dir.glob("*.xml"):
                try:
                    content = xml_file.read_text(encoding="utf-8", errors="ignore")
                    m_name = re.search(r'<configuration\s+[^>]*name="([^"]+)"', content)
                    m_type = re.search(r'<configuration\s+[^>]*type="([^"]+)"', content)
                    configs.append({
                        "name": m_name.group(1) if m_name else xml_file.stem,
                        "type": m_type.group(1) if m_type else "AndroidRunConfigurationType",
                        "file": xml_file.name,
                    })
                except Exception:
                    pass
        return configs

    def _determine_sync_and_build_state(self, project_path: Path) -> Tuple[str, str, List[str]]:
        """Checks whether project is synced and built based on on-disk outputs."""
        errors: List[str] = []
        sync_state = "SYNCED" if (project_path / ".gradle").exists() or (project_path / "build").exists() else "UNSYNCED"

        has_apk = any(project_path.rglob("*.apk"))
        has_classes = any(project_path.rglob("*.class"))
        build_state = "BUILT" if (has_apk or has_classes) else "NOT_BUILT"

        # Check for local.properties missing sdk.dir
        lp = project_path / "local.properties"
        if not lp.exists():
            errors.append("local.properties is missing.")
        else:
            txt = lp.read_text(encoding="utf-8", errors="ignore")
            if "sdk.dir" not in txt:
                errors.append("local.properties does not specify sdk.dir.")

        return sync_state, build_state, errors
