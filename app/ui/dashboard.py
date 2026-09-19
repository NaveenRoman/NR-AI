"""
NR AI Companion Dashboard & Live Status Engine.

Provides real-time visualization of assistant state:
- Assistant State: 🟢 Idle, 🎙 Listening, 🧠 Thinking, 🔊 Speaking, ⚠ Error
- Microphone & hardware availability status
- Current recognized command & conversation history
- Active agent slot & current task
- Active model architecture & verification status
- Latest live world & AI news feeds
- System activity & recent logs

Supports both console/terminal dashboard rendering and a lightweight local HTTP dashboard.
"""

from dataclasses import asdict, dataclass
import hashlib
import http.server
import json
import logging
import os
import secrets
import socketserver
import threading
import time
from typing import Any, Dict, List, Optional
import urllib.parse

from app.ui.avatar_state import AvatarMode
from app.remote.emergency import EmergencyStopController

logger = logging.getLogger("NRAI.Dashboard")


class CompanionDashboard:
    """
    Companion Dashboard aggregator and HTTP service.
    """

    def __init__(self, companion: Optional[Any] = None):
        if companion is None:
            from app.brain.companion import NRCompanion
            self.companion = NRCompanion()
        else:
            self.companion = companion
        self._server_thread: Optional[threading.Thread] = None
        self._httpd: Optional[socketserver.TCPServer] = None
        self.host: str = "127.0.0.1"
        self.port: int = 8585
        from app.ui.galaxy_engine import GalaxyEngine
        self.galaxy_engine = GalaxyEngine()

    def get_status_snapshot(self) -> Dict[str, Any]:
        """Collect real-time operational status across all NR-AI companion components."""
        avatar_frame = self.companion.avatar.current_frame
        mode = avatar_frame.mode

        # Format status badge with standard emoji indicator
        status_badges = {
            AvatarMode.IDLE: "🟢 Idle",
            AvatarMode.WAITING_FOR_WAKE_WORD: "👂 Waiting For Wake Word",
            AvatarMode.WAKE_WORD_DETECTED: "⚡ Wake Word Detected",
            AvatarMode.LISTENING: "🎙 Listening",
            AvatarMode.THINKING: "🧠 Thinking",
            AvatarMode.WORKING: "⚙ Working",
            AvatarMode.SPEAKING: "🔊 Speaking",
            AvatarMode.ERROR: "⚠ Error",
        }
        current_status = status_badges.get(mode, f"🟢 {mode.value}")

        # Microphone status
        mic_info = self.companion.listener.probe_microphone()

        # Orchestrator & Slot status
        orch_metrics = self.companion.orchestrator.get_system_metrics()
        slots = orch_metrics.get("slots", [])
        active_slot = next((s for s in slots if s["status"] == "BUSY"), None)
        active_agent_name = active_slot["name"] if active_slot else "Agent-1-Architect (Idle)"
        active_model_name = active_slot["active_model"] if active_slot else self.companion.config.default_model

        # Recent conversation command
        last_exchange = (
            self.companion.conversation_history[-1]
            if self.companion.conversation_history
            else {"user": "None", "assistant": "Ready"}
        )

        wake_phrase = getattr(
            self.companion,
            "last_wake_phrase",
            getattr(self.companion.listener, "last_recognized_phrase", "None") or "None",
        )
        current_route = getattr(self.companion, "current_route", "CompanionPersona")
        current_agent = getattr(self.companion, "current_agent", active_agent_name)
        current_task_status = getattr(self.companion, "current_task_status", "Idle")

        # Recent news
        latest_news = []
        try:
            items = self.companion.news_agent.fetch_category("World", limit=3)
            latest_news = [it.to_dict() for it in items]
        except Exception:
            pass

        # Real Model Execution Status
        model_exec = dict(getattr(
            self.companion,
            "last_model_execution",
            {},
        ) or {})
        model_exec.setdefault("configured_role", "GENERAL_INTELLIGENT_TASK")
        model_exec.setdefault("configured_model", self.companion.config.default_model)
        model_exec.setdefault("requested_model", self.companion.config.default_model)
        model_exec.setdefault("actual_model_used", "NONE")
        model_exec.setdefault("provider", "None")
        model_exec.setdefault("live_api_success", "NO")
        model_exec.setdefault("cloud_request_success", "NO")
        model_exec.setdefault("http_status", "NOT_CALLED")
        model_exec.setdefault("cloud_ai_status", "QUOTA EXHAUSTED (OpenAI) / NO CREDENTIALS (Gemini)")
        model_exec.setdefault("fallback_used", "NO")
        model_exec.setdefault("task_id", "N/A")
        model_exec.setdefault("agent", "Agent-1-Architect (Idle)")
        model_exec.setdefault("sources_queried", [])
        model_exec.setdefault("sources_successfully_accessed", [])
        model_exec.setdefault("failed_sources", [])
        model_exec.setdefault("articles_retrieved", 0)
        model_exec.setdefault("duplicate_stories_removed", 0)
        model_exec.setdefault("stories_verified", 0)
        model_exec.setdefault("stories_single_source", 0)
        model_exec.setdefault("verification_status", "UNAVAILABLE / NOT EXECUTED")
        model_exec.setdefault("safe_execution_evidence", "No requests executed yet")
        model_exec.setdefault("execution_evidence", "No requests executed yet")

        trinity_tel = {}
        k_engine = getattr(self.companion, "knowledge_engine", None)
        if k_engine and hasattr(k_engine, "get_telemetry_snapshot"):
            try:
                res = k_engine.get_telemetry_snapshot()
                if isinstance(res, dict):
                    trinity_tel = res
            except Exception:
                pass

        skyshield_st = {}
        s_coord = getattr(self.companion, "security_coordinator", None)
        if s_coord and hasattr(s_coord, "get_dashboard_state"):
            try:
                res = s_coord.get_dashboard_state()
                if isinstance(res, dict):
                    skyshield_st = res
            except Exception:
                pass

        droid_st = {}
        u_agent = getattr(self.companion, "unified_android_agent", None)
        if u_agent:
            try:
                rep = u_agent.get_device_lifecycle_status("Pixel_6_API_34")
                if hasattr(rep, "to_dict"):
                    droid_st = rep.to_dict()
                elif isinstance(rep, dict):
                    droid_st = rep
                active_task = u_agent.task_state_store.get_active_task()
                if active_task:
                    droid_st["active_task"] = active_task.to_dict()
            except Exception:
                pass

        return {
            "assistant_name": self.companion.name if isinstance(getattr(self.companion, "name", None), str) else "NR-AI Companion",
            "assistant_status": current_status,
            "raw_state": mode.value,
            "avatar_mode": mode.value,
            "avatar_emotion": avatar_frame.emotion.value,
            "recognized_wake_phrase": wake_phrase,
            "recognized_command": last_exchange.get("user", "None"),
            "current_route": current_route,
            "current_agent": current_agent,
            "current_task_status": current_task_status,
            "model_execution": model_exec,
            "microphone": {
                "available": mic_info.get("available", False),
                "device_name": mic_info.get("active_device", "None"),
                "device_count": mic_info.get("device_count", 0),
                "status": mic_info.get("status", "UNKNOWN"),
                "push_to_talk": mic_info.get("push_to_talk_fallback", True),
                "wake_word_enabled": self.companion.voice_config.wake_word_enabled,
                "wake_words": self.companion.voice_config.wake_words,
            },
            "conversation_count": len(self.companion.conversation_history),
            "voice": {
                "wake_word_enabled": self.companion.voice_config.wake_word_enabled,
                "wake_words": self.companion.voice_config.wake_words,
            },
            "current_command": last_exchange.get("user", "None"),
            "last_response": last_exchange.get("assistant", "Ready"),
            "active_agent": active_agent_name,
            "active_task": orch_metrics.get("running_tasks", 0),
            "active_model": model_exec.get("actual_model_used", active_model_name),
            "verification_status": "Consensus Engine Active (Cases A, B, C, D)",
            "orchestrator_metrics": {
                "total_slots": orch_metrics.get("total_slots", 10),
                "busy_slots": orch_metrics.get("busy_slots", 0),
                "idle_slots": orch_metrics.get("idle_slots", 10),
                "completed_tasks": orch_metrics.get("completed_tasks", 0),
            },
            "latest_news": latest_news,
            "trinity_telemetry": trinity_tel,
            "skyshield_status": skyshield_st,
            "droid_status": droid_st,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def render_terminal_view(self) -> str:
        """Render a clean ASCII status dashboard card for terminal monitoring."""
        s = self.get_status_snapshot()
        mic = s["microphone"]
        orch = s["orchestrator_metrics"]
        m_exec = s["model_execution"]

        lines = [
            "╔══════════════════════════════════════════════════════════════════════════════╗",
            "║                         NR-AI COMPANION DASHBOARD                            ║",
            "╠══════════════════════════════════════════════════════════════════════════════╣",
            f"║  Status:           {s['assistant_status']:<18} Cloud AI:         {m_exec['cloud_ai_status'][:20]:<20} ║",
            f"║  Configured Model: {m_exec['configured_model']:<18} Actual Model:    {m_exec['actual_model_used'][:20]:<20} ║",
            f"║  Provider:         {m_exec['provider'][:18]:<18} Cloud Success:   {m_exec['cloud_request_success']:<20} ║",
            f"║  HTTP Status:      {str(m_exec['http_status']):<18} Fallback Used:   {m_exec['fallback_used']:<20} ║",
            f"║  Task ID:          {str(m_exec['task_id'])[:18]:<18} Agent:           {str(m_exec['agent'])[:20]:<20} ║",
            f"║  Verification:     {str(m_exec['verification_status'])[:56]:<58} ║",
            f"║  Slots In Use:     {str(orch['busy_slots']) + '/' + str(orch['total_slots']):<18} Tasks Complete:   {str(orch['completed_tasks']):<20} ║",
            "╠══════════════════════════════════════════════════════════════════════════════╣",
            f"║  Last Command:     {(str(s['current_command'])[:56]):<58} ║",
            f"║  Evidence:         {(str(m_exec['execution_evidence'])[:56]):<58} ║",
            "╚══════════════════════════════════════════════════════════════════════════════╝",
        ]
        return "\n".join(lines)

    def start_http_server(self, port: int = 8585) -> bool:
        """Start background HTTP server providing web UI and JSON status API."""
        self.port = port
        dashboard_ref = self

        class DashboardHTTPHandler(http.server.BaseHTTPRequestHandler):
            def _send_json(self, code: int, payload: bytes):
                try:
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(payload)
                except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                    pass

            def _send_bytes(self, code: int, content_type: str, data: bytes):
                try:
                    self.send_response(code)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(data)
                except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                    pass

            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)

                # 1. Galaxy State API
                if parsed.path in ("/api/galaxy/state", "/api/galaxy/state/"):
                    snapshot = dashboard_ref.get_status_snapshot()
                    state = dashboard_ref.galaxy_engine.get_galaxy_state(companion_snapshot=snapshot)
                    payload = json.dumps(state, indent=2).encode("utf-8")
                    self._send_json(200, payload)

                # 1a. Trinity Real-Time Telemetry API
                elif parsed.path in ("/api/trinity/telemetry", "/api/trinity/telemetry/"):
                    telemetry = {}
                    if dashboard_ref.companion and hasattr(dashboard_ref.companion, "knowledge_engine"):
                        telemetry = dashboard_ref.companion.knowledge_engine.get_telemetry_snapshot()
                    payload = json.dumps({"success": True, "telemetry": telemetry}, indent=2).encode("utf-8")
                    self._send_json(200, payload)

                # 1c. SkyShield Security Dashboard API
                elif parsed.path in ("/api/skyshield/dashboard", "/api/skyshield/dashboard/", "/api/security/dashboard", "/api/security/dashboard/"):
                    coord = getattr(dashboard_ref.companion, "security_coordinator", None)
                    if coord:
                        data = coord.get_dashboard_state()
                    else:
                        data = {"success": False, "error": "Security coordinator unavailable", "state": "STOPPED"}
                    payload = json.dumps(data, indent=2).encode("utf-8")
                    self._send_json(200, payload)

                # SkyShield Enrolled Devices List
                elif parsed.path in ("/api/skyshield/devices", "/api/skyshield/devices/"):
                    coord = getattr(dashboard_ref.companion, "security_coordinator", None)
                    devices = coord.list_devices() if coord else []
                    payload = json.dumps({"success": True, "devices": devices}, indent=2).encode("utf-8")
                    self._send_json(200, payload)

                # SkyShield Enrolled Device Detail & Phase 3 Health Telemetry
                elif parsed.path.startswith("/api/skyshield/devices/"):
                    parts = parsed.path.strip("/").split("/")
                    device_id = parts[3] if len(parts) >= 4 else ""
                    sub_action = parts[4] if len(parts) >= 5 else ""
                    coord = getattr(dashboard_ref.companion, "security_coordinator", None)

                    if sub_action == "health":
                        res = coord.get_device_health(device_id) if coord else {"success": False, "error": "Coordinator unavailable"}
                        self._send_json(200 if res.get("success") else 404, json.dumps(res, indent=2).encode("utf-8"))
                    elif sub_action == "anomalies":
                        res = coord.get_device_anomalies(device_id) if coord else {"success": False, "error": "Coordinator unavailable"}
                        self._send_json(200 if res.get("success") else 404, json.dumps(res, indent=2).encode("utf-8"))
                    elif sub_action == "events":
                        res = coord.get_device_events(device_id) if coord else {"success": False, "error": "Coordinator unavailable"}
                        self._send_json(200 if res.get("success") else 404, json.dumps(res, indent=2).encode("utf-8"))
                    elif sub_action == "posture":
                        res = coord.get_device_posture(device_id) if coord else {"success": False, "error": "Coordinator unavailable"}
                        self._send_json(200 if res.get("success") else 404, json.dumps(res, indent=2).encode("utf-8"))
                    else:
                        dev = coord.get_device(device_id) if coord and device_id else None
                        if dev:
                            payload = json.dumps({"success": True, "device": dev.to_dict()}, indent=2).encode("utf-8")
                            self._send_json(200, payload)
                        else:
                            payload = json.dumps({"success": False, "error": f"Device '{device_id}' not found"}, indent=2).encode("utf-8")
                            self._send_json(404, payload)

                # SkyShield Pairing Requests List
                elif parsed.path in ("/api/skyshield/pair/requests", "/api/skyshield/pair/requests/"):
                    coord = getattr(dashboard_ref.companion, "security_coordinator", None)
                    reqs = coord.pairing_manager.list_pairing_requests() if (coord and hasattr(coord, "pairing_manager")) else []
                    payload = json.dumps({"success": True, "pairing_requests": reqs}, indent=2).encode("utf-8")
                    self._send_json(200, payload)

                # SkyShield Specific Pairing Request Detail
                elif parsed.path.startswith("/api/skyshield/pair/"):
                    parts = parsed.path.strip("/").split("/")
                    pairing_id = parts[3] if len(parts) >= 4 else ""
                    coord = getattr(dashboard_ref.companion, "security_coordinator", None)
                    req = coord.get_pairing_request(pairing_id) if coord and pairing_id else None
                    if req:
                        payload = json.dumps({"success": True, "pairing_request": req.to_dict()}, indent=2).encode("utf-8")
                        self._send_json(200, payload)
                    else:
                        payload = json.dumps({"success": False, "error": f"Pairing request '{pairing_id}' not found"}, indent=2).encode("utf-8")
                        self._send_json(404, payload)

                # SkyShield Phase 4 Security Overview
                elif parsed.path in ("/api/skyshield/overview", "/api/skyshield/overview/"):
                    coord = getattr(dashboard_ref.companion, "security_coordinator", None)
                    data = coord.get_security_overview() if coord else {"error": "Coordinator unavailable"}
                    self._send_json(200 if coord else 500, json.dumps({"success": bool(coord), "overview": data}, indent=2).encode("utf-8"))

                # SkyShield Phase 4 Incidents List & Detail
                elif parsed.path in ("/api/skyshield/incidents", "/api/skyshield/incidents/"):
                    params = urllib.parse.parse_qs(parsed.query)
                    dev_id = params.get("device_id", [None])[0]
                    st = params.get("status", [None])[0]
                    coord = getattr(dashboard_ref.companion, "security_coordinator", None)
                    incs = coord.list_incidents(device_id=dev_id, status=st) if coord else []
                    self._send_json(200, json.dumps({"success": True, "incidents": incs}, indent=2).encode("utf-8"))

                elif parsed.path.startswith("/api/skyshield/incidents/"):
                    parts = parsed.path.strip("/").split("/")
                    inc_id = parts[3] if len(parts) >= 4 else ""
                    sub_act = parts[4] if len(parts) >= 5 else ""
                    coord = getattr(dashboard_ref.companion, "security_coordinator", None)
                    if sub_act == "report":
                        rep = coord.generate_incident_report(inc_id) if coord else {"success": False, "error": "Coordinator unavailable"}
                        self._send_json(200 if rep.get("success") else 404, json.dumps(rep, indent=2).encode("utf-8"))
                    else:
                        inc = coord.get_incident(inc_id) if coord else None
                        if inc:
                            self._send_json(200, json.dumps({"success": True, "incident": inc}, indent=2).encode("utf-8"))
                        else:
                            self._send_json(404, json.dumps({"success": False, "error": f"Incident '{inc_id}' not found"}, indent=2).encode("utf-8"))

                # SkyShield Phase 4 Threat Intelligence & CVEs
                elif parsed.path in ("/api/skyshield/threats", "/api/skyshield/threats/"):
                    coord = getattr(dashboard_ref.companion, "security_coordinator", None)
                    threats = coord.list_threat_advisories() if coord else []
                    self._send_json(200, json.dumps({"success": True, "threats": threats}, indent=2).encode("utf-8"))

                elif parsed.path.startswith("/api/skyshield/threats/check/"):
                    parts = parsed.path.strip("/").split("/")
                    dev_id = parts[4] if len(parts) >= 5 else ""
                    coord = getattr(dashboard_ref.companion, "security_coordinator", None)
                    res = coord.check_device_vulnerability(dev_id) if coord else {"success": False, "error": "Coordinator unavailable"}
                    self._send_json(200 if res.get("success") else 404, json.dumps(res, indent=2).encode("utf-8"))

                # SkyShield Phase 4 Security Alerts
                elif parsed.path in ("/api/skyshield/alerts", "/api/skyshield/alerts/"):
                    coord = getattr(dashboard_ref.companion, "security_coordinator", None)
                    alerts = coord.list_alerts() if coord else []
                    self._send_json(200, json.dumps({"success": True, "alerts": alerts}, indent=2).encode("utf-8"))

                # SkyShield Phase 4 Gated Proposals
                elif parsed.path in ("/api/skyshield/proposals", "/api/skyshield/proposals/"):
                    coord = getattr(dashboard_ref.companion, "security_coordinator", None)
                    props = [p.to_dict() for p in coord.response_engine.list_proposals()] if coord else []
                    self._send_json(200, json.dumps({"success": True, "proposals": props}, indent=2).encode("utf-8"))

                # 1d. Diagnostics & Introduction APIs
                elif parsed.path == "/api/diagnostics/credentials":
                    from app.agent.credential_diagnostics import CredentialDiagnosticEngine
                    diag = CredentialDiagnosticEngine().diagnose_all()
                    self._send_json(200, json.dumps(diag, indent=2).encode("utf-8"))

                elif parsed.path == "/api/conversation/active":
                    comp = dashboard_ref.companion
                    payload = json.dumps({
                        "success": True,
                        "active_conversation_agent": getattr(comp, "active_conversation_agent", None),
                        "active_conversation_agent_name": getattr(comp, "active_conversation_agent_name", None),
                        "handoff_path": getattr(comp, "last_handoff_path", []),
                        "conversation_id": getattr(comp, "conversation_id", ""),
                    }, indent=2).encode("utf-8")
                    self._send_json(200, payload)

                elif parsed.path in ("/api/galaxy/introduction", "/api/galaxy/introduction/"):
                    snapshot = dashboard_ref.get_status_snapshot()
                    intro_data = dashboard_ref.galaxy_engine.get_agent_introductions(companion_snapshot=snapshot)
                    payload = json.dumps(intro_data, indent=2).encode("utf-8")
                    self._send_json(200, payload)

                # 2. Agent List API
                elif parsed.path in ("/api/agents", "/api/agents/"):
                    nodes = dashboard_ref.galaxy_engine.build_celestial_nodes(dashboard_ref.get_status_snapshot())
                    agents = [n.to_dict() for n in nodes]
                    payload = json.dumps({"success": True, "count": len(agents), "agents": agents}, indent=2).encode("utf-8")
                    self._send_json(200, payload)

                # 3. Specific Agent Workspace Context API
                elif parsed.path.startswith("/api/agent/") and (parsed.path.endswith("/context") or parsed.path.endswith("/context/")):
                    agent_id = parsed.path[len("/api/agent/"):].rstrip("/").replace("/context", "").strip("/")
                    snapshot = dashboard_ref.get_status_snapshot()
                    ctx = dashboard_ref.galaxy_engine.get_agent_context(agent_id, companion_snapshot=snapshot)
                    if hasattr(dashboard_ref.companion, "get_active_development_context"):
                        ctx["development_context"] = dashboard_ref.companion.get_active_development_context(agent_id)
                    payload = json.dumps(ctx, indent=2).encode("utf-8")
                    self._send_json(200, payload)

                # 3b. Specific Agent Chat History API
                elif parsed.path.startswith("/api/agent/") and (parsed.path.endswith("/chat") or parsed.path.endswith("/chat/")):
                    agent_id = parsed.path[len("/api/agent/"):].rstrip("/").replace("/chat", "").strip("/")
                    history = dashboard_ref.companion.get_agent_chat_history(agent_id) if hasattr(dashboard_ref.companion, "get_agent_chat_history") else []
                    payload = json.dumps({"success": True, "agent_id": agent_id, "count": len(history), "history": history}, indent=2).encode("utf-8")
                    self._send_json(200, payload)

                # 3c. Specific Agent Node API
                elif parsed.path.startswith("/api/agent/"):
                    agent_id = parsed.path[len("/api/agent/"):].strip("/")
                    nodes = dashboard_ref.galaxy_engine.build_celestial_nodes(dashboard_ref.get_status_snapshot())
                    matching = next((n for n in nodes if n.agent_id == agent_id), None)
                    if matching:
                        payload = json.dumps({"success": True, "agent": matching.to_dict()}, indent=2).encode("utf-8")
                        self._send_json(200, payload)
                    else:
                        payload = json.dumps({"success": False, "error": f"Agent '{agent_id}' not found"}).encode("utf-8")
                        self._send_json(404, payload)

                # 4. Static Assets (CSS, JS, Fonts, Images)
                elif parsed.path.startswith("/static/"):
                    static_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "static"))
                    req_file = parsed.path[len("/static/"):].split("?")[0]
                    file_path = os.path.abspath(os.path.join(static_dir, req_file))
                    if file_path.startswith(static_dir) and os.path.isfile(file_path):
                        content_type = "application/octet-stream"
                        if file_path.endswith(".css"):
                            content_type = "text/css; charset=utf-8"
                        elif file_path.endswith(".js"):
                            content_type = "application/javascript; charset=utf-8"
                        elif file_path.endswith(".html"):
                            content_type = "text/html; charset=utf-8"
                        elif file_path.endswith(".png"):
                            content_type = "image/png"
                        elif file_path.endswith(".svg"):
                            content_type = "image/svg+xml"
                        elif file_path.endswith(".json"):
                            content_type = "application/json"
                        with open(file_path, "rb") as f:
                            file_data = f.read()
                        self._send_bytes(200, content_type, file_data)
                    else:
                        self._send_json(404, json.dumps({"error": "STATIC_FILE_NOT_FOUND"}).encode("utf-8"))

                # 5. Legacy/Standard Companion Status API
                elif parsed.path in ("/api/status", "/status"):
                    data = dashboard_ref.get_status_snapshot()
                    payload = json.dumps(data, indent=2, default=str).encode("utf-8")
                    self._send_json(200, payload)

                # 6. Companion Command GET
                elif parsed.path in ("/api/command", "/command"):
                    params = urllib.parse.parse_qs(parsed.query)
                    cmd = params.get("text", [""])[0] or params.get("command", [""])[0]
                    resp = dashboard_ref.companion.interact(cmd, speak_output=False)
                    payload = json.dumps(resp.to_dict() if hasattr(resp, "to_dict") else {"text": str(resp)}, indent=2).encode("utf-8")
                    self._send_json(200, payload)

                # 7. Galaxy UI Dashboard (Default Route) or Legacy Fallback
                elif parsed.path in ("/", "/galaxy", "/index.html"):
                    params = urllib.parse.parse_qs(parsed.query)
                    if "legacy" in params:
                        data = dashboard_ref.get_status_snapshot()
                        html_content = dashboard_ref._render_html_page(data).encode("utf-8")
                        self._send_bytes(200, "text/html; charset=utf-8", html_content)
                    else:
                        template_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "templates", "galaxy.html"))
                        if os.path.isfile(template_path):
                            with open(template_path, "rb") as f:
                                html_content = f.read()
                            self._send_bytes(200, "text/html; charset=utf-8", html_content)
                        else:
                            data = dashboard_ref.get_status_snapshot()
                            html_content = dashboard_ref._render_html_page(data).encode("utf-8")
                            self._send_bytes(200, "text/html; charset=utf-8", html_content)

                else:
                    data = dashboard_ref.get_status_snapshot()
                    html_content = dashboard_ref._render_html_page(data).encode("utf-8")
                    self._send_bytes(200, "text/html; charset=utf-8", html_content)

            def do_POST(self):
                parsed = urllib.parse.urlparse(self.path)
                content_len = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else ""

                # 1. Global Command Execution
                if parsed.path in ("/api/command", "/command"):
                    try:
                        data = json.loads(body)
                        cmd = data.get("command") or data.get("text") or ""
                    except Exception:
                        cmd = body.strip()
                    resp = dashboard_ref.companion.interact(cmd, speak_output=False)
                    payload = json.dumps(resp.to_dict() if hasattr(resp, "to_dict") else {"text": str(resp)}, indent=2).encode("utf-8")
                    self._send_json(200, payload)

                # 2. Emergency Stop Trigger
                elif parsed.path in ("/api/emergency_stop", "/api/emergency_stop/"):
                    try:
                        body_data = json.loads(body) if body else {}
                    except Exception:
                        body_data = {}
                    by = body_data.get("triggered_by", "GalaxyUI_Operator")
                    reason = body_data.get("reason", "Operator triggered 1-touch Emergency Stop")
                    estop = EmergencyStopController()
                    status = estop.trigger(triggered_by=by, reason=reason)
                    payload = json.dumps({
                        "success": status.is_active,
                        "message": f"EMERGENCY STOP TRIGGERED by {by}: {reason}",
                        "emergency_stop": status.is_active,
                        "triggered_at": status.triggered_at,
                    }, indent=2).encode("utf-8")
                    self._send_json(200, payload)

                # SkyShield Security POST Endpoints
                elif parsed.path in ("/api/skyshield/scan", "/api/skyshield/scan/", "/api/security/scan", "/api/security/scan/"):
                    coord = getattr(dashboard_ref.companion, "security_coordinator", None)
                    if not coord:
                        self._send_json(503, json.dumps({"success": False, "error": "Security coordinator unavailable"}).encode("utf-8"))
                        return
                    res = coord.run_full_scan()
                    payload = json.dumps({"success": res.get("success", False), "result": res, "dashboard": coord.get_dashboard_state()}, indent=2).encode("utf-8")
                    self._send_json(200, payload)

                elif parsed.path in ("/api/skyshield/emergency_stop", "/api/skyshield/emergency_stop/", "/api/security/emergency_stop", "/api/security/emergency_stop/"):
                    try:
                        b_data = json.loads(body) if body else {}
                    except Exception:
                        b_data = {}
                    reason = b_data.get("reason", "Operator Emergency Stop via SkyShield Command Center")
                    coord = getattr(dashboard_ref.companion, "security_coordinator", None)
                    if coord:
                        res = coord.trigger_emergency_stop(reason=reason)
                        dash = coord.get_dashboard_state()
                    else:
                        estop = EmergencyStopController()
                        estop.trigger(triggered_by="SkyShield Operator", reason=reason)
                        res = {"success": True, "state": "STOPPED"}
                        dash = {"state": "STOPPED", "emergency_stop_active": True}
                    payload = json.dumps({"success": True, "emergency_stop": True, "result": res, "dashboard": dash}, indent=2).encode("utf-8")
                    self._send_json(200, payload)

                elif parsed.path in ("/api/skyshield/reset", "/api/skyshield/reset/", "/api/security/reset", "/api/security/reset/"):
                    coord = getattr(dashboard_ref.companion, "security_coordinator", None)
                    if not coord:
                        self._send_json(503, json.dumps({"success": False, "error": "Security coordinator unavailable"}).encode("utf-8"))
                        return
                    ok = coord.reset_emergency_stop()
                    payload = json.dumps({"success": ok, "state": coord.current_state.value, "dashboard": coord.get_dashboard_state()}, indent=2).encode("utf-8")
                    self._send_json(200, payload)

                # SkyShield Phase 2 Pairing & Device Management Endpoints
                elif parsed.path in ("/api/skyshield/pair/request", "/api/skyshield/pair/request/"):
                    coord = getattr(dashboard_ref.companion, "security_coordinator", None)
                    if not coord:
                        self._send_json(503, json.dumps({"success": False, "error": "Security coordinator unavailable"}).encode("utf-8"))
                        return
                    try:
                        b_data = json.loads(body) if body else {}
                    except Exception:
                        self._send_json(400, json.dumps({"success": False, "error": "Invalid JSON payload"}).encode("utf-8"))
                        return
                    ok, msg, req = coord.create_pairing_request(
                        device_name=b_data.get("device_name", "Unknown Device"),
                        platform=b_data.get("platform", "android"),
                        phone_number=b_data.get("phone_number"),
                        requested_capabilities=b_data.get("requested_capabilities"),
                        ttl_seconds=int(b_data.get("ttl_seconds", 600)),
                        requester_id=b_data.get("requester_id", "SkyShield Operator"),
                        device_id=b_data.get("device_id"),
                    )
                    payload = json.dumps({
                        "success": ok,
                        "message": msg,
                        "pairing_request": req.to_dict() if req else None,
                    }, indent=2).encode("utf-8")
                    self._send_json(200 if ok else 400, payload)

                elif parsed.path in ("/api/skyshield/pair/approve", "/api/skyshield/pair/approve/"):
                    coord = getattr(dashboard_ref.companion, "security_coordinator", None)
                    if not coord:
                        self._send_json(503, json.dumps({"success": False, "error": "Security coordinator unavailable"}).encode("utf-8"))
                        return
                    try:
                        b_data = json.loads(body) if body else {}
                    except Exception:
                        self._send_json(400, json.dumps({"success": False, "error": "Invalid JSON payload"}).encode("utf-8"))
                        return
                    pairing_id = b_data.get("pairing_id", "")
                    pairing_code = b_data.get("pairing_code", "")
                    fingerprint = b_data.get("device_fingerprint", "")
                    if not fingerprint:
                        fingerprint = hashlib.sha256(f"APPROVED_FP:{pairing_id}:{time.time()}".encode("utf-8")).hexdigest()
                    ok, msg, dev = coord.approve_pairing(
                        pairing_id=pairing_id,
                        pairing_code=pairing_code,
                        device_fingerprint=fingerprint,
                        public_key_hex=b_data.get("public_key_hex"),
                        approver_actor=b_data.get("approver_actor", "Device Owner"),
                    )
                    payload = json.dumps({
                        "success": ok,
                        "message": msg,
                        "device": dev.to_dict() if dev else None,
                    }, indent=2).encode("utf-8")
                    self._send_json(200 if ok else 400, payload)

                elif parsed.path in ("/api/skyshield/pair/reject", "/api/skyshield/pair/reject/"):
                    coord = getattr(dashboard_ref.companion, "security_coordinator", None)
                    if not coord:
                        self._send_json(503, json.dumps({"success": False, "error": "Security coordinator unavailable"}).encode("utf-8"))
                        return
                    try:
                        b_data = json.loads(body) if body else {}
                    except Exception:
                        self._send_json(400, json.dumps({"success": False, "error": "Invalid JSON payload"}).encode("utf-8"))
                        return
                    pairing_id = b_data.get("pairing_id", "")
                    reason = b_data.get("reason", "Device owner rejected pairing")
                    rejector = b_data.get("rejector_actor", "Device Owner")
                    ok, msg = coord.reject_pairing(pairing_id=pairing_id, reason=reason, rejector_actor=rejector)
                    payload = json.dumps({"success": ok, "message": msg}, indent=2).encode("utf-8")
                    self._send_json(200 if ok else 400, payload)

                elif (parsed.path.startswith("/api/skyshield/devices/") and parsed.path.endswith("/suspend")) or parsed.path in ("/api/skyshield/devices/suspend", "/api/skyshield/devices/suspend/"):
                    coord = getattr(dashboard_ref.companion, "security_coordinator", None)
                    if not coord:
                        self._send_json(503, json.dumps({"success": False, "error": "Security coordinator unavailable"}).encode("utf-8"))
                        return
                    try:
                        b_data = json.loads(body) if body else {}
                    except Exception:
                        b_data = {}
                    device_id = b_data.get("device_id")
                    if not device_id and "/api/skyshield/devices/" in parsed.path:
                        parts = parsed.path.strip("/").split("/")
                        if len(parts) >= 4:
                            device_id = parts[3]
                    reason = b_data.get("reason", "Suspended by operator")
                    ok, msg = coord.suspend_device(device_id=device_id, reason=reason)
                    payload = json.dumps({"success": ok, "message": msg, "device_id": device_id}, indent=2).encode("utf-8")
                    self._send_json(200 if ok else 400, payload)

                elif (parsed.path.startswith("/api/skyshield/devices/") and parsed.path.endswith("/revoke")) or parsed.path in ("/api/skyshield/devices/revoke", "/api/skyshield/devices/revoke/"):
                    coord = getattr(dashboard_ref.companion, "security_coordinator", None)
                    if not coord:
                        self._send_json(503, json.dumps({"success": False, "error": "Security coordinator unavailable"}).encode("utf-8"))
                        return
                    try:
                        b_data = json.loads(body) if body else {}
                    except Exception:
                        b_data = {}
                    device_id = b_data.get("device_id")
                    if not device_id and "/api/skyshield/devices/" in parsed.path:
                        parts = parsed.path.strip("/").split("/")
                        if len(parts) >= 4:
                            device_id = parts[3]
                    reason = b_data.get("reason", "Revoked by operator")
                    ok, msg = coord.revoke_device(device_id=device_id, reason=reason)
                    payload = json.dumps({"success": ok, "message": msg, "device_id": device_id}, indent=2).encode("utf-8")
                    self._send_json(200 if ok else 400, payload)

                elif (parsed.path.startswith("/api/skyshield/devices/") and parsed.path.endswith("/reauthorize")) or parsed.path in ("/api/skyshield/devices/reauthorize", "/api/skyshield/devices/reauthorize/"):
                    coord = getattr(dashboard_ref.companion, "security_coordinator", None)
                    if not coord:
                        self._send_json(503, json.dumps({"success": False, "error": "Security coordinator unavailable"}).encode("utf-8"))
                        return
                    try:
                        b_data = json.loads(body) if body else {}
                    except Exception:
                        b_data = {}
                    device_id = b_data.get("device_id")
                    if not device_id and "/api/skyshield/devices/" in parsed.path:
                        parts = parsed.path.strip("/").split("/")
                        if len(parts) >= 4:
                            device_id = parts[3]
                    reason = b_data.get("reason", "Reauthorized by operator")
                    ok, msg = coord.reauthorize_device(device_id=device_id, reason=reason)
                    payload = json.dumps({"success": ok, "message": msg, "device_id": device_id}, indent=2).encode("utf-8")
                    self._send_json(200 if ok else 400, payload)

                # Phase 3 Device Health Baseline Reset
                elif parsed.path.startswith("/api/skyshield/devices/") and (parsed.path.endswith("/baseline/reset") or parsed.path.endswith("/baseline/reset/")):
                    coord = getattr(dashboard_ref.companion, "security_coordinator", None)
                    if not coord:
                        self._send_json(503, json.dumps({"success": False, "error": "Security coordinator unavailable"}).encode("utf-8"))
                        return
                    parts = parsed.path.strip("/").split("/")
                    device_id = parts[3] if len(parts) >= 4 else ""
                    ok, msg = coord.reset_device_baseline(device_id=device_id)
                    payload = json.dumps({"success": ok, "message": msg, "device_id": device_id}, indent=2).encode("utf-8")
                    self._send_json(200 if ok else 400, payload)

                # Phase 3 Device Telemetry Analysis Trigger
                elif parsed.path.startswith("/api/skyshield/devices/") and (parsed.path.endswith("/analyze") or parsed.path.endswith("/analyze/")):
                    coord = getattr(dashboard_ref.companion, "security_coordinator", None)
                    if not coord:
                        self._send_json(503, json.dumps({"success": False, "error": "Security coordinator unavailable"}).encode("utf-8"))
                        return
                    parts = parsed.path.strip("/").split("/")
                    device_id = parts[3] if len(parts) >= 4 else ""
                    try:
                        b_data = json.loads(body) if body else {}
                    except Exception:
                        b_data = {}
                    mock_scen = b_data.get("mock_scenario")
                    snap_data = b_data.get("snapshot")
                    res = coord.analyze_device_telemetry(device_id=device_id, snapshot_data=snap_data, mock_scenario=mock_scen)
                    self._send_json(200 if res.get("success") else 400, json.dumps(res, indent=2).encode("utf-8"))

                elif parsed.path in ("/api/skyshield/session/authenticate", "/api/skyshield/session/authenticate/"):
                    coord = getattr(dashboard_ref.companion, "security_coordinator", None)
                    if not coord:
                        self._send_json(503, json.dumps({"success": False, "error": "Security coordinator unavailable"}).encode("utf-8"))
                        return
                    try:
                        b_data = json.loads(body) if body else {}
                    except Exception:
                        self._send_json(400, json.dumps({"success": False, "error": "Invalid JSON payload"}).encode("utf-8"))
                        return
                    device_id = b_data.get("device_id", "")
                    ttl = int(b_data.get("ttl_seconds", 3600))
                    scopes = b_data.get("requested_scopes")
                    ok, msg, sess = coord.create_device_session(device_id=device_id, ttl_seconds=ttl, requested_scopes=scopes)
                    payload = json.dumps({
                        "success": ok,
                        "message": msg,
                        "session": sess.to_dict() if sess else None,
                    }, indent=2).encode("utf-8")
                    self._send_json(200 if ok else 400, payload)

                elif parsed.path in ("/api/skyshield/session/validate", "/api/skyshield/session/validate/"):
                    coord = getattr(dashboard_ref.companion, "security_coordinator", None)
                    if not coord:
                        self._send_json(503, json.dumps({"success": False, "error": "Security coordinator unavailable"}).encode("utf-8"))
                        return
                    try:
                        b_data = json.loads(body) if body else {}
                    except Exception:
                        self._send_json(400, json.dumps({"success": False, "error": "Invalid JSON payload"}).encode("utf-8"))
                        return
                    ok, msg = coord.validate_session_request(
                        request_id=b_data.get("request_id", ""),
                        device_id=b_data.get("device_id", ""),
                        session_id=b_data.get("session_id", ""),
                        timestamp=float(b_data.get("timestamp", 0.0)),
                        nonce=b_data.get("nonce", ""),
                        action=b_data.get("action", ""),
                        scope=b_data.get("scope", ""),
                        signature=b_data.get("signature", ""),
                        payload=b_data.get("payload"),
                    )
                    payload = json.dumps({"success": ok, "message": msg}, indent=2).encode("utf-8")
                    self._send_json(200 if ok else 400, payload)

                # SkyShield Phase 4 Gated Safe Response Action Proposal
                elif parsed.path in ("/api/skyshield/response/propose", "/api/skyshield/response/propose/"):
                    coord = getattr(dashboard_ref.companion, "security_coordinator", None)
                    try:
                        b_data = json.loads(body) if body else {}
                    except Exception:
                        b_data = {}
                    if not coord:
                        self._send_json(500, json.dumps({"success": False, "error": "Coordinator unavailable"}).encode("utf-8"))
                    else:
                        try:
                            prop = coord.propose_response_action(
                                action=b_data.get("action", ""),
                                device_id=b_data.get("device_id", ""),
                                incident_id=b_data.get("incident_id"),
                                reason=b_data.get("reason", "Operator proposal"),
                                initiated_by=b_data.get("initiated_by", "Operator"),
                            )
                            self._send_json(200, json.dumps({"success": True, "proposal": prop}, indent=2).encode("utf-8"))
                        except Exception as ex:
                            self._send_json(400, json.dumps({"success": False, "error": str(ex)}).encode("utf-8"))

                # SkyShield Phase 4 Gated Safe Response Action Confirmation
                elif parsed.path in ("/api/skyshield/response/confirm", "/api/skyshield/response/confirm/"):
                    coord = getattr(dashboard_ref.companion, "security_coordinator", None)
                    try:
                        b_data = json.loads(body) if body else {}
                    except Exception:
                        b_data = {}
                    if not coord:
                        self._send_json(500, json.dumps({"success": False, "error": "Coordinator unavailable"}).encode("utf-8"))
                    else:
                        p_id = b_data.get("proposal_id", "")
                        conf = bool(b_data.get("operator_confirmed", True))
                        ok, msg, p = coord.confirm_response_action(p_id, operator_confirmed=conf)
                        status_code = 200 if ok else 403
                        self._send_json(status_code, json.dumps({"success": ok, "message": msg, "proposal": p}, indent=2).encode("utf-8"))

                # SkyShield Phase 4 Incident Status & False Positive Updates
                elif parsed.path.startswith("/api/skyshield/incidents/"):
                    parts = parsed.path.strip("/").split("/")
                    inc_id = parts[3] if len(parts) >= 4 else ""
                    sub_act = parts[4] if len(parts) >= 5 else ""
                    coord = getattr(dashboard_ref.companion, "security_coordinator", None)
                    try:
                        b_data = json.loads(body) if body else {}
                    except Exception:
                        b_data = {}

                    if not coord:
                        self._send_json(500, json.dumps({"success": False, "error": "Coordinator unavailable"}).encode("utf-8"))
                    elif sub_act == "false_positive":
                        reason = b_data.get("reason", "Operator determination")
                        ok, msg = coord.mark_false_positive(inc_id, reason=reason)
                        self._send_json(200 if ok else 400, json.dumps({"success": ok, "message": msg}, indent=2).encode("utf-8"))
                    elif sub_act == "resolve":
                        res_text = b_data.get("resolution", "Resolved by operator")
                        ok, msg = coord.resolve_incident(inc_id, resolution=res_text)
                        self._send_json(200 if ok else 400, json.dumps({"success": ok, "message": msg}, indent=2).encode("utf-8"))
                    elif sub_act == "status":
                        st = b_data.get("status", "")
                        reason = b_data.get("reason")
                        ok, msg = coord.update_incident_status(inc_id, new_status=st, reason=reason)
                        self._send_json(200 if ok else 400, json.dumps({"success": ok, "message": msg}, indent=2).encode("utf-8"))
                    elif sub_act == "analyze":
                        sec_agent = getattr(dashboard_ref.companion, "security_agent", None)
                        if sec_agent and hasattr(sec_agent, "analyze_incident"):
                            res = sec_agent.analyze_incident(inc_id)
                        else:
                            inc = coord.get_incident(inc_id)
                            res = {"success": bool(inc), "analysis": inc.get("ai_analysis") if inc else None}
                        self._send_json(200 if res.get("success") else 404, json.dumps(res, indent=2).encode("utf-8"))
                    else:
                        self._send_json(404, json.dumps({"success": False, "error": f"Unknown action '{sub_act}'"}).encode("utf-8"))

                # SkyShield Phase 4 Threat Vulnerability Check
                elif parsed.path in ("/api/skyshield/threats/check", "/api/skyshield/threats/check/"):
                    coord = getattr(dashboard_ref.companion, "security_coordinator", None)
                    try:
                        b_data = json.loads(body) if body else {}
                    except Exception:
                        b_data = {}
                    dev_id = b_data.get("device_id", "dev_mock_vivo_v2334")
                    res = coord.check_device_vulnerability(dev_id) if coord else {"success": False, "error": "Coordinator unavailable"}
                    self._send_json(200 if res.get("success") else 404, json.dumps(res, indent=2).encode("utf-8"))

                # 3. Agent Specific Action
                elif parsed.path == "/api/conversation/active":
                    try:
                        b_data = json.loads(body) if body else {}
                    except Exception:
                        b_data = {}
                    agent_id = b_data.get("agent_id")
                    comp = dashboard_ref.companion
                    if comp:
                        comp.active_conversation_agent = agent_id
                    self._send_json(200, json.dumps({"success": True, "active_conversation_agent": agent_id}).encode("utf-8"))

                elif parsed.path == "/api/conversation/interrupt":
                    comp = dashboard_ref.companion
                    if comp and hasattr(comp.speaker, "stop"):
                        comp.speaker.stop()
                    self._send_json(200, json.dumps({
                        "success": True,
                        "status": "USER_INTERRUPTED",
                        "active_conversation_agent": getattr(comp, "active_conversation_agent", None),
                    }).encode("utf-8"))

                elif parsed.path.startswith("/api/agent/") and (parsed.path.endswith("/activate") or parsed.path.endswith("/activate/")):
                    agent_id = parsed.path[len("/api/agent/"):].rstrip("/").replace("/activate", "").strip("/")
                    if agent_id in ("nova_discovery_agent", "aegis_verification_agent"):
                        agent_id = "universal_knowledge_engine"
                    speech = "Yes Boss, I'm ready."
                    is_first = False
                    if dashboard_ref.companion and hasattr(dashboard_ref.companion, "activate_agent_session"):
                        speech, is_first = dashboard_ref.companion.activate_agent_session(agent_id)
                    else:
                        if dashboard_ref.companion:
                            dashboard_ref.companion.active_conversation_agent = agent_id
                    payload = json.dumps({
                        "success": True,
                        "agent_id": agent_id,
                        "speech": speech,
                        "first_intro": is_first,
                        "active_conversation_agent": agent_id,
                    }, indent=2).encode("utf-8")
                    self._send_json(200, payload)

                elif parsed.path.startswith("/api/agent/") and (parsed.path.endswith("/chat") or parsed.path.endswith("/chat/")):
                    agent_id = parsed.path[len("/api/agent/"):].rstrip("/").replace("/chat", "").strip("/")
                    if agent_id in ("nova_discovery_agent", "aegis_verification_agent"):
                        agent_id = "universal_knowledge_engine"
                    try:
                        c_data = json.loads(body) if body else {}
                    except Exception:
                        c_data = {}
                    cmd = c_data.get("text") or c_data.get("command") or body.strip()
                    speak = bool(c_data.get("speak_output", False))
                    comp = dashboard_ref.companion
                    if comp and hasattr(comp, "interact"):
                        comp.active_conversation_agent = agent_id
                        resp = comp.interact(cmd, speak_output=speak)
                        resp_dict = resp.to_dict() if hasattr(resp, "to_dict") else {"text": str(resp)}
                        reply = getattr(resp, "text", str(resp))
                    else:
                        reply = f"Agent '{agent_id}' processed: {cmd}"
                        resp_dict = {"text": reply}
                    payload = json.dumps({
                        "success": True,
                        "agent_id": agent_id,
                        "reply": reply,
                        "response": resp_dict,
                        "card": resp_dict.get("data", {}) if isinstance(resp_dict, dict) else {},
                    }, indent=2).encode("utf-8")
                    self._send_json(200, payload)

                elif parsed.path.startswith("/api/agent/") and parsed.path.endswith("/action"):
                    path_parts = parsed.path.strip("/").split("/")
                    # path_parts: ["api", "agent", "<agent_id>", "action"]
                    agent_id = path_parts[2] if len(path_parts) >= 4 else "unknown"
                    try:
                        action_data = json.loads(body) if body else {}
                    except Exception:
                        action_data = {}
                    action_id = action_data.get("action_id", "")
                    label = action_data.get("label", action_id)

                    prompt = f"Agent '{agent_id}' executing action: {label}"
                    if dashboard_ref.companion and hasattr(dashboard_ref.companion, "interact"):
                        resp = dashboard_ref.companion.interact(prompt, speak_output=False)
                        reply_text = getattr(resp, "text", str(resp))
                    else:
                        reply_text = f"Action '{label}' executed for agent '{agent_id}'."

                    payload = json.dumps({
                        "success": True,
                        "agent_id": agent_id,
                        "action_id": action_id,
                        "result": reply_text,
                    }, indent=2).encode("utf-8")
                    self._send_json(200, payload)

                else:
                    self._send_json(404, json.dumps({"error": "NOT_FOUND"}).encode("utf-8"))

            def log_message(self, format, *args):
                pass  # suppress HTTP request logs in console

        try:
            class ReusableTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
                allow_reuse_address = True
                daemon_threads = True

            self._httpd = ReusableTCPServer(("127.0.0.1", self.port), DashboardHTTPHandler)
            self._server_thread = threading.Thread(
                target=self._httpd.serve_forever,
                name="NRAI-DashboardServer",
                daemon=True,
            )
            self._server_thread.start()
            logger.info(f"Companion Dashboard HTTP server started at http://localhost:{self.port}")
            return True
        except Exception as e:
            logger.error(f"Failed to start dashboard HTTP server on port {port}: {e}")
            return False

    def stop_http_server(self) -> None:
        if self._httpd:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception:
                pass
            self._httpd = None

    def _render_html_page(self, data: Dict[str, Any]) -> str:
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>NR-AI Companion Dashboard</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 20px; }}
        .header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #334155; padding-bottom: 15px; margin-bottom: 20px; }}
        .badge {{ background: #1e293b; border: 1px solid #38bdf8; padding: 6px 14px; border-radius: 20px; font-weight: bold; color: #38bdf8; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; }}
        .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 18px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.2); }}
        .card h3 {{ margin-top: 0; color: #94a3b8; font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.05em; }}
        .card .value {{ font-size: 1.3rem; font-weight: bold; color: #f1f5f9; }}
        .news-item {{ border-left: 3px solid #38bdf8; padding-left: 10px; margin-bottom: 10px; font-size: 0.9rem; }}
        .news-source {{ font-size: 0.75rem; color: #94a3b8; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>NR-AI Companion & Agent Dashboard</h1>
        <div class="badge">{data['assistant_status']}</div>
    </div>
    <div class="grid">
        <div class="card">
            <h3>Microphone Hardware</h3>
            <div class="value">{'ONLINE' if data['microphone']['available'] else 'OFFLINE'}</div>
            <p style="color:#94a3b8; font-size:0.85rem;">Device: {data['microphone']['device_name']}</p>
            <p style="color:#94a3b8; font-size:0.85rem;">Wake Word: {'Hey NR / Hello NR' if data['microphone']['wake_word_enabled'] else 'Push-To-Talk'}</p>
        </div>
        <div class="card" style="grid-column: span 2;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                <h3 style="margin: 0;">Model Execution Pipeline</h3>
                <span class="badge" style="background: {'#065f46' if data['model_execution']['cloud_request_success'] == 'YES' else '#7f1d1d'}; border-color: {'#10b981' if data['model_execution']['cloud_request_success'] == 'YES' else '#ef4444'}; color: {'#a7f3d0' if data['model_execution']['cloud_request_success'] == 'YES' else '#fca5a5'}; font-size: 0.75rem; padding: 4px 10px;">
                    {data['model_execution']['cloud_ai_status']}
                </span>
            </div>
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 8px; font-size:0.85rem; line-height:1.5;">
                <div><span style="color:#94a3b8;">CONFIGURED ROLE:</span> <b style="color:#a78bfa;">{data['model_execution']['configured_role']}</b></div>
                <div><span style="color:#94a3b8;">CONFIGURED MODEL:</span> <b style="color:#38bdf8;">{data['model_execution']['configured_model']}</b></div>
                <div><span style="color:#94a3b8;">REQUESTED MODEL:</span> <b style="color:#38bdf8;">{data['model_execution']['requested_model']}</b></div>
                <div><span style="color:#94a3b8;">ACTUAL MODEL USED:</span> <b style="color:#f59e0b;">{data['model_execution']['actual_model_used']}</b></div>
                <div><span style="color:#94a3b8;">PROVIDER:</span> <b>{data['model_execution']['provider']}</b></div>
                <div><span style="color:#94a3b8;">LIVE API SUCCESS:</span> <b style="color:{'#22c55e' if data['model_execution']['live_api_success'] == 'YES' else '#ef4444'};">{data['model_execution']['live_api_success']}</b></div>
                <div><span style="color:#94a3b8;">HTTP STATUS:</span> <b>{data['model_execution']['http_status']}</b></div>
                <div><span style="color:#94a3b8;">FALLBACK USED:</span> <b style="color:{'#ef4444' if data['model_execution']['fallback_used'] == 'YES' else '#22c55e'};">{data['model_execution']['fallback_used']}</b></div>
                <div><span style="color:#94a3b8;">TASK ID:</span> <span style="color:#e2e8f0;">{data['model_execution']['task_id']}</span></div>
                <div><span style="color:#94a3b8;">AGENT:</span> <span style="color:#e2e8f0;">{data['model_execution']['agent']}</span></div>
                <div style="grid-column: 1 / -1;"><span style="color:#94a3b8;">VERIFICATION STATUS:</span> <b style="color:#38bdf8;">{data['model_execution']['verification_status']}</b></div>
                <div style="grid-column: 1 / -1;"><span style="color:#94a3b8;">SAFE EXECUTION EVIDENCE:</span> <span style="color:#cbd5e1; font-size:0.78rem;">{data['model_execution']['safe_execution_evidence']}</span></div>
            </div>
        </div>
        <div class="card">
            <h3>10-Agent Concurrency</h3>
            <div class="value">{data['orchestrator_metrics']['busy_slots']}/{data['orchestrator_metrics']['total_slots']} Slots Active</div>
            <p style="color:#94a3b8; font-size:0.85rem;">Completed Tasks: {data['orchestrator_metrics']['completed_tasks']}</p>
            <p style="color:#94a3b8; font-size:0.85rem;">Avatar Emotion: {data['avatar_emotion']}</p>
        </div>
    </div>
    <div class="card" style="margin-top: 16px;">
        <h3>Voice & Wake Word Tracking</h3>
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px;">
            <div>
                <div style="color: #94a3b8; font-size: 0.8rem; text-transform: uppercase;">Wake Phrase</div>
                <div style="font-size: 1.1rem; font-weight: bold; color: #38bdf8;">"{data.get('recognized_wake_phrase', 'None')}"</div>
            </div>
            <div>
                <div style="color: #94a3b8; font-size: 0.8rem; text-transform: uppercase;">Current Route</div>
                <div style="font-size: 1.1rem; font-weight: bold; color: #f1f5f9;">{data.get('current_route', 'CompanionPersona')}</div>
            </div>
            <div>
                <div style="color: #94a3b8; font-size: 0.8rem; text-transform: uppercase;">Active Slot</div>
                <div style="font-size: 1.1rem; font-weight: bold; color: #f1f5f9;">{data.get('current_agent', 'Agent-1-Architect')}</div>
            </div>
            <div>
                <div style="color: #94a3b8; font-size: 0.8rem; text-transform: uppercase;">Task Status</div>
                <div style="font-size: 1.1rem; font-weight: bold; color: #38bdf8;">{data.get('current_task_status', 'Idle')}</div>
            </div>
        </div>
    </div>
    <div class="card" style="margin-top: 16px;">
        <h3>Last Recognized Command</h3>
        <p style="font-size: 1.1rem; color: #38bdf8;">"{data['current_command']}"</p>
        <p style="color: #cbd5e1;">{data['last_response']}</p>
    </div>
    <div class="card" style="margin-top: 16px;">
        <h3>Interactive Companion Command</h3>
        <form id="cmdForm" onsubmit="event.preventDefault(); sendCmd();" style="display: flex; gap: 8px; margin-top: 8px;">
            <input type="text" id="cmdInput" placeholder="Type a command (e.g. Hello NR, latest AI news)..." style="flex: 1; padding: 10px; border-radius: 6px; border: 1px solid #475569; background: #0f172a; color: #f8fafc; font-size: 1rem;">
            <button type="submit" style="padding: 10px 20px; background: #0284c7; color: white; border: none; border-radius: 6px; font-weight: bold; cursor: pointer;">Send</button>
        </form>
        <div id="cmdOutput" style="margin-top: 10px; font-size: 1rem; color: #38bdf8;"></div>
    </div>
    <script>
        async function sendCmd() {{
            const input = document.getElementById('cmdInput');
            const output = document.getElementById('cmdOutput');
            const cmd = input.value.trim();
            if (!cmd) return;
            output.innerText = 'Processing command...';
            try {{
                const res = await fetch('/api/command', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{command: cmd}})
                }});
                const data = await res.json();
                output.innerText = data.text;
                setTimeout(() => location.reload(), 1500);
            }} catch(e) {{
                output.innerText = 'Error: ' + e;
            }}
        }}
    </script>
    <div class="card" style="margin-top: 16px;">
        <h3>Latest Verified News</h3>
        {''.join(f"<div class='news-item'><div>{it['headline']}</div><div class='news-source'>Source: {it['source']} | Topic: {it['topic']}</div></div>" for it in data['latest_news'][:3])}
    </div>
</body>
</html>"""
