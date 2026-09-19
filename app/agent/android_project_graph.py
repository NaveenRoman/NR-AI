"""
NR-AI Multi-Module Project Reasoning & Unified Knowledge Graph Engine.

Constructs an authoritative, relational Android Engineering Knowledge Graph
connecting:
  Project -> Module -> BuildScript -> Dependency -> SourceFile -> Symbol -> Resource -> Test -> Runtime.

Enables multi-module reasoning:
  - "Which module caused this failure?"
  - "Which modules are affected by this change?"
  - "What tests should run after changing this file?"
  - "Which dependency introduced this class?"
  - Transitive module dependency paths and cycle detection.
"""

from collections import deque
from dataclasses import dataclass, field, asdict
from enum import Enum
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from app.agent.android_build_graph import (
    AndroidBuildGraph,
    AndroidBuildGraphEngine,
    GradleModuleNode,
    GradleDependencyNode,
)
from app.agent.android_semantic_engine import (
    AndroidSemanticEngine,
    SymbolDefinition,
    SymbolKind,
)
from app.agent.android_resource_graph import (
    AndroidResourceGraph,
    ResourceGraphReport,
)
from app.agent.android_compose_intelligence import (
    AndroidComposeIntelligence,
    ComposeIntelligenceReport,
)

logger = logging.getLogger("NRAI.AndroidProjectGraph")


class NodeType(str, Enum):
    PROJECT = "PROJECT"
    MODULE = "MODULE"
    BUILD_SCRIPT = "BUILD_SCRIPT"
    DEPENDENCY = "DEPENDENCY"
    SOURCE_FILE = "SOURCE_FILE"
    CLASS_SYMBOL = "CLASS_SYMBOL"
    METHOD_SYMBOL = "METHOD_SYMBOL"
    RESOURCE = "RESOURCE"
    TEST_CLASS = "TEST_CLASS"
    TEST_METHOD = "TEST_METHOD"


class EdgeType(str, Enum):
    CONTAINS_MODULE = "CONTAINS_MODULE"
    DEPENDS_ON_MODULE = "DEPENDS_ON_MODULE"
    DECLARES_DEPENDENCY = "DECLARES_DEPENDENCY"
    CONTAINS_FILE = "CONTAINS_FILE"
    DEFINES_SYMBOL = "DEFINES_SYMBOL"
    CALLS_SYMBOL = "CALLS_SYMBOL"
    USES_RESOURCE = "USES_RESOURCE"
    TESTS_SYMBOL = "TESTS_SYMBOL"
    TESTS_FILE = "TESTS_FILE"


@dataclass
class GraphNode:
    id: str
    node_type: NodeType
    label: str
    properties: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "node_type": self.node_type.value,
            "label": self.label,
            "properties": self.properties,
        }


@dataclass
class GraphEdge:
    source_id: str
    target_id: str
    edge_type: EdgeType
    properties: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "edge_type": self.edge_type.value,
            "properties": self.properties,
        }


@dataclass
class AndroidKnowledgeGraph:
    project_name: str
    project_path: str
    nodes: Dict[str, GraphNode] = field(default_factory=dict)
    edges: List[GraphEdge] = field(default_factory=list)
    modules: List[str] = field(default_factory=list)
    module_dependencies: Dict[str, List[str]] = field(default_factory=dict)

    def add_node(self, node: GraphNode) -> None:
        self.nodes[node.id] = node

    def add_edge(self, edge: GraphEdge) -> None:
        self.edges.append(edge)

    def get_node(self, node_id: str) -> Optional[GraphNode]:
        return self.nodes.get(node_id)

    def get_outgoing_edges(self, source_id: str, edge_type: Optional[EdgeType] = None) -> List[GraphEdge]:
        return [
            e for e in self.edges
            if e.source_id == source_id and (edge_type is None or e.edge_type == edge_type)
        ]

    def get_incoming_edges(self, target_id: str, edge_type: Optional[EdgeType] = None) -> List[GraphEdge]:
        return [
            e for e in self.edges
            if e.target_id == target_id and (edge_type is None or e.edge_type == edge_type)
        ]

    def get_summary(self) -> Dict[str, Any]:
        types: Dict[str, int] = {}
        for n in self.nodes.values():
            types[n.node_type.value] = types.get(n.node_type.value, 0) + 1
        return {
            "project_name": self.project_name,
            "project_path": self.project_path,
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "modules": self.modules,
            "node_types": types,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_name": self.project_name,
            "project_path": self.project_path,
            "total_nodes": len(self.nodes),
            "total_edges": len(self.edges),
            "modules": self.modules,
            "module_dependencies": self.module_dependencies,
            "nodes": {nid: n.to_dict() for nid, n in self.nodes.items()},
            "edges": [e.to_dict() for e in self.edges],
        }


