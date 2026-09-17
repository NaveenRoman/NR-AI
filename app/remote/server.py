"""
NR-AI Secure Transport Server & API Gateway.
Step 10 Phase 1 — Secure Phone <-> PC Communication Foundation.

Binds exclusively to 127.0.0.1:8585 to maintain strict localhost security.
Rejects any attempt to bind to 0.0.0.0 or external network interfaces.
"""

from dataclasses import asdict
import http.server
import json
import logging
import socketserver
import threading
import time
from typing import Any, Dict, Optional, Set, Tuple
import urllib.parse

from app.remote.audit import SecurityAuditLogger
from app.remote.auth import SessionManager
from app.remote.config import (
    ALLOWED_HOSTS,
    DEFAULT_FPS,
    DEFAULT_HOST,
    DEFAULT_PORT,
    MAX_FRAME_HEIGHT,
    MAX_FRAME_WIDTH,
    MAX_REQUEST_BYTES,
    PROHIBITED_HOSTS,
)
from app.remote.emergency import EmergencyStopController
from app.remote.identity import PairingManager, PairingState, PCIdentity
from app.remote.permissions import (
    PhonePermissionScope,
    authorize_action,
)
from app.remote.protocol import (
    SecureRequest,
    SecureResponse,
    parse_and_validate_request,
)
from app.remote.rate_limiter import RateLimiter, RemoteActionRateLimiter, VoiceRateLimiter
from app.remote.frame import FrameEncoding, StreamFrame
from app.remote.screen_capture import MockScreenCaptureEngine, ScreenCaptureEngine
from app.remote.stream import StreamManager, StreamSession, StreamState
from app.remote.telemetry import TelemetryDispatcher, TelemetryEvent, TelemetryEventType
from app.remote.transport import SecureTransport, TransportSecurityMode
from app.remote.audio import (
    AudioFormat,
    AudioRequest,
    VoiceResponse,
    create_audio_request,
    validate_audio_request,
)
from app.remote.speech_to_text import (
    DevelopmentSpeechToTextProvider,
    SpeechToTextProvider,
)
from app.remote.voice_session import (
    VoiceCommandSession,
    VoiceSessionManager,
    VoiceSessionState,
)
from app.remote.remote_actions import (
    RemoteActionRequest,
    RemoteActionResult,
    RemoteActionType,
    validate_remote_action_request,
)
from app.remote.remote_action_safety import RemoteActionSafetyGate
from app.remote.remote_action_session import (
    RemoteActionSession,
    RemoteActionSessionManager,
    RemoteActionState,
)

logger = logging.getLogger("NRAI.SecureServer")


class SecurityBindingError(Exception):
    """Raised when an attempt is made to bind outside the secure localhost boundary."""
    pass


