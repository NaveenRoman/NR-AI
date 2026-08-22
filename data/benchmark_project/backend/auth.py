import base64
import hashlib
import json
import os
import time
from typing import Any, Dict, Optional

SECRET_KEY = "nr-ai-super-secret-benchmark-key"


def hash_password(password: str, salt: Optional[str] = None) -> str:
    if not salt:
        salt = os.urandom(8).hex()
    hashed = hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()
    return f"{salt}${hashed}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, expected_hash = stored.split("$", 1)
        actual_hash = hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()
        return actual_hash == expected_hash
    except Exception:
        return False


def create_token(user_id: int, username: str, expires_in_seconds: int = 3600) -> str:
    payload = {
        "user_id": user_id,
        "username": username,
        "exp": int(time.time()) + expires_in_seconds,
    }
    raw = json.dumps(payload).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("utf-8")
    sig = hashlib.sha256(f"{encoded}:{SECRET_KEY}".encode("utf-8")).hexdigest()
    return f"{encoded}.{sig}"


def verify_token(token_str: str) -> Optional[Dict[str, Any]]:
    try:
        encoded, sig = token_str.strip().split(".", 1)
        expected_sig = hashlib.sha256(f"{encoded}:{SECRET_KEY}".encode("utf-8")).hexdigest()
        if sig != expected_sig:
            return None
        raw = base64.urlsafe_b64decode(encoded.encode("utf-8")).decode("utf-8")
        payload = json.loads(raw)
        if time.time() > payload.get("exp", 0):
            return None
        return payload
    except Exception:
        return None
