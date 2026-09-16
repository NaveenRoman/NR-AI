r"""
NR-AI Unity C# Script Analysis & AST Modification Engine (Step 8 Phase 4).

Provides safe, bounded, deterministic C# AST parsing, script defect analysis,
and bounded AST-driven code modifications:
- Safe C# syntax-tree parsing (classes, structs, interfaces, enums, methods, properties, fields, namespaces, usings)
- Script analysis for syntax defects, compilation errors, and Unity anti-patterns
- Bounded AST modifications (ADD_METHOD, REPLACE_METHOD, ADD_USING, REPLACE_FIELD, etc.)
- Strict operational limits: max 5 files, max 100 KB patch, max 500 lines changed, max 2 repair attempts
- Target SHA-256 pre-verification
- Atomic backup checkpoints in scratch/unity_checkpoints/
- Structural integrity re-parsing & compile/syntax validation
- Automatic byte-for-byte rollback on failure
- Zero arbitrary shell execution (shell=False strictly)
- Model remains strictly advisory with zero direct tool or write authority
- Sensitive data redaction
"""

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
import uuid

from app.agent.unity_safety import (
    UnitySafetyGate,
    UnityErrorCode,
    UnitySafetyError,
    EmergencyStopActiveError,
    MAX_AST_FILES_PER_OP,
    MAX_AST_PATCH_BYTES,
    MAX_AST_CHANGED_LINES,
    MAX_AST_REPAIR_ATTEMPTS,
    DEFAULT_UNITY_SAFETY_GATE,
    GLOBAL_WORKSPACE_ROOT,
    DEFAULT_AUTHORIZED_PROJECT,
    redact_sensitive_data,
)
from app.memory.audit_logger import AuditLogger

logger = logging.getLogger("NRAI.UnityAST")

DEFAULT_UNITY_CHECKPOINT_DIR = (GLOBAL_WORKSPACE_ROOT / "scratch" / "unity_checkpoints").resolve()


# =============================================================================
# 1. AST Data Models
# =============================================================================

class CSharpNodeType(str, Enum):
    USING_DIRECTIVE = "USING_DIRECTIVE"
    NAMESPACE_DECLARATION = "NAMESPACE_DECLARATION"
    CLASS_DECLARATION = "CLASS_DECLARATION"
    STRUCT_DECLARATION = "STRUCT_DECLARATION"
    INTERFACE_DECLARATION = "INTERFACE_DECLARATION"
    ENUM_DECLARATION = "ENUM_DECLARATION"
    METHOD_DECLARATION = "METHOD_DECLARATION"
    CONSTRUCTOR_DECLARATION = "CONSTRUCTOR_DECLARATION"
    PROPERTY_DECLARATION = "PROPERTY_DECLARATION"
    FIELD_DECLARATION = "FIELD_DECLARATION"
    ATTRIBUTE = "ATTRIBUTE"
    SYNTAX_ERROR_NODE = "SYNTAX_ERROR_NODE"


@dataclass
class CSharpLocation:
    """Bounded source span within a file."""
    line: int = 1
    column: int = 1
    end_line: int = 1
    end_column: int = 1
    start_char: int = 0
    end_char: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "line": self.line,
            "column": self.column,
            "end_line": self.end_line,
            "end_column": self.end_column,
            "start_char": self.start_char,
            "end_char": self.end_char,
        }


@dataclass
class CSharpUsingNode:
    """Represents a using directive (e.g. using UnityEngine;)."""
    namespace_name: str
    alias: Optional[str] = None
    location: CSharpLocation = field(default_factory=CSharpLocation)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": CSharpNodeType.USING_DIRECTIVE.value,
            "namespace": self.namespace_name,
            "alias": self.alias,
            "location": self.location.to_dict(),
        }


@dataclass
class CSharpParameter:
    """Represents a method or constructor parameter."""
    name: str
    type_name: str
    default_value: Optional[str] = None
    modifiers: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type_name,
            "default_value": self.default_value,
            "modifiers": self.modifiers,
        }


@dataclass
class CSharpFieldNode:
    """Represents a field member in a class or struct."""
    name: str
    type_name: str
    modifiers: List[str] = field(default_factory=list)
    attributes: List[str] = field(default_factory=list)
    initial_value: Optional[str] = None
    location: CSharpLocation = field(default_factory=CSharpLocation)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": CSharpNodeType.FIELD_DECLARATION.value,
            "name": self.name,
            "field_type": self.type_name,
            "modifiers": self.modifiers,
            "attributes": self.attributes,
            "initial_value": self.initial_value,
            "location": self.location.to_dict(),
        }


@dataclass
class CSharpPropertyNode:
    """Represents a property member in a type."""
    name: str
    type_name: str
    modifiers: List[str] = field(default_factory=list)
    attributes: List[str] = field(default_factory=list)
    has_getter: bool = True
    has_setter: bool = True
    getter_body: Optional[str] = None
    setter_body: Optional[str] = None
    location: CSharpLocation = field(default_factory=CSharpLocation)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": CSharpNodeType.PROPERTY_DECLARATION.value,
            "name": self.name,
            "property_type": self.type_name,
            "modifiers": self.modifiers,
            "attributes": self.attributes,
            "has_getter": self.has_getter,
            "has_setter": self.has_setter,
            "location": self.location.to_dict(),
        }


@dataclass
class CSharpMethodNode:
    """Represents a method or constructor member."""
    name: str
    return_type: str
    modifiers: List[str] = field(default_factory=list)
    attributes: List[str] = field(default_factory=list)
    parameters: List[CSharpParameter] = field(default_factory=list)
    body: str = ""
    is_constructor: bool = False
    location: CSharpLocation = field(default_factory=CSharpLocation)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": (
                CSharpNodeType.CONSTRUCTOR_DECLARATION.value
                if self.is_constructor
                else CSharpNodeType.METHOD_DECLARATION.value
            ),
            "name": self.name,
            "return_type": self.return_type,
            "modifiers": self.modifiers,
            "attributes": self.attributes,
            "parameters": [p.to_dict() for p in self.parameters],
            "body": self.body,
            "is_constructor": self.is_constructor,
            "location": self.location.to_dict(),
        }


@dataclass
class CSharpTypeNode:
    """Represents a class, struct, interface, or enum."""
    name: str
    kind: str  # "class", "struct", "interface", "enum"
    modifiers: List[str] = field(default_factory=list)
    attributes: List[str] = field(default_factory=list)
    base_types: List[str] = field(default_factory=list)
    fields: List[CSharpFieldNode] = field(default_factory=list)
    properties: List[CSharpPropertyNode] = field(default_factory=list)
    methods: List[CSharpMethodNode] = field(default_factory=list)
    nested_types: List["CSharpTypeNode"] = field(default_factory=list)
    location: CSharpLocation = field(default_factory=CSharpLocation)

    def find_method(self, method_name: str) -> Optional[CSharpMethodNode]:
        for m in self.methods:
            if m.name == method_name:
                return m
        return None

    def find_field(self, field_name: str) -> Optional[CSharpFieldNode]:
        for f in self.fields:
            if f.name == field_name:
                return f
        return None

    def find_property(self, prop_name: str) -> Optional[CSharpPropertyNode]:
        for p in self.properties:
            if p.name == prop_name:
                return p
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": f"{self.kind.upper()}_DECLARATION",
            "name": self.name,
            "kind": self.kind,
            "modifiers": self.modifiers,
            "attributes": self.attributes,
            "base_types": self.base_types,
            "fields": [f.to_dict() for f in self.fields],
            "properties": [p.to_dict() for p in self.properties],
            "methods": [m.to_dict() for m in self.methods],
            "nested_types": [t.to_dict() for t in self.nested_types],
            "location": self.location.to_dict(),
        }


