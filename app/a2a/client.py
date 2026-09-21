"""
NR-AI Local Agent-to-Agent (A2A) Client.
Provides high-level typed interface for an agent to communicate across the local A2A broker.
"""

from typing import Any, Dict, List, Optional
from app.a2a.protocol import A2ADelegationPayload, A2ARequest, A2AResponse
from app.a2a.router import LocalA2ABroker, global_a2a_broker


class A2AClient:
    """
    Local A2A client bound to a specific caller agent.
    Dispatches JSON-RPC 2.0 messages across the local in-process broker.
    """

    def __init__(self, agent_id: str, broker: Optional[LocalA2ABroker] = None):
        self.agent_id = agent_id.strip()
        self.broker = broker or global_a2a_broker

    def discover_agents(self) -> List[Dict[str, Any]]:
        """Query directory of registered active agents."""
        return self.broker.discover_agents(self.agent_id)

    def query_capabilities(self, target_agent_id: str) -> Optional[Dict[str, Any]]:
        """Query capabilities of target agent."""
        return self.broker.query_capabilities(self.agent_id, target_agent_id)

    def delegate_task(
        self,
        target_agent_id: str,
        instruction: str,
        checkpoint_id: Optional[str] = None,
        evidence_references: Optional[List[str]] = None,
        parameters: Optional[Dict[str, Any]] = None,
        workspace_scope: str = "global",
        timeout: float = 30.0,
    ) -> A2AResponse:
        """Delegate a task to a peer agent with checkpoint and evidence references."""
        payload = A2ADelegationPayload(
            instruction=instruction,
            checkpoint_id=checkpoint_id,
            evidence_references=evidence_references or [],
            workspace_scope=workspace_scope,
            parameters=parameters or {},
            timeout_seconds=timeout,
        )
        return self.broker.delegate_task(
            caller_agent_id=self.agent_id,
            target_agent_id=target_agent_id,
            payload=payload,
            timeout=timeout,
        )

    def get_task_status(self, target_agent_id: str, task_id: str, timeout: float = 10.0) -> A2AResponse:
        """Query status of a delegated task."""
        req = A2ARequest(
            method="tasks.status",
            params={"task_id": task_id},
            source_agent=self.agent_id,
            target_agent=target_agent_id,
        )
        return self.broker.dispatch(req, timeout=timeout)

    def get_task_result(self, target_agent_id: str, task_id: str, timeout: float = 10.0) -> A2AResponse:
        """Retrieve completed result of a delegated task."""
        req = A2ARequest(
            method="tasks.result",
            params={"task_id": task_id},
            source_agent=self.agent_id,
            target_agent=target_agent_id,
        )
        return self.broker.dispatch(req, timeout=timeout)

    def cancel_task(self, target_agent_id: str, task_id: str, reason: str = "", timeout: float = 10.0) -> A2AResponse:
        """Cancel an in-flight delegated task."""
        req = A2ARequest(
            method="tasks.cancel",
            params={"task_id": task_id, "reason": reason},
            source_agent=self.agent_id,
            target_agent=target_agent_id,
        )
        return self.broker.dispatch(req, timeout=timeout)

    def send_direct_message(self, target_agent_id: str, message: str, timeout: float = 10.0) -> A2AResponse:
        """Send a direct communication message to another agent."""
        req = A2ARequest(
            method="message.send",
            params={"message": message},
            source_agent=self.agent_id,
            target_agent=target_agent_id,
        )
        return self.broker.dispatch(req, timeout=timeout)
