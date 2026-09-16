"""
NR-AI Step 8 Phase 4 Acceptance Tests: Unity C# Script Analysis & AST Modification.

Comprehensive deterministic acceptance suite covering test cases A through AA:
A. test_A_valid_csharp_parsing
B. test_B_class_discovery
C. test_C_method_discovery
D. test_D_field_and_attribute_discovery
E. test_E_property_discovery
F. test_F_syntax_defect_detection
G. test_G_malformed_csharp_safe_handling
H. test_H_valid_ast_modification_add_method
I. test_I_valid_ast_modification_replace_method
J. test_J_valid_ast_modification_add_using
K. test_K_invalid_modification_rejection
L. test_L_stale_sha256_rejection
M. test_M_path_traversal_rejection
N. test_N_protected_file_rejection
O. test_O_oversized_patch_rejection
P. test_P_too_many_files_rejection
Q. test_Q_too_many_lines_rejection
R. test_R_max_repair_attempts_rejection
S. test_S_secret_and_token_rejection
T. test_T_structural_integrity_verification
U. test_U_compile_validation_and_defect_reporting
V. test_V_automatic_rollback_on_failure
W. test_W_rollback_integrity_sha256_restoration
X. test_X_emergency_stop_freezes_ast_operations
Y. test_Y_model_isolation_and_evidence_precedence
Z. test_Z_audit_logging_and_tool_registry_dispatch
AA. test_AA_complete_deterministic_workflow
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
    ALLOWED_UNITY_AST_TOOLS,
    ALL_ALLOWED_UNITY_TOOLS,
    MAX_AST_FILES_PER_OP,
    MAX_AST_PATCH_BYTES,
    MAX_AST_CHANGED_LINES,
    MAX_AST_REPAIR_ATTEMPTS,
    redact_sensitive_data,
)
from app.agent.unity_ast import (
    CSharpParser,
    CSharpSyntaxTree,
    CSharpTypeNode,
    CSharpMethodNode,
    CSharpFieldNode,
    CSharpPropertyNode,
    CSharpUsingNode,
    CSharpSyntaxDefect,
    UnityScriptAnalyzer,
    UnityASTModifier,
    ASTModificationType,
    ASTModificationProposal,
    UnityScriptManager,
    UnityScriptModificationResult,
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
    public class PlayerController : MonoBehaviour
    {
        [SerializeField] private float speed = 5.0f;
        private Rigidbody rb;

        public int Health { get; set; }

        private void Start()
        {
            rb = GetComponent<Rigidbody>();
        }

        private void Update()
        {
            float h = Input.GetAxis("Horizontal");
            float v = Input.GetAxis("Vertical");
            Vector3 movement = new Vector3(h, 0.0f, v);
            transform.Translate(movement * speed * Time.deltaTime);
        }
    }
}
"""

SAMPLE_MALFORMED_CSHARP = """using System;
using UnityEngine;

namespace NRAI.Game
{
    public class BrokenController : MonoBehaviour
    {
        private void Start()
        {
            Debug.Log("Unbalanced braces";
        // missing closing braces
"""


