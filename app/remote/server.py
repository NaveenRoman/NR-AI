"""
NR-AI Secure Transport Server & API Gateway.
Step 10 Phase 1 — Secure Phone <-> PC Communication Foundation.

Binds exclusively to 127.0.0.1:8585 to maintain strict localhost security.
Rejects any attempt to bind to 0.0.0.0 or external network interfaces.
"""

from dataclasses import asdict
import hashlib
import http.server
import json
import logging
import os
import secrets
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
from app.remote.companion_client_contract import (
    CompanionNotification,
    CompanionUIState,
    ConfirmationDialogPayload,
    TTSResponseContract,
    sanitize_tts_response,
)
from app.remote.companion_orchestrator import (
    CompanionCommandResult,
    CompanionOrchestrator,
    CompanionOrchestratorState,
)
from app.remote.companion_resilience import CompanionResilienceManager

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
        companion_orchestrator: Optional[CompanionOrchestrator] = None,
        companion_resilience: Optional[CompanionResilienceManager] = None,
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
        self.companion_resilience = companion_resilience or CompanionResilienceManager()
        self.companion_orchestrator = companion_orchestrator or CompanionOrchestrator(
            session_manager=self.session_manager,
            stream_session_manager=self.stream_manager,
            voice_session_manager=self.voice_session_manager,
            remote_action_session_manager=self.remote_action_session_manager,
            telemetry_hub=self.telemetry_dispatcher,
            emergency_controller=self.emergency_stop,
            resilience_manager=self.companion_resilience,
            audit_logger=self.audit_logger,
            computer_agent=comp_agent,
        )
        from app.ui.galaxy_engine import GalaxyEngine
        self.galaxy_engine = GalaxyEngine(emergency_stop=self.emergency_stop)

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

        # Transition companion orchestrator to IDLE_CONNECTED
        if hasattr(self, "companion_orchestrator") and self.companion_orchestrator:
            try:
                curr_st = self.companion_orchestrator.get_state(device_id)
                if curr_st in (CompanionOrchestratorState.UNPAIRED, CompanionOrchestratorState.PAIRING):
                    self.companion_orchestrator.transition_state(
                        device_id,
                        CompanionOrchestratorState.AUTHENTICATED,
                        "Authenticated session created",
                    )
                    self.companion_orchestrator.transition_state(
                        device_id,
                        CompanionOrchestratorState.IDLE_CONNECTED,
                        "Companion ready and connected",
                    )
            except Exception as e:
                logger.warning(f"Failed to transition orchestrator state on auth: {e}")

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

        elif action == "companion.command":
            res = self.companion_orchestrator.process_command(
                session_id=req.session_id,
                device_id=req.device_id,
                command_text=req.payload.get("command_text") or req.payload.get("command"),
                action_payload=req.payload.get("action_payload"),
                cached_target=req.payload.get("cached_target"),
                current_time=req.timestamp,
            )
            return res.to_dict()

        elif action == "companion.state":
            st = self.companion_orchestrator.get_state(req.device_id)
            return {
                "device_id": req.device_id,
                "state": st.value,
                "emergency_stop": self.emergency_stop.is_active(),
            }

        elif action == "companion.emergency_stop":
            reason = str(req.payload.get("reason", "Companion emergency stop triggered"))
            return self.companion_orchestrator.trigger_emergency_stop(req.device_id, reason=reason)

        elif action == "companion.confirm":
            aid = str(req.payload.get("action_id", "")).strip()
            token = str(req.payload.get("confirmation_token", "")).strip()
            conf = bool(req.payload.get("confirmed", True))
            res = self.companion_orchestrator.confirm_action(
                session_id=req.session_id,
                device_id=req.device_id,
                action_id=aid,
                confirmation_token=token,
                confirmed=conf,
                current_time=req.timestamp,
            )
            return res.to_dict()

        raise ValueError(f"Unhandled action: {action}")


