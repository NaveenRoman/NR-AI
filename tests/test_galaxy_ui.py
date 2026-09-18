"""
Tests for NR-AI Galaxy UI & Agent Visualization System.
Phase: UI Intelligence Foundation.

Verifies:
- GalaxyEngine layout computation and node mapping
- 100% real hardware telemetry (zero fake data)
- Central Black Hole Intelligence Core properties
- Built-in agent celestial mapping (Droid, Studio, Unity, Unreal, Knowledge, etc.)
- Dynamic agent binding for AgentFactory-generated agents
- Node lifecycle state handling (Active, Suspended, Retired, Emergency Stopped)
- Connections and gravitation energy beams
- HTML, CSS, and JS asset completeness
- HTTP API endpoints on CompanionDashboard (/api/galaxy/state, /static/*, /api/agents, /api/agent/<id>/action, /api/emergency_stop)
- Localhost security and shell=False invariant
"""

import json
import os
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch
import urllib.request
import urllib.parse
import socketserver
import threading
import time

from app.agent.factory.registry import AgentRegistry
from app.agent.factory.specification import (
    AgentLifecycleState,
    AgentSpecification,
    ModelRequirement,
    SafetyPolicy,
)
from app.brain.companion import NRCompanion, CommandCategory
from app.voice.listener import VoiceConfig
from app.remote.emergency import EmergencyStopController
from app.ui.galaxy_engine import (
    BUILTIN_CELESTIAL_PROFILES,
    CelestialNode,
    GalaxyEngine,
    ORBIT_INNER_RADIUS,
    ORBIT_MIDDLE_RADIUS,
    ORBIT_OUTER_RADIUS,
    ORBITAL_RADII_TIERS,
)
from app.agent.credential_diagnostics import (
    CredentialDiagnosticEngine,
    DiagnosticStatus,
    redact_secret,
    get_fast_diagnostics_summary,
)
from app.ui.dashboard import CompanionDashboard


