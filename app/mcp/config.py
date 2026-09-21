"""
NR-AI Model Context Protocol (MCP) Server Configuration & Trust Model.
Phase 2 Foundation: Explicitly configured servers, trust statuses, and tool allowlists.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set


class ServerTrustStatus(str, Enum):
    """Trust classification for an MCP server."""
    TRUSTED = "TRUSTED"
    UNTRUSTED = "UNTRUSTED"
    QUARANTINED = "QUARANTINED"


class AuditPolicy(str, Enum):
    """Audit level for MCP executions."""
    STANDARD = "STANDARD"
    STRICT = "STRICT"


@dataclass
class MCPServerConfig:
    """Explicit configuration of a permitted MCP server."""
    server_id: str
    trust_status: ServerTrustStatus = ServerTrustStatus.TRUSTED
    allowed_tools: Set[str] = field(default_factory=set)
    allowed_agents: Set[str] = field(default_factory=set)
    workspace_scope: str = "global"
    timeout_seconds: float = 15.0
    rate_limit_rpm: int = 60
    audit_policy: AuditPolicy = AuditPolicy.STRICT
    enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "server_id": self.server_id,
            "trust_status": self.trust_status.value,
            "allowed_tools": sorted(list(self.allowed_tools)),
            "allowed_agents": sorted(list(self.allowed_agents)),
            "workspace_scope": self.workspace_scope,
            "timeout_seconds": self.timeout_seconds,
            "rate_limit_rpm": self.rate_limit_rpm,
            "audit_policy": self.audit_policy.value,
            "enabled": self.enabled,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MCPServerConfig":
        raw_trust = data.get("trust_status", ServerTrustStatus.TRUSTED.value)
        try:
            trust = ServerTrustStatus(raw_trust)
        except ValueError:
            trust = ServerTrustStatus.UNTRUSTED

        raw_audit = data.get("audit_policy", AuditPolicy.STRICT.value)
        try:
            audit = AuditPolicy(raw_audit)
        except ValueError:
            audit = AuditPolicy.STRICT

        return cls(
            server_id=str(data.get("server_id", "")),
            trust_status=trust,
            allowed_tools=set(data.get("allowed_tools", [])),
            allowed_agents=set(data.get("allowed_agents", [])),
            workspace_scope=str(data.get("workspace_scope", "global")),
            timeout_seconds=float(data.get("timeout_seconds", 15.0)),
            rate_limit_rpm=int(data.get("rate_limit_rpm", 60)),
            audit_policy=audit,
            enabled=bool(data.get("enabled", True)),
            metadata=dict(data.get("metadata", {})),
        )


class MCPServerRegistry:
    """Registry managing authorized MCP servers."""

    def __init__(self):
        self._servers: Dict[str, MCPServerConfig] = {}

    def register_server(self, config: MCPServerConfig) -> None:
        """Register or update an authorized server."""
        self._servers[config.server_id] = config

    def unregister_server(self, server_id: str) -> bool:
        """Remove a server from the registry."""
        if server_id in self._servers:
            del self._servers[server_id]
            return True
        return False

    def get_server(self, server_id: str) -> Optional[MCPServerConfig]:
        """Fetch an authorized server configuration."""
        return self._servers.get(server_id)

    def list_servers(self) -> List[MCPServerConfig]:
        """List all registered servers."""
        return list(self._servers.values())


# Global singleton registry
global_mcp_registry = MCPServerRegistry()
