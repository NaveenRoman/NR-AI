import time
import pyautogui

from app.vision.menu_popup import MenuPopupVision
from app.agent.state_verifier import StateVerifier


class VerifiedAction:
    """
    Complete verified computer action.

    Flow:

        SEE
          ↓
        Locate menu
          ↓
        Open menu
          ↓
        Locate popup target
          ↓
        ACT
          ↓
        Click popup target
          ↓
        WAIT
          ↓
        SEE AGAIN
          ↓
        VERIFY final state
    """

    def __init__(self):
        self.menu_popup = MenuPopupVision(
            minimum_confidence=0.85
        )

        self.state_verifier = StateVerifier(
            minimum_confidence=0.85
        )

    def execute_menu_action(
        self,
        action_target,
        popup_target,
        final_expected
    ):
        print("========================================")
        print("        NR AI VERIFIED ACTION")
        print("========================================")

        print(
            f"\n🪟 Menu: {action_target}"
        )

        print(
            f"🎯 Action: {popup_target}"
        )

        print(
            f"✅ Final expected state: {final_expected}"
        )

        print("\n----------------------------------------")
        print("🔎 SEE → ACT → WAIT → SEE → VERIFY")
        print("----------------------------------------")

        # ---------------------------------
        # STEP 1 — SEE + LOCATE
        # ---------------------------------

        print(
            f"\n👁️ Locating '{action_target}'..."
        )

        result = self.menu_popup.open_and_find(
            action_target,
            popup_target
        )

        if not result:

            print("\n========================================")
            print("🔴 ACTION FAILED")
            print("========================================")

            return False

        # ---------------------------------
        # STEP 2 — TARGET FOUND
        # ---------------------------------

        print("\n----------------------------------------")
        print("🎯 TARGET LOCATED")
        print("----------------------------------------")

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

        x, y = result["center"]

        # ---------------------------------
        # STEP 3 — CONTROLLED ACTION
        # ---------------------------------

        print("\n⚠️ CONTROLLED COMPUTER ACTION")

        print(
            f'NR AI wants to click "{result["text"]}"'
        )

        confirmation = input(
            "Type YES to continue: "
        ).strip()

        if confirmation.upper() != "YES":

            print(
                "\n🛑 Action cancelled."
            )

            return False

        # ---------------------------------
        # STEP 4 — CLICK
        # ---------------------------------

        print(
            "\n🖱️ Moving to target..."
        )

        pyautogui.moveTo(
            x,
            y,
            duration=0.3
        )

        time.sleep(0.3)

        print(
            "🖱️ Clicking target..."
        )

        pyautogui.click(
            x,
            y
        )

        # ---------------------------------
        # STEP 5 — WAIT
        # ---------------------------------

        print(
            "\n⏳ Waiting for UI response..."
        )

        time.sleep(1)

        # ---------------------------------
        # STEP 6 — VERIFY FINAL STATE
        # ---------------------------------

        print(
            "\n👁️ Verifying final state..."
        )

        verification = self.state_verifier.verify(
            final_expected,
            wait_time=0
        )

        if not verification["success"]:

            print("\n========================================")
            print("🔴 ACTION NOT VERIFIED")
            print("========================================")

            print(
                f"Action: {popup_target}"
            )

            print(
                f"Expected final state: "
                f"{final_expected}"
            )

            print(
                f"Reason: "
                f"{verification['message']}"
            )

            return False

        # ---------------------------------
        # SUCCESS
        # ---------------------------------

        print("\n========================================")
        print("🟢 ACTION VERIFIED")
        print("========================================")

        print(
            f"Menu: {action_target}"
        )

        print(
            f"Clicked: {popup_target}"
        )

        print(
            f"Final state: {final_expected}"
        )

        print(
            f"Confidence: "
            f"{verification['confidence']:.2f}"
        )

        print(
            "\n🎉 SEE → ACT → WAIT → SEE → VERIFY completed."
        )

        return True


if __name__ == "__main__":

    print("========================================")
    print("        NR AI VERIFIED ACTION")
    print("========================================")

    menu = input(
        "Menu to open: "
    ).strip()

    action = input(
        "Popup target to click: "
    ).strip()

    final_state = input(
        "Expected final visible text: "
    ).strip()

    verifier = VerifiedAction()

    verifier.execute_menu_action(
        menu,
        action,
        final_state
    )