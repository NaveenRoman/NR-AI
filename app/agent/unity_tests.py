r"""
NR-AI Unity Agent Test Runner & PlayMode Foundation (Step 8 Phase 3).

Safe, bounded, deterministic foundation for inspecting, executing, observing,
diagnosing, and verifying Unity EditMode and PlayMode tests:
- EditMode and PlayMode test execution (-batchmode -runTests -testPlatform)
- Strict test mode, filter, and output path validation
- Structured data models (TestCaseResult, TestSuiteResult, TestSummary, TestExecutionResult)
- Safe NUnit3/Unity XML test result parser
- Deterministic test failure classification (11 distinct categories)
- Evidence-based artifact verification (SHA-256, XML validation, size check)
- 100% shell=False subprocess isolation & process tree cleanup
- Thread-safe emergency stop integration
- Sensitive data redaction across outputs, logs, and stack traces
"""

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
import xml.etree.ElementTree as ET

from app.agent.unity_safety import (
    ALLOWED_TEST_MODES,
    BUILD_TIMEOUT_SECONDS,
    TEST_TIMEOUT_SECONDS,
    MAX_TEST_RESULT_FILE_SIZE_BYTES,
    UnityErrorCode,
    UnitySafetyError,
    EmergencyStopActiveError,
    UnitySafetyGate,
    DEFAULT_UNITY_SAFETY_GATE,
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
from app.agent.unity_build import (
    UnityProcessRunner,
    UnityLogParser,
)

logger = logging.getLogger("NRAI.UnityTests")

DEFAULT_TEST_TIMEOUT: float = 120.0
MAX_CAPTURED_OUTPUT_BYTES: int = 100_000


# -----------------------------------------------------------------------------
# Enums & Structured Data Models
# -----------------------------------------------------------------------------

class TestStatus(str, Enum):
    PASSED = "Passed"
    FAILED = "Failed"
    SKIPPED = "Skipped"
    INCONCLUSIVE = "Inconclusive"


class TestFailureCategory(str, Enum):
    ASSERTION_FAILURE = "ASSERTION_FAILURE"
    TEST_ERROR = "TEST_ERROR"
    TEST_TIMEOUT = "TEST_TIMEOUT"
    TEST_DISCOVERY_FAILURE = "TEST_DISCOVERY_FAILURE"
    COMPILE_FAILURE = "COMPILE_FAILURE"
    RUNTIME_FAILURE = "RUNTIME_FAILURE"
    PLAYMODE_FAILURE = "PLAYMODE_FAILURE"
    EDITMODE_FAILURE = "EDITMODE_FAILURE"
    TEST_RUNNER_FAILURE = "TEST_RUNNER_FAILURE"
    DEVICE_OR_ENVIRONMENT_FAILURE = "DEVICE_OR_ENVIRONMENT_FAILURE"
    UNKNOWN_TEST_FAILURE = "UNKNOWN_TEST_FAILURE"


@dataclass
class TestFailure:
    """Represents a structured test failure with classification."""
    message: str
    stack_trace: str = ""
    category: TestFailureCategory = TestFailureCategory.UNKNOWN_TEST_FAILURE
    raw_text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message": redact_sensitive_data(self.message),
            "stack_trace": redact_sensitive_data(self.stack_trace),
            "category": self.category.value,
            "raw_text": redact_sensitive_data(self.raw_text),
        }


@dataclass
class TestCaseResult:
    """Result of an individual test method."""
    name: str
    full_name: str
    class_name: str = ""
    method_name: str = ""
    assembly: str = ""
    status: TestStatus = TestStatus.PASSED
    duration_seconds: float = 0.0
    failure: Optional[TestFailure] = None
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "full_name": self.full_name,
            "class_name": self.class_name,
            "method_name": self.method_name,
            "assembly": self.assembly,
            "status": self.status.value,
            "duration_seconds": round(self.duration_seconds, 3),
            "failure": self.failure.to_dict() if self.failure else None,
            "message": redact_sensitive_data(self.message),
        }