class SecureGateway:
    """
    Core security gateway orchestrating pairing, auth, sessions, scopes,
    rate limiting, emergency stop, and audit logging.
    """

    def __init__(
        self,
        companion: Optional[Any] = None,
        pairing_manager: Optional[PairingManager] = None,
        session_manager: Optional[SessionManager] = None,
        rate_limiter: Optional[RateLimiter] = None,
        emergency_stop: Optional[EmergencyStopController] = None,
        audit_logger: Optional[SecurityAuditLogger] = None,
        telemetry_dispatcher: Optional[TelemetryDispatcher] = None,
        stream_manager: Optional[StreamManager] = None,
        capture_engine: Optional[ScreenCaptureEngine] = None,
        voice_session_manager: Optional[VoiceSessionManager] = None,
        voice_rate_limiter: Optional[VoiceRateLimiter] = None,
        stt_provider: Optional[SpeechToTextProvider] = None,
        transport_mode: TransportSecurityMode = TransportSecurityMode.ENCRYPTED_SESSION,
        remote_action_session_manager: Optional[RemoteActionSessionManager] = None,
        remote_action_rate_limiter: Optional[RemoteActionRateLimiter] = None,
        computer_agent: Optional[Any] = None,
    ):
        self.companion = companion
        self.pairing_manager = pairing_manager or PairingManager()
        self.session_manager = session_manager or SessionManager(self.pairing_manager)
        self.rate_limiter = rate_limiter or RateLimiter()
        self.emergency_stop = emergency_stop or EmergencyStopController()
        self.audit_logger = audit_logger or SecurityAuditLogger()
        self.transport = SecureTransport(mode=transport_mode)
        self.telemetry_dispatcher = telemetry_dispatcher or TelemetryDispatcher()
        self.stream_manager = stream_manager or StreamManager(
            capture_engine=capture_engine,
            emergency_stop=self.emergency_stop,
        )
        self.voice_rate_limiter = voice_rate_limiter or VoiceRateLimiter()
        self.voice_session_manager = voice_session_manager or VoiceSessionManager(
            stt_provider=stt_provider,
            emergency_controller=self.emergency_stop,
            command_router_fn=(lambda cmd: self.companion.interact(cmd, speak_output=False).to_dict()) if self.companion and hasattr(self.companion, "interact") else None,
            audit_logger_fn=self.audit_logger.log_event,
            telemetry_event_fn=self.telemetry_dispatcher.dispatch if hasattr(self.telemetry_dispatcher, "dispatch") else None,
        )
        self.remote_action_rate_limiter = remote_action_rate_limiter or RemoteActionRateLimiter()
        comp_agent = computer_agent
        if comp_agent is None and self.companion and hasattr(self.companion, "computer_agent"):
            comp_agent = getattr(self.companion, "computer_agent")
        self.remote_action_session_manager = remote_action_session_manager or RemoteActionSessionManager(
            computer_agent=comp_agent,
            emergency_controller=self.emergency_stop,
            rate_limiter=self.remote_action_rate_limiter,
            audit_logger=self.audit_logger,
        )

    def handle_pairing_request(self, body: Dict[str, Any], client_ip: str) -> SecureResponse:
        device_id = str(body.get("device_id", "")).strip()
        code = str(body.get("pairing_code", "")).strip()
        name = str(body.get("device_name", "Android Companion")).strip()
        platform = str(body.get("platform", "Android")).strip()
        app_ver = str(body.get("app_version", "1.0")).strip()

        if not device_id or not code:
            self.audit_logger.log_event(
                event_type="PAIRING_ATTEMPT",
                result="DENIED",
                device_id=device_id,
                reason="MISSING_DEVICE_ID_OR_CODE",
                client_ip=client_ip,
            )
            return SecureResponse(
                request_id=str(body.get("request_id", "pair-req")),
                status="DENIED",
                code=400,
                error="device_id and pairing_code are required",
            )

        # Check rate limiting on pairing attempts
        allowed, reason, retry_after = self.rate_limiter.allow_request(f"pair:{client_ip}")
        if not allowed:
            self.audit_logger.log_event(
                event_type="PAIRING_ATTEMPT",
                result="BLOCKED",
                device_id=device_id,
                reason=reason,
                client_ip=client_ip,
            )
            return SecureResponse(
                request_id="pair-req",
                status="DENIED",
                code=429,
                error=reason,
            )

        success, msg, secret_hex = self.pairing_manager.confirm_pairing(
            device_id=device_id,
            pairing_code=code,
            device_name=name,
            platform=platform,
            app_version=app_ver,
        )

        if not success:
            self.audit_logger.log_event(
                event_type="PAIRING_ATTEMPT",
                result="DENIED",
                device_id=device_id,
                reason=msg,
                client_ip=client_ip,
            )
            return SecureResponse(
                request_id="pair-req",
                status="DENIED",
                code=403,
                error=f"Pairing rejected: {msg}",
            )

        self.audit_logger.log_event(
            event_type="PAIRING_SUCCESS",
            result="SUCCESS",
            device_id=device_id,
            reason="Device paired successfully",
            client_ip=client_ip,
        )
        return SecureResponse(
            request_id="pair-req",
            status="SUCCESS",
            code=200,
            data={
                "device_id": device_id,
                "pc_identity": self.pairing_manager.pc_identity.to_dict(),
                "shared_secret_hex": secret_hex,
                "paired_at": time.time(),
            },
        )

    def handle_auth_request(self, body: Dict[str, Any], client_ip: str) -> SecureResponse:
        device_id = str(body.get("device_id", "")).strip()
        secret_attempt = str(body.get("device_secret", "")).strip()

        # Check lockout
        allowed, reason, retry_after = self.rate_limiter.allow_request(f"auth:{device_id}")
        if not allowed:
            self.audit_logger.log_event(
                event_type="AUTH_ATTEMPT",
                result="BLOCKED",
                device_id=device_id,
                reason=reason,
                client_ip=client_ip,
            )
            return SecureResponse(
                request_id="auth-req",
                status="DENIED",
                code=429,
                error=reason,
            )

        stored_secret = self.pairing_manager.get_device_secret(device_id)
        if not stored_secret or stored_secret != secret_attempt:
            is_locked, failures, locked_until = self.rate_limiter.record_auth_failure(f"auth:{device_id}")
            err_msg = "INVALID_CREDENTIALS"
            if is_locked:
                err_msg = f"AUTH_LOCKOUT_TRIGGERED: too many failed attempts (5). Locked for 300s."
            self.audit_logger.log_event(
                event_type="AUTH_FAILURE",
                result="DENIED",
                device_id=device_id,
                reason=err_msg,
                client_ip=client_ip,
            )
            return SecureResponse(
                request_id="auth-req",
                status="DENIED",
                code=401,
                error=err_msg,
            )

        # Authentication succeeded! Reset failure counter
        self.rate_limiter.record_auth_success(f"auth:{device_id}")

        ttl = int(body.get("ttl_seconds", 3600))
        created, msg, session = self.session_manager.create_session(device_id, ttl_seconds=ttl)
        if not created or not session:
            self.audit_logger.log_event(
                event_type="SESSION_CREATION_FAILED",
                result="ERROR",
                device_id=device_id,
                reason=msg,
                client_ip=client_ip,
            )
            return SecureResponse(
                request_id="auth-req",
                status="ERROR",
                code=500,
                error=f"Session creation failed: {msg}",
            )

        self.audit_logger.log_event(
            event_type="SESSION_ESTABLISHED",
            result="SUCCESS",
            device_id=device_id,
            session_id=session.session_id,
            reason="Authenticated session created",
            client_ip=client_ip,
        )

        return SecureResponse(
            request_id="auth-req",
            status="SUCCESS",
            code=200,
            data={
                "session_id": session.session_id,
                "session_token": session.session_token,
                "session_key_hex": session.session_key_hex,
                "expires_at": session.expires_at,
                "scopes": [s.value for s in session.scopes],
                "transport_mode": self.transport.mode.value,
            },
        )

    def handle_secure_command(self, raw_data: str, client_ip: str) -> SecureResponse:
        # 1. Parse & validate request structure and size
        valid, req, err = parse_and_validate_request(raw_data)
        if not valid or not req:
            self.audit_logger.log_event(
                event_type="INVALID_REQUEST",
                result="DENIED",
                reason=err,
                client_ip=client_ip,
            )
            return SecureResponse(
                request_id="unknown",
                status="DENIED",
                code=400,
                error=f"Malformed request: {err}",
            )

        # 2. Rate limiting check per session/device
        allowed, reason, retry_after = self.rate_limiter.allow_request(req.session_id)
        if not allowed:
            self.audit_logger.log_event(
                event_type="RATE_LIMIT_EXCEEDED",
                result="BLOCKED",
                device_id=req.device_id,
                session_id=req.session_id,
                action=req.action,
                reason=reason,
                client_ip=client_ip,
            )
            return SecureResponse(
                request_id=req.request_id,
                status="DENIED",
                code=429,
                error=reason,
            )

        # 3. Session validation
        s_valid, s_msg, session = self.session_manager.validate_session(req.session_id, req.device_id)
        if not s_valid or not session:
            self.audit_logger.log_event(
                event_type="SESSION_VALIDATION_FAILED",
                result="DENIED",
                device_id=req.device_id,
                session_id=req.session_id,
                action=req.action,
                reason=s_msg,
                client_ip=client_ip,
            )
            return SecureResponse(
                request_id=req.request_id,
                status="DENIED",
                code=401,
                error=f"Session invalid: {s_msg}",
            )

        # 4. Request signature and replay verification
        sig_valid, sig_msg = self.session_manager.verify_request_signature(req)
        if not sig_valid:
            self.audit_logger.log_event(
                event_type="SIGNATURE_VERIFICATION_FAILED",
                result="DENIED",
                device_id=req.device_id,
                session_id=req.session_id,
                action=req.action,
                reason=sig_msg,
                client_ip=client_ip,
            )
            return SecureResponse(
                request_id=req.request_id,
                status="DENIED",
                code=403,
                error=f"Signature / Replay check failed: {sig_msg}",
            )

        # 5. Scoped authorization & emergency stop check
        auth_res = authorize_action(
            action=req.action,
            granted_scopes=session.scopes,
            is_emergency_active=self.emergency_stop.is_active(),
            command_text=str(req.payload.get("command", "")),
        )

        if not auth_res.allowed:
            self.audit_logger.log_event(
                event_type="AUTHORIZATION_DENIED",
                result="DENIED",
                device_id=req.device_id,
                session_id=req.session_id,
                action=req.action,
                scope=req.scope,
                reason=auth_res.reason,
                client_ip=client_ip,
            )
            return SecureResponse(
                request_id=req.request_id,
                status="DENIED",
                code=403,
                error=f"Action denied: {auth_res.decision_code} - {auth_res.reason}",
            )

        # 6. Execute action deterministically
        try:
            result_data = self._dispatch_action(req, session)
            self.audit_logger.log_event(
                event_type="COMMAND_EXECUTED",
                result="SUCCESS",
                device_id=req.device_id,
                session_id=req.session_id,
                action=req.action,
                scope=req.scope,
                reason="Action executed successfully",
                client_ip=client_ip,
            )
            return SecureResponse(
                request_id=req.request_id,
                status="SUCCESS",
                code=200,
                data=result_data,
            )
        except Exception as e:
            self.audit_logger.log_event(
                event_type="COMMAND_EXECUTION_ERROR",
                result="ERROR",
                device_id=req.device_id,
                session_id=req.session_id,
                action=req.action,
                reason=str(e),
                client_ip=client_ip,
            )
            return SecureResponse(
                request_id=req.request_id,
                status="ERROR",
                code=500,
                error=f"Execution error: {e}",
            )

    def _dispatch_action(self, req: SecureRequest, session: Any) -> Dict[str, Any]:
        action = req.action.lower().strip()
        if action == "status.read":
            if self.companion and hasattr(self.companion, "get_status_snapshot"):
                return self.companion.get_status_snapshot()
            return {"status": "ONLINE", "mode": "IDLE", "active_agent": "Agent-1"}

        elif action == "device.info":
            dev = self.pairing_manager.get_device(req.device_id)
            return {
                "device": dev.to_dict() if dev else {},
                "pc": self.pairing_manager.pc_identity.to_dict(),
            }

        elif action in ("command.send", "voice.command"):
            cmd_text = str(req.payload.get("command") or req.payload.get("text", "")).strip()
            if not cmd_text:
                return {"reply": "No command provided"}

            if self.companion and hasattr(self.companion, "interact"):
                resp = self.companion.interact(cmd_text, speak_output=False)
                if hasattr(resp, "to_dict"):
                    return resp.to_dict()
                return {"reply": str(resp)}
            return {"reply": f"Received command: {cmd_text}"}

        elif action == "emergency.stop":
            status = self.emergency_stop.trigger(
                triggered_by=f"PHONE:{req.device_id}",
                reason=str(req.payload.get("reason", "Phone initiated emergency stop")),
            )
            return status.to_dict()

        elif action == "emergency.status":
            return self.emergency_stop.get_status().to_dict()

        elif action in ("telemetry.read", "telemetry.subscribe"):
            since_seq = int(req.payload.get("since_sequence", 0))
            events = self.telemetry_dispatcher.get_events(req.session_id, since_sequence=since_seq)
            return {
                "events": [e.to_dict() for e in events],
                "latest_sequence": self.telemetry_dispatcher.get_latest_sequence(req.session_id),
            }

        elif action == "stream.start":
            fps = float(req.payload.get("target_fps", DEFAULT_FPS))
            w = int(req.payload.get("max_width", MAX_FRAME_WIDTH))
            h = int(req.payload.get("max_height", MAX_FRAME_HEIGHT))
            enc_str = str(req.payload.get("encoding", "JPEG")).upper()
            try:
                enc = FrameEncoding(enc_str)
            except ValueError:
                enc = FrameEncoding.JPEG

            ok, msg, stream = self.stream_manager.create_stream(
                device_id=req.device_id,
                session_id=req.session_id,
                target_fps=fps,
                max_width=w,
                max_height=h,
                encoding=enc,
            )
            if not ok or not stream:
                raise ValueError(f"Failed to create stream: {msg}")
            return stream.to_dict()

        elif action == "stream.stop":
            stream_id = str(req.payload.get("stream_id", "")).strip()
            ok, msg = self.stream_manager.stop_stream(stream_id, req.session_id)
            if not ok:
                raise ValueError(f"Failed to stop stream: {msg}")
            return {"stream_id": stream_id, "status": "STOPPED"}

        elif action == "stream.pause":
            stream_id = str(req.payload.get("stream_id", "")).strip()
            ok, msg = self.stream_manager.pause_stream(stream_id, req.session_id)
            if not ok:
                raise ValueError(f"Failed to pause stream: {msg}")
            return {"stream_id": stream_id, "status": "PAUSED"}

        elif action == "stream.resume":
            stream_id = str(req.payload.get("stream_id", "")).strip()
            ok, msg = self.stream_manager.resume_stream(stream_id, req.session_id)
            if not ok:
                raise ValueError(f"Failed to resume stream: {msg}")
            return {"stream_id": stream_id, "status": "ACTIVE"}

        elif action == "stream.frame":
            stream_id = str(req.payload.get("stream_id", "")).strip()
            # If queue is empty, attempt to produce a frame
            stream = self.stream_manager.get_stream(stream_id)
            if stream and stream.queue.get_stats()["queued_frames"] == 0:
                self.stream_manager.produce_frame(stream_id)
            ok, msg, frame = self.stream_manager.get_next_frame(stream_id, req.session_id)
            if not ok or not frame:
                return {"status": "NO_FRAME", "reason": msg}
            return frame.to_dict()

        elif action == "stream.status":
            stream_id = str(req.payload.get("stream_id", "")).strip()
            stream = self.stream_manager.get_stream(stream_id)
            if not stream:
                return {"status": "NOT_FOUND"}
            return stream.to_dict()

        elif action == "voice.audio_upload":
            import hashlib
            payload_hex = str(req.payload.get("payload_hex", ""))
            try:
                raw_bytes = bytes.fromhex(payload_hex) if payload_hex else req.payload.get("audio_bytes", b"")
                if isinstance(raw_bytes, str):
                    raw_bytes = raw_bytes.encode("utf-8")
            except Exception:
                raw_bytes = b""
            fmt = str(req.payload.get("audio_format", "WAV")).upper()
            sr = int(req.payload.get("sample_rate", 16000))
            ch = int(req.payload.get("channels", 1))
            dur = float(req.payload.get("duration_ms", 1000.0))
            audio_req = AudioRequest(
                request_id=req.request_id,
                session_id=req.session_id,
                device_id=req.device_id,
                timestamp=req.timestamp,
                nonce=req.nonce,
                audio_format=fmt,
                sample_rate=sr,
                channels=ch,
                duration_ms=dur,
                payload_size=len(raw_bytes),
                checksum=req.payload.get("checksum") or hashlib.sha256(raw_bytes).hexdigest(),
                audio_bytes=raw_bytes,
                metadata=req.payload.get("metadata", {}),
            )
            v_resp = self.voice_session_manager.process_voice_request(audio_req, session.scopes)
            return v_resp.to_dict()

        elif action == "voice.confirm":
            cid = str(req.payload.get("confirmation_id", "")).strip()
            conf = bool(req.payload.get("confirmed", True))
            ok, msg, v_resp = self.voice_session_manager.confirm_action(req.session_id, cid, conf)
            if not ok or not v_resp:
                return {"status": "ERROR", "reason": msg}
            return v_resp.to_dict()

        elif action == "voice.cancel":
            ok, msg = self.voice_session_manager.cancel_session(req.session_id)
            return {"status": "CANCELLED" if ok else "ERROR", "reason": msg}

        elif action == "voice.status":
            sess = self.voice_session_manager.get_session(req.session_id)
            if not sess:
                return {"status": "NOT_FOUND"}
            return sess.to_dict()

        # Phase 4: Scoped Remote Actions & Computer Control
        elif action in ("action.remote_execute", "action.approved_execute"):
            action_data = req.payload.get("action_request") or req.payload
            if not isinstance(action_data, dict):
                raise ValueError("Invalid action payload: expected dictionary")
            action_data_clean = dict(action_data)
            if "session_id" not in action_data_clean:
                action_data_clean["session_id"] = req.session_id
            if "device_id" not in action_data_clean:
                action_data_clean["device_id"] = req.device_id
            if "action_id" not in action_data_clean:
                action_data_clean["action_id"] = req.request_id

            valid, err_code, action_req = validate_remote_action_request(action_data_clean)
            if not valid or not action_req:
                raise ValueError(f"Action validation failed: {err_code}")

            res = self.remote_action_session_manager.process_action_request(
                action_req, session.scopes, cached_target=req.payload.get("cached_target")
            )
            return res.to_dict()

        elif action == "action.remote_confirm":
            aid = str(req.payload.get("action_id", "")).strip()
            token = str(req.payload.get("confirmation_token", "")).strip()
            conf = bool(req.payload.get("confirmed", True))
            ok, msg, a_resp = self.remote_action_session_manager.confirm_action(
                req.session_id, aid, req.device_id, token, conf
            )
            if not ok or not a_resp:
                return {"status": "ERROR", "reason": msg, "error": msg}
            return a_resp.to_dict()

        elif action == "action.remote_cancel":
            reason = str(req.payload.get("reason", "Cancelled by client"))
            ok, msg = self.remote_action_session_manager.cancel_action(req.session_id, reason)
            return {"status": "CANCELLED" if ok else "ERROR", "reason": msg}

        elif action == "action.remote_status":
            sess = self.remote_action_session_manager.get_session(req.session_id)
            if not sess:
                return {"status": "NOT_FOUND"}
            return sess.to_dict()

        raise ValueError(f"Unhandled action: {action}")


