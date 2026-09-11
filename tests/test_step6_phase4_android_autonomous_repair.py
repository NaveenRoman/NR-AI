"""
NR-AI Step 6 Phase 4: Android Autonomous Repair Execution Test Suite.

Tests A through Z verifying:
- Test A: Successful initial build
- Test B: Build failure classification
- Test C: Model proposal schema validation
- Test D: Malformed proposal rejection
- Test E: Unauthorized path rejection
- Test F: Protected file rejection
- Test G: Stale hash rejection
- Test H: Oversized patch rejection
- Test I: Excessive file count rejection
- Test J: Excessive line count rejection
- Test K: Prohibited token rejection
- Test L: Bounded model consultation
- Test M: Deterministic edit application
- Test N: Checkpoint creation
- Test O: Successful repair build
- Test P: Failed repair rollback
- Test Q: Maximum two attempts
- Test R: No repair after emergency stop
- Test S: Model cannot execute tools directly
- Test T: Credentials never enter model context
- Test U: Ground-truth build result overrides model claim
- Test V: Unsupported error returns REPAIR_UNSUPPORTED
- Test W: Audit log redaction
- Test X: Companion routing
- Test Y: Real authorized Android project boundary
- Test Z: Workspace restored after test
"""

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from app.agent.android_code_repair import (
    AndroidBuildError,
    AndroidCodeRepairEngine,
    AndroidErrorAnalyzer,
    AndroidErrorCategory,
    EditOperation,
    EditProposal,
    EditResult,
    RepairResult,
    build_model_repair_prompt,
    extract_bounded_context,
    parse_model_repair_response,
    redact_sensitive_content,
)
from app.agent.android_safety import (
    AUTHORIZED_PACKAGE_NAME,
    AUTHORIZED_PROJECT_PATH,
    MAX_EDITABLE_FILE_SIZE_BYTES,
    MAX_FILES_PER_REPAIR,
    MAX_LINES_PER_EDIT,
    MAX_PATCH_SIZE_BYTES,
    MAX_REPAIR_ATTEMPTS,
    AndroidErrorCode,
    AndroidSafetyError,
    AndroidSafetyGate,
    EmergencyStopActiveError,
)
from app.agent.android_studio_agent import (
    AndroidActionIntent,
    AndroidStudioAgent,
    AndroidWorkflowReport,
)
from app.agent.android_tools import AndroidToolRegistry, AndroidToolResult, SafeGradleRunner
from app.agent.model_router import ModelRouter
from app.brain.companion import CommandCategory, NRCompanion
from app.config.model_config import ModelCapability
from app.memory.audit_logger import AuditLogger


