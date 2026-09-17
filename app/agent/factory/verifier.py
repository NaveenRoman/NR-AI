"""
NR-AI Agent Factory: Agent Verifier & Multi-Stage Validation Pipeline.

Executes deterministic multi-stage verification before any generated agent can be registered:
1. Specification Schema Validation
2. Model Availability & Honesty Verification
3. Tool Allowlist & Risk Audit
4. Safety Gate Invariant Audit
5. Sandboxed Smoke Execution Test
6. Adversarial Boundary & Emergency Stop Verification
7. Final Consensus Gate

An agent CANNOT become APPROVED or ACTIVE unless every stage passes.
"""

from dataclasses import dataclass, field
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from app.agent.factory.base import AgentExecutionResult
from app.agent.factory.builder import AgentBuilder, GeneratedAgent
from app.agent.factory.safety import AgentFactorySafetyGate
from app.agent.factory.specification import (
    AgentLifecycleState,
    AgentSpecification,
)
from app.agent.factory.tools import ToolCatalog, ToolRiskLevel
from app.agent.model_provider import OpenAIProvider, UnifiedModelProvider
from app.config.model_config import GPT_6_ASTRA, MODEL_REGISTRY, ModelConfig
from app.remote.remote_action_safety import EmergencyStopController

logger = logging.getLogger("NRAI.AgentVerifier")


@dataclass
class StageResult:
    stage_name: str
    passed: bool
    details: str
    duration_ms: float = 0.0


@dataclass
class AgentVerificationReport:
    """Comprehensive multi-stage verification report for an agent specification."""
    agent_id: str
    passed: bool
    decision: str  # "APPROVED" or "REJECTED"
    recommended_state: AgentLifecycleState
    stage_results: List[StageResult] = field(default_factory=list)
    objections: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "passed": self.passed,
            "decision": self.decision,
            "recommended_state": self.recommended_state.value,
            "stage_results": [
                {"stage": s.stage_name, "passed": s.passed, "details": s.details, "duration_ms": s.duration_ms}
                for s in self.stage_results
            ],
            "objections": self.objections,
            "timestamp": self.timestamp,
        }


