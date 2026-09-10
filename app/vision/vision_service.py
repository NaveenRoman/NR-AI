"""
NR-AI Screen Vision Service (Read-Only).

Provides safe, deterministic, read-only perception of the interactive Windows desktop
and application windows. Operates 100% locally in-memory using RapidOCR without external
APIs, network calls, mouse clicks, keyboard events, or application launches.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from app.agent.window_manager import WindowManager
from app.vision.screen import ScreenVision
from app.vision.window_vision import WindowVision

logger = logging.getLogger("NRAI.VisionService")


class ScreenVisionService:
    """
    High-level read-only vision interface for NR-AI Companion.
    """

    def __init__(
        self,
        window_manager: Optional[WindowManager] = None,
        screen_vision: Optional[ScreenVision] = None,
        window_vision: Optional[WindowVision] = None,
    ):
        self.window_manager = window_manager or WindowManager()
        self.screen_vision = screen_vision or ScreenVision(
            window_manager=self.window_manager
        )
        self.window_vision = window_vision or WindowVision(
            window_manager=self.window_manager,
            screen_vision=self.screen_vision,
        )

    def _generate_voice_summary(
        self, target_info: Dict[str, Any], items: List[Dict[str, Any]]
    ) -> str:
        """
        Generates a concise, natural-language voice summary suitable for Android TTS.
        Avoids dumping raw OCR tokens into the voice stream.
        """
        target_type = target_info.get("type", "desktop")
        title = target_info.get("title", "")
        proc = target_info.get("process_name", "")

        # Filter out very short noise or low confidence elements
        usable_texts = []
        seen = set()
        for it in items:
            t = it.get("text", "").strip()
            conf = it.get("confidence", 0.0)
            if len(t) >= 3 and conf >= 0.70 and t.lower() not in seen:
                if not all(c in "-_=+|\/.,;: " for c in t):
                    seen.add(t.lower())
                    usable_texts.append(t)

        if target_type == "window":
            app_label = proc.replace(".exe", "").capitalize() if proc else "the application"
            if "chrome" in proc.lower():
                app_label = "Chrome"
            elif "msedge" in proc.lower():
                app_label = "Edge"
            elif "notepad" in proc.lower():
                app_label = "Notepad"
            elif "whatsapp" in proc.lower():
                app_label = "WhatsApp"
            elif "code" in proc.lower():
                app_label = "VS Code"

            if not usable_texts:
                return f"I can see {app_label} titled '{title}', but no visible text was recognized inside the window."

            highlights = usable_texts[:3]
            highlights_str = ", ".join([f"'{h}'" for h in highlights])
            return f"I can see {app_label} titled '{title}'. Visible content includes {highlights_str}."

        else:
            windows = self.window_manager.get_windows(include_cloaked=False)
            app_names = list(
                dict.fromkeys(
                    [
                        w["process_name"].replace(".exe", "").capitalize()
                        for w in windows
                        if w.get("process_name")
                    ]
                )
            )[:4]
            apps_str = ", ".join(app_names) if app_names else "your desktop"

            if not usable_texts:
                return f"I can see your desktop with active windows for {apps_str}."

            highlights = usable_texts[:3]
            highlights_str = ", ".join([f"'{h}'" for h in highlights])
            return f"On your screen, I can see windows for {apps_str}. Visible text includes {highlights_str}."

    def inspect_screen(self, target: Optional[str] = None) -> Dict[str, Any]:
        """
        Inspects the screen or a specific application window in-memory using local RapidOCR.
        Strictly read-only: never clicks, presses keys, or launches processes.
        """
        target_clean = (target or "").strip()

        # Case 1: Specific application target requested (e.g. "chrome", "notepad")
        if target_clean:
            matches, ambiguity_err = self.window_manager.find_window_deterministic(
                target_clean
            )
            if ambiguity_err:
                return {
                    "success": False,
                    "target": None,
                    "items": [],
                    "summary": ambiguity_err,
                }
            if not matches:
                return {
                    "success": False,
                    "target": None,
                    "items": [],
                    "summary": f"No open window found for '{target_clean}'. You can say 'open {target_clean}' to launch it.",
                }

            matched_win = matches[0]
            win_read = self.window_vision.read_window(matched_win["title"])
            if not win_read or not win_read.get("items"):
                items = []
            else:
                items = win_read["items"]

            target_info = {
                "type": "window",
                "title": matched_win["title"],
                "process_name": matched_win["process_name"],
                "hwnd": matched_win["hwnd"],
                "pid": matched_win["pid"],
            }

            summary = self._generate_voice_summary(target_info, items)
            return {
                "success": True,
                "target": target_info,
                "items": items,
                "summary": summary,
            }

        # Case 2: No specific target -> check active foreground window first
        active = self.window_manager.get_active_window()
        if (
            active
            and active.get("title")
            and active.get("process_name", "").lower()
            not in ("explorer.exe", "shellexperiencehost.exe")
        ):
            win_read = self.window_vision.read_window(active["title"])
            if win_read and win_read.get("items"):
                target_info = {
                    "type": "window",
                    "title": active["title"],
                    "process_name": active["process_name"],
                    "hwnd": active["hwnd"],
                    "pid": active["pid"],
                }
                summary = self._generate_voice_summary(target_info, win_read["items"])
                return {
                    "success": True,
                    "target": target_info,
                    "items": win_read["items"],
                    "summary": summary,
                }

        # Case 3: Fallback to full interactive desktop
        desktop_items = self.screen_vision.read_screen()
        processed_items = []
        for it in desktop_items:
            box = it["box"]
            x_coords = [p[0] for p in box]
            y_coords = [p[1] for p in box]
            center = (
                int((min(x_coords) + max(x_coords)) / 2),
                int((min(y_coords) + max(y_coords)) / 2),
            )
            processed_items.append({
                "text": it["text"],
                "confidence": it["confidence"],
                "box": box,
                "center": center,
            })

        target_info = {
            "type": "desktop",
            "title": "Interactive Desktop",
            "process_name": "explorer.exe",
            "hwnd": 0,
            "pid": 0,
        }
        summary = self._generate_voice_summary(target_info, processed_items)
        return {
            "success": True,
            "target": target_info,
            "items": processed_items,
            "summary": summary,
        }

    def find_text(
        self, query: str, target: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Scans screen or target window for specific text query (read-only).
        Never clicks or moves mouse.
        """
        query_clean = (query or "").strip()
        if not query_clean:
            return {
                "success": False,
                "found": False,
                "query": "",
                "target": None,
                "match": None,
                "summary": "No text search query provided.",
            }

        inspection = self.inspect_screen(target=target)
        if not inspection.get("success"):
            return {
                "success": False,
                "found": False,
                "query": query_clean,
                "target": None,
                "match": None,
                "summary": inspection.get("summary", "Could not inspect screen."),
            }

        items = inspection.get("items", [])

        def _normalize_token(s: str) -> str:
            return s.lower().replace("l", "i").replace("1", "i").replace("-", "").replace(" ", "").replace("_", "")

        def _search_items(items_list):
            q_norm = _normalize_token(query_clean)
            q_low = query_clean.lower()
            exact = []
            partial = []
            fuzzy = []
            for it in items_list:
                t_raw = it.get("text", "")
                t_low = t_raw.lower()
                t_norm = _normalize_token(t_raw)
                if t_low == q_low or t_norm == q_norm:
                    exact.append(it)
                elif q_low in t_low:
                    partial.append(it)
                elif q_norm and q_norm in t_norm:
                    fuzzy.append(it)
            if exact:
                return max(exact, key=lambda x: x.get("confidence", 0.0))
            if partial:
                return max(partial, key=lambda x: x.get("confidence", 0.0))
            if fuzzy:
                return max(fuzzy, key=lambda x: x.get("confidence", 0.0))
            return None

        best_match = _search_items(items)

        # If not found in active window and no explicit target was given, try full desktop
        if not best_match and target is None and inspection.get("target", {}).get("type") == "window":
            desktop_inspection = self.inspect_screen(target="")
            # Force desktop capture
            desktop_items = self.screen_vision.read_screen()
            processed = []
            for it in desktop_items:
                box = it["box"]
                xc = [p[0] for p in box]
                yc = [p[1] for p in box]
                processed.append({
                    "text": it["text"],
                    "confidence": it["confidence"],
                    "box": box,
                    "center": (int((min(xc) + max(xc)) / 2), int((min(yc) + max(yc)) / 2)),
                })
            match_desk = _search_items(processed)
            if match_desk:
                best_match = match_desk
                inspection = {
                    "success": True,
                    "target": {
                        "type": "desktop",
                        "title": "Interactive Desktop",
                        "process_name": "explorer.exe",
                    },
                    "items": processed,
                }

        target_info = inspection.get("target", {})
        win_label = (
            target_info.get("title", "")
            if target_info.get("type") == "window"
            else "your screen"
        )

        if best_match:
            center = best_match.get("center")
            conf_pct = int(best_match.get("confidence", 0.0) * 100)
            matched_text = best_match.get("text", "")
            summary = (
                f"I found '{matched_text}' on {win_label} at coordinates {center} "
                f"with {conf_pct}% confidence."
            )
            return {
                "success": True,
                "found": True,
                "query": query_clean,
                "target": target_info,
                "match": best_match,
                "summary": summary,
            }
        else:
            return {
                "success": True,
                "found": False,
                "query": query_clean,
                "target": target_info,
                "match": None,
                "summary": f"I could not find '{query_clean}' on {win_label}.",
            }


DesktopVisionManager = ScreenVisionService