@dataclass
class TestSuiteResult:
    """Result of a test fixture, class, or assembly suite."""
    name: str
    full_name: str
    suite_type: str = "Assembly"
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    inconclusive: int = 0
    duration_seconds: float = 0.0
    cases: List[TestCaseResult] = field(default_factory=list)
    suites: List["TestSuiteResult"] = field(default_factory=list)

    @property
    def test_cases(self) -> List[TestCaseResult]:
        """Alias for cases."""
        return self.cases

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "full_name": self.full_name,
            "suite_type": self.suite_type,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "inconclusive": self.inconclusive,
            "duration_seconds": round(self.duration_seconds, 3),
            "cases": [c.to_dict() for c in self.cases],
            "suites": [s.to_dict() for s in self.suites],
        }


@dataclass
class TestSummary:
    """High-level summary of a test run."""
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    inconclusive: int = 0
    duration_seconds: float = 0.0
    pass_rate: float = 0.0
    failure_categories: Dict[str, int] = field(default_factory=dict)
    status: str = "PASSED"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "inconclusive": self.inconclusive,
            "duration_seconds": round(self.duration_seconds, 3),
            "pass_rate": round(self.pass_rate, 2),
            "failure_categories": dict(self.failure_categories),
            "status": self.status,
        }


@dataclass
class TestArtifactInfo:
    """Verification metadata for a test result XML artifact."""
    path: str
    exists: bool = False
    size_bytes: int = 0
    sha256: str = ""
    verified: bool = False
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "exists": self.exists,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "verified": self.verified,
            "error": self.error,
        }


@dataclass
class TestExecutionResult:
    """Comprehensive result of a Unity test runner execution."""
    success: bool = False
    test_mode: str = "EditMode"
    summary: Optional[TestSummary] = None
    cases: List[TestCaseResult] = field(default_factory=list)
    failures: List[TestFailure] = field(default_factory=list)
    result_xml_path: Optional[str] = None
    artifact_info: Optional[TestArtifactInfo] = None
    log_path: Optional[str] = None
    exit_code: int = 0
    duration_seconds: float = 0.0
    output_preview: str = ""
    verified: bool = False
    error_summary: str = ""
    timestamp: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "test_mode": self.test_mode,
            "summary": self.summary.to_dict() if self.summary else None,
            "cases": [c.to_dict() for c in self.cases],
            "failures": [f.to_dict() for f in self.failures],
            "result_xml_path": self.result_xml_path,
            "artifact_info": self.artifact_info.to_dict() if self.artifact_info else None,
            "log_path": self.log_path,
            "exit_code": self.exit_code,
            "duration_seconds": round(self.duration_seconds, 2),
            "output_preview": self.output_preview,
            "verified": self.verified,
            "error_summary": self.error_summary,
            "timestamp": self.timestamp,
        }


# -----------------------------------------------------------------------------
# Test Failure Classifier
# -----------------------------------------------------------------------------

