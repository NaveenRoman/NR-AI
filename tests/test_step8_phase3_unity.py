"""
NR-AI Step 8 Phase 3 Acceptance Tests: Unity Test Runner & PlayMode Foundation.

Comprehensive deterministic acceptance suite covering test cases A through Z:
A. test_A_valid_editmode_execution_request
B. test_B_valid_playmode_execution_request
C. test_C_invalid_test_mode_rejection
D. test_D_invalid_filter_rejection
E. test_E_path_traversal_rejection
F. test_F_external_result_path_rejection
G. test_G_protected_output_rejection
H. test_H_timeout_enforcement
I. test_I_output_size_enforcement
J. test_J_process_cleanup
K. test_K_nunit_xml_parsing
L. test_L_passed_test_parsing
M. test_M_failed_test_parsing
N. test_N_skipped_test_parsing
O. test_O_assertion_failure_classification
P. test_P_test_runner_failure_classification
Q. test_Q_compile_failure_classification
R. test_R_runtime_failure_classification
S. test_S_sensitive_data_redaction
T. test_T_emergency_stop
U. test_U_model_isolation
V. test_V_evidence_precedence
W. test_W_test_artifact_verification
X. test_X_audit_logging
Y. test_Y_deterministic_editmode_workflow
Z. test_Z_deterministic_playmode_workflow
"""

import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from app.agent.unity_safety import (
    UnitySafetyGate,
    UnityErrorCode,
    UnitySafetyError,
    EmergencyStopActiveError,
    ALLOWED_TEST_MODES,
    ALLOWED_UNITY_TEST_TOOLS,
    ALL_ALLOWED_UNITY_TOOLS,
    redact_sensitive_data,
)
from app.agent.unity_environment import (
    UnityEnvironmentDetector,
    UnityEditorInstance,
)
from app.agent.unity_project import (
    UnityProjectInspector,
)
from app.agent.unity_build import (
    UnityProcessRunner,
    UnityBuildManager,
)
from app.agent.unity_tests import (
    UnityTestManager,
    UnityTestResultParser,
    UnityTestArtifactVerifier,
    UnityTestFailureClassifier,
    TestStatus,
    TestFailureCategory,
    TestFailure,
    TestCaseResult,
    TestSuiteResult,
    TestSummary,
    TestArtifactInfo,
    TestExecutionResult,
)
from app.agent.unity_tools import (
    UnityToolRegistry,
    UnityToolResult,
)
from app.memory.audit_logger import AuditLogger

WORKSPACE_ROOT = Path("C:/NR-AI").resolve()
FIXTURE_PROJECT = (WORKSPACE_ROOT / "nr_unity_test").resolve()


SAMPLE_NUNIT3_PASSED_XML = """<?xml version="1.0" encoding="utf-8"?>
<test-run id="2" testcasecount="2" result="Passed" total="2" passed="2" failed="0" inconclusive="0" skipped="0" asserts="2" runstate="Runnable" duration="0.045">
  <test-suite type="Assembly" id="1001" name="Tests.dll" fullname="Tests.dll" testcasecount="2" result="Passed" total="2" passed="2" failed="0" inconclusive="0" skipped="0" asserts="2" duration="0.045">
    <test-suite type="TestFixture" id="1002" name="PlayerMovementTests" fullname="Tests.PlayerMovementTests" testcasecount="2" result="Passed" total="2" passed="2" failed="0" inconclusive="0" skipped="0" asserts="2" duration="0.045">
      <test-case id="1003" name="Player_MovesForward_OnInput" fullname="Tests.PlayerMovementTests.Player_MovesForward_OnInput" methodname="Player_MovesForward_OnInput" classname="Tests.PlayerMovementTests" result="Passed" duration="0.020" asserts="1" />
      <test-case id="1004" name="Player_Jumps_WhenGrounded" fullname="Tests.PlayerMovementTests.Player_Jumps_WhenGrounded" methodname="Player_Jumps_WhenGrounded" classname="Tests.PlayerMovementTests" result="Passed" duration="0.025" asserts="1" />
    </test-suite>
  </test-suite>
</test-run>
"""

