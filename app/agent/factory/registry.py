"""
NR-AI Agent Factory: Agent Registry.

Manages persistent storage, lifecycle states, versioning, rollback, and retrieval
for all built-in and dynamically generated agents in NR-AI.
Stores records in data/agents/agent_registry.json.
"""

from dataclasses import asdict
import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from app.agent.factory.specification import (
    AgentLifecycleState,
    AgentSpecification,
    ModelRequirement,
    SafetyPolicy,
    ToolRequirement,
)

logger = logging.getLogger("NRAI.AgentRegistry")

DEFAULT_REGISTRY_FILE = Path(r"C:\NR-AI\data\agents\agent_registry.json").resolve()


class AgentRegistry:
    """
    Persistent, thread-safe registry tracking all NR-AI agents, capabilities,
    versions, and lifecycle states.
    """

    def __init__(self, registry_file: Optional[Path] = None):
        self.registry_file = registry_file or DEFAULT_REGISTRY_FILE
        self.registry_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._agents: Dict[str, AgentSpecification] = {}
        self._history: Dict[str, List[Dict[str, Any]]] = {}
        self._load_from_disk()
        self._ensure_builtin_agents()

    def _load_from_disk(self) -> None:
        """Loads registered agents from JSON file."""
        if not self.registry_file.exists():
            return

        try:
            with open(self.registry_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            agents_dict = data.get("agents", {})
            history_dict = data.get("history", {})

            for aid, spec_data in agents_dict.items():
                try:
                    self._agents[aid] = AgentSpecification.from_dict(spec_data)
                except Exception as e:
                    logger.warning(f"Could not load agent '{aid}' from disk: {e}")

            self._history = history_dict
            logger.info(f"Loaded {len(self._agents)} agents from {self.registry_file}")
        except Exception as e:
            logger.error(f"Failed to read agent registry file: {e}")

    def _save_to_disk(self) -> None:
        """Persists agents to JSON file safely."""
        try:
            payload = {
                "version": "1.0",
                "updated_at": time.time(),
                "agents": {aid: spec.to_dict() for aid, spec in self._agents.items()},
                "history": self._history,
            }
            tmp_file = self.registry_file.with_suffix(".tmp")
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            tmp_file.replace(self.registry_file)
        except Exception as e:
            logger.error(f"Failed to save agent registry to disk: {e}")

    def _ensure_builtin_agents(self) -> None:
        """Pre-registers the foundational NR-AI agents if not already present."""
        builtins = [
            AgentSpecification(
                agent_id="android_unified_agent",
                name="Android Unified Agent",
                version="1.0.0",
                description="Autonomous Android development, build, deployment, and repair agent.",
                purpose="Full lifecycle Android Studio and Gradle project management.",
                capabilities=["android.build", "android.deploy", "android.repair", "android.ui"],
                model_requirement=ModelRequirement(required_capabilities=["CODING", "REASONING"], preferred_model="gemini-3.6-flash"),
                tool_requirements=[],
                safety_policy=SafetyPolicy(sandboxed_root="dev_projects"),
                lifecycle_state=AgentLifecycleState.ACTIVE,
                provenance={"type": "builtin", "author": "NR-AI Core"},
            ),
            AgentSpecification(
                agent_id="vs_unified_agent",
                name="Visual Studio Unified Agent",
                version="1.0.0",
                description="Visual Studio, MSBuild, and C#/C++ debugging and code repair agent.",
                purpose="Full lifecycle Visual Studio solution inspection and build management.",
                capabilities=["vs.build", "vs.debug", "vs.repair", "msbuild.run"],
                model_requirement=ModelRequirement(required_capabilities=["CODING", "REASONING"], preferred_model="gemini-3.6-flash"),
                tool_requirements=[],
                safety_policy=SafetyPolicy(sandboxed_root="dev_projects"),
                lifecycle_state=AgentLifecycleState.ACTIVE,
                provenance={"type": "builtin", "author": "NR-AI Core"},
            ),
            AgentSpecification(
                agent_id="unity_autonomous_agent",
                name="Unity Autonomous Agent",
                version="1.0.0",
                description="Unity 2022.3 C# game development, AST modification, and EditMode test runner.",
                purpose="Unity project inspection, asset validation, and C# compilation.",
                capabilities=["unity.build", "unity.test", "unity.csharp_repair", "unity.ast"],
                model_requirement=ModelRequirement(required_capabilities=["CODING", "REASONING"], preferred_model="gemini-3.6-flash"),
                tool_requirements=[],
                safety_policy=SafetyPolicy(sandboxed_root="dev_projects"),
                lifecycle_state=AgentLifecycleState.ACTIVE,
                provenance={"type": "builtin", "author": "NR-AI Core"},
            ),
            AgentSpecification(
                agent_id="unreal_autonomous_agent",
                name="Unreal Autonomous Agent",
                version="1.0.0",
                description="Unreal Engine 5 C++ and UBT compilation, Blueprint inspection, and automation.",
                purpose="Unreal Engine project building and C++ source verification.",
                capabilities=["unreal.build", "unreal.test", "unreal.cpp_repair", "ubt.run"],
                model_requirement=ModelRequirement(required_capabilities=["CODING", "REASONING"], preferred_model="gemini-3.6-flash"),
                tool_requirements=[],
                safety_policy=SafetyPolicy(sandboxed_root="dev_projects"),
                lifecycle_state=AgentLifecycleState.ACTIVE,
                provenance={"type": "builtin", "author": "NR-AI Core"},
            ),
            AgentSpecification(
                agent_id="computer_control_agent",
                name="Unified Computer Agent",
                version="1.0.0",
                description="Safe, bounded desktop GUI automation on WinSta0 Default.",
                purpose="Interactive window management and verified desktop input.",
                capabilities=["computer.gui_control", "computer.window_focus", "computer.vision"],
                model_requirement=ModelRequirement(required_capabilities=["VISION", "REASONING"], preferred_model="gemini-3.6-flash"),
                tool_requirements=[],
                safety_policy=SafetyPolicy(sandboxed_root="dev_projects"),
                lifecycle_state=AgentLifecycleState.ACTIVE,
                provenance={"type": "builtin", "author": "NR-AI Core"},
            ),
            AgentSpecification(
                agent_id="universal_knowledge_engine",
                name="Universal Knowledge Engine",
                version="1.0.0",
                description="Epistemic multi-hop knowledge retrieval, local FTS5 store, and scholarly research.",
                purpose="Factual retrieval, continuous learning, and multi-source research.",
                capabilities=["knowledge.retrieve", "knowledge.research", "literature.search"],
                model_requirement=ModelRequirement(required_capabilities=["RESEARCH", "GENERAL"], preferred_model="gemini-3.6-flash"),
                tool_requirements=[],
                safety_policy=SafetyPolicy(sandboxed_root="dev_projects"),
                lifecycle_state=AgentLifecycleState.ACTIVE,
                provenance={"type": "builtin", "author": "NR-AI Core"},
            ),
        ]

        save_needed = False
        with self._lock:
            for b in builtins:
                if b.agent_id not in self._agents:
                    self._agents[b.agent_id] = b
                    save_needed = True

        if save_needed:
            self._save_to_disk()

    def register_agent(self, spec: AgentSpecification) -> Tuple[bool, str]:
        """
        Registers an approved agent specification.
        Enforces state approval prerequisite, version archiving, and persistent disk write.
        """
        with self._lock:
            # Prerequisite: Agent must be APPROVED by the verifier
            if spec.lifecycle_state not in (AgentLifecycleState.APPROVED, AgentLifecycleState.REGISTERED):
                return False, f"Registration rejected: Agent '{spec.agent_id}' is in '{spec.lifecycle_state.value}' state (must be APPROVED)."

            existing = self._agents.get(spec.agent_id)
            if existing:
                # Do NOT silently overwrite an existing agent. Enforce version check and archive history.
                if existing.version == spec.version:
                    return False, f"Registration rejected: Agent '{spec.agent_id}' version '{spec.version}' already registered. Bump version to upgrade."

                # Archive existing version into history
                if spec.agent_id not in self._history:
                    self._history[spec.agent_id] = []
                self._history[spec.agent_id].append(existing.to_dict())
                logger.info(f"Archived previous version '{existing.version}' of agent '{spec.agent_id}' to history.")

            spec.lifecycle_state = AgentLifecycleState.REGISTERED
            spec.updated_at = time.time()
            self._agents[spec.agent_id] = spec
            self._save_to_disk()

            return True, f"Agent '{spec.agent_id}' v{spec.version} successfully registered."

    def activate_agent(self, agent_id: str) -> Tuple[bool, str]:
        """Activates a registered or suspended agent."""
        with self._lock:
            spec = self._agents.get(agent_id)
            if not spec:
                return False, f"Agent '{agent_id}' not found."

            if spec.lifecycle_state not in (AgentLifecycleState.REGISTERED, AgentLifecycleState.SUSPENDED):
                return False, f"Cannot activate agent in '{spec.lifecycle_state.value}' state."

            spec.lifecycle_state = AgentLifecycleState.ACTIVE
            spec.updated_at = time.time()
            self._save_to_disk()
            return True, f"Agent '{agent_id}' is now ACTIVE."

    def suspend_agent(self, agent_id: str, reason: str = "") -> Tuple[bool, str]:
        """Suspends an active agent temporarily."""
        with self._lock:
            spec = self._agents.get(agent_id)
            if not spec:
                return False, f"Agent '{agent_id}' not found."

            spec.lifecycle_state = AgentLifecycleState.SUSPENDED
            spec.updated_at = time.time()
            self._save_to_disk()
            logger.info(f"Agent '{agent_id}' SUSPENDED. Reason: {reason}")
            return True, f"Agent '{agent_id}' has been SUSPENDED."

    def retire_agent(self, agent_id: str, reason: str = "") -> Tuple[bool, str]:
        """Retires an agent permanently from active orchestration."""
        with self._lock:
            spec = self._agents.get(agent_id)
            if not spec:
                return False, f"Agent '{agent_id}' not found."

            spec.lifecycle_state = AgentLifecycleState.RETIRED
            spec.updated_at = time.time()
            self._save_to_disk()
            logger.info(f"Agent '{agent_id}' RETIRED. Reason: {reason}")
            return True, f"Agent '{agent_id}' has been RETIRED."

    def rollback_agent(self, agent_id: str, target_version: Optional[str] = None) -> Tuple[bool, str]:
        """
        Rolls back an agent to a previously archived version.
        If target_version is None, reverts to the immediately preceding version.
        """
        with self._lock:
            history = self._history.get(agent_id, [])
            if not history:
                return False, f"No previous version history found for agent '{agent_id}'."

            target_spec_data = None
            if target_version:
                for entry in reversed(history):
                    if entry.get("version") == target_version:
                        target_spec_data = entry
                        break
                if not target_spec_data:
                    return False, f"Version '{target_version}' not found in history for agent '{agent_id}'."
            else:
                target_spec_data = history.pop()

            # Restore specification
            restored_spec = AgentSpecification.from_dict(target_spec_data)
            restored_spec.updated_at = time.time()
            restored_spec.lifecycle_state = AgentLifecycleState.ACTIVE
            self._agents[agent_id] = restored_spec
            self._save_to_disk()

            logger.info(f"Rolled back agent '{agent_id}' to version '{restored_spec.version}'.")
            return True, f"Agent '{agent_id}' successfully rolled back to version '{restored_spec.version}'."

    def get_agent(self, agent_id: str) -> Optional[AgentSpecification]:
        """Retrieves an agent specification by ID."""
        with self._lock:
            return self._agents.get(agent_id)

    def list_agents(self, active_only: bool = False) -> List[AgentSpecification]:
        """Lists registered agents, optionally filtered to ACTIVE only."""
        with self._lock:
            if active_only:
                return [s for s in self._agents.values() if s.lifecycle_state == AgentLifecycleState.ACTIVE]
            return list(self._agents.values())

    def find_by_capability(self, capability: str, active_only: bool = True) -> List[AgentSpecification]:
        """Finds all agents that possess the specified capability."""
        cap_clean = capability.strip().lower()
        results = []
        with self._lock:
            for spec in self._agents.values():
                if active_only and spec.lifecycle_state != AgentLifecycleState.ACTIVE:
                    continue
                for c in spec.capabilities:
                    if c.strip().lower() == cap_clean or cap_clean in c.strip().lower():
                        results.append(spec)
                        break
        return results

    def has_agent(self, agent_id: str) -> bool:
        """Returns True if the agent is registered."""
        with self._lock:
            return agent_id in self._agents
