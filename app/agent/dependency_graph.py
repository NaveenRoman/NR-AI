import ast
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Set, Optional


class DependencyGraph:
    """
    NR AI Dependency-Aware Impact Analysis Engine.

    Builds an internal directed dependency graph between project source files
    and test suites. Given a modified file, computes the exact set of downstream
    dependents that must be rebuilt or re-tested, eliminating unnecessary re-builds.
    """

    def __init__(self, workspace: Optional[str] = None):
        self.workspace = Path(workspace or os.getcwd()).resolve()
        self.forward_graph: Dict[str, Set[str]] = {}  # file -> files it imports
        self.reverse_graph: Dict[str, Set[str]] = {}  # file -> files that import it
        self.files_scanned: Set[str] = set()

    def build_graph(self, project_dir: Optional[str | Path] = None) -> Dict[str, Any]:
        """Scans the project directory and constructs forward and reverse dependency maps."""
        p = Path(project_dir or self.workspace).resolve()
        self.forward_graph.clear()
        self.reverse_graph.clear()
        self.files_scanned.clear()

        all_files = list(p.rglob("*.*"))
        for file in all_files:
            if not file.is_file():
                continue
            if any(part.startswith(".") or part in {"__pycache__", "node_modules", "target", "bin", "build"} for part in file.parts):
                continue

            rel_path = str(file.relative_to(p)).replace("\\", "/")
            self.files_scanned.add(rel_path)
            self.forward_graph[rel_path] = set()
            if rel_path not in self.reverse_graph:
                self.reverse_graph[rel_path] = set()

            ext = file.suffix.lower()
            try:
                if ext == ".py":
                    imports = self._parse_python_imports(file, p)
                elif ext in {".js", ".jsx", ".ts", ".tsx"}:
                    imports = self._parse_js_imports(file, p)
                elif ext in {".java", ".kt"}:
                    imports = self._parse_java_imports(file, p)
                elif ext in {".h", ".hpp", ".cpp", ".cs"}:
                    imports = self._parse_cpp_includes(file, p)
                else:
                    imports = set()

                for imp in imports:
                    self.forward_graph[rel_path].add(imp)
                    if imp not in self.reverse_graph:
                        self.reverse_graph[imp] = set()
                    self.reverse_graph[imp].add(rel_path)
            except Exception:
                pass

        return {
            "total_files_scanned": len(self.files_scanned),
            "total_edges": sum(len(v) for v in self.forward_graph.values()),
        }

    def get_impacted_files(self, modified_file: str) -> Set[str]:
        """
        Returns all files transitively impacted by changes to modified_file
        (downstream dependents that import or rely on modified_file).
        """
        norm_mod = modified_file.replace("\\", "/").strip()
        # Find matching key in graph
        target = None
        for key in self.reverse_graph:
            if key == norm_mod or key.endswith(norm_mod) or norm_mod.endswith(key):
                target = key
                break

        if not target:
            return {norm_mod}

        impacted: Set[str] = set()
        visited: Set[str] = set()
        queue = [target]

        while queue:
            curr = queue.pop(0)
            if curr in visited:
                continue
            visited.add(curr)
            impacted.add(curr)

            dependents = self.reverse_graph.get(curr, set())
            for dep in dependents:
                if dep not in visited:
                    queue.append(dep)

        return impacted

    def get_impacted_tests(self, modified_file: str) -> List[str]:
        """Filters impacted files to find which test suites need execution."""
        all_impacted = self.get_impacted_files(modified_file)
        tests = [
            f for f in all_impacted
            if "test" in f.lower() or f.endswith("_test.py") or f.startswith("test_") or f.endswith(".test.js")
        ]
        return sorted(tests)

    # -------------------------------------------------------------
    # Language-specific parsers
    # -------------------------------------------------------------
    def _parse_python_imports(self, filepath: Path, project_root: Path) -> Set[str]:
        imports = set()
        try:
            tree = ast.parse(filepath.read_text(encoding="utf-8", errors="ignore"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        mod = alias.name.split(".")[0]
                        candidate = f"{mod}.py"
                        if (filepath.parent / candidate).exists():
                            imports.add(str((filepath.parent / candidate).relative_to(project_root)).replace("\\", "/"))
                        elif (project_root / candidate).exists():
                            imports.add(candidate)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        mod = node.module.replace(".", "/")
                        candidates = [f"{mod}.py", f"{mod}/__init__.py"]
                        for c in candidates:
                            if (project_root / c).exists():
                                imports.add(str((project_root / c).relative_to(project_root)).replace("\\", "/"))
                                break
                            elif (filepath.parent / c).exists():
                                imports.add(str((filepath.parent / c).relative_to(project_root)).replace("\\", "/"))
                                break
        except Exception:
            pass
        return imports

    def _parse_js_imports(self, filepath: Path, project_root: Path) -> Set[str]:
        imports = set()
        try:
            txt = filepath.read_text(encoding="utf-8", errors="ignore")
            # Matches import ... from './path' or require('./path')
            for m in re.finditer(r"(?:import\s+.*?\s+from\s+['\"]|require\(['\"])([\.\/A-Za-z0-9_\-]+)['\"]", txt):
                target = m.group(1)
                if target.startswith("."):
                    resolved = (filepath.parent / target).resolve()
                    for ext in ["", ".js", ".jsx", ".ts", ".tsx", "/index.js"]:
                        cand = Path(str(resolved) + ext)
                        if cand.exists() and cand.is_file():
                            imports.add(str(cand.relative_to(project_root)).replace("\\", "/"))
                            break
        except Exception:
            pass
        return imports

    def _parse_java_imports(self, filepath: Path, project_root: Path) -> Set[str]:
        imports = set()
        try:
            txt = filepath.read_text(encoding="utf-8", errors="ignore")
            for m in re.finditer(r"import\s+([A-Za-z0-9_\.]+);", txt):
                imp = m.group(1).replace(".", "/") + ".java"
                for match in project_root.rglob(Path(imp).name):
                    imports.add(str(match.relative_to(project_root)).replace("\\", "/"))
        except Exception:
            pass
        return imports

    def _parse_cpp_includes(self, filepath: Path, project_root: Path) -> Set[str]:
        imports = set()
        try:
            txt = filepath.read_text(encoding="utf-8", errors="ignore")
            for m in re.finditer(r"#include\s+[\"<]([A-Za-z0-9_\-\.\/]+)[\">]", txt):
                inc_name = Path(m.group(1)).name
                for match in project_root.rglob(inc_name):
                    if match.is_file() and match != filepath:
                        imports.add(str(match.relative_to(project_root)).replace("\\", "/"))
        except Exception:
            pass
        return imports
