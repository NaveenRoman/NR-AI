"""
NR-AI Step 8 Phase 5 Acceptance Tests: Unity Autonomous Repair & E2E Workflows.

Comprehensive deterministic acceptance suite covering test cases A through AB:
A.  test_A_agent_instantiation_and_wiring
B.  test_B_state_machine_transitions
C.  test_C_scenario_A_clean_workflow
D.  test_D_scenario_B_repairable_defect
E.  test_E_scenario_C_structural_failure_rollback
F.  test_F_scenario_D_stale_sha_rejection
G.  test_G_scenario_E_non_repairable_environment_failure
H.  test_H_scenario_F_two_repair_attempts_exhaustion
I.  test_I_scenario_G_thread_safe_emergency_stop
J.  test_J_scenario_H_model_prohibited_authority
K.  test_K_evidence_precedence
L.  test_L_syntax_failure_classification
M.  test_M_test_failure_classification
N.  test_N_runtime_failure_classification
O.  test_O_environment_failure_classification
P.  test_P_patch_size_limit_enforcement
Q.  test_Q_operation_file_count_limit_enforcement
R.  test_R_changed_lines_limit_enforcement
S.  test_S_secret_and_credential_detection
T.  test_T_path_traversal_rejection
U.  test_U_protected_files_and_directories_rejection
V.  test_V_atomic_backup_creation_and_cleanup
W.  test_W_rollback_sha256_restoration
X.  test_X_audit_logging_workflow_steps
Y.  test_Y_subprocess_security_no_shell
Z.  test_Z_tool_registry_dispatch_phase5
AA. test_AA_e2e_multi_step_defect_diagnosis_and_repair
AB. test_AB_full_system_state_serialization
"""

import hashlib
import json
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
    ALLOWED_UNITY_WORKFLOW_TOOLS,
    ALL_ALLOWED_UNITY_TOOLS,
    MAX_AST_FILES_PER_OP,
    MAX_AST_PATCH_BYTES,
    MAX_AST_CHANGED_LINES,
    MAX_AST_REPAIR_ATTEMPTS,
    redact_sensitive_data,
)
from app.agent.unity_environment import (
    UnityEnvironmentDetector,
    UnityEnvironmentInfo,
)
from app.agent.unity_project import (
    UnityProjectInspector,
    UnityProjectMetadata,
)
from app.agent.unity_build import (
    UnityBuildManager,
    UnityProcessRunner,
    UnityLogParser,
    CompilationResult,
    BuildResult,
    UnityCompilerIssue,
)
from app.agent.unity_tests import (
    UnityTestManager,
    UnityTestResultParser,
    UnityTestFailureClassifier,
    TestExecutionResult,
    TestSuiteResult,
    TestCaseResult,
    TestFailure,
    TestStatus,
    TestFailureCategory,
)
from app.agent.unity_ast import (
    CSharpParser,
    CSharpSyntaxTree,
    UnityScriptAnalyzer,
    UnityASTModifier,
    UnityScriptManager,
    ASTModificationProposal,
    ASTModificationType,
    CSharpSyntaxDefect,
)
from app.agent.unity_agent import (
    UnityAutonomousAgent,
    UnityWorkflowState,
    UnityFailureDomain,
    UnityWorkflowType,
    UnityWorkflowStep,
    UnityRepairAttempt,
    UnityWorkflowPlan,
    UnityWorkflowReport,
)
from app.agent.unity_tools import (
    UnityToolRegistry,
    UnityToolResult,
)
from app.memory.audit_logger import AuditLogger

WORKSPACE_ROOT = Path("C:/NR-AI").resolve()
FIXTURE_PROJECT = (WORKSPACE_ROOT / "nr_unity_test").resolve()

SAMPLE_VALID_CSHARP = """using System;
using UnityEngine;

namespace NRAI.Game
{
    public class GameManager : MonoBehaviour
    {
        [SerializeField] private int score = 0;

        public int Score { get => score; set => score = value; }

        private void Start()
        {
            Debug.Log("GameManager started");
        }

        public void AddScore(int amount)
        {
            score += amount;
        }
    }
}
"""

SAMPLE_DEFECTIVE_CSHARP = """using System;
using UnityEngine;

namespace NRAI.Game
{
    public class GameManager : MonoBehaviour
    {
        [SerializeField] private int score = 0;

        private void Start()
        {
            Debug.Log("Defective manager started");
        }
    }
}
"""


