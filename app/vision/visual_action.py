import pyautogui

from app.vision.locator import VisualLocator


class VisualAction:
    """
    Connects NR AI vision with the computer-control layer.

    The system locates visible text first, verifies confidence,
    and only then performs the requested mouse action.
    """

    def __init__(self, minimum_confidence=0.85):
        self.locator = VisualLocator()
        self.minimum_confidence = minimum_confidence

    def locate(self, target):
        result = self.locator.find(target)

        if not result:
            return None

        if result["confidence"] < self.minimum_confidence:
            print(
                f"⚠️ Confidence too low: "
                f"{result['confidence']:.2f}"
            )
            return None

        return result

    def click_text(self, target):
        result = self.locate(target)

        if not result:
            return (
                False,
                f"I couldn't safely locate '{target}'."
            )

        x, y = result["center"]

        print(
            f"🎯 Target: {result['text']}"
        )

        print(
            f"📊 Confidence: "
            f"{result['confidence']:.2f}"
        )

        print(
            f"📍 Coordinates: ({x}, {y})"
        )

        # Move first so the action is observable.
        pyautogui.moveTo(
            x,
            y,
            duration=0.3
        )

        # Click the center of the detected text.
        pyautogui.click(x, y)

        return (
            True,
            f"Clicked '{result['text']}'."
        )


if __name__ == "__main__":

    print("========================================")
    print("        NR AI VISUAL ACTION")
    print("========================================")

    target = input(
        "Enter visible text to click: "
    ).strip()

    visual_action = VisualAction()

    print(
        f"\n🔎 Looking for: {target}"
    )

    print(
        "⚠️ The mouse will move and click "
        "the detected target."
    )

    confirmation = input(
        "Type YES to continue: "
    ).strip()

    if confirmation != "YES":
        print("🛑 Action cancelled.")
    else:
        success, message = visual_action.click_text(
            target
        )

        if success:
            print(f"✅ {message}")
        else:
            print(f"❌ {message}")