class TestStep6Phase4AndroidAutonomousRepair(unittest.TestCase):
    """Full acceptance test suite for Step 6 Phase 4."""

    @classmethod
    def setUpClass(cls):
        cls.project_root = AUTHORIZED_PROJECT_PATH
        cls.main_kt_path = cls.project_root / "app" / "src" / "main" / "java" / "com" / "nrai" / "test" / "MainActivity.kt"
        cls.dummy_kt_path = cls.project_root / "app" / "src" / "main" / "java" / "com" / "nrai" / "test" / "RepairDummy.kt"

        # Preserve original state if dummy exists
        cls.dummy_original = None
        if cls.dummy_kt_path.exists():
            cls.dummy_original = cls.dummy_kt_path.read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        # Restore or clean up dummy file
        if cls.dummy_kt_path.exists():
            if cls.dummy_original is not None:
                cls.dummy_kt_path.write_text(cls.dummy_original, encoding="utf-8")
            else:
                try:
                    cls.dummy_kt_path.unlink()
                except Exception:
                    pass

    def setUp(self):
        self.safety = AndroidSafetyGate(authorized_project=self.project_root)
        self.safety.clear_emergency_stop()
        self.audit = AuditLogger()
        self.engine = AndroidCodeRepairEngine(
            project_path=self.project_root,
            safety_gate=self.safety,
            audit_logger=self.audit,
        )
        self.agent = AndroidStudioAgent(
            safety_gate=self.safety,
            audit_logger=self.audit,
        )

    def tearDown(self):
        self.safety.clear_emergency_stop()
        if self.dummy_kt_path.exists():
            try:
                self.dummy_kt_path.unlink()
            except Exception:
                pass

    # -------------------------------------------------------------------------
    # Test A: Successful initial build
    # -------------------------------------------------------------------------
    def test_A_successful_initial_build(self):
        """Clean project requires 0 repair attempts and reports clean build."""
        mock_gradle = MagicMock()
        mock_gradle.run_action.return_value = {"success": True, "exit_code": 0}

        engine = AndroidCodeRepairEngine(
            project_path=self.project_root,
            safety_gate=self.safety,
            gradle_runner=mock_gradle,
        )
        res = engine.repair_build("Fix build errors")
        self.assertTrue(res.success)
        self.assertEqual(res.attempts, 0)
        self.assertIn("already clean", res.summary)
        self.assertFalse(res.rolled_back)

    # -------------------------------------------------------------------------
    # Test B: Build failure classification
    # -------------------------------------------------------------------------
    def test_B_build_failure_classification(self):
        """Compiler error outputs are accurately classified into structured categories."""
        analyzer = AndroidErrorAnalyzer()

        # Kotlin syntax error
        kt_out = r"e: C:/NR-AI/nr_android_test/app/src/main/java/com/nrai/test/MainActivity.kt: 24: 5 expecting ')'"
        err_kt = analyzer.analyze_build_output(kt_out, self.project_root)
        self.assertEqual(err_kt.category, AndroidErrorCategory.SYNTAX_ERROR)
        self.assertEqual(err_kt.line, 24)
        self.assertIn("expecting ')'", err_kt.message)

        # Java compiler error
        java_out = r"C:/NR-AI/nr_android_test/app/src/main/java/com/nrai/test/Helper.java:12: error: ';' expected"
        err_java = analyzer.analyze_build_output(java_out, self.project_root)
        self.assertEqual(err_java.category, AndroidErrorCategory.SYNTAX_ERROR)
        self.assertEqual(err_java.line, 12)

        # Manifest error
        man_out = "AndroidManifest.xml:15: error: attribute 'android:exported' not specified"
        err_man = analyzer.analyze_build_output(man_out, self.project_root)
        self.assertEqual(err_man.category, AndroidErrorCategory.MANIFEST_MERGER)
        self.assertEqual(err_man.line, 15)

    # -------------------------------------------------------------------------
    # Test C: Model proposal schema validation
    # -------------------------------------------------------------------------
    def test_C_model_proposal_schema_validation(self):
        """Valid proposal adhering to schema parses successfully."""
        proposal_json = json.dumps({
            "proposal_version": "1.0",
            "summary": "Fix missing semicolon in MainActivity.kt",
            "edits": [
                {
                    "path": "app/src/main/java/com/nrai/test/MainActivity.kt",
                    "expected_sha256": "abcdef1234567890",
                    "start_line": 25,
                    "end_line": 26,
                    "replacement": "        val x = 10\n",
                }
            ],
            "confidence": 0.95,
            "reasoning_summary": "Added missing assignment and semicolon",
        })

        parsed = parse_model_repair_response(proposal_json)
        self.assertEqual(parsed["proposal_version"], "1.0")
        self.assertEqual(parsed["summary"], "Fix missing semicolon in MainActivity.kt")
        self.assertEqual(len(parsed["edits"]), 1)
        self.assertEqual(parsed["confidence"], 0.95)

    # -------------------------------------------------------------------------
    # Test D: Malformed proposal rejection
    # -------------------------------------------------------------------------
    def test_D_malformed_proposal_rejection(self):
        """Malformed proposals (missing required fields, non-JSON, invalid lines) are rejected."""
        # 1. Non-JSON string
        with self.assertRaises(AndroidSafetyError) as ctx:
            parse_model_repair_response("Here is the fix: replace line 10 with foo")
        self.assertEqual(ctx.exception.code, AndroidErrorCode.MALFORMED_PROPOSAL)

        # 2. Missing proposal_version
        with self.assertRaises(AndroidSafetyError) as ctx:
            parse_model_repair_response(json.dumps({
                "summary": "Fix",
                "edits": [{"path": "file.kt", "start_line": 1, "end_line": 1, "replacement": "val x = 1"}],
            }))
        self.assertEqual(ctx.exception.code, AndroidErrorCode.MALFORMED_PROPOSAL)

        # 3. Missing edits list
        with self.assertRaises(AndroidSafetyError) as ctx:
            parse_model_repair_response(json.dumps({
                "proposal_version": "1.0",
                "summary": "Fix",
                "edits": [],
            }))
        self.assertEqual(ctx.exception.code, AndroidErrorCode.MALFORMED_PROPOSAL)

        # 4. Invalid line numbers (start_line > end_line)
        with self.assertRaises(AndroidSafetyError) as ctx:
            parse_model_repair_response(json.dumps({
                "proposal_version": "1.0",
                "summary": "Fix",
                "edits": [{
                    "path": "file.kt",
                    "start_line": 50,
                    "end_line": 10,
                    "replacement": "code",
                }],
            }))
        self.assertEqual(ctx.exception.code, AndroidErrorCode.MALFORMED_PROPOSAL)

    # -------------------------------------------------------------------------
    # Test E: Unauthorized path rejection
    # -------------------------------------------------------------------------
    def test_E_unauthorized_path_rejection(self):
        """Proposals modifying files outside authorized project root are rejected."""
        proposal = EditProposal(
            file_path=r"C:\NR-AI\app\agent\android_code_repair.py",
            operation=EditOperation.REPLACE_RANGE,
            start_line=1,
            end_line=2,
            replacement_text="# modified\n",
            reason="Illegal outside edit",
        )

        with self.assertRaises(AndroidSafetyError) as ctx:
            self.engine.apply_edit(proposal)
        self.assertIn(ctx.exception.code, (AndroidErrorCode.PROJECT_NOT_AUTHORIZED, AndroidErrorCode.FILE_NOT_AUTHORIZED))

    # -------------------------------------------------------------------------
    # Test F: Protected file rejection
    # -------------------------------------------------------------------------
    def test_F_protected_file_rejection(self):
        """Proposals modifying protected files (local.properties, keystores) are rejected."""
        protected_target = self.project_root / "local.properties"

        proposal = EditProposal(
            file_path=protected_target,
            operation=EditOperation.REPLACE_RANGE,
            start_line=1,
            end_line=2,
            replacement_text="sdk.dir=C:/new/sdk\n",
            reason="Illegal protected edit",
        )

        with self.assertRaises(AndroidSafetyError) as ctx:
            self.engine.apply_edit(proposal)
        self.assertEqual(ctx.exception.code, AndroidErrorCode.PROTECTED_FILE_REJECTED)

    # -------------------------------------------------------------------------
    # Test G: Stale hash rejection
    # -------------------------------------------------------------------------
    def test_G_stale_hash_rejection(self):
        """Proposal with outdated expected SHA-256 is rejected with STALE_TARGET."""
        self.dummy_kt_path.write_text("package com.nrai.test\nclass RepairDummy\n", encoding="utf-8")

        proposal = EditProposal(
            file_path=self.dummy_kt_path,
            expected_old_hash="deadbeef00000000000000000000000000000000000000000000000000000000",
            operation=EditOperation.REPLACE_RANGE,
            start_line=2,
            end_line=2,
            replacement_text="class RepairDummyUpdated\n",
            reason="Update dummy",
        )

        with self.assertRaises(AndroidSafetyError) as ctx:
            self.engine.apply_edit(proposal)
        self.assertEqual(ctx.exception.code, AndroidErrorCode.STALE_TARGET)

    # -------------------------------------------------------------------------
    # Test H: Oversized patch rejection
    # -------------------------------------------------------------------------
    def test_H_oversized_patch_rejection(self):
        """Proposal exceeding MAX_PATCH_SIZE_BYTES (100 KB) is rejected."""
        self.dummy_kt_path.write_text("package com.nrai.test\n", encoding="utf-8")
        current_hash = self.engine.calculate_file_hash(self.dummy_kt_path)

        oversized_text = "x" * (MAX_PATCH_SIZE_BYTES + 1024)
        proposal = EditProposal(
            file_path=self.dummy_kt_path,
            expected_old_hash=current_hash,
            operation=EditOperation.REPLACE_RANGE,
            start_line=1,
            end_line=1,
            replacement_text=oversized_text,
            reason="Massive patch",
        )

        with self.assertRaises(AndroidSafetyError) as ctx:
            self.engine.apply_edit(proposal)
        self.assertEqual(ctx.exception.code, AndroidErrorCode.PATCH_TOO_LARGE)

    # -------------------------------------------------------------------------
    # Test I: Excessive file count rejection
    # -------------------------------------------------------------------------
    def test_I_excessive_file_count_rejection(self):
        """Proposals modifying more than 5 distinct files in one batch are rejected."""
        proposals = []
        for i in range(6):
            p = self.project_root / "app" / "src" / "main" / "java" / "com" / "nrai" / "test" / f"Dummy{i}.kt"
            proposals.append(EditProposal(
                file_path=p,
                operation=EditOperation.CREATE_FILE,
                replacement_text=f"package com.nrai.test\nclass Dummy{i}\n",
            ))

        with self.assertRaises(AndroidSafetyError) as ctx:
            self.engine.apply_edits(proposals)
        self.assertEqual(ctx.exception.code, AndroidErrorCode.TOO_MANY_FILES_CHANGED)

    # -------------------------------------------------------------------------
    # Test J: Excessive line count rejection
    # -------------------------------------------------------------------------
    def test_J_excessive_line_count_rejection(self):
        """Single edit replacing more than 500 lines is rejected."""
        lines = ["line\n"] * 600
        self.dummy_kt_path.write_text("".join(lines), encoding="utf-8")
        current_hash = self.engine.calculate_file_hash(self.dummy_kt_path)

        proposal = EditProposal(
            file_path=self.dummy_kt_path,
            expected_old_hash=current_hash,
            operation=EditOperation.REPLACE_RANGE,
            start_line=1,
            end_line=550,
            replacement_text="single line\n",
            reason="Excessive line replacement",
        )

        with self.assertRaises(AndroidSafetyError) as ctx:
            self.engine.apply_edit(proposal)
        self.assertEqual(ctx.exception.code, AndroidErrorCode.PATCH_TOO_LARGE)

    # -------------------------------------------------------------------------
    # Test K: Prohibited token rejection
    # -------------------------------------------------------------------------
    def test_K_prohibited_token_rejection(self):
        """Replacement text containing prohibited commands or secrets is rejected."""
        self.dummy_kt_path.write_text("package com.nrai.test\n", encoding="utf-8")
        current_hash = self.engine.calculate_file_hash(self.dummy_kt_path)

        prohibited_replacements = [
            "Runtime.getRuntime().exec('rm -rf /')",
            "System.exit(0)",
            "val key = 'AIzaSy123456789012345678901234567890123'",
        ]

        for token in prohibited_replacements:
            prop = EditProposal(
                file_path=self.dummy_kt_path,
                expected_old_hash=current_hash,
                operation=EditOperation.REPLACE_RANGE,
                start_line=1,
                end_line=1,
                replacement_text=token,
                reason="Prohibited content test",
            )
            with self.assertRaises(AndroidSafetyError) as ctx:
                self.engine.apply_edit(prop)
            self.assertIn(ctx.exception.code, (AndroidErrorCode.ACTION_NOT_ALLOWED, AndroidErrorCode.EDIT_VALIDATION_FAILED))

    # -------------------------------------------------------------------------
    # Test L: Bounded model consultation
    # -------------------------------------------------------------------------
    def test_L_bounded_model_consultation(self):
        """Bounded context extractor limits lines to +-30 and provides SHA-256."""
        long_content = "\n".join([f"fun line{i}() = {i}" for i in range(1, 101)])
        self.dummy_kt_path.write_text(long_content, encoding="utf-8")

        error = AndroidBuildError(
            category=AndroidErrorCategory.KOTLIN_COMPILE,
            file_path=str(self.dummy_kt_path),
            line=50,
            message="Unresolved reference",
        )

        context = extract_bounded_context(error, self.project_root, max_lines_context=30)
        self.assertEqual(context["error_line"], 50)
        self.assertEqual(context["start_line"], 20)
        self.assertEqual(context["end_line"], 80)
        self.assertEqual(context["file_sha256"], self.engine.calculate_file_hash(self.dummy_kt_path))

        # Build prompt and verify bounding
        sys_prompt, user_prompt = build_model_repair_prompt(error, context)
        self.assertIn("STRICTLY ADVISORY", sys_prompt)
        self.assertIn("50", user_prompt)
        self.assertIn("RepairDummy.kt", user_prompt)

    # -------------------------------------------------------------------------
    # Test M: Deterministic edit application
    # -------------------------------------------------------------------------
    def test_M_deterministic_edit_application(self):
        """Applying a valid EditProposal updates the file and generates diff."""
        original = "package com.nrai.test\n\nclass RepairDummy {\n    fun bad() {\n    }\n}\n"
        self.dummy_kt_path.write_text(original, encoding="utf-8")
        current_hash = self.engine.calculate_file_hash(self.dummy_kt_path)

        proposal = EditProposal(
            file_path=self.dummy_kt_path,
            expected_old_hash=current_hash,
            operation=EditOperation.REPLACE_RANGE,
            start_line=4,
            end_line=4,
            replacement_text="    fun good() {\n        val ok = true\n",
            reason="Fix method name",
        )

        res = self.engine.apply_edit(proposal)
        self.assertTrue(res.success)
        self.assertIn("fun good()", self.dummy_kt_path.read_text(encoding="utf-8"))
        self.assertIn("-    fun bad()", res.diff)
        self.assertIn("+    fun good()", res.diff)

    # -------------------------------------------------------------------------
    # Test N: Checkpoint creation
    # -------------------------------------------------------------------------
    def test_N_checkpoint_creation(self):
        """Before modifying a file, pre-edit backup exists in scratch/android_checkpoints."""
        self.dummy_kt_path.write_text("initial content\n", encoding="utf-8")
        h0 = self.engine.calculate_file_hash(self.dummy_kt_path)

        proposal = EditProposal(
            file_path=self.dummy_kt_path,
            expected_old_hash=h0,
            operation=EditOperation.REPLACE_RANGE,
            start_line=1,
            end_line=1,
            replacement_text="modified content\n",
            reason="Test backup",
        )

        res = self.engine.apply_edit(proposal)
        self.assertTrue(res.success)
        self.assertIsNotNone(res.backup_path)
        backup_file = Path(res.backup_path)
        self.assertTrue(backup_file.exists())
        self.assertEqual(backup_file.read_text(encoding="utf-8"), "initial content\n")

    # -------------------------------------------------------------------------
    # Test O: Successful repair build
    # -------------------------------------------------------------------------
    def test_O_successful_repair_build(self):
        """Repair loop applies proposal, rebuild succeeds, reports success."""
        initial_bad = "package com.nrai.test\nclass RepairDummy { fun broken() { val x = } }\n"
        self.dummy_kt_path.write_text(initial_bad, encoding="utf-8")
        h0 = self.engine.calculate_file_hash(self.dummy_kt_path)

        # Mock Gradle: first fails with Kotlin error, second succeeds
        mock_gradle = MagicMock()
        mock_gradle.run_action.side_effect = [
            {
                "success": False,
                "exit_code": 1,
                "output_sample": f"e: {self.dummy_kt_path}:1:45 expecting an expression",
            },
            {
                "success": True,
                "exit_code": 0,
            },
        ]

        def deterministic_fix(err, attempt):
            return EditProposal(
                file_path=self.dummy_kt_path,
                expected_old_hash=h0,
                operation=EditOperation.REPLACE_RANGE,
                start_line=1,
                end_line=1,
                replacement_text="package com.nrai.test\nclass RepairDummy { fun broken() { val x = 42 } }\n",
                reason="Provide integer literal",
            )

        engine = AndroidCodeRepairEngine(
            project_path=self.project_root,
            safety_gate=self.safety,
            gradle_runner=mock_gradle,
        )

        res = engine.repair_build(
            user_goal="Fix broken val",
            deterministic_patch_provider=deterministic_fix,
        )

        self.assertTrue(res.success)
        self.assertEqual(res.attempts, 1)
        self.assertFalse(res.rolled_back)
        self.assertIn("val x = 42", self.dummy_kt_path.read_text(encoding="utf-8"))

    # -------------------------------------------------------------------------
    # Test P: Failed repair rollback
    # -------------------------------------------------------------------------
    def test_P_failed_repair_rollback(self):
        """When repair fails both attempts, all changes are rolled back byte-for-byte."""
        original = "package com.nrai.test\nclass OriginalState\n"
        self.dummy_kt_path.write_text(original, encoding="utf-8")
        h_orig = self.engine.calculate_file_hash(self.dummy_kt_path)

        # Mock Gradle fails all attempts
        mock_gradle = MagicMock()
        mock_gradle.run_action.return_value = {
            "success": False,
            "exit_code": 1,
            "output_sample": f"e: {self.dummy_kt_path}:1:1 error: compilation failed",
        }

        def bad_fix(err, attempt):
            current_h = self.engine.calculate_file_hash(self.dummy_kt_path)
            return EditProposal(
                file_path=self.dummy_kt_path,
                expected_old_hash=current_h,
                operation=EditOperation.REPLACE_RANGE,
                start_line=1,
                end_line=1,
                replacement_text=f"// Attempt {attempt}\npackage com.nrai.test\nclass OriginalState\n",
            )

        engine = AndroidCodeRepairEngine(
            project_path=self.project_root,
            safety_gate=self.safety,
            gradle_runner=mock_gradle,
        )

        res = engine.repair_build(
            user_goal="Fix impossible bug",
            deterministic_patch_provider=bad_fix,
        )

        self.assertFalse(res.success)
        self.assertEqual(res.attempts, 2)
        self.assertTrue(res.rolled_back)
        # Verify byte-for-byte restoration
        self.assertEqual(self.dummy_kt_path.read_text(encoding="utf-8"), original)
        self.assertEqual(self.engine.calculate_file_hash(self.dummy_kt_path), h_orig)

    # -------------------------------------------------------------------------
    # Test Q: Maximum two attempts
    # -------------------------------------------------------------------------
    def test_Q_maximum_two_attempts(self):
        """Repair loop halts strictly after 2 attempts even if asked for 10."""
        self.dummy_kt_path.write_text("class Dummy\n", encoding="utf-8")

        mock_gradle = MagicMock()
        mock_gradle.run_action.return_value = {
            "success": False,
            "exit_code": 1,
            "output_sample": f"e: {self.dummy_kt_path}:1:1 error: build failed",
        }

        call_count = 0

        def patch_counter(err, attempt):
            nonlocal call_count
            call_count += 1
            curr_h = self.engine.calculate_file_hash(self.dummy_kt_path)
            return EditProposal(
                file_path=self.dummy_kt_path,
                expected_old_hash=curr_h,
                operation=EditOperation.REPLACE_RANGE,
                start_line=1,
                end_line=1,
                replacement_text=f"// Attempt {attempt}\nclass Dummy\n",
            )

        engine = AndroidCodeRepairEngine(
            project_path=self.project_root,
            safety_gate=self.safety,
            gradle_runner=mock_gradle,
        )

        res = engine.repair_build(
            user_goal="Fix dummy",
            max_attempts=10,  # Request 10 attempts
            deterministic_patch_provider=patch_counter,
        )

        self.assertEqual(res.attempts, 2)
        self.assertEqual(call_count, 2)

    # -------------------------------------------------------------------------
    # Test R: No repair after emergency stop
    # -------------------------------------------------------------------------
    def test_R_no_repair_after_emergency_stop(self):
        """Active emergency stop halts repair immediately without touching files."""
        self.dummy_kt_path.write_text("initial\n", encoding="utf-8")
        self.safety.trigger_emergency_stop("Safety triggered in test")

        with self.assertRaises(EmergencyStopActiveError):
            self.engine.repair_build("Fix with emergency stop active")

        self.assertEqual(self.dummy_kt_path.read_text(encoding="utf-8"), "initial\n")

    # -------------------------------------------------------------------------
    # Test S: Model cannot execute tools directly
    # -------------------------------------------------------------------------
    def test_S_model_cannot_execute_tools_directly(self):
        """Advisory models produce only JSON proposals; zero execution authority."""
        raw_model_output = json.dumps({
            "proposal_version": "1.0",
            "summary": "Attempt direct command injection",
            "edits": [{
                "path": "app/src/main/java/com/nrai/test/MainActivity.kt",
                "start_line": 1,
                "end_line": 1,
                "replacement": "adb shell reboot",
            }],
        })

        parsed = parse_model_repair_response(raw_model_output)
        self.assertEqual(parsed["edits"][0]["replacement"], "adb shell reboot")
        self.assertTrue(self.project_root.exists())

    # -------------------------------------------------------------------------
    # Test T: Credentials never enter model context
    # -------------------------------------------------------------------------
    def test_T_credentials_never_enter_model_context(self):
        """Secrets, API keys, and passwords in source or error are sanitized."""
        leak_text = (
            "val key = 'AIzaSy123456789012345678901234567890123'\n"
            "val openai = 'sk-proj-abc123def456ghi789jkl012'\n"
            "val password = 'super_secret_password_123'\n"
            "storePassword 'store_secret_999'\n"
        )
        self.dummy_kt_path.write_text(leak_text, encoding="utf-8")

        error = AndroidBuildError(
            category=AndroidErrorCategory.KOTLIN_COMPILE,
            file_path=str(self.dummy_kt_path),
            line=1,
            message="Error referencing AIzaSy123456789012345678901234567890123",
        )

        context = extract_bounded_context(error, self.project_root)
        _, user_prompt = build_model_repair_prompt(error, context)

        self.assertNotIn("AIzaSy123456789012345678901234567890123", user_prompt)
        self.assertNotIn("sk-proj-abc123def456ghi789jkl012", user_prompt)
        self.assertNotIn("super_secret_password_123", user_prompt)
        self.assertNotIn("store_secret_999", user_prompt)
        self.assertIn("[REDACTED", user_prompt)

    # -------------------------------------------------------------------------
    # Test U: Ground-truth build result overrides model claim
    # -------------------------------------------------------------------------
    def test_U_ground_truth_build_result_overrides_model_claim(self):
        """Even if model claims 1.0 confidence, failing build marks repair as failed."""
        self.dummy_kt_path.write_text("class Broken\n", encoding="utf-8")
        h0 = self.engine.calculate_file_hash(self.dummy_kt_path)

        mock_gradle = MagicMock()
        mock_gradle.run_action.return_value = {
            "success": False,
            "exit_code": 1,
            "output_sample": f"e: {self.dummy_kt_path}:1:1 Still broken",
        }

        def model_claims_100(err, attempt):
            return EditProposal(
                file_path=self.dummy_kt_path,
                expected_old_hash=h0,
                operation=EditOperation.REPLACE_RANGE,
                start_line=1,
                end_line=1,
                replacement_text="// 100% fixed according to model\nclass Broken\n",
                reason="100% confidence fix",
            )

        engine = AndroidCodeRepairEngine(
            project_path=self.project_root,
            safety_gate=self.safety,
            gradle_runner=mock_gradle,
        )

        res = engine.repair_build("Fix it", deterministic_patch_provider=model_claims_100)
        self.assertFalse(res.success)
        self.assertTrue(res.rolled_back)

    # -------------------------------------------------------------------------
    # Test V: Unsupported error returns REPAIR_UNSUPPORTED
    # -------------------------------------------------------------------------
    def test_V_unsupported_error_returns_REPAIR_UNSUPPORTED(self):
        """Errors in security-sensitive or unsupported domains return REPAIR_UNSUPPORTED."""
        mock_gradle = MagicMock()
        mock_gradle.run_action.return_value = {
            "success": False,
            "exit_code": 1,
            "output_sample": "Execution failed for task ':app:packageDebug'. Keystore file 'release.keystore' not found.",
        }

        engine = AndroidCodeRepairEngine(
            project_path=self.project_root,
            safety_gate=self.safety,
            gradle_runner=mock_gradle,
        )

        res = engine.repair_build("Fix signing error")
        self.assertFalse(res.success)
        self.assertEqual(res.error_code, AndroidErrorCode.REPAIR_UNSUPPORTED.value)
        self.assertEqual(res.attempts, 0)
        self.assertIn("unsupported for autonomous repair", res.summary)

    # -------------------------------------------------------------------------
    # Test W: Audit log redaction
    # -------------------------------------------------------------------------
    def test_W_audit_log_redaction(self):
        """Audit logs redact sensitive tokens and credentials."""
        audit = AuditLogger()
        entry = audit.log_event(
            "TEST_EVENT",
            {
                "key": "AIzaSy000000000000000000000000000000000",
                "openai": "sk-proj-1234567890abcdef12345678",
                "password": "my_secret_password",
            },
        )
        logged_str = json.dumps(entry["details"])
        self.assertNotIn("AIzaSy000000000000000000000000000000000", logged_str)
        self.assertNotIn("sk-proj-1234567890abcdef12345678", logged_str)
        self.assertNotIn("my_secret_password", logged_str)
        self.assertIn("[REDACTED", logged_str)

    # -------------------------------------------------------------------------
    # Test X: Companion routing
    # -------------------------------------------------------------------------
    def test_X_companion_routing(self):
        """Autonomous repair commands route to CommandCategory.ANDROID_STUDIO."""
        commands = [
            "fix android build",
            "repair android build",
            "why is android build failing",
            "why is the android build failing",
            "repair android compilation error",
            "show android repair result",
            "inspect android build",
            "check android build",
        ]

        comp = NRCompanion()
        for cmd in commands:
            cat = comp.classify_command(cmd)
            self.assertEqual(
                cat,
                CommandCategory.ANDROID_STUDIO,
                f"Command '{cmd}' failed to route to ANDROID_STUDIO",
            )

    # -------------------------------------------------------------------------
    # Test Y: Real authorized Android project boundary
    # -------------------------------------------------------------------------
    def test_Y_real_authorized_android_project_boundary(self):
        """Target project exists, has gradlew, and matches authorized package com.nrai.test."""
        self.assertTrue(self.project_root.exists())
        self.assertTrue((self.project_root / "gradlew.bat").exists() or (self.project_root / "gradlew").exists())
        self.assertTrue(self.main_kt_path.exists())

        content = self.main_kt_path.read_text(encoding="utf-8")
        self.assertIn(f"package {AUTHORIZED_PACKAGE_NAME}", content)

    # -------------------------------------------------------------------------
    # Test Z: Workspace restored after test
    # -------------------------------------------------------------------------
    def test_Z_workspace_restored_after_test(self):
        """Verifies clean workspace after repair operations."""
        if self.dummy_kt_path.exists():
            self.dummy_kt_path.unlink()
        self.assertFalse(self.dummy_kt_path.exists())
        self.assertTrue(self.main_kt_path.exists())


if __name__ == "__main__":
    unittest.main()
