r"""
NR-AI Step 9 Phase 4 Acceptance Test Suite
Unreal Engine Agent — C++ / Blueprint Intelligence & Safe Modification Foundation

Tests all 40 required areas:
1. C++ header parsing (.h)
2. C++ source parsing (.cpp)
3. C++ symbol extraction (classes, methods, properties, enums)
4. Reflection metadata extraction (UCLASS, UPROPERTY, UFUNCTION, UENUM, GENERATED_BODY)
5. Class inheritance analysis and base hierarchy
6. Method symbol analysis (parameters, return type, qualifiers)
7. Enum symbol analysis and entry extraction
8. Balanced syntax verification (balanced brackets, braces, parentheses)
9. Comment and string stripping in syntax verification
10. Blueprint asset discovery in Content/
11. Blueprint metadata inspection and static limitations
12. Proposal data model serialization and deserialization
13. Proposal validation - valid C++ proposal
14. Proposal validation - workspace boundary confinement
15. Proposal validation - path traversal rejection
16. Proposal validation - protected file rejection
17. Proposal validation - source extension allowlist
18. Proposal validation - file size limit (1 MB)
19. Proposal validation - patch size limit (100 KB)
20. Proposal validation - line change limit (500 lines)
21. Proposal validation - prohibited token rejection (shell, subprocess, eval)
22. Stale target pre-verification (SHA-256 check)
23. Pre-modification backup creation and verification
24. Operation: ADD_INCLUDE (before *.generated.h)
25. Operation: REMOVE_INCLUDE
26. Operation: ADD_METHOD
27. Operation: REPLACE_METHOD_BODY
28. Operation: REPLACE_SOURCE_RANGE
29. Operation: ADD_MEMBER_PROPERTY
30. Operation: ADD_ENUM_ENTRY
31. Atomic write semantics and temp file swap
32. Post-write syntax and hash verification
33. Byte-for-byte exact rollback verification
34. Corrupted backup rejection on rollback
35. Safety gate payload validation
36. Model advisory isolation
37. Audit logging and sensitive token redaction
38. Duplicate tool detection across all 44 tools
39. Static safety audit (shell=True == 0 in all unreal_*.py)
40. Tool registry dispatch for all 14 Phase 4 tools
"""

import glob
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from app.agent.unreal_safety import (
    UnrealSafetyGate,
    DEFAULT_UNREAL_SAFETY_GATE,
    UnrealErrorCode,
    UnrealSafetyError,
    ALLOWED_UNREAL_PHASE4_TOOLS,
    ALL_ALLOWED_UNREAL_TOOLS,
    ALLOWED_UNREAL_SOURCE_EXTENSIONS,
    MAX_SOURCE_FILE_BYTES,
    MAX_PATCH_BYTES,
    MAX_CHANGED_LINES,
    PROHIBITED_SOURCE_TOKENS,
    redact_sensitive_data,
)
from app.agent.unreal_source import (
    UnrealCppAnalyzer,
    UnrealBlueprintInspector,
    UnrealSourceModifier,
    UnrealModificationProposal,
    UnrealModificationBackup,
    UnrealModificationResult,
    UnrealModificationOperation,
    UnrealCppFileAnalysis,
    UnrealClassSymbol,
    UnrealMethodSymbol,
    UnrealPropertySymbol,
    UnrealEnumSymbol,
    DEFAULT_UNREAL_CPP_ANALYZER,
    DEFAULT_UNREAL_BLUEPRINT_INSPECTOR,
    DEFAULT_UNREAL_SOURCE_MODIFIER,
)
from app.agent.unreal_tools import (
    UnrealToolRegistry,
    UnrealToolResult,
    DEFAULT_UNREAL_TOOL_REGISTRY,
)


SAMPLE_HEADER_CONTENT = """// Copyright Epic Games, Inc. All Rights Reserved.
#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Character.h"
#include "MyCharacter.generated.h"

UENUM(BlueprintType)
enum class ECharacterState : uint8
{
\tIdle UMETA(DisplayName = "Idle"),
\tRunning UMETA(DisplayName = "Running"),
\tJumping UMETA(DisplayName = "Jumping")
};

UCLASS(config=Game, Blueprintable)
class MYPROJECT_API AMyCharacter : public ACharacter
{
\tGENERATED_BODY()

public:
\tAMyCharacter();

\tUPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Stats")
\tfloat Health;

\tUPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "State")
\tECharacterState CurrentState;

\tUFUNCTION(BlueprintCallable, Category = "Actions")
\tvirtual void TakeDamage(float DamageAmount);

protected:
\tvirtual void BeginPlay() override;
};
"""

