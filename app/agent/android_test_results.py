"""
Android JUnit & Lint Structured Result Parsing Subsystem

Parses JUnit XML test results and Android Lint XML reports into strongly typed
structures, extracts failure locations (file and line), redacts sensitive data,
and generates actionable repair targets for AndroidErrorAnalyzer.

Guarantees:
  - Strongly typed JUnit and Lint data models
  - Extraction of failing class, method, file, line number, and stack trace
  - Secret redaction on stack traces and failure messages
  - Conversion to AndroidBuildError for seamless integration with AndroidErrorAnalyzer
  - Resilient to malformed XML and bounded size limits (<= 100 KB)
"""

from __future__ import annotations

import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from app.agent.android_code_repair import (
    AndroidBuildError,
    AndroidErrorCategory,
    redact_sensitive_content,
)
from app.agent.android_safety import MAX_PATCH_SIZE_BYTES

logger = logging.getLogger("AndroidTestResults")


# -----------------------------------------------------------------------------
# Data Models
# -----------------------------------------------------------------------------

@dataclass
class TestCaseResult:
    classname: str
    name: str
    time_sec: float
    status: str  # PASSED, FAILED, ERROR, SKIPPED
    failure_message: Optional[str] = None
    failure_type: Optional[str] = None
    stack_trace: Optional[str] = None
    failing_file: Optional[str] = None
    failing_line: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TestSuiteResult:
    name: str
    tests: int
    failures: int
    errors: int
    skipped: int
    time_sec: float
    test_cases: List[TestCaseResult] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "tests": self.tests,
            "failures": self.failures,
            "errors": self.errors,
            "skipped": self.skipped,
            "time_sec": self.time_sec,
            "test_cases": [t.to_dict() for t in self.test_cases],
        }


@dataclass
class JUnitReport:
    total_tests: int
    total_failures: int
    total_errors: int
    total_skipped: int
    total_time_sec: float
    suites: List[TestSuiteResult]
    failures: List[TestCaseResult]
    is_success: bool
    timestamp: float = field(default_factory=time.time)

    @property
    def passed(self) -> int:
        return self.total_tests - self.total_failures - self.total_errors - self.total_skipped

    @property
    def failed(self) -> int:
        return self.total_failures

    @property
    def errors(self) -> int:
        return self.total_errors

    @property
    def skipped(self) -> int:
        return self.total_skipped

    def get_failed_tests(self) -> List[TestCaseResult]:
        return self.failures

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_tests": self.total_tests,
            "total_failures": self.total_failures,
            "total_errors": self.total_errors,
            "total_skipped": self.total_skipped,
            "total_time_sec": self.total_time_sec,
            "suites": [s.to_dict() for s in self.suites],
            "failures": [f.to_dict() for f in self.failures],
            "is_success": self.is_success,
            "timestamp": self.timestamp,
        }


@dataclass
class LintIssue:
    id: str
    severity: str  # Fatal, Error, Warning, Information
    message: str
    category: str
    priority: int
    summary: str
    explanation: str
    file_path: Optional[str] = None
    line: Optional[int] = None
    column: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LintReport:
    issues: List[LintIssue]
    fatal_count: int
    error_count: int
    warning_count: int
    info_count: int
    has_blocking_errors: bool
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "issues": [i.to_dict() for i in self.issues],
            "fatal_count": self.fatal_count,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "info_count": self.info_count,
            "has_blocking_errors": self.has_blocking_errors,
            "timestamp": self.timestamp,
        }


# -----------------------------------------------------------------------------
# Parser Implementation
# -----------------------------------------------------------------------------