class AndroidProjectGraphEngine:
    """Constructs and queries the multi-module Android Engineering Knowledge Graph."""

    def __init__(self):
        self.build_graph_engine = AndroidBuildGraphEngine()
        self.semantic_engine = AndroidSemanticEngine()
        self.resource_graph_engine = AndroidResourceGraph()
        self.compose_engine = AndroidComposeIntelligence()

    def build_knowledge_graph(self, project_root: Union[str, Path]) -> AndroidKnowledgeGraph:
        root = Path(project_root).resolve()
        if not root.exists():
            raise FileNotFoundError(f"Project directory '{root}' does not exist.")

        kg = AndroidKnowledgeGraph(
            project_name=root.name,
            project_path=str(root),
        )

        # 1. Project Root Node
        proj_node_id = f"project:{root.name}"
        kg.add_node(GraphNode(
            id=proj_node_id,
            node_type=NodeType.PROJECT,
            label=root.name,
            properties={"path": str(root)},
        ))

        # 2. Build Graph (Modules, Dependencies)
        build_graph = self.build_graph_engine.build_graph(root)
        kg.modules = list(build_graph.modules.keys())

        # Module nodes and Module dependencies
        for mod_name, mod_node in build_graph.modules.items():
            mod_id = f"module:{mod_name}"
            kg.add_node(GraphNode(
                id=mod_id,
                node_type=NodeType.MODULE,
                label=mod_name,
                properties={
                    "path": mod_node.path,
                    "is_library": mod_node.is_library,
                    "plugins": mod_node.plugins,
                    "source_sets": mod_node.source_sets,
                },
            ))
            kg.add_edge(GraphEdge(
                source_id=proj_node_id,
                target_id=mod_id,
                edge_type=EdgeType.CONTAINS_MODULE,
            ))

            # Build script node
            build_file = Path(mod_node.path) / "build.gradle.kts"
            if not build_file.exists():
                build_file = Path(mod_node.path) / "build.gradle"
            if build_file.exists():
                script_id = f"build_script:{mod_name}"
                kg.add_node(GraphNode(
                    id=script_id,
                    node_type=NodeType.BUILD_SCRIPT,
                    label=build_file.name,
                    properties={"path": str(build_file)},
                ))
                kg.add_edge(GraphEdge(
                    source_id=mod_id,
                    target_id=script_id,
                    edge_type=EdgeType.CONTAINS_FILE,
                ))

            # Module dependencies
            mod_deps: List[str] = []
            for dep in mod_node.dependencies:
                dep_id = f"dependency:{dep.coordinate}"
                if not kg.get_node(dep_id):
                    kg.add_node(GraphNode(
                        id=dep_id,
                        node_type=NodeType.DEPENDENCY,
                        label=dep.coordinate,
                        properties={
                            "group": dep.group,
                            "name": dep.name,
                            "version": dep.version,
                            "scope": dep.scope.value,
                        },
                    ))
                kg.add_edge(GraphEdge(
                    source_id=mod_id,
                    target_id=dep_id,
                    edge_type=EdgeType.DECLARES_DEPENDENCY,
                    properties={"scope": dep.scope.value},
                ))

                # Check if it's a project module dependency (e.g. project(":core"))
                if dep.group == "project" or dep.coordinate.startswith("project:"):
                    target_mod = dep.name.lstrip(":")
                    target_mod_id = f"module:{target_mod}"
                    kg.add_edge(GraphEdge(
                        source_id=mod_id,
                        target_id=target_mod_id,
                        edge_type=EdgeType.DEPENDS_ON_MODULE,
                    ))
                    mod_deps.append(target_mod)

            kg.module_dependencies[mod_name] = mod_deps

        # 3. Source Files and Semantic Symbols
        src_files = list(root.glob("**/src/**/*.kt")) + list(root.glob("**/src/**/*.java"))
        for src_path in src_files:
            if "build" in src_path.parts:
                continue

            # Determine module
            mod_name = self._find_enclosing_module(src_path, root, build_graph)
            mod_id = f"module:{mod_name}"
            is_test = "test" in src_path.parts or "androidTest" in src_path.parts

            file_id = f"file:{src_path.relative_to(root).as_posix()}"
            kg.add_node(GraphNode(
                id=file_id,
                node_type=NodeType.TEST_CLASS if is_test else NodeType.SOURCE_FILE,
                label=src_path.name,
                properties={
                    "path": str(src_path),
                    "module": mod_name,
                    "is_test": is_test,
                },
            ))
            kg.add_edge(GraphEdge(
                source_id=mod_id,
                target_id=file_id,
                edge_type=EdgeType.CONTAINS_FILE,
            ))

            # Semantic analysis
            facts = self.semantic_engine.analyze_file(src_path)
            for sym in self.semantic_engine.symbols.values():
                if Path(sym.file_path).resolve() == src_path.resolve():
                    sym_id = f"symbol:{sym.qualified_name}"
                    node_type = (
                        NodeType.TEST_METHOD if (is_test and sym.kind == SymbolKind.METHOD)
                        else NodeType.CLASS_SYMBOL if sym.kind in (SymbolKind.CLASS, SymbolKind.INTERFACE, SymbolKind.OBJECT)
                        else NodeType.METHOD_SYMBOL
                    )
                    kg.add_node(GraphNode(
                        id=sym_id,
                        node_type=node_type,
                        label=sym.name,
                        properties={
                            "qualified_name": sym.qualified_name,
                            "kind": sym.kind.value,
                            "file_path": sym.file_path,
                            "line": sym.line,
                            "is_test": is_test,
                        },
                    ))
                    kg.add_edge(GraphEdge(
                        source_id=file_id,
                        target_id=sym_id,
                        edge_type=EdgeType.DEFINES_SYMBOL,
                    ))

                    # Test targeting links
                    if is_test:
                        tested_class_name = re.sub(r"Test(s)?$", "", sym.name)
                        if tested_class_name and tested_class_name != sym.name:
                            target_sym_matches = [
                                n for n in kg.nodes.values()
                                if n.node_type == NodeType.CLASS_SYMBOL and n.label == tested_class_name
                            ]
                            for t_node in target_sym_matches:
                                kg.add_edge(GraphEdge(
                                    source_id=sym_id,
                                    target_id=t_node.id,
                                    edge_type=EdgeType.TESTS_SYMBOL,
                                ))
                                kg.add_edge(GraphEdge(
                                    source_id=file_id,
                                    target_id=t_node.properties.get("file_path", ""),
                                    edge_type=EdgeType.TESTS_FILE,
                                ))

        # 4. Resources
        try:
            res_report = self.resource_graph_engine.build_graph(root)
            for res_key, defs in res_report.definitions.items():
                for d in defs:
                    res_id = f"resource:{d.res_type}/{d.name}"
                    if not kg.get_node(res_id):
                        kg.add_node(GraphNode(
                            id=res_id,
                            node_type=NodeType.RESOURCE,
                            label=f"{d.res_type}/{d.name}",
                            properties={
                                "res_type": d.res_type,
                                "name": d.name,
                                "file_path": d.file_path,
                                "line": d.line,
                                "config": d.config,
                            },
                        ))
            # Cross-reference resource usage from code
            for ref_key, refs in res_report.references.items():
                for r in refs:
                    res_id = f"resource:{r.res_type}/{r.name}"
                    file_id = f"file:{Path(r.source_file).relative_to(root).as_posix()}"
                    if kg.get_node(res_id) and kg.get_node(file_id):
                        kg.add_edge(GraphEdge(
                            source_id=file_id,
                            target_id=res_id,
                            edge_type=EdgeType.USES_RESOURCE,
                        ))
        except Exception as e:
            logger.warning("Resource graph indexing error during KG build: %s", e)

        return kg

    def _find_enclosing_module(
        self,
        file_path: Path,
        project_root: Path,
        build_graph: AndroidBuildGraph,
    ) -> str:
        for mod_name, mod_node in build_graph.modules.items():
            mod_path = Path(mod_node.path).resolve()
            if mod_path in file_path.resolve().parents:
                return mod_name
        return ":app" if ":app" in build_graph.modules else "root"

    # -------------------------------------------------------------------------
    # Multi-Module Reasoning Queries
    # -------------------------------------------------------------------------

    def which_module_caused_failure(
        self,
        kg: AndroidKnowledgeGraph,
        stack_trace_or_error: str,
    ) -> Optional[str]:
        """
        Extracts package/class/file identifiers from a stack trace or compilation error
        and determines which module owns that code.
        """
        if not stack_trace_or_error:
            return None

        # 1. Look for file paths in error (e.g. C:\NR-AI\nr_android_test\app\src\...)
        for node in kg.nodes.values():
            if node.node_type in (NodeType.SOURCE_FILE, NodeType.TEST_CLASS):
                file_path = node.properties.get("path", "")
                if file_path and (file_path in stack_trace_or_error or Path(file_path).name in stack_trace_or_error):
                    return node.properties.get("module")

        # 2. Look for stack trace lines: at com.nrai.test.MainActivity.onCreate(MainActivity.kt:35)
        pattern = r"at\s+([a-zA-Z0-9_$.]+)\.([a-zA-Z0-9_$]+)\(([^:]+):(\d+)\)"
        for match in re.finditer(pattern, stack_trace_or_error):
            class_fqcn, method_name, file_name, line_str = match.groups()
            for node in kg.nodes.values():
                if node.node_type in (NodeType.SOURCE_FILE, NodeType.TEST_CLASS):
                    if Path(node.properties.get("path", "")).name == file_name:
                        return node.properties.get("module")
                elif node.node_type == NodeType.CLASS_SYMBOL:
                    if node.properties.get("qualified_name") == class_fqcn or node.label in class_fqcn:
                        fpath = node.properties.get("file_path", "")
                        fnode = kg.get_node(f"file:{Path(fpath).relative_to(Path(kg.project_path)).as_posix()}")
                        if fnode:
                            return fnode.properties.get("module")

        # 3. Check for direct module mentions e.g. ':core:compileDebugKotlin'
        mod_task_match = re.search(r":([a-zA-Z0-9_\-:]+):[a-zA-Z0-9]+", stack_trace_or_error)
        if mod_task_match:
            mod_candidate = ":" + mod_task_match.group(1).split(":")[0]
            if mod_candidate in kg.modules:
                return mod_candidate

        return None

    def affected_modules(
        self,
        kg: AndroidKnowledgeGraph,
        changed_file: Union[str, Path],
    ) -> List[str]:
        """
        Finds the module containing changed_file, plus any downstream modules that
        transitively depend on it.
        """
        changed_path = Path(changed_file).resolve()
        origin_module: Optional[str] = None

        # Find origin module
        for node in kg.nodes.values():
            if node.node_type in (NodeType.SOURCE_FILE, NodeType.TEST_CLASS, NodeType.BUILD_SCRIPT):
                p = Path(node.properties.get("path", "")).resolve()
                if p == changed_path or p.name == changed_path.name:
                    origin_module = node.properties.get("module")
                    break

        if not origin_module:
            # Fallback based on path containment
            for mod_name in kg.modules:
                mod_node = kg.get_node(f"module:{mod_name}")
                if mod_node:
                    mpath = Path(mod_node.properties.get("path", "")).resolve()
                    if mpath in changed_path.parents:
                        origin_module = mod_name
                        break

        if not origin_module:
            return []

        # Find all modules that depend on origin_module (downstream consumers)
        affected = {origin_module}
        queue = deque([origin_module])

        # Inverted dependency map: dependency -> list of dependent modules
        dependents_map: Dict[str, List[str]] = {m: [] for m in kg.modules}
        for mod, deps in kg.module_dependencies.items():
            for d in deps:
                if d in dependents_map:
                    dependents_map[d].append(mod)

        while queue:
            curr = queue.popleft()
            for consumer in dependents_map.get(curr, []):
                if consumer not in affected:
                    affected.add(consumer)
                    queue.append(consumer)

        return sorted(list(affected))

    def tests_to_run_for_changes(
        self,
        kg: AndroidKnowledgeGraph,
        changed_files: List[Union[str, Path]],
    ) -> List[str]:
        """
        Finds targeted unit/instrumentation tests that must run for the changed files:
        1. Tests directly targeting symbols or files modified.
        2. Tests in the affected modules matching the changed file names.
        """
        targeted_tests: Set[str] = set()
        affected_mods = set()

        for cfile in changed_files:
            cpath = Path(cfile).resolve()
            mods = self.affected_modules(kg, cfile)
            affected_mods.update(mods)

            stem = cpath.stem
            # Direct name matching: Foo.kt -> FooTest.kt, TestFoo.kt
            for node in kg.nodes.values():
                if node.node_type == NodeType.TEST_CLASS:
                    tpath = Path(node.properties.get("path", ""))
                    if tpath.name == f"{stem}Test.kt" or tpath.name == f"{stem}Test.java" or tpath.stem.startswith(stem):
                        targeted_tests.add(str(tpath))

            # Edge based matching: TESTS_FILE or TESTS_SYMBOL
            for edge in kg.edges:
                if edge.edge_type in (EdgeType.TESTS_FILE, EdgeType.TESTS_SYMBOL):
                    if str(cpath) in edge.target_id or stem in edge.target_id:
                        src_node = kg.get_node(edge.source_id)
                        if src_node and "path" in src_node.properties:
                            targeted_tests.add(src_node.properties["path"])

        # If no direct test matched, return all tests from the directly affected modules
        if not targeted_tests:
            for node in kg.nodes.values():
                if node.node_type == NodeType.TEST_CLASS:
                    if node.properties.get("module") in affected_mods:
                        targeted_tests.add(node.properties.get("path", ""))

        return sorted(list(targeted_tests))

    def which_dependency_introduced_class(
        self,
        kg: AndroidKnowledgeGraph,
        class_name_or_fqcn: str,
    ) -> List[str]:
        """
        Maps a class name or package to the declared Gradle dependency that provides it.
        Uses known AndroidX / Kotlin / standard mappings and declared dependencies.
        """
        matched_deps: List[str] = []

        # Standard known package-to-group/name heuristics
        heuristics = [
            (r"^androidx\.compose\.material3\.", "androidx.compose.material3:material3"),
            (r"^androidx\.compose\.ui\.", "androidx.compose.ui:ui"),
            (r"^androidx\.compose\.foundation\.", "androidx.compose.foundation:foundation"),
            (r"^androidx\.lifecycle\.", "androidx.lifecycle:lifecycle-runtime-ktx"),
            (r"^androidx\.navigation\.", "androidx.navigation:navigation-compose"),
            (r"^kotlinx\.coroutines\.", "org.jetbrains.kotlinx:kotlinx-coroutines-android"),
            (r"^retrofit2\.", "com.squareup.retrofit2:retrofit"),
            (r"^okhttp3\.", "com.squareup.okhttp3:okhttp"),
            (r"^dagger\.hilt\.", "com.google.dagger:hilt-android"),
            (r"^org\.junit\.", "junit:junit"),
            (r"^androidx\.test\.", "androidx.test.ext:junit"),
        ]

        for pat, dep_coord in heuristics:
            if re.search(pat, class_name_or_fqcn):
                matched_deps.append(dep_coord)

        # Match against declared dependencies in KG
        simple_name = class_name_or_fqcn.split(".")[-1].lower()
        for node in kg.nodes.values():
            if node.node_type == NodeType.DEPENDENCY:
                label_lower = node.label.lower()
                if simple_name in label_lower or any(part in label_lower for part in class_name_or_fqcn.lower().split(".")):
                    if node.label not in matched_deps:
                        matched_deps.append(node.label)

        return matched_deps

    def find_module_dependency_path(
        self,
        kg: AndroidKnowledgeGraph,
        from_module: str,
        to_module: str,
    ) -> Optional[List[str]]:
        """Finds the shortest dependency path between two modules using BFS."""
        if from_module == to_module:
            return [from_module]

        queue: deque[List[str]] = deque([[from_module]])
        visited: Set[str] = {from_module}

        while queue:
            path = queue.popleft()
            curr = path[-1]

            for neighbor in kg.module_dependencies.get(curr, []):
                if neighbor == to_module:
                    return path + [neighbor]
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(path + [neighbor])

        return None

    def detect_module_cycles(self, kg: AndroidKnowledgeGraph) -> List[List[str]]:
        """Detects any circular dependency chains between modules."""
        cycles: List[List[str]] = []
        visited: Set[str] = set()
        rec_stack: Set[str] = set()
        current_path: List[str] = []

        def dfs(u: str):
            visited.add(u)
            rec_stack.add(u)
            current_path.append(u)

            for neighbor in kg.module_dependencies.get(u, []):
                if neighbor not in visited:
                    dfs(neighbor)
                elif neighbor in rec_stack:
                    # Cycle found
                    cycle_start = current_path.index(neighbor)
                    cycles.append(current_path[cycle_start:] + [neighbor])

            current_path.pop()
            rec_stack.remove(u)

        for mod in kg.modules:
            if mod not in visited:
                dfs(mod)

        return cycles
