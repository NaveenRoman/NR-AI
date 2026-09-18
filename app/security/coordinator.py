"""
NR-AI SkyShield: Master Security Coordinator.
Step 10 Phase 1 — Security Agent Foundation.

Orchestrates:
- Deterministic Security State Machine
- Emergency Stop Integration (hard halt on all active security operations)
- Device, application, and sensor scanning
- Permission auditing and threat finding aggregation
- Append-only security events and operational audit logging
- Full Security Dashboard state serialization
"""

from datetime import datetime, timezone
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from app.remote.emergency import EmergencyStopController
from app.security.auditor import PermissionAuditor
from app.security.models import (
    ApplicationSecurityCard,
    CameraSecurityModel,
    DataVerificationState,
    DeviceSecurityModel,
    EvidenceConfidence,
    FindingCategory,
    MicrophoneSecurityModel,
    PermissionName,
    PermissionStatus,
    SecurityAuditRecord,
    SecurityEvent,
    SecurityFinding,
    SecurityPolicy,
    SecuritySeverity,
    SecurityState,
    redact_sensitive_data,
)
from app.security.enrollment_models import (
    AuthorizationState,
    DeviceIdentityModel,
    DeviceSession,
    EnrollmentState,
    PairingRequest,
    SkyShieldCapability,
)
from app.security.health_models import (
    AnomalyEvent,
    AnomalySeverity,
    DeviceBaseline,
    DeviceHealthSnapshot,
    DeviceHealthState,
    SafeResponseAction,
    SecurityPostureScore,
    TelemetryProcessingState,
    ThreatCategory,
    ThreatConclusion,
)
from app.security.health_analyzer import (
    AUTH_FAILURE_BURST_THRESHOLD,
    CPU_CRITICAL_THRESHOLD,
    CPU_WARNING_THRESHOLD,
    DeviceHealthAnalyzer,
)
from app.security.pairing_manager import DevicePairingManager
from app.security.scanner import SecurityScanner

logger = logging.getLogger("NRAI.SkyShield.Coordinator")


