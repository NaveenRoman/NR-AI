"""
Tests for Droid Phase 1: JUnit & Lint Structured Result Parsing Subsystem
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from app.agent.android_code_repair import AndroidBuildError, AndroidErrorCategory
from app.agent.android_test_results import (
    AndroidTestResultParser,
    JUnitReport,
    LintReport,
    TestCaseResult,
)

SAMPLE_JUNIT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="com.example.app.CalculatorTest" tests="3" skipped="1" failures="1" errors="0" timestamp="2026-09-19T00:00:00" hostname="localhost" time="0.123">
  <properties/>
  <testcase name="testAddition" classname="com.example.app.CalculatorTest" time="0.015"/>
  <testcase name="testDivisionByZero" classname="com.example.app.CalculatorTest" time="0.020">
    <failure message="Expected exception not thrown with key=AIzaSyA1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6Q" type="java.lang.AssertionError">java.lang.AssertionError: Expected exception not thrown
\tat org.junit.Assert.fail(Assert.java:89)
\tat com.example.app.CalculatorTest.testDivisionByZero(CalculatorTest.kt:42)
\tat org.junit.runners.model.FrameworkMethod$1.runReflectiveCall(FrameworkMethod.java:59)
    </failure>
  </testcase>
  <testcase name="testPendingFeature" classname="com.example.app.CalculatorTest" time="0.0">
    <skipped/>
  </testcase>
</testsuite>
"""

SAMPLE_LINT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<issues format="6" by="lint 8.2.0">
    <issue
        id="HardcodedText"
        severity="Warning"
        message="Hardcoded string &quot;Submit&quot;, should use @string resource"
        category="Internationalization"
        priority="5"
        summary="Hardcoded text"
        explanation="Hardcoding text attributes directly in layout files is bad for apps that have to be localized.">
        <location
            file="app/src/main/res/layout/activity_main.xml"
            line="25"
            column="9"/>
    </issue>
    <issue
        id="MissingSuperCall"
        severity="Error"
        message="Overriding method should call super.onCreate"
        category="Correctness"
        priority="9"
        summary="Missing Super Call"
        explanation="Overriding method must call super.">
        <location
            file="app/src/main/java/com/example/app/MainActivity.kt"
            line="15"
            column="5"/>
    </issue>
</issues>
"""


class TestDroidPhase1TestResults(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.parser = AndroidTestResultParser()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_parse_junit_xml(self):
        """Verify parsing of JUnit XML with pass, fail, and skip."""
        report = self.parser.parse_junit_xml(SAMPLE_JUNIT_XML)
        self.assertIsInstance(report, JUnitReport)
        self.assertEqual(report.total_tests, 3)
        self.assertEqual(report.total_failures, 1)
        self.assertEqual(report.total_skipped, 1)
        self.assertFalse(report.is_success)

        suite = report.suites[0]
        self.assertEqual(suite.name, "com.example.app.CalculatorTest")
        self.assertEqual(len(suite.test_cases), 3)

        # Passing test
        t_pass = suite.test_cases[0]
        self.assertEqual(t_pass.status, "PASSED")
        self.assertEqual(t_pass.name, "testAddition")

        # Failing test
        t_fail = suite.test_cases[1]
        self.assertEqual(t_fail.status, "FAILED")
        self.assertEqual(t_fail.failure_type, "java.lang.AssertionError")
        self.assertEqual(t_fail.failing_file, "CalculatorTest.kt")
        self.assertEqual(t_fail.failing_line, 42)

        # Secret redaction check on message and stacktrace
        self.assertIn("[REDACTED_GEMINI_KEY]", t_fail.failure_message)
        self.assertNotIn("AIzaSyA1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P", t_fail.failure_message)

    def test_02_parse_lint_xml(self):
        """Verify parsing of Android Lint XML issues and counts."""
        report = self.parser.parse_lint_xml(SAMPLE_LINT_XML)
        self.assertIsInstance(report, LintReport)
        self.assertEqual(len(report.issues), 2)
        self.assertEqual(report.warning_count, 1)
        self.assertEqual(report.error_count, 1)
        self.assertTrue(report.has_blocking_errors)

        issue_err = next(i for i in report.issues if i.id == "MissingSuperCall")
        self.assertEqual(issue_err.severity, "Error")
        self.assertEqual(issue_err.line, 15)
        self.assertEqual(issue_err.column, 5)
        self.assertEqual(issue_err.file_path, "app/src/main/java/com/example/app/MainActivity.kt")

    def test_03_convert_to_build_errors(self):
        """Verify JUnit failures and Lint errors convert cleanly to AndroidBuildError objects."""
        junit_rep = self.parser.parse_junit_xml(SAMPLE_JUNIT_XML)
        lint_rep = self.parser.parse_lint_xml(SAMPLE_LINT_XML)

        build_errors = self.parser.to_build_errors(junit_report=junit_rep, lint_report=lint_rep)
        self.assertEqual(len(build_errors), 2)  # 1 JUnit failure + 1 Lint Error (warning skipped)

        # Test failure
        err_test = build_errors[0]
        self.assertIsInstance(err_test, AndroidBuildError)
        self.assertEqual(err_test.line, 42)
        self.assertIn("CalculatorTest", err_test.message)

        # Lint error
        err_lint = build_errors[1]
        self.assertIsInstance(err_lint, AndroidBuildError)
        self.assertEqual(err_lint.line, 15)
        self.assertIn("MissingSuperCall", err_lint.message)

    def test_04_scan_project_results(self):
        """Verify scanning project directories for JUnit and Lint reports."""
        root = Path(self.tmp_dir)
        test_res_dir = root / "app" / "build" / "test-results" / "testDebugUnitTest"
        test_res_dir.mkdir(parents=True, exist_ok=True)
        (test_res_dir / "TEST-com.example.app.CalculatorTest.xml").write_text(SAMPLE_JUNIT_XML, encoding="utf-8")

        lint_res_dir = root / "app" / "build" / "reports"
        lint_res_dir.mkdir(parents=True, exist_ok=True)
        (lint_res_dir / "lint-results-debug.xml").write_text(SAMPLE_LINT_XML, encoding="utf-8")

        summary = self.parser.scan_project_results(root)
        self.assertEqual(summary["total_test_suites"], 1)
        self.assertEqual(summary["total_test_failures"], 1)
        self.assertEqual(summary["total_lint_issues"], 2)

    def test_05_malformed_xml_resilience(self):
        """Verify malformed XML does not raise unhandled exceptions."""
        rep = self.parser.parse_junit_xml("<testsuite><unclosed>")
        self.assertIsInstance(rep, JUnitReport)
        self.assertEqual(rep.total_tests, 0)

        lint_rep = self.parser.parse_lint_xml("not valid xml at all")
        self.assertIsInstance(lint_rep, LintReport)
        self.assertEqual(len(lint_rep.issues), 0)


if __name__ == "__main__":
    unittest.main()