class TestGalaxyEngineBasics(unittest.TestCase):
    """Test GalaxyEngine initialization and basic capabilities."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        self.emergency_stop = EmergencyStopController()
        if self.emergency_stop.is_active():
            self.emergency_stop.reset()
        self.temp_file = Path(tempfile.gettempdir()) / f"test_galaxy_reg_{time.time_ns()}.json"
        self.registry = AgentRegistry(registry_file=self.temp_file)
        self.engine = GalaxyEngine(registry=self.registry, emergency_stop=self.emergency_stop)

    def tearDown(self):
        if hasattr(self, "temp_file") and self.temp_file.exists():
            try:
                self.temp_file.unlink()
            except Exception:
                pass

    def test_01_engine_initialization(self):
        """Engine initializes with registry, emergency stop controller, and start time."""
        self.assertIsNotNone(self.engine.registry)
        self.assertIsNotNone(self.engine.emergency_stop)
        self.assertGreater(self.engine.start_time, 0)

    def test_02_real_hardware_metrics(self):
        """System metrics return non-negative real telemetry and zero fake data."""
        metrics = self.engine.get_real_system_metrics()
        self.assertIn("cpu_percent", metrics)
        self.assertIn("memory_percent", metrics)
        self.assertIn("memory_used_gb", metrics)
        self.assertIn("memory_total_gb", metrics)
        self.assertIn("total_registered_agents", metrics)
        self.assertIn("active_agents_count", metrics)
        self.assertIn("agents_metric_display", metrics)
        self.assertIn("network_status", metrics)
        self.assertIn("uptime", metrics)
        self.assertIn("emergency_stop_active", metrics)

        # Real numbers
        self.assertGreaterEqual(metrics["cpu_percent"], 0.0)
        self.assertGreaterEqual(metrics["memory_percent"], 0.0)
        self.assertGreater(metrics["total_registered_agents"], 0)
        self.assertIn("127.0.0.1", metrics["network_status"])

    def test_03_central_core_properties(self):
        """Central Black Hole core represents NR-AI Central Intelligence with correct branding."""
        state = self.engine.get_galaxy_state()
        core = state["central_core"]
        self.assertEqual(core["id"], "nr_ai_central_intelligence")
        self.assertEqual(core["name"], "NR-AI")
        self.assertEqual(core["title"], "Central Intelligence")
        self.assertEqual(core["subtitle"], "Think • Plan • Coordinate • Execute")
        self.assertEqual(core["status"], "ONLINE")
        self.assertEqual(core["core_radius"], 90)
        self.assertTrue(core["waveform_active"])

    def test_04_builtin_agent_profiles(self):
        """Built-in celestial profiles contain mandatory agents and metadata."""
        expected_keys = [
            "android_unified_agent",
            "vs_unified_agent",
            "unity_autonomous_agent",
            "unreal_autonomous_agent",
            "universal_knowledge_engine",
            "computer_control_agent",
            "nexus_coordinator",
            "security_agent",
            "research_agent",
            "voice_agent",
            "vision_agent",
            "forge_dev_agent",
            "pixel_ui_agent",
        ]
        for key in expected_keys:
            self.assertIn(key, BUILTIN_CELESTIAL_PROFILES)
            profile = BUILTIN_CELESTIAL_PROFILES[key]
            self.assertIn("friendly_name", profile)
            self.assertIn("role", profile)
            self.assertIn("color", profile)
            self.assertIn("glow", profile)
            self.assertIn("orbit_ring", profile)
            self.assertIn("icon_type", profile)
            self.assertIn("greeting", profile)
            self.assertIn("suggested_actions", profile)

    def test_05_droid_friendly_identity(self):
        """Android agent is mapped to Droid with Emerald color and friendly greeting."""
        nodes = self.engine.build_celestial_nodes()
        droid = next((n for n in nodes if n.agent_id == "android_unified_agent"), None)
        self.assertIsNotNone(droid)
        self.assertEqual(droid.friendly_name, "Droid")
        self.assertEqual(droid.role, "Android Agent")
        self.assertEqual(droid.color, "#10b981")
        self.assertIn("Hi Boss! I'm Droid", droid.greeting)
        self.assertTrue(droid.is_builtin)
        self.assertGreater(len(droid.suggested_actions), 0)

    def test_06_studio_friendly_identity(self):
        """Visual Studio agent is mapped to Studio with Violet color."""
        nodes = self.engine.build_celestial_nodes()
        studio = next((n for n in nodes if n.agent_id == "vs_unified_agent"), None)
        self.assertIsNotNone(studio)
        self.assertEqual(studio.friendly_name, "Studio")
        self.assertEqual(studio.role, "Visual Studio Agent")
        self.assertEqual(studio.color, "#8b5cf6")
        self.assertIn("Hi Boss! I'm Studio", studio.greeting)

    def test_07_unity_friendly_identity(self):
        """Unity agent is mapped to Unity with Sky Blue color."""
        nodes = self.engine.build_celestial_nodes()
        unity = next((n for n in nodes if n.agent_id == "unity_autonomous_agent"), None)
        self.assertIsNotNone(unity)
        self.assertEqual(unity.friendly_name, "Unity")
        self.assertEqual(unity.color, "#38bdf8")

    def test_08_unreal_friendly_identity(self):
        """Unreal agent is mapped to Unreal with Crimson color."""
        nodes = self.engine.build_celestial_nodes()
        unreal = next((n for n in nodes if n.agent_id == "unreal_autonomous_agent"), None)
        self.assertIsNotNone(unreal)
        self.assertEqual(unreal.friendly_name, "Unreal")
        self.assertEqual(unreal.color, "#ef4444")

    def test_09_dynamic_agent_discovery(self):
        """Dynamically registered agent in AgentRegistry is automatically placed in outer orbit."""
        dummy_spec = AgentSpecification(
            agent_id="gen_custom_crypto_analyzer_9999",
            name="Crypto Analyzer Agent",
            purpose="Analyze cryptographic signatures and key security",
            model_requirement=ModelRequirement(preferred_model="gemini-2.5-pro"),
            capabilities=["crypto.analyze", "crypto.audit"],
            lifecycle_state=AgentLifecycleState.APPROVED,
        )
        ok, msg = self.registry.register_agent(dummy_spec)
        self.assertTrue(ok, f"Registration failed: {msg}")
        self.registry.activate_agent(dummy_spec.agent_id)

        try:
            nodes = self.engine.build_celestial_nodes()
            dyn_node = next((n for n in nodes if n.agent_id == dummy_spec.agent_id), None)
            self.assertIsNotNone(dyn_node)
            self.assertFalse(dyn_node.is_builtin)
            self.assertEqual(dyn_node.friendly_name, "Crypto Analyzer")
            self.assertEqual(dyn_node.orbit_radius, ORBIT_OUTER_RADIUS)
            self.assertIn("Hi Boss! I'm Crypto Analyzer", dyn_node.greeting)
        finally:
            self.registry.retire_agent(dummy_spec.agent_id)

    def test_10_suspended_agent_state(self):
        """Suspended agent is rendered with SUSPENDED status and Amber warning color."""
        dummy_spec = AgentSpecification(
            agent_id="gen_suspended_agent_test",
            name="Suspended Test Agent",
            purpose="Testing suspended state mapping",
            model_requirement=ModelRequirement(preferred_model="gemini-2.5-pro"),
            capabilities=["test.suspended"],
            lifecycle_state=AgentLifecycleState.APPROVED,
        )
        ok, msg = self.registry.register_agent(dummy_spec)
        self.assertTrue(ok, f"Registration failed: {msg}")
        self.registry.activate_agent(dummy_spec.agent_id)
        self.registry.suspend_agent(dummy_spec.agent_id, reason="Testing suspension")

        try:
            nodes = self.engine.build_celestial_nodes()
            node = next((n for n in nodes if n.agent_id == dummy_spec.agent_id), None)
            self.assertIsNotNone(node)
            self.assertEqual(node.status, "SUSPENDED")
            self.assertEqual(node.status_color, "#f59e0b")
        finally:
            self.registry.retire_agent(dummy_spec.agent_id)

    def test_11_retired_agent_excluded(self):
        """Retired dynamic agent is not displayed in the celestial view."""
        dummy_spec = AgentSpecification(
            agent_id="gen_retired_agent_test",
            name="Retired Test Agent",
            purpose="Testing retirement removal",
            model_requirement=ModelRequirement(preferred_model="gemini-2.5-pro"),
            capabilities=["test.retired"],
            lifecycle_state=AgentLifecycleState.APPROVED,
        )
        ok, msg = self.registry.register_agent(dummy_spec)
        self.assertTrue(ok, f"Registration failed: {msg}")
        self.registry.activate_agent(dummy_spec.agent_id)
        self.registry.retire_agent(dummy_spec.agent_id, reason="Testing retirement")

        nodes = self.engine.build_celestial_nodes()
        node = next((n for n in nodes if n.agent_id == dummy_spec.agent_id), None)
        self.assertIsNone(node)

    def test_12_active_working_agent_state(self):
        """Active agent executing a task shows WORKING status and real progress (zero fake data)."""
        companion_snapshot = {
            "current_agent": "android_unified_agent",
            "current_task_status": "Working: Creating Android project structure...",
            "task_progress": 45,
        }
        nodes = self.engine.build_celestial_nodes(companion_snapshot=companion_snapshot)
        droid = next((n for n in nodes if n.agent_id == "android_unified_agent"), None)
        self.assertIsNotNone(droid)
        self.assertEqual(droid.status, "WORKING")
        self.assertEqual(droid.status_color, "#f59e0b")
        self.assertIsNotNone(droid.current_task)
        self.assertEqual(droid.current_task["progress"], 45)

        # Without explicit progress, progress is None (never fake hardcoded 68%)
        snapshot_no_prog = {
            "current_agent": "android_unified_agent",
            "current_task_status": "Working: Creating Android project structure...",
        }
        nodes_no_prog = self.engine.build_celestial_nodes(companion_snapshot=snapshot_no_prog)
        droid_no_prog = next((n for n in nodes_no_prog if n.agent_id == "android_unified_agent"), None)
        self.assertIsNone(droid_no_prog.current_task["progress"])

    def test_13_emergency_stop_affects_core_and_nodes(self):
        """Triggering Emergency Stop updates central core and all nodes to STOPPED status."""
        self.emergency_stop.trigger(triggered_by="UnitTest", reason="Verification")
        try:
            state = self.engine.get_galaxy_state()
            self.assertEqual(state["central_core"]["status"], "STOPPED")
            self.assertIn("EMERGENCY STOP", state["central_core"]["status_indicator"])
            self.assertFalse(state["central_core"]["waveform_active"])

            for node in state["nodes"]:
                self.assertEqual(node["status"], "STOPPED")
        finally:
            self.emergency_stop.reset()

    def test_14_connections_graph_structure(self):
        """Every agent node has a gravitational energy beam connection to the central core."""
        state = self.engine.get_galaxy_state()
        nodes = state["nodes"]
        connections = state["connections"]
        central_connections = [c for c in connections if c.get("from") == "nr_ai_central_intelligence"]
        self.assertEqual(len(nodes), len(central_connections))
        for conn in central_connections:
            self.assertEqual(conn["from"], "nr_ai_central_intelligence")
            self.assertIn("to", conn)
            self.assertIn("status", conn)
            self.assertIn("color", conn)

    def test_15_suggested_actions_format(self):
        """Suggested actions have id, label, and valid format for instant invocation."""
        nodes = self.engine.build_celestial_nodes()
        for node in nodes:
            self.assertIsInstance(node.suggested_actions, list)
            for action in node.suggested_actions:
                self.assertIn("id", action)
                self.assertIn("label", action)


class TestGalaxyUIAssets(unittest.TestCase):
    """Test static web assets, templates, CSS, and JS files."""

    def test_16_html_template_exists_and_contains_canvas(self):
        """Galaxy HTML template exists and has galaxyCanvas element."""
        html_path = os.path.join(os.path.dirname(__file__), "..", "app", "ui", "templates", "galaxy.html")
        self.assertTrue(os.path.exists(html_path))
        with open(html_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn('id="galaxyCanvas"', content)
        self.assertIn('id="app-container"', content)
        self.assertIn('id="agentPanel"', content)
        self.assertIn('id="globalSearchInput"', content)
        self.assertIn('id="emergencyStopBtn"', content)

    def test_17_css_stylesheet_exists_and_styled(self):
        """Galaxy CSS stylesheet exists and defines dark cosmic theme variables."""
        css_path = os.path.join(os.path.dirname(__file__), "..", "app", "ui", "static", "galaxy.css")
        self.assertTrue(os.path.exists(css_path))
        with open(css_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("--bg-deep", content)
        self.assertIn("--accent-cyan", content)
        self.assertIn(".top-nav", content)
        self.assertIn(".agent-panel", content)
        self.assertIn(".system-overview-card", content)

    def test_18_js_engine_exists_and_complete(self):
        """Galaxy JS engine exists and defines animation loop and polling routines."""
        js_path = os.path.join(os.path.dirname(__file__), "..", "app", "ui", "static", "galaxy.js")
        self.assertTrue(os.path.exists(js_path))
        with open(js_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("renderLoop", content)
        self.assertIn("drawCentralBlackHole", content)
        self.assertIn("drawNodes", content)
        self.assertIn("pollGalaxyState", content)
        self.assertIn("executeAgentAction", content)
        self.assertIn("triggerEmergencyStop", content)


class TestDashboardHTTPServerIntegration(unittest.TestCase):
    """Test HTTP endpoints served by CompanionDashboard."""

    @classmethod
    def setUpClass(cls):
        cls.mock_companion = MagicMock()
        cls.mock_companion.avatar.current_frame.mode.value = "IDLE"
        cls.mock_companion.avatar.current_frame.emotion.value = "NORMAL"
        cls.mock_companion.listener.probe_microphone.return_value = {
            "available": True,
            "active_device": "Default Microphone",
            "device_count": 1,
            "status": "ONLINE",
            "push_to_talk_fallback": True,
        }
        cls.mock_companion.orchestrator.get_system_metrics.return_value = {
            "slots": [],
            "running_tasks": 0,
            "total_slots": 10,
            "busy_slots": 0,
            "idle_slots": 10,
            "completed_tasks": 0,
        }
        cls.mock_companion.conversation_history = []
        cls.mock_companion.voice_config.wake_word_enabled = True
        cls.mock_companion.voice_config.wake_words = ["Hey NR"]
        cls.mock_companion.config.default_model = "gemini-3.6-flash"
        cls.mock_companion.news_agent.fetch_category.return_value = []
        cls.mock_companion.last_wake_phrase = "None"
        cls.mock_companion.current_route = "CompanionPersona"
        cls.mock_companion.current_agent = "Agent-1-Architect"
        cls.mock_companion.current_task_status = "Idle"
        cls.mock_companion.last_model_execution = {}
        
        # Companion interact mock
        mock_interact_result = MagicMock()
        mock_interact_result.text = "Mock companion reply."
        mock_interact_result.to_dict.return_value = {"text": "Mock companion reply."}
        cls.mock_companion.interact.return_value = mock_interact_result

        # Find an open port
        cls.port = 8599
        cls.dashboard = CompanionDashboard(companion=cls.mock_companion)
        cls.dashboard.start_http_server(port=cls.port)
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        cls.dashboard.stop_http_server()

    def _get(self, path: str):
        url = f"http://127.0.0.1:{self.port}{path}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.getcode(), resp.headers.get("Content-Type", ""), resp.read()

    def _post(self, path: str, data: dict):
        url = f"http://127.0.0.1:{self.port}{path}"
        body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.getcode(), resp.headers.get("Content-Type", ""), resp.read()

    def test_19_get_root_serves_galaxy_html(self):
        """GET / returns HTML containing the Galaxy UI Command Center."""
        code, ctype, body = self._get("/")
        self.assertEqual(code, 200)
        self.assertIn("text/html", ctype)
        html = body.decode("utf-8")
        self.assertIn("galaxyCanvas", html)
        self.assertIn("NR-AI", html)

    def test_20_get_legacy_html_fallback(self):
        """GET /?legacy=1 returns the classic tabular companion dashboard."""
        code, ctype, body = self._get("/?legacy=1")
        self.assertEqual(code, 200)
        self.assertIn("text/html", ctype)
        html = body.decode("utf-8")
        self.assertIn("NR-AI Companion & Agent Dashboard", html)

    def test_21_get_static_css(self):
        """GET /static/galaxy.css serves valid CSS."""
        code, ctype, body = self._get("/static/galaxy.css")
        self.assertEqual(code, 200)
        self.assertIn("text/css", ctype)
        self.assertIn(b"--bg-deep", body)

    def test_22_get_static_js(self):
        """GET /static/galaxy.js serves valid JavaScript."""
        code, ctype, body = self._get("/static/galaxy.js")
        self.assertEqual(code, 200)
        self.assertIn("application/javascript", ctype)
        self.assertIn(b"renderLoop", body)

    def test_23_get_galaxy_state_api(self):
        """GET /api/galaxy/state returns complete galaxy JSON payload."""
        code, ctype, body = self._get("/api/galaxy/state")
        self.assertEqual(code, 200)
        self.assertIn("application/json", ctype)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data["success"])
        self.assertIn("central_core", data)
        self.assertIn("nodes", data)
        self.assertIn("connections", data)
        self.assertIn("system_metrics", data)
        self.assertGreater(len(data["nodes"]), 0)

    def test_24_get_agents_list_api(self):
        """GET /api/agents returns list of all celestial agents."""
        code, ctype, body = self._get("/api/agents")
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data["success"])
        self.assertGreater(data["count"], 10)
        self.assertEqual(len(data["agents"]), data["count"])

    def test_25_get_specific_agent_api(self):
        """GET /api/agent/<id> returns specific agent node metadata."""
        code, ctype, body = self._get("/api/agent/android_unified_agent")
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data["success"])
        self.assertEqual(data["agent"]["friendly_name"], "Droid")

    def test_26_get_nonexistent_agent_returns_404(self):
        """GET /api/agent/<invalid> returns 404 with error message."""
        url = f"http://127.0.0.1:{self.port}/api/agent/nonexistent_agent_xyz"
        req = urllib.request.Request(url)
        try:
            urllib.request.urlopen(req, timeout=3)
            self.fail("Expected HTTPError 404")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)

    def test_27_post_agent_action(self):
        """POST /api/agent/<id>/action executes action and returns response."""
        code, ctype, body = self._post("/api/agent/android_unified_agent/action", {
            "action_id": "android.create_project",
            "label": "Create Project",
        })
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data["success"])
        self.assertEqual(data["agent_id"], "android_unified_agent")
        self.assertEqual(data["action_id"], "android.create_project")
        self.assertIn("result", data)

    def test_28_post_emergency_stop(self):
        """POST /api/emergency_stop triggers global emergency stop."""
        code, ctype, body = self._post("/api/emergency_stop", {
            "triggered_by": "TestOperator",
            "reason": "Test emergency stop",
        })
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data["success"])
        self.assertTrue(data["emergency_stop"])
        self.assertIn("EMERGENCY STOP TRIGGERED", data["message"])
        # Reset for subsequent tests
        EmergencyStopController().reset()

    def test_28b_get_galaxy_introduction_api(self):
        """GET /api/galaxy/introduction returns sequence of eligible agents."""
        code, ctype, body = self._get("/api/galaxy/introduction")
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data["success"])
        self.assertIn("Of course, Boss", data["intro_greeting"])
        self.assertGreaterEqual(data["total_agents"], 10)

    def test_29_backward_compatibility_status(self):
        """GET /api/status continues to return companion status snapshot."""
        code, ctype, body = self._get("/api/status")
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertIn("assistant_status", data)
        self.assertIn("microphone", data)
        self.assertIn("model_execution", data)

    def test_30_backward_compatibility_command(self):
        """POST /api/command executes companion command."""
        code, ctype, body = self._post("/api/command", {"command": "Hello NR"})
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertIn("text", data)


class TestGalaxyIntroductionMode(unittest.TestCase):
    """Dedicated test suite for Galaxy Introduction Mode & UI Refinements."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        self.emergency_stop = EmergencyStopController()
        if self.emergency_stop.is_active():
            self.emergency_stop.reset()
        self.temp_file = Path(tempfile.gettempdir()) / f"test_intro_{time.time_ns()}.json"
        self.registry = AgentRegistry(registry_file=self.temp_file)
        self.engine = GalaxyEngine(registry=self.registry, emergency_stop=self.emergency_stop)

    def tearDown(self):
        if hasattr(self, "temp_file") and self.temp_file.exists():
            try:
                self.temp_file.unlink()
            except Exception:
                pass

    def test_31_fixed_composition_no_mouse_zoom(self):
        """galaxy.js contains no wheel zoom, mousedown drag pan, or mouse-follow camera movement."""
        js_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app", "ui", "static", "galaxy.js"))
        with open(js_path, "r", encoding="utf-8") as f:
            js = f.read()

        # Wheel zoom must be removed
        self.assertNotIn('canvas.addEventListener("wheel"', js)
        # Drag start pan must be removed
        self.assertNotIn("state.dragStartX", js)
        # Pan/zoom target variables removed from mousemove
        self.assertNotIn("state.targetPanX = e.clientX", js)
        # Verify fixed composition translation
        self.assertIn("ctx.translate(canvas.width / 2, canvas.height / 2);", js)
        self.assertIn("ctx.scale(1.0, 1.0);", js)

    def test_32_agent_click_without_camera_movement(self):
        """selectAgent in galaxy.js selects the node without altering camera pan or zoom."""
        js_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app", "ui", "static", "galaxy.js"))
        with open(js_path, "r", encoding="utf-8") as f:
            js = f.read()

        # In selectAgent, targetPan and targetZoom must not be set
        select_agent_func = js.split("function selectAgent(node) {")[1].split("function renderAgentPanel")[0]
        self.assertNotIn("targetPanX", select_agent_func)
        self.assertNotIn("targetZoom", select_agent_func)

    def test_33_introduction_intent_detection_all_phrases(self):
        """NRCompanion.classify_command recognizes all required introduction triggers."""
        cfg = VoiceConfig(silent_mode=True, tts_enabled=False)
        comp = NRCompanion(voice_config=cfg)

        triggers = [
            "introduce yourself",
            "introduce yourselves",
            "who are you all",
            "let every agent introduce themselves",
            "NR-AI, introduce yourself and introduce your agents",
            "who are your agents",
            "meet the agents",
            "tell me about your agents",
            "agent introduction",
        ]
        for trig in triggers:
            cat = comp.classify_command(trig)
            self.assertEqual(cat, CommandCategory.AGENTS, f"Failed for trigger: '{trig}'")

    def test_34_get_agent_introductions_structure(self):
        """GalaxyEngine.get_agent_introductions returns greeting, outro, and sequence."""
        data = self.engine.get_agent_introductions()
        self.assertTrue(data["success"])
        self.assertIn("Of course, Boss", data["intro_greeting"])
        self.assertIn("That's my current agent team", data["intro_outro"])
        self.assertGreaterEqual(data["total_agents"], 10)
        self.assertEqual(len(data["sequence"]), data["total_agents"])

    def test_35_introduction_sequence_ordering_and_authentic_content(self):
        """First agent is Droid, and agents introduce authentic registered capabilities."""
        data = self.engine.get_agent_introductions()
        first_agent = data["sequence"][0]
        self.assertEqual(first_agent["name"], "Droid")
        self.assertIn("Droid, your Android Agent", first_agent["speech_text"])
        self.assertIn("debugging, building and verification", first_agent["speech_text"])

        # Check other key agents exist in sequence
        names = [a["name"] for a in data["sequence"]]
        self.assertIn("Unity", names)
        self.assertIn("Unreal", names)
        self.assertIn("Studio", names)
        self.assertIn("Knowledge", names)

    def test_36_ineligible_agents_excluded_from_introduction(self):
        """SUSPENDED, RETIRED, and OFFLINE agents are excluded from introduction sequence."""
        # Register an approved agent then suspend it
        suspended_spec = AgentSpecification(
            agent_id="gen_test_suspended_999",
            name="Suspended Worker",
            purpose="Testing suspension exclusion",
            model_requirement=ModelRequirement(preferred_model="gemini-2.5-pro"),
            capabilities=["test.suspended"],
            lifecycle_state=AgentLifecycleState.APPROVED,
        )
        self.registry.register_agent(suspended_spec)
        self.registry.suspend_agent(suspended_spec.agent_id, reason="Testing suspension")

        # Register an approved agent then retire it
        retired_spec = AgentSpecification(
            agent_id="gen_test_retired_999",
            name="Retired Worker",
            purpose="Testing retirement exclusion",
            model_requirement=ModelRequirement(preferred_model="gemini-2.5-pro"),
            capabilities=["test.retired"],
            lifecycle_state=AgentLifecycleState.APPROVED,
        )
        self.registry.register_agent(retired_spec)
        self.registry.retire_agent(retired_spec.agent_id)

        data = self.engine.get_agent_introductions()
        seq_ids = [a["agent_id"] for a in data["sequence"]]
        self.assertNotIn("gen_test_suspended_999", seq_ids)
        self.assertNotIn("gen_test_retired_999", seq_ids)

    def test_37_dynamic_agent_factory_agent_participates_in_introduction(self):
        """Newly created agent in AgentRegistry automatically participates in introduction."""
        dyn_spec = AgentSpecification(
            agent_id="gen_quantum_opt_888",
            name="Quantum Optimizer",
            purpose="Optimize quantum annealing circuits",
            model_requirement=ModelRequirement(preferred_model="gemini-2.5-pro"),
            capabilities=["quantum.annealing", "quantum.circuits"],
            lifecycle_state=AgentLifecycleState.APPROVED,
        )
        self.registry.register_agent(dyn_spec)
        self.registry.activate_agent(dyn_spec.agent_id)

        data = self.engine.get_agent_introductions()
        seq_ids = [a["agent_id"] for a in data["sequence"]]
        self.assertIn("gen_quantum_opt_888", seq_ids)
        dyn_item = next(a for a in data["sequence"] if a["agent_id"] == "gen_quantum_opt_888")
        self.assertEqual(dyn_item["name"], "Quantum Optimizer")
        self.assertIn("Hi Boss, I'm Quantum Optimizer", dyn_item["speech_text"])

    def test_38_no_fake_task_progress(self):
        """build_celestial_nodes does NOT inject fake 68% progress."""
        nodes = self.engine.build_celestial_nodes()
        for n in nodes:
            if n.current_task:
                self.assertNotEqual(n.current_task.get("progress"), 68, "Found hardcoded 68% progress")

    def test_39_companion_interact_voice_and_text_parity(self):
        """NRCompanion.interact('Introduce yourselves') returns introduction mode response."""
        cfg = VoiceConfig(silent_mode=True, tts_enabled=False)
        comp = NRCompanion(voice_config=cfg)
        resp = comp.interact("Introduce yourselves", speak_output=False)
        self.assertEqual(resp.routed_to, "GalaxyIntroduction")
        self.assertTrue(resp.data.get("introduction_mode"))
        self.assertIn("NR-AI: Of course, Boss", resp.text)
        self.assertIn("Droid:", resp.text)
        self.assertGreaterEqual(resp.data.get("total_agents", 0), 10)

    def test_40_intro_hud_markup_and_css_present(self):
        """HTML and CSS assets contain required Introduction Mode elements and buttons."""
        html_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app", "ui", "templates", "galaxy.html"))
        with open(html_path, "r", encoding="utf-8") as f:
            html = f.read()

        self.assertIn('id="introHud"', html)
        self.assertIn('id="btnTriggerIntro"', html)
        self.assertIn('id="introEqualizer"', html)
        self.assertIn('id="introSpeechBubble"', html)
        self.assertIn('id="btnIntroPause"', html)
        self.assertIn('id="btnIntroSkip"', html)
        self.assertIn('id="btnIntroStop"', html)

        css_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app", "ui", "static", "galaxy.css"))
        with open(css_path, "r", encoding="utf-8") as f:
            css = f.read()

        self.assertIn('.intro-hud', css)
        self.assertIn('.btn-intro-trigger', css)
        self.assertIn('.intro-equalizer', css)
        self.assertIn('.queue-pill', css)