class AndroidTestResultParser:
    """
    Parses JUnit XML and Lint XML reports and maps findings into repairable build errors.
    """

    STACK_LINE_PATTERN = re.compile(
        r'at\s+([a-zA-Z0-9_$.]+)\.([a-zA-Z0-9_$]+)\(([^:)]+\.(?:kt|java)):(\d+)\)'
    )

    def __init__(self):
        pass

    def parse_junit_xml(self, xml_input: Union[str, Path]) -> JUnitReport:
        """Parses a JUnit XML report file or string."""
        root = self._load_xml_root(xml_input)
        if root is None:
            return JUnitReport(0, 0, 0, 0, 0.0, [], [], True)

        suites: List[TestSuiteResult] = []
        all_failures: List[TestCaseResult] = []

        suite_elements = [root] if root.tag == "testsuite" else root.findall("testsuite")

        for suite_el in suite_elements:
            suite_name = suite_el.get("name", "UnknownSuite")
            tests_count = int(suite_el.get("tests", 0))
            failures_count = int(suite_el.get("failures", 0))
            errors_count = int(suite_el.get("errors", 0))
            skipped_count = int(suite_el.get("skipped", 0))
            time_val = float(suite_el.get("time", 0.0))

            test_cases: List[TestCaseResult] = []

            for case_el in suite_el.findall("testcase"):
                classname = case_el.get("classname", "")
                name = case_el.get("name", "")
                case_time = float(case_el.get("time", 0.0))

                failure_el = case_el.find("failure")
                error_el = case_el.find("error")
                skipped_el = case_el.find("skipped")

                if failure_el is not None:
                    status = "FAILED"
                    msg = redact_sensitive_content(failure_el.get("message", ""))
                    ftype = failure_el.get("type", "")
                    raw_trace = failure_el.text or ""
                    stack = redact_sensitive_content(raw_trace)
                    f_file, f_line = self._extract_stack_location(stack, classname)
                    case_res = TestCaseResult(
                        classname=classname,
                        name=name,
                        time_sec=case_time,
                        status=status,
                        failure_message=msg,
                        failure_type=ftype,
                        stack_trace=stack,
                        failing_file=f_file,
                        failing_line=f_line,
                    )
                    test_cases.append(case_res)
                    all_failures.append(case_res)

                elif error_el is not None:
                    status = "ERROR"
                    msg = redact_sensitive_content(error_el.get("message", ""))
                    ftype = error_el.get("type", "")
                    raw_trace = error_el.text or ""
                    stack = redact_sensitive_content(raw_trace)
                    f_file, f_line = self._extract_stack_location(stack, classname)
                    case_res = TestCaseResult(
                        classname=classname,
                        name=name,
                        time_sec=case_time,
                        status=status,
                        failure_message=msg,
                        failure_type=ftype,
                        stack_trace=stack,
                        failing_file=f_file,
                        failing_line=f_line,
                    )
                    test_cases.append(case_res)
                    all_failures.append(case_res)

                elif skipped_el is not None:
                    test_cases.append(TestCaseResult(
                        classname=classname,
                        name=name,
                        time_sec=case_time,
                        status="SKIPPED",
                    ))
                else:
                    test_cases.append(TestCaseResult(
                        classname=classname,
                        name=name,
                        time_sec=case_time,
                        status="PASSED",
                    ))

            suites.append(TestSuiteResult(
                name=suite_name,
                tests=tests_count,
                failures=failures_count,
                errors=errors_count,
                skipped=skipped_count,
                time_sec=time_val,
                test_cases=test_cases,
            ))

        total_tests = sum(s.tests for s in suites)
        total_failures = sum(s.failures for s in suites)
        total_errors = sum(s.errors for s in suites)
        total_skipped = sum(s.skipped for s in suites)
        total_time = sum(s.time_sec for s in suites)
        is_success = (total_failures == 0 and total_errors == 0)

        return JUnitReport(
            total_tests=total_tests,
            total_failures=total_failures,
            total_errors=total_errors,
            total_skipped=total_skipped,
            total_time_sec=total_time,
            suites=suites,
            failures=all_failures,
            is_success=is_success,
        )

    def parse_lint_xml(self, xml_input: Union[str, Path]) -> LintReport:
        """Parses an Android Lint XML report file or string."""
        root = self._load_xml_root(xml_input)
        if root is None:
            return LintReport([], 0, 0, 0, 0, False)

        issues: List[LintIssue] = []
        fatal_count = 0
        error_count = 0
        warning_count = 0
        info_count = 0

        for issue_el in root.findall("issue"):
            issue_id = issue_el.get("id", "")
            severity = issue_el.get("severity", "Warning")
            msg = redact_sensitive_content(issue_el.get("message", ""))
            category = issue_el.get("category", "")
            priority = int(issue_el.get("priority", 0))
            summary = issue_el.get("summary", "")
            explanation = issue_el.get("explanation", "")

            location_el = issue_el.find("location")
            f_path = location_el.get("file") if location_el is not None else None
            line_str = location_el.get("line") if location_el is not None else None
            col_str = location_el.get("column") if location_el is not None else None

            line_no = int(line_str) if line_str and line_str.isdigit() else None
            col_no = int(col_str) if col_str and col_str.isdigit() else None

            if severity == "Fatal":
                fatal_count += 1
            elif severity == "Error":
                error_count += 1
            elif severity == "Warning":
                warning_count += 1
            else:
                info_count += 1

            issues.append(LintIssue(
                id=issue_id,
                severity=severity,
                message=msg,
                category=category,
                priority=priority,
                summary=summary,
                explanation=explanation,
                file_path=f_path,
                line=line_no,
                column=col_no,
            ))

        has_blocking = (fatal_count > 0 or error_count > 0)
        return LintReport(
            issues=issues,
            fatal_count=fatal_count,
            error_count=error_count,
            warning_count=warning_count,
            info_count=info_count,
            has_blocking_errors=has_blocking,
        )

    def scan_project_results(self, project_root: Union[str, Path]) -> Dict[str, Any]:
        """Scans project build directories for all JUnit XML and Lint XML results."""
        root = Path(project_root).resolve()
        junit_reports: List[JUnitReport] = []
        lint_reports: List[LintReport] = []

        # Find test-results XMLs
        for xml_file in root.glob("**/build/test-results/**/*.xml"):
            if xml_file.is_file() and xml_file.stat().st_size <= MAX_PATCH_SIZE_BYTES:
                junit_reports.append(self.parse_junit_xml(xml_file))

        # Find lint reports
        for lint_file in root.glob("**/build/reports/lint-results*.xml"):
            if lint_file.is_file() and lint_file.stat().st_size <= MAX_PATCH_SIZE_BYTES:
                lint_reports.append(self.parse_lint_xml(lint_file))

        return {
            "junit_reports": [r.to_dict() for r in junit_reports],
            "lint_reports": [l.to_dict() for l in lint_reports],
            "total_test_suites": sum(len(r.suites) for r in junit_reports),
            "total_test_failures": sum(len(r.failures) for r in junit_reports),
            "total_lint_issues": sum(len(l.issues) for l in lint_reports),
        }

    def to_build_errors(
        self,
        junit_report: Optional[JUnitReport] = None,
        lint_report: Optional[LintReport] = None,
        project_root: Optional[Path] = None,
    ) -> List[AndroidBuildError]:
        """
        Converts JUnit test failures and Lint blocking errors into structured AndroidBuildError
        objects for direct consumption by AndroidErrorAnalyzer / AndroidAutonomousRepair.
        """
        build_errors: List[AndroidBuildError] = []

        # 1. Convert JUnit failures
        if junit_report and not junit_report.is_success:
            for failure in junit_report.failures:
                # Find matching file on disk if possible
                resolved_file = failure.failing_file
                if resolved_file and project_root and not Path(resolved_file).is_absolute():
                    matches = list(Path(project_root).glob(f"**/{resolved_file}"))
                    if matches:
                        resolved_file = str(matches[0])

                build_errors.append(AndroidBuildError(
                    category=AndroidErrorCategory.JAVA_COMPILE,
                    file_path=resolved_file,
                    line=failure.failing_line,
                    message=f"Test failure in {failure.classname}.{failure.name}(): {failure.failure_message}",
                    diagnosis=f"JUnit test failure with type '{failure.failure_type}'. Inspect stack trace and assert conditions.",
                    relevant_files=[resolved_file] if resolved_file else [],
                ))

        # 2. Convert Lint blocking errors
        if lint_report and lint_report.has_blocking_errors:
            for issue in lint_report.issues:
                if issue.severity in ("Fatal", "Error"):
                    build_errors.append(AndroidBuildError(
                        category=AndroidErrorCategory.RESOURCE_LINKING if "Resource" in issue.category else AndroidErrorCategory.JAVA_COMPILE,
                        file_path=issue.file_path,
                        line=issue.line,
                        column=issue.column,
                        message=f"Lint [{issue.id}]: {issue.message}",
                        diagnosis=f"Lint {issue.severity} error in category '{issue.category}': {issue.summary}",
                        relevant_files=[issue.file_path] if issue.file_path else [],
                    ))

        return build_errors

    def _extract_stack_location(self, stack_trace: str, classname: str) -> Tuple[Optional[str], Optional[int]]:
        """Extracts failing file and line number from stack trace."""
        if not stack_trace:
            return None, None

        # Look for the innermost frame matching the test class or top project frame
        lines = stack_trace.splitlines()
        for line in lines:
            m = self.STACK_LINE_PATTERN.search(line)
            if m:
                target_cls = m.group(1)
                filename = m.group(3)
                line_no = int(m.group(4))
                # Prioritize frames matching the test class
                if classname and classname in target_cls:
                    return filename, line_no

        # Fallback to first matched frame
        for line in lines:
            m = self.STACK_LINE_PATTERN.search(line)
            if m:
                return m.group(3), int(m.group(4))

        return None, None

    def _load_xml_root(self, xml_input: Union[str, Path]) -> Optional[ET.Element]:
        """Safely parses XML input from file or string with size limit and fault-tolerance."""
        try:
            if isinstance(xml_input, Path) or (isinstance(xml_input, str) and os.path.exists(xml_input)):
                path = Path(xml_input).resolve()
                if path.stat().st_size > MAX_PATCH_SIZE_BYTES:
                    logger.warning(f"Skipping oversized XML report: {path}")
                    return None
                tree = ET.parse(path)
                return tree.getroot()
            elif isinstance(xml_input, str):
                if len(xml_input.encode("utf-8")) > MAX_PATCH_SIZE_BYTES:
                    return None
                return ET.fromstring(xml_input)
        except Exception as e:
            logger.warning(f"Failed to parse XML report: {e}")
            return None
        return None
