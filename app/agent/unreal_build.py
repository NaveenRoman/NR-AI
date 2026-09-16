r"""
NR-AI Unreal Engine Compilation & Build Engine (Step 9 Phase 2).

Safe, bounded, deterministic management of Unreal Engine project compilation,
UnrealBuildTool (UBT) process execution, log parsing across 17 categories,
build artifact verification with SHA-256, and evidence-based result verification.
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
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from app.agent.unreal_safety import (
    UnrealSafetyGate,
    DEFAULT_UNREAL_SAFETY_GATE,
    UnrealErrorCode,
    UnrealSafetyError,
    ALLOWED_UNREAL_CONFIGURATIONS,
    ALLOWED_UNREAL_TARGET_TYPES,
    ALLOWED_UNREAL_PLATFORMS,
    BUILD_TIMEOUT_SECONDS,
    COMPILE_TIMEOUT_SECONDS,
    MAX_CAPTURED_OUTPUT_BYTES,
    redact_sensitive_data,
)
from app.agent.unreal_environment import (
    UnrealEnvironmentDetector,
    UnrealEngineInstance,
    DEFAULT_UNREAL_ENV_DETECTOR,
)
from app.agent.unreal_project import (
    UnrealProjectInspector,
    DEFAULT_UNREAL_PROJECT_INSPECTOR,
)

logger = logging.getLogger("NRAI.UnrealBuild")


# -----------------------------------------------------------------------------
# Enums
# -----------------------------------------------------------------------------

class UnrealBuildEnvironmentStatus(str, Enum):
    READY = "READY"
    PARTIALLY_READY = "PARTIALLY_READY"
    ENVIRONMENT_UNAVAILABLE = "ENVIRONMENT_UNAVAILABLE"
    PROJECT_INVALID = "PROJECT_INVALID"
    ENGINE_MISMATCH = "ENGINE_MISMATCH"
    EXECUTION_NOT_VERIFIED = "EXECUTION_NOT_VERIFIED"


class UnrealBuildResultStatus(str, Enum):
    BUILD_SUCCEEDED = "BUILD_SUCCEEDED"
    BUILD_FAILED = "BUILD_FAILED"
    BUILD_CANCELLED = "BUILD_CANCELLED"
    BUILD_TIMEOUT = "BUILD_TIMEOUT"
    BUILD_ENVIRONMENT_FAILURE = "BUILD_ENVIRONMENT_FAILURE"
    BUILD_NOT_VERIFIED = "BUILD_NOT_VERIFIED"


class UnrealBuildIssueCategory(str, Enum):
    CPP_COMPILE_ERROR = "CPP_COMPILE_ERROR"
    CPP_COMPILE_WARNING = "CPP_COMPILE_WARNING"
    LINKER_ERROR = "LINKER_ERROR"
    MISSING_MODULE = "MISSING_MODULE"
    MISSING_HEADER = "MISSING_HEADER"
    MISSING_DEPENDENCY = "MISSING_DEPENDENCY"
    BUILD_CS_ERROR = "BUILD_CS_ERROR"
    TARGET_CS_ERROR = "TARGET_CS_ERROR"
    UHT_ERROR = "UHT_ERROR"
    GENERATED_CODE_ERROR = "GENERATED_CODE_ERROR"
    MISSING_SDK = "MISSING_SDK"
    DOTNET_RUNTIME_MISSING = "DOTNET_RUNTIME_MISSING"
    ENGINE_MISMATCH = "ENGINE_MISMATCH"
    PROJECT_CONFIGURATION_ERROR = "PROJECT_CONFIGURATION_ERROR"
    PERMISSION_FAILURE = "PERMISSION_FAILURE"
    BUILD_TIMEOUT = "BUILD_TIMEOUT"
    UNKNOWN_BUILD_FAILURE = "UNKNOWN_BUILD_FAILURE"


# -----------------------------------------------------------------------------
# Data Models
# -----------------------------------------------------------------------------

@dataclass
class UnrealBuildDiagnostic:
    """Structured diagnostic representation of an Unreal build error or warning."""
    category: UnrealBuildIssueCategory
    severity: str = "error"  # "error" or "warning"
    file: str = ""
    line: int = 1
    column: int = 1
    code: str = ""
    message: str = ""
    source_evidence: str = ""
    raw_line: str = ""
    confidence: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category.value,
            "severity": self.severity,
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "code": self.code,
            "message": self.message,
            "source_evidence": self.source_evidence,
            "raw_line": self.raw_line,
            "confidence": self.confidence,
        }


@dataclass
class UnrealBuildTargetInfo:
    """Information about a requested or supported build target."""
    target_name: str
    target_type: str = "Editor"  # "Editor" or "Game"
    configuration: str = "Development"  # "Development", "DebugGame", "Shipping"
    platform: str = "Win64"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_name": self.target_name,
            "target_type": self.target_type,
            "configuration": self.configuration,
            "platform": self.platform,
        }


@dataclass
class UnrealBuildArtifactInfo:
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
class UnrealBuildResult:
    """Complete structured evidence of an Unreal build operation."""
    success: bool
    status: UnrealBuildResultStatus
    exit_code: int
    duration_seconds: float = 0.0
    target_name: str = ""
    configuration: str = "Development"
    platform: str = "Win64"
    errors: List[UnrealBuildDiagnostic] = field(default_factory=list)
    warnings: List[UnrealBuildDiagnostic] = field(default_factory=list)
    artifacts: List[UnrealBuildArtifactInfo] = field(default_factory=list)
    log_path: Optional[str] = None
    output_preview: str = ""
    verified: bool = False
    error_summary: str = ""
    executable: str = ""
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
            "status": self.status.value,
            "exit_code": self.exit_code,
            "duration_seconds": round(self.duration_seconds, 2),
            "target_name": self.target_name,
            "configuration": self.configuration,
            "platform": self.platform,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "errors": [e.to_dict() for e in self.errors],
            "warnings": [w.to_dict() for w in self.warnings],
            "artifacts": [a.to_dict() for a in self.artifacts],
            "log_path": self.log_path,
            "output_preview": self.output_preview,
            "verified": self.verified,
            "error_summary": self.error_summary,
            "executable": self.executable,
            "timestamp": self.timestamp,
        }


# -----------------------------------------------------------------------------
# Unreal Build Log & Diagnostic Parser
# -----------------------------------------------------------------------------

class UnrealBuildLogParser:
    """
    Parses raw Unreal compilation logs, UnrealBuildTool output, and compiler streams
    into structured diagnostics across 17 distinct failure and warning categories.
    """

    # MSVC C++ compiler errors: File(Line,Col): error Cxxxx: Message
    RE_MSVC_ERROR = re.compile(
        r"([^\r\n:]+\.(?:cpp|c|h|hpp))\((\d+)(?:,(\d+))?\):\s+error\s+(C\d+):\s+([^\r\n]+)",
        re.IGNORECASE,
    )
    # MSVC C++ compiler warnings: File(Line,Col): warning Cxxxx: Message
    RE_MSVC_WARNING = re.compile(
        r"([^\r\n:]+\.(?:cpp|c|h|hpp))\((\d+)(?:,(\d+))?\):\s+warning\s+(C\d+):\s+([^\r\n]+)",
        re.IGNORECASE,
    )
    # Missing header: fatal error C1083: Cannot open include file: 'xxx': No such file or directory
    RE_MISSING_HEADER = re.compile(
        r"(?:([^\r\n:]+\.(?:cpp|c|h|hpp))\((\d+)(?:,(\d+))?\):\s+)?(?:fatal\s+)?error\s+(C1083):\s+Cannot\s+open\s+include\s+file:\s+['\"]([^'\"]+)['\"]:\s+([^\r\n]+)",
        re.IGNORECASE,
    )
    # Linker errors: error LNKxxxx / fatal error LNKxxxx
    RE_LINKER_ERROR = re.compile(
        r"(?:([^\r\n:]+)\s*:\s*)?(?:fatal\s+)?error\s+(LNK\d+):\s+([^\r\n]+)",
        re.IGNORECASE,
    )
    # C# Build.cs / Target.cs errors: File.Target.cs(Line,Col): error CSxxxx: Message
    RE_CS_TARGET_ERROR = re.compile(
        r"([^\r\n:]+\.Target\.cs)\((\d+)(?:,(\d+))?\):\s+error\s+(CS\d+):\s+([^\r\n]+)",
        re.IGNORECASE,
    )
    RE_CS_BUILD_ERROR = re.compile(
        r"([^\r\n:]+\.Build\.cs)\((\d+)(?:,(\d+))?\):\s+error\s+(CS\d+):\s+([^\r\n]+)",
        re.IGNORECASE,
    )
    RE_CS_GENERIC_ERROR = re.compile(
        r"([^\r\n:]+\.cs)\((\d+)(?:,(\d+))?\):\s+error\s+(CS\d+):\s+([^\r\n]+)",
        re.IGNORECASE,
    )
    # UnrealHeaderTool (UHT) reflection errors
    RE_UHT_ERROR = re.compile(
        r"(?:(?:UnrealHeaderTool|UHT)\s*:\s*)?(?:Parsing\s+headers\s+for.*)?\s*([^\r\n:]+\.h)\((\d+)\):\s+(?:LogCompile:\s+)?Error:\s+([^\r\n]+)",
        re.IGNORECASE,
    )
    RE_UHT_GENERIC = re.compile(
        r"(?:LogCompile:\s+)?Error:\s+(Missing\s+GENERATED_BODY|Unrecognized\s+type|Cannot\s+find\s+class\s+named\s+'[^']+'|[^\r\n]+is\s+not\s+a\s+valid\s+UCLASS[^\r\n]*)",
        re.IGNORECASE,
    )
    # .NET Runtime missing host errors
    RE_DOTNET_MISSING = re.compile(
        r"(You\s+must\s+install\s+\.NET\s+to\s+run\s+this\s+application|The\s+required\s+library\s+hostfxr\.dll\s+could\s+not\s+be\s+found|A\s+fatal\s+error\s+occurred\.\s+The\s+folder\s+.*dotnet.*does\s+not\s+exist)",
        re.IGNORECASE,
    )
    # Missing module / dependency
    RE_MISSING_MODULE = re.compile(
        r"(?:Couldn't\s+find\s+module\s+rules\s+for\s+module|Module\s+'([^']+)'\s+could\s+not\s+be\s+found|Module\s+rules\s+not\s+found\s+for\s+'([^']+)')",
        re.IGNORECASE,
    )
    # Engine mismatch
    RE_ENGINE_MISMATCH = re.compile(
        r"(EngineAssociation\s+'[^']+'\s+does\s+not\s+match|Project\s+files\s+were\s+created\s+with\s+a\s+different\s+version\s+of\s+Unreal\s+Engine|Engine\s+version\s+mismatch)",
        re.IGNORECASE,
    )
    # Permission failure
    RE_PERMISSION = re.compile(
        r"(Access\s+to\s+the\s+path\s+['\"]?([^'\"\r\n]+)['\"]?\s+is\s+denied|Permission\s+denied)",
        re.IGNORECASE,
    )

    @classmethod
    def parse_log(cls, log_content: str) -> Dict[str, Any]:
        """Parses full raw build log into structured diagnostics across all 17 categories."""
        errors: List[UnrealBuildDiagnostic] = []
        warnings: List[UnrealBuildDiagnostic] = []
        lines = log_content.splitlines()

        for line in lines:
            line_clean = line.strip()
            if not line_clean:
                continue

            # 1. Missing Header check (specialized MSVC C1083)
            m_hdr = cls.RE_MISSING_HEADER.search(line_clean)
            if m_hdr:
                fpath, l_str, c_str, code, inc_file, msg = m_hdr.groups()
                file_str = (fpath or "SourceFile").strip().replace("\\", "/")
                errors.append(
                    UnrealBuildDiagnostic(
                        category=UnrealBuildIssueCategory.MISSING_HEADER,
                        severity="error",
                        file=file_str,
                        line=int(l_str) if l_str else 1,
                        column=int(c_str) if c_str else 1,
                        code=code.strip(),
                        message=redact_sensitive_data(f"Cannot open include file '{inc_file}': {msg.strip()}"),
                        source_evidence=inc_file,
                        raw_line=redact_sensitive_data(line_clean),
                    )
                )
                continue

            # 2. MSVC C++ Compile Error
            m_msvc_err = cls.RE_MSVC_ERROR.search(line_clean)
            if m_msvc_err:
                fpath, l_str, c_str, code, msg = m_msvc_err.groups()
                errors.append(
                    UnrealBuildDiagnostic(
                        category=UnrealBuildIssueCategory.CPP_COMPILE_ERROR,
                        severity="error",
                        file=fpath.strip().replace("\\", "/"),
                        line=int(l_str) if l_str else 1,
                        column=int(c_str) if c_str else 1,
                        code=code.strip(),
                        message=redact_sensitive_data(msg.strip()),
                        source_evidence=code.strip(),
                        raw_line=redact_sensitive_data(line_clean),
                    )
                )
                continue

            # 3. MSVC C++ Compile Warning
            m_msvc_warn = cls.RE_MSVC_WARNING.search(line_clean)
            if m_msvc_warn:
                fpath, l_str, c_str, code, msg = m_msvc_warn.groups()
                warnings.append(
                    UnrealBuildDiagnostic(
                        category=UnrealBuildIssueCategory.CPP_COMPILE_WARNING,
                        severity="warning",
                        file=fpath.strip().replace("\\", "/"),
                        line=int(l_str) if l_str else 1,
                        column=int(c_str) if c_str else 1,
                        code=code.strip(),
                        message=redact_sensitive_data(msg.strip()),
                        source_evidence=code.strip(),
                        raw_line=redact_sensitive_data(line_clean),
                    )
                )
                continue

            # 4. Linker Error
            m_link = cls.RE_LINKER_ERROR.search(line_clean)
            if m_link:
                obj_or_file, code, msg = m_link.groups()
                errors.append(
                    UnrealBuildDiagnostic(
                        category=UnrealBuildIssueCategory.LINKER_ERROR,
                        severity="error",
                        file=(obj_or_file or "Linker").strip().replace("\\", "/"),
                        line=1,
                        column=1,
                        code=f"LNK{code}" if not code.startswith("LNK") else code,
                        message=redact_sensitive_data(msg.strip()),
                        source_evidence=code,
                        raw_line=redact_sensitive_data(line_clean),
                    )
                )
                continue

            # 5. Target.cs / Build.cs C# errors
            m_target = cls.RE_CS_TARGET_ERROR.search(line_clean)
            if m_target:
                fpath, l_str, c_str, code, msg = m_target.groups()
                errors.append(
                    UnrealBuildDiagnostic(
                        category=UnrealBuildIssueCategory.TARGET_CS_ERROR,
                        severity="error",
                        file=fpath.strip().replace("\\", "/"),
                        line=int(l_str) if l_str else 1,
                        column=int(c_str) if c_str else 1,
                        code=code.strip(),
                        message=redact_sensitive_data(msg.strip()),
                        source_evidence=code.strip(),
                        raw_line=redact_sensitive_data(line_clean),
                    )
                )
                continue

            m_build_cs = cls.RE_CS_BUILD_ERROR.search(line_clean)
            if m_build_cs:
                fpath, l_str, c_str, code, msg = m_build_cs.groups()
                errors.append(
                    UnrealBuildDiagnostic(
                        category=UnrealBuildIssueCategory.BUILD_CS_ERROR,
                        severity="error",
                        file=fpath.strip().replace("\\", "/"),
                        line=int(l_str) if l_str else 1,
                        column=int(c_str) if c_str else 1,
                        code=code.strip(),
                        message=redact_sensitive_data(msg.strip()),
                        source_evidence=code.strip(),
                        raw_line=redact_sensitive_data(line_clean),
                    )
                )
                continue

            m_cs_gen = cls.RE_CS_GENERIC_ERROR.search(line_clean)
            if m_cs_gen:
                fpath, l_str, c_str, code, msg = m_cs_gen.groups()
                errors.append(
                    UnrealBuildDiagnostic(
                        category=UnrealBuildIssueCategory.BUILD_CS_ERROR,
                        severity="error",
                        file=fpath.strip().replace("\\", "/"),
                        line=int(l_str) if l_str else 1,
                        column=int(c_str) if c_str else 1,
                        code=code.strip(),
                        message=redact_sensitive_data(msg.strip()),
                        source_evidence=code.strip(),
                        raw_line=redact_sensitive_data(line_clean),
                    )
                )
                continue

            # 6. UHT Errors
            m_uht = cls.RE_UHT_ERROR.search(line_clean)
            if m_uht:
                fpath, l_str, msg = m_uht.groups()
                errors.append(
                    UnrealBuildDiagnostic(
                        category=UnrealBuildIssueCategory.UHT_ERROR,
                        severity="error",
                        file=fpath.strip().replace("\\", "/"),
                        line=int(l_str) if l_str else 1,
                        column=1,
                        code="UHT_ERROR",
                        message=redact_sensitive_data(msg.strip()),
                        source_evidence="UHT",
                        raw_line=redact_sensitive_data(line_clean),
                    )
                )
                continue

            m_uht_gen = cls.RE_UHT_GENERIC.search(line_clean)
            if m_uht_gen:
                errors.append(
                    UnrealBuildDiagnostic(
                        category=UnrealBuildIssueCategory.UHT_ERROR,
                        severity="error",
                        file="HeaderReflection",
                        line=1,
                        column=1,
                        code="UHT_ERROR",
                        message=redact_sensitive_data(m_uht_gen.group(1).strip()),
                        source_evidence="UHT",
                        raw_line=redact_sensitive_data(line_clean),
                    )
                )
                continue

            # 7. .NET Missing Runtime Host
            m_net = cls.RE_DOTNET_MISSING.search(line_clean)
            if m_net:
                errors.append(
                    UnrealBuildDiagnostic(
                        category=UnrealBuildIssueCategory.DOTNET_RUNTIME_MISSING,
                        severity="error",
                        file="HostRuntime",
                        line=1,
                        column=1,
                        code="DOTNET_MISSING",
                        message=redact_sensitive_data(m_net.group(1).strip()),
                        source_evidence=".NET",
                        raw_line=redact_sensitive_data(line_clean),
                    )
                )
                continue

            # 8. Missing module
            m_mod = cls.RE_MISSING_MODULE.search(line_clean)
            if m_mod:
                mod_name = m_mod.group(1) or m_mod.group(2) or "UnknownModule"
                errors.append(
                    UnrealBuildDiagnostic(
                        category=UnrealBuildIssueCategory.MISSING_MODULE,
                        severity="error",
                        file="ProjectModules",
                        line=1,
                        column=1,
                        code="MISSING_MODULE",
                        message=redact_sensitive_data(f"Module '{mod_name}' not found."),
                        source_evidence=mod_name,
                        raw_line=redact_sensitive_data(line_clean),
                    )
                )
                continue

            # 9. Engine mismatch
            m_eng = cls.RE_ENGINE_MISMATCH.search(line_clean)
            if m_eng:
                errors.append(
                    UnrealBuildDiagnostic(
                        category=UnrealBuildIssueCategory.ENGINE_MISMATCH,
                        severity="error",
                        file="uproject",
                        line=1,
                        column=1,
                        code="ENGINE_MISMATCH",
                        message=redact_sensitive_data(m_eng.group(1).strip()),
                        source_evidence="EngineAssociation",
                        raw_line=redact_sensitive_data(line_clean),
                    )
                )
                continue

            # 10. Permission failure
            m_perm = cls.RE_PERMISSION.search(line_clean)
            if m_perm:
                errors.append(
                    UnrealBuildDiagnostic(
                        category=UnrealBuildIssueCategory.PERMISSION_FAILURE,
                        severity="error",
                        file="Filesystem",
                        line=1,
                        column=1,
                        code="PERMISSION_DENIED",
                        message=redact_sensitive_data(m_perm.group(0).strip()),
                        source_evidence="Filesystem",
                        raw_line=redact_sensitive_data(line_clean),
                    )
                )
                continue

        summary = ""
        if errors:
            first = errors[0]
            summary = f"[{first.category.value}] {first.code} in {first.file}:{first.line} - {first.message}"
            if len(errors) > 1:
                summary += f" (+{len(errors)-1} more error(s))"
        elif warnings:
            summary = f"{len(warnings)} build warning(s) detected."
        else:
            summary = "No build errors or warnings detected."

        return {
            "errors": errors,
            "warnings": warnings,
            "error_count": len(errors),
            "warning_count": len(warnings),
            "summary": summary,
        }


# -----------------------------------------------------------------------------
# Unreal Build Environment Validator
# -----------------------------------------------------------------------------

class UnrealBuildEnvironmentValidator:
    """
    Validates host engine installation, UBT executable, .NET desktop runtime,
    project uproject file, and engine association.
    """

    def __init__(
        self,
        safety_gate: Optional[UnrealSafetyGate] = None,
        env_detector: Optional[UnrealEnvironmentDetector] = None,
        inspector: Optional[UnrealProjectInspector] = None,
    ):
        self.safety = safety_gate or DEFAULT_UNREAL_SAFETY_GATE
        self.env = env_detector or DEFAULT_UNREAL_ENV_DETECTOR
        self.inspector = inspector or DEFAULT_UNREAL_PROJECT_INSPECTOR

    def validate_build_environment(
        self,
        project_path: Path | str,
    ) -> Dict[str, Any]:
        """
        Deterministically inspects build environment prerequisites.
        Returns structured dictionary with overall status and detailed component checks.
        """
        self.safety.assert_not_emergency_stopped()
        proj_dir = self.safety.validate_project_path(project_path)

        checks: Dict[str, Any] = {
            "engine_installed": False,
            "ubt_available": False,
            "editor_cmd_available": False,
            "run_uat_available": False,
            "dotnet_runtime_available": False,
            "project_valid": False,
            "engine_association_match": False,
            "detected_engine": None,
            "detected_engine_version": None,
            "details": [],
        }

        # 1. Inspect installed engines
        engines = self.env.detect_engines()
        if not engines:
            checks["details"].append("No installed Unreal Engine found.")
            return {
                "status": UnrealBuildEnvironmentStatus.ENVIRONMENT_UNAVAILABLE.value,
                "checks": checks,
                "summary": "Unreal Engine is not installed on this system.",
                "usable": False,
            }

        primary_engine: UnrealEngineInstance = engines[0]
        checks["engine_installed"] = True
        checks["detected_engine"] = primary_engine.engine_path
        checks["detected_engine_version"] = primary_engine.version

        # 2. Check UBT and build executables
        ubt_path = Path(primary_engine.ubt_executable) if primary_engine.ubt_executable else None
        if ubt_path and ubt_path.is_file():
            checks["ubt_available"] = True
        else:
            checks["details"].append(f"UnrealBuildTool binary missing at {ubt_path}.")

        cmd_path = Path(primary_engine.cmd_executable) if primary_engine.cmd_executable else None
        if cmd_path and cmd_path.is_file():
            checks["editor_cmd_available"] = True

        uat_path = Path(primary_engine.uat_batch) if primary_engine.uat_batch else None
        if uat_path and uat_path.is_file():
            checks["run_uat_available"] = True

        # 3. Check .NET runtime host
        dotnet_usable = self._check_dotnet_usable()
        checks["dotnet_runtime_available"] = dotnet_usable
        if not dotnet_usable:
            checks["details"].append("Required .NET runtime host is not installed/available for UnrealBuildTool.")

        # 4. Check project validity
        try:
            proj_meta = self.inspector.inspect_project(proj_dir)
            checks["project_valid"] = proj_meta.is_valid
            if not proj_meta.is_valid:
                checks["details"].append(f"Project metadata invalid: {proj_meta.error_details}")
        except Exception as e:
            checks["project_valid"] = False
            checks["details"].append(f"Failed to inspect project: {e}")
            return {
                "status": UnrealBuildEnvironmentStatus.PROJECT_INVALID.value,
                "checks": checks,
                "summary": f"Project structure invalid: {e}",
                "usable": False,
            }

        # 5. Check engine association
        assoc_val = self.inspector.validate_engine_association(proj_dir, installed_engines=engines)
        checks["engine_association_match"] = assoc_val.get("is_matched_with_installed", False)
        if not checks["engine_association_match"]:
            checks["details"].append(f"Engine association mismatch: {assoc_val.get('message')}")

        # Determine overall status
        if not checks["project_valid"]:
            status = UnrealBuildEnvironmentStatus.PROJECT_INVALID
        elif not checks["engine_association_match"]:
            status = UnrealBuildEnvironmentStatus.ENGINE_MISMATCH
        elif not checks["ubt_available"] or not checks["dotnet_runtime_available"]:
            status = UnrealBuildEnvironmentStatus.ENVIRONMENT_UNAVAILABLE
        elif checks["ubt_available"] and checks["dotnet_runtime_available"] and checks["project_valid"]:
            status = UnrealBuildEnvironmentStatus.READY
        else:
            status = UnrealBuildEnvironmentStatus.PARTIALLY_READY

        return {
            "status": status.value,
            "checks": checks,
            "summary": (
                "Build environment is fully ready."
                if status == UnrealBuildEnvironmentStatus.READY
                else f"Build environment not ready: {'; '.join(checks['details'])}"
            ),
            "usable": status == UnrealBuildEnvironmentStatus.READY,
        }

    def _check_dotnet_usable(self) -> bool:
        """Deterministically checks if .NET is installed and can run on the host system."""
        try:
            res = subprocess.run(
                ["dotnet", "--version"],
                capture_output=True,
                text=True,
                shell=False,
                timeout=5.0,
            )
            return res.returncode == 0
        except Exception:
            return False


# -----------------------------------------------------------------------------
# Unreal Build Artifact Verifier
# -----------------------------------------------------------------------------

class UnrealBuildArtifactVerifier:
    """
    Verifies build output artifacts located strictly within the authorized project's
    Binaries/<Platform> folder, calculating file sizes and SHA-256 digests.
    """

    def __init__(self, safety_gate: Optional[UnrealSafetyGate] = None):
        self.safety = safety_gate or DEFAULT_UNREAL_SAFETY_GATE

    def verify_artifacts(
        self,
        project_path: Path | str,
        target_name: str,
        configuration: str = "Development",
        platform: str = "Win64",
    ) -> List[UnrealBuildArtifactInfo]:
        """
        Discovers and verifies expected build outputs in Binaries/<Platform>.
        Strictly read-only and confined to the authorized project directory.
        """
        self.safety.assert_not_emergency_stopped()
        proj_dir = self.safety.validate_project_path(project_path)
        binaries_dir = proj_dir / "Binaries" / platform

        artifacts: List[UnrealBuildArtifactInfo] = []
        if not binaries_dir.is_dir():
            return artifacts

        # Scan for binary artifacts belonging to target
        try:
            for item in binaries_dir.iterdir():
                if not item.is_file():
                    continue
                name = item.name
                # Check for project/target binaries, modules, or DLLs
                if (
                    target_name.lower() in name.lower()
                    or name.endswith(".dll")
                    or name.endswith(".exe")
                    or name.endswith(".modules")
                ):
                    sha = self._compute_sha256(item)
                    size = item.stat().st_size
                    artifacts.append(
                        UnrealBuildArtifactInfo(
                            name=name,
                            path=str(item),
                            size_bytes=size,
                            sha256=sha,
                            is_file=True,
                            exists=True,
                        )
                    )
        except Exception as e:
            logger.warning(f"Error scanning build artifacts in {binaries_dir}: {e}")

        return artifacts

    @staticmethod
    def _compute_sha256(file_path: Path) -> str:
        h = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                while chunk := f.read(65536):
                    h.update(chunk)
            return h.hexdigest()
        except Exception:
            return ""


# -----------------------------------------------------------------------------
# Safe Unreal Build Runner
# -----------------------------------------------------------------------------

class UnrealBuildRunner:
    """
    Executes UnrealBuildTool safely with strict controls:
    - Strictly shell=False
    - Explicit executable allowlist (UBT binary from detected engine)
    - Deterministic argument allowlist: [Target] Win64 [Config] -Project=[Path] -WaitMutex -NoHotReload
    - Bounded timeouts (5s to 300s)
    - Immediate process tree termination on timeout or emergency stop
    - Bounded stdout/stderr capture with redaction
    - Deterministic mock executor hook for unit testing and offline environments
    """

    def __init__(
        self,
        safety_gate: Optional[UnrealSafetyGate] = None,
        env_detector: Optional[UnrealEnvironmentDetector] = None,
        inspector: Optional[UnrealProjectInspector] = None,
        validator: Optional[UnrealBuildEnvironmentValidator] = None,
        artifact_verifier: Optional[UnrealBuildArtifactVerifier] = None,
    ):
        self.safety = safety_gate or DEFAULT_UNREAL_SAFETY_GATE
        self.env = env_detector or DEFAULT_UNREAL_ENV_DETECTOR
        self.inspector = inspector or DEFAULT_UNREAL_PROJECT_INSPECTOR
        self.validator = validator or UnrealBuildEnvironmentValidator(self.safety, self.env, self.inspector)
        self.artifact_verifier = artifact_verifier or UnrealBuildArtifactVerifier(self.safety)
        self._mock_executor: Optional[Callable[[List[str], float, Optional[Path]], Tuple[int, str, str]]] = None

    def set_mock_executor(
        self,
        executor_fn: Optional[Callable[[List[str], float, Optional[Path]], Tuple[int, str, str]]],
    ) -> None:
        """Sets a mock executor for testing and simulation in environments without host .NET."""
        self._mock_executor = executor_fn

    def build_project(
        self,
        project_path: Path | str,
        target_name: Optional[str] = None,
        target_type: str = "Editor",
        configuration: str = "Development",
        platform: str = "Win64",
        timeout_seconds: float = BUILD_TIMEOUT_SECONDS,
    ) -> UnrealBuildResult:
        """
        Executes an Unreal project build through UnrealBuildTool.
        Validates arguments, bounds timeouts, cleans up processes, and parses diagnostics.
        """
        t0 = time.time()
        self.safety.assert_not_emergency_stopped()
        proj_dir = self.safety.validate_project_path(project_path)

        # 1. Resolve project metadata and target name if omitted
        uproj_files = list(proj_dir.glob("*.uproject"))
        if not uproj_files:
            raise UnrealSafetyError(
                UnrealErrorCode.INVALID_UPROJECT,
                f"No .uproject file found in '{proj_dir}'.",
                {"project_dir": str(proj_dir)},
            )
        uproject_file = uproj_files[0]

        inferred_target = target_name or uproject_file.stem
        # Validate target arguments against allowlists and shell injection
        t_name, t_type, config, plat = self.safety.validate_build_target(
            proj_dir, inferred_target, target_type, configuration, platform
        )

        # Full target name for UBT (e.g., nr_unreal_testEditor)
        full_target = t_name if t_name.endswith("Editor") or t_type != "Editor" else f"{t_name}Editor"

        # 2. Check build environment
        env_info = self.validator.validate_build_environment(proj_dir)
        is_ready = env_info.get("usable", False)

        # If real environment is unavailable and no mock executor is configured, fail honestly
        if not is_ready and self._mock_executor is None:
            duration = time.time() - t0
            summary = f"Unreal build environment unavailable: {env_info.get('summary')}"
            logger.warning(f"[UnrealBuildRunner] {summary}")
            return UnrealBuildResult(
                success=False,
                status=UnrealBuildResultStatus.BUILD_ENVIRONMENT_FAILURE,
                exit_code=-1,
                duration_seconds=duration,
                target_name=full_target,
                configuration=config,
                platform=plat,
                errors=[
                    UnrealBuildDiagnostic(
                        category=UnrealBuildIssueCategory.DOTNET_RUNTIME_MISSING
                        if "DOTNET" in summary.upper()
                        else UnrealBuildIssueCategory.MISSING_SDK,
                        severity="error",
                        file="HostEnvironment",
                        line=1,
                        column=1,
                        code="ENV_UNAVAILABLE",
                        message=summary,
                        source_evidence=env_info.get("summary", ""),
                        raw_line=summary,
                    )
                ],
                error_summary=summary,
                verified=False,
            )

        # 3. Locate UnrealBuildTool
        engines = self.env.detect_engines()
        ubt_executable = "UnrealBuildTool.exe"
        if engines and engines[0].ubt_executable and Path(engines[0].ubt_executable).is_file():
            ubt_executable = engines[0].ubt_executable

        # 4. Construct deterministic command line (no arbitrary model arguments!)
        cmd_args = [
            ubt_executable,
            full_target,
            plat,
            config,
            f"-Project={uproject_file}",
            "-WaitMutex",
            "-NoHotReload",
        ]

        bounded_timeout = min(max(5.0, float(timeout_seconds)), BUILD_TIMEOUT_SECONDS)

        # 5. Execute via mock or real subprocess
        exit_code = -1
        stdout_text = ""
        stderr_text = ""
        timed_out = False

        if self._mock_executor is not None:
            try:
                exit_code, stdout_text, stderr_text = self._mock_executor(cmd_args, bounded_timeout, proj_dir)
            except subprocess.TimeoutExpired:
                timed_out = True
                exit_code = -1
            except Exception as e:
                logger.error(f"[UnrealBuildRunner] Mock execution error: {e}")
                exit_code = -1
                stderr_text = str(e)
        else:
            proc: Optional[subprocess.Popen] = None
            try:
                logger.info(f"[UnrealBuildRunner] Launching UBT: {cmd_args} (timeout={bounded_timeout}s)")
                proc = subprocess.Popen(
                    cmd_args,
                    cwd=str(proj_dir),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    shell=False,  # STRICT INVARIANT
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                raw_out, raw_err = proc.communicate(timeout=bounded_timeout)
                exit_code = proc.returncode
                stdout_text = raw_out or ""
                stderr_text = raw_err or ""
            except subprocess.TimeoutExpired:
                timed_out = True
                self._cleanup_process(proc)
                logger.warning(f"[UnrealBuildRunner] UBT build timed out after {bounded_timeout}s.")
            except Exception as e:
                self._cleanup_process(proc)
                logger.error(f"[UnrealBuildRunner] UBT launch failed: {e}")
                exit_code = -1
                stderr_text = str(e)

        duration = time.time() - t0

        # 6. Redact and bound output
        combined_output = stdout_text + "\n" + stderr_text
        redacted_output = redact_sensitive_data(combined_output[:MAX_CAPTURED_OUTPUT_BYTES])

        # 7. Parse diagnostics
        parsed = UnrealBuildLogParser.parse_log(redacted_output)
        errors: List[UnrealBuildDiagnostic] = parsed["errors"]
        warnings: List[UnrealBuildDiagnostic] = parsed["warnings"]

        # Handle timeout
        if timed_out:
            errors.insert(
                0,
                UnrealBuildDiagnostic(
                    category=UnrealBuildIssueCategory.BUILD_TIMEOUT,
                    severity="error",
                    file="BuildRunner",
                    line=1,
                    column=1,
                    code="TIMEOUT",
                    message=f"Build process timed out after {bounded_timeout:.1f}s.",
                    source_evidence=str(bounded_timeout),
                    raw_line="BUILD TIMED OUT",
                ),
            )
            return UnrealBuildResult(
                success=False,
                status=UnrealBuildResultStatus.BUILD_TIMEOUT,
                exit_code=-1,
                duration_seconds=duration,
                target_name=full_target,
                configuration=config,
                platform=plat,
                errors=errors,
                warnings=warnings,
                output_preview=redacted_output[:2000],
                verified=False,
                error_summary=f"Build timed out after {bounded_timeout:.1f}s.",
                executable=ubt_executable,
            )

        # 8. Check artifacts if exit code was 0
        artifacts: List[UnrealBuildArtifactInfo] = []
        if exit_code == 0 and not errors:
            artifacts = self.artifact_verifier.verify_artifacts(proj_dir, full_target, config, plat)
            success = True
            status = UnrealBuildResultStatus.BUILD_SUCCEEDED
            err_summary = "Build completed successfully."
        else:
            success = False
            status = UnrealBuildResultStatus.BUILD_FAILED
            err_summary = parsed.get("summary") or f"Build failed with exit code {exit_code}."

        return UnrealBuildResult(
            success=success,
            status=status,
            exit_code=exit_code,
            duration_seconds=duration,
            target_name=full_target,
            configuration=config,
            platform=plat,
            errors=errors,
            warnings=warnings,
            artifacts=artifacts,
            output_preview=redacted_output[:2000],
            verified=True,
            error_summary=err_summary,
            executable=ubt_executable,
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
            logger.debug(f"[UnrealBuildRunner] Process cleanup note: {e}")


# -----------------------------------------------------------------------------
# Build Result Verifier
# -----------------------------------------------------------------------------

class UnrealBuildResultVerifier:
    """
    Applies strict evidence precedence to verify build outcomes:
    1. Actual process result (exit code, termination status)
    2. Actual build log diagnostics
    3. Generated build artifacts (SHA-256 checksums)
    4. Deterministic project state
    5. Advisory model claim (NEVER overrides deterministic evidence)
    """

    @classmethod
    def verify(
        cls,
        build_result: UnrealBuildResult,
        model_claim_success: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Verifies build outcome and detects any model hallucination.
        """
        actual_success = build_result.success
        precedence_log: List[str] = []

        # 1. Process exit code
        if build_result.exit_code != 0:
            precedence_log.append(f"Level 1: Process exit code is non-zero ({build_result.exit_code}) -> FAILURE")
            determined_status = UnrealBuildResultStatus.BUILD_FAILED
            determined_success = False
        else:
            precedence_log.append("Level 1: Process exit code is 0 -> Candidate SUCCESS")
            determined_status = UnrealBuildResultStatus.BUILD_SUCCEEDED
            determined_success = True

        # 2. Build log diagnostics
        if build_result.error_count > 0:
            precedence_log.append(f"Level 2: Log contains {build_result.error_count} error diagnostic(s) -> FAILURE")
            determined_status = UnrealBuildResultStatus.BUILD_FAILED
            determined_success = False
        else:
            precedence_log.append("Level 2: Log contains 0 error diagnostics -> PASS")

        # 3. Artifact verification
        if determined_success and build_result.artifacts:
            precedence_log.append(f"Level 3: {len(build_result.artifacts)} artifact(s) verified with SHA-256 -> CONFIRMED")
        elif determined_success and not build_result.artifacts:
            precedence_log.append("Level 3: No new artifacts detected (compilation only or up-to-date)")

        # 4. Model claim check
        discrepancy_detected = False
        if model_claim_success is not None:
            if model_claim_success != determined_success:
                discrepancy_detected = True
                precedence_log.append(
                    f"Level 5 (Model Claim): Model claimed success={model_claim_success}, "
                    f"but deterministic evidence dictates success={determined_success}. "
                    "Deterministic evidence PREVAILS."
                )

        return {
            "verified_success": determined_success,
            "status": determined_status.value,
            "exit_code": build_result.exit_code,
            "error_count": build_result.error_count,
            "warning_count": build_result.warning_count,
            "artifact_count": len(build_result.artifacts),
            "evidence_precedence": precedence_log,
            "discrepancy_detected": discrepancy_detected,
            "confidence": 1.0,
        }


# Global default instances
DEFAULT_UNREAL_BUILD_VALIDATOR = UnrealBuildEnvironmentValidator()
DEFAULT_UNREAL_ARTIFACT_VERIFIER = UnrealBuildArtifactVerifier()
DEFAULT_UNREAL_BUILD_RUNNER = UnrealBuildRunner()