SAMPLE_SOURCE_CONTENT = """// Copyright Epic Games, Inc. All Rights Reserved.

#include "MyCharacter.h"

AMyCharacter::AMyCharacter()
{
\tPrimaryActorTick.bCanEverTick = true;
\tHealth = 100.0f;
\tCurrentState = ECharacterState::Idle;
}

void AMyCharacter::BeginPlay()
{
\tSuper::BeginPlay();
}

void AMyCharacter::TakeDamage(float DamageAmount)
{
\tHealth -= DamageAmount;
\tif (Health < 0.0f)
\t{
\t\tHealth = 0.0f;
\t}
}
"""


class TestStep9Phase4Unreal(unittest.TestCase):
    """Test suite for Unreal Engine Agent Step 9 Phase 4 Foundation."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="nrai_unreal_phase4_test_")
        self.proj_dir = Path(self.tmp_dir) / "MyProject"
        self.proj_dir.mkdir(parents=True, exist_ok=True)
        self.source_dir = self.proj_dir / "Source" / "MyProject"
        self.source_dir.mkdir(parents=True, exist_ok=True)
        self.content_dir = self.proj_dir / "Content" / "Blueprints"
        self.content_dir.mkdir(parents=True, exist_ok=True)

        # Create .uproject
        self.uproject_file = self.proj_dir / "MyProject.uproject"
        self.uproject_file.write_text(json.dumps({"FileVersion": 3, "EngineAssociation": "5.4"}), encoding="utf-8")

        # Create header and source files
        self.header_file = self.source_dir / "MyCharacter.h"
        self.header_file.write_text(SAMPLE_HEADER_CONTENT, encoding="utf-8")

        self.cpp_file = self.source_dir / "MyCharacter.cpp"
        self.cpp_file.write_text(SAMPLE_SOURCE_CONTENT, encoding="utf-8")

        # Create mock Blueprint asset
        self.bp_file = self.content_dir / "BP_MyCharacter.uasset"
        self.bp_file.write_bytes(bytes([193, 131, 42, 158]) + bytes(256))

        # Initialize safety gate and components for this workspace
        self.safety = UnrealSafetyGate(
            workspace_root=Path(self.tmp_dir),
            authorized_projects=[self.proj_dir],
        )
        self.analyzer = UnrealCppAnalyzer(safety_gate=self.safety)
        self.bp_inspector = UnrealBlueprintInspector(safety_gate=self.safety)
        self.backup_dir = Path(self.tmp_dir) / "checkpoints"
        self.modifier = UnrealSourceModifier(
            safety_gate=self.safety,
            analyzer=self.analyzer,
            backup_root=self.backup_dir,
        )
        self.registry = UnrealToolRegistry(
            safety_gate=self.safety,
            workspace_root=Path(self.tmp_dir),
            cpp_analyzer=self.analyzer,
            bp_inspector=self.bp_inspector,
            source_modifier=self.modifier,
        )

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    # 1. C++ Header Parsing (.h)
    def test_01_cpp_header_parsing(self):
        analysis = self.analyzer.analyze_file(self.header_file)
        self.assertTrue(analysis.is_header)
        self.assertTrue(analysis.has_pragma_once)
        self.assertEqual(len(analysis.includes), 3)
        self.assertEqual(analysis.includes[0].header_name, "CoreMinimal.h")
        self.assertEqual(analysis.includes[2].header_name, "MyCharacter.generated.h")
        self.assertEqual(analysis.module_api_macro, "MYPROJECT_API")

    # 2. C++ Source Parsing (.cpp)
    def test_02_cpp_source_parsing(self):
        analysis = self.analyzer.analyze_file(self.cpp_file)
        self.assertFalse(analysis.is_header)
        self.assertEqual(len(analysis.includes), 1)
        self.assertEqual(analysis.includes[0].header_name, "MyCharacter.h")
        self.assertGreaterEqual(len(analysis.methods), 3)

    # 3. C++ Symbol Extraction
    def test_03_cpp_symbol_extraction(self):
        analysis = self.analyzer.analyze_file(self.header_file)
        self.assertEqual(len(analysis.classes), 1)
        cls = analysis.classes[0]
        self.assertEqual(cls.name, "AMyCharacter")
        self.assertIn("ACharacter", cls.base_classes)
        self.assertEqual(len(analysis.enums), 1)
        self.assertEqual(analysis.enums[0].name, "ECharacterState")

    # 4. Reflection Metadata Extraction
    def test_04_reflection_metadata_extraction(self):
        analysis = self.analyzer.analyze_file(self.header_file)
        macros = analysis.reflection_macros
        macro_types = [m.macro_type for m in macros]
        self.assertIn("UCLASS", macro_types)
        self.assertIn("UENUM", macro_types)
        self.assertIn("UPROPERTY", macro_types)
        self.assertIn("UFUNCTION", macro_types)
        self.assertIn("GENERATED_BODY", macro_types)

        uprop = next(m for m in macros if m.macro_type == "UPROPERTY")
        self.assertIn("EditAnywhere", uprop.specifiers)
        self.assertIn("BlueprintReadWrite", uprop.specifiers)

    # 5. Class Inheritance Analysis
    def test_05_class_inheritance_analysis(self):
        analysis = self.analyzer.analyze_file(self.header_file)
        cls = analysis.classes[0]
        self.assertIn("ACharacter", cls.base_classes)
        # Verify inheritance tool
        res = self.registry.execute_tool(
            "unreal.inspect_inheritance",
            file_path=str(self.header_file),
            class_name="AMyCharacter",
        )
        self.assertTrue(res.success)
        self.assertTrue(res.data["hierarchies"][0]["is_actor"])
        self.assertTrue(res.data["hierarchies"][0]["is_uobject"])

    # 6. Method Symbol Analysis
    def test_06_method_symbol_analysis(self):
        analysis = self.analyzer.analyze_file(self.header_file)
        take_damage = next((m for m in analysis.methods if m.name == "TakeDamage"), None)
        self.assertIsNotNone(take_damage)
        self.assertEqual(take_damage.return_type, "void")
        self.assertTrue(take_damage.is_virtual)
        self.assertIn("float DamageAmount", take_damage.parameters)

    # 7. Enum Symbol Analysis
    def test_07_enum_symbol_analysis(self):
        analysis = self.analyzer.analyze_file(self.header_file)
        self.assertEqual(len(analysis.enums), 1)
        en = analysis.enums[0]
        self.assertEqual(en.name, "ECharacterState")
        self.assertEqual(en.underlying_type, "uint8")
        entry_names = [e["name"] for e in en.entries]
        self.assertIn("Idle", entry_names)
        self.assertIn("Running", entry_names)
        self.assertIn("Jumping", entry_names)

    # 8. Balanced Syntax Verification
    def test_08_balanced_syntax_verification(self):
        valid_code = "void Test() { if (true) { DoWork(); } }"
        valid, err = self.analyzer.verify_balanced_syntax(valid_code)
        self.assertTrue(valid)
        self.assertIsNone(err)

        invalid_code = "void Test() { if (true) { DoWork(); }"
        valid, err = self.analyzer.verify_balanced_syntax(invalid_code)
        self.assertFalse(valid)
        self.assertTrue("Unclosed" in err or "Unbalanced" in err or "Unmatched" in err)

    # 9. Comment and String Stripping
    def test_09_comment_and_string_stripping(self):
        code_with_braces_in_string = 'FString S = "{not an unbalanced block}"; // } still closed'
        valid, err = self.analyzer.verify_balanced_syntax(code_with_braces_in_string)
        self.assertTrue(valid)

    # 10. Blueprint Asset Discovery
    def test_10_blueprint_asset_discovery(self):
        assets = self.bp_inspector.list_blueprint_assets(self.proj_dir)
        self.assertGreaterEqual(len(assets), 1)
        self.assertEqual(assets[0].asset_name, "BP_MyCharacter")
        self.assertTrue(assets[0].file_path.endswith("BP_MyCharacter.uasset"))

    # 11. Blueprint Metadata Inspection
    def test_11_blueprint_metadata_inspection(self):
        info = self.bp_inspector.inspect_blueprint(str(self.bp_file), self.proj_dir)
        self.assertEqual(info.asset_name, "BP_MyCharacter")
        self.assertTrue(info.is_valid_uasset)
        self.assertTrue(info.has_binary_header)
        self.assertIn("Binary Asset", info.limitations)

    # 12. Proposal Data Model Serialization
    def test_12_proposal_serialization(self):
        prop = UnrealModificationProposal(
            file_path=str(self.header_file),
            operation=UnrealModificationOperation.ADD_INCLUDE,
            content='#include "Kismet/GameplayStatics.h"',
            expected_sha256="aabbcc",
        )
        d = prop.to_dict()
        self.assertEqual(d["operation"], "ADD_INCLUDE")
        prop2 = UnrealModificationProposal.from_dict(d)
        self.assertEqual(prop2.proposal_id, prop.proposal_id)
        self.assertEqual(prop2.operation, UnrealModificationOperation.ADD_INCLUDE)

    # 13. Proposal Validation - Valid Proposal
    def test_13_proposal_validation_valid(self):
        sha = hashlib.sha256(self.header_file.read_bytes()).hexdigest()
        prop = UnrealModificationProposal(
            file_path=str(self.header_file),
            operation=UnrealModificationOperation.ADD_INCLUDE,
            content='#include "Kismet/GameplayStatics.h"',
            expected_sha256=sha,
        )
        val = self.modifier.validate_proposal(prop)
        self.assertTrue(val["valid"])

    # 14. Proposal Validation - Workspace Boundary Confinement
    def test_14_proposal_outside_workspace(self):
        outside_file = Path(tempfile.gettempdir()) / "outside.h"
        outside_file.write_text("#pragma once", encoding="utf-8")
        try:
            with self.assertRaises(UnrealSafetyError) as ctx:
                self.safety.validate_source_file_path(outside_file)
            self.assertIn(ctx.exception.code, (UnrealErrorCode.PROJECT_NOT_AUTHORIZED, UnrealErrorCode.FILE_NOT_AUTHORIZED))
        finally:
            if outside_file.exists():
                outside_file.unlink()

    # 15. Proposal Validation - Path Traversal Rejection
    def test_15_proposal_path_traversal(self):
        traversal_path = str(self.source_dir / ".." / ".." / "Secret.h")
        with self.assertRaises(UnrealSafetyError) as ctx:
            self.safety.validate_source_file_path(traversal_path)
        self.assertIn(ctx.exception.code, (UnrealErrorCode.PATH_TRAVERSAL_DETECTED, UnrealErrorCode.FILE_NOT_AUTHORIZED))

    # 16. Proposal Validation - Protected File Rejection
    def test_16_protected_file_rejection(self):
        with self.assertRaises(UnrealSafetyError) as ctx:
            self.safety.validate_source_file_path(self.uproject_file)
        self.assertEqual(ctx.exception.code, UnrealErrorCode.PROTECTED_FILE_REJECTED)

    # 17. Proposal Validation - Non-Source File Extension Rejection
    def test_17_non_source_extension_rejection(self):
        exe_file = self.source_dir / "bad.exe"
        exe_file.write_bytes(b"MZ123")
        with self.assertRaises(UnrealSafetyError) as ctx:
            self.safety.validate_source_file_path(exe_file)
        self.assertEqual(ctx.exception.code, UnrealErrorCode.FILE_NOT_AUTHORIZED)

    # 18. Proposal Validation - File Size Limit
    def test_18_file_size_limit(self):
        large_file = self.source_dir / "Large.cpp"
        # Write 1 MB + 10 bytes
        large_file.write_bytes(b"A" * (MAX_SOURCE_FILE_BYTES + 10))
        with self.assertRaises(UnrealSafetyError) as ctx:
            self.safety.validate_source_file_path(large_file)
        self.assertEqual(ctx.exception.code, UnrealErrorCode.FILE_TOO_LARGE)

    # 19. Proposal Validation - Patch Size Limit
    def test_19_patch_size_limit(self):
        large_patch = "A" * (MAX_PATCH_BYTES + 10)
        with self.assertRaises(UnrealSafetyError) as ctx:
            self.safety.validate_patch_content(large_patch)
        self.assertEqual(ctx.exception.code, UnrealErrorCode.PATCH_TOO_LARGE)

    # 20. Proposal Validation - Line Change Limit
    def test_20_line_change_limit(self):
        many_lines_patch = "\n".join(["int x = 0;"] * (MAX_CHANGED_LINES + 5))
        with self.assertRaises(UnrealSafetyError) as ctx:
            self.safety.validate_patch_content(many_lines_patch)
        self.assertEqual(ctx.exception.code, UnrealErrorCode.TOO_MANY_LINES_CHANGED)

    # 21. Proposal Validation - Prohibited Tokens
    def test_21_prohibited_tokens(self):
        bad_patches = [
            "system(\"cmd.exe /c calc\");",
            "powershell -Command Invoke-Item",
            "import subprocess",
            "bash -c evil.sh",
        ]
        for patch_code in bad_patches:
            with self.assertRaises(UnrealSafetyError) as ctx:
                self.safety.validate_patch_content(patch_code)
            self.assertEqual(ctx.exception.code, UnrealErrorCode.PROHIBITED_TOKEN)

    # 22. Stale Target Pre-verification
    def test_22_stale_target_preverification(self):
        prop = UnrealModificationProposal(
            file_path=str(self.header_file),
            operation=UnrealModificationOperation.ADD_INCLUDE,
            content='#include "Kismet/GameplayStatics.h"',
            expected_sha256="stale_non_matching_hash_0000000000000000000000000000000000000000",
        )
        val = self.modifier.validate_proposal(prop)
        self.assertFalse(val["valid"])
        self.assertEqual(val["error_code"], UnrealErrorCode.STALE_TARGET.value)

    # 23. Pre-modification Backup Creation & Verification
    def test_23_backup_creation_and_verification(self):
        sha_before = hashlib.sha256(self.header_file.read_bytes()).hexdigest()
        prop = UnrealModificationProposal(
            file_path=str(self.header_file),
            operation=UnrealModificationOperation.ADD_INCLUDE,
            content='#include "Kismet/GameplayStatics.h"',
            expected_sha256=sha_before,
        )
        res = self.modifier.apply_modification(prop)
        self.assertTrue(res.success)
        self.assertTrue(res.backup_created)
        self.assertTrue(Path(res.backup_path).exists())
        self.assertEqual(res.original_sha256, sha_before)

    # 24. Operation: ADD_INCLUDE (before *.generated.h)
    def test_24_operation_add_include_before_generated(self):
        sha = hashlib.sha256(self.header_file.read_bytes()).hexdigest()
        prop = UnrealModificationProposal(
            file_path=str(self.header_file),
            operation=UnrealModificationOperation.ADD_INCLUDE,
            content='#include "Kismet/GameplayStatics.h"',
            expected_sha256=sha,
        )
        res = self.modifier.apply_modification(prop)
        self.assertTrue(res.success)
        new_content = self.header_file.read_text(encoding="utf-8")
        self.assertIn('#include "Kismet/GameplayStatics.h"', new_content)

        # Confirm *.generated.h is STILL after GameplayStatics.h
        gen_idx = new_content.find('#include "MyCharacter.generated.h"')
        stat_idx = new_content.find('#include "Kismet/GameplayStatics.h"')
        self.assertGreater(gen_idx, stat_idx)

    # 25. Operation: REMOVE_INCLUDE
    def test_25_operation_remove_include(self):
        sha = hashlib.sha256(self.header_file.read_bytes()).hexdigest()
        prop = UnrealModificationProposal(
            file_path=str(self.header_file),
            operation=UnrealModificationOperation.REMOVE_INCLUDE,
            content='#include "GameFramework/Character.h"',
            expected_sha256=sha,
        )
        res = self.modifier.apply_modification(prop)
        self.assertTrue(res.success)
        new_content = self.header_file.read_text(encoding="utf-8")
        self.assertNotIn('#include "GameFramework/Character.h"', new_content)

    # 26. Operation: ADD_METHOD
    def test_26_operation_add_method(self):
        sha = hashlib.sha256(self.cpp_file.read_bytes()).hexdigest()
        method_code = """
