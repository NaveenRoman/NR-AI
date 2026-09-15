"""
NR-AI Visual Studio & MSBuild Error Analysis Engine (Step 7).

Parses MSBuild and compiler error streams, classifying diagnostics across:
- C# compiler errors (CSxxxx)
- VB.NET compiler errors (BCxxxx)
- F# compiler errors (FSxxxx)
- MSBuild engine errors (MSBxxxx)
- NuGet package resolution errors (NUxxxx)
- Missing reference and dependency errors
- Project configuration errors
- Test runner failures (MSTest, NUnit, xUnit)
- Code quality and Roslyn analyzer warnings/errors (CAxxxx, IDExxxx)
- Runtime launch failures
"""

from dataclasses import dataclass, field
from enum import Enum
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple

from app.agent.vs_safety import redact_sensitive_data, GLOBAL_WORKSPACE_ROOT

logger = logging.getLogger("NRAI.VSErrorAnalyzer")


class VSErrorCategory(str, Enum):
    CS_COMPILER_ERROR = "CS_COMPILER_ERROR"
    VB_COMPILER_ERROR = "VB_COMPILER_ERROR"
    FS_COMPILER_ERROR = "FS_COMPILER_ERROR"
    MSBUILD_ERROR = "MSBUILD_ERROR"
    NUGET_ERROR = "NUGET_ERROR"
    NUGET_PACKAGE_ERROR = "NUGET_ERROR"
    MISSING_REFERENCE = "MISSING_REFERENCE"
    MISSING_SDK = "MISSING_SDK"
    MISSING_TARGET_FRAMEWORK = "MISSING_TARGET_FRAMEWORK"
    PACKAGE_VERSION_CONFLICT = "PACKAGE_VERSION_CONFLICT"
    PROJECT_CONFIGURATION_ERROR = "PROJECT_CONFIGURATION_ERROR"
    TEST_RUNNER_ERROR = "TEST_RUNNER_ERROR"
    TEST_FAILURE = "TEST_RUNNER_ERROR"
    ANALYZER_CODE_QUALITY = "ANALYZER_CODE_QUALITY"
    LINKER_TRIMMING_ERROR = "LINKER_TRIMMING_ERROR"
    BUILD_TIMEOUT = "BUILD_TIMEOUT"
    PROCESS_LAUNCH_FAILURE = "PROCESS_LAUNCH_FAILURE"
    RUNTIME_LAUNCH_FAILURE = "RUNTIME_LAUNCH_FAILURE"
    RUNTIME_CRASH = "RUNTIME_CRASH"
    MISSING_RUNTIME = "MISSING_RUNTIME"
    MISSING_DEPENDENCY = "MISSING_DEPENDENCY"
    PORT_BIND_FAILURE = "PORT_BIND_FAILURE"
    CONFIGURATION_FAILURE = "CONFIGURATION_FAILURE"
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    PERMISSION_FAILURE = "PERMISSION_FAILURE"
    UNHANDLED_EXCEPTION = "UNHANDLED_EXCEPTION"
    TIMEOUT = "TIMEOUT"
    UNKNOWN_VS_ERROR = "UNKNOWN_VS_ERROR"



@dataclass
class VSBuildError:
    """Structured diagnostic representation of a Visual Studio or MSBuild error."""
    category: VSErrorCategory
    error_code: str = ""
    message: str = ""
    file_path: Optional[str] = None
    line: Optional[int] = None
    column: Optional[int] = None
    project_file: Optional[str] = None
    diagnosis: str = ""
    relevant_files: List[str] = field(default_factory=list)
    code: Optional[str] = None

    def __post_init__(self):
        if self.code and not self.error_code:
            self.error_code = self.code
        elif self.error_code and not self.code:
            self.code = self.error_code

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category.value,
            "error_code": self.error_code,
            "code": self.error_code,
            "message": self.message,
            "file_path": self.file_path,
            "line": self.line,
            "column": self.column,
            "project_file": self.project_file,
            "diagnosis": self.diagnosis,
            "relevant_files": self.relevant_files,
        }


