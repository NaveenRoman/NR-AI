import time

from app.vision.screen import ScreenVision


class StateVerifier:
    """
    Verifies whether expected text is visible
    after a computer action.

    Flow:

        Action
          ↓
        Wait
          ↓
        Screenshot
          ↓
        OCR
          ↓
        Exact text match
          ↓
        PASS / FAIL
    """

    def __init__(self, minimum_confidence=0.85):
        self.screen_vision = ScreenVision()
        self.minimum_confidence = minimum_confidence

    def verify(
        self,
        expected_text,
        wait_time=1.0
    ):
        print(
            f"\n⏳ Waiting {wait_time:.1f}s "
            "before verification..."
        )

        time.sleep(wait_time)

        print(
            "\n👁️ Capturing screen for verification..."
        )

        detected = self.screen_vision.read_screen()

        if not detected:

            print(
                "❌ No OCR results."
            )

            return {
                "success": False,
                "expected": expected_text,
                "message": "No text detected."
            }

        target = expected_text.strip().lower()

        for item in detected:

            text = item["text"].strip()

            confidence = item["confidence"]

            if (
                text.lower() == target
                and
                confidence >= self.minimum_confidence
            ):

                print(
                    "\n========================================"
                )

                print(
                    "🟢 EXPECTED STATE FOUND"
                )

                print(
                    "========================================"
                )

                print(
                    f"Text: {text}"
                )

                print(
                    f"Confidence: {confidence:.2f}"
                )

                box = item["box"]

                x_values = [point[0] for point in box]
                y_values = [point[1] for point in box]

                center = (
                int((min(x_values) + max(x_values)) / 2),
                int((min(y_values) + max(y_values)) / 2)
                )

                print(
                    f"Coordinates: {center}"
                )

                return {
                    "success": True,
                    "expected": expected_text,
                    "text": text,
                    "confidence": confidence,
                    "coordinates": center,
                    "message": "Expected state verified."
                }

        print(
            "\n========================================"
        )

        print(
            "🔴 EXPECTED STATE NOT FOUND"
        )

        print(
            "========================================"
        )

        print(
            f"Expected: {expected_text}"
        )

        return {
            "success": False,
            "expected": expected_text,
            "message": (
                f"Exact text '{expected_text}' "
                "was not found."
            )
        }


if __name__ == "__main__":

    print("========================================")
    print("        NR AI STATE VERIFIER")
    print("========================================")

    expected = input(
        "Expected visible text: "
    ).strip()

    verifier = StateVerifier()

    result = verifier.verify(
        expected
    )

    print(
        "\n========================================"
    )

    if result["success"]:

        print(
            "🟢 STATE VERIFICATION PASSED"
        )

    else:

        print(
            "🔴 STATE VERIFICATION FAILED"
        )

    print(
        "========================================"
    )