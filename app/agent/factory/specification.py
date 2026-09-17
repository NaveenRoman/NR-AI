"""
NR-AI Agent Factory: Structured Agent Specification & Lifecycle Contracts.

Defines the formal schema, bounded constraints, and lifecycle states for all
dynamically generated agents in NR-AI. Models are strictly advisory; all specifications
must pass deterministic validation before building or registration.
"""

from dataclasses import asdict, dataclass, field
from enum import Enum
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple


class AgentLifecycleState(str, Enum):
    """Deterministic lifecycle states of an agent within the factory."""
    PROPOSED = "PROPOSED"
    DESIGNING = "DESIGNING"
    MODEL_SELECTED = "MODEL_SELECTED"
    TOOLS_SELECTED = "TOOLS_SELECTED"
    BUILDING = "BUILDING"
    VALIDATING = "VALIDATING"
    SAFETY_REVIEW = "SAFETY_REVIEW"
    TESTING = "TESTING"
    VERIFICATION = "VERIFICATION"
    APPROVED = "APPROVED"
    REGISTERED = "REGISTERED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    FAILED = "FAILED"
    RETIRED = "RETIRED"


MAX_AGENT_ID_LENGTH = 64
MAX_NAME_LENGTH = 128
MAX_DESCRIPTION_LENGTH = 1024
MAX_PURPOSE_LENGTH = 2048
MAX_CAPABILITIES_COUNT = 32
MAX_TOOL_REQUIREMENTS = 20
MAX_SPEC_JSON_BYTES = 65536  # 64 KB max specification payload to prevent DoS
MAX_STEPS_LIMIT = 25
MAX_TIMEOUT_LIMIT_S = 300
MAX_RETRY_LIMIT = 3


@dataclass
class ToolRequirement:
    """Requirement for a specific tool allowlisted by the ToolCatalog."""
    tool_id: str
    permission: str = "READ_ONLY"
    risk_level: str = "LOW"
    required: bool = True
    purpose: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolRequirement":
        return cls(
            tool_id=str(data.get("tool_id", "")).strip(),
            permission=str(data.get("permission", "READ_ONLY")).strip(),
            risk_level=str(data.get("risk_level", "LOW")).strip(),
            required=bool(data.get("required", True)),
            purpose=str(data.get("purpose", "")).strip(),
        )


@dataclass
class ModelRequirement:
    """Requirement for cognitive LLM capabilities and preferred model tier."""
    required_capabilities: List[str] = field(default_factory=list)
    preferred_model: str = "gemini-3.6-flash"
    fallback_models: List[str] = field(default_factory=list)
    max_context_tokens: int = 128000
    latency_tier: str = "normal"  # "fast", "normal", "slow"
    requires_live_api_verified: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ModelRequirement":
        return cls(
            required_capabilities=[str(c).upper().strip() for c in data.get("required_capabilities", [])],
            preferred_model=str(data.get("preferred_model", "gemini-3.6-flash")).strip(),
            fallback_models=[str(m).strip() for m in data.get("fallback_models", [])],
            max_context_tokens=int(data.get("max_context_tokens", 128000)),
            latency_tier=str(data.get("latency_tier", "normal")).strip(),
            requires_live_api_verified=bool(data.get("requires_live_api_verified", True)),
        )


@dataclass
class SafetyPolicy:
    """Deterministic safety bounds inherited by the generated agent."""
    sandboxed_root: str = "dev_projects"
    max_steps: int = 20
    timeout_seconds: int = 120
    retry_limit: int = 2
    allow_shell: bool = False
    allow_network_listen: bool = False
    allow_credential_access: bool = False
    require_emergency_stop_check: bool = True
    prohibited_tools: List[str] = field(default_factory=lambda: [
        "shell.exec", "system.cmd", "powershell.exec", "network.listen",
        "credential.read", "os.raw_exec", "eval", "exec"
    ])

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SafetyPolicy":
        # Critical safety invariant: Never allow user or model input to set allow_shell=True
        return cls(
            sandboxed_root=str(data.get("sandboxed_root", "dev_projects")).strip(),
            max_steps=min(int(data.get("max_steps", 20)), MAX_STEPS_LIMIT),
            timeout_seconds=min(int(data.get("timeout_seconds", 120)), MAX_TIMEOUT_LIMIT_S),
            retry_limit=min(int(data.get("retry_limit", 2)), MAX_RETRY_LIMIT),
            allow_shell=False,  # Enforce 100% False invariant
            allow_network_listen=False,
            allow_credential_access=False,
            require_emergency_stop_check=True,
            prohibited_tools=list(data.get("prohibited_tools", [
                "shell.exec", "system.cmd", "powershell.exec", "network.listen",
                "credential.read", "os.raw_exec", "eval", "exec"
            ])),
        )