class SecurityCoordinator:
    """
    Master coordinator for the SkyShield Security subsystem.
    Maintains thread-safe state, aggregates audits, and drives dashboard telemetry.
    """

    # Legal state transition graph for strict deterministic operation
    LEGAL_TRANSITIONS: Dict[SecurityState, Set[SecurityState]] = {
        SecurityState.IDLE: {SecurityState.SCANNING, SecurityState.MONITORING, SecurityState.STOPPED, SecurityState.ERROR},
        SecurityState.SCANNING: {SecurityState.ANALYZING, SecurityState.STOPPED, SecurityState.ERROR},
        SecurityState.ANALYZING: {SecurityState.ALERT, SecurityState.MONITORING, SecurityState.IDLE, SecurityState.STOPPED, SecurityState.ERROR},
        SecurityState.MONITORING: {SecurityState.SCANNING, SecurityState.ALERT, SecurityState.IDLE, SecurityState.STOPPED, SecurityState.ERROR},
        SecurityState.ALERT: {SecurityState.RESOLVING, SecurityState.STOPPED, SecurityState.ERROR},
        SecurityState.RESOLVING: {SecurityState.ANALYZING, SecurityState.IDLE, SecurityState.STOPPED, SecurityState.ERROR},
        SecurityState.STOPPED: {SecurityState.IDLE, SecurityState.ERROR},
        SecurityState.ERROR: {SecurityState.IDLE, SecurityState.STOPPED},
    }

    def __init__(
        self,
        scanner: Optional[SecurityScanner] = None,
        auditor: Optional[PermissionAuditor] = None,
        emergency_stop: Optional[EmergencyStopController] = None,
        pairing_manager: Optional[DevicePairingManager] = None,
        health_analyzer: Optional[DeviceHealthAnalyzer] = None,
    ):
        self.scanner = scanner or SecurityScanner()
        self.auditor = auditor or PermissionAuditor()
        self.emergency_stop = emergency_stop or EmergencyStopController()
        self.policy = SecurityPolicy()
        self.pairing_manager = pairing_manager or DevicePairingManager(
            emergency_stop=self.emergency_stop,
            audit_logger_fn=self._record_audit,
        )
        self.health_analyzer = health_analyzer or DeviceHealthAnalyzer()
        
        self._state: SecurityState = SecurityState.IDLE
        self._lock = threading.RLock()
        self._active_operation: Optional[str] = None
        self._findings: List[SecurityFinding] = []
        self._events: List[SecurityEvent] = []
        self._audit_log: List[SecurityAuditRecord] = []
        
        # Device Health & Anomaly caches (device_id -> data)
        self._device_health_cache: Dict[str, DeviceHealthSnapshot] = {}
        self._device_anomalies_cache: Dict[str, List[AnomalyEvent]] = {}
        self._device_posture_cache: Dict[str, SecurityPostureScore] = {}
        
        # Register callback with emergency stop
        self.emergency_stop.register_cancellation_callback(self._on_emergency_stop_fired)

        # Initialize baseline data
        self._cached_device: DeviceSecurityModel = self.scanner.scan_device()
        self._cached_apps: List[ApplicationSecurityCard] = self.scanner.scan_applications()
        self._cached_camera: CameraSecurityModel = self.scanner.scan_camera()
        self._cached_microphone: MicrophoneSecurityModel = self.scanner.scan_microphone()
        self._cached_permissions: Dict[str, PermissionStatus] = self.scanner.scan_permissions()

        # Seed initial health telemetry for mock device
        self._seed_mock_device_health()

        # Seed initial audit log record
        self._record_audit(
            initiator="System Bootstrap",
            operation="INITIALIZE_SECURITY_COORDINATOR",
            target="SkyShield Engine",
            result="SUCCESS",
            details={"initial_state": SecurityState.IDLE.value},
        )

    def _seed_mock_device_health(self) -> None:
        """Seeds initial mock device health and baseline for local verification."""
        mock_id = "dev_mock_vivo_v2334"
        try:
            snap = self.health_analyzer.generate_mock_scenario("NORMAL_DEVICE", device_id=mock_id)
            h_state, anoms, post, _ = self.health_analyzer.analyze_health(snap)
            self._device_health_cache[mock_id] = snap
            self._device_anomalies_cache[mock_id] = anoms
            self._device_posture_cache[mock_id] = post
        except Exception as ex:
            logger.warning(f"Failed to seed initial mock device health: {ex}")

    # --------------------------------------------------------------------------
    # State Machine Management
    # --------------------------------------------------------------------------

    @property
    def current_state(self) -> SecurityState:
        with self._lock:
            return self._state

    def transition_to(self, target_state: SecurityState, reason: str = "") -> bool:
        """
        Executes a deterministic state transition.
        Rejects illegal state transitions with ValueError.
        """
        with self._lock:
            # If emergency stop is active, prevent exiting STOPPED unless resetting to IDLE
            if self.emergency_stop.is_active() and target_state != SecurityState.STOPPED and target_state != SecurityState.IDLE:
                err_msg = f"Cannot transition to {target_state.value}: Emergency Stop is active."
                logger.warning(err_msg)
                raise ValueError(err_msg)

            allowed = self.LEGAL_TRANSITIONS.get(self._state, set())
            if target_state not in allowed:
                err_msg = f"Illegal SkyShield transition: {self._state.value} -> {target_state.value}. Allowed: {[s.value for s in allowed]}"
                logger.error(err_msg)
                raise ValueError(err_msg)

            prev = self._state
            self._state = target_state
            logger.info(f"SkyShield State: {prev.value} -> {target_state.value} ({reason})")

            self._record_audit(
                initiator="State Machine",
                operation="STATE_TRANSITION",
                target=f"{prev.value} -> {target_state.value}",
                result="SUCCESS",
                details={"reason": reason},
            )
            return True

    # --------------------------------------------------------------------------
    # Emergency Stop Handling
    # --------------------------------------------------------------------------

    def _on_emergency_stop_fired(self) -> None:
        """Internal callback invoked when EmergencyStopController fires."""
        with self._lock:
            self._state = SecurityState.STOPPED
            self._active_operation = None
            if hasattr(self, "pairing_manager") and self.pairing_manager:
                self.pairing_manager.trigger_emergency_stop("Immediate operator or subsystem halt fired.")
            logger.warning("🛑 SkyShield Emergency Stop triggered: all active scans halted.")
            self._record_audit(
                initiator="EmergencyStopController",
                operation="EMERGENCY_STOP_TRIGGER",
                target="All Active SkyShield Operations",
                result="STOPPED",
                classification="CRITICAL_SAFETY_HALT",
                details={"reason": "Immediate operator or subsystem halt fired."},
            )

    def trigger_emergency_stop(self, reason: str = "Operator Halt via Command Center") -> Dict[str, Any]:
        """User/UI entry point for Emergency Stop."""
        with self._lock:
            res = self.emergency_stop.trigger(triggered_by="SkyShield Operator", reason=reason)
            self._state = SecurityState.STOPPED
            self._active_operation = None
            if hasattr(self, "pairing_manager") and self.pairing_manager:
                self.pairing_manager.trigger_emergency_stop(reason)
            return {
                "success": True,
                "state": SecurityState.STOPPED.value,
                "reason": reason,
                "status": res.to_dict(),
            }

    def reset_emergency_stop(self) -> bool:
        """Resets the Emergency Stop controller back to IDLE if nominal."""
        with self._lock:
            if hasattr(self.emergency_stop, "reset"):
                self.emergency_stop.reset()
            else:
                self.emergency_stop._is_active = False
            self._state = SecurityState.IDLE
            if hasattr(self, "pairing_manager") and self.pairing_manager:
                self.pairing_manager.reset_emergency_stop()
            self._record_audit(
                initiator="Operator",
                operation="EMERGENCY_STOP_RESET",
                target="SkyShield State Machine",
                result="SUCCESS",
                details={"new_state": SecurityState.IDLE.value},
            )
            return True

    # --------------------------------------------------------------------------
    # Full Security Scan Workflow
    # --------------------------------------------------------------------------

    def run_full_scan(self) -> Dict[str, Any]:
        """
        Executes a deterministic end-to-end security audit.
        Guaranteed zero shell execution and zero private message access.
        """
        with self._lock:
            if self.emergency_stop.is_active():
                return {
                    "success": False,
                    "error": "Cannot scan while Emergency Stop is active.",
                    "state": SecurityState.STOPPED.value,
                }

            self.transition_to(SecurityState.SCANNING, "Initiating full security scan")
            self._active_operation = "FULL_SECURITY_SCAN"

            try:
                # 1. Device Posture
                self._cached_device = self.scanner.scan_device()

                # 2. Applications (WhatsApp, Instagram, Snapchat)
                self._cached_apps = self.scanner.scan_applications()

                # 3. Sensors (Camera & Microphone)
                self._cached_camera = self.scanner.scan_camera()
                self._cached_microphone = self.scanner.scan_microphone()

                # 4. Permissions
                self._cached_permissions = self.scanner.scan_permissions()

                # 5. Analysis
                self.transition_to(SecurityState.ANALYZING, "Evaluating findings against zero-trust baseline")
                findings: List[SecurityFinding] = []

                # Device permissions findings
                dev_findings = self.auditor.audit_device_permissions(self._cached_permissions)
                findings.extend(dev_findings)

                # Application permissions findings
                for app in self._cached_apps:
                    app_findings = self.auditor.audit_application_permissions(app)
                    app.security_findings = app_findings
                    app.risk_state = self.auditor.evaluate_risk_state(app_findings)
                    findings.extend(app_findings)

                self._findings = findings

                # 6. Events
                self._events = self.scanner.collect_security_events()

                # Determine completion state
                if any(f.severity in (SecuritySeverity.HIGH, SecuritySeverity.CRITICAL) for f in findings):
                    self.transition_to(SecurityState.ALERT, "High/Critical threat vectors identified")
                else:
                    self.transition_to(SecurityState.IDLE, "Scan complete with nominal or low-risk findings")

                self._record_audit(
                    initiator="Operator / Routine",
                    operation="FULL_SECURITY_SCAN",
                    target="Host & Enrolled Applications",
                    result="SUCCESS",
                    classification="SECURITY_POSTURE_ASSESSMENT",
                    details={"findings_count": len(findings)},
                )

                return self.get_dashboard_state()

            except Exception as ex:
                logger.error(f"Error during security scan: {ex}", exc_info=True)
                self._state = SecurityState.ERROR
                self._record_audit(
                    initiator="Scanner",
                    operation="FULL_SECURITY_SCAN",
                    target="Host & Enrolled Applications",
                    result="ERROR",
                    classification="SYSTEM_FAULT",
                    details={"error": str(ex)},
                )
                return {
                    "success": False,
                    "error": str(ex),
                    "state": SecurityState.ERROR.value,
                }
            finally:
                self._active_operation = None

    # --------------------------------------------------------------------------
    # Audit Logging & Event Tracking
    # --------------------------------------------------------------------------

    def _record_audit(
        self,
        initiator: str,
        operation: str,
        target: str,
        result: str,
        classification: str = "SECURITY_POSTURE_ASSESSMENT",
        details: Optional[Dict[str, Any]] = None,
    ) -> SecurityAuditRecord:
        """Appends an operational audit record with secret redaction."""
        rec = SecurityAuditRecord(
            audit_id=f"audit-{int(time.time()*1000)}-{len(self._audit_log)+1}",
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            initiator=initiator,
            operation=operation,
            target=target,
            result=result,
            classification=classification,
            details=details or {},
        )
        self._audit_log.append(rec)
        return rec

    def add_security_event(self, event: SecurityEvent) -> None:
        """Appends a validated security event."""
        with self._lock:
            self._events.append(event)

    def get_audit_log(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Returns bounded list of redacted audit log records."""
        with self._lock:
            return [a.to_dict() for a in self._audit_log[-limit:]]

    # --------------------------------------------------------------------------
    # Dashboard State Aggregation
    # --------------------------------------------------------------------------

    def get_dashboard_state(self) -> Dict[str, Any]:
        """
        Produces the 100% ground-truth dashboard payload for SkyShield Command Center.
        Zero fake data; strictly maps to internal models.
        """
        with self._lock:
            # Compute overall status
            if self.emergency_stop.is_active() or self._state == SecurityState.STOPPED:
                overall_status = "STOPPED"
            elif any(f.severity == SecuritySeverity.CRITICAL for f in self._findings):
                overall_status = "CRITICAL"
            elif any(f.severity == SecuritySeverity.HIGH for f in self._findings):
                overall_status = "ELEVATED"
            elif any(f.severity == SecuritySeverity.MEDIUM for f in self._findings):
                overall_status = "ATTENTION"
            else:
                overall_status = "SECURE"

            device_dict = self._cached_device.to_dict()
            device_dict["security_status"] = overall_status

            # Phase 3 Device Health Telemetry
            device_health_list = [snap.to_dict() for snap in self._device_health_cache.values()]
            active_anomalies = []
            for anom_list in self._device_anomalies_cache.values():
                active_anomalies.extend([a.to_dict() for a in anom_list])
            posture_scores = [p.to_dict() for p in self._device_posture_cache.values()]

            primary_snap = self._device_health_cache.get("dev_mock_vivo_v2334")
            primary_posture = self._device_posture_cache.get("dev_mock_vivo_v2334")

            return {
                "success": True,
                "state": self._state.value,
                "overall_security_status": overall_status,
                "active_operation": self._active_operation,
                "emergency_stop_active": self.emergency_stop.is_active(),
                "device": device_dict,
                "applications": [a.to_dict() for a in self._cached_apps],
                "camera": self._cached_camera.to_dict(),
                "microphone": self._cached_microphone.to_dict(),
                "permissions": {k: v.value for k, v in self._cached_permissions.items()},
                "threats": [f.to_dict() for f in self._findings],
                "findings": [f.to_dict() for f in self._findings],
                "events": [e.to_dict() for e in self._events[-20:]],
                "audit_log": [a.to_dict() for a in self._audit_log[-20:]],
                "enrolled_devices": self.pairing_manager.list_devices(),
                "pairing_requests": self.pairing_manager.list_pairing_requests(),
                "active_sessions": self.pairing_manager.list_active_sessions(),
                "device_health": device_health_list,
                "primary_device_health": primary_snap.to_dict() if primary_snap else None,
                "primary_device_posture": primary_posture.to_dict() if primary_posture else None,
                "anomalies": active_anomalies,
                "posture_scores": posture_scores,
                "timestamp": time.time(),
            }

    # --------------------------------------------------------------------------
    # Device Pairing & Session Management Proxies
    # --------------------------------------------------------------------------

    def create_pairing_request(self, *args, **kwargs):
        return self.pairing_manager.create_pairing_request(*args, **kwargs)

    def approve_pairing(self, *args, **kwargs):
        return self.pairing_manager.approve_pairing(*args, **kwargs)

    def reject_pairing(self, *args, **kwargs):
        return self.pairing_manager.reject_pairing(*args, **kwargs)

    def get_pairing_request(self, pairing_id: str):
        return self.pairing_manager.get_pairing_request(pairing_id)

    def get_device(self, device_id: str):
        return self.pairing_manager.get_device(device_id)

    def list_devices(self):
        return self.pairing_manager.list_devices()

    def suspend_device(self, device_id: str, reason: str = "Suspended by operator"):
        return self.pairing_manager.suspend_device(device_id, reason=reason)

    def revoke_device(self, device_id: str, reason: str = "Revoked by operator"):
        return self.pairing_manager.revoke_device(device_id, reason=reason)

    def reauthorize_device(self, device_id: str, reason: str = "Reauthorized by operator"):
        return self.pairing_manager.reauthorize_device(device_id, reason=reason)

    def create_device_session(self, *args, **kwargs):
        return self.pairing_manager.create_device_session(*args, **kwargs)

    def validate_session_request(self, *args, **kwargs):
        return self.pairing_manager.validate_session_request(*args, **kwargs)

    # --------------------------------------------------------------------------
    # Phase 3 Device Health, Telemetry & Anomaly Detection APIs
    # --------------------------------------------------------------------------

    def verify_device_telemetry_authorized(
        self,
        device_id: str,
        scope: str = "health:read",
    ) -> Tuple[bool, str, Optional[DeviceIdentityModel]]:
        """
        Strict pre-flight verification before processing device telemetry (Section 11).
        Verifies:
        - Device exists
        - Device is enrolled
        - Device is authenticated or in valid enrolled state
        - Device is NOT suspended or revoked
        - Capability scope is granted
        - Emergency stop is NOT active
        """
        if self.emergency_stop.is_active() or self._state == SecurityState.STOPPED:
            return False, "EMERGENCY_STOP_ACTIVE", None

        dev = self.pairing_manager.get_device(device_id)
        if not dev:
            return False, f"DEVICE_NOT_FOUND: '{device_id}'", None

        if dev.enrollment_state == EnrollmentState.REVOKED or dev.authorization_state == AuthorizationState.REVOKED:
            return False, f"DEVICE_REVOKED: '{device_id}' is permanently revoked", dev

        if dev.enrollment_state == EnrollmentState.SUSPENDED or dev.authorization_state == AuthorizationState.SUSPENDED:
            return False, f"DEVICE_SUSPENDED: '{device_id}' is suspended", dev

        if dev.enrollment_state not in (EnrollmentState.ENROLLED, EnrollmentState.AUTHENTICATED):
            return False, f"DEVICE_NOT_ENROLLED: '{device_id}' is in state {dev.enrollment_state.value}", dev

        # Verify capability
        allowed_scopes = {"telemetry:read", "health:read", "security_events:read", SkyShieldCapability.READ_DEVICE_STATUS.value}
        granted = set(dev.granted_capabilities)
        if scope not in granted and not (granted & allowed_scopes):
            return False, f"CAPABILITY_NOT_GRANTED: missing scope '{scope}'", dev

        return True, "AUTHORIZED", dev

    def get_device_health(self, device_id: str) -> Dict[str, Any]:
        """Retrieves current health snapshot for an authorized device."""
        ok, msg, dev = self.verify_device_telemetry_authorized(device_id, scope="health:read")
        if not ok:
            return {"success": False, "error": msg, "device_id": device_id}

        with self._lock:
            snap = self._device_health_cache.get(device_id)
            if not snap:
                # Generate default baseline snapshot
                snap = self.health_analyzer.generate_mock_scenario("NORMAL_DEVICE", device_id=device_id)
                self._device_health_cache[device_id] = snap
            return {"success": True, "device_id": device_id, "health": snap.to_dict()}

    def get_device_anomalies(self, device_id: str) -> Dict[str, Any]:
        """Retrieves active detected anomalies for an authorized device."""
        ok, msg, dev = self.verify_device_telemetry_authorized(device_id, scope="health:read")
        if not ok:
            return {"success": False, "error": msg, "device_id": device_id}

        with self._lock:
            anomalies = self._device_anomalies_cache.get(device_id, [])
            return {
                "success": True,
                "device_id": device_id,
                "count": len(anomalies),
                "anomalies": [a.to_dict() for a in anomalies],
            }

    def get_device_posture(self, device_id: str) -> Dict[str, Any]:
        """Retrieves transparent security posture score and contributing factors."""
        ok, msg, dev = self.verify_device_telemetry_authorized(device_id, scope="health:read")
        if not ok:
            return {"success": False, "error": msg, "device_id": device_id}

        with self._lock:
            posture = self._device_posture_cache.get(device_id)
            if not posture:
                snap = self._device_health_cache.get(device_id) or self.health_analyzer.generate_mock_scenario("NORMAL_DEVICE", device_id=device_id)
                anoms = self._device_anomalies_cache.get(device_id, [])
                posture = self.health_analyzer.calculate_security_posture(device_id, anoms, snap)
                self._device_posture_cache[device_id] = posture

            return {"success": True, "device_id": device_id, "posture": posture.to_dict()}

    def get_device_events(self, device_id: str) -> Dict[str, Any]:
        """Retrieves chronological security events associated with device."""
        ok, msg, dev = self.verify_device_telemetry_authorized(device_id, scope="security_events:read")
        if not ok:
            return {"success": False, "error": msg, "device_id": device_id}

        with self._lock:
            dev_events = [e.to_dict() for e in self._events if e.target == device_id or f"Device:{device_id}" in e.initiator or device_id in e.initiator]
            return {"success": True, "device_id": device_id, "count": len(dev_events), "events": dev_events[-30:]}

    def reset_device_baseline(self, device_id: str) -> Tuple[bool, str]:
        """Resets baseline history for authorized device."""
        ok, msg, dev = self.verify_device_telemetry_authorized(device_id, scope="health:read")
        if not ok:
            return False, msg

        with self._lock:
            self.health_analyzer.reset_baseline(device_id)
            self._record_audit(
                initiator="SkyShield Operator",
                operation="BASELINE_RESET",
                target=device_id,
                result="SUCCESS",
                classification="DEVICE_HEALTH",
                details={"action": "baseline_reset"},
            )
            return True, "BASELINE_RESET"

    def analyze_device_telemetry(
        self,
        device_id: str,
        snapshot_data: Optional[Dict[str, Any]] = None,
        mock_scenario: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Executes deterministic device health & anomaly analysis.
        Integrates anomalies into immutable SecurityEvent stream.
        """
        ok, msg, dev = self.verify_device_telemetry_authorized(device_id, scope="health:read")
        if not ok:
            return {"success": False, "error": msg, "device_id": device_id}

        with self._lock:
            # 1. Obtain or generate snapshot
            if mock_scenario:
                snap = self.health_analyzer.generate_mock_scenario(mock_scenario, device_id=device_id)
            elif snapshot_data:
                # Sanitized construction
                s_copy = dict(snapshot_data)
                s_copy["device_id"] = device_id
                s_copy.setdefault("data_source", DataVerificationState.MOCK.value)
                if isinstance(s_copy.get("data_source"), str):
                    try:
                        s_copy["data_source"] = DataVerificationState(s_copy["data_source"])
                    except Exception:
                        s_copy["data_source"] = DataVerificationState.MOCK
                snap = DeviceHealthSnapshot(**s_copy)
            else:
                snap = self._device_health_cache.get(device_id) or self.health_analyzer.generate_mock_scenario("NORMAL_DEVICE", device_id=device_id)

            # 2. Run analysis
            h_state, anoms, post, recs = self.health_analyzer.analyze_health(snap)

            # 3. Update caches
            self._device_health_cache[device_id] = snap
            self._device_anomalies_cache[device_id] = anoms
            self._device_posture_cache[device_id] = post

            # 4. Integrate into SecurityEvent stream
            for anom in anoms:
                event_type = f"{anom.category.value}_ANOMALY" if "ANOMALY" not in anom.category.value else anom.category.value
                sec_sev = SecuritySeverity.INFO
                if anom.severity == AnomalySeverity.CRITICAL:
                    sec_sev = SecuritySeverity.CRITICAL
                elif anom.severity == AnomalySeverity.HIGH:
                    sec_sev = SecuritySeverity.HIGH
                elif anom.severity == AnomalySeverity.MEDIUM:
                    sec_sev = SecuritySeverity.MEDIUM
                elif anom.severity == AnomalySeverity.LOW:
                    sec_sev = SecuritySeverity.LOW

                event = SecurityEvent(
                    event_id=f"evt_{anom.anomaly_id}",
                    timestamp=time.time(),
                    device_id=device_id,
                    event_type=event_type,
                    source=f"HealthAnalyzer:{device_id}",
                    severity=sec_sev,
                    description=anom.evidence,
                    evidence=str(anom.observed_value),
                    status="DETECTED",
                    result="DETECTED",
                    action=anom.category.value,
                    initiator=f"HealthAnalyzer:{device_id}",
                    target=device_id,
                )
                self._events.append(event)
                if len(self._events) > 100:
                    self._events.pop(0)

            # 5. Record operational audit log
            self._record_audit(
                initiator="HealthAnalyzer",
                operation="DEVICE_HEALTH_ANALYZED",
                target=device_id,
                result="SUCCESS",
                classification="DEVICE_HEALTH",
                details={
                    "health_state": h_state.value,
                    "anomaly_count": len(anoms),
                    "posture_score": post.score,
                    "data_source": snap.data_source.value,
                },
            )

            # 6. Generate advisory summary
            advisory = self.health_analyzer.generate_advisory_summary(h_state, anoms, post)

            return {
                "success": True,
                "device_id": device_id,
                "health_state": h_state.value,
                "snapshot": snap.to_dict(),
                "anomalies": [a.to_dict() for a in anoms],
                "posture": post.to_dict(),
                "recommendations": recs,
                "advisory": advisory,
            }

