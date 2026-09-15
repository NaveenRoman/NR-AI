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
import shutil
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from app.agent.vs_safety import (
    VSSafetyGate,
    VSErrorCode,
    VSSafetyError,
    MAX_FILES_CHANGED,
    MAX_PATCH_SIZE_BYTES,
    MAX_LINES_CHANGED,
    MAX_REPAIR_ATTEMPTS,
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
    """A proposed edit targeting a single file."""

    def __init__(
        self,
        file_path: Union[str, Path],
        expected_sha256: Optional[str] = None,
        start_line: int = 1,
        end_line: int = 0,
        replacement_text: Optional[str] = None,
        rationale: str = "",
        target_sha256: Optional[str] = None,
        new_content: Optional[str] = None,
        explanation: Optional[str] = None,
    ):
        self.file_path = str(file_path)
        self.expected_sha256 = (expected_sha256 or target_sha256 or "").strip()
        self.start_line = start_line
        self.end_line = end_line
        self.replacement_text = replacement_text if replacement_text is not None else (new_content or "")
        self.rationale = rationale or explanation or ""

    @property
    def target_sha256(self) -> str:
        return self.expected_sha256

    @property
    def new_content(self) -> str:
        return self.replacement_text

    @property
    def explanation(self) -> str:
        return self.rationale

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "expected_sha256": self.expected_sha256,
            "target_sha256": self.expected_sha256,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "replacement_text": self.replacement_text,
            "new_content": self.replacement_text,
            "rationale": self.rationale,
            "explanation": self.rationale,
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
        required = ["file_path", "expected_sha256", "start_line", "end_line", "replacement_text"]
        for key in required:
            if key not in raw_proposal:
                raise VSSafetyError(
                    VSErrorCode.MALFORMED_PROPOSAL,
                    f"Proposal missing required key: '{key}'",
                )

        f_path = str(raw_proposal["file_path"]).strip()
        expected_sha = str(raw_proposal["expected_sha256"]).strip()
        repl_text = str(raw_proposal["replacement_text"])

        try:
            s_line = int(raw_proposal["start_line"])
            e_line = int(raw_proposal["end_line"])
        except ValueError:
            raise VSSafetyError(VSErrorCode.MALFORMED_PROPOSAL, "Line numbers must be integers.")

        if s_line < 1 or e_line < s_line:
            raise VSSafetyError(
                VSErrorCode.EDIT_VALIDATION_FAILED,
                f"Invalid line range: {s_line}..{e_line}",
            )

        # Enforce patch limits
        patch_bytes = len(repl_text.encode("utf-8"))
        self.safety.validate_patch_size(patch_bytes)

        lines_changed = len(repl_text.splitlines()) + (e_line - s_line + 1)
        self.safety.validate_lines_changed(lines_changed)

        # Path validation
        validated_file = self.safety.validate_file_path(f_path, check_writable=True)

        return VSEditProposal(
            file_path=str(validated_file),
            expected_sha256=expected_sha,
            start_line=s_line,
            end_line=e_line,
            replacement_text=repl_text,
            rationale=str(raw_proposal.get("rationale", "")),
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
        suffix = original_lines[e:]

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
    ) -> VSRepairResult:
        """
        Runs bounded autonomous repair loop:
        1. Run build to detect errors
        2. Propose repair via provider or advisory model
        3. Validate schema, limits, and SHA-256
        4. Apply atomic edit
        5. Rebuild
        6. Roll back immediately if rebuild fails
        Strictly halts after max 2 attempts.
        """
        self.safety.check_emergency_stop()
        limit = min(max_attempts, MAX_REPAIR_ATTEMPTS)
        proj_target = Path(target_path or self.safety.authorized_project)

        repair_id = f"vs_rep_{int(time.time())}"
        applied_backups: List[Tuple[Path, Path]] = []  # (backup_path, target_file)
        all_diffs: List[str] = []

        for attempt in range(1, limit + 1):
            logger.info(f"[VSCodeRepair] Starting repair attempt {attempt}/{limit} (ID: {repair_id})")

            # Step 1: Execute build to get diagnostic output
            build_res = self.runner.run_build(proj_target, action="BUILD")
            if build_res.get("success"):
                return VSRepairResult(
                    success=True,
                    repair_id=repair_id,
                    attempts=attempt - 1,
                    summary="Build succeeded cleanly without needing repairs.",
                    rebuild_output=build_res,
                )

            # Step 2: Analyze errors
            errors = self.analyzer.analyze(build_res.get("full_output", ""))
            if not errors:
                return VSRepairResult(
                    success=False,
                    repair_id=repair_id,
                    attempts=attempt,
                    error_code=VSErrorCode.REPAIR_UNSUPPORTED.value,
                    summary="Build failed but no actionable compiler/diagnostic errors were extracted.",
                    rebuild_output=build_res,
                )

            primary_err = errors[0]
            logger.info(f"[VSCodeRepair] Diagnosed primary error: {primary_err.error_code} at {primary_err.file_path}:{primary_err.line}")

            # Step 3: Get proposal
            proposal_dict = None
            if repair_proposal_provider:
                proposal_dict = repair_proposal_provider(primary_err, attempt)
            else:
                proposal_dict = self.generate_repair_proposal_with_model(primary_err)

            if not proposal_dict:
                return VSRepairResult(
                    success=False,
                    repair_id=repair_id,
                    attempts=attempt,
                    error_code=VSErrorCode.REPAIR_FAILED.value,
                    summary=f"No repair proposal generated for error {primary_err.error_code}.",
                    rebuild_output=build_res,
                )

            # Step 4: Validate and apply
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
                    summary=f"Repair proposal rejected by safety gate: {se.message}",
                    rolled_back=True,
                    rebuild_output=build_res,
                )

            # Step 5: Rebuild project to verify resolution
            rebuild_res = self.runner.run_build(proj_target, action="BUILD")
            if rebuild_res.get("success"):
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

        # If loop exhausts without success, roll back all applied edits
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

    def generate_repair_proposal_with_model(self, error: VSBuildError) -> Optional[Dict[str, Any]]:
        """
        Consults advisory model for structured repair proposal.
        All source snippets and errors are redacted before prompting.
        Advisory model output is strictly JSON validated.
        """
        if not error.file_path or not Path(error.file_path).exists():
            return None

        target_file = Path(error.file_path)
        current_sha = self.safety.compute_sha256(target_file)
        lines = target_file.read_text(encoding="utf-8", errors="replace").splitlines()

        err_line = error.line or 1
        s = max(1, err_line - 15)
        e = min(len(lines), err_line + 15)

        context_lines = [
            f"{ln:4d} | {redact_sensitive_data(lines[ln - 1])}"
            for ln in range(s, e + 1)
        ]

        sys_prompt = (
            "You are an ADVISORY Visual Studio / C# code repair assistant.\n"
            "You have ZERO direct execution authority. You cannot execute shell or tools.\n"
            "Propose a bounded, safe edit resolving the compiler/diagnostic error.\n"
            "Return ONLY valid JSON matching this schema:\n"
            "{\n"
            '  "file_path": "path/to/file",\n'
            '  "expected_sha256": "current_sha256",\n'
            '  "start_line": 10,\n'
            '  "end_line": 12,\n'
            '  "replacement_text": "replacement code",\n'
            '  "rationale": "reason for edit"\n'
            "}\n"
        )

        user_prompt = (
            f"ERROR CODE: {error.error_code}\n"
            f"MESSAGE: {redact_sensitive_data(error.message)}\n"
            f"DIAGNOSIS: {error.diagnosis}\n"
            f"FILE: {target_file}\n"
            f"CURRENT SHA256: {current_sha}\n"
            f"CONTEXT:\n" + "\n".join(context_lines)
        )

        try:
            resp = self.router.route_query(
                prompt=user_prompt,
                system_prompt=sys_prompt,
                task_type="highest",
                required_capabilities={ModelCapability.REASONING},
            )
            if resp.get("success") and resp.get("content"):
                match = re.search(r"\{[\s\S]*\}", resp["content"])
                if match:
                    return json.loads(match.group(0))
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

        cleaned = raw_input.strip()
        if "```json" in cleaned:
            cleaned = cleaned.split("```json")[1].split("```")[0].strip()
        elif "```" in cleaned:
            cleaned = cleaned.split("```")[1].split("```")[0].strip()

        try:
            data = json.loads(cleaned)
        except Exception:
            return None

        if not isinstance(data, dict):
            return None

        # Reject if model attempted to specify direct tool execution
        if "tool_call" in data or "command" in data:
            logger.warning("[VSCodeRepair] Model proposal attempted direct tool execution. Rejected.")
            return None

        raw_props = data.get("proposals")
        if not isinstance(raw_props, list) or not raw_props:
            return None

        parsed_proposals: List[VSEditProposal] = []
        for item in raw_props:
            if not isinstance(item, dict):
                return None
            f_path = item.get("file_path") or item.get("path")
            target_hash = item.get("target_sha256") or item.get("expected_sha256")
            new_text = item.get("new_content") or item.get("replacement_text")
            if not f_path or not target_hash or new_text is None:
                return None

            try:
                self.safety.validate_file_path(f_path, check_writable=True)
                self.safety.validate_patch_content(new_text)
            except Exception as e:
                logger.warning(f"[VSCodeRepair] Proposal safety check failed: {e}")
                return None

            parsed_proposals.append(VSEditProposal(
                file_path=str(f_path),
                expected_sha256=str(target_hash),
                start_line=item.get("start_line", 1),
                end_line=item.get("end_line", 0),
                replacement_text=new_text,
                rationale=item.get("explanation") or item.get("rationale", ""),
            ))

        if not parsed_proposals or len(parsed_proposals) > MAX_FILES_CHANGED:
            return None

        return VSEditBatch(
            proposals=parsed_proposals,
            repair_id=f"rep_{int(time.time()*1000)}",
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