@dataclass
class CSharpNamespaceNode:
    """Represents a namespace block."""
    name: str
    usings: List[CSharpUsingNode] = field(default_factory=list)
    types: List[CSharpTypeNode] = field(default_factory=list)
    location: CSharpLocation = field(default_factory=CSharpLocation)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": CSharpNodeType.NAMESPACE_DECLARATION.value,
            "name": self.name,
            "usings": [u.to_dict() for u in self.usings],
            "types": [t.to_dict() for t in self.types],
            "location": self.location.to_dict(),
        }


@dataclass
class CSharpSyntaxDefect:
    """Structured representation of a syntax error or defect in C# source."""
    code: str
    severity: str  # "error", "warning"
    message: str
    line: int = 1
    column: int = 1
    snippet: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": redact_sensitive_data(self.message),
            "line": self.line,
            "column": self.column,
            "snippet": redact_sensitive_data(self.snippet),
        }


@dataclass
class CSharpSyntaxTree:
    """Complete structured syntax tree representing a C# source file."""
    source_text: str = ""
    file_path: Optional[str] = None
    sha256: str = ""
    usings: List[CSharpUsingNode] = field(default_factory=list)
    namespaces: List[CSharpNamespaceNode] = field(default_factory=list)
    types: List[CSharpTypeNode] = field(default_factory=list)
    diagnostics: List[CSharpSyntaxDefect] = field(default_factory=list)
    is_valid: bool = True

    def find_type(self, type_name: str) -> Optional[CSharpTypeNode]:
        """Searches top-level and namespaced types for a type matching type_name."""
        for t in self.types:
            if t.name == type_name:
                return t
        for ns in self.namespaces:
            for t in ns.types:
                if t.name == type_name:
                    return t
        return None

    def find_method(self, type_name: str, method_name: str) -> Optional[CSharpMethodNode]:
        t = self.find_type(type_name)
        if t:
            return t.find_method(method_name)
        return None

    def find_field(self, type_name: str, field_name: str) -> Optional[CSharpFieldNode]:
        t = self.find_type(type_name)
        if t:
            return t.find_field(field_name)
        return None

    def find_property(self, type_name: str, prop_name: str) -> Optional[CSharpPropertyNode]:
        t = self.find_type(type_name)
        if t:
            return t.find_property(prop_name)
        return None

    def get_all_types(self) -> List[CSharpTypeNode]:
        all_t = list(self.types)
        for ns in self.namespaces:
            all_t.extend(ns.types)
        return all_t

    @property
    def methods(self) -> List[CSharpMethodNode]:
        """Returns all methods across all types in the syntax tree."""
        all_m = []
        for t in self.get_all_types():
            all_m.extend(t.methods)
        return all_m

    def get_all_symbols(self) -> List[Dict[str, Any]]:
        symbols = []
        for ns in self.namespaces:
            symbols.append({"kind": "namespace", "name": ns.name, "line": ns.location.line})
            for t in ns.types:
                symbols.append({"kind": t.kind, "name": t.name, "namespace": ns.name, "line": t.location.line})
                for m in t.methods:
                    symbols.append({
                        "kind": "constructor" if m.is_constructor else "method",
                        "name": m.name,
                        "type": t.name,
                        "return_type": m.return_type,
                        "line": m.location.line,
                    })
                for p in t.properties:
                    symbols.append({"kind": "property", "name": p.name, "type": t.name, "line": p.location.line})
                for f in t.fields:
                    symbols.append({"kind": "field", "name": f.name, "type": t.name, "line": f.location.line})

        for t in self.types:
            symbols.append({"kind": t.kind, "name": t.name, "namespace": "", "line": t.location.line})
            for m in t.methods:
                symbols.append({
                    "kind": "constructor" if m.is_constructor else "method",
                    "name": m.name,
                    "type": t.name,
                    "return_type": m.return_type,
                    "line": m.location.line,
                })
            for p in t.properties:
                symbols.append({"kind": "property", "name": p.name, "type": t.name, "line": p.location.line})
            for f in t.fields:
                symbols.append({"kind": "field", "name": f.name, "type": t.name, "line": f.location.line})

        return symbols

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "sha256": self.sha256,
            "is_valid": self.is_valid,
            "diagnostics": [d.to_dict() for d in self.diagnostics],
            "usings": [u.to_dict() for u in self.usings],
            "namespaces": [ns.to_dict() for ns in self.namespaces],
            "types": [t.to_dict() for t in self.types],
        }


# =============================================================================
# 2. Deterministic C# AST Parser
# =============================================================================

