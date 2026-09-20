"""
NR-AI Droid Child Specialists & Controlled Engineering Context.

Architecture:
        NR-AI (Sun)
           |
         Droid (Earth)
          / \
         /   \
Droid Scout   Droid Guardian

Hierarchy Invariants:
1. Parent/Owner is Droid (android_unified_agent).
2. Droid Scout and Droid Guardian communicate with Droid, NEVER directly as top-level agents to NR-AI.
3. Droid Scout: Android Studio Watch & Development Assistant (OBSERVE, ANALYZE, ADVISE).
   MUST NOT independently modify files.
4. Droid Guardian: Android Build & Verification Guardian (BUILD -> VERIFY -> FAILURE? -> DIAGNOSIS -> REPAIR -> VERIFY -> SUCCESS).
   MUST NOT silently modify the project. Deterministic evidence priority over model claims.
5. Shared Controlled Context: DroidContext (active_project, studio state, Gradle state, device state, task state, build evidence, test evidence, repair history).
6. Model Isolation: All three agents remain behind ModelIsolationGate (no arbitrary OS/shell access).
"""

from dataclasses import asdict, dataclass, field
from enum import Enum
import logging
import os
from pathlib import Path
import re
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("NRAI.DroidChildAgents")


# -----------------------------------------------------------------------------
# 1. Controlled Shared Context: DroidContext
# -----------------------------------------------------------------------------

@dataclass
class DroidContext:
    """
    Controlled shared context for Droid and its child agents (Scout and Guardian).
    Child agents share ONLY the Android engineering context relevant to Droid.
    """
    active_project: Optional[str] = "NR-AI"
    project_path: Optional[str] = r"C:\NR-AI\dev_projects\NR-AI"
    android_studio_state: Dict[str, Any] = field(default_factory=lambda: {
        "is_running": False,
        "pid": None,
        "window_title": "",
        "workspace_open": False,
        "project_view_files": [],
        "last_checked": 0.0,
    })
    gradle_state: Dict[str, Any] = field(default_factory=lambda: {
        "last_build_status": "UNKNOWN",
        "last_build_time": 0.0,
        "tasks_run": [],
        "error_lines": [],
        "compiler_errors": [],
        "warning_count": 0,
        "apk_path": None,
        "apk_size_bytes": 0,
    })
    device_state: Dict[str, Any] = field(default_factory=lambda: {
        "connected_devices": [],
        "active_serial": "emulator-5554",
        "device_model": "Pixel_6_API_34",
        "is_booted": True,
        "running_pid": None,
        "foreground_activity": None,
        "last_screenshot": None,
    })
    task_state: Dict[str, Any] = field(default_factory=lambda: {
        "current_task": "Idle",
        "current_stage": "READY",
        "progress_pct": 100.0,
        "history": [],
    })
    build_evidence: Dict[str, Any] = field(default_factory=dict)
    test_evidence: Dict[str, Any] = field(default_factory=lambda: {
        "junit_passed": 0,
        "junit_failed": 0,
        "lint_issues": [],
        "readiness_verdict": "UNKNOWN",
    })
    repair_history: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def update_studio_state(self, is_running: bool, pid: Optional[int], window_title: str = "", files: Optional[List[str]] = None) -> None:
        self.android_studio_state["is_running"] = is_running
        self.android_studio_state["pid"] = pid
        self.android_studio_state["window_title"] = window_title
        if files is not None:
            self.android_studio_state["project_view_files"] = list(files)
        self.android_studio_state["last_checked"] = time.time()

    def update_gradle_state(self, status: str, errors: Optional[List[str]] = None, apk_path: Optional[str] = None, apk_size: int = 0) -> None:
        self.gradle_state["last_build_status"] = status
        self.gradle_state["last_build_time"] = time.time()
        if errors is not None:
            self.gradle_state["error_lines"] = list(errors)
        if apk_path:
            self.gradle_state["apk_path"] = apk_path
            self.gradle_state["apk_size_bytes"] = apk_size

    def update_device_state(self, serial: str, booted: bool, pid: Optional[int] = None, foreground_act: Optional[str] = None, screenshot: Optional[str] = None) -> None:
        self.device_state["active_serial"] = serial
        self.device_state["is_booted"] = booted
        if pid is not None:
            self.device_state["running_pid"] = pid
        if foreground_act is not None:
            self.device_state["foreground_activity"] = foreground_act
        if screenshot is not None:
            self.device_state["last_screenshot"] = screenshot

    def record_repair(self, defect_desc: str, repair_action: str, verified_success: bool, evidence: Optional[Dict[str, Any]] = None) -> None:
        self.repair_history.append({
            "timestamp": time.time(),
            "time_display": time.strftime("%Y-%m-%d %H:%M:%S"),
            "defect": defect_desc,
            "repair_action": repair_action,
            "verified_success": verified_success,
            "evidence": evidence or {},
        })


