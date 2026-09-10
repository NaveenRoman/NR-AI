"""
NR AI Safe Action Dispatcher for Active Target Operations.

Executes safe, verified operations on resolved active targets:
- Files: Opened via native OS association (os.startfile) without shell invocation.
- Directories: Opened in Windows File Explorer.
- URLs: Validated (http/https only) and launched in default web browser.
- Executables: Guarded against unintended execution. "open" opens the containing folder
  and explains safely; "launch"/"run" executes via subprocess.Popen(shell=False).
- APKs: Guarded as Android installation packages; containing folder opened instead of auto-install.
"""

from dataclasses import dataclass, field
import logging
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Dict, Optional
import urllib.parse
import webbrowser

logger = logging.getLogger("NRAI.SafeActionDispatcher")


@dataclass
class ActionExecutionResult:
    success: bool
    action: str
    target: str
    target_type: str
    message: str
    executed_operation: str = "none"
    error: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "action": self.action,
            "target": self.target,
            "target_type": self.target_type,
            "message": self.message,
            "executed_operation": self.executed_operation,
            "error": self.error,
            "data": self.data,
            "timestamp": self.timestamp,
        }


class SafeActionDispatcher:
    """
    Validates and executes safe operations against verified session targets.
    Strictly forbids arbitrary shell execution or string concatenation into shells.
    """

    def execute_action(
        self,
        action: str,
        target_info: Dict[str, Any],
    ) -> ActionExecutionResult:
        action_verb = action.lower().strip()
        target_val = target_info.get("value", "").strip()
        target_type = target_info.get("type", "generic").lower()

        if not target_val:
            return ActionExecutionResult(
                success=False,
                action=action_verb,
                target="",
                target_type=target_type,
                message="Target validation failed: No target specified.",
                error="EMPTY_TARGET",
            )

        # 1. URL Target Validation & Dispatch
        if target_type == "url" or target_val.startswith(("http://", "https://")):
            return self._handle_url(action_verb, target_val)

        # 2. Filesystem Targets (File, Directory, Executable, APK)
        p = Path(target_val)
        if not p.exists():
            return ActionExecutionResult(
                success=False,
                action=action_verb,
                target=target_val,
                target_type=target_type,
                message=f"Target validation failed: '{target_val}' does not exist on the filesystem.",
                error="TARGET_NOT_FOUND",
            )

        # 3. APK Target
        if target_type == "apk" or p.suffix.lower() == ".apk":
            return self._handle_apk(action_verb, p)

        # 4. Executable Target
        if target_type == "executable" or p.suffix.lower() in (".exe", ".bat", ".cmd"):
            return self._handle_executable(action_verb, p)

        # 5. Directory Target
        if target_type == "directory" or p.is_dir():
            return self._handle_directory(action_verb, p)

        # 6. General File Target
        return self._handle_file(action_verb, p)

    def _handle_url(self, action: str, url: str) -> ActionExecutionResult:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return ActionExecutionResult(
                success=False,
                action=action,
                target=url,
                target_type="url",
                message=f"Refusing to open untrusted URL scheme: '{parsed.scheme}'. Only http and https allowed.",
                error="UNSAFE_URL_SCHEME",
            )

        try:
            webbrowser.open(url)
            return ActionExecutionResult(
                success=True,
                action=action,
                target=url,
                target_type="url",
                executed_operation="browser_open",
                message=f"Opened URL '{url}' in default web browser.",
            )
        except Exception as e:
            return ActionExecutionResult(
                success=False,
                action=action,
                target=url,
                target_type="url",
                error=str(e),
                message=f"Failed to open URL '{url}': {e}",
            )

    def _handle_directory(self, action: str, path: Path) -> ActionExecutionResult:
        try:
            os.startfile(str(path))
            return ActionExecutionResult(
                success=True,
                action=action,
                target=str(path),
                target_type="directory",
                executed_operation="open_directory",
                message=f"Opened directory '{path}' in File Explorer.",
            )
        except Exception as e:
            return ActionExecutionResult(
                success=False,
                action=action,
                target=str(path),
                target_type="directory",
                error=str(e),
                message=f"Failed to open directory '{path}': {e}",
            )

    def _handle_file(self, action: str, path: Path) -> ActionExecutionResult:
        try:
            os.startfile(str(path))
            return ActionExecutionResult(
                success=True,
                action=action,
                target=str(path),
                target_type="file",
                executed_operation="open_file_associated",
                message=f"Opened file '{path.name}' with system associated application.",
                data={"path": str(path), "size": path.stat().st_size},
            )
        except Exception as e:
            return ActionExecutionResult(
                success=False,
                action=action,
                target=str(path),
                target_type="file",
                error=str(e),
                message=f"Failed to open file '{path}': {e}",
            )

    def _handle_executable(self, action: str, path: Path) -> ActionExecutionResult:
        # If action is "open", "show", "view", do NOT execute the binary directly.
        # Instead, open its containing folder and explain.
        if action in ("open", "show", "view"):
            parent_dir = path.parent
            try:
                os.startfile(str(parent_dir))
                return ActionExecutionResult(
                    success=True,
                    action=action,
                    target=str(path),
                    target_type="executable",
                    executed_operation="open_parent_directory",
                    message=(
                        f"Target is an executable binary ('{path.name}'). "
                        f"Opened its containing folder '{parent_dir}' in File Explorer rather than executing it directly."
                    ),
                    data={"executable": str(path), "parent_directory": str(parent_dir)},
                )
            except Exception as e:
                return ActionExecutionResult(
                    success=False,
                    action=action,
                    target=str(path),
                    target_type="executable",
                    error=str(e),
                    message=f"Target is an executable ('{path.name}'). Could not open containing folder: {e}",
                )

        # If action explicitly means launch / run / start / execute
        if action in ("launch", "run", "start", "execute"):
            try:
                proc = subprocess.Popen([str(path)], shell=False)
                return ActionExecutionResult(
                    success=True,
                    action=action,
                    target=str(path),
                    target_type="executable",
                    executed_operation="process_spawn",
                    message=f"Launched executable '{path.name}' (PID: {proc.pid}).",
                    data={"pid": proc.pid, "path": str(path)},
                )
            except Exception as e:
                return ActionExecutionResult(
                    success=False,
                    action=action,
                    target=str(path),
                    target_type="executable",
                    error=str(e),
                    message=f"Failed to launch executable '{path.name}': {e}",
                )

        return ActionExecutionResult(
            success=False,
            action=action,
            target=str(path),
            target_type="executable",
            message=f"Action '{action}' is not supported for executable target '{path.name}'.",
            error="UNSUPPORTED_ACTION",
        )

    def _handle_apk(self, action: str, path: Path) -> ActionExecutionResult:
        # Guard APKs: Never automatically install without explicit adb install command.
        parent_dir = path.parent
        try:
            os.startfile(str(parent_dir))
            return ActionExecutionResult(
                success=True,
                action=action,
                target=str(path),
                target_type="apk",
                executed_operation="open_parent_directory",
                message=(
                    f"Target is an Android APK package ('{path.name}'). "
                    f"To install on a device, run an explicit adb install command. "
                    f"Opened containing folder '{parent_dir}' in File Explorer."
                ),
                data={"apk_path": str(path), "parent_directory": str(parent_dir)},
            )
        except Exception as e:
            return ActionExecutionResult(
                success=False,
                action=action,
                target=str(path),
                target_type="apk",
                error=str(e),
                message=f"Target is an APK ('{path.name}'). Could not open folder: {e}",
            )
