"""
NR-AI Test Failure -> Source -> Repair Intelligence Engine.

Performs deep diagnostics on JUnit 4/5 and Android Instrumentation test failures:
  - Connects test failure to source symbol, file, module, and line number
  - Classifies failure kind:
      DIRECT_TEST_FAILURE, RUNTIME_FAILURE, BUILD_FAILURE,
      DEPENDENCY_FAILURE, RESOURCE_FAILURE, ENVIRONMENT_FAILURE, UNKNOWN
  - Pinpoints root cause and generates evidence-backed repair recommendations
  - Determines targeted follow-up test set
"""

from dataclasses import dataclass, field, asdict
from enum import Enum
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union
import xml.etree.ElementTree as ET

from app.agent.android_project_graph import AndroidKnowledgeGraph, AndroidProjectGraphEngine

logger = logging.getLogger("NRAI.AndroidTestIntelligence")


class TestFailureKind(str, Enum):
    DIRECT_TEST_FAILURE = "DIRECT_TEST_FAILURE"
    RUNTIME_FAILURE = "RUNTIME_FAILURE"
    BUILD_FAILURE = "BUILD_FAILURE"
    DEPENDENCY_FAILURE = "DEPENDENCY_FAILURE"
    RESOURCE_FAILURE = "RESOURCE_FAILURE"
    ENVIRONMENT_FAILURE = "ENVIRONMENT_FAILURE"
    UNKNOWN = "UNKNOWN"


@dataclass
class TestFailureEvidence:
    test_class: str
    test_method: str
    failure_message: str
    stack_trace: str
    failure_kind: TestFailureKind = TestFailureKind.UNKNOWN
    target_source_file: Optional[str] = None
    target_symbol: Optional[str] = None
    target_line: Optional[int] = None
    target_module: Optional[str] = None
    recommended_repair: Optional[str] = None
    suggested_followup_tests: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["failure_kind"] = self.failure_kind.value
        return d


@dataclass
class AndroidTestDiagnosticReport:
    project_path: str
    total_tests_run: int = 0
    passed_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    failures: List[TestFailureEvidence] = field(default_factory=list)
    primary_cause_summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_path": self.project_path,
            "total_tests_run": self.total_tests_run,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "skipped_count": self.skipped_count,
            "failures": [f.to_dict() for f in self.failures],
            "primary_cause_summary": self.primary_cause_summary,
        }


