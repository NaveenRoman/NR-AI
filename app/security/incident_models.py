"""
NR-AI SkyShield: Security Intelligence & Incident Management Models.
Step 10 Phase 4 — Security Intelligence, Incident Response & Finalization.

Defines deterministic data structures, enumerations, and incident models for:
- Security Incident Lifecycle (DETECTED -> TRIAGING -> VERIFYING -> CONFIRMED -> MITIGATED -> RESOLVED)
- Evidence Verification States (OBSERVED, SUSPECTED, VERIFIED, NOT_VERIFIED, UNKNOWN)
- Multi-signal Event Correlation and Anomaly Attribution
- Public Threat Intelligence & CVE Advisory Records with Provenance & Freshness
- Gated Safe Response Action Proposals requiring human confirmation
- Security Alerting with Deduplication and Rate-limiting
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import time
from typing import Any, Dict, List, Optional, Set, Union

from app.security.models import redact_sensitive_data


# ==============================================================================
# 1. Enums
# ==============================================================================

class IncidentState(str, Enum):
    """Deterministic lifecycle states for security incidents."""
    DETECTED = "DETECTED"
    TRIAGING = "TRIAGING"
    VERIFYING = "VERIFYING"
    CONFIRMED = "CONFIRMED"
    UNCONFIRMED = "UNCONFIRMED"
    MITIGATED = "MITIGATED"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"
    ESCALATED = "ESCALATED"
    STOPPED = "STOPPED"


class EvidenceVerificationState(str, Enum):
    """Epistemic evidence verification levels distinguishing fact from inference."""
    OBSERVED = "OBSERVED"        # Directly observed telemetry or audit fact
    SUSPECTED = "SUSPECTED"      # Statistical or heuristic inference
    VERIFIED = "VERIFIED"        # Multi-factor corroborated finding
    NOT_VERIFIED = "NOT_VERIFIED"# Unsubstantiated or unconfirmed claim
    UNKNOWN = "UNKNOWN"          # Insufficient telemetry to classify


class AlertSeverity(str, Enum):
    """Security alert severity tiers."""
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class SafeResponseAction(str, Enum):
    """Allowed deterministic safe response actions."""
    REQUEST_REAUTH = "REQUEST_REAUTH"
    SUSPEND_DEVICE = "SUSPEND_DEVICE"
    REVOKE_DEVICE = "REVOKE_DEVICE"
    RESET_BASELINE = "RESET_BASELINE"
    REVIEW_PERMISSIONS = "REVIEW_PERMISSIONS"
    REVIEW_NETWORK = "REVIEW_NETWORK"
    ISOLATE_DEVICE = "ISOLATE_DEVICE"
    EMERGENCY_STOP = "EMERGENCY_STOP"


class ActionConfirmationStatus(str, Enum):
    """State machine for gated response action approval."""
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    EXECUTED = "EXECUTED"
    CANCELLED = "CANCELLED"


class SourceAuthority(str, Enum):
    """Authority classifications for external threat intelligence."""
    OFFICIAL_VENDOR = "OFFICIAL_VENDOR"
    NATIONAL_VULNERABILITY_DATABASE = "NATIONAL_VULNERABILITY_DATABASE"
    PUBLIC_SECURITY_ADVISORY = "PUBLIC_SECURITY_ADVISORY"
    COMMUNITY_CONSENSUS = "COMMUNITY_CONSENSUS"


# High impact actions that strictly mandate explicit human approval
HIGH_IMPACT_ACTIONS: Set[SafeResponseAction] = {
    SafeResponseAction.SUSPEND_DEVICE,
    SafeResponseAction.REVOKE_DEVICE,
    SafeResponseAction.ISOLATE_DEVICE,
    SafeResponseAction.EMERGENCY_STOP,
}


# ==============================================================================
# 2. Evidence Model
# ==============================================================================

@dataclass
class SecurityEvidence:
    """Atomic evidence record documenting observed or inferred security telemetry."""
    evidence_id: str
    evidence_type: str
    description: str
    source: str
    timestamp: float
    verification_state: EvidenceVerificationState = EvidenceVerificationState.OBSERVED
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "evidence_type": self.evidence_type,
            "description": self.description,
            "source": self.source,
            "timestamp": self.timestamp,
            "timestamp_iso": datetime.fromtimestamp(self.timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "verification_state": self.verification_state.value if hasattr(self.verification_state, "value") else str(self.verification_state),
            "data": redact_sensitive_data(self.data),
        }


# ==============================================================================
# 3. Threat Intelligence Advisory Model
# ==============================================================================

@dataclass
class ThreatAdvisory:
    """Authentic public vulnerability advisory or bulletin with provenance tracking."""
    advisory_id: str
    title: str
    severity: str
    affected_components: List[str]
    source: str
    url: str
    timestamp: float
    published_date: str
    last_checked: float
    source_authority: SourceAuthority = SourceAuthority.OFFICIAL_VENDOR
    claim_evidence_classification: str = "VERIFIED_PUBLIC_BULLETIN"
    summary: str = ""
    remediation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "advisory_id": self.advisory_id,
            "title": self.title,
            "severity": self.severity,
            "affected_components": list(self.affected_components),
            "source": self.source,
            "url": self.url,
            "timestamp": self.timestamp,
            "published_date": self.published_date,
            "last_checked": self.last_checked,
            "last_checked_iso": datetime.fromtimestamp(self.last_checked, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "source_authority": self.source_authority.value if hasattr(self.source_authority, "value") else str(self.source_authority),
            "claim_evidence_classification": self.claim_evidence_classification,
            "summary": self.summary,
            "remediation": self.remediation,
        }


# ==============================================================================
# 4. Security Incident Model
# ==============================================================================

@dataclass
class SecurityIncident:
    """
    Master incident record aggregating correlated anomalies, events, and evidence.
    Includes deterministic lifecycle state, confidence score, and safe response gating.
    """
    incident_id: str
    device_id: str
    created_at: float
    updated_at: float
    severity: str = "MEDIUM"
    category: str = "UNKNOWN"
    status: IncidentState = IncidentState.DETECTED
    title: str = ""
    description: str = ""
    evidence: List[SecurityEvidence] = field(default_factory=list)
    related_events: List[str] = field(default_factory=list)
    confidence: float = 0.5
    recommended_actions: List[str] = field(default_factory=list)
    verification_state: EvidenceVerificationState = EvidenceVerificationState.OBSERVED
    resolution: Optional[str] = None
    resolved_at: Optional[float] = None
    false_positive_reason: Optional[str] = None
    ai_analysis: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "device_id": self.device_id,
            "created_at": self.created_at,
            "created_at_iso": datetime.fromtimestamp(self.created_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "updated_at": self.updated_at,
            "updated_at_iso": datetime.fromtimestamp(self.updated_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "severity": self.severity,
            "category": self.category,
            "status": self.status.value if hasattr(self.status, "value") else str(self.status),
            "title": self.title,
            "description": self.description,
            "evidence": [e.to_dict() if hasattr(e, "to_dict") else e for e in self.evidence],
            "evidence_count": len(self.evidence),
            "related_events": list(self.related_events),
            "confidence": round(float(self.confidence), 3),
            "recommended_actions": list(self.recommended_actions),
            "verification_state": self.verification_state.value if hasattr(self.verification_state, "value") else str(self.verification_state),
            "resolution": self.resolution,
            "resolved_at": self.resolved_at,
            "resolved_at_iso": datetime.fromtimestamp(self.resolved_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") if self.resolved_at else None,
            "false_positive_reason": self.false_positive_reason,
            "ai_analysis": redact_sensitive_data(self.ai_analysis) if self.ai_analysis else None,
        }


# ==============================================================================
# 5. Security Alert Model (Deduplicated & Rate-Limited)
# ==============================================================================

@dataclass
class SecurityAlert:
    """Bounded, deduplicated alert item representing immediate operator notices."""
    alert_id: str
    device_id: str
    timestamp: float
    severity: AlertSeverity
    title: str
    message: str
    incident_id: Optional[str] = None
    dedup_key: str = ""
    count: int = 1
    acknowledged: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "device_id": self.device_id,
            "timestamp": self.timestamp,
            "timestamp_iso": datetime.fromtimestamp(self.timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "severity": self.severity.value if hasattr(self.severity, "value") else str(self.severity),
            "title": self.title,
            "message": self.message,
            "incident_id": self.incident_id,
            "dedup_key": self.dedup_key,
            "count": self.count,
            "acknowledged": self.acknowledged,
        }


# ==============================================================================
# 6. Safe Response Action Proposal Model
# ==============================================================================

@dataclass
class SafeActionProposal:
    """
    Gated proposal for taking defensive response action.
    Strictly prevents unauthorized autonomous execution of high-impact operations.
    """
    proposal_id: str
    action: str
    device_id: str
    incident_id: Optional[str] = None
    reason: str = ""
    requires_human_confirmation: bool = True
    status: ActionConfirmationStatus = ActionConfirmationStatus.PENDING_CONFIRMATION
    initiated_by: str = "SkyShield Intelligence"
    confirmed_by: Optional[str] = None
    executed_at: Optional[float] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "action": self.action,
            "device_id": self.device_id,
            "incident_id": self.incident_id,
            "reason": self.reason,
            "requires_human_confirmation": self.requires_human_confirmation,
            "status": self.status.value if hasattr(self.status, "value") else str(self.status),
            "initiated_by": self.initiated_by,
            "confirmed_by": self.confirmed_by,
            "executed_at": self.executed_at,
            "executed_at_iso": datetime.fromtimestamp(self.executed_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") if self.executed_at else None,
            "details": redact_sensitive_data(self.details),
        }
