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
    DEFAULT_HOST,
    DEFAULT_PORT,
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
from app.remote.rate_limiter import RateLimiter
from app.remote.transport import SecureTransport, TransportSecurityMode

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
        transport_mode: TransportSecurityMode = TransportSecurityMode.ENCRYPTED_SESSION,
    ):
        self.companion = companion
        self.pairing_manager = pairing_manager or PairingManager()
        self.session_manager = session_manager or SessionManager(self.pairing_manager)
        self.rate_limiter = rate_limiter or RateLimiter()
        self.emergency_stop = emergency_stop or EmergencyStopController()
        self.audit_logger = audit_logger or SecurityAuditLogger()
        self.transport = SecureTransport(mode=transport_mode)

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