SAMPLE_NUNIT3_FAILED_XML = """<?xml version="1.0" encoding="utf-8"?>
<test-run id="2" testcasecount="3" result="Failed" total="3" passed="1" failed="1" inconclusive="0" skipped="1" asserts="3" runstate="Runnable" duration="0.120">
  <test-suite type="Assembly" id="1001" name="Tests.dll" fullname="Tests.dll" testcasecount="3" result="Failed" total="3" passed="1" failed="1" inconclusive="0" skipped="1" asserts="3" duration="0.120">
    <test-suite type="TestFixture" id="1002" name="CombatTests" fullname="Tests.CombatTests" testcasecount="3" result="Failed" total="3" passed="1" failed="1" inconclusive="0" skipped="1" asserts="3" duration="0.120">
      <test-case id="1003" name="Player_TakesDamage" fullname="Tests.CombatTests.Player_TakesDamage" methodname="Player_TakesDamage" classname="Tests.CombatTests" result="Passed" duration="0.015" asserts="1" />
      <test-case id="1004" name="Player_DiesAtZeroHealth" fullname="Tests.CombatTests.Player_DiesAtZeroHealth" methodname="Player_DiesAtZeroHealth" classname="Tests.CombatTests" result="Failed" duration="0.050" asserts="2">
        <failure>
          <message><![CDATA[Expected: False but was: True (with secret AIzaSyFakeSecretToken12345)]]></message>
          <stack-trace><![CDATA[at Tests.CombatTests.Player_DiesAtZeroHealth () [0x00021] in C:\\NR-AI\\nr_unity_test\\Assets\\Tests\\CombatTests.cs:42]]></stack-trace>
        </failure>
      </test-case>
      <test-case id="1005" name="Player_Respawn" fullname="Tests.CombatTests.Player_Respawn" methodname="Player_Respawn" classname="Tests.CombatTests" result="Skipped" duration="0.000" asserts="0">
        <reason>
          <message><![CDATA[Respawn feature under refactoring]]></message>
        </reason>
      </test-case>
    </test-suite>
  </test-suite>
</test-run>
"""


