import os
import glob
import winreg


class AppDiscovery:
    """
    NR AI application discovery engine.

    Uses:
    1. Explicit aliases
    2. Windows Start Menu shortcuts
    3. Windows registry
    4. Known installation paths
    """

    START_MENU_LOCATIONS = [
        os.path.expandvars(
            r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"
        ),
        os.path.expandvars(
            r"%PROGRAMDATA%\Microsoft\Windows\Start Menu\Programs"
        ),
    ]

    def __init__(self):
        self.cache = {}

        # Exact application identities and aliases.
        self.aliases = {
            "vs code": "visual studio code",
            "vscode": "visual studio code",
            "code": "visual studio code",

            "vs": "visual studio",
            "visual studio": "visual studio",
            "visual studio 2022": "visual studio",

            "android": "android studio",
            "android studio": "android studio",

            "unity": "unity",
            "unity editor": "unity",
            "unity hub": "unity hub",

            "chrome": "google chrome",
            "google chrome": "google chrome",

            "notepad": "notepad",
            "calculator": "calculator",
            "calc": "calculator",
            "paint": "paint",
            "mspaint": "paint",
            "command prompt": "cmd",
            "cmd": "cmd",
            "powershell": "powershell",
        }

    # ---------------------------------------------------------
    # Normalize application names
    # ---------------------------------------------------------

    def normalize_name(self, name):
        name = name.lower().strip()

        return self.aliases.get(name, name)

    # ---------------------------------------------------------
    # Windows Start Menu
    # ---------------------------------------------------------

    def search_start_menu(self, application_name):
        target = self.normalize_name(application_name)

        candidates = []

        for location in self.START_MENU_LOCATIONS:
            if not os.path.exists(location):
                continue

            pattern = os.path.join(location, "**", "*.lnk")

            try:
                shortcuts = glob.glob(pattern, recursive=True)
            except OSError:
                continue

            for shortcut in shortcuts:
                filename = os.path.splitext(
                    os.path.basename(shortcut)
                )[0].strip().lower()

                candidates.append((filename, shortcut))

        # Exact match first
        for filename, shortcut in candidates:
            if filename == target:
                return shortcut

        # Controlled matching
        for filename, shortcut in candidates:
            if target in filename:
                # Prevent dangerous broad matches
                if target == "visual studio" and (
                    "visual studio code" in filename
                ):
                    continue

                if target == "unity hub" and (
                    "visual studio" in filename
                ):
                    continue

                if target == "unity" and "hub" in filename:
                    continue

                return shortcut

        return None

    # ---------------------------------------------------------
    # Registry
    # ---------------------------------------------------------

    def search_registry(self, application_name):
        target = self.normalize_name(application_name)

        registry_locations = [
            (
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                winreg.KEY_WOW64_64KEY,
            ),
            (
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                winreg.KEY_WOW64_32KEY,
            ),
            (
                winreg.HKEY_CURRENT_USER,
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                0,
            ),
        ]

        for root, path, flag in registry_locations:
            try:
                with winreg.OpenKey(
                    root,
                    path,
                    0,
                    winreg.KEY_READ | flag,
                ) as uninstall_key:

                    count = winreg.QueryInfoKey(
                        uninstall_key
                    )[0]

                    for index in range(count):
                        try:
                            subkey_name = winreg.EnumKey(
                                uninstall_key,
                                index,
                            )

                            with winreg.OpenKey(
                                uninstall_key,
                                subkey_name,
                            ) as app_key:

                                try:
                                    display_name = winreg.QueryValueEx(
                                        app_key,
                                        "DisplayName",
                                    )[0]
                                except FileNotFoundError:
                                    continue

                                display_name_lower = (
                                    display_name.lower().strip()
                                )

                                # Exact identity
                                if display_name_lower == target:
                                    return self._registry_result(
                                        app_key,
                                        display_name,
                                    )

                                # Special handling
                                if target == "visual studio":
                                    if (
                                        "visual studio" in display_name_lower
                                        and "code"
                                        not in display_name_lower
                                    ):
                                        return self._registry_result(
                                            app_key,
                                            display_name,
                                        )

                                elif target == "unity hub":
                                    if "unity hub" in display_name_lower:
                                        return self._registry_result(
                                            app_key,
                                            display_name,
                                        )

                                elif target == "google chrome":
                                    if "google chrome" in display_name_lower:
                                        return self._registry_result(
                                            app_key,
                                            display_name,
                                        )

                        except OSError:
                            continue

            except OSError:
                continue

        return None

    # ---------------------------------------------------------
    # Registry result helper
    # ---------------------------------------------------------

    @staticmethod
    def _registry_result(app_key, display_name):

        try:
            install_location = winreg.QueryValueEx(
                app_key,
                "InstallLocation",
            )[0]

            if install_location:
                return {
                    "name": display_name,
                    "path": install_location,
                }

        except FileNotFoundError:
            pass

        try:
            display_icon = winreg.QueryValueEx(
                app_key,
                "DisplayIcon",
            )[0]

            if display_icon:
                return {
                    "name": display_name,
                    "path": display_icon,
                }

        except FileNotFoundError:
            pass

        return {
            "name": display_name,
            "path": None,
        }

    # ---------------------------------------------------------
    # Known application locations
    # ---------------------------------------------------------

    def search_known_locations(self, application_name):
        target = self.normalize_name(application_name)

        known_locations = {
            "visual studio code": [
                os.path.expandvars(
                    r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"
                ),
            ],

            "unity": [
                r"C:\Program Files\Unity 2022.3.35f1\Editor\Unity.exe",
                r"C:\Program Files\Unity\Editor\Unity.exe",
            ],

            "unity hub": [
                r"C:\Program Files\Unity Hub\Unity Hub.exe",
                r"C:\Program Files\Unity\Hub\Unity Hub.exe",
            ],

            "google chrome": [
                os.path.expandvars(
                    r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"
                ),
                os.path.expandvars(
                    r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"
                ),
                os.path.expandvars(
                    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
                ),
            ],

            "notepad": [
                r"C:\Windows\System32\notepad.exe",
            ],

            "calculator": [
                r"C:\Windows\System32\calc.exe",
            ],

            "paint": [
                r"C:\Windows\System32\mspaint.exe",
            ],

            "cmd": [
                r"C:\Windows\System32\cmd.exe",
            ],

            "powershell": [
                r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
            ],
        }

        locations = known_locations.get(target, [])

        for location in locations:
            if os.path.isfile(location):
                return location

        if target == "unity":
            try:
                unity_matches = glob.glob(r"C:\Program Files\Unity*\Editor\Unity.exe")
                for m in unity_matches:
                    if os.path.isfile(m):
                        return m
            except OSError:
                pass

        return None

    # ---------------------------------------------------------
    # Main discovery
    # ---------------------------------------------------------

    def find_application(self, application_name):

        target = self.normalize_name(application_name)

        if not target:
            return None

        if target in self.cache:
            return self.cache[target]

        # 1. Known locations
        result = self.search_known_locations(target)

        if result:
            self.cache[target] = result
            return result

        # 2. Start Menu
        result = self.search_start_menu(target)

        if result:
            self.cache[target] = result
            return result

        # 3. Registry
        result = self.search_registry(target)

        if result:
            self.cache[target] = result
            return result

        return None

    # ---------------------------------------------------------
    # Human-readable result
    # ---------------------------------------------------------

    def describe(self, application_name):

        result = self.find_application(application_name)

        if result is None:
            return (
                f"I couldn't find {application_name} "
                "on this computer."
            )

        if isinstance(result, dict):

            if result["path"]:
                return (
                    f"I found {result['name']} at "
                    f"{result['path']}."
                )

            return f"I found {result['name']}."

        return (
            f"I found {application_name} at "
            f"{result}."
        )


# -------------------------------------------------------------
# Test
# -------------------------------------------------------------

if __name__ == "__main__":

    discovery = AppDiscovery()

    print("========================================")
    print("   NR AI ACCURATE APP DISCOVERY TEST")
    print("========================================")

    applications = [
        "Android Studio",
        "Visual Studio",
        "VS Code",
        "Unity",
        "Unity Hub",
        "Chrome",
        "Notepad",
        "Calculator",
    ]

    for application in applications:

        print(f"\n🔎 Searching: {application}")

        result = discovery.find_application(application)

        if result:
            print(f"✅ Found: {result}")
        else:
            print("❌ Not found")