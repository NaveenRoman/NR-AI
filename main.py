import argparse
import sys
import time

from app.brain.brain import NRBrain
from app.brain.companion import NRCompanion
from app.config.voice_config import VoiceConfig
from app.ui.dashboard import CompanionDashboard
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


def safe_print(msg: str, end: str = "\n") -> None:
    try:
        print(msg, end=end, flush=True)
    except Exception:
        try:
            print(msg.encode("ascii", "replace").decode("ascii"), end=end, flush=True)
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(description="NR AI Personal Assistant & Companion")
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
    parser.add_argument(
        "--port",
        type=int,
        default=8585,
        help="Port for the Companion Dashboard & Web UI (default: 8585)",
    )
    parser.add_argument(
        "--no-server",
        action="store_true",
        help="Disable the Companion Dashboard HTTP server",
    )
    parser.add_argument(
        "--no-voice",
        action="store_true",
        help="Run in server & dashboard mode without continuous microphone loop",
    )
    args = parser.parse_args()

    safe_print("========================================")
    safe_print("              NR AI")
    safe_print("     PERSONAL CODING & COMPUTER AGENT")
    safe_print("========================================")
    safe_print("🟢 NR AI systems online.")
    safe_print("🧠 Companion cognitive engine online.")
    safe_print("💻 10-Agent Multi-Model Orchestrator online.")

    config = VoiceConfig.from_env()
    if args.silent:
        config.silent_mode = True
        config.tts_enabled = False

    listener = VoiceListener(config=config)
    speaker = VoiceSpeaker(config=config)
    companion = NRCompanion(
        voice_config=config,
        voice_listener=listener,
        voice_speaker=speaker,
    )

    dashboard = None
    if not args.no_server:
        dashboard = CompanionDashboard(companion=companion)
        started = dashboard.start_http_server(port=args.port)
        if started:
            safe_print(f"🌐 Companion Dashboard & Web UI online at http://127.0.0.1:{args.port}")
        else:
            safe_print(f"⚠️ Could not start Dashboard HTTP server on port {args.port}")

    if not config.silent_mode and config.tts_enabled:
        safe_print("🔊 Voice speaker online.")
    if not args.cli and not args.no_voice and not args.command:
        safe_print("🎙️ Voice listener online.")
    safe_print("----------------------------------------")

    # Single command execution mode
    if args.command:
        safe_print(f"\n▶️ Executing command: {args.command}")
        resp = companion.interact(args.command, speak_output=not args.silent)
        safe_print(f"NR AI: {resp.text}")
        safe_print("----------------------------------------")
        if dashboard:
            dashboard.stop_http_server()
        return

    if not args.silent and config.tts_enabled:
        speaker.speak(
            "Hello. I am NR AI. All core systems are online. How can I help you?"
        )

    try:
        if args.cli:
            while True:
                safe_print("\nYou: ", end="")
                user_input = input().strip()
                if not user_input:
                    continue
                resp = companion.interact(user_input, speak_output=not args.silent)
                safe_print(f"NR AI: {resp.text}")
                if "going offline" in resp.text.lower():
                    break
        elif args.no_voice:
            safe_print(f"🟢 NR AI Server running at http://127.0.0.1:{args.port}. Press Ctrl+C to stop.")
            while True:
                time.sleep(1)
        else:
            # Real Two-Stage Voice Flow
            companion.avatar.set_waiting_wake("Waiting for wake word (Hey NR)...")
            safe_print("👂 Waiting for wake word ('Hey NR' / 'Hello NR')...")

            while True:
                # Stage 1: Listen specifically for wake word
                is_wake, extracted_cmd = listener.listen_for_wake_word(timeout=3.0)
                if not is_wake:
                    continue

                # Wake word detected!
                wake_phrase = listener.last_recognized_phrase or "Hey NR"
                companion.acknowledge_wake(phrase=wake_phrase)

                # Stage 2: Capture command
                cmd = extracted_cmd
                if not cmd:
                    companion.avatar.set_listening("Listening for command after wake activation...")
                    cmd = listener.listen_for_command(timeout=8.0)

                if not cmd:
                    safe_print("⏱️ No command heard. Returning to wake word listening.")
                    companion.avatar.set_waiting_wake("Waiting for wake word (Hey NR)...")
                    continue

                # Process command through Companion & Agent subsystems
                safe_print(f"▶️ Processing command: {cmd}")
                companion.avatar.set_working(f"Processing command: {cmd[:30]}...")
                resp = companion.interact(cmd, speak_output=not args.silent, wake_phrase_checked=True)
                safe_print(f"NR AI: {resp.text}")

                # Return to Stage 1: Waiting for wake word
                companion.avatar.set_waiting_wake("Waiting for wake word (Hey NR)...")
                safe_print("\n👂 Standing by. Waiting for wake word ('Hey NR' / 'Hello NR')...")

                if "going offline" in resp.text.lower():
                    break
    except (KeyboardInterrupt, EOFError):
        safe_print("\n🛑 Shutting down NR AI...")
        if not args.silent and config.tts_enabled:
            speaker.speak("Goodbye. NR AI is going offline.")
    finally:
        if dashboard:
            dashboard.stop_http_server()


if __name__ == "__main__":
    main()