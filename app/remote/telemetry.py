"""
NR-AI Remote Telemetry Protocol & Event Engine.
Step 10 Phase 2 — Remote Telemetry & Screen / Frame Streaming Protocol.
"""

from dataclasses import asdict, dataclass, field
from enum import Enum
import json
import secrets
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from app.remote.config import (
    MAX_TELEMETRY_EVENT_BYTES,
    MAX_TELEMETRY_PAYLOAD_BYTES,
)


class TelemetryEventType(str, Enum):
    STATUS_UPDATE = "STATUS_UPDATE"
    AGENT_STATE = "AGENT_STATE"
    WORKFLOW_STATE = "WORKFLOW_STATE"
    TASK_PROGRESS = "TASK_PROGRESS"
    ERROR_EVENT = "ERROR_EVENT"
    SECURITY_EVENT = "SECURITY_EVENT"
    CONNECTION_EVENT = "CONNECTION_EVENT"
    STREAM_STARTED = "STREAM_STARTED"
    STREAM_STOPPED = "STREAM_STOPPED"
    STREAM_PAUSED = "STREAM_PAUSED"
    STREAM_RESUMED = "STREAM_RESUMED"
    FRAME_AVAILABLE = "FRAME_AVAILABLE"


class VoiceTelemetryEventType(str, Enum):
    VOICE_RECEIVED = "VOICE_RECEIVED"
    VOICE_VALIDATED = "VOICE_VALIDATED"
    VOICE_TRANSCRIBING = "VOICE_TRANSCRIBING"
    VOICE_TRANSCRIBED = "VOICE_TRANSCRIBED"
    VOICE_INTENT_DETECTED = "VOICE_INTENT_DETECTED"
    VOICE_CONFIRMATION_REQUIRED = "VOICE_CONFIRMATION_REQUIRED"
    VOICE_EXECUTED = "VOICE_EXECUTED"
    VOICE_COMPLETED = "VOICE_COMPLETED"
    VOICE_FAILED = "VOICE_FAILED"
    VOICE_CANCELLED = "VOICE_CANCELLED"


@dataclass
class TelemetryEvent:
    event_id: str
    device_id: str
    session_id: str
    event_type: str
    timestamp: float
    sequence_number: int
    payload: Dict[str, Any] = field(default_factory=dict)
    signature: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "device_id": self.device_id,
            "session_id": self.session_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "sequence_number": self.sequence_number,
            "payload": self.payload,
            "signature": self.signature,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


def parse_and_validate_telemetry_event(raw_data: str) -> Tuple[bool, Optional[TelemetryEvent], Optional[str]]:
    """
    Parse and validate an incoming or outgoing telemetry event against bounds and schema.
    Returns (is_valid, event_obj, error_reason).
    """
    if raw_data is None:
        return False, None, "EVENT_BODY_NULL"

    raw_bytes = raw_data.encode("utf-8")
    if len(raw_bytes) > MAX_TELEMETRY_EVENT_BYTES:
        return False, None, f"EVENT_TOO_LARGE: {len(raw_bytes)} bytes exceeds limit {MAX_TELEMETRY_EVENT_BYTES}"

    try:
        data = json.loads(raw_data)
    except Exception as e:
        return False, None, f"MALFORMED_JSON: {e}"

    if not isinstance(data, dict):
        return False, None, "EVENT_MUST_BE_OBJECT"

    required_fields = [
        "event_id",
        "device_id",
        "session_id",
        "event_type",
        "timestamp",
        "sequence_number",
    ]
    for rf in required_fields:
        if rf not in data:
            return False, None, f"MISSING_REQUIRED_FIELD: {rf}"

    payload = data.get("payload", {})
    if not isinstance(payload, dict):
        return False, None, "PAYLOAD_MUST_BE_OBJECT"

    payload_json = json.dumps(payload)
    if len(payload_json.encode("utf-8")) > MAX_TELEMETRY_PAYLOAD_BYTES:
        return False, None, f"PAYLOAD_TOO_LARGE: exceeds limit {MAX_TELEMETRY_PAYLOAD_BYTES}"

    # Validate event_type against known types
    event_type_str = str(data["event_type"]).strip()
    valid_types = {e.value for e in TelemetryEventType} | {e.value for e in VoiceTelemetryEventType}
    if event_type_str not in valid_types:
        return False, None, f"UNKNOWN_EVENT_TYPE: '{event_type_str}'"

    try:
        ts = float(data["timestamp"])
    except (ValueError, TypeError):
        return False, None, "INVALID_TIMESTAMP_FORMAT"

    try:
        seq = int(data["sequence_number"])
        if seq < 0:
            return False, None, "SEQUENCE_NUMBER_MUST_BE_NON_NEGATIVE"
    except (ValueError, TypeError):
        return False, None, "INVALID_SEQUENCE_NUMBER_FORMAT"

    event = TelemetryEvent(
        event_id=str(data["event_id"]).strip(),
        device_id=str(data["device_id"]).strip(),
        session_id=str(data["session_id"]).strip(),
        event_type=event_type_str,
        timestamp=ts,
        sequence_number=seq,
        payload=payload,
        signature=str(data.get("signature", "")).strip() or None,
    )

    if not event.event_id:
        return False, None, "EMPTY_EVENT_ID"
    if not event.device_id:
        return False, None, "EMPTY_DEVICE_ID"
    if not event.session_id:
        return False, None, "EMPTY_SESSION_ID"

    return True, event, None


