import time

from app.vision.locator import VisualLocator


class ActionVerifier:
    """
    Verifies whether a visual action produced an observable change.
    """

    def __init__(self):
        self.locator = VisualLocator()

    def wait(self, seconds=1):
        time.sleep(seconds)

    def target_exists(self, text):
        """
        Check whether text is currently visible on the screen.
        """

        result = self.locator.find(text)

        return result is not None

    def wait_for_disappearance(
        self,
        text,
        timeout=3,
        interval=0.5
    ):
        """
        Wait for a target to disappear from the screen.
        """

        start = time.time()

        while time.time() - start < timeout:

            if not self.target_exists(text):
                return True

            time.sleep(interval)

        return False

    def wait_for_appearance(
        self,
        text,
        timeout=5,
        interval=0.5
    ):
        """
        Wait for a target to appear on the screen.
        """

        start = time.time()

        while time.time() - start < timeout:

            if self.target_exists(text):
                return True

            time.sleep(interval)

        return False

    def describe(self, text):
        """
        Return the current state of a visual target.
        """

        result = self.locator.find(text)

        if not result:
            return {
                "exists": False,
                "text": text
            }

        return {
            "exists": True,
            "text": result["text"],
            "confidence": result["confidence"],
            "center": result["center"],
            "box": result["box"]
        }


if __name__ == "__main__":

    verifier = ActionVerifier()

    print("========================================")
    print("        NR AI ACTION VERIFIER")
    print("========================================")

    target = input(
        "Visible text to inspect: "
    ).strip()

    print("\n🔎 Inspecting screen...")

    result = verifier.describe(target)

    if result["exists"]:

        print("\n✅ Target is visible.")

        print(
            f"Text: {result['text']}"
        )

        print(
            f"Confidence: "
            f"{result['confidence']:.2f}"
        )

        print(
            f"Coordinates: "
            f"{result['center']}"
        )

    else:

        print(
            f"\n❌ '{target}' "
            "is not visible."
        )

    print("\n🟢 Verification engine ready.")