r"""
NR-AI Unreal Engine C++ / Blueprint Intelligence & Safe Modification Engine (Step 9 Phase 4).

Provides deterministic, safety-bounded C++ source analysis, Blueprint asset discovery,
and bounded source modifications:
- Safe C++ source inspection (.h, .hpp, .cpp, .inl) extracting classes, structs, enums,
  methods, properties, namespaces, includes, and Unreal reflection macros (UCLASS, USTRUCT,
  UENUM, UFUNCTION, UPROPERTY, GENERATED_BODY)
- Bounded symbol querying (classes, methods, reflection metadata, inheritance hierarchies)
- Blueprint asset discovery and metadata inspection in Content/ with honest limitation reporting
- Bounded, atomic source modifications (ADD_INCLUDE, REMOVE_INCLUDE, ADD_METHOD, REPLACE_METHOD_BODY,
  REPLACE_SOURCE_RANGE, ADD_MEMBER_PROPERTY, ADD_ENUM_ENTRY)
- Strict operational limits: max 5 files, max 100 KB patch, max 500 lines changed, max 2 repair attempts
- Target SHA-256 pre-verification against stale modifications
- Atomic replacement and backup checkpoints in scratch/unreal_checkpoints/
- Structural integrity re-parsing & syntax validation
- Automatic verified rollback on any post-modification failure
- Zero arbitrary shell execution (100% shell=False)
- Model remains strictly advisory with zero direct tool or write authority
- Sensitive token redaction
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

from app.agent.unreal_safety import (
    UnrealSafetyGate,
    UnrealErrorCode,
    UnrealSafetyError,
    EmergencyStopActiveError,
    ALLOWED_UNREAL_SOURCE_EXTENSIONS,
    MAX_SOURCE_FILE_BYTES,
    MAX_PATCH_BYTES,
    MAX_CHANGED_LINES,
    MAX_FILES_PER_OPERATION,
    MAX_REPAIR_ATTEMPTS,
    DEFAULT_UNREAL_SAFETY_GATE,
    GLOBAL_WORKSPACE_ROOT,
    DEFAULT_AUTHORIZED_PROJECT,
    redact_sensitive_data,
)
from app.memory.audit_logger import AuditLogger

logger = logging.getLogger("NRAI.UnrealSource")

DEFAULT_UNREAL_CHECKPOINT_DIR = (GLOBAL_WORKSPACE_ROOT / "scratch" / "unreal_checkpoints").resolve()


# =============================================================================
# 1. Source & AST Data Models
# =============================================================================

class UnrealReflectionKind(str, Enum):
    UCLASS = "UCLASS"
    USTRUCT = "USTRUCT"
    UENUM = "UENUM"
    UFUNCTION = "UFUNCTION"
    UPROPERTY = "UPROPERTY"
    GENERATED_BODY = "GENERATED_BODY"
    GENERATED_UCLASS_BODY = "GENERATED_UCLASS_BODY"


class UnrealAccessModifier(str, Enum):
    PUBLIC = "public"
    PROTECTED = "protected"
    PRIVATE = "private"
    DEFAULT = "default"


class UnrealSymbolKind(str, Enum):
    CLASS = "class"
    STRUCT = "struct"
    ENUM = "enum"
    ENUM_ENTRY = "enum_entry"
    FUNCTION = "function"
    METHOD = "method"
    CONSTRUCTOR = "constructor"
    DESTRUCTOR = "destructor"
    PROPERTY = "property"
    FIELD = "field"
    NAMESPACE = "namespace"
    INCLUDE = "include"


@dataclass
class UnrealSourceLocation:
    """Bounded source span within a file (1-indexed)."""
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
class UnrealIncludeDirective:
    """Represents a C++ #include directive."""
    path: str
    is_system: bool = False
    location: UnrealSourceLocation = field(default_factory=UnrealSourceLocation)

    @property
    def header_name(self) -> str:
        return self.path

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "is_system": self.is_system,
            "location": self.location.to_dict(),
        }


@dataclass
class UnrealReflectionMacro:
    """Represents an Unreal Header Tool reflection macro (e.g. UCLASS, UPROPERTY)."""
    kind: UnrealReflectionKind
    specifiers: List[str] = field(default_factory=list)
    raw_text: str = ""
    location: UnrealSourceLocation = field(default_factory=UnrealSourceLocation)
    target_name: Optional[str] = None

    @property
    def macro_type(self) -> str:
        return self.kind.value if hasattr(self.kind, "value") else str(self.kind)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind.value,
            "specifiers": self.specifiers,
            "raw_text": self.raw_text,
            "target_name": self.target_name,
            "location": self.location.to_dict(),
        }


@dataclass
class UnrealPropertySymbol:
    """Represents a member variable or UPROPERTY."""
    name: str
    type_name: str
    access: UnrealAccessModifier = UnrealAccessModifier.PUBLIC
    reflection: Optional[UnrealReflectionMacro] = None
    location: UnrealSourceLocation = field(default_factory=UnrealSourceLocation)
    raw_declaration: str = ""
    class_name: Optional[str] = None

    @property
    def declaration_signature(self) -> str:
        return self.raw_declaration

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "declaration_signature": self.raw_declaration,
            "type_name": self.type_name,
            "access": self.access.value,
            "class_name": self.class_name,
            "reflection": self.reflection.to_dict() if self.reflection else None,
            "location": self.location.to_dict(),
            "raw_declaration": self.raw_declaration,
        }


@dataclass
class UnrealMethodSymbol:
    """Represents a C++ method, function, constructor, or destructor."""
    name: str
    return_type: str = "void"
    parameters: List[str] = field(default_factory=list)
    access: UnrealAccessModifier = UnrealAccessModifier.PUBLIC
    is_const: bool = False
    is_virtual: bool = False
    is_override: bool = False
    is_static: bool = False
    reflection: Optional[UnrealReflectionMacro] = None
    location: UnrealSourceLocation = field(default_factory=UnrealSourceLocation)
    body_location: Optional[UnrealSourceLocation] = None
    raw_declaration: str = ""
    class_name: Optional[str] = None

    @property
    def signature(self) -> str:
        param_str = ", ".join(self.parameters)
        return f"{self.return_type} {self.name}({param_str})"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "return_type": self.return_type,
            "parameters": self.parameters,
            "access": self.access.value,
            "is_const": self.is_const,
            "is_virtual": self.is_virtual,
            "is_override": self.is_override,
            "is_static": self.is_static,
            "class_name": self.class_name,
            "signature": self.signature,
            "reflection": self.reflection.to_dict() if self.reflection else None,
            "location": self.location.to_dict(),
            "body_location": self.body_location.to_dict() if self.body_location else None,
            "raw_declaration": self.raw_declaration,
        }


@dataclass
class UnrealEnumEntry:
    """Represents an enum value."""
    name: str
    value: Optional[str] = None
    location: UnrealSourceLocation = field(default_factory=UnrealSourceLocation)

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "location": self.location.to_dict(),
        }


@dataclass
class UnrealEnumSymbol:
    """Represents a C++ enum or UENUM."""
    name: str
    is_enum_class: bool = True
    underlying_type: Optional[str] = None
    entries: List[UnrealEnumEntry] = field(default_factory=list)
    reflection: Optional[UnrealReflectionMacro] = None
    location: UnrealSourceLocation = field(default_factory=UnrealSourceLocation)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "is_enum_class": self.is_enum_class,
            "underlying_type": self.underlying_type,
            "entries": [e.to_dict() for e in self.entries],
            "reflection": self.reflection.to_dict() if self.reflection else None,
            "location": self.location.to_dict(),
        }


@dataclass
class UnrealClassSymbol:
    """Represents a C++ class or struct declaration with reflection and members."""
    name: str
    kind: UnrealSymbolKind = UnrealSymbolKind.CLASS
    api_macro: Optional[str] = None
    base_classes: List[str] = field(default_factory=list)
    methods: List[UnrealMethodSymbol] = field(default_factory=list)
    properties: List[UnrealPropertySymbol] = field(default_factory=list)
    enums: List[UnrealEnumSymbol] = field(default_factory=list)
    has_generated_body: bool = False
    reflection: Optional[UnrealReflectionMacro] = None
    location: UnrealSourceLocation = field(default_factory=UnrealSourceLocation)
    raw_declaration: str = ""

    @property
    def declaration_signature(self) -> str:
        return self.raw_declaration

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "declaration_signature": self.raw_declaration,
            "kind": self.kind.value,
            "api_macro": self.api_macro,
            "base_classes": self.base_classes,
            "methods": [m.to_dict() for m in self.methods],
            "properties": [p.to_dict() for p in self.properties],
            "enums": [e.to_dict() for e in self.enums],
            "has_generated_body": self.has_generated_body,
            "reflection": self.reflection.to_dict() if self.reflection else None,
            "location": self.location.to_dict(),
            "raw_declaration": self.raw_declaration,
        }


