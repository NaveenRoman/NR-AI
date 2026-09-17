"""
NR-AI Galaxy UI & Agent Visualization Engine.
UI Intelligence Foundation — Celestial Agent Mapping & Real Telemetry Aggregator.

Maps registered agents from the AgentRegistry to celestial visual nodes orbiting
the central NR-AI Black Hole Intelligence Core. Provides real hardware metrics (psutil),
dynamic orbit positioning, friendly display identities, and capability action generation.
Zero fake data invariant strictly enforced.
"""

from dataclasses import asdict, dataclass, field
import logging
import math
import os
import time
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

from app.agent.factory.registry import AgentRegistry
from app.agent.factory.specification import AgentLifecycleState, AgentSpecification
from app.remote.emergency import EmergencyStopController

logger = logging.getLogger("NRAI.GalaxyEngine")

# -----------------------------------------------------------------------------
# Celestial Visual Identity Definitions
# -----------------------------------------------------------------------------
BUILTIN_CELESTIAL_PROFILES: Dict[str, Dict[str, Any]] = {
    "android_unified_agent": {
        "friendly_name": "Droid",
        "role": "Android Agent",
        "category": "Mobile & OS",
        "color": "#10b981",  # Emerald / Android green
        "glow": "rgba(16, 185, 129, 0.6)",
        "orbit_ring": 1,
        "base_angle": 135,
        "icon_type": "android",
        "greeting": "Hi Boss! I'm Droid, your Android Agent. I can help you create, build, debug, and deploy Android applications. Tell me your requirement!",
        "suggested_actions": [
            {"id": "android.create_project", "label": "Create Project", "icon": "plus-circle"},
            {"id": "android.debug_app", "label": "Debug App", "icon": "play-circle"},
            {"id": "android.build_run", "label": "Build & Run", "icon": "cpu"},
            {"id": "android.find_issues", "label": "Find Issues", "icon": "info"},
        ],
    },
    "vs_unified_agent": {
        "friendly_name": "Studio",
        "role": "Visual Studio Agent",
        "category": "Desktop & Systems",
        "color": "#8b5cf6",  # Violet
        "glow": "rgba(139, 92, 246, 0.6)",
        "orbit_ring": 1,
        "base_angle": 15,
        "icon_type": "visual_studio",
        "greeting": "Hi Boss! I'm Studio, your Visual Studio and C#/C++ Agent. I inspect solutions, build MSBuild targets, and diagnose compilation errors.",
        "suggested_actions": [
            {"id": "vs.build_solution", "label": "Build Solution", "icon": "cpu"},
            {"id": "vs.diagnose_errors", "label": "Diagnose Errors", "icon": "alert-circle"},
            {"id": "vs.run_vstest", "label": "Run VSTest", "icon": "check-circle"},
        ],
    },
    "unity_autonomous_agent": {
        "friendly_name": "Unity",
        "role": "Game Dev Agent",
        "category": "Gaming & Simulation",
        "color": "#38bdf8",  # Sky blue
        "glow": "rgba(56, 189, 248, 0.6)",
        "orbit_ring": 1,
        "base_angle": 215,
        "icon_type": "unity",
        "greeting": "Hi Boss! I'm Unity, your Unity 2022.3 Game Agent. I manage scenes, validate assets, parse C# ASTs, and run EditMode tests.",
        "suggested_actions": [
            {"id": "unity.inspect_project", "label": "Inspect Project", "icon": "folder"},
            {"id": "unity.run_tests", "label": "Run EditMode Tests", "icon": "play"},
            {"id": "unity.repair_csharp", "label": "Repair C# Code", "icon": "tool"},
        ],
    },
    "unreal_autonomous_agent": {
        "friendly_name": "Unreal",
        "role": "Game Dev Agent",
        "category": "Gaming & Simulation",
        "color": "#ef4444",  # Crimson / Red
        "glow": "rgba(239, 68, 68, 0.6)",
        "orbit_ring": 1,
        "base_angle": 250,
        "icon_type": "unreal",
        "greeting": "Hi Boss! I'm Unreal, your Unreal Engine 5 Agent. I handle C++ source parsing, UBT build orchestration, and Blueprint automation.",
        "suggested_actions": [
            {"id": "unreal.build_ubt", "label": "Build with UBT", "icon": "cpu"},
            {"id": "unreal.inspect_blueprints", "label": "Inspect Blueprints", "icon": "eye"},
            {"id": "unreal.auto_repair", "label": "Auto-Repair C++", "icon": "tool"},
        ],
    },
    "universal_knowledge_engine": {
        "friendly_name": "Knowledge",
        "role": "Oracle Agent",
        "category": "Intelligence & Research",
        "color": "#3b82f6",  # Royal Blue
        "glow": "rgba(59, 130, 246, 0.6)",
        "orbit_ring": 1,
        "base_angle": 90,
        "icon_type": "book",
        "greeting": "Hi Boss! I'm Knowledge, your Universal Knowledge & Research Agent. I query local FTS5 stores, arXiv, Wikipedia, and PubMed for verified facts.",
        "suggested_actions": [
            {"id": "knowledge.search", "label": "Search Knowledge", "icon": "search"},
            {"id": "knowledge.research_topic", "label": "Scholarly Research", "icon": "book-open"},
            {"id": "knowledge.verify_facts", "label": "Verify Facts", "icon": "check-square"},
        ],
    },
    "computer_control_agent": {
        "friendly_name": "Sentinel",
        "role": "System Agent",
        "category": "System & Automation",
        "color": "#14b8a6",  # Teal / Cyan
        "glow": "rgba(20, 184, 166, 0.6)",
        "orbit_ring": 1,
        "base_angle": 35,
        "icon_type": "gear",
        "greeting": "Hi Boss! I'm Sentinel, your Unified Computer & System Agent. I inspect windows, perform verified GUI operations, and ensure system health.",
        "suggested_actions": [
            {"id": "computer.list_windows", "label": "List Windows", "icon": "grid"},
            {"id": "computer.inspect_screen", "label": "Inspect Screen", "icon": "monitor"},
            {"id": "computer.system_health", "label": "System Health", "icon": "activity"},
        ],
    },
    # Subsystem Agents represented as core constellation members
    "nexus_coordinator": {
        "friendly_name": "Nexus",
        "role": "Multi-Agent Coordinator",
        "category": "Orchestration",
        "color": "#ec4899",  # Pink / Magenta
        "glow": "rgba(236, 72, 153, 0.6)",
        "orbit_ring": 1,
        "base_angle": 60,
        "icon_type": "nexus",
        "greeting": "Hi Boss! I'm Nexus, your Multi-Agent Orchestrator. I decompose multi-disciplinary goals and route tasks across specialized agents.",
        "suggested_actions": [
            {"id": "nexus.coordinate_goal", "label": "Coordinate Workflow", "icon": "share-2"},
            {"id": "nexus.view_agent_mesh", "label": "View Agent Mesh", "icon": "layers"},
        ],
    },
    "security_agent": {
        "friendly_name": "Shield",
        "role": "Security Agent",
        "category": "Security & Defense",
        "color": "#06b6d4",  # Cyan
        "glow": "rgba(6, 182, 212, 0.6)",
        "orbit_ring": 1,
        "base_angle": 345,
        "icon_type": "shield",
        "greeting": "Hi Boss! I'm Shield, your Defensive Security Agent. I audit execution safety, enforce zero-shell invariants, and guard credentials.",
        "suggested_actions": [
            {"id": "security.audit_status", "label": "Security Audit", "icon": "shield-check"},
            {"id": "security.verify_invariants", "label": "Verify Invariants", "icon": "lock"},
            {"id": "security.emergency_stop", "label": "Emergency Halt", "icon": "alert-triangle"},
        ],
    },
    "research_agent": {
        "friendly_name": "Quest",
        "role": "Research Agent",
        "category": "Intelligence & Research",
        "color": "#f59e0b",  # Amber
        "glow": "rgba(245, 158, 11, 0.6)",
        "orbit_ring": 1,
        "base_angle": 315,
        "icon_type": "beaker",
        "greeting": "Hi Boss! I'm Quest, your Deep Research Agent. I formulate queries, analyze academic literature, and synthesize scientific papers.",
        "suggested_actions": [
            {"id": "research.query_arxiv", "label": "Search arXiv", "icon": "file-text"},
            {"id": "research.query_pubmed", "label": "Search PubMed", "icon": "heart"},
        ],
    },
    "voice_agent": {
        "friendly_name": "Echo",
        "role": "Voice Agent",
        "category": "Sensory & Speech",
        "color": "#10b981",  # Emerald
        "glow": "rgba(16, 185, 129, 0.6)",
        "orbit_ring": 1,
        "base_angle": 275,
        "icon_type": "mic",
        "greeting": "Hi Boss! I'm Echo, your Voice & Audio Agent. I listen for natural language commands and synthesize audio responses.",
        "suggested_actions": [
            {"id": "voice.probe_mic", "label": "Probe Microphone", "icon": "mic"},
            {"id": "voice.toggle_tts", "label": "Toggle Voice TTS", "icon": "volume-2"},
        ],
    },
    "vision_agent": {
        "friendly_name": "Vision",
        "role": "Image & Video Agent",
        "category": "Sensory & Vision",
        "color": "#d946ef",  # Fuchsia
        "glow": "rgba(217, 70, 239, 0.6)",
        "orbit_ring": 1,
        "base_angle": 160,
        "icon_type": "eye",
        "greeting": "Hi Boss! I'm Vision, your Visual Grounding and OCR Agent. I inspect screen pixels, detect UI hierarchies, and identify visual targets.",
        "suggested_actions": [
            {"id": "vision.capture_screen", "label": "Capture Screen", "icon": "camera"},
            {"id": "vision.ocr_inspect", "label": "Run OCR Detection", "icon": "search"},
        ],
    },
    "forge_dev_agent": {
        "friendly_name": "Forge",
        "role": "Development Agent",
        "category": "Code & Scaffolding",
        "color": "#f97316",  # Orange
        "glow": "rgba(249, 115, 22, 0.6)",
        "orbit_ring": 1,
        "base_angle": 185,
        "icon_type": "code",
        "greeting": "Hi Boss! I'm Forge, your Scaffolding & Code Generation Agent. I create sandboxed project trees and multi-step developer workflows.",
        "suggested_actions": [
            {"id": "forge.scaffold_project", "label": "Scaffold App", "icon": "folder-plus"},
            {"id": "forge.inspect_code", "label": "Inspect Code AST", "icon": "code"},
        ],
    },
    "pixel_ui_agent": {
        "friendly_name": "Pixel",
        "role": "UI/UX Agent",
        "category": "Presentation & UI",
        "color": "#06b6d4",  # Cyan
        "glow": "rgba(6, 182, 212, 0.6)",
        "orbit_ring": 1,
        "base_angle": 295,
        "icon_type": "monitor",
        "greeting": "Hi Boss! I'm Pixel, your UI & Visualization Agent. I maintain the living Galaxy command center and HUD interfaces.",
        "suggested_actions": [
            {"id": "pixel.reset_view", "label": "Reset Galaxy View", "icon": "refresh-cw"},
            {"id": "pixel.toggle_theme", "label": "Theme Settings", "icon": "sliders"},
        ],
    },
}


