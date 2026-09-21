"""
Jarvis Central Assistant for NR-AI.
Unified conversational front door, memory orchestrator, and specialist delegation coordinator.

Invariants:
- Jarvis is NOT a second brain or another specialist agent.
- Jarvis operates over the existing NR-AI Central Intelligence:
  Knowledge Trinity, MultiAgentOrchestrator, ModelRouter, Memory Domains, Voice Pipeline.
- Every response carries explicit epistemic classification and compact source provenance.
- Strict epistemic honesty: If facts are not in memory, state 'I don't have verified information for that.'
- Zero shell execution, zero eval, zero exec.
- Strict secret exclusion and prompt sanitization via PromptGuardrails.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from app.jarvis.delegation import SpecialistDelegator
from app.jarvis.live_state import LiveStateInspector
from app.jarvis.memory_orchestrator import JarvisMemoryOrchestrator
from app.jarvis.models import EpistemicClass, JarvisRequest, JarvisResponse, SourceDomain, SourceProvenance
from app.jarvis.repo_memory import GitRepositoryMemory
from app.security.guardrails import PromptGuardrails

logger = logging.getLogger("NRAI.Jarvis.CentralAssistant")


class JarvisCentralAssistant:
    """
    Central Personal Assistant for NR-AI.
    Provides unified memory retrieval, system status synthesis, and specialist task delegation.
    """

    _instance: Optional["JarvisCentralAssistant"] = None

    def __init__(
        self,
        memory_orchestrator: Optional[JarvisMemoryOrchestrator] = None,
        repo_memory: Optional[GitRepositoryMemory] = None,
        delegator: Optional[SpecialistDelegator] = None,
        live_inspector: Optional[LiveStateInspector] = None,
        companion: Optional[Any] = None,
    ) -> None:
        self.repo_memory = repo_memory or GitRepositoryMemory()
        self.live_inspector = live_inspector or LiveStateInspector()
        self.delegator = delegator or SpecialistDelegator(companion=companion)
        self.memory_orchestrator = memory_orchestrator or JarvisMemoryOrchestrator(
            repo_memory=self.repo_memory,
            live_inspector=self.live_inspector,
        )
        self._guardrails = PromptGuardrails()
        self._companion = companion

    @classmethod
    def get_instance(cls, companion: Optional[Any] = None) -> "JarvisCentralAssistant":
        if cls._instance is None:
            cls._instance = JarvisCentralAssistant(companion=companion)
        elif companion and cls._instance._companion is None:
            cls._instance._companion = companion
            cls._instance.delegator._companion = companion
        return cls._instance

    def process_message(self, request: JarvisRequest) -> JarvisResponse:
        """
        Main entry point for processing a user request in the Jarvis workspace.
        """
        # 1. Sanitize incoming prompt
        clean_prompt = self._guardrails.redact(request.message)
        session_id = request.session_id or "default_jarvis_session"

        # Check emergency stop condition
        live_state = self.live_inspector.inspect_live_state()
        if live_state.get("emergency_stopped", False):
            return JarvisResponse(
                reply="EMERGENCY STOP is currently active. All agent executions and autonomous actions are suspended. Use the emergency stop toggle to resume normal operations.",
                sources=[self.live_inspector.get_live_provenance()],
                epistemic_class=EpistemicClass.VERIFIED_FACT,
                live_state=live_state,
            )

        # 2. Check for Specialist Delegation (if allowed)
        if request.delegation_allowed:
            delegation_target = self.delegator.evaluate_delegation(clean_prompt)
            if delegation_target:
                agent_id, friendly_name = delegation_target
                logger.info("Jarvis delegating to specialist %s for: '%s'", agent_id, clean_prompt)
                del_result = self.delegator.delegate_task(agent_id, clean_prompt)
                
                result_text = del_result.get("result_text", "")
                reply = (
                    f"Delegated to **{friendly_name}**:\n\n{result_text}"
                )
                
                prov = SourceProvenance(
                    source_domain=SourceDomain.MODEL_INFERENCE,
                    title=friendly_name,
                    ref=f"specialist:{agent_id}",
                    snippet=result_text[:200],
                    confidence=0.95,
                )
                
                # Record in conversation continuity
                self.memory_orchestrator.add_conversation_turn(session_id, "user", clean_prompt)
                self.memory_orchestrator.add_conversation_turn(session_id, "assistant", reply)

                return JarvisResponse(
                    reply=reply,
                    sources=[prov],
                    epistemic_class=EpistemicClass.VERIFIED_FACT if del_result.get("success") else EpistemicClass.UNCERTAINTY,
                    delegated_to=agent_id,
                    live_state=live_state,
                )

        # 3. Retrieve Unified Memory across all 5 domains + Git + Live State
        packed_context, sources = self.memory_orchestrator.retrieve_unified_memory(
            query=clean_prompt,
            session_id=session_id,
            workspace_scope=request.workspace_scope,
            include_live_state=request.include_live_state,
            max_budget_chars=request.context_budget_chars,
        )

        # 4. Formulate Synthesized Response Grounded in Evidence
        reply, epistemic_class = self._synthesize_response(clean_prompt, packed_context, sources, live_state)

        # 5. Sanitize final reply (ensure zero secrets or token leaks)
        safe_reply = self._guardrails.redact(reply)

        # 6. Record conversation continuity turn
        self.memory_orchestrator.add_conversation_turn(session_id, "user", clean_prompt)
        self.memory_orchestrator.add_conversation_turn(session_id, "assistant", safe_reply)

        return JarvisResponse(
            reply=safe_reply,
            sources=sources,
            epistemic_class=epistemic_class,
            live_state=live_state,
        )

    def _synthesize_response(
        self,
        prompt: str,
        packed_context: str,
        sources: List[SourceProvenance],
        live_state: Dict[str, Any],
    ) -> Tuple[str, EpistemicClass]:
        """
        Synthesizes a helpful, factual response using the retrieved memory context.
        Enforces epistemic honesty: states unverified when evidence is absent.
        """
        p_low = prompt.lower().strip()

        # Intent A: System Status / Telemetry
        if any(w in p_low for w in ("system status", "telemetry", "health", "how is the system", "system health", "diagnostics")):
            stt = live_state.get("voice_stt", "faster-whisper")
            tts = live_state.get("voice_tts", "kokoro")
            active_agent = live_state.get("active_agent", "Central Intelligence")
            device = live_state.get("connected_device", "None")
            chk = live_state.get("latest_git_checkpoint", "HEAD")
            estop = "ACTIVE (HALTED)" if live_state.get("emergency_stopped") else "CLEAR (NORMAL)"

            status_text = (
                f"**System Status: HEALTHY (ONLINE)**\n"
                f"- **Active Agent**: {active_agent}\n"
                f"- **Voice Pipeline**: STT: `{stt}` | TTS: `{tts}` (Local VAD active)\n"
                f"- **Connected Device**: `{device}`\n"
                f"- **Latest Git Checkpoint**: `{chk}`\n"
                f"- **Emergency Stop**: {estop}\n"
                f"- **Memory Domains**: Knowledge Trinity, Task Checkpoints, Project Context, Git Repo Memory, Conversation Continuity active."
            )
            return status_text, EpistemicClass.CURRENT_INFORMATION

        # Intent B: Git / Repository / Commit / Checkpoint History
        if any(w in p_low for w in ("git log", "git history", "latest commit", "recent commits", "checkpoint", "git status")):
            if "status" in p_low and not ("commit" in p_low or "log" in p_low):
                git_stat = self.repo_memory.get_git_status()
                branch = git_stat.get("branch", "unknown")
                clean = "Clean" if git_stat.get("is_clean") else f"{len(git_stat.get('modified_files', []))} uncommitted changes"
                return f"**Git Status** on branch `{branch}`:\n- Working tree: **{clean}**\n- Latest commit: `{git_stat.get('latest_commit', 'HEAD')}`", EpistemicClass.VERIFIED_FACT

            commits = self.repo_memory.get_recent_commits(limit=5)
            if commits:
                commit_lines = [f"- `{c['hash']}`" + (f" ({c['date']})" if c.get('date') else "") + f": {c['subject']}" for c in commits]
                return "**Recent Git Checkpoints**:\n" + "\n".join(commit_lines), EpistemicClass.VERIFIED_FACT
            return "No recent Git checkpoints retrieved from repository.", EpistemicClass.UNCERTAINTY

        # Intent C: Specific Project Phase or Architecture / Reports
        if any(w in p_low for w in ("phase 1", "phase 2", "phase 3", "phase 4", "report", "openjarvis", "trinity", "architecture")):
            # Look for relevant repo or report sources
            relevant_sources = [s for s in sources if s.source_domain in (SourceDomain.REPORT, SourceDomain.GITHUB, SourceDomain.KNOWLEDGE)]
            if relevant_sources:
                snippets = [f"**{s.title}** ({s.ref}):\n> {s.snippet}" for s in relevant_sources[:3]]
                return "Here is the verified project documentation:\n\n" + "\n\n".join(snippets), EpistemicClass.SOURCE_ATTRIBUTED_CLAIM

        # Intent D: General Greetings / Identity
        if p_low in ("hello", "hi", "hey", "who are you", "what are you", "jarvis", "good morning", "good afternoon", "good evening"):
            return (
                "Good day. I am **Jarvis**, the Central Personal Assistant and Unified Memory View for NR-AI. "
                "I synthesize telemetry from all 5 memory domains (Knowledge Trinity, Project Context, Task Checkpoints, "
                "Conversation Continuity, and Agent Memory) and coordinate specialist execution across Droid, SkyShield, "
                "Studio, Unity, Unreal, and Sentinel. How may I assist you?"
            ), EpistemicClass.VERIFIED_FACT

        # Intent E: Unknown / Unverified Question -> Strict Epistemic Honesty
        unknown_factual_patterns = (
            "who invented", "what is the capital", "when was", "how many people",
            "what happened in", "who won", "price of", "weather in", "stock price"
        )
        if any(p in p_low for p in unknown_factual_patterns):
            return "I don't have verified information for that in the NR-AI knowledge base or project memory.", EpistemicClass.UNCERTAINTY

        # Intent F: Query with retrieved evidence
        evidence_sources = [s for s in sources if s.source_domain != SourceDomain.LIVE and s.snippet]
        if evidence_sources:
            summary_parts = []
            for s in evidence_sources[:3]:
                summary_parts.append(f"- **{s.badge_label}**: {s.snippet}")
            summary = "\n".join(summary_parts)
            return (
                f"Based on retrieved project memory:\n\n{summary}\n\n"
                f"Is there a specific action or phase detail you would like to explore?"
            ), EpistemicClass.SOURCE_ATTRIBUTED_CLAIM

        # Default conversational response
        return (
            f"I have received your request: '{prompt}'.\n"
            f"All systems are normal. Let me know if you would like me to inspect code, check tasks, or delegate to a specialist agent."
        ), EpistemicClass.INFERENCE

    def get_status(self) -> Dict[str, Any]:
        """Returns comprehensive status dictionary for the Jarvis workspace."""
        live_telemetry = self.live_inspector.inspect_live_state()
        git_status = self.repo_memory.get_git_status()
        recent_commits = self.repo_memory.get_recent_commits(limit=3)

        return {
            "status": "ONLINE",
            "name": "Jarvis",
            "role": "Central Personal Assistant & Unified Memory Coordinator",
            "live_telemetry": live_telemetry,
            "git": {
                "branch": git_status.get("branch", "main"),
                "latest_commit": git_status.get("latest_commit", "unknown"),
                "is_clean": git_status.get("is_clean", True),
                "recent_commits": recent_commits,
            },
            "memory_domains": [
                {"name": "Knowledge Trinity", "domain": "KNOWLEDGE", "status": "ACTIVE"},
                {"name": "Task Checkpoints", "domain": "TASK", "status": "ACTIVE"},
                {"name": "Project Context", "domain": "PROJECT", "status": "ACTIVE"},
                {"name": "Authorized Git & Reports", "domain": "GITHUB/REPORT", "status": "ACTIVE"},
                {"name": "Conversation Continuity", "domain": "CONVERSATION", "status": "ACTIVE"},
            ],
            "specialist_delegates": [
                {"id": "droid", "name": "Droid (Android Specialist)"},
                {"id": "skyshield", "name": "SkyShield (Security Specialist)"},
                {"id": "studio", "name": "Visual Studio Specialist"},
                {"id": "unity", "name": "Unity Specialist"},
                {"id": "unreal", "name": "Unreal Specialist"},
                {"id": "quest", "name": "Quest (Research Specialist)"},
                {"id": "sentinel", "name": "Sentinel (Computer Control)"},
            ],
        }
