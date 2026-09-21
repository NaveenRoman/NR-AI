"""
NR-AI Context Budget & Channel Definitions.
Defines explicit channels, prioritization orders, and budget allocations.
"""

from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Any, Dict, List, Optional


class ContextChannel(str, Enum):
    """The seven explicit context channels recognized by NR-AI."""
    SYSTEM = "SYSTEM"                   # Core system instructions, persona, invariants
    TASK = "TASK"                       # Active task instruction, state, checkpoint
    PROJECT = "PROJECT"                 # Active engineering project, touched files, workspace
    KNOWLEDGE = "KNOWLEDGE"             # Grounded facts from Knowledge Trinity
    CONVERSATION = "CONVERSATION"       # Multi-turn dialogue history
    EVIDENCE = "EVIDENCE"               # Test results, build logs, execution traces
    AGENT = "AGENT"                     # Ephemeral specialist agent scratchpad


# Default channel priority (higher number = higher priority = preserved during truncation)
CHANNEL_PRIORITY: Dict[ContextChannel, int] = {
    ContextChannel.SYSTEM: 100,
    ContextChannel.TASK: 90,
    ContextChannel.PROJECT: 80,
    ContextChannel.KNOWLEDGE: 70,
    ContextChannel.CONVERSATION: 60,
    ContextChannel.EVIDENCE: 50,
    ContextChannel.AGENT: 40,
}


@dataclass
class ContextItem:
    """An individual piece of context belonging to a specific channel."""
    channel: ContextChannel
    content: str
    provenance: str = "local"
    priority_override: Optional[int] = None
    created_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def priority(self) -> int:
        return self.priority_override if self.priority_override is not None else CHANNEL_PRIORITY.get(self.channel, 50)

    @property
    def char_length(self) -> int:
        return len(self.content)

    @property
    def estimated_tokens(self) -> int:
        # Rough heuristic: ~4 characters per token
        return max(1, len(self.content) // 4)


@dataclass
class ContextBudget:
    """Configurable budget thresholds for prompt context assembly."""
    max_total_tokens: int = 8000
    max_total_chars: int = 32000
    channel_caps: Dict[ContextChannel, int] = field(default_factory=lambda: {
        ContextChannel.SYSTEM: 8000,
        ContextChannel.TASK: 8000,
        ContextChannel.PROJECT: 8000,
        ContextChannel.KNOWLEDGE: 6000,
        ContextChannel.CONVERSATION: 6000,
        ContextChannel.EVIDENCE: 4000,
        ContextChannel.AGENT: 2000,
    })


@dataclass
class PackedContext:
    """Final compacted and packed context ready for model input."""
    prompt_text: str
    total_tokens_est: int
    total_chars: int
    included_channels: List[ContextChannel]
    truncated_channels: List[ContextChannel]
    provenance_trail: List[str]
    scrubbed_secret_count: int = 0