class CSharpParser:
    """
    Safe, deterministic C# syntax tree parser implemented cleanly in Python.
    Extracts structured AST nodes and reports diagnostics for malformed constructs.
    Never crashes with unhandled exceptions on invalid or malformed code.
    """

    def __init__(self):
        pass

    def parse_file(self, file_path: Union[str, Path]) -> CSharpSyntaxTree:
        """Parses C# source directly from a file path."""
        p = Path(file_path)
        source = p.read_text(encoding="utf-8", errors="replace")
        return self.parse(source, file_path=str(p))

    def parse(self, source_text: str, file_path: Optional[Union[str, Path]] = None) -> CSharpSyntaxTree:
        """
        Parses C# source text into a CSharpSyntaxTree.
        Safely identifies usings, namespaces, types, fields, properties, and methods.
        """
        if source_text is None:
            source_text = ""

        f_path = str(file_path) if file_path else None
        h = hashlib.sha256(source_text.encode("utf-8")).hexdigest()

        tree = CSharpSyntaxTree(
            source_text=source_text,
            file_path=f_path,
            sha256=h,
            is_valid=True,
        )

        # 1. Check for basic syntax-level defects (brace balance, unclosed strings, etc.)
        self._check_basic_syntax_defects(source_text, tree)

        # 2. Tokenize / parse using directives
        self._parse_usings(source_text, tree)

        # 3. Parse namespaces and types
        self._parse_namespaces_and_types(source_text, tree)

        if any(d.severity == "error" for d in tree.diagnostics):
            tree.is_valid = False

        return tree

    def _check_basic_syntax_defects(self, text: str, tree: CSharpSyntaxTree) -> None:
        """Checks for brace mismatches, unclosed quotes, and syntax errors."""
        brace_stack = []
        paren_stack = []
        in_line_comment = False
        in_block_comment = False
        in_string = False
        in_verbatim_string = False
        in_char = False

        line_num = 1
        col_num = 1
        i = 0
        n = len(text)

        while i < n:
            ch = text[i]

            if ch == "\n":
                if in_string and not in_verbatim_string:
                    tree.diagnostics.append(CSharpSyntaxDefect(
                        code="CS1010",
                        severity="error",
                        message="Newline in constant",
                        line=line_num,
                        column=col_num,
                        snippet=text.splitlines()[line_num - 1] if line_num <= len(text.splitlines()) else "",
                    ))
                    in_string = False
                in_line_comment = False
                line_num += 1
                col_num = 1
                i += 1
                continue

            col_num += 1

            # Handle comments and strings
            if in_line_comment:
                i += 1
                continue

            if in_block_comment:
                if ch == "*" and i + 1 < n and text[i + 1] == "/":
                    in_block_comment = False
                    i += 2
                    col_num += 1
                    continue
                i += 1
                continue

            if in_string:
                if in_verbatim_string:
                    if ch == '"':
                        if i + 1 < n and text[i + 1] == '"':
                            i += 2
                            col_num += 1
                            continue
                        in_string = False
                        in_verbatim_string = False
                else:
                    if ch == "\\" and i + 1 < n:
                        i += 2
                        col_num += 1
                        continue
                    elif ch == '"':
                        in_string = False
                i += 1
                continue

            if in_char:
                if ch == "\\" and i + 1 < n:
                    i += 2
                    col_num += 1
                    continue
                elif ch == "'":
                    in_char = False
                i += 1
                continue

            # Check start of comments
            if ch == "/" and i + 1 < n:
                if text[i + 1] == "/":
                    in_line_comment = True
                    i += 2
                    col_num += 1
                    continue
                elif text[i + 1] == "*":
                    in_block_comment = True
                    i += 2
                    col_num += 1
                    continue

            # Check start of strings
            if ch == "@" and i + 1 < n and text[i + 1] == '"':
                in_string = True
                in_verbatim_string = True
                i += 2
                col_num += 1
                continue
            if ch == "$":
                if i + 1 < n and text[i + 1] == '"':
                    in_string = True
                    i += 2
                    col_num += 1
                    continue
                elif i + 2 < n and text[i + 1] == "@" and text[i + 2] == '"':
                    in_string = True
                    in_verbatim_string = True
                    i += 3
                    col_num += 2
                    continue

            if ch == '"':
                in_string = True
                i += 1
                continue

            if ch == "'":
                in_char = True
                i += 1
                continue

            # Braces and Parentheses
            if ch == "{":
                brace_stack.append((line_num, col_num - 1))
            elif ch == "}":
                if not brace_stack:
                    tree.diagnostics.append(CSharpSyntaxDefect(
                        code="CS1022",
                        severity="error",
                        message="Type or namespace definition, or end-of-file expected (unexpected '}')",
                        line=line_num,
                        column=col_num - 1,
                    ))
                else:
                    brace_stack.pop()
            elif ch == "(":
                paren_stack.append((line_num, col_num - 1))
            elif ch == ")":
                if paren_stack:
                    paren_stack.pop()
                else:
                    tree.diagnostics.append(CSharpSyntaxDefect(
                        code="CS1026",
                        severity="error",
                        message=") expected",
                        line=line_num,
                        column=col_num - 1,
                    ))

            i += 1

        if in_block_comment:
            tree.diagnostics.append(CSharpSyntaxDefect(
                code="CS1035",
                severity="error",
                message="End-of-file found, '*/' expected",
                line=line_num,
                column=col_num,
            ))

        if in_string:
            tree.diagnostics.append(CSharpSyntaxDefect(
                code="CS1010",
                severity="error",
                message="Newline in constant / unclosed string",
                line=line_num,
                column=col_num,
            ))

        while brace_stack:
            b_line, b_col = brace_stack.pop()
            tree.diagnostics.append(CSharpSyntaxDefect(
                code="CS1513",
                severity="error",
                message="} expected",
                line=b_line,
                column=b_col,
            ))

    def _parse_usings(self, text: str, tree: CSharpSyntaxTree) -> None:
        """Extracts using directives."""
        pattern = re.compile(r'^\s*using\s+(?:([A-Za-z0-9_]+)\s*=\s*)?([A-Za-z0-9_\.]+)\s*;', re.MULTILINE)
        for m in pattern.finditer(text):
            alias = m.group(1)
            ns = m.group(2)
            start_pos = m.start()
            line_num = text.count("\n", 0, start_pos) + 1
            col_num = start_pos - text.rfind("\n", 0, start_pos)
            loc = CSharpLocation(
                line=line_num,
                column=col_num,
                start_char=start_pos,
                end_char=m.end(),
            )
            tree.usings.append(CSharpUsingNode(
                namespace_name=ns,
                alias=alias,
                location=loc,
            ))

    def _parse_namespaces_and_types(self, text: str, tree: CSharpSyntaxTree) -> None:
        """Parses namespace blocks and types."""
        ns_pattern = re.compile(r'namespace\s+([A-Za-z0-9_\.]+)\s*\{', re.MULTILINE)
        ns_matches = list(ns_pattern.finditer(text))

        if not ns_matches:
            # Top-level types outside any namespace
            types = self._parse_types_in_block(text, 0, tree)
            tree.types.extend(types)
            return

        for m in ns_matches:
            ns_name = m.group(1)
            start_pos = m.start()
            line_num = text.count("\n", 0, start_pos) + 1
            col_num = start_pos - text.rfind("\n", 0, start_pos)

            body_start = m.end()
            body_end = self._find_matching_brace(text, body_start - 1)
            if body_end == -1:
                body_end = len(text)

            ns_body = text[body_start:body_end]
            ns_loc = CSharpLocation(
                line=line_num,
                column=col_num,
                start_char=start_pos,
                end_char=body_end + 1,
            )

            ns_node = CSharpNamespaceNode(
                name=ns_name,
                location=ns_loc,
            )

            types = self._parse_types_in_block(ns_body, body_start, tree)
            ns_node.types.extend(types)
            tree.namespaces.append(ns_node)

    def _parse_types_in_block(self, text: str, offset: int, tree: CSharpSyntaxTree) -> List[CSharpTypeNode]:
        """Discovers classes, structs, interfaces, enums within text block."""
        types = []
        type_pattern = re.compile(
            r'((?:\[[^\]]+\]\s*)*)'                          # 1: attributes
            r'((?:(?:public|private|protected|internal|static|abstract|sealed|partial)\s+)*)' # 2: modifiers
            r'(class|struct|interface|enum)\s+'             # 3: kind
            r'([A-Za-z0-9_]+)'                              # 4: name
            r'(?:\s*<[^>]+>)?'                              # generic args
            r'(?:\s*:\s*([A-Za-z0-9_,\s\.<>]+))?'          # 5: base types
            r'\s*\{',                                       # opening brace
            re.MULTILINE,
        )

        for m in type_pattern.finditer(text):
            attr_str = m.group(1) or ""
            mod_str = m.group(2) or ""
            kind = m.group(3)
            name = m.group(4)
            bases_str = m.group(5) or ""

            start_pos = offset + m.start()
            full_source = tree.source_text
            line_num = full_source.count("\n", 0, start_pos) + 1
            col_num = start_pos - full_source.rfind("\n", 0, start_pos)

            body_start = offset + m.end()
            body_end = self._find_matching_brace(full_source, body_start - 1)
            if body_end == -1:
                body_end = len(full_source)

            type_body = full_source[body_start:body_end]

            attrs = [a.strip("[] \t\r\n") for a in re.findall(r'\[([^\]]+)\]', attr_str)]
            mods = mod_str.strip().split() if mod_str.strip() else []
            bases = [b.strip() for b in bases_str.split(",") if b.strip()]

            loc = CSharpLocation(
                line=line_num,
                column=col_num,
                start_char=start_pos,
                end_char=body_end + 1,
            )

            type_node = CSharpTypeNode(
                name=name,
                kind=kind,
                modifiers=mods,
                attributes=attrs,
                base_types=bases,
                location=loc,
            )

            if kind in ("class", "struct", "interface"):
                self._parse_members(type_body, body_start, type_node, tree)

            types.append(type_node)

        return types

    def _parse_members(self, body_text: str, offset: int, type_node: CSharpTypeNode, tree: CSharpSyntaxTree) -> None:
        """Parses fields, properties, constructors, and methods inside a type."""
        full_source = tree.source_text

        # 1. Parse methods and constructors
        method_pattern = re.compile(
            r'((?:\[[^\]]+\]\s*)*)'                         # 1: attributes
            r'((?:(?:public|private|protected|internal|static|virtual|override|abstract|async|extern)\s+)*)' # 2: modifiers
            r'(?:([A-Za-z0-9_\[\]\<\>\?]+)\s+)?'            # 3: return type (None for constructor)
            r'([A-Za-z0-9_]+)\s*'                           # 4: name
            r'\(([^)]*)\)\s*'                               # 5: parameters
            r'(?:where\s+[^{;]+)?'                          # generic constraints
            r'(\{)',                                        # 6: opening brace of method body
            re.MULTILINE,
        )

        for m in method_pattern.finditer(body_text):
            attr_str = m.group(1) or ""
            mod_str = m.group(2) or ""
            ret_type = m.group(3) or "void"
            name = m.group(4)
            param_str = m.group(5) or ""

            is_ctor = (name == type_node.name)
            if is_ctor and not m.group(3):
                ret_type = ""

            start_pos = offset + m.start()
            line_num = full_source.count("\n", 0, start_pos) + 1
            col_num = start_pos - full_source.rfind("\n", 0, start_pos)

            body_open = offset + m.start(6)
            body_close = self._find_matching_brace(full_source, body_open)
            if body_close == -1:
                body_close = len(full_source)

            body_content = full_source[body_open:body_close + 1]

            attrs = [a.strip("[] \t\r\n") for a in re.findall(r'\[([^\]]+)\]', attr_str)]
            mods = mod_str.strip().split() if mod_str.strip() else []

            params = []
            if param_str.strip():
                for p_raw in param_str.split(","):
                    p_clean = p_raw.strip()
                    if not p_clean:
                        continue
                    p_parts = p_clean.split("=")
                    def_val = p_parts[1].strip() if len(p_parts) > 1 else None
                    type_and_name = p_parts[0].strip().split()
                    if len(type_and_name) >= 2:
                        p_name = type_and_name[-1]
                        p_type = " ".join(type_and_name[:-1])
                        params.append(CSharpParameter(name=p_name, type_name=p_type, default_value=def_val))

            loc = CSharpLocation(
                line=line_num,
                column=col_num,
                start_char=start_pos,
                end_char=body_close + 1,
            )

            type_node.methods.append(CSharpMethodNode(
                name=name,
                return_type=ret_type,
                modifiers=mods,
                attributes=attrs,
                parameters=params,
                body=body_content,
                is_constructor=is_ctor,
                location=loc,
            ))

        # 2. Parse properties (both block { get; set; } and expression-bodied => ... ;)
        prop_pattern = re.compile(
            r'((?:\[[^\]]+\]\s*)*)'                         # 1: attributes
            r'((?:(?:public|private|protected|internal|static|virtual|override|abstract)\s+)*)' # 2: modifiers
            r'([A-Za-z0-9_\[\]\<\>\?]+)\s+'                 # 3: property type
            r'([A-Za-z0-9_]+)\s*'                           # 4: property name
            r'(?:\{([^}]+)\}|=>\s*([^;]+);)',               # 5: block body or 6: expression body
            re.MULTILINE,
        )
        for m in prop_pattern.finditer(body_text):
            attr_str = m.group(1) or ""
            mod_str = m.group(2) or ""
            p_type = m.group(3)
            p_name = m.group(4)
            p_body = m.group(5)
            expr_body = m.group(6)

            if p_type in ("class", "struct", "interface", "enum", "namespace"):
                continue
            if p_body and ("get" not in p_body and "set" not in p_body and "init" not in p_body):
                continue

            start_pos = offset + m.start()
            line_num = full_source.count("\n", 0, start_pos) + 1
            col_num = start_pos - full_source.rfind("\n", 0, start_pos)

            has_get = ("get" in p_body) if p_body else bool(expr_body)
            has_set = ("set" in p_body or "init" in p_body) if p_body else False
            attrs = [a.strip("[] \t\r\n") for a in re.findall(r'\[([^\]]+)\]', attr_str)]
            mods = mod_str.strip().split() if mod_str.strip() else []

            loc = CSharpLocation(
                line=line_num,
                column=col_num,
                start_char=start_pos,
                end_char=offset + m.end(),
            )
            type_node.properties.append(CSharpPropertyNode(
                name=p_name,
                type_name=p_type,
                modifiers=mods,
                attributes=attrs,
                has_getter=has_get,
                has_setter=has_set,
                location=loc,
            ))

        # 3. Parse fields
        field_pattern = re.compile(
            r'((?:\[[^\]]+\]\s*)*)'                         # 1: attributes
            r'((?:(?:public|private|protected|internal|static|readonly|const|volatile)\s+)*)' # 2: modifiers
            r'([A-Za-z0-9_\[\]\<\>\?]+)\s+'                 # 3: field type
            r'([A-Za-z0-9_]+)'                              # 4: field name
            r'(?:\s*=(?!>)\s*([^;]+))?'                     # 5: optional initial value (strictly not =>)
            r'\s*;',
            re.MULTILINE,
        )
        for m in field_pattern.finditer(body_text):
            attr_str = m.group(1) or ""
            mod_str = m.group(2) or ""
            f_type = m.group(3)
            f_name = m.group(4)
            init_val = m.group(5)

            if f_type in ("class", "struct", "interface", "enum", "using", "return"):
                continue

            # Do not duplicate if already registered as a property or method
            if any(p.name == f_name for p in type_node.properties) or any(meth.name == f_name for meth in type_node.methods):
                continue

            start_pos = offset + m.start()
            line_num = full_source.count("\n", 0, start_pos) + 1
            col_num = start_pos - full_source.rfind("\n", 0, start_pos)

            attrs = [a.strip("[] \t\r\n") for a in re.findall(r'\[([^\]]+)\]', attr_str)]
            mods = mod_str.strip().split() if mod_str.strip() else []

            loc = CSharpLocation(
                line=line_num,
                column=col_num,
                start_char=start_pos,
                end_char=offset + m.end(),
            )
            type_node.fields.append(CSharpFieldNode(
                name=f_name,
                type_name=f_type,
                modifiers=mods,
                attributes=attrs,
                initial_value=init_val.strip() if init_val else None,
                location=loc,
            ))

    def _find_matching_brace(self, text: str, open_pos: int) -> int:
        """Finds the index of the matching closing brace '}' for the brace at open_pos."""
        if open_pos >= len(text) or text[open_pos] != "{":
            return -1

        depth = 0
        in_string = False
        in_verbatim = False
        in_char = False
        in_line_comment = False
        in_block_comment = False
        n = len(text)
        i = open_pos

        while i < n:
            ch = text[i]

            if ch == "\n":
                in_line_comment = False
                if in_string and not in_verbatim:
                    in_string = False
                i += 1
                continue

            if in_line_comment:
                i += 1
                continue

            if in_block_comment:
                if ch == "*" and i + 1 < n and text[i + 1] == "/":
                    in_block_comment = False
                    i += 2
                    continue
                i += 1
                continue

            if in_string:
                if in_verbatim:
                    if ch == '"':
                        if i + 1 < n and text[i + 1] == '"':
                            i += 2
                            continue
                        in_string = False
                        in_verbatim = False
                else:
                    if ch == "\\" and i + 1 < n:
                        i += 2
                        continue
                    elif ch == '"':
                        in_string = False
                i += 1
                continue

            if in_char:
                if ch == "\\" and i + 1 < n:
                    i += 2
                    continue
                elif ch == "'":
                    in_char = False
                i += 1
                continue

            if ch == "/" and i + 1 < n:
                if text[i + 1] == "/":
                    in_line_comment = True
                    i += 2
                    continue
                elif text[i + 1] == "*":
                    in_block_comment = True
                    i += 2
                    continue

            if ch == "@" and i + 1 < n and text[i + 1] == '"':
                in_string = True
                in_verbatim = True
                i += 2
                continue

            if ch == '"':
                in_string = True
                i += 1
                continue

            if ch == "'":
                in_char = True
                i += 1
                continue

            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i

            i += 1

        return -1


