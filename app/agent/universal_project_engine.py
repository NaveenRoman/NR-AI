import json
import os
import re
import sys
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.agent.android_toolchain import AndroidProjectDetector, AndroidToolchain
from app.agent.code_writer import CodeWriter
from app.agent.docker_toolchain import DockerProjectDetector, DockerToolchain
from app.agent.flutter_toolchain import FlutterProjectDetector, FlutterToolchain
from app.agent.node_react_toolchain import NodeReactProjectDetector, NodeReactToolchain
from app.agent.spring_boot_toolchain import SpringBootProjectDetector, SpringBootToolchain
from app.agent.unity_toolchain import UnityProjectDetector, UnityToolchain
from app.agent.unreal_toolchain import UnrealProjectDetector, UnrealToolchain


class Ecosystem(str, Enum):
    ANDROID = "android"
    FLUTTER = "flutter"
    UNITY = "unity"
    UNREAL = "unreal"
    SPRING_BOOT = "spring_boot"
    REACT = "react"
    NODE_JS = "node_js"
    PYTHON_BACKEND = "python_backend"
    DOCKER = "docker"
    FULLSTACK_SAAS = "fullstack_saas"
    GENERIC = "generic"


class UniversalProjectDetector:
    """
    Analyzes natural language prompts or existing workspaces to determine the
    primary ecosystem and multi-tier technology stack required.
    """

    @staticmethod
    def identify_from_prompt(prompt: str) -> Dict[str, Any]:
        text = str(prompt or "").lower().strip()

        # Multi-technology detection
        is_saas = "saas" in text or ("full-stack" in text and ("docker" in text or "database" in text or "multi" in text))
        is_react_python = ("react" in text or "dashboard" in text) and ("python" in text or "fastapi" in text or "flask" in text or "api" in text)
        is_react_node = "react" in text and ("node" in text or "express" in text)

        if is_saas:
            return {
                "primary_ecosystem": Ecosystem.FULLSTACK_SAAS.value,
                "ecosystems": [
                    Ecosystem.REACT.value,
                    Ecosystem.PYTHON_BACKEND.value,
                    Ecosystem.DOCKER.value,
                ],
                "description": "Full-stack SaaS application with React frontend, Python API backend, database models, and Docker containerization.",
            }

        if is_react_python:
            return {
                "primary_ecosystem": Ecosystem.REACT.value,
                "ecosystems": [Ecosystem.REACT.value, Ecosystem.PYTHON_BACKEND.value],
                "description": "React frontend dashboard connected to Python backend API.",
            }

        if is_react_node:
            return {
                "primary_ecosystem": Ecosystem.REACT.value,
                "ecosystems": [Ecosystem.REACT.value, Ecosystem.NODE_JS.value],
                "description": "React frontend with Node.js Express backend.",
            }

        # Single ecosystem classification
        if "unreal" in text or "ue5" in text or "ue4" in text or "uproject" in text or ("game" in text and ("c++" in text or "fps" in text or "third-person" in text or "third person" in text)):
            return {
                "primary_ecosystem": Ecosystem.UNREAL.value,
                "ecosystems": [Ecosystem.UNREAL.value],
                "description": "Unreal Engine C++ / Blueprint game project.",
            }

        if "unity" in text or ("game" in text and ("c#" in text or "3d" in text or "2d" in text)):
            return {
                "primary_ecosystem": Ecosystem.UNITY.value,
                "ecosystems": [Ecosystem.UNITY.value],
                "description": "Unity C# Game Engine Project.",
            }

        if "spring" in text or ("java" in text and ("backend" in text or "microservice" in text or "boot" in text or "banking" in text)):
            return {
                "primary_ecosystem": Ecosystem.SPRING_BOOT.value,
                "ecosystems": [Ecosystem.SPRING_BOOT.value],
                "description": "Spring Boot Java enterprise microservice backend.",
            }

        if "flutter" in text or ("dart" in text and ("app" in text or "mobile" in text)):
            return {
                "primary_ecosystem": Ecosystem.FLUTTER.value,
                "ecosystems": [Ecosystem.FLUTTER.value],
                "description": "Flutter multi-platform application.",
            }

        if "android" in text or "kotlin" in text or "jetpack" in text:
            return {
                "primary_ecosystem": Ecosystem.ANDROID.value,
                "ecosystems": [Ecosystem.ANDROID.value],
                "description": "Android Kotlin Jetpack Compose application.",
            }

        if "react" in text or "next.js" in text or "vite" in text:
            return {
                "primary_ecosystem": Ecosystem.REACT.value,
                "ecosystems": [Ecosystem.REACT.value],
                "description": "React / Next.js web application.",
            }

        if "docker" in text or "container" in text or "compose" in text:
            return {
                "primary_ecosystem": Ecosystem.DOCKER.value,
                "ecosystems": [Ecosystem.DOCKER.value],
                "description": "Docker containerized architecture.",
            }

        return {
            "primary_ecosystem": Ecosystem.GENERIC.value,
            "ecosystems": [Ecosystem.GENERIC.value],
            "description": "Standard Python / Polyglot project.",
        }

    @staticmethod
    def identify_from_directory(project_dir: str | Path) -> Dict[str, Any]:
        p = Path(project_dir).resolve()
        if not p.exists():
            return {"primary_ecosystem": "unknown", "ecosystems": []}

        detected = []
        if UnrealProjectDetector.detect(p).get("is_unreal"):
            detected.append(Ecosystem.UNREAL.value)
        if AndroidProjectDetector.detect(p).get("is_android"):
            detected.append(Ecosystem.ANDROID.value)
        if FlutterProjectDetector.detect(p).get("is_flutter"):
            detected.append(Ecosystem.FLUTTER.value)
        if UnityProjectDetector.detect(p).get("is_unity"):
            detected.append(Ecosystem.UNITY.value)
        if SpringBootProjectDetector.detect(p).get("is_spring_boot"):
            detected.append(Ecosystem.SPRING_BOOT.value)
        if NodeReactProjectDetector.detect(p).get("is_node_react"):
            detected.append(Ecosystem.REACT.value)
        if DockerProjectDetector.detect(p).get("is_docker"):
            detected.append(Ecosystem.DOCKER.value)

        primary = detected[0] if detected else Ecosystem.GENERIC.value
        return {
            "primary_ecosystem": primary,
            "ecosystems": detected or [Ecosystem.GENERIC.value],
        }


