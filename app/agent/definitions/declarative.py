"""
NR-AI Declarative Agent Definition Compatibility Layer.
OpenJarvis-inspired declarative operator TOML/Dict specifications mapped to NR-AI AgentSpecification.

Strict Invariants:
- Declarative Only: Agents cannot dynamically self-replicate or execute arbitrary code.
- Deterministic Tool Allowlist: Tools are strictly verified against prohibited patterns.
- Memory & Workspace Scoping: Bounded strictly to assigned scopes and dev_projects root.
- 100% Invariant: allow_shell is always False.
"""

from dataclasses import asdict, dataclass, field
from enum import Enum
import logging
import os
from pathlib import Path
import re
import tomllib
from typing import Any, Dict, List, Optional, Set, Tuple

from app.agent.factory.specification import (
    AgentSpecification,
    ModelRequirement,
    SafetyPolicy,
    ToolRequirement,
)
from app.agent.factory.tools import (
    PROHIBITED_TOOL_IDS,
    PROHIBITED_TOOL_PATTERNS,
    SANDBOX_ROOT,
    ToolPermission,
    ToolRiskLevel,
)

logger = logging.getLogger("NRAI.AgentDefinitions")


class MemoryScope(str, Enum):
    """Memory domain access permissions."""
    KNOWLEDGE = "KNOWLEDGE"
    CONVERSATION = "CONVERSATION"
    TASK = "TASK"
    PROJECT = "PROJECT"
    AGENT = "AGENT"


