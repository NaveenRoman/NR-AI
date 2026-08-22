import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.agent.code_writer import CodeWriter


class DockerProjectDetector:
    """
    Detects Dockerfiles, docker-compose.yml, and container orchestration files.
    """

    @staticmethod
    def detect(project_dir: str | Path) -> Dict[str, Any]:
        p = Path(project_dir).resolve()
        if not p.exists() or not p.is_dir():
            return {"is_docker": False, "reason": "Directory does not exist"}

        has_dockerfile = (p / "Dockerfile").exists()
        has_compose = (p / "docker-compose.yml").exists() or (p / "docker-compose.yaml").exists()

        services = []
        if has_compose:
            compose_file = p / "docker-compose.yml" if (p / "docker-compose.yml").exists() else p / "docker-compose.yaml"
            try:
                txt = compose_file.read_text(encoding="utf-8", errors="ignore")
                matches = re.findall(r"^\s{2}([a-zA-Z0-9_\-]+):", txt, re.MULTILINE)
                services = [m for m in matches if m not in {"build", "image", "ports", "environment", "volumes", "depends_on", "networks"}]
            except Exception:
                pass

        return {
            "is_docker": bool(has_dockerfile or has_compose),
            "project_path": str(p),
            "has_dockerfile": has_dockerfile,
            "has_compose": has_compose,
            "services": services,
        }


class DockerToolchain:
    """
    Docker & Container Orchestration Toolchain for NR-AI.
    """

    def __init__(self, workspace: Optional[str] = None):
        self.workspace = Path(workspace or os.getcwd()).resolve()
        self.writer = CodeWriter(workspace=str(self.workspace))

    def scaffold_saas_docker(
        self,
        project_dir: str | Path,
        app_name: str = "saas_platform",
        include_db: bool = True,
        db_type: str = "postgres",
    ) -> Dict[str, Any]:
        """Generates multi-container docker-compose.yml, Dockerfile, and environment configs."""
        root = Path(project_dir).resolve()
        root.mkdir(parents=True, exist_ok=True)

        # 1. Dockerfile (multi-stage Python/Node backend)
        dockerfile = (
            "FROM python:3.11-slim AS base\n"
            "WORKDIR /app\n"
            "ENV PYTHONDONTWRITEBYTECODE=1\n"
            "ENV PYTHONUNBUFFERED=1\n\n"
            "COPY requirements.txt ./\n"
            "RUN pip install --no-cache-dir -r requirements.txt\n\n"
            "COPY . .\n"
            "EXPOSE 8000\n"
            'CMD ["python", "app.py"]\n'
        )

        # 2. docker-compose.yml
        db_service = ""
        if include_db:
            db_service = (
                "  postgres:\n"
                "    image: postgres:15-alpine\n"
                "    environment:\n"
                f"      POSTGRES_DB: {app_name}_db\n"
                "      POSTGRES_USER: postgres\n"
                "      POSTGRES_PASSWORD: nraipassword\n"
                "    ports:\n"
                "      - '5432:5432'\n"
                "    volumes:\n"
                "      - pgdata:/var/lib/postgresql/data\n\n"
            )

        compose_yml = (
            "version: '3.8'\n\n"
            "services:\n"
            "  backend:\n"
            "    build:\n"
            "      context: .\n"
            "      dockerfile: Dockerfile\n"
            "    ports:\n"
            "      - '8000:8000'\n"
            "    environment:\n"
            f"      DATABASE_URL: postgresql://postgres:nraipassword@postgres:5432/{app_name}_db\n"
            "    depends_on:\n"
            "      - postgres\n\n"
            + db_service +
            "volumes:\n"
            "  pgdata:\n"
        )

        # 3. .dockerignore
        dockerignore = (
            ".git\n"
            "__pycache__\n"
            "*.pyc\n"
            "node_modules\n"
            ".venv\n"
            "dist\n"
            "build\n"
        )

        files_to_write = {
            root / "Dockerfile": dockerfile,
            root / "docker-compose.yml": compose_yml,
            root / ".dockerignore": dockerignore,
        }

        for path, content in files_to_write.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        return {
            "success": True,
            "project_path": str(root),
            "files_created": len(files_to_write),
            "services_configured": ["backend", "postgres"] if include_db else ["backend"],
        }
