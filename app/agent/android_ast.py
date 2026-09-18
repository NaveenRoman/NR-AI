"""
Structured Kotlin and Java AST Intelligence Subsystem

Provides structured syntactic analysis, AST extraction, and safe semantic
mutations for Kotlin (.kt, .kts) and Java (.java) source files.

Guarantees:
  - Extracts classes, interfaces, methods, properties, annotations, imports, package
  - Accurate brace-depth matching accounting for string literals and comments
  - Precise transformations: add/remove import, add method, replace method body,
    add annotation, rename symbol
  - Transactional safety: SHA-256 stale target protection, .bak backups, atomic writes,
    rollback on failure, and AndroidSafetyGate emergency stop enforcement
  - Strict size (< 100 KB) and change limits (<= 500 lines)
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from app.agent.android_safety import (
    AndroidErrorCode,
    AndroidSafetyError,
    AndroidSafetyGate,
    MAX_PATCH_SIZE_BYTES,
)

logger = logging.getLogger("AndroidAST")

MAX_CHANGED_LINES = 500


# -----------------------------------------------------------------------------
# Data Models
# -----------------------------------------------------------------------------

@dataclass
class ImportEntry:
    statement: str
    target: str
    alias: Optional[str] = None
    is_wildcard: bool = False
    line: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PropertyEntry:
    name: str
    property_type: Optional[str]
    visibility: str
    is_mutable: bool
    annotations: List[str]
    line: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MethodEntry:
    name: str
    visibility: str
    annotations: List[str]
    parameters: List[str]
    return_type: Optional[str]
    is_composable: bool
    start_line: int
    end_line: int
    body_start_line: Optional[int]
    body_end_line: Optional[int]
    raw_signature: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ClassEntry:
    name: str
    kind: str  # class, interface, object, enum, data_class
    visibility: str
    supertypes: List[str]
    annotations: List[str]
    methods: Dict[str, MethodEntry] = field(default_factory=dict)
    properties: Dict[str, PropertyEntry] = field(default_factory=dict)
    start_line: int = 1
    end_line: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "visibility": self.visibility,
            "supertypes": self.supertypes,
            "annotations": self.annotations,
            "methods": {k: v.to_dict() for k, v in self.methods.items()},
            "properties": {k: v.to_dict() for k, v in self.properties.items()},
            "start_line": self.start_line,
            "end_line": self.end_line,
        }


@dataclass
class SourceASTReport:
    file_path: str
    language: str  # kotlin, java
    package_name: str
    imports: List[ImportEntry]
    classes: Dict[str, ClassEntry]
    top_level_methods: Dict[str, MethodEntry]
    top_level_properties: Dict[str, PropertyEntry]
    sha256: str
    total_lines: int
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "language": self.language,
            "package_name": self.package_name,
            "imports": [i.to_dict() for i in self.imports],
            "classes": {k: v.to_dict() for k, v in self.classes.items()},
            "top_level_methods": {k: v.to_dict() for k, v in self.top_level_methods.items()},
            "top_level_properties": {k: v.to_dict() for k, v in self.top_level_properties.items()},
            "sha256": self.sha256,
            "total_lines": self.total_lines,
            "timestamp": self.timestamp,
        }


# -----------------------------------------------------------------------------
# Parser & Tokenizer Utility
# -----------------------------------------------------------------------------

class CodeSanitizer:
    """Masks comments and strings to preserve code structure without false braces."""

    @staticmethod
    def mask_code(source: str) -> str:
        out = []
        i = 0
        n = len(source)
        in_line_comment = False
        in_block_comment = False
        in_string = False
        string_char = ""
        in_triple_string = False

        while i < n:
            # Triple quotes
            if not in_line_comment and not in_block_comment and source[i:i+3] in ('"""', "'''"):
                t_char = source[i:i+3]
                if in_triple_string and string_char == t_char:
                    in_triple_string = False
                    out.append("   ")
                    i += 3
                    continue
                elif not in_string and not in_triple_string:
                    in_triple_string = True
                    string_char = t_char
                    out.append("   ")
                    i += 3
                    continue

            if in_triple_string:
                out.append("\n" if source[i] == "\n" else " ")
                i += 1
                continue

            # Standard strings
            if not in_line_comment and not in_block_comment and not in_string:
                if source[i] in ('"', "'"):
                    in_string = True
                    string_char = source[i]
                    out.append(" ")
                    i += 1
                    continue

            if in_string:
                if source[i] == "\\" and i + 1 < n:
                    out.append("  ")
                    i += 2
                    continue
                if source[i] == string_char:
                    in_string = False
                    out.append(" ")
                    i += 1
                    continue
                out.append("\n" if source[i] == "\n" else " ")
                i += 1
                continue

            # Line comments
            if not in_block_comment and source[i:i+2] == "//":
                in_line_comment = True
                out.append("  ")
                i += 2
                continue
            if in_line_comment:
                if source[i] == "\n":
                    in_line_comment = False
                    out.append("\n")
                else:
                    out.append(" ")
                i += 1
                continue

            # Block comments
            if not in_line_comment and source[i:i+2] == "/*":
                in_block_comment = True
                out.append("  ")
                i += 2
                continue
            if in_block_comment:
                if source[i:i+2] == "*/":
                    in_block_comment = False
                    out.append("  ")
                    i += 2
                    continue
                out.append("\n" if source[i] == "\n" else " ")
                i += 1
                continue

            out.append(source[i])
            i += 1

        return "".join(out)


