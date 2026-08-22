import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.agent.code_writer import CodeWriter


class UnityProjectDetector:
    """
    Detects whether a directory is a Unity game project and inspects its version
    and packages.
    """

    @staticmethod
    def detect(project_dir: str | Path) -> Dict[str, Any]:
        p = Path(project_dir).resolve()
        if not p.exists() or not p.is_dir():
            return {"is_unity": False, "reason": "Directory does not exist"}

        has_assets = (p / "Assets").exists() and (p / "Assets").is_dir()
        has_project_settings = (p / "ProjectSettings").exists()
        has_version = (p / "ProjectSettings" / "ProjectVersion.txt").exists()

        is_unity = has_assets and (has_project_settings or has_version or list(p.glob("Assets/**/*.cs")))

        unity_version = "2022.3.20f1"
        if has_version:
            try:
                txt = (p / "ProjectSettings" / "ProjectVersion.txt").read_text(encoding="utf-8", errors="ignore")
                m = re.search(r"m_EditorVersion:\s*([0-9a-zA-Z\.\_]+)", txt)
                if m:
                    unity_version = m.group(1)
            except Exception:
                pass

        return {
            "is_unity": is_unity,
            "project_path": str(p),
            "project_name": p.name,
            "unity_version": unity_version,
            "has_assets": has_assets,
            "has_project_settings": has_project_settings,
        }


class UnityProjectInspector:
    """
    Inspects Unity project assets, C# scripts, scenes, and package manifests.
    """

    def __init__(self, project_dir: str | Path):
        self.project_dir = Path(project_dir).resolve()

    def inspect(self) -> Dict[str, Any]:
        detection = UnityProjectDetector.detect(self.project_dir)
        if not detection.get("is_unity"):
            return {
                "success": False,
                "is_unity": False,
                "message": detection.get("reason", "Not a Unity project"),
            }

        cs_files = [str(f.relative_to(self.project_dir)).replace("\\", "/") for f in self.project_dir.glob("Assets/**/*.cs")]
        scenes = [str(f.relative_to(self.project_dir)).replace("\\", "/") for f in self.project_dir.glob("Assets/**/*.unity")]

        packages = self._parse_packages()

        return {
            "success": True,
            "is_unity": True,
            "project_name": detection["project_name"],
            "project_path": str(self.project_dir),
            "unity_version": detection["unity_version"],
            "total_scripts": len(cs_files),
            "scripts": cs_files[:20],
            "scenes": scenes,
            "packages_count": len(packages),
            "packages": packages[:10],
        }

    def _parse_packages(self) -> List[str]:
        manifest = self.project_dir / "Packages" / "manifest.json"
        if not manifest.exists():
            return []
        try:
            data = json.loads(manifest.read_text(encoding="utf-8", errors="ignore"))
            return list(data.get("dependencies", {}).keys())
        except Exception:
            return []


class UnityErrorAnalyzer:
    """
    Parses and categorizes C# compiler errors and Unity editor logs.
    """

    @staticmethod
    def analyze(log: str) -> Dict[str, Any]:
        text = str(log or "")

        # 1. C# compiler error: e.g., Assets/Scripts/PlayerController.cs(15,20): error CS0103: The name 'speed' does not exist
        cs_match = re.search(r"([^\r\n:]+\.cs)\((\d+),(\d+)\):\s+error\s+(CS\d+):\s+([^\r\n]+)", text)
        if cs_match:
            filepath, line_no, col_no, err_code, msg = cs_match.groups()
            return {
                "category": "csharp_compiler",
                "file": filepath.strip(),
                "line": int(line_no),
                "column": int(col_no),
                "error_code": err_code,
                "error": msg.strip(),
                "diagnosis": f"C# compilation error ({err_code}): {msg.strip()}",
                "suggestion": "Declare missing variables, fix typing, or add required 'using' namespaces.",
            }

        # 2. Missing MonoBehaviour or Component
        if "NullReferenceException" in text or "UnassignedReferenceException" in text:
            return {
                "category": "unity_runtime_exception",
                "file": "Assets/Scripts",
                "error": "Unity null reference or unassigned component reference.",
                "diagnosis": "A GameObject component, SerializeField, or reference was not instantiated or wired.",
                "suggestion": "Check GetComponent<T>() calls and ensure references are assigned in the inspector.",
            }

        return {
            "category": "unknown_unity_error",
            "file": "Assets",
            "error": text[:300].strip(),
            "diagnosis": "Unity engine or script build failure.",
            "suggestion": "Inspect C# script syntax or Unity package dependencies.",
        }