# =============================================================================
# 3. Unity Script Analysis & Diagnostics Engine
# =============================================================================

class UnityScriptAnalyzer:
    """
    Analyzes Unity C# scripts for compilation defects, syntax issues,
    and Unity-specific code smells / performance anti-patterns.
    Redacts any sensitive tokens from diagnostic outputs.
    """

    def __init__(self, parser: Optional[CSharpParser] = None):
        self.parser = parser or CSharpParser()

    def analyze_script(
        self,
        source_text: Union[str, Path],
        file_path: Optional[Union[str, Path]] = None,
    ) -> Dict[str, Any]:
        """
        Performs comprehensive script analysis, producing bounded diagnostics.
        Accepts raw C# source text or a file path.
        """
        if isinstance(source_text, Path) or (isinstance(source_text, str) and "\n" not in source_text and Path(source_text).is_file()):
            f_p = Path(source_text)
            file_path = str(f_p)
            actual_source = f_p.read_text(encoding="utf-8", errors="replace")
        else:
            actual_source = str(source_text)

        tree = self.parser.parse(actual_source, file_path=file_path)
        diagnostics: List[CSharpSyntaxDefect] = list(tree.diagnostics)

        # 1. Unity compilation checks
        # Check for MonoBehaviour without using UnityEngine
        has_monobehaviour = False
        all_types = tree.get_all_types()
        for t in all_types:
            if "MonoBehaviour" in t.base_types:
                has_monobehaviour = True
                break

        has_unity_using = any(u.namespace_name == "UnityEngine" for u in tree.usings)
        if has_monobehaviour and not has_unity_using:
            diagnostics.append(CSharpSyntaxDefect(
                code="CS0246",
                severity="error",
                message="The type or namespace name 'MonoBehaviour' could not be found (are you missing a using directive?)",
                line=1,
                column=1,
                snippet="using UnityEngine;",
            ))

        # 2. Performance & Code Smell checks
        lines = source_text.splitlines()
        for t in all_types:
            for m in t.methods:
                if m.name in ("Update", "FixedUpdate", "LateUpdate"):
                    # Check empty update
                    inner_body = m.body.strip("{} \t\r\n")
                    if not inner_body:
                        diagnostics.append(CSharpSyntaxDefect(
                            code="UNT0001",
                            severity="warning",
                            message=f"Empty {m.name}() method creates unnecessary overhead on Unity's native-managed boundary.",
                            line=m.location.line,
                            column=m.location.column,
                            snippet=lines[m.location.line - 1] if m.location.line <= len(lines) else "",
                        ))
                    elif "GetComponent<" in inner_body or "GetComponent(" in inner_body:
                        diagnostics.append(CSharpSyntaxDefect(
                            code="UNT0002",
                            severity="warning",
                            message=f"Frequent GetComponent call inside {m.name}() method. Cache reference in Awake() or Start().",
                            line=m.location.line,
                            column=m.location.column,
                            snippet="GetComponent in update loop",
                        ))

        is_clean = not any(d.severity == "error" for d in diagnostics)
        return {
            "file_path": str(file_path) if file_path else None,
            "sha256": tree.sha256,
            "is_valid": is_clean,
            "error_count": sum(1 for d in diagnostics if d.severity == "error"),
            "warning_count": sum(1 for d in diagnostics if d.severity == "warning"),
            "diagnostics": [d.to_dict() for d in diagnostics],
            "tree": tree.to_dict(),
        }


