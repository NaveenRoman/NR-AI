"""
NR-AI Visual Studio Autonomous Code Repair Engine (Step 7).

Coordinates safe, bounded autonomous repair of C#, VB, F#, and project files.
Guarantees:
- Models remain strictly ADVISORY with zero direct tool or write authority.
- Proposal schema validation & SHA-256 target verification.
- Pre-mutation backup checkpoints in scratch/vs_checkpoints/.
- Atomic writes & immediate byte-for-byte rollbacks on failure.
- Maximum 2 repair attempts strictly enforced.
- Sensitive data redaction.
"""

from dataclasses import dataclass, field
import difflib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
import uuid

from app.agent.vs_safety import (
    VSSafetyGate,
    VSErrorCode,
    VSSafetyError,
    MAX_FILES_CHANGED,
    MAX_PATCH_SIZE_BYTES,
    MAX_LINES_CHANGED,
    MAX_REPAIR_ATTEMPTS,
    GLOBAL_WORKSPACE_ROOT,
    redact_sensitive_data,
)
from app.agent.vs_error_analyzer import VSErrorAnalyzer, VSBuildError
from app.agent.vs_tools import SafeMSBuildRunner, VSToolResult
from app.agent.model_router import ModelRouter, ModelCapability
from app.memory.audit_logger import AuditLogger

logger = logging.getLogger("NRAI.VSCodeRepair")

DEFAULT_CHECKPOINT_DIR = Path("C:/NR-AI/scratch/vs_checkpoints").resolve()
redact_sensitive_vs_data = redact_sensitive_data


class VSEditProposal:
    """A structured, strictly validated proposal to edit a file."""

    def __init__(
        self,
        file_path: Union[str, Path, None] = None,
        expected_sha256: Optional[str] = None,
        start_line: int = 1,
        end_line: int = 0,
        replacement_text: Optional[str] = None,
        rationale: str = "",
        target_sha256: Optional[str] = None,
        new_content: Optional[str] = None,
        explanation: Optional[str] = None,
        target_file: Optional[Union[str, Path]] = None,
        expected_file_hash: Optional[str] = None,
        proposal_version: str = "1.0",
        proposal_id: Optional[str] = None,
        bounded_context: Optional[Dict[str, Any]] = None,
        reason: Optional[str] = None,
        diagnostics_addressed: Optional[List[str]] = None,
        replacement: Optional[str] = None,
        target_file_sha256: Optional[str] = None,
        replacement_code: Optional[str] = None,
        repair_id: Optional[str] = None,
        diagnostic_code: Optional[str] = None,
        original_code: Optional[str] = None,
    ):
        f_p = target_file if target_file is not None else file_path
        self.file_path = str(f_p) if f_p else ""
        self.target_file = self.file_path

        h = expected_file_hash or expected_sha256 or target_sha256 or target_file_sha256 or ""
        self.expected_sha256 = str(h).strip()
        self.expected_file_hash = self.expected_sha256
        self.target_sha256 = self.expected_sha256

        self.start_line = int(start_line) if start_line is not None else 1
        self.end_line = int(end_line) if end_line is not None else 0

        repl = replacement_text if replacement_text is not None else (new_content if new_content is not None else (replacement_code if replacement_code is not None else (replacement or "")))
        self.replacement_text = str(repl)
        self.new_content = self.replacement_text
        self.replacement = self.replacement_text

        r = reason if reason is not None else (rationale or explanation or "")
        self.rationale = str(r)
        self.reason = self.rationale
        self.explanation = self.rationale

        self.proposal_version = str(proposal_version)
        self.proposal_id = proposal_id or f"prop_{uuid.uuid4().hex[:8]}"
        self.bounded_context = bounded_context or {}
        self.diagnostics_addressed = list(diagnostics_addressed or [])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposal_version": self.proposal_version,
            "proposal_id": self.proposal_id,
            "file_path": self.file_path,
            "target_file": self.target_file,
            "expected_sha256": self.expected_sha256,
            "expected_file_hash": self.expected_file_hash,
            "target_sha256": self.expected_sha256,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "replacement_text": self.replacement_text,
            "new_content": self.replacement_text,
            "replacement": self.replacement_text,
            "rationale": self.rationale,
            "reason": self.reason,
            "explanation": self.rationale,
            "bounded_context": self.bounded_context,
            "diagnostics_addressed": self.diagnostics_addressed,
        }


