from app.agent.actions import ComputerActions


class AgentController:
    """
    High-level controller for NR AI computer actions.
    """

    def __init__(self):
        self.actions = ComputerActions()

    def move_mouse(self, x, y):
        self.actions.move_mouse(x, y)
        return f"Mouse moved to {x}, {y}."

    def click(self, x=None, y=None):
        self.actions.click(x, y)
        return "Mouse clicked."

    def double_click(self, x=None, y=None):
        self.actions.double_click(x, y)
        return "Mouse double-clicked."

    def right_click(self, x=None, y=None):
        self.actions.right_click(x, y)
        return "Right mouse button clicked."

    def type_text(self, text):
        self.actions.type_text(text)
        return "Text entered."

    def press_key(self, key):
        self.actions.press_key(key)
        return f"Pressed {key}."

    def hotkey(self, *keys):
        self.actions.hotkey(*keys)
        return f"Pressed {' + '.join(keys)}."

    def copy(self):
        self.actions.copy()
        return "Copied."

    def paste(self):
        self.actions.paste()
        return "Pasted."

    def screenshot(self, path="data/screenshot.png"):
        result = self.actions.screenshot(path)
        return f"Screenshot saved to {result}."

    def wait(self, seconds=1):
        self.actions.wait(seconds)
        return f"Waited {seconds} seconds."

if __name__ == "__main__":

    import os

    controller = AgentController()

    print("========================================")
    print("       NR AI MOUSE CONTROL TEST")
    print("========================================")

    print("\n🚀 Opening Notepad...")

    os.startfile("notepad.exe")

    controller.wait(2)

    print("⌨️ Typing test message...")

    controller.type_text(
        "NR AI mouse control test."
    )

    controller.press_key("enter")

    controller.type_text(
        "The next action will move the mouse."
    )

    controller.wait(2)

    print("🖱️ Moving mouse to a safe location...")

    controller.move_mouse(500, 300)

    controller.wait(1)

    print("🖱️ Performing click...")

    controller.click()

    controller.wait(1)

    print("📸 Taking screenshot...")

    screenshot_path = controller.screenshot(
        "data/nr_ai_mouse_test.png"
    )

    print(f"✅ Screenshot saved: {screenshot_path}")

    print("\n🟢 Mouse control test completed.")