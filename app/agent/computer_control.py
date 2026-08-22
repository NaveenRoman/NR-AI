import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.agent.action_engine import ActionEngine
from app.agent.action_verifier import ActionVerifier
from app.agent.code_writer import CodeWriter
from app.agent.project_inspector import ProjectInspector
from app.agent.state_verifier import StateVerifier
from app.agent.target_resolver import TargetResolver
from app.agent.terminal_manager import TerminalManager
from app.agent.visual_agent import VisualAgent
from app.vision.menu_popup import MenuPopupVision

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


class ComputerControl:
    """
    NR AI Unified Computer Control Engine.

    Provides a comprehensive suite of 20 generic, reusable computer-control
    primitives spanning GUI automation, OCR targeting, window switching,
    filesystem operations, project refactoring, terminal interaction, and verification.
    """

    def __init__(self, workspace: Optional[str] = None):
        self.workspace = Path(workspace or os.getcwd()).resolve()

        self.action_engine = ActionEngine()
        self.visual_agent = VisualAgent()
        self.state_verifier = StateVerifier()
        self.action_verifier = ActionVerifier()
        self.target_resolver = TargetResolver()
        self.terminal_manager = TerminalManager(workspace=str(self.workspace))
        self.project_inspector = ProjectInspector(workspace=str(self.workspace))
        self.code_writer = CodeWriter(workspace=str(self.workspace))
        self.menu_popup = MenuPopupVision(minimum_confidence=0.85)

    # -------------------------------------------------
    # 1-2. VISUAL CLICK & MENU INTERACTION
    # -------------------------------------------------

    def click_visible_text(
        self,
        target: str,
        region: str = "main_content",
        application: str = "Visual Studio Code",
    ) -> Dict[str, Any]:
        """1. Locate and click visible text on screen."""
        safe_print(f"🖱️ [ComputerControl] Click text: '{target}' (Region: {region})")
        res = self.visual_agent.locate(application, region, target)
        if not res["success"]:
            return res

        coords = res["target"]["center"]
        act_res = self.action_engine.click(coords[0], coords[1])
        return {
            "success": True,
            "target": res["target"],
            "coordinates": coords,
        }

    def click_popup_menu_item(
        self,
        menu_name: str,
        target_item: str,
    ) -> Dict[str, Any]:
        """2. Click an item inside an open menu popup."""
        safe_print(f"🖱️ [ComputerControl] Click popup: '{target_item}' in menu '{menu_name}'")
        item = self.menu_popup.find_in_open_popup(target_item)
        if not item:
            return {
                "success": False,
                "message": f"Popup item '{target_item}' not found in '{menu_name}'",
            }
        x, y = item["center"]
        self.action_engine.click(x, y)
        return {
            "success": True,
            "target": item,
            "coordinates": (x, y),
        }

    # -------------------------------------------------
    # 3-8. KEYBOARD, SELECTION & CLIPBOARD
    # -------------------------------------------------

    def type_text(self, text: str, interval: float = 0.01) -> Dict[str, Any]:
        """3. Type text using keyboard simulation."""
        safe_print(f"⌨️ [ComputerControl] Typing: {text[:50]}...")
        self.action_engine.type_text(text, interval=interval)
        return {"success": True, "text": text}

    def press_key(self, key: str) -> Dict[str, Any]:
        """4. Press a single keyboard key (enter, esc, tab, etc.)."""
        safe_print(f"⌨️ [ComputerControl] Pressing key: {key}")
        self.action_engine.press(key)
        return {"success": True, "key": key}

    def press_hotkey(self, *keys: str) -> Dict[str, Any]:
        """5. Press a combination of hotkeys (e.g. 'ctrl', 's')."""
        safe_print(f"⌨️ [ComputerControl] Hotkey: {' + '.join(keys)}")
        self.action_engine.hotkey(*keys)
        return {"success": True, "keys": list(keys)}

    def scroll(self, amount: int) -> Dict[str, Any]:
        """6. Scroll mouse wheel."""
        safe_print(f"🖱️ [ComputerControl] Scroll: {amount}")
        self.action_engine.scroll(amount)
        return {"success": True, "amount": amount}

    def select_all(self) -> Dict[str, Any]:
        """7. Select all text (Ctrl+A)."""
        safe_print("⌨️ [ComputerControl] Select All (Ctrl+A)")
        self.action_engine.hotkey("ctrl", "a")
        return {"success": True}

    def copy(self) -> Dict[str, Any]:
        """8a. Copy selection (Ctrl+C)."""
        safe_print("📋 [ComputerControl] Copy (Ctrl+C)")
        self.action_engine.hotkey("ctrl", "c")
        return {"success": True}

    def paste(self) -> Dict[str, Any]:
        """8b. Paste clipboard (Ctrl+V)."""
        safe_print("📋 [ComputerControl] Paste (Ctrl+V)")
        self.action_engine.hotkey("ctrl", "v")
        return {"success": True}

    # -------------------------------------------------
    # 9-10. FILE & TAB MANAGEMENT
    # -------------------------------------------------

    def open_file(self, filename: str) -> Dict[str, Any]:
        """9. Open a file in OS / default editor."""
        path = (self.workspace / filename).resolve()
        if not path.exists():
            return {"success": False, "message": f"File not found: {path}"}
        try:
            safe_print(f"📂 [ComputerControl] Opening file: {path}")
            os.startfile(str(path))
            return {"success": True, "path": str(path)}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def switch_tab(self, direction: str = "next") -> Dict[str, Any]:
        """10. Switch editor tabs (Ctrl+Tab or Ctrl+Shift+Tab)."""
        safe_print(f"🪟 [ComputerControl] Switch tab: {direction}")
        if direction == "prev":
            self.action_engine.hotkey("ctrl", "shift", "tab")
        else:
            self.action_engine.hotkey("ctrl", "tab")
        return {"success": True, "direction": direction}

    # -------------------------------------------------
    # 11-12. EXPLORER & TERMINAL LAUNCHERS
    # -------------------------------------------------

    def open_explorer(self, relative_dir: str = "") -> Dict[str, Any]:
        """11. Open Windows Explorer or VS Code Explorer."""
        target_dir = (self.workspace / relative_dir).resolve()
        safe_print(f"📁 [ComputerControl] Opening Explorer: {target_dir}")
        try:
            os.startfile(str(target_dir))
            return {"success": True, "path": str(target_dir)}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def open_terminal(self) -> Dict[str, Any]:
        """12. Open or toggle terminal (Ctrl+`)."""
        safe_print("💻 [ComputerControl] Toggle Terminal (Ctrl+`)")
        self.action_engine.hotkey("ctrl", "`")
        return {"success": True}

    # -------------------------------------------------
    # 13-14. TERMINAL COMMANDS & OUTPUT
    # -------------------------------------------------

    def run_terminal_command(
        self,
        command: str,
        cwd: Optional[str] = None,
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        """13. Execute terminal command safely."""
        return self.terminal_manager.run_command(command, cwd=cwd, timeout=timeout)

    def read_terminal_output(self) -> Dict[str, Any]:
        """14. Retrieve history of terminal outputs."""
        history = self.terminal_manager.get_history()
        latest = history[-1] if history else None
        return {
            "success": True,
            "total_commands": len(history),
            "latest": latest,
        }

    # -------------------------------------------------
    # 15-16. WINDOW DETECTION & SWITCHING
    # -------------------------------------------------

    def detect_window(self, application_name: str) -> Dict[str, Any]:
        """15. Detect target application window."""
        win = self.target_resolver.resolve_best_window(application_name)
        if win:
            return {"success": True, "window": win}
        return {"success": False, "message": f"Window not found: {application_name}"}

    def switch_window(self, application_name: str) -> Dict[str, Any]:
        """16. Switch focus to target application window."""
        win = self.target_resolver.resolve_best_window(application_name)
        if not win:
            return {"success": False, "message": f"Window not found: {application_name}"}
        ok, msg = self.target_resolver.window_manager.activate_window(win["title"])
        return {"success": ok, "message": msg, "window": win["title"]}

    # -------------------------------------------------
    # 17-18. PROJECT INSPECTION & BATCH REFACTORING
    # -------------------------------------------------

    def inspect_project(
        self, relative_dir: str = "", max_depth: int = 3
    ) -> Dict[str, Any]:
        """17. Inspect project tree and structure."""
        return self.project_inspector.inspect_structure(relative_dir, max_depth)

    def batch_modify_project(
        self,
        search_text: str,
        replace_text: str,
        file_pattern: str = "*",
    ) -> Dict[str, Any]:
        """18. Batch refactor across related files with automated checkpoints."""
        return self.project_inspector.batch_replace(
            search_text, replace_text, file_pattern=file_pattern, make_backup=True
        )

    # -------------------------------------------------
    # 19-20. STATE & FILE VERIFICATION
    # -------------------------------------------------

    def verify_ui_state(
        self, expected_text: str, wait_time: float = 1.0
    ) -> Dict[str, Any]:
        """19. Verify that expected text is visible on screen."""
        return self.state_verifier.verify(expected_text, wait_time=wait_time)

    def verify_file_state(
        self, filename: str, expected_snippet: Optional[str] = None
    ) -> Dict[str, Any]:
        """20. Verify file exists and contains expected content."""
        res = self.code_writer.read_file(filename)
        if not res["success"]:
            return res
        if expected_snippet and expected_snippet not in res["code"]:
            return {
                "success": False,
                "message": f"Expected snippet not found in {filename}",
                "file": filename,
            }
        return {"success": True, "file": filename, "lines": res["lines_count"]}

    def smoke_test_server(
        self,
        command: Any,
        port: int = 8000,
        endpoint: str = "/health",
        timeout: float = 10.0,
        cwd: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Start a background service, probe HTTP endpoint, verify health response,
        and cleanly terminate the service.
        """
        import json
        import subprocess
        import time
        import urllib.request

        work_dir = Path(cwd or self.workspace).resolve()
        safe_print(f"🚀 [SmokeTest] Starting server: {command} in {work_dir}")

        if isinstance(command, str):
            cmd_args = command.split()
        else:
            cmd_args = list(command)

        if cmd_args and cmd_args[0].endswith(".py"):
            cmd_args = [sys.executable, *cmd_args]

        proc = None
        try:
            proc = subprocess.Popen(
                cmd_args,
                cwd=work_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            url = f"http://127.0.0.1:{port}{endpoint}"
            safe_print(f"🌐 [SmokeTest] Probing {url} (timeout: {timeout}s)...")

            start_time = time.time()
            response_data = None
            status_code = None

            while time.time() - start_time < timeout:
                if proc.poll() is not None:
                    stdout, stderr = proc.communicate(timeout=1.0)
                    return {
                        "success": False,
                        "message": f"Server terminated prematurely with code {proc.returncode}",
                        "stdout": stdout,
                        "stderr": stderr,
                    }

                try:
                    req = urllib.request.Request(
                        url, headers={"User-Agent": "NR-AI-SmokeTest"}
                    )
                    with urllib.request.urlopen(req, timeout=1.0) as resp:
                        status_code = resp.getcode()
                        raw_body = resp.read().decode("utf-8")
                        try:
                            response_data = json.loads(raw_body)
                        except Exception:
                            response_data = raw_body
                        if status_code == 200:
                            safe_print(
                                f"✅ [SmokeTest] Service healthy (HTTP {status_code}): {response_data}"
                            )
                            return {
                                "success": True,
                                "status_code": status_code,
                                "response": response_data,
                                "endpoint": url,
                                "message": "Service started and responded to health probe successfully.",
                            }
                except Exception:
                    time.sleep(0.3)

            return {
                "success": False,
                "message": f"Server probe timed out after {timeout} seconds.",
                "endpoint": url,
            }

        except Exception as e:
            return {
                "success": False,
                "message": f"Failed to run server smoke test: {e}",
            }
        finally:
            if proc:
                if proc.poll() is None:
                    safe_print("🛑 [SmokeTest] Gracefully stopping test server...")
                    try:
                        proc.terminate()
                        proc.wait(timeout=2.0)
                    except Exception:
                        proc.kill()
                try:
                    if proc.stdout:
                        proc.stdout.close()
                    if proc.stderr:
                        proc.stderr.close()
                except Exception:
                    pass


if __name__ == "__main__":
    safe_print("========================================")
    safe_print("     NR AI COMPUTER CONTROL TEST")
    safe_print("========================================")

    cc = ComputerControl()
    safe_print("Testing project inspection primitive...")
    struct = cc.inspect_project(max_depth=1)
    safe_print(f"Inspection success: {struct['success']}")

    safe_print("Testing file state verification primitive...")
    f_res = cc.verify_file_state("main.py", "NRBrain")
    safe_print(f"File state verification success: {f_res['success']}")

    safe_print("\n========================================")
    safe_print("🟢 COMPUTER CONTROL MODULE READY")
    safe_print("========================================")
