"""
NR-AI Universal Engineering Intent Subsystem.

Establishes a unified, domain-aware engineering intent layer supporting:
- Multi-domain specialization (ANDROID, UNREAL, VISUAL_STUDIO, UNITY, GENERAL)
- 16 core engineering workflow actions (OPEN, CREATE_PROJECT, CONFIGURE_PROJECT,
  BUILD, RUN, INSTALL, TEST, DEBUG, INSPECT, MODIFY, DESIGN, REFACTOR, FIX,
  REBUILD, VERIFY, CONTINUE_PROJECT)
- Project continuity, pronoun resolution, and active context grounding
- Deterministic token safety against command injection
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
import re
from typing import Any, Dict, List, Optional, Set, Tuple


class EngineeringDomain(str, Enum):
    ANDROID = "ANDROID"
    UNREAL = "UNREAL"
    VISUAL_STUDIO = "VISUAL_STUDIO"
    UNITY = "UNITY"
    GENERAL = "GENERAL"


class EngineeringAction(str, Enum):
    OPEN = "OPEN"
    CREATE_PROJECT = "CREATE_PROJECT"
    CONFIGURE_PROJECT = "CONFIGURE_PROJECT"
    BUILD = "BUILD"
    RUN = "RUN"
    INSTALL = "INSTALL"
    TEST = "TEST"
    DEBUG = "DEBUG"
    INSPECT = "INSPECT"
    MODIFY = "MODIFY"
    DESIGN = "DESIGN"
    REFACTOR = "REFACTOR"
    FIX = "FIX"
    REBUILD = "REBUILD"
    VERIFY = "VERIFY"
    CONTINUE_PROJECT = "CONTINUE_PROJECT"


class VerificationLevel(str, Enum):
    NONE = "NONE"
    SYNTAX = "SYNTAX"
    BUILD = "BUILD"
    RUNTIME = "RUNTIME"
    E2E = "E2E"
    AUDIT = "AUDIT"


PROHIBITED_INJECTION_TOKENS: Set[str] = {
    "cmd.exe",
    "powershell",
    "bash",
    "sh -c",
    "rm -rf",
    "format c:",
    "del /f",
    "drop database",
    "curl | sh",
    "Invoke-Expression",
    "iex ",
    "../",
    "..\\",
    ";",
    "&&",
    "|",
}


@dataclass
class EngineeringIntent:
    """Canonical structured representation of a developer's engineering intent."""
    domain: EngineeringDomain
    action: EngineeringAction
    project: Optional[str] = None
    target: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    current_project_context: Optional[Dict[str, Any]] = None
    requested_verification_level: str = VerificationLevel.NONE.value
    raw_command: str = ""
    is_valid: bool = True
    rejection_reason: Optional[str] = None

    @property
    def instruction(self) -> str:
        return self.parameters.get("instruction") or self.raw_command or ""

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["domain"] = self.domain.value
        data["action"] = self.action.value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EngineeringIntent:
        d = dict(data)
        d["domain"] = EngineeringDomain(d.get("domain", EngineeringDomain.GENERAL.value))
        d["action"] = EngineeringAction(d.get("action", EngineeringAction.INSPECT.value))
        return cls(**d)


