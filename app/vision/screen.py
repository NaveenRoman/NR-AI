import os

import pyautogui
from rapidocr_onnxruntime import RapidOCR


class ScreenVision:
    """
    NR AI screen vision engine.

    Current capabilities:
    - Screen resolution
    - Screenshot capture
    - OCR text detection
    """

    def __init__(self):
        self.screenshot_directory = "data/screenshots"

        os.makedirs(
            self.screenshot_directory,
            exist_ok=True
        )

        self._ocr = None

    @property
    def ocr(self):
        if self._ocr is None:
            self._ocr = RapidOCR()
        return self._ocr

    def get_screen_size(self):
        width, height = pyautogui.size()

        return {
            "width": width,
            "height": height
        }

    def capture(self, filename="screen.png"):
        path = os.path.join(
            self.screenshot_directory,
            filename
        )

        screenshot = pyautogui.screenshot()
        screenshot.save(path)

        return path

    def read_screen(self, image_path=None):
        """
        Capture the current screen if an image path isn't provided,
        then run OCR on it.
        """

        if image_path is None:
            image_path = self.capture(
                "nr_ai_ocr_screen.png"
            )

        result, _ = self.ocr(image_path)

        detected_text = []

        if result:
            for item in result:
                box = item[0]
                text = item[1]
                confidence = item[2]

                detected_text.append({
                    "text": text,
                    "confidence": float(confidence),
                    "box": box
                })

        return detected_text

    def find_text(self, target):
        """
        Find visible text on the current screen.
        """

        target = target.lower().strip()

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