class AndroidTestIntelligenceEngine:
    """Diagnoses JUnit and Android Instrumentation test failures with source correlation."""

    def __init__(self):
        self.project_graph_engine = AndroidProjectGraphEngine()

    def parse_gradle_test_xml(self, xml_path: Union[str, Path]) -> List[TestFailureEvidence]:
        """Parses a JUnit XML report file (e.g. build/test-results/testDebugUnitTest/TEST-*.xml)."""
        path = Path(xml_path)
        if not path.exists():
            return []

        failures: List[TestFailureEvidence] = []
        try:
            tree = ET.parse(path)
            root = tree.getroot()
            # Can be <testsuite> or <testsuites>
            suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
            for suite in suites:
                suite_class = suite.attrib.get("name", "")
                for tc in suite.findall("testcase"):
                    classname = tc.attrib.get("classname", suite_class)
                    methodname = tc.attrib.get("name", "")

                    failure_elem = tc.find("failure")
                    error_elem = tc.find("error")
                    elem = failure_elem if failure_elem is not None else error_elem

                    if elem is not None:
                        msg = elem.attrib.get("message", "")
                        stack = elem.text or ""
                        evidence = TestFailureEvidence(
                            test_class=classname,
                            test_method=methodname,
                            failure_message=msg,
                            stack_trace=stack.strip(),
                        )
                        self._classify_and_correlate(evidence)
                        failures.append(evidence)
        except Exception as e:
            logger.warning("Failed parsing test XML %s: %s", xml_path, e)

        return failures

    def parse_console_output(self, output: str) -> List[TestFailureEvidence]:
        """Parses Gradle console test runner output for test failures."""
        failures: List[TestFailureEvidence] = []
        if not output:
            return failures

        # Pattern: com.example.FooTest > testBar FAILED
        # followed by stack trace or assertion error
        failure_header_re = re.compile(r"([a-zA-Z0-9_$.]+)\s*>\s*([a-zA-Z0-9_$]+)\s+FAILED")
        matches = list(failure_header_re.finditer(output))

        for i, match in enumerate(matches):
            test_cls, test_meth = match.group(1), match.group(2)
            start_idx = match.end()
            end_idx = matches[i + 1].start() if i + 1 < len(matches) else len(output)
            chunk = output[start_idx:end_idx]

            # Extract message and stack trace
            lines = chunk.strip().splitlines()
            msg = lines[0] if lines else ""
            stack = "\n".join(lines[:25])  # bounded stack trace

            evidence = TestFailureEvidence(
                test_class=test_cls,
                test_method=test_meth,
                failure_message=msg,
                stack_trace=stack,
            )
            self._classify_and_correlate(evidence)
            failures.append(evidence)

        return failures

    def _classify_and_correlate(self, evidence: TestFailureEvidence) -> None:
        """Classifies the test failure kind and extracts target file/symbol."""
        combined = f"{evidence.failure_message}\n{evidence.stack_trace}"

        # 1. Classification
        if "ComparisonFailure" in combined or "AssertionError" in combined or "expected:<" in combined:
            evidence.failure_kind = TestFailureKind.DIRECT_TEST_FAILURE
        elif "Resources$NotFoundException" in combined or "InflateException" in combined or "ResourceNotFound" in combined:
            evidence.failure_kind = TestFailureKind.RESOURCE_FAILURE
        elif "NoClassDefFoundError" in combined or "ClassNotFoundException" in combined or "NoSuchMethodError" in combined:
            evidence.failure_kind = TestFailureKind.DEPENDENCY_FAILURE
        elif "Compilation error" in combined or "Unresolved reference" in combined or "e: " in combined:
            evidence.failure_kind = TestFailureKind.BUILD_FAILURE
        elif "Device not found" in combined or "Connection refused" in combined or "offline" in combined:
            evidence.failure_kind = TestFailureKind.ENVIRONMENT_FAILURE
        elif any(exc in combined for exc in ["Exception", "Error", "NullPointerException", "ArithmeticException", "IllegalStateException"]):
            evidence.failure_kind = TestFailureKind.RUNTIME_FAILURE
        else:
            evidence.failure_kind = TestFailureKind.UNKNOWN

        # 2. Extract stack trace frames to find target source file and symbol
        # Look for non-framework lines (excluding android.*, androidx.*, org.junit.*, java.*)
        stack_frame_re = re.compile(r"at\s+([a-zA-Z0-9_$.]+)\.([a-zA-Z0-9_$]+)\(([^:]+):(\d+)\)")
        for match in stack_frame_re.finditer(evidence.stack_trace):
            fqcn, meth, fname, line_num = match.groups()
            # Ignore test runners, frameworks, and gradle internals
            if (not fqcn.startswith("org.junit.") and
                not fqcn.startswith("androidx.test.") and
                not fqcn.startswith("android.") and
                not fqcn.startswith("java.") and
                not fqcn.startswith("org.gradle.") and
                not fqcn.startswith("worker.org.gradle.")):
                if not evidence.target_source_file:
                    evidence.target_source_file = fname
                    evidence.target_symbol = f"{fqcn}.{meth}"
                    evidence.target_line = int(line_num)
                # If frame is not in the test class itself, prioritize it as the target source
                if fqcn != evidence.test_class and not fqcn.endswith("Test"):
                    evidence.target_source_file = fname
                    evidence.target_symbol = f"{fqcn}.{meth}"
                    evidence.target_line = int(line_num)
                    break

        # 3. Generate repair recommendation
        evidence.recommended_repair = self._generate_repair_recommendation(evidence)

    def _generate_repair_recommendation(self, evidence: TestFailureEvidence) -> str:
        if evidence.failure_kind == TestFailureKind.DIRECT_TEST_FAILURE:
            return (
                f"Assertion failed in {evidence.test_class}.{evidence.test_method}. "
                f"Inspect expected vs actual values and verify state transition in {evidence.target_symbol or evidence.target_source_file}."
            )
        elif evidence.failure_kind == TestFailureKind.RUNTIME_FAILURE:
            return (
                f"Runtime exception detected in {evidence.target_symbol or evidence.target_source_file} (line {evidence.target_line}). "
                f"Add boundary check, guard condition, or correct calculation logic."
            )
        elif evidence.failure_kind == TestFailureKind.RESOURCE_FAILURE:
            return (
                f"Resource resolution failed. Verify resource declaration in res/ directory "
                f"and ensure R reference matches configuration qualifier."
            )
        elif evidence.failure_kind == TestFailureKind.DEPENDENCY_FAILURE:
            return (
                f"Missing class/dependency detected. Inspect build.gradle dependencies "
                f"and ensure required library or transitive dependency is declared."
            )
        elif evidence.failure_kind == TestFailureKind.ENVIRONMENT_FAILURE:
            return "Test environment failure. Ensure AVD/device is booted and adb connection is active."
        return "Inspect stack trace and source symbols around failure location."

    def analyze_project_tests(
        self,
        project_root: Union[str, Path],
        kg: Optional[AndroidKnowledgeGraph] = None,
    ) -> AndroidTestDiagnosticReport:
        """
        Scans project directory for all JUnit XML test reports, parses failures,
        and correlates with the project knowledge graph.
        """
        root = Path(project_root).resolve()
        report = AndroidTestDiagnosticReport(project_path=str(root))

        if kg is None:
            try:
                kg = self.project_graph_engine.build_knowledge_graph(root)
            except Exception as e:
                logger.warning("Could not build knowledge graph for test diagnostics: %s", e)

        # Find all test XML result files
        xml_reports = list(root.glob("**/build/test-results/**/TEST-*.xml"))
        for xml_path in xml_reports:
            failures = self.parse_gradle_test_xml(xml_path)
            for f in failures:
                # Correlate module via KnowledgeGraph
                if kg:
                    mod = self.project_graph_engine.which_module_caused_failure(kg, f"{f.failure_message}\n{f.stack_trace}")
                    f.target_module = mod
                    # Determine follow-up tests
                    if f.target_source_file:
                        f.suggested_followup_tests = self.project_graph_engine.tests_to_run_for_changes(
                            kg, [f.target_source_file]
                        )
                report.failures.append(f)

        report.failed_count = len(report.failures)
        if report.failures:
            first_fail = report.failures[0]
            report.primary_cause_summary = (
                f"{first_fail.failure_kind.value} in {first_fail.test_class}.{first_fail.test_method}: "
                f"{first_fail.failure_message}"
            )
        else:
            report.primary_cause_summary = "All discovered test suites passed."

        return report
