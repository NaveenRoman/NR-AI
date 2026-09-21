"""
NR-AI Local A2A Broker & Message Router.
Routes internal Google A2A JSON-RPC 2.0 messages between authorized NR-AI agents.

Key Invariants:
- Local / In-process only: Bounded to 127.0.0.1; zero public or LAN network exposure.
- Explicit agent identity: Anonymous or unregistered agents are rejected.
- Scope-based access control: Agents can only perform actions covered by their granted scopes.
- Full audit event logging for all requests, responses, and authorization failures.
- Non-blocking timeout handling.
"""

from dataclasses import dataclass, field
from enum import Enum
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from app.a2a.protocol import (
    A2ARequest,
    A2AResponse,
    AgentCard,
    JsonRpcError,
    TaskState,
)

logger = logging.getLogger("NRAI.A2A.Broker")


class A2APermissionScope(str, Enum):
    """Scoped permissions for internal A2A operations."""
    TASK_CREATE = "a2a:task:create"
    TASK_QUERY = "a2a:task:query"
    TASK_CANCEL = "a2a:task:cancel"
    KNOWLEDGE_READ = "a2a:knowledge:read"
    TELEMETRY_READ = "a2a:telemetry:read"
    DIRECT_MESSAGE = "a2a:message:direct"


METHOD_SCOPE_MAP: Dict[str, A2APermissionScope] = {
    "tasks.create": A2APermissionScope.TASK_CREATE,
    "tasks.get": A2APermissionScope.TASK_QUERY,
    "tasks.list": A2APermissionScope.TASK_QUERY,
    "tasks.cancel": A2APermissionScope.TASK_CANCEL,
    "knowledge.query": A2APermissionScope.KNOWLEDGE_READ,
    "telemetry.get": A2APermissionScope.TELEMETRY_READ,
    "message.send": A2APermissionScope.DIRECT_MESSAGE,
}


@dataclass
class RegisteredAgent:
    """Descriptor of a local agent registered on the internal A2A bus."""
    agent_id: str
    card: AgentCard
    handler: Callable[[A2ARequest], A2AResponse]
    granted_scopes: Set[str]
    is_active: bool = True
    registered_at: float = field(default_factory=time.time)