class UnityTestFailureClassifier:
    """
    Deterministically classifies test failures into distinct categories:
    - ASSERTION_FAILURE: NUnit / Unity Assert failure
    - TEST_ERROR: Unhandled exception in test code
    - TEST_TIMEOUT: Test execution timeout
    - TEST_DISCOVERY_FAILURE: No tests found or discovery exception
    - COMPILE_FAILURE: Scripts failed compilation
    - RUNTIME_FAILURE: Engine / null reference crash during test execution
    - PLAYMODE_FAILURE: PlayMode lifecycle / scene / physics failure
    - EDITMODE_FAILURE: EditMode domain reload / editor failure
    - TEST_RUNNER_FAILURE: Unity test runner binary / runner process crash
    - DEVICE_OR_ENVIRONMENT_FAILURE: Host environment / licensing / path missing
    - UNKNOWN_TEST_FAILURE: Unclassified fallback
    """

    RE_ASSERTION = re.compile(
        r"(?i)(Expected:.*But was:|AssertionException|Assert\.|Assert\s+failed|Assertion failed)"
    )
    RE_COMPILATION = re.compile(
        r"(?i)(error CS\d{4}|Scripts have compiler errors|CompilerOutput:-error|Compilation failed)"
    )
    RE_RUNNER_CRASH = re.compile(
        r"(?i)(Fatal Error|UnityTestRunner crashed|Process crashed|Abnormal termination|CrashReport)"
    )
    RE_DISCOVERY = re.compile(
        r"(?i)(No tests found|Could not find test|Test not found|TestFilter did not match)"
    )
    RE_ENVIRONMENT = re.compile(
        r"(?i)(No valid Unity license|Unity license error|License not found|Failed to activate license|UnityEditor\.exe not found)"
    )
    RE_RUNTIME = re.compile(
        r"(?i)(NullReferenceException|MissingReferenceException|IndexOutOfRangeException|InvalidOperationException|StackOverflowException)"
    )
    RE_TIMEOUT = re.compile(
        r"(?i)(timed out|timeout|BUILD_TIMEOUT|TEST_TIMEOUT)"
    )

    @classmethod
    def classify(
        cls,
        message: str = "",
        stack_trace: str = "",
        log_text: str = "",
        exit_code: int = 0,
        test_mode: str = "EditMode",
        timed_out: bool = False,
    ) -> TestFailureCategory:
        """Classifies a failure deterministically based on available evidence."""
        combined = f"{message}\n{stack_trace}\n{log_text}".strip()

        if timed_out or cls.RE_TIMEOUT.search(combined):
            return TestFailureCategory.TEST_TIMEOUT

        # 1. Environment / licensing errors
        if cls.RE_ENVIRONMENT.search(combined):
            return TestFailureCategory.DEVICE_OR_ENVIRONMENT_FAILURE

        # 2. C# Compilation errors
        if cls.RE_COMPILATION.search(combined):
            return TestFailureCategory.COMPILE_FAILURE

        # 3. Test runner process crash
        if cls.RE_RUNNER_CRASH.search(combined) or (exit_code not in (0, 1, 2) and not message):
            return TestFailureCategory.TEST_RUNNER_FAILURE

        # 4. Test discovery failure
        if cls.RE_DISCOVERY.search(combined):
            return TestFailureCategory.TEST_DISCOVERY_FAILURE

        # 5. Assertion failures
        if cls.RE_ASSERTION.search(message) or cls.RE_ASSERTION.search(stack_trace):
            return TestFailureCategory.ASSERTION_FAILURE

        # 6. Specific runtime exceptions
        if cls.RE_RUNTIME.search(message) or cls.RE_RUNTIME.search(stack_trace):
            return TestFailureCategory.RUNTIME_FAILURE

        # 7. Mode-specific failures
        if test_mode == "PlayMode" and ("scene" in combined.lower() or "playmode" in combined.lower()):
            return TestFailureCategory.PLAYMODE_FAILURE
        if test_mode == "EditMode" and ("editmode" in combined.lower() or "assetdatabase" in combined.lower()):
            return TestFailureCategory.EDITMODE_FAILURE

        # 8. General test error vs unknown
        if message or stack_trace:
            return TestFailureCategory.TEST_ERROR

        if exit_code != 0:
            return TestFailureCategory.TEST_RUNNER_FAILURE

        return TestFailureCategory.UNKNOWN_TEST_FAILURE


# -----------------------------------------------------------------------------
# XML Test Result Parser (NUnit3 & Unity Test Runner)
# -----------------------------------------------------------------------------

