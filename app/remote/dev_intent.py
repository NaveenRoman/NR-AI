"""
NR-AI Structured Development Intent Model and Parser (Step 10).

Formalizes natural-language developer intent representation:
- Target Environment (Android, Visual Studio, Unity, Unreal, Desktop)
- Workflow Type (Create, Build, Inspect, Repair, Deploy, OpenApp, General)
- Project Name and Template Extraction
- Security and Injection Defense
"""

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any, Dict, List, Optional


class TargetEnvironment(str, Enum):
    ANDROID = "ANDROID"
    VISUAL_STUDIO = "VISUAL_STUDIO"
    UNITY = "UNITY"
    UNREAL = "UNREAL"
    DESKTOP = "DESKTOP"
    UNKNOWN = "UNKNOWN"


class DevelopmentWorkflowType(str, Enum):
    CREATE_PROJECT = "CREATE_PROJECT"
    BUILD_PROJECT = "BUILD_PROJECT"
    INSPECT_PROJECT = "INSPECT_PROJECT"
    DIAGNOSE_REPAIR = "DIAGNOSE_REPAIR"
    DEPLOY_RUN = "DEPLOY_RUN"
    OPEN_APP = "OPEN_APP"
    GENERAL_COMMAND = "GENERAL_COMMAND"


PROHIBITED_INJECTION_TOKENS = [
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
]


@dataclass
class DevelopmentIntent:
    """Strict structured intent representing a natural-language developer command."""
    target_environment: TargetEnvironment
    application: str
    workflow_type: DevelopmentWorkflowType
    project_name: str
    requested_actions: List[str]
    natural_language_command: str
    language: str = "Kotlin"
    template: str = ""
    is_valid: bool = True
    rejection_reason: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)

    @property
    def raw_text(self) -> str:
        return self.natural_language_command

    @property
    def launch_toolchain(self) -> bool:
        cmd_lower = self.natural_language_command.lower()
        return any(k in cmd_lower for k in ("open android studio", "launch android studio", "start android studio", "open studio"))

    @property
    def build_required(self) -> bool:
        return "build_project" in self.requested_actions or "verify_compilation" in self.requested_actions or self.workflow_type == DevelopmentWorkflowType.BUILD_PROJECT

    @property
    def activity_type(self) -> str:
        return self.template or "login"

    @property
    def package_name(self) -> str:
        clean = re.sub(r"[^a-zA-Z0-9]", "", self.project_name).lower()
        return f"com.nrai.{clean or 'devapp'}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_environment": self.target_environment.value,
            "application": self.application,
            "workflow_type": self.workflow_type.value,
            "project_name": self.project_name,
            "requested_actions": list(self.requested_actions),
            "natural_language_command": self.natural_language_command,
            "language": self.language,
            "template": self.template,
            "is_valid": self.is_valid,
            "rejection_reason": self.rejection_reason,
            "parameters": dict(self.parameters),
        }


