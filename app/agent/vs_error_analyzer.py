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
from typing import Any, Dict, List, Optional

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
    PROJECT_CONFIGURATION_ERROR = "PROJECT_CONFIGURATION_ERROR"
    TEST_RUNNER_ERROR = "TEST_RUNNER_ERROR"
    TEST_FAILURE = "TEST_RUNNER_ERROR"
    ANALYZER_CODE_QUALITY = "ANALYZER_CODE_QUALITY"
    RUNTIME_LAUNCH_FAILURE = "RUNTIME_LAUNCH_FAILURE"
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

        # 2. Parse NuGet standalone errors:
        # e.g.: error NU1101: Unable to find package Newtonsoft.Json
        if not errors:
            nu_pattern = re.compile(r'(?i)error\s+(NU\d+)\s*:\s*(.+)', re.MULTILINE)
            for m in nu_pattern.finditer(clean_output):
                code = m.group(1).upper()
                msg = m.group(2).strip()
                errors.append(VSBuildError(
                    category=VSErrorCategory.NUGET_ERROR,
                    error_code=code,
                    message=msg,
                    diagnosis=f"NuGet package error {code}: check package source or version constraint.",
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
        """Determines category based on compiler prefix."""
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
            return VSErrorCategory.MSBUILD_ERROR
        elif code_upper.startswith("NU"):
            return VSErrorCategory.NUGET_ERROR
        elif code_upper.startswith("CA") or code_upper.startswith("IDE"):
            return VSErrorCategory.ANALYZER_CODE_QUALITY
        return VSErrorCategory.UNKNOWN_VS_ERROR

    def _parse_test_failures(self, output: str) -> List[VSBuildError]:
        """Detects unit test failures from vstest, dotnet test, xUnit, NUnit, MSTest."""
        test_errors: List[VSBuildError] = []

        # Pattern for dotnet test: "Failed TestName [12 ms]" or "[FAIL] TestName"
        fail_re = re.compile(r'(?:Failed|\[FAIL\])\s+([A-Za-z0-9_\.]+)\s*(?:\[\d+\s*ms\])?', re.MULTILINE)
        msg_re = re.compile(r'Error Message:\s*\r?\n\s*(.+)', re.MULTILINE)
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
            return f"C# Name Resolution: Identifier is missing or not declared in the current scope. Check spelling or imports."
        elif code == "CS0246":
            return f"C# Type Missing: Type or namespace was not found. Verify using directives or project references."
        elif code == "CS1002":
            return f"C# Syntax: Semicolon ';' expected."
        elif code == "CS1503":
            return f"C# Type Mismatch: Argument cannot convert to parameter type."
        elif code == "MSB4019":
            return f"MSBuild Import Failure: Imported props/targets file was not found."
        elif code == "NU1101":
            return f"NuGet Dependency Failure: Unable to locate package in configured feeds."
        elif cat == VSErrorCategory.TEST_FAILURE:
            return f"Test Assertion Failure: Unit test failed assertion condition."
        return f"{cat.value} ({code}): {msg}"
