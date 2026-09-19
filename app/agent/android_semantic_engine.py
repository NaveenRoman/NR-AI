"""
NR-AI Kotlin & Java Semantic Intelligence Engine.

Performs structural AST extraction and semantic inference across Kotlin and Java source
codebases without claiming full compiler semantics. Builds a bounded symbol and reference
graph separating structural facts from semantic inferences.
"""

from dataclasses import dataclass, field, asdict
from enum import Enum
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

logger = logging.getLogger("NRAI.AndroidSemanticEngine")


class FactCategory(str, Enum):
    STRUCTURAL_FACT = "STRUCTURAL_FACT"
    SEMANTIC_INFERENCE = "SEMANTIC_INFERENCE"


class SymbolKind(str, Enum):
    CLASS = "CLASS"
    INTERFACE = "INTERFACE"
    OBJECT = "OBJECT"
    METHOD = "METHOD"
    CONSTRUCTOR = "CONSTRUCTOR"
    PROPERTY = "PROPERTY"
    PARAMETER = "PARAMETER"
    ANNOTATION = "ANNOTATION"


class LanguageKind(str, Enum):
    KOTLIN = "KOTLIN"
    JAVA = "JAVA"


ANDROID_LIFECYCLE_METHODS = {
    "onCreate",
    "onStart",
    "onResume",
    "onPause",
    "onStop",
    "onDestroy",
    "onRestart",
    "onSaveInstanceState",
    "onRestoreInstanceState",
    "onAttachedToWindow",
    "onDetachedFromWindow",
    "onCreateView",
    "onViewCreated",
    "onDestroyView",
    "onActivityResult",
    "onRequestPermissionsResult",
}

COROUTINE_MARKERS = {
    "suspend",
    "CoroutineScope",
    "viewModelScope",
    "lifecycleScope",
    "launch",
    "async",
    "withContext",
    "Flow",
    "StateFlow",
    "SharedFlow",
    "delay",
}


@dataclass
class SemanticFact:
    """A discrete verifiable semantic fact or inference."""
    category: FactCategory
    subject: str
    predicate: str
    object_val: Any
    source_file: str
    line: int
    confidence: float = 1.0  # 1.0 for STRUCTURAL_FACT, 0.8-0.95 for SEMANTIC_INFERENCE

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["category"] = self.category.value
        return d


@dataclass
class SymbolDefinition:
    """Structured descriptor of a code symbol in the project index."""
    name: str
    qualified_name: str
    kind: SymbolKind
    language: LanguageKind
    file_path: str
    line_number: int
    super_types: List[str] = field(default_factory=list)
    annotations: List[str] = field(default_factory=list)
    properties: List[str] = field(default_factory=list)
    methods: List[str] = field(default_factory=list)
    constructors: List[str] = field(default_factory=list)
    is_suspend: bool = False
    coroutine_markers: List[str] = field(default_factory=list)
    lifecycle_methods: List[str] = field(default_factory=list)
    outgoing_references: List[str] = field(default_factory=list)
    incoming_references: List[str] = field(default_factory=list)

    @property
    def line(self) -> int:
        return self.line_number

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["line"] = self.line
        d["kind"] = self.kind.value
        d["language"] = self.language.value
        return d


