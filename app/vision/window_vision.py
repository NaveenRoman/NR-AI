import os
import time

import pyautogui

from app.agent.window_manager import WindowManager
from app.vision.screen import ScreenVision


class WindowVision:
    """
    Vision engine restricted to a specific application window.

    It:
    1. Finds the target window.
    2. Activates it.
    3. Captures only its visible region.
    4. Runs OCR on that region.
    5. Converts coordinates back to screen coordinates.
    """

    def __init__(self):
        self.window_manager = WindowManager()
        self.screen_vision = ScreenVision()

        self.output_directory = "data/screenshots/window"

        os.makedirs(
            self.output_directory,
            exist_ok=True
        )

    # ---------------------------------------------------------
    # Find target window
    # ---------------------------------------------------------

    def find_window(self, application_name):
        return self.window_manager.find_window(
            application_name
        )

    # ---------------------------------------------------------
    # Activate target window
    # ---------------------------------------------------------

    def activate_window(self, application_name):
        matches = self.find_window(
            application_name
        )

        if not matches:
            return False, None

        # Prefer the currently active matching window.
        active = self.window_manager.get_active_window()

        selected = None

        if active:
            for match in matches:
                if match["title"] == active["title"]:
                    selected = match
                    break

        # Otherwise use the first visible, non-minimized window.
        if selected is None:

            for match in matches:
                if (
                    match["visible"]
                    and not match["minimized"]
                ):
                    selected = match
                    break

        if selected is None:
            selected = matches[0]

        success, message = (
            self.window_manager.activate_window(
                selected["title"]
            )
        )

        if not success:
            return False, None

        time.sleep(0.5)

        return True, selected

    # ---------------------------------------------------------
    # Capture window
    # ---------------------------------------------------------

    def capture_window(
        self,
        application_name,
        filename="window.png"
    ):
        success, window = self.activate_window(
            application_name
        )

        if not success:
            return None

        left = window["left"]
        top = window["top"]
        width = window["width"]
        height = window["height"]

        # Windows can report tiny negative offsets when a
        # window is maximized/border-adjusted.
        screenshot_left = max(0, left)
        screenshot_top = max(0, top)

        # If the reported window extends outside the screen,
        # clip it to the actual screen dimensions.
        screen_width, screen_height = pyautogui.size()

        screenshot_width = min(
            width,
            screen_width - screenshot_left
        )

        screenshot_height = min(
            height,
            screen_height - screenshot_top
        )

        if screenshot_width <= 0 or screenshot_height <= 0:
            return None

        screenshot = pyautogui.screenshot(
            region=(
                screenshot_left,
                screenshot_top,
                screenshot_width,
                screenshot_height
            )
        )

        path = os.path.join(
            self.output_directory,
            filename
        )

        screenshot.save(path)

        return {
            "path": path,
            "window": window,
            "region": {
                "left": screenshot_left,
                "top": screenshot_top,
                "width": screenshot_width,
                "height": screenshot_height
            }
        }

    # ---------------------------------------------------------
    # OCR window
    # ---------------------------------------------------------

    def read_window(
        self,
        application_name,
        filename="window_ocr.png"
    ):
        capture = self.capture_window(
            application_name,
            filename
        )

        if not capture:
            return None

        # Use ScreenVision's OCR engine on the cropped image.
        detected = self.screen_vision.read_screen(
            capture["path"]
        )

        region = capture["region"]

        converted = []

        for item in detected:

            original_box = item["box"]

            # Convert coordinates from window-local
            # coordinates back to screen coordinates.
            screen_box = []

            for point in original_box:

                screen_box.append([
                    point[0] + region["left"],
                    point[1] + region["top"]
                ])

            local_x = [
                point[0] for point in original_box
            ]

            local_y = [
                point[1] for point in original_box
            ]

            local_center_x = (
                min(local_x) + max(local_x)
            ) / 2

            local_center_y = (
                min(local_y) + max(local_y)
            ) / 2

            screen_center = (
                int(
                    local_center_x
                    + region["left"]
                ),
                int(
                    local_center_y
                    + region["top"]
                )
            )

            converted.append({
                "text": item["text"],
                "confidence": item["confidence"],
                "box": screen_box,
                "center": screen_center
            })

        return {
            "window": capture["window"],
            "region": region,
            "items": converted
        }

    # ---------------------------------------------------------
    # Find text inside window
    # ---------------------------------------------------------

    def find_text(
        self,
        application_name,
        target
    ):
        result = self.read_window(
            application_name,
            "window_find.png"
        )

        if not result:
            return None

        target = target.lower().strip()

        matches = []

        for item in result["items"]:

            if target in item["text"].lower():

                matches.append(item)

        if not matches:
            return None

        # Highest-confidence match.
        return max(
            matches,
            key=lambda item: item["confidence"]
        )


# -------------------------------------------------------------
# Standalone test
# -------------------------------------------------------------

if __name__ == "__main__":

    vision = WindowVision()

    print("========================================")
    print("        NR AI WINDOW VISION")
    print("========================================")

    application = input(
        "Application: "
    ).strip()

    target = input(
        "Text to find: "
    ).strip()

    print(
        f"\n🪟 Target window: {application}"
    )

    print(
        "📸 Capturing only that window..."
    )

    result = vision.find_text(
        application,
        target
    )

    if result:

        print("\n✅ Target found inside window.")

        print(
            f"Text: {result['text']}"
        )

        print(
            f"Confidence: "
            f"{result['confidence']:.2f}"
        )

        print(
            f"Screen coordinates: "
            f"{result['center']}"
        )

        print(
            f"Bounding box: "
            f"{result['box']}"
        )

    else:

        print(
            f"\n❌ Could not find "
            f"'{target}' inside "
            f"'{application}'."
        )

    print("\n🟢 Window vision test completed.")