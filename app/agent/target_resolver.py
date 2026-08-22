from app.agent.window_manager import WindowManager


class TargetResolver:
    """
    Resolves which application/window NR AI should operate on.
    """

    def __init__(self):
        self.window_manager = WindowManager()

    def resolve_application(self, application_name):
        """
        Find windows belonging to an application.
        """

        matches = self.window_manager.find_window(
            application_name
        )

        if not matches:
            return []

        return matches

    def resolve_best_window(self, application_name):
        """
        Choose the most appropriate visible window.

        Preference:
        1. Active window
        2. Visible, non-minimized window
        """

        matches = self.resolve_application(
            application_name
        )

        if not matches:
            return None

        active = self.window_manager.get_active_window()

        if active:
            active_title = active["title"]

            for match in matches:
                if match["title"] == active_title:
                    return match

        for match in matches:
            if (
                match["visible"]
                and not match["minimized"]
            ):
                return match

        return matches[0]

    def describe_target(self, application_name):
        """
        Return a human-readable description of
        the selected target window.
        """

        target = self.resolve_best_window(
            application_name
        )

        if not target:
            return (
                f"No window found for "
                f"{application_name}."
            )

        return (
            f"Target window: {target['title']} | "
            f"Position: "
            f"({target['left']}, {target['top']}) | "
            f"Size: "
            f"{target['width']}x{target['height']}"
        )


if __name__ == "__main__":

    resolver = TargetResolver()

    print("========================================")
    print("        NR AI TARGET RESOLVER")
    print("========================================")

    application = input(
        "Application to target: "
    ).strip()

    print("\n🔎 Resolving target...")

    result = resolver.resolve_best_window(
        application
    )

    if result:

        print("\n✅ Target resolved.")

        print(
            f"Application: {application}"
        )

        print(
            f"Window: {result['title']}"
        )

        print(
            f"Position: "
            f"({result['left']}, {result['top']})"
        )

        print(
            f"Size: "
            f"{result['width']}x{result['height']}"
        )

    else:

        print(
            f"\n❌ Could not resolve "
            f"{application}."
        )