"""
NR-AI Android Impact Analysis & Blast Radius Engine.

Calculates the blast radius of proposed source, resource, or build modifications
prior to patch execution using the Android Engineering Knowledge Graph:
  - Modules affected (direct and transitive)
  - Downstream source files and callers
  - XML resources affected
  - Targeted test sets affected
  - Minimum sufficient test set vs full regression requirement
"""

from dataclasses import dataclass, field, asdict
from enum import Enum
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from app.agent.android_project_graph import (
    AndroidKnowledgeGraph,
    AndroidProjectGraphEngine,
    EdgeType,
    NodeType,
)

logger = logging.getLogger("NRAI.AndroidImpact")


class ImpactLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass
class AndroidImpactReport:
    target_files: List[str]
    affected_modules: List[str] = field(default_factory=list)
    affected_source_files: List[str] = field(default_factory=list)
    affected_resources: List[str] = field(default_factory=list)
    affected_tests: List[str] = field(default_factory=list)
    affected_dependencies: List[str] = field(default_factory=list)
    affected_runtime_surfaces: List[str] = field(default_factory=list)
    blast_radius_level: ImpactLevel = ImpactLevel.LOW
    blast_radius_score: float = 0.0
    minimum_sufficient_tests: List[str] = field(default_factory=list)
    requires_full_regression: bool = False
    reasons: List[str] = field(default_factory=list)

    @property
    def direct_dependent_files(self) -> List[str]:
        return self.affected_source_files

    @property
    def minimum_tests_to_run(self) -> List[str]:
        return self.minimum_sufficient_tests

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["blast_radius_level"] = self.blast_radius_level.value
        d["direct_dependent_files"] = self.direct_dependent_files
        d["minimum_tests_to_run"] = self.minimum_tests_to_run
        return d


class AndroidImpactAnalyzer:
    """Calculates blast radius and minimum sufficient test set for Android changes."""

    def __init__(self):
        self.project_graph_engine = AndroidProjectGraphEngine()

    def analyze_impact(
        self,
        changed_files: List[Union[str, Path]],
        kg: AndroidKnowledgeGraph,
    ) -> AndroidImpactReport:
        """Analyzes impact across modules, files, resources, and tests."""
        report = AndroidImpactReport(target_files=[str(f) for f in changed_files])

        affected_mods: Set[str] = set()
        affected_sources: Set[str] = set()
        affected_resources: Set[str] = set()
        affected_tests: Set[str] = set()
        reasons: List[str] = []

        is_build_impact = False

        for f in changed_files:
            p = Path(f)
            fname = p.name

            # Check if build script or catalog changed
            if "gradle" in fname.lower() or fname.endswith(".toml"):
                is_build_impact = True
                reasons.append(f"Build configuration file modified: {fname}")
                affected_mods.update(kg.modules)
                continue

            # 1. Affected modules
            mods = self.project_graph_engine.affected_modules(kg, p)
            affected_mods.update(mods)

            # 2. Find node in KG
            rel_file_id = f"file:{p.name}"
            # Look up matching file nodes
            matching_nodes = [
                n for n in kg.nodes.values()
                if n.properties.get("path") == str(p) or Path(n.properties.get("path", "")).name == fname
            ]

            for node in matching_nodes:
                affected_sources.add(node.properties.get("path", str(p)))

                # Outgoing edges: resources used
                for edge in kg.get_outgoing_edges(node.id, EdgeType.USES_RESOURCE):
                    res_node = kg.get_node(edge.target_id)
                    if res_node:
                        affected_resources.add(res_node.label)

                # Incoming edges: symbols/files that test this file
                for edge in kg.get_incoming_edges(node.id, EdgeType.TESTS_FILE):
                    test_node = kg.get_node(edge.source_id)
                    if test_node and "path" in test_node.properties:
                        affected_tests.add(test_node.properties["path"])

            # 3. Targeted test matching
            tests = self.project_graph_engine.tests_to_run_for_changes(kg, [p])
            affected_tests.update(tests)

        report.affected_modules = sorted(list(affected_mods))
        report.affected_source_files = sorted(list(affected_sources))
        report.affected_resources = sorted(list(affected_resources))
        report.affected_tests = sorted(list(affected_tests))
        report.minimum_sufficient_tests = sorted(list(affected_tests))

        # Calculate blast radius score
        score = 0.0
        score += len(report.affected_source_files) * 3.0
        score += len(report.affected_modules) * 10.0
        score += len(report.affected_resources) * 2.0
        score += len(report.affected_tests) * 1.5

        if is_build_impact:
            score += 40.0
            report.requires_full_regression = True
            reasons.append("Build script/dependency modification requires full project regression.")

        report.blast_radius_score = min(100.0, score)

        if report.blast_radius_score > 70.0:
            report.blast_radius_level = ImpactLevel.CRITICAL
            report.requires_full_regression = True
        elif report.blast_radius_score > 40.0:
            report.blast_radius_level = ImpactLevel.HIGH
        elif report.blast_radius_score > 15.0:
            report.blast_radius_level = ImpactLevel.MEDIUM
        else:
            report.blast_radius_level = ImpactLevel.LOW

        report.reasons = reasons
        return report
