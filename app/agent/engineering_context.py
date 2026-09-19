"""
NR-AI Active Project Context & Engineering Continuity Subsystem.

Provides:
- Persistent tracking of active project and active features across conversation turns
- Project intelligence mapping high-level developer concepts (e.g. splash screen, logo,
  login) to concrete affected files without requiring the developer to name files
- Clean lifecycle tracking: creation, configuration, builds, runs, tests, repairs, and refinements
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
import json
import logging
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union

logger = logging.getLogger("NRAI.EngineeringContext")

DEFAULT_CONTEXT_FILE = Path(r"C:\NR-AI\data\active_engineering_context.json")


@dataclass
class ActiveProjectContext:
    """Snapshot of active project context maintained across dialogue turns."""
    project_id: str
    project_name: str
    domain: str = "ANDROID"
    canonical_path: Optional[str] = None
    active_feature: Optional[str] = None
    last_action: Optional[str] = None
    last_action_timestamp: float = field(default_factory=time.time)
    affected_files: List[str] = field(default_factory=list)
    parameters: Dict[str, Any] = field(default_factory=dict)
    history: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ActiveProjectContext:
        return cls(**data)


class ActiveProjectContextManager:
    """
    Manages active project context, multi-turn feature continuity, and file mapping.
    Persists state to disk across agent interactions.
    """

    def __init__(self, context_file: Optional[Union[str, Path]] = None, storage_path: Optional[Union[str, Path]] = None):
        target_file = storage_path or context_file
        self.is_memory = str(target_file) == ":memory:"
        self.context_file = Path(DEFAULT_CONTEXT_FILE).resolve() if self.is_memory else Path(target_file or DEFAULT_CONTEXT_FILE).resolve()
        self._active_context: Optional[ActiveProjectContext] = None

        if not self.is_memory:
            self.context_file.parent.mkdir(parents=True, exist_ok=True)
            self.load()
        else:
            self._active_context = ActiveProjectContext(
                project_id="nr_android_test",
                project_name="nr_android_test",
                domain="ANDROID",
                canonical_path=r"C:\NR-AI\nr_android_test",
            )

    def set_active_project(
        self,
        project_name: str,
        domain: str = "ANDROID",
        canonical_path: Optional[str] = None,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> ActiveProjectContext:
        """Activates a project context for subsequent turns."""
        clean_id = project_name.strip()
        path_str = canonical_path
        if not path_str:
            if clean_id.lower() == "nr_android_test":
                path_str = r"C:\NR-AI\nr_android_test"
            else:
                path_str = str(Path(r"C:\NR-AI\dev_projects") / clean_id)

        ctx = ActiveProjectContext(
            project_id=clean_id,
            project_name=project_name.strip(),
            domain=domain.upper(),
            canonical_path=path_str,
            parameters=dict(parameters or {}),
            history=[{
                "action": "ACTIVATE_PROJECT",
                "timestamp": time.time(),
                "project": clean_id,
            }],
        )
        self._active_context = ctx
        self.save()
        logger.info(f"Active project context switched to: '{clean_id}' ({domain}).")
        return ctx

    @property
    def storage_path(self) -> Path:
        return self.context_file

    def clear(self) -> None:
        """Resets the active project context and removes persistent state."""
        self._active_context = None
        if not self.is_memory and self.context_file and self.context_file.exists():
            try:
                self.context_file.unlink()
            except Exception:
                pass

    def get_active_project(self) -> Optional[ActiveProjectContext]:
        if self._active_context is None:
            # Default fallback to primary authorized workspace project
            self.set_active_project("nr_android_test", domain="ANDROID", canonical_path=r"C:\NR-AI\nr_android_test")
        return self._active_context

    def update_active_feature(
        self,
        feature_name: str,
        affected_files: Optional[List[str]] = None,
    ) -> None:
        """Sets the active feature under development (e.g. 'splash screen')."""
        ctx = self.get_active_project()
        if ctx:
            ctx.active_feature = feature_name
            if affected_files is not None:
                ctx.affected_files = list(affected_files)
            ctx.last_action_timestamp = time.time()
            self.save()

    def set_active_feature(
        self,
        feature_name: str,
        affected_files: Optional[List[str]] = None,
    ) -> None:
        self.update_active_feature(feature_name, affected_files)

    def record_action(
        self,
        action: str,
        target: Optional[str] = None,
        parameters: Optional[Dict[str, Any]] = None,
        affected_files: Optional[List[str]] = None,
    ) -> None:
        """Appends an executed engineering action to the project history."""
        ctx = self.get_active_project()
        if not ctx:
            return

        ctx.last_action = action
        ctx.last_action_timestamp = time.time()
        if target and target != ctx.project_name and target.lower() not in ("build", "app", "all", "clean"):
            ctx.active_feature = target
        if affected_files:
            ctx.affected_files = list(affected_files)
        if parameters:
            ctx.parameters.update(parameters)

        ctx.history.append({
            "action": action,
            "target": target,
            "timestamp": time.time(),
            "affected_files": list(affected_files or []),
            "parameters": dict(parameters or {}),
        })
        self.save()

    def resolve_target_and_files(
        self,
        target: Optional[str],
        instruction: Optional[str] = None,
    ) -> Tuple[str, List[str]]:
        """
        Uses project intelligence to determine affected source files and resources
        for a high-level goal without requiring the user to specify file names.
        """
        ctx = self.get_active_project()
        proj_dir = Path(ctx.canonical_path) if ctx and ctx.canonical_path else Path(r"C:\NR-AI\nr_android_test")

        feat = (target or (ctx.active_feature if ctx else "") or "").lower().strip()
        inst = (instruction or "").lower()

        affected: List[str] = []
        feature_label = target or (ctx.active_feature if ctx else "feature") or "feature"

        if "splash" in feat or "splash" in inst:
            feature_label = "splash screen"
            # Identify or plan splash activity and XML
            candidates = [
                proj_dir / "app" / "src" / "main" / "java" / "com" / "nrai" / "test" / "SplashActivity.kt",
                proj_dir / "app" / "src" / "main" / "res" / "layout" / "activity_splash.xml",
                proj_dir / "app" / "src" / "main" / "res" / "values" / "colors.xml",
                proj_dir / "app" / "src" / "main" / "res" / "values" / "strings.xml",
                proj_dir / "app" / "src" / "main" / "AndroidManifest.xml",
            ]
            for c in candidates:
                affected.append(str(c))

        elif "login" in feat or "login" in inst or "auth" in feat:
            feature_label = "login screen"
            candidates = [
                proj_dir / "app" / "src" / "main" / "java" / "com" / "nrai" / "test" / "LoginActivity.kt",
                proj_dir / "app" / "src" / "main" / "res" / "layout" / "activity_login.xml",
                proj_dir / "app" / "src" / "main" / "res" / "values" / "strings.xml",
            ]
            for c in candidates:
                affected.append(str(c))

        elif "button" in feat or "button" in inst or "click" in inst:
            feature_label = "button handler"
            candidates = [
                proj_dir / "app" / "src" / "main" / "java" / "com" / "nrai" / "test" / "MainActivity.kt",
                proj_dir / "app" / "src" / "main" / "res" / "layout" / "activity_main.xml",
            ]
            for c in candidates:
                affected.append(str(c))

        else:
            candidates = [
                proj_dir / "app" / "src" / "main" / "java" / "com" / "nrai" / "test" / "MainActivity.kt",
                proj_dir / "app" / "src" / "main" / "res" / "layout" / "activity_main.xml",
            ]
            for c in candidates:
                affected.append(str(c))

        return feature_label, affected

    def clear_context(self) -> None:
        self._active_context = None
        if not self.is_memory and self.context_file.exists():
            try:
                self.context_file.unlink()
            except Exception as e:
                logger.warning(f"Failed to delete context file: {e}")

    def save(self) -> None:
        if self.is_memory or self._active_context is None:
            return
        try:
            with open(self.context_file, "w", encoding="utf-8") as f:
                json.dump(self._active_context.to_dict(), f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save active engineering context: {e}")

    def load(self) -> None:
        if self.is_memory or not self.context_file.exists():
            return
        try:
            with open(self.context_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                self._active_context = ActiveProjectContext.from_dict(data)
        except Exception as e:
            logger.warning(f"Failed to load active engineering context: {e}")


# Alias for backward compatibility across acceptance test suites
EngineeringContextManager = ActiveProjectContextManager
