"""
NR-AI Unity Compilation & Build Engine (Step 8 Phase 2).

Safe, bounded, deterministic management of Unity project compilation,
player builds, process execution, log parsing, and build evidence verification.
Strictly shell=False, bounded timeouts, sensitive data redaction, and model isolation.
"""

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from app.agent.unity_safety import (
    UnitySafetyGate,
    DEFAULT_UNITY_SAFETY_GATE,
    UnityErrorCode,
    UnitySafetyError,
    ALLOWED_BUILD_TARGETS,
    BUILD_TIMEOUT_SECONDS,
    redact_sensitive_data,
)
from app.agent.unity_environment import (
    UnityEnvironmentDetector,
    DEFAULT_UNITY_ENV_DETECTOR,
)
from app.agent.unity_project import (
    UnityProjectInspector,
    DEFAULT_UNITY_PROJECT_INSPECTOR,
)

logger = logging.getLogger("NRAI.UnityBuild")

MAX_CAPTURED_OUTPUT_BYTES = 500_000  # 500 KB bounded output buffer
DEFAULT_COMPILE_TIMEOUT = 120.0
DEFAULT_BUILD_TIMEOUT = 300.0


# -----------------------------------------------------------------------------
# Data Models
# -----------------------------------------------------------------------------

@dataclass
class UnityCompilerIssue:
    """Structured C# compiler error, warning, or Unity build issue."""
    file: str
    line: int
    column: int
    code: str
    message: str
    severity: str  # "error" or "warning"
    raw_line: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "raw_line": self.raw_line,
        }


@dataclass
class CompilationResult:
    """Structured evidence of a Unity project compilation."""
    success: bool
    exit_code: int
    duration_seconds: float
    errors: List[UnityCompilerIssue] = field(default_factory=list)
    warnings: List[UnityCompilerIssue] = field(default_factory=list)
    log_path: Optional[str] = None
    output_preview: str = ""
    verified: bool = False
    error_summary: str = ""
    timestamp: float = field(default_factory=time.time)

    @property
    def error_count(self) -> int:
        return len(self.errors)

    @property
    def warning_count(self) -> int:
        return len(self.warnings)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "exit_code": self.exit_code,
            "duration_seconds": round(self.duration_seconds, 2),
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "errors": [e.to_dict() for e in self.errors],
            "warnings": [w.to_dict() for w in self.warnings],
            "log_path": self.log_path,
            "output_preview": self.output_preview,
            "verified": self.verified,
            "error_summary": self.error_summary,
            "timestamp": self.timestamp,
        }


@dataclass
class BuildArtifactInfo:
    """Information about a verified build output artifact."""
    name: str
    path: str
    size_bytes: int
    sha256: str
    is_file: bool = True
    exists: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "is_file": self.is_file,
            "exists": self.exists,
        }


@dataclass
class BuildResult:
    """Structured evidence of a Unity player build."""
    success: bool
    exit_code: int
    build_target: str
    output_path: str
    duration_seconds: float
    errors: List[UnityCompilerIssue] = field(default_factory=list)
    warnings: List[UnityCompilerIssue] = field(default_factory=list)
    artifacts: List[BuildArtifactInfo] = field(default_factory=list)
    log_path: Optional[str] = None
    output_preview: str = ""
    verified: bool = False
    error_summary: str = ""
    timestamp: float = field(default_factory=time.time)

    @property
    def error_count(self) -> int:
        return len(self.errors)

    @property
    def warning_count(self) -> int:
        return len(self.warnings)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "exit_code": self.exit_code,
            "build_target": self.build_target,
            "output_path": self.output_path,
            "duration_seconds": round(self.duration_seconds, 2),
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "errors": [e.to_dict() for e in self.errors],
            "warnings": [w.to_dict() for w in self.warnings],
            "artifacts": [a.to_dict() for a in self.artifacts],
            "log_path": self.log_path,
            "output_preview": self.output_preview,
            "verified": self.verified,
            "error_summary": self.error_summary,
            "timestamp": self.timestamp,
        }