@dataclass
class DeclarativeAgentDefinition:
    """
    Declarative specification of an NR-AI agent.
    Compatible with OpenJarvis TOML operator templates while maintaining NR-AI safety boundaries.
    """
    agent_id: str
    name: str
    purpose: str
    version: str = "1.0.0"
    description: str = ""
    capabilities: List[str] = field(default_factory=list)
    preferred_model: str = "gpt-5.6-terra"
    fallback_models: List[str] = field(default_factory=lambda: ["gpt-5.6-luna", "gemini-3.7-flash"])
    max_context_tokens: int = 128000
    allowed_tools: List[str] = field(default_factory=list)
    memory_scopes: List[MemoryScope] = field(default_factory=lambda: [MemoryScope.TASK, MemoryScope.AGENT])
    workspace_scope: str = "dev_projects"
    risk_level: str = "LOW"
    verification_requirements: List[str] = field(default_factory=lambda: ["syntax_check", "test_pass"])
    timeout_seconds: int = 120
    max_steps: int = 20

    def validate(self) -> Tuple[bool, List[str]]:
        """Conducts strict static safety audit of the declarative definition."""
        errors: List[str] = []

        if not self.agent_id or not re.match(r"^[a-zA-Z0-9_-]{3,64}$", self.agent_id):
            errors.append(f"Invalid agent_id '{self.agent_id}': Must be alphanumeric/hyphen/underscore (3-64 chars).")

        if not self.name or len(self.name) > 128:
            errors.append("Agent name must be 1 to 128 characters.")

        if not self.purpose or len(self.purpose) > 2048:
            errors.append("Agent purpose must be 1 to 2048 characters.")

        if self.timeout_seconds > 300:
            errors.append(f"Timeout {self.timeout_seconds}s exceeds maximum safety limit of 300s.")

        if self.max_steps > 25:
            errors.append(f"Max steps {self.max_steps} exceeds maximum safety limit of 25.")

        # Tool audit
        for tool_id in self.allowed_tools:
            clean = tool_id.strip().lower()
            if clean in PROHIBITED_TOOL_IDS:
                errors.append(f"Prohibited tool ID: '{tool_id}'.")
            clean_spaced = re.sub(r"[_\-.]", " ", clean)
            for pat in PROHIBITED_TOOL_PATTERNS:
                if re.search(pat, clean) or re.search(pat, clean_spaced):
                    errors.append(f"Tool '{tool_id}' matches prohibited pattern '{pat}'.")

        # Workspace audit
        clean_ws = self.workspace_scope.replace("/", "\\")
        if ".." in clean_ws or clean_ws.startswith("\\") or ":" in clean_ws:
            errors.append(f"Unsafe workspace relative path: '{self.workspace_scope}'. Must reside within dev_projects.")

        return len(errors) == 0, errors

    def to_agent_specification(self) -> AgentSpecification:
        """Converts to an authoritative NR-AI AgentSpecification."""
        is_valid, errors = self.validate()
        if not is_valid:
            raise ValueError(f"DeclarativeAgentDefinition validation failed: {'; '.join(errors)}")

        model_req = ModelRequirement(
            required_capabilities=[c.upper() for c in self.capabilities],
            preferred_model=self.preferred_model,
            fallback_models=self.fallback_models,
            max_context_tokens=self.max_context_tokens,
            latency_tier="normal",
            requires_live_api_verified=True,
        )

        tool_reqs = [
            ToolRequirement(
                tool_id=t,
                permission="READ_ONLY",
                risk_level=self.risk_level,
                required=True,
            )
            for t in self.allowed_tools
        ]

        safety_policy = SafetyPolicy(
            sandboxed_root=self.workspace_scope,
            max_steps=min(self.max_steps, 25),
            timeout_seconds=min(self.timeout_seconds, 300),
            allow_shell=False,  # Enforce 100% False invariant
            allow_network_listen=False,
            allow_credential_access=False,
            require_emergency_stop_check=True,
        )

        return AgentSpecification(
            agent_id=self.agent_id,
            name=self.name,
            version=self.version,
            description=self.description,
            purpose=self.purpose,
            capabilities=self.capabilities,
            model_requirement=model_req,
            tool_requirements=tool_reqs,
            safety_policy=safety_policy,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "purpose": self.purpose,
            "version": self.version,
            "description": self.description,
            "capabilities": self.capabilities,
            "preferred_model": self.preferred_model,
            "fallback_models": self.fallback_models,
            "max_context_tokens": self.max_context_tokens,
            "allowed_tools": self.allowed_tools,
            "memory_scopes": [m.value for m in self.memory_scopes],
            "workspace_scope": self.workspace_scope,
            "risk_level": self.risk_level,
            "verification_requirements": self.verification_requirements,
            "timeout_seconds": self.timeout_seconds,
            "max_steps": self.max_steps,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DeclarativeAgentDefinition":
        mem_scopes = []
        for s in data.get("memory_scopes", [MemoryScope.TASK.value, MemoryScope.AGENT.value]):
            try:
                mem_scopes.append(MemoryScope(str(s).upper()))
            except ValueError:
                mem_scopes.append(MemoryScope.AGENT)

        return cls(
            agent_id=str(data.get("agent_id", "")).strip(),
            name=str(data.get("name", "")).strip(),
            purpose=str(data.get("purpose", "")).strip(),
            version=str(data.get("version", "1.0.0")).strip(),
            description=str(data.get("description", "")).strip(),
            capabilities=list(data.get("capabilities", [])),
            preferred_model=str(data.get("preferred_model", "gpt-5.6-terra")).strip(),
            fallback_models=list(data.get("fallback_models", ["gpt-5.6-luna", "gemini-3.7-flash"])),
            max_context_tokens=int(data.get("max_context_tokens", 128000)),
            allowed_tools=list(data.get("allowed_tools", [])),
            memory_scopes=mem_scopes,
            workspace_scope=str(data.get("workspace_scope", "dev_projects")).strip(),
            risk_level=str(data.get("risk_level", "LOW")).strip(),
            verification_requirements=list(data.get("verification_requirements", ["syntax_check", "test_pass"])),
            timeout_seconds=int(data.get("timeout_seconds", 120)),
            max_steps=int(data.get("max_steps", 20)),
        )

    @classmethod
    def from_toml(cls, toml_content: str) -> "DeclarativeAgentDefinition":
        """Loads and parses a TOML operator definition."""
        parsed = tomllib.loads(toml_content)
        # Check if wrapped in [operator] or [template] or [agent]
        root = parsed.get("operator", parsed.get("template", parsed.get("agent", parsed)))
        # Map common OpenJarvis keys
        if "id" in root and "agent_id" not in root:
            root["agent_id"] = root["id"]
        if "tools" in root and "allowed_tools" not in root:
            root["allowed_tools"] = root["tools"]
        if "system_prompt_template" in root and "purpose" not in root:
            root["purpose"] = root["system_prompt_template"]
        return cls.from_dict(root)
