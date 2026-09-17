"""
NR-AI Agent Factory: Bounded Tool Catalog & Safety Contracts.

Maintains a deterministic allowlist of verified tools available for dynamic agent binding.
Models can NEVER create unrestricted tools or execute arbitrary shell commands.
"""

from dataclasses import dataclass, field
from enum import Enum
import json
import logging
import os
from pathlib import Path
import re
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("NRAI.AgentFactoryTools")

# Safe project root for sandboxed agent operations
SANDBOX_ROOT = Path(r"C:\NR-AI\dev_projects").resolve()


class ToolPermission(str, Enum):
    """Permission classes for tool usage."""
    READ_ONLY = "READ_ONLY"
    WORKSPACE_WRITE = "WORKSPACE_WRITE"
    RESEARCH_READ = "RESEARCH_READ"
    TEST_EXECUTE = "TEST_EXECUTE"
    HIGH_PRIVILEGE = "HIGH_PRIVILEGE"


class ToolRiskLevel(str, Enum):
    """Risk classification for tool contracts."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    PROHIBITED = "PROHIBITED"


PROHIBITED_TOOL_IDS: Set[str] = {
    "shell.exec",
    "shell.run",
    "system.cmd",
    "powershell.exec",
    "powershell.run",
    "os.raw_exec",
    "os.system",
    "eval",
    "exec",
    "credential.read",
    "credential.extract",
    "network.listen",
    "network.bind",
    "adb.raw_shell",
    "browser.eval",
    "file.format_disk",
    "file.delete_all",
    "process.kill_tree",
}

PROHIBITED_TOOL_PATTERNS = [
    r"\bshell\b",
    r"\bpowershell\b",
    r"\bcmd\b",
    r"\beval\b",
    r"\bexec\b",
    r"\bcredential\b",
    r"\btoken\b",
    r"\bpassword\b",
    r"\bsecret\b",
    r"\bformat\b",
    r"\braw_exec\b",
    r"\blisten\b",
]


@dataclass
class ToolDefinition:
    """Specification of a bounded, verified tool."""
    tool_id: str
    name: str
    description: str
    capability: str
    permission: ToolPermission = ToolPermission.READ_ONLY
    risk_level: ToolRiskLevel = ToolRiskLevel.LOW
    input_schema: Dict[str, Any] = field(default_factory=dict)
    output_schema: Dict[str, Any] = field(default_factory=dict)
    handler: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Executes the tool handler safely if defined."""
        if not self.handler:
            return {"success": False, "error": f"Tool '{self.tool_id}' has no executable handler registered."}
        try:
            return self.handler(params)
        except Exception as e:
            logger.error(f"Error executing tool '{self.tool_id}': {e}")
            return {"success": False, "error": f"Tool execution failed: {str(e)}"}


# -----------------------------------------------------------------------------
# Standard Bounded Tool Handlers
# -----------------------------------------------------------------------------

def _safe_resolve_sandbox_path(raw_path: str) -> Optional[Path]:
    """Ensures paths are strictly within SANDBOX_ROOT."""
    SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        p = (SANDBOX_ROOT / raw_path).resolve()
        if p == SANDBOX_ROOT or SANDBOX_ROOT in p.parents:
            return p
        return None
    except Exception:
        return None


def _handler_file_read_bounded(params: Dict[str, Any]) -> Dict[str, Any]:
    file_path = str(params.get("path", "")).strip()
    if not file_path:
        return {"success": False, "error": "Missing 'path' parameter."}

    p = _safe_resolve_sandbox_path(file_path)
    if not p:
        return {"success": False, "error": f"Access denied: '{file_path}' is outside sandbox root."}

    if not p.exists() or not p.is_file():
        return {"success": False, "error": f"File not found: '{file_path}'."}

    max_bytes = int(params.get("max_bytes", 524288))  # 512 KB
    try:
        size = p.stat().st_size
        if size > max_bytes:
            return {"success": False, "error": f"File size ({size} bytes) exceeds limit ({max_bytes} bytes)."}
        content = p.read_text(encoding="utf-8", errors="replace")
        return {"success": True, "content": content, "size_bytes": size, "path": str(p)}
    except Exception as e:
        return {"success": False, "error": f"Read failed: {str(e)}"}


