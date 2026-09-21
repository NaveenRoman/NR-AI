"""
Tests for NR-AI Controlled Model Context Protocol (MCP) Phase 2 Subsystem.
Verifies:
- Explicit server registration and trust states
- Tool allowlist verification
- ModelIsolationGate rejection (LLMs cannot call tools directly)
- Agent authorization checks
- Emergency stop enforcement
- Output sanitization and audit logging
"""

import unittest

from app.agent.factory.tools import ToolPermission
from app.mcp.adapter import MCPAdapter, MCPToolSpec
from app.mcp.client import ControlledMCPClient
from app.mcp.config import (
    AuditPolicy,
    MCPServerConfig,
    MCPServerRegistry,
    ServerTrustStatus,
)
from app.remote.emergency import EmergencyStopController


class TestMCPPhase2(unittest.TestCase):
    """Unit test suite for Controlled MCP Client."""

    def setUp(self):
        self.registry = MCPServerRegistry()
        self.adapter = MCPAdapter()
        self.e_stop = EmergencyStopController()
        self.client = ControlledMCPClient(
            registry=self.registry,
            adapter=self.adapter,
            emergency_controller=self.e_stop,
        )

        # Register authorized MCP server
        self.server_cfg = MCPServerConfig(
            server_id="dev_tools_server",
            trust_status=ServerTrustStatus.TRUSTED,
            allowed_tools={"read_project_spec", "list_emulator_devices"},
            allowed_agents={"Droid", "Studio"},
            workspace_scope=r"C:\NR-AI\dev_projects",
            timeout_seconds=5.0,
        )
        self.registry.register_server(self.server_cfg)

        # Register tool implementation in adapter
        self.adapter.register_tool(
            spec=MCPToolSpec(
                name="read_project_spec",
                description="Reads project specifications",
                permission=ToolPermission.READ_ONLY,
                server_id="dev_tools_server",
            ),
            handler=lambda params: f"Project spec for {params.get('project', 'NR-AI')} with key sk-secret123456789012345678",
        )

    def test_successful_tool_call_with_output_sanitization(self):
        """Verify permitted tool call executes and returned secrets are redacted."""
        res = self.client.call_tool(
            server_id="dev_tools_server",
            tool_name="read_project_spec",
            params={"project": "NR-AI"},
            caller_agent_id="Droid",
            agent_permissions={ToolPermission.READ_ONLY},
        )
        self.assertTrue(res.success)
        self.assertIn("Project spec for NR-AI", res.content)
        # Secret in tool output must be redacted
        self.assertNotIn("sk-secret", res.content)
        self.assertIn("SECRET_REDACTED", res.content)

    def test_model_isolation_gate_blocks_direct_llm_call(self):
        """Verify LLM proposing direct execution is unconditionally rejected."""
        res = self.client.call_tool(
            server_id="dev_tools_server",
            tool_name="read_project_spec",
            params={"project": "NR-AI"},
            caller_agent_id="Droid",
            is_direct_model_call=True,  # Direct model call flag
        )
        self.assertFalse(res.success)
        self.assertIn("MODEL_ISOLATION_VIOLATION", res.error)

    def test_unregistered_server_rejection(self):
        """Verify attempting to call an unknown MCP server is rejected."""
        res = self.client.call_tool(
            server_id="unknown_rogue_server",
            tool_name="some_tool",
            params={},
            caller_agent_id="Droid",
        )
        self.assertFalse(res.success)
        self.assertIn("UNKNOWN_MCP_SERVER", res.error)

    def test_quarantined_server_rejection(self):
        """Verify calling a quarantined server is rejected."""
        quarantined = MCPServerConfig(
            server_id="suspicious_server",
            trust_status=ServerTrustStatus.QUARANTINED,
            allowed_tools={"do_something"},
        )
        self.registry.register_server(quarantined)

        res = self.client.call_tool(
            server_id="suspicious_server",
            tool_name="do_something",
            params={},
            caller_agent_id="Droid",
        )
        self.assertFalse(res.success)
        self.assertIn("SERVER_QUARANTINED", res.error)

    def test_unauthorized_agent_rejection(self):
        """Verify calling agent not in allowed_agents is rejected."""
        res = self.client.call_tool(
            server_id="dev_tools_server",
            tool_name="read_project_spec",
            params={},
            caller_agent_id="UnregisteredAgent",
        )
        self.assertFalse(res.success)
        self.assertIn("AGENT_UNAUTHORIZED", res.error)

    def test_tool_not_in_allowlist_rejection(self):
        """Verify attempting to invoke a tool not in allowed_tools is rejected."""
        res = self.client.call_tool(
            server_id="dev_tools_server",
            tool_name="unregistered_dangerous_tool",
            params={},
            caller_agent_id="Droid",
        )
        self.assertFalse(res.success)
        self.assertIn("TOOL_NOT_ALLOWED", res.error)

    def test_emergency_stop_freezes_mcp_client(self):
        """Verify emergency stop freezes all MCP tool calls."""
        self.e_stop.trigger_emergency_stop("Safety test freeze")

        res = self.client.call_tool(
            server_id="dev_tools_server",
            tool_name="read_project_spec",
            params={},
            caller_agent_id="Droid",
        )
        self.assertFalse(res.success)
        self.assertIn("EMERGENCY_STOP_ACTIVE", res.error)


if __name__ == "__main__":
    unittest.main()
