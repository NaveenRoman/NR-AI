"""
NR-AI Model Context Protocol (MCP) Adapter & Safety Pipeline.
Adapts MCP tools into NR-AI's deterministic permission and isolation model.

Strict Invariants:
- Zero Model Bypass: Large Language Models cannot directly invoke MCP tools.
  All requests must follow:
  Model proposal -> NR-AI validation -> permission check -> deterministic safety gate -> execution -> verification -> audit.
- Deterministic Safety Gate: Prohibits shell execution, cmd, powershell, raw eval/exec,
  and credential extraction.
- Emergency Stop: Any active E-stop immediately freezes all tool execution.
- Scope-bounded: Tool execution requires explicitly matching agent permissions.
- Audit Logging: Every invocation, outcome, and rejection is recorded in the audit trail.
"""

from dataclasses import dataclass, field
from enum import Enum
import json
import logging
import re
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from app.agent.factory.tools import (
    PROHIBITED_TOOL_IDS,
    PROHIBITED_TOOL_PATTERNS,
    ToolPermission,
    ToolRiskLevel,
)
from app.remote.permissions import ModelIsolationGate, PROHIBITED_ACTIONS

logger = logging.getLogger("NRAI.MCP.Adapter")


@dataclass
class MCPToolSpec:
    """Standardized Model Context Protocol (MCP) Tool Specification."""
    name: str
    description: str
    input_schema: Dict[str, Any] = field(default_factory=dict)
    permission: ToolPermission = ToolPermission.READ_ONLY
    risk_level: ToolRiskLevel = ToolRiskLevel.LOW
    server_id: str = "local_mcp"
    enabled: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "permission": self.permission.value,
            "risk_level": self.risk_level.value,
            "server_id": self.server_id,
            "enabled": self.enabled,
        }


@dataclass
class MCPExecutionResult:
    """Structured result of an MCP tool execution."""
    success: bool
    tool_name: str
    content: str
    error: Optional[str] = None
    audit_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    execution_time_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "tool_name": self.tool_name,
            "content": self.content,
            "error": self.error,
            "audit_id": self.audit_id,
            "execution_time_ms": round(self.execution_time_ms, 2),
        }


class MCPSafetyGate:
    """
    Authoritative deterministic safety gate for all MCP tool invocations.
    """

    @classmethod
    def audit_invocation(
        cls,
        tool_name: str,
        params: Dict[str, Any],
        agent_permissions: Set[ToolPermission],
        spec: MCPToolSpec,
        is_direct_model_call: bool = False,
        is_emergency_active: bool = False,
    ) -> Tuple[bool, str]:
        clean_name = tool_name.strip().lower()

        # 1. Model Isolation Check
        if is_direct_model_call:
            return False, "MODEL_ISOLATION_VIOLATION: Direct LLM tool execution prohibited. All invocations must be mediated by NR-AI orchestrator."

        # 2. Emergency Stop Check
        if is_emergency_active:
            return False, "EMERGENCY_STOP_ACTIVE: System emergency freeze active. Tool execution halted."

        # 3. Prohibited Action / Tool ID Check
        if clean_name in PROHIBITED_ACTIONS or clean_name in PROHIBITED_TOOL_IDS:
            return False, f"PROHIBITED_TOOL: Action '{tool_name}' is permanently prohibited by safety policy."

        # 4. Prohibited Pattern Check in Tool Name
        clean_spaced = re.sub(r"[_\-.]", " ", clean_name)
        for pat in PROHIBITED_TOOL_PATTERNS:
            if re.search(pat, clean_name) or re.search(pat, clean_spaced):
                return False, f"PROHIBITED_PATTERN: Tool name '{tool_name}' matches prohibited regex pattern '{pat}'."

        # 5. Permission Scope Check
        if spec.permission not in agent_permissions:
            return False, (
                f"PERMISSION_DENIED: Tool '{tool_name}' requires permission '{spec.permission.value}', "
                f"which is not granted to this agent."
            )

        # 6. Tool Enabled Check
        if not spec.enabled:
            return False, f"TOOL_DISABLED: Tool '{tool_name}' is currently disabled."

        return True, "AUTHORIZED"


