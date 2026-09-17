"""
NR-AI Agent Factory: Central Autonomous Subsystem Facade.

Coordinates capability gap analysis, agent specification, model and tool selection,
deterministic verification, and lifecycle registration.
"""

from dataclasses import dataclass, field
import logging
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from app.agent.factory.base import AgentExecutionResult
from app.agent.factory.builder import AgentBuilder, GeneratedAgent
from app.agent.factory.registry import AgentRegistry
from app.agent.factory.safety import AgentFactorySafetyGate
from app.agent.factory.specification import (
    AgentLifecycleState,
    AgentSpecification,
    ModelRequirement,
    SafetyPolicy,
    ToolRequirement,
)
from app.agent.factory.tools import ToolCatalog, ToolDefinition
from app.agent.factory.verifier import AgentVerificationReport, AgentVerifier
from app.agent.model_provider import OpenAIProvider, UnifiedModelProvider
from app.agent.model_router import ModelRouter
from app.config.model_config import (
    GEMINI_3_6_FLASH,
    GEMINI_FLASH_LATEST,
    GPT_5_6_SOL,
    GPT_6_ASTRA,
    ModelCapability,
    ModelConfig,
)
from app.memory.audit_logger import AuditLogger
from app.remote.remote_action_safety import EmergencyStopController

logger = logging.getLogger("NRAI.AgentFactory")


@dataclass
class CapabilityGapAnalysis:
    """Outcome of analyzing a user request against registered agent capabilities."""
    requirement: str
    existing_agent_capable: bool
    capable_agent_id: Optional[str] = None
    capable_agent_name: Optional[str] = None
    capability_gap_detected: bool = False
    missing_capabilities: List[str] = field(default_factory=list)
    suggested_agent_name: str = ""
    suggested_purpose: str = ""
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "requirement": self.requirement,
            "existing_agent_capable": self.existing_agent_capable,
            "capable_agent_id": self.capable_agent_id,
            "capable_agent_name": self.capable_agent_name,
            "capability_gap_detected": self.capability_gap_detected,
            "missing_capabilities": self.missing_capabilities,
            "suggested_agent_name": self.suggested_agent_name,
            "suggested_purpose": self.suggested_purpose,
            "reason": self.reason,
        }


@dataclass
class AgentRegistrationResult:
    """Outcome of the complete verification and registration process."""
    success: bool
    agent_id: str
    version: str
    lifecycle_state: AgentLifecycleState
    verification_report: AgentVerificationReport
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "agent_id": self.agent_id,
            "version": self.version,
            "lifecycle_state": self.lifecycle_state.value,
            "verification_report": self.verification_report.to_dict(),
            "message": self.message,
        }