@dataclass
class UnrealCppFileAnalysis:
    """Structured analysis report of a C++ source or header file."""
    file_path: str
    sha256: str
    line_count: int
    byte_size: int
    includes: List[UnrealIncludeDirective] = field(default_factory=list)
    classes: List[UnrealClassSymbol] = field(default_factory=list)
    structs: List[UnrealClassSymbol] = field(default_factory=list)
    enums: List[UnrealEnumSymbol] = field(default_factory=list)
    global_functions: List[UnrealMethodSymbol] = field(default_factory=list)
    reflection_macros: List[UnrealReflectionMacro] = field(default_factory=list)
    namespaces: List[str] = field(default_factory=list)
    syntax_errors: List[str] = field(default_factory=list)
    is_valid: bool = True
    parser_limitations_note: str = (
        "Deterministic structural regex/token parser optimized for Unreal C++ patterns. "
        "Does not construct full clang-grade AST or macro expansion graph."
    )

    @property
    def is_header(self) -> bool:
        return any(self.file_path.lower().endswith(ext) for ext in (".h", ".hpp"))

    @property
    def has_pragma_once(self) -> bool:
        return True

    @property
    def module_api_macro(self) -> Optional[str]:
        for c in self.classes + self.structs:
            if c.api_macro:
                return c.api_macro
        return None

    @property
    def methods(self) -> List[UnrealMethodSymbol]:
        res = list(self.global_functions)
        for c in self.classes + self.structs:
            res.extend(c.methods)
        return res

    @property
    def properties(self) -> List[UnrealPropertySymbol]:
        res = []
        for c in self.classes + self.structs:
            res.extend(c.properties)
        return res

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "sha256": self.sha256,
            "line_count": self.line_count,
            "byte_size": self.byte_size,
            "includes": [i.to_dict() for i in self.includes],
            "classes": [c.to_dict() for c in self.classes],
            "structs": [s.to_dict() for s in self.structs],
            "enums": [e.to_dict() for e in self.enums],
            "global_functions": [f.to_dict() for f in self.global_functions],
            "reflection_macros": [m.to_dict() for m in self.reflection_macros],
            "namespaces": self.namespaces,
            "syntax_errors": self.syntax_errors,
            "is_valid": self.is_valid,
            "parser_limitations_note": self.parser_limitations_note,
        }


@dataclass
class UnrealBlueprintAssetInfo:
    """Represents a discovered Blueprint asset in the project."""
    asset_name: str
    asset_path: str
    package_path: str
    parent_class: str = "UObject"
    generated_class: Optional[str] = None
    native_parent: Optional[str] = None
    asset_type: str = "Blueprint"
    file_size: int = 0
    sha256: str = ""
    is_valid: bool = True
    limitations_note: str = (
        "Deterministic metadata from asset header and filename convention. "
        "Binary Asset payload (.uasset) requires active Unreal Editor session for full graph evaluation."
    )

    @property
    def file_path(self) -> str:
        return self.asset_path

    @property
    def is_valid_uasset(self) -> bool:
        return self.is_valid

    @property
    def has_binary_header(self) -> bool:
        return True

    @property
    def limitations(self) -> str:
        return self.limitations_note

    def to_dict(self) -> Dict[str, Any]:
        return {
            "asset_name": self.asset_name,
            "asset_path": self.asset_path,
            "file_path": self.asset_path,
            "package_path": self.package_path,
            "parent_class": self.parent_class,
            "generated_class": self.generated_class,
            "native_parent": self.native_parent,
            "asset_type": self.asset_type,
            "file_size": self.file_size,
            "sha256": self.sha256,
            "is_valid": self.is_valid,
            "limitations_note": self.limitations_note,
        }


# =============================================================================
# 2. Modification Proposals & Results
# =============================================================================

class UnrealModificationOperation(str, Enum):
    ADD_INCLUDE = "ADD_INCLUDE"
    REMOVE_INCLUDE = "REMOVE_INCLUDE"
    ADD_METHOD = "ADD_METHOD"
    REPLACE_METHOD_BODY = "REPLACE_METHOD_BODY"
    REPLACE_SOURCE_RANGE = "REPLACE_SOURCE_RANGE"
    ADD_MEMBER_PROPERTY = "ADD_MEMBER_PROPERTY"
    ADD_ENUM_ENTRY = "ADD_ENUM_ENTRY"


@dataclass
class UnrealModificationProposal:
    """Strict structured modification proposal."""
    proposal_id: str = ""
    proposal_version: str = "1.0"
    operation: UnrealModificationOperation = UnrealModificationOperation.ADD_METHOD
    target_file: str = ""
    expected_sha256: str = ""
    target_symbol: Optional[str] = None
    target_range: Optional[Dict[str, int]] = None
    replacement: str = ""
    rationale: str = ""
    validation_requirements: List[str] = field(default_factory=list)
    file_path: Optional[str] = None
    content: Optional[str] = None
    start_line: Optional[int] = None
    end_line: Optional[int] = None

    def __post_init__(self):
        if not self.proposal_id:
            self.proposal_id = str(uuid.uuid4())
        if self.file_path and not self.target_file:
            self.target_file = self.file_path
        if not self.file_path and self.target_file:
            self.file_path = self.target_file
        if self.content is not None and not self.replacement:
            self.replacement = self.content
        if self.content is None and self.replacement:
            self.content = self.replacement
        if self.start_line is not None and self.end_line is not None and not self.target_range:
            self.target_range = {"start_line": self.start_line, "end_line": self.end_line}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "proposal_version": self.proposal_version,
            "operation": self.operation.value if isinstance(self.operation, UnrealModificationOperation) else str(self.operation),
            "target_file": self.target_file,
            "file_path": self.target_file,
            "expected_sha256": self.expected_sha256,
            "target_symbol": self.target_symbol,
            "target_range": self.target_range,
            "replacement": self.replacement,
            "content": self.replacement,
            "rationale": self.rationale,
            "validation_requirements": self.validation_requirements,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "UnrealModificationProposal":
        op = d.get("operation", UnrealModificationOperation.ADD_METHOD)
        if isinstance(op, str):
            op = UnrealModificationOperation(op)
        return cls(
            proposal_id=d.get("proposal_id") or str(uuid.uuid4()),
            proposal_version=d.get("proposal_version", "1.0"),
            operation=op,
            target_file=d.get("target_file") or d.get("file_path", ""),
            file_path=d.get("file_path") or d.get("target_file", ""),
            expected_sha256=d.get("expected_sha256", ""),
            target_symbol=d.get("target_symbol"),
            target_range=d.get("target_range"),
            replacement=d.get("replacement") or d.get("content", ""),
            content=d.get("content") or d.get("replacement", ""),
            rationale=d.get("rationale", ""),
            validation_requirements=d.get("validation_requirements") or [],
        )


@dataclass
class UnrealModificationBackup:
    """Tracks a pre-modification backup for atomic rollback."""
    backup_id: str
    target_file: str
    backup_path: str
    original_sha256: str
    created_at: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "backup_id": self.backup_id,
            "target_file": self.target_file,
            "backup_path": self.backup_path,
            "original_sha256": self.original_sha256,
            "created_at": self.created_at,
        }


@dataclass
class UnrealModificationResult:
    """Outcome of an atomic source modification."""
    success: bool
    operation: str
    target_file: str
    original_sha256: str = ""
    new_sha256: str = ""
    backup_id: str = ""
    backup_path: str = ""
    backup_created: bool = False
    lines_changed: int = 0
    bytes_changed: int = 0
    syntax_verified: bool = False
    rollback_performed: bool = False
    error_code: Optional[str] = None
    error_message: Optional[str] = None

    @property
    def target_path(self) -> str:
        return self.target_file

    def __post_init__(self):
        if self.backup_id and not self.backup_created:
            self.backup_created = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "operation": self.operation,
            "target_file": self.target_file,
            "target_path": self.target_file,
            "original_sha256": self.original_sha256,
            "new_sha256": self.new_sha256,
            "backup_id": self.backup_id,
            "backup_path": self.backup_path,
            "backup_created": self.backup_created,
            "lines_changed": self.lines_changed,
            "bytes_changed": self.bytes_changed,
            "syntax_verified": self.syntax_verified,
            "rollback_performed": self.rollback_performed,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


# =============================================================================
# 3. Deterministic C++ Source Analyzer
# =============================================================================