class MCPToolRegistry:
    """
    Registry and execution supervisor for MCP tools.
    Binds MCP specs to safe Python callable handlers under the safety pipeline.
    """

    def __init__(self):
        self._tools: Dict[str, Tuple[MCPToolSpec, Callable[[Dict[str, Any]], Dict[str, Any]]]] = {}
        self._audit_log: List[Dict[str, Any]] = []
        self._emergency_stop_active: bool = False
        self._lock = threading.RLock()

    def set_emergency_stop(self, active: bool):
        """Toggle emergency stop status."""
        with self._lock:
            self._emergency_stop_active = active
        self._record_audit("EMERGENCY_STOP_CHANGED", "SYSTEM", {"active": active})

    def is_emergency_stop_active(self) -> bool:
        with self._lock:
            return self._emergency_stop_active

    def register_tool(
        self,
        spec: MCPToolSpec,
        handler: Callable[[Dict[str, Any]], Dict[str, Any]],
    ) -> bool:
        """Registers a verified tool into the MCP catalog."""
        clean_name = spec.name.strip().lower()

        # Reject prohibited tools at registration
        if clean_name in PROHIBITED_ACTIONS or clean_name in PROHIBITED_TOOL_IDS:
            logger.error(f"Cannot register prohibited tool: {spec.name}")
            return False

        clean_spaced = re.sub(r"[_\-.]", " ", clean_name)
        for pat in PROHIBITED_TOOL_PATTERNS:
            if re.search(pat, clean_name) or re.search(pat, clean_spaced):
                logger.error(f"Cannot register tool with prohibited pattern: {spec.name}")
                return False

        with self._lock:
            self._tools[spec.name] = (spec, handler)
        self._record_audit("TOOL_REGISTERED", "SYSTEM", {"tool_name": spec.name, "permission": spec.permission.value})
        return True

    def get_tool_spec(self, tool_name: str) -> Optional[MCPToolSpec]:
        with self._lock:
            entry = self._tools.get(tool_name)
            return entry[0] if entry else None

    def list_tool_specs(self) -> List[MCPToolSpec]:
        with self._lock:
            return [t[0] for t in self._tools.values()]

    def execute_tool(
        self,
        tool_name: str,
        params: Dict[str, Any],
        agent_id: str,
        agent_permissions: Set[ToolPermission],
        is_direct_model_call: bool = False,
        timeout: float = 15.0,
    ) -> MCPExecutionResult:
        """
        Executes an MCP tool through the full NR-AI validation and safety pipeline.
        """
        start_time = time.time()
        audit_id = uuid.uuid4().hex[:12]

        with self._lock:
            entry = self._tools.get(tool_name)
            e_stop = self._emergency_stop_active

        if not entry:
            self._record_audit("TOOL_NOT_FOUND", agent_id, {"tool_name": tool_name, "audit_id": audit_id})
            return MCPExecutionResult(
                success=False,
                tool_name=tool_name,
                content="",
                error=f"MCP Tool '{tool_name}' not found in registry.",
                audit_id=audit_id,
            )

        spec, handler = entry

        # Run Authoritative Safety Audit
        allowed, reason = MCPSafetyGate.audit_invocation(
            tool_name=tool_name,
            params=params,
            agent_permissions=agent_permissions,
            spec=spec,
            is_direct_model_call=is_direct_model_call,
            is_emergency_active=e_stop,
        )

        if not allowed:
            self._record_audit("TOOL_REJECTED", agent_id, {
                "tool_name": tool_name,
                "reason": reason,
                "audit_id": audit_id,
            })
            return MCPExecutionResult(
                success=False,
                tool_name=tool_name,
                content="",
                error=reason,
                audit_id=audit_id,
                execution_time_ms=(time.time() - start_time) * 1000,
            )

        # Execute Handler with Timeout
        result_holder: List[Dict[str, Any]] = []
        exception_holder: List[Exception] = []

        def worker():
            try:
                res = handler(params)
                result_holder.append(res)
            except Exception as e:
                exception_holder.append(e)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        thread.join(timeout=timeout)

        elapsed_ms = (time.time() - start_time) * 1000

        if thread.is_alive():
            self._record_audit("TOOL_TIMEOUT", agent_id, {
                "tool_name": tool_name,
                "timeout": timeout,
                "audit_id": audit_id,
            })
            return MCPExecutionResult(
                success=False,
                tool_name=tool_name,
                content="",
                error=f"Tool '{tool_name}' timed out after {timeout} seconds.",
                audit_id=audit_id,
                execution_time_ms=elapsed_ms,
            )

        if exception_holder:
            exc = exception_holder[0]
            self._record_audit("TOOL_EXCEPTION", agent_id, {
                "tool_name": tool_name,
                "error": str(exc),
                "audit_id": audit_id,
            })
            return MCPExecutionResult(
                success=False,
                tool_name=tool_name,
                content="",
                error=f"Tool execution exception: {str(exc)}",
                audit_id=audit_id,
                execution_time_ms=elapsed_ms,
            )

        output_data = result_holder[0] if result_holder else {}
        is_success = bool(output_data.get("success", True))
        content_str = str(output_data.get("content", output_data.get("result", json.dumps(output_data))))

        self._record_audit("TOOL_EXECUTED", agent_id, {
            "tool_name": tool_name,
            "success": is_success,
            "audit_id": audit_id,
            "elapsed_ms": round(elapsed_ms, 2),
        })

        return MCPExecutionResult(
            success=is_success,
            tool_name=tool_name,
            content=content_str,
            error=output_data.get("error"),
            audit_id=audit_id,
            execution_time_ms=elapsed_ms,
        )

    def _record_audit(self, event_type: str, actor: str, details: Dict[str, Any]):
        entry = {
            "timestamp": time.time(),
            "event_type": event_type,
            "actor": actor,
            "details": details,
        }
        with self._lock:
            self._audit_log.append(entry)
            if len(self._audit_log) > 1000:
                self._audit_log.pop(0)

    def get_audit_trail(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._audit_log[-limit:])


# Global singleton instance
global_mcp_registry = MCPToolRegistry()
