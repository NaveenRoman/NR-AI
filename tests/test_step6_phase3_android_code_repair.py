"""
NR-AI Step 6 Phase 3 Acceptance & Regression Test Suite.
Verifies Android Code Editing + Build Error Repair Foundation.

Tests:
- test_A_authorized_project_boundary
- test_B_unauthorized_path_rejection
- test_C_file_extension_allowlist
- test_D_protected_file_rejection
- test_E_file_size_limit
- test_F_patch_size_limit
- test_G_maximum_files_changed
- test_H_stale_hash_protection
- test_I_structured_edit_validation
- test_J_diff_generation
- test_K_backup_creation
- test_L_rollback
- test_M_model_advisory_isolation
- test_N_arbitrary_shell_rejection
- test_O_arbitrary_gradle_rejection
- test_P_repair_attempt_limit
- test_Q_build_error_classification
- test_R_successful_bounded_edit
- test_S_failed_edit_rollback
- test_T_deterministic_build_invocation
- test_U_audit_logging
- test_V_companion_routing
- test_W_step6_phase2_regression
- test_X_step6_phase1_regression
- test_Y_step5_regression
- test_Z_step4e_regression
"""

import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from app.agent.android_safety import (
    ALLOWED_ANDROID_EXTENSIONS,
    ALLOWED_ANDROID_TOOLS,
    AUTHORIZED_PROJECT_PATH,
    FORBIDDEN_ANDROID_EXTENSIONS,
    MAX_EDITABLE_FILE_SIZE_BYTES,
    MAX_FILES_PER_REPAIR,
    MAX_PATCH_SIZE_BYTES,
    MAX_REPAIR_ATTEMPTS,
    PROTECTED_ANDROID_FILES,
    AndroidErrorCode,
    AndroidSafetyError,
    AndroidSafetyGate,
)
from app.agent.android_tools import (
    AndroidToolRegistry,
    AndroidToolResult,
    SafeGradleRunner,
)
from app.agent.android_verifier import (
    AndroidVerificationReport,
    AndroidVerifier,
)
from app.agent.android_code_repair import (
    AndroidBuildError,
    AndroidCodeRepairEngine,
    AndroidErrorAnalyzer,
    AndroidErrorCategory,
    EditOperation,
    EditProposal,
    EditResult,
    RepairResult,
)
from app.agent.android_studio_agent import (
    AndroidStudioAgent,
    AndroidWorkflowReport,
)
from app.brain.companion import (
    CommandCategory,
    NRCompanion,
)
from app.memory.audit_logger import AuditLogger


