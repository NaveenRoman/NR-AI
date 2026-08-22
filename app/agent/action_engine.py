import time
import pyautogui


class ActionEngine:
    """
    Generic computer action layer.

    Supported actions:

        click
        double_click
        move
        type
        press
        hotkey
        scroll
        wait

    The planner decides WHAT to do.
    This engine decides HOW to physically perform it.
    """

    def __init__(self, move_duration=0.25):
        self.move_duration = move_duration

    # --------------------------------------------------
    # Mouse
    # --------------------------------------------------

    def move(self, x, y):
        print(f"🖱️ Moving to ({x}, {y})")

        pyautogui.moveTo(
            x,
            y,
            duration=self.move_duration
        )

    def click(self, x, y):
        print(f"🖱️ Clicking ({x}, {y})")

        self.move(x, y)
        pyautogui.click()

    def double_click(self, x, y):
        print(f"🖱️ Double-clicking ({x}, {y})")

        self.move(x, y)
        pyautogui.doubleClick()

    # --------------------------------------------------
    # Keyboard
    # --------------------------------------------------

    def type_text(self, text, interval=0.01):
        print(f"⌨️ Typing: {text}")

        pyautogui.write(
            text,
            interval=interval
        )

    def press(self, key):
        print(f"⌨️ Pressing: {key}")

        pyautogui.press(key)

    def hotkey(self, *keys):
        print(
            f"⌨️ Hotkey: {' + '.join(keys)}"
        )

        pyautogui.hotkey(*keys)

    # --------------------------------------------------
    # Scroll
    # --------------------------------------------------

    def scroll(self, amount):
        print(f"🖱️ Scrolling: {amount}")

        pyautogui.scroll(amount)

    # --------------------------------------------------
    # Wait
    # --------------------------------------------------

    def wait(self, seconds=1.0):
        print(
            f"⏳ Waiting {seconds:.2f}s"
        )

        time.sleep(seconds)

    # --------------------------------------------------
    # Generic dispatcher
    # --------------------------------------------------

    def execute(self, action):
        """
        Execute one structured action.

        Example:

        {
            "type": "click",
            "x": 500,
            "y": 300
        }
        """

        if not isinstance(action, dict):
            return {
                "success": False,
                "message": "Action must be a dictionary."
            }

        action_type = action.get("type")

        try:

            if action_type == "click":

                self.click(
                    action["x"],
                    action["y"]
                )

            elif action_type == "double_click":

                self.double_click(
                    action["x"],
                    action["y"]
                )

            elif action_type == "move":

                self.move(
                    action["x"],
                    action["y"]
                )

            elif action_type == "type":

                self.type_text(
                    action["text"]
                )

            elif action_type == "press":

                self.press(
                    action["key"]
                )

            elif action_type == "hotkey":

                self.hotkey(
                    *action["keys"]
                )

            elif action_type == "scroll":

                self.scroll(
                    action["amount"]
                )

            elif action_type == "wait":

                self.wait(
                    action.get(
                        "seconds",
                        1.0
                    )
                )

            else:

                return {
                    "success": False,
                    "message":
                        f"Unsupported action: {action_type}"
                }

            return {
                "success": True,
                "action": action
            }

        except Exception as error:

            return {
                "success": False,
                "action": action,
                "message": str(error)
            }


if __name__ == "__main__":

    print("========================================")
    print("        NR AI ACTION ENGINE")
    print("========================================")

    engine = ActionEngine()

    print("\n🧪 Testing safe WAIT action...")

    result = engine.execute({
        "type": "wait",
        "seconds": 0.5
    })

    print("\n========================================")

    if result["success"]:
        print("🟢 ACTION ENGINE TEST PASSED")
    else:
        print("🔴 ACTION ENGINE TEST FAILED")
        print(
            f"Message: {result['message']}"
        )

    print("========================================")