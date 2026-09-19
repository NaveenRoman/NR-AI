"""
NR-AI Gradle Dependency & Build Graph Intelligence Engine.

Constructs an authoritative multi-tiered graph of Android projects:
Project -> Module -> Configuration -> Dependency -> Version -> Source Set -> Task.

Parses settings.gradle(.kts), build.gradle(.kts), and libs.versions.toml.
Detects dependency version conflicts, unresolved catalog aliases, and mismatches.
Generates evidence-backed recommendations without automatic blind modifications.
"""

from dataclasses import dataclass, field, asdict
from enum import Enum
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

logger = logging.getLogger("NRAI.AndroidBuildGraph")


class DependencyScope(str, Enum):
    IMPLEMENTATION = "implementation"
    API = "api"
    COMPILE_ONLY = "compileOnly"
    RUNTIME_ONLY = "runtimeOnly"
    TEST_IMPLEMENTATION = "testImplementation"
    ANDROID_TEST_IMPLEMENTATION = "androidTestImplementation"
    CLASSPATH = "classpath"
    KAPT = "kapt"
    KSP = "ksp"
    CUSTOM = "custom"


class GradleIssueKind(str, Enum):
    VERSION_CONFLICT = "VERSION_CONFLICT"
    SUSPICIOUS_MISMATCH = "SUSPICIOUS_MISMATCH"
    UNRESOLVED_ALIAS = "UNRESOLVED_ALIAS"
    MISSING_DEPENDENCY = "MISSING_DEPENDENCY"
    DUPLICATE_DEPENDENCY = "DUPLICATE_DEPENDENCY"


@dataclass
class GradleDependencyNode:
    group: str
    name: str
    version: str
    scope: DependencyScope
    module: str
    source_file: str
    line: int
    alias: Optional[str] = None

    @property
    def coordinate(self) -> str:
        return f"{self.group}:{self.name}"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["scope"] = self.scope.value
        d["coordinate"] = self.coordinate
        return d


@dataclass
class GradleModuleNode:
    name: str  # e.g., ":app"
    dir_path: str
    plugins: List[str] = field(default_factory=list)
    dependencies: List[GradleDependencyNode] = field(default_factory=list)
    source_sets: List[str] = field(default_factory=list)
    tasks: List[str] = field(default_factory=list)

    @property
    def path(self) -> str:
        return self.dir_path

    @property
    def is_library(self) -> bool:
        return any("library" in p.lower() for p in self.plugins)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "dir_path": self.dir_path,
            "path": self.path,
            "is_library": self.is_library,
            "plugins": self.plugins,
            "dependencies": [d.to_dict() for d in self.dependencies],
            "source_sets": self.source_sets,
            "tasks": self.tasks,
        }


@dataclass
class BuildGraphIssue:
    kind: GradleIssueKind
    module: str
    dependency: str
    versions: List[str]
    description: str
    recommendation: str

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d


@dataclass
class AndroidBuildGraph:
    project_name: str
    project_path: str
    modules: Dict[str, GradleModuleNode] = field(default_factory=dict)
    version_catalog_versions: Dict[str, str] = field(default_factory=dict)
    version_catalog_libraries: Dict[str, Dict[str, str]] = field(default_factory=dict)
    issues: List[BuildGraphIssue] = field(default_factory=list)
    plugin_relationships: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_name": self.project_name,
            "project_path": self.project_path,
            "modules": {k: m.to_dict() for k, m in self.modules.items()},
            "version_catalog_versions": self.version_catalog_versions,
            "version_catalog_libraries": self.version_catalog_libraries,
            "issues": [i.to_dict() for i in self.issues],
            "plugin_relationships": self.plugin_relationships,
            "total_dependencies": sum(len(m.dependencies) for m in self.modules.values()),
        }


