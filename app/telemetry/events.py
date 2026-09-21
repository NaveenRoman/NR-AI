"""
NR-AI Structured Telemetry Events.
Defines authoritative event types, event structures, and thread-safe audit logging.
"""

from dataclasses import dataclass, field
from enum import Enum
import json
import logging
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from app.telemetry.sanitizer import sanitize_telemetry_data

logger = logging.getLogger("NRAI.Telemetry")


class TelemetryEventType(str, Enum):
    """Authoritative event types for NR-AI Phase 2."""
    AUTOMATION_CREATED = "AUTOMATION_CREATED"
    AUTOMATION_STARTED = "AUTOMATION_STARTED"
    AUTOMATION_COMPLETED = "AUTOMATION_COMPLETED"
    AUTOMATION_FAILED = "AUTOMATION_FAILED"
    AUTOMATION_CANCELLED = "AUTOMATION_CANCELLED"

    A2A_REQUEST = "A2A_REQUEST"
    A2A_RESPONSE = "A2A_RESPONSE"
    A2A_REJECTION = "A2A_REJECTION"

    MCP_REQUEST = "MCP_REQUEST"
    MCP_RESPONSE = "MCP_RESPONSE"
    MCP_REJECTION = "MCP_REJECTION"

    CONNECTOR_ACCESS = "CONNECTOR_ACCESS"
    CONNECTOR_REJECTION = "CONNECTOR_REJECTION"

    CONTEXT_COMPACTION = "CONTEXT_COMPACTION"
    CHECKPOINT_SAVE = "CHECKPOINT_SAVE"
    CHECKPOINT_RESTORE = "CHECKPOINT_RESTORE"


@dataclass
class TelemetryEvent:
    """A sanitized structured telemetry record."""
    event_id: str = field(default_factory=lambda: f"ev_{uuid.uuid4().hex[:12]}")
    event_type: TelemetryEventType = TelemetryEventType.AUTOMATION_CREATED
    actor_id: str = "system"
    target_id: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "actor_id": self.actor_id,
            "target_id": self.target_id,
            "data": self.data,
            "timestamp": self.timestamp,
        }


class TelemetryLogger:
    """
    Thread-safe structured event recorder with automated data sanitization.
    """

    def __init__(self, max_records: int = 2000):
        self._records: List[TelemetryEvent] = []
        self._lock = threading.RLock()
        self.max_records = max_records

    def record_event(
        self,
        event_type: TelemetryEventType,
        actor_id: str,
        target_id: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> TelemetryEvent:
        """
        Record a sanitized structured telemetry event.
        """
        cleaned_data = sanitize_telemetry_data(data or {})
        event = TelemetryEvent(
            event_type=event_type,
            actor_id=actor_id,
            target_id=target_id,
            data=cleaned_data,
        )

        with self._lock:
            self._records.append(event)
            if len(self._records) > self.max_records:
                self._records.pop(0)

        logger.debug(f"[Telemetry] {event.event_type.value} by {event.actor_id} -> {event.target_id}")
        return event

    def get_events(
        self,
        event_type: Optional[TelemetryEventType] = None,
        limit: int = 50,
    ) -> List[TelemetryEvent]:
        """Fetch recent events with optional type filtering."""
        with self._lock:
            if event_type:
                filtered = [e for e in self._records if e.event_type == event_type]
            else:
                filtered = list(self._records)
            return filtered[-limit:]


# Global singleton logger
global_telemetry_logger = TelemetryLogger()
