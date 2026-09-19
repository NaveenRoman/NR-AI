"""
NR-AI Android Studio Workspace & ProGuard/R8 Inspector (Droid Phase 5).

Provides deep inspection of Android Studio / IntelliJ workspace settings:
- .idea/gradle.xml (Gradle JDK, Gradle home, linked projects)
- .idea/runConfigurations/ (Run/debug launch configs)
- .idea/compiler.xml (Annotation processors, bytecode target)
- ProGuard / R8 minification rule analysis (proguard-rules.pro, -keep syntax)
"""

from dataclasses import dataclass, field, asdict
import logging
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Set, Tuple, Union

logger = logging.getLogger("NRAI.AndroidStudioWorkspace")


@dataclass
class ProGuardRule:
    directive: str
    target_class: str
    members: str = ""
    options: str = ""
    line_number: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StudioWorkspaceSnapshot:
    project_path: str
    gradle_jdk_name: Optional[str] = None
    gradle_home: Optional[str] = None
    linked_modules: List[str] = field(default_factory=list)
    run_configurations: List[Dict[str, Any]] = field(default_factory=list)
    compiler_target_bytecode: Optional[str] = None
    proguard_files: List[str] = field(default_factory=list)
    proguard_rules_count: int = 0
    proguard_warnings: List[str] = field(default_factory=list)
    has_idea_dir: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["gradle_jvm_version"] = self.gradle_jdk_name
        return d

    @property
    def gradle_jvm_version(self) -> Optional[str]:
        return self.gradle_jdk_name


class AndroidStudioWorkspaceEngine:
    """Deep inspector for Android Studio workspace configurations and ProGuard/R8 rules."""

    def inspect_workspace(self, project_path: Union[str, Path]) -> StudioWorkspaceSnapshot:
        proj = Path(project_path).resolve()
        idea_dir = proj / ".idea"
        has_idea = idea_dir.exists() and idea_dir.is_dir()

        jdk_name, gradle_home, modules = self.parse_gradle_xml(idea_dir)
        run_configs = self.parse_run_configurations(idea_dir)
        compiler_info = self.parse_compiler_xml(idea_dir)
        rules, warnings, pg_files = self.analyze_proguard_rules(proj)

        return StudioWorkspaceSnapshot(
            project_path=str(proj),
            gradle_jdk_name=jdk_name,
            gradle_home=gradle_home,
            linked_modules=modules,
            run_configurations=run_configs,
            compiler_target_bytecode=compiler_info.get("target_bytecode"),
            proguard_files=[str(p) for p in pg_files],
            proguard_rules_count=len(rules),
            proguard_warnings=warnings,
            has_idea_dir=has_idea,
        )

    def parse_gradle_xml(self, idea_dir: Path) -> Tuple[Optional[str], Optional[str], List[str]]:
        gradle_xml = idea_dir / "gradle.xml"
        if not gradle_xml.exists():
            return None, None, []

        try:
            tree = ET.parse(gradle_xml)
            root = tree.getroot()
            jdk_name = None
            gradle_home = None
            modules: List[str] = []

            for elem in root.iter("option"):
                if elem.get("name") == "gradleJvm":
                    jdk_name = elem.get("value")
                elif elem.get("name") == "gradleHome":
                    gradle_home = elem.get("value")

            for mod in root.iter("GradleProjectSettings"):
                ext_path = mod.get("externalProjectPath")
                if ext_path:
                    modules.append(Path(ext_path).name)
                for opt in mod.iter("option"):
                    opt_name = opt.get("name")
                    if opt_name == "externalProjectPath":
                        val = opt.get("value")
                        if val:
                            modules.append(Path(val).name)
                    elif opt_name == "modules":
                        for set_elem in opt.iter("set"):
                            for opt_sub in set_elem.iter("option"):
                                val = opt_sub.get("value")
                                if val:
                                    modules.append(Path(val).name)

            return jdk_name, gradle_home, list(dict.fromkeys(modules))
        except Exception as e:
            logger.warning(f"Error parsing gradle.xml: {e}")
            return None, None, []

    def parse_run_configurations(self, idea_dir: Path) -> List[Dict[str, Any]]:
        rc_dir = idea_dir / "runConfigurations"
        configs: List[Dict[str, Any]] = []
        if not rc_dir.exists():
            # Also check workspace.xml for inline run configurations
            ws_xml = idea_dir / "workspace.xml"
            if ws_xml.exists():
                try:
                    tree = ET.parse(ws_xml)
                    for cfg in tree.getroot().iter("configuration"):
                        name = cfg.get("name")
                        cfg_type = cfg.get("type")
                        if name and cfg_type:
                            configs.append({"name": name, "type": cfg_type})
                except Exception:
                    pass
            return configs

        for f in rc_dir.glob("*.xml"):
            try:
                tree = ET.parse(f)
                root = tree.getroot()
                cfg = root if root.tag == "configuration" else root.find("configuration")
                if cfg is not None:
                    configs.append({
                        "name": cfg.get("name", f.stem),
                        "type": cfg.get("type", "unknown"),
                        "file": f.name,
                    })
            except Exception as e:
                logger.warning(f"Failed parsing run config {f.name}: {e}")
        return configs

    def parse_compiler_xml(self, idea_dir: Path) -> Dict[str, Any]:
        comp_xml = idea_dir / "compiler.xml"
        res: Dict[str, Any] = {}
        if not comp_xml.exists():
            return res

        try:
            tree = ET.parse(comp_xml)
            root = tree.getroot()
            for opt in root.iter("bytecodeTargetLevel"):
                res["target_bytecode"] = opt.get("target")
        except Exception as e:
            logger.warning(f"Failed parsing compiler.xml: {e}")
        return res

    def analyze_proguard_rules(self, project_path: Path) -> Tuple[List[ProGuardRule], List[str], List[Path]]:
        rules: List[ProGuardRule] = []
        warnings: List[str] = []
        pg_files = list(project_path.glob("**/proguard-rules.pro")) + list(project_path.glob("**/consumer-rules.pro"))

        for pg in pg_files:
            try:
                lines = pg.read_text(encoding="utf-8", errors="ignore").splitlines()
                for idx, line in enumerate(lines, start=1):
                    raw = line.strip()
                    if not raw or raw.startswith("#"):
                        continue
                    m = re.match(r"^-(keep(?:classeswithmembers|classmembers|names)?)\s+([^{]+)(?:\{(.*)\})?", raw)
                    if m:
                        directive = m.group(1)
                        target = m.group(2).strip()
                        members = m.group(3).strip() if m.group(3) else ""
                        rules.append(ProGuardRule(directive=directive, target_class=target, members=members, line_number=idx))
                    elif raw.startswith("-"):
                        parts = raw.split(maxsplit=1)
                        directive = parts[0][1:]
                        opts = parts[1] if len(parts) > 1 else ""
                        rules.append(ProGuardRule(directive=directive, target_class="", options=opts, line_number=idx))

                content = "\n".join(lines)
                if "-keep" not in content and "getDefaultProguardFile" not in content:
                    warnings.append(f"{pg.name}: No -keep directives found. Ensure models/entities are preserved.")
            except Exception as e:
                warnings.append(f"Error reading {pg.name}: {e}")

        return rules, warnings, pg_files
