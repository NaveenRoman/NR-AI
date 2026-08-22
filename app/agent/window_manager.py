import pygetwindow as gw


class WindowManager:
    """
    Provides NR AI with awareness of open Windows applications.
    """

    def get_windows(self):
        """
        Return visible windows with useful information.
        """

        windows = []

        for window in gw.getAllWindows():

            title = window.title.strip()

            if not title:
                continue

            try:
                windows.append({
                    "title": title,
                    "left": window.left,
                    "top": window.top,
                    "width": window.width,
                    "height": window.height,
                    "visible": window.visible,
                    "minimized": window.isMinimized,
                    "maximized": window.isMaximized,
                })

            except Exception:
                continue

        return windows

    def find_window(self, search_text):
        """
        Find windows whose title contains the requested text.
        """

        search_text = search_text.lower().strip()

        if not search_text:
            return None

        windows = self.get_windows()

        matches = []

        for window in windows:

            title = window["title"].lower()

            if search_text in title:
                matches.append(window)

        if not matches:
            return None

        return matches

    def get_active_window(self):
        """
        Return information about the currently active window.
        """

        try:
            window = gw.getActiveWindow()

            if window is None:
                return None

            return {
                "title": window.title,
                "left": window.left,
                "top": window.top,
                "width": window.width,
                "height": window.height,
                "visible": window.visible,
                "minimized": window.isMinimized,
                "maximized": window.isMaximized,
            }

        except Exception:
            return None

    def activate_window(self, search_text):
        """
        Find a window and bring the best match to the foreground.
        """

        matches = self.find_window(search_text)

        if not matches:
            return False, f"Window '{search_text}' not found."

        window_info = matches[0]

        try:
            # Find the actual window again so we can activate it.
            for window in gw.getAllWindows():

                if window.title == window_info["title"]:

                    if window.isMinimized:
                        window.restore()

                    window.activate()

                    return (
                        True,
                        f"Activated '{window.title}'."
                    )

        except Exception as error:
            return (
                False,
                f"Could not activate window: {error}"
            )

        return False, f"Could not activate '{search_text}'."


if __name__ == "__main__":

    manager = WindowManager()

    print("========================================")
    print("        NR AI WINDOW MANAGER")
    print("========================================")

    print("\n🪟 Open windows:")

    windows = manager.get_windows()

    for window in windows:

        print(
            f"  • {window['title']}"
            f" | Position: "
            f"({window['left']}, {window['top']})"
            f" | Size: "
            f"{window['width']}x{window['height']}"
        )

    print("\n🎯 Active window:")

    active = manager.get_active_window()

    if active:
        print(
            f"  • {active['title']}"
            f" | Position: "
            f"({active['left']}, {active['top']})"
        )
    else:
        print("  ❌ Could not determine active window.")

    print("\n🔎 Testing VS Code search:")

    matches = manager.find_window("Visual Studio Code")

    if matches:

        for match in matches:
            print(
                f"  ✅ {match['title']}"
            )

    else:

        print("  ❌ VS Code window not found.")

    print("\n🟢 Window manager test completed.")