import pyautogui


class UIRegions:
    """
    Defines logical regions of a desktop application window.

    These are approximate regions used by NR AI's first
    computer-vision layer. Later, AI vision can replace
    these estimates with actual detected UI boundaries.
    """

    def __init__(self, window):
        self.window = window

        self.left = max(0, window["left"])
        self.top = max(0, window["top"])
        self.width = window["width"]
        self.height = window["height"]

    def region(self, name):
        """
        Return a screen-coordinate rectangle:

        {
            left,
            top,
            width,
            height
        }
        """

        # Approximate VS Code layout.
        #
        # These percentages are intentionally conservative.
        # They are NOT permanent UI coordinates.

        if name == "menu":
            return self._make_region(
                0.00,
                0.00,
                1.00,
                0.065
            )

        if name == "activity_bar":
            return self._make_region(
                0.00,
                0.065,
                0.035,
                0.90
            )

        if name == "explorer":
            return self._make_region(
                0.035,
                0.065,
                0.22,
                0.90
            )

        if name == "editor":
            return self._make_region(
                0.255,
                0.065,
                0.745,
                0.60
            )

        if name == "terminal":
            return self._make_region(
                0.255,
                0.665,
                0.745,
                0.265
            )

        if name == "status_bar":
            return self._make_region(
                0.00,
                0.93,
                1.00,
                0.07
            )

        if name == "main_content":
            return self._make_region(
                0.035,
                0.065,
                0.965,
                0.865
            )

        return None

    def _make_region(
        self,
        relative_left,
        relative_top,
        relative_width,
        relative_height
    ):
        left = int(
            self.left +
            self.width * relative_left
        )

        top = int(
            self.top +
            self.height * relative_top
        )

        width = int(
            self.width * relative_width
        )

        height = int(
            self.height * relative_height
        )

        # Keep the rectangle inside the screen.

        screen_width, screen_height = pyautogui.size()

        left = max(
            0,
            min(left, screen_width - 1)
        )

        top = max(
            0,
            min(top, screen_height - 1)
        )

        width = max(
            1,
            min(
                width,
                screen_width - left
            )
        )

        height = max(
            1,
            min(
                height,
                screen_height - top
            )
        )

        return {
            "left": left,
            "top": top,
            "width": width,
            "height": height
        }

    def all_regions(self):
        names = [
            "menu",
            "activity_bar",
            "explorer",
            "editor",
            "terminal",
            "status_bar",
            "main_content"
        ]

        return {
            name: self.region(name)
            for name in names
        }


if __name__ == "__main__":

    from app.agent.window_manager import WindowManager

    print("========================================")
    print("          NR AI UI REGIONS")
    print("========================================")

    manager = WindowManager()

    window = manager.get_active_window()

    if not window:

        print("❌ Could not determine active window.")

    else:

        print(
            f"🪟 Active window: "
            f"{window['title']}"
        )

        print(
            f"📐 Window: "
            f"{window['width']}x{window['height']}"
        )

        regions = UIRegions(window)

        print("\n📦 Detected logical regions:")

        for name, rectangle in regions.all_regions().items():

            print(
                f"\n  {name}:"
            )

            print(
                f"    left   = {rectangle['left']}"
            )

            print(
                f"    top    = {rectangle['top']}"
            )

            print(
                f"    width  = {rectangle['width']}"
            )

            print(
                f"    height = {rectangle['height']}"
            )

    print("\n🟢 Region model test completed.")