class DevelopmentIntentParser:
    """
    Deterministic natural-language developer intent parser.
    Disambiguates single application launches from composite multi-step workflows.
    Categorizes workflows into specialized agent domains without naive prefix truncation.
    """

    @classmethod
    def is_development_command(cls, command: str) -> bool:
        """Determines if command implies a specialized development workflow rather than a simple OS action."""
        cmd_lower = (command or "").strip().lower()
        if not cmd_lower:
            return False
        dev_action_keywords = ["create", "scaffold", "new project", "build", "compile", "assemble", "verify", "repair", "fix", "diagnose"]
        dev_domain_keywords = ["android", "kotlin", "login activity", "visual studio", "unity", "unreal", "c#", ".sln", ".csproj"]

        has_action = any(k in cmd_lower for k in dev_action_keywords)
        has_domain = any(k in cmd_lower for k in dev_domain_keywords)

        if has_action and has_domain:
            return True
        if "and create" in cmd_lower or "and build" in cmd_lower or "with a login" in cmd_lower:
            return True
        return False

    @classmethod
    def parse_intent(cls, command: str) -> DevelopmentIntent:
        cmd = (command or "").strip()
        cmd_lower = cmd.lower()

        # 1. Injection and Malicious Token Gate
        for token in PROHIBITED_INJECTION_TOKENS:
            if token in cmd_lower:
                return DevelopmentIntent(
                    target_environment=TargetEnvironment.UNKNOWN,
                    application="",
                    workflow_type=DevelopmentWorkflowType.GENERAL_COMMAND,
                    project_name="",
                    requested_actions=[],
                    natural_language_command=cmd,
                    is_valid=False,
                    rejection_reason=f"Prohibited execution token detected: '{token}'.",
                )

        if not cmd:
            return DevelopmentIntent(
                target_environment=TargetEnvironment.UNKNOWN,
                application="",
                workflow_type=DevelopmentWorkflowType.GENERAL_COMMAND,
                project_name="",
                requested_actions=[],
                natural_language_command="",
                is_valid=False,
                rejection_reason="Command cannot be empty.",
            )

        # 2. Target Environment Identification
        target_env = TargetEnvironment.UNKNOWN
        app_name = ""

        # Android markers
        android_markers = [
            "android", "android studio", "kotlin", "apk", "login activity",
            "activity", "gradle", "jetpack compose", "android project"
        ]
        # Visual Studio / C# markers
        vs_markers = [
            "visual studio", "devenv", "c#", ".sln", ".csproj",
            "msbuild", "wpf", "winforms", "dotnet build", "dotnet test"
        ]
        # Unity markers
        unity_markers = [
            "unity", "unity editor", "unityproject", "playmode", "editmode",
            "gameobject", "monobehaviour"
        ]
        # Unreal markers
        unreal_markers = [
            "unreal", "unreal engine", "ue5", "uproject", "unrealbuildtool", "ubt"
        ]

        if any(m in cmd_lower for m in android_markers):
            target_env = TargetEnvironment.ANDROID
            app_name = "Android Studio"
        elif any(m in cmd_lower for m in vs_markers):
            target_env = TargetEnvironment.VISUAL_STUDIO
            app_name = "Visual Studio"
        elif any(m in cmd_lower for m in unity_markers):
            target_env = TargetEnvironment.UNITY
            app_name = "Unity"
        elif any(m in cmd_lower for m in unreal_markers):
            target_env = TargetEnvironment.UNREAL
            app_name = "Unreal Engine"
        elif cmd_lower.startswith(("open ", "launch ", "start ")):
            target_env = TargetEnvironment.DESKTOP
            raw_target = re.sub(r"^(?:open|launch|start)\s+", "", cmd, flags=re.IGNORECASE).strip().rstrip(".?!")
            app_name = raw_target
        else:
            target_env = TargetEnvironment.DESKTOP

        # 3. Workflow Type Determination
        workflow_type = DevelopmentWorkflowType.GENERAL_COMMAND
        requested_actions: List[str] = []

        create_patterns = ["create", "scaffold", "new project", "make a new project", "generate"]
        build_patterns = ["build", "compile", "assemble", "verify that it compiles", "assembledebug"]
        repair_patterns = ["repair", "fix", "diagnose", "resolve error"]
        inspect_patterns = ["inspect", "status", "check project", "structure"]
        deploy_patterns = ["deploy", "install apk", "launch app"]

        is_create = any(p in cmd_lower for p in create_patterns)
        is_build = any(p in cmd_lower for p in build_patterns)
        is_repair = any(p in cmd_lower for p in repair_patterns)
        is_inspect = any(p in cmd_lower for p in inspect_patterns)
        is_deploy = any(p in cmd_lower for p in deploy_patterns)

        if is_create:
            workflow_type = DevelopmentWorkflowType.CREATE_PROJECT
            requested_actions.append("create_project")
            if is_build:
                requested_actions.append("build_project")
                requested_actions.append("verify_compilation")
        elif is_repair:
            workflow_type = DevelopmentWorkflowType.DIAGNOSE_REPAIR
            requested_actions.append("diagnose_repair")
        elif is_build:
            workflow_type = DevelopmentWorkflowType.BUILD_PROJECT
            requested_actions.append("build_project")
        elif is_inspect:
            workflow_type = DevelopmentWorkflowType.INSPECT_PROJECT
            requested_actions.append("inspect_project")
        elif is_deploy:
            workflow_type = DevelopmentWorkflowType.DEPLOY_RUN
            requested_actions.append("deploy_run")
        elif cmd_lower.startswith(("open ", "launch ", "start ")) and not (is_create or is_build or is_repair):
            workflow_type = DevelopmentWorkflowType.OPEN_APP
            requested_actions.append("open_app")
        else:
            workflow_type = DevelopmentWorkflowType.GENERAL_COMMAND
            requested_actions.append("general_execute")

        # 4. Template and Language Detection
        template = ""
        if "login" in cmd_lower or "login activity" in cmd_lower:
            template = "login_activity"
        elif "empty activity" in cmd_lower:
            template = "empty_activity"

        language = "Kotlin"
        if "java" in cmd_lower and "javascript" not in cmd_lower:
            language = "Java"
        elif "c#" in cmd_lower or "csharp" in cmd_lower:
            language = "C#"
        elif "c++" in cmd_lower or "cpp" in cmd_lower:
            language = "C++"

        # 5. Project Name Extraction
        project_name = "DevProject"
        m_proj = re.search(r"(?:project|activity|app)?\s*(?:named|called)\s+([A-Za-z0-9_-]+)", cmd, re.IGNORECASE)
        if m_proj:
            project_name = m_proj.group(1).strip()
        elif template == "login_activity":
            project_name = "DevLoginApp"
        elif target_env == TargetEnvironment.ANDROID:
            project_name = "DevAndroidApp"
        elif target_env == TargetEnvironment.VISUAL_STUDIO:
            project_name = "DevVSApp"
        elif target_env == TargetEnvironment.UNITY:
            project_name = "DevUnityApp"
        elif target_env == TargetEnvironment.UNREAL:
            project_name = "DevUnrealApp"

        return DevelopmentIntent(
            target_environment=target_env,
            application=app_name,
            workflow_type=workflow_type,
            project_name=project_name,
            requested_actions=requested_actions,
            natural_language_command=cmd,
            language=language,
            template=template,
            is_valid=True,
            rejection_reason=None,
        )