def _handler_file_write_bounded(params: Dict[str, Any]) -> Dict[str, Any]:
    file_path = str(params.get("path", "")).strip()
    content = str(params.get("content", ""))
    if not file_path:
        return {"success": False, "error": "Missing 'path' parameter."}

    p = _safe_resolve_sandbox_path(file_path)
    if not p:
        return {"success": False, "error": f"Access denied: '{file_path}' is outside sandbox root."}

    if len(content.encode("utf-8")) > 1048576:  # 1 MB
        return {"success": False, "error": "Content exceeds 1MB limit."}

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        # Backup if exists
        backup_path = None
        if p.exists():
            backup_path = p.with_suffix(p.suffix + f".bak_{int(time.time())}")
            backup_path.write_bytes(p.read_bytes())

        p.write_text(content, encoding="utf-8")
        return {
            "success": True,
            "path": str(p),
            "bytes_written": len(content.encode("utf-8")),
            "backup_created": str(backup_path) if backup_path else None,
        }
    except Exception as e:
        return {"success": False, "error": f"Write failed: {str(e)}"}


def _handler_file_list_directory(params: Dict[str, Any]) -> Dict[str, Any]:
    sub_dir = str(params.get("sub_dir", "")).strip()
    p = _safe_resolve_sandbox_path(sub_dir) if sub_dir else SANDBOX_ROOT
    if not p or not p.exists() or not p.is_dir():
        return {"success": False, "error": f"Directory not found or access denied: '{sub_dir}'."}

    try:
        items = []
        for it in sorted(p.iterdir()):
            items.append({
                "name": it.name,
                "is_dir": it.is_dir(),
                "size_bytes": it.stat().st_size if it.is_file() else 0,
            })
        return {"success": True, "directory": str(p), "items": items[:100]}
    except Exception as e:
        return {"success": False, "error": f"List directory failed: {str(e)}"}


def _handler_ast_parse_python(params: Dict[str, Any]) -> Dict[str, Any]:
    import ast
    code = str(params.get("code", ""))
    if not code:
        return {"success": False, "error": "Empty code string."}
    try:
        tree = ast.parse(code)
        classes = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
        functions = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
        return {
            "success": True,
            "syntax_valid": True,
            "classes": classes,
            "functions": functions,
        }
    except SyntaxError as se:
        return {
            "success": False,
            "syntax_valid": False,
            "error": f"SyntaxError: {se.msg} at line {se.lineno}",
            "line": se.lineno,
        }


def _handler_research_lookup(params: Dict[str, Any]) -> Dict[str, Any]:
    query = str(params.get("query", "")).strip()
    if not query:
        return {"success": False, "error": "Empty query parameter."}
    try:
        from app.knowledge.engine import UniversalKnowledgeEngine
        engine = UniversalKnowledgeEngine()
        card = engine.query_companion_card(query)
        return {
            "success": True,
            "display_text": card.get("display_text", ""),
            "epistemic_type": card.get("epistemic_type", "UNKNOWN"),
            "confidence": card.get("confidence", 0.0),
            "sources": card.get("sources", []),
        }
    except Exception as e:
        return {"success": False, "error": f"Research query failed: {str(e)}"}