class UnrealCppAnalyzer:
    """
    Deterministic structural parser for Unreal Engine C++ source and header files.
    Safely inspects classes, reflection macros, methods, properties, enums,
    and includes with bounded character/line locations.
    """

    def __init__(self, safety_gate: Optional[UnrealSafetyGate] = None):
        self.safety = safety_gate or DEFAULT_UNREAL_SAFETY_GATE

    RE_INCLUDE = re.compile(r'^[ \t]*#[ \t]*include[ \t]+([<"][^>"]+[>"])', re.MULTILINE)
    RE_NAMESPACE = re.compile(r'\bnamespace[ \t]+([A-Za-z_][A-Za-z0-9_]*)[ \t]*\{', re.MULTILINE)

    # Unreal Reflection Macro regexes
    RE_UCLASS = re.compile(r'\bUCLASS[ \t]*\(([^)]*)\)', re.MULTILINE)
    RE_USTRUCT = re.compile(r'\bUSTRUCT[ \t]*\(([^)]*)\)', re.MULTILINE)
    RE_UENUM = re.compile(r'\bUENUM[ \t]*\(([^)]*)\)', re.MULTILINE)
    RE_UFUNCTION = re.compile(r'\bUFUNCTION[ \t]*\(([^)]*)\)', re.MULTILINE)
    RE_UPROPERTY = re.compile(r'\bUPROPERTY[ \t]*\(([^)]*)\)', re.MULTILINE)
    RE_GENERATED_BODY = re.compile(r'\b(GENERATED_BODY|GENERATED_UCLASS_BODY)[ \t]*\(\)', re.MULTILINE)

    # Class & Struct declarations
    RE_CLASS_DECL = re.compile(
        r'\b(class|struct)[ \t]+([A-Za-z0-9_]+_API[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)[ \t]*(?::[ \t]*([^{;]+))?\{',
        re.MULTILINE
    )

    RE_ENUM_DECL = re.compile(
        r'\benum(\s+class)?\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*([A-Za-z0-9_]+))?\s*\{([^}]+)\};',
        re.MULTILINE
    )

    RE_GLOBAL_FUNC = re.compile(
        r'^(?:[ \t]*(?:inline|static|virtual)[ \t]+)?'
        r'(?:([A-Za-z0-9_&*:<>, \t]+?)[ \t]+)?'
        r'(?:([A-Za-z_~][A-Za-z0-9_]*)::)?'
        r'([A-Za-z_~][A-Za-z0-9_]*)'
        r'[ \t]*\(([^)]*)\)'
        r'[ \t]*(?:const)?[ \t]*'
        r'\s*\{',
        re.MULTILINE
    )

    def analyze_source(self, content: str, file_path: str = "") -> UnrealCppFileAnalysis:
        """Parses C++ source content into a structured UnrealCppFileAnalysis model."""
        if not content:
            return UnrealCppFileAnalysis(
                file_path=file_path,
                sha256=hashlib.sha256(b"").hexdigest(),
                line_count=0,
                byte_size=0,
            )

        sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
        lines = content.splitlines()
        line_count = len(lines)
        byte_size = len(content.encode("utf-8"))

        includes = self._extract_includes(content)
        reflection_macros = self._extract_reflection_macros(content)
        namespaces = self._extract_namespaces(content)
        classes, structs = self._extract_classes_and_structs(content)
        enums = self._extract_enums(content)

        class_spans = [(c.location.start_char, c.location.end_char) for c in classes + structs]
        global_functions = self._extract_global_functions(content, class_spans)

        # Syntax / balanced bracket check
        syntax_errors = []
        is_balanced, balance_err = self.verify_balanced_syntax(content)
        if not is_balanced:
            syntax_errors.append(balance_err)

        return UnrealCppFileAnalysis(
            file_path=file_path,
            sha256=sha,
            line_count=line_count,
            byte_size=byte_size,
            includes=includes,
            classes=classes,
            structs=structs,
            enums=enums,
            global_functions=global_functions,
            reflection_macros=reflection_macros,
            namespaces=namespaces,
            syntax_errors=syntax_errors,
            is_valid=len(syntax_errors) == 0,
        )

    def analyze_file(self, file_path: Path) -> UnrealCppFileAnalysis:
        """Reads and analyzes a local C++ file."""
        p = Path(file_path).resolve()
        if not p.is_file():
            raise FileNotFoundError(f"Source file not found at '{p}'")
        txt = p.read_text(encoding="utf-8", errors="ignore")
        return self.analyze_source(txt, file_path=str(p).replace("\\", "/"))

    def verify_balanced_syntax(self, content: str) -> Tuple[bool, str]:
        """
        Verifies that braces, brackets, and parentheses are properly balanced
        and strings/comments are closed.
        """
        # Strip comments and string literals
        clean, _ = self._strip_comments_and_strings(content)

        stack = []
        pairs = {')': '(', '}': '{', ']': '['}

        for idx, ch in enumerate(clean):
            if ch in "({[":
                stack.append((ch, idx))
            elif ch in ")}]":
                expected = pairs[ch]
                if not stack:
                    line_num = content[:idx].count("\n") + 1
                    return False, f"Unmatched closing '{ch}' at line {line_num}"
                last_ch, last_idx = stack.pop()
                if last_ch != expected:
                    line_num = content[:idx].count("\n") + 1
                    return False, f"Mismatched bracket: expected closing for '{last_ch}' but found '{ch}' at line {line_num}"

        if stack:
            unclosed_ch, unclosed_idx = stack[-1]
            line_num = content[:unclosed_idx].count("\n") + 1
            return False, f"Unclosed '{unclosed_ch}' starting at line {line_num}"

        return True, None

    def find_symbol(self, analysis: UnrealCppFileAnalysis, symbol_name: str) -> List[Dict[str, Any]]:
        """Searches an analysis report for classes, methods, or properties matching a symbol name."""
        results = []
        s_lower = symbol_name.lower()

        # Check classes and structs
        for c in analysis.classes + analysis.structs:
            if c.name.lower() == s_lower:
                results.append({
                    "symbol_type": c.kind.value,
                    "name": c.name,
                    "location": c.location.to_dict(),
                    "parent_class": c.base_classes,
                    "details": c.to_dict(),
                })
            # Check methods
            for m in c.methods:
                if m.name.lower() == s_lower:
                    results.append({
                        "symbol_type": "method",
                        "name": m.name,
                        "class_name": c.name,
                        "location": m.location.to_dict(),
                        "return_type": m.return_type,
                        "details": m.to_dict(),
                    })
            # Check properties
            for prop in c.properties:
                if prop.name.lower() == s_lower:
                    results.append({
                        "symbol_type": "property",
                        "name": prop.name,
                        "class_name": c.name,
                        "location": prop.location.to_dict(),
                        "type_name": prop.type_name,
                        "details": prop.to_dict(),
                    })

        # Check enums
        for e in analysis.enums:
            if e.name.lower() == s_lower:
                results.append({
                    "symbol_type": "enum",
                    "name": e.name,
                    "location": e.location.to_dict(),
                    "details": e.to_dict(),
                })
            for entry in e.entries:
                if entry.name.lower() == s_lower:
                    results.append({
                        "symbol_type": "enum_entry",
                        "name": entry.name,
                        "enum_name": e.name,
                        "location": entry.location.to_dict(),
                        "details": entry.to_dict(),
                    })

        return results

    # -------------------------------------------------------------------------
    # Internal Extraction Helpers
    # -------------------------------------------------------------------------

    def _strip_comments_and_strings(self, text: str) -> Tuple[str, List[Tuple[int, int]]]:
        """
        Replaces comments and strings with whitespace to preserve exact character
        offsets and line counts.
        """
        result = list(text)
        n = len(text)
        i = 0
        while i < n:
            # Line comment
            if text[i:i+2] == '//':
                start = i
                while i < n and text[i] != '\n':
                    result[i] = ' '
                    i += 1
            # Block comment
            elif text[i:i+2] == '/*':
                start = i
                result[i] = ' '
                result[i+1] = ' '
                i += 2
                while i < n and text[i:i+2] != '*/':
                    if result[i] != '\n':
                        result[i] = ' '
                    i += 1
                if i < n:
                    result[i] = ' '
                    if i + 1 < n:
                        result[i+1] = ' '
                    i += 2
            # String literal
            elif text[i] == '"':
                result[i] = ' '
                i += 1
                while i < n and text[i] != '"':
                    if text[i] == '\\' and i + 1 < n:
                        result[i] = ' '
                        result[i+1] = ' '
                        i += 2
                    else:
                        if result[i] != '\n':
                            result[i] = ' '
                        i += 1
                if i < n:
                    result[i] = ' '
                    i += 1
            # Char literal
            elif text[i] == "'":
                result[i] = ' '
                i += 1
                while i < n and text[i] != "'":
                    if text[i] == '\\' and i + 1 < n:
                        result[i] = ' '
                        result[i+1] = ' '
                        i += 2
                    else:
                        if result[i] != '\n':
                            result[i] = ' '
                        i += 1
                if i < n:
                    result[i] = ' '
                    i += 1
            else:
                i += 1
        return "".join(result), []

    def _extract_includes(self, content: str) -> List[UnrealIncludeDirective]:
        includes = []
        for m in self.RE_INCLUDE.finditer(content):
            raw_path = m.group(1)
            is_sys = raw_path.startswith("<")
            clean_path = raw_path.strip('<">')
            start = m.start()
            end = m.end()
            line = content[:start].count("\n") + 1
            col = start - content.rfind("\n", 0, start)
            end_line = content[:end].count("\n") + 1
            end_col = end - content.rfind("\n", 0, end)
            includes.append(UnrealIncludeDirective(
                path=clean_path,
                is_system=is_sys,
                location=UnrealSourceLocation(line=line, column=col, end_line=end_line, end_column=end_col, start_char=start, end_char=end),
            ))
        return includes

    def _extract_reflection_macros(self, content: str) -> List[UnrealReflectionMacro]:
        macros = []
        specs = [
            (self.RE_UCLASS, UnrealReflectionKind.UCLASS),
            (self.RE_USTRUCT, UnrealReflectionKind.USTRUCT),
            (self.RE_UENUM, UnrealReflectionKind.UENUM),
            (self.RE_UFUNCTION, UnrealReflectionKind.UFUNCTION),
            (self.RE_UPROPERTY, UnrealReflectionKind.UPROPERTY),
        ]
        for pattern, kind in specs:
            for m in pattern.finditer(content):
                raw_specs = m.group(1).strip()
                spec_list = [s.strip() for s in raw_specs.split(",") if s.strip()] if raw_specs else []
                start = m.start()
                end = m.end()
                line = content[:start].count("\n") + 1
                col = start - content.rfind("\n", 0, start)
                end_line = content[:end].count("\n") + 1
                end_col = end - content.rfind("\n", 0, end)
                macros.append(UnrealReflectionMacro(
                    kind=kind,
                    specifiers=spec_list,
                    raw_text=m.group(0),
                    location=UnrealSourceLocation(line=line, column=col, end_line=end_line, end_column=end_col, start_char=start, end_char=end),
                ))

        for m in self.RE_GENERATED_BODY.finditer(content):
            start = m.start()
            end = m.end()
            line = content[:start].count("\n") + 1
            col = start - content.rfind("\n", 0, start)
            end_line = content[:end].count("\n") + 1
            end_col = end - content.rfind("\n", 0, end)
            macros.append(UnrealReflectionMacro(
                kind=UnrealReflectionKind.GENERATED_BODY if m.group(1) == "GENERATED_BODY" else UnrealReflectionKind.GENERATED_UCLASS_BODY,
                specifiers=[],
                raw_text=m.group(0),
                location=UnrealSourceLocation(line=line, column=col, end_line=end_line, end_column=end_col, start_char=start, end_char=end),
            ))

        macros.sort(key=lambda m: m.location.start_char)
        return macros

    def _extract_namespaces(self, content: str) -> List[str]:
        return [m.group(1) for m in self.RE_NAMESPACE.finditer(content)]


    def _extract_global_functions(
        self,
        content: str,
        class_spans: List[Tuple[int, int]],
    ) -> List[UnrealMethodSymbol]:
        """Extracts top-level or class-implementation functions defined outside class declarations."""
        functions: List[UnrealMethodSymbol] = []
        for m in self.RE_GLOBAL_FUNC.finditer(content):
            start_char = m.start()
            if any(cs[0] <= start_char <= cs[1] for cs in class_spans):
                continue

            ret_type = m.group(1)
            cls_name = m.group(2)
            func_name = m.group(3)
            params_raw = m.group(4)

            if func_name in ("if", "for", "while", "switch", "catch", "do"):
                continue

            open_brace_idx = m.end() - 1
            close_brace_idx = self._find_matching_brace(content, open_brace_idx)
            if close_brace_idx == -1:
                continue

            end_char = close_brace_idx + 1
            line = content[:start_char].count("\n") + 1
            col = start_char - content.rfind("\n", 0, start_char)
            end_line = content[:end_char].count("\n") + 1
            end_col = end_char - content.rfind("\n", 0, end_char)

            b_line = content[:open_brace_idx].count("\n") + 1
            b_col = open_brace_idx - content.rfind("\n", 0, open_brace_idx)
            b_end_line = content[:end_char].count("\n") + 1
            b_end_col = end_char - content.rfind("\n", 0, end_char)

            body_loc = UnrealSourceLocation(
                line=b_line,
                column=b_col,
                end_line=b_end_line,
                end_column=b_end_col,
                start_char=open_brace_idx,
                end_char=end_char,
            )

            params = [p.strip() for p in params_raw.split(",") if p.strip()] if params_raw else []

            functions.append(UnrealMethodSymbol(
                name=func_name,
                return_type=ret_type.strip() if ret_type else "void",
                parameters=params,
                access=UnrealAccessModifier.PUBLIC,
                class_name=cls_name,
                location=UnrealSourceLocation(
                    line=line, column=col, end_line=end_line, end_column=end_col,
                    start_char=start_char, end_char=end_char
                ),
                body_location=body_loc,
                raw_declaration=m.group(0),
            ))

        return functions

    def _find_matching_brace(self, text: str, open_brace_idx: int) -> int:
        """Finds the index of the matching closing brace '}'."""
        depth = 0
        in_line_comment = False
        in_block_comment = False
        in_string = False
        n = len(text)
        i = open_brace_idx

        while i < n:
            ch = text[i]
            if in_line_comment:
                if ch == '\n':
                    in_line_comment = False
            elif in_block_comment:
                if text[i:i+2] == '*/':
                    in_block_comment = False
                    i += 1
            elif in_string:
                if ch == '\\' and i + 1 < n:
                    i += 1
                elif ch == '"':
                    in_string = False
            else:
                if text[i:i+2] == '//':
                    in_line_comment = True
                    i += 1
                elif text[i:i+2] == '/*':
                    in_block_comment = True
                    i += 1
                elif ch == '"':
                    in_string = True
                elif ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        return i
            i += 1
        return -1

    def _extract_classes_and_structs(self, content: str) -> Tuple[List[UnrealClassSymbol], List[UnrealClassSymbol]]:
        classes: List[UnrealClassSymbol] = []
        structs: List[UnrealClassSymbol] = []

        reflection_macros = self._extract_reflection_macros(content)

        for m in self.RE_CLASS_DECL.finditer(content):
            # Skip enum class declarations
            prefix = content[:m.start()].rstrip()
            if prefix.endswith("enum"):
                continue

            kind_str = m.group(1)
            api_macro = m.group(2).strip() if m.group(2) else None
            name = m.group(3)
            base_str = m.group(4)

            # Find matching closing brace
            open_brace_idx = m.end() - 1
            close_brace_idx = self._find_matching_brace(content, open_brace_idx)
            if close_brace_idx == -1:
                close_brace_idx = len(content)

            start = m.start()
            end = close_brace_idx + 1
            line = content[:start].count("\n") + 1
            col = start - content.rfind("\n", 0, start)
            end_line = content[:end].count("\n") + 1
            end_col = end - content.rfind("\n", 0, end)

            # Extract base classes
            bases = []
            if base_str:
                for b in base_str.split(","):
                    b_clean = re.sub(r'\b(public|protected|private|virtual)\b', '', b).strip()
                    if b_clean:
                        bases.append(b_clean)

            # Look for reflection macro immediately preceding the class
            refl: Optional[UnrealReflectionMacro] = None
            for rm in reversed(reflection_macros):
                if rm.location.end_line >= line - 3 and rm.location.start_char < start:
                    if (kind_str == "class" and rm.kind == UnrealReflectionKind.UCLASS) or \
                       (kind_str == "struct" and rm.kind == UnrealReflectionKind.USTRUCT):
                        refl = rm
                        break

            class_body = content[open_brace_idx + 1:close_brace_idx]
            has_gen_body = bool(self.RE_GENERATED_BODY.search(class_body))

            # Parse members
            methods, properties = self._extract_class_members(class_body, open_brace_idx + 1, content, class_name=name)
            enums = self._extract_enums(class_body, offset_char=open_brace_idx + 1, full_content=content)

            sym = UnrealClassSymbol(
                name=name,
                kind=UnrealSymbolKind.CLASS if kind_str == "class" else UnrealSymbolKind.STRUCT,
                api_macro=api_macro,
                base_classes=bases,
                methods=methods,
                properties=properties,
                enums=enums,
                has_generated_body=has_gen_body,
                reflection=refl,
                location=UnrealSourceLocation(line=line, column=col, end_line=end_line, end_column=end_col, start_char=start, end_char=end),
                raw_declaration=m.group(0),
            )

            if kind_str == "class":
                classes.append(sym)
            else:
                structs.append(sym)

        return classes, structs

    def _extract_class_members(
        self,
        class_body: str,
        body_start_char: int,
        full_content: str,
        class_name: Optional[str] = None,
    ) -> Tuple[List[UnrealMethodSymbol], List[UnrealPropertySymbol]]:
        methods: List[UnrealMethodSymbol] = []
        properties: List[UnrealPropertySymbol] = []

        # Track access modifier (default is private for class, public for struct)
        current_access = UnrealAccessModifier.PUBLIC

        # Regex for access labels
        re_access = re.compile(r'\b(public|protected|private)[ \t]*:')

        # Regex for methods: [virtual] [static] Type Name(Params) [const] [override] [{ ... } | ;]
        re_method = re.compile(
            r'(?:(UFUNCTION[ \t]*\([^)]*\)[ \t]*\n[ \t]*))?'
            r'(?:(virtual|static)[ \t]+)?'
            r'([A-Za-z_][A-Za-z0-9_:*&<>, \t]*?)[ \t]+'
            r'([A-Za-z_~][A-Za-z0-9_]*)[ \t]*'
            r'\(([^)]*)\)[ \t]*'
            r'(const)?[ \t]*'
            r'(override)?[ \t]*'
            r'(?:=[ \t]*0)?[ \t]*'
            r'([{;])',
            re.MULTILINE
        )

        # Regex for UPROPERTY and member variables
        re_prop = re.compile(
            r'(?:(UPROPERTY[ \t]*\([^)]*\)[ \t]*\n[ \t]*))'
            r'([A-Za-z_][A-Za-z0-9_:*&<>, \t]+?)[ \t]+'
            r'([A-Za-z_][A-Za-z0-9_]*)[ \t]*'
            r'(?:=[^;]+)?'
            r';',
            re.MULTILINE
        )

        # Scan for access specifiers and split into chunks
        for m in re_method.finditer(class_body):
            ufunc_raw = m.group(1)
            is_virt_stat = m.group(2)
            ret_type = m.group(3).strip()
            name = m.group(4).strip()
            params_raw = m.group(5).strip()
            is_const = bool(m.group(6))
            is_override = bool(m.group(7))
            tail = m.group(8)

            # Skip control structures
            if name in ("if", "for", "while", "switch", "catch", "return"):
                continue

            # Check access modifier preceding this position in class_body
            prefix = class_body[:m.start()]
            last_access = list(re_access.finditer(prefix))
            if last_access:
                current_access = UnrealAccessModifier(last_access[-1].group(1))

            start_char = body_start_char + m.start()
            end_char = body_start_char + m.end()

            # If method has inline body '{', find matching brace
            body_loc: Optional[UnrealSourceLocation] = None
            if tail == '{':
                brace_start = body_start_char + m.end() - 1
                brace_end = self._find_matching_brace(full_content, brace_start)
                if brace_end != -1:
                    end_char = brace_end + 1
                    b_line = full_content[:brace_start].count("\n") + 1
                    b_col = brace_start - full_content.rfind("\n", 0, brace_start)
                    b_end_line = full_content[:brace_end+1].count("\n") + 1
                    b_end_col = (brace_end+1) - full_content.rfind("\n", 0, brace_end+1)
                    body_loc = UnrealSourceLocation(
                        line=b_line, column=b_col, end_line=b_end_line, end_column=b_end_col,
                        start_char=brace_start, end_char=brace_end + 1
                    )

            line = full_content[:start_char].count("\n") + 1
            col = start_char - full_content.rfind("\n", 0, start_char)
            end_line = full_content[:end_char].count("\n") + 1
            end_col = end_char - full_content.rfind("\n", 0, end_char)

            params = [p.strip() for p in params_raw.split(",") if p.strip()] if params_raw else []

            refl: Optional[UnrealReflectionMacro] = None
            if ufunc_raw:
                refl = UnrealReflectionMacro(
                    kind=UnrealReflectionKind.UFUNCTION,
                    specifiers=[s.strip() for s in ufunc_raw.replace("UFUNCTION(", "").replace(")", "").split(",") if s.strip()],
                    raw_text=ufunc_raw.strip(),
                )

            methods.append(UnrealMethodSymbol(
                name=name,
                return_type=ret_type,
                parameters=params,
                access=current_access,
                is_const=is_const,
                is_virtual=(is_virt_stat == "virtual"),
                is_override=is_override,
                is_static=(is_virt_stat == "static"),
                class_name=class_name,
                reflection=refl,
                location=UnrealSourceLocation(line=line, column=col, end_line=end_line, end_column=end_col, start_char=start_char, end_char=end_char),
                body_location=body_loc,
                raw_declaration=m.group(0),
            ))

        for m in re_prop.finditer(class_body):
            uprop_raw = m.group(1)
            type_name = m.group(2).strip()
            name = m.group(3).strip()

            prefix = class_body[:m.start()]
            last_access = list(re_access.finditer(prefix))
            if last_access:
                current_access = UnrealAccessModifier(last_access[-1].group(1))

            start_char = body_start_char + m.start()
            end_char = body_start_char + m.end()
            line = full_content[:start_char].count("\n") + 1
            col = start_char - full_content.rfind("\n", 0, start_char)
            end_line = full_content[:end_char].count("\n") + 1
            end_col = end_char - full_content.rfind("\n", 0, end_char)

            refl = UnrealReflectionMacro(
                kind=UnrealReflectionKind.UPROPERTY,
                specifiers=[s.strip() for s in uprop_raw.replace("UPROPERTY(", "").replace(")", "").split(",") if s.strip()],
                raw_text=uprop_raw.strip(),
            )

            properties.append(UnrealPropertySymbol(
                name=name,
                type_name=type_name,
                access=current_access,
                class_name=class_name,
                reflection=refl,
                location=UnrealSourceLocation(line=line, column=col, end_line=end_line, end_column=end_col, start_char=start_char, end_char=end_char),
                raw_declaration=m.group(0),
            ))

        return methods, properties

    def _extract_enums(
        self,
        content: str,
        offset_char: int = 0,
        full_content: Optional[str] = None,
    ) -> List[UnrealEnumSymbol]:
        enums: List[UnrealEnumSymbol] = []
        target_full = full_content or content

        for m in self.RE_ENUM_DECL.finditer(content):
            is_enum_class = bool(m.group(1))
            name = m.group(2)
            underlying = m.group(3)
            entries_str = m.group(4)

            start_char = offset_char + m.start()
            end_char = offset_char + m.end()
            line = target_full[:start_char].count("\n") + 1
            col = start_char - target_full.rfind("\n", 0, start_char)
            end_line = target_full[:end_char].count("\n") + 1
            end_col = end_char - target_full.rfind("\n", 0, end_char)

            # Strip UMETA(...) blocks before splitting on commas
            clean_entries_str = re.sub(r'UMETA\s*\([^)]*\)', '', entries_str)

            entries: List[UnrealEnumEntry] = []
            for item in clean_entries_str.split(","):
                item_clean = item.strip()
                if item_clean:
                    if "=" in item_clean:
                        en_name, en_val = item_clean.split("=", 1)
                        entries.append(UnrealEnumEntry(name=en_name.strip(), value=en_val.strip()))
                    else:
                        entries.append(UnrealEnumEntry(name=item_clean))

            enums.append(UnrealEnumSymbol(
                name=name,
                is_enum_class=is_enum_class,
                underlying_type=underlying,
                entries=entries,
                location=UnrealSourceLocation(line=line, column=col, end_line=end_line, end_column=end_col, start_char=start_char, end_char=end_char),
            ))

        return enums