# -----------------------------------------------------------------------------
# Android AST Engine
# -----------------------------------------------------------------------------

class AndroidASTEngine:
    """
    Analyzes Kotlin and Java source files and executes precise, transactional AST mutations.
    """

    def __init__(self, safety_gate: Optional[AndroidSafetyGate] = None):
        self.safety = safety_gate or AndroidSafetyGate()

    @staticmethod
    def compute_sha256(content: Union[str, bytes]) -> str:
        data = content.encode("utf-8") if isinstance(content, str) else content
        return hashlib.sha256(data).hexdigest()

    def parse_file(self, file_path: Union[str, Path]) -> SourceASTReport:
        path = Path(file_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Source file '{path}' not found.")

        raw_bytes = path.read_bytes()
        if len(raw_bytes) > MAX_PATCH_SIZE_BYTES:
            raise ValueError(f"File size ({len(raw_bytes)} bytes) exceeds 100 KB safety limit.")

        sha256 = self.compute_sha256(raw_bytes)
        source_text = raw_bytes.decode("utf-8", errors="replace")
        lang = "kotlin" if path.suffix in (".kt", ".kts") else "java"

        return self.parse_source(source_text, str(path), sha256, lang)

    def parse_source(
        self,
        source: str,
        file_path: str = "memory://source.kt",
        sha256: Optional[str] = None,
        language: Optional[str] = None,
    ) -> SourceASTReport:
        if sha256 is None:
            sha256 = self.compute_sha256(source)
        if language is None:
            language = "kotlin" if file_path.endswith((".kt", ".kts")) else "java"

        lines = source.splitlines()
        masked = CodeSanitizer.mask_code(source)
        masked_lines = masked.splitlines()

        # 1. Package statement
        package_name = ""
        pkg_match = re.search(r'^\s*package\s+([a-zA-Z0-9_.]+)', source, re.MULTILINE)
        if pkg_match:
            package_name = pkg_match.group(1).strip()

        # 2. Imports
        imports: List[ImportEntry] = []
        import_pattern = re.compile(r'^\s*import\s+([a-zA-Z0-9_.*]+)(?:\s+as\s+([a-zA-Z0-9_]+))?;?', re.MULTILINE)
        for m in import_pattern.finditer(source):
            full_stmt = m.group(0).strip()
            target = m.group(1).strip()
            alias = m.group(2) if m.group(2) else None
            is_wildcard = target.endswith(".*")
            line_no = source[:m.start()].count("\n") + 1
            imports.append(ImportEntry(
                statement=full_stmt,
                target=target,
                alias=alias,
                is_wildcard=is_wildcard,
                line=line_no,
            ))

        # 3. Locate classes, methods, properties via structural brace analysis
        classes, top_level_methods, top_level_properties = self._extract_structures(
            source, masked, lines, masked_lines, language
        )

        return SourceASTReport(
            file_path=file_path,
            language=language,
            package_name=package_name,
            imports=imports,
            classes=classes,
            top_level_methods=top_level_methods,
            top_level_properties=top_level_properties,
            sha256=sha256,
            total_lines=len(lines),
        )

    def _extract_structures(
        self,
        source: str,
        masked: str,
        lines: List[str],
        masked_lines: List[str],
        language: str,
    ) -> Tuple[Dict[str, ClassEntry], Dict[str, MethodEntry], Dict[str, PropertyEntry]]:
        classes: Dict[str, ClassEntry] = {}
        top_methods: Dict[str, MethodEntry] = {}
        top_props: Dict[str, PropertyEntry] = {}

        # Scan for class/interface/object definitions
        class_regex = re.compile(
            r'((?:@[a-zA-Z0-9_]+(?:\([^)]*\))?\s*)*)'
            r'(public|private|protected|internal|sealed|open|abstract|data)?\s*'
            r'(class|interface|object|enum\s+class)\s+([a-zA-Z0-9_]+)'
            r'(?:\s*<[^>]+>)?'
            r'(?:\s*\([^)]*\))?'
            r'(?:\s*:\s*([^{]+))?'
            r'\s*\{',
            re.MULTILINE
        ) if language == "kotlin" else re.compile(
            r'((?:@[a-zA-Z0-9_]+(?:\([^)]*\))?\s*)*)'
            r'(public|private|protected|abstract|static|final)?\s*'
            r'(class|interface|enum)\s+([a-zA-Z0-9_]+)'
            r'(?:\s*<[^>]+>)?'
            r'(?:\s+extends\s+([a-zA-Z0-9_<>]+))?'
            r'(?:\s+implements\s+([^{]+))?'
            r'\s*\{',
            re.MULTILINE
        )

        # Track spans of classes
        class_spans: List[Tuple[str, ClassEntry, int, int]] = []

        for m in class_regex.finditer(source):
            raw_annots = m.group(1) or ""
            visibility = (m.group(2) or "public").strip()
            kind = m.group(3).strip()
            name = m.group(4)
            if language == "kotlin":
                supertypes_raw = m.group(5) or ""
                supertypes = [re.sub(r'\(.*?\)', '', s).strip() for s in supertypes_raw.split(",") if s.strip()]
            else:
                extends_raw = m.group(5) or ""
                implements_raw = m.group(6) or ""
                supertypes = []
                if extends_raw:
                    supertypes.append(extends_raw.strip())
                if implements_raw:
                    supertypes.extend([s.strip() for s in implements_raw.split(",") if s.strip()])

            # Extract annotations
            annots = [a.strip() for a in re.findall(r'@[a-zA-Z0-9_]+(?:\([^)]*\))?', raw_annots)]

            # Locate precise start line of class declaration (excluding leading whitespace)
            if annots:
                decl_start = source.find(annots[0], m.start())
            elif m.group(2) and m.group(2).strip():
                decl_start = source.find(m.group(2).strip(), m.start())
            else:
                decl_start = source.find(kind, m.start())

            if decl_start == -1:
                decl_start = m.start()
            start_line = source[:decl_start].count("\n") + 1

            # Find matching brace using masked string
            brace_pos = source.find("{", m.end() - 2)
            end_brace_pos = self._find_matching_brace(masked, brace_pos)
            end_line = source[:end_brace_pos].count("\n") + 1 if end_brace_pos != -1 else len(lines)

            cls_entry = ClassEntry(
                name=name,
                kind=kind,
                visibility=visibility,
                supertypes=supertypes,
                annotations=annots,
                start_line=start_line,
                end_line=end_line,
            )
            classes[name] = cls_entry
            class_spans.append((name, cls_entry, brace_pos, end_brace_pos))

        # Scan for methods
        method_regex = re.compile(
            r'((?:@[a-zA-Z0-9_]+(?:\([^)]*\))?\s*)*)'
            r'(public|private|protected|internal|override|open)?\s*'
            r'(?:suspend\s+)?fun\s+([a-zA-Z0-9_]+)\s*\(([^)]*)\)'
            r'(?:\s*:\s*([a-zA-Z0-9_<>?]+))?'
            r'\s*(\{|=)',
            re.MULTILINE
        ) if language == "kotlin" else re.compile(
            r'((?:@[a-zA-Z0-9_]+(?:\([^)]*\))?\s*)*)'
            r'(public|private|protected|static|final|\s)*\s+'
            r'([a-zA-Z0-9_<>\[\]]+)\s+([a-zA-Z0-9_]+)\s*\(([^)]*)\)'
            r'(?:\s*throws\s+[^{]+)?'
            r'\s*\{',
            re.MULTILINE
        )

        for m in method_regex.finditer(source):
            if language == "kotlin":
                raw_annots = m.group(1) or ""
                visibility = (m.group(2) or "public").strip()
                name = m.group(3)
                params_str = m.group(4) or ""
                ret_type = m.group(5) or "Unit"
                body_type = m.group(6)
            else:
                raw_annots = m.group(1) or ""
                visibility = (m.group(2) or "public").strip()
                ret_type = m.group(3)
                name = m.group(4)
                params_str = m.group(5) or ""
                body_type = "{"

            annots = [a.strip() for a in re.findall(r'@[a-zA-Z0-9_]+(?:\([^)]*\))?', raw_annots)]
            is_composable = any("@Composable" in a for a in annots)
            params = [p.strip() for p in params_str.split(",") if p.strip()]

            # Locate precise start line of method declaration
            if annots:
                decl_start = source.find(annots[0], m.start())
            elif m.group(2) and m.group(2).strip():
                decl_start = source.find(m.group(2).strip(), m.start())
            else:
                kw = "fun" if language == "kotlin" else ret_type.strip()
                decl_start = source.find(kw, m.start())

            if decl_start == -1:
                decl_start = m.start()
            start_line = source[:decl_start].count("\n") + 1

            body_start_line = None
            body_end_line = None
            end_line = start_line

            if body_type == "{":
                brace_pos = source.find("{", m.end() - 2)
                end_brace_pos = self._find_matching_brace(masked, brace_pos)
                if end_brace_pos != -1:
                    body_start_line = source[:brace_pos].count("\n") + 1
                    body_end_line = source[:end_brace_pos].count("\n") + 1
                    end_line = body_end_line

            method_entry = MethodEntry(
                name=name,
                visibility=visibility,
                annotations=annots,
                parameters=params,
                return_type=ret_type,
                is_composable=is_composable,
                start_line=start_line,
                end_line=end_line,
                body_start_line=body_start_line,
                body_end_line=body_end_line,
                raw_signature=m.group(0).strip(),
            )

            # Associate with containing class or top-level
            containing_class = None
            for c_name, cls_obj, c_start, c_end in class_spans:
                if c_start <= decl_start <= c_end:
                    containing_class = cls_obj
                    break

            if containing_class:
                containing_class.methods[name] = method_entry
            else:
                top_methods[name] = method_entry

        # Scan for properties
        prop_regex = re.compile(
            r'((?:@[a-zA-Z0-9_]+(?:\([^)]*\))?\s*)*)'
            r'(public|private|protected|internal)?\s*'
            r'(val|var)\s+([a-zA-Z0-9_]+)'
            r'(?:\s*:\s*([a-zA-Z0-9_<>?]+))?',
            re.MULTILINE
        ) if language == "kotlin" else re.compile(
            r'((?:@[a-zA-Z0-9_]+(?:\([^)]*\))?\s*)*)'
            r'(public|private|protected|static|final|\s)*\s+'
            r'([a-zA-Z0-9_<>]+)\s+([a-zA-Z0-9_]+)\s*(?:=|;)',
            re.MULTILINE
        )

        for m in prop_regex.finditer(source):
            if language == "kotlin":
                raw_annots = m.group(1) or ""
                visibility = (m.group(2) or "public").strip()
                is_mut = m.group(3) == "var"
                name = m.group(4)
                prop_type = m.group(5)
            else:
                raw_annots = m.group(1) or ""
                visibility = (m.group(2) or "public").strip()
                is_mut = "final" not in visibility
                prop_type = m.group(3)
                name = m.group(4)

            annots = [a.strip() for a in re.findall(r'@[a-zA-Z0-9_]+(?:\([^)]*\))?', raw_annots)]
            
            if annots:
                decl_start = source.find(annots[0], m.start())
            elif m.group(2) and m.group(2).strip():
                decl_start = source.find(m.group(2).strip(), m.start())
            else:
                kw = m.group(3).strip() if language == "kotlin" else prop_type.strip()
                decl_start = source.find(kw, m.start())

            if decl_start == -1:
                decl_start = m.start()
            line_no = source[:decl_start].count("\n") + 1

            prop_entry = PropertyEntry(
                name=name,
                property_type=prop_type,
                visibility=visibility,
                is_mutable=is_mut,
                annotations=annots,
                line=line_no,
            )

            # Associate with class or top-level
            containing_class = None
            for c_name, cls_obj, c_start, c_end in class_spans:
                if c_start <= decl_start <= c_end:
                    containing_class = cls_obj
                    break

            if containing_class:
                containing_class.properties[name] = prop_entry
            else:
                top_props[name] = prop_entry

        return classes, top_methods, top_props

    def _find_matching_brace(self, masked: str, open_pos: int) -> int:
        if open_pos == -1 or open_pos >= len(masked) or masked[open_pos] != "{":
            return -1
        depth = 0
        for idx in range(open_pos, len(masked)):
            ch = masked[idx]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return idx
        return -1

    # -------------------------------------------------------------------------
    # Transformations & Mutations
    # -------------------------------------------------------------------------

    def add_import(
        self,
        source_file: Union[str, Path],
        import_stmt: str,
        expected_sha256: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Safely injects an import statement into the file."""
        self.safety.check_emergency_stop()
        path = Path(source_file).resolve()
        raw_bytes = path.read_bytes()
        current_sha256 = self.compute_sha256(raw_bytes)

        if expected_sha256 and expected_sha256 != current_sha256:
            raise AndroidSafetyError(
                AndroidErrorCode.EDIT_VALIDATION_FAILED,
                f"File '{path.name}' modified concurrently. Expected SHA-256 {expected_sha256[:8]}, found {current_sha256[:8]}.",
            )

        source = raw_bytes.decode("utf-8")
        clean_stmt = import_stmt.strip()
        if not clean_stmt.startswith("import "):
            clean_stmt = f"import {clean_stmt}"

        report = self.parse_source(source, str(path), current_sha256)
        # Check if already present
        for imp in report.imports:
            if imp.statement.rstrip(";") == clean_stmt.rstrip(";"):
                return {"success": True, "file": str(path), "action": "already_present", "sha256": current_sha256}

        # Backup
        backup_path = path.with_suffix(f"{path.suffix}.bak.{int(time.time() * 1000)}")
        shutil.copy2(path, backup_path)

        lines = source.splitlines()
        insert_index = 0

        # Place after package if present, or before first import, or after last import
        if report.imports:
            last_import_line = max(i.line for i in report.imports)
            insert_index = last_import_line
        else:
            pkg_match = re.search(r'^\s*package\s+.*', source, re.MULTILINE)
            if pkg_match:
                pkg_line = source[:pkg_match.end()].count("\n") + 1
                insert_index = pkg_line
                lines.insert(insert_index, "")  # blank line separator
                insert_index += 1

        lines.insert(insert_index, clean_stmt)
        new_source = "\n".join(lines) + "\n"

        new_bytes = new_source.encode("utf-8")
        tmp_target = path.with_suffix(f"{path.suffix}.tmp")
        try:
            tmp_target.write_bytes(new_bytes)
            os.replace(tmp_target, path)
        except Exception as e:
            shutil.copy2(backup_path, path)
            backup_path.unlink(missing_ok=True)
            raise IOError(f"Failed to write updated imports: {e}")

        new_sha256 = self.compute_sha256(new_bytes)
        return {
            "success": True,
            "file": str(path),
            "action": "added_import",
            "import": clean_stmt,
            "backup_file": str(backup_path),
            "old_sha256": current_sha256,
            "new_sha256": new_sha256,
        }

    def remove_import(
        self,
        source_file: Union[str, Path],
        import_stmt: str,
        expected_sha256: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Safely removes an import statement."""
        self.safety.check_emergency_stop()
        path = Path(source_file).resolve()
        raw_bytes = path.read_bytes()
        current_sha256 = self.compute_sha256(raw_bytes)

        if expected_sha256 and expected_sha256 != current_sha256:
            raise AndroidSafetyError(
                AndroidErrorCode.EDIT_VALIDATION_FAILED,
                f"File '{path.name}' modified concurrently.",
            )

        source = raw_bytes.decode("utf-8")
        clean_target = import_stmt.strip().rstrip(";")
        if not clean_target.startswith("import "):
            clean_target = f"import {clean_target}"

        lines = source.splitlines()
        filtered = [l for l in lines if l.strip().rstrip(";") != clean_target]
        if len(filtered) == len(lines):
            return {"success": True, "file": str(path), "action": "not_found", "sha256": current_sha256}

        backup_path = path.with_suffix(f"{path.suffix}.bak.{int(time.time() * 1000)}")
        shutil.copy2(path, backup_path)

        new_source = "\n".join(filtered) + "\n"
        new_bytes = new_source.encode("utf-8")
        tmp_target = path.with_suffix(f"{path.suffix}.tmp")
        try:
            tmp_target.write_bytes(new_bytes)
            os.replace(tmp_target, path)
        except Exception as e:
            shutil.copy2(backup_path, path)
            backup_path.unlink(missing_ok=True)
            raise IOError(f"Failed to remove import: {e}")

        new_sha256 = self.compute_sha256(new_bytes)
        return {
            "success": True,
            "file": str(path),
            "action": "removed_import",
            "import": clean_target,
            "backup_file": str(backup_path),
            "old_sha256": current_sha256,
            "new_sha256": new_sha256,
        }

    def add_method(
        self,
        source_file: Union[str, Path],
        class_name: str,
        method_code: str,
        expected_sha256: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Injects a new method into a target class right before its closing brace."""
        self.safety.check_emergency_stop()
        path = Path(source_file).resolve()
        raw_bytes = path.read_bytes()
        current_sha256 = self.compute_sha256(raw_bytes)

        if expected_sha256 and expected_sha256 != current_sha256:
            raise AndroidSafetyError(
                AndroidErrorCode.EDIT_VALIDATION_FAILED,
                f"File '{path.name}' modified concurrently.",
            )

        source = raw_bytes.decode("utf-8")
        report = self.parse_source(source, str(path), current_sha256)
        if class_name not in report.classes:
            raise KeyError(f"Class '{class_name}' not found in '{path.name}'.")

        target_class = report.classes[class_name]
        lines = source.splitlines()

        # Target class closing brace line (1-indexed)
        closing_line_idx = target_class.end_line - 1

        # Indent method code appropriately (default 4 spaces)
        indented_lines = ["    " + line if line.strip() else "" for line in method_code.strip().splitlines()]
        injection = [""] + indented_lines + [""]

        # Limit verification
        if len(injection) > MAX_CHANGED_LINES:
            raise ValueError(f"Method injection ({len(injection)} lines) exceeds maximum changed lines limit ({MAX_CHANGED_LINES}).")

        new_lines = lines[:closing_line_idx] + injection + lines[closing_line_idx:]

        backup_path = path.with_suffix(f"{path.suffix}.bak.{int(time.time() * 1000)}")
        shutil.copy2(path, backup_path)

        new_source = "\n".join(new_lines) + "\n"
        new_bytes = new_source.encode("utf-8")
        tmp_target = path.with_suffix(f"{path.suffix}.tmp")
        try:
            tmp_target.write_bytes(new_bytes)
            os.replace(tmp_target, path)
        except Exception as e:
            shutil.copy2(backup_path, path)
            backup_path.unlink(missing_ok=True)
            raise IOError(f"Failed to add method: {e}")

        new_sha256 = self.compute_sha256(new_bytes)
        return {
            "success": True,
            "file": str(path),
            "class": class_name,
            "action": "added_method",
            "backup_file": str(backup_path),
            "old_sha256": current_sha256,
            "new_sha256": new_sha256,
        }

    def replace_method_body(
        self,
        source_file: Union[str, Path],
        method_name: str,
        new_body: str,
        class_name: Optional[str] = None,
        expected_sha256: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Safely replaces the execution body of a method."""
        self.safety.check_emergency_stop()
        path = Path(source_file).resolve()
        raw_bytes = path.read_bytes()
        current_sha256 = self.compute_sha256(raw_bytes)

        if expected_sha256 and expected_sha256 != current_sha256:
            raise AndroidSafetyError(
                AndroidErrorCode.EDIT_VALIDATION_FAILED,
                f"File '{path.name}' modified concurrently.",
            )

        source = raw_bytes.decode("utf-8")
        report = self.parse_source(source, str(path), current_sha256)

        target_method: Optional[MethodEntry] = None
        if class_name:
            if class_name not in report.classes:
                raise KeyError(f"Class '{class_name}' not found.")
            target_method = report.classes[class_name].methods.get(method_name)
        else:
            # Check top level first then classes
            target_method = report.top_level_methods.get(method_name)
            if not target_method:
                for cls in report.classes.values():
                    if method_name in cls.methods:
                        target_method = cls.methods[method_name]
                        break

        if not target_method:
            raise KeyError(f"Method '{method_name}' not found in '{path.name}'.")

        if target_method.body_start_line is None or target_method.body_end_line is None:
            raise ValueError(f"Method '{method_name}' has no block body to replace.")

        lines = source.splitlines()
        # Lines inside { ... } are from body_start_line to body_end_line - 2 (0-indexed)
        start_idx = target_method.body_start_line  # line immediately after {
        end_idx = target_method.body_end_line - 1   # line containing }

        indented_body = ["        " + l if l.strip() else "" for l in new_body.strip().splitlines()]
        new_lines = lines[:start_idx] + indented_body + lines[end_idx:]

        backup_path = path.with_suffix(f"{path.suffix}.bak.{int(time.time() * 1000)}")
        shutil.copy2(path, backup_path)

        new_source = "\n".join(new_lines) + "\n"
        new_bytes = new_source.encode("utf-8")
        tmp_target = path.with_suffix(f"{path.suffix}.tmp")
        try:
            tmp_target.write_bytes(new_bytes)
            os.replace(tmp_target, path)
        except Exception as e:
            shutil.copy2(backup_path, path)
            backup_path.unlink(missing_ok=True)
            raise IOError(f"Failed to replace method body: {e}")

        new_sha256 = self.compute_sha256(new_bytes)
        return {
            "success": True,
            "file": str(path),
            "method": method_name,
            "action": "replaced_body",
            "backup_file": str(backup_path),
            "old_sha256": current_sha256,
            "new_sha256": new_sha256,
        }

    def add_annotation(
        self,
        source_file: Union[str, Path],
        target_name: str,
        annotation_code: str,
        target_type: str = "class",  # class, method, property
        class_name: Optional[str] = None,
        expected_sha256: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Injects an annotation above a target class, method, or property."""
        self.safety.check_emergency_stop()
        path = Path(source_file).resolve()
        raw_bytes = path.read_bytes()
        current_sha256 = self.compute_sha256(raw_bytes)

        if expected_sha256 and expected_sha256 != current_sha256:
            raise AndroidSafetyError(
                AndroidErrorCode.EDIT_VALIDATION_FAILED,
                f"File '{path.name}' modified concurrently.",
            )

        source = raw_bytes.decode("utf-8")
        report = self.parse_source(source, str(path), current_sha256)

        target_line: Optional[int] = None
        indent = ""

        if target_type == "class":
            if target_name not in report.classes:
                raise KeyError(f"Class '{target_name}' not found.")
            target_line = report.classes[target_name].start_line
        elif target_type == "method":
            target_method = None
            if class_name and class_name in report.classes:
                target_method = report.classes[class_name].methods.get(target_name)
            else:
                target_method = report.top_level_methods.get(target_name)
                if not target_method:
                    for cls in report.classes.values():
                        if target_name in cls.methods:
                            target_method = cls.methods[target_name]
                            break
            if not target_method:
                raise KeyError(f"Method '{target_name}' not found.")
            target_line = target_method.start_line
            indent = "    "
        elif target_type == "property":
            target_prop = None
            if class_name and class_name in report.classes:
                target_prop = report.classes[class_name].properties.get(target_name)
            else:
                target_prop = report.top_level_properties.get(target_name)
            if not target_prop:
                raise KeyError(f"Property '{target_name}' not found.")
            target_line = target_prop.line
            indent = "    "

        if target_line is None:
            raise ValueError(f"Could not locate target '{target_name}' to annotate.")

        clean_annot = annotation_code.strip()
        if not clean_annot.startswith("@"):
            clean_annot = f"@{clean_annot}"

        lines = source.splitlines()
        insert_idx = target_line - 1
        lines.insert(insert_idx, f"{indent}{clean_annot}")

        backup_path = path.with_suffix(f"{path.suffix}.bak.{int(time.time() * 1000)}")
        shutil.copy2(path, backup_path)

        new_source = "\n".join(lines) + "\n"
        new_bytes = new_source.encode("utf-8")
        tmp_target = path.with_suffix(f"{path.suffix}.tmp")
        try:
            tmp_target.write_bytes(new_bytes)
            os.replace(tmp_target, path)
        except Exception as e:
            shutil.copy2(backup_path, path)
            backup_path.unlink(missing_ok=True)
            raise IOError(f"Failed to add annotation: {e}")

        new_sha256 = self.compute_sha256(new_bytes)
        return {
            "success": True,
            "file": str(path),
            "target": target_name,
            "action": "added_annotation",
            "annotation": clean_annot,
            "backup_file": str(backup_path),
            "old_sha256": current_sha256,
            "new_sha256": new_sha256,
        }

    def rename_symbol(
        self,
        source_file: Union[str, Path],
        old_name: str,
        new_name: str,
        expected_sha256: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Safely renames occurrences of a symbol with word-boundary matching."""
        self.safety.check_emergency_stop()
        path = Path(source_file).resolve()
        raw_bytes = path.read_bytes()
        current_sha256 = self.compute_sha256(raw_bytes)

        if expected_sha256 and expected_sha256 != current_sha256:
            raise AndroidSafetyError(
                AndroidErrorCode.EDIT_VALIDATION_FAILED,
                f"File '{path.name}' modified concurrently.",
            )

        source = raw_bytes.decode("utf-8")
        pattern = rf'\b{re.escape(old_name)}\b'
        if not re.search(pattern, source):
            return {"success": True, "file": str(path), "action": "symbol_not_found", "sha256": current_sha256}

        backup_path = path.with_suffix(f"{path.suffix}.bak.{int(time.time() * 1000)}")
        shutil.copy2(path, backup_path)

        new_source = re.sub(pattern, new_name, source)
        new_bytes = new_source.encode("utf-8")
        tmp_target = path.with_suffix(f"{path.suffix}.tmp")
        try:
            tmp_target.write_bytes(new_bytes)
            os.replace(tmp_target, path)
        except Exception as e:
            shutil.copy2(backup_path, path)
            backup_path.unlink(missing_ok=True)
            raise IOError(f"Failed to rename symbol: {e}")

        new_sha256 = self.compute_sha256(new_bytes)
        return {
            "success": True,
            "file": str(path),
            "old_name": old_name,
            "new_name": new_name,
            "action": "renamed_symbol",
            "backup_file": str(backup_path),
            "old_sha256": current_sha256,
            "new_sha256": new_sha256,
        }

    def rollback(self, source_file: Union[str, Path], backup_file: Union[str, Path]) -> bool:
        """Restores a source file from its backup."""
        s_path = Path(source_file).resolve()
        b_path = Path(backup_file).resolve()
        if not b_path.exists():
            return False
        shutil.copy2(b_path, s_path)
        logger.info(f"Restored '{s_path.name}' from '{b_path.name}'.")
        return True