class ToolCatalog:
    """
    Central repository of verified tools that dynamic agents may be granted.
    Maintains strict boundaries and rejects any prohibited tool assignment.
    """

    def __init__(self):
        self._tools: Dict[str, ToolDefinition] = {}
        self._register_default_tools()

    def _register_default_tools(self) -> None:
        """Populates the catalog with verified safe tools."""
        defaults = [
            ToolDefinition(
                tool_id="file.read_bounded",
                name="Bounded File Reader",
                description="Safely reads text files strictly within the dev_projects sandbox.",
                capability="FILE_READ",
                permission=ToolPermission.READ_ONLY,
                risk_level=ToolRiskLevel.LOW,
                input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
                output_schema={"type": "object", "properties": {"content": {"type": "string"}}},
                handler=_handler_file_read_bounded,
            ),
            ToolDefinition(
                tool_id="file.write_bounded",
                name="Bounded File Writer",
                description="Safely writes text files strictly within the dev_projects sandbox with atomic backup.",
                capability="FILE_WRITE",
                permission=ToolPermission.WORKSPACE_WRITE,
                risk_level=ToolRiskLevel.MEDIUM,
                input_schema={"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
                output_schema={"type": "object", "properties": {"bytes_written": {"type": "integer"}}},
                handler=_handler_file_write_bounded,
            ),
            ToolDefinition(
                tool_id="file.list_directory",
                name="Bounded Directory Lister",
                description="Lists files and subdirectories strictly within the dev_projects sandbox.",
                capability="DIRECTORY_LIST",
                permission=ToolPermission.READ_ONLY,
                risk_level=ToolRiskLevel.LOW,
                input_schema={"type": "object", "properties": {"sub_dir": {"type": "string"}}},
                output_schema={"type": "object", "properties": {"items": {"type": "array"}}},
                handler=_handler_file_list_directory,
            ),
            ToolDefinition(
                tool_id="ast.parse_python",
                name="Python AST Parser",
                description="Statically parses Python code into syntax trees without execution.",
                capability="CODE_INSPECTION",
                permission=ToolPermission.READ_ONLY,
                risk_level=ToolRiskLevel.LOW,
                input_schema={"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
                output_schema={"type": "object", "properties": {"syntax_valid": {"type": "boolean"}}},
                handler=_handler_ast_parse_python,
            ),
            ToolDefinition(
                tool_id="research.lookup",
                name="Universal Knowledge & Literature Lookup",
                description="Queries verified offline knowledge and public scholarly sources with SSRF protection.",
                capability="LITERATURE_RESEARCH",
                permission=ToolPermission.RESEARCH_READ,
                risk_level=ToolRiskLevel.LOW,
                input_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                output_schema={"type": "object", "properties": {"display_text": {"type": "string"}}},
                handler=_handler_research_lookup,
            ),
        ]

        for t in defaults:
            self._tools[t.tool_id] = t

    def is_tool_prohibited(self, tool_id: str) -> Tuple[bool, str]:
        """Checks whether a requested tool is explicitly prohibited."""
        t_clean = (tool_id or "").strip().lower()
        if not t_clean:
            return True, "Empty tool ID."

        if t_clean in PROHIBITED_TOOL_IDS:
            return True, f"Tool '{tool_id}' is on the explicit prohibited tools list."

        for pat in PROHIBITED_TOOL_PATTERNS:
            if re.search(pat, t_clean):
                return True, f"Tool '{tool_id}' matches prohibited security pattern '{pat}'."

        return False, ""

    def is_tool_allowed(self, tool_id: str) -> bool:
        """Returns True if the tool is registered in this catalog and not prohibited."""
        prohibited, _ = self.is_tool_prohibited(tool_id)
        if prohibited:
            return False
        return tool_id in self._tools

    def get_tool(self, tool_id: str) -> Optional[ToolDefinition]:
        """Retrieves a ToolDefinition by ID."""
        return self._tools.get(tool_id)

    def list_tools(
        self,
        permission: Optional[ToolPermission] = None,
        max_risk: Optional[ToolRiskLevel] = None,
    ) -> List[ToolDefinition]:
        """Lists registered tools matching optional permission and risk criteria."""
        results = []
        for t in self._tools.values():
            if permission and t.permission != permission:
                continue
            if max_risk and t.risk_level == ToolRiskLevel.HIGH and max_risk != ToolRiskLevel.HIGH:
                continue
            results.append(t)
        return results

    def register_tool(self, tool: ToolDefinition) -> Tuple[bool, str]:
        """Registers an additional verified tool if it passes security checks."""
        prohibited, reason = self.is_tool_prohibited(tool.tool_id)
        if prohibited:
            return False, f"Registration rejected: {reason}"

        if tool.risk_level == ToolRiskLevel.PROHIBITED:
            return False, "Registration rejected: Tool has PROHIBITED risk level."

        self._tools[tool.tool_id] = tool
        return True, f"Tool '{tool.tool_id}' registered successfully."