class UniversalProjectEngine:
    """
    Universal Project Engine for NR-AI.

    Orchestrates:
    - Requirement analysis & multi-ecosystem classification
    - Dependency-aware blueprint generation
    - Multi-tier project scaffolding (Database -> Backend -> Frontend -> Docker)
    - Toolchain routing across Android, Flutter, Unity, Spring Boot, React, and Docker
    """

    def __init__(self, workspace: Optional[str] = None):
        self.workspace = Path(workspace or os.getcwd()).resolve()
        self.writer = CodeWriter(workspace=str(self.workspace))
        self.android = AndroidToolchain(workspace=str(self.workspace))
        self.flutter = FlutterToolchain(workspace=str(self.workspace))
        self.unity = UnityToolchain(workspace=str(self.workspace))
        self.unreal = UnrealToolchain(workspace=str(self.workspace))
        self.spring_boot = SpringBootToolchain(workspace=str(self.workspace))
        self.node_react = NodeReactToolchain(workspace=str(self.workspace))
        self.docker = DockerToolchain(workspace=str(self.workspace))

    def generate_blueprint(self, prompt: str, target_dir: Optional[str] = None) -> Dict[str, Any]:
        """Generates a structured, dependency-ordered multi-tier project blueprint."""
        identification = UniversalProjectDetector.identify_from_prompt(prompt)
        primary = identification["primary_ecosystem"]
        ecosystems = identification["ecosystems"]

        safe_name = re.sub(r"[^a-z0-9_]", "_", prompt.lower()[:20].strip())
        root_dir = target_dir or f"data/{safe_name}"

        tiers = []
        if primary == Ecosystem.FULLSTACK_SAAS.value:
            tiers = [
                {"tier": "database", "tech": "PostgreSQL / SQLite", "path": f"{root_dir}/backend/models.py"},
                {"tier": "backend", "tech": "Python FastAPI / REST", "path": f"{root_dir}/backend/app.py"},
                {"tier": "frontend", "tech": "React (Vite)", "path": f"{root_dir}/frontend"},
                {"tier": "container", "tech": "Docker & Compose", "path": f"{root_dir}/docker-compose.yml"},
            ]
        elif primary == Ecosystem.UNREAL.value:
            tiers = [{"tier": "game_client", "tech": "Unreal Engine 5 (C++)", "path": root_dir}]
        elif primary == Ecosystem.SPRING_BOOT.value:
            tiers = [{"tier": "backend", "tech": "Spring Boot (Java 17)", "path": root_dir}]
        elif primary == Ecosystem.UNITY.value:
            tiers = [{"tier": "game_client", "tech": "Unity C#", "path": root_dir}]
        elif primary == Ecosystem.FLUTTER.value:
            tiers = [{"tier": "mobile_client", "tech": "Flutter (Dart)", "path": root_dir}]
        elif primary == Ecosystem.ANDROID.value:
            tiers = [{"tier": "mobile_client", "tech": "Android (Jetpack Compose)", "path": root_dir}]
        elif primary == Ecosystem.REACT.value:
            tiers = [
                {"tier": "frontend", "tech": "React (Vite)", "path": f"{root_dir}/frontend" if "python" in prompt.lower() else root_dir},
            ]
            if "python" in prompt.lower() or "api" in prompt.lower():
                tiers.append({"tier": "backend", "tech": "Python API", "path": f"{root_dir}/backend"})

        return {
            "success": True,
            "prompt": prompt,
            "primary_ecosystem": primary,
            "ecosystems": ecosystems,
            "description": identification["description"],
            "target_dir": root_dir,
            "tiers": tiers,
            "execution_order": [t["tier"] for t in tiers],
        }

    def scaffold_universal_project(self, prompt: str, target_dir: Optional[str] = None) -> Dict[str, Any]:
        """Scaffolds a project according to its natural-language requirements and ecosystem."""
        blueprint = self.generate_blueprint(prompt, target_dir)
        primary = blueprint["primary_ecosystem"]
        root = blueprint["target_dir"]

        results = {}

        if primary == Ecosystem.FULLSTACK_SAAS.value:
            # 1. Frontend (React)
            fe_res = self.node_react.scaffold_react_app(
                project_name="saas_frontend",
                target_dir=f"{root}/frontend",
                title="NR AI SaaS Dashboard",
                api_endpoint="http://localhost:8000/api",
            )
            # 2. Docker & Compose
            dock_res = self.docker.scaffold_saas_docker(
                project_dir=root,
                app_name="saas_platform",
                include_db=True,
            )
            results["frontend"] = fe_res
            results["docker"] = dock_res

        elif primary == Ecosystem.UNREAL.value:
            results["unreal"] = self.unreal.scaffold_project(
                project_name="UnrealFPSGame",
                target_dir=root,
                engine_version="5.3",
            )

        elif primary == Ecosystem.SPRING_BOOT.value:
            results["spring_boot"] = self.spring_boot.scaffold_project(
                project_name="banking_backend",
                target_dir=root,
                domain="banking",
            )

        elif primary == Ecosystem.UNITY.value:
            results["unity"] = self.unity.scaffold_project(
                project_name="MultiplayerGame",
                target_dir=root,
                game_type="Multiplayer Action",
            )

        elif primary == Ecosystem.FLUTTER.value:
            results["flutter"] = self.flutter.scaffold_project(
                project_name="food_delivery_app",
                target_dir=root,
                title="NR AI Food Delivery",
            )

        elif primary == Ecosystem.ANDROID.value:
            results["android"] = self.android.scaffold_project(
                project_name="ShoppingApp",
                target_dir=root,
                use_compose=True,
            )

        elif primary == Ecosystem.REACT.value:
            results["react"] = self.node_react.scaffold_react_app(
                project_name="react_dashboard",
                target_dir=f"{root}/frontend" if "python" in prompt.lower() else root,
                title="NR AI Dashboard",
            )

        return {
            "success": True,
            "blueprint": blueprint,
            "scaffold_results": results,
            "message": f"Successfully created {primary} project according to blueprint.",
        }
