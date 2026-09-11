"""
NR-AI Android Code Editing + Build Error Repair Engine (Step 6 Phase 3).

Provides bounded, safe, verifiable code editing and self-healing build error repair
for the authorized Android Studio test project:
    C:/NR-AI\nr_android_test

Enforces:
- Strict path boundaries (C:/NR-AI\nr_android_test only)
- Allowlisted file extensions (.kt, .java, .xml, .gradle, .gradle.kts, .properties, .json, .toml, .yaml, .yml, .md)
- Protected files (local.properties, *.jks, *.keystore, google-services.json, credentials)
- Hard file size limit (<= 1 MB) and patch size limit (<= 100 KB)
- Atomic check-before-modify with SHA-256 target hash verification
- Pre-edit checkpoint backups in scratch\android_checkpoints
- Unified diff generation and inspection
- Automated build -> diagnose -> repair -> rebuild loop with max 2 attempts
- Deterministic rollback to clean backup on failure
- Full structured audit logging for all proposed, applied, and rolled-back edits
- Models remain advisory only; deterministic safety and tools remain authoritative.
"""

from dataclasses import dataclass, field
import difflib
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
    AUTHORIZED_BUILD_ACTIONS,
    AUTHORIZED_PROJECT_PATH,
    FORBIDDEN_ANDROID_EXTENSIONS,
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
from app.agent.android_tools import AndroidToolResult, SafeGradleRunner
from app.agent.code_writer import CodeWriter
from app.agent.error_analyzer import ErrorAnalyzer
from app.agent.model_router import ModelRouter
from app.config.model_config import ModelCapability
from app.memory.audit_logger import AuditLogger

logger = logging.getLogger("NRAI.AndroidCodeRepair")


# -----------------------------------------------------------------------------
# Structured Edit Schema
# -----------------------------------------------------------------------------

class EditOperation(str, Enum):
    REPLACE_RANGE = "REPLACE_RANGE"
    REPLACE_EXACT = "REPLACE_EXACT"
    INSERT_AFTER = "INSERT_AFTER"
    INSERT_BEFORE = "INSERT_BEFORE"
    CREATE_FILE = "CREATE_FILE"


@dataclass
class EditProposal:
    """A structured, strictly validated proposal to edit a file."""
    file_path: Union[str, Path]
    expected_old_hash: str = ""
    operation: EditOperation = EditOperation.REPLACE_EXACT
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    target_snippet: Optional[str] = None
    replacement_text: str = ""
    reason: str = ""
    proposal_id: str = field(default_factory=lambda: f"prop_{uuid.uuid4().hex[:8]}")

    # Compatibility aliases
    old_str: Optional[str] = None
    new_str: Optional[str] = None
    expected_hash: Optional[str] = None
    operation_type: Optional[Any] = None
    after_marker: Optional[str] = None
    description: Optional[str] = None
    edits: Optional[List[Any]] = None

    def __post_init__(self):
        if self.old_str is not None and not self.target_snippet:
            self.target_snippet = self.old_str
        if self.after_marker is not None and not self.target_snippet:
            self.target_snippet = self.after_marker
        if self.new_str is not None and not self.replacement_text:
            self.replacement_text = self.new_str
        if self.expected_hash is not None and not self.expected_old_hash:
            self.expected_old_hash = self.expected_hash
        if self.description is not None and not self.reason:
            self.reason = self.description

        if self.operation_type is not None:
            if isinstance(self.operation_type, EditOperation):
                self.operation = self.operation_type
            else:
                try:
                    self.operation = EditOperation(str(self.operation_type))
                except ValueError:
                    pass
        elif isinstance(self.operation, str):
            try:
                self.operation = EditOperation(self.operation)
            except ValueError:
                pass

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "file_path": str(self.file_path),
            "expected_old_hash": self.expected_old_hash,
            "operation": self.operation.value if isinstance(self.operation, EditOperation) else str(self.operation),
            "start_line": self.start_line,
            "end_line": self.end_line,
            "target_snippet": self.target_snippet,
            "replacement_bytes": len(self.replacement_text.encode("utf-8")),
            "reason": self.reason,
        }


@dataclass
class EditResult:
    """Outcome of a validated file modification."""
    success: bool
    proposal_id: str
    file_path: str
    old_hash: str
    new_hash: str
    diff: str
    backup_path: Optional[str] = None
    lines_changed: int = 0
    error: Optional[str] = None
    error_code: Optional[str] = None
    backup_ids: List[str] = field(default_factory=list)
    modified_files: List[str] = field(default_factory=list)

    def __post_init__(self):
        if self.backup_path and not self.backup_ids:
            self.backup_ids = [self.backup_path]
        if self.file_path and not self.modified_files:
            self.modified_files = [self.file_path]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "proposal_id": self.proposal_id,
            "file_path": self.file_path,
            "old_hash": self.old_hash,
            "new_hash": self.new_hash,
            "diff": self.diff,
            "backup_path": self.backup_path,
            "backup_ids": self.backup_ids,
            "lines_changed": self.lines_changed,
            "error": self.error,
            "error_code": self.error_code,
            "modified_files": self.modified_files,
        }


# -----------------------------------------------------------------------------
# Structured Build Error Classification
# -----------------------------------------------------------------------------

class AndroidErrorCategory(str, Enum):
    JAVA_COMPILE = "JAVA_COMPILE"
    JAVA_COMPILE_ERROR = "JAVA_COMPILE"
    KOTLIN_COMPILE = "KOTLIN_COMPILE"
    KOTLIN_COMPILE_ERROR = "KOTLIN_COMPILE"
    RESOURCE_LINKING = "RESOURCE_LINKING"
    RESOURCE_ERROR = "RESOURCE_LINKING"
    MANIFEST_MERGER = "MANIFEST_MERGER"
    MANIFEST_ERROR = "MANIFEST_MERGER"
    GRADLE_CONFIGURATION = "GRADLE_CONFIGURATION"
    GRADLE_CONFIGURATION_ERROR = "GRADLE_CONFIGURATION"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"
    DUPLICATE_RESOURCE = "DUPLICATE_RESOURCE"
    DUPLICATE_CLASS = "DUPLICATE_CLASS"
    SYNTAX_ERROR = "SYNTAX_ERROR"
    TYPE_ERROR = "TYPE_ERROR"
    UNKNOWN_BUILD_ERROR = "UNKNOWN_BUILD_ERROR"


