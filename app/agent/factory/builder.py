"""
NR-AI Agent Factory: Agent Builder.

Constructs executable, bounded agent instances from validated specifications.
Uses structured class composition and dependency injection.
Strictly forbids dynamic code compilation (eval/exec) of model-generated strings.
"""

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from app.agent.factory.base import AgentExecutionResult, BaseFactoryAgent
from app.agent.factory.safety import AgentFactorySafetyGate
from app.agent.factory.specification import (
    AgentLifecycleState,
    AgentSpecification,
)
from app.agent.factory.tools import ToolCatalog, ToolDefinition
from app.agent.model_router import ModelRouter
from app.memory.audit_logger import AuditLogger
from app.remote.permissions import ModelIsolationGate
from app.remote.remote_action_safety import EmergencyStopController

logger = logging.getLogger("NRAI.AgentBuilder")


class GeneratedAgent(BaseFactoryAgent):
    """
    Concrete bounded agent instantiated by AgentBuilder.
    Coordinates structured model reasoning and allowlisted tool execution.
    """

    def run(self, task: str, context: Optional[Dict[str, Any]] = None) -> AgentExecutionResult:
        t0 = time.time()
        ctx = context or {}
        logs: List[str] = []

        def _log(msg: str):
            ts = time.strftime("%H:%M:%S")
            logs.append(f"[{ts}] {msg}")
            logger.info(f"[{self.agent_id}] {msg}")

        _log(f"Starting execution of task: '{task}'")
        self._log_audit("task_started", {"task": task})

        # 1. Emergency Stop Check
        if self._check_emergency_stop():
            _log("Execution halted immediately: Emergency Stop is active.")
            self._log_audit("task_halted", {"reason": "EMERGENCY_STOP_ACTIVE"}, status="blocked")
            return AgentExecutionResult(
                success=False,
                agent_id=self.agent_id,
                output="Execution blocked: Emergency Stop active.",
                steps_executed=0,
                error="EMERGENCY_STOP_ACTIVE",
                logs=logs,
                duration_ms=(time.time() - t0) * 1000,
                emergency_stopped=True,
            )

        max_steps = self.spec.safety_policy.max_steps
        steps_executed = 0
        current_state_summary = f"Task: {task}"

        # 2. Tool execution loop
        tool_results: Dict[str, Any] = {}

        # If agent has literature/knowledge research capability, execute research first
        if "research.lookup" in self.tools and any(w in task.lower() for w in ("search", "find", "research", "paper", "literature", "lookup")):
            if steps_executed < max_steps:
                steps_executed += 1
                _log("Executing tool 'research.lookup'...")
                r_res = self.execute_tool("research.lookup", {"query": task})
                tool_results["research"] = r_res
                current_state_summary += f"\nResearch Results: {r_res.get('display_text', '')[:400]}"

        # If agent has directory/file inspection capability and context gives a path
        if "file.list_directory" in self.tools and ctx.get("sub_dir"):
            if steps_executed < max_steps:
                steps_executed += 1
                _log(f"Executing tool 'file.list_directory' for sub_dir: '{ctx.get('sub_dir')}'...")
                f_res = self.execute_tool("file.list_directory", {"sub_dir": ctx.get("sub_dir")})
                tool_results["directory_list"] = f_res

        # If agent has AST code parsing capability and context has code
        if "ast.parse_python" in self.tools and ctx.get("code"):
            if steps_executed < max_steps:
                steps_executed += 1
                _log("Executing tool 'ast.parse_python' on provided code...")
                ast_res = self.execute_tool("ast.parse_python", {"code": ctx.get("code")})
                tool_results["ast"] = ast_res
                current_state_summary += f"\nAST Parsed: valid={ast_res.get('syntax_valid')}, classes={ast_res.get('classes')}, functions={ast_res.get('functions')}"

        # 3. Model Synthesis / Reasoning Step
        selected_model = self.spec.model_requirement.preferred_model
        _log(f"Synthesizing results via model router (preferred: {selected_model})...")

        system_prompt = (
            f"You are '{self.name}', a specialized autonomous agent in the NR-AI ecosystem.\n"
            f"Purpose: {self.spec.purpose}\n"
            f"Capabilities: {', '.join(self.spec.capabilities)}\n"
            "Provide a factual, verified, bounded summary addressing the task. Never invent unverified facts."
        )

        model_prompt = (
            f"Task: {task}\n\n"
            f"Context and Tool Findings:\n{current_state_summary}\n\n"
            "Please deliver the specialized analysis."
        )

        ai_response = ""
        if self.model_router:
            try:
                res = self.model_router.execute(
                    prompt=model_prompt,
                    system_prompt=system_prompt,
                    explicit_model=selected_model,
                    task_type="normal",
                )
                if res.get("success") and res.get("content"):
                    ai_response = res["content"].strip()
                else:
                    ai_response = f"Model execution fallback: Completed task using deterministic tool findings.\n{current_state_summary}"
            except Exception as e:
                _log(f"Model call failed, using deterministic tool synthesis: {e}")
                ai_response = f"Analysis completed via verified tools:\n{current_state_summary}"
        else:
            ai_response = f"Analysis completed via verified tools:\n{current_state_summary}"

        steps_executed += 1
        elapsed_ms = (time.time() - t0) * 1000
        _log(f"Agent '{self.agent_id}' completed execution in {steps_executed} steps ({elapsed_ms:.1f}ms).")

        self._log_audit("task_completed", {
            "steps": steps_executed,
            "duration_ms": elapsed_ms,
            "tool_calls": list(tool_results.keys()),
        })

        return AgentExecutionResult(
            success=True,
            agent_id=self.agent_id,
            output=ai_response,
            steps_executed=steps_executed,
            data={"tool_results": tool_results, "context": ctx},
            logs=logs,
            duration_ms=elapsed_ms,
            emergency_stopped=False,
        )


