import hashlib
import hmac
import time
from typing import Any, Dict, Optional

SECRET_KEY = b"uber_secret_jwt_hmac_key_2026"


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def generate_token(user_id: int, role: str) -> str:
    timestamp = int(time.time())
    payload = f"{user_id}:{role}:{timestamp}"
    signature = hmac.new(SECRET_KEY, payload.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
    return f"{payload}:{signature}"


def verify_token(token: str) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    try:
        parts = token.split(":")
        if len(parts) != 4:
            return None
        user_id_str, role, timestamp_str, sig = parts
        payload = f"{user_id_str}:{role}:{timestamp_str}"
        expected_sig = hmac.new(SECRET_KEY, payload.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
        if hmac.compare_digest(sig, expected_sig):
            return {"user_id": int(user_id_str), "role": role, "timestamp": int(timestamp_str)}
    except Exception:
        pass
    return None