@dataclass
class VSEditBatch:
    """A batch of edit proposals representing an atomic repair transaction."""
    proposals: List[VSEditProposal] = field(default_factory=list)
    repair_id: str = field(default_factory=lambda: f"rep_{int(time.time()*1000)}")
    attempt_number: int = 1
    checkpoint_id: str = ""

    def __post_init__(self):
        if not self.checkpoint_id:
            self.checkpoint_id = self.repair_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repair_id": self.repair_id,
            "checkpoint_id": self.checkpoint_id,
            "attempt_number": self.attempt_number,
            "proposal_count": len(self.proposals),
            "proposals": [p.to_dict() for p in self.proposals],
        }


@dataclass
class VSRepairResult:
    """Complete report of a repair transaction."""
    success: bool = False
    repair_id: str = ""
    attempts: int = 1
    applied_files: List[str] = field(default_factory=list)
    diff: str = ""
    error_code: Optional[str] = None
    summary: str = ""
    rolled_back: bool = False
    rebuild_output: Optional[Dict[str, Any]] = None
    checkpoint_id: str = ""
    stale_target_detected: bool = False
    error_message: Optional[str] = None
    attempt_number: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "repair_id": self.repair_id,
            "attempts": self.attempts,
            "applied_files": self.applied_files,
            "diff": self.diff,
            "error_code": self.error_code,
            "summary": self.summary,
            "rolled_back": self.rolled_back,
            "rebuild_output": self.rebuild_output,
            "checkpoint_id": self.checkpoint_id,
            "stale_target_detected": self.stale_target_detected,
            "error_message": self.error_message,
            "attempt_number": self.attempt_number,
        }


def extract_bounded_context(
    error: VSBuildError,
    project_root: Optional[Path] = None,
    max_lines_context: int = 30,
) -> Dict[str, Any]:
    """
    Extracts bounded source code context surrounding an error location (+- 30 lines).
    Redacts sensitive content and computes current file SHA-256 hash.
    Never exposes whole project or unnecessary files.
    """
    root = Path(project_root or GLOBAL_WORKSPACE_ROOT).resolve()
    if not error.file_path:
        return {
            "file_path": None,
            "target_file": None,
            "relative_path": None,
            "error_line": None,
            "start_line": 1,
            "end_line": 1,
            "context_code": "",
            "file_sha256": "",
            "expected_file_hash": "",
            "total_lines": 0,
        }

    target_path = Path(error.file_path)
    if not target_path.is_absolute():
        target_path = (root / target_path).resolve()

    if not target_path.exists() or not target_path.is_file():
        return {
            "file_path": str(target_path),
            "target_file": str(target_path),
            "relative_path": str(target_path.name),
            "error_line": error.line,
            "start_line": 1,
            "end_line": 1,
            "context_code": "",
            "file_sha256": "",
            "expected_file_hash": "",
            "total_lines": 0,
        }

    content = target_path.read_text(encoding="utf-8", errors="replace")
    file_sha256 = VSSafetyGate.compute_sha256(target_path)
    lines = content.splitlines()
    total_lines = len(lines)

    err_line = error.line if error.line and error.line >= 1 else 1
    start_line = max(1, err_line - max_lines_context)
    end_line = min(total_lines, err_line + max_lines_context)

    # 1-indexed line extraction
    context_slice = lines[start_line - 1: end_line]
    numbered_lines = [f"{start_line + i:4d} | {line}" for i, line in enumerate(context_slice)]
    raw_context = "\n".join(numbered_lines)
    sanitized_context = redact_sensitive_data(raw_context)

    try:
        rel_path = str(target_path.relative_to(root)).replace("\\", "/")
    except ValueError:
        rel_path = target_path.name

    return {
        "file_path": str(target_path),
        "target_file": str(target_path),
        "relative_path": rel_path,
        "error_line": err_line,
        "start_line": start_line,
        "end_line": end_line,
        "context_code": sanitized_context,
        "raw_snippet": "\n".join(context_slice),
        "file_sha256": file_sha256,
        "expected_file_hash": file_sha256,
        "total_lines": total_lines,
    }


