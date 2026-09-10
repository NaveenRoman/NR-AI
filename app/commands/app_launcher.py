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
        """Launch an executable or Windows shortcut safely without shell=True."""

        if not path:
            return False, "Target path is empty"

        try:
            # Windows shortcuts (.lnk) should be opened with the Windows shell.
            if str(path).lower().endswith(".lnk"):
                os.startfile(path)
                return True, f"Started shell shortcut: {path}"

            # Executables can be launched directly without shell interpretation.
            if str(path).lower().endswith(".exe"):
                proc = subprocess.Popen([str(path)], shell=False)
                return True, f"Started process (PID: {proc.pid}): {path}"

            # Other registered launch targets.
            os.startfile(path)
            return True, f"Started via startfile: {path}"

        except Exception as error:
            print(f"Launcher error: {error}")
            return False, str(error)

    def launch_detailed(self, application_name: str) -> dict:
        """
        Validates authorization, discovers target, and safely launches approved application.
        Returns detailed status metadata.
        """
        raw_name = (application_name or "").strip()
        name_lower = raw_name.lower()
        normalized = self.discovery.normalize_name(name_lower)

        # Strict authorization check against whitelist (checking both raw and normalized names)
        if name_lower not in self.approved_applications and normalized not in self.approved_applications:
            return {
                "success": False,
                "application": raw_name,
                "authorized": False,
                "path": None,
                "launch_detail": None,
                "message": f"I am not authorized to launch {raw_name} yet.",
                "error": "UNAUTHORIZED_APPLICATION",
            }

        discovered = self.discovery.find_application(normalized)

        if not discovered:
            return {
                "success": False,
                "application": raw_name,
                "authorized": True,
                "path": None,
                "launch_detail": None,
                "message": f"I couldn't find {raw_name} on this computer.",
                "error": "APPLICATION_NOT_FOUND",
            }

        # Registry results can be dictionaries.
        if isinstance(discovered, dict):
            path = discovered.get("path")
            app_display_name = discovered.get("name", raw_name)

            if not path:
                return {
                    "success": False,
                    "application": app_display_name,
                    "authorized": True,
                    "path": None,
                    "launch_detail": None,
                    "message": f"I found {app_display_name}, but I couldn't find a launch target.",
                    "error": "TARGET_PATH_MISSING",
                }
        else:
            path = discovered

        try:
            print(f"Found: {path}")
        except Exception:
            pass

        success, detail = self._launch_path(path)
        if success:
            return {
                "success": True,
                "application": raw_name,
                "authorized": True,
                "path": str(path),
                "launch_detail": detail,
                "message": f"{raw_name} is opening.",
                "error": None,
            }

        return {
            "success": False,
            "application": raw_name,
            "authorized": True,
            "path": str(path),
            "launch_detail": detail,
            "message": f"I couldn't open {raw_name}.",
            "error": detail or "LAUNCH_FAILED",
        }

    def launch(self, application_name):
        return self.launch_detailed(application_name)["message"]

    def execute(self, command):
        command = command.lower().strip()

        for prefix in ("open ", "launch ", "start "):
            if command.startswith(prefix):
                application_name = command[len(prefix):].strip()
                if application_name:
                    return self.launch(application_name)

        return None


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