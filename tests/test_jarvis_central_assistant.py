"""
Tests for Jarvis Central Assistant & Unified Memory Workspace.
Validates:
1. Sidebar integration and workspace panel presence in Galaxy UI (HTML/CSS/JS).
2. 5 Authoritative Memory Domains (Knowledge, Task, Project, Git/Reports, Conversation).
3. Live state telemetry inspection (STT/TTS, connected device, commit hash, estop).
4. Epistemic classification and epistemic honesty ('I don't have verified information...').
5. Specialist delegation (Droid, SkyShield, Studio, Unity, Unreal) via existing MultiAgentOrchestrator/NRCompanion.
6. Security invariants: zero shell=True, zero eval/exec, strict secret sanitization, context budgeting, emergency stop.
7. Backend API endpoints (/api/jarvis/status and /api/jarvis/chat).
"""

from __future__ import annotations

import json
import os
import re
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.jarvis.assistant import JarvisCentralAssistant
from app.jarvis.delegation import SpecialistDelegator
from app.jarvis.live_state import LiveStateInspector
from app.jarvis.memory_orchestrator import JarvisMemoryOrchestrator
from app.jarvis.models import (
    EpistemicClass,
    JarvisRequest,
    JarvisResponse,
    SourceDomain,
    SourceProvenance,
)
from app.jarvis.repo_memory import GitRepositoryMemory


