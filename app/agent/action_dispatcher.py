import sys
from pathlib import Path
from typing import Any, Dict, Optional

from app.agent.action_engine import ActionEngine
from app.agent.android_toolchain import AndroidProjectInspector, AndroidToolchain
from app.agent.code_agent import CodeAgent
from app.agent.code_writer import CodeWriter
from app.agent.computer_control import ComputerControl
from app.agent.docker_toolchain import DockerProjectDetector, DockerToolchain
from app.agent.flutter_toolchain import FlutterProjectInspector, FlutterToolchain
from app.agent.node_react_toolchain import NodeReactProjectInspector, NodeReactToolchain
from app.agent.service_supervisor import ServiceSupervisor
from app.agent.spring_boot_toolchain import SpringBootProjectInspector, SpringBootToolchain
from app.agent.unity_toolchain import UnityToolchain, UnityProjectInspector
from app.agent.unreal_toolchain import UnrealToolchain
from app.agent.universal_project_engine import UniversalProjectEngine
from app.agent.visual_agent import VisualAgent

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


class ActionDispatcher:
    """
    Converts high-level planned actions into physical computer actions,
    code interactions, terminal commands, Android, Flutter, Unity, Spring Boot,
    React, or Universal Project Engine operations.

    Planner
       ↓
    ActionDispatcher
       ├── UniversalProjectEngine (Multi-ecosystem orchestrator)
       ├── ComputerControl (Generic 20-primitive computer control)
       ├── CodeAgent (Code writing, execution, and self-healing)
       ├── AndroidToolchain (Android/Gradle project manager)
       ├── FlutterToolchain (Flutter/Dart project manager)
       ├── UnityToolchain (Unity/C# project manager)
       ├── SpringBootToolchain (Spring Boot/Java project manager)
       ├── NodeReactToolchain (Node/React project manager)
       ├── DockerToolchain (Docker/Compose manager)
       ├── ServiceSupervisor (Long-running process management)
       ├── VisualAgent / ActionEngine (Visual OCR and low-level mouse/keyboard)
    """

    def __init__(self, memory: Optional[Any] = None):
        self.memory = memory
        workspace = str(memory.workspace) if memory and hasattr(memory, "workspace") else None
        self.engine = ActionEngine()
        self.visual = VisualAgent()
        self.code_agent = CodeAgent(workspace=workspace)
        self.code_writer = CodeWriter(workspace=workspace)
        self.computer_control = ComputerControl(workspace=workspace)
        self.supervisor = ServiceSupervisor(workspace=workspace)
        self.android = AndroidToolchain(workspace=workspace)
        self.flutter = FlutterToolchain(workspace=workspace)
        self.unity = UnityToolchain(workspace=workspace)
        self.unreal = UnrealToolchain(workspace=workspace)
        self.spring_boot = SpringBootToolchain(workspace=workspace)
        self.node_react = NodeReactToolchain(workspace=workspace)
        self.docker = DockerToolchain(workspace=workspace)
        self.universal = UniversalProjectEngine(workspace=workspace)

    def click_text(
        self,
        application: str,
        region: str,
        target: str,
    ) -> Dict[str, Any]:
        return self.computer_control.click_visible_text(
            target=target, region=region, application=application
        )

    def click_popup_text(
        self,
        menu: str,
        target: str,
    ) -> Dict[str, Any]:
        return self.computer_control.click_popup_menu_item(
            menu_name=menu, target_item=target
        )

    def execute(
        self,
        action: Dict[str, Any],
        application: str = "Visual Studio Code",
    ) -> Dict[str, Any]:
        """
        Execute a high-level structured action (GUI, Code, Terminal, Project).
        """
        if not isinstance(action, dict):
            return {
                "success": False,
                "message": "Action must be a dictionary.",
            }

        action_type = action.get("type")

        # -------------------------------------------------
        # CODE ACTIONS
        # -------------------------------------------------
        if action_type == "code_execute":
            return self.code_agent.execute(
                filename=action["filename"],
                code=action.get("code", ""),
                expected_output=action.get("expected_output"),
                timeout=action.get("timeout", 15.0),
            )

        if action_type == "write_code":
            return self.code_agent.write_code(
                filename=action["filename"],
                code=action.get("code", ""),
            )

        if action_type == "run_code":
            return self.code_agent.run_code(
                filename=action["filename"],
                timeout=action.get("timeout", 15.0),
            )

        if action_type == "fix_code":
            return self.code_agent.fix_existing_file(
                filename=action["filename"],
                timeout=action.get("timeout", 15.0),
            )

        if action_type == "modify_code":
            fn = action["filename"].lower()
            if fn.endswith((".jsx", ".tsx", ".html", ".css", ".json", ".md", ".txt")) or not action.get("run", True):
                if action.get("search") and action.get("replace"):
                    return self.code_writer.modify_file(
                        action["filename"],
                        action["search"],
                        action["replace"],
                    )
                if action.get("code"):
                    return self.code_writer.write_file(
                        action["filename"],
                        action["code"],
                    )
                return {"success": True, "message": f"Updated {action['filename']}"}

            return self.code_agent.modify_and_run(
                filename=action["filename"],
                search_text=action.get("search", ""),
                replace_text=action.get("replace", ""),
                timeout=action.get("timeout", 15.0),
            )

        # -------------------------------------------------
        # PROJECT & MULTI-FILE ACTIONS
        # -------------------------------------------------
        if action_type in {"batch_replace", "replace_in_project"}:
            return self.computer_control.batch_modify_project(
                search_text=action["search"],
                replace_text=action["replace"],
                file_pattern=action.get("pattern", "*"),
            )

        if action_type == "inspect_project":
            return self.computer_control.inspect_project(
                relative_dir=action.get("dir", ""),
                max_depth=action.get("max_depth", 3),
            )

        # -------------------------------------------------
        # TERMINAL & OS ACTIONS
        # -------------------------------------------------
        if action_type in {"run_terminal_cmd", "run_terminal_command"}:
            return self.computer_control.run_terminal_command(
                command=action["command"],
                cwd=action.get("cwd"),
                timeout=action.get("timeout", 30.0),
            )

        if action_type == "open_terminal":
            return self.computer_control.open_terminal()

        if action_type == "read_terminal_output":
            return self.computer_control.read_terminal_output()

        if action_type == "open_file":
            return self.computer_control.open_file(action["path"])

        if action_type == "open_explorer":
            return self.computer_control.open_explorer(action.get("dir", ""))

        if action_type == "switch_tab":
            return self.computer_control.switch_tab(
                direction=action.get("direction", "next")
            )

        if action_type == "switch_window":
            return self.computer_control.switch_window(action["application"])

        if action_type == "detect_window":
            return self.computer_control.detect_window(action["application"])

        if action_type == "select_all":
            return self.computer_control.select_all()

        if action_type == "copy":
            return self.computer_control.copy()

        if action_type == "paste":
            return self.computer_control.paste()

        if action_type == "verify_ui":
            return self.computer_control.verify_ui_state(
                action["expected"], wait_time=action.get("wait_time", 1.0)
            )

        if action_type == "verify_file":
            return self.computer_control.verify_file_state(
                action["filename"], expected_snippet=action.get("expected")
            )

        if action_type in {"verify_service_startup", "smoke_test_server"}:
            return self.computer_control.smoke_test_server(
                command=action["command"],
                port=action.get("port", 8000),
                endpoint=action.get("endpoint", "/health"),
                timeout=action.get("timeout", 10.0),
                cwd=action.get("cwd"),
            )

        # -------------------------------------------------
        # SERVICE SUPERVISION & CONTROL ACTIONS
        # -------------------------------------------------
        if action_type == "service_start":
            return self.supervisor.start_service(
                name=action.get("name", "server"),
                command=action["command"],
                port=action.get("port"),
                cwd=action.get("cwd"),
                env=action.get("env"),
            )

        if action_type == "service_stop":
            return self.supervisor.stop_service(name=action.get("name", "server"))

        if action_type == "service_restart":
            return self.supervisor.restart_service(name=action.get("name", "server"))

        if action_type == "service_status":
            return self.supervisor.get_service_status(name=action.get("name", "server"))

        if action_type == "cancel_task":
            if self.memory:
                self.memory.request_cancellation()
            return {"success": True, "message": "Task cancelled successfully."}

        if action_type == "rollback_last_change":
            if self.memory:
                rb = self.memory.pop_rollback()
                if rb:
                    target = rb["file"]
                    bak = rb["backup_path"]
                    res = self.code_writer.restore_backup(target, bak)
                    return res
            chk_dir = Path("data/checkpoints")
            if chk_dir.exists():
                baks = sorted(chk_dir.glob("*.bak"), key=lambda p: p.stat().st_mtime, reverse=True)
                if baks:
                    bak = baks[0]
                    orig_name = bak.name.split(".")[0]
                    return self.code_writer.restore_backup(orig_name, str(bak))
            return {"success": False, "message": "No checkpoint available to roll back."}

        # -------------------------------------------------
        # ANDROID TOOLCHAIN ACTIONS
        # -------------------------------------------------
        if action_type in {"android_create", "create_android_project"}:
            return self.android.scaffold_project(
                project_name=action.get("name", "AndroidApp"),
                package_name=action.get("package", "com.example.nrai"),
                target_dir=action.get("target_dir"),
                use_compose=action.get("use_compose", True),
            )

        if action_type == "android_add_login":
            return self.android.add_login_screen(
                project_dir=action.get("project_dir", "data/androidapp"),
                package_name=action.get("package", "com.example.nrai"),
            )

        if action_type == "android_add_firebase":
            return self.android.add_firebase_auth(
                project_dir=action.get("project_dir", "data/androidapp"),
                package_name=action.get("package", "com.example.nrai"),
            )

        if action_type == "android_change_color":
            return self.android.change_button_color(
                project_dir=action.get("project_dir", "data/androidapp"),
                color_hex=action.get("color", "0xFF00C853"),
            )

        if action_type == "android_inspect":
            inspector = AndroidProjectInspector(action.get("project_dir", "data/androidapp"))
            return inspector.inspect()

        if action_type == "android_list_devices":
            return self.android.list_devices()

        # -------------------------------------------------
        # FLUTTER TOOLCHAIN ACTIONS
        # -------------------------------------------------
        if action_type in {"flutter_create", "create_flutter_project"}:
            return self.flutter.scaffold_project(
                project_name=action.get("name", "flutter_app"),
                target_dir=action.get("target_dir"),
                title=action.get("title", "NR AI Flutter App"),
            )

        if action_type == "flutter_inspect":
            inspector = FlutterProjectInspector(action.get("project_dir", "data/flutter_app"))
            return inspector.inspect()

        if action_type == "flutter_add_login":
            return self.flutter.add_login_screen(
                project_dir=action.get("project_dir", "data/flutter_app"),
            )

        if action_type == "flutter_add_firebase":
            return self.flutter.add_firebase_auth(
                project_dir=action.get("project_dir", "data/flutter_app"),
            )

        if action_type == "flutter_change_theme":
            return self.flutter.change_theme(
                project_dir=action.get("project_dir", "data/flutter_app"),
                primary_color_hex=action.get("color", "0xFF00C853"),
            )

        if action_type == "flutter_add_dependency":
            return self.flutter.add_dependency(
                project_dir=action.get("project_dir", "data/flutter_app"),
                package_name=action["package"],
                version=action.get("version", "any"),
            )

        if action_type == "flutter_analyze":
            return self.flutter.analyze(
                project_dir=action.get("project_dir", "data/flutter_app"),
            )

        if action_type == "flutter_test":
            return self.flutter.run_tests(
                project_dir=action.get("project_dir", "data/flutter_app"),
            )

        if action_type == "flutter_list_devices":
            return self.flutter.list_devices()

        # -------------------------------------------------
        # UNIVERSAL & MULTI-ECOSYSTEM ACTIONS
        # -------------------------------------------------
        if action_type in {"universal_create_project", "create_universal_project"}:
            return self.universal.scaffold_universal_project(
                prompt=action.get("prompt", "Full-stack application"),
                target_dir=action.get("target_dir"),
            )

        if action_type in {"unity_create", "create_unity_project"}:
            return self.unity.scaffold_project(
                project_name=action.get("name", "UnityGame"),
                target_dir=action.get("target_dir"),
                game_type=action.get("game_type", "2D/3D Multi-genre"),
            )

        if action_type in {"unreal_create", "create_unreal_project"}:
            return self.unreal.scaffold_project(
                project_name=action.get("name", "UnrealGame"),
                target_dir=action.get("target_dir"),
                engine_version=action.get("engine_version", "5.3"),
            )

        if action_type in {"unreal_build", "build_unreal_project"}:
            return self.unreal.build_project(
                project_dir=action.get("project_dir", "data/unreal_game")
            )

        if action_type in {"spring_create", "create_spring_boot_project"}:
            return self.spring_boot.scaffold_project(
                project_name=action.get("name", "banking_backend"),
                target_dir=action.get("target_dir"),
                domain=action.get("domain", "banking"),
            )

        if action_type in {"react_create", "create_react_project"}:
            return self.node_react.scaffold_react_app(
                project_name=action.get("name", "react_dashboard"),
                target_dir=action.get("target_dir"),
                title=action.get("title", "NR AI React Dashboard"),
            )

        if action_type in {"docker_create", "create_docker_setup"}:
            return self.docker.scaffold_saas_docker(
                project_dir=action.get("project_dir", "data/saas_platform"),
                app_name=action.get("name", "saas_platform"),
            )

        # -------------------------------------------------
        # VISUAL & PHYSICAL COMPUTER ACTIONS
        # -------------------------------------------------
        if action_type == "click_popup_text":
            return self.click_popup_text(
                action.get("menu", "File"),
                action["target"],
            )

        if action_type == "click_text":
            return self.click_text(
                application,
                action.get("region", "main_content"),
                action["target"],
            )

        if action_type in {
            "click",
            "double_click",
            "move",
            "type",
            "press",
            "hotkey",
            "scroll",
            "wait",
        }:
            return self.engine.execute(action)

        return {
            "success": False,
            "message": f"Unsupported action: {action_type}",
        }


if __name__ == "__main__":
    safe_print("========================================")
    safe_print("        NR AI ACTION DISPATCHER")
    safe_print("========================================")

    dispatcher = ActionDispatcher()

    safe_print("\n1. Safe terminal command execution...")
    res1 = dispatcher.execute({
        "type": "run_terminal_cmd",
        "command": "python --version",
    })
    safe_print(f"Terminal execute result: {res1['success']}")

    safe_print("\n2. Project inspection dispatch...")
    res2 = dispatcher.execute({"type": "inspect_project", "max_depth": 1})
    safe_print(f"Inspect project result: {res2['success']}")

    safe_print("\n========================================")
    if res1["success"] and res2["success"]:
        safe_print("🟢 ACTION DISPATCHER TEST PASSED")
    else:
        safe_print("🔴 ACTION DISPATCHER TEST FAILED")
    safe_print("========================================")