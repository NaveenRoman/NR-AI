"""
NR AI Desktop-Aware Window Manager for Windows (WinSta0\\default).

Provides real interactive desktop window awareness and foreground window activation:
- Enumerates real user windows on WinSta0\\default (overcoming background service isolation).
- Extracts window handle (HWND), title, PID, process name, visibility, minimized/maximized state.
- Deterministic window search with application aliases and anti-false-positive filtering.
- Safe window activation without mouse/keyboard injection or shell commands.
"""

import ctypes
import ctypes.wintypes
import logging
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    import psutil
except ImportError:
    psutil = None

logger = logging.getLogger("NRAI.WindowManager")

user32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None
kernel32 = ctypes.windll.kernel32 if hasattr(ctypes, "windll") else None
dwmapi = ctypes.windll.dwmapi if hasattr(ctypes, "windll") else None

SW_HIDE = 0
SW_SHOWNORMAL = 1
SW_SHOWMINIMIZED = 2
SW_MAXIMIZE = 3
SW_SHOW = 5
SW_MINIMIZE = 6
SW_RESTORE = 9

DWMWA_CLOAKED = 14
DESKTOP_ACCESS = 0x01FF  # Full desktop access rights

APP_ALIASES = {
    "chrome": ["chrome.exe", "google chrome"],
    "google chrome": ["chrome.exe", "google chrome"],
    "notepad": ["notepad.exe"],
    "android studio": ["studio64.exe", "studio.exe", "android studio"],
    "android": ["studio64.exe", "studio.exe", "android studio"],
    "visual studio": ["devenv.exe", "visual studio"],
    "visual studio 2022": ["devenv.exe", "visual studio 2022", "visual studio"],
    "vs": ["devenv.exe", "visual studio"],
    "visual studio code": ["code.exe", "visual studio code"],
    "vs code": ["code.exe", "visual studio code"],
    "vscode": ["code.exe", "visual studio code"],
    "unity": ["unity.exe", "unity editor"],
    "unity editor": ["unity.exe", "unity editor"],
    "edge": ["msedge.exe", "microsoft edge"],
    "microsoft edge": ["msedge.exe", "microsoft edge"],
    "whatsapp": ["whatsapp.exe", "whatsapp.root.exe", "whatsapp"],
    "calculator": ["calculator.exe", "calc.exe", "calculator"],
    "calc": ["calculator.exe", "calc.exe", "calculator"],
    "unreal engine": ["unrealengine.exe", "unrealeditor.exe", "unreal engine", "unreal editor"],
    "unreal": ["unrealengine.exe", "unrealeditor.exe", "unreal engine", "unreal editor"],
    "ue5": ["unrealengine.exe", "unrealeditor.exe", "unreal engine", "unreal editor"],
    "ue4": ["unrealengine.exe", "unrealeditor.exe", "unreal engine", "unreal editor"],
}

SYSTEM_IGNORE_TITLES = {
    "program manager",
    "windows input experience",
    "default ime",
    "msctfime ui",
}

ERROR_WINDOW_TITLES = {
    "setup error",
    "application error",
    "fatal error",
    "crash reporter",
    "crash report",
    "unhandled exception",
}


