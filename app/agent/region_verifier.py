import time

from app.vision.region_ocr import RegionOCR
from app.vision.text_segmenter import TextSegmenter


class RegionVerifier:
    """
    Verifies an expected UI state inside a specific
    application region.

    Example:

        Click File
             ↓
        Observe VS Code menu
             ↓
        Find exact "Save"
             ↓
        Verified
    """

    def __init__(self, minimum_confidence=0.85):
        self.minimum_confidence = minimum_confidence

        self.ocr = RegionOCR()
        self.segmenter = TextSegmenter(
            minimum_confidence=minimum_confidence
        )

    def inspect(
        self,
        application,
        region
    ):
        """
        Read one application region.
        """

        result = self.ocr.read_region(
            application,
            region
        )

        if not result:
            return None

        segments = (
            self.segmenter.segment_results(
                result["items"]
            )
        )

        return {
            "application": application,
            "region": region,
            "segments": segments
        }

    def verify_exact(
        self,
        application,
        region,
        expected_text,
        timeout=5,
        interval=0.5
    ):
        """
        Wait for an exact expected text match
        inside the specified region.
        """

        expected = expected_text.strip().lower()

        start = time.time()

        while time.time() - start < timeout:

            result = self.inspect(
                application,
                region
            )

            if result:

                for item in result["segments"]:

                    text = item["text"].strip()

                    if (
                        text.lower() == expected
                        and
                        item["confidence"]
                        >= self.minimum_confidence
                    ):

                        return {
                            "verified": True,
                            "text": item["text"],
                            "confidence": item[
                                "confidence"
                            ],
                            "center": item["center"],
                            "box": item["box"]
                        }

            time.sleep(interval)

        return {
            "verified": False,
            "expected": expected_text,
            "reason": (
                "Exact expected text was not "
                "found inside the selected region."
            )
        }


if __name__ == "__main__":

    print("========================================")
    print("        NR AI REGION VERIFIER")
    print("========================================")

    verifier = RegionVerifier()

    application = input(
        "Application: "
    ).strip()

    region = input(
        "Region "
        "(menu/editor/terminal/explorer): "
    ).strip()

    expected = input(
        "Expected exact text: "
    ).strip()

    print(
        f"\n🪟 Application: {application}"
    )

    print(
        f"📦 Region: {region}"
    )

    print(
        f"🎯 Expected: {expected}"
    )

    print(
        "\n👁️ Inspecting selected region..."
    )

    result = verifier.verify_exact(
        application,
        region,
        expected,
        timeout=5,
        interval=0.5
    )

    if result["verified"]:

        print(
            "\n========================================"
        )

        print(
            "🟢 EXACT STATE VERIFIED"
        )

        print(
            "========================================"
        )

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
            "\n========================================"
        )

        print(
            "🔴 STATE NOT VERIFIED"
        )

        print(
            "========================================"
        )

        print(
            f"Expected: "
            f"{result['expected']}"
        )

        print(
            f"Reason: "
            f"{result['reason']}"
        )

    print(
        "\n🟢 Region verifier test completed."
    )