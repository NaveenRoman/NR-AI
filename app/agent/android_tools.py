"""
NR-AI Android Tool Registry & Deterministic Safety Contracts (Step 6 Phase 1).

Implements 15 strongly validated, allowlisted Android tools:
1. android.list_devices
2. android.get_device_status
3. android.launch_studio
4. android.focus_studio
5. android.open_project
6. android.inspect_project
7. android.build_project
8. android.get_build_status
9. android.list_emulators
10. android.start_emulator
11. android.stop_emulator
12. android.install_test_apk
13. android.launch_app
14. android.capture_log
15. android.verify_app

Every tool guarantees:
- Strict parameter schema validation
- Project-root and device allowlist enforcement
- Emergency stop gating
- Structured ToolExecutionResult
- Audit logging
- Zero arbitrary shell, ADB, or Gradle command execution
"""

from dataclasses import dataclass, field
from enum import Enum
import logging
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from app.agent.android_safety import (
    ALLOWED_ANDROID_TOOLS,
    AUTHORIZED_AVDS,
    AUTHORIZED_BUILD_ACTIONS,
    AUTHORIZED_DEVICE_SERIALS,
    AUTHORIZED_PACKAGE_NAME,
    AUTHORIZED_PROJECT_PATH,
    BLOCKED_AVDS,
    DEFAULT_ADB_PATH,
    DEFAULT_EMULATOR_PATH,
    DEFAULT_JDK_PATH,
    DEFAULT_SDK_PATH,
    DEFAULT_STUDIO_PATH,
    AndroidErrorCode,
    AndroidRateLimiter,
    AndroidSafetyError,
    AndroidSafetyGate,
    EmergencyStopActiveError,
    RiskLevel,
    TOOL_RISK_MAP,
)
from app.agent.android_toolchain import AndroidErrorAnalyzer, AndroidProjectInspector
from app.agent.window_manager import WindowManager
from app.commands.app_discovery import AppDiscovery
from app.memory.audit_logger import AuditLogger

logger = logging.getLogger("NRAI.AndroidTools")


# -----------------------------------------------------------------------------
# Structured Results
# -----------------------------------------------------------------------------

@dataclass
class AndroidToolResult:
    """Standardized structured result returned by every Android tool."""
    success: bool
    tool: str
    data: Dict[str, Any] = field(default_factory=dict)
    message: str = ""
    error: Optional[str] = None
    error_code: Optional[str] = None
    risk_level: str = RiskLevel.LOW.value
    requires_confirmation: bool = False
    verified: bool = False
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "tool": self.tool,
            "data": self.data,
            "message": self.message,
            "error": self.error,
            "error_code": self.error_code,
            "risk_level": self.risk_level,
            "requires_confirmation": self.requires_confirmation,
            "verified": self.verified,
            "timestamp": self.timestamp,
        }


# -----------------------------------------------------------------------------
# Safe Deterministic Low-Level Abstractions
# -----------------------------------------------------------------------------

