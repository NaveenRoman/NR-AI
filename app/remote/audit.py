"""
NR-AI Security Audit Logging & Secret Redaction Engine.
Step 10 Phase 1 — Secure Phone <-> PC Communication Foundation.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import logging
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger("NRAI.SecurityAudit")

# Sensitive key names that must ALWAYS be redacted from audit logs
SENSITIVE_KEY_SUBSTRINGS = (
    "secret",
    "token",
    "key",
    "password",
    "signature",
    "code",
    "otp",
    "credential",
    "iv_hex",
    "ciphertext",
    "tag_hex",
)


def redact_sensitive_data(obj: Any) -> Any:
    """
    Recursively redact any dictionary fields matching sensitive security keys.
    """
    if isinstance(obj, dict):
        cleaned = {}
        for k, v in obj.items():
            k_lower = str(k).lower()
            if any(s in k_lower for s in SENSITIVE_KEY_SUBSTRINGS):
                cleaned[k] = "[REDACTED]"
            elif isinstance(v, (dict, list)):
                cleaned[k] = redact_sensitive_data(v)
            else:
                cleaned[k] = v
        return cleaned
    elif isinstance(obj, list):
        return [redact_sensitive_data(item) for item in obj]
    return obj


@dataclass
class AuditRecord:
    timestamp: str
    event_type: str
    device_id: Optional[str]
    session_id: Optional[str]
    action: Optional[str]
    scope: Optional[str]
    result: str  # "SUCCESS", "DENIED", "ERROR", "BLOCKED"
    reason: Optional[str] = None
    client_ip: Optional[str] = None
    safe_metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "device_id": self.device_id,
            "session_id": self.session_id,
            "action": self.action,
            "scope": self.scope,
            "result": self.result,
            "reason": self.reason,
            "client_ip": self.client_ip,
            "safe_metadata": self.safe_metadata,
        }


class SecurityAuditLogger:
    """
    Thread-safe security audit logger with automatic secret redaction.
    """

    def __init__(self, max_buffer_records: int = 500):
        self.max_records = max_buffer_records
        self._records: List[AuditRecord] = []
        self._lock = threading.Lock()

    def log_event(
        self,
        event_type: str,
        result: str,
        device_id: Optional[str] = None,
        session_id: Optional[str] = None,
        action: Optional[str] = None,
        scope: Optional[str] = None,
        reason: Optional[str] = None,
        client_ip: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AuditRecord:
        """
        Record a security-sensitive event with strict redaction of credentials.
        """
        ts = datetime.now(timezone.utc).isoformat()
        clean_metadata = redact_sensitive_data(metadata or {})

        rec = AuditRecord(
            timestamp=ts,
            event_type=str(event_type),
            device_id=str(device_id) if device_id else None,
            session_id=str(session_id) if session_id else None,
            action=str(action) if action else None,
            scope=str(scope) if scope else None,
            result=str(result),
            reason=str(reason) if reason else None,
            client_ip=str(client_ip) if client_ip else None,
            safe_metadata=clean_metadata,
        )

        with self._lock:
            self._records.append(rec)
            if len(self._records) > self.max_records:
                self._records.pop(0)

        # Emit to standard logging at appropriate level
        log_msg = (
            f"[{rec.result}] event={rec.event_type} dev={rec.device_id} "
            f"act={rec.action} reason={rec.reason}"
        )
        if rec.result in ("DENIED", "ERROR", "BLOCKED"):
            logger.warning(log_msg)
        else:
            logger.info(log_msg)

        return rec

    def get_records(
        self,
        device_id: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[AuditRecord]:
        with self._lock:
            filtered = self._records
            if device_id:
                filtered = [r for r in filtered if r.device_id == device_id]
            if event_type:
                filtered = [r for r in filtered if r.event_type == event_type]
            return list(filtered[-limit:])

    def clear(self) -> None:
        with self._lock:
            self._records.clear()
