"""
NR-AI SkyShield: Permission Auditor.
Step 10 Phase 1 — Security Agent Foundation.

Provides deterministic, read-only permission auditing for enrolled devices
and applications (WhatsApp, Instagram, Snapchat) against zero-trust baselines.
Identifies unusual or excessive permissions and generates structured security findings.
Strictly read-only: does not modify device permissions automatically.
"""

from typing import Any, Dict, List, Optional, Set
from app.security.models import (
    ApplicationSecurityCard,
    EvidenceConfidence,
    FindingCategory,
    PermissionName,
    PermissionStatus,
    SecurityFinding,
    SecurityPermission,
    SecuritySeverity,
)


class PermissionAuditor:
    """
    Deterministic permission auditing engine for SkyShield.
    Evaluates granted privileges against expected functional baselines.
    """

    # Baseline permissions that are expected and common for specific application categories
    EXPECTED_APP_PERMISSIONS: Dict[str, Set[str]] = {
        "WhatsApp": {
            PermissionName.CAMERA.value,
            PermissionName.MICROPHONE.value,
            PermissionName.CONTACTS.value,
            PermissionName.STORAGE.value,
            PermissionName.NOTIFICATIONS.value,
            PermissionName.PHONE.value,
        },
        "Instagram": {
            PermissionName.CAMERA.value,
            PermissionName.MICROPHONE.value,
            PermissionName.STORAGE.value,
            PermissionName.NOTIFICATIONS.value,
            PermissionName.LOCATION.value,
        },
        "Snapchat": {
            PermissionName.CAMERA.value,
            PermissionName.MICROPHONE.value,
            PermissionName.STORAGE.value,
            PermissionName.NOTIFICATIONS.value,
            PermissionName.LOCATION.value,
            PermissionName.CONTACTS.value,
        },
    }

    # High-risk permissions requiring strict scrutiny
    HIGH_RISK_PERMISSIONS: Set[str] = {
        PermissionName.SMS.value,
        PermissionName.LOCATION.value,
        PermissionName.CONTACTS.value,
        PermissionName.MICROPHONE.value,
        PermissionName.CAMERA.value,
    }

    def audit_permissions(
        self, permissions: Dict[Any, PermissionStatus]
    ) -> List[SecurityFinding]:
        """Audits a dictionary of permissions (keys can be PermissionName or str)."""
        norm_perms = {}
        for k, v in permissions.items():
            k_str = k.value if isinstance(k, PermissionName) else str(k)
            norm_perms[k_str] = v

        findings = self.audit_device_permissions(norm_perms)

        for k_str, v in norm_perms.items():
            if "SMS" in k_str and v == PermissionStatus.GRANTED:
                sms_finding = next((f for f in findings if "SMS" in f.title or "SMS" in f.description), None)
                if sms_finding:
                    sms_finding.severity = SecuritySeverity.HIGH
                else:
                    findings.append(
                        SecurityFinding(
                            finding_id="find-perm-sms-high",
                            category=FindingCategory.UNUSUAL_PERMISSION,
                            severity=SecuritySeverity.HIGH,
                            title="High-Risk SMS Permission Granted",
                            description="SMS access is granted on the device. Restrict to default messaging app.",
                            evidence=f"Permission '{k_str}' status is GRANTED.",
                            confidence=EvidenceConfidence.OBSERVED,
                            remediation="Audit apps with SMS permission.",
                        )
                    )
        return findings

    def audit_device_permissions(
        self, permissions: Dict[str, PermissionStatus]
    ) -> List[SecurityFinding]:
        """
        Audits overall device permission posture.
        Flags high-risk permissions that are globally unrestricted.
        """
        findings: List[SecurityFinding] = []

        # Example check: SMS permission granted globally
        sms_status = permissions.get(PermissionName.SMS.value, PermissionStatus.UNKNOWN)
        if sms_status == PermissionStatus.GRANTED:
            findings.append(
                SecurityFinding(
                    finding_id=f"find-perm-sms-{len(findings) + 1}",
                    category=FindingCategory.UNUSUAL_PERMISSION,
                    severity=SecuritySeverity.MEDIUM,
                    title="SMS Permission Globally Granted",
                    description="SMS permission is enabled on the device. Verify only trusted telephony apps have access.",
                    evidence="Permission 'SMS' status is GRANTED in system policy.",
                    confidence=EvidenceConfidence.OBSERVED,
                    remediation="Audit apps with SMS permission and restrict to the default messaging handler.",
                )
            )

        # Check for unverified/unknown permission states
        for perm_name, status in permissions.items():
            if status == PermissionStatus.UNKNOWN:
                findings.append(
                    SecurityFinding(
                        finding_id=f"find-perm-unk-{perm_name.lower()}",
                        category=FindingCategory.SUSPICIOUS_CONFIGURATION,
                        severity=SecuritySeverity.LOW,
                        title=f"Unverified Permission State: {perm_name}",
                        description=f"Permission status for '{perm_name}' could not be queried via platform API.",
                        evidence=f"Permission '{perm_name}' returned status UNKNOWN.",
                        confidence=EvidenceConfidence.UNVERIFIED,
                        remediation="Reconnect device diagnostics to refresh platform permission manifest.",
                    )
                )

        return findings

    def audit_application_permissions(
        self, app_card: ApplicationSecurityCard
    ) -> List[SecurityFinding]:
        """
        Audits an individual application's declared/granted permissions.
        Flags permissions granted beyond the expected functional profile.
        """
        findings: List[SecurityFinding] = []
        app_name = app_card.app_name
        expected = self.EXPECTED_APP_PERMISSIONS.get(app_name, set())

        for perm_name, status_str in app_card.permission_status.items():
            if status_str != PermissionStatus.GRANTED.value:
                continue

            # Flag permissions not part of standard functional profile
            if perm_name not in expected:
                severity = (
                    SecuritySeverity.HIGH
                    if perm_name in self.HIGH_RISK_PERMISSIONS
                    else SecuritySeverity.MEDIUM
                )
                findings.append(
                    SecurityFinding(
                        finding_id=f"find-app-{app_name.lower()}-{perm_name.lower()}",
                        category=FindingCategory.UNUSUAL_PERMISSION,
                        severity=severity,
                        title=f"Unusual Permission for {app_name}: {perm_name}",
                        description=(
                            f"Application '{app_name}' holds granted permission '{perm_name}', "
                            f"which is outside standard baseline requirements."
                        ),
                        evidence=f"Application manifest shows '{perm_name}: GRANTED'.",
                        confidence=EvidenceConfidence.OBSERVED,
                        remediation=f"Inspect '{app_name}' app info in system settings and revoke '{perm_name}'.",
                    )
                )

        return findings

    def evaluate_risk_state(self, findings: List[SecurityFinding]) -> str:
        """Computes aggregate risk label based on active findings."""
        if any(f.severity in (SecuritySeverity.HIGH, SecuritySeverity.CRITICAL) for f in findings):
            return "HIGH_RISK"
        if any(f.severity == SecuritySeverity.MEDIUM for f in findings):
            return "ATTENTION"
        return "SECURE"
