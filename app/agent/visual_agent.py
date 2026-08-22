import time

from app.agent.target_resolver import TargetResolver
from app.agent.action_verifier import ActionVerifier
from app.vision.region_ocr import RegionOCR
from app.vision.text_segmenter import TextSegmenter


class VisualAgent:
    """
    Region-aware computer-use agent.

    Flow:

        Application
            ↓
        Target Window
            ↓
        UI Region
            ↓
        Region OCR
            ↓
        Text Segmentation
            ↓
        Exact Target
            ↓
        Optional Mouse Action
            ↓
        Expected-State Verification
    """

    def __init__(self, minimum_confidence=0.85):

        self.minimum_confidence = (
            minimum_confidence
        )

        self.resolver = TargetResolver()
        self.region_ocr = RegionOCR()
        self.segmenter = TextSegmenter()
        self.verifier = ActionVerifier()

    # ---------------------------------------------------------
    # Locate
    # ---------------------------------------------------------

    def locate(
        self,
        application,
        region,
        target
    ):
        """
        Locate a target inside a specific application region.
        """

        print(
            f"\n🪟 Application: {application}"
        )

        print(
            f"📦 Region: {region}"
        )

        print(
            f"🎯 Target: {target}"
        )

        # -----------------------------------------------------
        # Resolve application window
        # -----------------------------------------------------

        window = (
            self.resolver.resolve_best_window(
                application
            )
        )

        if not window:

            return {
                "success": False,
                "stage": "window",
                "message": (
                    f"Could not find "
                    f"{application}."
                )
            }

        print(
            f"\n✅ Window resolved:"
            f"\n   {window['title']}"
        )

        # -----------------------------------------------------
        # Region OCR
        # -----------------------------------------------------

        print(
            "\n👁️ Reading selected region..."
        )

        result = self.region_ocr.read_region(
            application,
            region
        )

        if not result:

            return {
                "success": False,
                "stage": "ocr",
                "message": (
                    "Could not capture "
                    "the selected region."
                )
            }

        # -----------------------------------------------------
        # Segmentation
        # -----------------------------------------------------

        print(
            f"🔎 OCR detected "
            f"{len(result['items'])} items."
        )

        segments = (
            self.segmenter.segment_results(
                result["items"]
            )
        )

        print(
            f"🧩 Segmented into "
            f"{len(segments)} targets."
        )

        # -----------------------------------------------------
        # Find exact target
        # -----------------------------------------------------

        target_lower = target.lower().strip()

        exact_matches = []

        partial_matches = []

        for item in segments:

            text = item["text"].strip()

            if item["confidence"] < (
                self.minimum_confidence
            ):
                continue

            if text.lower() == target_lower:

                exact_matches.append(item)

            elif target_lower in text.lower():

                partial_matches.append(item)

        selected = None

        if exact_matches:

            selected = max(
                exact_matches,
                key=lambda item: item["confidence"]
            )

        elif partial_matches:

            selected = max(
                partial_matches,
                key=lambda item: item["confidence"]
            )

        if not selected:

            return {
                "success": False,
                "stage": "target",
                "message": (
                    f"Could not find "
                    f"'{target}' inside "
                    f"the {region} region."
                )
            }

        print(
            "\n🎯 Target located:"
        )

        print(
            f"   Text: {selected['text']}"
        )

        print(
            f"   Confidence: "
            f"{selected['confidence']:.2f}"
        )

        print(
            f"   Coordinates: "
            f"{selected['center']}"
        )

        return {
            "success": True,
            "application": application,
            "window": window,
            "region": region,
            "target": selected
        }

    # ---------------------------------------------------------
    # Click
    # ---------------------------------------------------------

    def click(
        self,
        application,
        region,
        target
    ):
        """
        Locate and click a target.
        """

        result = self.locate(
            application,
            region,
            target
        )

        if not result["success"]:
            return result

        target_info = result["target"]

        x, y = target_info["center"]

        print(
            "\n🖱️ Moving to target..."
        )

        time.sleep(0.3)

        self.region_ocr.screen_vision.capture(
            "visual_agent_before_click.png"
        )

        # Use the existing computer controller through
        # the resolver's window manager and pyautogui layer.
        import pyautogui

        pyautogui.moveTo(
            x,
            y,
            duration=0.3
        )

        print(
            "🖱️ Clicking..."
        )

        pyautogui.click(
            x,
            y
        )

        return {
            "success": True,
            "action": "click",
            "target": target_info,
            "coordinates": (x, y),
            "window": result["window"]["title"]
        }


if __name__ == "__main__":

    print("========================================")
    print("        NR AI VISUAL AGENT")
    print("========================================")

    agent = VisualAgent()

    application = input(
        "Application: "
    ).strip()

    region = input(
        "Region "
        "(menu/editor/terminal/explorer): "
    ).strip()

    target = input(
        "Target: "
    ).strip()

    print("\n----------------------------------------")
    print("🔎 LOCATE-ONLY TEST")
    print("----------------------------------------")

    result = agent.locate(
        application,
        region,
        target
    )

    if result["success"]:

        print(
            "\n🟢 VISUAL TARGET LOCATED"
        )

        print(
            f"Window: "
            f"{result['window']['title']}"
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
            f"{result['target']['center']}"
        )

    else:

        print(
            "\n🔴 LOCATE FAILED"
        )

        print(
            f"Stage: "
            f"{result['stage']}"
        )

        print(
            f"Message: "
            f"{result['message']}"
        )

    print(
        "\n🟢 Visual agent locate test completed."
    )