class UnityTestResultParser:
    """
    Safely parses NUnit3 and Unity Test Runner XML results into structured objects.
    Enforces maximum file size, sensitive data redaction, and deterministic totals.
    """

    @classmethod
    def parse_xml_string(
        cls,
        xml_content: str,
        test_mode: str = "EditMode",
    ) -> Tuple[TestSummary, List[TestCaseResult], List[TestFailure]]:
        """Parses XML string safely into (summary, test_cases, failures)."""
        if not xml_content or not xml_content.strip():
            raise UnitySafetyError(
                UnityErrorCode.TEST_PARSER_ERROR,
                "Test result XML content is empty.",
            )

        if len(xml_content.encode("utf-8")) > MAX_TEST_RESULT_FILE_SIZE_BYTES:
            raise UnitySafetyError(
                UnityErrorCode.TEST_PARSER_ERROR,
                f"Test result XML exceeds maximum allowed size of {MAX_TEST_RESULT_FILE_SIZE_BYTES} bytes.",
            )

        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError as e:
            raise UnitySafetyError(
                UnityErrorCode.TEST_PARSER_ERROR,
                f"Malformed test result XML: {e}",
            )

        cases: List[TestCaseResult] = []
        failures: List[TestFailure] = []
        failure_category_counts: Dict[str, int] = {}

        # Parse test cases recursively
        for tc_elem in root.iter("test-case"):
            name = tc_elem.attrib.get("name", "UnknownTest")
            fullname = tc_elem.attrib.get("fullname", name)
            classname = tc_elem.attrib.get("classname", "")
            methodname = tc_elem.attrib.get("methodname", "")
            duration_s = 0.0
            try:
                duration_s = float(tc_elem.attrib.get("duration", "0.0"))
            except ValueError:
                pass

            raw_result = tc_elem.attrib.get("result", "Inconclusive")
            status = TestStatus.INCONCLUSIVE
            if raw_result.lower() in ("passed", "success"):
                status = TestStatus.PASSED
            elif raw_result.lower() in ("failed", "failure"):
                status = TestStatus.FAILED
            elif raw_result.lower() in ("skipped", "ignored"):
                status = TestStatus.SKIPPED

            msg = ""
            failure_obj: Optional[TestFailure] = None

            fail_elem = tc_elem.find("failure")
            if fail_elem is not None:
                msg_elem = fail_elem.find("message")
                st_elem = fail_elem.find("stack-trace")
                err_msg = msg_elem.text.strip() if msg_elem is not None and msg_elem.text else ""
                err_st = st_elem.text.strip() if st_elem is not None and st_elem.text else ""
                msg = err_msg

                category = UnityTestFailureClassifier.classify(
                    message=err_msg,
                    stack_trace=err_st,
                    test_mode=test_mode,
                )
                failure_obj = TestFailure(
                    message=redact_sensitive_data(err_msg),
                    stack_trace=redact_sensitive_data(err_st),
                    category=category,
                    raw_text=redact_sensitive_data(f"{err_msg}\n{err_st}"),
                )
                failures.append(failure_obj)
                failure_category_counts[category.value] = failure_category_counts.get(category.value, 0) + 1

            elif status == TestStatus.SKIPPED:
                reason_elem = tc_elem.find("reason")
                if reason_elem is not None:
                    rmsg_elem = reason_elem.find("message")
                    if rmsg_elem is not None and rmsg_elem.text:
                        msg = rmsg_elem.text.strip()

            case_result = TestCaseResult(
                name=name,
                full_name=fullname,
                class_name=classname,
                method_name=methodname,
                status=status,
                duration_seconds=duration_s,
                failure=failure_obj,
                message=redact_sensitive_data(msg),
            )
            cases.append(case_result)

        # Extract or compute top-level summary
        total = int(root.attrib.get("total", len(cases)))
        passed = int(root.attrib.get("passed", sum(1 for c in cases if c.status == TestStatus.PASSED)))
        failed = int(root.attrib.get("failed", sum(1 for c in cases if c.status == TestStatus.FAILED)))
        skipped = int(root.attrib.get("skipped", sum(1 for c in cases if c.status == TestStatus.SKIPPED)))
        inconclusive = int(root.attrib.get("inconclusive", sum(1 for c in cases if c.status == TestStatus.INCONCLUSIVE)))

        duration = 0.0
        try:
            duration = float(root.attrib.get("duration", "0.0"))
        except ValueError:
            duration = sum(c.duration_seconds for c in cases)

        pass_rate = (passed / total * 100.0) if total > 0 else 0.0
        overall_status = "PASSED" if (failed == 0 and total > 0) else ("NO_TESTS" if total == 0 else "FAILED")

        summary = TestSummary(
            total=total,
            passed=passed,
            failed=failed,
            skipped=skipped,
            inconclusive=inconclusive,
            duration_seconds=duration,
            pass_rate=pass_rate,
            failure_categories=failure_category_counts,
            status=overall_status,
        )

        return summary, cases, failures

    @classmethod
    def parse_file(
        cls,
        file_path: Path | str,
        test_mode: str = "EditMode",
    ) -> Tuple[TestSummary, List[TestCaseResult], List[TestFailure]]:
        """Reads and parses an XML test results file."""
        p = Path(file_path).resolve()
        if not p.exists() or not p.is_file():
            raise UnitySafetyError(
                UnityErrorCode.TEST_ARTIFACT_MISSING,
                f"Test results XML file not found: '{p}'.",
            )
        text = p.read_text(encoding="utf-8", errors="replace")
        return cls.parse_xml_string(text, test_mode=test_mode)