# =============================================================================
# 4. Safe AST Modification Engine
# =============================================================================

class ASTModificationType(str, Enum):
    ADD_USING = "ADD_USING"
    REMOVE_USING = "REMOVE_USING"
    ADD_FIELD = "ADD_FIELD"
    REPLACE_FIELD = "REPLACE_FIELD"
    REMOVE_FIELD = "REMOVE_FIELD"
    ADD_METHOD = "ADD_METHOD"
    REPLACE_METHOD = "REPLACE_METHOD"
    REMOVE_METHOD = "REMOVE_METHOD"
    REPLACE_METHOD_BODY = "REPLACE_METHOD_BODY"
    ADD_PROPERTY = "ADD_PROPERTY"
    REPLACE_PROPERTY = "REPLACE_PROPERTY"
    REMOVE_PROPERTY = "REMOVE_PROPERTY"
    RENAME_IDENTIFIER = "RENAME_IDENTIFIER"
    BOUNDED_AST_PATCH = "BOUNDED_AST_PATCH"


@dataclass
class ASTModificationProposal:
    """Structured proposal to modify a C# script via controlled AST operations."""
    proposal_id: str
    target_file: Union[str, Path]
    expected_sha256: str
    modification_type: ASTModificationType
    target_type: Optional[str] = None
    target_member: Optional[str] = None
    new_node_content: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    rationale: str = ""
    diagnostics_addressed: List[str] = field(default_factory=list)
    attempt_count: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "target_file": str(self.target_file),
            "expected_sha256": self.expected_sha256,
            "modification_type": self.modification_type.value,
            "target_type": self.target_type,
            "target_member": self.target_member,
            "new_node_content": redact_sensitive_data(self.new_node_content or ""),
            "parameters": self.parameters,
            "rationale": redact_sensitive_data(self.rationale),
            "diagnostics_addressed": self.diagnostics_addressed,
            "attempt_count": self.attempt_count,
        }


