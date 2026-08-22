from app.vision.screen import ScreenVision


class VisualLocator:
    """
    Finds visible text on the screen and converts
    OCR bounding boxes into clickable coordinates.
    """

    def __init__(self):
        self.vision = ScreenVision()

    def find(self, text):
        matches = self.vision.find_text(text)

        if not matches:
            return None

        best_match = max(
            matches,
            key=lambda item: item["confidence"]
        )

        box = best_match["box"]

        # OCR returns four corner points:
        #
        # [top-left, top-right, bottom-right, bottom-left]
        #
        # Convert them into a center coordinate.

        x_coordinates = [
            point[0] for point in box
        ]

        y_coordinates = [
            point[1] for point in box
        ]

        center_x = int(
            (min(x_coordinates) + max(x_coordinates)) / 2
        )

        center_y = int(
            (min(y_coordinates) + max(y_coordinates)) / 2
        )

        return {
            "text": best_match["text"],
            "confidence": best_match["confidence"],
            "box": box,
            "center": (center_x, center_y)
        }


if __name__ == "__main__":

    locator = VisualLocator()

    print("========================================")
    print("        NR AI VISUAL LOCATOR")
    print("========================================")

    target = input("Enter visible text to find: ")

    result = locator.find(target)

    if result:

        print("\n✅ Found!")

        print(
            f"Text: {result['text']}"
        )

        print(
            f"Confidence: "
            f"{result['confidence']:.2f}"
        )

        print(
            f"Center: "
            f"{result['center']}"
        )

        print(
            f"Box: "
            f"{result['box']}"
        )

    else:

        print(
            f"\n❌ Could not find "
            f"'{target}' on the screen."
        )