"""
Dedicated Unit & Integration Tests: Declarative Agent Definition Compatibility.
Tests TOML and dictionary parsing, tool allowlist audit, allow_shell=False invariant,
memory scoping, and mapping to AgentSpecification.
"""

import unittest

from app.agent.definitions.declarative import (
    DeclarativeAgentDefinition,
    MemoryScope,
)
from app.agent.factory.specification import AgentSpecification


class TestAgentDefinitionAdapter(unittest.TestCase):

    def test_declarative_definition_validation(self):
        """Valid declarative definition passes audit and converts to AgentSpecification."""
        decl = DeclarativeAgentDefinition(
            agent_id="code_reviewer",
            name="Code Reviewer",
            purpose="Monitors repository and verifies code syntax and style.",
            capabilities=["CODING", "VERIFICATION"],
            allowed_tools=["file.read_file", "git.diff"],
            memory_scopes=[MemoryScope.TASK, MemoryScope.AGENT],
            workspace_scope="dev_projects/NR-AI",
        )
        is_valid, errors = decl.validate()
        self.assertTrue(is_valid, f"Validation errors: {errors}")

        spec = decl.to_agent_specification()
        self.assertIsInstance(spec, AgentSpecification)
        self.assertEqual(spec.agent_id, "code_reviewer")
        self.assertEqual(spec.name, "Code Reviewer")
        self.assertFalse(spec.safety_policy.allow_shell)  # Must be 100% False

    def test_prohibited_tools_rejected(self):
        """Declarative definitions with prohibited tools must fail validation."""
        decl = DeclarativeAgentDefinition(
            agent_id="dangerous_agent",
            name="Dangerous Agent",
            purpose="Executes raw commands.",
            allowed_tools=["powershell.exec", "system.cmd"],
        )
        is_valid, errors = decl.validate()
        self.assertFalse(is_valid)
        self.assertTrue(any("prohibited tool" in e.lower() for e in errors))

        with self.assertRaises(ValueError):
            decl.to_agent_specification()

    def test_prohibited_pattern_rejected(self):
        """Tools matching prohibited patterns must be rejected."""
        decl = DeclarativeAgentDefinition(
            agent_id="shady_agent",
            name="Shady Agent",
            purpose="Executes shell.",
            allowed_tools=["my_custom_shell_runner"],
        )
        is_valid, errors = decl.validate()
        self.assertFalse(is_valid)
        self.assertTrue(any("prohibited pattern" in e.lower() for e in errors))

    def test_unsafe_workspace_rejected(self):
        """Path traversal outside dev_projects must be rejected."""
        decl = DeclarativeAgentDefinition(
            agent_id="jailbreaker",
            name="Jailbreaker",
            purpose="Escapes workspace.",
            workspace_scope="../../Windows/System32",
        )
        is_valid, errors = decl.validate()
        self.assertFalse(is_valid)
        self.assertTrue(any("unsafe workspace" in e.lower() for e in errors))

    def test_toml_operator_loading(self):
        """TOML content in OpenJarvis template format loads correctly."""
        toml_content = """
        [template]
        id = "repo_monitor"
        name = "Repository Monitor"
        description = "Monitors repository changes."
        tools = ["file.read_file", "git.status"]
        system_prompt_template = "You are a repository monitor operative."
        """
        decl = DeclarativeAgentDefinition.from_toml(toml_content)
        self.assertEqual(decl.agent_id, "repo_monitor")
        self.assertEqual(decl.name, "Repository Monitor")
        self.assertIn("file.read_file", decl.allowed_tools)
        self.assertEqual(decl.purpose, "You are a repository monitor operative.")

        is_valid, errors = decl.validate()
        self.assertTrue(is_valid, f"Errors: {errors}")


if __name__ == "__main__":
    unittest.main()
