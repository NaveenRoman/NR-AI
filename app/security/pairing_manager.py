"""
NR-AI SkyShield: Authorized Device Pairing & Secure Enrollment Manager.
Step 10 Phase 2 — Authorized Device Pairing & Secure Enrollment.

Provides:
- Thread-safe Device Pairing & Enrollment state machine
- Cryptographically secure single-use pairing codes and challenges
- Strict target-device owner approval workflow (zero silent / OTP-only access)
- Explicit capability scope enforcement (strictly blocks surveillance capabilities)
- Device key registration (public key abstractions, zero private keys stored/transmitted)
- Authenticated cryptographic sessions with HMAC-SHA256 signature verification
- Nonce-based replay protection with strict timestamp tolerance
- Multi-device registry with bounded resource limits
- Device suspension, revocation, and reauthorization
- Immediate Emergency Stop override and session termination
- Complete operational audit logging with sensitive data redaction
"""

from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from app.remote.emergency import EmergencyStopController
from app.security.enrollment_models import (
    DEFAULT_PHASE2_CAPABILITIES,
    PROHIBITED_CAPABILITIES,
    AuthorizationState,
    DeviceIdentityModel,
    DeviceKeyRecord,
    DeviceSession,
    EnrollmentState,
    PairingRequest,
    PairingRequestStatus,
    SkyShieldCapability,
    generate_challenge,
    generate_pairing_code,
    is_capability_prohibited,
    mask_phone_number,
    normalize_phone_number,
)
from app.security.models import (
    DataVerificationState,
    SecurityAuditRecord,
    SecurityEvent,
    SecuritySeverity,
    redact_sensitive_data,
)

logger = logging.getLogger("NRAI.SkyShield.PairingManager")


# ==============================================================================
# Constants & Resource Bounds
# ==============================================================================

MAX_ENROLLED_DEVICES = 50
MAX_CONCURRENT_SESSIONS_PER_DEVICE = 5
MAX_GLOBAL_ACTIVE_SESSIONS = 100
MAX_PENDING_PAIRING_REQUESTS = 20
MAX_PAIRING_ATTEMPTS = 5

DEFAULT_PAIRING_TTL_SECONDS = 600       # 10 minutes
MIN_PAIRING_TTL_SECONDS = 60            # 1 minute
MAX_PAIRING_TTL_SECONDS = 3600          # 1 hour

DEFAULT_SESSION_TTL_SECONDS = 3600      # 1 hour
MIN_SESSION_TTL_SECONDS = 300           # 5 minutes
MAX_SESSION_TTL_SECONDS = 86400         # 24 hours

NONCE_TIMESTAMP_TOLERANCE_SECONDS = 120  # 2 minutes drift window


class IllegalEnrollmentTransitionError(ValueError):
    """Raised when an illegal device enrollment state transition is attempted."""
    pass


class CapabilityEscalationError(ValueError):
    """Raised when an unauthorized or prohibited capability scope is requested."""
    pass


class DeviceRevokedError(PermissionError):
    """Raised when an operation is attempted on a revoked device."""
    pass


