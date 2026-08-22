import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.agent.code_writer import CodeWriter


class NodeReactProjectDetector:
    """
    Detects Node.js, React, Next.js, and Express projects.
    """

    @staticmethod
    def detect(project_dir: str | Path) -> Dict[str, Any]:
        p = Path(project_dir).resolve()
        if not p.exists() or not p.is_dir():
            return {"is_node_react": False, "reason": "Directory does not exist"}

        has_pkg = (p / "package.json").exists()
        is_react = False
        is_next = False
        is_express = False
        project_name = p.name

        if has_pkg:
            try:
                data = json.loads((p / "package.json").read_text(encoding="utf-8", errors="ignore"))
                project_name = data.get("name", p.name)
                deps = data.get("dependencies", {})
                dev_deps = data.get("devDependencies", {})
                all_deps = {**deps, **dev_deps}
                is_react = "react" in all_deps
                is_next = "next" in all_deps
                is_express = "express" in all_deps
            except Exception:
                pass

        has_src = (p / "src").exists()
        has_vite = (p / "vite.config.js").exists() or (p / "vite.config.ts").exists()

        is_node = has_pkg or list(p.glob("**/*.js")) or list(p.glob("**/*.jsx"))

        return {
            "is_node_react": bool(is_node or is_react or is_next),
            "project_name": project_name,
            "project_path": str(p),
            "is_react": is_react,
            "is_next": is_next,
            "is_express": is_express,
            "has_vite": has_vite,
        }


class NodeReactProjectInspector:
    """
    Inspects Node / React / Next.js projects (components, pages, package dependencies).
    """

    def __init__(self, project_dir: str | Path):
        self.project_dir = Path(project_dir).resolve()

    def inspect(self) -> Dict[str, Any]:
        detection = NodeReactProjectDetector.detect(self.project_dir)
        if not detection.get("is_node_react"):
            return {
                "success": False,
                "is_node_react": False,
                "message": detection.get("reason", "Not a Node / React project"),
            }

        js_files = []
        for ext in ("*.js", "*.jsx", "*.ts", "*.tsx"):
            for f in self.project_dir.glob(f"src/**/{ext}"):
                if "node_modules" not in f.parts and "dist" not in f.parts and "build" not in f.parts:
                    js_files.append(str(f.relative_to(self.project_dir)).replace("\\", "/"))

        components = [f for f in js_files if "component" in f.lower() or f.endswith(".jsx") or f.endswith(".tsx")]
        dependencies = self._parse_dependencies()

        return {
            "success": True,
            "is_node_react": True,
            "project_name": detection["project_name"],
            "project_path": str(self.project_dir),
            "is_react": detection["is_react"],
            "is_next": detection["is_next"],
            "total_files": len(js_files),
            "components": components,
            "dependencies": dependencies,
        }

    def _parse_dependencies(self) -> Dict[str, str]:
        pkg = self.project_dir / "package.json"
        if not pkg.exists():
            return {}
        try:
            data = json.loads(pkg.read_text(encoding="utf-8", errors="ignore"))
            return data.get("dependencies", {})
        except Exception:
            return {}


class NodeReactErrorAnalyzer:
    """
    Parses JavaScript, TypeScript, JSX, ESLint, and NPM runtime error logs.
    """

    @staticmethod
    def analyze(log: str) -> Dict[str, Any]:
        text = str(log or "")

        # 1. SyntaxError: Unexpected token
        syntax_match = re.search(r"SyntaxError:\s*([^\r\n]+)\s*\(([^:\)]+):(\d+):(\d+)\)", text)
        if syntax_match:
            msg, filepath, line_no, col_no = syntax_match.groups()
            return {
                "category": "js_syntax_error",
                "file": filepath.strip(),
                "line": int(line_no),
                "column": int(col_no),
                "error": msg.strip(),
                "diagnosis": f"JavaScript/JSX syntax error: {msg.strip()}",
                "suggestion": "Check unclosed brackets, missing commas, or JSX tag mismatch.",
            }

        # 2. Cannot find module
        module_match = re.search(r"Error: Cannot find module '([^']+)'", text)
        if module_match:
            mod_name = module_match.group(1)
            return {
                "category": "missing_node_module",
                "file": "package.json",
                "missing_module": mod_name,
                "error": f"Cannot find module '{mod_name}'",
                "diagnosis": f"The package '{mod_name}' is not installed or imported from an invalid relative path.",
                "suggestion": f"Run 'npm install {mod_name}' or verify the relative import path.",
            }

        return {
            "category": "unknown_node_error",
            "file": "src",
            "error": text[:300].strip(),
            "diagnosis": "Node.js / React build or execution error.",
            "suggestion": "Run 'npm test' or inspect package.json dependencies.",
        }