class EngineeringIntentParser:
    """
    Deterministic natural-language engineering workflow intent parser.
    Disambiguates IDE open commands from project-level engineering workflows.
    Maintains project continuity and active feature grounding across consecutive turns.
    """

    @classmethod
    def parse(
        cls,
        command: str,
        active_context: Optional[Dict[str, Any]] = None,
        default_domain: Optional[EngineeringDomain] = None,
    ) -> EngineeringIntent:
        cmd = (command or "").strip()
        cmd_lower = cmd.lower()

        # 1. Injection Defense
        for token in PROHIBITED_INJECTION_TOKENS:
            if token in cmd_lower:
                return EngineeringIntent(
                    domain=EngineeringDomain.GENERAL,
                    action=EngineeringAction.INSPECT,
                    raw_command=cmd,
                    is_valid=False,
                    rejection_reason=f"Prohibited execution token detected: '{token}'.",
                )

        if not cmd:
            return EngineeringIntent(
                domain=EngineeringDomain.GENERAL,
                action=EngineeringAction.INSPECT,
                raw_command="",
                is_valid=False,
                rejection_reason="Command cannot be empty.",
            )

        active_ctx = active_context or {}
        active_project_name = active_ctx.get("project_name") or active_ctx.get("project_id")
        active_domain_str = active_ctx.get("domain")
        active_domain = None
        if active_domain_str:
            try:
                active_domain = EngineeringDomain(active_domain_str)
            except Exception:
                active_domain = None

        # Strip conversational preambles/polite requests (e.g. "can you", "please", "hey hi")
        preamble_regex = r"^(?:(?:hey|hi|hello|yo|please|can you|could you|would you|activate|switch to|select|talk to|go to|wake up)\s+)+"
        clean_cmd = re.sub(preamble_regex, "", cmd, flags=re.IGNORECASE).strip() or cmd
        clean_cmd_lower = clean_cmd.lower()

        # 2. Domain Identification
        domain = cls._identify_domain(clean_cmd_lower, active_domain or default_domain)

        # 3. Action & Target Identification
        action, target, extracted_project, params, verif_level = cls._identify_action_and_target(
            clean_cmd,
            clean_cmd_lower,
            domain,
            active_ctx,
        )
        if action is None:
            return EngineeringIntent(
                domain=domain,
                action=EngineeringAction.INSPECT,
                raw_command=cmd,
                is_valid=False,
                rejection_reason="No engineering action recognized in command.",
            )

        # 4. Project Name Resolution
        resolved_project = extracted_project or active_project_name
        if not resolved_project:
            if domain == EngineeringDomain.ANDROID:
                resolved_project = "nr_android_test"
            elif domain == EngineeringDomain.UNREAL:
                resolved_project = "NRUnrealProject"

        return EngineeringIntent(
            domain=domain,
            action=action,
            project=resolved_project,
            target=target,
            parameters=params,
            current_project_context=active_ctx if active_ctx else None,
            requested_verification_level=verif_level.value if isinstance(verif_level, VerificationLevel) else str(verif_level),
            raw_command=cmd,
            is_valid=True,
            rejection_reason=None,
        )

    @classmethod
    def _identify_domain(
        cls,
        cmd_lower: str,
        fallback_domain: Optional[EngineeringDomain] = None,
    ) -> EngineeringDomain:
        """Determines the engineering domain from command text and context."""
        unreal_keywords = ["unreal", "unreal engine", "ue5", "ue4", "uproject", "unrealbuildtool", "ubt"]
        android_keywords = [
            "android", "android studio", "kotlin", "apk", "gradle", "jetpack compose",
            "compose", "logcat", "avd", "emulator", "manifest", "droid"
        ]
        vs_keywords = ["visual studio", "devenv", "msbuild", ".sln", ".csproj", "dotnet", "vs:", "vs ", "solution"]
        unity_keywords = ["unity", "unity editor", "playmode", "editmode", "monobehaviour", "unity:"]

        # Explicit domain mentions take precedence
        if any(k in cmd_lower for k in unreal_keywords) or cmd_lower.startswith("unreal:"):
            return EngineeringDomain.UNREAL
        if any(k in cmd_lower for k in android_keywords) or cmd_lower.startswith(("android:", "droid:")):
            return EngineeringDomain.ANDROID
        if any(k in cmd_lower for k in vs_keywords) or cmd_lower.startswith(("vs:", "dotnet:", "msbuild:")):
            return EngineeringDomain.VISUAL_STUDIO
        if any(k in cmd_lower for k in unity_keywords) or cmd_lower.startswith("unity:"):
            return EngineeringDomain.UNITY

        # Fallback to active context domain if provided
        if fallback_domain:
            return fallback_domain

        return EngineeringDomain.GENERAL

    @classmethod
    def _identify_action_and_target(
        cls,
        cmd: str,
        cmd_lower: str,
        domain: EngineeringDomain,
        active_ctx: Dict[str, Any],
    ) -> Tuple[EngineeringAction, Optional[str], Optional[str], Dict[str, Any], VerificationLevel]:
        """Extracts action, target, project name, parameters, and verification level."""
        params: Dict[str, Any] = {}
        verif_level = VerificationLevel.NONE

        # Language extraction
        if "kotlin" in cmd_lower:
            params["language"] = "Kotlin"
        elif "java" in cmd_lower and "javascript" not in cmd_lower:
            params["language"] = "Java"
        elif "c++" in cmd_lower or "cpp" in cmd_lower:
            params["language"] = "C++"
        elif "c#" in cmd_lower or "csharp" in cmd_lower:
            params["language"] = "C#"

        # Project extraction pattern: "called/named <name>", "project <name>", "create <name>", "open the <name> project"
        extracted_project: Optional[str] = None
        m_named = re.search(r"(?:called|named)\s+([A-Za-z0-9_-]+)", cmd, re.IGNORECASE)
        if m_named:
            extracted_project = m_named.group(1).strip()
        else:
            m_create_proj = re.search(
                r"(?:create|new)\s+(?:an?\s+)?(?:android\s+|unreal\s+)?project\s+([A-Za-z0-9_-]+)",
                cmd,
                re.IGNORECASE,
            )
            if m_create_proj:
                extracted_project = m_create_proj.group(1).strip()
            else:
                m_the_proj = re.search(
                    r"(?:open|load|import|switch\s+to|view)?\s*(?:the\s+|our\s+|my\s+)?([A-Za-z0-9_-]+)\s+project",
                    cmd,
                    re.IGNORECASE,
                )
                if m_the_proj and m_the_proj.group(1).lower() not in ("a", "an", "the", "this", "new", "my", "our", "active", "android", "unreal", "unity", "existing", "open"):
                    extracted_project = m_the_proj.group(1).strip()
                else:
                    m_proj_named = re.search(
                        r"project\s+(?:called\s+|named\s+)?([A-Za-z0-9_-]+)",
                        cmd,
                        re.IGNORECASE,
                    )
                    if m_proj_named and m_proj_named.group(1).lower() not in ("called", "named", "in", "with", "for", "on", "a", "an", "the", "this", "new", "my", "active", "android", "unreal", "unity"):
                        extracted_project = m_proj_named.group(1).strip()
                    else:
                        m_create_single = re.search(
                            r"^create\s+([A-Za-z0-9_-]+)(?:\.|$)",
                            cmd,
                            re.IGNORECASE,
                        )
                        if m_create_single and m_create_single.group(1).lower() not in ("a", "an", "the", "project", "new"):
                            extracted_project = m_create_single.group(1).strip()

        # -------------------------------------------------------------
        # 1. OPEN (IDE / Workspace Open)
        # Crucial disambiguation: "open android studio", "launch studio", "open the project", etc.
        # -------------------------------------------------------------
        cmd_clean = cmd_lower.strip(".?! ")
        ide_open_patterns = (
            "open android studio", "launch android studio", "start android studio",
            "open studio", "launch studio", "start studio",
            "open unreal", "open unreal engine", "launch unreal",
            "open visual studio", "launch visual studio",
            "open unity", "launch unity",
            "open the project", "open project", "open workspace", "open"
        )
        if any(cmd_clean == p or cmd_clean.startswith(p + " ") or cmd_clean.endswith(" " + p) or cmd_lower == p or cmd_lower.startswith(p + " ") or cmd_lower.endswith(" " + p) for p in ide_open_patterns):
            if "unreal" in cmd_lower:
                return EngineeringAction.OPEN, "Unreal Engine", extracted_project, params, verif_level
            elif "visual studio" in cmd_lower:
                return EngineeringAction.OPEN, "Visual Studio", extracted_project, params, verif_level
            elif "unity" in cmd_lower:
                return EngineeringAction.OPEN, "Unity", extracted_project, params, verif_level
            elif any(k in cmd_clean for k in ("project", "workspace")):
                return EngineeringAction.OPEN, "project", extracted_project, params, verif_level
            else:
                return EngineeringAction.OPEN, "Android Studio", extracted_project, params, verif_level

        # -------------------------------------------------------------
        # 2. CREATE_PROJECT
        # e.g. "Create a new Android project called MyApp using Kotlin", "Create MyApp"
        # -------------------------------------------------------------
        if (
            any(k in cmd_lower for k in ("create a new", "create new project", "scaffold project", "new project", "create android project", "create unreal project"))
            or (cmd_lower.startswith("create ") and extracted_project and not any(f in cmd_lower for f in ("screen", "button", "activity", "feature", "fragment", "dialog", "view")))
        ):
            if "template" not in params:
                if "login" in cmd_lower:
                    params["template"] = "login_activity"
                else:
                    params["template"] = "empty_activity"
            return EngineeringAction.CREATE_PROJECT, None, extracted_project, params, verif_level

        # -------------------------------------------------------------
        # 3. CONFIGURE_PROJECT
        # -------------------------------------------------------------
        if any(k in cmd_lower for k in ("configure project", "setup project", "update gradle", "configure manifest", "set package")):
            return EngineeringAction.CONFIGURE_PROJECT, None, extracted_project, params, verif_level

        # -------------------------------------------------------------
        # 4. DEBUG / FIX (Must evaluate before BUILD to avoid "build fail" match)
        # -------------------------------------------------------------
        if any(k in cmd_lower for k in (
            "find the problem and fix it", "fix the issue", "fix issue", "fix error",
            "repair", "fix build", "fix crash", "diagnose crash", "root cause", "fix the problem", "fix problem"
        )) or cmd_lower == "fix" or cmd_lower.startswith("fix "):
            verif_level = VerificationLevel.SYNTAX
            return EngineeringAction.FIX, None, extracted_project, params, verif_level

        if any(k in cmd_lower for k in (
            "why did the build fail", "why did build fail", "why did it fail",
            "find any issues", "find issues", "find problem", "find the problem",
            "debug", "diagnose", "why is", "what went wrong", "logcat crash", "check errors", "what is the error"
        )):
            verif_level = VerificationLevel.SYNTAX
            return EngineeringAction.DEBUG, None, extracted_project, params, verif_level

        # -------------------------------------------------------------
        # 5. REBUILD
        # -------------------------------------------------------------
        if any(k in cmd_lower for k in ("rebuild", "clean build", "re-build", "clean and build")):
            verif_level = VerificationLevel.BUILD
            return EngineeringAction.REBUILD, None, extracted_project, params, verif_level

        # -------------------------------------------------------------
        # 6. BUILD
        # -------------------------------------------------------------
        if any(k in cmd_lower for k in ("build it", "build project", "assemble", "assembledebug", "compile", "gradle build", "build the project", "build")):
            verif_level = VerificationLevel.BUILD
            return EngineeringAction.BUILD, None, extracted_project, params, verif_level

        # -------------------------------------------------------------
        # 7. RUN
        # -------------------------------------------------------------
        if any(cmd_lower == p or cmd_lower.startswith(p + " ") or cmd_lower.endswith(" " + p) for p in (
            "run it", "run it again", "run again", "run", "run app", "run the app", "launch app", "launch the app", "start app", "deploy and run", "test run"
        )):
            verif_level = VerificationLevel.RUNTIME
            return EngineeringAction.RUN, None, extracted_project, params, verif_level

        # -------------------------------------------------------------
        # 8. INSTALL
        # -------------------------------------------------------------
        if any(k in cmd_lower for k in ("install apk", "install", "deploy apk", "push apk", "install dependency", "install an unavailable dependency")):
            verif_level = VerificationLevel.RUNTIME
            m_dep = re.search(r"(?:dependency|package|lib)\s+(?:called\s+)?([A-Za-z0-9_.-]+)", cmd, re.IGNORECASE)
            dep_name = m_dep.group(1).strip().rstrip(".?! ") if m_dep else None
            if dep_name:
                params["dependency"] = dep_name
            return EngineeringAction.INSTALL, dep_name, extracted_project, params, verif_level

        # -------------------------------------------------------------
        # 9. TEST
        # -------------------------------------------------------------
        if any(cmd_lower == "test" or cmd_lower == "test it" or cmd_lower == "test it again" or cmd_lower.startswith("test ") or f" {k} " in f" {cmd_lower} " or cmd_lower.endswith(k) for k in (
            "run tests", "run test", "test project", "unit test", "unit tests", "instrumentation test",
            "diagnose test", "run the tests", "test the app", "test the project", "execute tests", "test it again", "retest"
        )):
            verif_level = VerificationLevel.SYNTAX
            return EngineeringAction.TEST, None, extracted_project, params, verif_level

        # -------------------------------------------------------------
        # 10. VERIFY
        # -------------------------------------------------------------
        if any(k in cmd_lower for k in ("verify all", "verify screen", "verify ui", "readiness audit", "audit readiness", "verify project", "verify")):
            verif_level = VerificationLevel.E2E
            return EngineeringAction.VERIFY, None, extracted_project, params, verif_level

        # -------------------------------------------------------------
        # 11. INSPECT
        # -------------------------------------------------------------
        if any(k in cmd_lower for k in ("show me what changed", "what changed", "show changes", "show diff", "git diff", "inspect project", "inspect workspace", "inspect code", "project structure", "inspect")):
            target = "changes" if any(w in cmd_lower for w in ("changed", "diff", "changes")) else None
            return EngineeringAction.INSPECT, target, extracted_project, params, verif_level

        # -------------------------------------------------------------
        # 12. CONTINUE_PROJECT / REFACTOR / MODIFY (Feature Refinement & Multi-turn Continuity)
        # -------------------------------------------------------------
        active_feature = active_ctx.get("active_feature")

        # Check for feature refinement on active feature:
        refinement_signals = (
            "don't like", "dont like", "smaller", "larger", "bigger", "center", "center it",
            "move", "move it", "change the", "update the", "adjust the", "make the", "style the",
            "to the center"
        )
        element_signals = (
            "splash", "logo", "screen", "button", "text", "welcome", "background",
            "color", "layout", "view", "activity", "header", "title"
        )
        has_refinement = any(s in cmd_lower for s in refinement_signals)
        has_element = any(e in cmd_lower for e in element_signals) or " it" in cmd_lower or cmd_lower.endswith(" it")
        if has_refinement and (active_feature or has_element):
            target = active_feature
            if "logo" in cmd_lower:
                target = "logo" if not active_feature else active_feature
            elif "button" in cmd_lower:
                target = "button"
            elif "welcome" in cmd_lower:
                target = "welcome screen"
            elif "splash" in cmd_lower:
                target = "splash screen"
            elif not target:
                target = "layout"
            params["instruction"] = cmd
            return EngineeringAction.CONTINUE_PROJECT, target, extracted_project, params, VerificationLevel.SYNTAX

        # Specific activity / screen creation:
        m_act = re.search(r"(?:create|add)\s+(?:a\s+|an\s+)?([A-Za-z0-9_]+Activity|[A-Za-z0-9_]+\s+activity|new activity)", cmd, re.IGNORECASE)
        if m_act or "activity" in cmd_lower:
            act_name = "MainActivity" if "mainactivity" in cmd_lower or "main activity" in cmd_lower else (m_act.group(1).strip() if m_act else "NewActivity")
            params["instruction"] = cmd
            params["activity_name"] = act_name
            target = "welcome screen" if "welcome" in cmd_lower else act_name
            return EngineeringAction.MODIFY, target, extracted_project, params, VerificationLevel.SYNTAX

        # Feature addition / modification:
        m_add = re.search(r"(?:add|create|implement|design|put)\s+(?:a\s+|an\s+)?([a-zA-Z0-9\s_-]+?)(?:\s+screen|\s+page|\s+feature|\s+view|\s+button|\s+flow|$)", cmd, re.IGNORECASE)
        if m_add and any(w in cmd_lower for w in ("splash", "login", "screen", "button", "logo", "activity", "theme", "welcome")):
            raw_feature = m_add.group(1).strip()
            feature_name = f"{raw_feature} screen" if "screen" not in raw_feature.lower() and "button" not in raw_feature.lower() else raw_feature
            params["instruction"] = cmd
            return EngineeringAction.MODIFY, feature_name, extracted_project, params, VerificationLevel.SYNTAX

        if "refactor" in cmd_lower:
            return EngineeringAction.REFACTOR, active_feature or "codebase", extracted_project, params, VerificationLevel.SYNTAX

        # Fallback to MODIFY if active context has project and command expresses change
        if any(w in cmd_lower for w in ("add", "update", "modify", "edit", "change", "set", "style")):
            params["instruction"] = cmd
            return EngineeringAction.MODIFY, active_feature or "project", extracted_project, params, VerificationLevel.SYNTAX

        # No engineering action recognized
        return None, None, extracted_project, params, verif_level


