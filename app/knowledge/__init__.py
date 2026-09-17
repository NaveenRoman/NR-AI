"""
NR-AI Universal Knowledge & Continuous Learning Brain Package.
Provides epistemic classification, hybrid local knowledge storage (SQLite FTS5),
autonomous multi-hop web research, and continuous knowledge refresh.
"""

from app.knowledge.taxonomy import (
    EpistemicType,
    EpistemicBadge,
    KnowledgeSource,
    KnowledgeNode,
    ResearchReport,
)
from app.knowledge.store import HybridKnowledgeStore
from app.knowledge.engine import UniversalKnowledgeEngine

__all__ = [
    "EpistemicType",
    "EpistemicBadge",
    "KnowledgeSource",
    "KnowledgeNode",
    "ResearchReport",
    "HybridKnowledgeStore",
    "UniversalKnowledgeEngine",
]
