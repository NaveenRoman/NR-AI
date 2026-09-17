"""
NR-AI Hybrid Local Knowledge Store.

Provides high-performance, file-backed SQLite3 FTS5 (Full-Text Search) storage
for the Universal Knowledge Brain. Enables sub-10ms BM25 keyword matching,
domain partitioning, contradiction logging, and automatic TTL pruning.
Zero external server dependencies.
"""

from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Dict, Generator, List, Optional, Set, Tuple

from app.knowledge.taxonomy import (
    EpistemicType,
    KnowledgeDomain,
    KnowledgeNode,
    KnowledgeSource,
)

logger = logging.getLogger("NRAI.KnowledgeStore")


STOPWORDS: Set[str] = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "up", "about", "into", "over", "after",
    "is", "are", "was", "were", "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "can", "could", "should", "would", "will", "shall",
    "what", "who", "whom", "whose", "which", "where", "when", "why", "how",
    "tell", "explain", "describe", "show", "give", "me", "you", "it", "this", "that",
}

GENERIC_QUERY_MODIFIERS: Set[str] = {
    # Temporal spans, eras, decades, and generic time markers
    "1800s", "1810s", "1820s", "1830s", "1840s", "1850s", "1860s", "1870s", "1880s", "1890s",
    "1900s", "1910s", "1920s", "1930s", "1940s", "1950s", "1960s", "1970s", "1980s", "1990s",
    "2000s", "2010s", "2020s", "2030s",
    "century", "centuries", "decade", "decades", "modern", "contemporary", "historical", "history",
    "ancient", "future", "era", "age", "year", "years", "time", "times", "period", "periods",
    # Framing verbs & query nouns
    "evolved", "evolving", "evolution", "differs", "differ", "difference", "differences",
    "different", "basics", "basic", "fundamental", "fundamentals", "overview", "introduction",
    "changes", "major", "impact", "impacts", "technological", "technology", "technologies",
    "development", "developments", "advancement", "advancements", "concept", "concepts",
    "principles", "principle", "system", "systems", "work", "works", "world", "status",
    "compare", "comparison", "contrasting", "versus", "vs", "details", "detailed", "detail",
    "technical", "specs", "specifications",
    # Freshness markers
    "today", "now", "latest", "current", "recently", "recent", "newest", "breaking", "announced",
}

QUESTION_ATTRIBUTE_WORDS: Set[str] = {
    "capital", "creator", "inventor", "author", "founder", "definition", "meaning",
    "version", "speed", "population", "currency", "date", "created", "invented", "founded",
    "developer", "developed", "maker", "made", "father", "origin", "type", "purpose", "architecture",
}


def extract_subject_tokens(raw_query: str) -> List[str]:
    """
    Extracts core subject/entity tokens from a raw query, excluding standard
    stopwords and generic query modifiers (temporal spans, framing verbs).
    Disambiguates subject nouns from question attribute target words (e.g. 'capital', 'creator').
    """
    if not raw_query or not raw_query.strip():
        return []
    cleaned = re.sub(r"[^\w\s\-]", " ", raw_query)
    all_tokens = [t.strip().lower() for t in cleaned.split() if len(t.strip()) > 1]
    content_tokens = [
        t for t in all_tokens
        if t not in STOPWORDS
        and t not in GENERIC_QUERY_MODIFIERS
        and not (t.isdigit() and len(t) == 4)  # filter out 4-digit years from core subject tokens
    ]
    # If specific non-attribute tokens exist (e.g. 'australia' when query has 'capital of australia'),
    # prioritize them as the primary subject constraint
    specific_tokens = [t for t in content_tokens if t not in QUESTION_ATTRIBUTE_WORDS]
    return specific_tokens if specific_tokens else content_tokens