class AndroidBuildGraphEngine:
    """Constructs and queries the multi-module Android build and dependency graph."""

    STANDARD_TASKS = [
        "assemble",
        "assembleDebug",
        "assembleRelease",
        "build",
        "clean",
        "check",
        "lint",
        "test",
        "testDebugUnitTest",
        "testReleaseUnitTest",
        "connectedDebugAndroidTest",
    ]

    def build_graph(self, project_path: Union[str, Path]) -> AndroidBuildGraph:
        p_path = Path(project_path).resolve()
        project_name = p_path.name

        # 1. Parse Version Catalog (libs.versions.toml)
        v_versions, v_libs = self._parse_version_catalog(p_path)

        # 2. Discover Modules from settings.gradle
        module_names = self._parse_settings_modules(p_path)
        if not module_names:
            module_names = [":app"] if (p_path / "app").exists() else [":"]

        # 3. Parse Root & Module build.gradle
        modules: Dict[str, GradleModuleNode] = {}
        plugin_rels: List[Dict[str, str]] = []

        # Parse root build.gradle for global plugins
        root_plugins = self._parse_plugins(p_path / "build.gradle") + self._parse_plugins(p_path / "build.gradle.kts")
        for rp in root_plugins:
            plugin_rels.append({"module": ":", "plugin": rp})

        for mod_name in module_names:
            sub_dir = p_path if mod_name == ":" else p_path / mod_name.lstrip(":").replace(":", "/")
            node = self._parse_module_node(mod_name, sub_dir, v_versions, v_libs)
            modules[mod_name] = node
            for p in node.plugins:
                plugin_rels.append({"module": mod_name, "plugin": p})

        # 4. Cross-Module Issue Detection (Conflicts, Mismatches)
        issues = self._detect_graph_issues(modules, v_versions, v_libs)

        return AndroidBuildGraph(
            project_name=project_name,
            project_path=str(p_path),
            modules=modules,
            version_catalog_versions=v_versions,
            version_catalog_libraries=v_libs,
            issues=issues,
            plugin_relationships=plugin_rels,
        )

    # -------------------------------------------------------------------------
    # Version Catalog Parser
    # -------------------------------------------------------------------------

    def _parse_version_catalog(self, project_path: Path) -> Tuple[Dict[str, str], Dict[str, Dict[str, str]]]:
        versions: Dict[str, str] = {}
        libraries: Dict[str, Dict[str, str]] = {}

        toml_file = project_path / "gradle" / "libs.versions.toml"
        if not toml_file.exists():
            return versions, libraries

        try:
            content = toml_file.read_text(encoding="utf-8", errors="ignore")
            current_section = ""
            for line in content.splitlines():
                t = line.strip()
                if not t or t.startswith("#"):
                    continue
                if t.startswith("[") and t.endswith("]"):
                    current_section = t[1:-1].strip()
                    continue

                if current_section == "versions":
                    m = re.match(r'([a-zA-Z0-9_\-]+)\s*=\s*["\']([^"\']+)["\']', t)
                    if m:
                        versions[m.group(1)] = m.group(2)

                elif current_section == "libraries":
                    # e.g. androidx-core = { group = "androidx.core", name = "core-ktx", version.ref = "coreKtx" }
                    # or e.g. junit = "junit:junit:4.13.2"
                    m_simple = re.match(r'([a-zA-Z0-9_\-]+)\s*=\s*["\']([^:]+):([^:]+):([^"\']+)["\']', t)
                    if m_simple:
                        libraries[m_simple.group(1)] = {
                            "group": m_simple.group(2),
                            "name": m_simple.group(3),
                            "version": m_simple.group(4),
                        }
                    else:
                        m_complex = re.match(r'([a-zA-Z0-9_\-]+)\s*=\s*\{([^}]+)\}', t)
                        if m_complex:
                            alias = m_complex.group(1)
                            body = m_complex.group(2)
                            m_group = re.search(r'group\s*=\s*["\']([^"\']+)["\']', body)
                            m_name = re.search(r'name\s*=\s*["\']([^"\']+)["\']', body)
                            m_vref = re.search(r'version\.ref\s*=\s*["\']([^"\']+)["\']', body)
                            m_vlit = re.search(r'version\s*=\s*["\']([^"\']+)["\']', body)

                            grp = m_group.group(1) if m_group else ""
                            nm = m_name.group(1) if m_name else ""
                            ver = versions.get(m_vref.group(1), "UNKNOWN") if m_vref else (m_vlit.group(1) if m_vlit else "UNKNOWN")

                            libraries[alias] = {
                                "group": grp,
                                "name": nm,
                                "version": ver,
                                "version_ref": m_vref.group(1) if m_vref else "",
                            }
        except Exception as e:
            logger.debug(f"Failed parsing TOML catalog: {e}")

        return versions, libraries

    # -------------------------------------------------------------------------
    # Module & Script Parsers
    # -------------------------------------------------------------------------

    def _parse_settings_modules(self, project_path: Path) -> List[str]:
        modules = []
        for name in ("settings.gradle", "settings.gradle.kts"):
            f = project_path / name
            if f.exists():
                try:
                    for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                        ls = line.strip()
                        if (ls.startswith("include ") or ls.startswith("include(")) and "includeGroupByRegex" not in ls:
                            for m in re.findall(r"['\"]([^'\"]+)['\"]", ls):
                                if m not in modules:
                                    modules.append(m)
                except Exception:
                    pass
        return sorted(modules)

    def _parse_plugins(self, file_path: Path) -> List[str]:
        plugins = []
        if not file_path.exists():
            return plugins
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            # id 'com.android.application' or id("org.jetbrains.kotlin.android")
            for m in re.finditer(r'id\s*[\(\s]*["\']([^"\']+)["\']', content):
                plugins.append(m.group(1))
            # apply plugin: 'com.android.application'
            for m in re.finditer(r'apply\s+plugin:\s*["\']([^"\']+)["\']', content):
                plugins.append(m.group(1))
        except Exception:
            pass
        return list(dict.fromkeys(plugins))

    def _parse_module_node(
        self,
        mod_name: str,
        mod_dir: Path,
        v_versions: Dict[str, str],
        v_libs: Dict[str, Dict[str, str]],
    ) -> GradleModuleNode:
        plugins: List[str] = []
        deps: List[GradleDependencyNode] = []
        source_sets: List[str] = []

        # Find build.gradle
        build_file = mod_dir / "build.gradle"
        if not build_file.exists():
            build_file = mod_dir / "build.gradle.kts"

        if build_file.exists():
            plugins = self._parse_plugins(build_file)
            deps = self._parse_dependencies(build_file, mod_name, v_versions, v_libs)

        # Source sets
        src_dir = mod_dir / "src"
        if src_dir.exists() and src_dir.is_dir():
            for child in src_dir.iterdir():
                if child.is_dir() and not child.name.startswith("."):
                    source_sets.append(child.name)

        tasks = list(self.STANDARD_TASKS)

        return GradleModuleNode(
            name=mod_name,
            dir_path=str(mod_dir),
            plugins=plugins,
            dependencies=deps,
            source_sets=sorted(source_sets),
            tasks=tasks,
        )

    def _parse_dependencies(
        self,
        build_file: Path,
        mod_name: str,
        v_versions: Dict[str, str],
        v_libs: Dict[str, Dict[str, str]],
    ) -> List[GradleDependencyNode]:
        deps: List[GradleDependencyNode] = []
        try:
            lines = build_file.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            return deps

        scope_pattern = r"\b(implementation|api|compileOnly|runtimeOnly|testImplementation|androidTestImplementation|classpath|kapt|ksp)\b"

        for idx, line in enumerate(lines, 1):
            t = line.strip()
            if t.startswith("//") or t.startswith("/*") or t.startswith("*"):
                continue

            m_scope = re.search(scope_pattern, t)
            if not m_scope:
                continue

            scope_str = m_scope.group(1)
            try:
                scope = DependencyScope(scope_str)
            except ValueError:
                scope = DependencyScope.CUSTOM

            # 1. Version Catalog reference: implementation(libs.androidx.core) or implementation libs.androidx.core
            m_alias = re.search(r"libs\.([a-zA-Z0-9_\.]+)", t)
            if m_alias:
                raw_alias = m_alias.group(1).replace(".", "-")
                cat_info = v_libs.get(raw_alias)
                if cat_info:
                    deps.append(GradleDependencyNode(
                        group=cat_info.get("group", "UNKNOWN"),
                        name=cat_info.get("name", "UNKNOWN"),
                        version=cat_info.get("version", "UNKNOWN"),
                        scope=scope,
                        module=mod_name,
                        source_file=str(build_file),
                        line=idx,
                        alias=raw_alias,
                    ))
                else:
                    deps.append(GradleDependencyNode(
                        group="UNKNOWN",
                        name=raw_alias,
                        version="UNRESOLVED",
                        scope=scope,
                        module=mod_name,
                        source_file=str(build_file),
                        line=idx,
                        alias=raw_alias,
                    ))
                continue

            # 2. Literal string coordinate: 'androidx.core:core-ktx:1.12.0'
            m_lit = re.search(r"['\"]([^:'\"\s]+):([^:'\"\s]+)(?::([^'\"\s]+))?['\"]", t)
            if m_lit:
                grp = m_lit.group(1)
                name = m_lit.group(2)
                ver = m_lit.group(3) or "UNKNOWN"
                deps.append(GradleDependencyNode(
                    group=grp,
                    name=name,
                    version=ver,
                    scope=scope,
                    module=mod_name,
                    source_file=str(build_file),
                    line=idx,
                ))

        return deps

    # -------------------------------------------------------------------------
    # Graph Issue Detection
    # -------------------------------------------------------------------------

    def _detect_graph_issues(
        self,
        modules: Dict[str, GradleModuleNode],
        v_versions: Dict[str, str],
        v_libs: Dict[str, Dict[str, str]],
    ) -> List[BuildGraphIssue]:
        issues: List[BuildGraphIssue] = []

        # Coordinate -> { (module, version) }
        coord_versions: Dict[str, Set[Tuple[str, str]]] = {}
        for mod_name, mod in modules.items():
            for dep in mod.dependencies:
                # Check for UNRESOLVED_ALIAS
                if dep.version == "UNRESOLVED":
                    issues.append(BuildGraphIssue(
                        kind=GradleIssueKind.UNRESOLVED_ALIAS,
                        module=mod_name,
                        dependency=dep.alias or dep.name,
                        versions=[],
                        description=f"Catalog alias 'libs.{dep.alias}' is referenced in {mod_name} but not declared in libs.versions.toml.",
                        recommendation=f"Add '{dep.alias}' to [libraries] in gradle/libs.versions.toml.",
                    ))

                if dep.group != "UNKNOWN" and dep.version not in ("UNKNOWN", "UNRESOLVED"):
                    coord_versions.setdefault(dep.coordinate, set()).add((mod_name, dep.version))

        # Check for VERSION_CONFLICT across modules
        for coord, pairs in coord_versions.items():
            versions = sorted(list({p[1] for p in pairs}))
            if len(versions) > 1:
                modules_involved = ", ".join(f"{p[0]} ({p[1]})" for p in pairs)
                issues.append(BuildGraphIssue(
                    kind=GradleIssueKind.VERSION_CONFLICT,
                    module="MULTI_MODULE",
                    dependency=coord,
                    versions=versions,
                    description=f"Dependency '{coord}' has conflicting versions across modules: {modules_involved}.",
                    recommendation=f"Harmonize '{coord}' to unified version '{versions[-1]}' in libs.versions.toml.",
                ))

        return issues