# -----------------------------------------------------------------------------
# 2. Droid Scout: Android Studio Watch & Development Assistant
# -----------------------------------------------------------------------------

class DroidScoutAgent:
    """
    Droid Scout: Android Studio Watch & Development Assistant.
    Satellite agent orbiting Droid.
    Role: Observe, Analyze, Advise Droid.
    MUST NOT independently modify files.
    """

    def __init__(self, context: Optional[DroidContext] = None):
        self.agent_id = "droid_scout"
        self.name = "Droid Scout"
        self.role = "Android Studio Watch & Development Assistant"
        self.parent_agent_id = "android_unified_agent"
        self.context = context or DroidContext()
        self.capabilities = [
            "droid.scout.observe",
            "droid.scout.analyze",
            "droid.scout.advise",
            "droid.scout.locate_resources",
            "droid.scout.inspect_workspace",
        ]
        self.last_observation: Dict[str, Any] = {}

    def observe(self, project_path: Optional[str] = None) -> Dict[str, Any]:
        target_path = project_path or self.context.project_path or r"C:\NR-AI\dev_projects\NR-AI"
        p = Path(target_path)

        exists = p.exists() and p.is_dir()
        found_files = []
        project_type = "UNKNOWN"
        has_gradle = False
        has_manifest = False
        main_activity_file = None

        if exists:
            for rel in [
                "build.gradle.kts", "build.gradle",
                "settings.gradle.kts", "settings.gradle",
                "app/build.gradle.kts", "app/build.gradle",
                "app/src/main/AndroidManifest.xml",
                "app/src/main/res/layout/activity_main.xml",
                "app/src/main/java/com/nrai/nrai/MainActivity.java",
                "app/src/main/java/com/nrai/nrai/MainActivity.kt",
            ]:
                check_p = p / rel
                if check_p.exists():
                    found_files.append(rel)
                    if "MainActivity.java" in rel:
                        main_activity_file = str(check_p)
                        project_type = "Java Android"
                    elif "MainActivity.kt" in rel:
                        main_activity_file = str(check_p)
                        project_type = "Kotlin Android"
                    if "gradle" in rel:
                        has_gradle = True
                    if "AndroidManifest.xml" in rel:
                        has_manifest = True

        studio_running = False
        studio_pids = []
        try:
            import psutil
            for proc in psutil.process_iter(["pid", "name"]):
                name = (proc.info.get("name") or "").lower()
                if name in ("studio64.exe", "studio.exe"):
                    studio_running = True
                    studio_pids.append(proc.info.get("pid"))
        except Exception as e:
            logger.warning(f"Error checking studio process: {e}")

        observation = {
            "timestamp": time.time(),
            "time_display": time.strftime("%H:%M:%S"),
            "project_path": str(target_path),
            "project_exists": exists,
            "project_type": project_type,
            "has_gradle": has_gradle,
            "has_manifest": has_manifest,
            "main_activity_file": main_activity_file,
            "verified_files": found_files,
            "studio_running": studio_running,
            "studio_pids": studio_pids,
            "status": "ONLINE",
        }
        self.last_observation = observation
        if exists:
            self.context.active_project = p.name
            self.context.project_path = str(p)
            self.context.update_studio_state(
                is_running=studio_running,
                pid=studio_pids[0] if studio_pids else None,
                window_title=f"{p.name} [{str(p)}]" if studio_running else "",
                files=found_files,
            )
        return observation

    def analyze(self) -> Dict[str, Any]:
        obs = self.last_observation or self.observe()
        issues = []
        recommendations = []

        if not obs.get("project_exists"):
            issues.append(f"Target project directory does not exist: {obs.get('project_path')}")
            recommendations.append("Scaffold a new Android project using Java/Kotlin standard layout.")
        else:
            if not obs.get("has_gradle"):
                issues.append("Missing root or app build.gradle / build.gradle.kts.")
                recommendations.append("Generate compliant build.gradle.kts and settings.gradle.kts.")
            if not obs.get("has_manifest"):
                issues.append("Missing AndroidManifest.xml.")
                recommendations.append("Create AndroidManifest.xml declaring package and MainActivity.")
            if not obs.get("main_activity_file"):
                issues.append("No MainActivity found under src/main/java.")
                recommendations.append("Create MainActivity.java or MainActivity.kt with standard onCreate layout binding.")

        analysis = {
            "timestamp": time.time(),
            "healthy": len(issues) == 0,
            "issues": issues,
            "recommendations": recommendations,
            "files_count": len(obs.get("verified_files", [])),
            "project_type": obs.get("project_type", "UNKNOWN"),
        }
        return analysis

    def advise(self, intent: Optional[str] = None) -> Dict[str, Any]:
        obs = self.last_observation or self.observe()
        analysis = self.analyze()

        if not obs.get("project_exists"):
            action = "scaffold_project"
            advice_text = "Project directory not found. Suggest initializing a clean Java Android project."
        elif not analysis.get("healthy"):
            action = "repair_structure"
            advice_text = f"Project structure incomplete: {', '.join(analysis['issues'])}. Suggest creating missing files first."
        elif not obs.get("studio_running"):
            action = "launch_studio"
            advice_text = "Project structure verified. Android Studio is not running; suggest launching Studio to open the project."
        else:
            action = "build_and_deploy"
            advice_text = "Workspace is healthy and Studio is active. Ready to assembleDebug and deploy to emulator."

        return {
            "advisor": self.name,
            "parent": "Droid",
            "suggested_action": action,
            "advice": advice_text,
            "project": obs.get("project_path"),
            "timestamp": time.time(),
        }

    def locate_resources(self, pattern: str) -> List[str]:
        p = Path(self.context.project_path or r"C:\NR-AI\dev_projects\NR-AI")
        if not p.exists():
            return []
        matches = []
        try:
            for item in p.rglob(f"*{pattern}*"):
                if item.is_file():
                    matches.append(str(item.relative_to(p)))
        except Exception as e:
            logger.warning(f"Error locating resources: {e}")
        return matches[:20]


