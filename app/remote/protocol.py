"""
NR-AI Secure Request/Response Protocol & Schema Validation.
Step 10 Phase 1 — Secure Phone <-> PC Communication Foundation.
"""

from dataclasses import asdict, dataclass, field
import hashlib
import json
import time
from typing import Any, Dict, Optional, Tuple

from app.remote.config import (
    MAX_PAYLOAD_BYTES,
    MAX_REQUEST_BYTES,
)


@dataclass
class SecureRequest:
    request_id: str
    device_id: str
    session_id: str
    timestamp: float
    nonce: str
    action: str
    scope: str
    payload: Dict[str, Any] = field(default_factory=dict)
    signature: str = ""

    def compute_canonical_string(self) -> str:
        """
        Produce a deterministic canonical string representation for HMAC verification:
        request_id:device_id:session_id:timestamp:nonce:action:scope:payload_sha256
        """
        # Canonical JSON serialization of payload (sorted keys, compact delimiters)
        payload_json = json.dumps(self.payload, sort_keys=True, separators=(",", ":"))
        payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()

        # Format timestamp with fixed 3 decimal places to prevent float formatting drift
        ts_str = f"{self.timestamp:.3f}"
        canonical = (
            f"{self.request_id}:{self.device_id}:{self.session_id}:"
            f"{ts_str}:{self.nonce}:{self.action}:{self.scope}:{payload_sha256}"
        )
        return canonical

    def sign(self, secret: Any) -> str:
        """
        Compute HMAC-SHA256 signature using canonical string representation.
        Accepts hex string or raw bytes.
        """
        import hmac
        if isinstance(secret, str):
            secret_bytes = bytes.fromhex(secret)
        elif isinstance(secret, bytes):
            secret_bytes = secret
        else:
            raise TypeError("Secret must be str or bytes")

        canonical = self.compute_canonical_string()
        self.signature = hmac.new(secret_bytes, canonical.encode("utf-8"), hashlib.sha256).hexdigest()
        return self.signature

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "device_id": self.device_id,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "nonce": self.nonce,
            "action": self.action,
            "scope": self.scope,
            "payload": self.payload,
            "signature": self.signature,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


@dataclass
class SecureResponse:
    request_id: str
    status: str  # "SUCCESS", "ERROR", "DENIED"
    code: int
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "status": self.status,
            "code": self.code,
            "error": self.error,
            "data": self.data,
            "timestamp": self.timestamp,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


def parse_and_validate_request(raw_data: str) -> Tuple[bool, Optional[SecureRequest], Optional[str]]:
    """
    Parse and validate a raw JSON request against protocol bounds and schema.
    Returns (is_valid, request_obj, error_reason).
    """
    if raw_data is None:
        return False, None, "REQUEST_BODY_NULL"

    raw_bytes = raw_data.encode("utf-8")
    if len(raw_bytes) > MAX_REQUEST_BYTES:
        return False, None, f"REQUEST_TOO_LARGE: {len(raw_bytes)} bytes exceeds limit {MAX_REQUEST_BYTES}"

    try:
        data = json.loads(raw_data)
    except Exception as e:
        return False, None, f"MALFORMED_JSON: {e}"

    if not isinstance(data, dict):
        return False, None, "REQUEST_MUST_BE_OBJECT"

    required_fields = [
        "request_id",
        "device_id",
        "session_id",
        "timestamp",
        "nonce",
        "action",
        "scope",
        "signature",
    ]
    for rf in required_fields:
        if rf not in data:
            return False, None, f"MISSING_REQUIRED_FIELD: {rf}"

    payload = data.get("payload", {})
    if not isinstance(payload, dict):
        return False, None, "PAYLOAD_MUST_BE_OBJECT"

    payload_json = json.dumps(payload)
    if len(payload_json.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        return False, None, f"PAYLOAD_TOO_LARGE: exceeds limit {MAX_PAYLOAD_BYTES}"

    try:
        ts = float(data["timestamp"])
    except (ValueError, TypeError):
        return False, None, "INVALID_TIMESTAMP_FORMAT"

    req = SecureRequest(
        request_id=str(data["request_id"]).strip(),
        device_id=str(data["device_id"]).strip(),
        session_id=str(data["session_id"]).strip(),
        timestamp=ts,
        nonce=str(data["nonce"]).strip(),
        action=str(data["action"]).strip(),
        scope=str(data["scope"]).strip(),
        payload=payload,
        signature=str(data["signature"]).strip(),
    )

    if not req.request_id:
        return False, None, "EMPTY_REQUEST_ID"
    if not req.device_id:
        return False, None, "EMPTY_DEVICE_ID"
    if not req.nonce:
        return False, None, "EMPTY_NONCE"
    if not req.signature:
        return False, None, "EMPTY_SIGNATURE"

    return True, req, None