@dataclass
class AndroidBuildError:
    """Structured build diagnostic extracted from Gradle compiler output."""
    category: AndroidErrorCategory
    file_path: Optional[str] = None
    line: Optional[int] = None
    column: Optional[int] = None
    message: str = ""
    diagnosis: str = ""
    relevant_files: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category.value,
            "file_path": self.file_path,
            "line": self.line,
            "column": self.column,
            "message": self.message,
            "diagnosis": self.diagnosis,
            "relevant_files": self.relevant_files,
        }


@dataclass
class RepairResult:
    """Comprehensive result of an automated build repair session."""
    success: bool
    attempts: int
    repaired_files: List[str] = field(default_factory=list)
    initial_error: Optional[AndroidBuildError] = None
    final_error: Optional[AndroidBuildError] = None
    applied_edits: List[EditResult] = field(default_factory=list)
    summary: str = ""
    error_code: Optional[str] = None
    rolled_back: bool = False

    @property
    def rollback_performed(self) -> bool:
        return self.rolled_back

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "attempts": self.attempts,
            "repaired_files": self.repaired_files,
            "initial_error": self.initial_error.to_dict() if self.initial_error else None,
            "final_error": self.final_error.to_dict() if self.final_error else None,
            "applied_edits": [e.to_dict() for e in self.applied_edits],
            "summary": self.summary,
            "error_code": self.error_code,
            "rolled_back": self.rolled_back,
            "rollback_performed": self.rollback_performed,
        }


# -----------------------------------------------------------------------------
# -----------------------------------------------------------------------------
# Phase 4 Autonomous Repair Helpers: Redaction, Bounded Context & Prompting
# -----------------------------------------------------------------------------

def redact_sensitive_content(text: str) -> str:
    """
    Redacts secrets, API keys, passwords, and tokens from text.
    Ensures credentials never leak to models or external logs.
    """
    if not text:
        return text

    # Redact Google / Gemini API keys
    text = re.sub(r"AIzaSy[A-Za-z0-9_-]{33}", "[REDACTED_GEMINI_KEY]", text)
    # Redact OpenAI API keys
    text = re.sub(r"sk-proj-[A-Za-z0-9_-]+", "[REDACTED_OPENAI_KEY]", text)
    # Redact GitHub tokens
    text = re.sub(r"ghp_[A-Za-z0-9_]{36,}", "[REDACTED_GITHUB_TOKEN]", text)

    # Redact passwords, secrets, tokens in key=val or JSON-like forms
    patterns = [
        (r'''(?i)(["']?(?:password|passwd|secret|api[_-]?key|token|keystorepassword|keypassword|storepassword)["']?\s*[:=]\s*["'])([^"']+)(["'])''', r'\g<1>[REDACTED]\g<3>'),
        (r'''(?i)(storePassword\s+["'])([^"']+)(["'])''', r'\g<1>[REDACTED]\g<3>'),
        (r'''(?i)(keyPassword\s+["'])([^"']+)(["'])''', r'\g<1>[REDACTED]\g<3>'),
    ]
    for pattern, repl in patterns:
        text = re.sub(pattern, repl, text)
    return text


def extract_bounded_context(
    error: AndroidBuildError,
    project_root: Optional[Path] = None,
    max_lines_context: int = 30,
) -> Dict[str, Any]:
    """
    Extracts bounded source code context surrounding an error location (+- 30 lines).
    Redacts sensitive content and computes current file SHA-256 hash.
    Never exposes whole project or unnecessary files.
    """
    root = Path(project_root or AUTHORIZED_PROJECT_PATH).resolve()
    if not error.file_path:
        return {
            "file_path": None,
            "relative_path": None,
            "error_line": None,
            "start_line": 1,
            "end_line": 1,
            "context_code": "",
            "file_sha256": "",
            "total_lines": 0,
        }

    target_path = Path(error.file_path)
    if not target_path.is_absolute():
        target_path = (root / target_path).resolve()

    if not target_path.exists() or not target_path.is_file():
        return {
            "file_path": str(target_path),
            "relative_path": str(target_path.relative_to(root)) if root in target_path.parents else target_path.name,
            "error_line": error.line,
            "start_line": 1,
            "end_line": 1,
            "context_code": "",
            "file_sha256": "",
            "total_lines": 0,
        }

    content = target_path.read_text(encoding="utf-8", errors="replace")
    file_sha256 = AndroidCodeRepairEngine.calculate_file_hash(target_path)
    lines = content.splitlines()
    total_lines = len(lines)

    err_line = error.line if error.line and error.line >= 1 else 1
    start_line = max(1, err_line - max_lines_context)
    end_line = min(total_lines, err_line + max_lines_context)

    # 1-indexed line extraction
    context_slice = lines[start_line - 1: end_line]
    numbered_lines = [f"{start_line + i:4d} | {line}" for i, line in enumerate(context_slice)]
    raw_context = "\n".join(numbered_lines)

    sanitized_context = redact_sensitive_content(raw_context)

    try:
        rel_path = str(target_path.relative_to(root)).replace("\\", "/")
    except ValueError:
        rel_path = target_path.name

    return {
        "file_path": str(target_path),
        "relative_path": rel_path,
        "error_line": err_line,
        "start_line": start_line,
        "end_line": end_line,
        "context_code": sanitized_context,
        "raw_snippet": "\n".join(context_slice),
        "file_sha256": file_sha256,
        "total_lines": total_lines,
    }