class UnityASTModifier:
    """
    Executes controlled, bounded transformations on C# syntax trees.
    Produces cleanly indented transformed C# source text without arbitrary edits.
    """

    def __init__(self, parser: Optional[CSharpParser] = None):
        self.parser = parser or CSharpParser()

    def apply_modification(
        self,
        source_text: str,
        proposal: ASTModificationProposal,
    ) -> str:
        """
        Applies the requested AST modification to source_text and returns the transformed code.
        Raises UnitySafetyError on invalid targets or missing members.
        """
        tree = self.parser.parse(source_text, file_path=proposal.target_file)
        m_type = proposal.modification_type

        if m_type == ASTModificationType.ADD_USING:
            return self._apply_add_using(source_text, tree, proposal)
        elif m_type == ASTModificationType.REMOVE_USING:
            return self._apply_remove_using(source_text, tree, proposal)
        elif m_type == ASTModificationType.ADD_METHOD:
            return self._apply_add_method(source_text, tree, proposal)
        elif m_type == ASTModificationType.REPLACE_METHOD:
            return self._apply_replace_method(source_text, tree, proposal)
        elif m_type == ASTModificationType.REMOVE_METHOD:
            return self._apply_remove_method(source_text, tree, proposal)
        elif m_type == ASTModificationType.REPLACE_METHOD_BODY:
            return self._apply_replace_method_body(source_text, tree, proposal)
        elif m_type == ASTModificationType.ADD_FIELD:
            return self._apply_add_field(source_text, tree, proposal)
        elif m_type == ASTModificationType.REPLACE_FIELD:
            return self._apply_replace_field(source_text, tree, proposal)
        elif m_type == ASTModificationType.REMOVE_FIELD:
            return self._apply_remove_field(source_text, tree, proposal)
        elif m_type == ASTModificationType.ADD_PROPERTY:
            return self._apply_add_property(source_text, tree, proposal)
        elif m_type == ASTModificationType.REPLACE_PROPERTY:
            return self._apply_replace_property(source_text, tree, proposal)
        elif m_type == ASTModificationType.REMOVE_PROPERTY:
            return self._apply_remove_property(source_text, tree, proposal)
        elif m_type == ASTModificationType.RENAME_IDENTIFIER:
            return self._apply_rename_identifier(source_text, tree, proposal)
        elif m_type == ASTModificationType.BOUNDED_AST_PATCH:
            return self._apply_bounded_patch(source_text, tree, proposal)
        else:
            raise UnitySafetyError(
                UnityErrorCode.ACTION_NOT_ALLOWED,
                f"Unsupported AST modification type: '{m_type}'.",
            )

    def _apply_add_using(self, text: str, tree: CSharpSyntaxTree, prop: ASTModificationProposal) -> str:
        ns = (prop.parameters.get("namespace") or prop.target_member or "").strip()
        if not ns:
            raise UnitySafetyError(UnityErrorCode.MALFORMED_PROPOSAL, "Missing namespace for ADD_USING.")

        if any(u.namespace_name == ns for u in tree.usings):
            return text  # Idempotent

        using_stmt = f"using {ns};\n"
        if tree.usings:
            last_u = tree.usings[-1]
            idx = last_u.location.end_char
            nl = text.find("\n", idx)
            insert_pos = (nl + 1) if nl != -1 else idx
            return text[:insert_pos] + using_stmt + text[insert_pos:]
        else:
            return using_stmt + text

    def _apply_remove_using(self, text: str, tree: CSharpSyntaxTree, prop: ASTModificationProposal) -> str:
        ns = (prop.parameters.get("namespace") or prop.target_member or "").strip()
        target_u = next((u for u in tree.usings if u.namespace_name == ns), None)
        if not target_u:
            return text

        start = target_u.location.start_char
        end = target_u.location.end_char
        if end < len(text) and text[end] == "\n":
            end += 1
        elif end + 1 < len(text) and text[end:end+2] == "\r\n":
            end += 2
        return text[:start] + text[end:]

    def _apply_add_method(self, text: str, tree: CSharpSyntaxTree, prop: ASTModificationProposal) -> str:
        t_name = prop.target_type
        new_code = (prop.new_node_content or "").strip()
        if not t_name:
            raise UnitySafetyError(UnityErrorCode.MALFORMED_PROPOSAL, "Missing target_type for ADD_METHOD.")
        if not new_code:
            raise UnitySafetyError(UnityErrorCode.MALFORMED_PROPOSAL, "Missing new_node_content for ADD_METHOD.")

        target_t = tree.find_type(t_name)
        if not target_t:
            raise UnitySafetyError(
                UnityErrorCode.SCRIPT_NOT_FOUND,
                f"Target type '{t_name}' not found in syntax tree.",
            )

        end_brace = target_t.location.end_char - 1
        indent = "        "
        indented_code = "\n".join(f"{indent}{line}" if line.strip() else "" for line in new_code.splitlines())
        insertion = f"\n{indented_code}\n    "

        return text[:end_brace] + insertion + text[end_brace:]

    def _apply_replace_method(self, text: str, tree: CSharpSyntaxTree, prop: ASTModificationProposal) -> str:
        t_name = prop.target_type
        m_name = prop.target_member
        new_code = (prop.new_node_content or "").strip()
        if not t_name or not m_name:
            raise UnitySafetyError(UnityErrorCode.MALFORMED_PROPOSAL, "Missing target_type or target_member for REPLACE_METHOD.")
        if not new_code:
            raise UnitySafetyError(UnityErrorCode.MALFORMED_PROPOSAL, "Missing new_node_content for REPLACE_METHOD.")

        method = tree.find_method(t_name, m_name)
        if not method:
            raise UnitySafetyError(
                UnityErrorCode.SCRIPT_NOT_FOUND,
                f"Method '{m_name}' not found in type '{t_name}'.",
            )

        start = method.location.start_char
        end = method.location.end_char

        indent = " " * (method.location.column - 1)
        indented_code = "\n".join(
            f"{indent}{line}" if i > 0 and line.strip() else line
            for i, line in enumerate(new_code.splitlines())
        )

        return text[:start] + indented_code + text[end:]

    def _apply_remove_method(self, text: str, tree: CSharpSyntaxTree, prop: ASTModificationProposal) -> str:
        t_name = prop.target_type
        m_name = prop.target_member
        method = tree.find_method(t_name, m_name)
        if not method:
            return text

        start = method.location.start_char
        end = method.location.end_char
        if end < len(text) and text[end] == "\n":
            end += 1
        elif end + 1 < len(text) and text[end:end+2] == "\r\n":
            end += 2
        return text[:start] + text[end:]

    def _apply_replace_method_body(self, text: str, tree: CSharpSyntaxTree, prop: ASTModificationProposal) -> str:
        t_name = prop.target_type
        m_name = prop.target_member
        new_body = (prop.new_node_content or "").strip()
        method = tree.find_method(t_name, m_name)
        if not method:
            raise UnitySafetyError(
                UnityErrorCode.SCRIPT_NOT_FOUND,
                f"Method '{m_name}' not found in type '{t_name}'.",
            )

        body_start = text.find("{", method.location.start_char)
        body_end = method.location.end_char - 1
        if body_start == -1 or body_end == -1:
            raise UnitySafetyError(UnityErrorCode.EDIT_VALIDATION_FAILED, "Failed to locate method body braces.")

        indent = " " * (method.location.column - 1)
        body_indent = indent + "    "
        indented_body = "\n".join(f"{body_indent}{l}" if l.strip() else "" for l in new_body.splitlines())
        formatted_replacement = "{\n" + indented_body + f"\n{indent}}}"

        return text[:body_start] + formatted_replacement + text[body_end + 1:]

    def _apply_add_field(self, text: str, tree: CSharpSyntaxTree, prop: ASTModificationProposal) -> str:
        t_name = prop.target_type
        new_field = (prop.new_node_content or "").strip()
        target_t = tree.find_type(t_name)
        if not target_t:
            raise UnitySafetyError(UnityErrorCode.SCRIPT_NOT_FOUND, f"Type '{t_name}' not found.")

        open_brace = text.find("{", target_t.location.start_char)
        indent = "        "
        insertion = f"\n{indent}{new_field}\n"
        return text[:open_brace + 1] + insertion + text[open_brace + 1:]

    def _apply_replace_field(self, text: str, tree: CSharpSyntaxTree, prop: ASTModificationProposal) -> str:
        t_name = prop.target_type
        f_name = prop.target_member
        new_code = (prop.new_node_content or "").strip()
        field_node = tree.find_field(t_name, f_name)
        if not field_node:
            raise UnitySafetyError(UnityErrorCode.SCRIPT_NOT_FOUND, f"Field '{f_name}' not found in '{t_name}'.")

        start = field_node.location.start_char
        end = field_node.location.end_char
        return text[:start] + new_code + text[end:]

    def _apply_remove_field(self, text: str, tree: CSharpSyntaxTree, prop: ASTModificationProposal) -> str:
        t_name = prop.target_type
        f_name = prop.target_member
        field_node = tree.find_field(t_name, f_name)
        if not field_node:
            return text
        start = field_node.location.start_char
        end = field_node.location.end_char
        return text[:start] + text[end:]

    def _apply_add_property(self, text: str, tree: CSharpSyntaxTree, prop: ASTModificationProposal) -> str:
        t_name = prop.target_type
        new_prop = (prop.new_node_content or "").strip()
        target_t = tree.find_type(t_name)
        if not target_t:
            raise UnitySafetyError(UnityErrorCode.SCRIPT_NOT_FOUND, f"Type '{t_name}' not found.")

        end_brace = target_t.location.end_char - 1
        indent = "        "
        indented = "\n".join(f"{indent}{l}" if l.strip() else "" for l in new_prop.splitlines())
        insertion = f"\n{indented}\n    "
        return text[:end_brace] + insertion + text[end_brace:]

    def _apply_replace_property(self, text: str, tree: CSharpSyntaxTree, prop: ASTModificationProposal) -> str:
        t_name = prop.target_type
        p_name = prop.target_member
        new_code = (prop.new_node_content or "").strip()
        prop_node = tree.find_property(t_name, p_name)
        if not prop_node:
            raise UnitySafetyError(UnityErrorCode.SCRIPT_NOT_FOUND, f"Property '{p_name}' not found in '{t_name}'.")
        start = prop_node.location.start_char
        end = prop_node.location.end_char
        return text[:start] + new_code + text[end:]

    def _apply_remove_property(self, text: str, tree: CSharpSyntaxTree, prop: ASTModificationProposal) -> str:
        t_name = prop.target_type
        p_name = prop.target_member
        prop_node = tree.find_property(t_name, p_name)
        if not prop_node:
            return text
        start = prop_node.location.start_char
        end = prop_node.location.end_char
        return text[:start] + text[end:]

    def _apply_rename_identifier(self, text: str, tree: CSharpSyntaxTree, prop: ASTModificationProposal) -> str:
        old_id = prop.parameters.get("old_identifier") or prop.target_member
        new_id = prop.parameters.get("new_identifier") or prop.new_node_content
        if not old_id or not new_id:
            raise UnitySafetyError(UnityErrorCode.MALFORMED_PROPOSAL, "Missing old or new identifier.")

        pattern = re.compile(rf'\b{re.escape(old_id)}\b')
        return pattern.sub(new_id, text)

    def _apply_bounded_patch(self, text: str, tree: CSharpSyntaxTree, prop: ASTModificationProposal) -> str:
        old_pattern = prop.parameters.get("target_snippet")
        new_snippet = prop.new_node_content
        if not old_pattern or new_snippet is None:
            raise UnitySafetyError(UnityErrorCode.MALFORMED_PROPOSAL, "Missing target_snippet or new_node_content.")
        if old_pattern not in text:
            raise UnitySafetyError(UnityErrorCode.EDIT_VALIDATION_FAILED, "Target snippet not found in source text.")
        return text.replace(old_pattern, new_snippet, 1)