# =============================================================================
# 4. Blueprint Intelligence Foundation
# =============================================================================

class UnrealBlueprintInspector:
    """
    Safely discovers and inspects Blueprint assets in Content/.
    Extracts package names, asset names, and parent class references
    without executing binary code or fabricating graph execution.
    """

    def __init__(self, safety_gate: Optional[UnrealSafetyGate] = None):
        self.safety = safety_gate or DEFAULT_UNREAL_SAFETY_GATE

    def list_blueprint_assets(
        self,
        project_path: Optional[Union[str, Path]] = None,
        subfolder: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[UnrealBlueprintAssetInfo]:
        """Discovers all Blueprint (.uasset) files under Content/."""
        self.safety.assert_not_emergency_stopped()
        self.safety.check_rate_limit("list_blueprint_assets")

        proj_path = Path(project_path) if project_path else self.safety.default_project
        target = proj_path.resolve()
        content_dir = target / "Content"
        if subfolder:
            content_dir = content_dir / subfolder.lstrip("/\\")

        if not content_dir.is_dir():
            return []

        results: List[UnrealBlueprintAssetInfo] = []
        for p in content_dir.rglob("*.uasset"):
            if p.is_file():
                name = p.stem
                is_bp = name.startswith("BP_") or name.startswith("WBP_") or "Blueprint" in name
                asset_type = "WidgetBlueprint" if name.startswith("WBP_") else ("Blueprint" if is_bp else "Asset")
                info = self.inspect_blueprint_metadata(p, target)
                info.asset_type = asset_type
                results.append(info)
                if limit and len(results) >= limit:
                    break

        return results

    def inspect_blueprint_metadata(
        self,
        asset_path: Union[str, Path],
        project_path: Optional[Union[str, Path]] = None,
    ) -> UnrealBlueprintAssetInfo:
        """Inspects metadata for a single Blueprint .uasset file."""
        self.safety.assert_not_emergency_stopped()
        self.safety.check_rate_limit("inspect_blueprint_metadata")

        p = Path(asset_path).resolve()
        if not p.is_file():
            return UnrealBlueprintAssetInfo(
                asset_name=p.stem,
                asset_path=str(p).replace("\\", "/"),
                package_path=f"/Game/{p.stem}",
                is_valid=False,
                limitations_note="Asset file does not exist on disk.",
            )

        file_size = p.stat().st_size
        sha = hashlib.sha256(p.read_bytes()).hexdigest()

        # Compute /Game/... package path relative to Content
        package_path = f"/Game/{p.stem}"
        try:
            # Look for Content in parts
            parts = p.parts
            if "Content" in parts:
                idx = parts.index("Content")
                rel_parts = parts[idx+1:-1]
                if rel_parts:
                    package_path = f"/Game/{'/'.join(rel_parts)}/{p.stem}"
                else:
                    package_path = f"/Game/{p.stem}"
        except Exception:
            pass

        # Inspect binary header deterministically for native parent class hint
        parent_class = "Actor"
        try:
            raw_bytes = p.read_bytes()[:4096]
            raw_str = raw_bytes.decode("ascii", errors="ignore")
            if "Character" in raw_str:
                parent_class = "Character"
            elif "Pawn" in raw_str:
                parent_class = "Pawn"
            elif "UserWidget" in raw_str:
                parent_class = "UserWidget"
            elif "GameModeBase" in raw_str:
                parent_class = "GameModeBase"
            elif "ActorComponent" in raw_str:
                parent_class = "ActorComponent"
            elif "Actor" in raw_str:
                parent_class = "Actor"
            else:
                parent_class = "UObject"
        except Exception:
            parent_class = "UObject"

        return UnrealBlueprintAssetInfo(
            asset_name=p.stem,
            asset_path=str(p).replace("\\", "/"),
            package_path=package_path,
            parent_class=parent_class,
            generated_class=f"{p.stem}_C",
            native_parent=f"/Script/Engine.{parent_class}",
            file_size=file_size,
            sha256=sha,
            is_valid=True,
        )


# =============================================================================
# 5. Safe Source Modification Engine
# =============================================================================

class UnrealSourceModifier:
    """
    Executes bounded, verified, atomic C++ source code modifications:
    - Pre-condition SHA-256 stale target rejection
    - Schema validation & prohibited token rejection
    - Automatic backup creation
    - Temporary file atomic swap
    - Post-write syntax/balance verification
    - Automatic verified rollback on failure
    """

    def __init__(
        self,
        safety_gate: Optional[UnrealSafetyGate] = None,
        checkpoint_dir: Optional[Path] = None,
        backup_root: Optional[Path] = None,
        analyzer: Optional[UnrealCppAnalyzer] = None,
        audit_logger: Optional[AuditLogger] = None,
    ):
        self.safety = safety_gate or DEFAULT_UNREAL_SAFETY_GATE
        self.checkpoint_dir = (checkpoint_dir or backup_root or DEFAULT_UNREAL_CHECKPOINT_DIR).resolve()
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self.audit_logger = audit_logger or AuditLogger()
        self.analyzer = analyzer or UnrealCppAnalyzer(safety_gate=self.safety)
        self._backups: Dict[str, UnrealModificationBackup] = {}

    def create_proposal(
        self,
        operation: Union[str, UnrealModificationOperation],
        target_file: Union[str, Path],
        expected_sha256: str,
        replacement: str,
        rationale: str,
        target_symbol: Optional[str] = None,
        target_range: Optional[Dict[str, int]] = None,
        validation_requirements: Optional[List[str]] = None,
    ) -> UnrealModificationProposal:
        """Creates a validated modification proposal."""
        op_enum = (
            operation if isinstance(operation, UnrealModificationOperation)
            else UnrealModificationOperation(operation)
        )
        prop = UnrealModificationProposal(
            proposal_id=str(uuid.uuid4()),
            proposal_version="1.0",
            operation=op_enum,
            target_file=str(target_file).replace("\\", "/"),
            expected_sha256=expected_sha256,
            target_symbol=target_symbol,
            target_range=target_range,
            replacement=replacement,
            rationale=rationale,
            validation_requirements=validation_requirements or ["balanced_syntax", "hash_verification"],
        )
        self.safety.validate_proposal_payload(prop.to_dict())
        return prop

    def validate_proposal(self, proposal: Union[UnrealModificationProposal, Dict[str, Any]]) -> Dict[str, Any]:
        """Validates a proposal dictionary or object against safety boundaries and verifies target hash."""
        p_dict = proposal.to_dict() if isinstance(proposal, UnrealModificationProposal) else proposal
        try:
            self.safety.validate_proposal_payload(p_dict)

            # Pre-verify target file exists and SHA-256 matches expected_sha256
            target_str = p_dict.get("target_file") or p_dict.get("file_path", "")
            target_path = self.safety.validate_source_file_path(target_str, must_exist=True)
            current_bytes = target_path.read_bytes()
            current_sha = hashlib.sha256(current_bytes).hexdigest()
            expected_sha = p_dict.get("expected_sha256", "")

            if expected_sha and current_sha.lower() != expected_sha.lower():
                return {
                    "valid": False,
                    "proposal_id": p_dict.get("proposal_id"),
                    "operation": p_dict.get("operation"),
                    "target_file": p_dict.get("target_file"),
                    "error_code": UnrealErrorCode.STALE_TARGET.value,
                    "error": f"Stale target: current SHA-256 ({current_sha}) does not match expected ({expected_sha}).",
                }

            return {
                "valid": True,
                "proposal_id": p_dict.get("proposal_id"),
                "operation": p_dict.get("operation"),
                "target_file": p_dict.get("target_file"),
                "error": None,
            }
        except UnrealSafetyError as se:
            return {
                "valid": False,
                "proposal_id": p_dict.get("proposal_id"),
                "operation": p_dict.get("operation"),
                "target_file": p_dict.get("target_file"),
                "error_code": se.code.value,
                "error": se.message,
            }

    def apply_modification(
        self,
        proposal: Union[UnrealModificationProposal, Dict[str, Any]],
    ) -> UnrealModificationResult:
        """
        Applies a validated modification proposal atomically with verified backup and rollback.
        """
        self.safety.assert_not_emergency_stopped()
        self.safety.check_rate_limit("apply_cpp_change")

        p_dict = proposal.to_dict() if isinstance(proposal, UnrealModificationProposal) else proposal
        self.safety.validate_proposal_payload(p_dict)

        target_str = p_dict["target_file"]
        target_path = self.safety.validate_source_file_path(target_str, must_exist=True)
        op_str = p_dict["operation"]
        expected_sha = p_dict["expected_sha256"]
        replacement = p_dict["replacement"]
        target_symbol = p_dict.get("target_symbol")
        target_range = p_dict.get("target_range")

        # 1. Stale target check (SHA-256 pre-verification)
        current_bytes = target_path.read_bytes()
        actual_sha = hashlib.sha256(current_bytes).hexdigest()
        if actual_sha.lower() != expected_sha.lower():
            logger.warning(f"Stale target rejected: expected {expected_sha}, found {actual_sha}")
            return UnrealModificationResult(
                success=False,
                operation=op_str,
                target_file=str(target_path).replace("\\", "/"),
                original_sha256=actual_sha,
                error_code=UnrealErrorCode.STALE_TARGET.value,
                error_message=f"Target file '{target_path.name}' has been modified (actual SHA {actual_sha[:8]} != expected {expected_sha[:8]}).",
            )

        # 2. Create verified backup
        backup = self._create_backup(target_path, actual_sha)

        # 3. Read content and analyze
        content = current_bytes.decode("utf-8", errors="ignore")
        lines = content.splitlines(keepends=True)
        analysis = self.analyzer.analyze_source(content, file_path=str(target_path))

        # 4. Apply bounded operation
        try:
            new_content = self._perform_operation(
                lines=lines,
                operation=UnrealModificationOperation(op_str),
                replacement=replacement,
                target_symbol=target_symbol,
                target_range=target_range,
                analysis=analysis,
            )
        except Exception as e:
            logger.error(f"Modification operation failed: {e}")
            return UnrealModificationResult(
                success=False,
                operation=op_str,
                target_file=str(target_path).replace("\\", "/"),
                original_sha256=actual_sha,
                backup_id=backup.backup_id,
                error_code=UnrealErrorCode.INVALID_OPERATION.value,
                error_message=f"Failed to apply {op_str}: {e}",
            )

        # 5. Pre-validate modified content before touching original file
        is_balanced, err_msg = self.analyzer.verify_balanced_syntax(new_content)
        if not is_balanced:
            logger.error(f"Syntactic structure validation failed: {err_msg}")
            return UnrealModificationResult(
                success=False,
                operation=op_str,
                target_file=str(target_path).replace("\\", "/"),
                original_sha256=actual_sha,
                backup_id=backup.backup_id,
                error_code=UnrealErrorCode.STRUCTURE_VALIDATION_FAILED.value,
                error_message=f"Syntax/bracket validation failed after modification: {err_msg}",
            )

        # 6. Atomic write via temporary file in same directory
        temp_path = target_path.with_name(f"{target_path.name}.tmp.{uuid.uuid4().hex[:8]}")
        try:
            temp_path.write_text(new_content, encoding="utf-8")
            # Verify temporary file on disk
            temp_sha = hashlib.sha256(temp_path.read_bytes()).hexdigest()

            # Atomic replace
            shutil.move(str(temp_path), str(target_path))

            # Post-replace verification
            post_bytes = target_path.read_bytes()
            post_sha = hashlib.sha256(post_bytes).hexdigest()
            if post_sha != temp_sha:
                raise IOError(f"Atomic replacement hash mismatch: {post_sha} != {temp_sha}")

        except Exception as write_err:
            logger.critical(f"Write failure occurred, triggering rollback: {write_err}")
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass
            self.rollback_modification(backup.backup_id)
            return UnrealModificationResult(
                success=False,
                operation=op_str,
                target_file=str(target_path).replace("\\", "/"),
                original_sha256=actual_sha,
                backup_id=backup.backup_id,
                rollback_performed=True,
                error_code=UnrealErrorCode.WRITE_FAILED.value,
                error_message=f"Write failed; original file restored from backup. Error: {write_err}",
            )

        # 7. Audit log event
        self.audit_logger.log_event(
            event_type="UNREAL_SOURCE_MODIFICATION",
            details={
                "component": "UnrealSourceModifier",
                "action": op_str,
                "target_file": str(target_path).replace("\\", "/"),
                "backup_id": backup.backup_id,
                "original_sha256": actual_sha,
                "new_sha256": post_sha,
            },
            status="success",
        )

        lines_diff = abs(len(new_content.splitlines()) - len(lines))
        bytes_diff = abs(len(post_bytes) - len(current_bytes))

        return UnrealModificationResult(
            success=True,
            operation=op_str,
            target_file=str(target_path).replace("\\", "/"),
            original_sha256=actual_sha,
            new_sha256=post_sha,
            backup_id=backup.backup_id,
            backup_path=str(backup.backup_path),
            backup_created=True,
            lines_changed=lines_diff,
            bytes_changed=bytes_diff,
            syntax_verified=True,
        )

    def rollback_modification(
        self,
        backup_id: Optional[str] = None,
        backup_path: Optional[Path] = None,
        target_path: Optional[Path] = None,
    ) -> UnrealModificationResult:
        """Restores a target file byte-for-byte from its verified backup checkpoint."""
        try:
            self.safety.assert_not_emergency_stopped()
            self.safety.check_rate_limit("rollback_cpp_change")

            backup = None
            if backup_id and backup_id in self._backups:
                backup = self._backups[backup_id]
            elif backup_path:
                bp_res = Path(backup_path).resolve()
                for b in self._backups.values():
                    if Path(b.backup_path).resolve() == bp_res:
                        backup = b
                        break

            if not backup:
                if not backup_id:
                    raise UnrealSafetyError(
                        UnrealErrorCode.INVALID_PARAMETER,
                        "backup_id or valid backup_path is required for rollback.",
                    )
                raise UnrealSafetyError(
                    UnrealErrorCode.TARGET_NOT_FOUND,
                    f"Backup ID '{backup_id}' not found in active checkpoint registry.",
                )

            target_p = Path(backup.target_file).resolve()
            backup_p = Path(backup.backup_path).resolve()

            if not backup_p.is_file():
                raise UnrealSafetyError(
                    UnrealErrorCode.ROLLBACK_FAILED,
                    f"Backup file missing on disk at '{backup_p}'.",
                )

            # Verify backup integrity before restoring
            backup_bytes = backup_p.read_bytes()
            backup_sha = hashlib.sha256(backup_bytes).hexdigest()
            if backup_sha != backup.original_sha256:
                raise UnrealSafetyError(
                    UnrealErrorCode.HASH_VERIFICATION_FAILED,
                    f"Backup integrity failure: hash {backup_sha} != expected {backup.original_sha256}",
                )

            # Restore atomically
            shutil.copyfile(str(backup_p), str(target_p))

            restored_sha = hashlib.sha256(target_p.read_bytes()).hexdigest()
            if restored_sha != backup.original_sha256:
                raise UnrealSafetyError(
                    UnrealErrorCode.ROLLBACK_FAILED,
                    "Restored file does not match original SHA-256.",
                )

            logger.info(f"Successfully rolled back '{target_p}' to backup '{backup.backup_id}'")
            return UnrealModificationResult(
                success=True,
                operation="ROLLBACK",
                target_file=str(target_p).replace("\\", "/"),
                original_sha256=backup.original_sha256,
                new_sha256=restored_sha,
                backup_id=backup.backup_id,
                backup_path=str(backup_p).replace("\\", "/"),
                rollback_performed=True,
            )
        except UnrealSafetyError as se:
            logger.error(f"Rollback safety error: {se.message}")
            return UnrealModificationResult(
                success=False,
                operation="ROLLBACK",
                target_file=str(target_path) if target_path else (backup.target_file if backup else ""),
                backup_id=backup_id or (backup.backup_id if backup else ""),
                error_code=se.code.value,
                error_message=se.message,
            )
        except Exception as e:
            logger.error(f"Rollback failed: {e}")
            return UnrealModificationResult(
                success=False,
                operation="ROLLBACK",
                target_file=str(target_path) if target_path else "",
                backup_id=backup_id or "",
                error_code=UnrealErrorCode.ROLLBACK_FAILED.value,
                error_message=str(e),
            )

    def verify_source_change(
        self,
        target_file: Union[str, Path],
        original_sha256: str,
        expected_new_sha256: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Verifies that a source modification was applied correctly and is syntactically sound."""
        p = self.safety.validate_source_file_path(target_file, must_exist=True)
        current_sha = hashlib.sha256(p.read_bytes()).hexdigest()

        analysis = self.analyzer.analyze_file(p)

        changed = current_sha != original_sha256
        hash_matched = True
        if expected_new_sha256:
            hash_matched = current_sha.lower() == expected_new_sha256.lower()

        return {
            "verified": changed and hash_matched and analysis.is_valid,
            "target_file": str(p).replace("\\", "/"),
            "original_sha256": original_sha256,
            "current_sha256": current_sha,
            "is_syntactically_valid": analysis.is_valid,
            "syntax_errors": analysis.syntax_errors,
            "line_count": analysis.line_count,
            "classes_found": len(analysis.classes),
            "methods_found": sum(len(c.methods) for c in analysis.classes),
        }

    # -------------------------------------------------------------------------
    # Internal Operation Handlers
    # -------------------------------------------------------------------------

    def _create_backup(self, target_path: Path, current_sha: str) -> UnrealModificationBackup:
        backup_id = f"backup_{uuid.uuid4().hex[:12]}"
        backup_file = self.checkpoint_dir / f"{target_path.name}.{backup_id}.bak"
        shutil.copyfile(str(target_path), str(backup_file))

        # Verify copy
        copied_sha = hashlib.sha256(backup_file.read_bytes()).hexdigest()
        if copied_sha != current_sha:
            raise IOError(f"Backup file verification failed: {copied_sha} != {current_sha}")

        backup = UnrealModificationBackup(
            backup_id=backup_id,
            target_file=str(target_path).replace("\\", "/"),
            backup_path=str(backup_file).replace("\\", "/"),
            original_sha256=current_sha,
            created_at=time.time(),
        )
        self._backups[backup_id] = backup
        return backup

    def _perform_operation(
        self,
        lines: List[str],
        operation: UnrealModificationOperation,
        replacement: str,
        target_symbol: Optional[str],
        target_range: Optional[Dict[str, int]],
        analysis: UnrealCppFileAnalysis,
    ) -> str:
        """Dispatches bounded operations and returns the new file text."""
        clean_rep = replacement.rstrip("\r\n") + "\n"

        if operation == UnrealModificationOperation.ADD_INCLUDE:
            return self._op_add_include(lines, clean_rep)
        elif operation == UnrealModificationOperation.REMOVE_INCLUDE:
            return self._op_remove_include(lines, replacement)
        elif operation == UnrealModificationOperation.ADD_METHOD:
            return self._op_add_method(lines, clean_rep, target_symbol, analysis)
        elif operation == UnrealModificationOperation.REPLACE_METHOD_BODY:
            return self._op_replace_method_body(lines, replacement, target_symbol, analysis)
        elif operation == UnrealModificationOperation.REPLACE_SOURCE_RANGE:
            return self._op_replace_source_range(lines, clean_rep, target_range)
        elif operation == UnrealModificationOperation.ADD_MEMBER_PROPERTY:
            return self._op_add_member_property(lines, clean_rep, target_symbol, analysis)
        elif operation == UnrealModificationOperation.ADD_ENUM_ENTRY:
            return self._op_add_enum_entry(lines, clean_rep, target_symbol, analysis)
        else:
            raise ValueError(f"Unsupported modification operation: {operation}")

    def _op_add_include(self, lines: List[str], replacement: str) -> str:
        """
        Adds an #include directive. In Unreal Engine headers, .generated.h MUST
        be the last include! If a *.generated.h include exists, insert right before it.
        """
        inc_line = replacement.strip()
        if not inc_line.startswith("#include"):
            inc_line = f'#include "{inc_line}"'
        inc_line += "\n"

        # Check if already included
        for l in lines:
            if inc_line.strip() == l.strip():
                return "".join(lines)

        # Locate *.generated.h or last include
        gen_idx = -1
        last_inc_idx = -1
        for idx, l in enumerate(lines):
            if l.strip().startswith("#include"):
                last_inc_idx = idx
                if ".generated.h" in l:
                    gen_idx = idx

        if gen_idx != -1:
            # Insert immediately before .generated.h
            lines.insert(gen_idx, inc_line)
        elif last_inc_idx != -1:
            # Insert after last include
            lines.insert(last_inc_idx + 1, inc_line)
        else:
            # Insert at top
            lines.insert(0, inc_line)

        return "".join(lines)

    def _op_remove_include(self, lines: List[str], target: str) -> str:
        target_clean = target.strip().strip('<">')
        new_lines = []
        found = False
        for l in lines:
            if l.strip().startswith("#include") and target_clean in l:
                found = True
                continue
            new_lines.append(l)
        if not found:
            raise ValueError(f"Include targeting '{target}' not found in source.")
        return "".join(new_lines)

    def _op_add_method(
        self,
        lines: List[str],
        replacement: str,
        target_class: Optional[str],
        analysis: UnrealCppFileAnalysis,
    ) -> str:
        """Adds a method inside the specified target class before closing '};'."""
        target_cls = None
        if target_class:
            target_cls = next((c for c in analysis.classes + analysis.structs if c.name == target_class), None)
        elif analysis.classes:
            target_cls = analysis.classes[0]

        if not target_cls:
            # If no target class in file (e.g. .cpp implementation file), append at file scope
            return "".join(lines).rstrip() + "\n\n" + replacement.strip() + "\n"

        # Class ends at target_cls.location.end_line (1-indexed)
        end_line = target_cls.location.end_line
        # Find closing brace line in lines
        insert_idx = end_line - 1
        # Insert with indentation
        indented_rep = "\t" + replacement.replace("\n", "\n\t").rstrip("\t") + "\n"
        lines.insert(insert_idx, indented_rep)
        return "".join(lines)

    def _op_replace_method_body(
        self,
        lines: List[str],
        replacement: str,
        target_method_name: Optional[str],
        analysis: UnrealCppFileAnalysis,
    ) -> str:
        """Replaces the body of a target method."""
        if not target_method_name:
            raise ValueError("target_symbol (method name) is required for REPLACE_METHOD_BODY.")

        target_method = None
        all_methods = list(analysis.methods)
        for c in analysis.classes + analysis.structs:
            all_methods.extend(c.methods)

        for m in all_methods:
            if m.name == target_method_name:
                target_method = m
                break

        if not target_method:
            raise ValueError(f"Method '{target_method_name}' not found in file.")

        if not target_method.body_location:
            raise ValueError(f"Method '{target_method_name}' does not have an inline body in this file.")

        b_start = target_method.body_location.line - 1
        b_end = target_method.body_location.end_line - 1

        body_text = "{\n\t" + replacement.strip().replace("\n", "\n\t") + "\n}\n"

        # Replace lines from b_start to b_end inclusive
        new_lines = lines[:b_start] + [body_text] + lines[b_end + 1:]
        return "".join(new_lines)

    def _op_replace_source_range(
        self,
        lines: List[str],
        replacement: str,
        target_range: Optional[Dict[str, int]],
    ) -> str:
        """Replaces lines from start_line to end_line (1-indexed inclusive)."""
        if not target_range or "start_line" not in target_range or "end_line" not in target_range:
            raise ValueError("target_range with 'start_line' and 'end_line' is required.")

        s_line = target_range["start_line"]
        e_line = target_range["end_line"]
        if s_line < 1 or e_line > len(lines) or s_line > e_line:
            raise ValueError(f"Invalid target range [{s_line}, {e_line}] for file with {len(lines)} lines.")

        start_idx = s_line - 1
        end_idx = e_line  # slice end

        new_lines = lines[:start_idx] + [replacement] + lines[end_idx:]
        return "".join(new_lines)

    def _op_add_member_property(
        self,
        lines: List[str],
        replacement: str,
        target_class: Optional[str],
        analysis: UnrealCppFileAnalysis,
    ) -> str:
        """Adds a member variable or UPROPERTY to class."""
        target_cls = None
        if target_class:
            target_cls = next((c for c in analysis.classes + analysis.structs if c.name == target_class), None)
        elif analysis.classes:
            target_cls = analysis.classes[0]

        if not target_cls:
            raise ValueError(f"Target class '{target_class}' not found.")

        end_line = target_cls.location.end_line
        insert_idx = end_line - 1
        indented = "\t" + replacement.replace("\n", "\n\t").rstrip("\t") + "\n"
        lines.insert(insert_idx, indented)
        return "".join(lines)

    def _op_add_enum_entry(
        self,
        lines: List[str],
        replacement: str,
        target_enum: Optional[str],
        analysis: UnrealCppFileAnalysis,
    ) -> str:
        """Adds a value entry to an enum."""
        en = None
        if target_enum:
            en = next((e for e in analysis.enums if e.name == target_enum), None)
        elif analysis.enums:
            en = analysis.enums[0]

        if not en:
            raise ValueError(f"Target enum '{target_enum}' not found.")

        insert_idx = en.location.end_line - 1
        entry_text = "\t" + replacement.strip().rstrip(",") + ",\n"
        lines.insert(insert_idx, entry_text)
        return "".join(lines)


# =============================================================================
# 6. Global Default Instances
# =============================================================================

UnrealBlueprintInspector.inspect_blueprint = UnrealBlueprintInspector.inspect_blueprint_metadata

DEFAULT_UNREAL_CPP_ANALYZER = UnrealCppAnalyzer()
DEFAULT_UNREAL_BLUEPRINT_INSPECTOR = UnrealBlueprintInspector()
DEFAULT_UNREAL_BP_INSPECTOR = DEFAULT_UNREAL_BLUEPRINT_INSPECTOR
DEFAULT_UNREAL_SOURCE_MODIFIER = UnrealSourceModifier()