class WindowManager:
    """
    Real interactive desktop window manager for Windows (WinSta0\\default).
    Safely enumerates, inspects, and activates desktop application windows
    without shell injection or mouse/keyboard clicks.
    """

    def __init__(self):
        self.aliases = APP_ALIASES
        self._hdesktop = None
        self._ensure_interactive_desktop()

    def _ensure_interactive_desktop(self):
        """Opens and attaches thread to interactive user desktop WinSta0\\default once."""
        if not user32:
            return None
        if not self._hdesktop:
            hdesk = user32.OpenDesktopW("default", 0, False, DESKTOP_ACCESS)
            if not hdesk:
                hdesk = user32.OpenInputDesktop(0, False, DESKTOP_ACCESS)
            if hdesk:
                user32.SetThreadDesktop(hdesk)
                self._hdesktop = hdesk
        else:
            user32.SetThreadDesktop(self._hdesktop)
        return self._hdesktop

    def _open_interactive_desktop(self):
        """Backward-compatible alias for _ensure_interactive_desktop."""
        return self._ensure_interactive_desktop()

    def _build_window_dict(self, hwnd: int) -> Optional[Dict[str, Any]]:
        """Constructs structured window metadata dictionary from HWND."""
        if not user32 or not user32.IsWindow(hwnd):
            return None

        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return None

        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        raw_title = buf.value.strip()
        # Clean zero-width spaces and non-printable characters
        clean_title = raw_title.replace("\u200b", "").strip()
        if not clean_title or clean_title.lower() in SYSTEM_IGNORE_TITLES:
            return None

        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width <= 0 or height <= 0:
            return None

        pid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

        proc_name = ""
        if psutil and pid.value > 0:
            try:
                p = psutil.Process(pid.value)
                proc_name = p.name()
            except Exception:
                pass

        cloaked = ctypes.c_int(0)
        if dwmapi:
            dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), 4)

        cls_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls_buf, 256)
        class_name = cls_buf.value.strip()

        return {
            "hwnd": int(hwnd),
            "title": clean_title,
            "pid": int(pid.value),
            "process_name": proc_name,
            "class_name": class_name,
            "visible": bool(user32.IsWindowVisible(hwnd)),
            "minimized": bool(user32.IsIconic(hwnd)),
            "maximized": bool(user32.IsZoomed(hwnd)),
            "cloaked": bool(cloaked.value),
            "left": int(rect.left),
            "top": int(rect.top),
            "width": int(width),
            "height": int(height),
        }

    def get_windows(self, include_cloaked: bool = False) -> List[Dict[str, Any]]:
        """
        Enumerate visible windows on the real interactive Windows desktop (WinSta0\\default).
        Filters out cloaked/suspended background tiles and untitled system helper windows.
        """
        if not user32:
            return []

        hdesk = self._ensure_interactive_desktop()
        windows = []
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

        def enum_cb(hwnd, lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            w = self._build_window_dict(hwnd)
            if w:
                if not include_cloaked and w["cloaked"]:
                    return True
                windows.append(w)
            return True

        cb = WNDENUMPROC(enum_cb)
        user32.EnumDesktopWindows(hdesk, cb, 0)
        return windows

    def is_error_window(self, w: Dict[str, Any]) -> bool:
        """Returns True if the window represents a crash/setup error dialog rather than an interactive application."""
        if not w:
            return False
        title = (w.get("title") or "").strip().lower()
        if not title:
            return False
        if title in ERROR_WINDOW_TITLES:
            return True
        if title.startswith("error:") or title.startswith("fatal:"):
            return True
        if title.endswith(" - error") or title.endswith(" setup error"):
            return True
        if "setup error" in title or "fatal error" in title or "crash reporter" in title:
            return True
        if w.get("class_name") == "#32770" and ("error" in title or "crash" in title):
            return True
        return False

    def find_error_window(self, search_text: str) -> Optional[Dict[str, Any]]:
        """Finds if a crash/setup error dialog is open for the queried application or process."""
        query = (search_text or "").strip().lower()
        if not query:
            return None
        windows = self.get_windows(include_cloaked=False)
        patterns = self.aliases.get(query, [query])
        for pat in patterns:
            pat_clean = pat.lower()
            pat_exe = pat_clean if pat_clean.endswith(".exe") else pat_clean + ".exe"
            for w in windows:
                if w["process_name"].lower() in (pat_clean, pat_exe):
                    if self.is_error_window(w):
                        return w
        return None

    def get_dialog_text(self, hwnd: int) -> str:
        """Extracts text content from static controls in a modal dialog box (#32770)."""
        if not user32 or not hwnd or not user32.IsWindow(hwnd):
            return ""
        texts = []
        WM_GETTEXT = 0x000D
        WM_GETTEXTLENGTH = 0x000E

        def cb(child, _):
            cls_buf = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(child, cls_buf, 128)
            cls_name = cls_buf.value.strip().lower()
            if cls_name == "static":
                l = user32.SendMessageW(child, WM_GETTEXTLENGTH, 0, 0)
                if 0 < l < 1024:
                    buf = ctypes.create_unicode_buffer(l + 2)
                    user32.SendMessageW(child, WM_GETTEXT, l + 1, buf)
                    val = buf.value.strip()
                    if val and val not in texts:
                        texts.append(val)
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
        user32.EnumChildWindows(hwnd, WNDENUMPROC(cb), 0)
        return " ".join(texts).strip()

    def find_window_deterministic(
        self, search_text: str, exclude_error_windows: bool = True
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """
        Finds open desktop windows matching the search query deterministically.
        Rejects ambiguous matches across different applications.
        Returns: (matching_windows_list, error_message_if_ambiguous)
        """
        query = (search_text or "").strip().lower()
        if not query:
            return [], "Search text is empty."

        windows = self.get_windows(include_cloaked=False)
        if exclude_error_windows:
            windows = [w for w in windows if not self.is_error_window(w)]
        if not windows:
            return [], None

        patterns = self.aliases.get(query, [query])

        # Priority 1: Exact Title Match
        for w in windows:
            if w["title"].lower() == query:
                return [w], None

        # Priority 2: Exact Process Name Match
        proc_matches = []
        for pat in patterns:
            pat_clean = pat.lower()
            pat_exe = pat_clean if pat_clean.endswith(".exe") else pat_clean + ".exe"
            for w in windows:
                if w["process_name"].lower() in (pat_clean, pat_exe):
                    if w not in proc_matches:
                        proc_matches.append(w)

        if len(proc_matches) == 1:
            return proc_matches, None
        elif len(proc_matches) > 1:
            # Check if all matches belong to the same application/process
            distinct_procs = {w["process_name"].lower() for w in proc_matches}
            if len(distinct_procs) == 1:
                non_min = [w for w in proc_matches if not w["minimized"]]
                return [non_min[0] if non_min else proc_matches[0]], None
            titles_str = ", ".join([f"'{w['title']}'" for w in proc_matches[:3]])
            return [], f"Ambiguous matches found for '{search_text}': {titles_str}. Please specify exact title."

        # Priority 3: Title Substring Match with Disambiguation
        sub_matches = []
        for pat in patterns:
            pat_clean = pat.lower().replace(".exe", "")
            for w in windows:
                w_title = w["title"].lower()
                # Prevent Visual Studio query from matching Visual Studio Code
                if "visual studio" in pat_clean and "code" not in pat_clean:
                    if "visual studio code" in w_title or "code.exe" in w["process_name"].lower():
                        continue
                if pat_clean in w_title:
                    if w not in sub_matches:
                        sub_matches.append(w)

        if len(sub_matches) == 1:
            return sub_matches, None
        elif len(sub_matches) > 1:
            distinct_procs = {w["process_name"].lower() for w in sub_matches}
            if len(distinct_procs) == 1:
                non_min = [w for w in sub_matches if not w["minimized"]]
                return [non_min[0] if non_min else sub_matches[0]], None
            titles_str = ", ".join([f"'{w['title']}'" for w in sub_matches[:3]])
            return [], f"Ambiguous matches found for '{search_text}': {titles_str}. Please specify exact title."

        return [], None

    def find_window(self, search_text: str) -> Optional[List[Dict[str, Any]]]:
        """Backward-compatible find_window returning list of matches or None."""
        matches, _ = self.find_window_deterministic(search_text)
        return matches if matches else None

    def _get_foreground_hwnd(self) -> int:
        """Reliably gets foreground HWND, using thread fallback if calling thread has ERROR_BUSY."""
        if not user32:
            return 0
        self._ensure_interactive_desktop()
        hwnd = user32.GetForegroundWindow()
        if hwnd and user32.IsWindow(hwnd):
            return hwnd

        # Fallback to fresh worker thread with no message queue/hooks
        try:
            import concurrent.futures

            def _query_fg():
                self._ensure_interactive_desktop()
                return user32.GetForegroundWindow()

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                hwnd = executor.submit(_query_fg).result(timeout=1.0)
                if hwnd and user32.IsWindow(hwnd):
                    return hwnd
        except Exception:
            pass
        return 0

    def get_active_window(self) -> Optional[Dict[str, Any]]:
        """Return information about the currently active foreground window on the desktop."""
        if not user32:
            return None
        hwnd = self._get_foreground_hwnd()
        if not hwnd or not user32.IsWindow(hwnd):
            return None
        return self._build_window_dict(hwnd)

    def activate_window(self, search_text: str) -> Tuple[bool, str]:
        """
        Finds a window deterministically and brings it safely to the foreground.
        Restores the window if minimized. Does not inject mouse or keyboard input.
        """
        search_text = (search_text or "").strip()
        if not search_text:
            return False, "No window name specified."

        matches, ambiguity_err = self.find_window_deterministic(search_text)
        if ambiguity_err:
            return False, ambiguity_err
        if not matches:
            return False, f"No open window found for '{search_text}'."

        target = matches[0]
        hwnd = target["hwnd"]

        self._ensure_interactive_desktop()
        try:
            if not user32.IsWindow(hwnd):
                return False, f"Window handle for '{target['title']}' is no longer valid."

            # 1. Restore if minimized
            if user32.IsIconic(hwnd):
                user32.ShowWindowAsync(hwnd, SW_RESTORE)
                time.sleep(0.15)
            else:
                user32.ShowWindowAsync(hwnd, SW_SHOW)

            # 2. Attach thread input to overcome foreground lock
            fg_hwnd = self._get_foreground_hwnd()
            fg_thread_id = user32.GetWindowThreadProcessId(fg_hwnd, None) if fg_hwnd else 0
            cur_thread_id = kernel32.GetCurrentThreadId() if kernel32 else 0

            attached = False
            if fg_thread_id != 0 and cur_thread_id != 0 and fg_thread_id != cur_thread_id:
                attached = bool(user32.AttachThreadInput(cur_thread_id, fg_thread_id, True))

            try:
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
            finally:
                if attached:
                    user32.AttachThreadInput(cur_thread_id, fg_thread_id, False)

            time.sleep(0.25)
            new_fg = self._get_foreground_hwnd()
            clean_title = target["title"].encode("ascii", "replace").decode("ascii")
            success = (new_fg == hwnd) or not user32.IsIconic(hwnd)
            if success:
                return True, f"Switched to '{clean_title}'."
            return True, f"Brought '{clean_title}' to foreground."
        except Exception as error:
            return False, f"Could not activate window: {error}"


# Alias for explicit naming
DesktopAwareWindowManager = WindowManager


if __name__ == "__main__":
    manager = WindowManager()

    print("========================================")
    print("    NR AI DESKTOP WINDOW MANAGER")
    print("========================================")

    print("\n[+] Real Desktop Windows:")
    windows = manager.get_windows()
    for w in windows:
        clean = w["title"].encode("ascii", "replace").decode("ascii")
        print(f"  - [{w['hwnd']}] ({w['process_name']}) '{clean}' | PID: {w['pid']} | Minimized: {w['minimized']}")

    print("\n[*] Active Window:")
    active = manager.get_active_window()
    if active:
        clean = active["title"].encode("ascii", "replace").decode("ascii")
        print(f"  - [{active['hwnd']}] ({active['process_name']}) '{clean}'")
    else:
        print("  [x] No active window detected.")