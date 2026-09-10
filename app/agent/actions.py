import time

try:
    import pyautogui
except ImportError:
    pyautogui = None


class ComputerActions:
    """
    Low-level computer interaction layer for NR AI.

    This module performs only explicit computer actions.
    """

    def move_mouse(self, x, y, duration=0.3):
        pyautogui.moveTo(x, y, duration=duration)

    def click(self, x=None, y=None):
        if x is not None and y is not None:
            pyautogui.click(x, y)
        else:
            pyautogui.click()

    def double_click(self, x=None, y=None):
        if x is not None and y is not None:
            pyautogui.doubleClick(x, y)
        else:
            pyautogui.doubleClick()

    def right_click(self, x=None, y=None):
        if x is not None and y is not None:
            pyautogui.rightClick(x, y)
        else:
            pyautogui.rightClick()

    def type_text(self, text, interval=0.02):
        pyautogui.write(text, interval=interval)

    def press_key(self, key):
        pyautogui.press(key)

    def hotkey(self, *keys):
        pyautogui.hotkey(*keys)

    def copy(self):
        pyautogui.hotkey("ctrl", "c")

    def paste(self):
        pyautogui.hotkey("ctrl", "v")

    def screenshot(self, path="data/screenshot.png"):
        image = pyautogui.screenshot()
        image.save(path)

        return path

    def wait(self, seconds=1):
        time.sleep(seconds)