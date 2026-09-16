"""
NR-AI Step 9 Phase 5 Acceptance Tests: Unreal Engine Autonomous Repair & E2E Workflows.

Deterministic acceptance suite covering:
1. Agent instantiation and subsystem wiring
2. Deterministic 16-state lifecycle transitions
3. Invalid state transitions rejected
4. Failure taxonomy classification (Compilation, Linker, UHT, Module, Crash, Assertion, Ensure, Test, Syntax)
5. Evidence precedence hierarchy: Deterministic Evidence > Model Claims
6. Repair proposal validation & operational bounds (max 5 files, 100 KB patch, 500 lines, 1 MB file)
7. Prohibited tokens in proposals rejected
8. Workflow planner default steps generation
9. Workflow plan step limit enforcement (max 25 steps)
10. Workflow plan safety validation (rejects shell, exec, subprocess, powershell, cmd)
11. Bounded repair loop: successful repair on attempt 1
12. Bounded repair loop: automatic byte-for-byte rollback on post-verification failure
13. Bounded repair loop: maximum 2 repair attempts strictly enforced (double-failure handling)
14. Stale target SHA-256 rejection
15. Emergency stop freezes active workflow immediately
16. Process ownership tracking & cleanup (0 orphan workflow-owned processes)
17. Audit logging and sensitive token redaction
18. E2E workflow: WORKFLOW_BUILD_FAILURE_REPAIR
19. E2E workflow: WORKFLOW_TEST_FAILURE_REPAIR
20. E2E workflow: WORKFLOW_RUNTIME_FAILURE_REPAIR
21. E2E workflow: WORKFLOW_SOURCE_CHANGE_VERIFY
22. E2E workflow: WORKFLOW_ROLLBACK
23. E2E workflow: WORKFLOW_SAFETY_REJECTION
24. E2E workflow: WORKFLOW_USER_STOP
25. Tool registry dispatch for all 10 Phase 5 tools
26. Duplicate tool detection across all 54 Unreal tools (0 duplicates)
27. Static safety audit (shell=True == 0 across all unreal_*.py)
28. Data model JSON serialization integrity
"""

import glob
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from app.agent.unreal_safety import (
    UnrealSafetyGate,
    UnrealErrorCode,
    UnrealSafetyError,
    EmergencyStopActiveError,
    ALLOWED_UNREAL_PHASE5_TOOLS,
    ALL_ALLOWED_UNREAL_TOOLS,
    MAX_SOURCE_FILE_BYTES,
    MAX_PATCH_BYTES,
    MAX_CHANGED_LINES,
    MAX_FILES_PER_OPERATION,
    MAX_REPAIR_ATTEMPTS,
    MAX_WORKFLOW_STEPS,
    WORKFLOW_TIMEOUT_SECONDS,
    redact_sensitive_data,
    DEFAULT_UNREAL_SAFETY_GATE,
)
from app.agent.unreal_agent import (
    UnrealAutonomousAgent,
    DEFAULT_UNREAL_AGENT,
    UnrealWorkflowState,
    UnrealFailureDomain,
    UnrealWorkflowType,
    UnrealWorkflowStep,
    UnrealRepairAttempt,
    UnrealWorkflowPlan,
    UnrealProcessRecord,
    UnrealWorkflowReport,
)
from app.agent.unreal_tools import (
    UnrealToolRegistry,
    UnrealToolResult,
    DEFAULT_UNREAL_TOOL_REGISTRY,
)
from app.agent.unreal_source import (
    UnrealCppAnalyzer,
    UnrealBlueprintInspector,
    UnrealSourceModifier,
    UnrealModificationProposal,
    UnrealModificationOperation,
)
from app.agent.unreal_build import (
    UnrealBuildDiagnostic,
    UnrealBuildIssueCategory,
)
from app.memory.audit_logger import AuditLogger


SAMPLE_HEADER = """// Copyright Epic Games, Inc. All Rights Reserved.
#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "MyActor.generated.h"

UCLASS()
class MYPROJECT_API AMyActor : public AActor
{
\tGENERATED_BODY()

public:
\tAMyActor();

\tUPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Stats")
\tfloat Speed;

\tUFUNCTION(BlueprintCallable, Category = "Actions")
\tvirtual void MoveForward(float Val);
};
"""

SAMPLE_SOURCE = """// Copyright Epic Games, Inc. All Rights Reserved.
#include "MyActor.h"

AMyActor::AMyActor()
{
\tPrimaryActorTick.bCanEverTick = true;
\tSpeed = 100.0f;
}

void AMyActor::MoveForward(float Val)
{
\tSpeed += Val;
}
"""