class SecureDashboardServer:
    """
    HTTP Server wrapping SecureGateway and providing backwards-compatible /api/command
    and /api/status while strictly enforcing localhost-only socket binding.
    """

    def __init__(
        self,
        gateway: Optional[SecureGateway] = None,
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

                # Phase 5: Secure companion state GET endpoint
                elif parsed.path == "/api/v2/secure/companion/state":
                    params = urllib.parse.parse_qs(parsed.query)
                    session_id = params.get("session_id", [""])[0]
                    device_id = params.get("device_id", [""])[0]
                    valid, msg, sess = gateway_ref.session_manager.validate_session(session_id, device_id)
                    if not valid or not sess:
                        self._send_response(403, "application/json", json.dumps({"error": "REMOTE_AUTH_REQUIRED"}).encode("utf-8"))
                        return
                    st = gateway_ref.companion_orchestrator.get_state(device_id)
                    if st in (CompanionOrchestratorState.UNPAIRED, CompanionOrchestratorState.PAIRING):
                        try:
                            gateway_ref.companion_orchestrator.transition_state(
                                device_id,
                                CompanionOrchestratorState.AUTHENTICATED,
                                "Valid session verified",
                            )
                            gateway_ref.companion_orchestrator.transition_state(
                                device_id,
                                CompanionOrchestratorState.IDLE_CONNECTED,
                                "Companion ready",
                            )
                            st = gateway_ref.companion_orchestrator.get_state(device_id)
                        except Exception:
                            pass
                    data = {
                        "device_id": device_id,
                        "session_id": session_id,
                        "state": st.value,
                        "emergency_stop": gateway_ref.emergency_stop.is_active(),
                        "timestamp": time.time(),
                    }
                    self._send_response(200, "application/json", json.dumps(data).encode("utf-8"))

                # Phase 6: Galaxy UI & Celestial State Endpoints
                elif parsed.path in ("/api/galaxy/state", "/api/galaxy/state/"):
                    snapshot = gateway_ref.companion.dashboard.get_status_snapshot() if (gateway_ref.companion and hasattr(gateway_ref.companion, "dashboard")) else {}
                    state = gateway_ref.galaxy_engine.get_galaxy_state(companion_snapshot=snapshot)
                    self._send_response(200, "application/json", json.dumps(state, indent=2).encode("utf-8"))

                elif parsed.path in ("/api/trinity/telemetry", "/api/trinity/telemetry/"):
                    telemetry = {}
                    if gateway_ref.companion and hasattr(gateway_ref.companion, "knowledge_engine"):
                        telemetry = gateway_ref.companion.knowledge_engine.get_telemetry_snapshot()
                    payload = json.dumps({"success": True, "telemetry": telemetry}, indent=2).encode("utf-8")
                    self._send_response(200, "application/json", payload)

                # Droid Phase 2: Live Android Lifecycle & Telemetry APIs
                elif parsed.path in ("/api/droid/status", "/api/droid/status/"):
                    u_agent = getattr(gateway_ref.companion, "unified_android_agent", None) if gateway_ref.companion else None
                    if u_agent:
                        rep = u_agent.get_device_lifecycle_status("Pixel_6_API_34")
                        res = {"success": True, "status": rep.to_dict()}
                    else:
                        res = {"success": False, "error": "Unified Android Agent not available"}
                    self._send_response(200, "application/json", json.dumps(res, indent=2).encode("utf-8"))

                elif parsed.path in ("/api/droid/devices", "/api/droid/devices/"):
                    u_agent = getattr(gateway_ref.companion, "unified_android_agent", None) if gateway_ref.companion else None
                    if u_agent:
                        disc = u_agent.lifecycle_controller.discover_devices()
                        res = {"success": True, "discovery": disc}
                    else:
                        res = {"success": False, "error": "Unified Android Agent not available"}
                    self._send_response(200, "application/json", json.dumps(res, indent=2).encode("utf-8"))


                elif parsed.path in ("/api/droid/evidence", "/api/droid/evidence/"):
                    u_agent = getattr(gateway_ref.companion, "unified_android_agent", None) if gateway_ref.companion else None
                    if u_agent:
                        task = u_agent.task_state_store.get_active_task()
                        task_id = task.task_id if task else "T-DEFAULT"
                        records = [r.to_dict() for r in u_agent.evidence_collector.get_records_by_task(task_id)]
                        res = {"success": True, "task_id": task_id, "evidence": records}
                    else:
                        res = {"success": False, "error": "Unified Android Agent not available"}
                    self._send_response(200, "application/json", json.dumps(res, indent=2).encode("utf-8"))
                elif parsed.path in ("/api/droid/screenshots", "/api/droid/screenshots/"):
                    u_agent = getattr(gateway_ref.companion, "unified_android_agent", None) if gateway_ref.companion else None
                    if u_agent:
                        items = u_agent.screenshot_manager.list_screenshots()
                        res = {"success": True, "screenshots": items}
                    else:
                        res = {"success": False, "error": "Unified Android Agent not available"}
                    self._send_response(200, "application/json", json.dumps(res, indent=2).encode("utf-8"))
                elif parsed.path in ("/api/droid/knowledge-graph", "/api/droid/knowledge-graph/"):
                    u_agent = getattr(gateway_ref.companion, "unified_android_agent", None) if gateway_ref.companion else None
                    if u_agent:
                        kg = u_agent.build_gradle_knowledge_graph()
                        res = {"success": True, "knowledge_graph": kg.to_dict()}
                    else:
                        res = {"success": False, "error": "Unified Android Agent not available"}
                    self._send_response(200, "application/json", json.dumps(res, indent=2).encode("utf-8"))
                elif parsed.path in ("/api/droid/performance", "/api/droid/performance/"):
                    u_agent = getattr(gateway_ref.companion, "unified_android_agent", None) if gateway_ref.companion else None
                    if u_agent:
                        perf = u_agent.measure_startup_performance()
                        res = {"success": True, "performance": perf}
                    else:
                        res = {"success": False, "error": "Unified Android Agent not available"}
                    self._send_response(200, "application/json", json.dumps(res, indent=2).encode("utf-8"))

                # Droid Phase 5: Android Studio Specialist & Readiness APIs
                elif parsed.path in ("/api/droid/workspace", "/api/droid/workspace/"):
                    u_agent = getattr(gateway_ref.companion, "unified_android_agent", None) if gateway_ref.companion else None
                    if u_agent:
                        snap = u_agent.inspect_studio_workspace()
                        res = {"success": True, "workspace": snap.to_dict()}
                    else:
                        res = {"success": False, "error": "Unified Android Agent not available"}
                    self._send_response(200, "application/json", json.dumps(res, indent=2).encode("utf-8"))

                elif parsed.path in ("/api/droid/manifest-audit", "/api/droid/manifest-audit/"):
                    u_agent = getattr(gateway_ref.companion, "unified_android_agent", None) if gateway_ref.companion else None
                    if u_agent:
                        issues = u_agent.audit_manifest_merge()
                        res = {"success": True, "issues": [i.to_dict() for i in issues]}
                    else:
                        res = {"success": False, "error": "Unified Android Agent not available"}
                    self._send_response(200, "application/json", json.dumps(res, indent=2).encode("utf-8"))

                elif parsed.path in ("/api/droid/accessibility", "/api/droid/accessibility/"):
                    u_agent = getattr(gateway_ref.companion, "unified_android_agent", None) if gateway_ref.companion else None
                    if u_agent:
                        issues = u_agent.audit_accessibility()
                        res = {"success": True, "issues": [i.to_dict() for i in issues]}
                    else:
                        res = {"success": False, "error": "Unified Android Agent not available"}
                    self._send_response(200, "application/json", json.dumps(res, indent=2).encode("utf-8"))

                elif parsed.path in ("/api/droid/jank", "/api/droid/jank/"):
                    u_agent = getattr(gateway_ref.companion, "unified_android_agent", None) if gateway_ref.companion else None
                    if u_agent:
                        diag = u_agent.diagnose_jank()
                        res = {"success": True, "diagnostics": diag}
                    else:
                        res = {"success": False, "error": "Unified Android Agent not available"}
                    self._send_response(200, "application/json", json.dumps(res, indent=2).encode("utf-8"))

                elif parsed.path in ("/api/droid/projects", "/api/droid/projects/"):
                    u_agent = getattr(gateway_ref.companion, "unified_android_agent", None) if gateway_ref.companion else None
                    if u_agent:
                        matrix = u_agent.multi_project.generate_workspace_matrix()
                        res = {"success": True, "projects": [p.to_dict() for p in matrix]}
                    else:
                        res = {"success": False, "error": "Unified Android Agent not available"}
                    self._send_response(200, "application/json", json.dumps(res, indent=2).encode("utf-8"))

                elif parsed.path in ("/api/droid/readiness", "/api/droid/readiness/"):
                    u_agent = getattr(gateway_ref.companion, "unified_android_agent", None) if gateway_ref.companion else None
                    if u_agent:
                        scorecard = u_agent.audit_readiness()
                        res = {"success": True, "scorecard": scorecard.to_dict()}
                    else:
                        res = {"success": False, "error": "Unified Android Agent not available"}
                    self._send_response(200, "application/json", json.dumps(res, indent=2).encode("utf-8"))

                # SkyShield Security Dashboard API
                elif parsed.path in ("/api/skyshield/dashboard", "/api/skyshield/dashboard/", "/api/security/dashboard", "/api/security/dashboard/"):
                    coord = getattr(gateway_ref.companion, "security_coordinator", None) if gateway_ref.companion else None
                    if coord:
                        data = coord.get_dashboard_state()
                    else:
                        data = {"success": False, "error": "Security coordinator unavailable", "state": "STOPPED"}
                    payload = json.dumps(data, indent=2).encode("utf-8")
                    self._send_response(200, "application/json", payload)

                # SkyShield Enrolled Devices List
                elif parsed.path in ("/api/skyshield/devices", "/api/skyshield/devices/"):
                    coord = getattr(gateway_ref.companion, "security_coordinator", None) if gateway_ref.companion else None
                    devices = coord.list_devices() if coord else []
                    payload = json.dumps({"success": True, "devices": devices}, indent=2).encode("utf-8")
                    self._send_response(200, "application/json", payload)

                # SkyShield Enrolled Device Detail & Phase 3 Health Telemetry
                elif parsed.path.startswith("/api/skyshield/devices/"):
                    parts = parsed.path.strip("/").split("/")
                    device_id = parts[3] if len(parts) >= 4 else ""
                    sub_action = parts[4] if len(parts) >= 5 else ""
                    coord = getattr(gateway_ref.companion, "security_coordinator", None) if gateway_ref.companion else None

                    if sub_action == "health":
                        res = coord.get_device_health(device_id) if coord else {"success": False, "error": "Coordinator unavailable"}
                        self._send_response(200 if res.get("success") else 404, "application/json", json.dumps(res, indent=2).encode("utf-8"))
                    elif sub_action == "anomalies":
                        res = coord.get_device_anomalies(device_id) if coord else {"success": False, "error": "Coordinator unavailable"}
                        self._send_response(200 if res.get("success") else 404, "application/json", json.dumps(res, indent=2).encode("utf-8"))
                    elif sub_action == "events":
                        res = coord.get_device_events(device_id) if coord else {"success": False, "error": "Coordinator unavailable"}
                        self._send_response(200 if res.get("success") else 404, "application/json", json.dumps(res, indent=2).encode("utf-8"))
                    elif sub_action == "posture":
                        res = coord.get_device_posture(device_id) if coord else {"success": False, "error": "Coordinator unavailable"}
                        self._send_response(200 if res.get("success") else 404, "application/json", json.dumps(res, indent=2).encode("utf-8"))
                    else:
                        dev = coord.get_device(device_id) if coord and device_id else None
                        if dev:
                            payload = json.dumps({"success": True, "device": dev.to_dict()}, indent=2).encode("utf-8")
                            self._send_response(200, "application/json", payload)
                        else:
                            payload = json.dumps({"success": False, "error": f"Device '{device_id}' not found"}, indent=2).encode("utf-8")
                            self._send_response(404, "application/json", payload)

                # SkyShield Pairing Requests List
                elif parsed.path in ("/api/skyshield/pair/requests", "/api/skyshield/pair/requests/"):
                    coord = getattr(gateway_ref.companion, "security_coordinator", None) if gateway_ref.companion else None
                    reqs = coord.pairing_manager.list_pairing_requests() if (coord and hasattr(coord, "pairing_manager")) else []
                    payload = json.dumps({"success": True, "pairing_requests": reqs}, indent=2).encode("utf-8")
                    self._send_response(200, "application/json", payload)

                # SkyShield Specific Pairing Request Detail
                elif parsed.path.startswith("/api/skyshield/pair/"):
                    parts = parsed.path.strip("/").split("/")
                    pairing_id = parts[3] if len(parts) >= 4 else ""
                    coord = getattr(gateway_ref.companion, "security_coordinator", None) if gateway_ref.companion else None
                    req = coord.get_pairing_request(pairing_id) if coord and pairing_id else None
                    if req:
                        payload = json.dumps({"success": True, "pairing_request": req.to_dict()}, indent=2).encode("utf-8")
                        self._send_response(200, "application/json", payload)
                    else:
                        payload = json.dumps({"success": False, "error": f"Pairing request '{pairing_id}' not found"}, indent=2).encode("utf-8")
                        self._send_response(404, "application/json", payload)

                # SkyShield Phase 4 Security Overview
                elif parsed.path in ("/api/skyshield/overview", "/api/skyshield/overview/"):
                    coord = getattr(gateway_ref.companion, "security_coordinator", None)
                    data = coord.get_security_overview() if coord else {"error": "Coordinator unavailable"}
                    self._send_response(200 if coord else 500, 'application/json', json.dumps({"success": bool(coord), "overview": data}, indent=2).encode("utf-8"))

                # SkyShield Phase 4 Incidents List & Detail
                elif parsed.path in ("/api/skyshield/incidents", "/api/skyshield/incidents/"):
                    params = urllib.parse.parse_qs(parsed.query)
                    dev_id = params.get("device_id", [None])[0]
                    st = params.get("status", [None])[0]
                    coord = getattr(gateway_ref.companion, "security_coordinator", None)
                    incs = coord.list_incidents(device_id=dev_id, status=st) if coord else []
                    self._send_response(200, 'application/json', json.dumps({"success": True, "incidents": incs}, indent=2).encode("utf-8"))

                elif parsed.path.startswith("/api/skyshield/incidents/"):
                    parts = parsed.path.strip("/").split("/")
                    inc_id = parts[3] if len(parts) >= 4 else ""
                    sub_act = parts[4] if len(parts) >= 5 else ""
                    coord = getattr(gateway_ref.companion, "security_coordinator", None)
                    if sub_act == "report":
                        rep = coord.generate_incident_report(inc_id) if coord else {"success": False, "error": "Coordinator unavailable"}
                        self._send_response(200 if rep.get('success') else 404, 'application/json', json.dumps(rep, indent=2).encode("utf-8"))
                    else:
                        inc = coord.get_incident(inc_id) if coord else None
                        if inc:
                            self._send_response(200, 'application/json', json.dumps({"success": True, "incident": inc}, indent=2).encode("utf-8"))
                        else:
                            self._send_response(404, 'application/json', json.dumps({"success": False, "error": f"Incident '{inc_id}' not found"}, indent=2).encode("utf-8"))

                # SkyShield Phase 4 Threat Intelligence & CVEs
                elif parsed.path in ("/api/skyshield/threats", "/api/skyshield/threats/"):
                    coord = getattr(gateway_ref.companion, "security_coordinator", None)
                    threats = coord.list_threat_advisories() if coord else []
                    self._send_response(200, 'application/json', json.dumps({"success": True, "threats": threats}, indent=2).encode("utf-8"))

                elif parsed.path.startswith("/api/skyshield/threats/check/"):
                    parts = parsed.path.strip("/").split("/")
                    dev_id = parts[4] if len(parts) >= 5 else ""
                    coord = getattr(gateway_ref.companion, "security_coordinator", None)
                    res = coord.check_device_vulnerability(dev_id) if coord else {"success": False, "error": "Coordinator unavailable"}
                    self._send_response(200 if res.get('success') else 404, 'application/json', json.dumps(res, indent=2).encode("utf-8"))

                # SkyShield Phase 4 Security Alerts
                elif parsed.path in ("/api/skyshield/alerts", "/api/skyshield/alerts/"):
                    coord = getattr(gateway_ref.companion, "security_coordinator", None)
                    alerts = coord.list_alerts() if coord else []
                    self._send_response(200, 'application/json', json.dumps({"success": True, "alerts": alerts}, indent=2).encode("utf-8"))

                # SkyShield Phase 4 Gated Proposals
                elif parsed.path in ("/api/skyshield/proposals", "/api/skyshield/proposals/"):
                    coord = getattr(gateway_ref.companion, "security_coordinator", None)
                    props = [p.to_dict() for p in coord.response_engine.list_proposals()] if coord else []
                    self._send_response(200, 'application/json', json.dumps({"success": True, "proposals": props}, indent=2).encode("utf-8"))

                elif parsed.path == "/api/diagnostics/credentials":
                    from app.agent.credential_diagnostics import CredentialDiagnosticEngine
                    diag = CredentialDiagnosticEngine().diagnose_all()
                    self._send_response(200, "application/json", json.dumps(diag, indent=2).encode("utf-8"))

                elif parsed.path == "/api/conversation/active":
                    comp = gateway_ref.companion
                    payload = json.dumps({
                        "success": True,
                        "active_conversation_agent": getattr(comp, "active_conversation_agent", None),
                        "active_conversation_agent_name": getattr(comp, "active_conversation_agent_name", None),
                        "handoff_path": getattr(comp, "last_handoff_path", []),
                    }, indent=2).encode("utf-8")
                    self._send_response(200, "application/json", payload)

                elif parsed.path in ("/api/galaxy/introduction", "/api/galaxy/introduction/"):
                    snapshot = gateway_ref.companion.dashboard.get_status_snapshot() if (gateway_ref.companion and hasattr(gateway_ref.companion, "dashboard")) else {}
                    intro_data = gateway_ref.galaxy_engine.get_agent_introductions(companion_snapshot=snapshot)
                    self._send_response(200, "application/json", json.dumps(intro_data, indent=2).encode("utf-8"))

                elif parsed.path in ("/api/agents", "/api/agents/"):
                    snapshot = gateway_ref.companion.dashboard.get_status_snapshot() if (gateway_ref.companion and hasattr(gateway_ref.companion, "dashboard")) else {}
                    nodes = gateway_ref.galaxy_engine.build_celestial_nodes(snapshot)
                    payload = json.dumps({"success": True, "count": len(nodes), "agents": [n.to_dict() for n in nodes]}, indent=2).encode("utf-8")
                    self._send_response(200, "application/json", payload)

                elif parsed.path.startswith("/api/agent/") and (parsed.path.endswith("/context") or parsed.path.endswith("/context/")):
                    agent_id = parsed.path[len("/api/agent/"):].rstrip("/").replace("/context", "").strip("/")
                    snapshot = gateway_ref.companion.dashboard.get_status_snapshot() if (gateway_ref.companion and hasattr(gateway_ref.companion, "dashboard")) else {}
                    ctx = gateway_ref.galaxy_engine.get_agent_context(agent_id, companion_snapshot=snapshot)
                    if gateway_ref.companion and hasattr(gateway_ref.companion, "get_active_development_context"):
                        ctx["development_context"] = gateway_ref.companion.get_active_development_context(agent_id)
                    payload = json.dumps(ctx, indent=2).encode("utf-8")
                    self._send_response(200, "application/json", payload)

                elif parsed.path.startswith("/api/agent/") and (parsed.path.endswith("/chat") or parsed.path.endswith("/chat/")):
                    agent_id = parsed.path[len("/api/agent/"):].rstrip("/").replace("/chat", "").strip("/")
                    history = gateway_ref.companion.get_agent_chat_history(agent_id) if (gateway_ref.companion and hasattr(gateway_ref.companion, "get_agent_chat_history")) else []
                    payload = json.dumps({"success": True, "agent_id": agent_id, "count": len(history), "history": history}, indent=2).encode("utf-8")
                    self._send_response(200, "application/json", payload)

                elif parsed.path.startswith("/api/agent/"):
                    agent_id = parsed.path[len("/api/agent/"):].strip("/")
                    snapshot = gateway_ref.companion.dashboard.get_status_snapshot() if (gateway_ref.companion and hasattr(gateway_ref.companion, "dashboard")) else {}
                    nodes = gateway_ref.galaxy_engine.build_celestial_nodes(snapshot)
                    matching = next((n for n in nodes if n.agent_id == agent_id), None)
                    if matching:
                        payload = json.dumps({"success": True, "agent": matching.to_dict()}, indent=2).encode("utf-8")
                        self._send_response(200, "application/json", payload)
                    else:
                        payload = json.dumps({"success": False, "error": f"Agent '{agent_id}' not found"}).encode("utf-8")
                        self._send_response(404, "application/json", payload)

                elif parsed.path.startswith("/static/"):
                    static_dir = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(__file__)), "ui", "static"))
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
                        self._send_response(200, content_type, file_data)
                    else:
                        self._send_response(404, "application/json", json.dumps({"error": "STATIC_FILE_NOT_FOUND"}).encode("utf-8"))

                elif parsed.path in ("/", "/galaxy", "/index.html"):
                    template_path = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(__file__)), "ui", "templates", "galaxy.html"))
                    if os.path.isfile(template_path):
                        with open(template_path, "rb") as f:
                            html_content = f.read()
                        self._send_response(200, "text/html; charset=utf-8", html_content)
                    else:
                        self._send_response(200, "text/html; charset=utf-8", b"<h1>NR-AI Galaxy UI</h1>")

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

                # 4b. Secure emergency reset route (authenticated with operator confirmation)
                elif parsed.path == "/api/v2/secure/emergency_reset":
                    try:
                        data = json.loads(body_str)
                    except Exception:
                        data = {}
                    session_id = str(data.get("session_id", "")).strip()
                    device_id = str(data.get("device_id", "")).strip()
                    confirmed = bool(data.get("confirmed", False) or data.get("operator_confirmation", False))
                    valid, msg, sess = gateway_ref.session_manager.validate_session(session_id, device_id)
                    if not valid or not sess:
                        self._send_response(403, "application/json", json.dumps({"error": "REMOTE_AUTH_REQUIRED"}).encode("utf-8"))
                        return
                    if not confirmed:
                        self._send_response(400, "application/json", json.dumps({"error": "OPERATOR_CONFIRMATION_REQUIRED", "message": "Explicit operator confirmation is required to reset Emergency Stop."}).encode("utf-8"))
                        return

                    gateway_ref.emergency_stop.reset(reset_by=f"Operator:{device_id}")
                    if gateway_ref.companion_orchestrator:
                        gateway_ref.companion_orchestrator.reset_emergency_stop(device_id)
                    try:
                        from app.agent.android_safety import AndroidSafetyGate
                        AndroidSafetyGate.deactivate_emergency_stop()
                    except Exception:
                        pass

                    gateway_ref.audit_logger.log_event(
                        "EMERGENCY_STOP_RESET",
                        "SUCCESS",
                        device_id=device_id,
                        session_id=session_id,
                        client_ip=client_ip,
                        metadata={"operator_confirmed": True}
                    )
                    self._send_response(200, "application/json", json.dumps({
                        "status": "RESET",
                        "emergency_stop": gateway_ref.emergency_stop.is_active(),
                        "message": "Emergency Stop successfully reset by authorized operator."
                    }).encode("utf-8"))

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

                # Phase 5: Secure companion command endpoint
                elif parsed.path == "/api/v2/secure/companion/command":
                    try:
                        data = json.loads(body_str)
                    except Exception:
                        data = {}
                    session_id = str(data.get("session_id", "")).strip()
                    device_id = str(data.get("device_id", "")).strip()
                    valid, msg, sess = gateway_ref.session_manager.validate_session(session_id, device_id)
                    if not valid or not sess:
                        self._send_response(403, "application/json", json.dumps({"error": "REMOTE_AUTH_REQUIRED", "status": "DENIED"}).encode("utf-8"))
                        return
                    cmd_text = data.get("command_text") or data.get("command")
                    act_payload = data.get("action_payload")
                    cached_target = data.get("cached_target")
                    res = gateway_ref.companion_orchestrator.process_command(
                        session_id=session_id,
                        device_id=device_id,
                        command_text=cmd_text,
                        action_payload=act_payload,
                        cached_target=cached_target,
                    )
                    code = 200 if res.status in ("SUCCESS", "AWAITING_CONFIRMATION") else (403 if res.status in ("DENIED", "STOPPED") else 400)
                    self._send_response(code, "application/json", json.dumps(res.to_dict()).encode("utf-8"))

                # Phase 5: Secure companion emergency stop endpoint
                elif parsed.path == "/api/v2/secure/companion/emergency_stop":
                    try:
                        data = json.loads(body_str)
                    except Exception:
                        data = {}
                    session_id = str(data.get("session_id", "")).strip()
                    device_id = str(data.get("device_id", "")).strip()
                    reason = str(data.get("reason", "Operator Emergency Stop via companion endpoint"))
                    valid, msg, sess = gateway_ref.session_manager.validate_session(session_id, device_id)
                    if not valid or not sess:
                        self._send_response(403, "application/json", json.dumps({"error": "REMOTE_AUTH_REQUIRED"}).encode("utf-8"))
                        return
                    res_stop = gateway_ref.companion_orchestrator.trigger_emergency_stop(device_id, reason=reason)
                    self._send_response(200, "application/json", json.dumps(res_stop).encode("utf-8"))

                # Phase 5: Secure companion emergency reset endpoint
                elif parsed.path == "/api/v2/secure/companion/emergency_reset":
                    try:
                        data = json.loads(body_str)
                    except Exception:
                        data = {}
                    session_id = str(data.get("session_id", "")).strip()
                    device_id = str(data.get("device_id", "")).strip()
                    confirmed = bool(data.get("confirmed", False) or data.get("operator_confirmation", False))
                    valid, msg, sess = gateway_ref.session_manager.validate_session(session_id, device_id)
                    if not valid or not sess:
                        self._send_response(403, "application/json", json.dumps({"error": "REMOTE_AUTH_REQUIRED"}).encode("utf-8"))
                        return
                    if not confirmed:
                        self._send_response(400, "application/json", json.dumps({"error": "OPERATOR_CONFIRMATION_REQUIRED", "message": "Explicit operator confirmation is required to reset Emergency Stop."}).encode("utf-8"))
                        return

                    ok = gateway_ref.companion_orchestrator.reset_emergency_stop(device_id)
                    try:
                        from app.agent.android_safety import AndroidSafetyGate
                        AndroidSafetyGate.deactivate_emergency_stop()
                    except Exception:
                        pass

                    gateway_ref.audit_logger.log_event(
                        "COMPANION_EMERGENCY_STOP_RESET",
                        "SUCCESS" if ok else "FAILED",
                        device_id=device_id,
                        session_id=session_id,
                        client_ip=client_ip,
                        metadata={"operator_confirmed": True}
                    )
                    self._send_response(200, "application/json", json.dumps({
                        "status": "RESET" if ok else "ERROR",
                        "emergency_stop": gateway_ref.emergency_stop.is_active(),
                        "message": "Emergency Stop successfully reset by authorized operator."
                    }).encode("utf-8"))

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

                # Emergency Stop Trigger
                elif parsed.path in ("/api/emergency_stop", "/api/emergency_stop/"):
                    try:
                        body_data = json.loads(body_str) if body_str else {}
                    except Exception:
                        body_data = {}
                    by = body_data.get("triggered_by", "GalaxyUI_Operator")
                    status = gateway_ref.emergency_stop.trigger(triggered_by=by, reason=reason)
                    payload = json.dumps({
                        "success": status.is_active,
                        "message": f"EMERGENCY STOP TRIGGERED by {by}: {reason}",
                        "emergency_stop": status.is_active,
                        "triggered_at": status.triggered_at,
                    }, indent=2).encode("utf-8")
                    self._send_response(200, "application/json", payload)

                # SkyShield Security POST Endpoints
                elif parsed.path in ("/api/skyshield/scan", "/api/skyshield/scan/", "/api/security/scan", "/api/security/scan/"):
                    coord = getattr(gateway_ref.companion, "security_coordinator", None) if gateway_ref.companion else None
                    if not coord:
                        self._send_response(503, "application/json", json.dumps({"success": False, "error": "Security coordinator unavailable"}).encode("utf-8"))
                        return
                    res = coord.run_full_scan()
                    payload = json.dumps({"success": res.get("success", False), "result": res, "dashboard": coord.get_dashboard_state()}, indent=2).encode("utf-8")
                    self._send_response(200, "application/json", payload)

                elif parsed.path in ("/api/skyshield/emergency_stop", "/api/skyshield/emergency_stop/", "/api/security/emergency_stop", "/api/security/emergency_stop/"):
                    try:
                        b_data = json.loads(body_str) if body_str else {}
                    except Exception:
                        b_data = {}
                    reason = b_data.get("reason", "Operator Emergency Stop via SkyShield Command Center")
                    coord = getattr(gateway_ref.companion, "security_coordinator", None) if gateway_ref.companion else None
                    if coord:
                        res = coord.trigger_emergency_stop(reason=reason)
                        dash = coord.get_dashboard_state()
                    else:
                        estop = gateway_ref.emergency_stop
                        estop.trigger(triggered_by="SkyShield Operator", reason=reason)
                        res = {"success": True, "state": "STOPPED"}
                        dash = {"state": "STOPPED", "emergency_stop_active": True}
                    payload = json.dumps({"success": True, "emergency_stop": True, "result": res, "dashboard": dash}, indent=2).encode("utf-8")
                    self._send_response(200, "application/json", payload)

                elif parsed.path in ("/api/skyshield/reset", "/api/skyshield/reset/", "/api/security/reset", "/api/security/reset/"):
                    coord = getattr(gateway_ref.companion, "security_coordinator", None) if gateway_ref.companion else None
                    if not coord:
                        self._send_response(503, "application/json", json.dumps({"success": False, "error": "Security coordinator unavailable"}).encode("utf-8"))
                        return
                    ok = coord.reset_emergency_stop()
                    payload = json.dumps({"success": ok, "state": coord.current_state.value, "dashboard": coord.get_dashboard_state()}, indent=2).encode("utf-8")
                    self._send_response(200, "application/json", payload)

                # SkyShield Phase 2 Pairing & Device Management Endpoints
                elif parsed.path in ("/api/skyshield/pair/request", "/api/skyshield/pair/request/"):
                    coord = getattr(gateway_ref.companion, "security_coordinator", None) if gateway_ref.companion else None
                    if not coord:
                        self._send_response(503, "application/json", json.dumps({"success": False, "error": "Security coordinator unavailable"}).encode("utf-8"))
                        return
                    try:
                        b_data = json.loads(body_str) if body_str else {}
                    except Exception:
                        self._send_response(400, "application/json", json.dumps({"success": False, "error": "Invalid JSON payload"}).encode("utf-8"))
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
                    self._send_response(200 if ok else 400, "application/json", payload)

                elif parsed.path in ("/api/skyshield/pair/approve", "/api/skyshield/pair/approve/"):
                    coord = getattr(gateway_ref.companion, "security_coordinator", None) if gateway_ref.companion else None
                    if not coord:
                        self._send_response(503, "application/json", json.dumps({"success": False, "error": "Security coordinator unavailable"}).encode("utf-8"))
                        return
                    try:
                        b_data = json.loads(body_str) if body_str else {}
                    except Exception:
                        self._send_response(400, "application/json", json.dumps({"success": False, "error": "Invalid JSON payload"}).encode("utf-8"))
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
                    self._send_response(200 if ok else 400, "application/json", payload)

                elif parsed.path in ("/api/skyshield/pair/reject", "/api/skyshield/pair/reject/"):
                    coord = getattr(gateway_ref.companion, "security_coordinator", None) if gateway_ref.companion else None
                    if not coord:
                        self._send_response(503, "application/json", json.dumps({"success": False, "error": "Security coordinator unavailable"}).encode("utf-8"))
                        return
                    try:
                        b_data = json.loads(body_str) if body_str else {}
                    except Exception:
                        self._send_response(400, "application/json", json.dumps({"success": False, "error": "Invalid JSON payload"}).encode("utf-8"))
                        return
                    pairing_id = b_data.get("pairing_id", "")
                    reason = b_data.get("reason", "Device owner rejected pairing")
                    rejector = b_data.get("rejector_actor", "Device Owner")
                    ok, msg = coord.reject_pairing(pairing_id=pairing_id, reason=reason, rejector_actor=rejector)
                    payload = json.dumps({"success": ok, "message": msg}, indent=2).encode("utf-8")
                    self._send_response(200 if ok else 400, "application/json", payload)

                elif (parsed.path.startswith("/api/skyshield/devices/") and parsed.path.endswith("/suspend")) or parsed.path in ("/api/skyshield/devices/suspend", "/api/skyshield/devices/suspend/"):
                    coord = getattr(gateway_ref.companion, "security_coordinator", None) if gateway_ref.companion else None
                    if not coord:
                        self._send_response(503, "application/json", json.dumps({"success": False, "error": "Security coordinator unavailable"}).encode("utf-8"))
                        return
                    try:
                        b_data = json.loads(body_str) if body_str else {}
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
                    self._send_response(200 if ok else 400, "application/json", payload)

                elif (parsed.path.startswith("/api/skyshield/devices/") and parsed.path.endswith("/revoke")) or parsed.path in ("/api/skyshield/devices/revoke", "/api/skyshield/devices/revoke/"):
                    coord = getattr(gateway_ref.companion, "security_coordinator", None) if gateway_ref.companion else None
                    if not coord:
                        self._send_response(503, "application/json", json.dumps({"success": False, "error": "Security coordinator unavailable"}).encode("utf-8"))
                        return
                    try:
                        b_data = json.loads(body_str) if body_str else {}
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
                    self._send_response(200 if ok else 400, "application/json", payload)

                elif (parsed.path.startswith("/api/skyshield/devices/") and parsed.path.endswith("/reauthorize")) or parsed.path in ("/api/skyshield/devices/reauthorize", "/api/skyshield/devices/reauthorize/"):
                    coord = getattr(gateway_ref.companion, "security_coordinator", None) if gateway_ref.companion else None
                    if not coord:
                        self._send_response(503, "application/json", json.dumps({"success": False, "error": "Security coordinator unavailable"}).encode("utf-8"))
                        return
                    try:
                        b_data = json.loads(body_str) if body_str else {}
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
                    self._send_response(200 if ok else 400, "application/json", payload)

                # Phase 3 Device Health Baseline Reset
                elif parsed.path.startswith("/api/skyshield/devices/") and (parsed.path.endswith("/baseline/reset") or parsed.path.endswith("/baseline/reset/")):
                    coord = getattr(gateway_ref.companion, "security_coordinator", None) if gateway_ref.companion else None
                    if not coord:
                        self._send_response(503, "application/json", json.dumps({"success": False, "error": "Security coordinator unavailable"}).encode("utf-8"))
                        return
                    parts = parsed.path.strip("/").split("/")
                    device_id = parts[3] if len(parts) >= 4 else ""
                    ok, msg = coord.reset_device_baseline(device_id=device_id)
                    payload = json.dumps({"success": ok, "message": msg, "device_id": device_id}, indent=2).encode("utf-8")
                    self._send_response(200 if ok else 400, "application/json", payload)

                # Phase 3 Device Telemetry Analysis Trigger
                elif parsed.path.startswith("/api/skyshield/devices/") and (parsed.path.endswith("/analyze") or parsed.path.endswith("/analyze/")):
                    coord = getattr(gateway_ref.companion, "security_coordinator", None) if gateway_ref.companion else None
                    if not coord:
                        self._send_response(503, "application/json", json.dumps({"success": False, "error": "Security coordinator unavailable"}).encode("utf-8"))
                        return
                    parts = parsed.path.strip("/").split("/")
                    device_id = parts[3] if len(parts) >= 4 else ""
                    try:
                        b_data = json.loads(body_str) if body_str else {}
                    except Exception:
                        b_data = {}
                    mock_scen = b_data.get("mock_scenario")
                    snap_data = b_data.get("snapshot")
                    res = coord.analyze_device_telemetry(device_id=device_id, snapshot_data=snap_data, mock_scenario=mock_scen)
                    self._send_response(200 if res.get("success") else 400, "application/json", json.dumps(res, indent=2).encode("utf-8"))

                elif parsed.path in ("/api/skyshield/session/authenticate", "/api/skyshield/session/authenticate/"):
                    coord = getattr(gateway_ref.companion, "security_coordinator", None) if gateway_ref.companion else None
                    if not coord:
                        self._send_response(503, "application/json", json.dumps({"success": False, "error": "Security coordinator unavailable"}).encode("utf-8"))
                        return
                    try:
                        b_data = json.loads(body_str) if body_str else {}
                    except Exception:
                        self._send_response(400, "application/json", json.dumps({"success": False, "error": "Invalid JSON payload"}).encode("utf-8"))
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
                    self._send_response(200 if ok else 400, "application/json", payload)

                elif parsed.path in ("/api/skyshield/session/validate", "/api/skyshield/session/validate/"):
                    coord = getattr(gateway_ref.companion, "security_coordinator", None) if gateway_ref.companion else None
                    if not coord:
                        self._send_response(503, "application/json", json.dumps({"success": False, "error": "Security coordinator unavailable"}).encode("utf-8"))
                        return
                    try:
                        b_data = json.loads(body_str) if body_str else {}
                    except Exception:
                        self._send_response(400, "application/json", json.dumps({"success": False, "error": "Invalid JSON payload"}).encode("utf-8"))
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
                    self._send_response(200 if ok else 400, "application/json", payload)

                # SkyShield Phase 4 Gated Safe Response Action Proposal
                elif parsed.path in ("/api/skyshield/response/propose", "/api/skyshield/response/propose/"):
                    coord = getattr(gateway_ref.companion, "security_coordinator", None)
                    try:
                        b_data = json.loads(body_str) if body_str else {}
                    except Exception:
                        b_data = {}
                    if not coord:
                        self._send_response(500, 'application/json', json.dumps({"success": False, "error": "Coordinator unavailable"}).encode("utf-8"))
                    else:
                        try:
                            prop = coord.propose_response_action(
                                action=b_data.get("action", ""),
                                device_id=b_data.get("device_id", ""),
                                incident_id=b_data.get("incident_id"),
                                reason=b_data.get("reason", "Operator proposal"),
                                initiated_by=b_data.get("initiated_by", "Operator"),
                            )
                            self._send_response(200, 'application/json', json.dumps({"success": True, "proposal": prop}, indent=2).encode("utf-8"))
                        except Exception as ex:
                            self._send_response(400, 'application/json', json.dumps({"success": False, "error": str(ex)}).encode("utf-8"))

                # SkyShield Phase 4 Gated Safe Response Action Confirmation
                elif parsed.path in ("/api/skyshield/response/confirm", "/api/skyshield/response/confirm/"):
                    coord = getattr(gateway_ref.companion, "security_coordinator", None)
                    try:
                        b_data = json.loads(body_str) if body_str else {}
                    except Exception:
                        b_data = {}
                    if not coord:
                        self._send_response(500, 'application/json', json.dumps({"success": False, "error": "Coordinator unavailable"}).encode("utf-8"))
                    else:
                        p_id = b_data.get("proposal_id", "")
                        conf = bool(b_data.get("operator_confirmed", True))
                        ok, msg, p = coord.confirm_response_action(p_id, operator_confirmed=conf)
                        status_code = 200 if ok else 403
                        self._send_response(status_code, 'application/json', json.dumps({"success": ok, "message": msg, "proposal": p}, indent=2).encode("utf-8"))

                # SkyShield Phase 4 Incident Status & False Positive Updates
                elif parsed.path.startswith("/api/skyshield/incidents/"):
                    parts = parsed.path.strip("/").split("/")
                    inc_id = parts[3] if len(parts) >= 4 else ""
                    sub_act = parts[4] if len(parts) >= 5 else ""
                    coord = getattr(gateway_ref.companion, "security_coordinator", None)
                    try:
                        b_data = json.loads(body_str) if body_str else {}
                    except Exception:
                        b_data = {}

                    if not coord:
                        self._send_response(500, 'application/json', json.dumps({"success": False, "error": "Coordinator unavailable"}).encode("utf-8"))
                    elif sub_act == "false_positive":
                        reason = b_data.get("reason", "Operator determination")
                        ok, msg = coord.mark_false_positive(inc_id, reason=reason)
                        self._send_response(200 if ok else 400, 'application/json', json.dumps({"success": ok, "message": msg}, indent=2).encode("utf-8"))
                    elif sub_act == "resolve":
                        res_text = b_data.get("resolution", "Resolved by operator")
                        ok, msg = coord.resolve_incident(inc_id, resolution=res_text)
                        self._send_response(200 if ok else 400, 'application/json', json.dumps({"success": ok, "message": msg}, indent=2).encode("utf-8"))
                    elif sub_act == "status":
                        st = b_data.get("status", "")
                        reason = b_data.get("reason")
                        ok, msg = coord.update_incident_status(inc_id, new_status=st, reason=reason)
                        self._send_response(200 if ok else 400, 'application/json', json.dumps({"success": ok, "message": msg}, indent=2).encode("utf-8"))
                    elif sub_act == "analyze":
                        sec_agent = getattr(gateway_ref.companion, "security_agent", None)
                        if sec_agent and hasattr(sec_agent, "analyze_incident"):
                            res = sec_agent.analyze_incident(inc_id)
                        else:
                            inc = coord.get_incident(inc_id)
                            res = {"success": bool(inc), "analysis": inc.get("ai_analysis") if inc else None}
                        self._send_response(200 if res.get('success') else 404, 'application/json', json.dumps(res, indent=2).encode("utf-8"))
                    else:
                        self._send_response(404, 'application/json', json.dumps({"success": False, "error": f"Unknown action '{sub_act}'"}).encode("utf-8"))

                # Droid Phase 2: Live Android Execution & Verification APIs
                elif parsed.path in ("/api/droid/boot", "/api/droid/boot/"):
                    try:
                        b_data = json.loads(body_str) if body_str else {}
                    except Exception:
                        b_data = {}
                    u_agent = getattr(gateway_ref.companion, "unified_android_agent", None) if gateway_ref.companion else None
                    if u_agent:
                        avd = b_data.get("avd_name", "Pixel_6_API_34")
                        timeout_s = float(b_data.get("timeout_seconds", 15.0))
                        rep = u_agent.boot_device(avd_name=avd, timeout_seconds=timeout_s)
                        res = {"success": rep.boot_completed or rep.state.value in ("READY", "BOOTED", "AVAILABLE"), "report": rep.to_dict()}
                    else:
                        res = {"success": False, "error": "Unified Android Agent not available"}
                    self._send_response(200, "application/json", json.dumps(res, indent=2).encode("utf-8"))

                elif parsed.path in ("/api/droid/stop", "/api/droid/stop/"):
                    try:
                        b_data = json.loads(body_str) if body_str else {}
                    except Exception:
                        b_data = {}
                    u_agent = getattr(gateway_ref.companion, "unified_android_agent", None) if gateway_ref.companion else None
                    if u_agent:
                        serial = b_data.get("serial")
                        rep = u_agent.stop_device(serial=serial)
                        res = {"success": True, "report": rep.to_dict()}
                    else:
                        res = {"success": False, "error": "Unified Android Agent not available"}
                    self._send_response(200, "application/json", json.dumps(res, indent=2).encode("utf-8"))

                elif parsed.path in ("/api/droid/deploy", "/api/droid/deploy/"):
                    try:
                        b_data = json.loads(body_str) if body_str else {}
                    except Exception:
                        b_data = {}
                    u_agent = getattr(gateway_ref.companion, "unified_android_agent", None) if gateway_ref.companion else None
                    if u_agent:
                        apk_p = b_data.get("apk_path")
                        pkg = b_data.get("package_name", "com.nrai.test")
                        act = b_data.get("activity_name", "MainActivity")
                        serial = b_data.get("serial")
                        d_res = u_agent.deploy_and_launch(apk_path=apk_p, package_name=pkg, activity_name=act, serial=serial)
                        res = {"success": d_res.success, "result": d_res.to_dict()}
                    else:
                        res = {"success": False, "error": "Unified Android Agent not available"}
                    self._send_response(200, "application/json", json.dumps(res, indent=2).encode("utf-8"))

                elif parsed.path in ("/api/droid/preview", "/api/droid/preview/"):
                    try:
                        b_data = json.loads(body_str) if body_str else {}
                    except Exception:
                        b_data = {}
                    u_agent = getattr(gateway_ref.companion, "unified_android_agent", None) if gateway_ref.companion else None
                    if u_agent:
                        src = b_data.get("source") or b_data.get("file_path") or "C:\\NR-AI\\nr_android_test\\app\\src\\main\\java\\com\\nrai\\test\\MainActivity.kt"
                        p_rep = u_agent.analyze_compose_previews(src)
                        res = {"success": True, "report": p_rep.to_dict()}
                    else:
                        res = {"success": False, "error": "Unified Android Agent not available"}
                    self._send_response(200, "application/json", json.dumps(res, indent=2).encode("utf-8"))

                elif parsed.path in ("/api/droid/semantics", "/api/droid/semantics/"):
                    try:
                        b_data = json.loads(body_str) if body_str else {}
                    except Exception:
                        b_data = {}
                    u_agent = getattr(gateway_ref.companion, "unified_android_agent", None) if gateway_ref.companion else None
                    if u_agent:
                        src = b_data.get("source") or b_data.get("file_path") or "C:\\NR-AI\\nr_android_test\\app\\src\\main\\java\\com\\nrai\\test\\MainActivity.kt"
                        serial = b_data.get("serial")
                        s_rep = u_agent.correlate_runtime_semantics(src, serial=serial)
                        res = {"success": True, "report": s_rep.to_dict()}
                    else:
                        res = {"success": False, "error": "Unified Android Agent not available"}
                    self._send_response(200, "application/json", json.dumps(res, indent=2).encode("utf-8"))

                elif parsed.path in ("/api/droid/verify", "/api/droid/verify/"):
                    try:
                        b_data = json.loads(body_str) if body_str else {}
                    except Exception:
                        b_data = {}
                    u_agent = getattr(gateway_ref.companion, "unified_android_agent", None) if gateway_ref.companion else None
                    if u_agent:
                        assertions = b_data.get("assertions", [])
                        serial = b_data.get("serial")
                        v_rep = u_agent.verify_ui_state(assertions, serial=serial)
                        res = {"success": v_rep.status.value in ("PASS", "PARTIAL"), "report": v_rep.to_dict()}
                    else:
                        res = {"success": False, "error": "Unified Android Agent not available"}
                    self._send_response(200, "application/json", json.dumps(res, indent=2).encode("utf-8"))


                elif parsed.path in ("/api/droid/reproduce", "/api/droid/reproduce/"):
                    try:
                        b_data = json.loads(body_str) if body_str else {}
                    except Exception:
                        b_data = {}
                    u_agent = getattr(gateway_ref.companion, "unified_android_agent", None) if gateway_ref.companion else None
                    if u_agent:
                        bug_desc = b_data.get("description", "Generic bug")
                        serial = b_data.get("serial")
                        rep = u_agent.reproduce_failure(bug_desc, serial=serial)
                        res = {"success": rep.state.value == "REPRODUCED", "result": rep.to_dict()}
                    else:
                        res = {"success": False, "error": "Unified Android Agent not available"}
                    self._send_response(200, "application/json", json.dumps(res, indent=2).encode("utf-8"))

                elif parsed.path in ("/api/droid/action", "/api/droid/action/"):
                    try:
                        b_data = json.loads(body_str) if body_str else {}
                    except Exception:
                        b_data = {}
                    u_agent = getattr(gateway_ref.companion, "unified_android_agent", None) if gateway_ref.companion else None
                    if u_agent:
                        action_type = b_data.get("action_type", "TAP")
                        id_type = b_data.get("identifier_type", "resource_id")
                        id_val = b_data.get("identifier_value", "")
                        text = b_data.get("text")
                        serial = b_data.get("serial")
                        act_res = u_agent.execute_ui_action(action_type, id_type, id_val, text=text, serial=serial)
                        res = {"success": act_res.success, "result": act_res.to_dict()}
                    else:
                        res = {"success": False, "error": "Unified Android Agent not available"}
                    self._send_response(200, "application/json", json.dumps(res, indent=2).encode("utf-8"))

                elif parsed.path in ("/api/droid/diagnose", "/api/droid/diagnose/"):
                    try:
                        b_data = json.loads(body_str) if body_str else {}
                    except Exception:
                        b_data = {}
                    u_agent = getattr(gateway_ref.companion, "unified_android_agent", None) if gateway_ref.companion else None
                    if u_agent:
                        task_id = b_data.get("task_id", "T-DEFAULT")
                        rc = u_agent.diagnose_root_cause(task_id=task_id)
                        res = {"success": rc.classification.value != "UNRESOLVED", "report": rc.to_dict()}
                    else:
                        res = {"success": False, "error": "Unified Android Agent not available"}
                    self._send_response(200, "application/json", json.dumps(res, indent=2).encode("utf-8"))

                elif parsed.path in ("/api/droid/e2e", "/api/droid/e2e/"):
                    try:
                        b_data = json.loads(body_str) if body_str else {}
                    except Exception:
                        b_data = {}
                    u_agent = getattr(gateway_ref.companion, "unified_android_agent", None) if gateway_ref.companion else None
                    if u_agent:
                        bug_desc = b_data.get("description", "Autonomous debug run")
                        mock_mode = b_data.get("mock_mode", True)
                        e2e_res = u_agent.run_autonomous_engineering_loop(bug_desc, mock_mode=mock_mode)
                        res = {"success": e2e_res.verification_status.value == "VERIFIED", "report": e2e_res.to_dict()}
                    else:
                        res = {"success": False, "error": "Unified Android Agent not available"}
                    self._send_response(200, "application/json", json.dumps(res, indent=2).encode("utf-8"))

                elif parsed.path in ("/api/droid/regression", "/api/droid/regression/"):
                    try:
                        b_data = json.loads(body_str) if body_str else {}
                    except Exception:
                        b_data = {}
                    u_agent = getattr(gateway_ref.companion, "unified_android_agent", None) if gateway_ref.companion else None
                    if u_agent:
                        files = b_data.get("files", [])
                        comp_rep = u_agent.run_regression_check(files)
                        res = {"success": comp_rep.passed_cleanly, "report": comp_rep.to_dict()}
                    else:
                        res = {"success": False, "error": "Unified Android Agent not available"}
                    self._send_response(200, "application/json", json.dumps(res, indent=2).encode("utf-8"))

                elif parsed.path in ("/api/droid/impact", "/api/droid/impact/"):
                    try:
                        b_data = json.loads(body_str) if body_str else {}
                    except Exception:
                        b_data = {}
                    u_agent = getattr(gateway_ref.companion, "unified_android_agent", None) if gateway_ref.companion else None
                    if u_agent:
                        files = b_data.get("files", [])
                        impact = u_agent.calculate_blast_radius(files)
                        res = {"success": True, "impact": impact}
                    else:
                        res = {"success": False, "error": "Unified Android Agent not available"}
                    self._send_response(200, "application/json", json.dumps(res, indent=2).encode("utf-8"))

                elif parsed.path in ("/api/droid/engineering-loop", "/api/droid/engineering-loop/"):
                    try:
                        b_data = json.loads(body_str) if body_str else {}
                    except Exception:
                        b_data = {}
                    u_agent = getattr(gateway_ref.companion, "unified_android_agent", None) if gateway_ref.companion else None
                    if u_agent:
                        goal = b_data.get("goal", "Engineering loop task")
                        mock = b_data.get("mock_mode", True)
                        loop_res = u_agent.run_advanced_engineering_loop(goal, mock_mode=mock)
                        res = {"success": loop_res.verification_status.value == "VERIFIED", "report": loop_res.to_dict()}
                    else:
                        res = {"success": False, "error": "Unified Android Agent not available"}
                    self._send_response(200, "application/json", json.dumps(res, indent=2).encode("utf-8"))

                elif parsed.path in ("/api/droid/switch-project", "/api/droid/switch-project/"):
                    try:
                        b_data = json.loads(body_str) if body_str else {}
                    except Exception:
                        b_data = {}
                    u_agent = getattr(gateway_ref.companion, "unified_android_agent", None) if gateway_ref.companion else None
                    if u_agent:
                        project_id = b_data.get("project_id", "nr_android_test")
                        ok = u_agent.switch_active_project(project_id)
                        res = {"success": ok, "active_project_id": u_agent.multi_project.active_project_id}
                    else:
                        res = {"success": False, "error": "Unified Android Agent not available"}
                    self._send_response(200, "application/json", json.dumps(res, indent=2).encode("utf-8"))

                # SkyShield Phase 4 Threat Vulnerability Check
                elif parsed.path in ("/api/skyshield/threats/check", "/api/skyshield/threats/check/"):
                    coord = getattr(gateway_ref.companion, "security_coordinator", None)
                    try:
                        b_data = json.loads(body_str) if body_str else {}
                    except Exception:
                        b_data = {}
                    dev_id = b_data.get("device_id", "dev_mock_vivo_v2334")
                    res = coord.check_device_vulnerability(dev_id) if coord else {"success": False, "error": "Coordinator unavailable"}
                    self._send_response(200 if res.get('success') else 404, 'application/json', json.dumps(res, indent=2).encode("utf-8"))

                # Agent Specific Action
                elif parsed.path == "/api/conversation/active":
                    try:
                        b_data = json.loads(body_str) if body_str else {}
                    except Exception:
                        b_data = {}
                    agent_id = b_data.get("agent_id")
                    comp = gateway_ref.companion
                    if comp:
                        comp.active_conversation_agent = agent_id
                    self._send_response(200, "application/json", json.dumps({"success": True, "active_conversation_agent": agent_id}).encode("utf-8"))

                elif parsed.path == "/api/conversation/interrupt":
                    comp = gateway_ref.companion
                    if comp and hasattr(comp.speaker, "stop"):
                        comp.speaker.stop()
                    self._send_response(200, "application/json", json.dumps({
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
                    comp = gateway_ref.companion
                    if comp and hasattr(comp, "activate_agent_session"):
                        speech, is_first = comp.activate_agent_session(agent_id)
                    else:
                        if comp:
                            comp.active_conversation_agent = agent_id
                    payload = json.dumps({
                        "success": True,
                        "agent_id": agent_id,
                        "speech": speech,
                        "first_intro": is_first,
                        "active_conversation_agent": agent_id,
                    }, indent=2).encode("utf-8")
                    self._send_response(200, "application/json", payload)

                elif parsed.path.startswith("/api/agent/") and (parsed.path.endswith("/chat") or parsed.path.endswith("/chat/")):
                    agent_id = parsed.path[len("/api/agent/"):].rstrip("/").replace("/chat", "").strip("/")
                    if agent_id in ("nova_discovery_agent", "aegis_verification_agent"):
                        agent_id = "universal_knowledge_engine"
                    try:
                        c_data = json.loads(body_str) if body_str else {}
                    except Exception:
                        c_data = {}
                    cmd = c_data.get("text") or c_data.get("command") or body_str.strip()
                    speak = bool(c_data.get("speak_output", False))
                    comp = gateway_ref.companion
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
                    self._send_response(200, "application/json", payload)

                elif parsed.path.startswith("/api/agent/") and parsed.path.endswith("/action"):
                    path_parts = parsed.path.strip("/").split("/")
                    agent_id = path_parts[2] if len(path_parts) >= 4 else "unknown"
                    try:
                        action_data = json.loads(body_str) if body_str else {}
                    except Exception:
                        action_data = {}
                    action_id = action_data.get("action_id", "")
                    label = action_data.get("label", action_id)

                    prompt = f"Agent '{agent_id}' executing action: {label}"
                    if gateway_ref.companion and hasattr(gateway_ref.companion, "interact"):
                        resp = gateway_ref.companion.interact(prompt, speak_output=False)
                        reply_text = getattr(resp, "text", str(resp))
                    else:
                        reply_text = f"Action '{label}' executed for agent '{agent_id}'."

                    payload = json.dumps({
                        "success": True,
                        "agent_id": agent_id,
                        "action_id": action_id,
                        "result": reply_text,
                    }, indent=2).encode("utf-8")
                    self._send_response(200, "application/json", payload)

                else:
                    self._send_response(404, "text/plain", b"Not Found")

            def _send_response(self, code: int, content_type: str, body: bytes):
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                pass  # suppress noisy console logs

        try:
            class ReusableThreadingTCPServer(socketserver.ThreadingTCPServer):
                allow_reuse_address = True
                daemon_threads = True

            self._httpd = ReusableThreadingTCPServer((self.host, self.port), GatewayHTTPHandler)
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
