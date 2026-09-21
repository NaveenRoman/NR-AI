import time
try:
    import pyautogui
except ImportError:
    pyautogui = None


from app.agent.visual_agent import VisualAgent


class VisualAction:
    """
    Safe visual action layer.

    Flow:

    Application
        ↓
    Region
        ↓
    Target
        ↓
    Locate
        ↓
    Confirmation
        ↓
    Mouse action
    """

    def __init__(self):
        self.agent = VisualAgent()

    def locate(
        self,
        application,
        region,
        target
    ):
        return self.agent.locate(
            application,
            region,
            target
        )

    def click(
        self,
        application,
        region,
        target,
        require_confirmation=True
    ):
        print("\n🔎 Locating target...")

        result = self.locate(
            application,
            region,
            target
        )

        if not result["success"]:

            print("\n❌ Target could not be located.")

            return {
                "success": False,
                "stage": "locate",
                "message": result["message"]
            }

        target_info = result["target"]

        x, y = target_info["center"]

        print("\n🎯 Target ready.")

        print(
            f"Text: {target_info['text']}"
        )

        print(
            f"Confidence: "
            f"{target_info['confidence']:.2f}"
        )

        print(
            f"Coordinates: ({x}, {y})"
        )

        # -------------------------------------------------
        # Safety confirmation
        # -------------------------------------------------

        if require_confirmation:

            print("\n⚠️ CONTROLLED COMPUTER ACTION")

            print(
                f'NR AI wants to click "{target_info["text"]}"'
            )

            confirmation = input(
                "Type YES to continue: "
            ).strip()

            if confirmation.upper() != "YES":

                print("🛑 Action cancelled.")

                return {
                    "success": False,
                    "stage": "confirmation",
                    "message": "User cancelled action."
                }

        # -------------------------------------------------
        # Move
        # -------------------------------------------------

        print("\n🖱️ Moving mouse...")

        pyautogui.moveTo(
            x,
            y,
            duration=0.3
        )

        time.sleep(0.3)

        # -------------------------------------------------
        # Click
        # -------------------------------------------------

        print("🖱️ Clicking...")

        pyautogui.click(
            x,
            y
        )

        # -------------------------------------------------
        # Wait
        # -------------------------------------------------

        print(
            "\n⏳ Waiting for UI response..."
        )

        time.sleep(1)

        return {
            "success": True,
            "stage": "click",
            "application": application,
            "region": region,
            "target": target_info,
            "coordinates": (x, y),
            "message": (
                f'Clicked "{target_info["text"]}".'
            )
        }


if __name__ == "__main__":

    print("========================================")
    print("        NR AI VISUAL ACTION")
    print("========================================")

    visual_action = VisualAction()

    application = input(
        "Application: "
    ).strip()

    region = input(
        "Region "
        "(menu/editor/terminal/explorer): "
    ).strip()

    target = input(
        "Target to click: "
    ).strip()

    result = visual_action.click(
        application,
        region,
        target
    )

    print("\n========================================")

    if result["success"]:

        print("🟢 VISUAL ACTION COMPLETED")

        print(
            f"Application: "
            f"{result['application']}"
        )

        print(
            f"Region: "
            f"{result['region']}"
        )

        print(
            f"Target: "
            f"{result['target']['text']}"
        )

        print(
            f"Coordinates: "
            f"{result['coordinates']}"
        )

    else:

        print("🔴 VISUAL ACTION FAILED")

        print(
            f"Stage: "
            f"{result['stage']}"
        )

        print(
            f"Message: "
            f"{result['message']}"
        )

    print("========================================")