class UnityToolchain:
    """
    Unity C# Game Engine Toolchain Manager for NR-AI.
    """

    def __init__(self, workspace: Optional[str] = None):
        self.workspace = Path(workspace or os.getcwd()).resolve()
        self.writer = CodeWriter(workspace=str(self.workspace))

    def scaffold_project(
        self,
        project_name: str = "UnityGame",
        target_dir: Optional[str] = None,
        game_type: str = "2D/3D Multi-genre",
    ) -> Dict[str, Any]:
        """Scaffolds complete Unity C# project structure."""
        safe_name = re.sub(r"[^a-zA-Z0-9_]", "", project_name)
        root = (self.workspace / (target_dir or f"data/{safe_name.lower()}")).resolve()
        root.mkdir(parents=True, exist_ok=True)

        # 1. ProjectSettings/ProjectVersion.txt
        version_txt = (
            "m_EditorVersion: 2022.3.20f1\n"
            "m_EditorVersionWithRevision: 2022.3.20f1 (e3d5510698a8)\n"
        )

        # 2. Packages/manifest.json
        manifest_json = json.dumps(
            {
                "dependencies": {
                    "com.unity.inputsystem": "1.7.0",
                    "com.unity.textmeshpro": "3.0.6",
                    "com.unity.timeline": "1.7.6",
                    "com.unity.modules.ui": "1.0.0",
                    "com.unity.modules.physics": "1.0.0",
                    "com.unity.modules.audio": "1.0.0",
                }
            },
            indent=2,
        )

        # 3. Assets/Scripts/GameManager.cs
        game_manager_cs = (
            "using UnityEngine;\n\n"
            "namespace NRAI.GameCore\n"
            "{\n"
            "    public class GameManager : MonoBehaviour\n"
            "    {\n"
            "        public static GameManager Instance { get; private set; }\n"
            "        public int Score { get; private set; }\n"
            "        public bool IsGameOver { get; private set; }\n\n"
            "        private void Awake()\n"
            "        {\n"
            "            if (Instance == null)\n"
            "            {\n"
            "                Instance = this;\n"
            "                DontDestroyOnLoad(gameObject);\n"
            "            }\n"
            "            else\n"
            "            {\n"
            "                Destroy(gameObject);\n"
            "            }\n"
            "        }\n\n"
            "        public void AddScore(int points)\n"
            "        {\n"
            "            if (IsGameOver) return;\n"
            "            Score += points;\n"
            "            Debug.Log($\"[GameManager] Score updated: {Score}\");\n"
            "        }\n\n"
            "        public void TriggerGameOver()\n"
            "        {\n"
            "            IsGameOver = true;\n"
            "            Debug.Log(\"[GameManager] Game Over!\");\n"
            "        }\n"
            "    }\n"
            "}\n"
        )

        # 4. Assets/Scripts/PlayerController.cs
        player_controller_cs = (
            "using UnityEngine;\n\n"
            "namespace NRAI.GameCore\n"
            "{\n"
            "    [RequireComponent(typeof(Rigidbody2D))]\n"
            "    public class PlayerController : MonoBehaviour\n"
            "    {\n"
            "        [Header(\"Movement Settings\")]\n"
            "        [SerializeField] private float moveSpeed = 5.0f;\n"
            "        [SerializeField] private float jumpForce = 8.0f;\n\n"
            "        private Rigidbody2D _rb;\n"
            "        private Vector2 _moveInput;\n\n"
            "        private void Awake()\n"
            "        {\n"
            "            _rb = GetComponent<Rigidbody2D>();\n"
            "        }\n\n"
            "        private void Update()\n"
            "        {\n"
            "            float x = Input.GetAxisRaw(\"Horizontal\");\n"
            "            float y = Input.GetAxisRaw(\"Vertical\");\n"
            "            _moveInput = new Vector2(x, y).normalized;\n\n"
            "            if (Input.GetButtonDown(\"Jump\"))\n"
            "            {\n"
            "                Jump();\n"
            "            }\n"
            "        }\n\n"
            "        private void FixedUpdate()\n"
            "        {\n"
            "            _rb.velocity = new Vector2(_moveInput.x * moveSpeed, _rb.velocity.y);\n"
            "        }\n\n"
            "        private void Jump()\n"
            "        {\n"
            "            _rb.velocity = new Vector2(_rb.velocity.x, jumpForce);\n"
            "        }\n"
            "    }\n"
            "}\n"
        )

        # 5. Assets/Scripts/NetworkManager.cs (Multiplayer helper)
        network_manager_cs = (
            "using System;\n"
            "using UnityEngine;\n\n"
            "namespace NRAI.GameCore\n"
            "{\n"
            "    public class NetworkManager : MonoBehaviour\n"
            "    {\n"
            "        public bool IsConnected { get; private set; }\n"
            "        public string ServerAddress { get; set; } = \"127.0.0.1:7777\";\n\n"
            "        public void ConnectToServer()\n"
            "        {\n"
            "            IsConnected = true;\n"
            "            Debug.Log($\"[NetworkManager] Connected to {ServerAddress}\");\n"
            "        }\n\n"
            "        public void Disconnect()\n"
            "        {\n"
            "            IsConnected = false;\n"
            "            Debug.Log(\"[NetworkManager] Disconnected from server\");\n"
            "        }\n"
            "    }\n"
            "}\n"
        )

        # Write files
        files_to_write = {
            root / "ProjectSettings" / "ProjectVersion.txt": version_txt,
            root / "Packages" / "manifest.json": manifest_json,
            root / "Assets" / "Scripts" / "GameManager.cs": game_manager_cs,
            root / "Assets" / "Scripts" / "PlayerController.cs": player_controller_cs,
            root / "Assets" / "Scripts" / "NetworkManager.cs": network_manager_cs,
        }

        for path, content in files_to_write.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        return {
            "success": True,
            "project_name": safe_name,
            "project_path": str(root),
            "game_type": game_type,
            "files_created": len(files_to_write),
        }
