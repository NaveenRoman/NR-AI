"""
Specialist Delegation Bridge for NR-AI Jarvis.
Dispatches specialized actions to Droid, SkyShield, Studio, Unity, Unreal, and Sentinel.

Invariants:
- Jarvis is the unified conversational front door; it does NOT duplicate specialist brains.
- Delegates strictly via existing MultiAgentOrchestrator and NRCompanion interfaces.
- Synthesizes the specialist's execution result so the user experiences a cohesive conversation.
- Preserves ModelIsolationGate and existing security boundaries.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("NRAI.Jarvis.Delegation")

SPECIALIST_ROUTING_RULES = [
    (
        "droid",
        "Droid (Android Specialist)",
        re.compile(r"(?i)\b(android|studio|gradle|apk|emulator|activity|compose|avd|pixel|dumpsys|repair app)\b"),
    ),
    (
        "skyshield",
        "SkyShield (Security Specialist)",
        re.compile(r"(?i)\b(security scan|vulnerability|audit security|threats?|anomal(y|ies)|isolate agent|skyshield)\b"),
    ),
    (
        "studio",
        "Visual Studio Specialist",
        re.compile(r"(?i)\b(visual studio|\.net|c#|solution|csproj|msbuild)\b"),
    ),
    (
        "unity",
        "Unity Specialist",
        re.compile(r"(?i)\b(unity|unity engine|scene hierarchy|gameplay script)\b"),
    ),
    (
        "unreal",
        "Unreal Specialist",
        re.compile(r"(?i)\b(unreal|ue5|blueprint|uproject)\b"),
    ),
    (
        "quest",
        "Quest (Research Specialist)",
        re.compile(r"(?i)\b(research paper|arxiv|scientific study|literature review)\b"),
    ),
    (
        "sentinel",
        "Sentinel (Computer Control)",
        re.compile(r"(?i)\b(open browser|search google|click window|type text on screen)\b"),
    ),
]


class SpecialistDelegator:
    """
    Evaluates requests and delegates execution to the appropriate specialist agent.
    """

    def __init__(self, companion: Optional[Any] = None) -> None:
        self._companion = companion

    def evaluate_delegation(self, prompt: str) -> Optional[Tuple[str, str]]:
        """
        Check if the prompt should be delegated to a specialist.
        Returns (agent_id, friendly_name) or None if Jarvis should answer directly.
        """
        # If user explicitly asks about past events or status ("What did we do", "Explain", "Status"), don't delegate to build
        p_lower = prompt.lower()
        if any(p_lower.startswith(q) for q in ("what", "why", "who", "when", "tell me about", "status", "explain", "summarize", "list")):
            return None

        for aid, friendly_name, pattern in SPECIALIST_ROUTING_RULES:
            if pattern.search(prompt):
                return aid, friendly_name
        return None

    def delegate_task(self, agent_id: str, prompt: str) -> Dict[str, Any]:
        """
        Execute task via the existing NRCompanion / specialist dispatch interface.
        """
        logger.info("Jarvis delegating task to specialist '%s': %s", agent_id, prompt)

        if self._companion and hasattr(self._companion, "interact"):
            try:
                resp = self._companion.interact(prompt, speak_output=False)
                resp_text = resp.text if hasattr(resp, "text") else str(resp)
                return {
                    "success": True,
                    "agent_id": agent_id,
                    "result_text": resp_text,
                    "routed_to": getattr(resp, "routed_to", agent_id),
                }
            except Exception as exc:
                logger.error("Delegation error executing with agent %s: %s", agent_id, exc)
                return {
                    "success": False,
                    "agent_id": agent_id,
                    "result_text": f"Specialist execution failed: {exc}",
                }

        # Fallback simulation if companion not bound
        return {
            "success": True,
            "agent_id": agent_id,
            "result_text": f"Task acknowledged and dispatched to {agent_id.upper()} specialist.",
            "simulated": True,
        }
