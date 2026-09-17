import ctypes
import logging
import os
import subprocess
import time
from typing import Any, Dict, Optional, Tuple

from app.commands.app_discovery import AppDiscovery
from app.agent.window_manager import WindowManager

logger = logging.getLogger("NRAI.AppLauncher")


class AppLauncher:
    """Discovers, safely launches, and deterministically verifies approved applications on the user desktop."""

    def __init__(self):
        self.discovery = AppDiscovery()
        self.window_manager = WindowManager()

        # Applications that NR AI is currently allowed to launch.
        self.approved_applications = {
            "notepad",
            "calculator",
            "calc",
            "paint",
            "mspaint",
            "command prompt",
            "cmd",
            "powershell",
            "android studio",
            "android",
            "visual studio",
            "visual studio 2022",
            "vs",
            "visual studio code",
            "vs code",
            "vscode",
            "code",
            "chrome",
            "google chrome",
            "unity",
            "unity hub",
            "unity editor",
            "unreal engine",
            "unreal",
            "ue5",
            "ue4",
        }

    def _launch_interactive_shell_process(self, path: str) -> Tuple[bool, str]:
        """
        Spawns an application process using explorer.exe (the interactive desktop shell)
        as parent, guaranteeing that the child process is created natively on WinSta0\\Default
        even when NR-AI server is invoked from a background task or service.
        """
        if not hasattr(ctypes, "windll"):
            return False, "Not on Windows"

        try:
            from ctypes import wintypes
            kernel32 = ctypes.windll.kernel32
            import psutil

            explorer_pid = None
            for p in psutil.process_iter(["pid", "name"]):
                if p.name().lower() == "explorer.exe":
                    explorer_pid = p.pid
                    break

            if not explorer_pid:
                return False, "Explorer shell process not found"

            PROCESS_CREATE_PROCESS = 0x0080
            hExplorer = kernel32.OpenProcess(PROCESS_CREATE_PROCESS, False, explorer_pid)
            if not hExplorer:
                return False, f"Failed to open explorer process {explorer_pid}"

            try:
                PROC_THREAD_ATTRIBUTE_PARENT_PROCESS = 0x00020000
                EXTENDED_STARTUPINFO_PRESENT = 0x00080000

                class STARTUPINFO(ctypes.Structure):
                    _fields_ = [
                        ('cb', wintypes.DWORD),
                        ('lpReserved', wintypes.LPWSTR),
                        ('lpDesktop', wintypes.LPWSTR),
                        ('lpTitle', wintypes.LPWSTR),
                        ('dwX', wintypes.DWORD),
                        ('dwY', wintypes.DWORD),
                        ('dwXSize', wintypes.DWORD),
                        ('dwYSize', wintypes.DWORD),
                        ('dwXCountChars', wintypes.DWORD),
                        ('dwYCountChars', wintypes.DWORD),
                        ('dwFillAttribute', wintypes.DWORD),
                        ('dwFlags', wintypes.DWORD),
                        ('wShowWindow', wintypes.WORD),
                        ('cbReserved2', wintypes.WORD),
                        ('lpReserved2', ctypes.c_char_p),
                        ('hStdInput', wintypes.HANDLE),
                        ('hStdOutput', wintypes.HANDLE),
                        ('hStdError', wintypes.HANDLE),
                    ]

                class STARTUPINFOEX(ctypes.Structure):
                    _fields_ = [
                        ("StartupInfo", STARTUPINFO),
                        ("lpAttributeList", ctypes.c_void_p),
                    ]

                class PROCESS_INFORMATION(ctypes.Structure):
                    _fields_ = [
                        ('hProcess', wintypes.HANDLE),
                        ('hThread', wintypes.HANDLE),
                        ('dwProcessId', wintypes.DWORD),
                        ('dwThreadId', wintypes.DWORD),
                    ]

                size = ctypes.c_size_t(0)
                kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
                attr_list = ctypes.create_string_buffer(size.value)
                kernel32.InitializeProcThreadAttributeList(attr_list, 1, 0, ctypes.byref(size))

                handle_buf = wintypes.HANDLE(hExplorer)
                kernel32.UpdateProcThreadAttribute(
                    attr_list,
                    0,
                    PROC_THREAD_ATTRIBUTE_PARENT_PROCESS,
                    ctypes.byref(handle_buf),
                    ctypes.sizeof(handle_buf),
                    None,
                    None,
                )

                siex = STARTUPINFOEX()
                siex.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEX)
                siex.StartupInfo.lpDesktop = r"WinSta0\Default"
                siex.lpAttributeList = ctypes.cast(attr_list, ctypes.c_void_p)

                pi = PROCESS_INFORMATION()

                app_dir = os.path.dirname(str(path))
                success = kernel32.CreateProcessW(
                    str(path),
                    None,
                    None,
                    None,
                    False,
                    EXTENDED_STARTUPINFO_PRESENT,
                    None,
                    app_dir if os.path.isdir(app_dir) else None,
                    ctypes.byref(siex.StartupInfo),
                    ctypes.byref(pi),
                )
                kernel32.DeleteProcThreadAttributeList(attr_list)

                if success:
                    pid = pi.dwProcessId
                    kernel32.CloseHandle(pi.hProcess)
                    kernel32.CloseHandle(pi.hThread)
                    return True, f"Spawned via explorer shell (PID: {pid}): {path}"
                else:
                    err = kernel32.GetLastError()
                    return False, f"CreateProcessW failed with error code {err}"
            finally:
                kernel32.CloseHandle(hExplorer)
        except Exception as ex:
            return False, f"Shell parent dispatch error: {ex}"

    def _launch_path(self, path):
        """Launch an executable or Windows shortcut safely onto the interactive desktop."""

        if not path:
            return False, "Target path is empty"

        try:
            # Ensure calling thread is attached to interactive user desktop
            if hasattr(ctypes, "windll"):
                try:
                    user32 = ctypes.windll.user32
                    hdesk = user32.OpenDesktopW("Default", 0, False, 0x01FF)
                    if hdesk:
                        user32.SetThreadDesktop(hdesk)
                except Exception as de:
                    logger.debug(f"Desktop attach notice: {de}")

            resolved_target = str(path)
            if resolved_target.lower().endswith(".lnk"):
                try:
                    import win32com.client
                    wscript = win32com.client.Dispatch("WScript.Shell")
                    sc = wscript.CreateShortcut(resolved_target)
                    if sc.TargetPath and os.path.exists(sc.TargetPath):
                        resolved_target = sc.TargetPath
                except Exception:
                    pass

            # Try interactive explorer parent process creation first for executables
            if resolved_target.lower().endswith(".exe"):
                ok, detail = self._launch_interactive_shell_process(resolved_target)
                if ok:
                    return True, detail

            # Windows shortcuts (.lnk) fallback to Windows shell startfile.
            if str(path).lower().endswith(".lnk"):
                os.startfile(path)
                return True, f"Started shell shortcut: {path}"

            # Executables fallback if shell parent was not available.
            if str(path).lower().endswith(".exe"):
                si = subprocess.STARTUPINFO()
                si.lpDesktop = r"WinSta0\Default"
                proc = subprocess.Popen([str(path)], shell=False, startupinfo=si)
                return True, f"Started process (PID: {proc.pid}): {path}"

            # Other registered launch targets.
            os.startfile(path)
            return True, f"Started via startfile: {path}"

        except Exception as error:
            logger.error(f"Launcher error for {path}: {error}")
            return False, str(error)

    def launch_detailed(self, application_name: str, verify_timeout: Optional[float] = None) -> dict:
        """
        Validates authorization, discovers target, safely launches onto interactive desktop,
        and enforces deterministic multi-step window visibility verification.
        Returns detailed status metadata.
        """
        raw_name = (application_name or "").strip()
        name_lower = raw_name.lower()
        normalized = self.discovery.normalize_name(name_lower)

        if verify_timeout is None:
            if normalized in (
                "android studio", "android", "visual studio", "visual studio 2022",
                "vs", "unity", "unity editor", "unreal engine"
            ):
                verify_timeout = 25.0
            else:
                verify_timeout = 12.0

        # 1. Strict authorization check against whitelist
        if name_lower not in self.approved_applications and normalized not in self.approved_applications:
            return {
                "success": False,
                "verified": False,
                "application": raw_name,
                "authorized": False,
                "path": None,
                "launch_detail": None,
                "message": f"I am not authorized to launch {raw_name} yet.",
                "error": "UNAUTHORIZED_APPLICATION",
            }

        # 2. Check if application already has a visible window open on the interactive desktop
        existing_matches, _ = self.window_manager.find_window_deterministic(normalized)
        if existing_matches:
            self.window_manager.activate_window(normalized)
            w = existing_matches[0]
            clean_title = w.get("title", raw_name)
            return {
                "success": True,
                "verified": True,
                "application": raw_name,
                "authorized": True,
                "path": str(w.get("process_name", "")),
                "launch_detail": f"Activated existing window '{clean_title}' (HWND: {w.get('hwnd')})",
                "message": f"{raw_name} is already open and brought to the foreground.",
                "error": None,
                "window": w,
            }

        # 3. Discover application executable / shortcut
        discovered = self.discovery.find_application(normalized)

        if not discovered:
            return {
                "success": False,
                "verified": False,
                "application": raw_name,
                "authorized": True,
                "path": None,
                "launch_detail": None,
                "message": f"I couldn't find {raw_name} on this computer.",
                "error": "APPLICATION_NOT_FOUND",
            }

        # Registry results can be dictionaries.
        if isinstance(discovered, dict):
            path = discovered.get("path")
            app_display_name = discovered.get("name", raw_name)

            if not path:
                return {
                    "success": False,
                    "verified": False,
                    "application": app_display_name,
                    "authorized": True,
                    "path": None,
                    "launch_detail": None,
                    "message": f"I found {app_display_name}, but I couldn't find a launch target.",
                    "error": "TARGET_PATH_MISSING",
                }
        else:
            path = discovered

        try:
            logger.info(f"Launching {raw_name} from path: {path}")
        except Exception:
            pass

        # 4. Launch process onto WinSta0\Default
        success, detail = self._launch_path(path)
        if not success:
            return {
                "success": False,
                "verified": False,
                "application": raw_name,
                "authorized": True,
                "path": str(path),
                "launch_detail": detail,
                "message": f"I couldn't open {raw_name}.",
                "error": detail or "LAUNCH_FAILED",
            }

        # 5. Multi-step deterministic window verification loop
        poll_start = time.time()
        poll_interval = 0.5
        verified_window = None
        error_dialog = None

        while time.time() - poll_start < verify_timeout:
            time.sleep(poll_interval)
            matches, _ = self.window_manager.find_window_deterministic(normalized)
            if matches:
                w = matches[0]
                # Bring to foreground and verify
                self.window_manager.activate_window(normalized)
                if w.get("visible", False) or (w.get("width", 0) > 100 and w.get("height", 0) > 100):
                    verified_window = w
                    break

            # Detect if a setup error or crash dialog appeared
            err_w = self.window_manager.find_error_window(normalized)
            if err_w:
                error_dialog = err_w
                break

        if verified_window:
            clean_title = verified_window.get("title", raw_name)
            return {
                "success": True,
                "verified": True,
                "application": raw_name,
                "authorized": True,
                "path": str(path),
                "launch_detail": f"Verified visible window '{clean_title}' (HWND: {verified_window.get('hwnd')})",
                "message": f"{raw_name} opened and verified visible on screen.",
                "error": None,
                "window": verified_window,
            }

        if error_dialog:
            err_title = error_dialog.get("title", "Error")
            err_detail = self.window_manager.get_dialog_text(error_dialog.get("hwnd")) or err_title
            return {
                "success": False,
                "verified": False,
                "application": raw_name,
                "authorized": True,
                "path": str(path),
                "launch_detail": f"Application failed with error dialog: {err_title} ({err_detail})",
                "message": f"I couldn't open {raw_name}. An error occurred: {err_detail}",
                "error": "APPLICATION_ERROR_DIALOG",
                "window": error_dialog,
            }

        # 6. Verification failed: process started but no visible window on desktop within grace period
        return {
            "success": False,
            "verified": False,
            "application": raw_name,
            "authorized": True,
            "path": str(path),
            "launch_detail": f"Process launched but top-level window did not appear after {verify_timeout}s.",
            "message": f"I attempted to open {raw_name}, but its window did not appear visibly on screen.",
            "error": "WINDOW_NOT_DETECTED",
        }

    def launch(self, application_name):
        return self.launch_detailed(application_name)["message"]

    def execute(self, command):
        command = command.lower().strip()

        for prefix in ("open ", "launch ", "start "):
            if command.startswith(prefix):
                application_name = command[len(prefix):].strip()
                if application_name:
                    return self.launch(application_name)

        return None


if __name__ == "__main__":

    launcher = AppLauncher()

    print("========================================")
    print("       NR AI DISCOVERY LAUNCHER")
    print("========================================")

    tests = [
        "open android studio",
        "open visual studio",
        "open vs code",
        "open chrome",
    ]

    for command in tests:
        print(f"\n🎙️ Command: {command}")

        response = launcher.execute(command)

        print(f"🤖 NR AI: {response}")