class DevicePairingManager:
    """
    Master manager for SkyShield device identity, pairing, secure enrollment,
    cryptographic session verification, and revocation.
    """

    # Deterministic transition graph for enrollment lifecycle
    LEGAL_ENROLLMENT_TRANSITIONS: Dict[EnrollmentState, Set[EnrollmentState]] = {
        EnrollmentState.UNREGISTERED: {
            EnrollmentState.PAIRING_REQUESTED,
            EnrollmentState.STOPPED,
        },
        EnrollmentState.PAIRING_REQUESTED: {
            EnrollmentState.AWAITING_OWNER_APPROVAL,
            EnrollmentState.OWNER_APPROVED,
            EnrollmentState.EXPIRED,
            EnrollmentState.FAILED,
            EnrollmentState.STOPPED,
        },
        EnrollmentState.AWAITING_OWNER_APPROVAL: {
            EnrollmentState.OWNER_APPROVED,
            EnrollmentState.EXPIRED,
            EnrollmentState.FAILED,
            EnrollmentState.REVOKED,
            EnrollmentState.STOPPED,
        },
        EnrollmentState.OWNER_APPROVED: {
            EnrollmentState.KEY_EXCHANGE,
            EnrollmentState.FAILED,
            EnrollmentState.REVOKED,
            EnrollmentState.STOPPED,
        },
        EnrollmentState.KEY_EXCHANGE: {
            EnrollmentState.ENROLLED,
            EnrollmentState.FAILED,
            EnrollmentState.REVOKED,
            EnrollmentState.STOPPED,
        },
        EnrollmentState.ENROLLED: {
            EnrollmentState.AUTHENTICATED,
            EnrollmentState.SUSPENDED,
            EnrollmentState.REVOKED,
            EnrollmentState.STOPPED,
        },
        EnrollmentState.AUTHENTICATED: {
            EnrollmentState.ENROLLED,
            EnrollmentState.SUSPENDED,
            EnrollmentState.REVOKED,
            EnrollmentState.EXPIRED,
            EnrollmentState.STOPPED,
        },
        EnrollmentState.SUSPENDED: {
            EnrollmentState.AUTHENTICATED,
            EnrollmentState.ENROLLED,
            EnrollmentState.REVOKED,
            EnrollmentState.STOPPED,
        },
        EnrollmentState.REVOKED: {
            # Revocation is terminal for that enrollment; re-enrollment begins anew
            EnrollmentState.UNREGISTERED,
            EnrollmentState.STOPPED,
        },
        EnrollmentState.EXPIRED: {
            EnrollmentState.PAIRING_REQUESTED,
            EnrollmentState.UNREGISTERED,
            EnrollmentState.STOPPED,
        },
        EnrollmentState.FAILED: {
            EnrollmentState.PAIRING_REQUESTED,
            EnrollmentState.UNREGISTERED,
            EnrollmentState.STOPPED,
        },
        EnrollmentState.STOPPED: {
            EnrollmentState.UNREGISTERED,
            EnrollmentState.ENROLLED,
            EnrollmentState.AUTHENTICATED,
            EnrollmentState.SUSPENDED,
            EnrollmentState.REVOKED,
        },
    }

    def __init__(
        self,
        emergency_stop: Optional[EmergencyStopController] = None,
        audit_logger_fn: Optional[Callable[..., Any]] = None,
    ):
        self.emergency_stop = emergency_stop or EmergencyStopController()
        self._audit_logger_fn = audit_logger_fn
        
        self._devices: Dict[str, DeviceIdentityModel] = {}          # device_id -> DeviceIdentityModel
        self._pairing_requests: Dict[str, PairingRequest] = {}       # pairing_id -> PairingRequest
        self._pairing_code_map: Dict[str, str] = {}                  # pairing_code -> pairing_id
        self._keys: Dict[str, DeviceKeyRecord] = {}                  # key_id -> DeviceKeyRecord
        self._shared_secrets: Dict[str, bytes] = {}                  # device_id -> raw shared secret bytes (never logged)
        self._sessions: Dict[str, DeviceSession] = {}                # session_id -> DeviceSession
        self._seen_nonces: Dict[Tuple[str, str], float] = {}         # (device_id, nonce) -> timestamp
        
        self._emergency_stop_latched = False
        self._lock = threading.RLock()

        # Connect emergency stop cancel callback
        self.emergency_stop.register_cancellation_callback(self._on_emergency_stop_triggered)

        # Seed local mock device for testing & transparent verification
        self._seed_default_mock_device()

    def _seed_default_mock_device(self) -> None:
        """Seeds an explicitly labeled deterministic mock device for verification."""
        mock_id = "dev_mock_vivo_v2334"
        fingerprint = hashlib.sha256(f"MOCK_HARDWARE:{mock_id}".encode("utf-8")).hexdigest()
        mock_dev = DeviceIdentityModel(
            device_id=mock_id,
            device_name="Mock Vivo V2334 Test Device",
            platform="android",
            device_fingerprint=fingerprint,
            registration_time=time.time() - 3600,
            last_seen=time.time(),
            enrollment_state=EnrollmentState.ENROLLED,
            authorization_state=AuthorizationState.AUTHORIZED,
            phone_number_masked="+91 ******1234",
            verification_state=DataVerificationState.MOCK,
            granted_capabilities=list(DEFAULT_PHASE2_CAPABILITIES),
            metadata={"model": "V2334", "build": "MOCK-BUILD-01"},
        )
        self._devices[mock_id] = mock_dev
        
        # Register mock device key
        mock_key_id = f"key_{mock_id[:12]}"
        mock_secret = hashlib.sha256(f"SECRET_{mock_id}".encode("utf-8")).digest()
        self._shared_secrets[mock_id] = mock_secret
        self._keys[mock_key_id] = DeviceKeyRecord(
            device_id=mock_id,
            key_id=mock_key_id,
            algorithm="HMAC-SHA256",
            public_key_hex=hashlib.sha256(mock_secret).hexdigest(),
        )

    # --------------------------------------------------------------------------
    # Audit & Event Helpers
    # --------------------------------------------------------------------------

    def _record_audit(
        self,
        initiator: str,
        operation: str,
        target: str,
        result: str,
        classification: str = "DEVICE_ENROLLMENT",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Emits an operational audit record through the security coordinator."""
        if self._audit_logger_fn:
            try:
                self._audit_logger_fn(
                    initiator=initiator,
                    operation=operation,
                    target=target,
                    result=result,
                    classification=classification,
                    details=details or {},
                )
            except Exception as ex:
                logger.warning(f"Failed to emit audit record: {ex}")

    # --------------------------------------------------------------------------
    # Emergency Stop Handling
    # --------------------------------------------------------------------------

    def _on_emergency_stop_triggered(self) -> None:
        """Callback fired when Emergency Stop is activated."""
        self.trigger_emergency_stop("Emergency Stop Activated by Operator")

    def trigger_emergency_stop(self, reason: str = "Emergency Stop Latch Activated") -> None:
        """
        Immediately suspends all active sessions, terminates in-flight pairing requests,
        transitions enrolled devices to STOPPED, and blocks further operations.
        """
        with self._lock:
            self._emergency_stop_latched = True
            
            # Invalidate all active sessions
            for s in self._sessions.values():
                s.status = "STOPPED"
            
            # Expire/Revoke all pending pairing requests
            for req in self._pairing_requests.values():
                if req.status == PairingRequestStatus.PENDING:
                    req.status = PairingRequestStatus.REVOKED

            # Transition all active devices to STOPPED
            for dev in self._devices.values():
                if dev.enrollment_state in (EnrollmentState.ENROLLED, EnrollmentState.AUTHENTICATED, EnrollmentState.PAIRING_REQUESTED, EnrollmentState.AWAITING_OWNER_APPROVAL):
                    dev.enrollment_state = EnrollmentState.STOPPED
                    dev.authorization_state = AuthorizationState.SUSPENDED

            self._record_audit(
                initiator="Emergency Controller",
                operation="EMERGENCY_STOP",
                target="All Enrolled Devices & Sessions",
                result="HALTED",
                classification="EMERGENCY_HALT",
                details={"reason": reason},
            )

    def reset_emergency_stop(self) -> bool:
        """Resets the Emergency Stop latch and restores devices to ENROLLED/SUSPENDED."""
        with self._lock:
            self._emergency_stop_latched = False
            for dev in self._devices.values():
                if dev.enrollment_state == EnrollmentState.STOPPED:
                    dev.enrollment_state = EnrollmentState.ENROLLED
                    dev.authorization_state = AuthorizationState.AUTHORIZED
            
            self._record_audit(
                initiator="Operator",
                operation="RESET_EMERGENCY_STOP",
                target="Device Manager Latch",
                result="SUCCESS",
                classification="OPERATIONAL_LIFECYCLE",
                details={"status": "RESTORED"},
            )
            return True

    def is_emergency_stopped(self) -> bool:
        """Returns True if Emergency Stop is active."""
        return self._emergency_stop_latched or self.emergency_stop.is_active()

    # --------------------------------------------------------------------------
    # Enrollment State Machine
    # --------------------------------------------------------------------------

    def transition_device_state(
        self,
        device_id: str,
        target_state: EnrollmentState,
        reason: str = "",
        initiator: str = "SkyShield Device Manager",
    ) -> None:
        """
        Transition a device's enrollment state strictly validating against the legal transition graph.
        Raises IllegalEnrollmentTransitionError on illegal jumps.
        """
        with self._lock:
            dev = self._devices.get(device_id)
            if not dev:
                raise ValueError(f"Device '{device_id}' not found")

            current_state = dev.enrollment_state

            # Allow self-transitions as idempotent
            if current_state == target_state:
                return

            legal_next = self.LEGAL_ENROLLMENT_TRANSITIONS.get(current_state, set())
            if target_state not in legal_next:
                err_msg = (
                    f"Illegal enrollment transition for device '{device_id}': "
                    f"{current_state.value} -> {target_state.value}. Allowed: {[s.value for s in legal_next]}"
                )
                logger.error(err_msg)
                self._record_audit(
                    initiator=initiator,
                    operation="ENROLLMENT_STATE_TRANSITION_REJECTED",
                    target=device_id,
                    result="REJECTED",
                    classification="POLICY_VIOLATION",
                    details={"current_state": current_state.value, "attempted_state": target_state.value, "reason": reason},
                )
                raise IllegalEnrollmentTransitionError(err_msg)

            dev.enrollment_state = target_state
            
            # Synchronize authorization state
            if target_state == EnrollmentState.AUTHENTICATED:
                dev.authorization_state = AuthorizationState.AUTHORIZED
            elif target_state == EnrollmentState.ENROLLED:
                dev.authorization_state = AuthorizationState.AUTHORIZED
            elif target_state == EnrollmentState.SUSPENDED:
                dev.authorization_state = AuthorizationState.SUSPENDED
            elif target_state == EnrollmentState.REVOKED:
                dev.authorization_state = AuthorizationState.REVOKED
            elif target_state == EnrollmentState.STOPPED:
                dev.authorization_state = AuthorizationState.SUSPENDED
            elif target_state in (EnrollmentState.UNREGISTERED, EnrollmentState.FAILED, EnrollmentState.EXPIRED):
                dev.authorization_state = AuthorizationState.UNAUTHORIZED

            dev.last_seen = time.time()

            self._record_audit(
                initiator=initiator,
                operation="ENROLLMENT_STATE_TRANSITION",
                target=device_id,
                result="SUCCESS",
                classification="DEVICE_ENROLLMENT",
                details={
                    "from_state": current_state.value,
                    "to_state": target_state.value,
                    "reason": reason,
                },
            )

    # --------------------------------------------------------------------------
    # Pairing Request Lifecycle
    # --------------------------------------------------------------------------

    def create_pairing_request(
        self,
        device_name: str,
        platform: str = "android",
        phone_number: Optional[str] = None,
        requested_capabilities: Optional[List[str]] = None,
        ttl_seconds: int = DEFAULT_PAIRING_TTL_SECONDS,
        requester_id: str = "SkyShield Operator",
        device_id: Optional[str] = None,
    ) -> Tuple[bool, str, Optional[PairingRequest]]:
        """
        Creates an explicit pairing request requiring device owner confirmation.
        A phone number alone never grants access or initiates enrollment.
        """
        with self._lock:
            if self.is_emergency_stopped():
                return False, "EMERGENCY_STOP_ACTIVE", None

            # 1. Check pending requests bound
            active_pending = [r for r in self._pairing_requests.values() if r.status == PairingRequestStatus.PENDING and not r.is_expired()]
            if len(active_pending) >= MAX_PENDING_PAIRING_REQUESTS:
                return False, "MAX_PENDING_PAIRING_REQUESTS_EXCEEDED", None

            # 2. Validate capability scopes (strictly block prohibited surveillance)
            caps = requested_capabilities or list(DEFAULT_PHASE2_CAPABILITIES)
            for cap in caps:
                if cap in PROHIBITED_CAPABILITIES or is_capability_prohibited(cap):
                    err = f"Capability escalation rejected: '{cap}' is strictly prohibited."
                    logger.warning(err)
                    self._record_audit(
                        initiator=requester_id,
                        operation="CAPABILITY_ESCALATION_BLOCKED",
                        target=device_name,
                        result="DENIED",
                        classification="SECURITY_POLICY_VIOLATION",
                        details={"prohibited_capability": cap},
                    )
                    return False, f"PROHIBITED_CAPABILITY: {cap}", None

            # 3. Bound TTL
            bounded_ttl = max(MIN_PAIRING_TTL_SECONDS, min(ttl_seconds, MAX_PAIRING_TTL_SECONDS))
            now = time.time()
            expires_at = now + bounded_ttl

            # 4. Generate cryptographically random parameters
            pairing_id = f"pair_{secrets.token_hex(8)}"
            pairing_code = generate_pairing_code(6)
            challenge = generate_challenge(32)
            
            # Derive deterministic or assigned device_id
            dev_id = device_id or f"dev_{hashlib.sha256(f'{device_name}:{now}:{secrets.token_hex(4)}'.encode('utf-8')).hexdigest()[:12]}"

            # Clean and mask phone number
            masked_phone = mask_phone_number(phone_number) if phone_number else None

            # Create or update provisional device entry
            if dev_id not in self._devices:
                provisional_dev = DeviceIdentityModel(
                    device_id=dev_id,
                    device_name=device_name,
                    platform=platform,
                    device_fingerprint=hashlib.sha256(f"PROVISIONAL:{dev_id}".encode("utf-8")).hexdigest(),
                    registration_time=now,
                    last_seen=now,
                    enrollment_state=EnrollmentState.PAIRING_REQUESTED,
                    authorization_state=AuthorizationState.PENDING_APPROVAL,
                    phone_number_masked=masked_phone,
                    verification_state=DataVerificationState.MOCK if "mock" in device_name.lower() else DataVerificationState.LIVE,
                    granted_capabilities=caps,
                )
                self._devices[dev_id] = provisional_dev
            else:
                dev = self._devices[dev_id]
                if dev.enrollment_state == EnrollmentState.REVOKED:
                    return False, "DEVICE_REVOKED_REPAIRING_FORBIDDEN", None
                dev.enrollment_state = EnrollmentState.PAIRING_REQUESTED
                dev.authorization_state = AuthorizationState.PENDING_APPROVAL

            req = PairingRequest(
                pairing_id=pairing_id,
                device_id=dev_id,
                requester_id=requester_id,
                pairing_code=pairing_code,
                challenge=challenge,
                created_at=now,
                expires_at=expires_at,
                status=PairingRequestStatus.PENDING,
                requested_capabilities=caps,
                phone_number_masked=masked_phone,
                device_name=device_name,
                platform=platform,
                attempts_remaining=MAX_PAIRING_ATTEMPTS,
            )

            self._pairing_requests[pairing_id] = req
            self._pairing_code_map[pairing_code] = pairing_id

            self._record_audit(
                initiator=requester_id,
                operation="PAIRING_REQUEST_CREATED",
                target=dev_id,
                result="SUCCESS",
                classification="DEVICE_ENROLLMENT",
                details={
                    "pairing_id": pairing_id,
                    "expires_in_seconds": bounded_ttl,
                    "requested_capabilities": caps,
                    "phone_number_masked": masked_phone,
                },
            )

            return True, "PAIRING_REQUEST_CREATED", req

    def get_pairing_request(self, pairing_id: str) -> Optional[PairingRequest]:
        """Fetches pairing request by ID, checking expiration."""
        with self._lock:
            req = self._pairing_requests.get(pairing_id)
            if not req:
                return None
            if req.status == PairingRequestStatus.PENDING and req.is_expired():
                req.status = PairingRequestStatus.EXPIRED
                dev = self._devices.get(req.device_id)
                if dev and dev.enrollment_state == EnrollmentState.PAIRING_REQUESTED:
                    dev.enrollment_state = EnrollmentState.EXPIRED
            return req

    # --------------------------------------------------------------------------
    # Owner Approval & Rejection Workflow
    # --------------------------------------------------------------------------

    def approve_pairing(
        self,
        pairing_id: str,
        pairing_code: str,
        device_fingerprint: str,
        public_key_hex: Optional[str] = None,
        approver_actor: str = "Device Owner",
    ) -> Tuple[bool, str, Optional[DeviceIdentityModel]]:
        """
        Explicit approval by device owner.
        Requires valid unexpired pairing code, updates state to ENROLLED,
        registers device key and establishes shared secret for authenticated sessions.
        """
        with self._lock:
            if self.is_emergency_stopped():
                return False, "EMERGENCY_STOP_ACTIVE", None

            req = self._pairing_requests.get(pairing_id)
            if not req:
                return False, "PAIRING_REQUEST_NOT_FOUND", None

            if req.is_expired() or req.status == PairingRequestStatus.EXPIRED:
                req.status = PairingRequestStatus.EXPIRED
                return False, "PAIRING_CODE_EXPIRED", None

            if req.status != PairingRequestStatus.PENDING:
                return False, f"INVALID_PAIRING_STATUS_{req.status.value}", None

            if req.attempts_remaining <= 0:
                req.status = PairingRequestStatus.EXPIRED
                return False, "MAX_PAIRING_ATTEMPTS_EXCEEDED", None

            # Constant-time comparison of single-use pairing code
            if not hmac.compare_digest(req.pairing_code, pairing_code.strip()):
                req.attempts_remaining -= 1
                logger.warning(f"Invalid pairing code attempt for pairing {pairing_id}. Attempts remaining: {req.attempts_remaining}")
                self._record_audit(
                    initiator=approver_actor,
                    operation="PAIRING_CODE_INVALID",
                    target=req.device_id,
                    result="FAILED",
                    classification="AUTHENTICATION_FAILURE",
                    details={"attempts_remaining": req.attempts_remaining},
                )
                if req.attempts_remaining <= 0:
                    req.status = PairingRequestStatus.EXPIRED
                    dev = self._devices.get(req.device_id)
                    if dev:
                        dev.enrollment_state = EnrollmentState.FAILED
                return False, "INVALID_PAIRING_CODE", None

            # Validate target device
            dev = self._devices.get(req.device_id)
            if not dev:
                return False, "DEVICE_NOT_FOUND", None

            if dev.enrollment_state == EnrollmentState.REVOKED:
                return False, "DEVICE_REVOKED", None

            # Check enrolled device bound
            enrolled_count = len([d for d in self._devices.values() if d.enrollment_state in (EnrollmentState.ENROLLED, EnrollmentState.AUTHENTICATED)])
            if enrolled_count >= MAX_ENROLLED_DEVICES:
                return False, "MAX_ENROLLED_DEVICES_EXCEEDED", None

            # Step 1: Owner Approved
            self.transition_device_state(dev.device_id, EnrollmentState.OWNER_APPROVED, reason="Explicit owner approval")
            
            # Step 2: Key Exchange & Shared Secret Derivation
            self.transition_device_state(dev.device_id, EnrollmentState.KEY_EXCHANGE, reason="Initiating cryptographic key registration")

            # Generate shared secret and key record
            shared_secret = secrets.token_bytes(32)
            self._shared_secrets[dev.device_id] = shared_secret
            
            key_id = f"key_{dev.device_id[:12]}_{int(time.time())}"
            pub_hex = public_key_hex or hashlib.sha256(shared_secret).hexdigest()
            
            key_record = DeviceKeyRecord(
                device_id=dev.device_id,
                key_id=key_id,
                algorithm="HMAC-SHA256",
                public_key_hex=pub_hex,
                created_at=time.time(),
                status="ACTIVE",
                last_used=time.time(),
            )
            self._keys[key_id] = key_record

            # Step 3: Complete Enrollment
            dev.device_fingerprint = device_fingerprint
            dev.granted_capabilities = list(req.requested_capabilities)
            self.transition_device_state(dev.device_id, EnrollmentState.ENROLLED, reason="Key exchange and verification complete")

            # Finalize pairing request
            req.status = PairingRequestStatus.COMPLETED
            req.shared_secret_hash = hashlib.sha256(shared_secret).hexdigest()
            # Invalidate pairing code so it cannot be reused
            self._pairing_code_map.pop(req.pairing_code, None)

            self._record_audit(
                initiator=approver_actor,
                operation="DEVICE_ENROLLED",
                target=dev.device_id,
                result="SUCCESS",
                classification="DEVICE_ENROLLMENT",
                details={
                    "pairing_id": pairing_id,
                    "key_id": key_id,
                    "granted_capabilities": dev.granted_capabilities,
                    "device_name": dev.device_name,
                },
            )

            return True, "DEVICE_ENROLLED", dev

    def reject_pairing(
        self,
        pairing_id: str,
        reason: str = "Device owner rejected pairing",
        rejector_actor: str = "Device Owner",
    ) -> Tuple[bool, str]:
        """Explicit rejection of pairing request by device owner."""
        with self._lock:
            req = self._pairing_requests.get(pairing_id)
            if not req:
                return False, "PAIRING_REQUEST_NOT_FOUND"

            req.status = PairingRequestStatus.REJECTED
            self._pairing_code_map.pop(req.pairing_code, None)

            dev = self._devices.get(req.device_id)
            if dev and dev.enrollment_state == EnrollmentState.PAIRING_REQUESTED:
                dev.enrollment_state = EnrollmentState.UNREGISTERED
                dev.authorization_state = AuthorizationState.UNAUTHORIZED

            self._record_audit(
                initiator=rejector_actor,
                operation="OWNER_APPROVAL_REJECTED",
                target=req.device_id,
                result="SUCCESS",
                classification="DEVICE_ENROLLMENT",
                details={"pairing_id": pairing_id, "reason": reason},
            )

            return True, "PAIRING_REJECTED"

    # --------------------------------------------------------------------------
    # Cryptographic Session Management
    # --------------------------------------------------------------------------

    def create_device_session(
        self,
        device_id: str,
        ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
        requested_scopes: Optional[List[str]] = None,
    ) -> Tuple[bool, str, Optional[DeviceSession]]:
        """
        Creates an authenticated session for an ENROLLED device.
        Bounded session lifetime, strictly checked capabilities.
        """
        with self._lock:
            if self.is_emergency_stopped():
                return False, "EMERGENCY_STOP_ACTIVE", None

            dev = self._devices.get(device_id)
            if not dev:
                return False, "DEVICE_NOT_FOUND", None

            if dev.enrollment_state == EnrollmentState.REVOKED or dev.authorization_state == AuthorizationState.REVOKED:
                return False, "DEVICE_REVOKED", None

            if dev.enrollment_state == EnrollmentState.SUSPENDED or dev.authorization_state == AuthorizationState.SUSPENDED:
                return False, "DEVICE_SUSPENDED", None

            if dev.enrollment_state not in (EnrollmentState.ENROLLED, EnrollmentState.AUTHENTICATED):
                return False, f"DEVICE_NOT_ENROLLED (current: {dev.enrollment_state.value})", None

            # Clean expired sessions
            self._cleanup_expired_sessions_locked()

            # Check bounds
            if len(self._sessions) >= MAX_GLOBAL_ACTIVE_SESSIONS:
                return False, "GLOBAL_SESSION_LIMIT_EXCEEDED", None

            active_dev_sessions = [s for s in self._sessions.values() if s.device_id == device_id and s.status == "ACTIVE"]
            if len(active_dev_sessions) >= MAX_CONCURRENT_SESSIONS_PER_DEVICE:
                # Evict oldest session
                active_dev_sessions.sort(key=lambda s: s.issued_at)
                oldest = active_dev_sessions[0]
                oldest.status = "EXPIRED"

            # Find active key for device
            active_key = next((k for k in self._keys.values() if k.device_id == device_id and k.status == "ACTIVE"), None)
            if not active_key:
                return False, "DEVICE_KEY_NOT_FOUND", None

            # Determine scopes (intersection with granted capabilities, zero prohibited)
            scopes = []
            cand_scopes = requested_scopes or dev.granted_capabilities
            for sc in cand_scopes:
                if sc in dev.granted_capabilities and sc not in PROHIBITED_CAPABILITIES and not is_capability_prohibited(sc):
                    scopes.append(sc)

            bounded_ttl = max(MIN_SESSION_TTL_SECONDS, min(ttl_seconds, MAX_SESSION_TTL_SECONDS))
            now = time.time()
            session_id = f"sess_{secrets.token_hex(16)}"

            session = DeviceSession(
                session_id=session_id,
                device_id=device_id,
                key_id=active_key.key_id,
                issued_at=now,
                expires_at=now + bounded_ttl,
                last_activity=now,
                permissions=scopes,
                status="ACTIVE",
            )

            self._sessions[session_id] = session
            dev.active_session_id = session_id
            self.transition_device_state(device_id, EnrollmentState.AUTHENTICATED, reason="Session authenticated")

            self._record_audit(
                initiator="Device Session Authenticator",
                operation="SESSION_CREATED",
                target=device_id,
                result="SUCCESS",
                classification="SESSION_MANAGEMENT",
                details={
                    "session_id": session_id,
                    "expires_in_seconds": bounded_ttl,
                    "permissions": scopes,
                },
            )

            return True, "SESSION_CREATED", session

    def validate_session_request(
        self,
        request_id: str,
        device_id: str,
        session_id: str,
        timestamp: float,
        nonce: str,
        action: str,
        scope: str,
        signature: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str]:
        """
        Validates an authenticated request against active session, replay protection,
        and HMAC-SHA256 signature verification.
        """
        with self._lock:
            if self.is_emergency_stopped():
                return False, "EMERGENCY_STOP_ACTIVE"

            # 1. Device check
            dev = self._devices.get(device_id)
            if not dev:
                return False, "DEVICE_NOT_FOUND"

            if dev.enrollment_state == EnrollmentState.REVOKED or dev.authorization_state == AuthorizationState.REVOKED:
                return False, "DEVICE_REVOKED"

            if dev.enrollment_state == EnrollmentState.SUSPENDED or dev.authorization_state == AuthorizationState.SUSPENDED:
                return False, "DEVICE_SUSPENDED"

            # 2. Session check
            session = self._sessions.get(session_id)
            if not session:
                return False, "SESSION_NOT_FOUND"

            if session.device_id != device_id:
                return False, "SESSION_DEVICE_MISMATCH"

            if session.status != "ACTIVE":
                return False, f"SESSION_{session.status}"

            now = time.time()
            if session.is_expired(now):
                session.status = "EXPIRED"
                return False, "SESSION_EXPIRED"

            # 3. Scope / capability authorization
            if scope not in session.permissions or scope in PROHIBITED_CAPABILITIES or is_capability_prohibited(scope):
                self._record_audit(
                    initiator=f"Device:{device_id}",
                    operation="UNAUTHORIZED_CAPABILITY_REQUEST",
                    target=action,
                    result="DENIED",
                    classification="AUTHORIZATION_FAILURE",
                    details={"scope": scope, "granted_scopes": session.permissions},
                )
                return False, f"UNAUTHORIZED_CAPABILITY: {scope}"

            # 4. Replay protection: Timestamp drift check
            if abs(now - timestamp) > NONCE_TIMESTAMP_TOLERANCE_SECONDS:
                return False, "TIMESTAMP_OUT_OF_BOUNDS"

            # 5. Replay protection: Nonce deduplication
            nonce_key = (device_id, nonce)
            if nonce_key in self._seen_nonces:
                self._record_audit(
                    initiator=f"Device:{device_id}",
                    operation="REPLAY_ATTACK_DETECTED",
                    target=request_id,
                    result="BLOCKED",
                    classification="SECURITY_ALERT",
                    details={"nonce": nonce},
                )
                return False, "REPLAY_DETECTED"
            self._seen_nonces[nonce_key] = timestamp

            # Prune old nonces
            cutoff = now - (NONCE_TIMESTAMP_TOLERANCE_SECONDS * 2)
            self._seen_nonces = {k: v for k, v in self._seen_nonces.items() if v >= cutoff}

            # 6. Cryptographic signature check (HMAC-SHA256)
            shared_secret = self._shared_secrets.get(device_id)
            if not shared_secret:
                return False, "DEVICE_SHARED_SECRET_NOT_FOUND"

            # Reconstruct canonical string:
            # request_id:device_id:session_id:timestamp:nonce:action:scope:payload_sha256
            payload_json = json.dumps(payload or {}, sort_keys=True, separators=(",", ":"))
            payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
            canonical = f"{request_id}:{device_id}:{session_id}:{timestamp:.3f}:{nonce}:{action}:{scope}:{payload_hash}"
            expected_sig = hmac.new(shared_secret, canonical.encode("utf-8"), hashlib.sha256).hexdigest()

            if not hmac.compare_digest(expected_sig, signature):
                self._record_audit(
                    initiator=f"Device:{device_id}",
                    operation="INVALID_SIGNATURE",
                    target=request_id,
                    result="FAILED",
                    classification="AUTHENTICATION_FAILURE",
                )
                return False, "INVALID_SIGNATURE"

            # Validated: Touch session
            session.touch(now)
            dev.last_seen = now
            return True, "OK"

    def revoke_session(self, session_id: str, reason: str = "Operator revoked session") -> bool:
        """Explicitly revokes an active session."""
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return False
            session.status = "REVOKED"
            
            dev = self._devices.get(session.device_id)
            if dev and dev.active_session_id == session_id:
                dev.active_session_id = None
                if dev.enrollment_state == EnrollmentState.AUTHENTICATED:
                    dev.enrollment_state = EnrollmentState.ENROLLED

            self._record_audit(
                initiator="SkyShield Operator",
                operation="SESSION_REVOKED",
                target=session.device_id,
                result="SUCCESS",
                classification="SESSION_MANAGEMENT",
                details={"session_id": session_id, "reason": reason},
            )
            return True

    def _cleanup_expired_sessions_locked(self) -> None:
        """Internal helper to mark expired sessions."""
        now = time.time()
        for s in self._sessions.values():
            if s.status == "ACTIVE" and s.is_expired(now):
                s.status = "EXPIRED"

    # --------------------------------------------------------------------------
    # Device Suspension, Revocation & Reauthorization
    # --------------------------------------------------------------------------

    def suspend_device(self, device_id: str, reason: str = "Suspended by operator") -> Tuple[bool, str]:
        """Suspends an enrolled device, temporarily disabling its sessions and access."""
        with self._lock:
            dev = self._devices.get(device_id)
            if not dev:
                return False, "DEVICE_NOT_FOUND"

            if dev.enrollment_state == EnrollmentState.REVOKED:
                return False, "CANNOT_SUSPEND_REVOKED_DEVICE"

            self.transition_device_state(device_id, EnrollmentState.SUSPENDED, reason=reason)
            
            # Suspend active sessions
            for s in self._sessions.values():
                if s.device_id == device_id and s.status == "ACTIVE":
                    s.status = "SUSPENDED"

            self._record_audit(
                initiator="SkyShield Operator",
                operation="DEVICE_SUSPENDED",
                target=device_id,
                result="SUCCESS",
                classification="DEVICE_MANAGEMENT",
                details={"reason": reason},
            )
            return True, "DEVICE_SUSPENDED"

    def revoke_device(self, device_id: str, reason: str = "Revoked by operator") -> Tuple[bool, str]:
        """
        Permanently revokes an enrolled device.
        Invalidates all sessions, pending pairings, capability grants, and blocks reconnect.
        """
        with self._lock:
            dev = self._devices.get(device_id)
            if not dev:
                return False, "DEVICE_NOT_FOUND"

            # Invalidate all active sessions for device
            for s in self._sessions.values():
                if s.device_id == device_id:
                    s.status = "REVOKED"

            # Invalidate any pending pairing requests
            for req in self._pairing_requests.values():
                if req.device_id == device_id and req.status == PairingRequestStatus.PENDING:
                    req.status = PairingRequestStatus.REVOKED
                    self._pairing_code_map.pop(req.pairing_code, None)

            # Revoke registered keys
            for k in self._keys.values():
                if k.device_id == device_id:
                    k.status = "REVOKED"

            # Purge shared secret
            self._shared_secrets.pop(device_id, None)

            # Strip capabilities and transition to REVOKED
            dev.granted_capabilities = []
            dev.active_session_id = None
            self.transition_device_state(device_id, EnrollmentState.REVOKED, reason=reason)

            self._record_audit(
                initiator="SkyShield Operator",
                operation="DEVICE_REVOKED",
                target=device_id,
                result="SUCCESS",
                classification="DEVICE_MANAGEMENT",
                details={"reason": reason},
            )
            return True, "DEVICE_REVOKED"

    def reauthorize_device(self, device_id: str, reason: str = "Reauthorized by operator") -> Tuple[bool, str]:
        """Lifts suspension on a suspended device, returning it to ENROLLED."""
        with self._lock:
            dev = self._devices.get(device_id)
            if not dev:
                return False, "DEVICE_NOT_FOUND"

            if dev.enrollment_state == EnrollmentState.REVOKED:
                return False, "CANNOT_REAUTHORIZE_REVOKED_DEVICE"

            if dev.enrollment_state != EnrollmentState.SUSPENDED:
                return False, f"DEVICE_NOT_SUSPENDED (current: {dev.enrollment_state.value})"

            self.transition_device_state(device_id, EnrollmentState.ENROLLED, reason=reason)

            self._record_audit(
                initiator="SkyShield Operator",
                operation="DEVICE_REAUTHORIZED",
                target=device_id,
                result="SUCCESS",
                classification="DEVICE_MANAGEMENT",
                details={"reason": reason},
            )
            return True, "DEVICE_REAUTHORIZED"

    # --------------------------------------------------------------------------
    # Retrieval & Telemetry
    # --------------------------------------------------------------------------

    def get_device(self, device_id: str) -> Optional[DeviceIdentityModel]:
        """Returns device identity model if registered."""
        with self._lock:
            return self._devices.get(device_id)

    def list_devices(self) -> List[Dict[str, Any]]:
        """Returns safe list of all registered devices with transparent provenance."""
        with self._lock:
            return [d.to_dict() for d in self._devices.values()]

    def list_pairing_requests(self) -> List[Dict[str, Any]]:
        """Returns safe list of all pairing requests."""
        with self._lock:
            return [r.to_dict() for r in self._pairing_requests.values()]

    def list_active_sessions(self) -> List[Dict[str, Any]]:
        """Returns safe list of active sessions without leaking tokens."""
        with self._lock:
            return [s.to_dict() for s in self._sessions.values() if s.status == "ACTIVE" and not s.is_expired()]

    def get_shared_secret(self, device_id: str) -> Optional[bytes]:
        """Internal helper for signing test packets (never logged or exposed via API)."""
        with self._lock:
            return self._shared_secrets.get(device_id)
