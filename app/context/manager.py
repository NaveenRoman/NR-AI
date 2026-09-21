"""
NR-AI Context Manager.
Assembles, sanitizes, prioritizes, and compacts multi-channel context for model dispatch.
Never allows secrets to enter context. Preserves provenance and enforces bounded budgets.
"""

from typing import Any, Dict, List, Optional, Set
import logging
import time

from app.context.budget import (
    CHANNEL_PRIORITY,
    ContextBudget,
    ContextChannel,
    ContextItem,
    PackedContext,
)
from app.security.guardrails import PromptGuardrails
from app.task.checkpoint_store import sanitize_secrets

logger = logging.getLogger("NRAI.ContextManager")


class ContextManager:
    """
    Orchestrates what context is supplied to an agent or model.
    Guarantees strict channel budgeting, secret scrubbing, and provenance tracking.
    """

    def __init__(self, budget: Optional[ContextBudget] = None):
        self.budget = budget or ContextBudget()
        self._items: List[ContextItem] = []

    def clear(self) -> None:
        """Clear all stored context items."""
        self._items.clear()

    def add_item(
        self,
        channel: ContextChannel,
        content: str,
        provenance: str = "local",
        priority_override: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Add a piece of context to a specific channel."""
        if not content:
            return
        self._items.append(ContextItem(
            channel=channel,
            content=content,
            provenance=provenance,
            priority_override=priority_override,
            metadata=metadata or {},
        ))

    def set_system_context(self, content: str, provenance: str = "system_policy") -> None:
        """Set or replace the core system context."""
        self._items = [it for it in self._items if it.channel != ContextChannel.SYSTEM]
        self.add_item(ContextChannel.SYSTEM, content, provenance=provenance)

    def set_task_context(
        self,
        task_id: str,
        instruction: str,
        current_step: int = 1,
        checkpoint_summary: Optional[str] = None,
        provenance: str = "task_store",
    ) -> None:
        """Set structured task context."""
        self._items = [it for it in self._items if it.channel != ContextChannel.TASK]
        text = f"TASK ID: {task_id}\nINSTRUCTION: {instruction}\nCURRENT STEP: {current_step}"
        if checkpoint_summary:
            text += f"\nCHECKPOINT: {checkpoint_summary}"
        self.add_item(ContextChannel.TASK, text, provenance=provenance)

    def set_project_context(
        self,
        project_name: str,
        active_feature: Optional[str] = None,
        affected_files: Optional[List[str]] = None,
        provenance: str = "project_context",
    ) -> None:
        """Set structured project context."""
        self._items = [it for it in self._items if it.channel != ContextChannel.PROJECT]
        text = f"ACTIVE PROJECT: {project_name}"
        if active_feature:
            text += f"\nACTIVE FEATURE: {active_feature}"
        if affected_files:
            text += f"\nAFFECTED FILES: {', '.join(affected_files)}"
        self.add_item(ContextChannel.PROJECT, text, provenance=provenance)

    def add_knowledge_item(self, fact: str, provenance: str = "knowledge_trinity") -> None:
        """Add a grounded fact from Knowledge Trinity."""
        self.add_item(ContextChannel.KNOWLEDGE, fact, provenance=provenance)

    def add_conversation_turn(self, role: str, text: str, provenance: str = "session_history") -> None:
        """Add a conversational dialogue turn."""
        turn_text = f"{role.upper()}: {text}"
        self.add_item(ContextChannel.CONVERSATION, turn_text, provenance=provenance)

    def add_evidence(self, evidence_text: str, provenance: str = "execution_log") -> None:
        """Add execution or test evidence."""
        self.add_item(ContextChannel.EVIDENCE, evidence_text, provenance=provenance)

    def pack_context(self) -> PackedContext:
        """
        Assemble, scrub, prioritize, and compact all context into a bounded PackedContext.
        """
        if not self._items:
            return PackedContext(
                prompt_text="",
                total_tokens_est=0,
                total_chars=0,
                included_channels=[],
                truncated_channels=[],
                provenance_trail=[],
                scrubbed_secret_count=0,
            )

        # 1. Secret & PII Scrubbing across all items
        scrubbed_count = 0
        cleaned_items: List[ContextItem] = []
        for it in self._items:
            raw_scrubbed = sanitize_secrets(it.content)
            if isinstance(raw_scrubbed, str):
                g_res = PromptGuardrails.sanitize_text(raw_scrubbed)
                final_text = g_res.sanitized_text
                scrubbed_count += len(g_res.matches)
            else:
                final_text = str(raw_scrubbed)

            cleaned_items.append(ContextItem(
                channel=it.channel,
                content=final_text,
                provenance=it.provenance,
                priority_override=it.priority_override,
                created_at=it.created_at,
                metadata=it.metadata,
            ))

        # 2. Sort items by channel priority (highest first)
        cleaned_items.sort(key=lambda x: x.priority, reverse=True)

        # 3. Apply channel caps and total budget
        channel_used: Dict[ContextChannel, int] = {}
        included: List[ContextItem] = []
        truncated_channels: Set[ContextChannel] = set()
        current_total_chars = 0

        for it in cleaned_items:
            cap = self.budget.channel_caps.get(it.channel, 8000)
            used = channel_used.get(it.channel, 0)
            available_channel_chars = cap - used

            if available_channel_chars <= 0:
                truncated_channels.add(it.channel)
                continue

            available_total_chars = self.budget.max_total_chars - current_total_chars
            if available_total_chars <= 0:
                truncated_channels.add(it.channel)
                continue

            allowed_chars = min(available_channel_chars, available_total_chars)

            if len(it.content) <= allowed_chars:
                included.append(it)
                channel_used[it.channel] = used + len(it.content)
                current_total_chars += len(it.content)
            else:
                # Deterministic truncation for partially fitting item
                truncated_content = it.content[:allowed_chars] + " ... [TRUNCATED]"
                it.content = truncated_content
                included.append(it)
                channel_used[it.channel] = cap
                current_total_chars += len(truncated_content)
                truncated_channels.add(it.channel)

        # 4. Group by channel in standard presentation order
        presentation_order = [
            ContextChannel.SYSTEM,
            ContextChannel.KNOWLEDGE,
            ContextChannel.PROJECT,
            ContextChannel.TASK,
            ContextChannel.CONVERSATION,
            ContextChannel.EVIDENCE,
            ContextChannel.AGENT,
        ]

        sections: List[str] = []
        provenances: Set[str] = set()
        included_channels: Set[ContextChannel] = set()

        for ch in presentation_order:
            ch_items = [it for it in included if it.channel == ch]
            if not ch_items:
                continue
            included_channels.add(ch)
            header = f"=== {ch.value} CONTEXT ==="
            body = "\n".join(it.content for it in ch_items)
            sections.append(f"{header}\n{body}")
            for it in ch_items:
                provenances.add(it.provenance)

        final_prompt = "\n\n".join(sections)
        est_tokens = max(1, len(final_prompt) // 4)

        return PackedContext(
            prompt_text=final_prompt,
            total_tokens_est=est_tokens,
            total_chars=len(final_prompt),
            included_channels=sorted(list(included_channels), key=lambda c: CHANNEL_PRIORITY.get(c, 0), reverse=True),
            truncated_channels=sorted(list(truncated_channels), key=lambda c: CHANNEL_PRIORITY.get(c, 0), reverse=True),
            provenance_trail=sorted(list(provenances)),
            scrubbed_secret_count=scrubbed_count,
        )