class LocalA2ABroker:
    """
    Internal broker dispatching JSON-RPC 2.0 messages between registered NR-AI agents.
    """

    def __init__(self):
        self._agents: Dict[str, RegisteredAgent] = {}
        self._audit_log: List[Dict[str, Any]] = []
        self._lock = threading.RLock()

    def register_agent(
        self,
        agent_id: str,
        card: AgentCard,
        handler: Callable[[A2ARequest], A2AResponse],
        granted_scopes: Optional[Set[str]] = None,
    ) -> bool:
        """Registers an agent for local A2A message exchange."""
        clean_id = agent_id.strip()
        if not clean_id:
            return False

        scopes = set(granted_scopes or card.permission_scopes)
        with self._lock:
            self._agents[clean_id] = RegisteredAgent(
                agent_id=clean_id,
                card=card,
                handler=handler,
                granted_scopes=scopes,
                is_active=True,
            )
        self._record_audit("AGENT_REGISTERED", clean_id, {"scopes": sorted(list(scopes))})
        return True

    def unregister_agent(self, agent_id: str) -> bool:
        """Unregisters an agent from the internal broker."""
        with self._lock:
            if agent_id in self._agents:
                del self._agents[agent_id]
                self._record_audit("AGENT_UNREGISTERED", agent_id, {})
                return True
        return False

    def get_agent_card(self, agent_id: str) -> Optional[AgentCard]:
        """Fetch discovery card for a registered agent."""
        with self._lock:
            agent = self._agents.get(agent_id)
            return agent.card if agent and agent.is_active else None

    def list_agent_cards(self) -> List[AgentCard]:
        """List all active agent cards."""
        with self._lock:
            return [a.card for a in self._agents.values() if a.is_active]

    def dispatch(self, request: A2ARequest, timeout: float = 10.0) -> A2AResponse:
        """
        Dispatches a JSON-RPC 2.0 request from source_agent to target_agent.
        Enforces authentication, authorization scopes, timeout limits, and audit logging.
        """
        req_id = request.request_id
        src = request.source_agent
        tgt = request.target_agent
        method = request.method

        # 1. Verify Sender Registration
        with self._lock:
            src_agent = self._agents.get(src)
            if not src_agent or not src_agent.is_active:
                err = JsonRpcError.make_error(
                    JsonRpcError.UNAUTHORIZED_AGENT,
                    f"Sender agent '{src}' is not registered or inactive.",
                )
                self._record_audit("AUTH_FAILURE", src, {"target": tgt, "reason": "UNAUTHORIZED_SENDER"})
                return A2AResponse(request_id=req_id, error=err)

            # 2. Verify Target Registration
            tgt_agent = self._agents.get(tgt)
            if not tgt_agent or not tgt_agent.is_active:
                err = JsonRpcError.make_error(
                    JsonRpcError.METHOD_NOT_FOUND,
                    f"Target agent '{tgt}' not found or inactive.",
                )
                self._record_audit("TARGET_NOT_FOUND", src, {"target": tgt})
                return A2AResponse(request_id=req_id, error=err)

        # 3. Scope Verification
        required_scope = METHOD_SCOPE_MAP.get(method)
        if required_scope and required_scope.value not in src_agent.granted_scopes:
            err = JsonRpcError.make_error(
                JsonRpcError.SCOPE_DENIED,
                f"Agent '{src}' lacks required scope '{required_scope.value}' for method '{method}'.",
            )
            self._record_audit("SCOPE_DENIED", src, {"target": tgt, "method": method, "required": required_scope.value})
            return A2AResponse(request_id=req_id, error=err)

        # 4. Dispatch to Target Handler with Timeout
        self._record_audit("MESSAGE_DISPATCH", src, {"target": tgt, "method": method, "request_id": req_id})
        result_holder: List[A2AResponse] = []
        exception_holder: List[Exception] = []

        def worker():
            try:
                res = tgt_agent.handler(request)
                result_holder.append(res)
            except Exception as e:
                exception_holder.append(e)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        thread.join(timeout=timeout)

        if thread.is_alive():
            err = JsonRpcError.make_error(
                JsonRpcError.TIMEOUT,
                f"Request timed out after {timeout} seconds awaiting '{tgt}'.",
            )
            self._record_audit("TIMEOUT", src, {"target": tgt, "method": method, "timeout": timeout})
            return A2AResponse(request_id=req_id, error=err)

        if exception_holder:
            exc = exception_holder[0]
            err = JsonRpcError.make_error(
                JsonRpcError.INTERNAL_ERROR,
                f"Error in target agent '{tgt}': {str(exc)}",
            )
            self._record_audit("DISPATCH_ERROR", src, {"target": tgt, "error": str(exc)})
            return A2AResponse(request_id=req_id, error=err)

        response = result_holder[0] if result_holder else A2AResponse(
            request_id=req_id,
            error=JsonRpcError.make_error(JsonRpcError.INTERNAL_ERROR, "No response produced by handler"),
        )
        self._record_audit("RESPONSE_DELIVERED", tgt, {"source": src, "request_id": req_id})
        return response

    def _record_audit(self, event_type: str, actor: str, details: Dict[str, Any]):
        entry = {
            "timestamp": time.time(),
            "event_type": event_type,
            "actor": actor,
            "details": details,
        }
        with self._lock:
            self._audit_log.append(entry)
            if len(self._audit_log) > 1000:
                self._audit_log.pop(0)

    def get_audit_trail(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Fetch recent A2A audit events."""
        with self._lock:
            return list(self._audit_log[-limit:])


# Global singleton instance
global_a2a_broker = LocalA2ABroker()
