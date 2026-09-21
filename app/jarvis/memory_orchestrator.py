"""
Unified Memory Orchestrator for NR-AI Jarvis.
Federates retrieval across Knowledge Trinity, Task Store, Project Context, Git Repo, and Conversation Memory.

Invariants:
- Strictly respects 5 memory domain boundaries (Knowledge, Conversation, Task, Project, Agent).
- Never merges databases blindly; attaches unambiguous provenance to every snippet.
- Leverages ContextManager to enforce strict character and token budgets.
- All secrets are redacted before entering memory context.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from app.context.budget import ContextBudget, ContextChannel
from app.context.manager import ContextManager
from app.jarvis.live_state import LiveStateInspector
from app.jarvis.models import SourceDomain, SourceProvenance
from app.jarvis.repo_memory import GitRepositoryMemory
from app.memory.integration import UnifiedMemoryIntegrator

logger = logging.getLogger("NRAI.Jarvis.MemoryOrchestrator")


class JarvisMemoryOrchestrator:
    """
    Retrieves and budgets evidence across all authorized NR-AI memory domains.
    """

    def __init__(
        self,
        repo_memory: Optional[GitRepositoryMemory] = None,
        memory_integrator: Optional[UnifiedMemoryIntegrator] = None,
        live_inspector: Optional[LiveStateInspector] = None,
        context_manager: Optional[ContextManager] = None,
    ) -> None:
        self.repo_memory = repo_memory or GitRepositoryMemory()
        self.memory_integrator = memory_integrator or UnifiedMemoryIntegrator()
        self.live_inspector = live_inspector or LiveStateInspector()
        self.context_manager = context_manager or ContextManager()
        self._conversation_history: Dict[str, List[Dict[str, str]]] = {}

    def add_conversation_turn(self, session_id: str, role: str, content: str) -> None:
        """Store a conversation turn for continuity."""
        if session_id not in self._conversation_history:
            self._conversation_history[session_id] = []
        self._conversation_history[session_id].append({
            "role": role,
            "content": content,
            "timestamp": str(time.time()),
        })
        # Keep bounded history
        if len(self._conversation_history[session_id]) > 20:
            self._conversation_history[session_id] = self._conversation_history[session_id][-20:]

    def get_conversation_history(self, session_id: str) -> List[Dict[str, str]]:
        return list(self._conversation_history.get(session_id, []))

    def retrieve_unified_memory(
        self,
        query: str,
        session_id: str = "default",
        workspace_scope: str = "global",
        include_live_state: bool = True,
        max_budget_chars: int = 3500,
    ) -> Tuple[str, List[SourceProvenance]]:
        """
        Query across Knowledge, Repo, Project, Task, Conversation, and Live State.
        Packs within budget and returns (packed_context, provenance_list).
        """
        provenance_records: List[SourceProvenance] = []
        budget = ContextBudget(max_total_chars=max_budget_chars)
        ctx = ContextManager(budget=budget)

        q_lower = query.lower()

        # 1. LIVE SYSTEM STATE (if requested)
        if include_live_state:
            live_prov = self.live_inspector.get_live_provenance()
            provenance_records.append(live_prov)
            ctx.add_item(
                channel=ContextChannel.SYSTEM,
                content=f"LIVE RUNTIME STATE: {live_prov.snippet}",
                provenance="live_telemetry",
            )

        # 2. GITHUB & REPOSITORY DOCUMENTATION MEMORY
        repo_matches = self.repo_memory.search_repository_knowledge(query, limit=3)
        for r in repo_matches:
            provenance_records.append(r)
            ctx.add_item(
                channel=ContextChannel.EVIDENCE,
                content=f"[{r.source_domain.value} - {r.title} ({r.ref})]: {r.snippet}",
                provenance=r.ref,
            )

        # 3. PROJECT CONTEXT MEMORY
        ok, proj_ctx, _ = self.memory_integrator.restore_project_context(
            caller_agent_id="jarvis",
            caller_workspace=workspace_scope,
        )
        if ok and proj_ctx:
            proj_snippet = f"Project: {proj_ctx.project_name} | Domain: {proj_ctx.domain.value} | Feature: {proj_ctx.active_feature} | Last Action: {proj_ctx.last_action}"
            p_prov = SourceProvenance(
                source_domain=SourceDomain.PROJECT,
                title="Active Engineering Project",
                ref=proj_ctx.project_name,
                snippet=proj_snippet,
                confidence=0.9,
            )
            provenance_records.append(p_prov)
            ctx.add_item(
                channel=ContextChannel.PROJECT,
                content=proj_snippet,
                provenance="project_context",
            )

        # 4. TASK CHECKPOINT MEMORY
        # Check active or recent tasks if query mentions task/progress/checkpoint
        if any(w in q_lower for w in ("task", "checkpoint", "progress", "workflow", "state")):
            try:
                # Query TaskCheckpointStore for recent tasks
                tasks = self.memory_integrator.task_store.list_tasks(limit=3)
                for t in tasks:
                    t_snippet = f"Task: {t.task_id} | Agent: {t.agent_id} | Status: {t.status.value} | Step: {t.current_step}"
                    t_prov = SourceProvenance(
                        source_domain=SourceDomain.TASK,
                        title="Task Checkpoint",
                        ref=t.task_id,
                        snippet=t_snippet,
                        confidence=0.85,
                    )
                    provenance_records.append(t_prov)
                    ctx.add_item(
                        channel=ContextChannel.TASK,
                        content=t_snippet,
                        provenance=t.task_id,
                    )
            except Exception:
                pass

        # 5. KNOWLEDGE TRINITY MEMORY
        # Probe facts from KnowledgeTrinity / KnowledgeStore if relevant
        try:
            from app.knowledge.store import KnowledgeStore
            kstore = KnowledgeStore()
            k_facts = kstore.search(query, limit=2)
            for f in k_facts:
                f_text = f.get("fact") or f.get("statement") or str(f)
                k_prov = SourceProvenance(
                    source_domain=SourceDomain.KNOWLEDGE,
                    title="Knowledge Trinity Fact",
                    ref=f.get("entity", "core"),
                    snippet=f_text[:200],
                    confidence=0.95,
                )
                provenance_records.append(k_prov)
                ctx.add_item(
                    channel=ContextChannel.KNOWLEDGE,
                    content=f"[Knowledge Trinity]: {f_text}",
                    provenance="trinity_store",
                )
        except Exception:
            pass

        # 6. CONVERSATION CONTINUITY MEMORY
        history = self.get_conversation_history(session_id)
        if history:
            history_lines = [f"{turn['role'].upper()}: {turn['content']}" for turn in history[-4:]]
            conv_snippet = "\n".join(history_lines)
            c_prov = SourceProvenance(
                source_domain=SourceDomain.CONVERSATION,
                title="Conversation Continuity",
                ref=session_id,
                snippet=conv_snippet,
                confidence=1.0,
            )
            provenance_records.append(c_prov)
            ctx.add_item(
                channel=ContextChannel.CONVERSATION,
                content=conv_snippet,
                provenance="dialogue_history",
            )

        # Pack within budget
        packed = ctx.pack_context()
        return packed.prompt_text, provenance_records
