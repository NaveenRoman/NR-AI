"""
NR-AI Audio Request Protocol & Validation Engine.
Step 10 Phase 3 — Phone Voice Command & Secure Audio Pipeline.
"""

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import secrets
import time
from typing import Any, Dict, Optional, Set, Tuple

from app.remote.config import (
    AUDIO_CHECKSUM_MISMATCH,
    AUDIO_TOO_LARGE,
    AUDIO_TOO_LONG,
    AUDIO_TOO_SHORT,
    EMPTY_AUDIO,
    INVALID_AUDIO_REQUEST,
    INVALID_CHANNEL_COUNT,
    INVALID_SAMPLE_RATE,
    MAX_AUDIO_BYTES,
    MAX_AUDIO_CHANNELS,
    MAX_AUDIO_DURATION_SECONDS,
    MAX_AUDIO_SAMPLE_RATE,
    MIN_AUDIO_CHANNELS,
    MIN_AUDIO_DURATION_SECONDS,
    MIN_AUDIO_SAMPLE_RATE,
    UNSUPPORTED_AUDIO_FORMAT,
)


class AudioFormat(str, Enum):
    WAV = "WAV"
    PCM = "PCM"
    OPUS = "OPUS"
    OGG = "OGG"
    MOCK_AUDIO = "MOCK_AUDIO"


ALLOWED_AUDIO_FORMATS: Set[str] = {
    AudioFormat.WAV.value,
    AudioFormat.PCM.value,
    AudioFormat.OPUS.value,
    AudioFormat.OGG.value,
    AudioFormat.MOCK_AUDIO.value,
}


@dataclass
class AudioRequest:
    request_id: str
    session_id: str
    device_id: str
    timestamp: float
    nonce: str
    audio_format: str
    sample_rate: int
    channels: int
    duration_ms: float
    payload_size: int
    checksum: str
    audio_bytes: bytes
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_metadata_dict(self) -> Dict[str, Any]:
        """
        Safe dictionary representation containing strictly audio metadata.
        Raw audio bytes are NEVER included in this dictionary (safe for audit logs).
        """
        return {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "device_id": self.device_id,
            "timestamp": self.timestamp,
            "nonce": self.nonce,
            "audio_format": self.audio_format,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "duration_ms": self.duration_ms,
            "payload_size": self.payload_size,
            "checksum": self.checksum,
            "metadata": self.metadata,
        }

    def to_dict(self) -> Dict[str, Any]:
        """Full dictionary with hex payload for serialization."""
        d = self.to_metadata_dict()
        d["payload_hex"] = self.audio_bytes.hex()
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


@dataclass
class VoiceResponse:
    request_id: str
    session_id: str
    status: str  # "SUCCESS", "CONFIRMATION_REQUIRED", "DENIED", "ERROR", "STOPPED"
    transcript: str
    intent_type: str
    result_text: str
    error_code: Optional[str] = None
    requires_confirmation: bool = False
    confirmation_id: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "status": self.status,
            "transcript": self.transcript,
            "intent_type": self.intent_type,
            "result_text": self.result_text,
            "error_code": self.error_code,
            "requires_confirmation": self.requires_confirmation,
            "confirmation_id": self.confirmation_id,
            "timestamp": self.timestamp,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


def create_audio_request(
    session_id: str,
    device_id: str,
    audio_format: AudioFormat,
    sample_rate: int,
    channels: int,
    duration_ms: float,
    audio_bytes: bytes,
    nonce: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    timestamp: Optional[float] = None,
) -> AudioRequest:
    """Construct and compute checksum for a validated AudioRequest."""
    if not isinstance(audio_bytes, (bytes, bytearray)):
        raise TypeError("Audio payload must be bytes.")

    raw = bytes(audio_bytes)
    payload_size = len(raw)
    checksum = hashlib.sha256(raw).hexdigest()

    fmt_str = audio_format.value if isinstance(audio_format, AudioFormat) else str(audio_format).upper()

    return AudioRequest(
        request_id=f"AUD-{secrets.token_hex(8).upper()}",
        session_id=session_id,
        device_id=device_id,
        timestamp=timestamp if timestamp is not None else time.time(),
        nonce=nonce or secrets.token_hex(16),
        audio_format=fmt_str,
        sample_rate=sample_rate,
        channels=channels,
        duration_ms=duration_ms,
        payload_size=payload_size,
        checksum=checksum,
        audio_bytes=raw,
        metadata=metadata or {},
    )


def validate_audio_request(req: AudioRequest) -> Tuple[bool, str]:
    """
    Perform deterministic validation on an AudioRequest against bounded invariants.
    Returns (is_valid, decision_reason).
    """
    if not isinstance(req, AudioRequest):
        return False, INVALID_AUDIO_REQUEST

    if not req.request_id or not req.session_id or not req.device_id:
        return False, INVALID_AUDIO_REQUEST

    # Check payload presence and size
    if req.payload_size == 0 or len(req.audio_bytes) == 0:
        return False, EMPTY_AUDIO

    if req.payload_size > MAX_AUDIO_BYTES or len(req.audio_bytes) > MAX_AUDIO_BYTES:
        return False, AUDIO_TOO_LARGE

    # Check duration bounds
    duration_sec = req.duration_ms / 1000.0
    if duration_sec < MIN_AUDIO_DURATION_SECONDS:
        return False, AUDIO_TOO_SHORT
    if duration_sec > MAX_AUDIO_DURATION_SECONDS:
        return False, AUDIO_TOO_LONG

    # Check sample rate bounds
    if req.sample_rate < MIN_AUDIO_SAMPLE_RATE or req.sample_rate > MAX_AUDIO_SAMPLE_RATE:
        return False, INVALID_SAMPLE_RATE

    # Check channels
    if req.channels < MIN_AUDIO_CHANNELS or req.channels > MAX_AUDIO_CHANNELS:
        return False, INVALID_CHANNEL_COUNT

    # Check format allowlist
    fmt = str(req.audio_format).upper()
    if fmt not in ALLOWED_AUDIO_FORMATS:
        return False, UNSUPPORTED_AUDIO_FORMAT

    # Verify SHA-256 payload checksum matches audio bytes
    computed = hashlib.sha256(req.audio_bytes).hexdigest()
    if not secrets.compare_digest(computed.lower(), req.checksum.lower()):
        return False, AUDIO_CHECKSUM_MISMATCH

    return True, "AUDIO_REQUEST_VALID"
