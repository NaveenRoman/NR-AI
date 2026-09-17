"""
NR-AI Structured Stream Frame Protocol & Image Validation.
Step 10 Phase 2 — Remote Telemetry & Screen / Frame Streaming Protocol.
"""

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import secrets
import time
from typing import Any, Dict, Optional, Set, Tuple

from app.remote.config import (
    MAX_FRAME_BYTES,
    MAX_FRAME_HEIGHT,
    MAX_FRAME_WIDTH,
)


class FrameEncoding(str, Enum):
    JPEG = "JPEG"
    PNG = "PNG"
    WEBP = "WEBP"
    MOCK_RGB = "MOCK_RGB"


ALLOWED_FRAME_ENCODINGS: Set[str] = {
    FrameEncoding.JPEG.value,
    FrameEncoding.PNG.value,
    FrameEncoding.WEBP.value,
    FrameEncoding.MOCK_RGB.value,
}


@dataclass
class StreamFrame:
    frame_id: str
    stream_id: str
    sequence_number: int
    timestamp: float
    width: int
    height: int
    encoding: str
    compressed_size: int
    payload_bytes: bytes
    checksum: str  # SHA-256 hex digest of payload_bytes

    def to_metadata_dict(self) -> Dict[str, Any]:
        """
        Safe dictionary representation containing ONLY frame metadata.
        Zero pixel/image payload bytes are included (for audit logging and status).
        """
        return {
            "frame_id": self.frame_id,
            "stream_id": self.stream_id,
            "sequence_number": self.sequence_number,
            "timestamp": self.timestamp,
            "width": self.width,
            "height": self.height,
            "encoding": self.encoding,
            "compressed_size": self.compressed_size,
            "checksum": self.checksum,
        }

    def to_dict(self) -> Dict[str, Any]:
        """
        Full dictionary including hex-encoded payload for JSON transport.
        """
        data = self.to_metadata_dict()
        data["payload_hex"] = self.payload_bytes.hex()
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


def create_stream_frame(
    stream_id: str,
    sequence_number: int,
    width: int,
    height: int,
    encoding: FrameEncoding,
    payload_bytes: bytes,
    timestamp: Optional[float] = None,
) -> StreamFrame:
    """
    Construct a validated StreamFrame instance.
    """
    if not isinstance(payload_bytes, (bytes, bytearray)):
        raise TypeError("Frame payload must be bytes.")

    payload = bytes(payload_bytes)
    compressed_size = len(payload)
    checksum = hashlib.sha256(payload).hexdigest()

    frame = StreamFrame(
        frame_id=f"FRM-{secrets.token_hex(8).upper()}",
        stream_id=stream_id,
        sequence_number=sequence_number,
        timestamp=timestamp if timestamp is not None else time.time(),
        width=int(width),
        height=int(height),
        encoding=encoding.value if isinstance(encoding, FrameEncoding) else str(encoding),
        compressed_size=compressed_size,
        payload_bytes=payload,
        checksum=checksum,
    )

    valid, err = validate_frame(frame)
    if not valid:
        raise ValueError(f"Invalid StreamFrame: {err}")

    return frame


def validate_frame(frame: StreamFrame) -> Tuple[bool, Optional[str]]:
    """
    Validate frame properties, dimensions, size limits, encodings, and checksum integrity.
    """
    if not frame.frame_id:
        return False, "EMPTY_FRAME_ID"
    if not frame.stream_id:
        return False, "EMPTY_STREAM_ID"
    if frame.sequence_number < 0:
        return False, "NEGATIVE_SEQUENCE_NUMBER"

    # Dimension bounds
    if frame.width <= 0 or frame.height <= 0:
        return False, f"INVALID_DIMENSIONS: {frame.width}x{frame.height}"
    if frame.width > MAX_FRAME_WIDTH:
        return False, f"FRAME_WIDTH_EXCEEDED: {frame.width} > {MAX_FRAME_WIDTH}"
    if frame.height > MAX_FRAME_HEIGHT:
        return False, f"FRAME_HEIGHT_EXCEEDED: {frame.height} > {MAX_FRAME_HEIGHT}"

    # Size bounds
    if frame.compressed_size <= 0:
        return False, "EMPTY_FRAME_PAYLOAD"
    if frame.compressed_size > MAX_FRAME_BYTES:
        return False, f"FRAME_SIZE_EXCEEDED: {frame.compressed_size} > {MAX_FRAME_BYTES}"

    if len(frame.payload_bytes) != frame.compressed_size:
        return False, "PAYLOAD_SIZE_MISMATCH"

    # Encoding allowlist
    if frame.encoding not in ALLOWED_FRAME_ENCODINGS:
        return False, f"PROHIBITED_FRAME_ENCODING: '{frame.encoding}'"

    # Checksum integrity
    calc_sha = hashlib.sha256(frame.payload_bytes).hexdigest()
    if calc_sha.lower() != frame.checksum.lower():
        return False, "CHECKSUM_INTEGRITY_MISMATCH"

    return True, None
