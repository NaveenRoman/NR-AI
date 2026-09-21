import os
import time

try:
    import pyautogui
except ImportError:
    pyautogui = None


from app.vision.screen import ScreenVision
from app.vision.text_segmenter import TextSegmenter


class MenuPopupVision:
    """
    Open a menu item and then inspect the popup.

    Flow:

        Find File
          ↓
        Click File
          ↓
        Wait
          ↓
        Capture screen
          ↓
        OCR
          ↓
        Segment
          ↓
        Find exact Save
    """

    def __init__(self, minimum_confidence=0.85):
        self.screen_vision = ScreenVision()

        self.segmenter = TextSegmenter(
            minimum_confidence=minimum_confidence
        )

        self.minimum_confidence = minimum_confidence

        self.output_directory = (
            "data/screenshots/menu_popup"
        )

        os.makedirs(
            self.output_directory,
            exist_ok=True
        )

    def find_menu_item(self, text):
        """
        Find an exact menu-bar item on the screen.
        """

        screenshot = pyautogui.screenshot()

        path = os.path.join(
            self.output_directory,
            "menu_bar.png"
        )

        screenshot.save(path)

        detected = self.screen_vision.read_screen(
            path
        )

        if not detected:
            return None

        segments = (
            self.segmenter.segment_results(
                detected
            )
        )

        target = text.strip().lower()

        for item in segments:

            if (
                item["text"].strip().lower()
                == target
                and
                item["confidence"]
                >= self.minimum_confidence
            ):

                return item

        return None

    def open_menu(self, menu_name):
        """
        Locate and click a menu-bar item.
        """

        print(
            f"\n🔎 Looking for menu: {menu_name}"
        )

        target = self.find_menu_item(
            menu_name
        )

        if not target:

            print(
                f"❌ Menu '{menu_name}' not found."
            )

            return False

        print(
            f"✅ Found '{target['text']}'"
        )

        print(
            f"📊 Confidence: "
            f"{target['confidence']:.2f}"
        )

        print(
            f"📍 Coordinates: "
            f"{target['center']}"
        )

        x, y = target["center"]

        print(
            "\n🖱️ Moving to menu..."
        )

        pyautogui.moveTo(
            x,
            y,
            duration=0.2
        )

        print(
            "🖱️ Clicking menu..."
        )

        pyautogui.click()

        print(
            "⏳ Waiting for popup..."
        )

        time.sleep(0.7)

        return True

    def capture_popup(self):
        """
        Capture the area below the VS Code menu bar.

        We capture a large enough area to accommodate
        the dropdown while avoiding the bottom terminal.
        """

        screen_width, screen_height = (
            pyautogui.size()
        )

        left = 0
        top = 35

        width = min(
            900,
            screen_width
        )

        height = min(
            650,
            screen_height - top
        )

        screenshot = pyautogui.screenshot(
            region=(
                left,
                top,
                width,
                height
            )
        )

        path = os.path.join(
            self.output_directory,
            "popup_after_click.png"
        )

        screenshot.save(path)

        return {
            "path": path,
            "region": {
                "left": left,
                "top": top,
                "width": width,
                "height": height
            }
        }

    def read_popup(self):
        """
        OCR the screen after the menu has opened.
        """

        capture = self.capture_popup()

        print(
            f"\n📸 Popup screenshot:"
            f" {capture['path']}"
        )

        detected = self.screen_vision.read_screen(
            capture["path"]
        )

        if not detected:
            return []

        region = capture["region"]

        converted = []

        for item in detected:

            box = item["box"]

            screen_box = []

            for point in box:

                screen_box.append([
                    point[0] + region["left"],
                    point[1] + region["top"]
                ])

            x_values = [
                point[0]
                for point in box
            ]

            y_values = [
                point[1]
                for point in box
            ]

            center = (
                int(
                    (
                        min(x_values)
                        + max(x_values)
                    ) / 2
                    + region["left"]
                ),
                int(
                    (
                        min(y_values)
                        + max(y_values)
                    ) / 2
                    + region["top"]
                )
            )

            converted.append({
                "text": item["text"],
                "confidence": item["confidence"],
                "box": screen_box,
                "center": center
            })

        return converted

    def find_exact_popup_item(self, target):
        """
        Find an exact text match in the popup.
        """

        items = self.read_popup()

        segments = (
            self.segmenter.segment_results(
                items
            )
        )

        target = target.strip().lower()

        print(
            f"\n🔎 Searching popup for exact:"
            f" '{target}'"
        )

        for item in segments:

            text = item["text"].strip()

            print(
                f"  • {text}"
                f" | confidence="
                f"{item['confidence']:.2f}"
                f" | center="
                f"{item['center']}"
            )

            if (
                text.lower() == target
                and
                item["confidence"]
                >= self.minimum_confidence
            ):

                return item

        return None

    def find_in_open_popup(self, target):
        """
        Find an exact text match inside an already-open popup.

        IMPORTANT:
        This method does NOT click/open the menu.
        It only captures and searches the current popup.
        """

        print(
            "\n🔎 Searching already-open popup..."
        )

        print(
            f"🎯 Popup target: {target}"
        )

        item = self.find_exact_popup_item(
            target
        )

        if not item:

            print(
                "\n========================================"
            )

            print(
                "❌ POPUP TARGET NOT FOUND"
            )

            print(
                "========================================"
            )

            return None

        print(
            "\n========================================"
        )

        print(
            "✅ POPUP TARGET FOUND"
        )

        print(
            "========================================"
        )

        print(
            f"Text: {item['text']}"
        )

        print(
            f"Confidence: "
            f"{item['confidence']:.2f}"
        )

        print(
            f"Screen coordinates: "
            f"{item['center']}"
        )

        print(
            f"Bounding box: "
            f"{item['box']}"
        )

        return item

    def open_and_find(
        self,
        menu_name,
        popup_target
    ):
        """
        Complete menu-popup operation.
        """

        print(
            "\n========================================"
        )

        print(
            "       NR AI MENU POPUP VISION"
        )

        print(
            "========================================"
        )

        print(
            f"\nMenu: {menu_name}"
        )

        print(
            f"Popup target: {popup_target}"
        )

        opened = self.open_menu(
            menu_name
        )

        if not opened:
            return None

        target = self.find_exact_popup_item(
            popup_target
        )

        if not target:

            print(
                "\n========================================"
            )

            print(
                "❌ POPUP TARGET NOT FOUND"
            )

            print(
                "========================================"
            )

            return None

        print(
            "\n========================================"
        )

        print(
            "✅ POPUP TARGET FOUND"
        )

        print(
            "========================================"
        )

        print(
            f"Text: {target['text']}"
        )

        print(
            f"Confidence: "
            f"{target['confidence']:.2f}"
        )

        print(
            f"Screen coordinates: "
            f"{target['center']}"
        )

        print(
            f"Bounding box: "
            f"{target['box']}"
        )

        return target


if __name__ == "__main__":

    vision = MenuPopupVision()

    menu_name = input(
        "Menu to open: "
    ).strip()

    popup_target = input(
        "Popup text to find: "
    ).strip()

    result = vision.open_and_find(
        menu_name,
        popup_target
    )

    if result:

        print(
            "\n🟢 Menu popup vision test completed."
        )

    else:

        print(
            "\n🔴 Menu popup vision test failed."
        )