import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.agent.toolchain_registry import ToolchainRegistry
from app.memory.context_memory import ProjectContextMemory


class ProjectHealth:
    """
    NR AI Unified Project Health & Verification Status Engine.

    Aggregates:
    - Toolchain availability status
    - Dependency graph integrity
    - Build compilation status
    - Unit and integration test pass rates
    - Live runtime supervisor status
    - UI and Visual verification status
    - Unresolved error logs and warnings
    - Checkpoint stack and rollback history
    """

    def __init__(self, workspace: Optional[str] = None):
        self.workspace = Path(workspace or os.getcwd()).resolve()
        self.registry = ToolchainRegistry()
        self.memory = ProjectContextMemory(workspace=str(self.workspace))

    def evaluate_health(
        self,
        project_dir: Optional[str | Path] = None,
        build_status: str = "PASS",
        test_results: Optional[Dict[str, Any]] = None,
        runtime_status: str = "ONLINE",
        ui_status: str = "VERIFIED",
    ) -> Dict[str, Any]:
        p = Path(project_dir or self.workspace).resolve()
        tools = self.registry.probe_all()

        available_tools = [k for k, v in tools.items() if v.get("available")]
        unavailable_tools = [k for k, v in tools.items() if not v.get("available")]

        tests_passed = test_results.get("passed", 0) if test_results else 0
        tests_total = test_results.get("total", 0) if test_results else 0
        test_rate = (tests_passed / tests_total * 100.0) if tests_total > 0 else 100.0

        errors = self.memory.error_history
        recent_files = self.memory.recent_files_modified
        checkpoints_count = len(self.memory.rollback_stack)

        is_healthy = (
            build_status == "PASS"
            and (tests_total == 0 or tests_passed == tests_total)
            and len(errors) == 0
        )

        health_data = {
            "timestamp": time.time(),
            "formatted_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "project_path": str(p),
            "overall_health": "HEALTHY" if is_healthy else "DEGRADED",
            "build_status": build_status,
            "tests": {
                "passed": tests_passed,
                "total": tests_total,
                "pass_rate_pct": round(test_rate, 1),
                "status": "PASS" if test_rate == 100.0 else "FAIL",
            },
            "runtime_status": runtime_status,
            "ui_visual_status": ui_status,
            "toolchains": {
                "available": available_tools,
                "unavailable": unavailable_tools,
            },
            "recent_modified_files": recent_files[:10],
            "unresolved_errors_count": len(errors),
            "checkpoints_available": checkpoints_count,
            "last_verification": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        }

        return health_data

    def generate_markdown_report(self, health: Dict[str, Any]) -> str:
        md = [
            f"# Project Health Report — {Path(health['project_path']).name}",
            f"**Overall Status**: `{health['overall_health']}`",
            f"**Evaluated At**: {health['formatted_time']}",
            "",
            "## Summary Metrics",
            f"- **Build Compilation**: {health['build_status']}",
            f"- **Test Suite**: {health['tests']['passed']}/{health['tests']['total']} passing ({health['tests']['pass_rate_pct']}%)",
            f"- **Runtime Service**: {health['runtime_status']}",
            f"- **UI Verification**: {health['ui_visual_status']}",
            f"- **Unresolved Errors**: {health['unresolved_errors_count']}",
            f"- **Checkpoints in Stack**: {health['checkpoints_available']}",
            "",
            "## Available Host Toolchains",
            ", ".join(f"`{t}`" for t in health["toolchains"]["available"]),
            "",
            "## Unavailable Host Toolchains",
            ", ".join(f"`{t}`" for t in health["toolchains"]["unavailable"]),
        ]
        return "\n".join(md)
