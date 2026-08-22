import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def safe_print(msg: str) -> None:
    try:
        print(msg)
    except Exception:
        try:
            print(msg.encode("ascii", "replace").decode("ascii"))
        except Exception:
            pass


class TerminalManager:
    """
    NR AI Terminal Interaction & Subprocess Execution Manager.

    Features:
        - Safe terminal command execution with timeouts
        - Working directory isolation
        - Destructive command guardrail protection
        - Output capture and history logging
    """

    DANGEROUS_PATTERNS = [
        r"\bformat\s+[a-zA-Z]:",
        r"\brmdir\s+/[sq]",
        r"\bdel\s+/[sqf]",
        r"\bdiskpart\b",
        r"\bshutdown\s+/[sr]",
        r"\breg\s+delete\b",
    ]

    def __init__(self, workspace: Optional[str] = None):
        self.workspace = Path(workspace or os.getcwd()).resolve()
        self.command_history: List[Dict[str, Any]] = []

    def _is_safe(self, command: str) -> Tuple[bool, str]:
        cmd_lower = command.lower().strip()
        import re
        for pat in self.DANGEROUS_PATTERNS:
            if re.search(pat, cmd_lower):
                return False, f"Potentially destructive system command blocked: {command}"
        return True, ""

    def run_command(
        self,
        command: str,
        cwd: Optional[str] = None,
        timeout: float = 30.0,
        shell: bool = True,
        force: bool = False,
    ) -> Dict[str, Any]:
        """Execute a shell command with safety checks and timeout."""
        work_dir = Path(cwd or self.workspace).resolve()

        if not force:
            safe, reason = self._is_safe(command)
            if not safe:
                safe_print(f"🛑 Security Guard: {reason}")
                return {
                    "success": False,
                    "returncode": -1,
                    "stdout": "",
                    "stderr": reason,
                    "duration_ms": 0.0,
                    "command": command,
                    "message": reason,
                }

        safe_print(f"\n💻 Terminal Command: {command}")
        safe_print(f"📂 CWD: {work_dir}")

        start_time = time.perf_counter()
        try:
            process = subprocess.run(
                command,
                cwd=work_dir,
                shell=shell,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            duration_ms = (time.perf_counter() - start_time) * 1000
            stdout = process.stdout.strip()
            stderr = process.stderr.strip()

            result = {
                "success": (process.returncode == 0),
                "returncode": process.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "duration_ms": duration_ms,
                "command": command,
                "timed_out": False,
                "message": "Command completed successfully"
                if process.returncode == 0
                else f"Command exited with code {process.returncode}",
            }

            self.command_history.append(result)

            safe_print(f"📤 Exit code: {process.returncode} ({duration_ms:.1f}ms)")
            if stdout:
                safe_print(f"📋 STDOUT:\n{stdout}")
            if stderr:
                safe_print(f"❌ STDERR:\n{stderr}")

            return result

        except subprocess.TimeoutExpired:
            duration_ms = (time.perf_counter() - start_time) * 1000
            return {
                "success": False,
                "returncode": -1,
                "stdout": "",
                "stderr": f"Command timed out after {timeout} seconds.",
                "duration_ms": duration_ms,
                "command": command,
                "timed_out": True,
                "message": f"Timeout expired ({timeout}s)",
            }
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            return {
                "success": False,
                "returncode": -1,
                "stdout": "",
                "stderr": str(e),
                "duration_ms": duration_ms,
                "command": command,
                "timed_out": False,
                "message": f"Failed to execute command: {e}",
            }

    def get_history(self) -> List[Dict[str, Any]]:
        return list(self.command_history)


if __name__ == "__main__":
    safe_print("========================================")
    safe_print("     NR AI TERMINAL MANAGER TEST")
    safe_print("========================================")

    manager = TerminalManager()
    res = manager.run_command("python --version")
    safe_print(f"Version check success: {res['success']}")

    safe_print("\n========================================")
    safe_print("🟢 TERMINAL MANAGER READY")
    safe_print("========================================")