class AndroidSemanticEngine:
    """
    Parses and indexes Kotlin and Java sources into structural facts and semantic inferences.
    Constructs an index of symbols and cross-symbol references.
    """

    def __init__(self):
        self.symbols: Dict[str, SymbolDefinition] = {}
        self.facts: List[SemanticFact] = []
        self._reference_graph: Dict[str, Set[str]] = {}  # symbol -> referenced symbols

    def analyze_file(self, file_path: Union[str, Path]) -> List[SemanticFact]:
        """Analyzes a single Kotlin or Java source file and records facts and symbols."""
        path = Path(file_path).resolve()
        if not path.exists() or not path.is_file():
            return []

        lang = LanguageKind.KOTLIN if path.suffix == ".kt" else (LanguageKind.JAVA if path.suffix == ".java" else None)
        if not lang:
            return []

        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            logger.debug(f"Failed reading file {path}: {e}")
            return []

        file_facts: List[SemanticFact] = []
        if lang == LanguageKind.KOTLIN:
            self._analyze_kotlin_source(str(path), content, file_facts)
        else:
            self._analyze_java_source(str(path), content, file_facts)

        self.facts.extend(file_facts)
        return file_facts

    def analyze_project(self, project_path: Union[str, Path]) -> Dict[str, Any]:
        """Scans all Kotlin and Java sources in project src dirs and builds the index."""
        p_path = Path(project_path).resolve()
        source_files = list(p_path.rglob("*.kt")) + list(p_path.rglob("*.java"))

        # Exclude build directory
        filtered_files = [f for f in source_files if "build" not in f.parts and ".gradle" not in f.parts]

        for sf in filtered_files:
            self.analyze_file(sf)

        self._resolve_cross_references()

        return {
            "total_files_analyzed": len(filtered_files),
            "total_symbols_indexed": len(self.symbols),
            "total_facts_recorded": len(self.facts),
            "structural_facts_count": sum(1 for f in self.facts if f.category == FactCategory.STRUCTURAL_FACT),
            "semantic_inferences_count": sum(1 for f in self.facts if f.category == FactCategory.SEMANTIC_INFERENCE),
        }

    # -------------------------------------------------------------------------
    # Kotlin Analyzer
    # -------------------------------------------------------------------------

    def _analyze_kotlin_source(self, file_str: str, content: str, facts: List[SemanticFact]) -> None:
        lines = content.splitlines()
        pkg_name = ""

        # Find package
        for i, line in enumerate(lines, 1):
            m_pkg = re.match(r"^\s*package\s+([a-zA-Z0-9_\.]+)", line)
            if m_pkg:
                pkg_name = m_pkg.group(1)
                facts.append(SemanticFact(
                    category=FactCategory.STRUCTURAL_FACT,
                    subject=file_str,
                    predicate="declares_package",
                    object_val=pkg_name,
                    source_file=file_str,
                    line=i,
                ))
                break

        class_stack: List[Tuple[SymbolDefinition, int]] = []  # (symbol, brace_depth)
        current_brace_depth = 0
        pending_annotations: List[Tuple[str, int]] = []

        for i, line in enumerate(lines, 1):
            trimmed = line.strip()

            # Track brace depth for scope popping
            opens = trimmed.count("{")
            closes = trimmed.count("}")

            # Annotations: @Composable, @Override, etc.
            annot_matches = re.findall(r"@([a-zA-Z0-9_]+)", trimmed)
            if annot_matches and not (trimmed.startswith("//") or trimmed.startswith("/*")):
                for ann in annot_matches:
                    pending_annotations.append((ann, i))
                    scope_name = class_stack[-1][0].name if class_stack else file_str
                    facts.append(SemanticFact(
                        category=FactCategory.STRUCTURAL_FACT,
                        subject=scope_name,
                        predicate="annotated_with",
                        object_val=ann,
                        source_file=file_str,
                        line=i,
                    ))

            # Class / Interface / Object declarations
            m_class = re.search(
                r"\b(class|interface|object|enum\s+class|data\s+class|sealed\s+class)\s+([a-zA-Z0-9_]+)(?:<[^>]+>)?(?:\s*\([^)]*\))?(?:\s*:\s*([^{]+))?",
                trimmed
            )
            if m_class and not trimmed.startswith("//"):
                decl_type = m_class.group(1)
                name = m_class.group(2)
                raw_supers = m_class.group(3) or ""

                kind = SymbolKind.INTERFACE if "interface" in decl_type else (
                    SymbolKind.OBJECT if decl_type == "object" else SymbolKind.CLASS
                )
                qname = f"{pkg_name}.{name}" if pkg_name else name

                supers = []
                for s in raw_supers.split(","):
                    s_clean = re.sub(r"\(.*?\)", "", s).strip()
                    if s_clean:
                        supers.append(s_clean)

                annotations_for_class = [a[0] for a in pending_annotations]
                pending_annotations.clear()

                sym = SymbolDefinition(
                    name=name,
                    qualified_name=qname,
                    kind=kind,
                    language=LanguageKind.KOTLIN,
                    file_path=file_str,
                    line_number=i,
                    super_types=supers,
                    annotations=annotations_for_class,
                )
                self.symbols[qname] = sym
                self.symbols[name] = sym

                # Push to class stack at current brace depth
                class_stack.append((sym, current_brace_depth + (1 if "{" in trimmed else 0)))

                facts.append(SemanticFact(
                    category=FactCategory.STRUCTURAL_FACT,
                    subject=qname,
                    predicate="declares_symbol",
                    object_val=decl_type,
                    source_file=file_str,
                    line=i,
                ))
                for s in supers:
                    facts.append(SemanticFact(
                        category=FactCategory.STRUCTURAL_FACT,
                        subject=qname,
                        predicate="inherits_from",
                        object_val=s,
                        source_file=file_str,
                        line=i,
                    ))

            current_class = class_stack[-1][0] if class_stack else None

            # Method declarations: fun foo(...) or suspend fun foo(...)
            m_fun = re.search(r"\b(suspend\s+)?fun\s+([a-zA-Z0-9_]+)\s*\(([^)]*)\)(?:\s*:\s*([^{=\n]+))?", trimmed)
            if m_fun and not trimmed.startswith("//"):
                is_suspend = bool(m_fun.group(1))
                fun_name = m_fun.group(2)
                params_raw = m_fun.group(3)
                ret_type = (m_fun.group(4) or "Unit").strip()

                scope_sym = current_class.name if current_class else (pkg_name or Path(file_str).stem)
                fun_qname = f"{scope_sym}.{fun_name}"

                pending_annotations.clear()

                if current_class:
                    if fun_name not in current_class.methods:
                        current_class.methods.append(fun_name)
                    if is_suspend:
                        current_class.is_suspend = True
                    if fun_name in ANDROID_LIFECYCLE_METHODS and fun_name not in current_class.lifecycle_methods:
                        current_class.lifecycle_methods.append(fun_name)

                facts.append(SemanticFact(
                    category=FactCategory.STRUCTURAL_FACT,
                    subject=fun_qname,
                    predicate="declares_method",
                    object_val={"params": params_raw, "return": ret_type, "suspend": is_suspend},
                    source_file=file_str,
                    line=i,
                ))

                if is_suspend:
                    facts.append(SemanticFact(
                        category=FactCategory.STRUCTURAL_FACT,
                        subject=fun_qname,
                        predicate="is_suspend",
                        object_val=True,
                        source_file=file_str,
                        line=i,
                    ))

                if fun_name in ANDROID_LIFECYCLE_METHODS:
                    facts.append(SemanticFact(
                        category=FactCategory.SEMANTIC_INFERENCE,
                        subject=fun_qname,
                        predicate="lifecycle_method",
                        object_val=fun_name,
                        source_file=file_str,
                        line=i,
                        confidence=0.98,
                    ))

            # Adjust brace depth and pop ended classes
            current_brace_depth += opens - closes
            while class_stack and current_brace_depth < class_stack[-1][1]:
                class_stack.pop()

            # Property declarations: val x: Type = ... or var y: Type?
            m_prop = re.search(r"\b(val|var)\s+([a-zA-Z0-9_]+)(?:\s*:\s*([a-zA-Z0-9_<>, ?]+))?", trimmed)
            if m_prop and not (trimmed.startswith("//") or "fun " in trimmed):
                p_mut = m_prop.group(1)
                p_name = m_prop.group(2)
                p_type = (m_prop.group(3) or "").strip()
                is_nullable = "?" in p_type if p_type else False

                if current_class:
                    current_class.properties.append(p_name)

                facts.append(SemanticFact(
                    category=FactCategory.STRUCTURAL_FACT,
                    subject=f"{current_class.name if current_class else Path(file_str).stem}.{p_name}",
                    predicate="declares_property",
                    object_val={"mutable": (p_mut == "var"), "type": p_type, "nullable": is_nullable},
                    source_file=file_str,
                    line=i,
                ))

            # Coroutine Markers in Body
            for marker in COROUTINE_MARKERS:
                if re.search(rf"\b{marker}\b", trimmed):
                    if current_class and marker not in current_class.coroutine_markers:
                        current_class.coroutine_markers.append(marker)
                    facts.append(SemanticFact(
                        category=FactCategory.SEMANTIC_INFERENCE,
                        subject=current_class.name if current_class else Path(file_str).stem,
                        predicate="coroutine_marker",
                        object_val=marker,
                        source_file=file_str,
                        line=i,
                        confidence=0.90,
                    ))

            # Semantic Reference Inferences (instantiations, method calls, ViewModel references)
            for ref_match in re.finditer(r"\b([A-Z][a-zA-Z0-9_]+)\b", trimmed):
                ref_sym = ref_match.group(1)
                if ref_sym not in ("String", "Int", "Boolean", "Unit", "Float", "Double", "Long", "Any", "List", "Map", "Set"):
                    if current_class and ref_sym != current_class.name:
                        if ref_sym not in current_class.outgoing_references:
                            current_class.outgoing_references.append(ref_sym)
                        facts.append(SemanticFact(
                            category=FactCategory.SEMANTIC_INFERENCE,
                            subject=current_class.name,
                            predicate="references_symbol",
                            object_val=ref_sym,
                            source_file=file_str,
                            line=i,
                            confidence=0.85,
                        ))

    # -------------------------------------------------------------------------
    # Java Analyzer
    # -------------------------------------------------------------------------

    def _analyze_java_source(self, file_str: str, content: str, facts: List[SemanticFact]) -> None:
        lines = content.splitlines()
        pkg_name = ""

        for i, line in enumerate(lines, 1):
            m_pkg = re.match(r"^\s*package\s+([a-zA-Z0-9_\.]+);", line)
            if m_pkg:
                pkg_name = m_pkg.group(1)
                facts.append(SemanticFact(
                    category=FactCategory.STRUCTURAL_FACT,
                    subject=file_str,
                    predicate="declares_package",
                    object_val=pkg_name,
                    source_file=file_str,
                    line=i,
                ))
                break

        current_class: Optional[SymbolDefinition] = None
        for i, line in enumerate(lines, 1):
            trimmed = line.strip()

            # Class / Interface
            m_class = re.search(
                r"\bpublic\s+(class|interface|enum)\s+([a-zA-Z0-9_]+)(?:\s+extends\s+([a-zA-Z0-9_\.]+))?(?:\s+implements\s+([^{]+))?",
                trimmed
            )
            if m_class and not trimmed.startswith("//"):
                decl_type = m_class.group(1)
                name = m_class.group(2)
                ext = m_class.group(3)
                impls = m_class.group(4) or ""

                kind = SymbolKind.INTERFACE if decl_type == "interface" else SymbolKind.CLASS
                qname = f"{pkg_name}.{name}" if pkg_name else name

                supers = []
                if ext:
                    supers.append(ext.strip())
                for im in impls.split(","):
                    im_clean = im.strip()
                    if im_clean:
                        supers.append(im_clean)

                sym = SymbolDefinition(
                    name=name,
                    qualified_name=qname,
                    kind=kind,
                    language=LanguageKind.JAVA,
                    file_path=file_str,
                    line_number=i,
                    super_types=supers,
                )
                self.symbols[qname] = sym
                self.symbols[name] = sym
                current_class = sym

                facts.append(SemanticFact(
                    category=FactCategory.STRUCTURAL_FACT,
                    subject=qname,
                    predicate="declares_symbol",
                    object_val=decl_type,
                    source_file=file_str,
                    line=i,
                ))

            # Methods
            m_method = re.search(r"(?:public|protected|private)\s+(?:static\s+)?([a-zA-Z0-9_<>]+)\s+([a-zA-Z0-9_]+)\s*\(([^)]*)\)", trimmed)
            if m_method and not trimmed.startswith("//"):
                ret_t = m_method.group(1)
                m_name = m_method.group(2)
                if current_class:
                    current_class.methods.append(m_name)
                    if m_name in ANDROID_LIFECYCLE_METHODS:
                        current_class.lifecycle_methods.append(m_name)

                facts.append(SemanticFact(
                    category=FactCategory.STRUCTURAL_FACT,
                    subject=f"{current_class.name if current_class else Path(file_str).stem}.{m_name}",
                    predicate="declares_method",
                    object_val={"return": ret_t},
                    source_file=file_str,
                    line=i,
                ))

    # -------------------------------------------------------------------------
    # Reference Graph & Querying
    # -------------------------------------------------------------------------

    def _resolve_cross_references(self) -> None:
        """Connects incoming references across all indexed symbols."""
        for sym_name, sym in self.symbols.items():
            for out_ref in sym.outgoing_references:
                if out_ref in self.symbols:
                    target = self.symbols[out_ref]
                    if sym.name not in target.incoming_references:
                        target.incoming_references.append(sym.name)

    def get_symbol(self, name: str) -> Optional[SymbolDefinition]:
        """Looks up a symbol definition by simple or qualified name."""
        return self.symbols.get(name)

    def get_call_chain(self, start_symbol: str, depth: int = 5) -> List[List[str]]:
        """Returns bounded reference paths starting from a symbol (e.g. Activity -> ViewModel -> Repo)."""
        chains: List[List[str]] = []

        def _traverse(current: str, path: List[str], current_depth: int):
            if current_depth > depth or current in path:
                return
            new_path = path + [current]
            sym = self.symbols.get(current)
            if not sym or not sym.outgoing_references:
                if len(new_path) > 1:
                    chains.append(new_path)
                return

            valid_outs = [o for o in sym.outgoing_references if o in self.symbols]
            if not valid_outs and len(new_path) > 1:
                chains.append(new_path)
                return

            for nxt in valid_outs:
                _traverse(nxt, new_path, current_depth + 1)

        _traverse(start_symbol, [], 0)
        return chains

    def query_references(self, symbol_name: str) -> Dict[str, List[str]]:
        """Returns incoming and outgoing references for a given symbol."""
        sym = self.symbols.get(symbol_name)
        if not sym:
            return {"outgoing": [], "incoming": []}
        return {
            "outgoing": sym.outgoing_references,
            "incoming": sym.incoming_references,
        }