class TestStep8Phase3Unity(unittest.TestCase):
    """Acceptance suite for Step 8 Phase 3: Unity Test Runner & PlayMode Foundation."""

    def setUp(self):
        self.safety = UnitySafetyGate(
            authorized_project=FIXTURE_PROJECT,
            workspace_root=WORKSPACE_ROOT,
            rate_limit_calls_per_minute=200,
        )
        self.env = UnityEnvironmentDetector()
        self.inspector = UnityProjectInspector(safety_gate=self.safety)
        self.runner = UnityProcessRunner(safety_gate=self.safety)
        self.build = UnityBuildManager(
            safety_gate=self.safety,
            env_detector=self.env,
            project_inspector=self.inspector,
            runner=self.runner,
        )
        self.tests = UnityTestManager(
            safety_gate=self.safety,
            env_detector=self.env,
            project_inspector=self.inspector,
            runner=self.runner,
        )
        self.temp_audit_dir = tempfile.mkdtemp()
        self.audit = AuditLogger(log_dir=self.temp_audit_dir)
        self.registry = UnityToolRegistry(
            safety_gate=self.safety,
            env_detector=self.env,
            inspector=self.inspector,
            build_manager=self.build,
            test_manager=self.tests,
            audit_logger=self.audit,
            workspace_root=WORKSPACE_ROOT,
            include_build_tools=True,
            include_test_tools=True,
        )
        self.test_tmp_dir = tempfile.mkdtemp(dir=str(WORKSPACE_ROOT / "scratch"))

    def tearDown(self):
        if hasattr(self, "temp_audit_dir") and os.path.exists(self.temp_audit_dir):
            try:
                shutil.rmtree(self.temp_audit_dir, ignore_errors=True)
            except Exception:
                pass
        if hasattr(self, "test_tmp_dir") and os.path.exists(self.test_tmp_dir):
            try:
                shutil.rmtree(self.test_tmp_dir, ignore_errors=True)
            except Exception:
                pass

    # -------------------------------------------------------------------------
    # Test A: Valid EditMode execution request
    # -------------------------------------------------------------------------
    def test_A_valid_editmode_execution_request(self):
        """Test A: Valid EditMode test execution request with deterministic mock."""
        out_xml = Path(self.test_tmp_dir) / "EditMode_results.xml"

        def mock_editmode_runner(cmd: list, timeout: float):
            # Write sample passed XML
            out_xml.write_text(SAMPLE_NUNIT3_PASSED_XML, encoding="utf-8")
            return 0, "Finished running tests in EditMode.", ""

        self.runner.set_mock_executor(mock_editmode_runner)

        res = self.tests.run_editmode_tests(
            project_path=FIXTURE_PROJECT,
            result_xml_path=out_xml,
            test_filter="PlayerMovementTests",
        )

        self.assertTrue(res.success)
        self.assertEqual(res.test_mode, "EditMode")
        self.assertTrue(res.verified)
        self.assertIsNotNone(res.summary)
        self.assertEqual(res.summary.passed, 2)
        self.assertEqual(res.summary.failed, 0)
        self.assertEqual(len(res.cases), 2)

    # -------------------------------------------------------------------------
    # Test B: Valid PlayMode execution request
    # -------------------------------------------------------------------------
    def test_B_valid_playmode_execution_request(self):
        """Test B: Valid PlayMode test execution request with deterministic mock."""
        out_xml = Path(self.test_tmp_dir) / "PlayMode_results.xml"

        def mock_playmode_runner(cmd: list, timeout: float):
            out_xml.write_text(SAMPLE_NUNIT3_PASSED_XML, encoding="utf-8")
            return 0, "Finished running tests in PlayMode.", ""

        self.runner.set_mock_executor(mock_playmode_runner)

        res = self.tests.run_playmode_tests(
            project_path=FIXTURE_PROJECT,
            result_xml_path=out_xml,
        )

        self.assertTrue(res.success)
        self.assertEqual(res.test_mode, "PlayMode")
        self.assertTrue(res.verified)
        self.assertEqual(res.summary.passed, 2)

    # -------------------------------------------------------------------------
    # Test C: Invalid test mode rejection
    # -------------------------------------------------------------------------
    def test_C_invalid_test_mode_rejection(self):
        """Test C: Rejection of invalid or unapproved test modes."""
        with self.assertRaises(UnitySafetyError) as ctx:
            self.safety.validate_test_mode("InvalidMode")
        self.assertEqual(ctx.exception.code, UnityErrorCode.INVALID_TEST_MODE)

        with self.assertRaises(UnitySafetyError) as ctx2:
            self.tests.run_tests(test_mode="KernelMode", project_path=FIXTURE_PROJECT)
        self.assertEqual(ctx2.exception.code, UnityErrorCode.INVALID_TEST_MODE)

    # -------------------------------------------------------------------------
    # Test D: Invalid filter rejection
    # -------------------------------------------------------------------------
    def test_D_invalid_filter_rejection(self):
        """Test D: Rejection of test filters containing dangerous shell chars or traversal."""
        dangerous_filters = [
            "Test; rm -rf /",
            "Test | cat",
            "Test && dir",
            "Test`whoami`",
            "Test$PATH",
            "Test\nEvil",
            "../OutsideTest",
            "Test<Redirect>",
            'Test"Quote',
        ]
        for df in dangerous_filters:
            with self.assertRaises(UnitySafetyError) as ctx:
                self.safety.validate_test_filter(df)
            self.assertEqual(ctx.exception.code, UnityErrorCode.INVALID_TEST_FILTER)

    # -------------------------------------------------------------------------
    # Test E: Path traversal rejection
    # -------------------------------------------------------------------------
    def test_E_path_traversal_rejection(self):
        """Test E: Rejection of path traversal sequences in test output paths."""
        traversal_path = FIXTURE_PROJECT / "TestResults" / ".." / ".." / "evil.xml"
        with self.assertRaises(UnitySafetyError) as ctx:
            self.safety.validate_test_output_path(traversal_path)
        self.assertEqual(ctx.exception.code, UnityErrorCode.PATH_TRAVERSAL_DETECTED)

    # -------------------------------------------------------------------------
    # Test F: External result path rejection
    # -------------------------------------------------------------------------
    def test_F_external_result_path_rejection(self):
        """Test F: Rejection of test output paths outside workspace boundaries."""
        ext_path = Path("C:/Windows/Temp/test_results.xml")
        with self.assertRaises(UnitySafetyError) as ctx:
            self.safety.validate_test_output_path(ext_path)
        self.assertEqual(ctx.exception.code, UnityErrorCode.FILE_NOT_AUTHORIZED)

    # -------------------------------------------------------------------------
    # Test G: Protected output rejection
    # -------------------------------------------------------------------------
    def test_G_protected_output_rejection(self):
        """Test G: Rejection of test output written into protected project folders (Assets, ProjectSettings)."""
        protected_targets = [
            FIXTURE_PROJECT / "Assets" / "test_out.xml",
            FIXTURE_PROJECT / "ProjectSettings" / "test_out.xml",
            FIXTURE_PROJECT / "Library" / "test_out.xml",
        ]
        for pt in protected_targets:
            with self.assertRaises(UnitySafetyError) as ctx:
                self.safety.validate_test_output_path(pt, project_root=FIXTURE_PROJECT)
            self.assertEqual(ctx.exception.code, UnityErrorCode.PROTECTED_DIRECTORY_REJECTED)

    # -------------------------------------------------------------------------
    # Test H: Timeout enforcement
    # -------------------------------------------------------------------------
    def test_H_timeout_enforcement(self):
        """Test H: Bounded execution timeout and classification as TEST_TIMEOUT."""
        out_xml = Path(self.test_tmp_dir) / "Timeout_results.xml"

        def mock_timeout_runner(cmd: list, timeout: float):
            raise UnitySafetyError(
                UnityErrorCode.BUILD_TIMEOUT,
                f"Unity execution timed out after {timeout:.1f}s.",
            )

        self.runner.set_mock_executor(mock_timeout_runner)

        res = self.tests.run_editmode_tests(
            project_path=FIXTURE_PROJECT,
            result_xml_path=out_xml,
            timeout_seconds=5.0,
        )

        self.assertFalse(res.success)
        self.assertFalse(res.verified)
        self.assertTrue(len(res.failures) > 0)
        self.assertEqual(res.failures[0].category, TestFailureCategory.TEST_TIMEOUT)

    # -------------------------------------------------------------------------
    # Test I: Output size enforcement
    # -------------------------------------------------------------------------
    def test_I_output_size_enforcement(self):
        """Test I: Output preview bounded to MAX_CAPTURED_OUTPUT_BYTES."""
        out_xml = Path(self.test_tmp_dir) / "LargeOutput_results.xml"
        large_output = "TEST OUTPUT LINE\n" * 15_000  # ~250 KB

        def mock_large_runner(cmd: list, timeout: float):
            out_xml.write_text(SAMPLE_NUNIT3_PASSED_XML, encoding="utf-8")
            return 0, large_output, ""

        self.runner.set_mock_executor(mock_large_runner)

        res = self.tests.run_editmode_tests(
            project_path=FIXTURE_PROJECT,
            result_xml_path=out_xml,
        )

        self.assertTrue(res.success)
        self.assertLessEqual(len(res.output_preview), 1000)

    # -------------------------------------------------------------------------
    # Test J: Process cleanup
    # -------------------------------------------------------------------------
    def test_J_process_cleanup(self):
        """Test J: Safe process cleanup without raising uncaught exceptions."""
        self.runner._cleanup_process(None)

        class DummyProc:
            def __init__(self):
                self.terminated = False
                self.killed = False

            def terminate(self):
                self.terminated = True

            def wait(self, timeout=None):
                pass

            def kill(self):
                self.killed = True

        dummy = DummyProc()
        self.runner._cleanup_process(dummy)
        self.assertTrue(dummy.terminated)

    # -------------------------------------------------------------------------
    # Test K: NUnit XML parsing
    # -------------------------------------------------------------------------
    def test_K_nunit_xml_parsing(self):
        """Test K: NUnit3 XML parsing correctly computes totals and metrics."""
        summary, cases, failures = UnityTestResultParser.parse_xml_string(SAMPLE_NUNIT3_PASSED_XML)
        self.assertEqual(summary.total, 2)
        self.assertEqual(summary.passed, 2)
        self.assertEqual(summary.failed, 0)
        self.assertEqual(summary.status, "PASSED")
        self.assertAlmostEqual(summary.pass_rate, 100.0)
        self.assertEqual(len(cases), 2)
        self.assertEqual(len(failures), 0)

    # -------------------------------------------------------------------------
    # Test L: Passed test parsing
    # -------------------------------------------------------------------------
    def test_L_passed_test_parsing(self):
        """Test L: Individual passed test case parsing."""
        summary, cases, failures = UnityTestResultParser.parse_xml_string(SAMPLE_NUNIT3_PASSED_XML)
        first = cases[0]
        self.assertEqual(first.name, "Player_MovesForward_OnInput")
        self.assertEqual(first.class_name, "Tests.PlayerMovementTests")
        self.assertEqual(first.status, TestStatus.PASSED)
        self.assertEqual(first.duration_seconds, 0.020)
        self.assertIsNone(first.failure)

    # -------------------------------------------------------------------------
    # Test M: Failed test parsing
    # -------------------------------------------------------------------------
    def test_M_failed_test_parsing(self):
        """Test M: Individual failed test case parsing with message and stack trace."""
        summary, cases, failures = UnityTestResultParser.parse_xml_string(SAMPLE_NUNIT3_FAILED_XML)
        self.assertEqual(summary.total, 3)
        self.assertEqual(summary.failed, 1)
        self.assertEqual(summary.status, "FAILED")

        failed_case = next(c for c in cases if c.status == TestStatus.FAILED)
        self.assertEqual(failed_case.name, "Player_DiesAtZeroHealth")
        self.assertIsNotNone(failed_case.failure)
        self.assertIn("Expected: False but was: True", failed_case.failure.message)
        self.assertIn("CombatTests.cs:42", failed_case.failure.stack_trace)

    # -------------------------------------------------------------------------
    # Test N: Skipped test parsing
    # -------------------------------------------------------------------------
    def test_N_skipped_test_parsing(self):
        """Test N: Skipped/ignored test parsing with skip reason."""
        summary, cases, failures = UnityTestResultParser.parse_xml_string(SAMPLE_NUNIT3_FAILED_XML)
        skipped_case = next(c for c in cases if c.status == TestStatus.SKIPPED)
        self.assertEqual(skipped_case.name, "Player_Respawn")
        self.assertEqual(skipped_case.status, TestStatus.SKIPPED)
        self.assertIn("Respawn feature under refactoring", skipped_case.message)

    # -------------------------------------------------------------------------
    # Test O: Assertion failure classification
    # -------------------------------------------------------------------------
    def test_O_assertion_failure_classification(self):
        """Test O: Deterministic classification of assertion failures."""
        cat = UnityTestFailureClassifier.classify(
            message="Expected: 100 but was: 50",
            stack_trace="at NUnit.Framework.Assert.AreEqual()",
        )
        self.assertEqual(cat, TestFailureCategory.ASSERTION_FAILURE)

    # -------------------------------------------------------------------------
    # Test P: Test runner failure classification
    # -------------------------------------------------------------------------
    def test_P_test_runner_failure_classification(self):
        """Test P: Deterministic classification of runner crashes and exit codes."""
        cat = UnityTestFailureClassifier.classify(
            message="",
            stack_trace="",
            log_text="Fatal Error: UnityTestRunner crashed with SIGSEGV",
            exit_code=139,
        )
        self.assertEqual(cat, TestFailureCategory.TEST_RUNNER_FAILURE)

    # -------------------------------------------------------------------------
    # Test Q: Compile failure classification
    # -------------------------------------------------------------------------
    def test_Q_compile_failure_classification(self):
        """Test Q: Deterministic classification of compilation failures blocking tests."""
        cat = UnityTestFailureClassifier.classify(
            message="Scripts have compiler errors",
            log_text="Assets/Tests/TestScript.cs(10,5): error CS0103: The name 'xyz' does not exist in the current context",
            exit_code=1,
        )
        self.assertEqual(cat, TestFailureCategory.COMPILE_FAILURE)

    # -------------------------------------------------------------------------
    # Test R: Runtime failure classification
    # -------------------------------------------------------------------------
    def test_R_runtime_failure_classification(self):
        """Test R: Deterministic classification of unhandled runtime exceptions."""
        cat = UnityTestFailureClassifier.classify(
            message="System.NullReferenceException: Object reference not set to an instance of an object",
            stack_trace="at Tests.PlayerTests.Update () [0x00000] in Assets/Tests/PlayerTests.cs:15",
        )
        self.assertEqual(cat, TestFailureCategory.RUNTIME_FAILURE)

    # -------------------------------------------------------------------------
    # Test S: Sensitive data redaction
    # -------------------------------------------------------------------------
    def test_S_sensitive_data_redaction(self):
        """Test S: Automatic masking of sensitive tokens in failure messages and XML."""
        summary, cases, failures = UnityTestResultParser.parse_xml_string(SAMPLE_NUNIT3_FAILED_XML)
        failed_case = next(c for c in cases if c.status == TestStatus.FAILED)
        self.assertNotIn("AIzaSyFakeSecretToken12345", failed_case.failure.message)
        self.assertIn("[REDACTED_API_KEY]", failed_case.failure.message)

    # -------------------------------------------------------------------------
    # Test T: Emergency stop
    # -------------------------------------------------------------------------
    def test_T_emergency_stop(self):
        """Test T: Thread-safe emergency stop immediately freezes all test operations."""
        self.safety.emergency_stop("Operator security halt")
        self.assertTrue(self.safety.is_emergency_stopped())

        with self.assertRaises(EmergencyStopActiveError):
            self.safety.validate_test_mode("EditMode")

        with self.assertRaises(EmergencyStopActiveError):
            self.tests.run_editmode_tests(project_path=FIXTURE_PROJECT)

        r = self.registry.execute_tool("unity.run_editmode_tests", {"project_path": str(FIXTURE_PROJECT)})
        self.assertFalse(r.success)
        self.assertEqual(r.error_code, UnityErrorCode.EMERGENCY_STOPPED.value)

        self.safety.deactivate_emergency_stop()
        self.assertFalse(self.safety.is_emergency_stopped())

    # -------------------------------------------------------------------------
    # Test U: Model isolation
    # -------------------------------------------------------------------------
    def test_U_model_isolation(self):
        """Test U: Advisory models have zero shell authority and cannot bypass gates."""
        with self.assertRaises(UnitySafetyError) as ctx:
            self.safety.validate_tool_allowed("shell.execute")
        self.assertEqual(ctx.exception.code, UnityErrorCode.TOOL_NOT_ALLOWED)

        with self.assertRaises(UnitySafetyError) as ctx2:
            self.safety.validate_tool_allowed("unity.dangerous_eval")
        self.assertEqual(ctx2.exception.code, UnityErrorCode.TOOL_NOT_ALLOWED)

    # -------------------------------------------------------------------------
    # Test V: Evidence precedence
    # -------------------------------------------------------------------------
    def test_V_evidence_precedence(self):
        """Test V: Deterministic artifact evidence overrules model or exit code claims."""
        out_xml = Path(self.test_tmp_dir) / "EvidenceTest_results.xml"

        # Runner returns exit code 0 and says "Passed", but XML has a failed test
        def mock_conflicting_runner(cmd: list, timeout: float):
            out_xml.write_text(SAMPLE_NUNIT3_FAILED_XML, encoding="utf-8")
            return 0, "Model claim: All 3 tests passed successfully!", ""

        self.runner.set_mock_executor(mock_conflicting_runner)

        res = self.tests.run_editmode_tests(
            project_path=FIXTURE_PROJECT,
            result_xml_path=out_xml,
        )

        # Must report failure based on XML evidence, ignoring claim
        self.assertFalse(res.success)
        self.assertEqual(res.summary.failed, 1)

    # -------------------------------------------------------------------------
    # Test W: Test artifact verification
    # -------------------------------------------------------------------------
    def test_W_test_artifact_verification(self):
        """Test W: Verification of test artifact existence, XML validity, and SHA-256."""
        valid_xml = Path(self.test_tmp_dir) / "ValidArtifact.xml"
        valid_xml.write_text(SAMPLE_NUNIT3_PASSED_XML, encoding="utf-8")

        verifier = UnityTestArtifactVerifier(self.safety)
        info = verifier.verify_test_artifact(valid_xml)

        self.assertTrue(info.exists)
        self.assertTrue(info.verified)
        self.assertGreater(info.size_bytes, 0)
        self.assertTrue(len(info.sha256) == 64)

        # Empty file
        empty_xml = Path(self.test_tmp_dir) / "EmptyArtifact.xml"
        empty_xml.write_text("", encoding="utf-8")
        info_empty = verifier.verify_test_artifact(empty_xml)
        self.assertFalse(info_empty.verified)
        self.assertEqual(info_empty.size_bytes, 0)

        # Corrupt file
        corrupt_xml = Path(self.test_tmp_dir) / "CorruptArtifact.xml"
        corrupt_xml.write_text("<invalid><unclosed>", encoding="utf-8")
        info_corrupt = verifier.verify_test_artifact(corrupt_xml)
        self.assertFalse(info_corrupt.verified)

    # -------------------------------------------------------------------------
    # Test X: Audit logging
    # -------------------------------------------------------------------------
    def test_X_audit_logging(self):
        """Test X: All Phase 3 test tools dispatched through registry are audited."""
        registered = self.registry.get_registered_tools()
        for t in ALLOWED_UNITY_TEST_TOOLS:
            self.assertIn(t, registered)

        # 1. unity.validate_test_mode
        r1 = self.registry.execute_tool("unity.validate_test_mode", {"mode": "EditMode"})
        self.assertTrue(r1.success)

        # 2. unity.validate_test_filter
        r2 = self.registry.execute_tool("unity.validate_test_filter", {"filter": "MyTestClass"})
        self.assertTrue(r2.success)

        # 3. unity.parse_test_results
        r3 = self.registry.execute_tool("unity.parse_test_results", {"xml_content": SAMPLE_NUNIT3_PASSED_XML})
        self.assertTrue(r3.success)

        # 4. unity.get_test_summary
        r4 = self.registry.execute_tool("unity.get_test_summary", {"xml_content": SAMPLE_NUNIT3_PASSED_XML})
        self.assertTrue(r4.success)

        # 5. unity.diagnose_test_failure
        r5 = self.registry.execute_tool("unity.diagnose_test_failure", {"message": "Expected: 1 but was: 0"})
        self.assertTrue(r5.success)
        self.assertEqual(r5.data["category"], "ASSERTION_FAILURE")

        # 6. unity.verify_test_artifact
        valid_xml = Path(self.test_tmp_dir) / "AuditValid.xml"
        valid_xml.write_text(SAMPLE_NUNIT3_PASSED_XML, encoding="utf-8")
        r6 = self.registry.execute_tool("unity.verify_test_artifact", {"output_path": str(valid_xml)})
        self.assertTrue(r6.success)

        # 7. unity.run_editmode_tests (with mock)
        def mock_audit_run(cmd, timeout):
            return 0, "Tests ran", ""
        self.runner.set_mock_executor(mock_audit_run)
        out_xml_audit = Path(self.test_tmp_dir) / "AuditEditMode.xml"
        out_xml_audit.write_text(SAMPLE_NUNIT3_PASSED_XML, encoding="utf-8")
        r7 = self.registry.execute_tool("unity.run_editmode_tests", {
            "project_path": str(FIXTURE_PROJECT),
            "result_xml_path": str(out_xml_audit),
        })
        self.assertTrue(r7.success)

        # 8. unity.run_playmode_tests (with mock)
        out_xml_play = Path(self.test_tmp_dir) / "AuditPlayMode.xml"
        out_xml_play.write_text(SAMPLE_NUNIT3_PASSED_XML, encoding="utf-8")
        r8 = self.registry.execute_tool("unity.run_playmode_tests", {
            "project_path": str(FIXTURE_PROJECT),
            "result_xml_path": str(out_xml_play),
        })
        self.assertTrue(r8.success)

        # Verify audit records exist
        self.assertGreaterEqual(len(self.audit.entries), 8)

    # -------------------------------------------------------------------------
    # Test Y: Deterministic EditMode workflow
    # -------------------------------------------------------------------------
    def test_Y_deterministic_editmode_workflow(self):
        """Test Y: Complete controlled EditMode test verification workflow."""
        out_xml = Path(self.test_tmp_dir) / "WorkflowEditMode.xml"

        def mock_editmode_workflow(cmd: list, timeout: float):
            out_xml.write_text(SAMPLE_NUNIT3_PASSED_XML, encoding="utf-8")
            return 0, "All tests passed in EditMode", ""

        self.runner.set_mock_executor(mock_editmode_workflow)

        # Step 1: Validate mode
        mode = self.safety.validate_test_mode("EditMode")
        self.assertEqual(mode, "EditMode")

        # Step 2: Validate filter
        filt = self.safety.validate_test_filter("Tests.PlayerMovementTests")
        self.assertEqual(filt, "Tests.PlayerMovementTests")

        # Step 3: Run EditMode tests
        res = self.tests.run_editmode_tests(
            project_path=FIXTURE_PROJECT,
            test_filter=filt,
            result_xml_path=out_xml,
        )
        self.assertTrue(res.success)
        self.assertTrue(res.verified)

        # Step 4: Verify test artifact
        art = self.tests.verify_test_artifact(out_xml)
        self.assertTrue(art["verified"])

        # Step 5: Get summary
        summary = self.tests.get_test_summary(xml_path=out_xml)
        self.assertEqual(summary["status"], "PASSED")
        self.assertEqual(summary["passed"], 2)

    # -------------------------------------------------------------------------
    # Test Z: Deterministic PlayMode workflow
    # -------------------------------------------------------------------------
    def test_Z_deterministic_playmode_workflow(self):
        """Test Z: Complete controlled PlayMode test and failure diagnosis workflow."""
        out_xml = Path(self.test_tmp_dir) / "WorkflowPlayMode.xml"

        def mock_playmode_workflow(cmd: list, timeout: float):
            out_xml.write_text(SAMPLE_NUNIT3_FAILED_XML, encoding="utf-8")
            return 1, "PlayMode tests completed with failures", ""

        self.runner.set_mock_executor(mock_playmode_workflow)

        # Step 1: Validate mode
        mode = self.safety.validate_test_mode("PlayMode")
        self.assertEqual(mode, "PlayMode")

        # Step 2: Run PlayMode tests (expect failure)
        res = self.tests.run_playmode_tests(
            project_path=FIXTURE_PROJECT,
            result_xml_path=out_xml,
        )
        self.assertFalse(res.success)
        self.assertEqual(res.summary.failed, 1)

        # Step 3: Diagnose failure
        diag = self.tests.diagnose_test_failure(
            message=res.failures[0].message,
            stack_trace=res.failures[0].stack_trace,
            test_mode="PlayMode",
        )
        self.assertEqual(diag["category"], "ASSERTION_FAILURE")
        self.assertEqual(diag["distinction"], "ASSERTION / TEST CODE")


if __name__ == "__main__":
    unittest.main()