# -----------------------------------------------------------------------------
# 3. Droid Guardian: Android Build & Verification Guardian
# -----------------------------------------------------------------------------

class DroidGuardianAgent:
    """
    Droid Guardian: Android Build & Verification Guardian.
    Independent verification partner orbiting Droid.
    Closed Feedback Loop:
    BUILD -> VERIFY -> FAILURE? -> GUARDIAN DIAGNOSIS -> DROID REPAIR -> BUILD -> GUARDIAN VERIFY -> SUCCESS
    Deterministic evidence priority over model claims.
    MUST NOT silently modify the project.
    """

    def __init__(self, context: Optional[DroidContext] = None):
        self.agent_id = "droid_guardian"
        self.name = "Droid Guardian"
        self.role = "Android Build & Verification Guardian"
        self.parent_agent_id = "android_unified_agent"
        self.context = context or DroidContext()
        self.capabilities = [
            "droid.guardian.verify_build",
            "droid.guardian.diagnose_failure",
            "droid.guardian.verify_repair",
            "droid.guardian.inspect_runtime",
            "droid.guardian.inspect_adb",
        ]
        self.last_verification: Dict[str, Any] = {}

    def monitor_gradle_build(self, build_output: Any, exit_code: Optional[int] = None) -> Dict[str, Any]:
        if isinstance(build_output, int) and isinstance(exit_code, str):
            build_output, exit_code = exit_code, build_output
        elif isinstance(build_output, int) and exit_code is None:
            exit_code = build_output
            build_output = ""
        if exit_code is None:
            exit_code = 0
        build_output = str(build_output or "")
        is_success = (exit_code == 0) and ("BUILD SUCCESSFUL" in build_output or "BUILD SUCCESS" in build_output)
        compiler_errors = []

        error_regex = re.compile(r"(?:e:\s+)?((?:[A-Za-z]:)?[A-Za-z0-9_/\\\s-]+\.(?:java|kt)):(\d+):(?:\d+:)?\s+error:\s+(.+)", re.IGNORECASE)
        lines = build_output.splitlines()
        for idx, line in enumerate(lines):
            line_str = line.strip()
            m = error_regex.search(line_str)
            if m:
                err_file = m.group(1).strip()
                err_line = int(m.group(2))
                err_msg = m.group(3).strip()
                # Check following lines for context (e.g. "symbol:   variable missing_text_view")
                extra = []
                for next_l in lines[idx + 1:idx + 5]:
                    n_str = next_l.strip()
                    if any(k in n_str for k in ("symbol:", "location:", "required:", "found:")):
                        extra.append(n_str)
                if extra:
                    err_msg += " (" + "; ".join(extra) + ")"

                compiler_errors.append({
                    "file": err_file,
                    "line": err_line,
                    "message": err_msg,
                    "raw": line_str,
                })
            elif "error:" in line_str.lower() and not any(e["raw"] == line_str for e in compiler_errors):
                compiler_errors.append({
                    "file": "build",
                    "line": 0,
                    "message": line_str,
                    "raw": line_str,
                })

        verdict = "PASSED" if is_success else "FAILED"
        result = {
            "timestamp": time.time(),
            "time_display": time.strftime("%H:%M:%S"),
            "verdict": verdict,
            "exit_code": exit_code,
            "is_success": is_success,
            "compiler_errors_count": len(compiler_errors),
            "compiler_errors": compiler_errors[:10],
            "raw_output_snippet": build_output[-1500:] if len(build_output) > 1500 else build_output,
        }
        self.context.update_gradle_state(
            status=verdict,
            errors=[e["message"] for e in compiler_errors],
        )
        self.last_verification = result
        return result

    def diagnose_failure(self, verification_result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        v = verification_result or self.last_verification
        if not v or v.get("is_success"):
            return {
                "status": "HEALTHY",
                "diagnosis": "No active failure detected.",
                "recommendation": "Proceed with planned execution.",
            }

        errors = v.get("compiler_errors", [])
        raw = v.get("raw_output_snippet", "")
        diagnostic_type = "BUILD_FAILURE"
        root_cause = "Unknown build failure."
        recommended_fix = "Review Gradle build output."
        defect_target_file = None
        defect_line = None

        if errors:
            first_err = errors[0]
            defect_target_file = first_err.get("file")
            defect_line = first_err.get("line")
            msg = first_err.get("message", "")

            if "cannot find symbol" in msg or "unresolved reference" in msg:
                diagnostic_type = "UNRESOLVED_SYMBOL"
                root_cause = f"Symbol reference error in {defect_target_file} at line {defect_line}: {msg}"
                recommended_fix = f"Import missing class or correct variable identifier in {defect_target_file}."
            elif "not a statement" in msg or "expected" in msg or "syntax error" in msg.lower():
                diagnostic_type = "SYNTAX_ERROR"
                root_cause = f"Syntax error in {defect_target_file} at line {defect_line}: {msg}"
                recommended_fix = f"Fix syntax / punctuation / semicolon at line {defect_line} in {defect_target_file}."
            elif "incompatible types" in msg or "type mismatch" in msg:
                diagnostic_type = "TYPE_MISMATCH"
                root_cause = f"Type mismatch error in {defect_target_file} at line {defect_line}: {msg}"
                recommended_fix = f"Align argument or assignment types in {defect_target_file}."
            else:
                diagnostic_type = "COMPILER_ERROR"
                root_cause = f"Compiler error in {defect_target_file}: {msg}"
                recommended_fix = f"Correct defect in {defect_target_file}."
        elif "execution failed for task" in raw.lower():
            diagnostic_type = "GRADLE_TASK_FAILURE"
            root_cause = "Gradle task execution failed (check task dependencies or AndroidManifest/resource errors)."
            recommended_fix = "Inspect Gradle error logs, check manifest package name, and verify resource IDs."

        diagnosis = {
            "guardian": self.name,
            "timestamp": time.time(),
            "diagnostic_type": diagnostic_type,
            "root_cause": root_cause,
            "defect_target_file": defect_target_file,
            "defect_line": defect_line,
            "recommended_fix": recommended_fix,
            "prescribed_action_for_droid": f"Apply targeted code repair to {defect_target_file or 'project'} and rebuild.",
        }
        return diagnosis

    def verify_repair(self, pre_build_result: Dict[str, Any], post_build_result: Dict[str, Any]) -> Dict[str, Any]:
        pre_failed = not pre_build_result.get("is_success", False)
        post_success = post_build_result.get("is_success", False)

        is_verified_fixed = pre_failed and post_success
        verdict = "VERIFIED_REPAIRED" if is_verified_fixed else ("STILL_FAILING" if not post_success else "NO_OP")

        report = {
            "guardian": self.name,
            "timestamp": time.time(),
            "verdict": verdict,
            "repair_successful": is_verified_fixed,
            "pre_exit_code": pre_build_result.get("exit_code"),
            "post_exit_code": post_build_result.get("exit_code"),
            "pre_errors": pre_build_result.get("compiler_errors_count", 0),
            "post_errors": post_build_result.get("compiler_errors_count", 0),
            "evidence_note": "Deterministic build verification confirms defect is 100% resolved." if is_verified_fixed else "Defect remains unresolved.",
        }

        self.context.record_repair(
            defect_desc=f"Build failure with {pre_build_result.get('compiler_errors_count', 0)} error(s)",
            repair_action="Droid targeted code repair",
            verified_success=is_verified_fixed,
            evidence=report,
        )
        return report

    def inspect_runtime(self, package_name: str, adb_runner: Optional[Callable[[List[str]], Tuple[int, str, str]]] = None) -> Dict[str, Any]:
        running_pid = None
        foreground_activity = None
        has_fatal_crash = False

        if adb_runner:
            ret, out, _ = adb_runner(["shell", "pidof", package_name])
            if ret == 0 and out.strip():
                try:
                    running_pid = int(out.strip().split()[0])
                except Exception:
                    pass

            ret2, out2, _ = adb_runner(["shell", "dumpsys", "window", "displays"])
            if ret2 == 0:
                for line in out2.splitlines():
                    if "mCurrentFocus" in line or "mFocusedApp" in line:
                        foreground_activity = line.strip()
                        break

            ret3, out3, _ = adb_runner(["logcat", "-d", "-s", "AndroidRuntime:E", "*:F"])
            if ret3 == 0 and "FATAL EXCEPTION" in out3 and package_name in out3:
                has_fatal_crash = True

        runtime_report = {
            "timestamp": time.time(),
            "package_name": package_name,
            "is_running": running_pid is not None,
            "pid": running_pid,
            "foreground_activity": foreground_activity,
            "has_fatal_crash": has_fatal_crash,
            "status": "CRASHED" if has_fatal_crash else ("RUNNING" if running_pid else "STOPPED"),
        }
        self.context.update_device_state(
            serial=self.context.device_state.get("active_serial", "emulator-5554"),
            booted=True,
            pid=running_pid,
            foreground_act=foreground_activity,
        )
        return runtime_report