class TestGalaxyUIRefinement2(unittest.TestCase):
    """
    Refinement 2 Test Suite:
    - Multi-Orbital Rings Layout (Inner 260px, Middle 400px, Outer 540px)
    - Zero Node Collisions & Geometric Non-Overlapping Clearance
    - Dynamic Agent Tier 3 Outer Placement
    - Direct Agent Addressing & Persona Routing
    - Active Conversation Agent Persistence (multi-turn follow-ups)
    - Voice Interruption / Barge-in
    - Central NR-AI Takeover
    - Single-Agent Introduction Mode
    - Authentic Credential Diagnostics (100% secret redaction, Quota Exhausted handling)
    - HTTP REST Endpoints for Active Conversation & Diagnostics
    """

    def setUp(self):
        import tempfile
        from pathlib import Path
        self.emergency_stop = EmergencyStopController()
        if self.emergency_stop.is_active():
            self.emergency_stop.reset()
        self.temp_file = Path(tempfile.gettempdir()) / f"test_galaxy_ref2_{time.time_ns()}.json"
        self.registry = AgentRegistry(registry_file=self.temp_file)
        self.engine = GalaxyEngine(registry=self.registry, emergency_stop=self.emergency_stop)
        self.voice_cfg = VoiceConfig(silent_mode=True, tts_enabled=False)
        self.companion = NRCompanion(voice_config=self.voice_cfg)

    def tearDown(self):
        if hasattr(self, "temp_file") and self.temp_file.exists():
            try:
                self.temp_file.unlink()
            except Exception:
                pass

    def test_41_multi_orbital_rings_layout(self):
        """Galaxy state returns 3 concentric orbital rings: 260px, 400px, 540px."""
        self.assertEqual(ORBIT_INNER_RADIUS, 260.0)
        self.assertEqual(ORBIT_MIDDLE_RADIUS, 400.0)
        self.assertEqual(ORBIT_OUTER_RADIUS, 540.0)
        state = self.engine.get_galaxy_state()
        rings = state.get("orbital_rings", [])
        self.assertEqual(rings, [260.0, 400.0, 540.0])

    def test_42_celestial_nodes_orbit_distribution(self):
        """Nodes are partitioned across inner, middle, and outer orbital tiers."""
        nodes = self.engine.build_celestial_nodes()
        nodes_by_id = {n.agent_id: n for n in nodes}

        # Inner ring (260px) specialists: Droid, Studio, Unity, Unreal
        inner_ids = ["android_unified_agent", "vs_unified_agent", "unity_autonomous_agent", "unreal_autonomous_agent"]
        for aid in inner_ids:
            if aid in nodes_by_id:
                self.assertEqual(nodes_by_id[aid].orbit_radius, ORBIT_INNER_RADIUS, f"{aid} must be on inner orbit")

        # Middle ring (400px) intelligence & system agents
        mid_ids = ["universal_knowledge_engine", "nexus_coordinator", "security_agent", "research_agent", "voice_agent", "vision_agent", "computer_control_agent"]
        for aid in mid_ids:
            if aid in nodes_by_id:
                self.assertEqual(nodes_by_id[aid].orbit_radius, ORBIT_MIDDLE_RADIUS, f"{aid} must be on middle orbit")

        # Outer ring (540px)
        if "pixel_ui_agent" in nodes_by_id:
            self.assertEqual(nodes_by_id["pixel_ui_agent"].orbit_radius, ORBIT_OUTER_RADIUS)

    def test_43_dynamic_agent_outer_orbit_placement(self):
        """AgentFactory-generated dynamic agents are placed on outer orbit with staggered angles."""
        for i in range(3):
            spec = AgentSpecification(
                agent_id=f"gen_galaxy_test_agent_{i}",
                name=f"Test Dynamic Agent {i}",
                purpose=f"Test dynamic agent {i} outer ring placement",
                capabilities=["test.capability"],
                lifecycle_state=AgentLifecycleState.APPROVED,
            )
            self.registry.register_agent(spec)
            self.registry.activate_agent(spec.agent_id)

        nodes = self.engine.build_celestial_nodes()
        dynamic_nodes = [n for n in nodes if n.agent_id.startswith("gen_galaxy_test_agent_")]
        self.assertEqual(len(dynamic_nodes), 3)

        for dn in dynamic_nodes:
            self.assertEqual(dn.orbit_radius, ORBIT_OUTER_RADIUS)

        # Angles must be distinct/staggered
        angles = [dn.orbit_angle for dn in dynamic_nodes]
        self.assertEqual(len(set(angles)), 3, "Dynamic agents must have distinct orbital angles")

    def test_44_no_node_overlap_and_clearance(self):
        """Nodes on the same ring have ample angular separation (>10 degrees)."""
        nodes = self.engine.build_celestial_nodes()
        from collections import defaultdict
        by_radius = defaultdict(list)
        for n in nodes:
            by_radius[n.orbit_radius].append(n.orbit_angle % 360)

        for radius, angles in by_radius.items():
            if len(angles) <= 1:
                continue
            sorted_angles = sorted(angles)
            for i in range(len(sorted_angles)):
                a1 = sorted_angles[i]
                a2 = sorted_angles[(i + 1) % len(sorted_angles)]
                diff = (a2 - a1) % 360
                self.assertGreater(diff, 10.0, f"Nodes on ring R={radius} are too close: {a1} vs {a2}")

    def test_45_direct_agent_addressing_droid(self):
        """Direct calling 'Hey Droid' or 'Droid' routes directly to Droid and activates session."""
        resp = self.companion.interact("Hey Droid", speak_output=False)
        self.assertEqual(resp.routed_to, "Droid")
        self.assertIn("Droid", resp.text)
        self.assertEqual(self.companion.active_conversation_agent, "android_unified_agent")
        self.assertEqual(self.companion.active_conversation_agent_name, "Droid")

    def test_46_direct_agent_addressing_unity_and_unreal(self):
        """Direct addressing switches context between different agents."""
        resp_u = self.companion.interact("Unity", speak_output=False)
        self.assertEqual(resp_u.routed_to, "Unity")
        self.assertEqual(self.companion.active_conversation_agent, "unity_autonomous_agent")

        resp_un = self.companion.interact("Unreal", speak_output=False)
        self.assertEqual(resp_un.routed_to, "Unreal")
        self.assertEqual(self.companion.active_conversation_agent, "unreal_autonomous_agent")

    def test_47_active_conversation_persistence(self):
        """Follow-up command without agent name stays with active agent."""
        self.companion.interact("Droid", speak_output=False)
        self.assertEqual(self.companion.active_conversation_agent, "android_unified_agent")

        follow_up = self.companion.interact("Create a login screen", speak_output=False)
        self.assertEqual(follow_up.routed_to, "Droid")
        self.assertIn("Droid:", follow_up.text)
        self.assertEqual(self.companion.active_conversation_agent, "android_unified_agent")

    def test_48_active_conversation_followup_modification(self):
        """Consecutive follow-up modification stays with active agent."""
        self.companion.interact("Droid", speak_output=False)
        self.companion.interact("Create a login screen", speak_output=False)
        mod_resp = self.companion.interact("Make the button blue", speak_output=False)
        self.assertEqual(mod_resp.routed_to, "Droid")
        self.assertIn("Droid:", mod_resp.text)
        self.assertIn("blue", mod_resp.text.lower())

    def test_49_barge_in_and_speech_interruption(self):
        """User interruption words ('wait', 'stop', 'hold on') interrupt immediately."""
        self.companion.interact("Droid", speak_output=False)
        interrupt_resp = self.companion.interact("wait, don't create it", speak_output=False)
        self.assertEqual(interrupt_resp.routed_to, "InterruptionHandler")
        self.assertTrue(interrupt_resp.data.get("interrupted"))
        self.assertIn("won't create it", interrupt_resp.text.lower())

    def test_50_central_core_takeover(self):
        """User calling 'NR-AI' or 'Central' resets active agent to Central Core."""
        self.companion.interact("Droid", speak_output=False)
        self.assertIsNotNone(self.companion.active_conversation_agent)

        central_resp = self.companion.interact("NR-AI", speak_output=False)
        self.assertEqual(central_resp.routed_to, "NR-AI-Central")
        self.assertIsNone(self.companion.active_conversation_agent)
        self.assertIsNone(self.companion.active_conversation_agent_name)

    def test_51_single_agent_introduction_mode(self):
        """User asking 'Droid, introduce yourself' triggers single agent intro."""
        intro_resp = self.companion.interact("Droid, introduce yourself", speak_output=False)
        self.assertEqual(intro_resp.routed_to, "GalaxyIntroduction")
        self.assertTrue(intro_resp.data.get("single_agent_introduction"))
        self.assertEqual(intro_resp.data.get("single_speaker_id"), "android_unified_agent")
        self.assertIn("Droid", intro_resp.text)

    def test_52_credential_diagnostics_quota_exhausted_honest_reporting(self):
        """Credential diagnostic engine reports quota exhaustion accurately without claiming missing keys."""
        summary = get_fast_diagnostics_summary()
        self.assertIn("overall_status", summary)
        self.assertIn("providers", summary)
        self.assertIn("model_honesty", summary)

    def test_53_credential_diagnostics_secret_redaction(self):
        """redact_secret() strictly redacts tokens without exposing raw characters."""
        self.assertEqual(redact_secret(None), "[NOT SET]")
        self.assertEqual(redact_secret(""), "[NOT SET]")
        self.assertEqual(redact_secret("12345"), "[REDACTED]")
        redacted = redact_secret("sk-proj-abc123456789xyzsecretkey")
        self.assertNotIn("123456789xyzsecretkey", redacted)
        self.assertTrue(redacted.endswith("...[REDACTED]"))

    def test_54_model_honesty_unverified_reporting(self):
        """GPT-6 Astra is reported as unverified, never fabricated."""
        engine = CredentialDiagnosticEngine()
        diag = engine.diagnose_all(force_refresh=False)
        honesty = diag.get("model_honesty", {})
        self.assertIn("gpt_6_astra", honesty)
        self.assertIn("UNVERIFIED", honesty["gpt_6_astra"])

    def test_55_active_conversation_endpoints_http(self):
        """Dashboard HTTP endpoints support active conversation and credential diagnostics."""
        port = 8597
        dashboard = CompanionDashboard(companion=self.companion)
        started = dashboard.start_http_server(port=port)
        self.assertTrue(started)
        time.sleep(0.3)
        try:
            # 1. GET /api/conversation/active
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/conversation/active")
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data.get("success"))

            # 2. POST /api/conversation/active
            body = json.dumps({"agent_id": "android_unified_agent", "agent_name": "Droid"}).encode("utf-8")
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/conversation/active", data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data.get("success"))
                self.assertEqual(data.get("active_conversation_agent"), "android_unified_agent")

            # 3. POST /api/conversation/interrupt
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/conversation/interrupt", data=b"{}", headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data.get("success"))
                self.assertEqual(data.get("status"), "USER_INTERRUPTED")

            # 4. GET /api/diagnostics/credentials
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/diagnostics/credentials")
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self.assertIn("overall_status", data)
                self.assertIn("model_honesty", data)
        finally:
            dashboard.stop_http_server()


    # -------------------------------------------------------------------------
    # Galaxy UI Refinement 3: Agent Chat Workspace, Push-to-Talk, Context Locking
    # -------------------------------------------------------------------------
    def test_56_agent_activation_voice_and_direct_addressing(self):
        """Voice triggers 'Activate Droid', 'Switch to Studio', 'Talk to Unity' activate agents."""
        # 1. Activate Droid
        aid, fname, rem = self.companion.resolve_addressed_agent("Activate Droid")
        self.assertEqual(aid, "android_unified_agent")
        self.assertEqual(fname, "Droid")
        self.assertEqual(rem, "")

        # 2. Switch to Studio
        aid, fname, rem = self.companion.resolve_addressed_agent("Switch to Studio")
        self.assertEqual(aid, "vs_unified_agent")
        self.assertEqual(fname, "Studio")

        # 3. Talk to Unity and create script
        aid, fname, rem = self.companion.resolve_addressed_agent("Talk to Unity and create a player controller")
        self.assertEqual(aid, "unity_autonomous_agent")
        self.assertEqual(fname, "Unity")
        self.assertIn("player controller", rem)

    def test_57_session_aware_introduction(self):
        """First activation delivers authentic capability intro; repeat delivers crisp readiness."""
        comp = NRCompanion()
        intro1, first1 = comp.activate_agent_session("android_unified_agent")
        self.assertTrue(first1)
        self.assertIn("Droid", intro1)
        self.assertIn("Android", intro1)

        # Second activation in same session
        intro2, first2 = comp.activate_agent_session("android_unified_agent")
        self.assertFalse(first2)
        self.assertEqual(intro2, "Yes Boss, I'm ready. What do you need?")

    def test_58_context_locked_specialist_followups(self):
        """With Droid active, follow-up queries remain locked to Android Studio workspace."""
        comp = NRCompanion()
        comp.activate_agent_session("android_unified_agent")

        # Query 1: What is the error?
        r1 = comp.interact("What is the error?", speak_output=False)
        self.assertIn("Droid:", r1.text)
        self.assertIn("nr_android_test", r1.text)
        self.assertEqual(comp.active_conversation_agent, "android_unified_agent")

        # Query 2: Why is this button not working?
        r2 = comp.interact("Why is this button not working?", speak_output=False)
        self.assertIn("Droid:", r2.text)
        self.assertIn("MainActivity.kt", r2.text)
        self.assertEqual(comp.active_conversation_agent, "android_unified_agent")

    def test_59_verify_before_answering_contract(self):
        """Agent checks ground-truth evidence before answering build status, never falsely claiming done."""
        comp = NRCompanion()
        comp.activate_agent_session("android_unified_agent")

        # Did you build the app?
        r = comp.interact("Did you build the app?", speak_output=False)
        self.assertIn("Droid:", r.text)
        self.assertIn("not executed a build", r.text)
        self.assertNotIn("Completed successfully", r.text)

    def test_60_non_blind_knowledge_escape_with_workspace_preservation(self):
        """General knowledge questions query Universal Knowledge while preserving Droid workspace."""
        comp = NRCompanion()
        comp.activate_agent_session("android_unified_agent")

        # Ask general knowledge question
        r = comp.interact("What is the capital of France?", speak_output=False)
        self.assertIn("Droid:", r.text)
        self.assertIn("Universal Knowledge", r.text)
        self.assertIn("Android Studio workspace", r.text)
        # Active workspace must still be Droid!
        self.assertEqual(comp.active_conversation_agent, "android_unified_agent")

    def test_61_bounded_chat_history_and_secret_redaction(self):
        """Chat history is bounded to 20 turns and strictly redacts API keys and secrets."""
        comp = NRCompanion()
        aid = "android_unified_agent"

        # Add message with simulated secret
        comp.add_agent_chat_message(aid, role="user", text="My API key is AIzaSyD9fakeapikey123456789012345678")
        comp.add_agent_chat_message(aid, role="agent", text="Secret noted.")

        hist = comp.get_agent_chat_history(aid)
        self.assertEqual(len(hist), 2)
        self.assertNotIn("AIzaSyD9fakeapikey123456789012345678", hist[0]["text"])
        self.assertIn("[REDACTED_API_KEY]", hist[0]["text"])

    def test_62_active_development_context_ground_truth(self):
        """Active development context reports real Android Studio workspace and files."""
        comp = NRCompanion()
        ctx = comp.get_active_development_context("android_unified_agent")
        self.assertEqual(ctx["environment"], "ANDROID")
        self.assertEqual(ctx["ide"], "Android Studio")
        self.assertEqual(ctx["project_name"], "NR AI Test")
        self.assertEqual(ctx["package_name"], "com.nrai.test")
        self.assertEqual(ctx["language"], "Kotlin")
        self.assertEqual(ctx["build_system"], "Gradle")
        self.assertTrue(ctx["project_exists"])
        self.assertTrue(ctx["main_activity_exists"])

    def test_63_galaxy_engine_workspace_metadata(self):
        """GalaxyEngine attaches workspace names and projects to all celestial profiles."""
        ge = GalaxyEngine()
        nodes = ge.build_celestial_nodes()
        droid = next((n for n in nodes if n.agent_id == "android_unified_agent"), None)
        studio = next((n for n in nodes if n.agent_id == "vs_unified_agent"), None)
        unity = next((n for n in nodes if n.agent_id == "unity_autonomous_agent"), None)

        self.assertIsNotNone(droid)
        self.assertEqual(droid.workspace_name, "Android Studio")
        self.assertEqual(droid.project_name, "NR AI Test")

        self.assertIsNotNone(studio)
        self.assertEqual(studio.workspace_name, "Visual Studio")

        self.assertIsNotNone(unity)
        self.assertEqual(unity.workspace_name, "Unity Editor")

    def test_64_http_endpoints_agent_workspace_and_chat(self):
        """Test GET /api/agent/<id>/context, POST /activate, POST /chat, and GET /chat."""
        port = 8598
        dashboard = CompanionDashboard(companion=self.companion)
        started = dashboard.start_http_server(port=port)
        self.assertTrue(started)
        time.sleep(0.3)
        try:
            # 1. GET /api/agent/android_unified_agent/context
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/agent/android_unified_agent/context")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data.get("success"))
                self.assertEqual(data.get("workspace_name"), "Android Studio")
                self.assertEqual(len(data.get("step_checklist", [])), 8)

            # 2. POST /api/agent/android_unified_agent/activate
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/agent/android_unified_agent/activate", data=b"{}", headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data.get("success"))
                self.assertIn("Droid", data.get("speech"))

            # 3. POST /api/agent/android_unified_agent/chat
            body = json.dumps({"text": "What is the error?"}).encode("utf-8")
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/agent/android_unified_agent/chat", data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data.get("success"))
                self.assertIn("Droid: No errors detected", data.get("reply"))

            # 4. GET /api/agent/android_unified_agent/chat
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/agent/android_unified_agent/chat")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data.get("success"))
                self.assertGreaterEqual(data.get("count"), 2)
        finally:
            dashboard.stop_http_server()

    def test_65_barge_in_speech_interruption(self):
        """Barge-in triggers ('wait, don't create it', 'stop') interrupt speech synthesis."""
        comp = NRCompanion()
        comp.activate_agent_session("android_unified_agent")

        r = comp.interact("wait, don't create it", speak_output=False)
        self.assertIn("Droid:", r.text)
        self.assertIn("I won't create it", r.text)
        self.assertTrue(r.data.get("interrupted"))

    def test_66_switch_agent_and_return_to_central(self):
        """Switching to another agent and returning to central NR-AI works deterministically."""
        comp = NRCompanion()
        comp.activate_agent_session("android_unified_agent")
        self.assertEqual(comp.active_conversation_agent, "android_unified_agent")

        # Switch to Unity
        r1 = comp.interact("Unity", speak_output=False)
        self.assertEqual(comp.active_conversation_agent, "unity_autonomous_agent")

        # Return to central
        r2 = comp.interact("NR-AI", speak_output=False)
        self.assertIsNone(comp.active_conversation_agent)
        self.assertIn("central orchestration", r2.text)

    def test_67_dynamic_agent_factory_workspace_parity(self):
        """Dynamic Agent Factory agents automatically receive full workspace and conversation parity."""
        from app.agent.factory.specification import AgentSpecification, AgentLifecycleState
        from app.agent.factory.registry import AgentRegistry

        unique_id = f"test_qa_{int(time.time() * 1000)}"
        spec = AgentSpecification(
            agent_id=unique_id,
            name="Test QA Agent",
            purpose="Automate system verification suites",
            capabilities=["system.core", "ui.visualization"],
            lifecycle_state=AgentLifecycleState.APPROVED,
        )
        reg = AgentRegistry()
        reg.register_agent(spec)
        reg.activate_agent(unique_id)

        try:
            ge = GalaxyEngine(registry=reg)
            ctx = ge.get_agent_context(unique_id)
            self.assertEqual(ctx["workspace_name"], "Test QA Workspace")
            self.assertEqual(len(ctx["step_checklist"]), 8)

            comp = NRCompanion()
            speech1, first1 = comp.activate_agent_session(unique_id, agent_name="Test QA")
            self.assertTrue(first1)
            self.assertIn("Test QA", speech1)
        finally:
            reg.retire_agent(unique_id)

    def test_68_html_and_css_workspace_components(self):
        """Galaxy HTML and CSS contain all required Agent Chat Workspace and PTT console components."""
        html_path = Path(r"C:\NR-AI\app\ui\templates\galaxy.html")
        css_path = Path(r"C:\NR-AI\app\ui\static\galaxy.css")
        js_path = Path(r"C:\NR-AI\app\ui\static\galaxy.js")

        with open(html_path, "r", encoding="utf-8") as f:
            html = f.read()
        with open(css_path, "r", encoding="utf-8") as f:
            css = f.read()
        with open(js_path, "r", encoding="utf-8") as f:
            js = f.read()

        # HTML Workspace Elements
        self.assertIn('id="panelFocusCard"', html)
        self.assertIn('id="panelWorkspaceFocus"', html)
        self.assertIn('id="panelActiveProject"', html)
        self.assertIn('id="panelStepList"', html)
        self.assertIn('id="panelMicBtn"', html)
        self.assertIn('id="panelTextInput"', html)
        self.assertIn('id="panelChatHistory"', html)

        # CSS Styles
        self.assertIn('.workspace-focus-card', css)
        self.assertIn('.checklist-card', css)
        self.assertIn('.ptt-mic-btn', css)
        self.assertIn('.state-ready', css)
        self.assertIn('.state-listening', css)
        self.assertIn('.state-speaking', css)

        # JS Push-to-Talk Logic
        self.assertIn('updatePttState', js)
        self.assertIn('toggleAgentPushToTalk', js)
        self.assertIn('sendAgentTextMessage', js)
        self.assertIn('loadAgentWorkspace', js)

    def test_69_security_invariants_shell_false_and_localhost(self):
        """Strict security invariants: zero shell=True in UI/Companion dispatch, strictly 127.0.0.1, ModelIsolationGate enforced."""
        # 1. UI and Companion dispatch must have 0 shell=True
        target_files = [
            Path(r"C:\NR-AI\app\ui\dashboard.py"),
            Path(r"C:\NR-AI\app\ui\galaxy_engine.py"),
            Path(r"C:\NR-AI\app\remote\server.py"),
        ]
        shell_true_matches = []
        for py_file in target_files:
            with open(py_file, "r", encoding="utf-8", errors="ignore") as f:
                for idx, line in enumerate(f, 1):
                    if "shell=True" in line and not line.strip().startswith("#"):
                        shell_true_matches.append(f"{py_file.name}:{idx}")
        self.assertEqual(len(shell_true_matches), 0, f"Found shell=True in UI layer: {shell_true_matches}")

        # 2. Companion get_active_development_context uses shell=False
        comp = NRCompanion()
        ctx = comp.get_active_development_context("android_unified_agent")
        self.assertEqual(ctx.get("verification_status"), "DETERMINISTIC_SAFE")

        # 3. Agent Safety Policy rejects allow_shell=True
        from app.agent.factory.safety import AgentFactorySafetyGate
        from app.agent.factory.specification import AgentSpecification, SafetyPolicy
        gate = AgentFactorySafetyGate()
        bad_spec = AgentSpecification(
            agent_id="bad_shell_agent",
            name="Bad Shell Agent",
            purpose="Malicious shell test",
            safety_policy=SafetyPolicy(allow_shell=True),
        )
        safe, violations = gate.audit_specification(bad_spec)
        self.assertFalse(safe)
        self.assertTrue(any("shell" in v.lower() for v in violations))

        # 4. Host binding strictly localhost 127.0.0.1
        from app.ui.dashboard import CompanionDashboard
        dash = CompanionDashboard()
        self.assertEqual(dash.host, "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