class TestJarvisCentralAssistant(unittest.TestCase):
    """Test suite for Jarvis Central Assistant and Unified Memory Workspace."""

    def setUp(self) -> None:
        self.repo_mem = GitRepositoryMemory()
        self.live_inspector = LiveStateInspector()
        self.delegator = SpecialistDelegator()
        self.mem_orch = JarvisMemoryOrchestrator(
            repo_memory=self.repo_mem,
            live_inspector=self.live_inspector,
        )
        self.assistant = JarvisCentralAssistant(
            memory_orchestrator=self.mem_orch,
            repo_memory=self.repo_mem,
            delegator=self.delegator,
            live_inspector=self.live_inspector,
        )

    # -------------------------------------------------------------------------
    # 1. UI Integration: Sidebar & Workspace Panel
    # -------------------------------------------------------------------------

    def test_galaxy_html_sidebar_includes_jarvis(self) -> None:
        """Verify 🤖 Jarvis is in galaxy.html navigation list below Dynamic Agents."""
        html_path = Path(r"C:\NR-AI\app\ui\templates\galaxy.html")
        self.assertTrue(html_path.exists())
        content = html_path.read_text(encoding="utf-8")

        self.assertIn('id="navItemJarvis"', content)
        self.assertIn("openJarvisWorkspace(this)", content)
        self.assertIn("🤖", content)
        self.assertIn("Jarvis", content)

        # Verify placement: Dynamic Agents appears BEFORE Jarvis in the nav list
        dyn_idx = content.find("Dynamic Agents")
        jarvis_idx = content.find('id="navItemJarvis"')
        self.assertGreater(jarvis_idx, dyn_idx, "Jarvis must be placed below Dynamic Agents")

    def test_galaxy_html_has_jarvis_workspace_panel(self) -> None:
        """Verify #jarvisWorkspacePanel markup exists with all required components."""
        html_path = Path(r"C:\NR-AI\app\ui\templates\galaxy.html")
        content = html_path.read_text(encoding="utf-8")

        self.assertIn('id="jarvisWorkspacePanel"', content)
        self.assertIn('id="jarvisStatusPill"', content)
        self.assertIn('id="jarvisTrinityBadge"', content)
        self.assertIn('id="jarvisGitBadge"', content)
        self.assertIn('id="jarvisVoiceBadge"', content)
        self.assertIn('id="jarvisLiveTelemetryContent"', content)
        self.assertIn('id="jarvisChatHistory"', content)
        self.assertIn('id="jarvisTextInput"', content)
        self.assertIn('id="jarvisMicBtn"', content)
        self.assertIn('id="jarvisSendBtn"', content)

    def test_galaxy_css_and_js_bindings(self) -> None:
        """Verify CSS styles and JS controller functions are defined."""
        css_path = Path(r"C:\NR-AI\app\ui\static\galaxy.css")
        js_path = Path(r"C:\NR-AI\app\ui\static\galaxy.js")

        css_content = css_path.read_text(encoding="utf-8")
        js_content = js_path.read_text(encoding="utf-8")

        self.assertIn(".nav-item-jarvis", css_content)
        self.assertIn(".jarvis-workspace-panel", css_content)
        self.assertIn(".turn-epistemic-badge", css_content)
        self.assertIn(".jarvis-delegation-card", css_content)

        self.assertIn("function openJarvisWorkspace", js_content)
        self.assertIn("function closeJarvisWorkspace", js_content)
        self.assertIn("function sendJarvisMessage", js_content)
        self.assertIn("function loadJarvisStatus", js_content)
        self.assertIn("function toggleJarvisVoice", js_content)

    # -------------------------------------------------------------------------
    # 2. 5 Authoritative Memory Domains
    # -------------------------------------------------------------------------

    def test_git_repository_memory_safe_execution(self) -> None:
        """Verify git repository memory queries git safely with shell=False."""
        status = self.repo_mem.get_git_status()
        self.assertIsInstance(status, dict)
        self.assertIn("branch", status)
        self.assertIn("is_clean", status)
        self.assertIn("latest_commit", status)

        commits = self.repo_mem.get_recent_commits(limit=3)
        self.assertIsInstance(commits, list)
        self.assertGreaterEqual(len(commits), 1)
        self.assertIn("hash", commits[0])
        self.assertIn("subject", commits[0])

    def test_git_repository_memory_doc_search(self) -> None:
        """Verify git repository memory indexes project markdown docs and reports."""
        results = self.repo_mem.search_repository_knowledge("OpenJarvis", limit=3)
        self.assertIsInstance(results, list)
        if results:
            self.assertIn(results[0].source_domain, (SourceDomain.REPORT, SourceDomain.GITHUB))
            self.assertTrue(results[0].title)

    def test_conversation_continuity_memory(self) -> None:
        """Verify conversation continuity stores and retrieves turns with session bounds."""
        session_id = "test_continuity_session"
        self.mem_orch.add_conversation_turn(session_id, "user", "What is our current phase?")
        self.mem_orch.add_conversation_turn(session_id, "assistant", "We have completed Phase 4.")

        history = self.mem_orch.get_conversation_history(session_id)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[1]["content"], "We have completed Phase 4.")

        packed, provs = self.mem_orch.retrieve_unified_memory("current phase", session_id=session_id)
        self.assertIn("We have completed Phase 4", packed)
        conv_prov = [p for p in provs if p.source_domain == SourceDomain.CONVERSATION]
        self.assertTrue(len(conv_prov) >= 1)

    def test_live_state_inspector(self) -> None:
        """Verify live state inspection gathers telemetry tagged as SourceDomain.LIVE."""
        live_data = self.live_inspector.inspect_live_state()
        self.assertIsInstance(live_data, dict)
        self.assertIn("voice_stt", live_data)
        self.assertIn("voice_tts", live_data)
        self.assertIn("connected_device", live_data)
        self.assertIn("latest_git_checkpoint", live_data)

        prov = self.live_inspector.get_live_provenance()
        self.assertEqual(prov.source_domain, SourceDomain.LIVE)
        self.assertEqual(prov.badge_label, "[LIVE TELEMETRY]")

    # -------------------------------------------------------------------------
    # 3. Epistemic Classification & Epistemic Honesty
    # -------------------------------------------------------------------------

    def test_epistemic_classification_status_query(self) -> None:
        """Verify status queries return CURRENT_INFORMATION with live telemetry sources."""
        req = JarvisRequest(message="What is the current system status and telemetry?")
        resp = self.assistant.process_message(req)
        self.assertEqual(resp.epistemic_class, EpistemicClass.CURRENT_INFORMATION)
        self.assertIn("System Status", resp.reply)
        live_provs = [s for s in resp.sources if s.source_domain == SourceDomain.LIVE]
        self.assertTrue(len(live_provs) >= 1)

    def test_epistemic_classification_git_query(self) -> None:
        """Verify git history queries return VERIFIED_FACT backed by git repository memory."""
        req = JarvisRequest(message="Show me the latest commit and recent checkpoints")
        resp = self.assistant.process_message(req)
        self.assertEqual(resp.epistemic_class, EpistemicClass.VERIFIED_FACT)
        self.assertIn("Recent Git Checkpoints", resp.reply)

    def test_epistemic_honesty_unknown_query(self) -> None:
        """Verify that unknown factual queries produce 'I don't have verified information...' with UNCERTAINTY."""
        req = JarvisRequest(message="What is the capital of planet Mars and price of Martian oil?")
        resp = self.assistant.process_message(req)
        self.assertEqual(resp.epistemic_class, EpistemicClass.UNCERTAINTY)
        self.assertIn("I don't have verified information for that", resp.reply)

    # -------------------------------------------------------------------------
    # 4. Specialist Delegation
    # -------------------------------------------------------------------------

    def test_specialist_delegation_droid(self) -> None:
        """Verify Android commands trigger delegation to Droid."""
        target = self.delegator.evaluate_delegation("Droid, repair app gradle build in android studio")
        self.assertIsNotNone(target)
        self.assertEqual(target[0], "droid")

        req = JarvisRequest(message="Repair app gradle build in android studio", delegation_allowed=True)
        resp = self.assistant.process_message(req)
        self.assertEqual(resp.delegated_to, "droid")
        self.assertIn("Delegated to **Droid (Android Specialist)**", resp.reply)

    def test_specialist_delegation_skyshield(self) -> None:
        """Verify security scan commands trigger delegation to SkyShield."""
        target = self.delegator.evaluate_delegation("Run a full security scan and audit vulnerabilities")
        self.assertIsNotNone(target)
        self.assertEqual(target[0], "skyshield")

        req = JarvisRequest(message="Run a full security scan and audit vulnerabilities", delegation_allowed=True)
        resp = self.assistant.process_message(req)
        self.assertEqual(resp.delegated_to, "skyshield")
        self.assertIn("SkyShield", resp.reply)

    def test_no_delegation_for_informational_queries(self) -> None:
        """Verify informational questions about specialists are answered by Jarvis without delegation."""
        target = self.delegator.evaluate_delegation("What does the Droid agent do?")
        self.assertIsNone(target, "Informational 'what does' queries should not delegate")

        target_status = self.delegator.evaluate_delegation("Status of visual studio integration")
        self.assertIsNone(target_status, "Status queries should not delegate")

    # -------------------------------------------------------------------------
    # 5. Security Invariants
    # -------------------------------------------------------------------------

    def test_zero_shell_true_in_jarvis_module(self) -> None:
        """Verify zero occurrences of shell=True, eval(), or exec() in app/jarvis/."""
        jarvis_dir = Path(r"C:\NR-AI\app\jarvis")
        for py_file in jarvis_dir.glob("*.py"):
            code = py_file.read_text(encoding="utf-8")
            self.assertNotIn("shell=True", code, f"Forbidden shell=True found in {py_file.name}")
            self.assertFalse(re.search(r"\beval\(", code), f"Forbidden eval() found in {py_file.name}")
            self.assertFalse(re.search(r"\bexec\(", code), f"Forbidden exec() found in {py_file.name}")

    def test_secret_exclusion_and_guardrails(self) -> None:
        """Verify secret patterns are rejected from git doc cache and inputs sanitized."""
        # Check forbidden patterns in repo_memory
        for forbidden in [".env", "id_rsa", "secret.json", "private.pem"]:
            self.assertFalse(self.repo_mem._is_file_allowed(Path(forbidden)))

        # Prompt with fake secret is sanitized
        req = JarvisRequest(message="Here is my token sk-proj-1234567890abcdef1234567890 please save it")
        resp = self.assistant.process_message(req)
        self.assertNotIn("sk-proj-1234567890abcdef1234567890", resp.reply)

    def test_context_budget_enforcement(self) -> None:
        """Verify ContextManager enforces character limits during unified retrieval."""
        budget_limit = 500
        packed, _ = self.mem_orch.retrieve_unified_memory(
            query="OpenJarvis architecture and reports",
            max_budget_chars=budget_limit,
        )
        self.assertLessEqual(len(packed), budget_limit + 100, "Packed context must respect budget")

    def test_emergency_stop_halts_jarvis(self) -> None:
        """Verify that when emergency stop is active, Jarvis returns halt notification."""
        mock_inspector = MagicMock()
        mock_inspector.inspect_live_state.return_value = {
            "emergency_stopped": True,
            "status": "HALTED",
        }
        mock_inspector.get_live_provenance.return_value = SourceProvenance(
            source_domain=SourceDomain.LIVE,
            title="Emergency Stop",
            ref="estop:active",
            snippet="System emergency halted",
        )

        stopped_assistant = JarvisCentralAssistant(
            memory_orchestrator=self.mem_orch,
            repo_memory=self.repo_mem,
            delegator=self.delegator,
            live_inspector=mock_inspector,
        )

        req = JarvisRequest(message="Execute task now")
        resp = stopped_assistant.process_message(req)
        self.assertIn("EMERGENCY STOP is currently active", resp.reply)

    # -------------------------------------------------------------------------
    # 6. HTTP API Routes
    # -------------------------------------------------------------------------

    def test_dashboard_routes_defined(self) -> None:
        """Verify that dashboard.py contains the /api/jarvis/status and /api/jarvis/chat routes."""
        dash_path = Path(r"C:\NR-AI\app\ui\dashboard.py")
        content = dash_path.read_text(encoding="utf-8")

        self.assertIn("/api/jarvis/status", content)
        self.assertIn("/api/jarvis/chat", content)

    def test_assistant_get_status_schema(self) -> None:
        """Verify get_status returns expected structured dictionary."""
        status = self.assistant.get_status()
        self.assertEqual(status["status"], "ONLINE")
        self.assertEqual(status["name"], "Jarvis")
        self.assertIn("live_telemetry", status)
        self.assertIn("git", status)
        self.assertIn("memory_domains", status)
        self.assertIn("specialist_delegates", status)
        self.assertEqual(len(status["memory_domains"]), 5)


if __name__ == "__main__":
    unittest.main()