class TestStep6Phase3AndroidCodeRepair(unittest.TestCase):
    """Acceptance test suite for NR-AI Step 6 Phase 3."""

    def setUp(self):
        self.safety = AndroidSafetyGate()
        self.audit = AuditLogger()
        self.tools = AndroidToolRegistry(safety_gate=self.safety)
        self.verifier = AndroidVerifier(safety_gate=self.safety, tool_registry=self.tools)
        self.engine = AndroidCodeRepairEngine(
            project_path=self.safety.authorized_project,
            safety_gate=self.safety,
            gradle_runner=self.tools.gradle,
            audit_logger=self.audit,
        )
        self.agent = AndroidStudioAgent(
            tool_registry=self.tools,
            verifier=self.verifier,
            safety_gate=self.safety,
            audit_logger=self.audit,
        )

        # Create a benign test file inside authorized project for testing
        self.test_file_dir = self.safety.authorized_project / "app" / "src" / "main" / "java" / "com" / "nrai" / "test"
        self.test_file = self.test_file_dir / "TestDummy.kt"
        self.test_file_dir.mkdir(parents=True, exist_ok=True)
        self.test_file.write_text("package com.nrai.test\n\nclass TestDummy {\n    fun hello() = \"world\"\n}\n", encoding="utf-8")
        self.initial_content = self.test_file.read_text(encoding="utf-8")

    def tearDown(self):
        if self.test_file.exists():
            try:
                self.test_file.unlink()
            except Exception:
                pass

    # -------------------------------------------------------------------------
    # Test A: Authorized Project Boundary
    # -------------------------------------------------------------------------
    def test_A_authorized_project_boundary(self):
        """Operations outside C:/NR-AI/nr_android_test are blocked with FILE_NOT_AUTHORIZED."""
        # Inside project is allowed
        self.assertTrue(self.safety.validate_editable_file(self.test_file))

        # Outside project is blocked
        outside_path = Path("C:/NR-AI/app/agent/android_safety.py")
        with self.assertRaises(AndroidSafetyError) as ctx:
            self.safety.validate_editable_file(outside_path)
        self.assertEqual(ctx.exception.code, AndroidErrorCode.FILE_NOT_AUTHORIZED)

        # System directory is blocked
        system_path = Path("C:/Windows/System32/drivers/etc/hosts")
        with self.assertRaises(AndroidSafetyError) as ctx:
            self.safety.validate_editable_file(system_path)
        self.assertEqual(ctx.exception.code, AndroidErrorCode.FILE_NOT_AUTHORIZED)

    # -------------------------------------------------------------------------
    # Test B: Unauthorized Path Rejection (traversal, UNC, external)
    # -------------------------------------------------------------------------
    def test_B_unauthorized_path_rejection(self):
        """Path traversal (..), UNC paths, and external roots are rejected."""
        # Traversal out of project
        traversal_path = self.safety.authorized_project / ".." / "secret.txt"
        with self.assertRaises(AndroidSafetyError) as ctx:
            self.safety.validate_editable_file(traversal_path)
        self.assertEqual(ctx.exception.code, AndroidErrorCode.FILE_NOT_AUTHORIZED)

        # Deep traversal
        deep_traversal = self.safety.authorized_project / "app" / ".." / ".." / ".." / "Windows" / "cmd.exe"
        with self.assertRaises(AndroidSafetyError) as ctx:
            self.safety.validate_editable_file(deep_traversal)
        self.assertEqual(ctx.exception.code, AndroidErrorCode.FILE_NOT_AUTHORIZED)

        # UNC path
        unc_path = r"\\192.168.1.100\share\TestDummy.kt"
        with self.assertRaises(AndroidSafetyError) as ctx:
            self.safety.validate_editable_file(unc_path)
        self.assertEqual(ctx.exception.code, AndroidErrorCode.FILE_NOT_AUTHORIZED)

    # -------------------------------------------------------------------------
    # Test C: File Extension Allowlist
    # -------------------------------------------------------------------------
    def test_C_file_extension_allowlist(self):
        """Only authorized Android file extensions are permitted; forbidden types are rejected."""
        # Allowed extensions
        allowed_exts = [".kt", ".java", ".xml", ".gradle", ".gradle.kts", ".properties", ".json", ".toml", ".yaml", ".yml", ".md"]
        for ext in allowed_exts:
            p = self.safety.authorized_project / f"sample{ext}"
            self.assertTrue(self.safety.validate_editable_file(p, is_creation=True), f"Extension {ext} should be allowed")

        # Forbidden extensions
        forbidden_exts = [".exe", ".bat", ".cmd", ".ps1", ".py", ".dll", ".so", ".apk", ".aab", ".class", ".jar"]
        for ext in forbidden_exts:
            p = self.safety.authorized_project / f"malicious{ext}"
            with self.assertRaises(AndroidSafetyError) as ctx:
                self.safety.validate_editable_file(p)
            self.assertEqual(ctx.exception.code, AndroidErrorCode.FILE_NOT_AUTHORIZED)

    # -------------------------------------------------------------------------
    # Test D: Protected File Rejection
    # -------------------------------------------------------------------------
    def test_D_protected_file_rejection(self):
        """Protected files (local.properties, keystores, credentials) are rejected."""
        protected_targets = [
            self.safety.authorized_project / "local.properties",
            self.safety.authorized_project / "google-services.json",
            self.safety.authorized_project / "release.jks",
            self.safety.authorized_project / "app" / "debug.keystore",
            self.safety.authorized_project / ".env",
            self.safety.authorized_project / "credentials.json",
            self.safety.authorized_project / "secret_token.txt",
        ]
        for p in protected_targets:
            with self.assertRaises(AndroidSafetyError) as ctx:
                self.safety.validate_editable_file(p)
            self.assertEqual(ctx.exception.code, AndroidErrorCode.PROTECTED_FILE_REJECTED, f"Failed for {p}")

    # -------------------------------------------------------------------------
    # Test E: File Size Limit (1 MB)
    # -------------------------------------------------------------------------
    def test_E_file_size_limit(self):
        """Target files exceeding 1 MB are rejected with FILE_TOO_LARGE."""
        mock_stat = MagicMock()
        mock_stat.st_size = MAX_EDITABLE_FILE_SIZE_BYTES + 1024
        with patch.object(Path, "stat", return_value=mock_stat):
            with self.assertRaises(AndroidSafetyError) as ctx:
                self.safety.validate_editable_file(self.test_file)
            self.assertEqual(ctx.exception.code, AndroidErrorCode.FILE_TOO_LARGE)

    # -------------------------------------------------------------------------
    # Test F: Patch Size Limit (100 KB)
    # -------------------------------------------------------------------------
    def test_F_patch_size_limit(self):
        """Patches exceeding 100 KB are rejected with PATCH_TOO_LARGE."""
        small_patch = "val x = 1\n" * 50
        self.assertTrue(self.safety.validate_patch_size(small_patch))

        large_patch = "A" * (MAX_PATCH_SIZE_BYTES + 500)
        with self.assertRaises(AndroidSafetyError) as ctx:
            self.safety.validate_patch_size(large_patch)
        self.assertEqual(ctx.exception.code, AndroidErrorCode.PATCH_TOO_LARGE)

    # -------------------------------------------------------------------------
    # Test G: Maximum Files Changed (Capped at 5)
    # -------------------------------------------------------------------------
    def test_G_maximum_files_changed(self):
        """Repairs proposing more than 5 files in one transaction are rejected."""
        six_edits = [
            EditProposal(
                file_path=str(self.safety.authorized_project / f"File{i}.kt"),
                operation=EditOperation.REPLACE_EXACT,
                old_str="val a = 1",
                new_str="val a = 2",
            )
            for i in range(MAX_FILES_PER_REPAIR + 1)
        ]
        proposal = EditProposal(
            file_path=str(self.safety.authorized_project / "File0.kt"),
            operation=EditOperation.REPLACE_EXACT,
            description="Modify 6 files",
            edits=six_edits,
        )
        with self.assertRaises(AndroidSafetyError) as ctx:
            self.engine.apply_edits(proposal)
        self.assertEqual(ctx.exception.code, AndroidErrorCode.TOO_MANY_FILES_CHANGED)

    # -------------------------------------------------------------------------
    # Test H: Stale Hash Protection
    # -------------------------------------------------------------------------
    def test_H_stale_hash_protection(self):
        """Mismatch between expected and actual SHA-256 raises STALE_TARGET."""
        proposal = EditProposal(
            file_path=str(self.test_file),
            operation=EditOperation.REPLACE_EXACT,
            old_str="world",
            new_str="nrai",
            expected_hash="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            description="Stale test",
        )
        with self.assertRaises(AndroidSafetyError) as ctx:
            self.engine.apply_edits(proposal)
        self.assertEqual(ctx.exception.code, AndroidErrorCode.STALE_TARGET)

    # -------------------------------------------------------------------------
    # Test I: Structured Edit Validation (REPLACE_RANGE, REPLACE_EXACT, INSERT_AFTER, CREATE_FILE)
    # -------------------------------------------------------------------------
    def test_I_structured_edit_validation(self):
        """Operations validate arguments and succeed or fail with EDIT_VALIDATION_FAILED."""
        # 1. REPLACE_EXACT failing when old_str not found
        proposal_bad_exact = EditProposal(
            file_path=str(self.test_file),
            operation=EditOperation.REPLACE_EXACT,
            old_str="NON_EXISTENT_STRING_XYZ",
            new_str="replacement",
        )
        with self.assertRaises(AndroidSafetyError) as ctx:
            self.engine.apply_edits(proposal_bad_exact)
        self.assertEqual(ctx.exception.code, AndroidErrorCode.EDIT_VALIDATION_FAILED)

        # 2. REPLACE_RANGE failing on invalid range
        proposal_bad_range = EditProposal(
            file_path=str(self.test_file),
            operation=EditOperation.REPLACE_RANGE,
            start_line=10,
            end_line=5,
            new_str="replacement",
        )
        with self.assertRaises(AndroidSafetyError) as ctx:
            self.engine.apply_edits(proposal_bad_range)
        self.assertEqual(ctx.exception.code, AndroidErrorCode.EDIT_VALIDATION_FAILED)

        # 3. INSERT_AFTER failing when marker not found
        proposal_bad_marker = EditProposal(
            file_path=str(self.test_file),
            operation=EditOperation.INSERT_AFTER,
            after_marker="MISSING_MARKER",
            new_str="val added = true",
        )
        with self.assertRaises(AndroidSafetyError) as ctx:
            self.engine.apply_edits(proposal_bad_marker)
        self.assertEqual(ctx.exception.code, AndroidErrorCode.EDIT_VALIDATION_FAILED)

    # -------------------------------------------------------------------------
    # Test J: Diff Generation
    # -------------------------------------------------------------------------
    def test_J_diff_generation(self):
        """Engine generates valid unified diff with headers and line changes."""
        old_txt = "line 1\nline 2\nline 3\n"
        new_txt = "line 1\nline 2 modified\nline 3\n"
        diff = self.engine.generate_diff(old_txt, new_txt, "Test.kt")
        self.assertIn("---", diff)
        self.assertIn("+++", diff)
        self.assertIn("-line 2", diff)
        self.assertIn("+line 2 modified", diff)

    # -------------------------------------------------------------------------
    # Test K: Backup Creation
    # -------------------------------------------------------------------------
    def test_K_backup_creation(self):
        """Pre-edit backup is created in checkpoints directory before disk write."""
        orig_content = self.test_file.read_text(encoding="utf-8")
        orig_hash = self.engine.calculate_file_hash(self.test_file)
        proposal = EditProposal(
            file_path=str(self.test_file),
            operation=EditOperation.REPLACE_EXACT,
            old_str="world",
            new_str="NR-AI",
            expected_hash=orig_hash,
            description="Test backup",
        )
        res = self.engine.apply_edits(proposal)
        self.assertTrue(res.success)
        self.assertTrue(len(res.backup_ids) > 0)

        # Verify backup exists on disk
        backup_file = Path(res.backup_ids[0])
        self.assertTrue(backup_file.exists())
        self.assertEqual(backup_file.read_text(encoding="utf-8"), orig_content)

    # -------------------------------------------------------------------------
    # Test L: Rollback
    # -------------------------------------------------------------------------
    def test_L_rollback(self):
        """Rollback accurately restores the pre-edit content and SHA-256 hash."""
        orig_content = self.test_file.read_text(encoding="utf-8")
        orig_hash = self.engine.calculate_file_hash(self.test_file)
        proposal = EditProposal(
            file_path=str(self.test_file),
            operation=EditOperation.REPLACE_EXACT,
            old_str="world",
            new_str="NR-AI Modified",
            expected_hash=orig_hash,
            description="Rollback test",
        )
        res = self.engine.apply_edits(proposal)
        self.assertTrue(res.success)
        self.assertIn("NR-AI Modified", self.test_file.read_text(encoding="utf-8"))

        # Rollback
        rollback_ok = self.engine.rollback(res.backup_ids[0], str(self.test_file))
        self.assertTrue(rollback_ok)
        restored_content = self.test_file.read_text(encoding="utf-8")
        self.assertEqual(restored_content, orig_content)
        self.assertEqual(self.engine.calculate_file_hash(self.test_file), orig_hash)

    # -------------------------------------------------------------------------
    # Test M: Model Advisory Isolation
    # -------------------------------------------------------------------------
    def test_M_model_advisory_isolation(self):
        """Advisory models have zero direct file write or execution capabilities."""
        raw_model_response = (
            "```json\n"
            "{\n"
            '  "description": "Dangerous model proposal",\n'
            '  "edits": [\n'
            "    {\n"
            '      "operation_type": "CREATE_FILE",\n'
            '      "file_path": "C:/Windows/System32/hack.exe",\n'
            '      "new_str": "malicious payload"\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "```"
        )
        parsed = self.engine.parse_model_proposal(raw_model_response)
        self.assertIsNotNone(parsed)
        with self.assertRaises(AndroidSafetyError) as ctx:
            self.engine.apply_edits(parsed)
        self.assertEqual(ctx.exception.code, AndroidErrorCode.FILE_NOT_AUTHORIZED)

    # -------------------------------------------------------------------------
    # Test N: Arbitrary Shell Rejection
    # -------------------------------------------------------------------------
    def test_N_arbitrary_shell_rejection(self):
        """Prohibited shell and subprocess injection patterns are rejected."""
        malicious_snippets = [
            'import subprocess\nsubprocess.call([\"rm\", \"-rf\"])',
            'os.system(\"calc.exe\")',
            'Runtime.getRuntime().exec(\"cmd.exe /c dir\")',
            'powershell.exe -ExecutionPolicy Bypass',
            'val key = \"AIzaSyB1234567890123456789012345678901\"',
            'val token = \"sk-proj-1234567890abcdef1234567890abcdef\"',
        ]
        for snippet in malicious_snippets:
            with self.assertRaises(AndroidSafetyError) as ctx:
                self.safety.validate_prohibited_content(snippet)
            self.assertEqual(ctx.exception.code, AndroidErrorCode.EDIT_VALIDATION_FAILED)

    # -------------------------------------------------------------------------
    # Test O: Arbitrary Gradle Rejection
    # -------------------------------------------------------------------------
    def test_O_arbitrary_gradle_rejection(self):
        """Only authorized actions (DEBUG_ASSEMBLE, CLEAN, CHECK) are allowed in SafeGradleRunner."""
        disallowed_actions = ["cleanBuildCache", "publish", "assembleRelease", "uploadArchives", "bintrayUpload"]
        for act in disallowed_actions:
            res = self.tools.gradle.run_gradle_task(act)
            self.assertFalse(res.success)
            self.assertEqual(res.error_code, AndroidErrorCode.BUILD_NOT_ALLOWED.value)

    # -------------------------------------------------------------------------
    # Test P: Repair Attempt Limit (Capped at 2)
    # -------------------------------------------------------------------------
    def test_P_repair_attempt_limit(self):
        """Build repair loop halts after at most 2 attempts and restores pre-repair state."""
        call_count = 0

        def failing_build(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return {
                "success": False,
                "returncode": 1,
                "tasks": ["assembleDebug"],
                "duration_s": 0.5,
                "output_sample": "e: /app/src/main/java/com/nrai/test/MainActivity.kt: (10, 5): Unresolved reference: foo",
            }

        mock_proposal = EditProposal(
            file_path=str(self.test_file),
            operation=EditOperation.REPLACE_EXACT,
            old_str="world",
            new_str="world_attempt",
            description="Mock fix attempt",
        )

        with patch.object(self.tools.gradle, "run_action", side_effect=failing_build):
            rep_res = self.engine.repair_build(
                user_goal="Fix unresolved reference",
                max_attempts=MAX_REPAIR_ATTEMPTS,
                deterministic_patch_provider=lambda err, attempt: mock_proposal,
            )

        self.assertFalse(rep_res.success)
        self.assertLessEqual(rep_res.attempts, MAX_REPAIR_ATTEMPTS)
        self.assertTrue(rep_res.rollback_performed)
        self.assertEqual(self.test_file.read_text(encoding="utf-8"), self.initial_content)

    # -------------------------------------------------------------------------
    # Test Q: Build Error Classification
    # -------------------------------------------------------------------------
    def test_Q_build_error_classification(self):
        """AndroidErrorAnalyzer accurately classifies build errors into categories."""
        analyzer = AndroidErrorAnalyzer()

        # 1. Kotlin compile
        err_kt = analyzer.analyze("e: /path/MainActivity.kt: (15, 20): Unresolved reference: xyz")
        self.assertEqual(err_kt.category, AndroidErrorCategory.KOTLIN_COMPILE)

        # 2. Java compile
        err_java = analyzer.analyze("/path/MainActivity.java:25: error: cannot find symbol")
        self.assertEqual(err_java.category, AndroidErrorCategory.JAVA_COMPILE)

        # 3. Manifest merger
        err_manifest = analyzer.analyze('Execution failed for task \":app:processDebugMainManifest\".\nManifest merger failed with multiple errors')
        self.assertEqual(err_manifest.category, AndroidErrorCategory.MANIFEST_MERGER)

        # 4. Resource linking
        err_res = analyzer.analyze("AAPT: error: resource color/primary_color not found.")
        self.assertEqual(err_res.category, AndroidErrorCategory.RESOURCE_LINKING)

        # 5. Gradle configuration
        err_cfg = analyzer.analyze("Build was configured to prefer settings repositories over project repositories\nPlugin [id: org.jetbrains.kotlin] was not found")
        self.assertEqual(err_cfg.category, AndroidErrorCategory.GRADLE_CONFIGURATION)

    # -------------------------------------------------------------------------
    # Test R: Successful Bounded Edit
    # -------------------------------------------------------------------------
    def test_R_successful_bounded_edit(self):
        """Applying a valid bounded edit updates file content, generates diff, and logs audit."""
        proposal = EditProposal(
            file_path=str(self.test_file),
            operation=EditOperation.REPLACE_EXACT,
            old_str='fun hello() = \"world\"',
            new_str='fun hello() = \"NR-AI Verified\"',
            description="Update hello greeting",
        )
        res = self.engine.apply_edits(proposal)

        self.assertTrue(res.success)
        self.assertEqual(len(res.modified_files), 1)
        self.assertIn('+    fun hello() = \"NR-AI Verified\"', res.diff)
        self.assertIn("NR-AI Verified", self.test_file.read_text(encoding="utf-8"))

    # -------------------------------------------------------------------------
    # Test S: Failed Edit Rollback
    # -------------------------------------------------------------------------
    def test_S_failed_edit_rollback(self):
        """If an edit batch encounters an error halfway, all applied edits are rolled back."""
        file2 = self.test_file_dir / "TestDummy2.kt"
        file2.write_text("package com.nrai.test\nclass TestDummy2\n", encoding="utf-8")
        try:
            edit1 = EditProposal(
                file_path=str(self.test_file),
                operation=EditOperation.REPLACE_EXACT,
                old_str="world",
                new_str="mutated_first",
            )
            # edit2 has a bad target old_str which will fail
            edit2 = EditProposal(
                file_path=str(file2),
                operation=EditOperation.REPLACE_EXACT,
                old_str="NON_EXISTENT_MARKER",
                new_str="mutated_second",
            )
            proposal = EditProposal(
                file_path=str(self.test_file),
                operation=EditOperation.REPLACE_EXACT,
                description="Atomic two-file batch",
                edits=[edit1, edit2],
            )
            with self.assertRaises(AndroidSafetyError):
                self.engine.apply_edits(proposal)

            self.assertEqual(self.test_file.read_text(encoding="utf-8"), self.initial_content)
        finally:
            if file2.exists():
                file2.unlink()

    # -------------------------------------------------------------------------
    # Test T: Deterministic Build Invocation
    # -------------------------------------------------------------------------
    def test_T_deterministic_build_invocation(self):
        """Engine invokes SafeGradleRunner with DEBUG_ASSEMBLE."""
        with patch.object(self.tools.gradle, "run_gradle_task") as mock_gradle:
            mock_gradle.return_value = AndroidToolResult(
                success=True,
                tool="android.build_project",
                data={"success": True, "task": "assembleDebug"},
            )
            res = self.engine.run_build()
            self.assertTrue(res.success)
            mock_gradle.assert_called_once_with("DEBUG_ASSEMBLE", safety_gate=self.safety)

    # -------------------------------------------------------------------------
    # Test U: Audit Logging
    # -------------------------------------------------------------------------
    def test_U_audit_logging(self):
        """Structured audit logs are emitted for proposed, applied, and rolled back edits."""
        proposal = EditProposal(
            file_path=str(self.test_file),
            operation=EditOperation.REPLACE_EXACT,
            old_str="world",
            new_str="audit_test",
            description="Audit verification",
        )
        res = self.engine.apply_edits(proposal)
        self.assertTrue(res.success)

        # Rollback
        self.engine.rollback(res.backup_ids[0], str(self.test_file))

        recent_logs = self.audit.get_recent_logs(limit=20)
        action_names = [log.get("action") for log in recent_logs]
        self.assertTrue(any("ANDROID_CODE_EDIT_PROPOSED" in str(a) for a in action_names))
        self.assertTrue(any("ANDROID_CODE_EDIT_APPLIED" in str(a) for a in action_names))
        self.assertTrue(any("ANDROID_CODE_ROLLBACK" in str(a) for a in action_names))

    # -------------------------------------------------------------------------
    # Test V: Companion Routing
    # -------------------------------------------------------------------------
    def test_V_companion_routing(self):
        """Companion correctly classifies inspect, repair, and explain commands to ANDROID_STUDIO."""
        comp = NRCompanion()
        test_phrases = [
            "inspect code",
            "fix build",
            "repair build",
            "explain build error",
            "inspect android code",
            "android: inspect code in project",
        ]
        for phrase in test_phrases:
            cat = comp.classify_command(phrase)
            self.assertEqual(cat, CommandCategory.ANDROID_STUDIO, f"Failed for phrase: {phrase}")

    # -------------------------------------------------------------------------
    # Test W: Step 6 Phase 2 Regression
    # -------------------------------------------------------------------------
    def test_W_step6_phase2_regression(self):
        """Verify Step 6 Phase 2 pipeline contracts remain intact."""
        self.assertEqual(self.safety.authorized_devices, {"emulator-5554", "15930545720012G"})
        self.assertEqual(self.safety.authorized_package, "com.nrai.test")
        self.assertEqual(len(ALLOWED_ANDROID_TOOLS), 15)
        self.assertTrue(hasattr(self.agent, "execute_pipeline"))

    # -------------------------------------------------------------------------
    # Test X: Step 6 Phase 1 Regression
    # -------------------------------------------------------------------------
    def test_X_step6_phase1_regression(self):
        """Verify Step 6 Phase 1 AndroidVerifier checks pass 10/10."""
        report = self.verifier.run_full_verification()
        self.assertEqual(report.total_checks, 10)
        self.assertEqual(report.passed_checks, 10)
        self.assertTrue(report.all_passed)

    # -------------------------------------------------------------------------
    # Test Y: Step 5 Regression
    # -------------------------------------------------------------------------
    def test_Y_step5_regression(self):
        """Verify Step 5 browser safety and consensus systems remain intact."""
        from app.agent.browser_safety import BrowserSafetyGate
        gate = BrowserSafetyGate()
        ok, _ = gate.validate_url("http://127.0.0.1:8585/test")
        self.assertTrue(ok)
        bad_js, _ = gate.validate_url("javascript:alert(1)")
        self.assertFalse(bad_js)
        bad_file, _ = gate.validate_url("file:///C:/Windows")
        self.assertFalse(bad_file)

    # -------------------------------------------------------------------------
    # Test Z: Step 4E Regression
    # -------------------------------------------------------------------------
    def test_Z_step4e_regression(self):
        """Verify Step 4E UnifiedComputerAgent safety and input freeze remain intact."""
        from app.agent.input_controller import InputController
        controller = InputController()
        controller.emergency_stop()
        self.assertTrue(controller.is_emergency_stopped())
        res = controller.click_target(100, 100)
        self.assertFalse(res.success)
        self.assertEqual(res.error, "EMERGENCY_STOP_ACTIVE")
        controller.reset_emergency_stop()
        self.assertFalse(controller.is_emergency_stopped())


if __name__ == "__main__":
    unittest.main()
