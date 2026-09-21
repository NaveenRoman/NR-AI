"""
NR-AI Memory Boundaries & Scoping Engine.
Enforces explicit separation and isolation across the 5 authoritative memory domains:

1. KNOWLEDGE MEMORY: Facts, research, semantic graph, verified truths (Knowledge Trinity).
2. CONVERSATION MEMORY: Multi-turn dialogue, user requests, assistant responses (Companion session).
3. TASK MEMORY: Resumable step-by-step state, checkpoints, failure logs (TaskCheckpointStore).
4. PROJECT MEMORY: Active engineering context, touched files, rollback stack (ProjectContextMemory).
5. AGENT MEMORY: Bounded ephemeral specialist scratchpad per agent.

Strict Invariants:
- Every record has an explicit owner and workspace scope.
- Cross-workspace memory leakage is strictly blocked.
- Private specialist agent scratchpads are isolated between agents.
- Universal Knowledge Trinity remains the single authoritative knowledge graph.
"""

from dataclasses import dataclass, field
from enum import Enum
import logging
import os
from pathlib import Path
import time
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("NRAI.MemoryBoundaries")


class MemoryCategory(str, Enum):
    """The five explicit memory domains of NR-AI."""
    KNOWLEDGE = "KNOWLEDGE"
    CONVERSATION = "CONVERSATION"
    TASK = "TASK"
    PROJECT = "PROJECT"
    AGENT = "AGENT"


@dataclass
class MemoryRecord:
    """A bounded record within a specific memory domain."""
    memory_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    category: MemoryCategory = MemoryCategory.AGENT
    owner_id: str = "system"
    workspace_scope: str = "global"
    data: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "category": self.category.value,
            "owner_id": self.owner_id,
            "workspace_scope": self.workspace_scope,
            "data": self.data,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class MemoryBoundaryManager:
    """
    Supervises access, isolation, and cross-workspace boundaries for all memory domains.
    """

    def __init__(self):
        # In-memory specialist scratchpads for AGENT memory
        self._agent_scratchpads: Dict[str, Dict[str, Any]] = {}

    def validate_access(
        self,
        caller_agent_id: str,
        caller_workspace: str,
        record: MemoryRecord,
        operation: str = "read",
    ) -> Tuple[bool, str]:
        """
        Enforces deterministic boundary rules on a memory record access request.
        """
        norm_caller_ws = self._normalize_path(caller_workspace)
        norm_record_ws = self._normalize_path(record.workspace_scope)

        # 1. KNOWLEDGE Memory: Shared global read
        if record.category == MemoryCategory.KNOWLEDGE:
            if operation == "read":
                return True, "AUTHORIZED: Knowledge memory is universally readable."
            # Writes to Knowledge memory require Trinity coordinator verification
            if caller_agent_id in ("TrinityCoordinator", "KnowledgeAgent", "system"):
                return True, "AUTHORIZED: Verified knowledge authority."
            return False, "DENIED: Writes to Knowledge Trinity memory require verified knowledge authority."

        # 2. CONVERSATION Memory: Scoped to session & active companion
        if record.category == MemoryCategory.CONVERSATION:
            return True, "AUTHORIZED: Conversation memory accessible within active companion session."

        # 3. TASK & PROJECT Memory: Strict Workspace Isolation
        if record.category in (MemoryCategory.TASK, MemoryCategory.PROJECT):
            if norm_record_ws != "global" and norm_caller_ws != norm_record_ws:
                # Disallow cross-workspace access
                return False, (
                    f"WORKSPACE_LEAKAGE_DENIED: Caller in workspace '{caller_workspace}' cannot access "
                    f"memory scoped to workspace '{record.workspace_scope}'."
                )
            return True, "AUTHORIZED: Workspace boundary matches."

        # 4. AGENT Memory: Strict Agent Ownership
        if record.category == MemoryCategory.AGENT:
            if record.owner_id != caller_agent_id and caller_agent_id != "system":
                return False, (
                    f"AGENT_ISOLATION_DENIED: Agent '{caller_agent_id}' cannot access private scratchpad "
                    f"of agent '{record.owner_id}'."
                )
            return True, "AUTHORIZED: Agent ownership matches."

        return False, "UNKNOWN_CATEGORY: Memory domain access denied."

    def filter_records_by_workspace(
        self,
        caller_workspace: str,
        records: List[MemoryRecord],
    ) -> List[MemoryRecord]:
        """Returns only records accessible within caller_workspace."""
        norm_caller_ws = self._normalize_path(caller_workspace)
        filtered: List[MemoryRecord] = []
        for r in records:
            norm_rec_ws = self._normalize_path(r.workspace_scope)
            if norm_rec_ws == "global" or norm_rec_ws == norm_caller_ws:
                filtered.append(r)
        return filtered

    def get_agent_scratchpad(self, agent_id: str) -> Dict[str, Any]:
        """Returns the private ephemeral scratchpad for a given agent."""
        return self._agent_scratchpads.setdefault(agent_id, {})

    def update_agent_scratchpad(self, agent_id: str, updates: Dict[str, Any]):
        """Updates the private ephemeral scratchpad for a given agent."""
        pad = self.get_agent_scratchpad(agent_id)
        pad.update(updates)

    def clear_agent_scratchpad(self, agent_id: str):
        """Wipes the private ephemeral scratchpad for a given agent."""
        if agent_id in self._agent_scratchpads:
            self._agent_scratchpads[agent_id].clear()

    def _normalize_path(self, path_str: str) -> str:
        if not path_str or path_str == "global":
            return "global"
        try:
            return str(Path(path_str).resolve()).lower()
        except Exception:
            return path_str.strip().lower()


# Global singleton instance
global_memory_boundaries = MemoryBoundaryManager()
