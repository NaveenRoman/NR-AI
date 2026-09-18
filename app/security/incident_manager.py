"""
NR-AI SkyShield: Security Incident & Event Correlation Engine.
Step 10 Phase 4 — Security Intelligence, Incident Response & Finalization.

Correlates device health snapshots, telemetry anomalies, and security events into
structured, actionable security incidents.
Enforces epistemic evidence verification (OBSERVED, SUSPECTED, VERIFIED), alert deduplication,
bounded memory limits, and false-positive handling without data loss.
"""

from datetime import datetime, timezone
import hashlib
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from app.security.health_models import (
    AnomalyEvent,
    AnomalySeverity,
    DeviceHealthSnapshot,
    ThreatCategory,
)
from app.security.incident_models import (
    AlertSeverity,
    EvidenceVerificationState,
    IncidentState,
    SafeResponseAction,
    SecurityAlert,
    SecurityEvidence,
    SecurityIncident,
)
from app.security.models import SecurityEvent, redact_sensitive_data

logger = logging.getLogger("NRAI.SkyShield.IncidentEngine")


class SecurityIncidentEngine:
    """
    Master incident manager and event correlation engine for SkyShield.
    Maintains bounded collections of incidents and alerts.
    """

    MAX_INCIDENTS = 100
    MAX_ALERTS = 100
    ALERT_DEDUP_WINDOW_SECONDS = 300.0  # 5 minutes
    ALERT_RATE_LIMIT_PER_MINUTE = 15

    def __init__(self, audit_logger_fn: Optional[Callable[..., None]] = None):
        self._audit_logger = audit_logger_fn or (lambda **kwargs: None)
        self._incidents: Dict[str, SecurityIncident] = {}
        self._alerts: List[SecurityAlert] = []
        self._alert_dedup_cache: Dict[str, float] = {}  # dedup_key -> last_timestamp
        self._alert_timestamps: List[float] = []        # For rate-limiting
        self._incident_counter = 0
        self._alert_counter = 0
        self._lock = threading.RLock()

    # --------------------------------------------------------------------------
    # Multi-Signal Event Correlation Engine
    # --------------------------------------------------------------------------

    def correlate_telemetry_and_events(
        self,
        device_id: str,
        snapshot: Optional[DeviceHealthSnapshot],
        anomalies: List[AnomalyEvent],
        recent_events: List[SecurityEvent],
    ) -> List[SecurityIncident]:
        """
        Analyzes recent anomalies, events, and health metrics to produce or update correlated incidents.
        Distinguishes observed facts from inferences. Never declares compromise without verified evidence.
        """
        with self._lock:
            now = time.time()
            created_incidents: List[SecurityIncident] = []

            # 1. Check for Authentication Failure Bursts (Credential Attack Pattern)
            auth_failures = [e for e in recent_events if "AUTH_FAIL" in e.event_type.upper() or "AUTHENTICATION" in str(e.evidence).upper()]
            auth_anoms = [a for a in anomalies if a.category == ThreatCategory.AUTHENTICATION]
            total_auth_issues = len(auth_failures) + len(auth_anoms)

            if total_auth_issues >= 3:
                inc = self._create_or_update_incident(
                    device_id=device_id,
                    title="Repeated Authentication Failures Detected",
                    category="AUTHENTICATION",
                    severity="CRITICAL" if total_auth_issues >= 6 else "HIGH",
                    description=f"Observed {total_auth_issues} consecutive authentication failure signals within evaluation window. Potential brute force or credential fatigue pattern.",
                    evidence=[
                        SecurityEvidence(
                            evidence_id=f"ev_auth_{int(now)}_{i}",
                            evidence_type="AUTHENTICATION_FAILURE_BURST",
                            description=f"Auth failure count reached {total_auth_issues}",
                            source="AUDIT_LOG_AND_TELEMETRY",
                            timestamp=now,
                            verification_state=EvidenceVerificationState.OBSERVED,
                            data={"failure_count": total_auth_issues},
                        ) for i in range(1)
                    ],
                    related_events=[e.event_id for e in auth_failures] + [a.anomaly_id for a in auth_anoms],
                    confidence=0.88,
                    recommended_actions=[SafeResponseAction.REQUEST_REAUTH.value, SafeResponseAction.SUSPEND_DEVICE.value],
                    verification_state=EvidenceVerificationState.OBSERVED,
                )
                created_incidents.append(inc)

            # 2. Check for Multi-Signal Compromise Suspicion (Auth failure + Permission addition + Network flapping)
            has_perm_issue = any(a.category == ThreatCategory.PERMISSION for a in anomalies)
            has_net_issue = any(a.category == ThreatCategory.NETWORK for a in anomalies)
            if total_auth_issues >= 2 and (has_perm_issue or has_net_issue):
                inc = self._create_or_update_incident(
                    device_id=device_id,
                    title="Correlated Multi-Vector Security Anomaly",
                    category="AUTHORIZATION",
                    severity="CRITICAL",
                    description="Multiple distinct anomaly signals observed concurrently: repeated authentication failures combined with unexpected permission changes or network instability.",
                    evidence=[
                        SecurityEvidence(
                            evidence_id=f"ev_multi_{int(now)}",
                            evidence_type="MULTI_VECTOR_CORRELATION",
                            description="Co-occurrence of auth anomaly and permission/network anomaly",
                            source="CORRELATION_ENGINE",
                            timestamp=now,
                            verification_state=EvidenceVerificationState.SUSPECTED,
                            data={"auth_count": total_auth_issues, "permission_creep": has_perm_issue, "network_flapping": has_net_issue},
                        )
                    ],
                    related_events=[a.anomaly_id for a in anomalies],
                    confidence=0.75,
                    recommended_actions=[
                        SafeResponseAction.ISOLATE_DEVICE.value,
                        SafeResponseAction.REVIEW_PERMISSIONS.value,
                        SafeResponseAction.SUSPEND_DEVICE.value,
                    ],
                    verification_state=EvidenceVerificationState.SUSPECTED,
                )
                created_incidents.append(inc)

            # 3. Check for Resource Exhaustion / Starvation Pattern
            resource_anoms = [a for a in anomalies if a.category == ThreatCategory.RESOURCE_EXHAUSTION]
            if len(resource_anoms) >= 2 or any(a.severity == AnomalySeverity.CRITICAL for a in resource_anoms):
                inc = self._create_or_update_incident(
                    device_id=device_id,
                    title="Severe Hardware Resource Exhaustion",
                    category="RESOURCE_EXHAUSTION",
                    severity="HIGH",
                    description="Severe saturation of system CPU, Memory, or Storage. May degrade security monitoring capability.",
                    evidence=[
                        SecurityEvidence(
                            evidence_id=f"ev_res_{int(now)}",
                            evidence_type="RESOURCE_SATURATION",
                            description="Resource thresholds breached",
                            source="TELEMETRY_SNAPSHOT",
                            timestamp=now,
                            verification_state=EvidenceVerificationState.OBSERVED,
                            data={"cpu": getattr(snapshot, "cpu_usage", 0.0), "memory": getattr(snapshot, "memory_usage", {})},
                        )
                    ],
                    related_events=[a.anomaly_id for a in resource_anoms],
                    confidence=0.95,
                    recommended_actions=[SafeResponseAction.RESET_BASELINE.value, SafeResponseAction.REVIEW_NETWORK.value],
                    verification_state=EvidenceVerificationState.OBSERVED,
                )
                created_incidents.append(inc)

            # Trigger immediate alerts for high or critical incidents
            for inc in created_incidents:
                if inc.severity in ("HIGH", "CRITICAL"):
                    self.trigger_alert(
                        device_id=device_id,
                        severity=AlertSeverity.CRITICAL if inc.severity == "CRITICAL" else AlertSeverity.HIGH,
                        title=f"Incident Detected: {inc.title}",
                        message=inc.description,
                        incident_id=inc.incident_id,
                    )

            return created_incidents

    def _create_or_update_incident(
        self,
        device_id: str,
        title: str,
        category: str,
        severity: str,
        description: str,
        evidence: List[SecurityEvidence],
        related_events: List[str],
        confidence: float,
        recommended_actions: List[str],
        verification_state: EvidenceVerificationState,
    ) -> SecurityIncident:
        """Helper to create a new incident or update an existing open incident."""
        # Look for open incident with same category and device
        existing = next(
            (
                inc for inc in self._incidents.values()
                if inc.device_id == device_id
                and inc.category == category
                and inc.status not in (IncidentState.RESOLVED, IncidentState.DISMISSED, IncidentState.STOPPED)
            ),
            None,
        )
        now = time.time()
        if existing:
            existing.updated_at = now
            existing.severity = severity
            existing.description = description
            existing.confidence = confidence
            existing.verification_state = verification_state
            # Merge evidence without duplicates
            existing_ev_ids = {e.evidence_id for e in existing.evidence}
            for ev in evidence:
                if ev.evidence_id not in existing_ev_ids:
                    existing.evidence.append(ev)
            # Merge related events
            for rev in related_events:
                if rev not in existing.related_events:
                    existing.related_events.append(rev)
            return existing

        # Bounded capacity check
        if len(self._incidents) >= self.MAX_INCIDENTS:
            oldest_key = min(self._incidents.keys(), key=lambda k: self._incidents[k].created_at)
            del self._incidents[oldest_key]

        self._incident_counter += 1
        inc_id = f"inc_{int(now)}_{self._incident_counter:04d}"
        incident = SecurityIncident(
            incident_id=inc_id,
            device_id=device_id,
            created_at=now,
            updated_at=now,
            severity=severity,
            category=category,
            status=IncidentState.DETECTED,
            title=title,
            description=description,
            evidence=list(evidence),
            related_events=list(related_events),
            confidence=confidence,
            recommended_actions=list(recommended_actions),
            verification_state=verification_state,
        )
        self._incidents[inc_id] = incident

        self._audit_logger(
            initiator="Correlation Engine",
            operation="CREATE_SECURITY_INCIDENT",
            target=device_id,
            result="DETECTED",
            details={"incident_id": inc_id, "title": title, "severity": severity, "category": category},
        )
        return incident

    # --------------------------------------------------------------------------
    # Incident Lifecycle Operations
    # --------------------------------------------------------------------------

    def get_incident(self, incident_id: str) -> Optional[SecurityIncident]:
        with self._lock:
            return self._incidents.get(incident_id)

    def list_incidents(
        self,
        device_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[SecurityIncident]:
        with self._lock:
            res = list(self._incidents.values())
            if device_id:
                res = [i for i in res if i.device_id == device_id]
            if status:
                res = [i for i in res if i.status.value.upper() == status.upper()]
            res.sort(key=lambda x: x.created_at, reverse=True)
            return res[:limit]

    def update_incident_status(
        self,
        incident_id: str,
        new_status: Union[str, IncidentState],
        reason: Optional[str] = None,
        actor: str = "Operator",
    ) -> Tuple[bool, str]:
        """Transitions incident lifecycle state."""
        with self._lock:
            inc = self._incidents.get(incident_id)
            if not inc:
                return False, "INCIDENT_NOT_FOUND"

            status_str = new_status.value if hasattr(new_status, "value") else str(new_status)
            try:
                st_enum = IncidentState(status_str)
            except ValueError:
                return False, f"INVALID_STATUS: {status_str}"

            old_st = inc.status.value
            inc.status = st_enum
            inc.updated_at = time.time()
            if reason:
                inc.description += f" [Update by {actor}: {reason}]"

            self._audit_logger(
                initiator=actor,
                operation="UPDATE_INCIDENT_STATUS",
                target=inc.device_id,
                result="SUCCESS",
                details={"incident_id": incident_id, "from": old_st, "to": status_str, "reason": reason},
            )
            return True, "STATUS_UPDATED"

    def mark_false_positive(
        self,
        incident_id: str,
        reason: str,
        actor: str = "Operator",
    ) -> Tuple[bool, str]:
        """
        Marks an incident as a false positive without deleting evidence.
        Transitions state to DISMISSED, records rationale, and preserves original telemetry.
        """
        with self._lock:
            inc = self._incidents.get(incident_id)
            if not inc:
                return False, "INCIDENT_NOT_FOUND"

            if not reason or not reason.strip():
                return False, "REASON_REQUIRED"

            inc.status = IncidentState.DISMISSED
            inc.false_positive_reason = reason.strip()
            inc.updated_at = time.time()

            self._audit_logger(
                initiator=actor,
                operation="MARK_FALSE_POSITIVE",
                target=inc.device_id,
                result="DISMISSED",
                details={"incident_id": incident_id, "reason": reason},
            )
            return True, "MARKED_FALSE_POSITIVE"

    def resolve_incident(
        self,
        incident_id: str,
        resolution: str,
        actor: str = "Operator",
    ) -> Tuple[bool, str]:
        """Marks an incident as resolved with explanation."""
        with self._lock:
            inc = self._incidents.get(incident_id)
            if not inc:
                return False, "INCIDENT_NOT_FOUND"

            inc.status = IncidentState.RESOLVED
            inc.resolution = resolution or "Mitigation verified by operator."
            inc.resolved_at = time.time()
            inc.updated_at = inc.resolved_at

            self._audit_logger(
                initiator=actor,
                operation="RESOLVE_INCIDENT",
                target=inc.device_id,
                result="RESOLVED",
                details={"incident_id": incident_id, "resolution": resolution},
            )
            return True, "RESOLVED"

    def escalate_incident(
        self,
        incident_id: str,
        reason: str,
        actor: str = "Operator",
    ) -> Tuple[bool, str]:
        """Escalates an incident to senior security oversight."""
        with self._lock:
            inc = self._incidents.get(incident_id)
            if not inc:
                return False, "INCIDENT_NOT_FOUND"

            inc.status = IncidentState.ESCALATED
            inc.severity = "CRITICAL"
            inc.updated_at = time.time()

            self._audit_logger(
                initiator=actor,
                operation="ESCALATE_INCIDENT",
                target=inc.device_id,
                result="ESCALATED",
                details={"incident_id": incident_id, "reason": reason},
            )
            return True, "ESCALATED"

    # --------------------------------------------------------------------------
    # Alert Deduplication & Rate-Limiting Engine
    # --------------------------------------------------------------------------

    def trigger_alert(
        self,
        device_id: str,
        severity: AlertSeverity,
        title: str,
        message: str,
        incident_id: Optional[str] = None,
    ) -> Optional[SecurityAlert]:
        """
        Emits a security alert with deduplication and rate-limiting.
        Suppresses duplicate alerts for the same condition within 5 minutes.
        """
        with self._lock:
            now = time.time()
            sev_str = severity.value if hasattr(severity, "value") else str(severity)
            dedup_key = f"{device_id}:{sev_str}:{title}"

            # 1. Deduplication check
            last_time = self._alert_dedup_cache.get(dedup_key, 0.0)
            if (now - last_time) < self.ALERT_DEDUP_WINDOW_SECONDS:
                # Find matching existing alert and bump count
                matching = next((a for a in self._alerts if a.dedup_key == dedup_key), None)
                if matching:
                    matching.count += 1
                    matching.timestamp = now
                    return matching

            # 2. Rate-limiting check (max 15 alerts / minute)
            self._alert_timestamps = [t for t in self._alert_timestamps if now - t < 60.0]
            if len(self._alert_timestamps) >= self.ALERT_RATE_LIMIT_PER_MINUTE:
                logger.warning(f"Alert rate limit reached ({self.ALERT_RATE_LIMIT_PER_MINUTE}/min). Suppressing alert: {title}")
                return None

            # Bounded capacity check
            if len(self._alerts) >= self.MAX_ALERTS:
                self._alerts.pop(0)

            self._alert_counter += 1
            alert_id = f"alt_{int(now)}_{self._alert_counter:04d}"
            alert = SecurityAlert(
                alert_id=alert_id,
                device_id=device_id,
                timestamp=now,
                severity=severity if isinstance(severity, AlertSeverity) else AlertSeverity(sev_str),
                title=title,
                message=message,
                incident_id=incident_id,
                dedup_key=dedup_key,
                count=1,
            )
            self._alerts.append(alert)
            self._alert_dedup_cache[dedup_key] = now
            self._alert_timestamps.append(now)

            self._audit_logger(
                initiator="Alert Engine",
                operation="DISPATCH_SECURITY_ALERT",
                target=device_id,
                result="ALERT_SENT",
                details={"alert_id": alert_id, "title": title, "severity": sev_str},
            )
            return alert

    def list_alerts(self, limit: int = 50) -> List[SecurityAlert]:
        with self._lock:
            return list(reversed(self._alerts[-limit:]))

    def acknowledge_alert(self, alert_id: str) -> bool:
        with self._lock:
            for alt in self._alerts:
                if alt.alert_id == alert_id:
                    alt.acknowledged = True
                    return True
            return False

    # --------------------------------------------------------------------------
    # Incident Report Generation
    # --------------------------------------------------------------------------

    def generate_incident_report(self, incident_id: str) -> Dict[str, Any]:
        """
        Generates comprehensive, audit-ready security incident report.
        Excludes secrets, private keys, or private application data.
        """
        with self._lock:
            inc = self._incidents.get(incident_id)
            if not inc:
                return {"success": False, "error": "INCIDENT_NOT_FOUND"}

            report_markdown = f"""# SkyShield Incident Report: {inc.incident_id}
**Device ID**: `{inc.device_id}`  
**Title**: {inc.title}  
**Severity**: **{inc.severity}**  
**Category**: {inc.category}  
**Status**: {inc.status.value}  
**Verification Level**: {inc.verification_state.value} (Confidence: {inc.confidence * 100:.1f}%)  
**Created At**: {datetime.fromtimestamp(inc.created_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")}  
**Last Updated**: {datetime.fromtimestamp(inc.updated_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")}  

---

## 1. Description & Threat Context
{inc.description}

## 2. Documented Evidence ({len(inc.evidence)} artifacts)
"""
            for ev in inc.evidence:
                report_markdown += f"- **[{ev.verification_state.value}] {ev.evidence_type}** ({ev.source}): {ev.description}\n"

            report_markdown += f"""
## 3. Recommended Actions
"""
            for act in inc.recommended_actions:
                report_markdown += f"- `{act}`\n"

            if inc.resolution:
                report_markdown += f"""
## 4. Resolution
**Resolved At**: {datetime.fromtimestamp(inc.resolved_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") if inc.resolved_at else "N/A"}  
**Details**: {inc.resolution}
"""
            if inc.false_positive_reason:
                report_markdown += f"""
## 5. False Positive Determination
**Rationale**: {inc.false_positive_reason}
"""

            report_markdown += """
---
*Confidentiality Notice: This report contains security telemetry only. Zero private messages, photos, audio, or user credentials are included.*
"""
            return {
                "success": True,
                "incident": inc.to_dict(),
                "markdown_report": report_markdown,
                "generated_at": time.time(),
                "generated_at_iso": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            }