class AgentFactory:
    """
    Central Coordinator for the NR-AI Self-Expanding Agent Subsystem.
    Allows NR-AI to safely inspect capability gaps and expand capabilities
    without unrestricted self-modifying code or dangerous model autonomy.
    """

    def __init__(
        self,
        registry: Optional[AgentRegistry] = None,
        tool_catalog: Optional[ToolCatalog] = None,
        safety_gate: Optional[AgentFactorySafetyGate] = None,
        model_router: Optional[ModelRouter] = None,
        audit_logger: Optional[AuditLogger] = None,
        emergency_controller: Optional[EmergencyStopController] = None,
        config: Optional[ModelConfig] = None,
    ):
        self.config = config or ModelConfig.from_env()
        self.registry = registry or AgentRegistry()
        self.tool_catalog = tool_catalog or ToolCatalog()
        self.safety_gate = safety_gate or AgentFactorySafetyGate(tool_catalog=self.tool_catalog)
        self.model_router = model_router or ModelRouter(config=self.config)
        self.audit_logger = audit_logger
        self.emergency_controller = emergency_controller or EmergencyStopController()

        self.builder = AgentBuilder(
            tool_catalog=self.tool_catalog,
            safety_gate=self.safety_gate,
            model_router=self.model_router,
            audit_logger=self.audit_logger,
            emergency_controller=self.emergency_controller,
        )

        self.verifier = AgentVerifier(
            tool_catalog=self.tool_catalog,
            safety_gate=self.safety_gate,
            builder=self.builder,
            config=self.config,
        )

    # -------------------------------------------------------------------------
    # A. Agent Discovery & Capability Gap Analysis
    # -------------------------------------------------------------------------

    def analyze_capability_gap(self, requirement: str) -> CapabilityGapAnalysis:
        """
        Determines whether any existing active agent can satisfy the requirement.
        Avoids redundant agent creation when an existing agent is capable.
        """
        req_clean = requirement.strip()
        req_low = req_clean.lower()

        # 1. Inspect existing active agents against requirement keywords
        active_agents = self.registry.list_agents(active_only=True)

        domain_mappings = [
            (("unity", "c#", "csharp", "gameobject", "monobehaviour", "unity engine"), "unity_autonomous_agent"),
            (("unreal", "c++", "cpp", "blueprint", "ubt", "uproperty", "unreal engine"), "unreal_autonomous_agent"),
            (("android", "gradle", "apk", "adb", "activity", "jetpack", "android studio"), "android_unified_agent"),
            (("visual studio", "msbuild", "solution", ".sln", ".csproj"), "vs_unified_agent"),
            (("click", "mouse", "keyboard", "window focus", "desktop screen", "gui action"), "computer_control_agent"),
            (("news", "breaking news", "rss", "headlines"), "universal_knowledge_engine"),
        ]

        for keywords, agent_id in domain_mappings:
            if any(k in req_low for k in keywords):
                agent = self.registry.get_agent(agent_id)
                if agent and agent.lifecycle_state == AgentLifecycleState.ACTIVE:
                    return CapabilityGapAnalysis(
                        requirement=req_clean,
                        existing_agent_capable=True,
                        capable_agent_id=agent.agent_id,
                        capable_agent_name=agent.name,
                        capability_gap_detected=False,
                        reason=f"Existing agent '{agent.name}' ({agent.agent_id}) satisfies requirement.",
                    )

        # 2. Capability gap detected: Determine missing capabilities
        missing = []
        if any(w in req_low for w in ("medical", "paper", "literature", "biomedical", "research", "pubmed", "arxiv")):
            missing.append("literature.biomedical_search")
        if any(w in req_low for w in ("ast", "syntax", "lint", "refactor", "python inspection")):
            missing.append("code.python_ast_analysis")
        if any(w in req_low for w in ("file", "inspect", "directory", "folder")):
            missing.append("file.bounded_inspection")

        if not missing:
            missing.append("specialized.domain_analysis")

        # Generate a clean candidate agent name and purpose
        slug = re.sub(r"[^\w\s]", "", req_clean).split()
        cand_name = " ".join([w.capitalize() for w in slug[:4]]) + " Agent"
        agent_slug = "_".join([w.lower() for w in slug[:3]]) + "_agent"

        return CapabilityGapAnalysis(
            requirement=req_clean,
            existing_agent_capable=False,
            capability_gap_detected=True,
            missing_capabilities=missing,
            suggested_agent_name=cand_name,
            suggested_purpose=f"Specialized agent created to address: {req_clean}",
            reason=f"No existing active agent possesses capabilities: {missing}",
        )

    # -------------------------------------------------------------------------
    # B. Agent Design & Specification Generation
    # -------------------------------------------------------------------------

    def create_agent_specification(
        self,
        requirement: str,
        preferred_model: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> AgentSpecification:
        """
        Creates a bounded AgentSpecification from a natural language requirement.
        Sanitizes input through the AgentFactorySafetyGate.
        """
        # Safety Gate Audit on the requirement text
        is_safe, sanitized_req, denial_reason = self.safety_gate.sanitize_user_or_model_requirement(requirement)
        if not is_safe:
            raise ValueError(f"AgentSpecification creation rejected: {denial_reason}")

        gap = self.analyze_capability_gap(sanitized_req)

        # Generate deterministic agent ID
        raw_id = agent_id or f"gen_{re.sub(r'[^a-zA-Z0-9_]', '_', gap.suggested_agent_name.lower())[:32]}_{int(time.time()) % 10000}"
        raw_id = re.sub(r"_+", "_", raw_id).strip("_")

        # Determine tools needed based on requirement
        req_low = sanitized_req.lower()
        tool_reqs = []

        if any(w in req_low for w in ("search", "find", "research", "paper", "literature", "lookup")):
            tool_reqs.append(ToolRequirement("research.lookup", permission="RESEARCH_READ", risk_level="LOW", purpose="Scholarly literature and verified knowledge search"))

        if any(w in req_low for w in ("file", "inspect", "directory", "list", "read")):
            tool_reqs.append(ToolRequirement("file.read_bounded", permission="READ_ONLY", risk_level="LOW", purpose="Sandboxed text file reading"))
            tool_reqs.append(ToolRequirement("file.list_directory", permission="READ_ONLY", risk_level="LOW", purpose="Sandboxed directory inspection"))

        if any(w in req_low for w in ("ast", "parse", "syntax", "python")):
            tool_reqs.append(ToolRequirement("ast.parse_python", permission="READ_ONLY", risk_level="LOW", purpose="Static AST code structure parsing"))

        # Model requirement & selection
        selected_model = preferred_model or GEMINI_3_6_FLASH
        model_req = ModelRequirement(
            required_capabilities=["REASONING", "RESEARCH"],
            preferred_model=selected_model,
            fallback_models=[GEMINI_FLASH_LATEST, GPT_5_6_SOL],
            requires_live_api_verified=True,
        )

        spec = AgentSpecification(
            agent_id=raw_id,
            name=gap.suggested_agent_name or "Generated Specialized Agent",
            version="1.0.0",
            description=f"Specialized autonomous agent for: {sanitized_req}",
            purpose=gap.suggested_purpose or sanitized_req,
            capabilities=gap.missing_capabilities or ["specialized.task_execution"],
            model_requirement=model_req,
            tool_requirements=tool_reqs,
            safety_policy=SafetyPolicy(sandboxed_root="dev_projects", max_steps=20, timeout_seconds=120),
            lifecycle_state=AgentLifecycleState.DESIGNING,
            provenance={"created_by": "AgentFactory", "source_requirement": sanitized_req, "created_at": time.time()},
        )

        spec.lifecycle_state = AgentLifecycleState.PROPOSED
        return spec

    # -------------------------------------------------------------------------
    # C. Verification & Registration Pipeline
    # -------------------------------------------------------------------------

    def verify_and_register(
        self,
        spec: AgentSpecification,
        auto_activate: bool = False,
    ) -> AgentRegistrationResult:
        """
        Executes the complete verification pipeline.
        Only registers the agent if ALL verification stages pass.
        """
        logger.info(f"Initiating verification for agent '{spec.agent_id}'...")

        # Run verification
        report = self.verifier.verify_agent_specification(spec)

        if not report.passed:
            spec.lifecycle_state = AgentLifecycleState.FAILED
            msg = f"Verification failed with {len(report.objections)} objections: {'; '.join(report.objections)}"
            logger.warning(f"Agent '{spec.agent_id}' failed verification: {msg}")
            return AgentRegistrationResult(
                success=False,
                agent_id=spec.agent_id,
                version=spec.version,
                lifecycle_state=AgentLifecycleState.FAILED,
                verification_report=report,
                message=msg,
            )

        # Verification passed -> Register in AgentRegistry
        reg_ok, reg_msg = self.registry.register_agent(spec)
        if not reg_ok:
            spec.lifecycle_state = AgentLifecycleState.FAILED
            return AgentRegistrationResult(
                success=False,
                agent_id=spec.agent_id,
                version=spec.version,
                lifecycle_state=AgentLifecycleState.FAILED,
                verification_report=report,
                message=f"Registration failed: {reg_msg}",
            )

        # Optional activation
        if auto_activate:
            act_ok, act_msg = self.registry.activate_agent(spec.agent_id)
            if act_ok:
                spec.lifecycle_state = AgentLifecycleState.ACTIVE
                reg_msg += f" {act_msg}"

        return AgentRegistrationResult(
            success=True,
            agent_id=spec.agent_id,
            version=spec.version,
            lifecycle_state=spec.lifecycle_state,
            verification_report=report,
            message=reg_msg,
        )

    # -------------------------------------------------------------------------
    # D. Lifecycle & Execution Management
    # -------------------------------------------------------------------------

    def build_executable_agent(self, agent_id: str) -> GeneratedAgent:
        """Builds an executable agent instance from the registry."""
        spec = self.registry.get_agent(agent_id)
        if not spec:
            raise ValueError(f"Agent '{agent_id}' not found in registry.")

        if spec.lifecycle_state not in (AgentLifecycleState.REGISTERED, AgentLifecycleState.ACTIVE):
            raise ValueError(f"Agent '{agent_id}' is in '{spec.lifecycle_state.value}' state and cannot be executed.")

        return self.builder.build_agent(spec)

    def execute_agent(self, agent_id: str, task: str, context: Optional[Dict[str, Any]] = None) -> AgentExecutionResult:
        """Loads and executes an active agent safely."""
        agent = self.build_executable_agent(agent_id)
        return agent.run(task, context=context)

    def list_agents(self, active_only: bool = False) -> List[AgentSpecification]:
        return self.registry.list_agents(active_only=active_only)

    def activate_agent(self, agent_id: str) -> Tuple[bool, str]:
        return self.registry.activate_agent(agent_id)

    def suspend_agent(self, agent_id: str, reason: str = "") -> Tuple[bool, str]:
        return self.registry.suspend_agent(agent_id, reason=reason)

    def retire_agent(self, agent_id: str, reason: str = "") -> Tuple[bool, str]:
        return self.registry.retire_agent(agent_id, reason=reason)

    def rollback_agent(self, agent_id: str, target_version: Optional[str] = None) -> Tuple[bool, str]:
        return self.registry.rollback_agent(agent_id, target_version=target_version)