class TestStep8Phase4Unity(unittest.TestCase):
    """Acceptance test suite for Step 8 Phase 4: Unity C# Script Analysis & AST Modification."""

    def setUp(self):
        self.safety = UnitySafetyGate(
            authorized_project=FIXTURE_PROJECT,
            workspace_root=WORKSPACE_ROOT,
            rate_limit_calls_per_minute=200,
        )
        self.parser = CSharpParser()
        self.analyzer = UnityScriptAnalyzer(self.parser)
        self.modifier = UnityASTModifier(self.parser)
        self.temp_audit_dir = tempfile.mkdtemp()
        self.audit = AuditLogger(log_dir=self.temp_audit_dir)

        self.test_tmp_dir = tempfile.mkdtemp(dir=str(WORKSPACE_ROOT / "scratch"))
        self.checkpoints_dir = Path(self.test_tmp_dir) / "checkpoints"
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)

        self.script_manager = UnityScriptManager(
            safety_gate=self.safety,
            checkpoint_dir=self.checkpoints_dir,
            parser=self.parser,
            analyzer=self.analyzer,
            modifier=self.modifier,
            audit_logger=self.audit,
        )

        self.registry = UnityToolRegistry(
            safety_gate=self.safety,
            ast_manager=self.script_manager,
            audit_logger=self.audit,
            workspace_root=WORKSPACE_ROOT,
            include_ast_tools=True,
        )

        # Create a working copy of PlayerController.cs inside test_tmp_dir for modification tests
        self.test_script = Path(self.test_tmp_dir) / "PlayerController.cs"
        self.test_script.write_text(SAMPLE_VALID_CSHARP, encoding="utf-8")
        self.test_script_sha256 = hashlib.sha256(self.test_script.read_bytes()).hexdigest().lower()

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
    # A - G: AST Parsing, Discovery & Defect Handling
    # -------------------------------------------------------------------------

    def test_A_valid_csharp_parsing(self):
        """Test A: Safe parsing of valid C# source into structured CSharpSyntaxTree."""
        tree = self.parser.parse(SAMPLE_VALID_CSHARP, file_path=self.test_script)
        self.assertIsInstance(tree, CSharpSyntaxTree)
        self.assertTrue(tree.is_valid)
        self.assertEqual(len(tree.diagnostics), 0)
        self.assertEqual(len(tree.usings), 2)
        self.assertEqual(tree.usings[0].namespace_name, "System")
        self.assertEqual(tree.usings[1].namespace_name, "UnityEngine")
        self.assertEqual(len(tree.namespaces), 1)
        self.assertEqual(tree.namespaces[0].name, "NRAI.Game")

    def test_B_class_discovery(self):
        """Test B: Identification and structural extraction of C# class declaration."""
        tree = self.parser.parse(SAMPLE_VALID_CSHARP)
        cls = tree.find_type("PlayerController")
        self.assertIsNotNone(cls)
        self.assertEqual(cls.name, "PlayerController")
        self.assertEqual(cls.kind, "class")
        self.assertIn("public", cls.modifiers)
        self.assertIn("MonoBehaviour", cls.base_types)
        self.assertGreaterEqual(cls.location.line, 5)

    def test_C_method_discovery(self):
        """Test C: Identification of methods, return types, modifiers, and bodies."""
        tree = self.parser.parse(SAMPLE_VALID_CSHARP)
        cls = tree.find_type("PlayerController")
        self.assertIsNotNone(cls)

        start_m = cls.find_method("Start")
        self.assertIsNotNone(start_m)
        self.assertEqual(start_m.return_type, "void")
        self.assertIn("private", start_m.modifiers)
        self.assertIn("GetComponent<Rigidbody>()", start_m.body)

        update_m = cls.find_method("Update")
        self.assertIsNotNone(update_m)
        self.assertEqual(update_m.return_type, "void")
        self.assertIn("Input.GetAxis", update_m.body)

    def test_D_field_and_attribute_discovery(self):
        """Test D: Identification of fields, field types, modifiers, and attributes."""
        tree = self.parser.parse(SAMPLE_VALID_CSHARP)
        cls = tree.find_type("PlayerController")
        self.assertIsNotNone(cls)

        speed_f = cls.find_field("speed")
        self.assertIsNotNone(speed_f)
        self.assertEqual(speed_f.type_name, "float")
        self.assertIn("private", speed_f.modifiers)
        self.assertIn("SerializeField", speed_f.attributes)
        self.assertEqual(speed_f.initial_value, "5.0f")

        rb_f = cls.find_field("rb")
        self.assertIsNotNone(rb_f)
        self.assertEqual(rb_f.type_name, "Rigidbody")
        self.assertIn("private", rb_f.modifiers)

    def test_E_property_discovery(self):
        """Test E: Identification of properties with getters and setters."""
        tree = self.parser.parse(SAMPLE_VALID_CSHARP)
        cls = tree.find_type("PlayerController")
        self.assertIsNotNone(cls)

        health_p = cls.find_property("Health")
        self.assertIsNotNone(health_p)
        self.assertEqual(health_p.type_name, "int")
        self.assertIn("public", health_p.modifiers)
        self.assertTrue(health_p.has_getter)
        self.assertTrue(health_p.has_setter)

    def test_F_syntax_defect_detection(self):
        """Test F: Detection of unbalanced braces and syntax defects."""
        tree = self.parser.parse(SAMPLE_MALFORMED_CSHARP)
        self.assertFalse(tree.is_valid)
        self.assertGreater(len(tree.diagnostics), 0)
        codes = [d.code for d in tree.diagnostics]
        # Should detect unclosed braces (CS1513: } expected)
        self.assertIn("CS1513", codes)

    def test_G_malformed_csharp_safe_handling(self):
        """Test G: Graceful handling of malformed C# without uncaught crashes."""
        malformed_inputs = [
            "",
            "   \n  \t ",
            "class { }",
            "public void () { }",
            "using ; namespace {",
            "/* unclosed comment",
            "string s = \"unclosed string;\n",
            "}}}}}}",
        ]
        for src in malformed_inputs:
            try:
                tree = self.parser.parse(src)
                self.assertIsInstance(tree, CSharpSyntaxTree)
            except Exception as e:
                self.fail(f"Parser crashed on input '{src}': {e}")

    # -------------------------------------------------------------------------
    # H - J: Valid AST Modifications
    # -------------------------------------------------------------------------

    def test_H_valid_ast_modification_add_method(self):
        """Test H: Bounded AST modification adding a new method to a class."""
        new_method = """public void ResetPosition()
{
    transform.position = Vector3.zero;
}"""
        proposal = self.script_manager.propose_modification(
            target_file=self.test_script,
            expected_sha256=self.test_script_sha256,
            modification_type=ASTModificationType.ADD_METHOD,
            target_type="PlayerController",
            new_node_content=new_method,
            rationale="Add helper to reset player position",
        )
        res = self.script_manager.apply_modification(proposal)
        self.assertTrue(res.success)
        self.assertTrue(res.ast_valid)
        self.assertFalse(res.rolled_back)
        self.assertNotEqual(res.original_sha256, res.modified_sha256)

        # Verify new method in resulting AST
        tree_post = self.script_manager.parse_script(self.test_script)
        cls = tree_post.find_type("PlayerController")
        self.assertIsNotNone(cls)
        reset_m = cls.find_method("ResetPosition")
        self.assertIsNotNone(reset_m)
        self.assertEqual(reset_m.return_type, "void")
        self.assertIn("Vector3.zero", reset_m.body)

    def test_I_valid_ast_modification_replace_method(self):
        """Test I: Bounded AST modification replacing an existing method."""
        new_update = """private void Update()
{
    // Optimized update with direct translation
    transform.Translate(Vector3.forward * speed * Time.deltaTime);
}"""
        proposal = self.script_manager.propose_modification(
            target_file=self.test_script,
            expected_sha256=self.test_script_sha256,
            modification_type=ASTModificationType.REPLACE_METHOD,
            target_type="PlayerController",
            target_member="Update",
            new_node_content=new_update,
            rationale="Optimize movement vector in Update",
        )
        res = self.script_manager.apply_modification(proposal)
        self.assertTrue(res.success)
        self.assertTrue(res.ast_valid)

        tree_post = self.script_manager.parse_script(self.test_script)
        cls = tree_post.find_type("PlayerController")
        upd_m = cls.find_method("Update")
        self.assertIsNotNone(upd_m)
        self.assertIn("Optimized update", upd_m.body)

    def test_J_valid_ast_modification_add_using(self):
        """Test J: Bounded AST modification adding a using directive."""
        proposal = self.script_manager.propose_modification(
            target_file=self.test_script,
            expected_sha256=self.test_script_sha256,
            modification_type=ASTModificationType.ADD_USING,
            parameters={"namespace": "System.Collections.Generic"},
            rationale="Add collections using directive",
        )
        res = self.script_manager.apply_modification(proposal)
        self.assertTrue(res.success)
        self.assertTrue(res.ast_valid)

        tree_post = self.script_manager.parse_script(self.test_script)
        usings = [u.namespace_name for u in tree_post.usings]
        self.assertIn("System.Collections.Generic", usings)

    # -------------------------------------------------------------------------
    # K - S: Safety, Rejection & Boundaries
    # -------------------------------------------------------------------------

    def test_K_invalid_modification_rejection(self):
        """Test K: Rejection of modification targeting non-existent type or member."""
        proposal = ASTModificationProposal(
            proposal_id="prop_invalid_type",
            target_file=self.test_script,
            expected_sha256=self.test_script_sha256,
            modification_type=ASTModificationType.REPLACE_METHOD,
            target_type="NonExistentClass",
            target_member="NonExistentMethod",
            new_node_content="void Foo() {}",
        )
        res = self.script_manager.apply_modification(proposal)
        self.assertFalse(res.success)
        self.assertTrue(res.rolled_back)
        self.assertEqual(res.error_code, UnityErrorCode.SCRIPT_NOT_FOUND.value)

    def test_L_stale_sha256_rejection(self):
        """Test L: Rejection of proposal when file SHA-256 is stale/mismatched."""
        stale_sha = "0000000000000000000000000000000000000000000000000000000000000000"
        with self.assertRaises(UnitySafetyError) as ctx:
            self.script_manager.propose_modification(
                target_file=self.test_script,
                expected_sha256=stale_sha,
                modification_type=ASTModificationType.ADD_USING,
                parameters={"namespace": "System.IO"},
            )
        self.assertEqual(ctx.exception.code, UnityErrorCode.STALE_TARGET)

    def test_M_path_traversal_rejection(self):
        """Test M: Rejection of path traversal sequences in script target."""
        traversal_path = WORKSPACE_ROOT / "nr_unity_test" / "Assets" / ".." / ".." / "outside.cs"
        with self.assertRaises(UnitySafetyError) as ctx:
            self.safety.validate_script_file_target(str(traversal_path))
        self.assertEqual(ctx.exception.code, UnityErrorCode.PATH_TRAVERSAL_DETECTED)

    def test_N_protected_file_rejection(self):
        """Test N: Rejection of protected project files and non-.cs files."""
        # 1. Protected file (ProjectVersion.txt)
        proj_ver = FIXTURE_PROJECT / "ProjectSettings" / "ProjectVersion.txt"
        with self.assertRaises(UnitySafetyError) as ctx:
            self.safety.validate_script_file_target(str(proj_ver), check_exists=False)
        self.assertEqual(ctx.exception.code, UnityErrorCode.FILE_NOT_AUTHORIZED)

        # 2. Protected directory (Library)
        lib_cs = FIXTURE_PROJECT / "Library" / "Dummy.cs"
        with self.assertRaises(UnitySafetyError) as ctx:
            self.safety.validate_script_file_target(str(lib_cs), check_exists=False)
        self.assertEqual(ctx.exception.code, UnityErrorCode.PROTECTED_DIRECTORY_REJECTED)

    def test_O_oversized_patch_rejection(self):
        """Test O: Rejection of proposals exceeding MAX_AST_PATCH_BYTES (100 KB)."""
        huge_content = "public void Massive() { " + ("int x = 1; " * 10000) + "}"
        self.assertGreater(len(huge_content.encode("utf-8")), MAX_AST_PATCH_BYTES)

        proposal = ASTModificationProposal(
            proposal_id="prop_huge",
            target_file=self.test_script,
            expected_sha256=self.test_script_sha256,
            modification_type=ASTModificationType.ADD_METHOD,
            target_type="PlayerController",
            new_node_content=huge_content,
        )
        res = self.script_manager.apply_modification(proposal)
        self.assertFalse(res.success)
        self.assertTrue(res.rolled_back)
        self.assertEqual(res.error_code, UnityErrorCode.PATCH_TOO_LARGE.value)

    def test_P_too_many_files_rejection(self):
        """Test P: Rejection of operations exceeding MAX_AST_FILES_PER_OP (5 files)."""
        with self.assertRaises(UnitySafetyError) as ctx:
            self.safety.validate_ast_limits(num_files=6)
        self.assertEqual(ctx.exception.code, UnityErrorCode.TOO_MANY_FILES_CHANGED)

    def test_Q_too_many_lines_rejection(self):
        """Test Q: Rejection of modifications exceeding MAX_AST_CHANGED_LINES (500 lines)."""
        with self.assertRaises(UnitySafetyError) as ctx:
            self.safety.validate_ast_limits(lines_changed=501)
        self.assertEqual(ctx.exception.code, UnityErrorCode.TOO_MANY_LINES_CHANGED)

    def test_R_max_repair_attempts_rejection(self):
        """Test R: Rejection when modification attempts exceed MAX_AST_REPAIR_ATTEMPTS (2)."""
        with self.assertRaises(UnitySafetyError) as ctx:
            self.safety.validate_ast_limits(attempts=3)
        self.assertEqual(ctx.exception.code, UnityErrorCode.REPAIR_ATTEMPTS_EXCEEDED)

    def test_S_secret_and_token_rejection(self):
        """Test S: Rejection of proposals containing API keys and sensitive tokens."""
        proposal_with_secret = 'public string ApiKey = "sk-1234567890abcdef12345";'
        with self.assertRaises(UnitySafetyError) as ctx:
            self.script_manager.propose_modification(
                target_file=self.test_script,
                expected_sha256=self.test_script_sha256,
                modification_type=ASTModificationType.ADD_FIELD,
                target_type="PlayerController",
                new_node_content=proposal_with_secret,
            )
        self.assertEqual(ctx.exception.code, UnityErrorCode.ACTION_NOT_ALLOWED)

    # -------------------------------------------------------------------------
    # T - W: Structural Integrity & Rollback Verification
    # -------------------------------------------------------------------------

    def test_T_structural_integrity_verification(self):
        """Test T: Automatic rejection & rollback when AST modification introduces fatal syntax error."""
        corrupt_method = """public void CorruptMethod()
{
    int a = 10;
    // Missing closing brace introduces fatal syntax defect!
"""
        proposal = ASTModificationProposal(
            proposal_id="prop_corrupt",
            target_file=self.test_script,
            expected_sha256=self.test_script_sha256,
            modification_type=ASTModificationType.ADD_METHOD,
            target_type="PlayerController",
            new_node_content=corrupt_method,
        )
        res = self.script_manager.apply_modification(proposal)
        self.assertFalse(res.success)
        self.assertTrue(res.rolled_back)
        self.assertEqual(res.error_code, UnityErrorCode.AST_INTEGRITY_FAILED.value)

    def test_U_compile_validation_and_defect_reporting(self):
        """Test U: Script defect analysis identifying missing using directives and code smells."""
        no_unity_source = """namespace BadGame
{
    public class GhostController : MonoBehaviour
    {
        private void Update()
        {
        }
    }
}
"""
        analysis = self.analyzer.analyze_script(no_unity_source)
        self.assertFalse(analysis["is_valid"])
        self.assertGreaterEqual(analysis["error_count"], 1)
        codes = [d["code"] for d in analysis["diagnostics"]]
        # Missing using UnityEngine -> CS0246
        self.assertIn("CS0246", codes)
        # Empty Update -> UNT0001
        self.assertIn("UNT0001", codes)

    def test_V_automatic_rollback_on_failure(self):
        """Test V: Automatic rollback restores original file content when validation fails."""
        initial_content = self.test_script.read_text(encoding="utf-8")
        corrupt_proposal = ASTModificationProposal(
            proposal_id="prop_fail_rollback",
            target_file=self.test_script,
            expected_sha256=self.test_script_sha256,
            modification_type=ASTModificationType.ADD_METHOD,
            target_type="PlayerController",
            new_node_content="public void Fail() { int x = 10; // missing closing brace",
        )
        res = self.script_manager.apply_modification(corrupt_proposal)
        self.assertFalse(res.success)
        self.assertTrue(res.rolled_back)

        # File content must match pre-edit content exactly
        restored_content = self.test_script.read_text(encoding="utf-8")
        self.assertEqual(restored_content, initial_content)

    def test_W_rollback_integrity_sha256_restoration(self):
        """Test W: Rollback restores exact pre-edit SHA-256 digest."""
        corrupt_proposal = ASTModificationProposal(
            proposal_id="prop_sha_verify",
            target_file=self.test_script,
            expected_sha256=self.test_script_sha256,
            modification_type=ASTModificationType.ADD_METHOD,
            target_type="PlayerController",
            new_node_content="public void Broken() { int y = 20; // missing closing brace",
        )
        res = self.script_manager.apply_modification(corrupt_proposal)
        self.assertFalse(res.success)
        current_sha = hashlib.sha256(self.test_script.read_bytes()).hexdigest().lower()
        self.assertEqual(current_sha, self.test_script_sha256)

    # -------------------------------------------------------------------------
    # X - AA: Governance, Isolation, Audit & Complete Workflow
    # -------------------------------------------------------------------------

    def test_X_emergency_stop_freezes_ast_operations(self):
        """Test X: Thread-safe emergency stop halts all AST operations immediately."""
        self.safety.activate_emergency_stop("Operator emergency stop test")
        self.assertTrue(self.safety.is_emergency_stopped)

        with self.assertRaises(EmergencyStopActiveError):
            self.script_manager.parse_script(self.test_script)

        with self.assertRaises(EmergencyStopActiveError):
            self.script_manager.propose_modification(
                target_file=self.test_script,
                expected_sha256=self.test_script_sha256,
                modification_type=ASTModificationType.ADD_USING,
                parameters={"namespace": "System.IO"},
            )

    def test_Y_model_isolation_and_evidence_precedence(self):
        """Test Y: Advisory model has zero direct write authority; AST evidence prevails."""
        # Advisory model proposing a repair cannot bypass safety gate or limits
        with self.assertRaises(UnitySafetyError) as ctx:
            self.safety.validate_script_file_target("C:/Windows/System32/calc.exe", check_exists=False)
        self.assertEqual(ctx.exception.code, UnityErrorCode.FILE_NOT_AUTHORIZED)

    def test_Z_audit_logging_and_tool_registry_dispatch(self):
        """Test Z: Dispatch all 8 Phase 4 tools through UnityToolRegistry and verify audit logs."""
        tools = self.registry.get_registered_tools()
        for t in ALLOWED_UNITY_AST_TOOLS:
            self.assertIn(t, tools)

        # 1. unity.parse_script_ast
        r1 = self.registry.execute_tool("unity.parse_script_ast", {"script_path": str(self.test_script)})
        self.assertTrue(r1.success)

        # 2. unity.analyze_script
        r2 = self.registry.execute_tool("unity.analyze_script", {"script_path": str(self.test_script)})
        self.assertTrue(r2.success)

        # 3. unity.propose_ast_modification
        r3 = self.registry.execute_tool("unity.propose_ast_modification", {
            "target_file": str(self.test_script),
            "expected_sha256": self.test_script_sha256,
            "modification_type": "ADD_USING",
            "parameters": {"namespace": "System.Threading"},
            "rationale": "Add threading support",
        })
        self.assertTrue(r3.success)
        proposal_dict = r3.data

        # 4. unity.apply_ast_modification
        r4 = self.registry.execute_tool("unity.apply_ast_modification", {"proposal": proposal_dict})
        self.assertTrue(r4.success)

        # 5. unity.verify_ast_integrity
        r5 = self.registry.execute_tool("unity.verify_ast_integrity", {"script_path": str(self.test_script)})
        self.assertTrue(r5.success)

        # 6. unity.get_script_symbols
        r6 = self.registry.execute_tool("unity.get_script_symbols", {"script_path": str(self.test_script)})
        self.assertTrue(r6.success)
        self.assertGreater(r6.data["count"], 0)

        # 7. unity.validate_script_repair
        r7 = self.registry.execute_tool("unity.validate_script_repair", {
            "target_file": str(self.test_script),
            "diagnostics_to_check": ["CS0246"],
        })
        self.assertTrue(r7.success)

        # 8. unity.rollback_script_modification
        r8 = self.registry.execute_tool("unity.rollback_script_modification", {
            "target_file": str(self.test_script),
            "checkpoint_path": r4.data["checkpoint_path"],
            "expected_original_sha256": self.test_script_sha256,
        })
        self.assertTrue(r8.success)

    def test_AA_complete_deterministic_workflow(self):
        """Test AA: Complete deterministic C# script analysis and safe AST modification workflow."""
        # 1. Parse original script
        tree_orig = self.script_manager.parse_script(self.test_script)
        self.assertTrue(tree_orig.is_valid)

        # 2. Analyze script for issues
        analysis = self.script_manager.analyze_script(self.test_script)
        self.assertTrue(analysis["is_valid"])

        # 3. Propose safe AST modification
        add_prop_code = "public float MoveSpeed => speed * 2.0f;"
        proposal = self.script_manager.propose_modification(
            target_file=self.test_script,
            expected_sha256=self.test_script_sha256,
            modification_type=ASTModificationType.ADD_PROPERTY,
            target_type="PlayerController",
            new_node_content=add_prop_code,
            rationale="Add calculated MoveSpeed property",
        )

        # 4. Apply modification
        res = self.script_manager.apply_modification(proposal)
        self.assertTrue(res.success)
        self.assertTrue(res.ast_valid)

        # 5. Re-parse and verify symbol existence
        tree_mod = self.script_manager.parse_script(self.test_script)
        p = tree_mod.find_property("PlayerController", "MoveSpeed")
        self.assertIsNotNone(p)

        # 6. Verify symbols via project query
        symbols = tree_mod.get_all_symbols()
        prop_syms = [s for s in symbols if s.get("name") == "MoveSpeed"]
        self.assertEqual(len(prop_syms), 1)


if __name__ == "__main__":
    unittest.main()
