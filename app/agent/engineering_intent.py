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

        # 2. Domain Identification
        domain = cls._identify_domain(cmd_lower, active_domain or default_domain)

        # 3. Action & Target Identification
        action, target, extracted_project, params, verif_level = cls._identify_action_and_target(
            cmd, cmd_lower, domain, active_ctx
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

        # Project extraction pattern: "called/named <name>", "project <name>", "create <name>"
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
                m_create_single = re.search(
                    r"^create\s+([A-Za-z0-9_-]+)(?:\.|$)",
                    cmd,
                    re.IGNORECASE,
                )
                if m_create_single and m_create_single.group(1).lower() not in ("a", "an", "the", "project", "new"):
                    extracted_project = m_create_single.group(1).strip()

        # -------------------------------------------------------------
        # 1. OPEN (IDE / Workspace Open)
        # Crucial disambiguation: "open android studio", "launch studio", etc.
        # -------------------------------------------------------------
        ide_open_patterns = (
            "open android studio", "launch android studio", "start android studio",
            "open studio", "launch studio", "start studio",
            "open unreal", "open unreal engine", "launch unreal",
            "open visual studio", "launch visual studio",
            "open unity", "launch unity"
        )
        if any(cmd_lower == p or cmd_lower.startswith(p + " ") or cmd_lower.endswith(" " + p) for p in ide_open_patterns):
            if "unreal" in cmd_lower:
                return EngineeringAction.OPEN, "Unreal Engine", extracted_project, params, verif_level
            elif "visual studio" in cmd_lower:
                return EngineeringAction.OPEN, "Visual Studio", extracted_project, params, verif_level
            elif "unity" in cmd_lower:
                return EngineeringAction.OPEN, "Unity", extracted_project, params, verif_level
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
        # 4. REBUILD
        # -------------------------------------------------------------
        if any(k in cmd_lower for k in ("rebuild", "clean build", "re-build", "clean and build")):
            verif_level = VerificationLevel.BUILD
            return EngineeringAction.REBUILD, None, extracted_project, params, verif_level

        # -------------------------------------------------------------
        # 5. BUILD
        # -------------------------------------------------------------
        if any(k in cmd_lower for k in ("build it", "build project", "assemble", "assembledebug", "compile", "gradle build", "build")):
            verif_level = VerificationLevel.BUILD
            return EngineeringAction.BUILD, None, extracted_project, params, verif_level

        # -------------------------------------------------------------
        # 6. RUN
        # -------------------------------------------------------------
        if any(cmd_lower == p or cmd_lower.startswith(p + " ") or cmd_lower.endswith(" " + p) for p in (
            "run it", "run", "run app", "run the app", "launch app", "launch the app", "start app", "deploy and run"
        )):
            verif_level = VerificationLevel.RUNTIME
            return EngineeringAction.RUN, None, extracted_project, params, verif_level

        # -------------------------------------------------------------
        # 7. INSTALL
        # -------------------------------------------------------------
        if any(k in cmd_lower for k in ("install apk", "install", "deploy apk", "push apk")):
            verif_level = VerificationLevel.RUNTIME
            return EngineeringAction.INSTALL, None, extracted_project, params, verif_level

        # -------------------------------------------------------------
        # 8. TEST
        # -------------------------------------------------------------
        if any(cmd_lower == "test" or cmd_lower == "test it" or cmd_lower.startswith("test ") or f" {k} " in f" {cmd_lower} " or cmd_lower.endswith(k) for k in (
            "run tests", "run test", "test project", "unit test", "unit tests", "instrumentation test",
            "diagnose test", "run the tests", "test the app", "test the project", "execute tests"
        )):
            verif_level = VerificationLevel.SYNTAX
            return EngineeringAction.TEST, None, extracted_project, params, verif_level

        # -------------------------------------------------------------
        # 9. DEBUG / FIX
        # -------------------------------------------------------------
        if any(k in cmd_lower for k in ("fix error", "repair", "fix build", "fix crash", "diagnose crash", "root cause", "fix")):
            verif_level = VerificationLevel.SYNTAX
            return EngineeringAction.FIX, None, extracted_project, params, verif_level
        if any(k in cmd_lower for k in ("debug", "diagnose", "why is", "what went wrong", "logcat crash")):
            return EngineeringAction.DEBUG, None, extracted_project, params, verif_level

        # -------------------------------------------------------------
        # 10. VERIFY
        # -------------------------------------------------------------
        if any(k in cmd_lower for k in ("verify all", "verify screen", "verify ui", "readiness audit", "audit readiness", "verify")):
            verif_level = VerificationLevel.E2E
            return EngineeringAction.VERIFY, None, extracted_project, params, verif_level

        # -------------------------------------------------------------
        # 11. INSPECT
        # -------------------------------------------------------------
        if any(k in cmd_lower for k in ("inspect project", "inspect workspace", "inspect code", "project structure", "inspect")):
            return EngineeringAction.INSPECT, None, extracted_project, params, verif_level

        # -------------------------------------------------------------
        # 12. CONTINUE_PROJECT / REFACTOR / MODIFY (Feature Refinement & Multi-turn Continuity)
        # e.g.:
        # - "Add a splash screen"
        # - "I don't like the splash screen. Make the logo smaller and center it."
        # - "Refactor MainActivity"
        # -------------------------------------------------------------
        active_feature = active_ctx.get("active_feature")

        # Check for feature refinement on active feature:
        # e.g. "I don't like...", "Change...", "Make the logo smaller...", "Center it"
        refinement_signals = (
            "don't like", "dont like", "smaller", "larger", "bigger", "center", "center it",
            "move", "change the", "update the", "adjust the", "make the"
        )
        if any(s in cmd_lower for s in refinement_signals) and (active_feature or "splash" in cmd_lower or "logo" in cmd_lower or "screen" in cmd_lower):
            target = active_feature or "splash screen"
            params["instruction"] = cmd
            return EngineeringAction.CONTINUE_PROJECT, target, extracted_project, params, VerificationLevel.SYNTAX

        # Feature addition / modification:
        # e.g. "Add a splash screen", "Add login", "Design settings screen"
        m_add = re.search(r"(?:add|create|implement|design|put)\s+(?:a\s+|an\s+)?([a-zA-Z0-9\s_-]+?)(?:\s+screen|\s+page|\s+feature|\s+view|\s+button|\s+flow|$)", cmd, re.IGNORECASE)
        if m_add and any(w in cmd_lower for w in ("splash", "login", "screen", "button", "logo", "activity", "theme")):
            raw_feature = m_add.group(1).strip()
            feature_name = f"{raw_feature} screen" if "screen" not in raw_feature.lower() and "button" not in raw_feature.lower() else raw_feature
            params["instruction"] = cmd
            return EngineeringAction.MODIFY, feature_name, extracted_project, params, VerificationLevel.SYNTAX

        if "refactor" in cmd_lower:
            return EngineeringAction.REFACTOR, active_feature or "codebase", extracted_project, params, VerificationLevel.SYNTAX

        # Fallback to MODIFY if active context has project and command expresses change
        if any(w in cmd_lower for w in ("add", "update", "modify", "edit", "change", "set", "style")):
            return EngineeringAction.MODIFY, active_feature or "project", extracted_project, params, VerificationLevel.SYNTAX

        # No engineering action recognized
        return None, None, extracted_project, params, verif_level