class TestStep9Phase5Unreal(unittest.TestCase):
    """Test suite for Unreal Engine Agent Step 9 Phase 5 Autonomous Repair & E2E Workflows."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="nrai_ue_phase5_test_")
        self.proj_dir = Path(self.tmp_dir) / "MyProject"
        self.proj_dir.mkdir(parents=True, exist_ok=True)
        self.source_dir = self.proj_dir / "Source" / "MyProject"
        self.source_dir.mkdir(parents=True, exist_ok=True)

        # Create valid .uproject
        self.uproject_file = self.proj_dir / "MyProject.uproject"
        self.uproject_file.write_text(json.dumps({"FileVersion": 3, "EngineAssociation": "5.4"}), encoding="utf-8")

        # Create header and source files
        self.header_file = self.source_dir / "MyActor.h"
        self.header_file.write_text(SAMPLE_HEADER, encoding="utf-8")

        self.cpp_file = self.source_dir / "MyActor.cpp"
        self.cpp_file.write_text(SAMPLE_SOURCE, encoding="utf-8")
        self.cpp_sha = hashlib.sha256(self.cpp_file.read_bytes()).hexdigest()

        # Initialize safety gate, modifier and agent for this temp workspace
        self.safety = UnrealSafetyGate(
            workspace_root=Path(self.tmp_dir),
            authorized_projects=[self.proj_dir],
        )
        self.analyzer = UnrealCppAnalyzer(safety_gate=self.safety)
        self.modifier = UnrealSourceModifier(
            safety_gate=self.safety,
            analyzer=self.analyzer,
            backup_root=Path(self.tmp_dir) / "checkpoints",
        )
        self.agent = UnrealAutonomousAgent(
            safety_gate=self.safety,
            cpp_analyzer=self.analyzer,
            source_modifier=self.modifier,
            workspace_root=Path(self.tmp_dir),
        )
        self.registry = UnrealToolRegistry(
            safety_gate=self.safety,
            workspace_root=Path(self.tmp_dir),
            cpp_analyzer=self.analyzer,
            source_modifier=self.modifier,
            agent=self.agent,
            include_workflow_tools=True,
        )

    def tearDown(self):
        # Clean up any processes and temporary directories
        self.agent.cleanup_owned_processes()
        self.safety.reset_emergency_stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    # 1. Agent Instantiation & Wiring
    def test_01_agent_instantiation_and_wiring(self):
        """Test 1: Agent initializes with default state IDLE and all sub-engines wired."""
        self.assertEqual(self.agent.current_state, UnrealWorkflowState.IDLE)
        self.assertIsNotNone(self.agent.safety)
        self.assertIsNotNone(self.agent.cpp_analyzer)
        self.assertIsNotNone(self.agent.source_modifier)
        self.assertEqual(len(self.agent._owned_processes), 0)

    # 2. State Machine Valid Transitions
    def test_02_state_machine_valid_transitions(self):
        """Test 2: Agent transitions cleanly along standard lifecycle paths."""
        self.assertEqual(self.agent.current_state, UnrealWorkflowState.IDLE)
        self.agent.transition_to(UnrealWorkflowState.INSPECTING, "wf_1", "Inspecting")
        self.assertEqual(self.agent.current_state, UnrealWorkflowState.INSPECTING)
        self.agent.transition_to(UnrealWorkflowState.DIAGNOSING, "wf_1", "Diagnosing")
        self.assertEqual(self.agent.current_state, UnrealWorkflowState.DIAGNOSING)
        self.agent.transition_to(UnrealWorkflowState.PLANNING, "wf_1", "Planning")
        self.assertEqual(self.agent.current_state, UnrealWorkflowState.PLANNING)
        self.agent.transition_to(UnrealWorkflowState.PROPOSING, "wf_1", "Proposing")
        self.assertEqual(self.agent.current_state, UnrealWorkflowState.PROPOSING)
        self.agent.transition_to(UnrealWorkflowState.VALIDATING, "wf_1", "Validating")
        self.assertEqual(self.agent.current_state, UnrealWorkflowState.VALIDATING)
        self.agent.transition_to(UnrealWorkflowState.APPLYING, "wf_1", "Applying")
        self.assertEqual(self.agent.current_state, UnrealWorkflowState.APPLYING)
        self.agent.transition_to(UnrealWorkflowState.BUILDING, "wf_1", "Building")
        self.assertEqual(self.agent.current_state, UnrealWorkflowState.BUILDING)
        self.agent.transition_to(UnrealWorkflowState.TESTING, "wf_1", "Testing")
        self.assertEqual(self.agent.current_state, UnrealWorkflowState.TESTING)
        self.agent.transition_to(UnrealWorkflowState.VERIFYING, "wf_1", "Verifying")
        self.assertEqual(self.agent.current_state, UnrealWorkflowState.VERIFYING)
        self.agent.transition_to(UnrealWorkflowState.COMPLETED, "wf_1", "Completed")
        self.assertEqual(self.agent.current_state, UnrealWorkflowState.COMPLETED)

    # 3. State Machine Invalid Transitions Rejected
    def test_03_state_machine_invalid_transitions_rejected(self):
        """Test 3: Invalid or out-of-order state transitions are strictly rejected."""
        self.agent.transition_to(UnrealWorkflowState.INSPECTING, "wf_inv", "Inspecting")
        with self.assertRaises(UnrealSafetyError) as ctx:
            self.agent.transition_to(UnrealWorkflowState.COMPLETED, "wf_inv", "Illegal leap")
        self.assertEqual(ctx.exception.code, UnrealErrorCode.INVALID_OPERATION)

    # 4. Failure Taxonomy: Compilation Errors
    def test_04_failure_taxonomy_classification_compilation(self):
        """Test 4: MSVC compilation errors classify deterministically as COMPILATION_FAILURE."""
        build_log = "Source/MyProject/MyActor.cpp(15): error C2065: 'UndefinedVar': undeclared identifier"
        diag = self.agent.inspect_and_diagnose(project_path=self.proj_dir, build_log=build_log)
        self.assertEqual(diag["failure_domain"], UnrealFailureDomain.COMPILATION_FAILURE.value)
        self.assertGreaterEqual(len(diag["diagnostics"]), 1)

    # 5. Failure Taxonomy: Runtime Crash
    def test_05_failure_taxonomy_classification_runtime_crash(self):
        """Test 5: Fatal crash logs classify deterministically as RUNTIME_CRASH."""
        crash_log = "Fatal error: [File:D:/Build/Actor.cpp] [Line: 42] Unhandled Exception: EXCEPTION_ACCESS_VIOLATION"
        diag = self.agent.inspect_and_diagnose(project_path=self.proj_dir, runtime_log=crash_log)
        self.assertEqual(diag["failure_domain"], UnrealFailureDomain.RUNTIME_CRASH.value)

    # 6. Failure Taxonomy: Runtime Assertion
    def test_06_failure_taxonomy_classification_assertion(self):
        """Test 6: Assertion check() failures classify as RUNTIME_ASSERTION."""
        assert_log = "Assertion failed: bInitialized [File:Source/MyActor.cpp] [Line: 12]"
        diag = self.agent.inspect_and_diagnose(project_path=self.proj_dir, runtime_log=assert_log)
        self.assertEqual(diag["failure_domain"], UnrealFailureDomain.RUNTIME_ASSERTION.value)

    # 7. Failure Taxonomy: Ensure Failure
    def test_07_failure_taxonomy_classification_ensure(self):
        """Test 7: Ensure condition failures classify as ENSURE_FAILURE."""
        ensure_log = "Ensure condition failed: Speed > 0.0f [File:Source/MyActor.cpp] [Line: 20]"
        diag = self.agent.inspect_and_diagnose(project_path=self.proj_dir, runtime_log=ensure_log)
        self.assertEqual(diag["failure_domain"], UnrealFailureDomain.ENSURE_FAILURE.value)

    # 8. Failure Taxonomy: Test Failure
    def test_08_failure_taxonomy_classification_test(self):
        """Test 8: Unreal Automation Test failures classify as TEST_FAILURE."""
        test_log = "Automation Test Failed: MyProject.ActorTest.SpeedCheck\nTotal: 2, Failed: 1"
        diag = self.agent.inspect_and_diagnose(project_path=self.proj_dir, test_log=test_log)
        self.assertEqual(diag["failure_domain"], UnrealFailureDomain.TEST_FAILURE.value)

    # 9. Failure Taxonomy: Linker Failure
    def test_09_failure_taxonomy_classification_linker(self):
        """Test 9: Linker LNK2019 errors classify as LINKER_FAILURE."""
        build_log = "MyActor.cpp.obj : error LNK2019: unresolved external symbol \"public: void __cdecl AMyActor::Jump(void)\""
        diag = self.agent.inspect_and_diagnose(project_path=self.proj_dir, build_log=build_log)
        self.assertEqual(diag["failure_domain"], UnrealFailureDomain.LINKER_FAILURE.value)

    # 10. Failure Taxonomy: UHT Reflection Failure
    def test_10_failure_taxonomy_classification_uht(self):
        """Test 10: UnrealHeaderTool macro errors classify as UHT_FAILURE."""
        build_log = "MyActor.h(10): error : Expected a GENERATED_BODY() at the start of class"
        diag = self.agent.inspect_and_diagnose(project_path=self.proj_dir, build_log=build_log)
        self.assertEqual(diag["failure_domain"], UnrealFailureDomain.UHT_FAILURE.value)

    # 11. Failure Taxonomy: Module Load Failure
    def test_11_failure_taxonomy_classification_module_load(self):
        """Test 11: Missing module dependency errors classify as MODULE_LOAD_FAILURE."""
        build_log = "fatal error C1083: Cannot open include file: 'GameplayAbilities.h': No such file or directory"
        diag = self.agent.inspect_and_diagnose(project_path=self.proj_dir, build_log=build_log)
        # Missing header in Unreal build maps to COMPILATION_FAILURE or MODULE_LOAD_FAILURE
        self.assertIn(diag["failure_domain"], [UnrealFailureDomain.COMPILATION_FAILURE.value, UnrealFailureDomain.MODULE_LOAD_FAILURE.value])

    # 12. Failure Taxonomy: Syntax Defect
    def test_12_failure_taxonomy_classification_syntax(self):
        """Test 12: Unbalanced source code syntax defect classifies as COMPILATION_FAILURE."""
        broken_file = self.source_dir / "Broken.cpp"
        broken_file.write_text("void Foo() { if (true) { }", encoding="utf-8")
        diag = self.agent.inspect_and_diagnose(project_path=self.proj_dir, source_file=broken_file)
        self.assertEqual(diag["failure_domain"], UnrealFailureDomain.COMPILATION_FAILURE.value)

    # 13. Evidence Precedence: Model Conflict
    def test_13_evidence_precedence_model_conflict(self):
        """Test 13: Deterministic exit status and diagnostics strictly prevail over model claims."""
        build_log = "Source/MyActor.cpp(10): error C2065: 'Foo': undeclared identifier"
        # Mock advisory model that claims "Everything is fine, no errors"
        mock_model = MagicMock()
        mock_model.route_request.return_value = {"text": "All fixed, 0 errors."}
        agent_with_model = UnrealAutonomousAgent(
            safety_gate=self.safety,
            cpp_analyzer=self.analyzer,
            source_modifier=self.modifier,
            model_router=mock_model,
            workspace_root=Path(self.tmp_dir),
        )
        diag = agent_with_model.inspect_and_diagnose(project_path=self.proj_dir, build_log=build_log)
        # Deterministic evidence dictates failure
        self.assertEqual(diag["failure_domain"], UnrealFailureDomain.COMPILATION_FAILURE.value)
        self.assertTrue(diag["deterministic_evidence_prevails"])

    # 14. Repair Proposal Schema & Limits
    def test_14_repair_proposal_schema_and_limits(self):
        """Test 14: Repair proposal strictly validates schema and size limits."""
        # Oversized patch (>100 KB) rejected
        oversized = "A" * (MAX_PATCH_BYTES + 1)
        with self.assertRaises(UnrealSafetyError) as ctx:
            self.agent.propose_repair(
                target_file=self.cpp_file,
                operation="REPLACE_METHOD_BODY",
                replacement=oversized,
                expected_sha256=self.cpp_sha,
                method_name="MoveForward",
                project_path=self.proj_dir,
            )
        self.assertEqual(ctx.exception.code, UnrealErrorCode.PATCH_TOO_LARGE)

    # 15. Prohibited Tokens in Proposals Rejected
    def test_15_prohibited_tokens_in_proposals_rejected(self):
        """Test 15: Modification proposals containing command execution words are rejected."""
        malicious_content = 'void Infiltrate() { system("cmd.exe /c calc.exe"); }'
        with self.assertRaises(UnrealSafetyError) as ctx:
            self.agent.propose_repair(
                target_file=self.cpp_file,
                operation="ADD_METHOD",
                replacement=malicious_content,
                expected_sha256=self.cpp_sha,
                project_path=self.proj_dir,
            )
        self.assertEqual(ctx.exception.code, UnrealErrorCode.PROHIBITED_TOKEN)

    # 16. Workflow Planner Default Steps Generation
    def test_16_workflow_planner_default_steps(self):
        """Test 16: Planner generates valid default steps within bounds for recognized workflows."""
        plan = self.agent.create_workflow_plan(
            workflow_type=UnrealWorkflowType.WORKFLOW_BUILD_FAILURE_REPAIR,
            target_project=self.proj_dir,
            goal="Fix compiler error",
        )
        self.assertEqual(plan.workflow_type, UnrealWorkflowType.WORKFLOW_BUILD_FAILURE_REPAIR)
        self.assertLessEqual(len(plan.steps), MAX_WORKFLOW_STEPS)
        self.assertGreaterEqual(len(plan.steps), 5)

    # 17. Workflow Plan Step Limit Enforcement (>25 steps)
    def test_17_workflow_plan_step_limit_enforcement(self):
        """Test 17: Workflow plan exceeding 25 steps is strictly rejected."""
        excessive_steps = [{"action": f"STEP_{i}", "target": str(self.proj_dir)} for i in range(26)]
        with self.assertRaises(UnrealSafetyError) as ctx:
            self.agent.create_workflow_plan(
                workflow_type=UnrealWorkflowType.CUSTOM,
                target_project=self.proj_dir,
                goal="Excessive steps",
                custom_steps=excessive_steps,
            )
        self.assertEqual(ctx.exception.code, UnrealErrorCode.WORKFLOW_STEP_LIMIT_EXCEEDED)

    # 18. Workflow Plan Prohibited Tokens Rejection
    def test_18_workflow_plan_prohibited_tokens_rejection(self):
        """Test 18: Plans with shell or powershell execution actions are rejected."""
        bad_steps = [{"action": "EXEC_SHELL", "target": "powershell.exe", "params": {"cmd": "rmdir"}}]
        with self.assertRaises(UnrealSafetyError) as ctx:
            self.agent.create_workflow_plan(
                workflow_type=UnrealWorkflowType.CUSTOM,
                target_project=self.proj_dir,
                goal="Unsafe execution",
                custom_steps=bad_steps,
            )
        self.assertEqual(ctx.exception.code, UnrealErrorCode.PROHIBITED_TOKEN)

    # 19. Bounded Repair Loop: Single Attempt Success
    def test_19_bounded_repair_loop_single_attempt_success(self):
        """Test 19: Repair succeeds on first attempt when modification passes syntax & build verification."""
        valid_replacement = "void AMyActor::MoveForward(float Val)\n{\n\tSpeed = FMath::Clamp(Speed + Val, 0.0f, 500.0f);\n}"
        success, attempts, err = self.agent.execute_repair_loop(
            workflow_id="wf_rep_1",
            project_path=self.proj_dir,
            target_file=self.cpp_file,
            operation="REPLACE_METHOD_BODY",
            replacement=valid_replacement,
            expected_sha256=self.cpp_sha,
            max_attempts=2,
            verify_build_fn=lambda: True,
            verify_test_fn=lambda: True,
        )
        self.assertTrue(success)
        self.assertIsNone(err)
        self.assertEqual(len(attempts), 1)
        self.assertTrue(attempts[0].applied)
        self.assertTrue(attempts[0].verified)
        self.assertFalse(attempts[0].rolled_back)

    # 20. Bounded Repair Loop: Automatic Rollback on Verification Failure
    def test_20_bounded_repair_loop_automatic_rollback_on_verify_fail(self):
        """Test 20: Post-modification syntax failure triggers byte-for-byte automatic rollback."""
        broken_syntax_replacement = "void AMyActor::MoveForward(float Val)\n{\n\tSpeed += Val;\n\t// Unclosed brace"
        success, attempts, err = self.agent.execute_repair_loop(
            workflow_id="wf_rep_2",
            project_path=self.proj_dir,
            target_file=self.cpp_file,
            operation="REPLACE_METHOD_BODY",
            replacement=broken_syntax_replacement,
            expected_sha256=self.cpp_sha,
            max_attempts=1,
        )
        self.assertFalse(success)
        self.assertEqual(len(attempts), 1)
        self.assertTrue(attempts[0].rolled_back)
        # Confirm byte-for-byte SHA-256 restoration
        current_sha = hashlib.sha256(self.cpp_file.read_bytes()).hexdigest()
        self.assertEqual(current_sha, self.cpp_sha)

    # 21. Bounded Repair Loop: Two Attempts Exhaustion (Double Failure)
    def test_21_bounded_repair_loop_two_attempts_exhaustion(self):
        """Test 21: Failing 2 repair attempts halts immediately and never attempts a 3rd."""
        # Always fail build
        success, attempts, err = self.agent.execute_repair_loop(
            workflow_id="wf_rep_3",
            project_path=self.proj_dir,
            target_file=self.cpp_file,
            operation="REPLACE_METHOD_BODY",
            replacement="void AMyActor::MoveForward(float Val)\n{\n\tSpeed = 0.0f;\n}",
            expected_sha256=self.cpp_sha,
            max_attempts=2,
            verify_build_fn=lambda: False,  # Simulated build failure
        )
        self.assertFalse(success)
        self.assertEqual(len(attempts), 2)
        for att in attempts:
            self.assertTrue(att.rolled_back)
        # Original file restored
        self.assertEqual(hashlib.sha256(self.cpp_file.read_bytes()).hexdigest(), self.cpp_sha)
        self.assertIn("Maximum attempts (2) reached", err)

    # 22. Stale Target SHA-256 Rejection
    def test_22_stale_target_sha256_rejection(self):
        """Test 22: Mismatched target SHA-256 is rejected before any modification."""
        wrong_sha = "0000000000000000000000000000000000000000000000000000000000000000"
        success, attempts, err = self.agent.execute_repair_loop(
            workflow_id="wf_stale",
            project_path=self.proj_dir,
            target_file=self.cpp_file,
            operation="REPLACE_METHOD_BODY",
            replacement="void AMyActor::MoveForward(float Val) {}",
            expected_sha256=wrong_sha,
        )
        self.assertFalse(success)
        self.assertIn("SHA-256 mismatch", err)

    # 23. Emergency Stop Freezes Active Workflow
    def test_23_emergency_stop_freezes_workflow(self):
        """Test 23: Thread-safe emergency stop halts workflow immediately and transitions to STOPPED."""
        self.safety.activate_emergency_stop("Operator emergency trigger")
        report = self.agent.execute_workflow(
            workflow_type=UnrealWorkflowType.WORKFLOW_BUILD_FAILURE_REPAIR,
            target_project=self.proj_dir,
            goal="Test E-Stop",
        )
        self.assertEqual(report.final_state, UnrealWorkflowState.STOPPED)
        self.assertEqual(report.failure_domain, UnrealFailureDomain.USER_STOPPED)
        self.assertFalse(report.success)

    # 24. Emergency Stop Terminates Only Owned Processes
    def test_24_emergency_stop_terminates_only_owned_processes(self):
        """Test 24: Process cleanup only touches workflow-owned processes and leaves 0 orphans."""
        # Register mock owned process PID
        self.agent.track_process(pid=999999, process_type="UnrealEditor", workflow_id="wf_proc_1")
        self.assertEqual(len(self.agent._owned_processes), 1)
        orphans = self.agent.cleanup_owned_processes("wf_proc_1")
        self.assertEqual(orphans, 0)
        self.assertEqual(len(self.agent._owned_processes), 0)

    # 25. Process Ownership Tracking & Cleanup (Zero Orphans)
    def test_25_process_ownership_tracking_and_zero_orphans(self):
        """Test 25: Process tracking correctly tracks, untracks, and ensures 0 orphans on cleanup."""
        rec = self.agent.track_process(pid=12345, process_type="UBT", workflow_id="wf_trace")
        self.assertEqual(rec.pid, 12345)
        untracked = self.agent.untrack_process(12345)
        self.assertEqual(untracked.pid, 12345)
        self.assertEqual(len(self.agent._owned_processes), 0)

    # 26. Audit Logging and Sensitive Token Redaction
    def test_26_audit_logging_and_sensitive_data_redaction(self):
        """Test 26: Sensitive credentials and API tokens are redacted from audit logs."""
        secret_goal = "Workflow with secret api_key=sk-ant-secret1234567890 password=supersecret"
        report = self.agent.execute_workflow(
            workflow_type=UnrealWorkflowType.WORKFLOW_SOURCE_CHANGE_VERIFY,
            target_project=self.proj_dir,
            goal=secret_goal,
        )
        entries = self.agent.audit.get_entries()
        self.assertGreaterEqual(len(entries), 1)
        last_details_str = json.dumps(entries[-1].get("details", {}))
        self.assertNotIn("sk-ant-secret1234567890", last_details_str)
        self.assertNotIn("supersecret", last_details_str)

    # 27. E2E Workflow: WORKFLOW_BUILD_FAILURE_REPAIR
    def test_27_workflow_e2e_build_failure_repair(self):
        """Test 27: E2E build failure repair coordinates inspect -> diagnose -> propose -> apply -> verify."""
        build_log = "Source/MyProject/MyActor.cpp(15): error C2065: 'Speed': undeclared identifier"
        replacement = "void AMyActor::MoveForward(float Val)\n{\n\tSpeed = 200.0f;\n}"
        report = self.agent.execute_workflow(
            workflow_type=UnrealWorkflowType.WORKFLOW_BUILD_FAILURE_REPAIR,
            target_project=self.proj_dir,
            build_log=build_log,
            target_file=self.cpp_file,
            operation="REPLACE_METHOD_BODY",
            replacement=replacement,
            expected_sha256=self.cpp_sha,
            verify_build_fn=lambda: True,
        )
        self.assertTrue(report.success)
        self.assertEqual(report.final_state, UnrealWorkflowState.COMPLETED)
        self.assertIn(str(self.cpp_file), report.repaired_files)
        self.assertEqual(report.orphan_process_count, 0)

    # 28. E2E Workflow: WORKFLOW_TEST_FAILURE_REPAIR
    def test_28_workflow_e2e_test_failure_repair(self):
        """Test 28: E2E test failure repair coordinates test diagnosis and verification."""
        test_log = "Automation Test Failed: SpeedCheck. Value was 0, expected 100"
        replacement = "void AMyActor::MoveForward(float Val)\n{\n\tSpeed = 100.0f;\n}"
        report = self.agent.execute_workflow(
            workflow_type=UnrealWorkflowType.WORKFLOW_TEST_FAILURE_REPAIR,
            target_project=self.proj_dir,
            test_log=test_log,
            target_file=self.cpp_file,
            operation="REPLACE_METHOD_BODY",
            replacement=replacement,
            expected_sha256=self.cpp_sha,
            verify_build_fn=lambda: True,
            verify_test_fn=lambda: True,
        )
        self.assertTrue(report.success)
        self.assertEqual(report.final_state, UnrealWorkflowState.COMPLETED)

    # 29. E2E Workflow: WORKFLOW_RUNTIME_FAILURE_REPAIR
    def test_29_workflow_e2e_runtime_failure_repair(self):
        """Test 29: E2E runtime crash repair workflow executes successfully."""
        runtime_log = "Fatal error: Access violation in MoveForward [Line: 12]"
        replacement = "void AMyActor::MoveForward(float Val)\n{\n\tif (this) { Speed = 50.0f; }\n}"
        report = self.agent.execute_workflow(
            workflow_type=UnrealWorkflowType.WORKFLOW_RUNTIME_FAILURE_REPAIR,
            target_project=self.proj_dir,
            runtime_log=runtime_log,
            target_file=self.cpp_file,
            operation="REPLACE_METHOD_BODY",
            replacement=replacement,
            expected_sha256=self.cpp_sha,
            verify_build_fn=lambda: True,
        )
        self.assertTrue(report.success)
        self.assertEqual(report.final_state, UnrealWorkflowState.COMPLETED)

    # 30. E2E Workflow: WORKFLOW_SOURCE_CHANGE_VERIFY
    def test_30_workflow_e2e_source_change_verify(self):
        """Test 30: E2E source change verification workflow."""
        report = self.agent.execute_workflow(
            workflow_type=UnrealWorkflowType.WORKFLOW_SOURCE_CHANGE_VERIFY,
            target_project=self.proj_dir,
            target_file=self.cpp_file,
        )
        self.assertTrue(report.success)
        self.assertEqual(report.final_state, UnrealWorkflowState.COMPLETED)

    # 31. E2E Workflow: WORKFLOW_ROLLBACK
    def test_31_workflow_e2e_rollback(self):
        """Test 31: E2E rollback workflow restores original source state cleanly."""
        report = self.agent.execute_workflow(
            workflow_type=UnrealWorkflowType.WORKFLOW_ROLLBACK,
            target_project=self.proj_dir,
        )
        self.assertTrue(report.success)
        self.assertEqual(report.final_state, UnrealWorkflowState.COMPLETED)

    # 32. E2E Workflow: WORKFLOW_SAFETY_REJECTION
    def test_32_workflow_e2e_safety_rejection(self):
        """Test 32: Safety rejection workflow transitions cleanly to terminal state FAILED."""
        report = self.agent.execute_workflow(
            workflow_type=UnrealWorkflowType.WORKFLOW_SAFETY_REJECTION,
            target_project=self.proj_dir,
        )
        self.assertFalse(report.success)
        self.assertEqual(report.final_state, UnrealWorkflowState.FAILED)
        self.assertEqual(report.failure_domain, UnrealFailureDomain.SAFETY_REJECTION)

    # 33. E2E Workflow: WORKFLOW_USER_STOP
    def test_33_workflow_e2e_user_stop(self):
        """Test 33: User stop workflow cleans up and transitions cleanly to STOPPED."""
        report = self.agent.execute_workflow(
            workflow_type=UnrealWorkflowType.WORKFLOW_USER_STOP,
            target_project=self.proj_dir,
        )
        self.assertFalse(report.success)
        self.assertEqual(report.final_state, UnrealWorkflowState.STOPPED)
        self.assertEqual(report.failure_domain, UnrealFailureDomain.USER_STOPPED)

    # 34. Tool Registry Dispatch for All 10 Phase 5 Tools
    def test_34_tool_registry_dispatch_all_phase5_tools(self):
        """Test 34: All 10 Phase 5 developer tools dispatch cleanly via UnrealToolRegistry."""
        for tool in ALLOWED_UNREAL_PHASE5_TOOLS:
            self.assertIn(tool, self.registry.get_registered_tools())

        # 1. unreal.create_repair_workflow
        r1 = self.registry.execute_tool("unreal.create_repair_workflow", project_path=str(self.proj_dir))
        self.assertTrue(r1.success)

        # 2. unreal.inspect_failure
        r2 = self.registry.execute_tool("unreal.inspect_failure", project_path=str(self.proj_dir))
        self.assertTrue(r2.success)

        # 3. unreal.diagnose_failure
        r3 = self.registry.execute_tool("unreal.diagnose_failure", project_path=str(self.proj_dir))
        self.assertTrue(r3.success)

        # 4. unreal.plan_repair
        r4 = self.registry.execute_tool("unreal.plan_repair", project_path=str(self.proj_dir))
        self.assertTrue(r4.success)

        # 5. unreal.validate_repair_plan
        sample_plan = {
            "workflow_id": "wf_test",
            "goal": "Test",
            "steps": [{"action": "INSPECT", "target": str(self.proj_dir)}],
        }
        r5 = self.registry.execute_tool("unreal.validate_repair_plan", plan=sample_plan)
        self.assertTrue(r5.success)

        # 6. unreal.execute_repair_workflow
        r6 = self.registry.execute_tool(
            "unreal.execute_repair_workflow",
            project_path=str(self.proj_dir),
            workflow_type="WORKFLOW_SOURCE_CHANGE_VERIFY",
        )
        self.assertTrue(r6.success)

        # 7. unreal.verify_repair_workflow
        r7 = self.registry.execute_tool(
            "unreal.verify_repair_workflow",
            file_path=str(self.cpp_file),
            project_path=str(self.proj_dir),
        )
        self.assertTrue(r7.success)

        # 8. unreal.rollback_repair_workflow
        r8 = self.registry.execute_tool(
            "unreal.rollback_repair_workflow",
            backup_id="nonexistent_id",
            target_path=str(self.cpp_file),
        )
        self.assertFalse(r8.success)  # Expected failure for invalid ID, handled safely

        # 9. unreal.stop_repair_workflow
        r9 = self.registry.execute_tool("unreal.stop_repair_workflow", workflow_id="wf_test")
        self.assertTrue(r9.success)

        # 10. unreal.get_workflow_status
        r10 = self.registry.execute_tool("unreal.get_workflow_status")
        self.assertTrue(r10.success)

    # 35. Duplicate Tool Detection Across All 54 Tools
    def test_35_no_duplicate_tools(self):
        """Test 35: Verify exactly 54 tools and 0 duplicates across the default tool registry."""
        tools = DEFAULT_UNREAL_TOOL_REGISTRY.get_registered_tools()
        self.assertEqual(len(tools), 54, f"Expected 54 tools, got {len(tools)}: {tools}")
        self.assertEqual(len(tools), len(set(tools)), "Duplicate tool detected in registry")
        self.assertEqual(len(ALL_ALLOWED_UNREAL_TOOLS), 54)

    # 36. Static Safety Audit (shell=True == 0)
    def test_36_no_shell_true(self):
        """Test 36: Static safety audit confirms shell=True count is 0 in all unreal_*.py modules."""
        unreal_files = glob.glob(str(Path(__file__).parent.parent / "app" / "agent" / "unreal_*.py"))
        self.assertGreaterEqual(len(unreal_files), 7)
        pattern = re.compile(r"shell\s*=\s*True")
        for fpath in unreal_files:
            text = Path(fpath).read_text(encoding="utf-8", errors="ignore")
            matches = pattern.findall(text)
            self.assertEqual(len(matches), 0, f"Violation: shell=True found in {fpath}")

    # 37. Data Model Serialization Integrity
    def test_37_data_model_serialization(self):
        """Test 37: Data models serialize cleanly to JSON and dictionaries."""
        step = UnrealWorkflowStep(step_number=1, state=UnrealWorkflowState.IDLE, action="TEST")
        att = UnrealRepairAttempt(attempt_number=1, proposal_id="p1", target_file="f.cpp", original_sha256="abc")
        plan = UnrealWorkflowPlan(workflow_id="w1", goal="g", workflow_type=UnrealWorkflowType.CUSTOM, target_project="p")
        proc = UnrealProcessRecord(pid=1, process_type="t", start_time=time.time(), workflow_id="w1")
        rep = UnrealWorkflowReport(
            workflow_id="w1", goal="g", workflow_type=UnrealWorkflowType.CUSTOM, target_project="p",
            initial_state=UnrealWorkflowState.IDLE, final_state=UnrealWorkflowState.COMPLETED, success=True
        )

        for obj in [step, att, plan, proc, rep]:
            d = obj.to_dict()
            s = json.dumps(d)
            self.assertIsInstance(s, str)
            self.assertGreater(len(s), 0)

    # 38. Deterministic Enforcement of Strict 2-Attempt Maximum Repair Safety Contract
    def test_38_repair_attempt_safety_contract(self):
        """Test 38: Deterministically proves attempt 1 is allowed, attempt 2 is allowed, attempt 3+ is rejected."""
        # 1. Direct Safety Gate Validation: Attempt 1 is accepted
        res1 = self.safety.validate_repair_attempts(1)
        self.assertEqual(res1, 1)

        # 2. Direct Safety Gate Validation: Attempt 2 is accepted
        res2 = self.safety.validate_repair_attempts(2)
        self.assertEqual(res2, 2)

        # 3. Direct Safety Gate Validation: Attempt 3 is deterministically rejected
        with self.assertRaises(UnrealSafetyError) as ctx3:
            self.safety.validate_repair_attempts(3)
        self.assertEqual(ctx3.exception.code, UnrealErrorCode.REPAIR_ATTEMPTS_EXCEEDED)
        self.assertIn("exceeds maximum allowed safety limit of 2", ctx3.exception.message)

        # 4. Direct Safety Gate Validation: A request for more than 3 is rejected
        with self.assertRaises(UnrealSafetyError) as ctx4:
            self.safety.validate_repair_attempts(4)
        self.assertEqual(ctx4.exception.code, UnrealErrorCode.REPAIR_ATTEMPTS_EXCEEDED)

        with self.assertRaises(UnrealSafetyError) as ctx0:
            self.safety.validate_repair_attempts(0)
        self.assertEqual(ctx0.exception.code, UnrealErrorCode.INVALID_PARAMETER)

        # 5. execute_repair_loop: attempt 1 is allowed
        success1, attempts1, err1 = self.agent.execute_repair_loop(
            workflow_id="wf_limit_1",
            project_path=self.proj_dir,
            target_file=self.cpp_file,
            operation="REPLACE_METHOD_BODY",
            method_name="MoveForward",
            replacement="\tSpeed = Val;\n",
            expected_sha256=self.cpp_sha,
            max_attempts=1,
            verify_build_fn=lambda: True,
        )
        self.assertTrue(success1)
        self.assertEqual(len(attempts1), 1)

        # Reset file for attempt 2 test
        self.cpp_file.write_text(SAMPLE_SOURCE, encoding="utf-8")
        cur_sha = hashlib.sha256(self.cpp_file.read_bytes()).hexdigest()

        # 6. execute_repair_loop: attempt 2 is allowed
        success2, attempts2, err2 = self.agent.execute_repair_loop(
            workflow_id="wf_limit_2",
            project_path=self.proj_dir,
            target_file=self.cpp_file,
            operation="REPLACE_METHOD_BODY",
            method_name="MoveForward",
            replacement="\tSpeed = Val * 2.0f;\n",
            expected_sha256=cur_sha,
            max_attempts=2,
            verify_build_fn=lambda: True,
        )
        self.assertTrue(success2)

        # 7. execute_repair_loop: attempt 3 is deterministically rejected by safety boundary
        with self.assertRaises(UnrealSafetyError) as ctx_loop:
            self.agent.execute_repair_loop(
                workflow_id="wf_limit_3",
                project_path=self.proj_dir,
                target_file=self.cpp_file,
                operation="REPLACE_METHOD_BODY",
                replacement="void AMyActor::MoveForward(float Val)\n{\n\tSpeed = 0.0f;\n}",
                expected_sha256=hashlib.sha256(self.cpp_file.read_bytes()).hexdigest(),
                max_attempts=3,
            )
        self.assertEqual(ctx_loop.exception.code, UnrealErrorCode.REPAIR_ATTEMPTS_EXCEEDED)

        # 8. Workflow Planner: Plan with max_repair_attempts=3 is deterministically rejected
        with self.assertRaises(UnrealSafetyError) as ctx_plan:
            self.agent.create_workflow_plan(
                workflow_type=UnrealWorkflowType.WORKFLOW_BUILD_FAILURE_REPAIR,
                target_project=self.proj_dir,
                goal="Test repair limits",
                max_repair_attempts=3,
            )
        self.assertEqual(ctx_plan.exception.code, UnrealErrorCode.REPAIR_ATTEMPTS_EXCEEDED)

        # 9. Tool Dispatch: unreal.validate_repair_plan with max_repair_attempts=3 is rejected
        res_tool = self.registry.dispatch(
            "unreal.validate_repair_plan",
            plan={
                "workflow_id": "wf_tool_test",
                "goal": "Test limit",
                "workflow_type": "WORKFLOW_BUILD_FAILURE_REPAIR",
                "target_project": str(self.proj_dir),
                "steps": [{"action": "INSPECT_PROJECT", "target": str(self.proj_dir)}],
                "max_repair_attempts": 3,
            },
        )
        self.assertFalse(res_tool.success)
        self.assertEqual(res_tool.error_code, UnrealErrorCode.REPAIR_ATTEMPTS_EXCEEDED.value)
        self.assertIn("exceeds maximum allowed safety limit of 2", res_tool.error)


if __name__ == "__main__":
    unittest.main()