class AgentVerifier:
    """
    Independent multi-stage verifier ensuring strict quality, safety, and operational guarantees.
    """

    def __init__(
        self,
        tool_catalog: Optional[ToolCatalog] = None,
        safety_gate: Optional[AgentFactorySafetyGate] = None,
        builder: Optional[AgentBuilder] = None,
        config: Optional[ModelConfig] = None,
    ):
        self.tool_catalog = tool_catalog or ToolCatalog()
        self.safety_gate = safety_gate or AgentFactorySafetyGate(tool_catalog=self.tool_catalog)
        self.builder = builder or AgentBuilder(tool_catalog=self.tool_catalog, safety_gate=self.safety_gate)
        self.config = config or ModelConfig.from_env()
        self.openai_provider = OpenAIProvider(config=self.config)

    def verify_agent_specification(self, spec: AgentSpecification) -> AgentVerificationReport:
        """
        Executes all 7 verification stages on the given specification and agent instance.
        """
        spec.lifecycle_state = AgentLifecycleState.VERIFICATION
        stages: List[StageResult] = []
        objections: List[str] = []

        # ---------------------------------------------------------------------
        # Stage 1: Specification Schema & Bounds Validation
        # ---------------------------------------------------------------------
        t0 = time.time()
        bounds_ok, bound_errs = spec.validate_bounds()
        dur = (time.time() - t0) * 1000
        if bounds_ok:
            stages.append(StageResult("1_SchemaValidation", True, "All structural and length bounds satisfied.", dur))
        else:
            stages.append(StageResult("1_SchemaValidation", False, f"Schema bounds failed: {bound_errs}", dur))
            objections.extend(bound_errs)

        # ---------------------------------------------------------------------
        # Stage 2: Model Availability & Honesty Verification
        # ---------------------------------------------------------------------
        t0 = time.time()
        model_name = spec.model_requirement.preferred_model.strip()
        model_ok = True
        model_details = f"Model '{model_name}' evaluated."

        known_model_ids = {m.lower() for m in MODEL_REGISTRY.keys()}
        if model_name.lower() not in known_model_ids:
            model_ok = False
            reason = f"Model '{model_name}' is not recognized in NR-AI MODEL_REGISTRY."
            objections.append(reason)
            model_details = reason
        elif GPT_6_ASTRA in model_name.lower():
            # Strict honesty rule: if preferred model is GPT-6 Astra, verify live API
            live_stat = self.openai_provider.verify_live_api(GPT_6_ASTRA)
            if not live_stat.get("live_api_verified"):
                if spec.model_requirement.requires_live_api_verified:
                    model_ok = False
                    reason = f"Model '{GPT_6_ASTRA}' is configured but unverified on live API ({live_stat.get('status')})."
                    objections.append(reason)
                    model_details = reason
                else:
                    model_details = f"Model '{GPT_6_ASTRA}' is unverified, but live verification was not strictly required."
            else:
                model_details = f"Model '{GPT_6_ASTRA}' verified live (HTTP 200)."
        else:
            model_details = f"Standard supported model '{model_name}' accepted from registry."

        dur = (time.time() - t0) * 1000
        stages.append(StageResult("2_ModelVerification", model_ok, model_details, dur))

        # ---------------------------------------------------------------------
        # Stage 3: Tool Allowlist & Risk Classification
        # ---------------------------------------------------------------------
        t0 = time.time()
        tools_ok = True
        tool_errs = []
        for req in spec.tool_requirements:
            prohibited, reason = self.tool_catalog.is_tool_prohibited(req.tool_id)
            if prohibited:
                tools_ok = False
                tool_errs.append(f"Prohibited tool: {req.tool_id} ({reason})")
            elif not self.tool_catalog.is_tool_allowed(req.tool_id):
                tools_ok = False
                tool_errs.append(f"Tool not in catalog: {req.tool_id}")
            else:
                td = self.tool_catalog.get_tool(req.tool_id)
                if td and td.risk_level == ToolRiskLevel.HIGH and req.risk_level != "HIGH":
                    tools_ok = False
                    tool_errs.append(f"Risk mismatch on '{req.tool_id}': Tool is HIGH risk.")

        dur = (time.time() - t0) * 1000
        if tools_ok:
            stages.append(StageResult("3_ToolAllowlist", True, f"All {len(spec.tool_requirements)} tools allowlisted.", dur))
        else:
            stages.append(StageResult("3_ToolAllowlist", False, f"Tool audit failed: {tool_errs}", dur))
            objections.extend(tool_errs)

        # ---------------------------------------------------------------------
        # Stage 4: Safety Gate Audit
        # ---------------------------------------------------------------------
        t0 = time.time()
        safe_ok, safety_violations = self.safety_gate.audit_specification(spec)
        dur = (time.time() - t0) * 1000
        if safe_ok:
            stages.append(StageResult("4_SafetyGateAudit", True, "Zero safety violations detected.", dur))
        else:
            stages.append(StageResult("4_SafetyGateAudit", False, f"Safety gate rejected spec: {safety_violations}", dur))
            objections.extend(safety_violations)

        # ---------------------------------------------------------------------
        # Stage 5: Sandboxed Smoke Execution Test
        # ---------------------------------------------------------------------
        t0 = time.time()
        smoke_ok = True
        smoke_details = ""
        agent_instance: Optional[GeneratedAgent] = None

        if len(objections) == 0:
            try:
                agent_instance = self.builder.build_agent(spec)
                smoke_res = agent_instance.run("Run diagnostic smoke verification test.")
                if not smoke_res.success:
                    smoke_ok = False
                    reason = f"Smoke test returned failure: {smoke_res.error}"
                    smoke_details = reason
                    objections.append(reason)
                else:
                    smoke_details = f"Smoke test passed in {smoke_res.steps_executed} steps."
            except Exception as e:
                smoke_ok = False
                reason = f"Agent building or smoke execution crashed: {str(e)}"
                smoke_details = reason
                objections.append(reason)
        else:
            smoke_ok = False
            smoke_details = "Skipped due to earlier validation failures."

        dur = (time.time() - t0) * 1000
        stages.append(StageResult("5_SmokeExecutionTest", smoke_ok, smoke_details, dur))

        # ---------------------------------------------------------------------
        # Stage 6: Adversarial Emergency Stop Verification
        # ---------------------------------------------------------------------
        t0 = time.time()
        estop_ok = True
        estop_details = ""

        if agent_instance:
            try:
                # Test that Emergency Stop immediately halts agent execution
                test_estop_controller = EmergencyStopController()
                if hasattr(test_estop_controller, "trigger"):
                    test_estop_controller.trigger(triggered_by="VERIFIER", reason="Verification E-Stop Test")
                elif hasattr(test_estop_controller, "trigger_emergency_stop"):
                    test_estop_controller.trigger_emergency_stop("Verification E-Stop Test")
                agent_instance.emergency_controller = test_estop_controller

                halt_res = agent_instance.run("Execute critical task under E-Stop.")
                if not halt_res.emergency_stopped or halt_res.success:
                    estop_ok = False
                    reason = "Agent failed to halt when Emergency Stop was active!"
                    estop_details = reason
                    objections.append(reason)
                else:
                    estop_details = "Agent correctly and immediately halted when Emergency Stop was triggered."

                # Reset to normal controller
                agent_instance.emergency_controller = EmergencyStopController()
            except Exception as e:
                estop_ok = False
                estop_details = f"E-Stop test error: {e}"
                objections.append(estop_details)
        else:
            estop_ok = False
            estop_details = "Skipped due to build failure."

        dur = (time.time() - t0) * 1000
        stages.append(StageResult("6_AdversarialEStopTest", estop_ok, estop_details, dur))

        # ---------------------------------------------------------------------
        # Stage 7: Final Decision
        # ---------------------------------------------------------------------
        all_passed = all(s.passed for s in stages)
        final_decision = "APPROVED" if all_passed else "REJECTED"
        final_state = AgentLifecycleState.APPROVED if all_passed else AgentLifecycleState.FAILED
        spec.lifecycle_state = final_state

        report = AgentVerificationReport(
            agent_id=spec.agent_id,
            passed=all_passed,
            decision=final_decision,
            recommended_state=final_state,
            stage_results=stages,
            objections=objections,
        )

        logger.info(f"Verification for '{spec.agent_id}' completed: decision={final_decision}, objections={len(objections)}")
        return report
