"""
NR-AI Agent Factory / Self-Expanding Agent Builder.

Provides controlled, safe, deterministic agent design, model selection,
tool allowlisting, verification, and persistent lifecycle management.
"""

from app.agent.factory.base import (
    AgentExecutionResult,
    BaseFactoryAgent,
)
from app.agent.factory.builder import (
    AgentBuilder,
    GeneratedAgent,
)
from app.agent.factory.factory import (
    AgentFactory,
    AgentRegistrationResult,
    CapabilityGapAnalysis,
)
from app.agent.factory.registry import (
    AgentRegistry,
)
from app.agent.factory.safety import (
    AgentFactorySafetyGate,
)
from app.agent.factory.specification import (
    AgentLifecycleState,
    AgentSpecification,
    ModelRequirement,
    SafetyPolicy,
    ToolRequirement,
)
from app.agent.factory.tools import (
    ToolCatalog,
    ToolDefinition,
    ToolPermission,
    ToolRiskLevel,
)
from app.agent.factory.verifier import (
    AgentVerificationReport,
    AgentVerifier,
)

__all__ = [
    "AgentExecutionResult",
    "BaseFactoryAgent",
    "AgentBuilder",
    "GeneratedAgent",
    "AgentFactory",
    "AgentRegistrationResult",
    "CapabilityGapAnalysis",
    "AgentRegistry",
    "AgentFactorySafetyGate",
    "AgentLifecycleState",
    "AgentSpecification",
    "ModelRequirement",
    "SafetyPolicy",
    "ToolRequirement",
    "ToolCatalog",
    "ToolDefinition",
    "ToolPermission",
    "ToolRiskLevel",
    "AgentVerificationReport",
    "AgentVerifier",
]