def sanitize_fts5_query(raw_query: str) -> str:
    """
    Sanitizes raw user input for safe SQLite FTS5 syntax.
    Removes stopwords and special characters, formats content tokens for BM25 ranking.
    """
    if not raw_query or not raw_query.strip():
        return ""

    # Remove non-alphanumeric except whitespace
    cleaned = re.sub(r"[^\w\s\-]", " ", raw_query)
    all_tokens = [t.strip() for t in cleaned.split() if len(t.strip()) > 1]
    # Filter out common stopwords
    content_tokens = [t for t in all_tokens if t.lower() not in STOPWORDS]
    tokens = content_tokens if content_tokens else all_tokens
    if not tokens:
        return ""

    # In FTS5, OR allows BM25 to rank documents matching the most content tokens first
    return " OR ".join(f'"{t}"*' for t in tokens)


class HybridKnowledgeStore:
    """
    Persistent SQLite3 FTS5 Knowledge Store for NR-AI.

    Features:
    - WAL-mode SQLite database with automatic schema migrations.
    - Full-text search virtual table (knowledge_fts) with Porter stemming.
    - Synchronized insert/update/delete triggers between physical table and FTS index.
    - Domain and topic indexing with BM25 relevance ranking.
    - Contradiction and epistemic dispute logging.
    - Automatic temporal expiration (TTL) pruning.
    """

    def __init__(self, db_path: Optional[str] = None, workspace: Optional[str] = None):
        if db_path == ":memory:":
            self.db_path = ":memory:"
        elif db_path:
            self.db_path = str(Path(db_path).resolve())
        else:
            base_dir = Path(workspace or os.getcwd()).resolve()
            k_dir = base_dir / "data" / "knowledge"
            k_dir.mkdir(parents=True, exist_ok=True)
            self.db_path = str(k_dir / "universal_knowledge.db")

        self._is_memory = (self.db_path == ":memory:")
        self._memory_conn: Optional[sqlite3.Connection] = None
        if self._is_memory:
            # Persistent connection for in-memory database across calls
            self._memory_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._memory_conn.row_factory = sqlite3.Row

        self.initialize_db()

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Provides a thread-safe connection to the SQLite database."""
        if self._is_memory and self._memory_conn:
            yield self._memory_conn
        else:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
            finally:
                conn.close()

    def initialize_db(self) -> None:
        """Initializes tables, FTS5 virtual tables, indexes, and triggers."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if not self._is_memory:
                cursor.execute("PRAGMA journal_mode=WAL;")
                cursor.execute("PRAGMA synchronous=NORMAL;")
            cursor.execute("PRAGMA foreign_keys=ON;")

            # 1. Primary Knowledge Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_nodes (
                    node_id TEXT PRIMARY KEY,
                    domain TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    epistemic_type TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    sources_json TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    temporal_anchor TEXT,
                    ttl_seconds INTEGER,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    metadata_json TEXT NOT NULL,
                    version INTEGER DEFAULT 1,
                    effective_from REAL,
                    effective_until REAL,
                    supersedes TEXT,
                    superseded_by TEXT
                );
            """)

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_kn_domain ON knowledge_nodes(domain);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_kn_topic ON knowledge_nodes(topic);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_kn_epistemic ON knowledge_nodes(epistemic_type);")

            # Check and run migrations if table already exists without version columns
            cursor.execute("PRAGMA table_info(knowledge_nodes);")
            existing_cols = {row["name"] for row in cursor.fetchall()}
            if "version" not in existing_cols:
                cursor.execute("ALTER TABLE knowledge_nodes ADD COLUMN version INTEGER DEFAULT 1;")
            if "effective_from" not in existing_cols:
                cursor.execute("ALTER TABLE knowledge_nodes ADD COLUMN effective_from REAL;")
            if "effective_until" not in existing_cols:
                cursor.execute("ALTER TABLE knowledge_nodes ADD COLUMN effective_until REAL;")
            if "supersedes" not in existing_cols:
                cursor.execute("ALTER TABLE knowledge_nodes ADD COLUMN supersedes TEXT;")
            if "superseded_by" not in existing_cols:
                cursor.execute("ALTER TABLE knowledge_nodes ADD COLUMN superseded_by TEXT;")

            # 2. SQLite FTS5 Full-Text Search Virtual Table
            cursor.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
                    node_id UNINDEXED,
                    domain,
                    topic,
                    title,
                    content,
                    tags,
                    tokenize='porter unicode61'
                );
            """)

            # 3. Triggers to synchronize FTS with physical table
            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS trg_kn_ai AFTER INSERT ON knowledge_nodes BEGIN
                    INSERT INTO knowledge_fts (node_id, domain, topic, title, content, tags)
                    VALUES (new.node_id, new.domain, new.topic, new.title, new.content, new.tags_json);
                END;
            """)

            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS trg_kn_ad AFTER DELETE ON knowledge_nodes BEGIN
                    DELETE FROM knowledge_fts WHERE node_id = old.node_id;
                END;
            """)

            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS trg_kn_au AFTER UPDATE ON knowledge_nodes BEGIN
                    DELETE FROM knowledge_fts WHERE node_id = old.node_id;
                    INSERT INTO knowledge_fts (node_id, domain, topic, title, content, tags)
                    VALUES (new.node_id, new.domain, new.topic, new.title, new.content, new.tags_json);
                END;
            """)

            # 4. Contradictions Log Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_contradictions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic TEXT NOT NULL,
                    claim_a TEXT NOT NULL,
                    source_a TEXT NOT NULL,
                    claim_b TEXT NOT NULL,
                    source_b TEXT NOT NULL,
                    difference_description TEXT,
                    status TEXT DEFAULT 'OPEN',
                    detected_at REAL NOT NULL
                );
            """)

            conn.commit()
            logger.info(f"Initialized Universal Knowledge Store at: {self.db_path}")

    # =========================================================================
    # NODE CRUD OPERATIONS
    # =========================================================================

    def upsert_node(self, node: KnowledgeNode) -> bool:
        """
        Inserts or updates a KnowledgeNode in the store.
        Triggers automatically keep the FTS5 index in sync.
        """
        sources_json = json.dumps([s.to_dict() for s in node.sources], ensure_ascii=False)
        tags_json = json.dumps(node.tags, ensure_ascii=False)
        metadata_json = json.dumps(node.metadata, ensure_ascii=False)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO knowledge_nodes (
                    node_id, domain, topic, title, content,
                    epistemic_type, confidence, sources_json, tags_json,
                    temporal_anchor, ttl_seconds, created_at, updated_at, metadata_json,
                    version, effective_from, effective_until, supersedes, superseded_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    domain=excluded.domain,
                    topic=excluded.topic,
                    title=excluded.title,
                    content=excluded.content,
                    epistemic_type=excluded.epistemic_type,
                    confidence=excluded.confidence,
                    sources_json=excluded.sources_json,
                    tags_json=excluded.tags_json,
                    temporal_anchor=excluded.temporal_anchor,
                    ttl_seconds=excluded.ttl_seconds,
                    updated_at=excluded.updated_at,
                    metadata_json=excluded.metadata_json,
                    version=excluded.version,
                    effective_from=excluded.effective_from,
                    effective_until=excluded.effective_until,
                    supersedes=excluded.supersedes,
                    superseded_by=excluded.superseded_by;
            """, (
                node.node_id,
                node.domain,
                node.topic,
                node.title,
                node.content,
                node.epistemic_type.value,
                node.confidence,
                sources_json,
                tags_json,
                node.temporal_anchor,
                node.ttl_seconds,
                node.created_at,
                node.updated_at,
                metadata_json,
                node.version,
                node.effective_from,
                node.effective_until,
                node.supersedes,
                node.superseded_by,
            ))
            conn.commit()
            return True

    def supersede_node(self, old_node_id: str, new_node: KnowledgeNode) -> bool:
        """
        Marks an old node as superseded by a new version and indexes the new version.
        Preserves complete version history and provenance.
        """
        old_node = self.get_node(old_node_id)
        now = time.time()
        if old_node:
            old_node.superseded_by = new_node.node_id
            old_node.effective_until = now
            old_node.updated_at = now
            self.upsert_node(old_node)
            new_node.supersedes = old_node_id
            new_node.version = old_node.version + 1

        if not new_node.effective_from:
            new_node.effective_from = now
        return self.upsert_node(new_node)

    def get_node_history(self, node_id: str) -> List[KnowledgeNode]:
        """Traverses the supersedes / superseded_by chain to return full node history."""
        history: List[KnowledgeNode] = []
        current = self.get_node(node_id)
        if not current:
            return []

        # Walk backward to find root
        root = current
        visited: Set[str] = {root.node_id}
        while root.supersedes:
            prev = self.get_node(root.supersedes)
            if not prev or prev.node_id in visited:
                break
            visited.add(prev.node_id)
            root = prev

        # Walk forward from root
        curr: Optional[KnowledgeNode] = root
        visited.clear()
        while curr and curr.node_id not in visited:
            history.append(curr)
            visited.add(curr.node_id)
            if curr.superseded_by:
                curr = self.get_node(curr.superseded_by)
            else:
                break

        return sorted(history, key=lambda x: x.version)

    def get_node(self, node_id: str) -> Optional[KnowledgeNode]:
        """Retrieves a specific node by its unique node_id."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM knowledge_nodes WHERE node_id = ?", (node_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_node(row)

    def delete_node(self, node_id: str) -> bool:
        """Deletes a node and triggers corresponding FTS removal."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM knowledge_nodes WHERE node_id = ?", (node_id,))
            conn.commit()
            return cursor.rowcount > 0

    # =========================================================================
    # HYBRID SEARCH & RETRIEVAL (BM25 + FALLBACK)
    # =========================================================================

    def search_bm25(
        self,
        query: str,
        domain: Optional[str] = None,
        limit: int = 10,
        min_confidence: float = 0.0,
        include_expired: bool = False,
        primary_subject: Optional[str] = None,
    ) -> List[Tuple[KnowledgeNode, float]]:
        """
        Executes an FTS5 full-text search with BM25 ranking.
        Returns a list of tuples: (KnowledgeNode, relevance_score [0.0 to 1.0]).
        """
        sanitized = sanitize_fts5_query(query)
        if not sanitized:
            return self._fallback_search(
                query, domain=domain, limit=limit, min_confidence=min_confidence, primary_subject=primary_subject
            )

        # FTS5 BM25 with column weights:
        # col 0 (node_id): 0.0, col 1 (domain): 2.0, col 2 (topic): 3.0,
        # col 3 (title): 10.0, col 4 (content): 1.0, col 5 (tags): 10.0
        sql = """
            SELECT kn.*, bm25(knowledge_fts, 0.0, 2.0, 3.0, 10.0, 1.0, 10.0) AS rank
            FROM knowledge_fts fts
            JOIN knowledge_nodes kn ON fts.node_id = kn.node_id
            WHERE knowledge_fts MATCH ?
        """
        params: List[Any] = [sanitized]

        if domain:
            if domain in ("science", "stem", "physics", "chemistry", "biology"):
                sql += " AND kn.domain IN ('science', 'stem', 'physics', 'chemistry', 'biology')"
            elif domain in ("computer_science", "programming", "software"):
                sql += " AND kn.domain IN ('computer_science', 'programming', 'software')"
            else:
                sql += " AND kn.domain = ?"
                params.append(domain)

        if min_confidence > 0.0:
            sql += " AND kn.confidence >= ?"
            params.append(min_confidence)

        # Retrieve a wider candidate set to apply topical relevance filtering
        candidate_limit = max(limit * 3, 15)
        sql += " ORDER BY rank ASC LIMIT ?"
        params.append(candidate_limit)

        subject_tokens = extract_subject_tokens(query)
        if primary_subject and primary_subject.strip():
            ps_tokens = [
                t.lower() for t in re.sub(r"[^\w\s\-]", " ", primary_subject).split()
                if len(t) > 1 and t.lower() not in STOPWORDS
            ]
            if ps_tokens:
                subject_tokens = ps_tokens

        results: List[Tuple[KnowledgeNode, float]] = []
        now = time.time()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(sql, params)
                rows = cursor.fetchall()
            except sqlite3.OperationalError as e:
                logger.warning(f"FTS5 query failed ('{sanitized}'): {e}. Falling back to LIKE search.")
                return self._fallback_search(
                    query, domain=domain, limit=limit, min_confidence=min_confidence, primary_subject=primary_subject
                )

            for row in rows:
                node = self._row_to_node(row)
                if not include_expired and node.is_expired(now):
                    continue

                # Topical relevance check:
                # If query contains core subject tokens (e.g. 'australia', 'transformer', 'linux'), candidate
                # document MUST match at least one subject token in title, tags, or content.
                if subject_tokens:
                    doc_title_lower = node.title.lower()
                    doc_tags_lower = [t.lower() for t in node.tags]
                    doc_content_lower = node.content.lower()

                    matches_subject = any(
                        st in doc_title_lower
                        or any(st in tag for tag in doc_tags_lower)
                        or st in doc_content_lower
                        for st in subject_tokens
                    )
                    if not matches_subject:
                        continue

                # In SQLite FTS5, bm25() values are negative; lower (more negative) is better.
                raw_rank = float(row["rank"]) if "rank" in row.keys() else 0.0
                abs_rank = abs(raw_rank)
                score = round(max(0.35, abs_rank / (1.0 + abs_rank)), 3)

                # Boost score when core subject tokens appear prominently in title or tags
                if subject_tokens:
                    doc_title_lower = node.title.lower()
                    doc_tags_lower = [t.lower() for t in node.tags]
                    doc_content_lower = node.content.lower()
                    if any(st in doc_title_lower for st in subject_tokens):
                        score = min(1.0, round(score + 0.35, 3))
                    elif any(any(st in tag for tag in doc_tags_lower) for st in subject_tokens):
                        score = min(1.0, round(score + 0.20, 3))
                    elif any(st in doc_content_lower for st in subject_tokens):
                        score = min(1.0, round(score + 0.05, 3))

                if score < min_confidence:
                    continue

                results.append((node, score))
                if len(results) >= limit:
                    break

        # If FTS returns 0 hits, try fallback LIKE search for fuzzy / partial strings
        if not results:
            return self._fallback_search(
                query, domain=domain, limit=limit, min_confidence=min_confidence, primary_subject=primary_subject
            )

        return results

    def _fallback_search(
        self,
        query: str,
        domain: Optional[str] = None,
        limit: int = 5,
        min_confidence: float = 0.0,
        primary_subject: Optional[str] = None,
    ) -> List[Tuple[KnowledgeNode, float]]:
        """Fallback substring search for queries that don't match FTS5 stems."""
        if not query or not query.strip():
            return []

        subject_tokens = extract_subject_tokens(query)
        if primary_subject and primary_subject.strip():
            ps_tokens = [
                t.lower() for t in re.sub(r"[^\w\s\-]", " ", primary_subject).split()
                if len(t) > 1 and t.lower() not in STOPWORDS
            ]
            if ps_tokens:
                subject_tokens = ps_tokens

        cleaned = re.sub(r"[^\w\s\-]", " ", query)
        all_tokens = [t.strip().lower() for t in cleaned.split() if len(t.strip()) > 1]
        content_tokens = [t for t in all_tokens if t not in STOPWORDS]
        tokens = subject_tokens if subject_tokens else (content_tokens if content_tokens else all_tokens)
        if not tokens:
            return []

        sql = "SELECT * FROM knowledge_nodes WHERE ("
        conds = []
        params: List[Any] = []
        for t in tokens:
            conds.append("(LOWER(title) LIKE ? OR LOWER(content) LIKE ? OR LOWER(tags_json) LIKE ?)")
            p = f"%{t}%"
            params.extend([p, p, p])
        sql += " OR ".join(conds) + ")"

        if domain:
            if domain in ("science", "stem", "physics", "chemistry", "biology"):
                sql += " AND domain IN ('science', 'stem', 'physics', 'chemistry', 'biology')"
            elif domain in ("computer_science", "programming", "software"):
                sql += " AND domain IN ('computer_science', 'programming', 'software')"
            else:
                sql += " AND domain = ?"
                params.append(domain)

        if min_confidence > 0.0:
            sql += " AND confidence >= ?"
            params.append(min_confidence)

        sql += " LIMIT ?"
        params.append(max(limit * 2, 10))

        results: List[Tuple[KnowledgeNode, float]] = []
        now = time.time()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            for row in cursor.fetchall():
                node = self._row_to_node(row)
                if not node.is_expired(now):
                    # Enforce subject constraint
                    if subject_tokens:
                        doc_text = f"{node.title} {' '.join(node.tags)} {node.content}".lower()
                        if not any(st in doc_text for st in subject_tokens):
                            continue
                    results.append((node, 0.5))
                    if len(results) >= limit:
                        break

        return results

    # =========================================================================
    # CONTRADICTION & DISPUTE TRACKING
    # =========================================================================

    def record_contradiction(
        self,
        topic: str,
        claim_a: str,
        source_a: str,
        claim_b: str,
        source_b: str,
        difference_description: str = "",
    ) -> int:
        """Logs an identified contradiction between two sources on a given topic."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO knowledge_contradictions (
                    topic, claim_a, source_a, claim_b, source_b,
                    difference_description, status, detected_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?)
            """, (topic, claim_a, source_a, claim_b, source_b, difference_description, time.time()))
            conn.commit()
            return cursor.lastrowid or 0

    def get_contradictions(self, topic: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieves recorded contradictions, optionally filtered by topic."""
        sql = "SELECT * FROM knowledge_contradictions"
        params: List[Any] = []
        if topic:
            sql += " WHERE LOWER(topic) = ?"
            params.append(topic.lower().strip())
        sql += " ORDER BY detected_at DESC"

        records = []
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            for row in cursor.fetchall():
                records.append({
                    "id": row["id"],
                    "topic": row["topic"],
                    "claim_a": row["claim_a"],
                    "source_a": row["source_a"],
                    "claim_b": row["claim_b"],
                    "source_b": row["source_b"],
                    "difference": row["difference_description"],
                    "status": row["status"],
                    "detected_at": row["detected_at"],
                })
        return records

    # =========================================================================
    # BULK OPERATIONS, PRUNING & STATISTICS
    # =========================================================================

    def bulk_import(self, nodes: List[KnowledgeNode]) -> int:
        """Efficiently imports a batch of knowledge nodes within a single transaction."""
        if not nodes:
            return 0
        imported = 0
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for node in nodes:
                sources_json = json.dumps([s.to_dict() for s in node.sources], ensure_ascii=False)
                tags_json = json.dumps(node.tags, ensure_ascii=False)
                metadata_json = json.dumps(node.metadata, ensure_ascii=False)

                cursor.execute("""
                    INSERT INTO knowledge_nodes (
                        node_id, domain, topic, title, content,
                        epistemic_type, confidence, sources_json, tags_json,
                        temporal_anchor, ttl_seconds, created_at, updated_at, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(node_id) DO UPDATE SET
                        domain=excluded.domain,
                        topic=excluded.topic,
                        title=excluded.title,
                        content=excluded.content,
                        epistemic_type=excluded.epistemic_type,
                        confidence=excluded.confidence,
                        sources_json=excluded.sources_json,
                        tags_json=excluded.tags_json,
                        temporal_anchor=excluded.temporal_anchor,
                        ttl_seconds=excluded.ttl_seconds,
                        updated_at=excluded.updated_at,
                        metadata_json=excluded.metadata_json;
                """, (
                    node.node_id,
                    node.domain,
                    node.topic,
                    node.title,
                    node.content,
                    node.epistemic_type.value,
                    node.confidence,
                    sources_json,
                    tags_json,
                    node.temporal_anchor,
                    node.ttl_seconds,
                    node.created_at,
                    node.updated_at,
                    metadata_json,
                ))
                imported += 1
            conn.commit()
        return imported

    def prune_expired(self) -> int:
        """Removes all nodes whose TTL has elapsed. Returns number of pruned nodes."""
        now = time.time()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM knowledge_nodes
                WHERE ttl_seconds IS NOT NULL AND (? - updated_at) > ttl_seconds
            """, (now,))
            conn.commit()
            return cursor.rowcount

    def count_nodes(self, domain: Optional[str] = None) -> int:
        """Returns total count of stored nodes, optionally filtered by domain."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if domain:
                cursor.execute("SELECT COUNT(*) AS c FROM knowledge_nodes WHERE domain = ?", (domain,))
            else:
                cursor.execute("SELECT COUNT(*) AS c FROM knowledge_nodes")
            row = cursor.fetchone()
            return int(row["c"]) if row else 0

    def list_domains(self) -> List[Dict[str, Any]]:
        """Returns summary statistics for each domain in the store."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT domain, COUNT(*) AS count, AVG(confidence) AS avg_confidence
                FROM knowledge_nodes
                GROUP BY domain
                ORDER BY count DESC;
            """)
            out = []
            for row in cursor.fetchall():
                out.append({
                    "domain": row["domain"],
                    "count": row["count"],
                    "avg_confidence": round(float(row["avg_confidence"]), 3),
                })
            return out

    def list_topics(self, domain: Optional[str] = None) -> List[str]:
        """Returns distinct topics present in the store."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if domain:
                cursor.execute("SELECT DISTINCT topic FROM knowledge_nodes WHERE domain = ? ORDER BY topic", (domain,))
            else:
                cursor.execute("SELECT DISTINCT topic FROM knowledge_nodes ORDER BY topic")
            return [row["topic"] for row in cursor.fetchall()]

    # =========================================================================
    # INTERNAL HELPERS
    # =========================================================================

    @staticmethod
    def _row_to_node(row: sqlite3.Row) -> KnowledgeNode:
        """Converts a database row into a structured KnowledgeNode."""
        raw_sources = json.loads(row["sources_json"]) if row["sources_json"] else []
        sources = [KnowledgeSource.from_dict(s) for s in raw_sources]
        tags = json.loads(row["tags_json"]) if row["tags_json"] else []
        meta = json.loads(row["metadata_json"]) if row["metadata_json"] else {}

        raw_type = row["epistemic_type"]
        try:
            e_type = EpistemicType(raw_type)
        except ValueError:
            e_type = EpistemicType.VERIFIED_FACT

        cols = row.keys()
        return KnowledgeNode(
            node_id=row["node_id"],
            domain=row["domain"],
            topic=row["topic"],
            title=row["title"],
            content=row["content"],
            epistemic_type=e_type,
            confidence=float(row["confidence"]),
            sources=sources,
            tags=tags,
            temporal_anchor=row["temporal_anchor"],
            ttl_seconds=row["ttl_seconds"],
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            metadata=meta,
            version=int(row["version"]) if "version" in cols and row["version"] is not None else 1,
            effective_from=float(row["effective_from"]) if "effective_from" in cols and row["effective_from"] is not None else None,
            effective_until=float(row["effective_until"]) if "effective_until" in cols and row["effective_until"] is not None else None,
            supersedes=str(row["supersedes"]) if "supersedes" in cols and row["supersedes"] is not None else None,
            superseded_by=str(row["superseded_by"]) if "superseded_by" in cols and row["superseded_by"] is not None else None,
        )
