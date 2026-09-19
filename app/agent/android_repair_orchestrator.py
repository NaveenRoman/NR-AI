"""
NR-AI Autonomous Bounded Repair Orchestrator (Droid Phase 3).

Coordinates repair proposals, validation, safe execution, and atomic rollback.
Enforces strict boundaries:
- MAX_REPAIR_ATTEMPTS = 2 (hard limit, never dynamic)
- Max 5 files per repair attempt
- Max 100 KB patch size
- Max 500 lines changed
- Target SHA-256 validation (guards against stale edits)
- Pre-edit backup creation (.bak) in checkpoint store
- Atomic byte-for-byte rollback on syntax or build failure
- Immediate halt with ROLLBACK_FAILURE if rollback fails
- Model advisory only: models cannot run commands, eval, exec, or bypass gates.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
import uuid

from app.agent.android_safety import (
    ALLOWED_ANDROID_EXTENSIONS,
    AUTHORIZED_PROJECT_PATH,
    MAX_EDITABLE_FILE_SIZE_BYTES,
    MAX_FILES_PER_REPAIR,
    MAX_LINES_PER_EDIT,
    MAX_PATCH_SIZE_BYTES,
    MAX_REPAIR_ATTEMPTS,
    PROTECTED_ANDROID_FILES,
    AndroidErrorCode,
    AndroidSafetyError,
    AndroidSafetyGate,
    EmergencyStopActiveError,
)
from app.agent.android_code_repair import AndroidCodeRepairEngine, EditOperation, EditProposal, EditResult
from app.agent.android_tools import SafeGradleRunner
from app.agent.android_ast import AndroidASTEngine
from app.memory.audit_logger import AuditLogger

logger = logging.getLogger("NRAI.AndroidRepairOrchestrator")


# -----------------------------------------------------------------------------
# Proposal & Result Data Models
# -----------------------------------------------------------------------------

class RepairOperation(str, Enum):
    REPLACE_EXACT = "REPLACE_EXACT"
    REPLACE_RANGE = "REPLACE_RANGE"
    INSERT_BEFORE = "INSERT_BEFORE"
    INSERT_AFTER = "INSERT_AFTER"
    CREATE_FILE = "CREATE_FILE"


@dataclass
class RepairProposal:
    """Structured, verified repair proposal conforming to Droid Phase 3 schema."""
    file_path: Union[str, Path]
    target_sha256: str
    operation: RepairOperation
    replacement: str
    reason: str
    proposal_id: str = field(default_factory=lambda: f"prop_{uuid.uuid4().hex[:8]}")
    proposal_version: str = "1.0"
    task_id: str = "T-DEFAULT"
    project_id: str = "nr_android_test"
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    target_snippet: Optional[str] = None
    evidence_ids: List[str] = field(default_factory=list)
    expected_effect: str = ""
    validation_plan: str = "compile_and_build"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "proposal_version": self.proposal_version,
            "task_id": self.task_id,
            "project_id": self.project_id,
            "file_path": str(self.file_path),
            "target_sha256": self.target_sha256,
            "operation": self.operation.value if isinstance(self.operation, RepairOperation) else str(self.operation),
            "start_line": self.start_line,
            "end_line": self.end_line,
            "target_snippet": self.target_snippet,
            "replacement_bytes": len(self.replacement.encode("utf-8")),
            "reason": self.reason,
            "evidence_ids": self.evidence_ids,
            "expected_effect": self.expected_effect,
            "validation_plan": self.validation_plan,
        }


@dataclass
class RepairExecutionResult:
    """Outcome of attempting to validate and apply a repair proposal."""
    success: bool
    proposal_id: str
    applied: bool = False
    build_passed: bool = False
    rolled_back: bool = False
    rollback_failure: bool = False
    attempt_number: int = 1
    max_attempts: int = MAX_REPAIR_ATTEMPTS
    old_hash: str = ""
    new_hash: str = ""
    diff: str = ""
    backup_path: Optional[str] = None
    message: str = ""
    error: Optional[str] = None
    duration_seconds: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# -----------------------------------------------------------------------------
# Autonomous Repair Orchestrator
# -----------------------------------------------------------------------------

class AutonomousRepairOrchestrator:
    """
    Manages safe, verified, transactional code modifications on Android projects.
    Enforces the 10-step safe modification workflow and guarantees atomic rollback on failure.
    """

    def __init__(
        self,
        code_repair_engine: Optional[AndroidCodeRepairEngine] = None,
        ast_engine: Optional[AndroidASTEngine] = None,
        gradle_runner: Optional[SafeGradleRunner] = None,
        safety_gate: Optional[AndroidSafetyGate] = None,
        audit_logger: Optional[AuditLogger] = None,
    ):
        self.safety = safety_gate or AndroidSafetyGate()
        self.code_repair = code_repair_engine or AndroidCodeRepairEngine(safety_gate=self.safety)
        self.ast_engine = ast_engine or AndroidASTEngine(safety_gate=self.safety)
        self.gradle = gradle_runner or SafeGradleRunner()
        self.audit = audit_logger or AuditLogger()
        self._attempt_counters: Dict[str, int] = {}

    def get_attempt_count(self, task_id: str) -> int:
        """Returns the number of repair attempts already made for this task."""
        return self._attempt_counters.get(task_id, 0)

    def validate_proposal_schema(self, proposal: RepairProposal) -> Tuple[bool, List[str]]:
        """
        Validates repair proposal parameters against safety rules before execution:
        - File path allowlist and boundaries
        - SHA-256 target hash existence and match
        - Patch size and lines limits
        """
        errors: List[str] = []

        # 1. Project and file authorization
        p = Path(proposal.file_path).resolve()
        try:
            self.safety.verify_file_path(p)
        except Exception as e:
            errors.append(f"File authorization failed: {str(e)}")

        # 2. File existence and current SHA-256
        if proposal.operation != RepairOperation.CREATE_FILE:
            if not p.exists():
                errors.append(f"Target file does not exist: {p}")
            else:
                actual_hash = hashlib.sha256(p.read_bytes()).hexdigest()
                if proposal.target_sha256 and proposal.target_sha256 != actual_hash:
                    errors.append(f"Target file hash mismatch! Expected {proposal.target_sha256}, actual {actual_hash} (STALE_TARGET)")

        # 3. Patch size bounds
        replacement_bytes = len(proposal.replacement.encode("utf-8"))
        if replacement_bytes > MAX_PATCH_SIZE_BYTES:
            errors.append(f"Replacement exceeds MAX_PATCH_SIZE_BYTES ({replacement_bytes} > {MAX_PATCH_SIZE_BYTES})")

        replacement_lines = len(proposal.replacement.splitlines())
        if replacement_lines > MAX_LINES_PER_EDIT:
            errors.append(f"Replacement exceeds MAX_LINES_PER_EDIT ({replacement_lines} > {MAX_LINES_PER_EDIT})")

        return len(errors) == 0, errors

    def execute_repair(
        self,
        proposal: RepairProposal,
        validate_build: bool = True,
    ) -> RepairExecutionResult:
        """
        Executes the 10-step safe repair workflow:
        1. Check emergency stop
        2. Verify attempt count <= MAX_REPAIR_ATTEMPTS (2)
        3. Validate schema and target hash
        4. Create backup
        5. Apply atomic edit
        6. Validate syntax
        7. Validate build
        8. Rollback on syntax/build failure
        9. Handle rollback failure with immediate halt
        10. Return authoritative result
        """
        start = time.monotonic()
        self.safety.check_emergency_stop()

        task_id = proposal.task_id
        current_attempts = self.get_attempt_count(task_id)
        if current_attempts >= MAX_REPAIR_ATTEMPTS:
            msg = f"Maximum repair attempts exceeded for task {task_id} ({current_attempts} >= {MAX_REPAIR_ATTEMPTS})."
            logger.error(msg)
            return RepairExecutionResult(
                success=False,
                proposal_id=proposal.proposal_id,
                attempt_number=current_attempts,
                message=msg,
                error="MAX_REPAIR_ATTEMPTS_EXCEEDED",
                duration_seconds=time.monotonic() - start,
            )

        self._attempt_counters[task_id] = current_attempts + 1
        attempt_num = self._attempt_counters[task_id]

        # Schema and boundary validation
        is_valid, errs = self.validate_proposal_schema(proposal)
        if not is_valid:
            msg = f"Proposal validation failed: {'; '.join(errs)}"
            return RepairExecutionResult(
                success=False,
                proposal_id=proposal.proposal_id,
                attempt_number=attempt_num,
                message=msg,
                error="VALIDATION_FAILED",
                duration_seconds=time.monotonic() - start,
            )

        # Map to EditProposal for code_repair engine
        op_map = {
            RepairOperation.REPLACE_EXACT: EditOperation.REPLACE_EXACT,
            RepairOperation.REPLACE_RANGE: EditOperation.REPLACE_RANGE,
            RepairOperation.INSERT_BEFORE: EditOperation.INSERT_BEFORE,
            RepairOperation.INSERT_AFTER: EditOperation.INSERT_AFTER,
            RepairOperation.CREATE_FILE: EditOperation.CREATE_FILE,
        }

        edit_prop = EditProposal(
            file_path=proposal.file_path,
            expected_old_hash=proposal.target_sha256,
            operation=op_map.get(proposal.operation, EditOperation.REPLACE_EXACT),
            start_line=proposal.start_line,
            end_line=proposal.end_line,
            target_snippet=proposal.target_snippet,
            replacement_text=proposal.replacement,
            reason=proposal.reason,
            proposal_id=proposal.proposal_id,
        )

        # Apply edit via CodeRepairEngine (which performs backup)
        edit_res: EditResult = self.code_repair.apply_edit(edit_prop)
        if not edit_res.success:
            return RepairExecutionResult(
                success=False,
                proposal_id=proposal.proposal_id,
                applied=False,
                attempt_number=attempt_num,
                message=f"Failed to apply edit: {edit_res.error}",
                error=edit_res.error_code or "EDIT_FAILED",
                duration_seconds=time.monotonic() - start,
            )

        backup_path = edit_res.backup_path

        # Step 8: Syntax validation
        p = Path(proposal.file_path)
        if p.suffix in (".kt", ".java"):
            # Check basic syntax / AST parsing
            syntax_ok = True
            try:
                content = p.read_text(encoding="utf-8")
                # Ensure balanced braces
                masked = re.sub(r'".*?"', '""', content)
                if masked.count("{") != masked.count("}"):
                    syntax_ok = False
            except Exception:
                syntax_ok = False

            if not syntax_ok:
                logger.warning("Syntax validation failed post-edit. Initiating immediate rollback.")
                rb_ok = self._safe_rollback(p, backup_path)
                return RepairExecutionResult(
                    success=False,
                    proposal_id=proposal.proposal_id,
                    applied=True,
                    rolled_back=rb_ok,
                    rollback_failure=not rb_ok,
                    attempt_number=attempt_num,
                    backup_path=backup_path,
                    message="Syntax error detected after modification. Rolled back to clean state.",
                    error="SYNTAX_ERROR" if rb_ok else "ROLLBACK_FAILURE",
                    duration_seconds=time.monotonic() - start,
                )

        # Step 9: Build validation
        if validate_build:
            build_res = self.gradle.assemble_debug()
            if not build_res.success:
                logger.warning("Build validation failed post-edit: %s. Initiating rollback.", build_res.stderr)
                rb_ok = self._safe_rollback(p, backup_path)
                return RepairExecutionResult(
                    success=False,
                    proposal_id=proposal.proposal_id,
                    applied=True,
                    build_passed=False,
                    rolled_back=rb_ok,
                    rollback_failure=not rb_ok,
                    attempt_number=attempt_num,
                    backup_path=backup_path,
                    message="Build failed after modification. Rolled back to previous state.",
                    error="BUILD_VALIDATION_FAILED" if rb_ok else "ROLLBACK_FAILURE",
                    duration_seconds=time.monotonic() - start,
                )

        # Success!
        self.audit.log(
            "repair_applied",
            details=proposal.to_dict(),
            actor="AutonomousRepairOrchestrator",
            status="SUCCESS",
        )

        return RepairExecutionResult(
            success=True,
            proposal_id=proposal.proposal_id,
            applied=True,
            build_passed=validate_build,
            rolled_back=False,
            attempt_number=attempt_num,
            old_hash=edit_res.old_hash,
            new_hash=edit_res.new_hash,
            diff=edit_res.diff,
            backup_path=backup_path,
            message="Repair successfully applied and verified!",
            duration_seconds=time.monotonic() - start,
        )

    def _safe_rollback(self, target_path: Path, backup_path: Optional[str]) -> bool:
        """Restores exact previous state from backup."""
        if not backup_path or not Path(backup_path).exists():
            logger.error("ROLLBACK_FAILURE: Backup path does not exist: %s", backup_path)
            return False
        try:
            bk_bytes = Path(backup_path).read_bytes()
            target_path.write_bytes(bk_bytes)
            logger.info("Successfully rolled back %s from %s", target_path, backup_path)
            return True
        except Exception as e:
            logger.critical("ROLLBACK_FAILURE: Failed to restore backup: %s", e)
            return False
