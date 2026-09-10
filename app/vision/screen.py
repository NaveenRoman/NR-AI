import concurrent.futures
import os
from typing import Any, Dict, List, Optional, Tuple, Union

from PIL import Image, ImageGrab
import numpy as np

try:
    import pyautogui
except ImportError:
    pyautogui = None

try:
    from rapidocr_onnxruntime import RapidOCR
except ImportError:
    RapidOCR = None

from app.agent.window_manager import WindowManager


class ScreenVision:
    """
    NR AI screen vision engine.

    Capabilities:
    - Interactive desktop-aware screen resolution
    - In-memory and file-based screenshot capture
    - Local RapidOCR offline text detection
    """

    def __init__(self, window_manager: Optional[WindowManager] = None):
        self.screenshot_directory = "data/screenshots"
        os.makedirs(self.screenshot_directory, exist_ok=True)
        self.window_manager = window_manager or WindowManager()
        self._ocr = None
        self._ensure_desktop()

    def _ensure_desktop(self):
        """Ensure thread is attached to the interactive desktop."""
        if hasattr(self.window_manager, "_ensure_interactive_desktop"):
            self.window_manager._ensure_interactive_desktop()

    @property
    def ocr(self):
        if self._ocr is None and RapidOCR is not None:
            self._ocr = RapidOCR()
        return self._ocr

    def get_screen_size(self) -> Dict[str, int]:
        """Returns the primary screen dimensions."""
        self._ensure_desktop()
        if pyautogui:
            try:
                width, height = pyautogui.size()
                return {"width": int(width), "height": int(height)}
            except Exception:
                pass
        try:
            import ctypes
            u32 = ctypes.windll.user32
            return {
                "width": int(u32.GetSystemMetrics(0)),
                "height": int(u32.GetSystemMetrics(1)),
            }
        except Exception:
            return {"width": 1920, "height": 1080}

    def grab_image(
        self, region: Optional[Tuple[int, int, int, int]] = None
    ) -> Image.Image:
        """
        Reliably captures desktop or specified region in memory as PIL.Image.
        Region format: (left, top, width, height).
        Uses worker thread fallback if calling thread is locked with ERROR_BUSY.
        """
        self._ensure_desktop()

        def _do_grab():
            self._ensure_desktop()
            if region:
                left, top, width, height = region
                bbox = (max(0, left), max(0, top), max(0, left + width), max(0, top + height))
                return ImageGrab.grab(bbox=bbox)
            return ImageGrab.grab()

        try:
            return _do_grab()
        except Exception:
            # Fallback to fresh worker thread with clean desktop handle
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                return executor.submit(_do_grab).result(timeout=2.5)

    def capture(self, filename: str = "screen.png") -> str:
        """Captures the screen to a file on disk (backward compatibility)."""
        path = os.path.join(self.screenshot_directory, filename)
        img = self.grab_image()
        img.save(path)
        return path

    def read_screen(
        self,
        image_or_path: Optional[Union[str, Image.Image, np.ndarray]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Runs OCR on an image. If image_or_path is None, captures the current desktop
        in memory without writing files to disk.
        """
        if self.ocr is None:
            return []

        # Prepare input for RapidOCR
        if image_or_path is None:
            pil_img = self.grab_image()
            img_input = np.array(pil_img)
        elif isinstance(image_or_path, Image.Image):
            img_input = np.array(image_or_path)
        elif isinstance(image_or_path, np.ndarray):
            img_input = image_or_path
        elif isinstance(image_or_path, str):
            if not os.path.exists(image_or_path):
                return []
            img_input = image_or_path
        else:
            return []

        try:
            result, _ = self.ocr(img_input)
        except Exception:
            return []

        detected_text = []
        if result:
            for item in result:
                box = item[0]
                text = item[1]
                confidence = float(item[2])
                detected_text.append({
                    "text": str(text),
                    "confidence": confidence,
                    "box": box,
                })

        return detected_text

    def find_text(self, target: str) -> List[Dict[str, Any]]:
        """Find visible text on the current screen (in-memory)."""
        target = (target or "").lower().strip()
        if not target:
            return []

        results = self.read_screen()
        matches = []
        for item in results:
            if target in item["text"].lower():
                matches.append(item)
        return matches


if __name__ == "__main__":

    vision = ScreenVision()

    print("========================================")
    print("        NR AI VISUAL OCR TEST")
    print("========================================")

    size = vision.get_screen_size()

    print(
        f"🖥️ Screen: "
        f"{size['width']} x {size['height']}"
    )

    print("\n📸 Capturing screen...")

    screenshot = vision.capture(
        "nr_ai_ocr_test.png"
    )

    print(f"✅ Screenshot: {screenshot}")

    print("\n👁️ Reading screen...")

    detected = vision.read_screen(
        screenshot
    )

    print(f"\n🔎 Detected {len(detected)} text elements.")

    for item in detected:
        print(
            f"  • {item['text']} "
            f"(confidence: {item['confidence']:.2f})"
        )

    print("\n🟢 OCR test completed.")