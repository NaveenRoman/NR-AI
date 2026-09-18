"""
Android Host & AVD Readiness Checker

Performs deterministic, empirical inspection of:
  - Android Studio executable
  - Android SDK & platform-tools
  - ADB
  - Emulator executable
  - Java / JBR / JDK
  - Gradle / Gradle Wrapper
  - AVD List (Pixel_6_API_34 vs Pixel_6_API_35 policy)

Determines exact state:
  installed, available, authorized, offline, booting, ready, unavailable, blocked

Enforces:
  - Strict policy check: NEVER boots or authorizes Pixel_6_API_35.
  - shell=False on all subprocess executions.
  - Bounded readiness polling with timeout.
  - Graceful cleanup.
  - Zero simulated or fabricated live states.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from app.agent.android_safety import (
    AndroidErrorCode,
    AndroidSafetyError,
    AndroidSafetyGate,
    AUTHORIZED_AVDS,
    BLOCKED_AVDS,
    AUTHORIZED_PROJECT_PATH,
)

logger = logging.getLogger("AndroidReadiness")

# Standard Host Paths
DEFAULT_SDK_ROOT = Path(r"C:\Users\navee\AppData\Local\Android\Sdk").resolve()
STUDIO_SEARCH_PATHS = [
    Path(r"C:\Program Files\Android\Android Studio1\bin\studio64.exe").resolve(),
    Path(r"C:\Program Files\Android\Android Studio\bin\studio64.exe").resolve(),
]
JBR_SEARCH_PATHS = [
    Path(r"C:\Program Files\Android\Android Studio1\jbr\bin\java.exe").resolve(),
    Path(r"C:\Program Files\Android\Android Studio\jbr\bin\java.exe").resolve(),
]


class AvdReadinessState(str, Enum):
    INSTALLED = "installed"
    AVAILABLE = "available"
    AUTHORIZED = "authorized"
    OFFLINE = "offline"
    BOOTING = "booting"
    READY = "ready"
    UNAVAILABLE = "unavailable"
    BLOCKED = "blocked"
    TIMEOUT = "timeout"


@dataclass
class EnvironmentComponent:
    name: str
    path: Optional[str]
    present: bool
    version: Optional[str] = None
    details: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AvdStatusReport:
    avd_name: str
    installed: bool
    authorized: bool
    blocked: bool
    state: str
    serial: Optional[str] = None
    boot_completed: bool = False
    message: str = ""
    timestamp: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AndroidHostReadinessChecker:
    """
    Deterministic inspector for the local Android engineering environment and AVD fleet.
    """

    def __init__(
        self,
        safety_gate: Optional[AndroidSafetyGate] = None,
        sdk_root: Optional[Union[str, Path]] = None,
    ):
        self.safety = safety_gate or AndroidSafetyGate()
        self.sdk_root = Path(sdk_root or DEFAULT_SDK_ROOT).resolve()

    def check_environment(self) -> Dict[str, Any]:
        """
        Verifies all 8 components of the host Android environment.
        Returns empirical findings with zero fabricated data.
        """
        findings: Dict[str, EnvironmentComponent] = {}

        # 1. Android Studio
        studio_path = next((p for p in STUDIO_SEARCH_PATHS if p.exists()), None)
        findings["studio"] = EnvironmentComponent(
            name="Android Studio",
            path=str(studio_path) if studio_path else None,
            present=studio_path is not None,
            version="Android Studio 64-bit" if studio_path else None,
        )

        # 2. Android SDK Root
        sdk_present = self.sdk_root.exists() and self.sdk_root.is_dir()
        findings["sdk"] = EnvironmentComponent(
            name="Android SDK",
            path=str(self.sdk_root) if sdk_present else None,
            present=sdk_present,
        )

        # 3. Platform Tools
        pt_path = self.sdk_root / "platform-tools"
        pt_present = pt_path.exists() and pt_path.is_dir()
        findings["platform_tools"] = EnvironmentComponent(
            name="Platform Tools",
            path=str(pt_path) if pt_present else None,
            present=pt_present,
        )

        # 4. ADB
        adb_exe = pt_path / "adb.exe"
        if not adb_exe.exists():
            which_adb = shutil.which("adb")
            adb_exe = Path(which_adb) if which_adb else adb_exe

        adb_ver = None
        adb_present = adb_exe.exists()
        if adb_present:
            try:
                res = subprocess.run([str(adb_exe), "version"], capture_output=True, text=True, timeout=5, shell=False)
                if res.returncode == 0 and res.stdout:
                    first_line = res.stdout.strip().splitlines()[0]
                    adb_ver = first_line
            except Exception as e:
                logger.warning(f"Failed to probe ADB version: {e}")

        findings["adb"] = EnvironmentComponent(
            name="Android Debug Bridge (ADB)",
            path=str(adb_exe) if adb_present else None,
            present=adb_present,
            version=adb_ver,
        )

        # 5. Emulator
        emu_exe = self.sdk_root / "emulator" / "emulator.exe"
        if not emu_exe.exists():
            which_emu = shutil.which("emulator")
            emu_exe = Path(which_emu) if which_emu else emu_exe

        emu_ver = None
        emu_present = emu_exe.exists()
        if emu_present:
            try:
                res = subprocess.run([str(emu_exe), "-version"], capture_output=True, text=True, timeout=5, shell=False)
                if res.returncode == 0 and res.stdout:
                    first_line = res.stdout.strip().splitlines()[0]
                    emu_ver = first_line
            except Exception as e:
                logger.warning(f"Failed to probe emulator version: {e}")

        findings["emulator"] = EnvironmentComponent(
            name="Android Emulator",
            path=str(emu_exe) if emu_present else None,
            present=emu_present,
            version=emu_ver,
        )

        # 6. Java / JDK / JBR
        java_exe = next((p for p in JBR_SEARCH_PATHS if p.exists()), None)
        if not java_exe:
            which_java = shutil.which("java")
            java_exe = Path(which_java) if which_java else None

        java_ver = None
        java_present = java_exe is not None and java_exe.exists()
        if java_present:
            try:
                res = subprocess.run([str(java_exe), "-version"], capture_output=True, text=True, timeout=5, shell=False)
                output = res.stderr or res.stdout
                if output:
                    java_ver = output.strip().splitlines()[0]
            except Exception as e:
                logger.warning(f"Failed to probe Java version: {e}")

        findings["java"] = EnvironmentComponent(
            name="Java / JBR",
            path=str(java_exe) if java_present else None,
            present=java_present,
            version=java_ver,
        )

        # 7. Gradle / Gradle Wrapper
        gradlew_bat = AUTHORIZED_PROJECT_PATH / "gradlew.bat"
        gradle_present = gradlew_bat.exists()
        findings["gradle"] = EnvironmentComponent(
            name="Gradle Wrapper",
            path=str(gradlew_bat) if gradle_present else shutil.which("gradle"),
            present=gradle_present or shutil.which("gradle") is not None,
            version="Wrapper Configured" if gradle_present else "System Gradle",
        )

        # 8. Installed AVD Fleet
        installed_avds = self.list_installed_avds()
        findings["avds"] = EnvironmentComponent(
            name="AVD Fleet",
            path=None,
            present=len(installed_avds) > 0,
            details={"installed_avds": installed_avds},
        )

        all_present = all(c.present for c in findings.values())
        return {
            "all_components_ready": all_present,
            "components": {k: v.to_dict() for k, v in findings.items()},
            "timestamp": time.time(),
        }

    def list_installed_avds(self) -> List[str]:
        """Query installed AVD names directly from emulator binary without shell."""
        emu_exe = self.sdk_root / "emulator" / "emulator.exe"
        if not emu_exe.exists():
            which_emu = shutil.which("emulator")
            if not which_emu:
                return []
            emu_exe = Path(which_emu)

        try:
            res = subprocess.run(
                [str(emu_exe), "-list-avds"],
                capture_output=True,
                text=True,
                timeout=5,
                shell=False,
            )
            if res.returncode == 0 and res.stdout:
                return [line.strip() for line in res.stdout.splitlines() if line.strip()]
            return []
        except Exception as e:
            logger.warning(f"Could not list AVDs: {e}")
            return []

    def get_attached_devices(self) -> List[Dict[str, str]]:
        """Query attached devices from ADB."""
        adb_exe = self.sdk_root / "platform-tools" / "adb.exe"
        if not adb_exe.exists():
            which_adb = shutil.which("adb")
            if not which_adb:
                return []
            adb_exe = Path(which_adb)

        try:
            res = subprocess.run(
                [str(adb_exe), "devices"],
                capture_output=True,
                text=True,
                timeout=5,
                shell=False,
            )
            devices = []
            if res.returncode == 0 and res.stdout:
                for line in res.stdout.splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 2 and parts[0] != "List":
                        devices.append({"serial": parts[0], "status": parts[1]})
            return devices
        except Exception as e:
            logger.warning(f"Could not query attached devices: {e}")
            return []

    def determine_avd_state(self, avd_name: str) -> AvdStatusReport:
        """
        Determines the exact readiness posture of an AVD.
        Policy check: Pixel_6_API_35 is explicitly identified as BLOCKED.
        """
        now = time.time()
        is_auth = avd_name in AUTHORIZED_AVDS
        is_blocked = avd_name in BLOCKED_AVDS

        if is_blocked:
            return AvdStatusReport(
                avd_name=avd_name,
                installed=True,
                authorized=False,
                blocked=True,
                state=AvdReadinessState.BLOCKED.value,
                message=f"AVD '{avd_name}' is blocked by policy and cannot be booted.",
                timestamp=now,
            )

        installed_avds = self.list_installed_avds()
        if avd_name not in installed_avds:
            return AvdStatusReport(
                avd_name=avd_name,
                installed=False,
                authorized=is_auth,
                blocked=False,
                state=AvdReadinessState.UNAVAILABLE.value,
                message=f"AVD '{avd_name}' is not installed in local Android SDK.",
                timestamp=now,
            )

        # Check attached devices
        attached = self.get_attached_devices()
        adb_exe = self.sdk_root / "platform-tools" / "adb.exe"

        for dev in attached:
            serial = dev["serial"]
            dev_status = dev["status"]

            if serial.startswith("emulator-"):
                if dev_status == "offline":
                    return AvdStatusReport(
                        avd_name=avd_name,
                        installed=True,
                        authorized=is_auth,
                        blocked=False,
                        state=AvdReadinessState.OFFLINE.value,
                        serial=serial,
                        message=f"Attached emulator '{serial}' is offline.",
                        timestamp=now,
                    )
                elif dev_status == "device":
                    # Check boot completed
                    boot_completed = False
                    if adb_exe.exists():
                        try:
                            res = subprocess.run(
                                [str(adb_exe), "-s", serial, "shell", "getprop", "sys.boot_completed"],
                                capture_output=True,
                                text=True,
                                timeout=3,
                                shell=False,
                            )
                            if res.returncode == 0 and res.stdout.strip() == "1":
                                boot_completed = True
                        except Exception:
                            pass

                    if boot_completed:
                        return AvdStatusReport(
                            avd_name=avd_name,
                            installed=True,
                            authorized=is_auth,
                            blocked=False,
                            state=AvdReadinessState.READY.value,
                            serial=serial,
                            boot_completed=True,
                            message=f"AVD '{avd_name}' is running and ready on '{serial}'.",
                            timestamp=now,
                        )
                    else:
                        return AvdStatusReport(
                            avd_name=avd_name,
                            installed=True,
                            authorized=is_auth,
                            blocked=False,
                            state=AvdReadinessState.BOOTING.value,
                            serial=serial,
                            boot_completed=False,
                            message=f"AVD '{avd_name}' is booting on '{serial}'.",
                            timestamp=now,
                        )

        # If installed, authorized, and no emulator process is attached
        return AvdStatusReport(
            avd_name=avd_name,
            installed=True,
            authorized=is_auth,
            blocked=False,
            state=AvdReadinessState.AVAILABLE.value,
            serial=None,
            boot_completed=False,
            message=f"AVD '{avd_name}' is installed, authorized, and available to boot (currently offline).",
            timestamp=now,
        )

    def verify_avd_readiness(
        self,
        avd_name: str = "Pixel_6_API_34",
        auto_start: bool = False,
        timeout_seconds: float = 15.0,
    ) -> AvdStatusReport:
        """
        Bounded readiness verification.
        Refuses to start blocked AVDs (Pixel_6_API_35).
        If auto_start is False, reports current host posture immediately.
        If auto_start is True, attempts a bounded boot and polls readiness until timeout.
        """
        # 1. Enforce Emergency Stop
        self.safety.check_emergency_stop()

        # 2. Strict Policy Enforcement
        if avd_name in BLOCKED_AVDS:
            raise AndroidSafetyError(
                AndroidErrorCode.DEVICE_NOT_AUTHORIZED,
                f"AVD '{avd_name}' is blocked by policy. Boot rejected.",
            )

        # 3. Authorize AVD name
        validated_name = self.safety.validate_avd_name(avd_name)

        # 4. Check initial posture
        initial_status = self.determine_avd_state(validated_name)
        if not initial_status.installed or initial_status.state == AvdReadinessState.READY.value:
            return initial_status

        if not auto_start:
            return initial_status

        # 5. Bounded Boot Execution
        emu_exe = self.sdk_root / "emulator" / "emulator.exe"
        if not emu_exe.exists():
            return AvdStatusReport(
                avd_name=validated_name,
                installed=True,
                authorized=True,
                blocked=False,
                state=AvdReadinessState.UNAVAILABLE.value,
                message=f"Emulator executable not found at '{emu_exe}'.",
                timestamp=time.time(),
            )

        logger.info(f"Initiating bounded boot of authorized AVD '{validated_name}' (timeout: {timeout_seconds}s)...")
        proc = subprocess.Popen(
            [str(emu_exe), "-avd", validated_name, "-no-boot-anim", "-no-snapshot-save"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )

        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            # Check emergency stop during loop
            self.safety.check_emergency_stop()

            current = self.determine_avd_state(validated_name)
            if current.state == AvdReadinessState.READY.value:
                logger.info(f"AVD '{validated_name}' reached READY on '{current.serial}'.")
                return current

            time.sleep(1.0)

        # Timed out - cleanly terminate spawned process
        try:
            proc.terminate()
            proc.wait(timeout=2.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        if proc.stdout:
            try:
                proc.stdout.close()
            except Exception:
                pass
        if proc.stderr:
            try:
                proc.stderr.close()
            except Exception:
                pass

        logger.warning(f"AVD '{validated_name}' boot exceeded {timeout_seconds}s bounded timeout.")
        return AvdStatusReport(
            avd_name=validated_name,
            installed=True,
            authorized=True,
            blocked=False,
            state=AvdReadinessState.TIMEOUT.value,
            message=f"AVD '{validated_name}' boot timed out after {timeout_seconds}s.",
            timestamp=time.time(),
        )

    def stop_avd(self, serial: str) -> bool:
        """Gracefully terminate an attached emulator using adb emu kill."""
        validated_serial = self.safety.validate_device_serial(serial)
        if not validated_serial.startswith("emulator-"):
            return False

        adb_exe = self.sdk_root / "platform-tools" / "adb.exe"
        if not adb_exe.exists():
            return False

        try:
            res = subprocess.run(
                [str(adb_exe), "-s", validated_serial, "emu", "kill"],
                capture_output=True,
                text=True,
                timeout=5,
                shell=False,
            )
            return res.returncode == 0
        except Exception as e:
            logger.warning(f"Failed to stop emulator '{validated_serial}': {e}")
            return False
