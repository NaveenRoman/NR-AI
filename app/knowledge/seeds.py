"""
NR-AI Seed Knowledge Ingestion & Population Service.

Loads curated, verified offline seed files (History, STEM, Computer Science, AI/ML)
and populates the Hybrid Knowledge Store on initialization or on demand.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.knowledge.store import HybridKnowledgeStore
from app.knowledge.taxonomy import KnowledgeNode

logger = logging.getLogger("NRAI.KnowledgeSeeds")


def get_default_seeds_dir(workspace: Optional[str] = None) -> Path:
    """Returns the absolute path to data/knowledge/seeds/."""
    base_dir = Path(workspace or os.getcwd()).resolve()
    return base_dir / "data" / "knowledge" / "seeds"


def load_all_seeds(seeds_dir: Optional[str] = None) -> List[KnowledgeNode]:
    """
    Loads all JSON seed files from the seeds directory and constructs KnowledgeNode objects.
    """
    s_dir = Path(seeds_dir) if seeds_dir else get_default_seeds_dir()
    if not s_dir.exists() or not s_dir.is_dir():
        logger.warning(f"Seeds directory not found at: {s_dir}")
        return []

    nodes: List[KnowledgeNode] = []
    for json_file in sorted(s_dir.glob("*.json")):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        node = KnowledgeNode.from_dict(item)
                        nodes.append(node)
            logger.info(f"Loaded {len(data)} seeds from: {json_file.name}")
        except Exception as e:
            logger.error(f"Failed to load seed file {json_file}: {e}")

    return nodes


def populate_knowledge_store(
    store: HybridKnowledgeStore,
    force: bool = False,
    seeds_dir: Optional[str] = None,
) -> int:
    """
    Populates the store with curated seeds if empty, or when force=True.
    Returns the count of nodes imported or existing.
    """
    existing_count = store.count_nodes()
    if existing_count > 0 and not force:
        logger.info(f"Knowledge store already populated ({existing_count} nodes). Skipping seed import.")
        return existing_count

    nodes = load_all_seeds(seeds_dir=seeds_dir)
    if not nodes:
        logger.warning("No seeds found to import.")
        return existing_count

    imported = store.bulk_import(nodes)
    logger.info(f"Successfully populated knowledge store with {imported} verified seed nodes.")
    return imported


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    store = HybridKnowledgeStore()
    count = populate_knowledge_store(store, force=True)
    print(f"[OK] Universal Knowledge Store Seeded: {count} total nodes.")
    for d in store.list_domains():
        print(f"   * Domain: {d['domain']:<18} Count: {d['count']:<4} Avg Confidence: {d['avg_confidence']}")
