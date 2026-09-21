"""
NR-AI Unified Memory Integration Engine.
Coordinates access across the 5 authoritative memory domains:
KNOWLEDGE, CONVERSATION, TASK, PROJECT, AGENT.
Enforces strict ownership, workspace boundaries, and authorized checkpoint restoration.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from app.memory.boundaries import (
    MemoryBoundaryManager,
    MemoryCategory,
    MemoryRecord,
)
from app.task.checkpoint_store import (
    SharedTaskStatus,
    TaskCheckpointStore,
    TaskRecord,
    sanitize_secrets,
)
from app.agent.engineering_context import (
    ActiveProjectContext,
    ActiveProjectContextManager,
)

logger = logging.getLogger("NRAI.MemoryIntegration")


class UnifiedMemoryIntegrator:
    """
    Unified coordinator ensuring strict 5-domain isolation and authorized state retrieval.
    Prevents cross-domain, cross-workspace, and cross-agent leakage.
    """

    def __init__(
        self,
        boundary_manager: Optional[MemoryBoundaryManager] = None,
        task_store: Optional[TaskCheckpointStore] = None,
        project_manager: Optional[ActiveProjectContextManager] = None,
    ):
        self.boundary_manager = boundary_manager or MemoryBoundaryManager()
        self.task_store = task_store or TaskCheckpointStore()
        self.project_manager = project_manager or ActiveProjectContextManager()
        self._conversation_histories: Dict[str, List[Dict[str, Any]]] = {}

    def save_task_checkpoint(
        self,
        task_id: str,
        agent_id: str,
        workspace_scope: str,
        step_number: int,
        checkpoint_data: Dict[str, Any],
        evidence_references: Optional[List[str]] = None,
        project_id: str = "default_project",
    ) -> bool:
        """
        Persist a task checkpoint with boundary and secret verification.
        """
        # 1. Scrub secrets
        cleaned_data = sanitize_secrets(checkpoint_data)

        # 2. Check or create TaskRecord in TaskCheckpointStore
        existing = self.task_store.get_task(task_id)
        if existing:
            # Verify owner
            if existing.agent_id != agent_id and agent_id != "system":
                logger.warning(f"Checkpoint save denied: agent '{agent_id}' does not own task '{task_id}'")
                return False
            success = self.task_store.save_checkpoint(
                task_id=task_id,
                step_number=step_number,
                state_data=cleaned_data,
                evidence_references=evidence_references or [],
            )
            return success
        else:
            new_task = TaskRecord(
                task_id=task_id,
                agent_id=agent_id,
                project_id=project_id,
                status=SharedTaskStatus.RUNNING,
                current_step=step_number,
                checkpoint=cleaned_data,
                workspace_context={"workspace_scope": workspace_scope},
                evidence_references=evidence_references or [],
            )
            return bool(self.task_store.create_task(new_task))

    def restore_task_checkpoint(
        self,
        task_id: str,
        caller_agent_id: str,
        caller_workspace: str,
    ) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """
        Restore a task checkpoint verifying that caller is authorized for the workspace & task.
        Guarantees: A task resumed after restart must restore only the context it is authorized to access.
        """
        task_rec = self.task_store.get_task(task_id)
        if not task_rec:
            return False, None, f"TASK_NOT_FOUND: Task '{task_id}' does not exist."

        task_ws = task_rec.workspace_context.get("workspace_scope", "global")

        # Boundary check via MemoryBoundaryManager
        mock_mem_record = MemoryRecord(
            memory_id=f"mem_{task_id}",
            category=MemoryCategory.TASK,
            owner_id=task_rec.agent_id,
            workspace_scope=task_ws,
            data=task_rec.checkpoint,
        )

        allowed, reason = self.boundary_manager.validate_access(
            caller_agent_id=caller_agent_id,
            caller_workspace=caller_workspace,
            record=mock_mem_record,
            operation="read",
        )
        if not allowed:
            return False, None, reason

        return True, task_rec.checkpoint, "AUTHORIZED: Checkpoint restored under authorized boundary."

    def restore_project_context(
        self,
        caller_agent_id: str,
        caller_workspace: str,
    ) -> Tuple[bool, Optional[ActiveProjectContext], str]:
        """
        Restore active engineering project context ensuring workspace containment.
        """
        ctx = self.project_manager.get_active_context()
        if not ctx:
            return False, None, "NO_ACTIVE_PROJECT: No project context currently active."

        proj_path = ctx.canonical_path or "global"
        mock_mem = MemoryRecord(
            memory_id=f"proj_{ctx.project_id}",
            category=MemoryCategory.PROJECT,
            owner_id=caller_agent_id,
            workspace_scope=proj_path,
            data=ctx.to_dict(),
        )

        allowed, reason = self.boundary_manager.validate_access(
            caller_agent_id=caller_agent_id,
            caller_workspace=caller_workspace,
            record=mock_mem,
            operation="read",
        )
        if not allowed:
            return False, None, reason

        return True, ctx, "AUTHORIZED: Project context restored under authorized boundary."

    def access_agent_scratchpad(
        self,
        caller_agent_id: str,
        target_agent_id: str,
        operation: str = "read",
    ) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """
        Enforce strict private agent scratchpad isolation.
        """
        mock_mem = MemoryRecord(
            memory_id=f"agent_{target_agent_id}",
            category=MemoryCategory.AGENT,
            owner_id=target_agent_id,
            workspace_scope="global",
        )

        allowed, reason = self.boundary_manager.validate_access(
            caller_agent_id=caller_agent_id,
            caller_workspace="global",
            record=mock_mem,
            operation=operation,
        )
        if not allowed:
            return False, None, reason

        pad = self.boundary_manager.get_agent_scratchpad(target_agent_id)
        return True, pad, "AUTHORIZED: Agent scratchpad accessed."

    def update_agent_scratchpad(
        self,
        caller_agent_id: str,
        target_agent_id: str,
        key: str,
        value: Any,
    ) -> Tuple[bool, str]:
        """Update private agent scratchpad verifying strict agent ownership."""
        mock_mem = MemoryRecord(
            memory_id=f"agent_{target_agent_id}",
            category=MemoryCategory.AGENT,
            owner_id=target_agent_id,
            workspace_scope="global",
        )
        allowed, reason = self.boundary_manager.validate_access(
            caller_agent_id=caller_agent_id,
            caller_workspace="global",
            record=mock_mem,
            operation="write",
        )
        if not allowed:
            return False, reason

        self.boundary_manager.update_agent_scratchpad(target_agent_id, key, value)
        return True, "AUTHORIZED: Agent scratchpad updated."
