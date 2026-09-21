"""
NR-AI Controlled MCP Client.
Phase 2 Foundation: Enforces strict server allowlisting, model isolation,
safety gating, output sanitization, and structured audit logs.
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import logging
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from app.agent.factory.tools import ToolPermission, ToolRiskLevel
from app.mcp.adapter import (
    MCPAdapter,
    MCPExecutionResult,
    MCPSafetyGate,
    MCPToolSpec,
)
from app.mcp.config import (
    MCPServerConfig,
    MCPServerRegistry,
    ServerTrustStatus,
    global_mcp_registry,
)
from app.remote.emergency import EmergencyStopController
from app.security.guardrails import PromptGuardrails
from app.task.checkpoint_store import sanitize_secrets

logger = logging.getLogger("NRAI.MCP.Client")


class ControlledMCPClient:
    """
    Safe MCP Client mediator enforcing the full verification pipeline:
    Server Registration -> Permission Check -> ModelIsolationGate -> Safety Gate ->
    Tool Allowlist -> Execution -> Result Sanitization -> Audit Log.
    """

    def __init__(
        self,
        registry: Optional[MCPServerRegistry] = None,
        adapter: Optional[MCPAdapter] = None,
        emergency_controller: Optional[EmergencyStopController] = None,
    ):
        self.registry = registry or global_mcp_registry
        self.adapter = adapter or MCPAdapter()
        self.emergency_controller = emergency_controller or EmergencyStopController()
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="NRAI-MCPClient")
        self._audit_trail: List[Dict[str, Any]] = []

    def call_tool(
        self,
        server_id: str,
        tool_name: str,
        params: Dict[str, Any],
        caller_agent_id: str,
        agent_permissions: Optional[Set[ToolPermission]] = None,
        is_direct_model_call: bool = False,
    ) -> MCPExecutionResult:
        """
        Execute an MCP tool under strict safety policies.
        """
        start_time = time.time()
        audit_id = f"mcp_{uuid.uuid4().hex[:12]}"
        perms = agent_permissions or {ToolPermission.READ_ONLY}

        # 1. Model Isolation Gate: LLMs can never invoke MCP tools directly
        if is_direct_model_call:
            err_msg = (
                "MODEL_ISOLATION_VIOLATION: Direct LLM MCP execution prohibited. "
                "All invocations must be mediated through verified NR-AI orchestrator."
            )
            self._record_audit(audit_id, server_id, tool_name, caller_agent_id, False, err_msg)
            return MCPExecutionResult(
                success=False,
                tool_name=tool_name,
                content="",
                error=err_msg,
                audit_id=audit_id,
                execution_time_ms=0.0,
            )

        # 2. Emergency Stop Check
        if self.emergency_controller.is_active():
            err_msg = "EMERGENCY_STOP_ACTIVE: System freeze active. MCP tool call blocked."
            self._record_audit(audit_id, server_id, tool_name, caller_agent_id, False, err_msg)
            return MCPExecutionResult(
                success=False,
                tool_name=tool_name,
                content="",
                error=err_msg,
                audit_id=audit_id,
                execution_time_ms=0.0,
            )

        # 3. Server Configuration Check
        server_config = self.registry.get_server(server_id)
        if not server_config:
            err_msg = f"UNKNOWN_MCP_SERVER: Server '{server_id}' is not configured in authorized registry."
            self._record_audit(audit_id, server_id, tool_name, caller_agent_id, False, err_msg)
            return MCPExecutionResult(
                success=False,
                tool_name=tool_name,
                content="",
                error=err_msg,
                audit_id=audit_id,
                execution_time_ms=0.0,
            )

        if not server_config.enabled:
            err_msg = f"SERVER_DISABLED: MCP server '{server_id}' is disabled by administrative policy."
            self._record_audit(audit_id, server_id, tool_name, caller_agent_id, False, err_msg)
            return MCPExecutionResult(
                success=False,
                tool_name=tool_name,
                content="",
                error=err_msg,
                audit_id=audit_id,
                execution_time_ms=0.0,
            )

        if server_config.trust_status == ServerTrustStatus.QUARANTINED:
            err_msg = f"SERVER_QUARANTINED: MCP server '{server_id}' is quarantined due to safety evaluation."
            self._record_audit(audit_id, server_id, tool_name, caller_agent_id, False, err_msg)
            return MCPExecutionResult(
                success=False,
                tool_name=tool_name,
                content="",
                error=err_msg,
                audit_id=audit_id,
                execution_time_ms=0.0,
            )

        # 4. Caller Agent Authorization
        if server_config.allowed_agents and caller_agent_id not in server_config.allowed_agents:
            err_msg = f"AGENT_UNAUTHORIZED: Agent '{caller_agent_id}' is not authorized to call server '{server_id}'."
            self._record_audit(audit_id, server_id, tool_name, caller_agent_id, False, err_msg)
            return MCPExecutionResult(
                success=False,
                tool_name=tool_name,
                content="",
                error=err_msg,
                audit_id=audit_id,
                execution_time_ms=0.0,
            )

        # 5. Tool Allowlist Check
        if tool_name not in server_config.allowed_tools:
            err_msg = f"TOOL_NOT_ALLOWED: Tool '{tool_name}' is not in server '{server_id}' allowlist."
            self._record_audit(audit_id, server_id, tool_name, caller_agent_id, False, err_msg)
            return MCPExecutionResult(
                success=False,
                tool_name=tool_name,
                content="",
                error=err_msg,
                audit_id=audit_id,
                execution_time_ms=0.0,
            )

        # 6. Adapter & Safety Gate Check
        spec = self.adapter.get_tool_spec(tool_name)
        if not spec:
            # Construct ephemeral spec bound to server config
            spec = MCPToolSpec(
                name=tool_name,
                description="Explicitly permitted MCP server tool",
                permission=ToolPermission.READ_ONLY,
                risk_level=ToolRiskLevel.LOW,
                server_id=server_id,
            )

        allowed, reason = MCPSafetyGate.audit_invocation(
            tool_name=tool_name,
            params=params,
            agent_permissions=perms,
            spec=spec,
            is_direct_model_call=False,
            is_emergency_active=False,
        )
        if not allowed:
            self._record_audit(audit_id, server_id, tool_name, caller_agent_id, False, reason)
            return MCPExecutionResult(
                success=False,
                tool_name=tool_name,
                content="",
                error=reason,
                audit_id=audit_id,
                execution_time_ms=0.0,
            )

        # 7. Execution under Timeout
        timeout = min(60.0, max(1.0, server_config.timeout_seconds))
        try:
            future = self._executor.submit(
                self.adapter.execute_tool,
                tool_name=tool_name,
                params=params,
                agent_id=caller_agent_id,
                agent_permissions=perms,
                is_direct_model_call=False,
                timeout=timeout,
            )
            raw_result = future.result(timeout=timeout)
        except FutureTimeoutError:
            err_msg = f"MCP_TIMEOUT: Execution exceeded {timeout}s deadline on server '{server_id}'."
            self._record_audit(audit_id, server_id, tool_name, caller_agent_id, False, err_msg)
            return MCPExecutionResult(
                success=False,
                tool_name=tool_name,
                content="",
                error=err_msg,
                audit_id=audit_id,
                execution_time_ms=round((time.time() - start_time) * 1000, 2),
            )
        except Exception as ex:
            err_msg = f"MCP_EXECUTION_FAILURE: {str(ex)}"
            self._record_audit(audit_id, server_id, tool_name, caller_agent_id, False, err_msg)
            return MCPExecutionResult(
                success=False,
                tool_name=tool_name,
                content="",
                error=err_msg,
                audit_id=audit_id,
                execution_time_ms=round((time.time() - start_time) * 1000, 2),
            )

        # 8. Output Sanitization (Guardrails & Secret Scrubbing)
        sanitized_content = raw_result.content
        if sanitized_content:
            scrubbed_secrets = sanitize_secrets(sanitized_content)
            if isinstance(scrubbed_secrets, str):
                guardrail_res = PromptGuardrails.sanitize_text(scrubbed_secrets)
                sanitized_content = guardrail_res.sanitized_text
            else:
                sanitized_content = str(scrubbed_secrets)

        exec_time = round((time.time() - start_time) * 1000, 2)
        final_result = MCPExecutionResult(
            success=raw_result.success,
            tool_name=tool_name,
            content=sanitized_content,
            error=raw_result.error,
            audit_id=audit_id,
            execution_time_ms=exec_time,
        )

        self._record_audit(
            audit_id=audit_id,
            server_id=server_id,
            tool_name=tool_name,
            caller=caller_agent_id,
            success=final_result.success,
            details=final_result.error or "Success",
            execution_time_ms=exec_time,
        )
        return final_result

    def _record_audit(
        self,
        audit_id: str,
        server_id: str,
        tool_name: str,
        caller: str,
        success: bool,
        details: str,
        execution_time_ms: float = 0.0,
    ) -> None:
        self._audit_trail.append({
            "timestamp": time.time(),
            "audit_id": audit_id,
            "server_id": server_id,
            "tool_name": tool_name,
            "caller": caller,
            "success": success,
            "details": details,
            "execution_time_ms": execution_time_ms,
        })
        if len(self._audit_trail) > 1000:
            self._audit_trail.pop(0)

    def get_audit_trail(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieve recent MCP execution audit records."""
        return list(self._audit_trail[-limit:])
