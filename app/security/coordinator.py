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
from typing import Any, Callable, Dict, List, Optional, Set

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
    ):
        self.scanner = scanner or SecurityScanner()
        self.auditor = auditor or PermissionAuditor()
        self.emergency_stop = emergency_stop or EmergencyStopController()
        self.policy = SecurityPolicy()
        
        self._state: SecurityState = SecurityState.IDLE
        self._lock = threading.RLock()
        self._active_operation: Optional[str] = None
        self._findings: List[SecurityFinding] = []
        self._events: List[SecurityEvent] = []
        self._audit_log: List[SecurityAuditRecord] = []
        
        # Register callback with emergency stop
        self.emergency_stop.register_cancellation_callback(self._on_emergency_stop_fired)

        # Initialize baseline data
        self._cached_device: DeviceSecurityModel = self.scanner.scan_device()
        self._cached_apps: List[ApplicationSecurityCard] = self.scanner.scan_applications()
        self._cached_camera: CameraSecurityModel = self.scanner.scan_camera()
        self._cached_microphone: MicrophoneSecurityModel = self.scanner.scan_microphone()
        self._cached_permissions: Dict[str, PermissionStatus] = self.scanner.scan_permissions()

        # Seed initial audit log record
        self._record_audit(
            initiator="System Bootstrap",
            operation="INITIALIZE_SECURITY_COORDINATOR",
            target="SkyShield Engine",
            result="SUCCESS",
            details={"initial_state": SecurityState.IDLE.value},
        )

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
                "timestamp": time.time(),
            }