def build_model_repair_prompt(error: VSBuildError, context: Dict[str, Any]) -> Tuple[str, str]:
    """
    Constructs strict JSON system and user prompts for advisory repair proposal.
    Enforces strict output JSON schema:
    {
      "proposal_version": "1.0",
      "proposal_id": "vs_prop_xxxx",
      "target_file": "path/to/File.cs",
      "expected_file_hash": "<current file SHA-256>",
      "start_line": 10,
      "end_line": 12,
      "replacement_text": "...",
      "reason": "Fix missing semicolon or undeclared identifier",
      "diagnostics_addressed": ["CS1002"]
    }
    """
    system_prompt = (
        "You are an expert Visual Studio / .NET code repair advisor for NR-AI.\n"
        "Your role is STRICTLY ADVISORY. You do NOT have shell, filesystem, or tool execution privileges.\n"
        "You cannot run msbuild, dotnet, vstest, or arbitrary commands.\n"
        "You must analyze the compiler diagnostic and propose a minimal, deterministic code fix.\n\n"
        "RULES:\n"
        "1. Output ONLY a single valid JSON object. Do not include markdown code fences outside the JSON.\n"
        "2. The JSON MUST follow this exact schema:\n"
        "{\n"
        '  "proposal_version": "1.0",\n'
        '  "proposal_id": "vs_prop_001",\n'
        '  "target_file": "relative/path/to/File.cs",\n'
        '  "expected_file_hash": "<current file SHA-256>",\n'
        '  "start_line": <1-based start line of code to replace>,\n'
        '  "end_line": <1-based end line of code to replace>,\n'
        '  "replacement_text": "<exact replacement code string>",\n'
        '  "reason": "Why this fixes the compilation error",\n'
        '  "diagnostics_addressed": ["CSxxxx"]\n'
        "}\n"
        "3. Replace ONLY the minimal necessary lines. Do NOT rewrite entire files.\n"
        "4. The expected_file_hash MUST match the file SHA-256 provided in the context.\n"
        "5. Never include secrets, API keys, credentials, or dangerous operations.\n"
    )

    user_prompt = (
        f"Visual Studio Build Error Classification: {error.category.value}\n"
        f"Error Code: {error.error_code}\n"
        f"Diagnostic Message: {redact_sensitive_data(error.message)}\n"
        f"Diagnosis: {error.diagnosis}\n"
        f"Target File: {context.get('relative_path') or context.get('file_path')}\n"
        f"File SHA-256: {context.get('file_sha256')}\n"
        f"Error Line: {context.get('error_line')}\n\n"
        f"Surrounding Code Context (Lines {context.get('start_line')} to {context.get('end_line')}):\n"
        f"```\n{context.get('context_code')}\n```\n\n"
        "Please provide a JSON repair proposal to resolve this compiler error."
    )

    return system_prompt, user_prompt


def parse_model_repair_response(raw_text: str) -> Dict[str, Any]:
    """
    Parses and strictly validates the model repair JSON response.
    Raises VSSafetyError(VSErrorCode.MALFORMED_PROPOSAL, ...) on failure.
    """
    if not raw_text or not str(raw_text).strip():
        raise VSSafetyError(
            VSErrorCode.MALFORMED_PROPOSAL,
            "Model returned empty response for repair proposal.",
        )

    text = str(raw_text).strip()
    if "```" in text:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if m:
            text = m.group(1).strip()

    try:
        data = json.loads(text)
    except Exception as e:
        raise VSSafetyError(
            VSErrorCode.MALFORMED_PROPOSAL,
            f"Failed to parse model response as JSON: {e}",
        )

    if not isinstance(data, dict):
        raise VSSafetyError(
            VSErrorCode.MALFORMED_PROPOSAL,
            "Model response must be a JSON object.",
        )

    # Reject direct tool or shell commands
    for forbidden_key in ("tool_call", "command", "tool", "execute", "shell", "exec"):
        if forbidden_key in data:
            raise VSSafetyError(
                VSErrorCode.MALFORMED_PROPOSAL,
                f"Model proposal attempted direct tool execution via '{forbidden_key}'. Rejected.",
            )

    return data