class AgentBuilder:
    """
    Constructs and binds executable GeneratedAgent instances from specifications.
    Performs deterministic safety auditing, tool binding, and dependency injection.
    """

    def __init__(
        self,
        tool_catalog: Optional[ToolCatalog] = None,
        safety_gate: Optional[AgentFactorySafetyGate] = None,
        model_router: Optional[ModelRouter] = None,
        audit_logger: Optional[AuditLogger] = None,
        emergency_controller: Optional[EmergencyStopController] = None,
    ):
        self.tool_catalog = tool_catalog or ToolCatalog()
        self.safety_gate = safety_gate or AgentFactorySafetyGate(tool_catalog=self.tool_catalog)
        self.model_router = model_router or ModelRouter()
        self.audit_logger = audit_logger
        self.emergency_controller = emergency_controller or EmergencyStopController()

    def build_agent(self, spec: AgentSpecification) -> GeneratedAgent:
        """
        Builds a concrete GeneratedAgent from a specification.
        Raises ValueError if specification violates safety or contains unapproved tools.
        """
        spec.lifecycle_state = AgentLifecycleState.BUILDING

        # 1. Safety audit
        is_safe, violations = self.safety_gate.audit_specification(spec)
        if not is_safe:
            spec.lifecycle_state = AgentLifecycleState.FAILED
            raise ValueError(f"AgentBuilder rejected specification for '{spec.agent_id}': {'; '.join(violations)}")

        # 2. Tool Resolution & Binding
        bound_tools: Dict[str, ToolDefinition] = {}
        for req in spec.tool_requirements:
            tool = self.tool_catalog.get_tool(req.tool_id)
            if not tool:
                spec.lifecycle_state = AgentLifecycleState.FAILED
                raise ValueError(f"Requested tool '{req.tool_id}' not found in ToolCatalog.")
            bound_tools[req.tool_id] = tool

        spec.lifecycle_state = AgentLifecycleState.VALIDATING

        # 3. Class instantiation with dependency injection
        agent = GeneratedAgent(
            specification=spec,
            tools=bound_tools,
            model_router=self.model_router,
            emergency_controller=self.emergency_controller,
            audit_logger=self.audit_logger,
        )

        if self.audit_logger:
            self.audit_logger.log_event(
                event_type="agent_factory.agent_built",
                details={
                    "agent_id": spec.agent_id,
                    "version": spec.version,
                    "tools": list(bound_tools.keys()),
                    "preferred_model": spec.model_requirement.preferred_model,
                },
            )

        return agent