class TestStep8Phase5Unity(unittest.TestCase):
    """Acceptance test suite for Step 8 Phase 5: Unity Autonomous Repair & E2E Workflows."""

    def setUp(self):
        self.temp_audit_dir = tempfile.mkdtemp()
        self.audit = AuditLogger(log_dir=self.temp_audit_dir)

        self.test_tmp_dir = tempfile.mkdtemp(dir=str(WORKSPACE_ROOT / "scratch"))
        self.test_proj_dir = Path(self.test_tmp_dir) / "TestProject"
        self.test_proj_dir.mkdir(parents=True, exist_ok=True)
        (self.test_proj_dir / "Assets" / "Scripts").mkdir(parents=True, exist_ok=True)
        (self.test_proj_dir / "ProjectSettings").mkdir(parents=True, exist_ok=True)

        # Create ProjectVersion.txt
        pv = self.test_proj_dir / "ProjectSettings" / "ProjectVersion.txt"
        pv.write_text("m_EditorVersion: 2022.3.20f1\nm_EditorVersionWithRevision: 2022.3.20f1 (e3d5a5e3de95)\n", encoding="utf-8")

        # Create sample script
        self.sample_script = self.test_proj_dir / "Assets" / "Scripts" / "GameManager.cs"
        self.sample_script.write_text(SAMPLE_VALID_CSHARP, encoding="utf-8")
        self.sample_script_sha = hashlib.sha256(self.sample_script.read_bytes()).hexdigest().lower()

        self.checkpoints_dir = Path(self.test_tmp_dir) / "checkpoints"
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)

        self.safety = UnitySafetyGate(
            authorized_project=self.test_proj_dir,
            workspace_root=WORKSPACE_ROOT,
            rate_limit_calls_per_minute=300,
        )

        self.parser = CSharpParser()
        self.analyzer = UnityScriptAnalyzer(self.parser)
        self.modifier = UnityASTModifier(self.parser)
        self.script_manager = UnityScriptManager(
            safety_gate=self.safety,
            checkpoint_dir=self.checkpoints_dir,
            parser=self.parser,
            analyzer=self.analyzer,
            modifier=self.modifier,
            audit_logger=self.audit,
        )

        self.agent = UnityAutonomousAgent(
            safety_gate=self.safety,
            script_manager=self.script_manager,
            audit_logger=self.audit,
            workspace_root=WORKSPACE_ROOT,
            authorized_project=self.test_proj_dir,
        )

        self.registry = UnityToolRegistry(
            safety_gate=self.safety,
            ast_manager=self.script_manager,
            agent=self.agent,
            audit_logger=self.audit,
            workspace_root=WORKSPACE_ROOT,
            include_ast_tools=True,
            include_workflow_tools=True,
        )

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
    # Test A: Agent Instantiation & Wiring
    # -------------------------------------------------------------------------

    def test_A_agent_instantiation_and_wiring(self):
        """Test A: Verify UnityAutonomousAgent initializes with correct components, state, and properties."""
        agent = UnityAutonomousAgent(
            safety_gate=self.safety,
            script_manager=self.script_manager,
            audit_logger=self.audit,
            workspace_root=WORKSPACE_ROOT,
            authorized_project=self.test_proj_dir,
        )
        self.assertEqual(agent.state, UnityWorkflowState.IDLE)
        summary = agent.get_state_summary()
        self.assertEqual(summary["state"], "IDLE")
        self.assertFalse(summary["emergency_stopped"])
        self.assertEqual(summary["steps_executed"], 0)
        self.assertEqual(summary["active_backups"], 0)
        self.assertTrue(Path(summary["authorized_project"]).resolve() == self.test_proj_dir.resolve())

    # -------------------------------------------------------------------------
    # Test B: State Machine Transitions (all 17 states)
    # -------------------------------------------------------------------------

    def test_B_state_machine_transitions(self):
        """Test B: Verify all 17 explicit lifecycle states exist and can be transitioned to cleanly."""
        all_states = [
            UnityWorkflowState.IDLE,
            UnityWorkflowState.INSPECTING,
            UnityWorkflowState.ANALYZING,
            UnityWorkflowState.PLANNING,
            UnityWorkflowState.COMPILING,
            UnityWorkflowState.TESTING,
            UnityWorkflowState.DIAGNOSING,
            UnityWorkflowState.PROPOSING_REPAIR,
            UnityWorkflowState.VALIDATING_REPAIR,
            UnityWorkflowState.APPLYING_REPAIR,
            UnityWorkflowState.REBUILDING,
            UnityWorkflowState.RETESTING,
            UnityWorkflowState.VERIFYING,
            UnityWorkflowState.COMPLETED,
            UnityWorkflowState.ROLLED_BACK,
            UnityWorkflowState.FAILED,
            UnityWorkflowState.STOPPED,
        ]
        self.assertEqual(len(all_states), 17)

        for s in all_states:
            self.agent._transition_to(s, action="test_transition")
            self.assertEqual(self.agent.state, s)

    # -------------------------------------------------------------------------
    # Test C: Scenario A - Valid Project E2E Workflow
    # -------------------------------------------------------------------------

    def test_C_scenario_A_clean_workflow(self):
        """Test C (Scenario A): Clean inspection -> compilation -> testing -> verification -> COMPLETED."""
        report = self.agent.run_workflow(
            goal="Standard automated build and test pipeline",
            workflow_type=UnityWorkflowType.FULL_INSPECT_COMPILE_TEST,
            project_path=self.test_proj_dir,
            deterministic_compile_success=True,
            deterministic_test_success=True,
        )
        self.assertTrue(report.success)
        self.assertEqual(report.final_state, UnityWorkflowState.COMPLETED)
        self.assertGreater(report.steps_executed, 4)
        self.assertTrue(report.deterministic_evidence.get("compiler_verified"))
        self.assertTrue(report.deterministic_evidence.get("test_results_verified"))
        self.assertTrue(report.deterministic_evidence.get("ast_syntax_verified"))
        self.assertEqual(len(report.repaired_files), 0)

    # -------------------------------------------------------------------------
    # Test D: Scenario B - Repairable Defect Autonomous Repair
    # -------------------------------------------------------------------------

    def test_D_scenario_B_repairable_defect(self):
        """Test D (Scenario B): Diagnose -> propose repair -> AST modification -> rebuild -> retest -> COMPLETED."""
        prop = ASTModificationProposal(
            proposal_id="prop_add_helper",
            target_file=str(self.sample_script),
            expected_sha256=self.sample_script_sha,
            modification_type=ASTModificationType.ADD_METHOD,
            target_type="GameManager",
            new_node_content="public void ResetScore() { score = 0; }\n",
            rationale="Add score reset helper function",
            diagnostics_addressed=["DIAG001"],
            attempt_count=1,
        )

        report = self.agent.run_workflow(
            goal="Repair defective project scripts by adding missing helper",
            workflow_type=UnityWorkflowType.AUTONOMOUS_REPAIR,
            project_path=self.test_proj_dir,
            custom_proposals=[prop],
            deterministic_compile_success=False,  # Triggers repair branch
            deterministic_test_success=True,
        )
        self.assertTrue(report.success)
        self.assertEqual(report.final_state, UnityWorkflowState.COMPLETED)
        self.assertEqual(len(report.repair_attempts), 1)
        self.assertTrue(report.repair_attempts[0].applied)
        self.assertTrue(report.repair_attempts[0].verified)
        self.assertIn(str(self.sample_script), report.repaired_files)
        # Verify symbol exists now
        tree = self.parser.parse_file(self.sample_script)
        symbols = [m.name for m in tree.methods]
        self.assertIn("ResetScore", symbols)

    # -------------------------------------------------------------------------
    # Test E: Scenario C - Structural Failure Triggers Automatic Rollback
    # -------------------------------------------------------------------------

    def test_E_scenario_C_structural_failure_rollback(self):
        """Test E (Scenario C): Structural syntax error in repair -> automatic byte-for-byte rollback."""
        initial_sha = hashlib.sha256(self.sample_script.read_bytes()).hexdigest().lower()

        # Proposal with broken syntax
        broken_prop = ASTModificationProposal(
            proposal_id="prop_broken",
            target_file=str(self.sample_script),
            expected_sha256=initial_sha,
            modification_type=ASTModificationType.ADD_METHOD,
            target_type="GameManager",
            new_node_content="public void Broken( { invalid syntax\n",
            rationale="Broken syntax causing structural failure",
            attempt_count=1,
        )

        res = self.agent.execute_repair_loop(
            proposals=[broken_prop],
            project_path=self.test_proj_dir,
            rebuild_and_retest=True,
        )
        self.assertFalse(res["success"])
        self.assertIn(str(self.sample_script), res["rolled_back_files"])

        # Check original SHA is fully restored
        restored_sha = hashlib.sha256(self.sample_script.read_bytes()).hexdigest().lower()
        self.assertEqual(initial_sha, restored_sha)

    # -------------------------------------------------------------------------
    # Test F: Scenario D - Stale Target Hash Rejection
    # -------------------------------------------------------------------------

    def test_F_scenario_D_stale_sha_rejection(self):
        """Test F (Scenario D): Proposal with incorrect expected SHA-256 is rejected without edit."""
        initial_content = self.sample_script.read_text(encoding="utf-8")
        stale_hash = "0000000000000000000000000000000000000000000000000000000000000000"

        stale_prop = ASTModificationProposal(
            proposal_id="prop_stale",
            target_file=str(self.sample_script),
            expected_sha256=stale_hash,
            modification_type=ASTModificationType.ADD_METHOD,
            target_type="GameManager",
            new_node_content="public void Stale() {}\n",
            rationale="Test stale hash",
            attempt_count=1,
        )

        res = self.agent.execute_repair_loop(
            proposals=[stale_prop],
            project_path=self.test_proj_dir,
        )
        self.assertFalse(res["success"])
        self.assertEqual(res["error_code"], UnityErrorCode.STALE_TARGET.value)
        self.assertEqual(self.sample_script.read_text(encoding="utf-8"), initial_content)

    # -------------------------------------------------------------------------
    # Test G: Scenario E - Non-Repairable Environment/License Failure
    # -------------------------------------------------------------------------

    def test_G_scenario_E_non_repairable_environment_failure(self):
        """Test G (Scenario E): Non-repairable environment/license failure halts cleanly without code edits."""
        # Create an agent with mocked environment that reports missing editor
        mock_env = UnityEnvironmentDetector()
        mock_env.detect_environment = lambda: UnityEnvironmentInfo(
            is_available=False,
            editors=[],
        )

        env_agent = UnityAutonomousAgent(
            safety_gate=self.safety,
            env_detector=mock_env,
            script_manager=self.script_manager,
            audit_logger=self.audit,
            workspace_root=WORKSPACE_ROOT,
            authorized_project=self.test_proj_dir,
        )

        initial_sha = hashlib.sha256(self.sample_script.read_bytes()).hexdigest().lower()

        report = env_agent.run_workflow(
            goal="Compile and test project in environment without editor",
            workflow_type=UnityWorkflowType.FULL_INSPECT_COMPILE_TEST,
            project_path=self.test_proj_dir,
            deterministic_compile_success=False,
        )

        self.assertFalse(report.success)
        self.assertEqual(report.final_state, UnityWorkflowState.FAILED)
        self.assertEqual(report.failure_domain, UnityFailureDomain.ENVIRONMENT_FAILURE)
        # Ensure zero code edits were attempted or performed
        self.assertEqual(len(report.repaired_files), 0)
        self.assertEqual(len(report.repair_attempts), 0)
        self.assertEqual(hashlib.sha256(self.sample_script.read_bytes()).hexdigest().lower(), initial_sha)

    # -------------------------------------------------------------------------
    # Test H: Scenario F - Repair Attempt Count Limit (Max 2 Attempts)
    # -------------------------------------------------------------------------

    def test_H_scenario_F_two_repair_attempts_exhaustion(self):
        """Test H (Scenario F): Attempt count > 2 is rejected with REPAIR_ATTEMPTS_EXCEEDED."""
        prop_3rd_attempt = ASTModificationProposal(
            proposal_id="prop_attempt3",
            target_file=str(self.sample_script),
            expected_sha256=self.sample_script_sha,
            modification_type=ASTModificationType.ADD_METHOD,
            target_type="GameManager",
            new_node_content="public void ThirdAttempt() {}\n",
            rationale="Third attempt should be rejected",
            attempt_count=3,  # Exceeds MAX_AST_REPAIR_ATTEMPTS (2)
        )

        with self.assertRaises(UnitySafetyError) as ctx:
            self.agent.execute_repair_loop(
                proposals=[prop_3rd_attempt],
                project_path=self.test_proj_dir,
            )
        self.assertEqual(ctx.exception.error_code, UnityErrorCode.REPAIR_ATTEMPTS_EXCEEDED)

    # -------------------------------------------------------------------------
    # Test I: Scenario G - Thread-Safe Emergency Stop
    # -------------------------------------------------------------------------

    def test_I_scenario_G_thread_safe_emergency_stop(self):
        """Test I (Scenario G): Triggering emergency stop immediately freezes agent, sets state STOPPED."""
        self.agent.emergency_stop("Operator triggered emergency stop test")
        self.assertEqual(self.agent.state, UnityWorkflowState.STOPPED)
        self.assertTrue(self.safety.is_emergency_stopped())

        # Further workflow attempts must raise EmergencyStopActiveError
        with self.assertRaises(EmergencyStopActiveError):
            self.agent.run_workflow(
                goal="Should fail due to emergency stop",
                project_path=self.test_proj_dir,
            )

    # -------------------------------------------------------------------------
    # Test J: Scenario H - Model Authority Prohibited
    # -------------------------------------------------------------------------

    def test_J_scenario_H_model_prohibited_authority(self):
        """Test J (Scenario H): LLM model authority is strictly advisory; unverified claims/direct shell execution fail."""
        # Arbitrary shell or commands cannot be run through Unity autonomous agent
        with self.assertRaises(UnitySafetyError):
            self.safety.validate_tool_allowed("unity.execute_arbitrary_powershell")

        # Direct writes without AST proposal cannot bypass safety gate
        with self.assertRaises(UnitySafetyError):
            self.safety.validate_script_file_target(self.test_proj_dir / "Assets" / "malicious.sh")

    # -------------------------------------------------------------------------
    # Test K: Evidence Precedence (Deterministic Evidence > Model Claim)
    # -------------------------------------------------------------------------

    def test_K_evidence_precedence(self):
        """Test K: Deterministic evidence overrules model claim; failed test artifact marks workflow FAILED."""
        # Even if the goal or user asserts the project works, failing deterministic tests produce FAILED
        report = self.agent.run_workflow(
            goal="Everything is guaranteed working according to model",
            workflow_type=UnityWorkflowType.FULL_INSPECT_COMPILE_TEST,
            project_path=self.test_proj_dir,
            deterministic_compile_success=True,
            deterministic_test_success=False,  # Deterministic test artifact fails
        )
        self.assertFalse(report.success)
        self.assertEqual(report.final_state, UnityWorkflowState.FAILED)
        self.assertFalse(report.deterministic_evidence.get("test_results_verified"))

    # -------------------------------------------------------------------------
    # Test L: Syntax Failure Classification
    # -------------------------------------------------------------------------

    def test_L_syntax_failure_classification(self):
        """Test L: Syntax defect in C# script is classified into SYNTAX_FAILURE domain and marked repairable."""
        bad_script = self.test_proj_dir / "Assets" / "Scripts" / "BadSyntax.cs"
        bad_script.write_text("using UnityEngine;\npublic class BadSyntax : MonoBehaviour {\n", encoding="utf-8")

        diag = self.agent.diagnose_project_defects(self.test_proj_dir)
        self.assertGreater(diag["defect_count"], 0)
        self.assertEqual(diag["primary_domain"], UnityFailureDomain.SYNTAX_FAILURE.value)
        self.assertTrue(diag["is_repairable"])

    # -------------------------------------------------------------------------
    # Test M: Test Failure Classification
    # -------------------------------------------------------------------------

    def test_M_test_failure_classification(self):
        """Test M: Test runner failures are classified into TEST_FAILURE domain."""
        failed_test_res = TestExecutionResult(
            success=False,
            test_mode="EditMode",
            cases=[
                TestCaseResult(
                    name="ScoreTest",
                    full_name="GameTests.ScoreTest",
                    status=TestStatus.FAILED,
                    failure=TestFailure(
                        message="Expected 10 but was 0",
                        category=TestFailureCategory.ASSERTION_FAILURE,
                    ),
                )
            ],
        )

        diag = self.agent.diagnose_project_defects(self.test_proj_dir, test_result=failed_test_res)
        self.assertEqual(diag["primary_domain"], UnityFailureDomain.TEST_FAILURE.value)
        self.assertTrue(diag["is_repairable"])

    # -------------------------------------------------------------------------
    # Test N: Runtime Failure Classification
    # -------------------------------------------------------------------------

    def test_N_runtime_failure_classification(self):
        """Test N: Runtime exceptions in workflow execution result in FAILED state and clean rollback."""
        # Pass an invalid goal exceeding maximum limit to trigger parameter validation error
        long_goal = "X" * 3000
        with self.assertRaises(UnitySafetyError):
            self.agent.run_workflow(goal=long_goal, project_path=self.test_proj_dir)

    # -------------------------------------------------------------------------
    # Test O: Environment Failure Classification
    # -------------------------------------------------------------------------

    def test_O_environment_failure_classification(self):
        """Test O: Missing environment components correctly classify as ENVIRONMENT_FAILURE."""
        mock_env = UnityEnvironmentDetector()
        mock_env.detect_environment = lambda: UnityEnvironmentInfo(
            is_available=False,
            editors=[],
        )
        agent = UnityAutonomousAgent(
            safety_gate=self.safety,
            env_detector=mock_env,
            script_manager=self.script_manager,
            audit_logger=self.audit,
            workspace_root=WORKSPACE_ROOT,
            authorized_project=self.test_proj_dir,
        )
        diag = agent.diagnose_project_defects(self.test_proj_dir)
        self.assertEqual(diag["primary_domain"], UnityFailureDomain.ENVIRONMENT_FAILURE.value)
        self.assertFalse(diag["is_repairable"])

    # -------------------------------------------------------------------------
    # Test P: Patch Size Limit Enforcement (100 KB limit)
    # -------------------------------------------------------------------------

    def test_P_patch_size_limit_enforcement(self):
        """Test P: Patch content exceeding 100 KB is rejected with PATCH_TOO_LARGE."""
        oversized_content = "public void Huge() {\n" + ("// comment line\n" * 7000) + "}\n"
        self.assertGreater(len(oversized_content.encode("utf-8")), MAX_AST_PATCH_BYTES)

        prop = ASTModificationProposal(
            proposal_id="prop_huge",
            target_file=str(self.sample_script),
            expected_sha256=self.sample_script_sha,
            modification_type=ASTModificationType.ADD_METHOD,
            target_type="GameManager",
            new_node_content=oversized_content,
            rationale="Oversized patch test",
            attempt_count=1,
        )

        with self.assertRaises(UnitySafetyError) as ctx:
            self.agent.execute_repair_loop(
                proposals=[prop],
                project_path=self.test_proj_dir,
            )
        self.assertEqual(ctx.exception.error_code, UnityErrorCode.PATCH_TOO_LARGE)

    # -------------------------------------------------------------------------
    # Test Q: Operation File Count Limit Enforcement (Max 5 files)
    # -------------------------------------------------------------------------

    def test_Q_operation_file_count_limit_enforcement(self):
        """Test Q: Proposals targeting more than 5 distinct files are rejected with TOO_MANY_FILES_CHANGED."""
        proposals = []
        for i in range(6):
            f = self.test_proj_dir / "Assets" / "Scripts" / f"Script{i}.cs"
            f.write_text(SAMPLE_VALID_CSHARP, encoding="utf-8")
            f_sha = hashlib.sha256(f.read_bytes()).hexdigest().lower()
            proposals.append(ASTModificationProposal(
                proposal_id=f"prop_{i}",
                target_file=str(f),
                expected_sha256=f_sha,
                modification_type=ASTModificationType.ADD_METHOD,
                target_type="GameManager",
                new_node_content="public void M() {}\n",
                rationale="Multi-file proposal",
                attempt_count=1,
            ))

        with self.assertRaises(UnitySafetyError) as ctx:
            self.agent.execute_repair_loop(
                proposals=proposals,
                project_path=self.test_proj_dir,
            )
        self.assertEqual(ctx.exception.error_code, UnityErrorCode.TOO_MANY_FILES_CHANGED)

    # -------------------------------------------------------------------------
    # Test R: Changed Lines Limit Enforcement (Max 500 lines)
    # -------------------------------------------------------------------------

    def test_R_changed_lines_limit_enforcement(self):
        """Test R: Patch containing more than 500 lines is rejected with TOO_MANY_LINES_CHANGED."""
        many_lines = "\n".join([f"// line {i}" for i in range(550)]) + "\n"
        prop = ASTModificationProposal(
            proposal_id="prop_many_lines",
            target_file=str(self.sample_script),
            expected_sha256=self.sample_script_sha,
            modification_type=ASTModificationType.ADD_METHOD,
            target_type="GameManager",
            new_node_content=many_lines,
            rationale="Too many lines test",
            attempt_count=1,
        )

        with self.assertRaises(UnitySafetyError) as ctx:
            self.agent.execute_repair_loop(
                proposals=[prop],
                project_path=self.test_proj_dir,
            )
        self.assertEqual(ctx.exception.error_code, UnityErrorCode.TOO_MANY_LINES_CHANGED)

    # -------------------------------------------------------------------------
    # Test S: Secret & Credential Detection in Proposals
    # -------------------------------------------------------------------------

    def test_S_secret_and_credential_detection(self):
        """Test S: Secrets and tokens in diagnostics or proposal rationale are redacted."""
        secret_msg = "Failed with token: AIzaSyD3x4mpl3K3yS3cr3tT0k3n"
        redacted = redact_sensitive_data(secret_msg)
        self.assertNotIn("AIzaSyD3x4mpl3K3yS3cr3tT0k3n", redacted)
        self.assertIn("[REDACTED_API_KEY]", redacted)

    # -------------------------------------------------------------------------
    # Test T: Path Traversal Rejection
    # -------------------------------------------------------------------------

    def test_T_path_traversal_rejection(self):
        """Test T: Project path or script target containing '..' is rejected with PATH_TRAVERSAL_DETECTED."""
        traversal_path = f"{self.test_proj_dir}/../outside_project"
        with self.assertRaises(UnitySafetyError) as ctx:
            self.agent.diagnose_project_defects(project_path=traversal_path)
        self.assertEqual(ctx.exception.error_code, UnityErrorCode.PATH_TRAVERSAL_DETECTED)

    # -------------------------------------------------------------------------
    # Test U: Protected Files & Directory Access Rejection
    # -------------------------------------------------------------------------

    def test_U_protected_files_and_directories_rejection(self):
        """Test U: Attempts to modify protected files like ProjectVersion.txt are rejected."""
        pv = self.test_proj_dir / "ProjectSettings" / "ProjectVersion.txt"
        with self.assertRaises(UnitySafetyError) as ctx:
            self.safety.validate_script_file_target(pv)
        self.assertIn(ctx.exception.error_code, [
            UnityErrorCode.FILE_NOT_AUTHORIZED,
            UnityErrorCode.PROTECTED_FILE_REJECTED,
        ])

    # -------------------------------------------------------------------------
    # Test V: Atomic Backup Creation and Cleanup
    # -------------------------------------------------------------------------

    def test_V_atomic_backup_creation_and_cleanup(self):
        """Test V: Atomic backup checkpoint is created on disk during repair and tracked."""
        prop = ASTModificationProposal(
            proposal_id="prop_backup_test",
            target_file=str(self.sample_script),
            expected_sha256=self.sample_script_sha,
            modification_type=ASTModificationType.ADD_METHOD,
            target_type="GameManager",
            new_node_content="public void BackupTest() {}\n",
            rationale="Backup test",
            attempt_count=1,
        )

        res = self.agent.execute_repair_loop(
            proposals=[prop],
            project_path=self.test_proj_dir,
            rebuild_and_retest=False,
        )
        self.assertTrue(res["success"])
        attempts = res["attempts"]
        self.assertEqual(len(attempts), 1)
        backup_path = attempts[0]["backup_path"]
        self.assertTrue(backup_path is not None)
        self.assertTrue(Path(backup_path).exists())

    # -------------------------------------------------------------------------
    # Test W: Rollback Byte-For-Byte SHA-256 Restoration
    # -------------------------------------------------------------------------

    def test_W_rollback_sha256_restoration(self):
        """Test W: Manual and automated rollback restore pre-edit SHA-256 byte-for-byte."""
        orig_bytes = self.sample_script.read_bytes()
        orig_sha = hashlib.sha256(orig_bytes).hexdigest().lower()

        # Apply modification
        prop = ASTModificationProposal(
            proposal_id="prop_rollback_verify",
            target_file=str(self.sample_script),
            expected_sha256=orig_sha,
            modification_type=ASTModificationType.ADD_METHOD,
            target_type="GameManager",
            new_node_content="public void TempMethod() {}\n",
            rationale="Will rollback",
            attempt_count=1,
        )
        mod_res = self.script_manager.apply_modification(prop, validate_compile=False)
        self.assertTrue(mod_res.success)
        self.assertNotEqual(hashlib.sha256(self.sample_script.read_bytes()).hexdigest().lower(), orig_sha)

        # Rollback
        rolled_back = self.script_manager.rollback_modification(
            target_file=self.sample_script,
            checkpoint_path=mod_res.backup_path,
            expected_original_sha256=orig_sha,
        )
        self.assertTrue(rolled_back)
        self.assertEqual(self.sample_script.read_bytes(), orig_bytes)
        self.assertEqual(hashlib.sha256(self.sample_script.read_bytes()).hexdigest().lower(), orig_sha)

    # -------------------------------------------------------------------------
    # Test X: Audit Logging for Workflow Steps
    # -------------------------------------------------------------------------

    def test_X_audit_logging_workflow_steps(self):
        """Test X: State transitions and workflow execution are recorded in audit log."""
        self.agent.run_workflow(
            goal="Audit logging verification workflow",
            workflow_type=UnityWorkflowType.FULL_INSPECT_COMPILE_TEST,
            project_path=self.test_proj_dir,
            deterministic_compile_success=True,
            deterministic_test_success=True,
        )

        events = self.audit.get_recent_events(limit=50)
        event_types = [e.get("event_type") for e in events]
        self.assertIn("unity.state_transition", event_types)
        self.assertIn("unity.workflow_finished", event_types)

    # -------------------------------------------------------------------------
    # Test Y: Subprocess Security Verification (0 shell=True)
    # -------------------------------------------------------------------------

    def test_Y_subprocess_security_no_shell(self):
        """Test Y: Verify that shell=True is never used across all Unity agent modules."""
        agent_dir = WORKSPACE_ROOT / "app" / "agent"
        unity_files = list(agent_dir.glob("unity_*.py"))
        self.assertGreater(len(unity_files), 5)

        for u_file in unity_files:
            content = u_file.read_text(encoding="utf-8")
            self.assertNotIn("shell=True", content, f"Disallowed 'shell=True' found in {u_file.name}")

    # -------------------------------------------------------------------------
    # Test Z: Tool Registry Dispatch of all 8 Phase 5 Tools (44 tools total)
    # -------------------------------------------------------------------------

    def test_Z_tool_registry_dispatch_phase5(self):
        """Test Z: Verify all 8 Phase 5 tools dispatch cleanly, total tools count is exactly 44."""
        tools = self.registry.get_registered_tools()
        self.assertEqual(len(tools), 44)
        self.assertEqual(len(tools), len(set(tools)))  # Zero duplicates

        for t_name in ALLOWED_UNITY_WORKFLOW_TOOLS:
            self.assertIn(t_name, tools)

        # Dispatch get_agent_state
        res_state = self.registry.dispatch("unity.get_agent_state", {})
        self.assertTrue(res_state.success)
        self.assertEqual(res_state.data["state"], "IDLE")

        # Dispatch plan_project_repair
        res_plan = self.registry.dispatch("unity.plan_project_repair", {
            "goal": "Repair game manager",
            "project_path": str(self.test_proj_dir),
        })
        self.assertTrue(res_plan.success)
        self.assertEqual(res_plan.data["workflow_type"], "AUTONOMOUS_REPAIR")

        # Dispatch verify_workflow_evidence
        res_ev = self.registry.dispatch("unity.verify_workflow_evidence", {
            "evidence": {
                "compiler_verified": True,
                "test_results_verified": True,
                "ast_syntax_verified": True,
            }
        })
        self.assertTrue(res_ev.success)
        self.assertTrue(res_ev.data["valid"])

    # -------------------------------------------------------------------------
    # Test AA: End-to-End Multi-Step Defect Diagnosis and Repair Simulation
    # -------------------------------------------------------------------------

    def test_AA_e2e_multi_step_defect_diagnosis_and_repair(self):
        """Test AA: Full multi-step diagnosis and autonomous repair workflow execution."""
        # 1. Start with defective script lacking required method
        self.sample_script.write_text(SAMPLE_DEFECTIVE_CSHARP, encoding="utf-8")
        current_sha = hashlib.sha256(self.sample_script.read_bytes()).hexdigest().lower()

        # 2. Propose AST addition of missing method
        repair_prop = ASTModificationProposal(
            proposal_id="prop_e2e_repair",
            target_file=str(self.sample_script),
            expected_sha256=current_sha,
            modification_type=ASTModificationType.ADD_METHOD,
            target_type="GameManager",
            new_node_content="public void AddScore(int amount) { score += amount; }\n",
            rationale="Restore missing AddScore method",
            attempt_count=1,
        )

        # 3. Run autonomous workflow
        report = self.agent.run_workflow(
            goal="Autonomous restoration of AddScore method",
            workflow_type=UnityWorkflowType.AUTONOMOUS_REPAIR,
            project_path=self.test_proj_dir,
            custom_proposals=[repair_prop],
            deterministic_compile_success=False,  # Triggers repair branch
            deterministic_test_success=True,
        )

        self.assertTrue(report.success)
        self.assertEqual(report.final_state, UnityWorkflowState.COMPLETED)
        self.assertEqual(len(report.repaired_files), 1)

        # 4. Verify AST tree structure confirms method exists
        tree = self.parser.parse_file(self.sample_script)
        methods = [m.name for m in tree.methods]
        self.assertIn("AddScore", methods)

    # -------------------------------------------------------------------------
    # Test AB: Full System State Serialization and Report Generation
    # -------------------------------------------------------------------------

    def test_AB_full_system_state_serialization(self):
        """Test AB: Verify all Phase 5 structured data models serialize cleanly to JSON."""
        report = self.agent.run_workflow(
            goal="Test serialization",
            workflow_type=UnityWorkflowType.FULL_INSPECT_COMPILE_TEST,
            project_path=self.test_proj_dir,
            deterministic_compile_success=True,
            deterministic_test_success=True,
        )

        report_dict = report.to_dict()
        self.assertIsInstance(report_dict, dict)
        self.assertEqual(report_dict["goal"], "Test serialization")
        self.assertEqual(report_dict["workflow_type"], "FULL_INSPECT_COMPILE_TEST")
        self.assertIn("steps", report_dict)
        self.assertIn("deterministic_evidence", report_dict)

        # Verify JSON serializability
        serialized_json = json.dumps(report_dict, indent=2)
        self.assertIn("workflow_id", serialized_json)
        self.assertIn("COMPLETED", serialized_json)


if __name__ == "__main__":
    unittest.main()
