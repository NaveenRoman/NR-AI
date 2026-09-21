"""
Dedicated Unit & Integration Tests: MCP Tool Adapter & Safety Pipeline.
Tests MCP tool registration, ModelIsolationGate rejection, prohibited tool rejection,
scope check, timeout, and E-stop freeze.
"""

import time
import unittest

from app.agent.factory.tools import ToolPermission, ToolRiskLevel
from app.mcp.adapter import (
    MCPExecutionResult,
    MCPSafetyGate,
    MCPToolRegistry,
    MCPToolSpec,
)


class TestMCPAdapter(unittest.TestCase):

    def setUp(self):
        self.registry = MCPToolRegistry()

        # Register a safe read-only tool
        self.read_spec = MCPToolSpec(
            name="workspace_file_reader",
            description="Reads files safely within workspace",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
            permission=ToolPermission.READ_ONLY,
            risk_level=ToolRiskLevel.LOW,
        )
        self.registry.register_tool(
            self.read_spec,
            lambda p: {"success": True, "content": f"Contents of {p.get('path')}"},
        )

        # Register a tool that times out
        self.slow_spec = MCPToolSpec(
            name="slow_analyzer",
            description="Slow mock tool",
            permission=ToolPermission.READ_ONLY,
        )
        self.registry.register_tool(
            self.slow_spec,
            lambda p: time.sleep(1.0) or {"success": True, "content": "done"},
        )

    def test_safe_tool_execution(self):
        """Authorized agent executing allowlisted tool succeeds."""
        res = self.registry.execute_tool(
            tool_name="workspace_file_reader",
            params={"path": "README.md"},
            agent_id="ArchitectAgent",
            agent_permissions={ToolPermission.READ_ONLY},
        )
        self.assertTrue(res.success)
        self.assertIn("Contents of README.md", res.content)

    def test_direct_model_bypass_rejected(self):
        """Model cannot directly invoke MCP tool bypassing NR-AI orchestration."""
        res = self.registry.execute_tool(
            tool_name="workspace_file_reader",
            params={"path": "secret.txt"},
            agent_id="gpt-6-astra",
            agent_permissions={ToolPermission.READ_ONLY},
            is_direct_model_call=True,  # Direct model call prohibited
        )
        # workspace_file_reader is not authorized for direct model execution
        # (ModelIsolationGate strictly isolates models from direct tool invocation)
        self.assertFalse(res.success)
        self.assertIn("MODEL_ISOLATION_VIOLATION", res.error)

    def test_prohibited_tool_registration_rejected(self):
        """Attempting to register shell or cmd execution tools must be rejected."""
        bad_spec = MCPToolSpec(
            name="shell.exec",
            description="Arbitrary shell execution",
            permission=ToolPermission.HIGH_PRIVILEGE,
        )
        ok = self.registry.register_tool(bad_spec, lambda p: {})
        self.assertFalse(ok)

        # Regex matching check
        bad_spec_2 = MCPToolSpec(
            name="run_powershell_script",
            description="Powershell execution",
            permission=ToolPermission.HIGH_PRIVILEGE,
        )
        ok2 = self.registry.register_tool(bad_spec_2, lambda p: {})
        self.assertFalse(ok2)

    def test_agent_permission_denial(self):
        """Agent lacking required ToolPermission must be denied."""
        # Agent has no permissions, tool requires READ_ONLY
        res = self.registry.execute_tool(
            tool_name="workspace_file_reader",
            params={"path": "README.md"},
            agent_id="UntrustedAgent",
            agent_permissions=set(),
        )
        self.assertFalse(res.success)
        self.assertIn("PERMISSION_DENIED", res.error)

    def test_emergency_stop_freeze(self):
        """When Emergency Stop is active, all tool invocations are frozen."""
        self.registry.set_emergency_stop(True)
        res = self.registry.execute_tool(
            tool_name="workspace_file_reader",
            params={"path": "README.md"},
            agent_id="ArchitectAgent",
            agent_permissions={ToolPermission.READ_ONLY},
        )
        self.assertFalse(res.success)
        self.assertIn("EMERGENCY_STOP_ACTIVE", res.error)

    def test_timeout_protection(self):
        """Tools exceeding execution timeout abort safely."""
        res = self.registry.execute_tool(
            tool_name="slow_analyzer",
            params={},
            agent_id="TestAgent",
            agent_permissions={ToolPermission.READ_ONLY},
            timeout=0.1,
        )
        self.assertFalse(res.success)
        self.assertIn("timed out", res.error)

    def test_audit_logging(self):
        """Executions and rejections must record audit events."""
        self.registry.execute_tool(
            tool_name="workspace_file_reader",
            params={"path": "README.md"},
            agent_id="ArchitectAgent",
            agent_permissions={ToolPermission.READ_ONLY},
        )
        trail = self.registry.get_audit_trail()
        self.assertTrue(any(e["event_type"] == "TOOL_EXECUTED" for e in trail))


if __name__ == "__main__":
    unittest.main()
