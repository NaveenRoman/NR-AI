import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure standard output safely encodes unicode/emojis on Windows consoles
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


class CodeWriter:
    """
    NR AI Safe Code Writer & Checkpoint Engine.

    Handles reading, writing, modifying, and checkpointing source files
    with auditing and automated rollback capabilities.
    """

    def __init__(
        self,
        workspace: Optional[str] = None,
        checkpoint_dir: Optional[str] = None,
    ):
        self.workspace = Path(workspace or os.getcwd()).resolve()
        self.checkpoint_dir = Path(
            checkpoint_dir or (self.workspace / "data" / "checkpoints")
        ).resolve()
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self.audit_log: List[Dict[str, Any]] = []

    def _log_action(self, action: str, details: Dict[str, Any]) -> None:
        entry = {
            "timestamp": datetime.now().isoformat(),
            "action": action,
            **details,
        }
        self.audit_log.append(entry)

    def _resolve_path(self, filename: str) -> Path:
        path = Path(filename)
        if not path.is_absolute():
            path = (self.workspace / path).resolve()
        return path

    def _create_backup(self, file_path: Path) -> Optional[Path]:
        """Creates a timestamped checkpoint of the file if it exists."""
        if not file_path.exists():
            return None

        timestamp = int(time.time() * 1000)
        rel_path = file_path.name
        backup_filename = f"{rel_path}.{timestamp}.bak"
        backup_path = self.checkpoint_dir / backup_filename

        try:
            shutil.copy2(file_path, backup_path)
            return backup_path
        except Exception as e:
            safe_print(f"⚠️ Failed to create backup for {file_path}: {e}")
            return None

    # -------------------------------------------------
    # READ
    # -------------------------------------------------

    def read_file(self, filename: str) -> Dict[str, Any]:
        """Read source code from a file."""
        path = self._resolve_path(filename)

        if not path.exists():
            return {
                "success": False,
                "message": f"File not found: {path}",
                "path": str(path),
            }

        try:
            code = path.read_text(encoding="utf-8")
            return {
                "success": True,
                "code": code,
                "path": str(path),
                "lines_count": len(code.splitlines()),
                "size_bytes": path.stat().st_size,
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Error reading {path}: {e}",
                "path": str(path),
            }

    # -------------------------------------------------
    # WRITE
    # -------------------------------------------------

    def write_file(
        self,
        filename: str,
        code: str,
        overwrite: bool = True,
        make_backup: bool = True,
    ) -> Dict[str, Any]:
        """Write source code to a file with optional backup creation."""
        path = self._resolve_path(filename)

        if path.exists() and not overwrite:
            return {
                "success": False,
                "message": f"File {path} already exists and overwrite=False",
                "path": str(path),
            }

        backup_path = None
        if path.exists() and make_backup:
            backup_path = self._create_backup(path)

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(code, encoding="utf-8")

            self._log_action(
                "write",
                {
                    "file": str(path),
                    "bytes": len(code.encode("utf-8")),
                    "backup": str(backup_path) if backup_path else None,
                },
            )

            safe_print(f"✍️ Code written: {path}")
            if backup_path:
                safe_print(f"📦 Backup created: {backup_path.name}")

            return {
                "success": True,
                "path": str(path),
                "backup_path": str(backup_path) if backup_path else None,
                "size_bytes": len(code.encode("utf-8")),
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Failed to write {path}: {e}",
                "path": str(path),
            }

    # -------------------------------------------------
    # MODIFY
    # -------------------------------------------------

    def modify_file(
        self,
        filename: str,
        search_text: str,
        replace_text: str,
        make_backup: bool = True,
    ) -> Dict[str, Any]:
        """Perform a search and replace modification on a file."""
        read_res = self.read_file(filename)
        if not read_res["success"]:
            return read_res

        code = read_res["code"]
        if search_text not in code:
            return {
                "success": False,
                "message": f"Target content not found in {filename}",
                "path": read_res["path"],
            }

        new_code = code.replace(search_text, replace_text, 1)
        write_res = self.write_file(
            filename,
            new_code,
            overwrite=True,
            make_backup=make_backup,
        )

        if write_res["success"]:
            self._log_action(
                "modify_replace",
                {
                    "file": read_res["path"],
                    "search_snippet": search_text[:50],
                    "replace_snippet": replace_text[:50],
                },
            )

        return write_res

    def replace_lines(
        self,
        filename: str,
        start_line: int,
        end_line: int,
        new_content: str,
        make_backup: bool = True,
    ) -> Dict[str, Any]:
        """
        Replace lines in range [start_line, end_line] (1-indexed, inclusive)
        with new_content.
        """
        read_res = self.read_file(filename)
        if not read_res["success"]:
            return read_res

        lines = read_res["code"].splitlines()
        total_lines = len(lines)

        if start_line < 1 or end_line > total_lines or start_line > end_line:
            return {
                "success": False,
                "message": f"Invalid line range [{start_line}, {end_line}] for file with {total_lines} lines.",
                "path": read_res["path"],
            }

        new_line_list = new_content.splitlines() if new_content else []
        before = lines[: start_line - 1]
        after = lines[end_line:]
        modified_lines = before + new_line_list + after
        new_code = "\n".join(modified_lines)
        if read_res["code"].endswith("\n") or not new_code.endswith("\n"):
            new_code += "\n"

        return self.write_file(
            filename,
            new_code,
            overwrite=True,
            make_backup=make_backup,
        )

    # -------------------------------------------------
    # ROLLBACK / RESTORE
    # -------------------------------------------------

    def restore_backup(self, filename: str, backup_path: str) -> Dict[str, Any]:
        """Restore a file from a specified backup checkpoint."""
        target_path = self._resolve_path(filename)
        b_path = Path(backup_path)

        if not b_path.exists():
            return {
                "success": False,
                "message": f"Backup file {b_path} does not exist.",
            }

        try:
            shutil.copy2(b_path, target_path)
            self._log_action(
                "restore",
                {
                    "file": str(target_path),
                    "backup_source": str(b_path),
                },
            )
            safe_print(f"⏪ Restored {target_path} from {b_path.name}")
            return {
                "success": True,
                "path": str(target_path),
                "restored_from": str(b_path),
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Failed to restore backup: {e}",
            }

    def list_backups(self, filename: str) -> List[Dict[str, Any]]:
        """List all available backups for a given filename."""
        path = self._resolve_path(filename)
        pattern = f"{path.name}.*.bak"
        backups = sorted(
            self.checkpoint_dir.glob(pattern),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        return [
            {
                "path": str(b),
                "filename": b.name,
                "timestamp": datetime.fromtimestamp(
                    b.stat().st_mtime
                ).isoformat(),
                "size_bytes": b.stat().st_size,
            }
            for b in backups
        ]

    def get_audit_log(self) -> List[Dict[str, Any]]:
        """Return the current audit log of all write operations."""
        return list(self.audit_log)


if __name__ == "__main__":
    safe_print("========================================")
    safe_print("        NR AI CODE WRITER TEST")
    safe_print("========================================")

    writer = CodeWriter()
    test_file = "data/test_sample.py"
    initial_code = 'def greet():\n    print("Hello World")\n\ngreet()\n'

    safe_print("\n1. Writing initial file...")
    res1 = writer.write_file(test_file, initial_code)
    safe_print(f"Write result: {res1['success']}")

    safe_print("\n2. Modifying file...")
    res2 = writer.modify_file(
        test_file,
        'print("Hello World")',
        'print("Hello from NR AI")',
    )
    safe_print(f"Modify result: {res2['success']}")

    safe_print("\n3. Reading modified file...")
    res3 = writer.read_file(test_file)
    safe_print(f"Read code:\n{res3.get('code')}")

    safe_print("\n4. Backups available:")
    backups = writer.list_backups(test_file)
    for b in backups:
        safe_print(f" - {b['filename']} ({b['timestamp']})")

    if backups:
        safe_print("\n5. Testing restore...")
        res4 = writer.restore_backup(test_file, backups[0]["path"])
        safe_print(f"Restore result: {res4['success']}")

    safe_print("\n========================================")
    safe_print("🟢 CODE WRITER TEST COMPLETE")
    safe_print("========================================")