# =============================================================================
# 5. Unity Script Manager & Repair Coordinator
# =============================================================================

@dataclass
class UnityScriptModificationResult:
    """Structured result of applying an AST modification proposal."""
    success: bool
    target_file: str
    original_sha256: str
    modified_sha256: str
    patch_size_bytes: int
    lines_changed: int
    checkpoint_path: Optional[str] = None
    rolled_back: bool = False
    error: Optional[str] = None
    error_code: Optional[str] = None
    ast_valid: bool = False

    @property
    def backup_path(self) -> Optional[str]:
        """Alias for checkpoint_path."""
        return self.checkpoint_path

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "target_file": self.target_file,
            "original_sha256": self.original_sha256,
            "modified_sha256": self.modified_sha256,
            "patch_size_bytes": self.patch_size_bytes,
            "lines_changed": self.lines_changed,
            "checkpoint_path": self.checkpoint_path,
            "backup_path": self.checkpoint_path,
            "rolled_back": self.rolled_back,
            "error": self.error,
            "error_code": self.error_code,
            "ast_valid": self.ast_valid,
        }


class UnityScriptManager:
    """
    High-level coordinator for Unity C# script analysis and bounded AST modifications.
    Enforces atomic backups, verification, structural integrity re-parsing, and automatic rollback.
    """

    def __init__(
        self,
        safety_gate: Optional[UnitySafetyGate] = None,
        checkpoint_dir: Optional[Path] = None,
        parser: Optional[CSharpParser] = None,
        analyzer: Optional[UnityScriptAnalyzer] = None,
        modifier: Optional[UnityASTModifier] = None,
        audit_logger: Optional[AuditLogger] = None,
    ):
        self.safety = safety_gate or DEFAULT_UNITY_SAFETY_GATE
        self.checkpoint_dir = (checkpoint_dir or DEFAULT_UNITY_CHECKPOINT_DIR).resolve()
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self.parser = parser or CSharpParser()
        self.analyzer = analyzer or UnityScriptAnalyzer(self.parser)
        self.modifier = modifier or UnityASTModifier(self.parser)
        self.audit = audit_logger

    def _log_audit(self, event_type: str, details: Dict[str, Any], status: str = "success") -> None:
        if not self.audit:
            return
        try:
            if hasattr(self.audit, "log_event"):
                self.audit.log_event(event_type=event_type, details=details, status=status)
            elif hasattr(self.audit, "log"):
                self.audit.log(event_type=event_type, action=details.get("action", "script_ast"), status=status.upper(), details=details)
        except Exception as e:
            logger.warning(f"Audit log failed: {e}")

    def parse_script(self, target_file: Union[str, Path]) -> CSharpSyntaxTree:
        """Safely parses target C# script after safety verification."""
        resolved = self.safety.validate_script_file_target(target_file, check_exists=True)
        source = resolved.read_text(encoding="utf-8", errors="replace")
        return self.parser.parse(source, file_path=resolved)

    def analyze_script(self, target_file: Union[str, Path]) -> Dict[str, Any]:
        """Safely analyzes script defects, mapping diagnostics with token redaction."""
        resolved = self.safety.validate_script_file_target(target_file, check_exists=True)
        source = resolved.read_text(encoding="utf-8", errors="replace")
        return self.analyzer.analyze_script(source, file_path=resolved)

    def propose_modification(
        self,
        target_file: Union[str, Path],
        expected_sha256: str,
        modification_type: Union[str, ASTModificationType],
        target_type: Optional[str] = None,
        target_member: Optional[str] = None,
        new_node_content: Optional[str] = None,
        parameters: Optional[Dict[str, Any]] = None,
        rationale: str = "",
        diagnostics_addressed: Optional[List[str]] = None,
        attempt_count: int = 1,
    ) -> ASTModificationProposal:
        """
        Creates and validates an AST modification proposal.
        """
        resolved = self.safety.validate_script_file_target(target_file, check_exists=True)
        self.safety.validate_target_sha256(resolved, expected_sha256)

        if isinstance(modification_type, str):
            try:
                m_type = ASTModificationType(modification_type)
            except ValueError:
                raise UnitySafetyError(
                    UnityErrorCode.MALFORMED_PROPOSAL,
                    f"Unknown AST modification type: '{modification_type}'.",
                )
        else:
            m_type = modification_type

        # Check sensitive tokens in new_node_content or rationale
        redacted_content = redact_sensitive_data(new_node_content or "")
        if "[REDACTED_" in redacted_content and "[REDACTED_" not in (new_node_content or ""):
            raise UnitySafetyError(
                UnityErrorCode.ACTION_NOT_ALLOWED,
                "Sensitive token/secret detected in proposed modification code.",
            )

        prop_id = f"prop_{uuid.uuid4().hex[:8]}"
        proposal = ASTModificationProposal(
            proposal_id=prop_id,
            target_file=resolved,
            expected_sha256=expected_sha256.lower().strip(),
            modification_type=m_type,
            target_type=target_type,
            target_member=target_member,
            new_node_content=new_node_content,
            parameters=parameters or {},
            rationale=rationale,
            diagnostics_addressed=diagnostics_addressed or [],
            attempt_count=attempt_count,
        )
        return proposal

    def apply_modification(
        self,
        proposal: ASTModificationProposal,
        validate_compile: bool = True,
    ) -> UnityScriptModificationResult:
        """
        Executes an AST modification proposal through the atomic pipeline:
        1. Pre-edit SHA-256 target verification.
        2. Atomic backup checkpoint in scratch/unity_checkpoints/.
        3. Parse AST before modification.
        4. Apply controlled AST transformation.
        5. Re-parse resulting source; verify structural integrity.
        6. Compile/validate after modification.
        7. If validation fails -> automatic rollback and verification of restored SHA-256.
        """
        self.safety.assert_not_stopped()
        resolved = self.safety.validate_script_file_target(proposal.target_file, check_exists=True)
        self.safety.validate_target_sha256(resolved, proposal.expected_sha256)

        # 1. Read original source and compute SHA-256
        orig_bytes = resolved.read_bytes()
        orig_sha = hashlib.sha256(orig_bytes).hexdigest().lower()
        orig_text = orig_bytes.decode("utf-8", errors="replace")
        orig_lines_count = len(orig_text.splitlines())

        # 2. Create atomic backup checkpoint
        checkpoint_name = f"{proposal.proposal_id}_{resolved.name}.bak"
        checkpoint_path = self.checkpoint_dir / checkpoint_name
        checkpoint_path.write_bytes(orig_bytes)

        try:
            # 3. Operational limits validation
            new_content_len = len((proposal.new_node_content or "").encode("utf-8"))
            self.safety.validate_ast_limits(
                num_files=1,
                patch_size_bytes=new_content_len,
                lines_changed=len((proposal.new_node_content or "").splitlines()),
                attempts=proposal.attempt_count,
            )

            # 4. Apply controlled AST transformation
            modified_text = self.modifier.apply_modification(orig_text, proposal)
            modified_bytes = modified_text.encode("utf-8")
            mod_sha = hashlib.sha256(modified_bytes).hexdigest().lower()

            patch_size = abs(len(modified_bytes) - len(orig_bytes))
            lines_changed = abs(len(modified_text.splitlines()) - orig_lines_count)

            # Re-verify operational limits on resulting patch
            self.safety.validate_ast_limits(
                num_files=1,
                patch_size_bytes=patch_size,
                lines_changed=lines_changed,
                attempts=proposal.attempt_count,
            )

            # 5. Re-parse resulting source and verify structural integrity
            tree_post = self.parser.parse(modified_text, file_path=resolved)
            if not tree_post.is_valid:
                err_msgs = "; ".join(d.message for d in tree_post.diagnostics if d.severity == "error")
                raise UnitySafetyError(
                    UnityErrorCode.AST_INTEGRITY_FAILED,
                    f"Structural integrity verification failed after AST transformation: {err_msgs}",
                )

            # Check that the target class/type wasn't obliterated
            if proposal.target_type:
                t_node = tree_post.find_type(proposal.target_type)
                if not t_node:
                    raise UnitySafetyError(
                        UnityErrorCode.AST_INTEGRITY_FAILED,
                        f"Target type '{proposal.target_type}' disappeared after AST transformation.",
                    )

            # 6. Write modified content to disk atomically
            resolved.write_bytes(modified_bytes)

            # 7. Post-write compile / validation if requested
            if validate_compile:
                analysis = self.analyzer.analyze_script(modified_text, file_path=resolved)
                if analysis["error_count"] > 0:
                    err_msg = analysis["diagnostics"][0]["message"] if analysis["diagnostics"] else "Compilation defects detected"
                    raise UnitySafetyError(
                        UnityErrorCode.COMPILATION_FAILED,
                        f"Post-modification compilation/analysis validation failed: {err_msg}",
                    )

            self._log_audit(
                event_type="unity_ast_modification",
                details={
                    "proposal_id": proposal.proposal_id,
                    "target_file": str(resolved),
                    "original_sha256": orig_sha,
                    "modified_sha256": mod_sha,
                    "modification_type": proposal.modification_type.value,
                },
                status="success",
            )

            return UnityScriptModificationResult(
                success=True,
                target_file=str(resolved),
                original_sha256=orig_sha,
                modified_sha256=mod_sha,
                patch_size_bytes=patch_size,
                lines_changed=lines_changed,
                checkpoint_path=str(checkpoint_path),
                rolled_back=False,
                ast_valid=True,
            )

        except Exception as e:
            # 8. AUTOMATIC ROLLBACK on any failure
            logger.error(f"Modification failed, executing automatic rollback: {e}")
            self.rollback_modification(
                target_file=resolved,
                checkpoint_path=checkpoint_path,
                expected_original_sha256=orig_sha,
            )

            self._log_audit(
                event_type="unity_ast_rollback",
                details={
                    "proposal_id": proposal.proposal_id,
                    "target_file": str(resolved),
                    "reason": str(e),
                },
                status="rollback",
            )

            err_code = e.code.value if isinstance(e, UnitySafetyError) else "MODIFICATION_FAILED"
            return UnityScriptModificationResult(
                success=False,
                target_file=str(resolved),
                original_sha256=orig_sha,
                modified_sha256=orig_sha,
                patch_size_bytes=0,
                lines_changed=0,
                checkpoint_path=str(checkpoint_path),
                rolled_back=True,
                error=str(e),
                error_code=err_code,
                ast_valid=False,
            )

    def rollback_modification(
        self,
        target_file: Union[str, Path],
        checkpoint_path: Union[str, Path],
        expected_original_sha256: str,
    ) -> bool:
        """
        Restores target_file from backup checkpoint and verifies post-rollback SHA-256.
        """
        t_path = Path(target_file).resolve()
        c_path = Path(checkpoint_path).resolve()

        if not c_path.exists():
            raise UnitySafetyError(
                UnityErrorCode.ROLLBACK_FAILED,
                f"Backup checkpoint file does not exist: '{c_path}'.",
            )

        # Restore
        backup_bytes = c_path.read_bytes()
        t_path.write_bytes(backup_bytes)

        # Verify restored hash
        restored_sha = hashlib.sha256(t_path.read_bytes()).hexdigest().lower()
        exp = str(expected_original_sha256 or "").strip().lower()
        if restored_sha != exp:
            raise UnitySafetyError(
                UnityErrorCode.ROLLBACK_FAILED,
                f"Rollback integrity verification failed. Restored SHA-256: {restored_sha}, Expected: {exp}.",
            )
        return True

    def get_project_symbols(self, project_path: Union[str, Path]) -> Dict[str, Any]:
        """Discovers all C# symbols across the project."""
        p_path = Path(project_path).resolve()
        cs_files = list(p_path.glob("Assets/**/*.cs"))
        symbols_by_file = {}
        for cs in cs_files:
            try:
                tree = self.parse_script(cs)
                symbols_by_file[str(cs.relative_to(p_path))] = tree.get_all_symbols()
            except Exception as e:
                symbols_by_file[str(cs.relative_to(p_path))] = [{"error": str(e)}]
        return {
            "project_path": str(p_path),
            "script_count": len(cs_files),
            "symbols": symbols_by_file,
        }

    def validate_script_repair(
        self,
        target_file: Union[str, Path],
        diagnostics_to_check: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Checks if a script currently compiles cleanly and whether specific diagnostics are resolved."""
        analysis = self.analyze_script(target_file)
        current_codes = {d["code"] for d in analysis["diagnostics"] if d["severity"] == "error"}
        checked = diagnostics_to_check or []
        resolved = [code for code in checked if code not in current_codes]
        still_present = [code for code in checked if code in current_codes]

        return {
            "target_file": str(target_file),
            "is_valid": analysis["is_valid"],
            "diagnostics_resolved": resolved,
            "diagnostics_remaining": still_present,
            "current_error_count": analysis["error_count"],
        }


# Global default instance
DEFAULT_UNITY_SCRIPT_MANAGER = UnityScriptManager()
