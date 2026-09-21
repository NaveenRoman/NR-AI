"""
Authorized Git Repository Memory and Indexer for NR-AI Jarvis.
Safely queries local Git metadata, commit history, checkpoints, and project documentation.

Invariants:
- Zero shell execution: all git calls use strict argument lists via subprocess.run(..., shell=False).
- Strict secret exclusion: never indexes or exposes .env, credentials, private keys, or API tokens.
- All extracted text is sanitized via PromptGuardrails.
- Bounded context retrieval: never dumps entire files or the whole repository into prompts.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.jarvis.models import SourceDomain, SourceProvenance
from app.security.guardrails import PromptGuardrails

logger = logging.getLogger("NRAI.Jarvis.RepoMemory")

FORBIDDEN_PATTERNS = [
    re.compile(r"(?i)\.env($|\.)"),
    re.compile(r"(?i)\.pem$"),
    re.compile(r"(?i)\.key$"),
    re.compile(r"(?i)id_rsa"),
    re.compile(r"(?i)credentials"),
    re.compile(r"(?i)secrets?\.json"),
]

DOC_FILES = [
    "README.md",
    "NR-AI_ARCHITECTURE.md",
    "NR-AI_CURRENT_STATE.md",
    "NR-AI_MASTER_STATUS.md",
    "NR-AI_PHASE_HISTORY.md",
    "NR-AI_ROADMAP.md",
    "NR-AI_TEST_RESULTS.md",
]


class GitRepositoryMemory:
    """
    Manages indexing and querying of the authorized NR-AI repository and reports.
    """

    def __init__(
        self,
        repo_root: Optional[str] = None,
        reports_dir: Optional[str] = None,
    ) -> None:
        self.repo_root = Path(repo_root or r"C:\NR-AI")
        self.reports_dir = Path(reports_dir or r"C:\Users\navee\Desktop\NR-AI Project Report")
        self._guardrails = PromptGuardrails()
        self._doc_cache: Dict[str, str] = {}
        self._load_doc_cache()

    def _load_doc_cache(self) -> None:
        """Cache authorized markdown documents with secret scrubbing."""
        # 1. Root documentation files
        for filename in DOC_FILES:
            file_path = self.repo_root / filename
            if file_path.is_file():
                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        raw = f.read()
                        self._doc_cache[filename] = self._guardrails.redact(raw)
                except Exception as exc:
                    logger.debug("Failed to read %s: %s", filename, exc)

        # 2. Phase reports in Desktop folder
        if self.reports_dir.is_dir():
            try:
                for rfile in self.reports_dir.glob("*.md"):
                    if any(p.search(rfile.name) for p in FORBIDDEN_PATTERNS):
                        continue
                    try:
                        with open(rfile, "r", encoding="utf-8", errors="ignore") as f:
                            raw = f.read()
                            self._doc_cache[f"reports/{rfile.name}"] = self._guardrails.redact(raw)
                    except Exception:
                        pass
            except Exception as exc:
                logger.debug("Failed to scan reports directory: %s", exc)

    def _run_git(self, args: List[str], timeout_sec: float = 3.0) -> Optional[str]:
        """Execute a git command safely with shell=False."""
        try:
            res = subprocess.run(
                ["git"] + args,
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                shell=False,
            )
            if res.returncode == 0:
                return res.stdout.strip()
            return None
        except Exception as exc:
            logger.debug("Git execution error for %s: %s", args, exc)
            return None

    def get_latest_commit(self) -> Dict[str, Any]:
        """Return the latest git commit hash, summary, and author."""
        out = self._run_git(["log", "-n", "1", "--pretty=format:%h|%H|%s|%an|%ad", "--date=iso"])
        if not out:
            return {
                "short_hash": "unknown",
                "full_hash": "unknown",
                "subject": "Git information unavailable",
                "author": "NR-AI",
                "date": "current",
            }
        parts = out.split("|")
        if len(parts) >= 5:
            return {
                "short_hash": parts[0],
                "full_hash": parts[1],
                "subject": parts[2],
                "author": parts[3],
                "date": parts[4],
            }
        return {"short_hash": out[:7], "full_hash": out, "subject": "Committed", "author": "NR-AI", "date": "current"}

    def get_recent_commits(self, count: int = 5, limit: Optional[int] = None) -> List[Dict[str, str]]:
        """Return recent commits formatted as list of dicts."""
        n = limit if limit is not None else count
        out = self._run_git(["log", f"-n", str(n), "--pretty=format:%h|%s|%cd", "--date=short"])
        if not out:
            return []
        commits = []
        for line in out.splitlines():
            line = line.strip()
            if line:
                parts = line.split("|", 2)
                short_hash = parts[0]
                subj = parts[1] if len(parts) > 1 else ""
                date = parts[2] if len(parts) > 2 else ""
                commits.append({"hash": short_hash, "subject": subj, "date": date})
        return commits

    def get_git_status_summary(self) -> Dict[str, Any]:
        """Return clean branch and working tree summary."""
        branch = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"]) or "main"
        status_raw = self._run_git(["status", "--porcelain"]) or ""
        is_clean = len(status_raw.strip()) == 0
        return {
            "branch": branch,
            "is_clean": is_clean,
            "modified_count": len([l for l in status_raw.splitlines() if l.strip()]),
        }

    def get_git_status(self) -> Dict[str, Any]:
        """Return clean branch, working tree, and latest commit info."""
        summary = self.get_git_status_summary()
        latest = self.get_latest_commit()
        return {
            "branch": summary.get("branch", "main"),
            "is_clean": summary.get("is_clean", True),
            "modified_count": summary.get("modified_count", 0),
            "latest_commit": latest.get("short_hash", "HEAD"),
        }

    def _is_file_allowed(self, path: Path | str) -> bool:
        """Check if a file path is allowed for indexing (excluding secrets and keys)."""
        name = path.name if isinstance(path, Path) else str(path)
        return not any(p.search(name) for p in FORBIDDEN_PATTERNS)

    def search_repository_knowledge(self, query: str, limit: int = 4) -> List[SourceProvenance]:
        """
        Search indexed documentation, phase reports, and commit logs for relevant evidence.
        Enforces that snippets are bounded, secret-free, and attributed.
        """
        results: List[SourceProvenance] = []
        q_lower = query.lower()
        q_terms = set(re.findall(r"\w+", q_lower))

        # 1. Search git commit subjects if asking about commits, changes, recent work
        if any(term in q_lower for term in ("commit", "commits", "changed", "changes", "latest", "recent", "log", "git")):
            recent = self.get_recent_commits(5)
            for c in recent:
                if any(term in c["subject"].lower() for term in q_terms) or "commit" in q_lower or "recent" in q_lower:
                    results.append(
                        SourceProvenance(
                            source_domain=SourceDomain.GITHUB,
                            title="Git Commit History",
                            ref=c["hash"],
                            snippet=f"Commit {c['hash']}: {c['subject']}",
                            confidence=0.95,
                        )
                    )
                    if len(results) >= limit:
                        return results

        # 2. Search cached reports and documentation files
        scored_matches = []
        for doc_key, content in self._doc_cache.items():
            content_lower = content.lower()
            score = 0
            for term in q_terms:
                if len(term) < 3:
                    continue
                occurrences = content_lower.count(term)
                score += min(occurrences, 5) * 2

            # Boost if doc name matches query
            if any(term in doc_key.lower() for term in q_terms if len(term) > 2):
                score += 10

            if score > 0:
                # Find best matching paragraph/snippet
                paragraphs = content.split("\n\n")
                best_para = ""
                best_para_score = 0
                for p in paragraphs:
                    p_clean = p.strip()
                    if not p_clean or len(p_clean) < 20:
                        continue
                    p_lower = p_clean.lower()
                    p_score = sum(p_lower.count(t) for t in q_terms if len(t) > 2)
                    if p_score > best_para_score:
                        best_para_score = p_score
                        best_para = p_clean

                snippet = best_para[:350] if best_para else content[:350]
                domain = SourceDomain.REPORT if "reports/" in doc_key else SourceDomain.PROJECT
                doc_title = doc_key.replace("reports/", "").replace(".md", "").replace("_", " ")

                scored_matches.append((score, SourceProvenance(
                    source_domain=domain,
                    title=doc_title,
                    ref=doc_key,
                    snippet=snippet,
                    confidence=min(1.0, 0.5 + (score * 0.05)),
                )))

        scored_matches.sort(key=lambda x: x[0], reverse=True)
        for _, prov in scored_matches[:limit]:
            results.append(prov)

        return results[:limit]