def canonicalize_agent_id(raw_id: Optional[str]) -> str:
    """
    Maps various aliases, class names, or friendly names to a stable canonical agent ID.
    Examples:
      'android_unified_agent' -> 'droid'
      'droid_scout' -> 'droid_scout'
      'vs_unified_agent' -> 'studio'
      'unity_autonomous_agent' -> 'unity'
      'unreal_autonomous_agent' -> 'unreal'
      'universal_knowledge_engine' -> 'knowledge'
      'nr_ai_central_intelligence' -> 'nr_ai'
    """
    if not raw_id:
        return "nr_ai"
    aid = str(raw_id).lower().strip().replace("-", "_").replace(" ", "_")
    if aid in ("nr_ai", "nr_ai_central_intelligence", "central", "root", "nrai", "nr ai", "central_intelligence"):
        return "nr_ai"
    aliases = {
        "android_unified_agent": "droid",
        "android": "droid",
        "android_agent": "droid",
        "droid": "droid",
        "droid_scout": "droid_scout",
        "scout": "droid_scout",
        "droid_guardian": "droid_guardian",
        "guardian": "droid_guardian",
        "vs_unified_agent": "studio",
        "visual_studio": "studio",
        "visual_studio_agent": "studio",
        "studio": "studio",
        "vs": "studio",
        "unity_autonomous_agent": "unity",
        "unity": "unity",
        "unreal_autonomous_agent": "unreal",
        "unreal": "unreal",
        "security_agent": "skyshield",
        "shield": "skyshield",
        "security": "skyshield",
        "skyshield": "skyshield",
        "universal_knowledge_engine": "knowledge",
        "knowledge": "knowledge",
        "oracle": "knowledge",
        "nova_discovery_agent": "nova",
        "nova": "nova",
        "aegis_verification_agent": "aegis",
        "aegis": "aegis",
        "research_agent": "quest",
        "quest": "quest",
        "research": "quest",
        "vision_agent": "vision",
        "vision": "vision",
        "computer_control_agent": "sentinel",
        "sentinel": "sentinel",
        "computer": "sentinel",
        "forge_dev_agent": "forge",
        "forge": "forge",
        "pixel_ui_agent": "pixel",
        "pixel": "pixel",
        "nexus_coordinator": "nexus",
        "nexus": "nexus",
        "coordinator": "nexus",
    }
    if aid in aliases:
        return aliases[aid]
    clean = aid.replace("_autonomous_agent", "").replace("_unified_agent", "").replace("_agent", "")
    return clean
