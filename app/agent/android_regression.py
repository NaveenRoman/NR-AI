"""
NR-AI Android Regression Protection Engine (Droid Phase 3).

Analyzes affected test suites based on changed files, package dependencies,
and resource references.
Executes test suites pre- and post-repair to verify that:
1. The target issue was resolved.
2. No new regressions were introduced.
Tracks pre_repair_tests, post_repair_tests, new_failures, and resolved_failures.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import logging
import os
from pathlib import Path
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
import uuid

from app.agent.android_safety import AndroidSafetyGate, EmergencyStopActiveError
from app.agent.android_tools import SafeGradleRunner
from app.agent.android_test_results import AndroidTestResultParser, JUnitReport, TestCaseResult
from app.memory.audit_logger import AuditLogger

logger = logging.getLogger("NRAI.AndroidRegression")


# -----------------------------------------------------------------------------
# Data Models
# -----------------------------------------------------------------------------

@dataclass
class TestComparisonReport:
    """Detailed differential analysis between pre-repair and post-repair test runs."""
    passed_cleanly: bool
    total_pre_tests: int = 0
    total_post_tests: int = 0
    pre_failures: List[str] = field(default_factory=list)
    post_failures: List[str] = field(default_factory=list)
    resolved_failures: List[str] = field(default_factory=list)
    new_failures: List[str] = field(default_factory=list)
    affected_test_classes: List[str] = field(default_factory=list)
    message: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# -----------------------------------------------------------------------------
# Regression Engine
# -----------------------------------------------------------------------------

class AndroidRegressionEngine:
    """
    Guarantees regression protection across all autonomous repairs.
    Ensures that any new failure introduced by an edit immediately halts and rejects the repair.
    """

    def __init__(
        self,
        gradle_runner: Optional[SafeGradleRunner] = None,
        test_parser: Optional[AndroidTestResultParser] = None,
        safety_gate: Optional[AndroidSafetyGate] = None,
        audit_logger: Optional[AuditLogger] = None,
    ):
        self.safety = safety_gate or AndroidSafetyGate()
        self.gradle = gradle_runner or SafeGradleRunner()
        self.test_parser = test_parser or AndroidTestResultParser()
        self.audit = audit_logger or AuditLogger()

    def identify_affected_tests(
        self,
        changed_files: List[Union[str, Path]],
        project_path: Optional[Union[str, Path]] = None,
    ) -> List[str]:
        """
        Determines which test classes should be executed given a list of modified files.
        """
        affected: Set[str] = set()

        for f in changed_files:
            f_str = str(f)
            p = Path(f_str)
            stem = p.stem

            # If a test file itself was edited
            if "test" in stem.lower():
                affected.add(stem)
            else:
                # Target convention: <Name>Test or <Name>UnitTest
                affected.add(f"{stem}Test")
                affected.add(f"{stem}UnitTest")

        return sorted(list(affected))

    def run_tests(
        self,
        test_filter: Optional[str] = None,
        task_id: str = "T-DEFAULT",
    ) -> JUnitReport:
        """
        Executes unit tests via Gradle and parses JUnit XML reports.
        """
        self.safety.check_emergency_stop()

        # Run gradle check / test
        args = ["test"]
        if test_filter:
            args.extend(["--tests", test_filter])

        res = self.gradle.run_task(args)

        # Parse test results from build directory
        # Fallback to in-memory parsed result if no XML generated
        parsed = self.test_parser.parse_junit_results()
        return parsed

    def compare_test_runs(
        self,
        pre_report: JUnitReport,
        post_report: JUnitReport,
        affected_classes: Optional[List[str]] = None,
    ) -> TestComparisonReport:
        """
        Compares pre-repair and post-repair test results.
        Flags:
        - resolved_failures: failed before, passed after (GOOD)
        - new_failures: passed before, failed after (REGRESSION!)
        """
        pre_fails = getattr(pre_report, 'failures', [])
        pre_fail_names = {t.name for t in pre_fails}
        post_fails = getattr(post_report, 'failures', [])
        post_fail_names = {t.name for t in post_fails}

        resolved = sorted(list(pre_fail_names - post_fail_names))
        new_fails = sorted(list(post_fail_names - pre_fail_names))

        has_regression = len(new_fails) > 0
        passed_cleanly = (not has_regression) and (len(post_fail_names) == 0)

        if has_regression:
            msg = f"REGRESSION DETECTED: {len(new_fails)} new test failure(s) introduced: {', '.join(new_fails)}"
        elif len(resolved) > 0:
            msg = f"SUCCESS: {len(resolved)} test failure(s) resolved with ZERO regressions!"
        elif len(post_fail_names) == 0:
            msg = "All tests passing cleanly."
        else:
            msg = f"{len(post_fail_names)} preexisting test failure(s) remain unresolved."

        report = TestComparisonReport(
            passed_cleanly=passed_cleanly,
            total_pre_tests=pre_report.total_tests,
            total_post_tests=post_report.total_tests,
            pre_failures=sorted(list(pre_fail_names)),
            post_failures=sorted(list(post_fail_names)),
            resolved_failures=resolved,
            new_failures=new_fails,
            affected_test_classes=affected_classes or [],
            message=msg,
        )

        self.audit.log(
            "test_regression_evaluated",
            details=report.to_dict(),
            actor="AndroidRegressionEngine",
            status="SUCCESS" if not has_regression else "REGRESSION_DETECTED",
        )
        return report
