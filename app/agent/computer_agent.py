from app.agent.target_resolver import TargetResolver
from app.agent.controller import AgentController
from app.vision.locator import VisualLocator


class ComputerAgent:
    """
    NR AI computer-use agent.

    Resolves a target application/window, locates visible
    UI text, and can perform a controlled click.
    """

    def __init__(self):
        self.resolver = TargetResolver()
        self.controller = AgentController()
        self.locator = VisualLocator()

    def inspect_application(self, application):
        target = self.resolver.resolve_best_window(
            application
        )

        if not target:
            return {
                "success": False,
                "message": f"{application} window not found."
            }

        return {
            "success": True,
            "application": application,
            "window": target
        }

    def locate_text(self, application, text):
        target = self.resolver.resolve_best_window(
            application
        )

        if not target:
            return {
                "success": False,
                "message": f"{application} window not found."
            }

        # Bring the target window to the foreground.
        success, message = self.resolver.window_manager.activate_window(
            target["title"]
        )

        if not success:
            return {
                "success": False,
                "message": message
            }

        # Give Windows a moment to switch the active window.
        self.controller.wait(0.5)

        result = self.locator.find(text)

        if not result:
            return {
                "success": False,
                "message": (
                    f"'{text}' was not found "
                    f"after activating {application}."
                )
            }

        return {
            "success": True,
            "application": application,
            "window": target,
            "target": result
        }

    def click_text(self, application, text):
        result = self.locate_text(
            application,
            text
        )

        if not result["success"]:
            return result

        target = result["target"]

        x, y = target["center"]

        self.controller.move_mouse(
            x,
            y
        )

        self.controller.click(
            x,
            y
        )

        return {
            "success": True,
            "message": (
                f"Clicked '{target['text']}' "
                f"in {application}."
            ),
            "coordinates": (x, y),
            "confidence": target["confidence"],
            "window": result["window"]["title"]
        }


if __name__ == "__main__":

    print("========================================")
    print("        NR AI COMPUTER AGENT")
    print("========================================")

    agent = ComputerAgent()

    application = input(
        "Application: "
    ).strip()

    text = input(
        "Visible text to find: "
    ).strip()

    print(
        f"\n🔎 Targeting {application}..."
    )

    result = agent.locate_text(
        application,
        text
    )

    if not result["success"]:

        print(
            f"❌ {result['message']}"
        )

    else:

        target = result["target"]

        print("\n✅ Target located.")

        print(
            f"Application: {result['application']}"
        )

        print(
            f"Window: "
            f"{result['window']['title']}"
        )

        print(
            f"Text: {target['text']}"
        )

        print(
            f"Confidence: "
            f"{target['confidence']:.2f}"
        )

        print(
            f"Coordinates: "
            f"{target['center']}"
        )

        print("\n🟢 Locate test completed.")