# -----------------------------------------------------------------------------
# Unity Log & Error Parser
# -----------------------------------------------------------------------------

class UnityLogParser:
    """
    Parses Unity Editor and C# compilation logs into structured diagnostics.
    Identifies compiler errors, compiler warnings, BuildFailedExceptions,
    and fatal engine errors.
    """

    RE_CS_ERROR = re.compile(
        r"([^\r\n:]+\.cs)(?:\((\d+)(?:,(\d+))?\))?:\s+error\s+([A-Z0-9]+):\s+([^\r\n]+)"
    )
    RE_CS_WARNING = re.compile(
        r"([^\r\n:]+\.cs)(?:\((\d+)(?:,(\d+))?\))?:\s+warning\s+([A-Z0-9]+):\s+([^\r\n]+)"
    )
    RE_BUILD_EXCEPTION = re.compile(
        r"(?i)(?:UnityEditor\.BuildPlayerWindow\+)?(BuildFailedException:\s+[^\r\n]+)"
    )
    RE_COMPILATION_FAILED_BANNER = re.compile(
        r"(?i)(Scripts have compiler errors|CompilerOutput:-error|Compilation failed)"
    )
    RE_SHADER_ERROR = re.compile(
        r"(?i)Shader error in '([^']+)':\s+([^\r\n]+)"
    )

    @classmethod
    def parse_log(cls, log_content: str) -> Dict[str, Any]:
        """Parses full log text into structured errors, warnings, and summary."""
        errors: List[UnityCompilerIssue] = []
        warnings: List[UnityCompilerIssue] = []
        lines = log_content.splitlines()

        for line in lines:
            line_clean = line.strip()
            if not line_clean:
                continue

            m_err = cls.RE_CS_ERROR.search(line_clean)
            if m_err:
                filepath, l_str, c_str, code, msg = m_err.groups()
                line_no = int(l_str) if l_str else 1
                col_no = int(c_str) if c_str else 1
                errors.append(
                    UnityCompilerIssue(
                        file=filepath.strip().replace("\\", "/"),
                        line=line_no,
                        column=col_no,
                        code=code.strip(),
                        message=redact_sensitive_data(msg.strip()),
                        severity="error",
                        raw_line=redact_sensitive_data(line_clean),
                    )
                )
                continue

            m_warn = cls.RE_CS_WARNING.search(line_clean)
            if m_warn:
                filepath, l_str, c_str, code, msg = m_warn.groups()
                line_no = int(l_str) if l_str else 1
                col_no = int(c_str) if c_str else 1
                warnings.append(
                    UnityCompilerIssue(
                        file=filepath.strip().replace("\\", "/"),
                        line=line_no,
                        column=col_no,
                        code=code.strip(),
                        message=redact_sensitive_data(msg.strip()),
                        severity="warning",
                        raw_line=redact_sensitive_data(line_clean),
                    )
                )
                continue

            m_bfe = cls.RE_BUILD_EXCEPTION.search(line_clean)
            if m_bfe:
                errors.append(
                    UnityCompilerIssue(
                        file="Project",
                        line=1,
                        column=1,
                        code="BUILD_EXCEPTION",
                        message=redact_sensitive_data(m_bfe.group(1)),
                        severity="error",
                        raw_line=redact_sensitive_data(line_clean),
                    )
                )
                continue

            m_shader = cls.RE_SHADER_ERROR.search(line_clean)
            if m_shader:
                shader_name, s_msg = m_shader.groups()
                errors.append(
                    UnityCompilerIssue(
                        file=shader_name,
                        line=1,
                        column=1,
                        code="SHADER_ERROR",
                        message=redact_sensitive_data(s_msg.strip()),
                        severity="error",
                        raw_line=redact_sensitive_data(line_clean),
                    )
                )
                continue

        if not errors and cls.RE_COMPILATION_FAILED_BANNER.search(log_content):
            errors.append(
                UnityCompilerIssue(
                    file="Project",
                    line=1,
                    column=1,
                    code="COMPILATION_FAILED",
                    message="Unity reported compilation errors in project scripts.",
                    severity="error",
                    raw_line="Scripts have compiler errors.",
                )
            )

        summary = ""
        if errors:
            first_err = errors[0]
            summary = f"{first_err.code} in {first_err.file}:{first_err.line} - {first_err.message}"
            if len(errors) > 1:
                summary += f" (+{len(errors)-1} more errors)"
        elif warnings:
            summary = f"{len(warnings)} warning(s) detected during compilation/build."
        else:
            summary = "No compilation or build errors detected."

        return {
            "errors": errors,
            "warnings": warnings,
            "error_count": len(errors),
            "warning_count": len(warnings),
            "summary": summary,
        }


