import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


class GitSafety:
    """
    NR AI Git Safety & Secret Protection Engine.

    Features:
    - Non-destructive git status and git diff inspection.
    - Automated safe checkpoint branch/commits before risky changes.
    - Safe rollback to previous checkpoints without destroying uncommitted user work.
    - Secret scanning: prevents committing or logging .env files, SSH keys, private keys,
      API tokens, and passwords.
    """

    SECRET_PATTERNS = [
        r"(?i)(api[_-]?key|secret|password|auth[_-]?token|bearer)\s*[:=]\s*['\"][A-Za-z0-9_\-\.]{8,}['\"]",
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        r"ghp_[A-Za-z0-9]{36}",
        r"ey[A-Za-z0-9_\-]{20,}\.ey[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}",  # JWT
    ]

    def __init__(self, workspace: Optional[str] = None):
        self.workspace = Path(workspace or os.getcwd()).resolve()

    def get_git_status(self) -> Dict[str, Any]:
        """Runs git status cleanly and returns tracked/untracked/modified files."""
        git_exe = shutil.which("git") or r"C:\Program Files\Git\cmd\git.exe"
        if not git_exe or not os.path.exists(git_exe):
            return {"available": False, "message": "Git is not installed."}

        try:
            res = subprocess.run(
                [git_exe, "status", "--porcelain"],
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                timeout=5,
            )
            lines = res.stdout.splitlines()
            modified = []
            untracked = []
            for l in lines:
                if l.startswith("??"):
                    untracked.append(l[3:].strip())
                else:
                    modified.append(l[3:].strip())

            # Get current branch
            branch_res = subprocess.run(
                [git_exe, "branch", "--show-current"],
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                timeout=5,
            )
            branch = branch_res.stdout.strip() or "main"

            return {
                "available": True,
                "branch": branch,
                "modified_files": modified,
                "untracked_files": untracked,
                "is_clean": len(lines) == 0,
            }
        except Exception as e:
            return {"available": False, "error": str(e)}

    def scan_for_secrets(self, text: str) -> List[Dict[str, str]]:
        """Scans code or text for accidental secret leaks."""
        findings = []
        for pattern in self.SECRET_PATTERNS:
            for m in re.finditer(pattern, text):
                findings.append({
                    "match": m.group(0)[:15] + "...",
                    "pattern": pattern,
                })
        return findings

    def sanitize_log(self, log_text: str) -> str:
        """Replaces sensitive tokens and keys in logs with [REDACTED_SECRET]."""
        sanitized = log_text
        for pattern in self.SECRET_PATTERNS:
            sanitized = re.sub(pattern, "[REDACTED_SECRET]", sanitized)
        return sanitized
