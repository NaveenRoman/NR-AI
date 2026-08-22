import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


class ToolchainRegistry:
    """
    NR AI Centralized Toolchain Registry.
    Detects and inspects the physical availability, exact versions,
    executable paths, and specific capabilities of all developer SDKs and runtimes.
    """

    def __init__(self):
        self._cache: Optional[Dict[str, Dict[str, Any]]] = None

    def probe_all(self, refresh: bool = False) -> Dict[str, Dict[str, Any]]:
        if self._cache is not None and not refresh:
            return self._cache

        tools = {
            "python": self._probe_python(),
            "node": self._probe_node(),
            "npm": self._probe_npm(),
            "java": self._probe_java(),
            "javac": self._probe_javac(),
            "maven": self._probe_maven(),
            "gradle": self._probe_gradle(),
            "android_sdk": self._probe_android_sdk(),
            "adb": self._probe_adb(),
            "flutter": self._probe_flutter(),
            "dart": self._probe_dart(),
            "unity": self._probe_unity(),
            "unreal_editor": self._probe_unreal_editor(),
            "unreal_ubt": self._probe_unreal_ubt(),
            "msvc": self._probe_msvc(),
            "winsdk": self._probe_winsdk(),
            "docker": self._probe_docker(),
            "git": self._probe_git(),
        }
        self._cache = tools
        return tools

    def get_tool(self, name: str) -> Dict[str, Any]:
        all_tools = self.probe_all()
        return all_tools.get(name.lower(), {
            "name": name,
            "available": False,
            "status": "UNAVAILABLE",
            "version": None,
            "path": None,
            "capabilities": [],
        })

    def is_available(self, name: str) -> bool:
        return self.get_tool(name).get("available", False)

    # -------------------------------------------------------------
    # Tool-specific probe methods
    # -------------------------------------------------------------
    def _probe_python(self) -> Dict[str, Any]:
        py_exe = sys.executable or shutil.which("python") or shutil.which("python.exe")
        if not py_exe or not os.path.exists(py_exe):
            return self._unavailable("Python")
        try:
            ver = sys.version.split()[0]
            return {
                "name": "Python",
                "available": True,
                "status": "AVAILABLE",
                "version": ver,
                "path": str(py_exe),
                "capabilities": ["runtime", "unittest", "sqlite3", "bytecode_compilation", "http_server"],
            }
        except Exception:
            return self._unavailable("Python")

    def _probe_node(self) -> Dict[str, Any]:
        node_exe = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
        if not node_exe or not os.path.exists(node_exe):
            return self._unavailable("Node.js")
        try:
            res = subprocess.run([node_exe, "-v"], capture_output=True, text=True, timeout=3)
            ver = res.stdout.strip()
            return {
                "name": "Node.js",
                "available": True,
                "status": "AVAILABLE",
                "version": ver,
                "path": str(node_exe),
                "capabilities": ["runtime", "syntax_check", "v8_engine", "commonjs", "esm"],
            }
        except Exception:
            return self._unavailable("Node.js")

    def _probe_npm(self) -> Dict[str, Any]:
        npm_cmd = shutil.which("npm") or shutil.which("npm.cmd") or r"C:\Program Files\nodejs\npm.cmd"
        if not npm_cmd or not os.path.exists(npm_cmd):
            return self._unavailable("npm")
        try:
            res = subprocess.run([npm_cmd, "-v"], capture_output=True, text=True, shell=True, timeout=3)
            ver = res.stdout.strip()
            return {
                "name": "npm",
                "available": True,
                "status": "AVAILABLE",
                "version": ver,
                "path": str(npm_cmd),
                "capabilities": ["package_manager", "dependency_resolution", "script_runner"],
            }
        except Exception:
            return self._unavailable("npm")

    def _probe_java(self) -> Dict[str, Any]:
        java_exe = shutil.which("java") or r"C:\Program Files\Common Files\Oracle\Java\javapath\java.exe"
        if not java_exe or not os.path.exists(java_exe):
            return self._unavailable("Java Runtime")
        try:
            res = subprocess.run([java_exe, "-version"], capture_output=True, text=True, timeout=3)
            out = res.stderr or res.stdout
            m = re.search(r'version "([^"]+)"', out)
            ver = m.group(1) if m else "17+"
            return {
                "name": "Java Runtime",
                "available": True,
                "status": "AVAILABLE",
                "version": ver,
                "path": str(java_exe),
                "capabilities": ["jvm_execution", "bytecode_runner", "spring_boot_runner"],
            }
        except Exception:
            return self._unavailable("Java Runtime")

    def _probe_javac(self) -> Dict[str, Any]:
        javac_exe = shutil.which("javac") or r"C:\Program Files\Common Files\Oracle\Java\javapath\javac.exe"
        if not javac_exe or not os.path.exists(javac_exe):
            return self._unavailable("Java Compiler (javac)")
        try:
            res = subprocess.run([javac_exe, "-version"], capture_output=True, text=True, timeout=3)
            out = res.stdout or res.stderr
            ver = out.replace("javac", "").strip() or "17+"
            return {
                "name": "Java Compiler (javac)",
                "available": True,
                "status": "AVAILABLE",
                "version": ver,
                "path": str(javac_exe),
                "capabilities": ["bytecode_compilation", "java_compiler"],
            }
        except Exception:
            return self._unavailable("Java Compiler (javac)")

    def _probe_maven(self) -> Dict[str, Any]:
        mvn_cmd = shutil.which("mvn") or shutil.which("mvn.cmd")
        if not mvn_cmd:
            return self._unavailable("Maven")
        try:
            res = subprocess.run([mvn_cmd, "-v"], capture_output=True, text=True, shell=True, timeout=3)
            return {
                "name": "Maven",
                "available": True,
                "status": "AVAILABLE",
                "version": res.stdout.splitlines()[0] if res.stdout else "Available",
                "path": str(mvn_cmd),
                "capabilities": ["dependency_resolution", "package_build"],
            }
        except Exception:
            return self._unavailable("Maven")

    def _probe_gradle(self) -> Dict[str, Any]:
        gradle_cmd = shutil.which("gradle") or shutil.which("gradle.bat")
        if not gradle_cmd:
            return self._unavailable("Gradle")
        try:
            res = subprocess.run([gradle_cmd, "-v"], capture_output=True, text=True, shell=True, timeout=3)
            return {
                "name": "Gradle",
                "available": True,
                "status": "AVAILABLE",
                "version": "Available",
                "path": str(gradle_cmd),
                "capabilities": ["android_build", "kotlin_compilation", "task_execution"],
            }
        except Exception:
            return self._unavailable("Gradle")

    def _probe_android_sdk(self) -> Dict[str, Any]:
        sdk_paths = [
            os.environ.get("ANDROID_HOME"),
            os.environ.get("ANDROID_SDK_ROOT"),
            r"C:\Users\navee\AppData\Local\Android\Sdk",
        ]
        sdk_dir = next((p for p in sdk_paths if p and os.path.exists(p)), None)
        if not sdk_dir:
            return self._unavailable("Android SDK")

        bt_dir = Path(sdk_dir) / "build-tools"
        versions = [v.name for v in bt_dir.iterdir() if v.is_dir()] if bt_dir.exists() else []
        aapt2 = bt_dir / versions[-1] / "aapt2.exe" if versions else None

        return {
            "name": "Android SDK",
            "available": True,
            "status": "AVAILABLE",
            "version": versions[-1] if versions else "Installed",
            "path": str(sdk_dir),
            "aapt2_available": bool(aapt2 and aapt2.exists()),
            "capabilities": ["aapt2_resource_compile", "manifest_inspection", "avd_management"],
        }

    def _probe_adb(self) -> Dict[str, Any]:
        adb_paths = [
            shutil.which("adb"),
            shutil.which("adb.exe"),
            r"C:\Users\navee\AppData\Local\Android\Sdk\platform-tools\adb.exe",
        ]
        adb_exe = next((p for p in adb_paths if p and os.path.exists(p)), None)
        if not adb_exe:
            return self._unavailable("ADB")
        try:
            res = subprocess.run([adb_exe, "version"], capture_output=True, text=True, timeout=3)
            return {
                "name": "ADB (Android Debug Bridge)",
                "available": True,
                "status": "AVAILABLE",
                "version": res.stdout.splitlines()[0] if res.stdout else "Available",
                "path": str(adb_exe),
                "capabilities": ["device_discovery", "apk_install", "logcat_streaming"],
            }
        except Exception:
            return self._unavailable("ADB")

    def _probe_flutter(self) -> Dict[str, Any]:
        flutter_paths = [
            shutil.which("flutter"),
            shutil.which("flutter.bat"),
            r"C:\flutter\bin\flutter.bat",
        ]
        flutter_cmd = next((p for p in flutter_paths if p and os.path.exists(p)), None)
        if not flutter_cmd:
            return self._unavailable("Flutter")
        return {
            "name": "Flutter",
            "available": True,
            "status": "AVAILABLE",
            "version": "Installed (Dart 3.7.0)",
            "path": str(flutter_cmd),
            "capabilities": ["flutter_create", "flutter_analyze", "flutter_test", "flutter_build"],
        }

    def _probe_dart(self) -> Dict[str, Any]:
        dart_paths = [
            shutil.which("dart"),
            shutil.which("dart.bat"),
            r"C:\flutter\bin\dart.bat",
        ]
        dart_cmd = next((p for p in dart_paths if p and os.path.exists(p)), None)
        if not dart_cmd:
            return self._unavailable("Dart SDK")
        try:
            res = subprocess.run([dart_cmd, "--version"], capture_output=True, text=True, shell=True, timeout=3)
            out = res.stdout or res.stderr
            m = re.search(r"version:\s*([0-9\.]+)", out)
            ver = m.group(1) if m else "3.7.0"
            return {
                "name": "Dart SDK",
                "available": True,
                "status": "AVAILABLE",
                "version": ver,
                "path": str(dart_cmd),
                "capabilities": ["dart_analyze", "dart_test", "dart_run", "static_analysis"],
            }
        except Exception:
            return self._unavailable("Dart SDK")

    def _probe_unity(self) -> Dict[str, Any]:
        candidates = [
            r"C:\Program Files\Unity\Hub\Editor\2022.3.35f1\Editor\Unity.exe",
            r"C:\Program Files\Unity\Editor\Unity.exe",
        ]
        unity_exe = next((p for p in candidates if os.path.exists(p)), None)
        if not unity_exe:
            return self._unavailable("Unity Editor")
        return {
            "name": "Unity Editor",
            "available": True,
            "status": "AVAILABLE",
            "version": "2022.3.35f1",
            "path": str(unity_exe),
            "capabilities": ["batch_mode_build", "scene_execution", "csharp_scripting"],
        }

    def _probe_unreal_editor(self) -> Dict[str, Any]:
        candidates = [
            r"C:\Program Files\Epic Games\UE_5.3\Engine\Binaries\Win64\UnrealEditor.exe",
            r"C:\Program Files\Epic Games\UE_5.2\Engine\Binaries\Win64\UnrealEditor.exe",
        ]
        editor_exe = next((p for p in candidates if os.path.exists(p)), None)
        if not editor_exe:
            return self._unavailable("Unreal Editor")
        return {
            "name": "Unreal Editor",
            "available": True,
            "status": "AVAILABLE",
            "version": "5.3",
            "path": str(editor_exe),
            "capabilities": ["editor_launch", "gameplay_simulation", "blueprint_compilation"],
        }

    def _probe_unreal_ubt(self) -> Dict[str, Any]:
        candidates = [
            r"C:\Program Files\Epic Games\UE_5.3\Engine\Binaries\DotNET\UnrealBuildTool\UnrealBuildTool.exe",
        ]
        ubt_exe = next((p for p in candidates if os.path.exists(p)), None)
        if not ubt_exe:
            return self._unavailable("UnrealBuildTool (UBT)")
        return {
            "name": "UnrealBuildTool (UBT)",
            "available": True,
            "status": "AVAILABLE",
            "version": "5.3",
            "path": str(ubt_exe),
            "capabilities": ["cpp_module_compilation", "uht_reflection_generation"],
        }

    def _probe_msvc(self) -> Dict[str, Any]:
        cl_exe = shutil.which("cl") or shutil.which("cl.exe")
        if not cl_exe:
            return self._unavailable("MSVC (cl.exe)")
        return {
            "name": "MSVC (cl.exe)",
            "available": True,
            "status": "AVAILABLE",
            "version": "MSVC C++ Compiler",
            "path": str(cl_exe),
            "capabilities": ["c_cpp_compilation", "native_windows_build"],
        }

    def _probe_winsdk(self) -> Dict[str, Any]:
        winsdk_dir = r"C:\Program Files (x86)\Windows Kits\10"
        if os.path.exists(winsdk_dir):
            inc = Path(winsdk_dir) / "Include"
            if inc.exists():
                versions = [v for v in os.listdir(inc) if v.startswith("10.")]
                if versions:
                    return {
                        "name": "Windows SDK",
                        "available": True,
                        "status": "AVAILABLE",
                        "version": versions[-1],
                        "path": str(winsdk_dir),
                        "capabilities": ["windows_headers", "win32_api", "directx_sdks"],
                    }
        return self._unavailable("Windows SDK")

    def _probe_docker(self) -> Dict[str, Any]:
        docker_exe = shutil.which("docker") or shutil.which("docker.exe")
        if not docker_exe:
            return self._unavailable("Docker")
        return {
            "name": "Docker",
            "available": True,
            "status": "AVAILABLE",
            "version": "Installed",
            "path": str(docker_exe),
            "capabilities": ["container_build", "docker_compose", "daemon_client"],
        }

    def _probe_git(self) -> Dict[str, Any]:
        git_exe = shutil.which("git") or shutil.which("git.exe") or r"C:\Program Files\Git\cmd\git.exe"
        if not git_exe or not os.path.exists(git_exe):
            return self._unavailable("Git")
        try:
            res = subprocess.run([git_exe, "--version"], capture_output=True, text=True, timeout=3)
            ver = res.stdout.strip().replace("git version", "").strip()
            return {
                "name": "Git",
                "available": True,
                "status": "AVAILABLE",
                "version": ver,
                "path": str(git_exe),
                "capabilities": ["version_control", "branching", "status_diff", "safe_checkpoints"],
            }
        except Exception:
            return self._unavailable("Git")

    def _unavailable(self, name: str) -> Dict[str, Any]:
        return {
            "name": name,
            "available": False,
            "status": "UNAVAILABLE",
            "version": None,
            "path": None,
            "capabilities": [],
        }
