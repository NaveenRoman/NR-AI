import fnmatch
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.agent.code_writer import CodeWriter

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


class ProjectInspector:
    """
    NR AI Project Inspector & Multi-File Refactoring Engine.

    Capabilities:
        - Inspect directory trees and project structure
        - Recursive file discovery and search
        - Grep search across source files
        - Multi-file batch find-and-replace with automated checkpointing
        - Language and project type detection
    """

    IGNORE_DIRS = {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".idea",
        ".vscode",
        "dist",
        "build",
        "bin",
        "obj",
        ".gradle",
    }

    PROTECTED_FRAMEWORK_DIRS = {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        "app",
        "tests",
        ".gemini",
    }

    def __init__(self, workspace: Optional[str] = None):
        self.workspace = Path(workspace or os.getcwd()).resolve()
        self.writer = CodeWriter(workspace=str(self.workspace))

    def inspect_structure(
        self,
        relative_dir: str = "",
        max_depth: int = 3,
        include_hidden: bool = False,
    ) -> Dict[str, Any]:
        """Inspect and return the directory tree and file statistics."""
        target_dir = (self.workspace / relative_dir).resolve()
        if not target_dir.exists() or not target_dir.is_dir():
            return {
                "success": False,
                "message": f"Directory not found: {target_dir}",
                "tree": {},
            }

        total_files = 0
        total_dirs = 0
        ext_counts: Dict[str, int] = {}

        def build_tree(current_dir: Path, current_depth: int) -> Dict[str, Any]:
            nonlocal total_files, total_dirs
            if current_depth > max_depth:
                return {"_truncated": True}

            result: Dict[str, Any] = {"dirs": {}, "files": []}

            try:
                entries = sorted(
                    current_dir.iterdir(),
                    key=lambda p: (not p.is_dir(), p.name.lower()),
                )
            except Exception as e:
                return {"_error": str(e)}

            for entry in entries:
                if entry.name in self.IGNORE_DIRS and not include_hidden:
                    continue
                if entry.name.startswith(".") and not include_hidden:
                    continue

                if entry.is_dir():
                    total_dirs += 1
                    result["dirs"][entry.name] = build_tree(
                        entry, current_depth + 1
                    )
                else:
                    total_files += 1
                    ext = entry.suffix.lower() or "no_ext"
                    ext_counts[ext] = ext_counts.get(ext, 0) + 1
                    result["files"].append({
                        "name": entry.name,
                        "size_bytes": entry.stat().st_size,
                    })

            return result

        tree = build_tree(target_dir, 1)

        return {
            "success": True,
            "root": str(target_dir),
            "total_files": total_files,
            "total_dirs": total_dirs,
            "file_types": ext_counts,
            "tree": tree,
        }

    def find_files(
        self,
        pattern: str = "*",
        relative_dir: str = "",
        include_framework: bool = False,
    ) -> List[str]:
        """Find all files matching a glob pattern."""
        target_dir = (self.workspace / relative_dir).resolve()
        matches = []

        ignore_set = self.IGNORE_DIRS if include_framework else self.PROTECTED_FRAMEWORK_DIRS

        for root, dirs, files in os.walk(target_dir):
            dirs[:] = [
                d
                for d in dirs
                if d not in ignore_set and not d.startswith(".")
            ]
            for file in files:
                if fnmatch.fnmatch(file, pattern):
                    rel_path = Path(root, file).relative_to(self.workspace)
                    matches.append(str(rel_path))

        return matches

    def search_content(
        self,
        query: str,
        file_pattern: str = "*",
        case_sensitive: bool = False,
        include_framework: bool = False,
    ) -> List[Dict[str, Any]]:
        """Grep for text/regex across project files."""
        results = []
        files = self.find_files(file_pattern, include_framework=include_framework)

        flags = 0 if case_sensitive else re.IGNORECASE

        for rel_file in files:
            full_path = self.workspace / rel_file
            try:
                text = full_path.read_text(encoding="utf-8", errors="ignore")
                for line_idx, line in enumerate(text.splitlines(), start=1):
                    if re.search(re.escape(query), line, flags=flags):
                        results.append({
                            "file": rel_file,
                            "line": line_idx,
                            "content": line.strip(),
                        })
            except Exception:
                continue

        return results

    def batch_replace(
        self,
        search_text: str,
        replace_text: str,
        file_pattern: str = "*",
        relative_dir: str = "",
        make_backup: bool = True,
        include_framework: bool = False,
    ) -> Dict[str, Any]:
        """
        Replace search_text with replace_text across all matching files in the project.
        Creates automated checkpoints for safety and rollback.
        """
        files = self.find_files(
            file_pattern,
            relative_dir=relative_dir,
            include_framework=include_framework,
        )
        modified_files = []
        total_replacements = 0

        safe_print(
            f"🔍 Searching for '{search_text}' across {len(files)} target files..."
        )

        for rel_file in files:
            full_path = self.workspace / rel_file
            try:
                content = full_path.read_text(
                    encoding="utf-8", errors="ignore"
                )
                if search_text in content:
                    count = content.count(search_text)
                    new_content = content.replace(search_text, replace_text)

                    # Use CodeWriter for safe checkpointing
                    w_res = self.writer.write_file(
                        rel_file,
                        new_content,
                        overwrite=True,
                        make_backup=make_backup,
                    )
                    if w_res["success"]:
                        modified_files.append({
                            "file": rel_file,
                            "occurrences": count,
                            "backup": w_res.get("backup_path"),
                        })
                        total_replacements += count
            except Exception as e:
                safe_print(f"⚠️ Error modifying {rel_file}: {e}")

        safe_print(
            f"✅ Modified {len(modified_files)} file(s), replaced {total_replacements} occurrence(s)."
        )

        return {
            "success": True,
            "search_text": search_text,
            "replace_text": replace_text,
            "files_modified_count": len(modified_files),
            "total_replacements": total_replacements,
            "modified_files": modified_files,
        }


if __name__ == "__main__":
    safe_print("========================================")
    safe_print("     NR AI PROJECT INSPECTOR TEST")
    safe_print("========================================")

    inspector = ProjectInspector()
    struct = inspector.inspect_structure(max_depth=2)
    safe_print(f"Project structure inspect: {struct['success']}")
    safe_print(f"Total files detected: {struct['total_files']}")
    safe_print(f"File types: {struct['file_types']}")

    python_files = inspector.find_files("*.py", include_framework=True)
    safe_print(f"Python files found (with framework): {len(python_files)}")

    safe_print("\n========================================")
    safe_print("🟢 PROJECT INSPECTOR READY")
    safe_print("========================================")