class VSCodeRepairEngine:
    """
    Orchestrates deterministic Visual Studio code repair workflows.
    Validates proposals, manages backups, performs atomic writes, and executes rollbacks.
    """

    def __init__(
        self,
        safety_gate: Optional[VSSafetyGate] = None,
        msbuild_runner: Optional[SafeMSBuildRunner] = None,
        error_analyzer: Optional[VSErrorAnalyzer] = None,
        model_router: Optional[ModelRouter] = None,
        audit_logger: Optional[AuditLogger] = None,
        checkpoint_dir: Optional[Path] = None,
    ):
        self.safety = safety_gate or VSSafetyGate()
        self.runner = msbuild_runner or SafeMSBuildRunner(safety_gate=self.safety)
        self.analyzer = error_analyzer or VSErrorAnalyzer()
        self.router = model_router or ModelRouter()
        self.audit = audit_logger or AuditLogger()
        self.checkpoint_dir = Path(checkpoint_dir or DEFAULT_CHECKPOINT_DIR).resolve()
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self.max_attempts = MAX_REPAIR_ATTEMPTS

    # -------------------------------------------------------------------------
    # Checkpoint & Backup Management
    # -------------------------------------------------------------------------

    def create_checkpoint(self, file_path: Path, repair_id: str) -> Path:
        """Saves a byte-for-byte backup of a file before modification."""
        if not file_path.exists():
            raise VSSafetyError(VSErrorCode.FILE_NOT_AUTHORIZED, f"Cannot checkpoint non-existent file: {file_path}")

        ts = int(time.time() * 1000)
        bk_name = f"{file_path.name}.{repair_id}.{ts}.bak"
        bk_path = self.checkpoint_dir / bk_name
        shutil.copy2(file_path, bk_path)
        logger.info(f"[VSCodeRepair] Created checkpoint for '{file_path.name}' at '{bk_path}'")
        return bk_path

    def restore_checkpoint(self, backup_path: Path, target_file: Path) -> None:
        """Restores target file from pre-edit checkpoint."""
        if not backup_path.exists():
            raise VSSafetyError(VSErrorCode.ROLLBACK_FAILED, f"Backup file '{backup_path}' does not exist.")
        shutil.copy2(backup_path, target_file)
        logger.info(f"[VSCodeRepair] Restored '{target_file.name}' from checkpoint '{backup_path.name}'.")

    # -------------------------------------------------------------------------
    # Proposal Validation
    # -------------------------------------------------------------------------

    def validate_proposal_schema(self, raw_proposal: Dict[str, Any]) -> VSEditProposal:
        """Validates that proposal strictly conforms to the expected schema."""
        if not isinstance(raw_proposal, dict):
            raise VSSafetyError(VSErrorCode.MALFORMED_PROPOSAL, "Proposal must be a dictionary.")

        for forbidden in ("tool_call", "command", "tool", "execute", "shell", "exec"):
            if forbidden in raw_proposal:
                raise VSSafetyError(
                    VSErrorCode.MALFORMED_PROPOSAL,
                    f"Proposal attempted direct tool execution via '{forbidden}'. Rejected.",
                )

        f_path = str(raw_proposal.get("target_file") or raw_proposal.get("file_path") or "").strip()
        if not f_path:
            raise VSSafetyError(VSErrorCode.MALFORMED_PROPOSAL, "Proposal missing target file path.")

        expected_sha = str(raw_proposal.get("expected_file_hash") or raw_proposal.get("expected_sha256") or raw_proposal.get("target_sha256") or "").strip()
        if not expected_sha:
            raise VSSafetyError(VSErrorCode.MALFORMED_PROPOSAL, "Proposal missing expected file hash.")

        repl_text = raw_proposal.get("replacement_text")
        if repl_text is None:
            repl_text = raw_proposal.get("new_content")
        if repl_text is None:
            repl_text = raw_proposal.get("replacement")
        if repl_text is None:
            raise VSSafetyError(VSErrorCode.MALFORMED_PROPOSAL, "Proposal missing replacement text.")
        repl_text = str(repl_text)

        try:
            s_line = int(raw_proposal.get("start_line", 1))
            e_line = int(raw_proposal.get("end_line", 0))
        except (ValueError, TypeError):
            raise VSSafetyError(VSErrorCode.MALFORMED_PROPOSAL, "Line numbers must be integers.")

        if s_line < 1 or (e_line > 0 and e_line < s_line):
            raise VSSafetyError(
                VSErrorCode.EDIT_VALIDATION_FAILED,
                f"Invalid line range: {s_line}..{e_line}",
            )

        # Enforce patch limits & prohibited tokens
        self.safety.validate_patch_content(repl_text)

        # Path validation
        validated_file = self.safety.validate_file_path(f_path, check_writable=True)

        return VSEditProposal(
            file_path=str(validated_file),
            target_file=str(validated_file),
            expected_sha256=expected_sha,
            expected_file_hash=expected_sha,
            start_line=s_line,
            end_line=e_line,
            replacement_text=repl_text,
            rationale=str(raw_proposal.get("reason") or raw_proposal.get("rationale") or raw_proposal.get("explanation") or ""),
            proposal_version=str(raw_proposal.get("proposal_version", "1.0")),
            proposal_id=str(raw_proposal.get("proposal_id") or f"prop_{uuid.uuid4().hex[:8]}"),
            bounded_context=raw_proposal.get("bounded_context") or {},
            diagnostics_addressed=raw_proposal.get("diagnostics_addressed") or [],
        )

    def validate_batch(self, batch: VSEditBatch) -> None:
        """Validates edit batch boundaries."""
        self.safety.validate_batch_file_count(len(batch.proposals))
        total_patch_bytes = sum(len(p.replacement_text.encode("utf-8")) for p in batch.proposals)
        self.safety.validate_patch_size(total_patch_bytes)

    # -------------------------------------------------------------------------
    # Atomic Edit Application
    # -------------------------------------------------------------------------

    def apply_edit_proposal(self, proposal: VSEditProposal, repair_id: str) -> Tuple[Path, str]:
        """Applies a single validated edit proposal atomically, returning backup path and diff."""
        file_path = Path(proposal.file_path)

        # 1. SHA-256 target validation
        self.safety.verify_target_hash(file_path, proposal.expected_sha256)

        # 2. Backup creation
        backup_path = self.create_checkpoint(file_path, repair_id)

        # 3. Read existing content
        original_lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        total_lines = len(original_lines)

        s = proposal.start_line
        e = proposal.end_line

        if s > total_lines + 1:
            raise VSSafetyError(
                VSErrorCode.EDIT_VALIDATION_FAILED,
                f"start_line {s} exceeds total lines {total_lines}.",
            )

        # 4. Construct modified content
        prefix = original_lines[:s - 1]
        suffix = original_lines[e:] if e > 0 else []

        replacement_str = proposal.replacement_text
        if replacement_str and not replacement_str.endswith("\n"):
            replacement_str += "\n"
        replacement_lines = [line + "\n" if not line.endswith("\n") else line for line in replacement_str.splitlines()]

        new_lines = prefix + replacement_lines + suffix

        # 5. Atomic write using temporary file
        temp_fd, temp_path = tempfile.mkstemp(dir=file_path.parent, prefix="vs_edit_", suffix=".tmp")
        try:
            with os.fdopen(temp_fd, "w", encoding="utf-8") as tf:
                tf.writelines(new_lines)
            # Atomic replace
            os.replace(temp_path, file_path)
        except Exception as err:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            self.restore_checkpoint(backup_path, file_path)
            raise VSSafetyError(VSErrorCode.EDIT_VALIDATION_FAILED, f"Failed to apply edit atomically: {err}")

        # 6. Generate diff
        orig_text = "".join(original_lines)
        new_text = "".join(new_lines)
        diff = "".join(difflib.unified_diff(
            orig_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=f"a/{file_path.name}",
            tofile=f"b/{file_path.name}",
        ))

        return backup_path, diff

    # -------------------------------------------------------------------------
    # Autonomous Repair Loop
    # -------------------------------------------------------------------------

    def repair_build(
        self,
        target_path: Optional[Path] = None,
        max_attempts: int = MAX_REPAIR_ATTEMPTS,
        repair_proposal_provider: Optional[Any] = None,
        test_target: Optional[Path] = None,
        run_tests: bool = False,
    ) -> VSRepairResult:
        """
        Runs bounded autonomous repair loop:
        1. Run build to detect errors
        2. Verify repairability (CS, BC, FS, syntax, source references)
        3. Propose repair via provider or advisory model
        4. Validate schema, limits, prohibited tokens, and SHA-256
        5. Apply atomic edit with pre-mutation checkpoint
        6. Rebuild and optionally run tests
        7. Roll back immediately if rebuild or tests fail
        Strictly halts after max 2 attempts.
        """
        self.safety.check_emergency_stop()
        limit = min(max_attempts, MAX_REPAIR_ATTEMPTS)
        proj_target = Path(target_path or self.safety.authorized_project)

        repair_id = f"vs_rep_{int(time.time())}"
        applied_backups: List[Tuple[Path, Path]] = []  # (backup_path, target_file)
        all_diffs: List[str] = []

        # Step 1: Initial build
        build_res = self.runner.run_build(proj_target, action="BUILD")
        if build_res.get("success"):
            if run_tests and test_target:
                test_res = self.runner.run_test(test_target)
                if test_res.get("success"):
                    return VSRepairResult(
                        success=True,
                        repair_id=repair_id,
                        attempts=0,
                        summary="Build and tests passed cleanly without repairs.",
                        rebuild_output=build_res,
                    )
                errors = self.analyzer.analyze(test_res.get("full_output", ""))
            else:
                return VSRepairResult(
                    success=True,
                    repair_id=repair_id,
                    attempts=0,
                    summary="Build succeeded cleanly without needing repairs.",
                    rebuild_output=build_res,
                )
        else:
            errors = self.analyzer.analyze(build_res.get("full_output", ""))

        if not errors:
            return VSRepairResult(
                success=False,
                repair_id=repair_id,
                attempts=0,
                error_code=VSErrorCode.REPAIR_UNSUPPORTED.value,
                summary="Build failed but no actionable compiler/diagnostic errors were extracted.",
                rebuild_output=build_res,
            )

        primary_err = errors[0]
        is_rep, rep_reason = self.analyzer.is_repairable_error(primary_err)
        if not is_rep:
            return VSRepairResult(
                success=False,
                repair_id=repair_id,
                attempts=0,
                error_code=VSErrorCode.REPAIR_UNSUPPORTED.value,
                summary=f"Build error {primary_err.error_code} cannot safely be repaired automatically: {rep_reason}",
                error_message=rep_reason,
                rebuild_output=build_res,
            )

        for attempt in range(1, limit + 1):
            logger.info(f"[VSCodeRepair] Starting repair attempt {attempt}/{limit} (ID: {repair_id})")
            context = extract_bounded_context(primary_err, proj_target)

            # Step 2: Get proposal
            proposal_dict = None
            if repair_proposal_provider:
                try:
                    proposal_dict = repair_proposal_provider(primary_err, attempt, context)
                except TypeError:
                    proposal_dict = repair_proposal_provider(primary_err, attempt)
            else:
                proposal_dict = self.generate_repair_proposal_with_model(primary_err, context)

            if not proposal_dict:
                self._rollback_all(applied_backups)
                return VSRepairResult(
                    success=False,
                    repair_id=repair_id,
                    attempts=attempt,
                    error_code=VSErrorCode.REPAIR_FAILED.value,
                    summary=f"No repair proposal generated for error {primary_err.error_code}.",
                    rolled_back=bool(applied_backups),
                    rebuild_output=build_res,
                )

            # Step 3: Validate and apply
            try:
                proposal = self.validate_proposal_schema(proposal_dict)
                bk, diff = self.apply_edit_proposal(proposal, repair_id)
                applied_backups.append((bk, Path(proposal.file_path)))
                all_diffs.append(diff)
            except VSSafetyError as se:
                self._rollback_all(applied_backups)
                return VSRepairResult(
                    success=False,
                    repair_id=repair_id,
                    attempts=attempt,
                    error_code=se.code.value,
                    error_message=se.message,
                    summary=f"Repair proposal rejected by safety gate: {se.message}",
                    rolled_back=True,
                    rebuild_output=build_res,
                )

            # Step 4: Rebuild
            rebuild_res = self.runner.run_build(proj_target, action="BUILD")
            if rebuild_res.get("success"):
                if run_tests:
                    t_target = test_target or proj_target
                    test_res = self.runner.run_test(t_target)
                    if test_res.get("success"):
                        logger.info(f"[VSCodeRepair] Rebuild and tests succeeded on attempt {attempt}.")
                        self.audit.log_event("VS_REPAIR_SUCCESS", {
                            "repair_id": repair_id,
                            "attempt": attempt,
                            "diff": redact_sensitive_data("".join(all_diffs)),
                        }, status="success")
                        return VSRepairResult(
                            success=True,
                            repair_id=repair_id,
                            attempts=attempt,
                            applied_files=[str(target) for _, target in applied_backups],
                            diff="".join(all_diffs),
                            summary=f"Autonomous repair resolved error {primary_err.error_code} on attempt {attempt}.",
                            rebuild_output=rebuild_res,
                        )
                    else:
                        logger.warning(f"[VSCodeRepair] Rebuild succeeded but tests failed on attempt {attempt}.")
                        if attempt < limit:
                            test_errs = self.analyzer.analyze(test_res.get("full_output", ""))
                            if test_errs:
                                primary_err = test_errs[0]
                                continue
                        self._rollback_all(applied_backups)
                        return VSRepairResult(
                            success=False,
                            repair_id=repair_id,
                            attempts=attempt,
                            error_code=VSErrorCode.TEST_FAILED.value,
                            summary=f"Rebuild succeeded but post-repair tests failed after {attempt} attempt(s). Changes rolled back.",
                            error_message="Post-repair tests failed.",
                            rolled_back=True,
                            rebuild_output=rebuild_res,
                        )
                else:
                    logger.info(f"[VSCodeRepair] Rebuild succeeded on attempt {attempt}.")
                    self.audit.log_event("VS_REPAIR_SUCCESS", {
                        "repair_id": repair_id,
                        "attempt": attempt,
                        "diff": redact_sensitive_data("".join(all_diffs)),
                    }, status="success")

                    return VSRepairResult(
                        success=True,
                        repair_id=repair_id,
                        attempts=attempt,
                        applied_files=[str(target) for _, target in applied_backups],
                        diff="".join(all_diffs),
                        summary=f"Autonomous repair resolved build error {primary_err.error_code} on attempt {attempt}.",
                        rebuild_output=rebuild_res,
                    )
            else:
                logger.warning(f"[VSCodeRepair] Rebuild failed on attempt {attempt}.")
                if attempt < limit:
                    new_errors = self.analyzer.analyze(rebuild_res.get("full_output", ""))
                    if new_errors:
                        primary_err = new_errors[0]

        # Exhausted attempts
        self._rollback_all(applied_backups)
        return VSRepairResult(
            success=False,
            repair_id=repair_id,
            attempts=limit,
            error_code=VSErrorCode.REPAIR_FAILED.value,
            summary=f"Build repair failed after {limit} attempts. All changes rolled back.",
            rolled_back=True,
            rebuild_output=rebuild_res,
        )

    def _rollback_all(self, applied_backups: List[Tuple[Path, Path]]) -> None:
        """Rolls back all modified files in reverse order."""
        for bk, target in reversed(applied_backups):
            try:
                self.restore_checkpoint(bk, target)
            except Exception as e:
                logger.critical(f"[VSCodeRepair] CRITICAL: Failed rollback for '{target}': {e}")

    # -------------------------------------------------------------------------
    # Model Prompt Generation (Advisory Only)
    # -------------------------------------------------------------------------

    def generate_repair_proposal_with_model(
        self,
        error: VSBuildError,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Consults advisory model for structured repair proposal.
        All source snippets and errors are redacted before prompting.
        Advisory model output is strictly JSON validated.
        """
        if not error.file_path or not Path(error.file_path).exists():
            return None

        ctx = context or extract_bounded_context(error, self.safety.authorized_project)
        sys_prompt, user_prompt = build_model_repair_prompt(error, ctx)

        try:
            resp = self.router.route_query(
                prompt=user_prompt,
                system_prompt=sys_prompt,
                task_type="highest",
                required_capabilities={ModelCapability.REASONING},
            )
            if resp.get("success") and resp.get("content"):
                return parse_model_repair_response(resp["content"])
        except Exception as err:
            logger.warning(f"Advisory repair query failed: {err}")

        return None

    def create_repair_prompt(
        self,
        errors: List[VSBuildError],
        project_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Constructs an advisory prompt explicitly enforcing zero direct tool authority."""
        err_summary = "\n".join([f"- [{e.category.value}] {e.error_code}: {e.message} ({e.file_path}:{e.line})" for e in errors])
        prompt = (
            "SYSTEM: You are an ADVISORY ONLY assistant for Visual Studio code repair.\n"
            "CRITICAL SAFETY RULE: You have NO DIRECT TOOL AUTHORITY. You cannot execute tools, shell commands, or modify files.\n"
            "You may only propose a structured JSON patch for verification.\n\n"
            f"ERRORS ENCOUNTERED:\n{err_summary}\n\n"
            f"PROJECT CONTEXT: {json.dumps(project_context or {})}\n"
        )
        return prompt

    def validate_model_proposal(self, raw_input: str) -> Optional[VSEditBatch]:
        """Validates model JSON proposal and returns VSEditBatch or None."""
        if not raw_input or not isinstance(raw_input, str):
            return None

        try:
            data = parse_model_repair_response(raw_input)
        except Exception as e:
            logger.warning(f"[VSCodeRepair] Failed to parse model response: {e}")
            return None

        # Reject if model attempted to specify direct tool execution
        for forbidden in ("tool_call", "command", "tool", "execute", "shell", "exec"):
            if forbidden in data:
                logger.warning(f"[VSCodeRepair] Model proposal attempted direct tool execution via '{forbidden}'. Rejected.")
                return None

        raw_props = data.get("proposals") or data.get("edits")
        if raw_props is None:
            if data.get("target_file") or data.get("file_path"):
                raw_props = [data]
            else:
                return None

        if not isinstance(raw_props, list) or not raw_props:
            return None

        if len(raw_props) > MAX_FILES_CHANGED:
            logger.warning(f"[VSCodeRepair] Batch has {len(raw_props)} files, exceeding limit of {MAX_FILES_CHANGED}.")
            return None

        parsed_proposals: List[VSEditProposal] = []
        for item in raw_props:
            if not isinstance(item, dict):
                return None
            try:
                proposal = self.validate_proposal_schema(item)
                parsed_proposals.append(proposal)
            except Exception as e:
                logger.warning(f"[VSCodeRepair] Proposal safety check failed: {e}")
                return None

        return VSEditBatch(
            proposals=parsed_proposals,
            repair_id=str(data.get("proposal_id") or f"rep_{int(time.time()*1000)}"),
        )

    def apply_repair(self, batch: VSEditBatch) -> VSRepairResult:
        """Applies a batch of edit proposals atomically with pre-mutation checkpoints."""
        self.safety.check_emergency_stop()
        try:
            self.safety.validate_edit_batch(batch)
        except VSSafetyError as se:
            return VSRepairResult(
                success=False,
                repair_id=batch.repair_id,
                attempts=batch.attempt_number,
                error_code=se.code.value,
                error_message=se.message,
                summary=f"Safety validation failed: {se.message}",
            )

        checkpoint_id = batch.checkpoint_id or f"ckpt_{int(time.time()*1000)}"
        applied: List[Tuple[Path, Path, str]] = []  # (backup_path, target_file, orig_text)
        applied_files: List[str] = []

        # 1. Pre-check target hashes
        for prop in batch.proposals:
            f_path = Path(prop.file_path).resolve()
            if not f_path.exists():
                return VSRepairResult(
                    success=False,
                    repair_id=batch.repair_id,
                    attempts=batch.attempt_number,
                    error_message=f"Target file {f_path.name} not found",
                    summary="Target file missing.",
                )
            cur_hash = VSSafetyGate.compute_sha256(f_path)
            if prop.expected_sha256 and cur_hash.lower() != prop.expected_sha256.lower():
                return VSRepairResult(
                    success=False,
                    repair_id=batch.repair_id,
                    attempts=batch.attempt_number,
                    stale_target_detected=True,
                    error_code=VSErrorCode.STALE_TARGET.value,
                    error_message=f"Target file {f_path.name} hash mismatch (stale target)",
                    summary="Stale target detected. Repair aborted.",
                )

        # 2. Checkpoint and apply atomically
        try:
            ckpt_manifest = {"checkpoint_id": checkpoint_id, "files": []}
            for prop in batch.proposals:
                f_path = Path(prop.file_path).resolve()
                bk = self.create_checkpoint(f_path, checkpoint_id)
                orig_text = f_path.read_text(encoding="utf-8", errors="replace")
                applied.append((bk, f_path, orig_text))
                ckpt_manifest["files"].append({"file": str(f_path), "backup": str(bk)})

                temp_fd, temp_path = tempfile.mkstemp(dir=f_path.parent, prefix="vs_edit_", suffix=".tmp")
                try:
                    with os.fdopen(temp_fd, "w", encoding="utf-8") as tf:
                        if prop.end_line > 0:
                            lines = orig_text.splitlines(keepends=True)
                            prefix = lines[:max(0, prop.start_line - 1)]
                            suffix = lines[prop.end_line:]
                            repl = prop.replacement_text
                            if repl and not repl.endswith("\n"):
                                repl += "\n"
                            tf.writelines(prefix + [repl] + suffix)
                        else:
                            tf.write(prop.replacement_text)
                    os.replace(temp_path, f_path)
                    applied_files.append(str(f_path))
                except Exception as e:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    raise e

            ckpt_manifest_file = self.checkpoint_dir / f"{checkpoint_id}.json"
            ckpt_manifest_file.write_text(json.dumps(ckpt_manifest, indent=2), encoding="utf-8")

            return VSRepairResult(
                success=True,
                repair_id=batch.repair_id,
                attempts=batch.attempt_number,
                checkpoint_id=checkpoint_id,
                applied_files=applied_files,
                summary=f"Successfully applied {len(applied_files)} edit(s).",
            )
        except Exception as err:
            for bk, target, _ in applied:
                try:
                    shutil.copy2(bk, target)
                except Exception:
                    pass
            return VSRepairResult(
                success=False,
                repair_id=batch.repair_id,
                attempts=batch.attempt_number,
                rolled_back=True,
                error_message=f"Edit application failed: {err}",
                summary=f"Edit failed and was rolled back: {err}",
            )

    def rollback_repair(self, checkpoint_id: str) -> bool:
        """Restores files from a checkpoint manifest byte-for-byte."""
        ckpt_manifest_file = self.checkpoint_dir / f"{checkpoint_id}.json"
        if not ckpt_manifest_file.exists():
            found = False
            for bk in self.checkpoint_dir.glob(f"*.{checkpoint_id}.*.bak"):
                parts = bk.name.split(f".{checkpoint_id}.")
                if parts:
                    orig_name = parts[0]
                    target = self.safety.authorized_project / orig_name
                    if target.parent.exists():
                        shutil.copy2(bk, target)
                        found = True
            return found

        try:
            data = json.loads(ckpt_manifest_file.read_text(encoding="utf-8"))
            for item in data.get("files", []):
                bk = Path(item["backup"])
                target = Path(item["file"])
                if bk.exists():
                    shutil.copy2(bk, target)
            return True
        except Exception as e:
            logger.error(f"[VSCodeRepair] Rollback failed for {checkpoint_id}: {e}")
            return False

    def attempt_repair(self, *args, **kwargs) -> VSRepairResult:
        """Alias for repair_build."""
        return self.repair_build(*args, **kwargs)
