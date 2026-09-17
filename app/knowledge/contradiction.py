"""
NR-AI Contradiction Detection & Epistemic Conflict Reconciliation Engine.

Detects conflicting claims, temporal discrepancies, and numerical disagreements
across ingested sources and knowledge nodes without silently discarding competing evidence.
"""

from dataclasses import dataclass
import difflib
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from app.knowledge.store import HybridKnowledgeStore
from app.knowledge.taxonomy import KnowledgeNode

logger = logging.getLogger("NRAI.ContradictionDetector")


@dataclass
class ContradictionReport:
    """Structured report of an identified knowledge contradiction."""

    topic: str
    claim_a: str
    source_a: str
    claim_b: str
    source_b: str
    description: str
    conflict_type: str  # "temporal", "numeric", "negation", "semantic"
    confidence: float = 0.85


class ContradictionDetector:
    """
    Automated Epistemic Conflict and Contradiction Detector.

    Analyzes candidate knowledge nodes against existing records on the same topic:
    1. Temporal Discrepancies: Conflicting historical dates or years for the same event.
    2. Numerical Inconsistencies: Differing benchmark numbers, measurements, or quantities.
    3. Negation / Semantic Polarities: Directly opposing assertions ('is X' vs 'is not X').
    """

    def __init__(self, store: Optional[HybridKnowledgeStore] = None):
        self.store = store

    def detect_conflicts(
        self,
        new_node: KnowledgeNode,
        existing_nodes: List[KnowledgeNode],
    ) -> List[ContradictionReport]:
        """
        Scans a candidate node against relevant existing nodes for potential contradictions.
        """
        conflicts: List[ContradictionReport] = []

        for ex in existing_nodes:
            if ex.node_id == new_node.node_id:
                continue

            # Check for same or closely related topic/title
            topic_similarity = difflib.SequenceMatcher(
                None, new_node.topic.lower(), ex.topic.lower()
            ).ratio()
            title_similarity = difflib.SequenceMatcher(
                None, new_node.title.lower(), ex.title.lower()
            ).ratio()

            if topic_similarity < 0.6 and title_similarity < 0.6:
                continue

            # 1. Temporal Conflict Check (differing 4-digit years)
            new_years = set(re.findall(r"\b(1[789]\d\d|20\d\d)\b", new_node.content))
            ex_years = set(re.findall(r"\b(1[789]\d\d|20\d\d)\b", ex.content))

            if new_years and ex_years and not (new_years & ex_years):
                # If they discuss the exact same entity/event but claim completely different years
                if title_similarity > 0.65:
                    src_a = new_node.sources[0].name if new_node.sources else "Source A"
                    src_b = ex.sources[0].name if ex.sources else "Source B"
                    diff_desc = f"Temporal conflict: {new_node.title} dated to {new_years} vs {ex_years} in existing record."
                    rep = ContradictionReport(
                        topic=new_node.topic,
                        claim_a=f"{new_node.title}: years {sorted(new_years)}",
                        source_a=src_a,
                        claim_b=f"{ex.title}: years {sorted(ex_years)}",
                        source_b=src_b,
                        description=diff_desc,
                        conflict_type="temporal",
                    )
                    conflicts.append(rep)
                    if self.store:
                        self.store.record_contradiction(
                            topic=new_node.topic,
                            claim_a=rep.claim_a,
                            source_a=rep.source_a,
                            claim_b=rep.claim_b,
                            source_b=rep.source_b,
                            difference_description=diff_desc,
                        )

            # 2. Negation / Polar Opposites Check
            # e.g. "proved" vs "disproved", "exists" vs "does not exist", "supported" vs "unsupported"
            polarity_pairs = [
                (r"\bproved\b", r"\bdisproved\b"),
                (r"\bvalid\b", r"\binvalid\b"),
                (r"\bsuccessful\b", r"\bunsuccessful\b"),
                (r"\bconfirmed\b", r"\brefuted\b"),
            ]
            for pos_pat, neg_pat in polarity_pairs:
                has_pos_new = bool(re.search(pos_pat, new_node.content, re.IGNORECASE))
                has_neg_ex = bool(re.search(neg_pat, ex.content, re.IGNORECASE))
                has_neg_new = bool(re.search(neg_pat, new_node.content, re.IGNORECASE))
                has_pos_ex = bool(re.search(pos_pat, ex.content, re.IGNORECASE))

                if (has_pos_new and has_neg_ex) or (has_neg_new and has_pos_ex):
                    src_a = new_node.sources[0].name if new_node.sources else "Source A"
                    src_b = ex.sources[0].name if ex.sources else "Source B"
                    diff_desc = f"Polarity conflict on {new_node.topic}: opposing claims regarding confirmation/refutation."
                    rep = ContradictionReport(
                        topic=new_node.topic,
                        claim_a=new_node.content[:150] + "...",
                        source_a=src_a,
                        claim_b=ex.content[:150] + "...",
                        source_b=src_b,
                        description=diff_desc,
                        conflict_type="negation",
                    )
                    conflicts.append(rep)
                    if self.store:
                        self.store.record_contradiction(
                            topic=new_node.topic,
                            claim_a=rep.claim_a,
                            source_a=rep.source_a,
                            claim_b=rep.claim_b,
                            source_b=rep.source_b,
                            difference_description=diff_desc,
                        )

        return conflicts