class SecureDashboardServer:
    """
    HTTP Server wrapping SecureGateway and providing backwards-compatible /api/command
    and /api/status while strictly enforcing localhost-only socket binding.
    """

    def __init__(
        self,
        gateway: SecureGateway,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
    ):
        self._validate_bind_address(host)
        self.host = host
        self.port = port
        self.gateway = gateway
        self._httpd: Optional[socketserver.TCPServer] = None
        self._server_thread: Optional[threading.Thread] = None

    @staticmethod
    def _validate_bind_address(host: str) -> None:
        """
        Enforce localhost boundary invariant.
        Strictly reject 0.0.0.0 or any wildcard address.
        """
        cleaned = (host or "").strip().lower()
        if cleaned in PROHIBITED_HOSTS or cleaned not in ALLOWED_HOSTS:
            raise SecurityBindingError(
                f"Prohibited socket bind address: '{host}'. "
                f"NR-AI backend must ONLY bind to localhost (127.0.0.1)."
            )

    def start(self) -> bool:
        """Start background server on 127.0.0.1:port."""
        self._validate_bind_address(self.host)
        gateway_ref = self.gateway

        class GatewayHTTPHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                client_ip = self.client_address[0]
                parsed = urllib.parse.urlparse(self.path)

                # Legacy Android Companion status
                if parsed.path in ("/api/status", "/status"):
                    if gateway_ref.companion and hasattr(gateway_ref.companion, "dashboard"):
                        data = gateway_ref.companion.dashboard.get_status_snapshot()
                    elif gateway_ref.companion and hasattr(gateway_ref.companion, "get_status_snapshot"):
                        data = gateway_ref.companion.get_status_snapshot()
                    else:
                        data = {
                            "assistant_status": "🟢 Online",
                            "avatar_mode": "IDLE",
                            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                        }
                    payload = json.dumps(data, indent=2).encode("utf-8")
                    self._send_response(200, "application/json", payload)

                # Legacy Android Companion GET command
                elif parsed.path in ("/api/command", "/command"):
                    params = urllib.parse.parse_qs(parsed.query)
                    cmd = params.get("text", [""])[0] or params.get("command", [""])[0]
                    if gateway_ref.companion and hasattr(gateway_ref.companion, "interact"):
                        resp = gateway_ref.companion.interact(cmd, speak_output=False)
                        payload = json.dumps(resp.to_dict() if hasattr(resp, "to_dict") else {"text": str(resp)}).encode("utf-8")
                    else:
                        payload = json.dumps({"text": f"Simulated execution: {cmd}"}).encode("utf-8")
                    self._send_response(200, "application/json", payload)

                # Secure status endpoint
                elif parsed.path == "/api/v2/secure/emergency_status":
                    status = gateway_ref.emergency_stop.get_status()
                    payload = json.dumps(status.to_dict(), indent=2).encode("utf-8")
                    self._send_response(200, "application/json", payload)

                # Phase 2: Secure telemetry polling endpoint
                elif parsed.path == "/api/v2/secure/telemetry":
                    params = urllib.parse.parse_qs(parsed.query)
                    session_id = params.get("session_id", [""])[0]
                    device_id = params.get("device_id", [""])[0]
                    since_seq = int(params.get("since_sequence", [0])[0])
                    valid, msg, sess = gateway_ref.session_manager.validate_session(session_id, device_id)
                    if not valid or not sess or PhonePermissionScope.READ_TELEMETRY not in sess.scopes:
                        self._send_response(403, "application/json", json.dumps({"error": "TELEMETRY_ACCESS_DENIED"}).encode("utf-8"))
                        return
                    events = gateway_ref.telemetry_dispatcher.get_events(session_id, since_sequence=since_seq)
                    payload = json.dumps({
                        "events": [e.to_dict() for e in events],
                        "latest_sequence": gateway_ref.telemetry_dispatcher.get_latest_sequence(session_id),
                    }).encode("utf-8")
                    self._send_response(200, "application/json", payload)

                # Phase 2: Secure stream frame pull endpoint
                elif parsed.path == "/api/v2/secure/stream/frame":
                    params = urllib.parse.parse_qs(parsed.query)
                    stream_id = params.get("stream_id", [""])[0]
                    session_id = params.get("session_id", [""])[0]
                    device_id = params.get("device_id", [""])[0]
                    valid, msg, sess = gateway_ref.session_manager.validate_session(session_id, device_id)
                    if not valid or not sess or PhonePermissionScope.READ_SCREEN_STREAM not in sess.scopes:
                        self._send_response(403, "application/json", json.dumps({"error": "STREAM_ACCESS_DENIED"}).encode("utf-8"))
                        return
                    stream = gateway_ref.stream_manager.get_stream(stream_id)
                    if stream and stream.queue.get_stats()["queued_frames"] == 0:
                        gateway_ref.stream_manager.produce_frame(stream_id)
                    ok, msg, frame = gateway_ref.stream_manager.get_next_frame(stream_id, session_id)
                    if not ok or not frame:
                        self._send_response(204, "application/json", b"{}")
                        return
                    payload = frame.to_json().encode("utf-8")
                    self._send_response(200, "application/json", payload)

                # Phase 3: Secure voice session status endpoint
                elif parsed.path == "/api/v2/secure/voice/status":
                    params = urllib.parse.parse_qs(parsed.query)
                    session_id = params.get("session_id", [""])[0]
                    device_id = params.get("device_id", [""])[0]
                    valid, msg, sess = gateway_ref.session_manager.validate_session(session_id, device_id)
                    if not valid or not sess or (
                        PhonePermissionScope.READ_STATUS not in sess.scopes and
                        PhonePermissionScope.VOICE_COMMAND not in sess.scopes
                    ):
                        self._send_response(403, "application/json", json.dumps({"error": "VOICE_STATUS_ACCESS_DENIED"}).encode("utf-8"))
                        return
                    v_sess = gateway_ref.voice_session_manager.get_session(session_id)
                    if not v_sess:
                        self._send_response(404, "application/json", json.dumps({"error": "VOICE_SESSION_NOT_FOUND"}).encode("utf-8"))
                        return
                    self._send_response(200, "application/json", json.dumps(v_sess.to_dict()).encode("utf-8"))

                # Phase 4: Secure remote action status GET endpoint
                elif parsed.path == "/api/v2/secure/action/status":
                    params = urllib.parse.parse_qs(parsed.query)
                    session_id = params.get("session_id", [""])[0]
                    device_id = params.get("device_id", [""])[0]
                    valid, msg, sess = gateway_ref.session_manager.validate_session(session_id, device_id)
                    if not valid or not sess or (
                        PhonePermissionScope.READ_STATUS not in sess.scopes and
                        PhonePermissionScope.APPROVED_COMPUTER_ACTION not in sess.scopes
                    ):
                        self._send_response(403, "application/json", json.dumps({"error": "REMOTE_PERMISSION_DENIED"}).encode("utf-8"))
                        return
                    act_sess = gateway_ref.remote_action_session_manager.get_session(session_id)
                    if not act_sess:
                        self._send_response(404, "application/json", json.dumps({"error": "SESSION_NOT_FOUND"}).encode("utf-8"))
                        return
                    self._send_response(200, "application/json", json.dumps(act_sess.to_dict()).encode("utf-8"))

                else:
                    self._send_response(404, "text/plain", b"Not Found")

            def do_POST(self):
                client_ip = self.client_address[0]
                parsed = urllib.parse.urlparse(self.path)
                content_len = int(self.headers.get("Content-Length", 0))

                if content_len > MAX_REQUEST_BYTES:
                    resp = SecureResponse(
                        request_id="size-err",
                        status="DENIED",
                        code=413,
                        error=f"Request size exceeds limit of {MAX_REQUEST_BYTES} bytes",
                    )
                    self._send_response(413, "application/json", resp.to_json().encode("utf-8"))
                    return

                body_bytes = self.rfile.read(content_len) if content_len > 0 else b""
                body_str = body_bytes.decode("utf-8", errors="replace")

                # 1. Secure pairing route
                if parsed.path == "/api/v2/secure/pair":
                    try:
                        data = json.loads(body_str)
                    except Exception:
                        data = {}
                    resp = gateway_ref.handle_pairing_request(data, client_ip)
                    self._send_response(resp.code, "application/json", resp.to_json().encode("utf-8"))

                # 2. Secure auth route
                elif parsed.path == "/api/v2/secure/auth":
                    try:
                        data = json.loads(body_str)
                    except Exception:
                        data = {}
                    resp = gateway_ref.handle_auth_request(data, client_ip)
                    self._send_response(resp.code, "application/json", resp.to_json().encode("utf-8"))

                # 3. Secure command route
                elif parsed.path == "/api/v2/secure/command":
                    resp = gateway_ref.handle_secure_command(body_str, client_ip)
                    self._send_response(resp.code, "application/json", resp.to_json().encode("utf-8"))

                # 4. Secure emergency stop route
                elif parsed.path == "/api/v2/secure/emergency_stop":
                    status = gateway_ref.emergency_stop.trigger(
                        triggered_by=f"HTTP:{client_ip}",
                        reason="Emergency stop via direct HTTP post",
                    )
                    payload = json.dumps(status.to_dict()).encode("utf-8")
                    self._send_response(200, "application/json", payload)

                # Phase 2: Secure stream start route
                elif parsed.path == "/api/v2/secure/stream/start":
                    try:
                        data = json.loads(body_str)
                    except Exception:
                        data = {}
                    session_id = str(data.get("session_id", "")).strip()
                    device_id = str(data.get("device_id", "")).strip()
                    valid, msg, sess = gateway_ref.session_manager.validate_session(session_id, device_id)
                    if not valid or not sess or PhonePermissionScope.READ_SCREEN_STREAM not in sess.scopes:
                        self._send_response(403, "application/json", json.dumps({"error": "STREAM_PERMISSION_DENIED"}).encode("utf-8"))
                        return
                    fps = float(data.get("target_fps", DEFAULT_FPS))
                    w = int(data.get("max_width", MAX_FRAME_WIDTH))
                    h = int(data.get("max_height", MAX_FRAME_HEIGHT))
                    enc_str = str(data.get("encoding", "JPEG")).upper()
                    try:
                        enc = FrameEncoding(enc_str)
                    except ValueError:
                        enc = FrameEncoding.JPEG
                    ok, s_msg, stream = gateway_ref.stream_manager.create_stream(device_id, session_id, target_fps=fps, max_width=w, max_height=h, encoding=enc)
                    if not ok or not stream:
                        self._send_response(400, "application/json", json.dumps({"error": s_msg}).encode("utf-8"))
                        return
                    gateway_ref.audit_logger.log_event("STREAM_STARTED", "SUCCESS", device_id=device_id, session_id=session_id, client_ip=client_ip, metadata={"stream_id": stream.stream_id, "fps": fps, "res": f"{w}x{h}"})
                    self._send_response(200, "application/json", json.dumps(stream.to_dict()).encode("utf-8"))

                # Phase 2: Secure stream stop route
                elif parsed.path == "/api/v2/secure/stream/stop":
                    try:
                        data = json.loads(body_str)
                    except Exception:
                        data = {}
                    stream_id = str(data.get("stream_id", "")).strip()
                    session_id = str(data.get("session_id", "")).strip()
                    ok, msg = gateway_ref.stream_manager.stop_stream(stream_id, session_id)
                    gateway_ref.audit_logger.log_event("STREAM_STOPPED", "SUCCESS" if ok else "DENIED", session_id=session_id, client_ip=client_ip, metadata={"stream_id": stream_id})
                    code = 200 if ok else 400
                    self._send_response(code, "application/json", json.dumps({"status": "STOPPED" if ok else "ERROR", "message": msg}).encode("utf-8"))

                # Phase 2: Secure stream pause route
                elif parsed.path == "/api/v2/secure/stream/pause":
                    try:
                        data = json.loads(body_str)
                    except Exception:
                        data = {}
                    stream_id = str(data.get("stream_id", "")).strip()
                    session_id = str(data.get("session_id", "")).strip()
                    ok, msg = gateway_ref.stream_manager.pause_stream(stream_id, session_id)
                    code = 200 if ok else 400
                    self._send_response(code, "application/json", json.dumps({"status": "PAUSED" if ok else "ERROR", "message": msg}).encode("utf-8"))

                # Phase 2: Secure stream resume route
                elif parsed.path == "/api/v2/secure/stream/resume":
                    try:
                        data = json.loads(body_str)
                    except Exception:
                        data = {}
                    stream_id = str(data.get("stream_id", "")).strip()
                    session_id = str(data.get("session_id", "")).strip()
                    ok, msg = gateway_ref.stream_manager.resume_stream(stream_id, session_id)
                    code = 200 if ok else 400
                    self._send_response(code, "application/json", json.dumps({"status": "ACTIVE" if ok else "ERROR", "message": msg}).encode("utf-8"))

                # Phase 3: Secure voice audio upload endpoint
                elif parsed.path == "/api/v2/secure/voice/upload":
                    try:
                        data = json.loads(body_str)
                    except Exception:
                        data = {}
                    session_id = str(data.get("session_id", "")).strip()
                    device_id = str(data.get("device_id", "")).strip()
                    valid, msg, sess = gateway_ref.session_manager.validate_session(session_id, device_id)
                    if not valid or not sess or PhonePermissionScope.VOICE_COMMAND not in sess.scopes:
                        self._send_response(403, "application/json", json.dumps({"error": "VOICE_PERMISSION_DENIED"}).encode("utf-8"))
                        return

                    # Voice rate limit check
                    allowed, r_reason, retry_after = gateway_ref.voice_rate_limiter.allow_request(f"voice:{device_id}")
                    if not allowed:
                        gateway_ref.audit_logger.log_event("VOICE_RATE_LIMIT", "BLOCKED", device_id=device_id, session_id=session_id, client_ip=client_ip, reason=r_reason)
                        self._send_response(429, "application/json", json.dumps({"error": "AUDIO_RATE_LIMITED", "reason": r_reason}).encode("utf-8"))
                        return

                    # Extract audio payload
                    import hashlib
                    payload_hex = str(data.get("payload_hex", ""))
                    try:
                        raw_audio = bytes.fromhex(payload_hex) if payload_hex else data.get("audio_bytes", b"")
                        if isinstance(raw_audio, str):
                            raw_audio = raw_audio.encode("utf-8")
                    except Exception:
                        raw_audio = b""

                    audio_req = AudioRequest(
                        request_id=str(data.get("request_id", f"AUD-{secrets.token_hex(6)}")),
                        session_id=session_id,
                        device_id=device_id,
                        timestamp=float(data.get("timestamp", time.time())),
                        nonce=str(data.get("nonce", secrets.token_hex(8))),
                        audio_format=str(data.get("audio_format", "WAV")).upper(),
                        sample_rate=int(data.get("sample_rate", 16000)),
                        channels=int(data.get("channels", 1)),
                        duration_ms=float(data.get("duration_ms", 1000.0)),
                        payload_size=len(raw_audio),
                        checksum=data.get("checksum") or hashlib.sha256(raw_audio).hexdigest(),
                        audio_bytes=raw_audio,
                        metadata=data.get("metadata", {}),
                    )

                    # Process voice pipeline
                    v_resp = gateway_ref.voice_session_manager.process_voice_request(audio_req, sess.scopes)

                    # Audit logging (strictly metadata only, zero raw audio)
                    gateway_ref.audit_logger.log_event(
                        "VOICE_COMMAND_PROCESSED",
                        "SUCCESS" if v_resp.status in ("SUCCESS", "CONFIRMATION_REQUIRED") else "DENIED",
                        device_id=device_id,
                        session_id=session_id,
                        client_ip=client_ip,
                        metadata=audio_req.to_metadata_dict(),
                    )
                    code = 200 if v_resp.status in ("SUCCESS", "CONFIRMATION_REQUIRED") else (403 if v_resp.status == "DENIED" else 400)
                    self._send_response(code, "application/json", v_resp.to_json().encode("utf-8"))

                # Phase 3: Secure voice confirmation endpoint
                elif parsed.path == "/api/v2/secure/voice/confirm":
                    try:
                        data = json.loads(body_str)
                    except Exception:
                        data = {}
                    session_id = str(data.get("session_id", "")).strip()
                    device_id = str(data.get("device_id", "")).strip()
                    cid = str(data.get("confirmation_id", "")).strip()
                    conf = bool(data.get("confirmed", True))
                    valid, msg, sess = gateway_ref.session_manager.validate_session(session_id, device_id)
                    if not valid or not sess or PhonePermissionScope.VOICE_COMMAND not in sess.scopes:
                        self._send_response(403, "application/json", json.dumps({"error": "VOICE_PERMISSION_DENIED"}).encode("utf-8"))
                        return
                    ok, msg, v_resp = gateway_ref.voice_session_manager.confirm_action(session_id, cid, conf)
                    code = 200 if ok else 400
                    resp_dict = v_resp.to_dict() if v_resp else {"error": msg}
                    self._send_response(code, "application/json", json.dumps(resp_dict).encode("utf-8"))

                # Phase 3: Secure voice cancel endpoint
                elif parsed.path == "/api/v2/secure/voice/cancel":
                    try:
                        data = json.loads(body_str)
                    except Exception:
                        data = {}
                    session_id = str(data.get("session_id", "")).strip()
                    device_id = str(data.get("device_id", "")).strip()
                    valid, msg, sess = gateway_ref.session_manager.validate_session(session_id, device_id)
                    if not valid or not sess or PhonePermissionScope.VOICE_COMMAND not in sess.scopes:
                        self._send_response(403, "application/json", json.dumps({"error": "VOICE_PERMISSION_DENIED"}).encode("utf-8"))
                        return
                    ok, msg = gateway_ref.voice_session_manager.cancel_session(session_id)
                    code = 200 if ok else 400
                    self._send_response(code, "application/json", json.dumps({"status": "CANCELLED" if ok else "ERROR", "message": msg}).encode("utf-8"))

                # Phase 4: Secure remote action execute endpoint
                elif parsed.path == "/api/v2/secure/action/execute":
                    try:
                        data = json.loads(body_str)
                    except Exception:
                        data = {}
                    session_id = str(data.get("session_id", "")).strip()
                    device_id = str(data.get("device_id", "")).strip()
                    valid, msg, sess = gateway_ref.session_manager.validate_session(session_id, device_id)
                    if not valid or not sess or PhonePermissionScope.APPROVED_COMPUTER_ACTION not in sess.scopes:
                        self._send_response(403, "application/json", json.dumps({"error": "REMOTE_PERMISSION_DENIED", "status": "DENIED"}).encode("utf-8"))
                        return

                    is_valid, err_code, action_req = validate_remote_action_request(data)
                    if not is_valid or not action_req:
                        self._send_response(400, "application/json", json.dumps({"error": err_code, "status": "DENIED"}).encode("utf-8"))
                        return

                    res = gateway_ref.remote_action_session_manager.process_action_request(
                        action_req, sess.scopes, cached_target=data.get("cached_target")
                    )
                    code = 200 if res.status in ("SUCCESS", "AWAITING_CONFIRMATION") else (403 if res.status == "DENIED" or res.error == "REMOTE_STOPPED" else 400)
                    self._send_response(code, "application/json", res.to_json().encode("utf-8"))

                # Phase 4: Secure remote action confirm endpoint
                elif parsed.path == "/api/v2/secure/action/confirm":
                    try:
                        data = json.loads(body_str)
                    except Exception:
                        data = {}
                    session_id = str(data.get("session_id", "")).strip()
                    device_id = str(data.get("device_id", "")).strip()
                    action_id = str(data.get("action_id", "")).strip()
                    token = str(data.get("confirmation_token", "")).strip()
                    conf = bool(data.get("confirmed", True))
                    valid, msg, sess = gateway_ref.session_manager.validate_session(session_id, device_id)
                    if not valid or not sess or PhonePermissionScope.APPROVED_COMPUTER_ACTION not in sess.scopes:
                        self._send_response(403, "application/json", json.dumps({"error": "REMOTE_PERMISSION_DENIED"}).encode("utf-8"))
                        return
                    ok, c_msg, a_res = gateway_ref.remote_action_session_manager.confirm_action(
                        session_id, action_id, device_id, token, conf
                    )
                    code = 200 if ok else (403 if a_res and a_res.error == "REMOTE_STOPPED" else 400)
                    resp_dict = a_res.to_dict() if a_res else {"error": c_msg}
                    self._send_response(code, "application/json", json.dumps(resp_dict).encode("utf-8"))

                # Phase 4: Secure remote action cancel endpoint
                elif parsed.path == "/api/v2/secure/action/cancel":
                    try:
                        data = json.loads(body_str)
                    except Exception:
                        data = {}
                    session_id = str(data.get("session_id", "")).strip()
                    device_id = str(data.get("device_id", "")).strip()
                    reason = str(data.get("reason", "Cancelled by client"))
                    valid, msg, sess = gateway_ref.session_manager.validate_session(session_id, device_id)
                    if not valid or not sess:
                        self._send_response(403, "application/json", json.dumps({"error": "REMOTE_AUTH_REQUIRED"}).encode("utf-8"))
                        return
                    ok, c_msg = gateway_ref.remote_action_session_manager.cancel_action(session_id, reason)
                    code = 200 if ok else 400
                    self._send_response(code, "application/json", json.dumps({"status": "CANCELLED" if ok else "ERROR", "message": c_msg}).encode("utf-8"))

                # 5. Legacy Android Companion /api/command POST
                elif parsed.path in ("/api/command", "/command"):
                    try:
                        data = json.loads(body_str)
                        cmd = data.get("command") or data.get("text") or ""
                    except Exception:
                        cmd = body_str.strip()

                    # Check emergency stop
                    if gateway_ref.emergency_stop.is_active():
                        err_payload = json.dumps({
                            "text": "Execution blocked: EMERGENCY STOP is active.",
                            "status": "EMERGENCY_STOPPED",
                        }).encode("utf-8")
                        self._send_response(403, "application/json", err_payload)
                        return

                    if gateway_ref.companion and hasattr(gateway_ref.companion, "interact"):
                        resp = gateway_ref.companion.interact(cmd, speak_output=False)
                        payload = json.dumps(resp.to_dict() if hasattr(resp, "to_dict") else {"text": str(resp)}).encode("utf-8")
                    else:
                        payload = json.dumps({"text": f"Simulated execution: {cmd}", "category": "GENERAL"}).encode("utf-8")
                    self._send_response(200, "application/json", payload)

                else:
                    self._send_response(404, "text/plain", b"Not Found")

            def _send_response(self, code: int, content_type: str, body: bytes):
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                pass  # suppress noisy console logs

        try:
            class ReusableTCPServer(socketserver.TCPServer):
                allow_reuse_address = True

            self._httpd = ReusableTCPServer((self.host, self.port), GatewayHTTPHandler)
            self._server_thread = threading.Thread(
                target=self._httpd.serve_forever,
                name="NRAI-SecureServer",
                daemon=True,
            )
            self._server_thread.start()
            logger.info(f"NR-AI Secure Server running on {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"Failed to start secure server on {self.host}:{self.port}: {e}")
            return False

    def stop(self) -> None:
        if self._httpd:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception:
                pass
            self._httpd = None