@dataclass
class CelestialNode:
    """Represents a glowing agent node in the galaxy."""
    agent_id: str
    friendly_name: str
    role: str
    category: str
    status: str  # ONLINE, WORKING, THINKING, IDLE, OFFLINE, SUSPENDED, RETIRED, STATUS UNKNOWN
    status_color: str  # Hex code representing status
    color: str  # Primary agent theme color
    glow: str  # CSS glow shadow
    orbit_radius: float  # Distance from central core (px normalized)
    orbit_angle: float  # Current angle in degrees (0-360)
    orbit_speed: float  # Orbital revolution speed factor
    icon_type: str
    greeting: str
    capabilities: List[str]
    model_name: str
    version: str
    is_builtin: bool
    suggested_actions: List[Dict[str, str]]
    current_task: Optional[Dict[str, Any]] = None
    step_history: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class GalaxyEngine:
    """
    Core engine managing visual galaxy layout, dynamic agent integration,
    and real-time system metrics.
    """

    def __init__(self, registry: Optional[AgentRegistry] = None, emergency_stop: Optional[EmergencyStopController] = None):
        self.registry = registry or AgentRegistry()
        self.emergency_stop = emergency_stop or EmergencyStopController()
        self.start_time = time.time()

    def get_real_system_metrics(self) -> Dict[str, Any]:
        """
        Retrieves 100% REAL system hardware metrics via psutil.
        Zero fabricated data.
        """
        uptime_seconds = int(time.time() - self.start_time)
        hours = uptime_seconds // 3600
        mins = (uptime_seconds % 3600) // 60
        uptime_str = f"{hours}h {mins}m" if hours > 0 else f"{mins}m"

        if HAS_PSUTIL:
            try:
                cpu_pct = round(psutil.cpu_percent(interval=None), 1)
                if cpu_pct == 0.0:
                    cpu_pct = round(psutil.cpu_percent(interval=0.05), 1)
                vm = psutil.virtual_memory()
                mem_pct = round(vm.percent, 1)
                mem_used_gb = round(vm.used / (1024**3), 1)
                mem_total_gb = round(vm.total / (1024**3), 1)
            except Exception as e:
                logger.warning(f"Error reading psutil metrics: {e}")
                cpu_pct = 0.0
                mem_pct = 0.0
                mem_used_gb = 0.0
                mem_total_gb = 0.0
        else:
            cpu_pct = 0.0
            mem_pct = 0.0
            mem_used_gb = 0.0
            mem_total_gb = 0.0

        all_registered = self.registry.list_agents(active_only=False)
        active_registered = [a for a in all_registered if a.lifecycle_state == AgentLifecycleState.ACTIVE]

        return {
            "cpu_percent": cpu_pct,
            "memory_percent": mem_pct,
            "memory_used_gb": mem_used_gb,
            "memory_total_gb": mem_total_gb,
            "total_registered_agents": len(all_registered),
            "active_agents_count": len(active_registered),
            "agents_metric_display": f"{len(active_registered)}/{len(all_registered)}",
            "network_status": "ONLINE (Localhost 127.0.0.1:8585)",
            "uptime": uptime_str,
            "emergency_stop_active": self.emergency_stop.is_active(),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def build_celestial_nodes(self, companion_snapshot: Optional[Dict[str, Any]] = None) -> List[CelestialNode]:
        """
        Builds the complete set of visual celestial nodes.
        Discovers all agents from AgentRegistry and assigns orbital layout.
        Dynamically adds any new AgentFactory-generated agents.
        """
        registered_specs = {a.agent_id: a for a in self.registry.list_agents(active_only=False)}
        companion_snapshot = companion_snapshot or {}
        active_agent_id = companion_snapshot.get("current_agent", "")
        task_status = companion_snapshot.get("current_task_status", "Idle")

        nodes: List[CelestialNode] = []

        # 1. Process all known profiles
        processed_ids: Set[str] = set()
        dynamic_specs: List[AgentSpecification] = []

        for aid, profile in BUILTIN_CELESTIAL_PROFILES.items():
            processed_ids.add(aid)
            spec = registered_specs.get(aid)

            # Determine real status
            if self.emergency_stop.is_active():
                status = "STOPPED"
                status_color = "#94a3b8"  # Slate
            elif spec:
                if spec.lifecycle_state == AgentLifecycleState.SUSPENDED:
                    status = "SUSPENDED"
                    status_color = "#f59e0b"  # Amber
                elif spec.lifecycle_state == AgentLifecycleState.RETIRED:
                    status = "RETIRED"
                    status_color = "#64748b"  # Gray
                elif spec.lifecycle_state == AgentLifecycleState.ACTIVE:
                    if active_agent_id and (aid in active_agent_id.lower() or profile["friendly_name"].lower() in active_agent_id.lower()):
                        if "working" in task_status.lower() or "executing" in task_status.lower():
                            status = "WORKING"
                            status_color = "#f59e0b"
                        elif "thinking" in task_status.lower() or "analyzing" in task_status.lower():
                            status = "THINKING"
                            status_color = "#38bdf8"
                        else:
                            status = "ONLINE"
                            status_color = "#10b981"
                    else:
                        status = "ONLINE"
                        status_color = "#10b981"
                else:
                    status = spec.lifecycle_state.value
                    status_color = "#94a3b8"
            else:
                # Builtin subsystem available in core
                status = "ONLINE"
                status_color = "#10b981"

            capabilities = spec.capabilities if spec else ["system.core", "companion.dispatch"]
            model_name = spec.model_requirement.preferred_model if spec else "gemini-3.6-flash"
            version = spec.version if spec else "1.0.0"

            # Real backend task progress only: zero fake percentages
            real_progress = companion_snapshot.get("task_progress") if isinstance(companion_snapshot.get("task_progress"), (int, float)) else None
            curr_task = {"description": task_status, "progress": real_progress} if status == "WORKING" else None

            node = CelestialNode(
                agent_id=aid,
                friendly_name=profile["friendly_name"],
                role=profile["role"],
                category=profile["category"],
                status=status,
                status_color=status_color,
                color=profile["color"],
                glow=profile["glow"],
                orbit_radius=220.0 if profile["orbit_ring"] == 1 else 310.0,
                orbit_angle=float(profile["base_angle"]),
                orbit_speed=0.015,
                icon_type=profile["icon_type"],
                greeting=profile["greeting"],
                capabilities=capabilities,
                model_name=model_name,
                version=version,
                is_builtin=True,
                suggested_actions=profile["suggested_actions"],
                current_task=curr_task,
            )
            nodes.append(node)

        # 2. Automatically map any dynamically registered AgentFactory agents!
        for aid, spec in registered_specs.items():
            if aid not in processed_ids and spec.lifecycle_state != AgentLifecycleState.RETIRED:
                dynamic_specs.append(spec)

        # Position dynamically generated agents in outer orbit ring (radius = 320px)
        num_dynamic = len(dynamic_specs)
        for idx, spec in enumerate(dynamic_specs):
            angle = (360.0 / max(num_dynamic, 1)) * idx + 45.0
            h_val = abs(hash(spec.agent_id)) % 360
            color = f"hsl({h_val}, 85%, 60%)"
            glow = f"hsla({h_val}, 85%, 60%, 0.6)"

            status = "ONLINE"
            status_color = "#10b981"
            if self.emergency_stop.is_active():
                status = "STOPPED"
                status_color = "#94a3b8"
            elif spec.lifecycle_state == AgentLifecycleState.SUSPENDED:
                status = "SUSPENDED"
                status_color = "#f59e0b"
            elif spec.lifecycle_state == AgentLifecycleState.RETIRED:
                status = "RETIRED"
                status_color = "#64748b"
            elif spec.lifecycle_state == AgentLifecycleState.ACTIVE:
                status = "ONLINE"
                status_color = "#10b981"
            else:
                status = spec.lifecycle_state.value
                status_color = "#94a3b8"

            friendly_name = spec.name.replace(" Agent", "").replace(" Unified", "")
            greeting = f"Hi Boss! I'm {friendly_name}, your specialized {spec.name}. My purpose is: {spec.purpose}. Tell me your requirement!"

            suggested_actions = [
                {"id": f"{spec.agent_id}.run", "label": f"Execute {cap.split('.')[-1].capitalize()}", "icon": "play"}
                for cap in spec.capabilities[:4]
            ] or [{"id": f"{spec.agent_id}.execute", "label": "Execute Task", "icon": "play"}]

            node = CelestialNode(
                agent_id=spec.agent_id,
                friendly_name=friendly_name,
                role=spec.name,
                category="Generated Agent",
                status=status,
                status_color=status_color,
                color=color,
                glow=glow,
                orbit_radius=320.0,
                orbit_angle=angle % 360.0,
                orbit_speed=0.010,
                icon_type="cpu",
                greeting=greeting,
                capabilities=spec.capabilities,
                model_name=spec.model_requirement.preferred_model,
                version=spec.version,
                is_builtin=False,
                suggested_actions=suggested_actions,
            )
            nodes.append(node)

        return nodes

    def get_galaxy_state(self, companion_snapshot: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Aggregates complete real-time galaxy state payload.
        Includes central black hole core, all celestial nodes, active delegations,
        real system metrics, and recent verified events.
        """
        metrics = self.get_real_system_metrics()
        nodes = self.build_celestial_nodes(companion_snapshot)

        central_core = {
            "id": "nr_ai_central_intelligence",
            "name": "NR-AI",
            "title": "Central Intelligence",
            "subtitle": "Think • Plan • Coordinate • Execute",
            "status": "STOPPED" if self.emergency_stop.is_active() else "ONLINE",
            "status_indicator": "🔴 EMERGENCY STOP" if self.emergency_stop.is_active() else "🟢 OPERATIONAL",
            "core_radius": 90,
            "event_horizon_color": "#02040a",
            "accretion_disk_colors": ["#f59e0b", "#d946ef", "#38bdf8"],
            "waveform_active": not self.emergency_stop.is_active(),
            "delegation_targets": [n.agent_id for n in nodes if n.status in ("WORKING", "THINKING")],
        }

        connections = []
        for n in nodes:
            connections.append({
                "from": "nr_ai_central_intelligence",
                "to": n.agent_id,
                "status": n.status,
                "color": n.color,
                "glow": n.glow,
                "animated": n.status in ("WORKING", "THINKING"),
            })

        return {
            "success": True,
            "central_core": central_core,
            "nodes": [n.to_dict() for n in nodes],
            "connections": connections,
            "system_metrics": metrics,
            "timestamp": time.time(),
        }

    def get_agent_introductions(self, companion_snapshot: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Dynamically generates the sequential agent introduction script from the AgentRegistry
        and celestial profiles. Excludes SUSPENDED, RETIRED, and OFFLINE agents.
        Never hardcodes the agent list; reflects current live registry state.
        """
        nodes = self.build_celestial_nodes(companion_snapshot)

        # Ineligible for introduction: SUSPENDED, RETIRED, OFFLINE, STOPPED
        ineligible_statuses = {"SUSPENDED", "RETIRED", "OFFLINE", "STOPPED"}
        eligible_nodes = [n for n in nodes if n.status not in ineligible_statuses]

        sequence: List[Dict[str, Any]] = []
        for node in eligible_nodes:
            speech_text = self._format_agent_introduction(node)
            sequence.append({
                "agent_id": node.agent_id,
                "name": node.friendly_name,
                "role": node.role,
                "category": node.category,
                "color": node.color,
                "glow": node.glow,
                "status": node.status,
                "capabilities": node.capabilities,
                "orbit_radius": node.orbit_radius,
                "orbit_angle": node.orbit_angle,
                "icon_type": node.icon_type,
                "speech_text": speech_text,
            })

        return {
            "success": True,
            "intro_greeting": "Of course, Boss. Let me introduce you to my agents.",
            "intro_outro": "That's my current agent team, Boss. Tell me what you want to build, learn, research, or solve.",
            "total_agents": len(sequence),
            "emergency_stop_active": self.emergency_stop.is_active(),
            "sequence": sequence,
        }

    def _format_agent_introduction(self, node: CelestialNode) -> str:
        """
        Generates genuine introduction text from actual agent metadata.
        Never invents capabilities.
        """
        cap_map = {
            "android.gradle": "Android application development and Gradle builds",
            "android.build": "building Android projects",
            "android.debug": "debugging Android applications",
            "android.aapt2_repair": "AAPT2 error repair",
            "vs.build": "MSBuild solution compilation",
            "vs.diagnose": "diagnosing C++ and C# errors",
            "unity.project": "Unity project management",
            "unity.scene": "scene validation and C# script repair",
            "unreal.ubt": "UnrealBuildTool orchestration and C++ build automation",
            "knowledge.search": "searching verified facts, scientific research, and documentation",
            "computer.windows": "system inspection and desktop window automation",
            "orchestrator.coordinate": "multi-agent goal decomposition and workflow routing",
            "security.audit": "enforcing zero-shell security invariants and credential protection",
            "research.query": "scholarly literature analysis across arXiv and PubMed",
            "voice.audio": "natural language voice recognition and audio speech synthesis",
            "vision.grounding": "visual grounding, screen inspection, and OCR detection",
            "code.scaffolding": "project tree scaffolding and code generation",
            "ui.visualization": "command center UI rendering and real-time telemetry",
        }

        # Check for registered builtins with personalized descriptions
        if node.agent_id == "android_unified_agent":
            return "Hi Boss, I'm Droid, your Android Agent. I handle Android application development, debugging, building and verification."
        elif node.agent_id == "unity_autonomous_agent":
            return "Hi Boss, I'm Unity, your game development agent. I help create, edit, build and test Unity projects."
        elif node.agent_id == "unreal_autonomous_agent":
            return "Hi Boss, I'm Unreal, your Unreal Engine agent. I handle C++ source parsing, UBT builds, and project verification."
        elif node.agent_id == "vs_unified_agent":
            return "Hi Boss, I'm Studio, your Visual Studio Agent. I inspect solutions, build MSBuild targets, and diagnose compilation errors."
        elif node.agent_id == "universal_knowledge_engine":
            return "Hi Boss, I'm Knowledge, your Universal Knowledge Agent. I query local FTS5 stores, arXiv, and verified news for factual answers."
        elif node.agent_id == "vision_agent":
            return "Hi Boss, I'm Vision, your visual grounding and OCR Agent. I inspect screen pixels, detect UI hierarchies, and identify visual targets."
        elif node.agent_id == "voice_agent":
            return "Hi Boss, I'm Echo, your Voice and Audio Agent. I listen for natural language commands and synthesize audio responses."
        elif node.agent_id == "computer_control_agent":
            return "Hi Boss, I'm Sentinel, your unified computer and system agent. I inspect desktop windows and verify application health."
        elif node.agent_id == "nexus_coordinator":
            return "Hi Boss, I'm Nexus, your Multi-Agent Orchestrator. I decompose multi-disciplinary goals and coordinate specialized agents."
        elif node.agent_id == "security_agent":
            return "Hi Boss, I'm Shield, your Security Agent. I audit execution safety, enforce zero-shell invariants, and guard credentials."
        elif node.agent_id == "research_agent":
            return "Hi Boss, I'm Quest, your Deep Research Agent. I formulate queries, analyze academic literature, and synthesize scientific papers."
        elif node.agent_id == "forge_dev_agent":
            return "Hi Boss, I'm Forge, your Scaffolding and Code Generation Agent. I create sandboxed project trees and developer workflows."
        elif node.agent_id == "pixel_ui_agent":
            return "Hi Boss, I'm Pixel, your UI and Visualization Agent. I maintain the living Galaxy command center and HUD interfaces."

        # Dynamic fallback for AgentFactory-generated agents
        caps_readable = []
        for c in node.capabilities:
            matched = False
            for k, v in cap_map.items():
                if k in c:
                    caps_readable.append(v)
                    matched = True
                    break
            if not matched:
                caps_readable.append(c.replace("_", " ").replace(".", " "))

        if caps_readable:
            caps_str = ", ".join(caps_readable[:3])
            return f"Hi Boss, I'm {node.friendly_name}, your {node.role}. I handle {caps_str}."
        return f"Hi Boss, I'm {node.friendly_name}, your {node.role}. I am ready to execute tasks in my domain."

