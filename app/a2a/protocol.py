"""
NR-AI Agent-to-Agent (A2A) Protocol Specification.
Aligned with Google A2A Specification (JSON-RPC 2.0).

Strict Invariants:
- All communications are strictly internal to the NR-AI host (127.0.0.1 loopback / in-process).
- Explicit sender and receiver identities.
- JSON-RPC 2.0 compliant request, response, and error structures.
"""

from dataclasses import dataclass, field
from enum import Enum
import json
import uuid
from typing import Any, Dict, List, Optional


class TaskState(str, Enum):
    """Lifecycle states of an A2A task according to Google A2A specification."""
    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    CANCELED = "canceled"
    FAILED = "failed"


@dataclass
class AgentCard:
    """
    Agent discovery and identity card.
    Describes agent identity, capabilities, and allowed permission scopes.
    """
    name: str
    description: str = ""
    url: str = "http://127.0.0.1:8585/a2a"
    version: str = "1.0.0"
    capabilities: List[str] = field(default_factory=list)
    skills: List[str] = field(default_factory=list)
    authentication: Dict[str, Any] = field(default_factory=lambda: {"type": "local_bearer", "realm": "nrai_internal"})
    permission_scopes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "url": self.url,
            "version": self.version,
            "capabilities": self.capabilities,
            "skills": self.skills,
            "authentication": self.authentication,
            "permission_scopes": self.permission_scopes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentCard":
        return cls(
            name=str(data.get("name", "")),
            description=str(data.get("description", "")),
            url=str(data.get("url", "http://127.0.0.1:8585/a2a")),
            version=str(data.get("version", "1.0.0")),
            capabilities=list(data.get("capabilities", [])),
            skills=list(data.get("skills", [])),
            authentication=dict(data.get("authentication", {})),
            permission_scopes=list(data.get("permission_scopes", [])),
        )


@dataclass
class A2ATask:
    """An asynchronous task managed across agents via A2A."""
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    state: TaskState = TaskState.SUBMITTED
    input_text: str = ""
    output_text: str = ""
    history: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.task_id,
            "state": self.state.value,
            "input": self.input_text,
            "output": self.output_text,
            "history": self.history,
            "metadata": self.metadata,
        }


@dataclass
class A2ARequest:
    """JSON-RPC 2.0 Request payload for internal agent communication."""
    method: str
    params: Dict[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    source_agent: str = "anonymous"
    target_agent: str = ""
    jsonrpc: str = "2.0"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "jsonrpc": self.jsonrpc,
            "method": self.method,
            "params": self.params,
            "id": self.request_id,
            "source_agent": self.source_agent,
            "target_agent": self.target_agent,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "A2ARequest":
        return cls(
            method=str(data.get("method", "")),
            params=dict(data.get("params", {})),
            request_id=str(data.get("id", uuid.uuid4().hex[:8])),
            source_agent=str(data.get("source_agent", "anonymous")),
            target_agent=str(data.get("target_agent", "")),
            jsonrpc=str(data.get("jsonrpc", "2.0")),
        )


@dataclass
class A2AResponse:
    """JSON-RPC 2.0 Response payload for internal agent communication."""
    request_id: str
    result: Any = None
    error: Optional[Dict[str, Any]] = None
    jsonrpc: str = "2.0"

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "jsonrpc": self.jsonrpc,
            "id": self.request_id,
        }
        if self.error:
            payload["error"] = self.error
        else:
            payload["result"] = self.result
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


class JsonRpcError:
    """Standard JSON-RPC 2.0 error codes and constructors."""
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603

    # Application-specific error codes
    UNAUTHORIZED_AGENT = -32001
    SCOPE_DENIED = -32002
    TIMEOUT = -32003
    TASK_NOT_FOUND = -32004

    @classmethod
    def make_error(cls, code: int, message: str, data: Optional[Any] = None) -> Dict[str, Any]:
        err = {"code": code, "message": message}
        if data is not None:
            err["data"] = data
        return err