# -----------------------------------------------------------------------------
# Unity Test Artifact Verifier
# -----------------------------------------------------------------------------

class UnityTestArtifactVerifier:
    """Verifies existence, integrity, size, and SHA-256 hash of test result artifacts."""

    def __init__(self, safety_gate: Optional[UnitySafetyGate] = None):
        self.safety = safety_gate or DEFAULT_UNITY_SAFETY_GATE

    def verify_test_artifact(
        self,
        output_path: Path | str,
        project_root: Optional[Path] = None,
    ) -> TestArtifactInfo:
        """
        Validates artifact path, verifies file existence, size, SHA-256,
        and ensures valid XML parseability.
        """
        try:
            validated_p = self.safety.validate_test_output_path(output_path, project_root=project_root)
        except Exception as e:
            return TestArtifactInfo(
                path=str(output_path),
                exists=False,
                error=str(e),
                verified=False,
            )

        if not validated_p.exists() or not validated_p.is_file():
            return TestArtifactInfo(
                path=str(validated_p),
                exists=False,
                error=f"Artifact '{validated_p.name}' does not exist.",
                verified=False,
            )

        size = validated_p.stat().st_size
        if size == 0:
            return TestArtifactInfo(
                path=str(validated_p),
                exists=True,
                size_bytes=0,
                error="Artifact file is empty (0 bytes).",
                verified=False,
            )

        if size > MAX_TEST_RESULT_FILE_SIZE_BYTES:
            return TestArtifactInfo(
                path=str(validated_p),
                exists=True,
                size_bytes=size,
                error=f"Artifact size ({size} bytes) exceeds limit ({MAX_TEST_RESULT_FILE_SIZE_BYTES} bytes).",
                verified=False,
            )

        # Compute SHA-256
        h = hashlib.sha256()
        try:
            with open(validated_p, "rb") as f:
                while chunk := f.read(65536):
                    h.update(chunk)
            sha = h.hexdigest()
        except Exception as e:
            return TestArtifactInfo(
                path=str(validated_p),
                exists=True,
                size_bytes=size,
                error=f"Failed reading artifact for hash: {e}",
                verified=False,
            )

        # Validate that it's parseable XML
        try:
            with open(validated_p, "r", encoding="utf-8", errors="replace") as f:
                ET.parse(f)
        except Exception as e:
            return TestArtifactInfo(
                path=str(validated_p),
                exists=True,
                size_bytes=size,
                sha256=sha,
                error=f"Artifact contains invalid XML: {e}",
                verified=False,
            )

        return TestArtifactInfo(
            path=str(validated_p),
            exists=True,
            size_bytes=size,
            sha256=sha,
            verified=True,
            error=None,
        )


# -----------------------------------------------------------------------------
# Unity Test Manager
# -----------------------------------------------------------------------------

