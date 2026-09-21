import os

try:
    import pyautogui
except ImportError:
    pyautogui = None


from app.agent.window_manager import WindowManager
from app.vision.screen import ScreenVision
from app.vision.regions import UIRegions


class RegionOCR:
    """
    Runs OCR only inside a selected logical UI region.

    Flow:

    Application
        ↓
    Window
        ↓
    UI Region
        ↓
    Screenshot
        ↓
    OCR
        ↓
    Text + screen coordinates
    """

    def __init__(self):
        self.window_manager = WindowManager()
        self.screen_vision = ScreenVision()

        self.output_directory = "data/screenshots/regions"

        os.makedirs(
            self.output_directory,
            exist_ok=True
        )

    def get_target_window(self, application):
        """
        Find the best matching application window.
        """

        matches = self.window_manager.find_window(
            application
        )

        if not matches:
            return None

        active = self.window_manager.get_active_window()

        if active:

            for match in matches:

                if match["title"] == active["title"]:
                    return match

        for match in matches:

            if (
                match["visible"]
                and not match["minimized"]
            ):
                return match

        return matches[0]

    def activate_window(self, window):
        """
        Activate the selected window.
        """

        success, message = (
            self.window_manager.activate_window(
                window["title"]
            )
        )

        return success

    def capture_region(
        self,
        application,
        region_name
    ):
        """
        Capture only the requested logical region.
        """

        window = self.get_target_window(
            application
        )

        if not window:
            return None

        if not self.activate_window(window):
            return None

        import time

        time.sleep(0.5)

        regions = UIRegions(window)

        region = regions.region(
            region_name
        )

        if not region:
            return None

        screenshot = pyautogui.screenshot(
            region=(
                region["left"],
                region["top"],
                region["width"],
                region["height"]
            )
        )

        filename = (
            f"{application.replace(' ', '_')}_"
            f"{region_name}.png"
        )

        path = os.path.join(
            self.output_directory,
            filename
        )

        screenshot.save(path)

        return {
            "path": path,
            "window": window,
            "region": region
        }

    def read_region(
        self,
        application,
        region_name
    ):
        """
        Capture a region and run OCR on it.
        """

        capture = self.capture_region(
            application,
            region_name
        )

        if not capture:
            return None

        detected = self.screen_vision.read_screen(
            capture["path"]
        )

        region = capture["region"]

        results = []

        for item in detected:

            box = item["box"]

            screen_box = []

            for point in box:

                screen_box.append([
                    point[0] + region["left"],
                    point[1] + region["top"]
                ])

            local_x = [
                point[0]
                for point in box
            ]

            local_y = [
                point[1]
                for point in box
            ]

            center_x = int(
                (
                    min(local_x)
                    + max(local_x)
                ) / 2
                + region["left"]
            )

            center_y = int(
                (
                    min(local_y)
                    + max(local_y)
                ) / 2
                + region["top"]
            )

            results.append({
                "text": item["text"],
                "confidence": item["confidence"],
                "box": screen_box,
                "center": (
                    center_x,
                    center_y
                )
            })

        return {
            "application": application,
            "region_name": region_name,
            "region": region,
            "items": results
        }

    def find_text(
        self,
        application,
        region_name,
        target
    ):
        """
        Find text only inside the requested region.
        """

        result = self.read_region(
            application,
            region_name
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

        return max(
            matches,
            key=lambda item: item["confidence"]
        )


if __name__ == "__main__":

    ocr = RegionOCR()

    print("========================================")
    print("        NR AI REGION OCR")
    print("========================================")

    application = input(
        "Application: "
    ).strip()

    region = input(
        "Region "
        "(menu/editor/terminal/explorer): "
    ).strip()

    target = input(
        "Text to find: "
    ).strip()

    print(
        f"\n🪟 Application: {application}"
    )

    print(
        f"📦 Region: {region}"
    )

    print(
        f"🔎 Target: {target}"
    )

    print("\n👁️ Running region OCR...")

    result = ocr.find_text(
        application,
        region,
        target
    )

    if result:

        print(
            "\n✅ TARGET FOUND"
        )

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
            "\n❌ Target not found "
            "inside the selected region."
        )

    print(
        "\n🟢 Region OCR test completed."
    )