@dataclass
class AgentSpecification:
    """
    Formal, bounded specification defining a specialized agent.
    Must be validated statically and cryptographically audited before generation.
    """
    agent_id: str
    name: str
    version: str = "1.0.0"
    description: str = ""
    purpose: str = ""
    capabilities: List[str] = field(default_factory=list)
    model_requirement: ModelRequirement = field(default_factory=ModelRequirement)
    tool_requirements: List[ToolRequirement] = field(default_factory=list)
    input_schema: Dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {"task": {"type": "string"}}})
    output_schema: Dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {"success": {"type": "boolean"}, "result": {"type": "string"}}})
    safety_policy: SafetyPolicy = field(default_factory=SafetyPolicy)
    lifecycle_state: AgentLifecycleState = AgentLifecycleState.PROPOSED
    verification_requirements: List[str] = field(default_factory=list)
    test_requirements: List[str] = field(default_factory=list)
    provenance: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def validate_bounds(self) -> Tuple[bool, List[str]]:
        """
        Statically checks structural and safety bounds of this specification.
        Returns (is_valid, list_of_errors).
        """
        errors = []

        # 1. Identifier checks
        if not self.agent_id or not re.match(r"^[a-zA-Z0-9_-]{3,64}$", self.agent_id):
            errors.append(f"Invalid agent_id '{self.agent_id}': Must match ^[a-zA-Z0-9_-]{{3,64}}$")

        if not self.name or len(self.name) > MAX_NAME_LENGTH:
            errors.append(f"Name exceeds max length ({MAX_NAME_LENGTH}) or is empty.")

        if len(self.description) > MAX_DESCRIPTION_LENGTH:
            errors.append(f"Description exceeds max length ({MAX_DESCRIPTION_LENGTH}).")

        if len(self.purpose) > MAX_PURPOSE_LENGTH:
            errors.append(f"Purpose exceeds max length ({MAX_PURPOSE_LENGTH}).")

        if len(self.capabilities) > MAX_CAPABILITIES_COUNT:
            errors.append(f"Too many capabilities ({len(self.capabilities)} > {MAX_CAPABILITIES_COUNT}).")

        if len(self.tool_requirements) > MAX_TOOL_REQUIREMENTS:
            errors.append(f"Too many tool requirements ({len(self.tool_requirements)} > {MAX_TOOL_REQUIREMENTS}).")

        # 2. Safety bounds
        if self.safety_policy.allow_shell:
            errors.append("Safety violation: allow_shell is True. Shell execution is strictly forbidden.")

        if self.safety_policy.allow_network_listen:
            errors.append("Safety violation: allow_network_listen is True.")

        if self.safety_policy.allow_credential_access:
            errors.append("Safety violation: allow_credential_access is True.")

        if self.safety_policy.max_steps > MAX_STEPS_LIMIT:
            errors.append(f"Safety violation: max_steps ({self.safety_policy.max_steps}) exceeds limit ({MAX_STEPS_LIMIT}).")

        if self.safety_policy.timeout_seconds > MAX_TIMEOUT_LIMIT_S:
            errors.append(f"Safety violation: timeout_seconds ({self.safety_policy.timeout_seconds}) exceeds limit ({MAX_TIMEOUT_LIMIT_S}).")

        # 3. Prohibited tools check
        prohibited_set = set(self.safety_policy.prohibited_tools)
        for req in self.tool_requirements:
            if req.tool_id in prohibited_set or any(p in req.tool_id.lower() for p in ("shell", "cmd", "powershell", "exec", "eval", "credential")):
                errors.append(f"Safety violation: Prohibited tool requested '{req.tool_id}'.")

        return len(errors) == 0, errors

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the specification to a dictionary."""
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "purpose": self.purpose,
            "capabilities": self.capabilities,
            "model_requirement": self.model_requirement.to_dict(),
            "tool_requirements": [t.to_dict() for t in self.tool_requirements],
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "safety_policy": self.safety_policy.to_dict(),
            "lifecycle_state": self.lifecycle_state.value,
            "verification_requirements": self.verification_requirements,
            "test_requirements": self.test_requirements,
            "provenance": self.provenance,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentSpecification":
        """Reconstructs an AgentSpecification with defensive sanitization."""
        # Check overall payload size
        import json
        raw_bytes = len(json.dumps(data).encode("utf-8"))
        if raw_bytes > MAX_SPEC_JSON_BYTES:
            raise ValueError(f"Specification JSON exceeds maximum allowed size ({raw_bytes} > {MAX_SPEC_JSON_BYTES} bytes)")

        agent_id = str(data.get("agent_id", "")).strip()
        name = str(data.get("name", "")).strip()
        version = str(data.get("version", "1.0.0")).strip()
        description = str(data.get("description", "")).strip()
        purpose = str(data.get("purpose", "")).strip()
        capabilities = [str(c).strip() for c in data.get("capabilities", [])]

        model_req = ModelRequirement.from_dict(data.get("model_requirement", {}))
        tool_reqs = [ToolRequirement.from_dict(t) for t in data.get("tool_requirements", [])]
        safety_pol = SafetyPolicy.from_dict(data.get("safety_policy", {}))

        state_str = str(data.get("lifecycle_state", AgentLifecycleState.PROPOSED.value)).strip().upper()
        lifecycle_state = getattr(AgentLifecycleState, state_str, AgentLifecycleState.PROPOSED)

        return cls(
            agent_id=agent_id,
            name=name,
            version=version,
            description=description,
            purpose=purpose,
            capabilities=capabilities,
            model_requirement=model_req,
            tool_requirements=tool_reqs,
            input_schema=data.get("input_schema", {"type": "object"}),
            output_schema=data.get("output_schema", {"type": "object"}),
            safety_policy=safety_pol,
            lifecycle_state=lifecycle_state,
            verification_requirements=[str(v) for v in data.get("verification_requirements", [])],
            test_requirements=[str(t) for t in data.get("test_requirements", [])],
            provenance=data.get("provenance", {}),
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
        )
