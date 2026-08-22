import os
import subprocess

from app.commands.app_discovery import AppDiscovery


class AppLauncher:
    """Discovers and safely launches approved applications."""

    def __init__(self):
        self.discovery = AppDiscovery()

        # Applications that NR AI is currently allowed to launch.
        self.approved_applications = {
            "notepad",
            "calculator",
            "paint",
            "command prompt",
            "cmd",
            "powershell",
            "android studio",
            "visual studio",
            "visual studio code",
            "vs code",
            "vscode",
            "chrome",
            "google chrome",
            "unity",
            "unity hub",
        }

    def _launch_path(self, path):
        """Launch an executable or Windows shortcut."""

        if not path:
            return False

        try:
            # Windows shortcuts (.lnk) should be opened with
            # the Windows shell.
            if path.lower().endswith(".lnk"):
                os.startfile(path)
                return True

            # Executables can be launched directly.
            if path.lower().endswith(".exe"):
                subprocess.Popen([path])
                return True

            # Other registered launch targets.
            os.startfile(path)
            return True

        except Exception as error:
            print(f"Launcher error: {error}")
            return False

    def launch(self, application_name):
        name = application_name.lower().strip()

        if name not in self.approved_applications:
            return (
                f"I am not authorized to launch "
                f"{application_name} yet."
            )

        discovered = self.discovery.find_application(name)

        if not discovered:
            return (
                f"I couldn't find {application_name} "
                "on this computer."
            )

        # Registry results can be dictionaries.
        if isinstance(discovered, dict):
            path = discovered.get("path")

            if not path:
                return (
                    f"I found {discovered.get('name', application_name)}, "
                    "but I couldn't find a launch target."
                )
        else:
            path = discovered

        print(f"🔎 Found: {path}")

        if self._launch_path(path):
            return f"{application_name} is opening."

        return f"I couldn't open {application_name}."

    def execute(self, command):
        command = command.lower().strip()

        if not command.startswith("open "):
            return None

        application_name = command[5:].strip()

        if not application_name:
            return None

        if application_name not in self.approved_applications:
            return None

        return self.launch(application_name)


if __name__ == "__main__":

    launcher = AppLauncher()

    print("========================================")
    print("       NR AI DISCOVERY LAUNCHER")
    print("========================================")

    tests = [
        "open android studio",
        "open visual studio",
        "open vs code",
        "open chrome",
    ]

    for command in tests:
        print(f"\n🎙️ Command: {command}")

        response = launcher.execute(command)

        print(f"🤖 NR AI: {response}")