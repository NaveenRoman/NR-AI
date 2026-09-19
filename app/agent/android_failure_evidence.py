"""
NR-AI Android Failure Evidence Collector (Droid Phase 3).

Aggregates, normalizes, and indexes multi-domain engineering evidence from:
SOURCE, BUILD, GRADLE, JUNIT, LINT, LOGCAT, UI_HIERARCHY, SCREENSHOT, RUNTIME_STATE, DEVICE_STATE.
Enforces strict secret scrubbing so that credentials, private tokens, and passwords
are never persisted in evidence records, task states, or logs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
import uuid

from app.agent.android_safety import AndroidSafetyGate, EmergencyStopActiveError
from app.memory.audit_logger import AuditLogger

logger = logging.getLogger("NRAI.AndroidFailureEvidence")


# -----------------------------------------------------------------------------
# Evidence Enums & Data Models
# -----------------------------------------------------------------------------

class EvidenceType(str, Enum):
    SOURCE = "SOURCE"
    BUILD = "BUILD"
    GRADLE = "GRADLE"
    JUNIT = "JUNIT"
    LINT = "LINT"
    LOGCAT = "LOGCAT"
    UI_HIERARCHY = "UI_HIERARCHY"
    SCREENSHOT = "SCREENSHOT"
    RUNTIME_STATE = "RUNTIME_STATE"
    DEVICE_STATE = "DEVICE_STATE"


class EvidenceSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    FATAL = "FATAL"
    CRITICAL = "CRITICAL"


# Secret redaction patterns
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9_]{36,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.]+"),
    re.compile(r"(?i)(password|secret|passwd|token|apikey|api_key)\s*[:=]\s*['\"][^'\"]+['\"]"),
]

def redact_sensitive_evidence(data: Any) -> Tuple[Any, bool]:
    """Recursively redacts secrets from evidence strings and structured data."""
    redacted = False
    if isinstance(data, str):
        cleaned = data
        for pat in SECRET_PATTERNS:
            if pat.search(cleaned):
                cleaned = pat.sub("[REDACTED_SECRET]", cleaned)
                redacted = True
        return cleaned, redacted
    elif isinstance(data, dict):
        new_d = {}
        for k, v in data.items():
            k_low = str(k).lower()
            if any(s in k_low for s in ("password", "secret", "token", "apikey", "api_key", "auth", "key")):
                new_d[k] = "[REDACTED_SECRET]"
                redacted = True
            else:
                val_clean, was_redacted = redact_sensitive_evidence(v)
                new_d[k] = val_clean
                if was_redacted:
                    redacted = True
        return new_d, redacted
    elif isinstance(data, list):
        new_list = []
        for item in data:
            item_clean, was_redacted = redact_sensitive_evidence(item)
            new_list.append(item_clean)
            if was_redacted:
                redacted = True
        return new_list, redacted
    return data, redacted


@dataclass
class EvidenceRecord:
    """Normalized, immutable evidence artifact."""
    evidence_id: str
    type: EvidenceType
    timestamp: float
    project_id: str
    task_id: str
    source: str
    location: Optional[str] = None
    message: str = ""
    severity: EvidenceSeverity = EvidenceSeverity.ERROR
    structured_data: Dict[str, Any] = field(default_factory=dict)
    redaction_status: str = "CLEAN"  # CLEAN or REDACTED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "type": self.type.value if isinstance(self.type, EvidenceType) else str(self.type),
            "timestamp": self.timestamp,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "source": self.source,
            "location": self.location,
            "message": self.message,
            "severity": self.severity.value if isinstance(self.severity, EvidenceSeverity) else str(self.severity),
            "structured_data": self.structured_data,
            "redaction_status": self.redaction_status,
        }


# -----------------------------------------------------------------------------
# Failure Evidence Collector
# -----------------------------------------------------------------------------

class FailureEvidenceCollector:
    """
    Central repository and normalizer for all diagnostic and failure evidence.
    Supports querying by task ID, evidence type, and severity.
    """

    def __init__(
        self,
        safety_gate: Optional[AndroidSafetyGate] = None,
        audit_logger: Optional[AuditLogger] = None,
    ):
        self.safety = safety_gate or AndroidSafetyGate()
        self.audit = audit_logger or AuditLogger()
        self._records: List[EvidenceRecord] = []

    def record_evidence(
        self,
        evidence_type: EvidenceType,
        source: str,
        message: str,
        project_id: str = "nr_android_test",
        task_id: str = "T-DEFAULT",
        location: Optional[str] = None,
        severity: EvidenceSeverity = EvidenceSeverity.ERROR,
        structured_data: Optional[Dict[str, Any]] = None,
    ) -> EvidenceRecord:
        """Creates, sanitizes, and indexes a new evidence record."""
        self.safety.check_emergency_stop()

        # Scrub sensitive details from message and structured data
        clean_msg, msg_redacted = redact_sensitive_evidence(message)
        clean_data, data_redacted = redact_sensitive_evidence(structured_data or {})
        redaction_status = "REDACTED" if (msg_redacted or data_redacted) else "CLEAN"

        rec = EvidenceRecord(
            evidence_id=f"ev_{uuid.uuid4().hex[:8]}",
            type=evidence_type,
            timestamp=time.time(),
            project_id=project_id,
            task_id=task_id,
            source=source,
            location=location,
            message=clean_msg,
            severity=severity,
            structured_data=clean_data,
            redaction_status=redaction_status,
        )
        self._records.append(rec)
        return rec

    def collect_from_logcat(
        self,
        logcat_text: str,
        task_id: str,
        project_id: str = "nr_android_test",
    ) -> List[EvidenceRecord]:
        """Parses raw or filtered logcat into structured evidence records."""
        results: List[EvidenceRecord] = []
        crash_patterns = [
            (re.compile(r"FATAL EXCEPTION:\s*(.*)"), EvidenceSeverity.FATAL),
            (re.compile(r"Process:\s*([a-zA-Z0-9_\.]+),\s*PID:\s*(\d+)"), EvidenceSeverity.ERROR),
            (re.compile(r"java\.lang\.([a-zA-Z0-9_]+Exception|Error):\s*(.*)"), EvidenceSeverity.FATAL),
            (re.compile(r"ANR in\s*([a-zA-Z0-9_\.]+)"), EvidenceSeverity.CRITICAL),
        ]

        lines = logcat_text.splitlines()
        for idx, line in enumerate(lines):
            for pat, sev in crash_patterns:
                m = pat.search(line)
                if m:
                    # Look ahead for stack trace
                    stack_snippet = "\n".join(lines[idx:idx+15])
                    loc = None
                    file_line_m = re.search(r"at\s+([a-zA-Z0-9_\.]+)\.([a-zA-Z0-9_]+)\(([a-zA-Z0-9_]+\.kt|[a-zA-Z0-9_]+\.java):(\d+)\)", stack_snippet)
                    if file_line_m:
                        loc = f"{file_line_m.group(3)}:{file_line_m.group(4)} ({file_line_m.group(1)}.{file_line_m.group(2)})"

                    rec = self.record_evidence(
                        evidence_type=EvidenceType.LOGCAT,
                        source="AndroidRuntime",
                        message=line.strip(),
                        project_id=project_id,
                        task_id=task_id,
                        location=loc,
                        severity=sev,
                        structured_data={"stack_snippet": stack_snippet, "raw_line": line},
                    )
                    results.append(rec)
                    break
        return results

    def collect_from_build_error(
        self,
        error_message: str,
        task_id: str,
        project_id: str = "nr_android_test",
        file_path: Optional[str] = None,
        line: Optional[int] = None,
    ) -> EvidenceRecord:
        """Normalizes a compiler/Gradle build failure into an evidence record."""
        loc = f"{file_path}:{line}" if (file_path and line) else file_path
        return self.record_evidence(
            evidence_type=EvidenceType.BUILD,
            source="GradleCompiler",
            message=error_message,
            project_id=project_id,
            task_id=task_id,
            location=loc,
            severity=EvidenceSeverity.ERROR,
            structured_data={"file_path": file_path, "line": line},
        )

    def collect_from_junit_report(
        self,
        test_case_name: str,
        failure_message: str,
        stack_trace: str,
        task_id: str,
        project_id: str = "nr_android_test",
    ) -> EvidenceRecord:
        """Normalizes a JUnit test failure."""
        return self.record_evidence(
            evidence_type=EvidenceType.JUNIT,
            source=test_case_name,
            message=failure_message,
            project_id=project_id,
            task_id=task_id,
            location=test_case_name,
            severity=EvidenceSeverity.ERROR,
            structured_data={"stack_trace": stack_trace},
        )

    def get_records_by_task(self, task_id: str) -> List[EvidenceRecord]:
        """Retrieves all evidence collected for a specific task."""
        return [r for r in self._records if r.task_id == task_id]

    def get_records_by_type(self, evidence_type: EvidenceType) -> List[EvidenceRecord]:
        """Retrieves all evidence matching a specific type."""
        return [r for r in self._records if r.type == evidence_type]

    def get_records_by_severity(self, severity: EvidenceSeverity) -> List[EvidenceRecord]:
        """Retrieves all evidence matching a minimum severity."""
        return [r for r in self._records if r.severity == severity]

    def clear(self) -> None:
        """Clears in-memory records."""
        self._records.clear()