# -----------------------------------------------------------------------------
# Safe Unity Process Runner
# -----------------------------------------------------------------------------

class UnityProcessRunner:
    """
    Executes Unity Editor commands safely with strict bounds:
    - Strictly shell=False
    - Bounded execution timeout
    - Immediate process tree termination on timeout or emergency stop
    - Captures stdout/stderr or designated logfile safely
    - Redacts sensitive tokens
    - Supports mock/simulated executor hook for deterministic verification
    """

    def __init__(self, safety_gate: Optional[UnitySafetyGate] = None):
        self.safety = safety_gate or DEFAULT_UNITY_SAFETY_GATE
        self._mock_executor: Optional[Callable[[List[str], float], Tuple[int, str, str]]] = None

    def set_mock_executor(
        self,
        executor_fn: Optional[Callable[[List[str], float], Tuple[int, str, str]]],
    ) -> None:
        """Sets a deterministic mock executor for unit testing and offline environments."""
        self._mock_executor = executor_fn

    def run_command(
        self,
        cmd_args: List[str],
        timeout_seconds: float = DEFAULT_COMPILE_TIMEOUT,
        cwd: Optional[Path] = None,
    ) -> Tuple[int, str, str, float]:
        """
        Executes command with shell=False, timeout, process cleanup, and redaction.
        Returns (exit_code, stdout, stderr, duration_seconds).
        """
        self.safety.assert_not_stopped()

        bounded_timeout = min(max(5.0, float(timeout_seconds)), BUILD_TIMEOUT_SECONDS)

        if self._mock_executor is not None:
            t0 = time.time()
            try:
                code, out, err = self._mock_executor(cmd_args, bounded_timeout)
                duration = time.time() - t0
                return (
                    code,
                    redact_sensitive_data(out[:MAX_CAPTURED_OUTPUT_BYTES]),
                    redact_sensitive_data(err[:MAX_CAPTURED_OUTPUT_BYTES]),
                    duration,
                )
            except Exception as e:
                duration = time.time() - t0
                logger.error(f"[UnityProcessRunner] Mock executor error: {e}")
                return -1, "", str(e), duration

        t0 = time.time()
        proc: Optional[subprocess.Popen] = None
        try:
            logger.info(f"[UnityProcessRunner] Launching Unity process: {cmd_args[0]} (timeout={bounded_timeout}s)")
            proc = subprocess.Popen(
                cmd_args,
                cwd=str(cwd) if cwd else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            stdout_data, stderr_data = proc.communicate(timeout=bounded_timeout)
            duration = time.time() - t0
            exit_code = proc.returncode

            return (
                exit_code,
                redact_sensitive_data((stdout_data or "")[:MAX_CAPTURED_OUTPUT_BYTES]),
                redact_sensitive_data((stderr_data or "")[:MAX_CAPTURED_OUTPUT_BYTES]),
                duration,
            )

        except subprocess.TimeoutExpired:
            duration = time.time() - t0
            logger.warning(f"[UnityProcessRunner] Unity process timed out after {bounded_timeout}s. Terminating...")
            self._cleanup_process(proc)
            raise UnitySafetyError(
                UnityErrorCode.BUILD_TIMEOUT,
                f"Unity execution timed out after {bounded_timeout:.1f}s.",
                {"timeout": bounded_timeout, "cmd": cmd_args[0] if cmd_args else ""},
            )

        except Exception as e:
            duration = time.time() - t0
            self._cleanup_process(proc)
            logger.error(f"[UnityProcessRunner] Unity execution failed: {e}")
            raise UnitySafetyError(
                UnityErrorCode.BUILD_FAILED,
                f"Failed to execute Unity command: {e}",
                {"details": str(e)},
            )

    def _cleanup_process(self, proc: Optional[subprocess.Popen]) -> None:
        """Forcefully terminates the subprocess if still active."""
        if proc is None:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2.0)
        except Exception as e:
            logger.debug(f"[UnityProcessRunner] Process cleanup note: {e}")


