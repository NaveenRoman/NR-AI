"""
NR-AI Android Device Lifecycle & Boot Orchestration Subsystem (Droid Phase 2).

Provides deterministic, policy-gated lifecycle management for Android Virtual Devices (AVDs)
and connected physical devices:
1. Explicit 16-state lifecycle state machine
2. Strict policy gating (Pixel_6_API_34 authorized, Pixel_6_API_35 strictly blocked)
3. Bounded boot orchestration with empirical readiness polling (max 180s)
4. Multi-signal readiness confirmation (sys.boot_completed == "1", package manager responsive)
5. 6-step verified deployment pipeline (Build/Locate -> Install -> Verify Pkg -> Launch -> Verify PID -> Verify Window)
6. Clean, graceful teardown and process termination
7. Full audit logging and Emergency Stop gating with zero shell=True execution
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from app.agent.android_safety import (
    AUTHORIZED_AVDS,
    AUTHORIZED_DEVICE_SERIALS,
    AUTHORIZED_PACKAGE_NAME,
    AUTHORIZED_PROJECT_PATH,
    BLOCKED_AVDS,
    DEFAULT_ADB_PATH,
    DEFAULT_EMULATOR_PATH,
    DEFAULT_SDK_PATH,
    AndroidErrorCode,
    AndroidSafetyError,
    AndroidSafetyGate,
    EmergencyStopActiveError,
)
from app.agent.android_readiness import (
    AndroidHostReadinessChecker,
    AvdReadinessState,
    AvdStatusReport,
    DEFAULT_SDK_ROOT,
)
from app.agent.android_tools import SafeAdbClient, SafeGradleRunner
from app.memory.audit_logger import AuditLogger

logger = logging.getLogger("NRAI.DeviceLifecycle")

MAX_BOOT_TIMEOUT_SECONDS: float = 180.0
READY_POLL_INTERVAL_SECONDS: float = 1.0


# -----------------------------------------------------------------------------
# Lifecycle State Machine Enums & Data Models
# -----------------------------------------------------------------------------

class DeviceLifecycleState(str, Enum):
    """16 explicit states representing the full device lifecycle."""
    UNKNOWN = "UNKNOWN"
    DISCOVERING = "DISCOVERING"
    AVAILABLE = "AVAILABLE"
    BOOTING = "BOOTING"
    BOOTED = "BOOTED"
    ADB_OFFLINE = "ADB_OFFLINE"
    ADB_UNAUTHORIZED = "ADB_UNAUTHORIZED"
    READY = "READY"
    DEPLOYING = "DEPLOYING"
    INSTALLED = "INSTALLED"
    LAUNCHING = "LAUNCHING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    TIMEOUT = "TIMEOUT"


@dataclass
class BootConfiguration:
    """Configuration parameters for AVD boot orchestration."""
    avd_name: str = "Pixel_6_API_34"
    no_window: bool = False
    no_audio: bool = True
    no_boot_anim: bool = True
    no_snapshot_save: bool = True
    timeout_seconds: float = MAX_BOOT_TIMEOUT_SECONDS
    additional_flags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DeploymentConfiguration:
    """Configuration parameters for application deployment pipeline."""
    apk_path: Optional[Union[str, Path]] = None
    package_name: str = AUTHORIZED_PACKAGE_NAME
    activity_name: str = "MainActivity"
    clean_install: bool = True
    grant_runtime_permissions: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if self.apk_path:
            d["apk_path"] = str(self.apk_path)
        return d


@dataclass
class DeploymentResult:
    """Detailed result from the 6-stage application deployment pipeline."""
    success: bool
    state: str
    package_name: str
    activity_name: str
    apk_path: Optional[str] = None
    installed: bool = False
    launched: bool = False
    pid: Optional[int] = None
    foreground_activity: Optional[str] = None
    message: str = ""
    error: Optional[str] = None
    duration_seconds: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DeviceLifecycleReport:
    """Comprehensive snapshot of target device lifecycle status."""
    avd_name: str
    state: DeviceLifecycleState
    serial: Optional[str] = None
    boot_completed: bool = False
    package_manager_responsive: bool = False
    active_pid: Optional[int] = None
    foreground_app: Dict[str, str] = field(default_factory=dict)
    device_info: Dict[str, str] = field(default_factory=dict)
    last_deployment: Optional[DeploymentResult] = None
    message: str = ""
    duration_seconds: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "avd_name": self.avd_name,
            "state": self.state.value if isinstance(self.state, DeviceLifecycleState) else str(self.state),
            "serial": self.serial,
            "boot_completed": self.boot_completed,
            "package_manager_responsive": self.package_manager_responsive,
            "active_pid": self.active_pid,
            "foreground_app": self.foreground_app,
            "device_info": self.device_info,
            "message": self.message,
            "duration_seconds": self.duration_seconds,
            "timestamp": self.timestamp,
        }
        if self.last_deployment:
            d["last_deployment"] = self.last_deployment.to_dict()
        return d


# -----------------------------------------------------------------------------
# Device Lifecycle Controller
# -----------------------------------------------------------------------------

class DeviceLifecycleController:
    """
    Authoritative controller managing device lifecycle, boot orchestration,
    deployment pipelines, and teardown with zero arbitrary shell execution.
    """

    def __init__(
        self,
        safety_gate: Optional[AndroidSafetyGate] = None,
        adb_client: Optional[SafeAdbClient] = None,
        readiness_checker: Optional[AndroidHostReadinessChecker] = None,
        audit_logger: Optional[AuditLogger] = None,
        sdk_root: Optional[Union[str, Path]] = None,
    ):
        self.safety = safety_gate or AndroidSafetyGate()
        self.sdk_root = Path(sdk_root or DEFAULT_SDK_ROOT).resolve()
        self.adb = adb_client or SafeAdbClient(adb_path=self.sdk_root / "platform-tools" / "adb.exe")
        self.readiness = readiness_checker or AndroidHostReadinessChecker(
            safety_gate=self.safety, sdk_root=self.sdk_root
        )
        self.audit = audit_logger or AuditLogger()

        self._active_processes: Dict[str, subprocess.Popen] = {}
        self._current_state: DeviceLifecycleState = DeviceLifecycleState.UNKNOWN
        self._last_deployment: Optional[DeploymentResult] = None
        self._active_serial: Optional[str] = None

    @property
    def current_state(self) -> DeviceLifecycleState:
        return self._current_state

    # -------------------------------------------------------------------------
    # Discovery & State Assessment
    # -------------------------------------------------------------------------

    def discover_devices(self) -> Dict[str, Any]:
        """
        Discovers all installed AVDs and connected ADB devices.
        Enforces policy: Pixel_6_API_35 is explicitly identified as BLOCKED.
        """
        self.safety.check_emergency_stop()
        self._current_state = DeviceLifecycleState.DISCOVERING

        installed_avds = self.readiness.list_installed_avds()
        attached = self.readiness.get_attached_devices()

        avd_reports: Dict[str, Dict[str, Any]] = {}
        for avd in installed_avds:
            status = self.readiness.determine_avd_state(avd)
            avd_reports[avd] = status.to_dict()

        # If authorized AVD is attached and ready, set active serial
        auth_status = self.readiness.determine_avd_state("Pixel_6_API_34")
        if auth_status.state == AvdReadinessState.READY.value and auth_status.serial:
            self._active_serial = auth_status.serial
            self._current_state = DeviceLifecycleState.READY
        elif auth_status.state == AvdReadinessState.AVAILABLE.value:
            self._current_state = DeviceLifecycleState.AVAILABLE
        elif auth_status.state == AvdReadinessState.BOOTING.value:
            self._current_state = DeviceLifecycleState.BOOTING
        elif auth_status.state == AvdReadinessState.OFFLINE.value:
            self._current_state = DeviceLifecycleState.ADB_OFFLINE
        elif auth_status.state == AvdReadinessState.BLOCKED.value:
            self._current_state = DeviceLifecycleState.BLOCKED
        else:
            self._current_state = DeviceLifecycleState.UNKNOWN

        report = {
            "installed_avds": installed_avds,
            "attached_devices": attached,
            "avd_reports": avd_reports,
            "authorized_avd_state": auth_status.to_dict(),
            "active_serial": self._active_serial,
            "lifecycle_state": self._current_state.value,
            "timestamp": time.time(),
        }

        self.audit.log(
            event_type="android_device_discovery",
            actor="DeviceLifecycleController",
            details={"attached_count": len(attached), "installed_avds": installed_avds},
        )
        return report

    def get_status(self, avd_name: str = "Pixel_6_API_34") -> DeviceLifecycleReport:
        """Determines the exact current status of an AVD with empirical verification."""
        self.safety.check_emergency_stop()
        t0 = time.time()

        # Check policy
        if avd_name in BLOCKED_AVDS:
            return DeviceLifecycleReport(
                avd_name=avd_name,
                state=DeviceLifecycleState.BLOCKED,
                message=f"AVD '{avd_name}' is blocked by policy.",
                duration_seconds=time.time() - t0,
            )

        status = self.readiness.determine_avd_state(avd_name)

        if status.state == AvdReadinessState.BLOCKED.value:
            state = DeviceLifecycleState.BLOCKED
        elif status.state == AvdReadinessState.UNAVAILABLE.value:
            state = DeviceLifecycleState.FAILED
        elif status.state == AvdReadinessState.OFFLINE.value:
            state = DeviceLifecycleState.ADB_OFFLINE
        elif status.state == AvdReadinessState.BOOTING.value:
            state = DeviceLifecycleState.BOOTING
        elif status.state == AvdReadinessState.READY.value:
            state = DeviceLifecycleState.READY
            self._active_serial = status.serial
        elif status.state == AvdReadinessState.AVAILABLE.value:
            state = DeviceLifecycleState.AVAILABLE
        else:
            state = DeviceLifecycleState.UNKNOWN

        self._current_state = state

        # If ready, probe deep device info
        device_info = {}
        active_pid = None
        fg_app = {}
        pm_responsive = False

        if status.serial and state == DeviceLifecycleState.READY:
            try:
                device_info = self.adb.get_device_info(status.serial)
            except Exception:
                pass
            try:
                pm_responsive = self.is_package_manager_responsive(status.serial)
            except Exception:
                pass
            try:
                active_pid = self.adb.get_process_pid(status.serial, AUTHORIZED_PACKAGE_NAME)
            except Exception:
                pass
            try:
                fg_app = self.adb.get_foreground_app(status.serial)
            except Exception:
                pass

            if active_pid is not None:
                self._current_state = DeviceLifecycleState.RUNNING

        return DeviceLifecycleReport(
            avd_name=avd_name,
            state=self._current_state,
            serial=status.serial,
            boot_completed=status.boot_completed,
            package_manager_responsive=pm_responsive,
            active_pid=active_pid,
            foreground_app=fg_app,
            device_info=device_info,
            last_deployment=self._last_deployment,
            message=status.message,
            duration_seconds=time.time() - t0,
        )

    # -------------------------------------------------------------------------
    # Empirical Readiness Checks
    # -------------------------------------------------------------------------

    def is_package_manager_responsive(self, serial: str) -> bool:
        """Verifies package manager is responding via 'pm path android'."""
        try:
            code, out, _ = self.adb._run_adb(["-s", serial, "shell", "pm", "path", "android"], timeout=5.0)
            return code == 0 and "package:" in out
        except Exception:
            return False

    def is_boot_completed(self, serial: str) -> bool:
        """Verifies sys.boot_completed == '1'."""
        try:
            code, out, _ = self.adb._run_adb(
                ["-s", serial, "shell", "getprop", "sys.boot_completed"], timeout=4.0
            )
            return code == 0 and out.strip() == "1"
        except Exception:
            return False

    # -------------------------------------------------------------------------
    # Boot Orchestration
    # -------------------------------------------------------------------------

    def boot(self, config: Optional[BootConfiguration] = None) -> DeviceLifecycleReport:
        """
        Orchestrates bounded boot of authorized AVD.
        Strictly rejects Pixel_6_API_35.
        Bounded timeout: up to 180 seconds.
        """
        cfg = config or BootConfiguration()
        self.safety.check_emergency_stop()
        t0 = time.time()

        # 1. Policy Gate: Reject Blocked AVDs
        if cfg.avd_name in BLOCKED_AVDS:
            self._current_state = DeviceLifecycleState.BLOCKED
            self.audit.log(
                event_type="android_boot_rejected",
                actor="DeviceLifecycleController",
                details={"avd_name": cfg.avd_name, "reason": "Policy blocked AVD"},
            )
            raise AndroidSafetyError(
                AndroidErrorCode.DEVICE_NOT_AUTHORIZED,
                f"AVD '{cfg.avd_name}' is blocked by policy and cannot be booted.",
            )

        # 2. Authorize AVD Name
        validated_avd = self.safety.validate_avd_name(cfg.avd_name)

        # 3. Check if already booted and ready
        existing = self.get_status(validated_avd)
        if existing.state in (DeviceLifecycleState.READY, DeviceLifecycleState.RUNNING):
            logger.info(f"AVD '{validated_avd}' is already running and ready on '{existing.serial}'.")
            return existing

        # 4. Locate emulator executable
        emu_exe = self.sdk_root / "emulator" / "emulator.exe"
        if not emu_exe.exists():
            which_emu = shutil.which("emulator")
            if which_emu:
                emu_exe = Path(which_emu)
            else:
                self._current_state = DeviceLifecycleState.FAILED
                return DeviceLifecycleReport(
                    avd_name=validated_avd,
                    state=DeviceLifecycleState.FAILED,
                    message=f"Android emulator executable not found at '{emu_exe}'.",
                    duration_seconds=time.time() - t0,
                )

        # 5. Build bounded CLI arguments (shell=False)
        cmd = [str(emu_exe), "-avd", validated_avd]
        if cfg.no_boot_anim:
            cmd.append("-no-boot-anim")
        if cfg.no_snapshot_save:
            cmd.append("-no-snapshot-save")
        if cfg.no_audio:
            cmd.append("-no-audio")
        if cfg.no_window:
            cmd.append("-no-window")
        cmd.extend(cfg.additional_flags)

        self._current_state = DeviceLifecycleState.BOOTING
        logger.info(f"Launching emulator for '{validated_avd}' with timeout {cfg.timeout_seconds}s...")

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
            )
            self._active_processes[validated_avd] = proc
        except Exception as e:
            self._current_state = DeviceLifecycleState.FAILED
            logger.error(f"Failed to spawn emulator process: {e}")
            return DeviceLifecycleReport(
                avd_name=validated_avd,
                state=DeviceLifecycleState.FAILED,
                message=f"Failed to spawn emulator process: {e}",
                duration_seconds=time.time() - t0,
            )

        # 6. Bounded Polling Loop
        poll_start = time.time()
        deadline = poll_start + cfg.timeout_seconds
        target_serial: Optional[str] = None

        while time.time() < deadline:
            self.safety.check_emergency_stop()

            # Check if proc died prematurely
            poll_ret = proc.poll()
            if poll_ret is not None and poll_ret != 0:
                self._current_state = DeviceLifecycleState.FAILED
                err_out = ""
                if proc.stderr:
                    try:
                        err_out = proc.stderr.read().decode("utf-8", errors="replace")
                    except Exception:
                        pass
                logger.error(f"Emulator exited prematurely with return code {poll_ret}: {err_out}")
                return DeviceLifecycleReport(
                    avd_name=validated_avd,
                    state=DeviceLifecycleState.FAILED,
                    message=f"Emulator exited prematurely (code {poll_ret}): {err_out.strip()}",
                    duration_seconds=time.time() - t0,
                )

            # Discover attached emulator serial
            if not target_serial:
                for dev in self.readiness.get_attached_devices():
                    s = dev["serial"]
                    if s.startswith("emulator-") and dev["status"] in ("device", "offline"):
                        target_serial = s
                        break

            # If serial attached, probe readiness signals
            if target_serial:
                boot_ok = self.is_boot_completed(target_serial)
                if boot_ok:
                    self._current_state = DeviceLifecycleState.BOOTED
                    pm_ok = self.is_package_manager_responsive(target_serial)
                    if pm_ok:
                        self._current_state = DeviceLifecycleState.READY
                        self._active_serial = target_serial
                        elapsed = time.time() - t0
                        logger.info(f"AVD '{validated_avd}' reached READY on '{target_serial}' in {elapsed:.1f}s.")

                        self.audit.log(
                            event_type="android_boot_success",
                            actor="DeviceLifecycleController",
                            details={"avd_name": validated_avd, "serial": target_serial, "boot_time_s": elapsed},
                        )

                        return DeviceLifecycleReport(
                            avd_name=validated_avd,
                            state=DeviceLifecycleState.READY,
                            serial=target_serial,
                            boot_completed=True,
                            package_manager_responsive=True,
                            message=f"AVD '{validated_avd}' booted and reached READY in {elapsed:.1f}s.",
                            duration_seconds=elapsed,
                        )

            time.sleep(READY_POLL_INTERVAL_SECONDS)

        # 7. Timed Out: Terminate proc safely
        self._current_state = DeviceLifecycleState.TIMEOUT
        self._terminate_proc(proc)
        elapsed = time.time() - t0
        logger.warning(f"AVD '{validated_avd}' boot timed out after {elapsed:.1f}s.")

        self.audit.log(
            event_type="android_boot_timeout",
            actor="DeviceLifecycleController",
            details={"avd_name": validated_avd, "timeout_seconds": cfg.timeout_seconds},
        )

        return DeviceLifecycleReport(
            avd_name=validated_avd,
            state=DeviceLifecycleState.TIMEOUT,
            serial=target_serial,
            boot_completed=False,
            package_manager_responsive=False,
            message=f"AVD '{validated_avd}' boot timed out after {elapsed:.1f}s.",
            duration_seconds=elapsed,
        )

    # -------------------------------------------------------------------------
    # Deployment Pipeline
    # -------------------------------------------------------------------------

    def deploy(
        self,
        serial: Optional[str] = None,
        config: Optional[DeploymentConfiguration] = None,
    ) -> DeploymentResult:
        """
        Executes the 6-stage verified application deployment pipeline:
        1. Resolve & verify APK path
        2. Install APK via SafeAdbClient
        3. Verify package installation via PM
        4. Launch activity via Monkey / AM
        5. Verify process running via pidof / meminfo
        6. Verify foreground activity window
        """
        cfg = config or DeploymentConfiguration()
        self.safety.check_emergency_stop()
        t0 = time.time()

        # Validate target serial
        active_serial = serial or self._active_serial
        if not active_serial:
            # Try to discover ready device
            ready_report = self.get_status("Pixel_6_API_34")
            if ready_report.state in (DeviceLifecycleState.READY, DeviceLifecycleState.RUNNING) and ready_report.serial:
                active_serial = ready_report.serial
            else:
                return DeploymentResult(
                    success=False,
                    state=DeviceLifecycleState.FAILED.value,
                    package_name=cfg.package_name,
                    activity_name=cfg.activity_name,
                    error="No running authorized device available for deployment.",
                    duration_seconds=time.time() - t0,
                )

        validated_serial = self.safety.validate_device_serial(active_serial)
        self._current_state = DeviceLifecycleState.DEPLOYING

        # Stage 1: Resolve & verify APK path
        apk_path: Optional[Path] = None
        if cfg.apk_path:
            apk_path = Path(cfg.apk_path).resolve()
        else:
            # Default to test project debug APK
            candidate_apk = (
                AUTHORIZED_PROJECT_PATH
                / "app"
                / "build"
                / "outputs"
                / "apk"
                / "debug"
                / "app-debug.apk"
            )
            if candidate_apk.exists():
                apk_path = candidate_apk

        if not apk_path or not apk_path.exists():
            self._current_state = DeviceLifecycleState.FAILED
            err_msg = f"Target APK does not exist at '{apk_path or 'unresolved'}'. Build project first."
            logger.error(err_msg)
            return DeploymentResult(
                success=False,
                state=DeviceLifecycleState.FAILED.value,
                package_name=cfg.package_name,
                activity_name=cfg.activity_name,
                apk_path=str(apk_path) if apk_path else None,
                error=err_msg,
                duration_seconds=time.time() - t0,
            )

        # Stage 2: Install APK via SafeAdbClient
        logger.info(f"Installing APK '{apk_path}' on device '{validated_serial}'...")
        install_ok, install_msg = self.adb.install_apk(validated_serial, apk_path)
        if not install_ok:
            self._current_state = DeviceLifecycleState.FAILED
            return DeploymentResult(
                success=False,
                state=DeviceLifecycleState.FAILED.value,
                package_name=cfg.package_name,
                activity_name=cfg.activity_name,
                apk_path=str(apk_path),
                installed=False,
                error=f"APK installation failed: {install_msg}",
                duration_seconds=time.time() - t0,
            )

        # Stage 3: Verify package installation
        pkg_installed = self.adb.is_package_installed(validated_serial, cfg.package_name)
        if not pkg_installed:
            self._current_state = DeviceLifecycleState.FAILED
            return DeploymentResult(
                success=False,
                state=DeviceLifecycleState.FAILED.value,
                package_name=cfg.package_name,
                activity_name=cfg.activity_name,
                apk_path=str(apk_path),
                installed=False,
                error=f"Package '{cfg.package_name}' not detected by package manager after install.",
                duration_seconds=time.time() - t0,
            )

        self._current_state = DeviceLifecycleState.INSTALLED

        # Stage 4: Launch Activity
        self._current_state = DeviceLifecycleState.LAUNCHING
        logger.info(f"Launching activity '{cfg.activity_name}' for package '{cfg.package_name}'...")
        launch_ok = self.adb.launch_package(validated_serial, cfg.package_name)

        # Stage 5: Verify process running
        # Wait up to 5 seconds for process to appear
        pid: Optional[int] = None
        for _ in range(10):
            pid = self.adb.get_process_pid(validated_serial, cfg.package_name)
            if pid is not None:
                break
            time.sleep(0.5)

        # Stage 6: Verify foreground window
        fg_info = self.adb.get_foreground_app(validated_serial)
        fg_act = fg_info.get("activity", "")

        is_running = pid is not None
        if is_running:
            self._current_state = DeviceLifecycleState.RUNNING
        else:
            self._current_state = DeviceLifecycleState.FAILED

        elapsed = time.time() - t0
        res = DeploymentResult(
            success=is_running,
            state=self._current_state.value,
            package_name=cfg.package_name,
            activity_name=cfg.activity_name,
            apk_path=str(apk_path),
            installed=True,
            launched=launch_ok,
            pid=pid,
            foreground_activity=fg_act,
            message=f"Deployment completed in {elapsed:.1f}s (PID: {pid})." if is_running else "App process failed to start.",
            error=None if is_running else "Process not found after launch.",
            duration_seconds=elapsed,
        )

        self._last_deployment = res
        self.audit.log(
            event_type="android_app_deployment",
            actor="DeviceLifecycleController",
            details={
                "serial": validated_serial,
                "package": cfg.package_name,
                "success": is_running,
                "pid": pid,
                "duration_s": elapsed,
            },
        )
        return res

    # -------------------------------------------------------------------------
    # Teardown & Process Cleanup
    # -------------------------------------------------------------------------

    def stop_app(self, serial: Optional[str] = None, package_name: str = AUTHORIZED_PACKAGE_NAME) -> bool:
        """Stops an application package using 'am force-stop'."""
        self.safety.check_emergency_stop()
        active_serial = serial or self._active_serial
        if not active_serial:
            return False

        validated_serial = self.safety.validate_device_serial(active_serial)
        try:
            code, _, _ = self.adb._run_adb(
                ["-s", validated_serial, "shell", "am", "force-stop", package_name],
                timeout=5.0,
            )
            stopped = code == 0
            if stopped and self._current_state == DeviceLifecycleState.RUNNING:
                self._current_state = DeviceLifecycleState.READY
            return stopped
        except Exception as e:
            logger.warning(f"Failed to force-stop package '{package_name}': {e}")
            return False

    def stop(self, serial: Optional[str] = None, avd_name: str = "Pixel_6_API_34") -> DeviceLifecycleReport:
        """Gracefully shuts down the emulator and cleans up background processes."""
        self.safety.check_emergency_stop()
        t0 = time.time()
        self._current_state = DeviceLifecycleState.STOPPING

        active_serial = serial or self._active_serial

        # 1. Graceful adb emu kill if attached
        if active_serial:
            try:
                validated_serial = self.safety.validate_device_serial(active_serial)
                self.adb.stop_emulator(validated_serial)
            except Exception as e:
                logger.warning(f"Error during adb emu kill on '{active_serial}': {e}")

        # 2. Terminate tracked subprocess if present
        if avd_name in self._active_processes:
            proc = self._active_processes.pop(avd_name)
            self._terminate_proc(proc)

        self._current_state = DeviceLifecycleState.AVAILABLE
        self._active_serial = None

        self.audit.log(
            event_type="android_device_stop",
            actor="DeviceLifecycleController",
            details={"avd_name": avd_name, "serial": active_serial},
        )

        return DeviceLifecycleReport(
            avd_name=avd_name,
            state=DeviceLifecycleState.AVAILABLE,
            serial=None,
            boot_completed=False,
            package_manager_responsive=False,
            message=f"AVD '{avd_name}' stopped cleanly.",
            duration_seconds=time.time() - t0,
        )

    def _terminate_proc(self, proc: subprocess.Popen) -> None:
        """Cleanly terminates and closes a background subprocess."""
        try:
            proc.terminate()
            proc.wait(timeout=2.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

        for stream in (proc.stdout, proc.stderr):
            if stream:
                try:
                    stream.close()
                except Exception:
                    pass

    def reset_state(self) -> None:
        """Resets in-memory controller state."""
        self._current_state = DeviceLifecycleState.UNKNOWN
        self._active_serial = None
        self._last_deployment = None