class NodeReactToolchain:
    """
    Node.js, React, Next.js, and Express Toolchain Manager for NR-AI.
    """

    def __init__(self, workspace: Optional[str] = None):
        self.workspace = Path(workspace or os.getcwd()).resolve()
        self.writer = CodeWriter(workspace=str(self.workspace))

    def scaffold_react_app(
        self,
        project_name: str = "react_dashboard",
        target_dir: Optional[str] = None,
        title: str = "NR AI React Dashboard",
        api_endpoint: str = "http://localhost:8000/api",
    ) -> Dict[str, Any]:
        """Scaffolds a modern React Vite application with components, API client, and responsive CSS."""
        safe_name = re.sub(r"[^a-z0-9_\-]", "_", project_name.lower())
        root = (self.workspace / (target_dir or f"data/{safe_name}")).resolve()
        root.mkdir(parents=True, exist_ok=True)

        # 1. package.json
        pkg_json = json.dumps(
            {
                "name": safe_name,
                "version": "1.0.0",
                "private": True,
                "type": "module",
                "scripts": {
                    "dev": "vite",
                    "build": "vite build",
                    "preview": "vite preview",
                    "test": "node --test",
                },
                "dependencies": {
                    "react": "^18.2.0",
                    "react-dom": "^18.2.0",
                    "lucide-react": "^0.344.0",
                },
                "devDependencies": {
                    "@vitejs/plugin-react": "^4.2.1",
                    "vite": "^5.1.4",
                },
            },
            indent=2,
        )

        # 2. vite.config.js
        vite_config = (
            "import { defineConfig } from 'vite';\n"
            "import react from '@vitejs/plugin-react';\n\n"
            "export default defineConfig({\n"
            "  plugins: [react()],\n"
            "  server: {\n"
            "    port: 3000,\n"
            "  }\n"
            "});\n"
        )

        # 3. index.html
        index_html = (
            "<!DOCTYPE html>\n"
            '<html lang="en">\n'
            "  <head>\n"
            '    <meta charset="UTF-8" />\n'
            '    <meta name="viewport" content="width=device-width, initial-scale=1.0" />\n'
            f"    <title>{title}</title>\n"
            "  </head>\n"
            "  <body>\n"
            '    <div id="root"></div>\n'
            '    <script type="module" src="/src/main.jsx"></script>\n'
            "  </body>\n"
            "</html>\n"
        )

        # 4. src/main.jsx
        main_jsx = (
            "import React from 'react';\n"
            "import ReactDOM from 'react-dom/client';\n"
            "import App from './App.jsx';\n"
            "import './index.css';\n\n"
            "ReactDOM.createRoot(document.getElementById('root')).render(\n"
            "  <React.StrictMode>\n"
            "    <App />\n"
            "  </React.StrictMode>\n"
            ");\n"
        )

        # 5. src/App.jsx
        app_jsx = (
            "import React, { useState, useEffect } from 'react';\n"
            "import { fetchMetrics } from './api';\n\n"
            "export default function App() {\n"
            "  const [metrics, setMetrics] = useState({ users: 1420, revenue: 58200, uptime: '99.98%' });\n"
            "  const [loading, setLoading] = useState(false);\n\n"
            "  useEffect(() => {\n"
            "    fetchMetrics().then(data => setMetrics(data));\n"
            "  }, []);\n\n"
            "  return (\n"
            '    <div className="dashboard-container">\n'
            f'      <header className="dashboard-header">\n'
            f"        <h1>{title}</h1>\n"
            '        <span className="badge">Live Status</span>\n'
            "      </header>\n"
            '      <main className="metrics-grid">\n'
            '        <div className="card">\n'
            "          <h3>Active Users</h3>\n"
            '          <p className="stat">{metrics.users}</p>\n'
            "        </div>\n"
            '        <div className="card">\n'
            "          <h3>Total Revenue</h3>\n"
            '          <p className="stat">${metrics.revenue}</p>\n'
            "        </div>\n"
            '        <div className="card">\n'
            "          <h3>System Uptime</h3>\n"
            '          <p className="stat">{metrics.uptime}</p>\n'
            "        </div>\n"
            "      </main>\n"
            "    </div>\n"
            "  );\n"
            "}\n"
        )

        # 6. src/api.js
        api_js = (
            f"const API_BASE = '{api_endpoint}';\n\n"
            "export async function fetchMetrics() {\n"
            "  try {\n"
            "    const res = await fetch(`${API_BASE}/metrics`);\n"
            "    if (res.ok) return await res.json();\n"
            "  } catch (err) {\n"
            "    console.warn('API offline, using mock metrics');\n"
            "  }\n"
            "  return { users: 1420, revenue: 58200, uptime: '99.98%' };\n"
            "}\n"
        )

        # 7. src/index.css
        index_css = (
            ":root {\n"
            "  --primary: #6366f1;\n"
            "  --bg: #0f172a;\n"
            "  --card-bg: #1e293b;\n"
            "  --text: #f8fafc;\n"
            "}\n"
            "* { box-sizing: border-box; margin: 0; padding: 0; font-family: sans-serif; }\n"
            "body { background: var(--bg); color: var(--text); padding: 2rem; }\n"
            ".dashboard-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 2rem; }\n"
            ".metrics-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 1.5rem; }\n"
            ".card { background: var(--card-bg); padding: 1.5rem; border-radius: 12px; border: 1px solid #334155; }\n"
            ".stat { font-size: 2rem; font-weight: bold; color: var(--primary); margin-top: 0.5rem; }\n"
        )

        # Write files
        files_to_write = {
            root / "package.json": pkg_json,
            root / "vite.config.js": vite_config,
            root / "index.html": index_html,
            root / "src" / "main.jsx": main_jsx,
            root / "src" / "App.jsx": app_jsx,
            root / "src" / "api.js": api_js,
            root / "src" / "index.css": index_css,
        }

        for path, content in files_to_write.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        return {
            "success": True,
            "project_name": safe_name,
            "project_path": str(root),
            "framework": "React (Vite)",
            "files_created": len(files_to_write),
        }
