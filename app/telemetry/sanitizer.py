"""
NR-AI Telemetry Sanitizer.
Removes secrets, authorization headers, cookies, and sensitive tokens
from structured event data before persistence or broadcast.
"""

import re
from typing import Any, Dict, List, Union

from app.security.guardrails import PromptGuardrails
from app.task.checkpoint_store import sanitize_secrets

SENSITIVE_KEYS = {
    "authorization", "auth", "token", "access_token", "refresh_token",
    "password", "secret", "cookie", "cookies", "set-cookie", "api_key",
    "apikey", "private_key", "bearer"
}


def sanitize_telemetry_data(data: Any) -> Any:
    """
    Recursively sanitize arbitrary data for telemetry logging:
    - Strips authentication headers, cookies, and bearer tokens.
    - Applies secret redaction patterns.
    """
    if isinstance(data, str):
        # 1. Strip raw cookie headers
        cleaned = re.sub(r"(?i)cookie\s*:\s*[^;\r\n]+", "Cookie: [COOKIE_REDACTED]", data)
        cleaned = re.sub(r"(?i)authorization\s*:\s*bearer\s+[A-Za-z0-9_\-\.]+", "Authorization: Bearer [TOKEN_REDACTED]", cleaned)
        # 2. General secret scrub
        scrubbed = sanitize_secrets(cleaned)
        if isinstance(scrubbed, str):
            g_res = PromptGuardrails.sanitize_text(scrubbed)
            return g_res.sanitized_text
        return str(scrubbed)

    elif isinstance(data, dict):
        sanitized_dict: Dict[str, Any] = {}
        for k, v in data.items():
            k_low = str(k).lower().strip()
            if any(sens in k_low for sens in SENSITIVE_KEYS):
                sanitized_dict[k] = "[REDACTED]"
            else:
                sanitized_dict[k] = sanitize_telemetry_data(v)
        return sanitized_dict

    elif isinstance(data, list):
        return [sanitize_telemetry_data(item) for item in data]

    return data
