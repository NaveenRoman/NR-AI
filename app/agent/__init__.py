"""
NR AI Agent Package.
"""

from app.agent.agent_slot import AgentSlot, SlotManager
from app.agent.concurrency_manager import (
    LockAcquisitionError,
    LockConflictError,
    ResourceLockManager,
)
from app.agent.consensus_engine import (
    ConsensusDecision,
    ConsensusEngine,
    ConsensusReport,
    DecisionCase,
    ExecutionEvidence,
    VerificationReview,
)
from app.agent.model_provider import (
    GeminiProvider,
    OpenAIProvider,
    UnifiedModelProvider,
)
from app.agent.model_router import ModelRouter
from app.agent.multi_agent_orchestrator import (
    MultiAgentOrchestrator,
    OrchestratorTask,
    TaskStatus,
)

__all__ = [
    "AgentSlot",
    "SlotManager",
    "ResourceLockManager",
    "LockAcquisitionError",
    "LockConflictError",
    "ConsensusDecision",
    "ConsensusEngine",
    "ConsensusReport",
    "DecisionCase",
    "ExecutionEvidence",
    "VerificationReview",
    "OpenAIProvider",
    "GeminiProvider",
    "UnifiedModelProvider",
    "ModelRouter",
    "MultiAgentOrchestrator",
    "OrchestratorTask",
    "TaskStatus",
]