def build_model_repair_prompt(error: AndroidBuildError, context: Dict[str, Any]) -> Tuple[str, str]:
    """
    Constructs strict JSON system and user prompts for advisory repair proposal.
    Enforces strict output JSON schema:
    {
      "proposal_version": "1.0",
      "summary": "...",
      "edits": [
        {
          "path": "...",
          "expected_sha256": "...",
          "start_line": 1,
          "end_line": 2,
          "replacement": "..."
        }
      ],
      "confidence": 0.95,
      "reasoning_summary": "..."
    }
    """
    system_prompt = (
        "You are an expert Android build-repair advisor for NR-AI.\n"
        "Your role is STRICTLY ADVISORY. You do NOT have shell, filesystem, or tool execution privileges.\n"
        "You must analyze the compiler diagnostic and propose a minimal, deterministic code fix.\n\n"
        "RULES:\n"
        "1. Output ONLY a single valid JSON object. Do not include explanatory markdown outside the JSON.\n"
        "2. The JSON MUST follow this exact schema:\n"
        "{\n"
        '  "proposal_version": "1.0",\n'
        '  "summary": "Concise description of the fix",\n'
        '  "edits": [\n'
        "    {\n"
        '      "path": "relative/path/to/file.kt",\n'
        '      "expected_sha256": "<current file SHA-256>",\n'
        '      "start_line": <1-based start line of code to replace>,\n'
        '      "end_line": <1-based end line of code to replace>,\n'
        '      "replacement": "<exact replacement code string>"\n'
        "    }\n"
        "  ],\n"
        '  "confidence": 0.95,\n'
        '  "reasoning_summary": "Why this fixes the compilation error"\n'
        "}\n"
        "3. Replace ONLY the minimal necessary lines. Do NOT rewrite entire files.\n"
        "4. The expected_sha256 MUST match the file SHA-256 provided in the context.\n"
        "5. Never include secrets, API keys, credentials, or dangerous operations.\n"
    )

    user_prompt = (
        f"Android Build Error Classification: {error.category.value}\n"
        f"Diagnostic Message: {redact_sensitive_content(error.message)}\n"
        f"Target File: {context.get('relative_path')}\n"
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
    Raises AndroidSafetyError(AndroidErrorCode.MALFORMED_PROPOSAL, ...) on failure.
    """
    if not raw_text or not raw_text.strip():
        raise AndroidSafetyError(
            AndroidErrorCode.MALFORMED_PROPOSAL,
            "Model returned empty response for repair proposal.",
        )

    text = raw_text.strip()
    if "```" in text:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if m:
            text = m.group(1).strip()

    try:
        data = json.loads(text)
    except Exception as e:
        raise AndroidSafetyError(
            AndroidErrorCode.MALFORMED_PROPOSAL,
            f"Failed to parse model response as JSON: {e}",
        )

    if not isinstance(data, dict):
        raise AndroidSafetyError(
            AndroidErrorCode.MALFORMED_PROPOSAL,
            "Model proposal root must be a JSON object.",
        )

    # 1. Version check
    version = data.get("proposal_version")
    if not version or not isinstance(version, str) or version != "1.0":
        raise AndroidSafetyError(
            AndroidErrorCode.MALFORMED_PROPOSAL,
            f"Invalid or missing 'proposal_version' (expected '1.0', got '{version}').",
        )

    # 2. Summary check
    summary = data.get("summary")
    if not summary or not isinstance(summary, str) or not summary.strip():
        raise AndroidSafetyError(
            AndroidErrorCode.MALFORMED_PROPOSAL,
            "Missing or empty 'summary' in repair proposal.",
        )

    # 3. Edits check
    edits = data.get("edits")
    if not isinstance(edits, list) or len(edits) == 0:
        raise AndroidSafetyError(
            AndroidErrorCode.MALFORMED_PROPOSAL,
            "Missing or empty 'edits' list in repair proposal.",
        )

    if len(edits) > MAX_FILES_PER_REPAIR:
        raise AndroidSafetyError(
            AndroidErrorCode.TOO_MANY_FILES_CHANGED,
            f"Proposal contains {len(edits)} edits, exceeding limit of {MAX_FILES_PER_REPAIR}.",
        )

    for idx, edit in enumerate(edits):
        if not isinstance(edit, dict):
            raise AndroidSafetyError(
                AndroidErrorCode.MALFORMED_PROPOSAL,
                f"Edit item {idx} is not a valid JSON object.",
            )

        edit_path = edit.get("path") or edit.get("file_path")
        if not edit_path or not isinstance(edit_path, str) or not edit_path.strip():
            raise AndroidSafetyError(
                AndroidErrorCode.MALFORMED_PROPOSAL,
                f"Edit item {idx} missing valid 'path'.",
            )

        start_line = edit.get("start_line")
        end_line = edit.get("end_line")
        if start_line is None or not isinstance(start_line, int) or start_line < 1:
            raise AndroidSafetyError(
                AndroidErrorCode.MALFORMED_PROPOSAL,
                f"Edit item {idx} invalid 'start_line' ({start_line}). Must be integer >= 1.",
            )

        if end_line is None or not isinstance(end_line, int) or end_line < start_line:
            raise AndroidSafetyError(
                AndroidErrorCode.MALFORMED_PROPOSAL,
                f"Edit item {idx} invalid 'end_line' ({end_line}). Must be integer >= start_line ({start_line}).",
            )

        if (end_line - start_line + 1) > MAX_LINES_PER_EDIT:
            raise AndroidSafetyError(
                AndroidErrorCode.PATCH_TOO_LARGE,
                f"Edit item {idx} replaces {end_line - start_line + 1} lines, exceeding limit of {MAX_LINES_PER_EDIT}.",
            )

        replacement = edit.get("replacement")
        if replacement is None or not isinstance(replacement, str):
            replacement = edit.get("replacement_text")
        if replacement is None or not isinstance(replacement, str):
            raise AndroidSafetyError(
                AndroidErrorCode.MALFORMED_PROPOSAL,
                f"Edit item {idx} missing valid 'replacement' string.",
            )

        if len(replacement.encode("utf-8")) > MAX_PATCH_SIZE_BYTES:
            raise AndroidSafetyError(
                AndroidErrorCode.PATCH_TOO_LARGE,
                f"Edit item {idx} replacement size exceeds limit of {MAX_PATCH_SIZE_BYTES} bytes.",
            )

    return data


# -----------------------------------------------------------------------------
# Android Error Analyzer
# -----------------------------------------------------------------------------

class AndroidErrorAnalyzer:
    """
    Analyzes Gradle build outputs and classifies Android build errors.
    Reuses and extends ErrorAnalyzer.
    """

    def __init__(self, base_analyzer: Optional[ErrorAnalyzer] = None):
        self.base_analyzer = base_analyzer or ErrorAnalyzer()

    @classmethod
    def analyze(cls, output: str, project_root: Optional[Path] = None) -> AndroidBuildError:
        analyzer = cls()
        return analyzer.analyze_build_output(output, project_root)

    def analyze_build_output(self, output: str, project_root: Optional[Path] = None) -> AndroidBuildError:
        """Parses compiler/Gradle output into a structured AndroidBuildError."""
        if not output:
            return AndroidBuildError(
                category=AndroidErrorCategory.UNKNOWN_BUILD_ERROR,
                message="No build output provided.",
                diagnosis="Empty compiler output.",
            )

        root = project_root or AUTHORIZED_PROJECT_PATH

        # 1. Check for Kotlin Compile Errors
        kt_match = re.search(r"e:\s*(?:file:///)?([a-zA-Z]:[^\r\n:]+?\.kt|[^\r\n:]+?\.kt)[:\s]+\(?(\d+)[,:]\s*(\d+)\)?[:\s]+(.*)", output)
        if not kt_match:
            kt_match = re.search(r"([a-zA-Z]:[^\r\n:]+?\.kt|[^\r\n:]+?\.kt):\((\d+),\s*(\d+)\):\s*(.*)", output)

        if kt_match:
            fpath = kt_match.group(1).strip().replace("/", os.sep)
            line = int(kt_match.group(2))
            col = int(kt_match.group(3))
            msg = kt_match.group(4).strip()

            cat = AndroidErrorCategory.KOTLIN_COMPILE
            if "type mismatch" in msg.lower() or "incompatible type" in msg.lower():
                cat = AndroidErrorCategory.TYPE_ERROR
            elif "expecting" in msg.lower() or "syntax" in msg.lower():
                cat = AndroidErrorCategory.SYNTAX_ERROR

            return AndroidBuildError(
                category=cat,
                file_path=fpath,
                line=line,
                column=col,
                message=msg,
                diagnosis=f"Kotlin compiler error at line {line}: {msg}",
                relevant_files=[fpath],
            )

        # 2. Check for Java Compile Errors
        java_match = re.search(r"([a-zA-Z]:[^\r\n:]+?\.java|[^\r\n:]+?\.java):(\d+):\s*error:\s*(.*)", output)
        if not java_match:
            java_match = re.search(r"([a-zA-Z]:[^\r\n:]+?\.java|[^\r\n:]+?\.java):\((\d+),\s*(\d+)\):\s*(.*)", output)

        if java_match:
            fpath = java_match.group(1).strip().replace("/", os.sep)
            line = int(java_match.group(2))
            msg = java_match.group(3).strip()

            cat = AndroidErrorCategory.JAVA_COMPILE
            if "incompatible types" in msg.lower():
                cat = AndroidErrorCategory.TYPE_ERROR
            elif "';' expected" in msg.lower() or "syntax" in msg.lower():
                cat = AndroidErrorCategory.SYNTAX_ERROR

            return AndroidBuildError(
                category=cat,
                file_path=fpath,
                line=line,
                message=msg,
                diagnosis=f"Java compiler error at line {line}: {msg}",
                relevant_files=[fpath],
            )

        # 3. Check for Manifest Merger Errors
        if "manifest merger failed" in output.lower() or ("manifest" in output.lower() and "processdebugmainmanifest" in output.lower()) or "androidmanifest.xml:" in output.lower():
            man_match = re.search(r"AndroidManifest\.xml:(\d+):\s*(?:error:\s*)?(.*)", output)
            line = int(man_match.group(1)) if man_match else None
            msg = man_match.group(2).strip() if man_match else "Manifest merger failed with multiple errors."
            man_path = str(root / "app" / "src" / "main" / "AndroidManifest.xml")
            return AndroidBuildError(
                category=AndroidErrorCategory.MANIFEST_MERGER,
                file_path=man_path,
                line=line,
                message=msg,
                diagnosis=f"Android manifest merger error: {msg}",
                relevant_files=[man_path],
            )

        # 4. Check for AAPT / Resource Errors
        if "aapt:" in output.lower() or ("resource" in output.lower() and "not found" in output.lower()):
            res_match = re.search(r"resource ([A-Za-z0-9_/@:]+) not found", output)
            res_name = res_match.group(1) if res_match else "unresolved"
            return AndroidBuildError(
                category=AndroidErrorCategory.RESOURCE_LINKING,
                message=f"AAPT resource resolution failure: {res_name}",
                diagnosis=f"Resource '{res_name}' is referenced but not declared in res/.",
                relevant_files=[],
            )

        # 5. Check for Duplicate Class / Duplicate Resource
        if "Duplicate class" in output:
            dup_match = re.search(r"Duplicate class ([A-Za-z0-9_.$]+) found", output)
            cls_name = dup_match.group(1) if dup_match else "unknown"
            return AndroidBuildError(
                category=AndroidErrorCategory.DUPLICATE_CLASS,
                message=f"Duplicate class detected: {cls_name}",
                diagnosis="Conflict in dependencies or redundant source classes.",
                relevant_files=[],
            )

        if "Duplicate resources" in output or "duplicate resource" in output.lower():
            return AndroidBuildError(
                category=AndroidErrorCategory.DUPLICATE_RESOURCE,
                message="Duplicate Android resource detected across res folders.",
                diagnosis="Multiple resource files or entries share the same identifier.",
                relevant_files=[],
            )

        # 6. Check for Gradle Configuration or Dependency Errors
        if "plugin" in output.lower() and "not found" in output.lower():
            return AndroidBuildError(
                category=AndroidErrorCategory.GRADLE_CONFIGURATION,
                message="Plugin declaration error during Gradle configuration phase.",
                diagnosis="Required Gradle plugin was not found or could not be loaded.",
                relevant_files=[str(root / "build.gradle"), str(root / "app" / "build.gradle")],
            )

        if "A problem occurred evaluating project" in output or "Build file" in output or "settings repositories" in output.lower():
            gf_match = re.search(r"Build file '([^']+)' line:\s*(\d+)", output)
            gf_path = gf_match.group(1) if gf_match else str(root / "app" / "build.gradle")
            line = int(gf_match.group(2)) if gf_match else None
            return AndroidBuildError(
                category=AndroidErrorCategory.GRADLE_CONFIGURATION,
                file_path=gf_path,
                line=line,
                message="Gradle build script configuration error.",
                diagnosis="Syntax or property error during Gradle configuration phase.",
                relevant_files=[gf_path],
            )

        if "Could not resolve all files" in output or "Could not find" in output:
            return AndroidBuildError(
                category=AndroidErrorCategory.DEPENDENCY_ERROR,
                message="Gradle dependency resolution failed.",
                diagnosis="Declared repository or dependency coordinates could not be retrieved.",
                relevant_files=[str(root / "app" / "build.gradle")],
            )

        # Fallback to generic build error
        return AndroidBuildError(
            category=AndroidErrorCategory.UNKNOWN_BUILD_ERROR,
            message=output[:300].strip(),
            diagnosis="Unclassified Gradle build failure.",
            relevant_files=[],
        )


# -----------------------------------------------------------------------------
# Android Code Repair Engine
# -----------------------------------------------------------------------------

class AndroidCodeRepairEngine:
    """
    Core engine for safe, verifiable Android code editing and automated build repair.
    Operates strictly within C:/NR-AI\nr_android_test.
    """

    def __init__(
        self,
        project_path: Path = AUTHORIZED_PROJECT_PATH,
        safety_gate: Optional[AndroidSafetyGate] = None,
        gradle_runner: Optional[SafeGradleRunner] = None,
        audit_logger: Optional[AuditLogger] = None,
        code_writer: Optional[CodeWriter] = None,
        analyzer: Optional[AndroidErrorAnalyzer] = None,
        router: Optional[ModelRouter] = None,
    ):
        self.project_path = Path(project_path).resolve()
        self.safety = safety_gate or AndroidSafetyGate(authorized_project=self.project_path)
        self.gradle = gradle_runner or SafeGradleRunner(project_dir=self.project_path)
        self.audit = audit_logger or AuditLogger()
        self.analyzer = analyzer or AndroidErrorAnalyzer()
        self.router = router or ModelRouter()

        # Checkpoints directory
        self.checkpoints_dir = Path(r"C:/NR-AI\scratch\android_checkpoints").resolve()
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)

        self.code_writer = code_writer or CodeWriter(
            workspace=str(self.project_path),
            checkpoint_dir=str(self.checkpoints_dir),
        )

    # -------------------------------------------------------------------------
    # Hashing & File Utilities
    # -------------------------------------------------------------------------

    @staticmethod
    def calculate_hash(content: str) -> str:
        """Computes SHA-256 hash of a string content."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    def calculate_file_hash(path: Path) -> str:
        """Computes SHA-256 hash of a file on disk."""
        if not path.exists():
            return ""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()

    # -------------------------------------------------------------------------
    # Diff Generation
    # -------------------------------------------------------------------------

    def generate_diff(self, a: str, b: str, c: str = "") -> str:
        """
        Generates unified diff representation.
        Supports both (file_path, old_content, new_content) and (old_content, new_content, file_path).
        """
        if "\n" in a or (not c and not os.path.exists(a)):
            old_content, new_content, file_path = a, b, c or "file.kt"
        else:
            file_path, old_content, new_content = a, b, c

        from_file = f"a/{Path(file_path).name}"
        to_file = f"b/{Path(file_path).name}"
        diff_lines = difflib.unified_diff(
            old_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=from_file,
            tofile=to_file,
        )
        return "".join(diff_lines)

    # -------------------------------------------------------------------------
    # Deterministic Build
    # -------------------------------------------------------------------------

    def run_build(self, action: str = "DEBUG_ASSEMBLE") -> AndroidToolResult:
        """Executes authorized Gradle build deterministically."""
        return self.gradle.run_gradle_task(action, safety_gate=self.safety)

    # -------------------------------------------------------------------------
    # Model Proposal Parsing
    # -------------------------------------------------------------------------

    def parse_model_proposal(self, raw_text: str) -> Optional[EditProposal]:
        """
        Safely parses an advisory model's output into a structured EditProposal.
        Extracts JSON block, validates schema, and returns typed proposal.
        Zero execution or disk modification occurs here.
        """
        if not raw_text:
            return None
        text = raw_text.strip()
        if "```" in text:
            m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
            if m:
                text = m.group(1).strip()

        try:
            data = json.loads(text)
        except Exception as e:
            if "proposal_version" in text or "{" in text:
                raise AndroidSafetyError(
                    AndroidErrorCode.MALFORMED_PROPOSAL,
                    f"Invalid JSON proposal: {e}",
                )
            return None

        if not isinstance(data, dict):
            raise AndroidSafetyError(
                AndroidErrorCode.MALFORMED_PROPOSAL,
                "Model proposal must be a JSON object.",
            )

        # If it has proposal_version, validate strictly
        if "proposal_version" in data:
            validated_data = parse_model_repair_response(raw_text)
            edits = validated_data.get("edits", [])
            proposals = []
            for e in edits:
                p_path = e.get("path") or e.get("file_path", "")
                exp_h = e.get("expected_sha256") or e.get("expected_hash", "")
                ep = EditProposal(
                    file_path=p_path,
                    expected_old_hash=exp_h,
                    operation=EditOperation.REPLACE_RANGE,
                    start_line=e.get("start_line"),
                    end_line=e.get("end_line"),
                    replacement_text=e.get("replacement") or e.get("replacement_text", ""),
                    reason=validated_data.get("summary", "Model repair proposal"),
                )
                proposals.append(ep)

            if not proposals:
                return None
            root_prop = proposals[0]
            root_prop.edits = proposals
            return root_prop

        # Fallback to legacy format
        edits = data.get("edits")
        edit_dict = edits[0] if isinstance(edits, list) and edits else data

        op_name = edit_dict.get("operation_type") or edit_dict.get("operation") or "REPLACE_EXACT"
        try:
            op = EditOperation(op_name)
        except ValueError:
            op = EditOperation.REPLACE_EXACT

        return EditProposal(
            file_path=edit_dict.get("file_path", ""),
            expected_old_hash=edit_dict.get("expected_hash") or edit_dict.get("expected_old_hash") or "",
            operation=op,
            start_line=edit_dict.get("start_line"),
            end_line=edit_dict.get("end_line"),
            target_snippet=edit_dict.get("old_str") or edit_dict.get("target_snippet"),
            replacement_text=edit_dict.get("new_str") or edit_dict.get("replacement_text") or "",
        )


    # -------------------------------------------------------------------------
    # Proposal Validation
    # -------------------------------------------------------------------------

    def validate_proposal(self, proposal: EditProposal) -> Path:
        """
        Validates an EditProposal against all Step 6 Phase 3 safety constraints:
        - Project boundary (C:/NR-AI\nr_android_test)
        - Allowed file extensions
        - Protected files rejected
        - Stale hash check
        - Patch size limit (<= 100 KB)
        - Prohibited content rejection
        """
        self.safety.check_emergency_stop()

        is_create = (proposal.operation == EditOperation.CREATE_FILE)
        resolved = self.safety.validate_editable_file(proposal.file_path, is_creation=is_create)

        # Stale Target Protection (Hash Check)
        if not is_create:
            current_hash = self.calculate_file_hash(resolved)
            text_hash = self.calculate_hash(resolved.read_text(encoding="utf-8", errors="replace"))
            if proposal.expected_old_hash and proposal.expected_old_hash not in (current_hash, text_hash):
                raise AndroidSafetyError(
                    AndroidErrorCode.STALE_TARGET,
                    f"Stale target for '{resolved.name}': expected hash {proposal.expected_old_hash[:8]}..., current {current_hash[:8]}... File was modified externally.",
                )

        # Patch size limit (100 KB)
        patch_bytes = len(proposal.replacement_text.encode("utf-8"))
        self.safety.validate_patch_size(patch_bytes)

        # Prohibited content check
        self.safety.validate_prohibited_content(proposal.replacement_text)

        # Line range validation if REPLACE_RANGE
        if proposal.operation == EditOperation.REPLACE_RANGE:
            if proposal.start_line is not None and proposal.end_line is not None:
                if proposal.start_line > proposal.end_line or proposal.start_line < 1:
                    raise AndroidSafetyError(
                        AndroidErrorCode.EDIT_VALIDATION_FAILED,
                        f"Invalid line range [{proposal.start_line}, {proposal.end_line}].",
                    )

        return resolved

    # -------------------------------------------------------------------------
    # Apply Single Edit
    # -------------------------------------------------------------------------

    def apply_edit(self, proposal: EditProposal) -> EditResult:
        """
        Applies a validated edit proposal to disk with backup creation.
        """
        self.safety.check_emergency_stop()

        # Audit proposal
        self.audit.log_event(
            "ANDROID_CODE_EDIT_PROPOSED",
            {
                "proposal_id": proposal.proposal_id,
                "file": str(proposal.file_path),
                "operation": proposal.operation.value,
                "expected_hash": proposal.expected_old_hash,
                "reason": proposal.reason,
            },
        )

        resolved = self.validate_proposal(proposal)

        is_create = (proposal.operation == EditOperation.CREATE_FILE)
        old_content = ""
        backup_path = None

        if not is_create:
            old_content = resolved.read_text(encoding="utf-8", errors="replace")
            b_file = self.code_writer._create_backup(resolved)
            backup_path = str(b_file) if b_file else None

        old_hash = self.calculate_hash(old_content) if old_content else ""
        new_content = ""

        if proposal.operation == EditOperation.CREATE_FILE:
            new_content = proposal.replacement_text

        elif proposal.operation == EditOperation.REPLACE_RANGE:
            lines = old_content.splitlines()
            start = proposal.start_line or 1
            end = proposal.end_line or len(lines)
            if start < 1 or end > len(lines) or start > end:
                raise AndroidSafetyError(
                    AndroidErrorCode.EDIT_VALIDATION_FAILED,
                    f"Invalid line range [{start}, {end}] for file with {len(lines)} lines.",
                )
            if (end - start + 1) > MAX_LINES_PER_EDIT:
                raise AndroidSafetyError(
                    AndroidErrorCode.PATCH_TOO_LARGE,
                    f"Lines changed ({end - start + 1}) exceeds limit of {MAX_LINES_PER_EDIT}.",
                )
            rep_lines = proposal.replacement_text.splitlines()
            before = lines[: start - 1]
            after = lines[end:]
            new_content = "\n".join(before + rep_lines + after)
            if old_content.endswith("\n"):
                new_content += "\n"

        elif proposal.operation == EditOperation.REPLACE_EXACT:
            target = proposal.target_snippet or ""
            if not target or target not in old_content:
                raise AndroidSafetyError(
                    AndroidErrorCode.EDIT_VALIDATION_FAILED,
                    f"Target snippet not found in '{resolved.name}'.",
                )
            new_content = old_content.replace(target, proposal.replacement_text, 1)

        elif proposal.operation == EditOperation.INSERT_AFTER:
            target = proposal.target_snippet or ""
            if target and target in old_content:
                idx = old_content.find(target) + len(target)
                new_content = old_content[:idx] + "\n" + proposal.replacement_text + old_content[idx:]
            else:
                raise AndroidSafetyError(
                    AndroidErrorCode.EDIT_VALIDATION_FAILED,
                    f"Target snippet for INSERT_AFTER not found in '{resolved.name}'.",
                )

        elif proposal.operation == EditOperation.INSERT_BEFORE:
            target = proposal.target_snippet or ""
            if target and target in old_content:
                idx = old_content.find(target)
                new_content = old_content[:idx] + proposal.replacement_text + "\n" + old_content[idx:]
            else:
                raise AndroidSafetyError(
                    AndroidErrorCode.EDIT_VALIDATION_FAILED,
                    f"Target snippet for INSERT_BEFORE not found in '{resolved.name}'.",
                )
        else:
            raise AndroidSafetyError(
                AndroidErrorCode.ACTION_NOT_ALLOWED,
                f"Unsupported edit operation: {proposal.operation}",
            )

        # Check total new file size (<= 1 MB)
        if len(new_content.encode("utf-8")) > MAX_EDITABLE_FILE_SIZE_BYTES:
            raise AndroidSafetyError(
                AndroidErrorCode.FILE_TOO_LARGE,
                "Resulting file exceeds 1 MB limit.",
            )

        # Write to disk
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(new_content, encoding="utf-8")
        new_hash = self.calculate_hash(new_content)
        diff_str = self.generate_diff(str(resolved), old_content, new_content)
        lines_changed = abs(len(new_content.splitlines()) - len(old_content.splitlines()))

        # Audit log
        self.audit.log_event(
            "ANDROID_CODE_EDIT_APPLIED",
            {
                "proposal_id": proposal.proposal_id,
                "file": str(resolved),
                "operation": proposal.operation.value,
                "old_hash": old_hash,
                "new_hash": new_hash,
                "backup": backup_path,
                "reason": proposal.reason,
            },
        )

        return EditResult(
            success=True,
            proposal_id=proposal.proposal_id,
            file_path=str(resolved),
            old_hash=old_hash,
            new_hash=new_hash,
            diff=diff_str,
            backup_path=backup_path,
            backup_ids=[backup_path] if backup_path else [],
            modified_files=[str(resolved)],
            lines_changed=lines_changed,
        )

    # -------------------------------------------------------------------------
    # Apply Batch Edits (apply_edits)
    # -------------------------------------------------------------------------

    def apply_edits(self, proposals: Union[EditProposal, List[EditProposal]]) -> EditResult:
        """
        Applies an atomic batch of EditProposals:
        - Rejects proposals modifying > 5 files with TOO_MANY_FILES_CHANGED.
        - Pre-validates all edits.
        - Applies each edit; if any fails, rolls back all applied edits in this batch.
        """
        if isinstance(proposals, EditProposal):
            if proposals.edits:
                proplist = list(proposals.edits)
            else:
                proplist = [proposals]
        elif isinstance(proposals, list):
            proplist = proposals
        else:
            proplist = [proposals]

        # Convert raw dicts or EditOperation objects if needed
        converted: List[EditProposal] = []
        for p in proplist:
            if isinstance(p, EditProposal):
                converted.append(p)
            elif isinstance(p, dict):
                converted.append(EditProposal(**p))
            else:
                # Fallback
                converted.append(p)
        proplist = converted

        # Check maximum files changed (capped at 5)
        distinct_files = set(str(p.file_path) for p in proplist)
        if len(distinct_files) > MAX_FILES_PER_REPAIR:
            raise AndroidSafetyError(
                AndroidErrorCode.TOO_MANY_FILES_CHANGED,
                f"Proposed change modifies {len(distinct_files)} files, exceeding limit of {MAX_FILES_PER_REPAIR}.",
            )

        applied: List[EditResult] = []
        combined_diffs: List[str] = []
        backup_ids: List[str] = []

        try:
            for p in proplist:
                res = self.apply_edit(p)
                applied.append(res)
                if res.diff:
                    combined_diffs.append(res.diff)
                if res.backup_path:
                    backup_ids.append(res.backup_path)

            return EditResult(
                success=True,
                proposal_id=proplist[0].proposal_id if proplist else "",
                file_path=str(proplist[0].file_path) if proplist else "",
                old_hash=applied[0].old_hash if applied else "",
                new_hash=applied[-1].new_hash if applied else "",
                diff="\n".join(combined_diffs),
                backup_path=backup_ids[0] if backup_ids else None,
                backup_ids=backup_ids,
                modified_files=[r.file_path for r in applied],
                lines_changed=sum(r.lines_changed for r in applied),
            )
        except Exception:
            if applied:
                self.rollback(applied)
            raise

    # -------------------------------------------------------------------------
    # Rollback
    # -------------------------------------------------------------------------

    def rollback(self, target: Any, target_file: Optional[str] = None) -> bool:
        """
        Rolls back applied edits safely within authorized project root.
        Supports:
        - rollback(applied_edits: List[EditResult])
        - rollback(backup_path: str, target_file: str)
        - rollback(edit: EditResult)
        """
        if isinstance(target, str) and target_file:
            res = self.code_writer.restore_backup(target_file, target)
            success = bool(res.get("success", False))
            self.audit.log_event(
                "ANDROID_CODE_ROLLBACK",
                {
                    "files_rolled_back": [target_file],
                    "all_restored": success,
                },
            )
            return success

        applied_edits = target if isinstance(target, list) else [target]
        all_restored = True
        rolled_back_paths = []

        for edit in reversed(applied_edits):
            target_path = Path(edit.file_path).resolve()
            try:
                target_path.relative_to(self.project_path)
            except ValueError:
                logger.error(f"Cannot rollback file outside project: {target_path}")
                all_restored = False
                continue

            if edit.backup_path and Path(edit.backup_path).exists():
                res = self.code_writer.restore_backup(str(target_path), edit.backup_path)
                if not res.get("success"):
                    all_restored = False
                else:
                    rolled_back_paths.append(str(target_path))
            elif not edit.old_hash:
                # Newly created file, remove
                try:
                    if target_path.exists():
                        target_path.unlink()
                        rolled_back_paths.append(str(target_path))
                except Exception as e:
                    logger.error(f"Failed to remove created file during rollback: {e}")
                    all_restored = False

        self.audit.log_event(
            "ANDROID_CODE_ROLLBACK",
            {
                "files_rolled_back": rolled_back_paths,
                "all_restored": all_restored,
            },
        )
        return all_restored

    # -------------------------------------------------------------------------
    # Code Inspection
    # -------------------------------------------------------------------------

    def inspect_code_context(
        self,
        file_path: Optional[str] = None,
        max_lines: int = 200,
    ) -> Dict[str, Any]:
        """
        Bounded, safe inspection of authorized Android project code.
        """
        self.safety.check_emergency_stop()

        if file_path:
            resolved = self.safety.validate_editable_file(file_path)
            content = resolved.read_text(encoding="utf-8", errors="replace")
            lines = content.splitlines()
            bounded_content = "\n".join(lines[:max_lines])
            return {
                "file_path": str(resolved),
                "name": resolved.name,
                "size_bytes": resolved.stat().st_size,
                "lines_count": len(lines),
                "hash": self.calculate_hash(content),
                "is_truncated": len(lines) > max_lines,
                "content_sample": bounded_content,
            }

        source_files = []
        for ext in ALLOWED_ANDROID_EXTENSIONS:
            for p in self.project_path.rglob(f"*{ext}"):
                p_str = str(p)
                if "build" in p_str or ".gradle" in p_str or "scratch" in p_str:
                    continue
                try:
                    rel = p.relative_to(self.project_path)
                    source_files.append({
                        "path": str(rel),
                        "name": p.name,
                        "size_bytes": p.stat().st_size,
                    })
                except ValueError:
                    pass

        return {
            "project_path": str(self.project_path),
            "package_name": self.safety.authorized_package,
            "total_source_files": len(source_files),
            "source_files": source_files[:50],
        }

    # -------------------------------------------------------------------------
    # Build Error Repair Loop
    # -------------------------------------------------------------------------

    def repair_build(
        self,
        user_goal: str = "Fix build errors",
        max_attempts: int = MAX_REPAIR_ATTEMPTS,
        deterministic_patch_provider: Optional[Callable] = None,
    ) -> RepairResult:
        """
        Bounded, safe self-healing repair loop:
        Build -> Parse Error -> Propose Repair -> Validate Patch -> Apply -> Rebuild -> Verify/Rollback.
        Strictly capped at max_attempts (default: 2).
        """
        self.safety.check_emergency_stop()
        logger.info(f"Starting Android build repair loop: '{user_goal}' (Max attempts: {max_attempts})")

        # 1. Initial Build Check
        initial_build = self.gradle.run_action("DEBUG_ASSEMBLE")
        if initial_build.get("success"):
            return RepairResult(
                success=True,
                attempts=0,
                summary="Build already clean. No errors found.",
            )

        # 2. Parse Initial Error
        raw_output = initial_build.get("full_output") or initial_build.get("output_sample", "") or str(initial_build.get("diagnosis", ""))
        initial_error = self.analyzer.analyze_build_output(raw_output, self.project_path)
        logger.info(f"Initial build error detected: {initial_error.category.value} - {initial_error.message}")

        # Check for unsupported error domains (keystores, credentials, dangerous permissions, etc.)
        if self.safety.is_unsupported_error(initial_error.category, initial_error.message, initial_error.file_path):
            logger.warning(f"Initial error '{initial_error.category.value}' is unsupported for autonomous repair.")
            return RepairResult(
                success=False,
                attempts=0,
                initial_error=initial_error,
                final_error=initial_error,
                summary=f"Build error in category '{initial_error.category.value}' is unsupported for autonomous repair (high-risk or security boundary).",
                error_code=AndroidErrorCode.REPAIR_UNSUPPORTED.value,
                rolled_back=False,
            )

        applied_edits: List[EditResult] = []
        current_error = initial_error
        attempts_run = 0

        bounded_max = min(max_attempts, MAX_REPAIR_ATTEMPTS)

        while attempts_run < bounded_max:
            attempts_run += 1
            self.safety.check_emergency_stop()
            logger.info(f"[Repair Attempt {attempts_run}/{bounded_max}] Addressing {current_error.category.value}")

            # Check if current error is unsupported
            if self.safety.is_unsupported_error(current_error.category, current_error.message, current_error.file_path):
                logger.warning(f"Error '{current_error.category.value}' is unsupported for autonomous repair.")
                return RepairResult(
                    success=False,
                    attempts=attempts_run,
                    initial_error=initial_error,
                    final_error=current_error,
                    applied_edits=applied_edits,
                    summary=f"Build error in category '{current_error.category.value}' is unsupported for autonomous repair.",
                    error_code=AndroidErrorCode.REPAIR_UNSUPPORTED.value,
                    rolled_back=False,
                )

            if not current_error.file_path:
                logger.warning(f"Repair attempt {attempts_run} aborted: No specific source file identified in error.")
                break

            raw_fp = str(current_error.file_path)
            if raw_fp.startswith(("/", "\\")):
                err_file = (self.project_path / raw_fp.lstrip("/\\")).resolve()
            elif not Path(current_error.file_path).is_absolute():
                err_file = (self.project_path / Path(current_error.file_path)).resolve()
            else:
                err_file = Path(current_error.file_path).resolve()

            try:
                self.safety.validate_editable_file(err_file)
            except AndroidSafetyError as se:
                logger.warning(f"Cannot repair protected or uneditable file '{err_file}': {se.message}")
                break

            # 3. Create Repair Proposal
            proposal = None
            if deterministic_patch_provider:
                source_text = err_file.read_text(encoding="utf-8", errors="replace") if err_file.exists() else ""
                try:
                    proposal = deterministic_patch_provider(current_error, attempts_run)
                except TypeError:
                    proposal = deterministic_patch_provider(current_error, source_text)

            if not proposal:
                try:
                    proposal = self._consult_model_for_repair(current_error, err_file)
                except AndroidSafetyError as se:
                    logger.warning(f"Model repair proposal rejected: {se.message}")
                    if applied_edits:
                        self.rollback(applied_edits)
                    return RepairResult(
                        success=False,
                        attempts=attempts_run,
                        initial_error=initial_error,
                        final_error=current_error,
                        applied_edits=applied_edits,
                        summary=f"Model repair proposal rejected: {se.message}",
                        error_code=se.code.value,
                        rolled_back=bool(applied_edits),
                    )

            if not proposal:
                logger.warning(f"No repair proposal generated for {current_error.category.value}.")
                break

            # 4. Apply Proposed Edit with Backup
            try:
                if isinstance(proposal, list) or (isinstance(proposal, EditProposal) and proposal.edits):
                    edit_res = self.apply_edits(proposal)
                else:
                    edit_res = self.apply_edit(proposal)

                if not edit_res.success:
                    logger.warning(f"Repair proposal failed: {edit_res.error}")
                    break
                applied_edits.append(edit_res)
            except AndroidSafetyError as se:
                logger.warning(f"Repair proposal rejected by safety gate: {se.message}")
                if applied_edits:
                    self.rollback(applied_edits)
                return RepairResult(
                    success=False,
                    attempts=attempts_run,
                    initial_error=initial_error,
                    final_error=current_error,
                    applied_edits=applied_edits,
                    summary=f"Repair proposal rejected by safety gate: {se.message}",
                    error_code=se.code.value,
                    rolled_back=bool(applied_edits),
                )

            # 5. Rebuild
            rebuild_res = self.gradle.run_action("DEBUG_ASSEMBLE")
            if rebuild_res.get("success"):
                logger.info(f"Build repair succeeded on attempt {attempts_run}!")
                return RepairResult(
                    success=True,
                    attempts=attempts_run,
                    repaired_files=[e.file_path for e in applied_edits],
                    initial_error=initial_error,
                    final_error=None,
                    applied_edits=applied_edits,
                    summary=f"Build repaired successfully in {attempts_run} attempt(s).",
                )

            # If rebuild failed, re-analyze
            raw_output = rebuild_res.get("full_output") or rebuild_res.get("output_sample", "") or str(rebuild_res.get("diagnosis", ""))
            current_error = self.analyzer.analyze_build_output(raw_output, self.project_path)

        # 6. Repair Failed -> Automatic Rollback of All Applied Edits
        logger.warning(f"Build repair failed after {attempts_run} attempts. Initiating rollback.")
        if applied_edits:
            self.rollback(applied_edits)

        return RepairResult(
            success=False,
            attempts=attempts_run,
            repaired_files=[e.file_path for e in applied_edits],
            initial_error=initial_error,
            final_error=current_error,
            applied_edits=applied_edits,
            summary=f"Build repair failed after {attempts_run} bounded attempt(s). Rollback completed.",
            error_code=AndroidErrorCode.REPAIR_FAILED.value,
            rolled_back=True,
        )

    def _consult_model_for_repair(self, error: AndroidBuildError, file_path: Path) -> Optional[EditProposal]:
        """
        Consults advisory model for repair patch proposal.
        The model provides structured proposal data only; never touches disk.
        """
        self.safety.check_emergency_stop()

        # Step 1: Bounded context extraction
        context = extract_bounded_context(error, self.project_path)

        # Step 2: Build prompts
        sys_prompt, user_prompt = build_model_repair_prompt(error, context)

        try:
            # Step 3: Route and execute through ModelRouter
            logger.info(f"Consulting advisory model for {error.category.value} in {file_path.name}")
            response = self.router.execute(
                prompt=user_prompt,
                system_prompt=sys_prompt,
                task_type="highest",
                required_capabilities={ModelCapability.CODING, ModelCapability.REASONING},
            )

            if not response.get("success"):
                err_msg = response.get("error", "Unknown model execution error")
                logger.warning(f"Advisory model repair request failed: {err_msg}")
                return None

            content = response.get("content", "")
            if not content:
                logger.warning("Advisory model returned empty content.")
                return None

            # Step 4: Parse and validate response
            return self.parse_model_proposal(content)

        except AndroidSafetyError:
            raise
        except Exception as e:
            logger.warning(f"Error during advisory model consultation: {e}")
            return None
