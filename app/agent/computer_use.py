from app.agent.computer_agent import ComputerAgent
from app.agent.action_verifier import ActionVerifier


class ComputerUseAgent:
    """
    NR AI's first complete computer-use loop.

    Observe → Locate → Act → Verify
    """

    def __init__(self):
        self.agent = ComputerAgent()
        self.verifier = ActionVerifier()

    def click_and_verify(self, application, target):
        print(f"\n🔎 Application: {application}")
        print(f"🎯 Target: {target}")

        # -------------------------------------------------
        # 1. LOCATE
        # -------------------------------------------------

        print("\n👁️ Locating target...")

        located = self.agent.locate_text(
            application,
            target
        )

        if not located["success"]:
            return {
                "success": False,
                "stage": "locate",
                "message": located["message"]
            }

        target_info = located["target"]

        confidence = target_info["confidence"]

        print(
            f"✅ Found '{target_info['text']}'"
        )

        print(
            f"📊 Confidence: {confidence:.2f}"
        )

        print(
            f"📍 Coordinates: "
            f"{target_info['center']}"
        )

        # -------------------------------------------------
        # 2. SAFETY CHECK
        # -------------------------------------------------

        if confidence < 0.85:
            return {
                "success": False,
                "stage": "confidence",
                "message": (
                    f"Confidence too low: "
                    f"{confidence:.2f}"
                )
            }

        # -------------------------------------------------
        # 3. ACT
        # -------------------------------------------------

        x, y = target_info["center"]

        print("\n🖱️ Moving to target...")

        self.agent.controller.move_mouse(
            x,
            y
        )

        self.agent.controller.wait(0.3)

        print("🖱️ Clicking target...")

        self.agent.controller.click(
            x,
            y
        )

        # -------------------------------------------------
        # 4. WAIT
        # -------------------------------------------------

        print("\n⏳ Waiting for UI response...")

        self.verifier.wait(1)

        # -------------------------------------------------
        # 5. VERIFY
        # -------------------------------------------------

        print("👁️ Verifying screen...")

        still_visible = self.verifier.target_exists(
            target
        )

        if still_visible:
            return {
                "success": True,
                "verified": False,
                "message": (
                    f"'{target}' was clicked, "
                    "but it is still visible."
                ),
                "coordinates": (x, y)
            }

        return {
            "success": True,
            "verified": True,
            "message": (
                f"'{target}' disappeared after "
                "the click."
            ),
            "coordinates": (x, y)
        }


if __name__ == "__main__":

    print("========================================")
    print("       NR AI COMPUTER-USE AGENT")
    print("========================================")

    agent = ComputerUseAgent()

    application = input(
        "Application: "
    ).strip()

    target = input(
        "Target to click: "
    ).strip()

    print("\n⚠️ CONTROLLED ACTION")
    print(
        "NR AI will locate and click "
        "the specified target."
    )

    confirmation = input(
        "Type YES to continue: "
    ).strip()

    if confirmation.upper() != "YES":
        print("🛑 Action cancelled.")
        raise SystemExit

    result = agent.click_and_verify(
        application,
        target
    )

    print("\n========================================")

    if result["success"]:

        print("🟢 ACTION COMPLETED")

    else:

        print("🔴 ACTION FAILED")

    print(
        f"Message: {result['message']}"
    )

    if "verified" in result:
        print(
            f"Verified: {result['verified']}"
        )

    print("========================================")