def validate_telemetry_event(data_or_json: Any) -> Tuple[bool, Optional[str], Optional[TelemetryEvent]]:
    """
    Validate a telemetry event dictionary or JSON string against size, schema and type limits.
    Returns (is_valid, error_reason, event_obj).
    """
    if isinstance(data_or_json, dict):
        try:
            raw_data = json.dumps(data_or_json)
        except Exception as e:
            return False, f"MALFORMED_DICT: {e}", None
    elif isinstance(data_or_json, str):
        raw_data = data_or_json
    elif data_or_json is None:
        return False, "EVENT_BODY_NULL", None
    else:
        return False, "INVALID_DATA_TYPE", None

    valid, event, err = parse_and_validate_telemetry_event(raw_data)
    return valid, err, event


class TelemetryDispatcher:
    """
    Thread-safe telemetry event hub that publishes events, manages sequence monotonicity,
    and maintains a bounded historical ring buffer per session.
    """

    def __init__(self, max_buffer_per_session: int = 100):
        self.max_buffer = max_buffer_per_session
        self._session_buffers: Dict[str, List[TelemetryEvent]] = {}
        self._session_sequences: Dict[str, int] = {}
        self._lock = threading.Lock()

    def create_and_publish_event(
        self,
        device_id: str,
        session_id: str,
        event_type: TelemetryEventType,
        payload: Optional[Dict[str, Any]] = None,
    ) -> TelemetryEvent:
        """
        Build a bounded telemetry event, assign strictly monotonic sequence number,
        and publish into session buffer.
        """
        clean_payload = payload if payload is not None else {}
        payload_bytes = len(json.dumps(clean_payload).encode("utf-8"))
        if payload_bytes > MAX_TELEMETRY_PAYLOAD_BYTES:
            raise ValueError(
                f"Telemetry payload exceeds limit: {payload_bytes} > {MAX_TELEMETRY_PAYLOAD_BYTES}"
            )

        with self._lock:
            current_seq = self._session_sequences.get(session_id, 0) + 1
            self._session_sequences[session_id] = current_seq

            event = TelemetryEvent(
                event_id=f"EVT-{secrets.token_hex(8).upper()}",
                device_id=device_id,
                session_id=session_id,
                event_type=event_type.value if hasattr(event_type, "value") else str(event_type),
                timestamp=time.time(),
                sequence_number=current_seq,
                payload=clean_payload,
            )

            buf = self._session_buffers.setdefault(session_id, [])
            buf.append(event)
            if len(buf) > self.max_buffer:
                buf.pop(0)

            return event

    def emit_event(
        self,
        device_id: str,
        session_id: str,
        event_type: TelemetryEventType,
        payload: Optional[Dict[str, Any]] = None,
    ) -> TelemetryEvent:
        """Alias for create_and_publish_event."""
        return self.create_and_publish_event(device_id, session_id, event_type, payload)

    def get_events(
        self,
        session_id: str,
        since_sequence: int = 0,
        limit: int = 50,
    ) -> List[TelemetryEvent]:
        """
        Retrieve events for a session with sequence number > since_sequence.
        """
        with self._lock:
            buf = self._session_buffers.get(session_id, [])
            filtered = [e for e in buf if e.sequence_number > since_sequence]
            return filtered[:limit]

    def get_latest_sequence(self, session_id: str) -> int:
        with self._lock:
            return self._session_sequences.get(session_id, 0)

    def clear_session(self, session_id: str) -> None:
        with self._lock:
            self._session_buffers.pop(session_id, None)
            self._session_sequences.pop(session_id, None)

TelemetryHub = TelemetryDispatcher