void AMyCharacter::Heal(float HealAmount)
{
\tHealth += HealAmount;
}
"""
        prop = UnrealModificationProposal(
            file_path=str(self.cpp_file),
            operation=UnrealModificationOperation.ADD_METHOD,
            content=method_code,
            target_symbol="AMyCharacter",
            expected_sha256=sha,
        )
        res = self.modifier.apply_modification(prop)
        self.assertTrue(res.success)
        new_content = self.cpp_file.read_text(encoding="utf-8")
        self.assertIn("void AMyCharacter::Heal(float HealAmount)", new_content)

    # 27. Operation: REPLACE_METHOD_BODY
    def test_27_operation_replace_method_body(self):
        sha = hashlib.sha256(self.cpp_file.read_bytes()).hexdigest()
        new_body = "\tHealth = FMath::Max(0.0f, Health - DamageAmount);\n"
        prop = UnrealModificationProposal(
            file_path=str(self.cpp_file),
            operation=UnrealModificationOperation.REPLACE_METHOD_BODY,
            content=new_body,
            target_symbol="TakeDamage",
            expected_sha256=sha,
        )
        res = self.modifier.apply_modification(prop)
        self.assertTrue(res.success)
        new_content = self.cpp_file.read_text(encoding="utf-8")
        self.assertIn("FMath::Max(0.0f, Health - DamageAmount)", new_content)

    # 28. Operation: REPLACE_SOURCE_RANGE
    def test_28_operation_replace_source_range(self):
        sha = hashlib.sha256(self.cpp_file.read_bytes()).hexdigest()
        prop = UnrealModificationProposal(
            file_path=str(self.cpp_file),
            operation=UnrealModificationOperation.REPLACE_SOURCE_RANGE,
            content="// Replaced comments range\n",
            start_line=1,
            end_line=2,
            expected_sha256=sha,
        )
        res = self.modifier.apply_modification(prop)
        self.assertTrue(res.success)
        new_content = self.cpp_file.read_text(encoding="utf-8")
        self.assertTrue(new_content.startswith("// Replaced comments range\n"))

    # 29. Operation: ADD_MEMBER_PROPERTY
    def test_29_operation_add_member_property(self):
        sha = hashlib.sha256(self.header_file.read_bytes()).hexdigest()
        prop_code = 'UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Stats")\nfloat Mana;\n'
        prop = UnrealModificationProposal(
            file_path=str(self.header_file),
            operation=UnrealModificationOperation.ADD_MEMBER_PROPERTY,
            content=prop_code,
            target_symbol="AMyCharacter",
            expected_sha256=sha,
        )
        res = self.modifier.apply_modification(prop)
        self.assertTrue(res.success)
        new_content = self.header_file.read_text(encoding="utf-8")
        self.assertIn("float Mana;", new_content)

    # 30. Operation: ADD_ENUM_ENTRY
    def test_30_operation_add_enum_entry(self):
        sha = hashlib.sha256(self.header_file.read_bytes()).hexdigest()
        prop = UnrealModificationProposal(
            file_path=str(self.header_file),
            operation=UnrealModificationOperation.ADD_ENUM_ENTRY,
            content='Dead UMETA(DisplayName = "Dead")',
            target_symbol="ECharacterState",
            expected_sha256=sha,
        )
        res = self.modifier.apply_modification(prop)
        self.assertTrue(res.success)
        new_content = self.header_file.read_text(encoding="utf-8")
        self.assertIn("Dead UMETA", new_content)

    # 31. Atomic Write Semantics
    def test_31_atomic_write_semantics(self):
        sha = hashlib.sha256(self.header_file.read_bytes()).hexdigest()
        prop = UnrealModificationProposal(
            file_path=str(self.header_file),
            operation=UnrealModificationOperation.ADD_INCLUDE,
            content='#include "Engine/Engine.h"',
            expected_sha256=sha,
        )
        res = self.modifier.apply_modification(prop)
        self.assertTrue(res.success)
        # Check no temporary files left in source directory
        temp_files = list(self.source_dir.glob("*.tmp*"))
        self.assertEqual(len(temp_files), 0)

    # 32. Post-Write Syntax and Hash Verification
    def test_32_post_write_syntax_and_hash_verification(self):
        sha = hashlib.sha256(self.cpp_file.read_bytes()).hexdigest()
        prop = UnrealModificationProposal(
            file_path=str(self.cpp_file),
            operation=UnrealModificationOperation.ADD_METHOD,
            content="void AMyCharacter::BadSyntax() {",  # Missing closing brace
            target_symbol="AMyCharacter",
            expected_sha256=sha,
        )
        res = self.modifier.apply_modification(prop)
        # Should fail verification and leave original file untouched!
        self.assertFalse(res.success)
        self.assertEqual(res.error_code, UnrealErrorCode.STRUCTURE_VALIDATION_FAILED.value)
        sha_after = hashlib.sha256(self.cpp_file.read_bytes()).hexdigest()
        self.assertEqual(sha, sha_after)

    # 33. Byte-for-byte Exact Rollback
    def test_33_exact_rollback_verification(self):
        original_bytes = self.header_file.read_bytes()
        original_sha = hashlib.sha256(original_bytes).hexdigest()

        prop = UnrealModificationProposal(
            file_path=str(self.header_file),
            operation=UnrealModificationOperation.ADD_INCLUDE,
            content='#include "Engine/World.h"',
            expected_sha256=original_sha,
        )
        res = self.modifier.apply_modification(prop)
        self.assertTrue(res.success)
        self.assertNotEqual(self.header_file.read_bytes(), original_bytes)

        # Rollback
        rb_res = self.modifier.rollback_modification(backup_id=res.backup_id)
        self.assertTrue(rb_res.success)
        self.assertEqual(self.header_file.read_bytes(), original_bytes)
        self.assertEqual(rb_res.new_sha256, original_sha)

    # 34. Corrupted Backup Rejection on Rollback
    def test_34_corrupted_backup_rejection(self):
        original_sha = hashlib.sha256(self.header_file.read_bytes()).hexdigest()
        prop = UnrealModificationProposal(
            file_path=str(self.header_file),
            operation=UnrealModificationOperation.ADD_INCLUDE,
            content='#include "Engine/World.h"',
            expected_sha256=original_sha,
        )
        res = self.modifier.apply_modification(prop)
        self.assertTrue(res.success)

        # Corrupt backup file
        backup_p = Path(res.backup_path)
        backup_p.write_bytes(b"CORRUPTED BACKUP CONTENT")

        rb_res = self.modifier.rollback_modification(backup_id=res.backup_id)
        self.assertFalse(rb_res.success)
        self.assertEqual(rb_res.error_code, UnrealErrorCode.HASH_VERIFICATION_FAILED.value)

    # 35. Safety Gate Payload Validation
    def test_35_safety_gate_payload_validation(self):
        payload = {
            "file_path": str(self.header_file),
            "operation": "ADD_INCLUDE",
            "content": "#include \"Kismet/KismetSystemLibrary.h\"",
            "expected_sha256": hashlib.sha256(self.header_file.read_bytes()).hexdigest(),
        }
        # Should pass
        self.safety.validate_proposal_payload(payload)

        # Invalid operation
        bad_payload = dict(payload)
        bad_payload["operation"] = "DROP_DATABASE"
        with self.assertRaises(UnrealSafetyError) as ctx:
            self.safety.validate_proposal_payload(bad_payload)
        self.assertEqual(ctx.exception.code, UnrealErrorCode.INVALID_OPERATION)

    # 36. Model Advisory Isolation
    def test_36_model_advisory_isolation(self):
        # Verify tool registry cannot execute arbitrary string commands
        with self.assertRaises(UnrealSafetyError) as ctx:
            self.safety.validate_tool_allowed("unreal.execute_arbitrary_code")
        self.assertEqual(ctx.exception.code, UnrealErrorCode.TOOL_NOT_ALLOWED)

    # 37. Audit Logging and Sensitive Token Redaction
    def test_37_audit_logging_and_sensitive_redaction(self):
        secret_line = 'FString api_key = "sk-ant-api03-abcdefghijklmnop1234567890"; password=supersecretpassword123'
        redacted = redact_sensitive_data(secret_line)
        self.assertNotIn("sk-ant-api03-abcdefghijklmnop1234567890", redacted)
        self.assertNotIn("supersecretpassword123", redacted)
        self.assertIn("***REDACTED***", redacted)

    # 38. Duplicate Tool Check Across All 44 Tools
    def test_38_no_duplicate_tools(self):
        tools = self.registry.get_registered_tools()
        self.assertEqual(len(tools), 44)
        self.assertEqual(len(tools), len(set(tools)))
        self.assertEqual(len(ALL_ALLOWED_UNREAL_TOOLS), 44)

    # 39. Static Safety Check (shell=True == 0)
    def test_39_no_shell_true(self):
        unreal_files = glob.glob(str(Path(__file__).parent.parent / "app" / "agent" / "unreal_*.py"))
        self.assertGreaterEqual(len(unreal_files), 6)
        for fpath in unreal_files:
            text = Path(fpath).read_text(encoding="utf-8")
            self.assertNotIn("shell=True", text, f"Violation: shell=True found in {fpath}")

    # 40. Tool Registry Dispatch for All 14 Phase 4 Tools
    def test_40_tool_registry_dispatch_all_phase4_tools(self):
        sha_hdr = hashlib.sha256(self.header_file.read_bytes()).hexdigest()

        # 1. inspect_cpp_source
        r1 = self.registry.execute_tool("unreal.inspect_cpp_source", file_path=str(self.header_file))
        self.assertTrue(r1.success)

        # 2. list_cpp_symbols
        r2 = self.registry.execute_tool("unreal.list_cpp_symbols", file_path=str(self.header_file))
        self.assertTrue(r2.success)

        # 3. inspect_cpp_class
        r3 = self.registry.execute_tool("unreal.inspect_cpp_class", file_path=str(self.header_file), class_name="AMyCharacter")
        self.assertTrue(r3.success)

        # 4. inspect_cpp_method
        r4 = self.registry.execute_tool("unreal.inspect_cpp_method", file_path=str(self.header_file), method_name="TakeDamage")
        self.assertTrue(r4.success)

        # 5. find_cpp_symbol
        r5 = self.registry.execute_tool("unreal.find_cpp_symbol", symbol_name="AMyCharacter", project_path=str(self.proj_dir))
        self.assertTrue(r5.success)
        self.assertGreaterEqual(r5.data["count"], 1)

        # 6. inspect_reflection_metadata
        r6 = self.registry.execute_tool("unreal.inspect_reflection_metadata", file_path=str(self.header_file))
        self.assertTrue(r6.success)

        # 7. inspect_inheritance
        r7 = self.registry.execute_tool("unreal.inspect_inheritance", file_path=str(self.header_file))
        self.assertTrue(r7.success)

        # 8. list_blueprint_assets
        r8 = self.registry.execute_tool("unreal.list_blueprint_assets", project_path=str(self.proj_dir))
        self.assertTrue(r8.success)

        # 9. inspect_blueprint_metadata
        r9 = self.registry.execute_tool("unreal.inspect_blueprint_metadata", asset_path=str(self.bp_file), project_path=str(self.proj_dir))
        self.assertTrue(r9.success)

        # 10. validate_cpp_change
        r10 = self.registry.execute_tool(
            "unreal.validate_cpp_change",
            file_path=str(self.header_file),
            operation="ADD_INCLUDE",
            content='#include "Engine/Engine.h"',
            expected_sha256=sha_hdr,
        )
        self.assertTrue(r10.success)

        # 11. propose_cpp_change
        r11 = self.registry.execute_tool(
            "unreal.propose_cpp_change",
            file_path=str(self.header_file),
            operation="ADD_INCLUDE",
            content='#include "Engine/Engine.h"',
            expected_sha256=sha_hdr,
        )
        self.assertTrue(r11.success)

        # 12. apply_cpp_change
        r12 = self.registry.execute_tool(
            "unreal.apply_cpp_change",
            file_path=str(self.header_file),
            operation="ADD_INCLUDE",
            content='#include "Engine/Engine.h"',
            expected_sha256=sha_hdr,
        )
        self.assertTrue(r12.success)
        new_sha = r12.data["new_sha256"]

        # 13. verify_source_change
        r13 = self.registry.execute_tool(
            "unreal.verify_source_change",
            file_path=str(self.header_file),
            expected_sha256=new_sha,
            check_syntax=True,
        )
        self.assertTrue(r13.success)

        # 14. rollback_cpp_change
        r14 = self.registry.execute_tool(
            "unreal.rollback_cpp_change",
            backup_id=r12.data["backup_id"],
        )
        self.assertTrue(r14.success)
        self.assertEqual(hashlib.sha256(self.header_file.read_bytes()).hexdigest(), sha_hdr)


if __name__ == "__main__":
    unittest.main()
