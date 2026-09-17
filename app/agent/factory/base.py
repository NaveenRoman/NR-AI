"""
NR-AI Agent Factory: Base Agent Interface & Execution Contracts.

Provides the foundational contract for all generated agents.
Enforces priority-0 Emergency Stop, bounded step loops, strict tool allowlists,
and comprehensive audit logging.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import logging
import time
from typing import Any, Dict, List, Optional

from app.agent.factory.specification import AgentSpecification
from app.agent.factory.tools import ToolDefinition
from app.remote.remote_action_safety import EmergencyStopController

logger = logging.getLogger("NRAI.BaseFactoryAgent")


@dataclass
class AgentExecutionResult:
    """Standardized execution result produced by a factory-built agent."""
    success: bool
    agent_id: str
    output: str
    steps_executed: int = 0
    error: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)
    logs: List[str] = field(default_factory=list)
    duration_ms: float = 0.0
    emergency_stopped: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "agent_id": self.agent_id,
            "output": self.output,
            "steps_executed": self.steps_executed,
            "error": self.error,
            "data": self.data,
            "logs": self.logs,
            "duration_ms": self.duration_ms,
            "emergency_stopped": self.emergency_stopped,
        }


class BaseFactoryAgent(ABC):
    """
    Abstract base class for all dynamically generated agents in NR-AI.
    No dynamic Python eval/exec: all agents execute through structured, bounded methods.
    """

    def __init__(
        self,
        specification: AgentSpecification,
        tools: Optional[Dict[str, ToolDefinition]] = None,
        model_router: Optional[Any] = None,
        emergency_controller: Optional[EmergencyStopController] = None,
        audit_logger: Optional[Any] = None,
    ):
        self.spec = specification
        self.tools = tools or {}
        self.model_router = model_router
        self.emergency_controller = emergency_controller or EmergencyStopController()
        self.audit_logger = audit_logger
        self.agent_id = specification.agent_id
        self.name = specification.name
        self.version = specification.version

    def _check_emergency_stop(self) -> bool:
        """Returns True if the system-wide Emergency Stop is active."""
        if hasattr(self.emergency_controller, "is_active"):
            return self.emergency_controller.is_active()
        if hasattr(self.emergency_controller, "is_stopped"):
            return self.emergency_controller.is_stopped()
        return False

    def _log_audit(self, event_type: str, details: Dict[str, Any], status: str = "success") -> None:
        if self.audit_logger:
            try:
                self.audit_logger.log_event(
                    event_type=f"agent.{self.agent_id}.{event_type}",
                    details=details,
                    status=status,
                )
            except Exception as e:
                logger.debug(f"Audit log write failed: {e}")

    def execute_tool(self, tool_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Safely invokes an allowlisted tool granted to this agent."""
        if self._check_emergency_stop():
            return {"success": False, "error": "Execution blocked: Emergency Stop active."}

        tool = self.tools.get(tool_id)
        if not tool:
            return {"success": False, "error": f"Tool '{tool_id}' is not granted to agent '{self.agent_id}'."}

        res = tool.execute(params)
        self._log_audit("tool_call", {"tool_id": tool_id, "params": params, "success": res.get("success")})
        return res

    @abstractmethod
    def run(self, task: str, context: Optional[Dict[str, Any]] = None) -> AgentExecutionResult:
        """Executes the specialized agent task bounded by safety invariants."""
        pass