class UnityTestManager:
    """
    Central manager for Unity EditMode and PlayMode test execution and verification.
    Coordinates subprocess execution, argument sanitization, timeout management,
    XML parsing, failure classification, and deterministic artifact verification.
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
        self.artifact_verifier = UnityTestArtifactVerifier(self.safety)

    def validate_test_mode(self, mode: str) -> str:
        """Validates test mode against allowed test modes."""
        return self.safety.validate_test_mode(mode)

    def validate_test_filter(self, filter_str: Optional[str], filter_type: str = "filter") -> Optional[str]:
        """Validates filter string against shell metacharacters and traversal."""
        return self.safety.validate_test_filter(filter_str, filter_type=filter_type)

    def run_editmode_tests(
        self,
        project_path: Optional[Path | str] = None,
        editor_path: Optional[Path | str] = None,
        test_filter: Optional[str] = None,
        assembly_names: Optional[str | List[str]] = None,
        category_names: Optional[str | List[str]] = None,
        result_xml_path: Optional[Path | str] = None,
        timeout_seconds: float = DEFAULT_TEST_TIMEOUT,
    ) -> TestExecutionResult:
        """Runs Unity EditMode tests safely."""
        return self.run_tests(
            test_mode="EditMode",
            project_path=project_path,
            editor_path=editor_path,
            test_filter=test_filter,
            assembly_names=assembly_names,
            category_names=category_names,
            result_xml_path=result_xml_path,
            timeout_seconds=timeout_seconds,
        )

    def run_playmode_tests(
        self,
        project_path: Optional[Path | str] = None,
        editor_path: Optional[Path | str] = None,
        test_filter: Optional[str] = None,
        assembly_names: Optional[str | List[str]] = None,
        category_names: Optional[str | List[str]] = None,
        result_xml_path: Optional[Path | str] = None,
        timeout_seconds: float = DEFAULT_TEST_TIMEOUT,
    ) -> TestExecutionResult:
        """Runs Unity PlayMode tests safely."""
        return self.run_tests(
            test_mode="PlayMode",
            project_path=project_path,
            editor_path=editor_path,
            test_filter=test_filter,
            assembly_names=assembly_names,
            category_names=category_names,
            result_xml_path=result_xml_path,
            timeout_seconds=timeout_seconds,
        )

    def run_tests(
        self,
        test_mode: str,
        project_path: Optional[Path | str] = None,
        editor_path: Optional[Path | str] = None,
        test_filter: Optional[str] = None,
        assembly_names: Optional[str | List[str]] = None,
        category_names: Optional[str | List[str]] = None,
        result_xml_path: Optional[Path | str] = None,
        timeout_seconds: float = DEFAULT_TEST_TIMEOUT,
    ) -> TestExecutionResult:
        """
        Core test runner method.
        Validates parameters, constructs bounded batchmode command,
        executes via UnityProcessRunner (shell=False), captures logs,
        parses results, and verifies test artifact.
        """
        # 1. Emergency stop check
        self.safety.assert_not_stopped()

        # 2. Validate test mode
        valid_mode = self.validate_test_mode(test_mode)

        # 3. Validate project path
        target_proj = Path(project_path or self.safety.authorized_project).resolve()
        validated_proj = self.safety.validate_path(target_proj, allow_read_only_workspace=True)

        # 4. Resolve editor executable
        editor_exe: str
        if editor_path:
            editor_exe = str(Path(editor_path).resolve())
            if not Path(editor_exe).exists() and self.runner._mock_executor is None:
                raise UnitySafetyError(
                    UnityErrorCode.UNITY_NOT_FOUND,
                    f"Specified Unity editor binary does not exist: '{editor_exe}'.",
                )
        elif self.runner._mock_executor is not None:
            editor_exe = "Unity.exe"
        else:
            best_editor = self.env.find_editor_for_project(validated_proj)
            if not best_editor:
                env_info = self.env.detect_environment()
                best_editor = env_info.preferred_editor
            if best_editor and best_editor.is_executable:
                editor_exe = best_editor.editor_path
            else:
                raise UnitySafetyError(
                    UnityErrorCode.UNITY_NOT_FOUND,
                    "No installed Unity Editor instance found on system.",
                )

        # 5. Validate filters
        val_filter = self.validate_test_filter(test_filter, filter_type="filter")

        val_assemblies: Optional[str] = None
        if assembly_names:
            if isinstance(assembly_names, list):
                val_assemblies = ";".join(self.validate_test_filter(a, "assembly") for a in assembly_names if a)
            else:
                val_assemblies = self.validate_test_filter(str(assembly_names), "assembly")

        val_categories: Optional[str] = None
        if category_names:
            if isinstance(category_names, list):
                val_categories = ";".join(self.validate_test_filter(c, "category") for c in category_names if c)
            else:
                val_categories = self.validate_test_filter(str(category_names), "category")

        # 6. Validate and ensure test result XML path
        if not result_xml_path:
            out_dir = validated_proj / "TestResults"
            out_dir.mkdir(parents=True, exist_ok=True)
            ts_str = time.strftime("%Y%m%d_%H%M%S")
            result_xml_path = out_dir / f"{valid_mode}_results_{ts_str}.xml"

        val_result_xml = self.safety.validate_test_output_path(result_xml_path, project_root=validated_proj)
        val_result_xml.parent.mkdir(parents=True, exist_ok=True)

        log_path = val_result_xml.parent / f"{val_result_xml.stem}_editor.log"

        # 7. Construct command (100% shell=False)
        cmd_args = [
            str(editor_exe),
            "-batchmode",
            "-runTests",
            "-testPlatform", valid_mode,
            "-projectPath", str(validated_proj),
            "-testResults", str(val_result_xml),
            "-logFile", str(log_path),
        ]

        if val_filter:
            cmd_args.extend(["-testFilter", val_filter])
        if val_assemblies:
            cmd_args.extend(["-assemblyNames", val_assemblies])
        if val_categories:
            cmd_args.extend(["-testCategories", val_categories])

        t0 = time.time()
        timeout_b = min(max(5.0, float(timeout_seconds)), TEST_TIMEOUT_SECONDS)

        logger.info(f"[UnityTestManager] Executing {valid_mode} tests on '{validated_proj.name}' (timeout={timeout_b}s)")

        exit_code = -1
        stdout_str = ""
        stderr_str = ""
        duration = 0.0
        timed_out = False

        try:
            exit_code, stdout_str, stderr_str, duration = self.runner.run_command(
                cmd_args=cmd_args,
                timeout_seconds=timeout_b,
                cwd=validated_proj,
            )
            if "timed out" in stderr_str.lower() or "timeout" in stderr_str.lower():
                timed_out = True
        except UnitySafetyError as ue:
            if ue.code in (UnityErrorCode.BUILD_TIMEOUT, UnityErrorCode.TEST_TIMEOUT):
                timed_out = True
                duration = time.time() - t0
                exit_code = -1
                stderr_str = f"Execution timed out after {timeout_b:.1f}s."
            else:
                raise

        output_preview = (stdout_str + "\n" + stderr_str).strip()[:1000]

        # 8. Check and parse result artifact
        artifact_info = self.artifact_verifier.verify_test_artifact(val_result_xml, project_root=validated_proj)

        summary: Optional[TestSummary] = None
        cases: List[TestCaseResult] = []
        failures: List[TestFailure] = []
        error_summary = ""

        if artifact_info.verified:
            try:
                summary, cases, failures = UnityTestResultParser.parse_file(val_result_xml, test_mode=valid_mode)
            except Exception as pe:
                error_summary = f"Failed to parse test results XML: {pe}"
        else:
            # Fallback diagnostics when XML was not created or is invalid
            log_content = ""
            if log_path.exists():
                try:
                    log_content = log_path.read_text(encoding="utf-8", errors="replace")[:20_000]
                except Exception:
                    pass

            category = UnityTestFailureClassifier.classify(
                message=stderr_str or "Test runner did not produce verified results artifact.",
                log_text=log_content,
                exit_code=exit_code,
                test_mode=valid_mode,
                timed_out=timed_out,
            )
            diag_fail = TestFailure(
                message=f"Test run failed: {artifact_info.error or stderr_str or 'Process exited without artifact'}",
                category=category,
                raw_text=redact_sensitive_data(output_preview),
            )
            failures.append(diag_fail)
            error_summary = f"[{category.value}] {diag_fail.message}"

        # 9. Evidence precedence determination
        # A test run is ONLY verified true if:
        # - artifact is verified (valid XML with SHA256)
        # - summary exists, total > 0, failed == 0
        # - exit_code == 0
        is_success = (
            artifact_info.verified
            and summary is not None
            and summary.total > 0
            and summary.failed == 0
            and exit_code == 0
        )

        verified = (
            artifact_info.verified
            and summary is not None
            and (summary.failed == 0 if is_success else True)
        )

        if not is_success and not error_summary:
            if summary and summary.failed > 0:
                first_f = failures[0] if failures else None
                error_summary = f"{summary.failed} test(s) failed. First failure: {first_f.message if first_f else 'Unknown'}"
            elif summary and summary.total == 0:
                error_summary = "No tests were executed matching the specified filter."
            elif exit_code != 0:
                error_summary = f"Test runner exited with code {exit_code}."

        return TestExecutionResult(
            success=is_success,
            test_mode=valid_mode,
            summary=summary,
            cases=cases,
            failures=failures,
            result_xml_path=str(val_result_xml) if artifact_info.exists else None,
            artifact_info=artifact_info,
            log_path=str(log_path) if log_path.exists() else None,
            exit_code=exit_code,
            duration_seconds=duration,
            output_preview=output_preview,
            verified=verified,
            error_summary=error_summary,
            timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
        )

    def parse_test_results(
        self,
        xml_content: Optional[str] = None,
        xml_path: Optional[Path | str] = None,
        test_mode: str = "EditMode",
    ) -> Dict[str, Any]:
        """Parses test results from string or file and returns structured dictionary."""
        self.safety.assert_not_stopped()
        if xml_content:
            summary, cases, failures = UnityTestResultParser.parse_xml_string(xml_content, test_mode=test_mode)
        elif xml_path:
            validated_p = self.safety.validate_path(Path(xml_path))
            summary, cases, failures = UnityTestResultParser.parse_file(validated_p, test_mode=test_mode)
        else:
            raise UnitySafetyError(
                UnityErrorCode.TEST_PARSER_ERROR,
                "Either xml_content or xml_path must be provided.",
            )

        return {
            "summary": summary.to_dict(),
            "cases_count": len(cases),
            "cases": [c.to_dict() for c in cases],
            "failures_count": len(failures),
            "failures": [f.to_dict() for f in failures],
        }

    def verify_test_artifact(
        self,
        output_path: Path | str,
        project_root: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Verifies test results XML artifact existence, size, and SHA-256."""
        self.safety.assert_not_stopped()
        res = self.artifact_verifier.verify_test_artifact(output_path, project_root=project_root)
        return res.to_dict()

    def get_test_summary(
        self,
        xml_content: Optional[str] = None,
        xml_path: Optional[Path | str] = None,
        test_mode: str = "EditMode",
    ) -> Dict[str, Any]:
        """Extracts high-level summary and pass/fail metrics from test results."""
        parsed = self.parse_test_results(xml_content=xml_content, xml_path=xml_path, test_mode=test_mode)
        return parsed["summary"]

    def diagnose_test_failure(
        self,
        message: str = "",
        stack_trace: str = "",
        log_text: str = "",
        exit_code: int = 0,
        test_mode: str = "EditMode",
    ) -> Dict[str, Any]:
        """Diagnoses failure cause and classifies into deterministic category."""
        self.safety.assert_not_stopped()
        category = UnityTestFailureClassifier.classify(
            message=message,
            stack_trace=stack_trace,
            log_text=log_text,
            exit_code=exit_code,
            test_mode=test_mode,
        )
        return {
            "category": category.value,
            "message": redact_sensitive_data(message),
            "diagnostics": f"Failure classified as {category.value}.",
            "distinction": (
                "ASSERTION / TEST CODE" if category in (TestFailureCategory.ASSERTION_FAILURE, TestFailureCategory.TEST_ERROR, TestFailureCategory.RUNTIME_FAILURE)
                else ("COMPILER / SCRIPTS" if category == TestFailureCategory.COMPILE_FAILURE
                else ("RUNNER / ENGINE" if category == TestFailureCategory.TEST_RUNNER_FAILURE
                else "ENVIRONMENT / HOST"))
            ),
        }


# Global default instance
DEFAULT_UNITY_TEST_MANAGER = UnityTestManager()