class SafeAdbClient:
    """
    Deterministic, bounded wrapper around ADB.
    Uses shell=False and strict fixed command arrays.
    Never exposes arbitrary shell or command construction.
    """

    def __init__(self, adb_path: Optional[Union[str, Path]] = None):
        self.adb_path = Path(adb_path or DEFAULT_ADB_PATH).resolve()

    def _resolve_adb(self) -> Path:
        if self.adb_path.exists():
            return self.adb_path
        # Fallback to PATH search if default does not exist
        which_adb = shutil_which("adb")
        if which_adb:
            return Path(which_adb)
        return self.adb_path

    def _run_adb(self, args: List[str], timeout: float = 10.0) -> Tuple[int, str, str]:
        exe = self._resolve_adb()
        if not exe.exists() and not shutil_which("adb"):
            raise AndroidSafetyError(
                AndroidErrorCode.DEVICE_NOT_FOUND,
                f"ADB executable not found at '{exe}'.",
            )
        cmd = [str(exe)] + args
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
            timeout=timeout,
        )
        return res.returncode, res.stdout.strip(), res.stderr.strip()

    def _run_adb_bytes(self, args: List[str], timeout: float = 10.0) -> Tuple[int, bytes, str]:
        exe = self._resolve_adb()
        if not exe.exists() and not shutil_which("adb"):
            raise AndroidSafetyError(
                AndroidErrorCode.DEVICE_NOT_FOUND,
                f"ADB executable not found at '{exe}'.",
            )
        cmd = [str(exe)] + args
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            shell=False,
            timeout=timeout,
        )
        return res.returncode, res.stdout, res.stderr.decode("utf-8", errors="replace").strip()

    def list_devices(self) -> List[Dict[str, str]]:
        """Lists attached devices with serials and states."""
        try:
            code, out, _ = self._run_adb(["devices", "-l"])
            devices = []
            lines = out.splitlines()[1:]
            for line in lines:
                parts = line.strip().split()
                if len(parts) >= 2:
                    serial = parts[0]
                    state = parts[1]
                    details = " ".join(parts[2:]) if len(parts) > 2 else ""
                    devices.append({"serial": serial, "state": state, "details": details})
            return devices
        except Exception as e:
            logger.warning(f"Failed to query adb devices: {e}")
            return []

    def get_device_state(self, serial: str) -> str:
        """Queries the state of an authorized device ('device', 'offline', 'bootloader', etc.)."""
        code, out, _ = self._run_adb(["-s", serial, "get-state"])
        if code != 0 or not out:
            return "offline"
        return out.strip()

    def is_package_installed(self, serial: str, package_name: str) -> bool:
        """Checks if a specific package is installed on the target device."""
        code, out, _ = self._run_adb(["-s", serial, "shell", "pm", "list", "packages", package_name])
        return f"package:{package_name}" in out

    def get_process_pid(self, serial: str, package_name: str) -> Optional[int]:
        """Queries the process PID for an installed package."""
        code, out, _ = self._run_adb(["-s", serial, "shell", "pidof", package_name])
        if code == 0 and out.strip().isdigit():
            return int(out.strip())
        return None

    def launch_package(self, serial: str, package_name: str) -> bool:
        """Launches the app using the Android Monkey runner for category.LAUNCHER."""
        code, out, err = self._run_adb(
            ["-s", serial, "shell", "monkey", "-p", package_name, "-c", "android.intent.category.LAUNCHER", "1"]
        )
        return code == 0 and ("Events injected: 1" in out or "Monkey" in out)

    def install_apk(self, serial: str, apk_path: Path) -> Tuple[bool, str]:
        """Installs an authorized APK onto an authorized device."""
        code, out, err = self._run_adb(["-s", serial, "install", "-r", str(apk_path)], timeout=60.0)
        success = (code == 0 and "Success" in out)
        message = out if success else (err or out or "APK installation failed.")
        return success, message

    def capture_logcat_advanced(
        self,
        serial: str,
        lines: int = 100,
        severity: Optional[str] = None,
        filter_package: Optional[str] = None,
        filter_tag: Optional[str] = None,
        clear_buffer: bool = False,
    ) -> str:
        """Captures bounded, formatted, and sanitized logcat entries."""
        if clear_buffer:
            self._run_adb(["-s", serial, "logcat", "-c"], timeout=5.0)

        bounded_lines = max(1, min(lines, 1000))
        cmd = ["-s", serial, "logcat", "-d", "-v", "threadtime", "-t", str(bounded_lines)]

        if severity:
            sev = severity.strip().upper()
            if sev in ("V", "D", "I", "W", "E", "F"):
                cmd.append(f"*:{sev}")

        code, out, _ = self._run_adb(cmd, timeout=15.0)
        if code != 0 or not out:
            return ""

        result_lines = out.splitlines()
        if filter_package:
            result_lines = [l for l in result_lines if filter_package in l]
        if filter_tag:
            result_lines = [l for l in result_lines if filter_tag in l]

        result_text = "\n".join(result_lines)
        if len(result_text.encode("utf-8")) > 200_000:
            result_text = result_text[:199_000] + "\n... [LOGCAT TRUNCATED AT 200KB] ..."

        from app.agent.android_code_repair import redact_sensitive_content
        return redact_sensitive_content(result_text)

    def capture_logcat(self, serial: str, lines: int = 100, filter_package: Optional[str] = None) -> str:
        """Captures bounded recent logcat messages, optionally filtered to package."""
        return self.capture_logcat_advanced(serial, lines=lines, filter_package=filter_package)

    def get_device_info(self, serial: str) -> Dict[str, str]:
        """Queries core hardware, OS, and build properties for an authorized device."""
        code, out, _ = self._run_adb(["-s", serial, "shell", "getprop"], timeout=8.0)
        props: Dict[str, str] = {}
        if code == 0 and out:
            for line in out.splitlines():
                m = re.match(r"\[(.*?)\]:\s*\[(.*?)\]", line.strip())
                if m:
                    props[m.group(1)] = m.group(2)

        return {
            "serial": serial,
            "os_version": props.get("ro.build.version.release", "unknown"),
            "sdk_level": props.get("ro.build.version.sdk", "unknown"),
            "model": props.get("ro.product.model", "unknown"),
            "manufacturer": props.get("ro.product.manufacturer", "unknown"),
            "brand": props.get("ro.product.brand", "unknown"),
            "fingerprint": props.get("ro.build.fingerprint", "unknown"),
        }

    def get_process_info(self, serial: str, package_name: str) -> Dict[str, Any]:
        """Queries runtime process information for a package."""
        pid = self.get_process_pid(serial, package_name)
        is_running = pid is not None
        info: Dict[str, Any] = {
            "package": package_name,
            "is_running": is_running,
            "pid": pid,
        }
        if is_running and pid is not None:
            code, out, _ = self._run_adb(["-s", serial, "shell", "dumpsys", "meminfo", package_name], timeout=5.0)
            if code == 0 and out:
                m = re.search(r"TOTAL\s+PSS:\s*(\d+)", out)
                if m:
                    info["total_pss_kb"] = int(m.group(1))
        return info

    def get_app_state(self, serial: str, package_name: str = AUTHORIZED_PACKAGE_NAME) -> Dict[str, Any]:
        """Captures bounded deterministic state for package on target device."""
        installed = self.is_package_installed(serial, package_name)
        pid = self.get_process_pid(serial, package_name) if installed else None
        dev_status = self.get_device_state(serial)
        return {
            "serial": serial,
            "package_name": package_name,
            "installed": installed,
            "is_running": (pid is not None),
            "pid": pid,
            "device_status": dev_status,
        }

    def stop_emulator(self, serial: str) -> bool:
        """Kills an emulator safely using emu kill."""
        code, _, _ = self._run_adb(["-s", serial, "emu", "kill"])
        return code == 0

    def get_foreground_app(self, serial: str) -> Dict[str, str]:
        """Queries the current focused foreground application and activity."""
        code, out, _ = self._run_adb(["-s", serial, "shell", "dumpsys", "window", "displays"], timeout=5.0)
        if code != 0 or not out:
            code, out, _ = self._run_adb(["-s", serial, "shell", "dumpsys", "activity", "recents"], timeout=5.0)

        # Match mCurrentFocus or mFocusedApp or topResumedActivity
        m = re.search(r"(?:mCurrentFocus|mFocusedApp|topResumedActivity|ResumedActivity)[^=\n:]*[=:]\s*(?:Window\{|ActivityRecord\{)?[^}\n]*?\s+([a-zA-Z0-9._]+)/([a-zA-Z0-9._$]+)", out)
        if m:
            pkg, act = m.group(1).strip(), m.group(2).strip()
            if act.startswith("."):
                act = pkg + act
            return {"package": pkg, "activity": act}

        # Fallback: check mCurrentFocus without slash
        m2 = re.search(r"mCurrentFocus=Window\{[^}\n]*?\s+([a-zA-Z0-9._]+)\}", out)
        if m2:
            return {"package": m2.group(1).strip(), "activity": "unknown"}

        return {"package": "unknown", "activity": "unknown"}

    def get_screen_size(self, serial: str) -> Tuple[int, int]:
        """Queries the physical or override screen dimensions of the target device."""
        code, out, _ = self._run_adb(["-s", serial, "shell", "wm", "size"], timeout=5.0)
        if code == 0 and out:
            m = re.search(r"(?:Override size|Physical size):\s*(\d+)x(\d+)", out)
            if m:
                return int(m.group(1)), int(m.group(2))
        return 1080, 2400

    def capture_screen(self, serial: str, dest_path: Optional[Path] = None) -> bytes:
        """Captures screenshot bytes from authorized device and optionally writes to dest_path."""
        code, raw_bytes, err = self._run_adb_bytes(["-s", serial, "exec-out", "screencap", "-p"], timeout=15.0)
        if code != 0 or not raw_bytes or not raw_bytes.startswith(b"\x89PNG"):
            # Fallback to device-local file capture and pull
            temp_dev = "/sdcard/nrai_temp_cap.png"
            self._run_adb(["-s", serial, "shell", "screencap", "-p", temp_dev], timeout=10.0)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
                local_tmp = Path(tf.name)
            try:
                self._run_adb(["-s", serial, "pull", temp_dev, str(local_tmp)], timeout=15.0)
                if local_tmp.exists():
                    raw_bytes = local_tmp.read_bytes()
            finally:
                self._run_adb(["-s", serial, "shell", "rm", "-f", temp_dev])
                if local_tmp.exists():
                    try:
                        local_tmp.unlink()
                    except Exception:
                        pass

        if not raw_bytes:
            raise AndroidSafetyError(
                AndroidErrorCode.SCREEN_CAPTURE_FAILED,
                f"Failed to capture screen on device '{serial}'.",
            )

        if dest_path:
            p = Path(dest_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(raw_bytes)

        return raw_bytes

    def dump_ui_hierarchy(self, serial: str) -> str:
        """Captures and returns the raw UI hierarchy XML via uiautomator dump with cleanup."""
        temp_dev = "/sdcard/nrai_ui_dump.xml"
        code, out, err = self._run_adb(["-s", serial, "shell", "uiautomator", "dump", temp_dev], timeout=15.0)
        xml_content = ""
        if code == 0:
            c_code, c_out, _ = self._run_adb(["-s", serial, "shell", "cat", temp_dev], timeout=10.0)
            if c_code == 0:
                xml_content = c_out
            self._run_adb(["-s", serial, "shell", "rm", "-f", temp_dev])
        return xml_content

    def tap(self, serial: str, x: int, y: int) -> bool:
        """Injects a single tap event at validated screen coordinates."""
        code, out, err = self._run_adb(["-s", serial, "shell", "input", "tap", str(int(x)), str(int(y))])
        return code == 0

    def press_key(self, serial: str, keycode: int) -> bool:
        """Injects a keyevent for an allowlisted keycode."""
        code, out, err = self._run_adb(["-s", serial, "shell", "input", "keyevent", str(int(keycode))])
        return code == 0

    def back(self, serial: str) -> bool:
        """Presses the Back key (KEYCODE_BACK = 4)."""
        return self.press_key(serial, 4)

    def home(self, serial: str) -> bool:
        """Presses the Home key (KEYCODE_HOME = 3)."""
        return self.press_key(serial, 3)

    def swipe(self, serial: str, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> bool:
        """Injects a swipe gesture between validated screen coordinates."""
        code, out, err = self._run_adb(
            ["-s", serial, "shell", "input", "swipe", str(int(x1)), str(int(y1)), str(int(x2)), str(int(y2)), str(int(duration_ms))]
        )
        return code == 0

    def scroll(self, serial: str, direction: str = "down", distance_ratio: float = 0.5) -> bool:
        """Performs a bounded directional scroll gesture on the target device."""
        w, h = self.get_screen_size(serial)
        cx = w // 2
        ratio = max(0.1, min(distance_ratio, 0.8))
        if direction.lower() == "down":
            start_y = int(h * (0.5 + ratio / 2))
            end_y = int(h * (0.5 - ratio / 2))
            return self.swipe(serial, cx, start_y, cx, end_y)
        elif direction.lower() == "up":
            start_y = int(h * (0.5 - ratio / 2))
            end_y = int(h * (0.5 + ratio / 2))
            return self.swipe(serial, cx, start_y, cx, end_y)
        elif direction.lower() == "right":
            start_x = int(w * (0.5 + ratio / 2))
            end_x = int(w * (0.5 - ratio / 2))
            cy = h // 2
            return self.swipe(serial, start_x, cy, end_x, cy)
        elif direction.lower() == "left":
            start_x = int(w * (0.5 - ratio / 2))
            end_x = int(w * (0.5 + ratio / 2))
            cy = h // 2
            return self.swipe(serial, start_x, cy, end_x, cy)
        else:
            return False


class SafeGradleRunner:
    """
    Deterministic Gradle build execution engine.
    Strictly constrained to C:\\NR-AI\\nr_android_test and allowlisted actions.
    """

    def __init__(
        self,
        project_dir: Path = AUTHORIZED_PROJECT_PATH,
        jdk_path: Path = DEFAULT_JDK_PATH,
        sdk_path: Path = DEFAULT_SDK_PATH,
    ):
        self.project_dir = project_dir.resolve()
        self.jdk_path = jdk_path.resolve()
        self.sdk_path = sdk_path.resolve()

    def _get_gradle_cmd(self) -> Path:
        gradlew_bat = self.project_dir / "gradlew.bat"
        if not gradlew_bat.exists():
            raise AndroidSafetyError(
                AndroidErrorCode.PROJECT_NOT_AUTHORIZED,
                f"gradlew.bat not found in project '{self.project_dir}'.",
            )
        return gradlew_bat

    def _build_env(self) -> Dict[str, str]:
        env = os.environ.copy()
        if self.jdk_path.exists():
            env["JAVA_HOME"] = str(self.jdk_path)
            jdk_bin = str(self.jdk_path / "bin")
            env["PATH"] = f"{jdk_bin};" + env.get("PATH", "")
        if self.sdk_path.exists():
            env["ANDROID_HOME"] = str(self.sdk_path)
            env["ANDROID_SDK_ROOT"] = str(self.sdk_path)
        return env

    @staticmethod
    def _redact_output(text: str) -> str:
        """Redacts sensitive values from output."""
        patterns = [
            (r'(?i)(password\s*[:=]\s*)["\']?[^\s"\']+["\']?', r'\1[REDACTED]'),
            (r'(?i)(secret\s*[:=]\s*)["\']?[^\s"\']+["\']?', r'\1[REDACTED]'),
            (r'(?i)(token\s*[:=]\s*)["\']?[^\s"\']+["\']?', r'\1[REDACTED]'),
            (r'(?i)(api[_-]?key\s*[:=]\s*)["\']?[^\s"\']+["\']?', r'\1[REDACTED]'),
        ]
        redacted = text
        for pat, repl in patterns:
            redacted = re.sub(pat, repl, redacted)
        return redacted

    def run_action(self, action_tasks: Union[List[str], str], timeout: float = 180.0) -> Dict[str, Any]:
        """Runs an authorized Gradle action task list."""
        if isinstance(action_tasks, str):
            from app.agent.android_safety import AUTHORIZED_BUILD_ACTIONS
            clean = action_tasks.strip().upper()
            if clean in AUTHORIZED_BUILD_ACTIONS:
                action_tasks = AUTHORIZED_BUILD_ACTIONS[clean]
            else:
                action_tasks = [action_tasks]

        gradle_cmd = self._get_gradle_cmd()
        full_cmd = [str(gradle_cmd)] + action_tasks + ["--console=plain"]

        start_time = time.time()
        try:
            res = subprocess.run(
                full_cmd,
                cwd=str(self.project_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=False,
                env=self._build_env(),
                timeout=timeout,
            )
            duration = time.time() - start_time
            success = (res.returncode == 0)
            raw_output = res.stdout + ("\n" + res.stderr if res.stderr else "")
            combined_output = self._redact_output(raw_output)

            diagnosis = None
            if not success:
                diagnosis = AndroidErrorAnalyzer.analyze(combined_output)

            output_sample = combined_output
            if not success and len(combined_output) > 4000:
                m = re.search(r"(?:e:\s*|error:\s*|> Task :[^\n]*FAILED)[\s\S]*", combined_output)
                if m:
                    output_sample = m.group(0)[:4000]
                else:
                    output_sample = combined_output[-4000:]

            return {
                "success": success,
                "returncode": res.returncode,
                "tasks": action_tasks,
                "duration_s": round(duration, 2),
                "output_sample": output_sample,
                "full_output": combined_output,
                "diagnosis": diagnosis,
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "returncode": -1,
                "tasks": action_tasks,
                "duration_s": round(time.time() - start_time, 2),
                "output_sample": "Gradle build execution timed out.",
                "diagnosis": {"category": "timeout", "diagnosis": "Gradle build exceeded timeout limit."},
            }
        except Exception as e:
            return {
                "success": False,
                "returncode": -1,
                "tasks": action_tasks,
                "duration_s": round(time.time() - start_time, 2),
                "output_sample": str(e),
                "diagnosis": {"category": "execution_error", "diagnosis": str(e)},
            }

    def run_gradle_task(self, action_name: str, safety_gate: Optional[Any] = None) -> AndroidToolResult:
        """Executes an authorized Gradle task, returning an AndroidToolResult."""
        from app.agent.android_safety import AndroidSafetyGate, AndroidErrorCode, AndroidSafetyError
        gate = safety_gate or AndroidSafetyGate()
        try:
            tasks = gate.validate_build_action(action_name)
            res = self.run_action(tasks)
            success = res.get("success", False)
            return AndroidToolResult(
                success=success,
                tool="android.build_project",
                data=res,
                message=f"Gradle build '{action_name}' {'succeeded' if success else 'failed'}.",
                error=None if success else f"Build task failed: {res.get('output_sample', '')[:200]}",
                error_code=None if success else AndroidErrorCode.BUILD_FAILED.value,
                verified=success,
            )
        except AndroidSafetyError as se:
            return AndroidToolResult(
                success=False,
                tool="android.build_project",
                error=se.message,
                error_code=se.code.value,
            )


class SafeEmulatorManager:
    """Safe emulator management for allowlisted AVDs."""

    def __init__(self, emulator_path: Optional[Path] = None):
        self.emulator_path = Path(emulator_path or DEFAULT_EMULATOR_PATH).resolve()

    def list_avds(self) -> List[str]:
        if not self.emulator_path.exists():
            which_emu = shutil_which("emulator")
            if which_emu:
                self.emulator_path = Path(which_emu)
            else:
                return []
        try:
            res = subprocess.run(
                [str(self.emulator_path), "-list-avds"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=False,
                timeout=10.0,
            )
            if res.returncode == 0:
                return [line.strip() for line in res.stdout.splitlines() if line.strip()]
            return []
        except Exception as e:
            logger.warning(f"Failed to list AVDs: {e}")
            return []

    def start_emulator(self, avd_name: str) -> Dict[str, Any]:
        if not self.emulator_path.exists():
            raise AndroidSafetyError(
                AndroidErrorCode.DEVICE_NOT_FOUND,
                f"Emulator executable not found at '{self.emulator_path}'.",
            )
        try:
            # Spawn in detached background process without shell=True
            proc = subprocess.Popen(
                [str(self.emulator_path), "-avd", avd_name, "-no-boot-anim"],
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return {
                "success": True,
                "avd": avd_name,
                "pid": proc.pid,
                "message": f"Spawned emulator '{avd_name}' (PID: {proc.pid}).",
            }
        except Exception as e:
            raise AndroidSafetyError(
                AndroidErrorCode.ACTION_NOT_ALLOWED,
                f"Failed to launch emulator '{avd_name}': {e}",
            )


def shutil_which(cmd: str) -> Optional[str]:
    """Helper to locate an executable in PATH."""
    import shutil
    return shutil.which(cmd)


# -----------------------------------------------------------------------------
# Android Tool Registry
# -----------------------------------------------------------------------------

class AndroidToolRegistry:
    """
    Central Registry for Approved Android Interaction Tools.
    Enforces deterministic safety validation before any operation executes.
    """

    def __init__(
        self,
        safety_gate: Optional[AndroidSafetyGate] = None,
        adb_client: Optional[SafeAdbClient] = None,
        gradle_runner: Optional[SafeGradleRunner] = None,
        emulator_manager: Optional[SafeEmulatorManager] = None,
        window_manager: Optional[WindowManager] = None,
        audit_logger: Optional[AuditLogger] = None,
    ):
        self.safety = safety_gate or AndroidSafetyGate()
        self.adb = adb_client or SafeAdbClient()
        self.gradle = gradle_runner or SafeGradleRunner(project_dir=self.safety.authorized_project)
        self.emulator = emulator_manager or SafeEmulatorManager()
        self.window_manager = window_manager or WindowManager()
        self.audit = audit_logger or AuditLogger()
        self.discovery = AppDiscovery()

    # -------------------------------------------------------------------------
    # Core Dispatcher
    # -------------------------------------------------------------------------

    def execute_tool(
        self,
        tool_name: str,
        params: Optional[Dict[str, Any]] = None,
        user_confirmed: bool = False,
    ) -> AndroidToolResult:
        """
        Validates safety contracts, enforces rate limits, checks emergency stop,
        dispatches to deterministic handler, and writes audit record.
        """
        params = params or {}
        tool_name = (tool_name or "").strip()

        # 1. Validate tool allowlist & emergency stop
        try:
            self.safety.validate_tool_name(tool_name)
        except EmergencyStopActiveError:
            return self._audit_and_return(
                AndroidToolResult(
                    success=False,
                    tool=tool_name,
                    error="EMERGENCY STOP is active. All Android operations are frozen.",
                    error_code=AndroidErrorCode.EMERGENCY_STOPPED.value,
                    risk_level=RiskLevel.HIGH.value,
                )
            )
        except AndroidSafetyError as e:
            return self._audit_and_return(
                AndroidToolResult(
                    success=False,
                    tool=tool_name,
                    error=e.message,
                    error_code=e.code.value,
                    risk_level=RiskLevel.LOW.value,
                )
            )

        # 2. Enforce Rate Limit
        try:
            self.safety.rate_limiter.check_and_record()
        except RateLimitExceededError as rle:
            return self._audit_and_return(
                AndroidToolResult(
                    success=False,
                    tool=tool_name,
                    error=rle.message,
                    error_code=rle.code.value,
                    risk_level=RiskLevel.LOW.value,
                )
            )

        # 3. Assess Risk & Confirmation Gate
        risk, requires_confirmation = self.safety.assess_risk(tool_name, params)
        if requires_confirmation and not user_confirmed:
            return self._audit_and_return(
                AndroidToolResult(
                    success=False,
                    tool=tool_name,
                    data={"params": params},
                    message=f"Action '{tool_name}' is high risk and requires explicit user confirmation.",
                    error=f"Confirmation required for high-risk action: {tool_name}",
                    error_code=AndroidErrorCode.ACTION_NOT_ALLOWED.value,
                    risk_level=risk.value,
                    requires_confirmation=True,
                )
            )

        # 4. Dispatch to Specific Tool Implementation
        try:
            handler = self._get_handler(tool_name)
            result = handler(params)
            result.risk_level = risk.value
            return self._audit_and_return(result)
        except AndroidSafetyError as se:
            return self._audit_and_return(
                AndroidToolResult(
                    success=False,
                    tool=tool_name,
                    error=se.message,
                    error_code=se.code.value,
                    risk_level=risk.value,
                )
            )
        except Exception as ex:
            logger.exception(f"Unexpected error in tool '{tool_name}': {ex}")
            return self._audit_and_return(
                AndroidToolResult(
                    success=False,
                    tool=tool_name,
                    error=str(ex),
                    error_code=AndroidErrorCode.ACTION_NOT_ALLOWED.value,
                    risk_level=risk.value,
                )
            )

    def _get_handler(self, tool_name: str) -> Callable[[Dict[str, Any]], AndroidToolResult]:
        handlers: Dict[str, Callable[[Dict[str, Any]], AndroidToolResult]] = {
            "android.list_devices": self._tool_list_devices,
            "android.get_device_status": self._tool_get_device_status,
            "android.launch_studio": self._tool_launch_studio,
            "android.focus_studio": self._tool_focus_studio,
            "android.open_project": self._tool_open_project,
            "android.inspect_project": self._tool_inspect_project,
            "android.build_project": self._tool_build_project,
            "android.get_build_status": self._tool_get_build_status,
            "android.list_emulators": self._tool_list_emulators,
            "android.start_emulator": self._tool_start_emulator,
            "android.stop_emulator": self._tool_stop_emulator,
            "android.install_test_apk": self._tool_install_test_apk,
            "android.launch_app": self._tool_launch_app,
            "android.capture_log": self._tool_capture_log,
            "android.verify_app": self._tool_verify_app,
        }
        return handlers[tool_name]

    def _audit_and_return(self, result: AndroidToolResult) -> AndroidToolResult:
        try:
            self.audit.log_event(
                event_type="ANDROID_TOOL_EXECUTION",
                details={
                    "tool": result.tool,
                    "success": result.success,
                    "error_code": result.error_code,
                    "risk_level": result.risk_level,
                    "verified": result.verified,
                    "message": result.message or result.error,
                },
                status="SUCCESS" if result.success else "FAILED",
            )
        except Exception as ae:
            logger.warning(f"Failed to write audit log: {ae}")
        return result

    # -------------------------------------------------------------------------
    # Tool 1: android.list_devices
    # -------------------------------------------------------------------------
    def _tool_list_devices(self, params: Dict[str, Any]) -> AndroidToolResult:
        devices = self.adb.list_devices()
        authorized = []
        unauthorized = []

        for d in devices:
            serial = d["serial"]
            if serial in AUTHORIZED_DEVICE_SERIALS:
                authorized.append(d)
            else:
                unauthorized.append(d)

        return AndroidToolResult(
            success=True,
            tool="android.list_devices",
            data={
                "authorized_devices": authorized,
                "unauthorized_devices": unauthorized,
                "total_count": len(devices),
                "authorized_count": len(authorized),
            },
            message=f"Found {len(authorized)} authorized and {len(unauthorized)} unauthorized device(s).",
            verified=True,
        )

    # -------------------------------------------------------------------------
    # Tool 2: android.get_device_status
    # -------------------------------------------------------------------------
    def _tool_get_device_status(self, params: Dict[str, Any]) -> AndroidToolResult:
        serial = self.safety.validate_device_serial(params.get("serial", ""))
        state = self.adb.get_device_state(serial)

        if state == "offline":
            return AndroidToolResult(
                success=False,
                tool="android.get_device_status",
                data={"serial": serial, "state": state},
                error=f"Device '{serial}' is offline or not attached.",
                error_code=AndroidErrorCode.DEVICE_NOT_FOUND.value,
            )

        return AndroidToolResult(
            success=True,
            tool="android.get_device_status",
            data={"serial": serial, "state": state},
            message=f"Device '{serial}' state: {state}.",
            verified=True,
        )

    # -------------------------------------------------------------------------
    # Tool 3: android.launch_studio
    # -------------------------------------------------------------------------
    def _tool_launch_studio(self, params: Dict[str, Any]) -> AndroidToolResult:
        studio_path = self.discovery.find_application("android studio")
        if not studio_path:
            studio_path = DEFAULT_STUDIO_PATH if DEFAULT_STUDIO_PATH.exists() else None

        if not studio_path or not Path(studio_path).exists():
            raise AndroidSafetyError(
                AndroidErrorCode.ANDROID_STUDIO_NOT_FOUND,
                "Android Studio executable not found on host machine.",
            )

        # Launch safely without shell=True
        try:
            proc = subprocess.Popen(
                [str(studio_path)],
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return AndroidToolResult(
                success=True,
                tool="android.launch_studio",
                data={"path": str(studio_path), "pid": proc.pid},
                message=f"Launched Android Studio (PID: {proc.pid}).",
                verified=True,
            )
        except Exception as e:
            raise AndroidSafetyError(
                AndroidErrorCode.ACTION_NOT_ALLOWED,
                f"Failed to launch Android Studio: {e}",
            )

    # -------------------------------------------------------------------------
    # Tool 4: android.focus_studio
    # -------------------------------------------------------------------------
    def _tool_focus_studio(self, params: Dict[str, Any]) -> AndroidToolResult:
        success, msg = self.window_manager.activate_window("Android Studio")
        return AndroidToolResult(
            success=success,
            tool="android.focus_studio",
            message=msg,
            error=None if success else msg,
            error_code=None if success else AndroidErrorCode.ANDROID_STUDIO_NOT_FOUND.value,
            verified=success,
        )


    # -------------------------------------------------------------------------
    # Tool 5: android.open_project
    # -------------------------------------------------------------------------
    def _tool_open_project(self, params: Dict[str, Any]) -> AndroidToolResult:
        proj_dir = self.safety.validate_project_path(params.get("project_path"))
        studio_path = self.discovery.find_application("android studio") or DEFAULT_STUDIO_PATH

        if not Path(studio_path).exists():
            raise AndroidSafetyError(
                AndroidErrorCode.ANDROID_STUDIO_NOT_FOUND,
                "Android Studio executable not found.",
            )

        try:
            proc = subprocess.Popen(
                [str(studio_path), str(proj_dir)],
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return AndroidToolResult(
                success=True,
                tool="android.open_project",
                data={"project_path": str(proj_dir), "pid": proc.pid},
                message=f"Opened project '{proj_dir.name}' in Android Studio.",
                verified=True,
            )
        except Exception as e:
            raise AndroidSafetyError(
                AndroidErrorCode.ACTION_NOT_ALLOWED,
                f"Failed to open project in Android Studio: {e}",
            )

    # -------------------------------------------------------------------------
    # Tool 6: android.inspect_project
    # -------------------------------------------------------------------------
    def _tool_inspect_project(self, params: Dict[str, Any]) -> AndroidToolResult:
        proj_dir = self.safety.validate_project_path(params.get("project_path"))
        inspector = AndroidProjectInspector(proj_dir)
        info = inspector.inspect()

        if not info.get("is_android"):
            raise AndroidSafetyError(
                AndroidErrorCode.PROJECT_NOT_AUTHORIZED,
                f"Project at '{proj_dir}' is not a valid Android project.",
            )

        return AndroidToolResult(
            success=True,
            tool="android.inspect_project",
            data=info,
            message=f"Inspected Android project at '{proj_dir.name}' (Namespace: {info.get('app_module', {}).get('namespace')}).",
            verified=True,
        )

    # -------------------------------------------------------------------------
    # Tool 7: android.build_project
    # -------------------------------------------------------------------------
    def _tool_build_project(self, params: Dict[str, Any]) -> AndroidToolResult:
        action_name = params.get("action", "DEBUG_ASSEMBLE")
        action_tasks = self.safety.validate_build_action(action_name)

        build_res = self.gradle.run_action(action_tasks)
        success = build_res.get("success", False)

        return AndroidToolResult(
            success=success,
            tool="android.build_project",
            data=build_res,
            message=f"Gradle build '{action_name}' {'succeeded' if success else 'failed'} in {build_res.get('duration_s')}s.",
            error=None if success else f"Build task failed: {build_res.get('output_sample', '')[:200]}",
            error_code=None if success else AndroidErrorCode.BUILD_NOT_ALLOWED.value,
            verified=success,
        )

    # -------------------------------------------------------------------------
    # Tool 8: android.get_build_status
    # -------------------------------------------------------------------------
    def _tool_get_build_status(self, params: Dict[str, Any]) -> AndroidToolResult:
        apk_dir = (self.safety.authorized_project / "app" / "build" / "outputs" / "apk" / "debug").resolve()
        has_apk = False
        apk_info = {}

        if apk_dir.exists():
            apks = list(apk_dir.glob("*.apk"))
            if apks:
                has_apk = True
                apk_file = apks[0]
                stat = apk_file.stat()
                apk_info = {
                    "name": apk_file.name,
                    "path": str(apk_file),
                    "size_bytes": stat.st_size,
                    "last_modified": stat.st_mtime,
                }

        return AndroidToolResult(
            success=True,
            tool="android.get_build_status",
            data={
                "has_debug_apk": has_apk,
                "apk_info": apk_info,
                "build_dir": str(apk_dir),
            },
            message=f"Debug APK {'found: ' + apk_info.get('name', '') if has_apk else 'not found'}.",
            verified=True,
        )

    # -------------------------------------------------------------------------
    # Tool 9: android.list_emulators
    # -------------------------------------------------------------------------
    def _tool_list_emulators(self, params: Dict[str, Any]) -> AndroidToolResult:
        avds = self.emulator.list_avds()
        authorized = [a for a in avds if a in AUTHORIZED_AVDS]
        blocked = [a for a in avds if a in BLOCKED_AVDS]
        unauthorized = [a for a in avds if a not in AUTHORIZED_AVDS and a not in BLOCKED_AVDS]

        return AndroidToolResult(
            success=True,
            tool="android.list_emulators",
            data={
                "authorized_avds": authorized,
                "blocked_avds": blocked,
                "unauthorized_avds": unauthorized,
                "all_avds": avds,
            },
            message=f"Available AVDs: {authorized} (Authorized), {blocked} (Blocked).",
            verified=True,
        )

    # -------------------------------------------------------------------------
    # Tool 10: android.start_emulator
    # -------------------------------------------------------------------------
    def _tool_start_emulator(self, params: Dict[str, Any]) -> AndroidToolResult:
        avd_name = self.safety.validate_avd_name(params.get("avd_name", "Pixel_6_API_34"))
        start_res = self.emulator.start_emulator(avd_name)
        return AndroidToolResult(
            success=start_res["success"],
            tool="android.start_emulator",
            data=start_res,
            message=start_res["message"],
            verified=True,
        )

    # -------------------------------------------------------------------------
    # Tool 11: android.stop_emulator
    # -------------------------------------------------------------------------
    def _tool_stop_emulator(self, params: Dict[str, Any]) -> AndroidToolResult:
        serial = self.safety.validate_device_serial(params.get("serial", "emulator-5554"))
        if not serial.startswith("emulator-"):
            raise AndroidSafetyError(
                AndroidErrorCode.ACTION_NOT_ALLOWED,
                f"Cannot stop physical device '{serial}' via emu kill.",
            )

        success = self.adb.stop_emulator(serial)
        return AndroidToolResult(
            success=success,
            tool="android.stop_emulator",
            data={"serial": serial},
            message=f"Sent emu kill signal to '{serial}'.",
            verified=success,
        )

    # -------------------------------------------------------------------------
    # Tool 12: android.install_test_apk
    # -------------------------------------------------------------------------
    def _tool_install_test_apk(self, params: Dict[str, Any]) -> AndroidToolResult:
        serial = self.safety.validate_device_serial(params.get("serial", ""))
        raw_apk = params.get("apk_path")

        # If apk_path is omitted, attempt to use current build debug output
        if not raw_apk:
            default_apk = self.safety.authorized_project / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
            raw_apk = default_apk

        validated_apk = self.safety.validate_apk_path(raw_apk)
        success, msg = self.adb.install_apk(serial, validated_apk)

        return AndroidToolResult(
            success=success,
            tool="android.install_test_apk",
            data={"serial": serial, "apk_path": str(validated_apk), "output": msg},
            message=f"Installed APK to '{serial}': {msg}",
            error=None if success else msg,
            error_code=None if success else AndroidErrorCode.APK_NOT_AUTHORIZED.value,
            verified=success,
        )

    # -------------------------------------------------------------------------
    # Tool 13: android.launch_app
    # -------------------------------------------------------------------------
    def _tool_launch_app(self, params: Dict[str, Any]) -> AndroidToolResult:
        serial = self.safety.validate_device_serial(params.get("serial", ""))
        pkg = self.safety.validate_package_name(params.get("package_name", AUTHORIZED_PACKAGE_NAME))

        # Ensure package is installed first
        if not self.adb.is_package_installed(serial, pkg):
            return AndroidToolResult(
                success=False,
                tool="android.launch_app",
                data={"serial": serial, "package": pkg},
                error=f"Package '{pkg}' is not installed on device '{serial}'.",
                error_code=AndroidErrorCode.APP_NOT_FOUND.value,
            )

        success = self.adb.launch_package(serial, pkg)
        return AndroidToolResult(
            success=success,
            tool="android.launch_app",
            data={"serial": serial, "package": pkg},
            message=f"Launched '{pkg}' on device '{serial}'.",
            error=None if success else f"Failed to launch '{pkg}'.",
            error_code=None if success else AndroidErrorCode.ACTION_NOT_ALLOWED.value,
            verified=success,
        )

    # -------------------------------------------------------------------------
    # Tool 14: android.capture_log
    # -------------------------------------------------------------------------
    def _tool_capture_log(self, params: Dict[str, Any]) -> AndroidToolResult:
        serial = self.safety.validate_device_serial(params.get("serial", ""))
        lines = int(params.get("lines", 100))
        package_filter = params.get("package_name")
        logs = self.adb.capture_logcat(serial, lines=lines, filter_package=package_filter)

        return AndroidToolResult(
            success=True,
            tool="android.capture_log",
            data={
                "serial": serial,
                "lines_requested": lines,
                "package_filter": package_filter,
                "log_sample": logs[-2000:] if len(logs) > 2000 else logs,
            },
            message=f"Captured {len(logs.splitlines())} log lines from '{serial}'.",
            verified=True,
        )

    # -------------------------------------------------------------------------
    # Tool 15: android.verify_app
    # -------------------------------------------------------------------------
    def _tool_verify_app(self, params: Dict[str, Any]) -> AndroidToolResult:
        serial = self.safety.validate_device_serial(params.get("serial", ""))
        pkg = self.safety.validate_package_name(params.get("package_name", AUTHORIZED_PACKAGE_NAME))

        installed = self.adb.is_package_installed(serial, pkg)
        pid = self.adb.get_process_pid(serial, pkg) if installed else None

        return AndroidToolResult(
            success=True,
            tool="android.verify_app",
            data={
                "serial": serial,
                "package_name": pkg,
                "installed": installed,
                "is_running": (pid is not None),
                "pid": pid,
            },
            message=f"Package '{pkg}' on '{serial}': Installed={installed}, Running={pid is not None} (PID: {pid}).",
            verified=True,
        )