class VSErrorAnalyzer:
    """
    Analyzes compiler output, MSBuild logs, and test reports.
    Extracts structured VSBuildError items mapped to project files.
    """

    def __init__(self, project_root: Optional[Path] = None):
        self.project_root = Path(project_root or GLOBAL_WORKSPACE_ROOT).resolve()

    def parse_build_output(self, output_text: str) -> List[VSBuildError]:
        """Alias for analyze."""
        return self.analyze(output_text)

    def analyze(self, output_text: str) -> List[VSBuildError]:
        """Parses output text and returns structured error list."""
        if not output_text:
            return []

        clean_output = redact_sensitive_data(output_text)
        errors: List[VSBuildError] = []

        # 1. Parse standard MSBuild error lines:
        # e.g.: path\to\file.cs(12,15): error CS0103: The name 'x' does not exist in the current context [proj.csproj]
        # or: path\to\proj.csproj : error MSB4019: The imported project ... was not found.
        msbuild_pattern = re.compile(
            r'^(?P<path>[a-zA-Z]:[^\r\n:]+?|[^:\r\n]+?\.[a-zA-Z0-9]+)\s*'
            r'(?:\((?P<line>\d+)(?:,(?P<col>\d+))?\))?\s*:\s*'
            r'(?P<severity>error|fatal error)\s+'
            r'(?P<code>[A-Za-z]+[0-9]+)\s*:\s*'
            r'(?P<msg>[^\[\r\n]+)'
            r'(?:\s*\[(?P<proj>[^\]]+)\])?',
            re.MULTILINE
        )

        for m in msbuild_pattern.finditer(clean_output):
            raw_path = m.group("path").strip()
            line = int(m.group("line")) if m.group("line") else None
            col = int(m.group("col")) if m.group("col") else None
            code = m.group("code").strip().upper()
            msg = m.group("msg").strip()
            proj = m.group("proj").strip() if m.group("proj") else None

            cat = self._categorize_code(code, msg)
            fpath = self._resolve_file_path(raw_path)

            diag = self._generate_diagnosis(cat, code, msg)
            errors.append(VSBuildError(
                category=cat,
                error_code=code,
                message=msg,
                file_path=fpath,
                line=line,
                column=col,
                project_file=proj,
                diagnosis=diag,
                relevant_files=[fpath] if fpath else [],
            ))

        # 2. Parse standalone compiler / MSBuild / NuGet errors without file path:
        # e.g.: error MSB4236: The SDK 'Microsoft.NET.Sdk' specified could not be found.
        # or: error NU1101: Unable to find package Newtonsoft.Json
        if not errors:
            standalone_pattern = re.compile(r'(?i)(?:error|fatal error)\s+([A-Za-z]+[0-9]+)\s*:\s*([^\r\n]+)', re.MULTILINE)
            for m in standalone_pattern.finditer(clean_output):
                code = m.group(1).upper()
                msg = m.group(2).strip()
                cat = self._categorize_code(code, msg)
                diag = self._generate_diagnosis(cat, code, msg)
                errors.append(VSBuildError(
                    category=cat,
                    error_code=code,
                    message=msg,
                    diagnosis=diag,
                ))

        # 3. Parse Test Failures:
        # e.g. "Failed MyTestName [25 ms]" or "Error Message: Assert.AreEqual failed..."
        test_errors = self._parse_test_failures(clean_output)
        errors.extend(test_errors)

        # 4. Fallback: general build failure detection
        if not errors and any(k in clean_output.lower() for k in ("build failed", "compilation failed", "msbuild: error")):
            errors.append(VSBuildError(
                category=VSErrorCategory.UNKNOWN_VS_ERROR,
                error_code="BUILD_FAILED",
                message=clean_output[:300].strip(),
                diagnosis="Unspecified build failure detected in MSBuild console output.",
            ))

        return errors

    def _categorize_code(self, code: str, message: str) -> VSErrorCategory:
        """Determines category based on compiler prefix and diagnostic message."""
        code_upper = code.upper()
        msg_lower = message.lower()

        if code_upper.startswith("CS"):
            if any(k in msg_lower for k in ("missing reference", "type or namespace name", "could not be found")):
                return VSErrorCategory.MISSING_REFERENCE
            return VSErrorCategory.CS_COMPILER_ERROR
        elif code_upper.startswith("BC"):
            return VSErrorCategory.VB_COMPILER_ERROR
        elif code_upper.startswith("FS"):
            return VSErrorCategory.FS_COMPILER_ERROR
        elif code_upper.startswith("MSB"):
            if code_upper == "MSB4236" or "the sdk" in msg_lower:
                return VSErrorCategory.MISSING_SDK
            return VSErrorCategory.MSBUILD_ERROR
        elif code_upper.startswith("NU"):
            if code_upper in ("NU1605", "NU1107", "NU1108") or any(k in msg_lower for k in ("version conflict", "downgrade", "detected package downgrade")):
                return VSErrorCategory.PACKAGE_VERSION_CONFLICT
            return VSErrorCategory.NUGET_ERROR
        elif code_upper.startswith("NETSDK"):
            if code_upper == "NETSDK1045" or "does not support targeting" in msg_lower:
                return VSErrorCategory.MISSING_TARGET_FRAMEWORK
            return VSErrorCategory.PROJECT_CONFIGURATION_ERROR
        elif code_upper.startswith("IL"):
            return VSErrorCategory.LINKER_TRIMMING_ERROR
        elif code_upper.startswith("CA") or code_upper.startswith("IDE"):
            return VSErrorCategory.ANALYZER_CODE_QUALITY
        return VSErrorCategory.UNKNOWN_VS_ERROR

    def _parse_test_failures(self, output: str) -> List[VSBuildError]:
        """Detects unit test failures from vstest, dotnet test, xUnit, NUnit, MSTest."""
        test_errors: List[VSBuildError] = []

        # Pattern for dotnet test: "Failed TestName [12 ms]" or "[FAIL] TestName"
        fail_re = re.compile(r'(?:Failed|\[FAIL\])\s+([A-Za-z0-9_\.]+)\s*(?:\[\d+\s*ms\])?', re.MULTILINE)
        msg_re = re.compile(r'Error Message:\s*(?:\r?\n\s*)?(.+)', re.MULTILINE)
        stack_re = re.compile(r'Stack Trace:\s*\r?\n\s*(?:at\s+.*in\s+(.+):line\s+(\d+))?', re.MULTILINE)

        for m in fail_re.finditer(output):
            test_name = m.group(1).strip()
            # Look for adjacent error message
            sub_text = output[m.end():m.end() + 1000]
            m_msg = msg_re.search(sub_text)
            err_msg = m_msg.group(1).strip() if m_msg else f"Test '{test_name}' failed."

            m_stack = stack_re.search(sub_text)
            f_path = m_stack.group(1).strip() if (m_stack and m_stack.group(1)) else None
            line = int(m_stack.group(2)) if (m_stack and m_stack.group(2)) else None

            resolved_path = self._resolve_file_path(f_path) if f_path else None

            test_errors.append(VSBuildError(
                category=VSErrorCategory.TEST_RUNNER_ERROR,
                error_code="TEST_FAILED",
                message=f"{test_name}: {err_msg}",
                file_path=resolved_path,
                line=line,
                diagnosis=f"Assertion failed in test '{test_name}'.",
                relevant_files=[resolved_path] if resolved_path else [],
            ))

        return test_errors

    def parse_test_summary(self, output: str) -> Dict[str, Any]:
        """Parses high-level test counts from dotnet test or vstest output."""
        clean = redact_sensitive_data(output or "")
        # Look for "Passed! - Failed: 0, Passed: 5, Skipped: 0, Total: 5, Duration: 120 ms"
        # or "Total tests: 5. Passed: 5. Failed: 0. Skipped: 0."
        total, passed, failed, skipped = 0, 0, 0, 0
        duration_s = 0.0

        m_summary = re.search(r'Failed:\s*(\d+),\s*Passed:\s*(\d+),\s*Skipped:\s*(\d+),\s*Total:\s*(\d+)', clean)
        if m_summary:
            failed = int(m_summary.group(1))
            passed = int(m_summary.group(2))
            skipped = int(m_summary.group(3))
            total = int(m_summary.group(4))
        else:
            m_alt = re.search(r'Total tests:\s*(\d+).*?Passed:\s*(\d+).*?Failed:\s*(\d+).*?Skipped:\s*(\d+)', clean, re.DOTALL)
            if m_alt:
                total = int(m_alt.group(1))
                passed = int(m_alt.group(2))
                failed = int(m_alt.group(3))
                skipped = int(m_alt.group(4))
            else:
                # Count individual PASS/FAIL lines
                passed = len(re.findall(r'(?i)(?:Passed|\[PASS\])\s+[A-Za-z0-9_\.]+', clean))
                failed = len(re.findall(r'(?i)(?:Failed|\[FAIL\])\s+[A-Za-z0-9_\.]+', clean))
                skipped = len(re.findall(r'(?i)(?:Skipped|\[SKIP\])\s+[A-Za-z0-9_\.]+', clean))
                total = passed + failed + skipped

        m_dur = re.search(r'Duration:\s*(\d+(?:\.\d+)?)\s*(ms|s)', clean)
        if m_dur:
            val = float(m_dur.group(1))
            unit = m_dur.group(2)
            duration_s = val / 1000.0 if unit == "ms" else val

        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "success": (failed == 0 and total > 0),
            "duration_s": round(duration_s, 3),
        }

    def classify_runtime_failure(self, output_text: str, exit_code: Optional[int] = None) -> VSBuildError:
        """Deterministically classifies runtime execution errors and crashes."""
        clean = redact_sensitive_data(output_text or "")
        clean_lower = clean.lower()

        # Extract file and line from stack trace if present: e.g. "in C:\path\file.cs:line 25"
        f_path = None
        line_num = None
        m_stack = re.search(r'in\s+([a-zA-Z]:[^\r\n:]+?|[^:\r\n]+?\.[a-zA-Z0-9]+):line\s+(\d+)', clean)
        if m_stack:
            f_path = self._resolve_file_path(m_stack.group(1).strip())
            line_num = int(m_stack.group(2))

        cat = VSErrorCategory.RUNTIME_CRASH
        err_code = "RUNTIME_CRASH"
        diag = "Process terminated unexpectedly."
        first_line = clean.splitlines()[0] if clean.splitlines() else f"Runtime failure (exit code {exit_code})"

        if exit_code in (-1073741819, 3221225477):
            cat = VSErrorCategory.RUNTIME_CRASH
            err_code = "STATUS_ACCESS_VIOLATION"
            diag = "Access violation (0xC0000005) - Process dereferenced invalid memory."
            first_line = f"Process terminated due to access violation (0xC0000005). {first_line}"
        elif "you must install or update .net" in clean_lower or "missing runtime" in clean_lower:
            cat = VSErrorCategory.MISSING_RUNTIME
            err_code = "MISSING_RUNTIME"
            diag = "Required .NET Runtime is not installed on the system."
        elif "could not load file or assembly" in clean_lower:
            cat = VSErrorCategory.MISSING_DEPENDENCY
            err_code = "MISSING_DEPENDENCY"
            diag = "A required assembly or dependency could not be resolved at runtime."
        elif any(k in clean_lower for k in ("failed to bind to address", "address already in use", "port already in use")):
            cat = VSErrorCategory.PORT_BIND_FAILURE
            err_code = "PORT_BIND_FAILURE"
            diag = "The network port is already in use by another process."
        elif "unauthorizedaccessexception" in clean_lower or "permission denied" in clean_lower:
            cat = VSErrorCategory.PERMISSION_FAILURE
            err_code = "PERMISSION_FAILURE"
            diag = "Access to the requested file or resource was denied."
        elif "filenotfoundexception" in clean_lower:
            cat = VSErrorCategory.FILE_NOT_FOUND
            err_code = "FILE_NOT_FOUND"
            diag = "A required runtime file or asset was not found."
        elif any(k in clean_lower for k in ("connection string", "configuration exception", "invalidoperationexception: connection")):
            cat = VSErrorCategory.CONFIGURATION_FAILURE
            err_code = "CONFIGURATION_FAILURE"
            diag = "Runtime configuration error: missing or invalid configuration key."
        elif "timed out" in clean_lower:
            cat = VSErrorCategory.TIMEOUT
            err_code = "TIMEOUT"
            diag = "Process execution timed out."
        elif "unhandled exception" in clean_lower:
            cat = VSErrorCategory.UNHANDLED_EXCEPTION
            err_code = "UNHANDLED_EXCEPTION"
            diag = "An unhandled exception caused the application to terminate."
        elif exit_code and exit_code != 0:
            cat = VSErrorCategory.RUNTIME_CRASH
            err_code = f"EXIT_{exit_code}"
            diag = f"Process exited with non-zero exit code: {exit_code}."

        return VSBuildError(
            category=cat,
            error_code=err_code,
            message=first_line[:300],
            file_path=f_path,
            line=line_num,
            diagnosis=diag,
            relevant_files=[f_path] if f_path else [],
        )

    def _resolve_file_path(self, raw_path: str) -> Optional[str]:
        if not raw_path:
            return None
        p = Path(raw_path.strip().replace("/", os.sep))
        if p.is_absolute():
            return str(p)
        candidate = (self.project_root / p).resolve()
        if candidate.exists():
            return str(candidate)
        return str(p)

    def _generate_diagnosis(self, cat: VSErrorCategory, code: str, msg: str) -> str:
        """Generates actionable explanation for common errors."""
        if code == "CS0103":
            return f"[{code}] C# Name Resolution: Identifier is missing or not declared in the current scope. Check spelling or imports."
        elif code == "CS0246":
            return f"[{code}] C# Type Missing: Type or namespace was not found. Verify using directives or project references."
        elif code == "CS1002":
            return f"[{code}] C# Syntax: Semicolon ';' expected."
        elif code == "CS1503":
            return f"[{code}] C# Type Mismatch: Argument cannot convert to parameter type."
        elif code == "BC30451":
            return f"[{code}] VB.NET Name Resolution: Identifier is not declared. Check spelling or imports."
        elif code == "FS0001":
            return f"[{code}] F# Type Mismatch: This expression was expected to have a different type."
        elif code == "MSB4019":
            return f"[{code}] MSBuild Import Failure: Imported props/targets file was not found."
        elif code == "MSB4236":
            return f"[{code}] MSBuild SDK Failure: The specified SDK could not be found."
        elif code == "NU1101":
            return f"[{code}] NuGet Dependency Failure: Unable to locate package in configured feeds."
        elif code == "NU1605":
            return f"[{code}] NuGet Version Conflict: Package downgrade or conflict detected."
        elif cat == VSErrorCategory.TEST_FAILURE:
            return f"[{code or 'TEST'}] Test Assertion Failure: Unit test failed assertion condition."
        elif cat == VSErrorCategory.MISSING_TARGET_FRAMEWORK:
            return f"[{code or 'NETSDK'}] Missing Target Framework: Installed .NET SDK does not support requested TargetFramework."
        return f"{cat.value} ({code}): {msg}"

    def is_repairable_error(self, error: VSBuildError) -> Tuple[bool, str]:
        """
        Deterministically evaluates whether an error is safely repairable by autonomous code editing.
        Returns:
            (True, "Rationale for repairability") if repairable.
            (False, "Explanation of why error cannot safely be repaired automatically") if not.
        """
        if not error:
            return False, "No error diagnostic provided."

        cat = error.category
        code = (error.error_code or "").upper()
        msg = (error.message or "").lower()
        file_p = error.file_path or ""

        # 1. Check for protected files
        file_lower = Path(file_p).name.lower()
        if file_lower in ("secrets.json", ".env", "appsettings.production.json") or file_lower.endswith((".snk", ".pfx", ".key")):
            return False, f"Target file '{file_lower}' is a protected security asset and cannot be modified automatically."

        # 2. Check explicitly non-repairable categories
        unrepairable_categories = {
            VSErrorCategory.MISSING_SDK,
            VSErrorCategory.MISSING_RUNTIME,
            VSErrorCategory.MISSING_TARGET_FRAMEWORK,
            VSErrorCategory.PERMISSION_FAILURE,
            VSErrorCategory.BUILD_TIMEOUT,
            VSErrorCategory.TIMEOUT,
            VSErrorCategory.PROCESS_LAUNCH_FAILURE,
        }
        if cat in unrepairable_categories:
            return False, f"Error category '{cat.value}' represents an environment or host SDK failure outside project code boundary."

        # 3. Check for external/environment tokens in message or code
        unrepairable_tokens = [
            "missing sdk",
            "the sdk '",
            "msb4236",
            "netsdk1045",
            "authentication failed",
            "credentials",
            "secret",
            "unauthorized",
            "permission denied",
            "access denied",
            "nuget feed",
            "401 unauthorized",
            "403 forbidden",
            "debugger",
            "status_access_violation",
        ]
        for tok in unrepairable_tokens:
            if tok in msg or tok in code.lower():
                return False, f"Diagnostic indicates external or security constraint ('{tok}') that cannot be repaired safely."

        # 4. Check for arbitrary package installation requirements
        if cat == VSErrorCategory.NUGET_ERROR and any(k in msg for k in ("unable to find package", "package source")):
            return False, "Missing external NuGet package requires network/feed installation outside autonomous code repair boundary."

        # 5. Check repairable categories
        repairable_categories = {
            VSErrorCategory.CS_COMPILER_ERROR,
            VSErrorCategory.VB_COMPILER_ERROR,
            VSErrorCategory.FS_COMPILER_ERROR,
            VSErrorCategory.MISSING_REFERENCE,
            VSErrorCategory.ANALYZER_CODE_QUALITY,
            VSErrorCategory.TEST_RUNNER_ERROR,
            VSErrorCategory.PROJECT_CONFIGURATION_ERROR,
            VSErrorCategory.CONFIGURATION_FAILURE,
        }

        if cat in repairable_categories:
            # Source file check: must have a target file path
            if not file_p and cat not in (VSErrorCategory.PROJECT_CONFIGURATION_ERROR, VSErrorCategory.CONFIGURATION_FAILURE):
                return False, f"Error '{code}' lacks an identifiable target source file for editing."
            return True, f"Eligible for autonomous repair: {cat.value} ({code}) in '{Path(file_p).name if file_p else 'configuration'}'."

        if code.startswith(("CS", "BC", "FS")):
            return True, f"Compiler error '{code}' is eligible for autonomous source code repair."

        return False, f"Error '{code}' in category '{cat.value}' is not within authorized autonomous repair patterns."

    def is_repairable(self, error: VSBuildError) -> bool:
        """Boolean convenience wrapper for is_repairable_error."""
        repairable, _ = self.is_repairable_error(error)
        return repairable