# -----------------------------------------------------------------------------
# Unity Build & Compilation Manager
# -----------------------------------------------------------------------------

class UnityBuildManager:
    """
    Central manager for Unity script compilation and player builds.
    Validates targets, outputs, and editors, executes bounded processes,
    parses logs, and verifies build artifacts.
    """

    def __init__(
        self,
        safety_gate: Optional[UnitySafetyGate] = None,
        env_detector: Optional[UnityEnvironmentDetector] = None,
        project_inspector: Optional[UnityProjectInspector] = None,
        runner: Optional[UnityProcessRunner] = None,
    ):
        self.safety = safety_gate or DEFAULT_UNITY_SAFETY_GATE
        self.env = env_detector or DEFAULT_UNITY_ENV_DETECTOR
        self.inspector = project_inspector or DEFAULT_UNITY_PROJECT_INSPECTOR
        self.runner = runner or UnityProcessRunner(self.safety)

    def validate_build_target(self, target: str) -> str:
        """Validates build target against allowed targets."""
        return self.safety.validate_build_target(target)

    def validate_build_output_path(self, target_path: Path | str, project_root: Optional[Path] = None) -> Path:
        """Validates build output destination path."""
        return self.safety.validate_build_output_path(target_path, project_root)

    def get_build_configuration(self, project_path: Path | str) -> Dict[str, Any]:
        """Inspects build configuration including scenes in build and target version."""
        self.safety.assert_not_stopped()
        p = Path(project_path).resolve()
        if not self.inspector.is_valid_project(p):
            raise UnitySafetyError(
                UnityErrorCode.PROJECT_INVALID,
                f"Path '{p}' is not a valid Unity project.",
                {"path": str(p)},
            )

        scenes = self.inspector.inspect_scenes_in_build(p)
        version = self.inspector.get_project_version(p)
        meta = self.inspector.inspect_project(p)
        asmdefs = meta.asmdef_files if meta else []

        return {
            "project_name": p.name,
            "project_path": str(p),
            "unity_version": version,
            "scenes_in_build_count": len(scenes),
            "scenes_in_build": scenes,
            "asmdef_count": len(asmdefs),
            "asmdef_files": asmdefs,
            "allowed_targets": sorted(list(ALLOWED_BUILD_TARGETS)),
        }

    def clean_build_target(self, output_path: Path | str, project_root: Optional[Path] = None) -> bool:
        """
        Safely removes existing build artifacts to protect against stale targets.
        Refuses to delete outside authorized build locations or protected directories.
        """
        self.safety.assert_not_stopped()
        resolved = self.validate_build_output_path(output_path, project_root)

        if not resolved.exists():
            return True

        if resolved == self.safety.workspace_root or resolved in self.safety.authorized_projects:
            raise UnitySafetyError(
                UnityErrorCode.PROTECTED_DIRECTORY_REJECTED,
                f"Refusing to delete project/workspace root '{resolved}'.",
                {"path": str(resolved)},
            )

        try:
            if resolved.is_file():
                resolved.unlink()
            elif resolved.is_dir():
                shutil.rmtree(resolved)
            logger.info(f"[UnityBuildManager] Cleaned build target: {resolved}")
            return True
        except Exception as e:
            logger.error(f"[UnityBuildManager] Failed to clean build target '{resolved}': {e}")
            return False

    def compile_project(
        self,
        project_path: Path | str,
        editor_path: Optional[str] = None,
        timeout_seconds: float = DEFAULT_COMPILE_TIMEOUT,
    ) -> CompilationResult:
        """
        Executes controlled batchmode compilation of Unity project scripts.
        Captures exit code, parses compiler errors and warnings, and verifies result.
        """
        self.safety.assert_not_stopped()
        p = Path(project_path).resolve()
        if not self.inspector.is_valid_project(p):
            raise UnitySafetyError(
                UnityErrorCode.PROJECT_INVALID,
                f"Path '{p}' is not a valid Unity project.",
                {"path": str(p)},
            )

        unity_exe = editor_path
        if not unity_exe and self.runner._mock_executor is None:
            best_editor = self.env.find_editor_for_project(p)
            if not best_editor:
                env_info = self.env.detect_environment()
                best_editor = env_info.preferred_editor
            if best_editor and best_editor.is_executable:
                unity_exe = best_editor.editor_path

        if not unity_exe and self.runner._mock_executor is None:
            raise UnitySafetyError(
                UnityErrorCode.UNITY_NOT_FOUND,
                "No valid Unity Editor executable found for compilation.",
            )

        temp_log = p / "Temp" / "compile.log"

        cmd_args = [
            unity_exe or "Unity.exe",
            "-batchmode",
            "-quit",
            "-projectPath", str(p),
            "-logFile", str(temp_log),
        ]

        try:
            exit_code, stdout_str, stderr_str, duration = self.runner.run_command(
                cmd_args,
                timeout_seconds=timeout_seconds,
                cwd=p,
            )
        except UnitySafetyError:
            raise
        except Exception as e:
            raise UnitySafetyError(
                UnityErrorCode.COMPILATION_FAILED,
                f"Compilation execution failed: {e}",
            )

        log_content = stdout_str + "\n" + stderr_str
        if temp_log.exists():
            try:
                log_content = temp_log.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass

        parsed = UnityLogParser.parse_log(log_content)
        success = (exit_code == 0) and (parsed["error_count"] == 0)

        error_summary = ""
        if not success:
            if parsed["error_count"] > 0:
                error_summary = parsed["summary"]
            else:
                error_summary = f"Unity exited with code {exit_code}."

        return CompilationResult(
            success=success,
            exit_code=exit_code,
            duration_seconds=duration,
            errors=parsed["errors"],
            warnings=parsed["warnings"],
            log_path=str(temp_log) if temp_log.exists() else None,
            output_preview=redact_sensitive_data(log_content[:2000]),
            verified=success,
            error_summary=error_summary,
        )

    def build_player(
        self,
        project_path: Path | str,
        build_target: str = "StandaloneWindows64",
        output_path: Optional[str] = None,
        scenes: Optional[List[str]] = None,
        editor_path: Optional[str] = None,
        timeout_seconds: float = DEFAULT_BUILD_TIMEOUT,
    ) -> BuildResult:
        """
        Executes controlled player build for target platform.
        Enforces stale target protection, path confinement, exit code validation,
        log parsing, and output artifact verification.
        """
        self.safety.assert_not_stopped()
        p = Path(project_path).resolve()
        if not self.inspector.is_valid_project(p):
            raise UnitySafetyError(
                UnityErrorCode.PROJECT_INVALID,
                f"Path '{p}' is not a valid Unity project.",
                {"path": str(p)},
            )

        valid_target = self.validate_build_target(build_target)

        if not output_path:
            out_file = "App.exe" if "Windows" in valid_target else f"App_{valid_target}"
            output_target = p / "Builds" / valid_target / out_file
        else:
            output_target = Path(output_path)

        resolved_output = self.validate_build_output_path(output_target, project_root=p)

        self.clean_build_target(resolved_output, project_root=p)
        resolved_output.parent.mkdir(parents=True, exist_ok=True)

        unity_exe = editor_path
        if not unity_exe and self.runner._mock_executor is None:
            best_editor = self.env.find_editor_for_project(p)
            if not best_editor:
                env_info = self.env.detect_environment()
                best_editor = env_info.preferred_editor
            if best_editor and best_editor.is_executable:
                unity_exe = best_editor.editor_path

        if not unity_exe and self.runner._mock_executor is None:
            raise UnitySafetyError(
                UnityErrorCode.UNITY_NOT_FOUND,
                f"No valid Unity Editor executable found for build target '{valid_target}'.",
            )

        temp_log = p / "Temp" / f"build_{valid_target}.log"

        cmd_args = [
            unity_exe or "Unity.exe",
            "-batchmode",
            "-quit",
            "-projectPath", str(p),
            "-buildTarget", valid_target,
            f"-build{valid_target}Player", str(resolved_output),
            "-logFile", str(temp_log),
        ]

        try:
            exit_code, stdout_str, stderr_str, duration = self.runner.run_command(
                cmd_args,
                timeout_seconds=timeout_seconds,
                cwd=p,
            )
        except UnitySafetyError:
            raise
        except Exception as e:
            raise UnitySafetyError(
                UnityErrorCode.BUILD_FAILED,
                f"Player build execution failed: {e}",
            )

        log_content = stdout_str + "\n" + stderr_str
        if temp_log.exists():
            try:
                log_content = temp_log.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass

        parsed = UnityLogParser.parse_log(log_content)

        artifacts: List[BuildArtifactInfo] = []
        artifact_verified = False
        if resolved_output.exists():
            try:
                size = resolved_output.stat().st_size
                h = hashlib.sha256()
                with open(resolved_output, "rb") as f:
                    for chunk in iter(lambda: f.read(65536), b""):
                        h.update(chunk)
                sha = h.hexdigest()
                artifacts.append(
                    BuildArtifactInfo(
                        name=resolved_output.name,
                        path=str(resolved_output),
                        size_bytes=size,
                        sha256=sha,
                        is_file=resolved_output.is_file(),
                        exists=True,
                    )
                )
                artifact_verified = True
            except Exception as e:
                logger.warning(f"[UnityBuildManager] Failed to inspect generated artifact: {e}")

        success = (exit_code == 0) and (parsed["error_count"] == 0) and artifact_verified

        error_summary = ""
        if not success:
            if parsed["error_count"] > 0:
                error_summary = parsed["summary"]
            elif not artifact_verified:
                error_summary = f"Build exited with code {exit_code} but expected artifact '{resolved_output.name}' was not created."
            else:
                error_summary = f"Build failed with exit code {exit_code}."

        return BuildResult(
            success=success,
            exit_code=exit_code,
            build_target=valid_target,
            output_path=str(resolved_output),
            duration_seconds=duration,
            errors=parsed["errors"],
            warnings=parsed["warnings"],
            artifacts=artifacts,
            log_path=str(temp_log) if temp_log.exists() else None,
            output_preview=redact_sensitive_data(log_content[:2000]),
            verified=success,
            error_summary=error_summary,
        )

    def verify_build_artifact(self, output_path: Path | str) -> Dict[str, Any]:
        """
        Deterministically verifies build artifact existence, size, and SHA-256 hash.
        """
        self.safety.assert_not_stopped()
        p = Path(output_path).resolve()
        if not p.exists():
            return {
                "verified": False,
                "exists": False,
                "path": str(p),
                "error": f"Build artifact '{p}' does not exist.",
            }

        size = p.stat().st_size
        h = hashlib.sha256()
        try:
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            sha = h.hexdigest()
        except Exception as e:
            return {
                "verified": False,
                "exists": True,
                "path": str(p),
                "error": f"Failed to compute checksum: {e}",
            }

        return {
            "verified": True,
            "exists": True,
            "path": str(p),
            "name": p.name,
            "size_bytes": size,
            "sha256": sha,
            "is_file": p.is_file(),
        }


# Global default manager
DEFAULT_UNITY_BUILD_MANAGER = UnityBuildManager()
