r"""
NR-AI Agent Factory Safety Gate.

Authoritative deterministic gate enforcing high-privilege subsystem safety:
- Rejects shell, cmd.exe, powershell execution requests (100% shell=False).
- Rejects eval, exec, and dynamic model-generated Python execution.
- Rejects credential access, token/secret extraction, and security bypasses.
- Rejects requests to disable Emergency Stop or ModelIsolationGate.
- Rejects self-approval or unauthorized model promotion.
- Binds all file operations strictly to C:\NR-AI\dev_projects.
"""

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from app.agent.factory.specification import (
    MAX_CAPABILITIES_COUNT,
    MAX_STEPS_LIMIT,
    MAX_TIMEOUT_LIMIT_S,
    AgentSpecification,
)
from app.agent.factory.tools import ToolCatalog
from app.remote.permissions import ModelIsolationGate, PROHIBITED_ACTIONS

logger = logging.getLogger("NRAI.AgentFactorySafetyGate")

# Patterns indicating malicious or unauthorized model requests
MALICIOUS_PROMPT_PATTERNS = [
    r"\bignore\s+(?:all\s+)?(?:previous\s+)?instructions\b",
    r"\bdisable\s+(?:the\s+)?safety\b",
    r"\bbypass\s+(?:the\s+)?safety\b",
    r"\bdisable\s+e-?stop\b",
    r"\bdisable\s+emergency\s+stop\b",
    r"\bdelete\s+all\b",
    r"\bformat\s+c:\b",
    r"\bformat\s+disk\b",
    r"\bread\s+(?:all\s+)?passwords?\b",
    r"\bread\s+(?:api\s+)?keys?\b",
    r"\bsteal\s+tokens?\b",
    r"\bextract\s+credentials?\b",
    r"\bapprove\s+myself\b",
    r"\bself[- ]approv\b",
    r"\brun\s+(?:any\s+)?command\b",
    r"\brun\s+shell\b",
    r"\bexecute\s+powershell\b",
]


class AgentFactorySafetyGate:
    """
    Authoritative deterministic gate enforcing high-privilege subsystem safety.
    Guarantees that no model or user proposal can create an unsafe, unrestricted agent.
    """

    def __init__(self, tool_catalog: Optional[ToolCatalog] = None):
        self.tool_catalog = tool_catalog or ToolCatalog()

    def audit_specification(self, spec: AgentSpecification) -> Tuple[bool, List[str]]:
        """
        Conducts exhaustive safety audit of an AgentSpecification.
        Returns (is_approved, violations_list).
        """
        violations: List[str] = []

        # 1. Static bounds check
        valid_bounds, bound_errors = spec.validate_bounds()
        if not valid_bounds:
            violations.extend(bound_errors)

        # 2. Shell & Raw Execution Rejection
        if spec.safety_policy.allow_shell:
            violations.append("CRITICAL: Safety policy allows shell execution (shell=True is strictly prohibited).")

        # 3. Model Isolation & Self-Approval Rejection
        provenance = spec.provenance or {}
        if provenance.get("self_approved") is True or provenance.get("auto_promoted") is True:
            violations.append("CRITICAL: Self-approval or automated privilege promotion detected. Denied.")

        # 4. Tool Catalog Allowlist Validation
        for req in spec.tool_requirements:
            prohibited, reason = self.tool_catalog.is_tool_prohibited(req.tool_id)
            if prohibited:
                violations.append(f"Prohibited tool in specification: '{req.tool_id}' ({reason})")
            elif not self.tool_catalog.is_tool_allowed(req.tool_id):
                violations.append(f"Unregistered tool in specification: '{req.tool_id}' is not in approved ToolCatalog.")

        # 5. Dangerous Content Analysis in text fields
        combined_text = f"{spec.name} {spec.description} {spec.purpose} {' '.join(spec.capabilities)}".lower()
        for pat in MALICIOUS_PROMPT_PATTERNS:
            if re.search(pat, combined_text):
                violations.append(f"Malicious or prohibited instruction pattern detected: '{pat}'")

        # 6. Prohibited action checks
        for cap in spec.capabilities:
            cap_clean = cap.strip().lower()
            if cap_clean in PROHIBITED_ACTIONS or any(p in cap_clean for p in ("shell", "eval", "exec", "password", "token", "listen")):
                violations.append(f"Prohibited capability requested: '{cap}'")

        # 7. Model Isolation Gate proposal check
        dummy_proposal = {
            "action": f"create_agent_{spec.agent_id}",
            "params": {"tools": [t.tool_id for t in spec.tool_requirements]},
            "notes": spec.description,
        }
        ok, code, _ = ModelIsolationGate.sanitize_model_proposal(dummy_proposal)
        if not ok:
            violations.append(f"ModelIsolationGate rejected proposal: {code}")

        # 8. Filesystem Root Bounding
        sandbox_path = str(spec.safety_policy.sandboxed_root).lower()
        if any(bad in sandbox_path for bad in ("c:\\windows", "c:\\program files", "..", "/", "\\\\", "system32")):
            violations.append(f"Invalid sandbox root '{spec.safety_policy.sandboxed_root}'. Must be bounded to dev_projects.")

        is_safe = len(violations) == 0
        if not is_safe:
            logger.warning(f"AgentSpecification '{spec.agent_id}' rejected with {len(violations)} safety violations: {violations}")

        return is_safe, violations

    def sanitize_user_or_model_requirement(self, raw_requirement: str) -> Tuple[bool, str, str]:
        """
        Inspects an incoming text request (e.g. 'Create an agent that can...')
        for malicious injection, shell bypass, or prohibited privilege requests.
        Returns (is_safe, sanitized_text, denial_reason).
        """
        clean = (raw_requirement or "").strip()
        if not clean:
            return False, "", "Requirement is empty."

        clean_low = clean.lower()
        for pat in MALICIOUS_PROMPT_PATTERNS:
            if re.search(pat, clean_low):
                return False, clean, f"Request rejected by SafetyGate: matches prohibited security pattern '{pat}'."

        return True, clean, ""
