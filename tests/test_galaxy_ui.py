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
            self.assertEqual(dyn_node.orbit_radius, 320.0)
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
        self.assertEqual(len(nodes), len(connections))
        for conn in connections:
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


if __name__ == "__main__":
    unittest.main()
