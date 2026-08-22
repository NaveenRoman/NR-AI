import argparse
import sys

from app.brain.brain import NRBrain
from app.config.voice_config import VoiceConfig
from app.voice.listener import VoiceListener
from app.voice.speaker import VoiceSpeaker

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def safe_print(msg: str) -> None:
    try:
        print(msg)
    except Exception:
        try:
            print(msg.encode("ascii", "replace").decode("ascii"))
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(description="NR AI Personal Assistant")
    parser.add_argument(
        "--cli",
        "--text",
        action="store_true",
        help="Run in interactive typed CLI mode (no microphone required)",
    )
    parser.add_argument(
        "--command",
        type=str,
        default=None,
        help="Execute a single command directly and exit",
    )
    parser.add_argument(
        "--silent",
        action="store_true",
        help="Disable audio speech synthesis output",
    )
    args = parser.parse_args()

    safe_print("========================================")
    safe_print("              NR AI")
    safe_print("     PERSONAL CODING & COMPUTER AGENT")
    safe_print("========================================")
    safe_print("🟢 NR AI systems online.")
    safe_print("🧠 Brain cognitive engine online.")
    safe_print("💻 Computer control & Code Agent online.")

    config = VoiceConfig.from_env()
    if args.silent:
        config.silent_mode = True
        config.tts_enabled = False

    listener = VoiceListener(config=config)
    speaker = VoiceSpeaker(config=config)
    brain = NRBrain()

    if not config.silent_mode and config.tts_enabled:
        safe_print("🔊 Voice speaker online.")
    if not args.cli and not args.command:
        safe_print("🎙️ Voice listener online.")
    safe_print("----------------------------------------")

    # Single command execution mode
    if args.command:
        safe_print(f"\n▶️ Executing command: {args.command}")
        response = brain.think(args.command)
        speaker.speak(response)
        safe_print("----------------------------------------")
        return

    speaker.speak(
        "Hello. I am NR AI. All core systems are online. How can I help you?"
    )

    is_cli_mode = args.cli

    while True:
        try:
            if is_cli_mode:
                safe_print("\nYou: ", end="")
                user_input = input().strip()
            else:
                user_input = listener.listen()
                if not user_input:
                    # Provide typed fallback if user wants to type
                    continue

            if not user_input:
                continue

            response = brain.think(user_input)
            speaker.speak(response)

            if "going offline" in response.lower():
                break

        except (KeyboardInterrupt, EOFError):
            safe_print("\n🛑 Shutting down NR AI...")
            speaker.speak("Goodbye. NR AI is going offline.")
            break


if __name__ == "__main__":
    main()