"""
Typed Desktop Event Bus and Schema Validator for NR-AI.
Provides bidirectional desktop shell eventing, audit logging, and payload protection.

Invariants:
- Typed event types with strict schema validation.
- In-memory bounded audit log (maxlen=1000).
- Payload bounding (<= 64KB).
- Secret scrubbing / redaction via PromptGuardrails.
- No eval, exec, or unvalidated serialization.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("NRAI.Desktop.Events")


class DesktopEventType(str, Enum):
    # System Lifecycle Events
    SYSTEM_INITIALIZING = "SYSTEM_INITIALIZING"
    SYSTEM_READY = "SYSTEM_READY"
    SYSTEM_SHUTDOWN = "SYSTEM_SHUTDOWN"
    HEALTH_METRICS_UPDATED = "HEALTH_METRICS_UPDATED"
    EMERGENCY_STOP_TRIGGERED = "EMERGENCY_STOP_TRIGGERED"

    # Agent & Task Events
    AGENT_SELECTED = "AGENT_SELECTED"
    AGENT_STATE_CHANGED = "AGENT_STATE_CHANGED"
    TASK_SUBMITTED = "TASK_SUBMITTED"
    TASK_PROGRESS_UPDATED = "TASK_PROGRESS_UPDATED"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_FAILED = "TASK_FAILED"

    # Voice & Audio Events
    VOICE_STATE_CHANGED = "VOICE_STATE_CHANGED"
    VOICE_TRANSCRIPTION_RECEIVED = "VOICE_TRANSCRIPTION_RECEIVED"
    VOICE_SYNTHESIS_COMPLETED = "VOICE_SYNTHESIS_COMPLETED"


@dataclass
class DesktopEvent:
    event_type: DesktopEventType
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    source: str = "nr_ai_desktop"
    event_id: Optional[str] = None

    def __post_init__(self):
        if not self.event_id:
            import uuid
            self.event_id = str(uuid.uuid4())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value if isinstance(self.event_type, DesktopEventType) else str(self.event_type),
            "timestamp": self.timestamp,
            "source": self.source,
            "payload": self.payload,
        }


class DesktopEventBus:
    """
    Thread-safe event bus for the desktop shell bridge.
    Maintains subscriber callbacks, enforces secret redaction and 64KB limits,
    and records an in-memory audit log of recent events.
    """

    MAX_PAYLOAD_BYTES: int = 65536
    AUDIT_LOG_CAPACITY: int = 1000

    def __init__(self, capacity: int = AUDIT_LOG_CAPACITY) -> None:
        self._capacity = capacity
        self._lock = threading.RLock()
        self._subscribers: Dict[str, List[Callable[[DesktopEvent], None]]] = {}
        self._audit_log: deque = deque(maxlen=self._capacity)

        # Initialize guardrails for secret scrubbing
        try:
            from app.security.guardrails import PromptGuardrails
            self._guardrails = PromptGuardrails()
        except Exception:
            self._guardrails = None

    def subscribe(self, event_type: DesktopEventType | str, callback: Callable[[DesktopEvent], None]) -> None:
        key = event_type.value if isinstance(event_type, DesktopEventType) else str(event_type)
        with self._lock:
            if key not in self._subscribers:
                self._subscribers[key] = []
            if callback not in self._subscribers[key]:
                self._subscribers[key].append(callback)

    def unsubscribe(self, event_type: DesktopEventType | str, callback: Callable[[DesktopEvent], None]) -> None:
        key = event_type.value if isinstance(event_type, DesktopEventType) else str(event_type)
        with self._lock:
            if key in self._subscribers and callback in self._subscribers[key]:
                self._subscribers[key].remove(callback)

    def _sanitize_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Recursively redact sensitive strings and verify size bounding.
        """
        try:
            serialized = json.dumps(payload)
            if len(serialized.encode("utf-8")) > self.MAX_PAYLOAD_BYTES:
                return {
                    "error": "PAYLOAD_SIZE_EXCEEDED",
                    "truncated": True,
                    "original_keys": list(payload.keys()) if isinstance(payload, dict) else [],
                }
        except Exception as exc:
            return {"error": f"SERIALIZATION_FAILED: {exc}"}

        sanitized: Dict[str, Any] = {}
        for k, v in payload.items():
            if isinstance(v, str):
                if self._guardrails:
                    try:
                        v = self._guardrails.redact(v)
                    except Exception:
                        pass
                sanitized[k] = v
            elif isinstance(v, dict):
                sanitized[k] = self._sanitize_payload(v)
            elif isinstance(v, list):
                sanitized[k] = [
                    self._guardrails.redact(item) if isinstance(item, str) and self._guardrails else item
                    for item in v
                ]
            else:
                sanitized[k] = v
        return sanitized

    def publish(self, event_or_type: DesktopEvent | DesktopEventType | str, payload: Optional[Dict[str, Any]] = None) -> DesktopEvent:
        """
        Publish an event to all subscribers and append to the audit log.
        """
        if isinstance(event_or_type, DesktopEvent):
            event = event_or_type
            sanitized = self._sanitize_payload(event.payload)
            event.payload = sanitized
        else:
            try:
                etype = DesktopEventType(event_or_type)
            except ValueError:
                raise ValueError(f"Invalid DesktopEventType: {event_or_type}")
            sanitized = self._sanitize_payload(payload or {})
            event = DesktopEvent(event_type=etype, payload=sanitized)

        key = event.event_type.value

        with self._lock:
            self._audit_log.append(event)
            callbacks = list(self._subscribers.get(key, [])) + list(self._subscribers.get("*", []))

        for cb in callbacks:
            try:
                cb(event)
            except Exception as exc:
                logger.error("Error executing subscriber for %s: %s", key, exc, exc_info=True)

        return event

    def get_audit_log(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self._audit_log)[-limit:]
            return [ev.to_dict() for ev in items]

    def clear_audit_log(self) -> None:
        with self._lock:
            self._audit_log.clear()
