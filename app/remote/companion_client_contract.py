"""
NR-AI Companion Client Contract & UX Schemas.
Step 10 Phase 5 — Autonomous Mobile Companion UX, End-to-End Orchestration & Polish.

Defines client UI states, notification models, confirmation dialog payloads,
and speech synthesis (TTS) response sanitization rules.
"""

from dataclasses import asdict, dataclass, field
from enum import Enum
import json
import re
import time
from typing import Any, Dict, List, Optional

from app.remote.config import MAX_TTS_RESPONSE_CHARS


class CompanionUIState(str, Enum):
    PAIRING_REQUIRED = "PAIRING_REQUIRED"
    CONNECTED_IDLE = "CONNECTED_IDLE"
    OBSERVING_STREAM = "OBSERVING_STREAM"
    LISTENING_MIC = "LISTENING_MIC"
    PROCESSING_PC = "PROCESSING_PC"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    SPEAKING_TTS = "SPEAKING_TTS"
    EMERGENCY_STOPPED = "EMERGENCY_STOPPED"
    DISCONNECTED = "DISCONNECTED"
    RECONNECTING = "RECONNECTING"
    ERROR = "ERROR"


@dataclass
class CompanionNotification:
    """Structured notification pushed to the mobile companion."""
    notification_id: str
    title: str
    message: str
    level: str = "INFO"  # INFO, WARNING, ERROR, EMERGENCY
    timestamp: float = field(default_factory=time.time)
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "notification_id": self.notification_id,
            "title": self.title,
            "message": self.message,
            "level": self.level,
            "timestamp": self.timestamp,
            "data": self.data,
        }


@dataclass
class ConfirmationDialogPayload:
    """Payload sent to Android to present a modal confirmation prompt."""
    action_id: str
    action_type: str
    description: str
    target: Optional[str] = None
    risk_level: str = "HIGH"
    confirmation_token: str = ""
    expires_at: float = 0.0
    timeout_seconds: float = 30.0
    parameters_summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_id": self.action_id,
            "action_type": self.action_type,
            "description": self.description,
            "target": self.target,
            "risk_level": self.risk_level,
            "confirmation_token": self.confirmation_token,
            "expires_at": self.expires_at,
            "timeout_seconds": self.timeout_seconds,
            "parameters_summary": self.parameters_summary,
        }


@dataclass
class TTSResponseContract:
    """Sanitized and structured response intended for Android native TextToSpeech synthesis."""
    text: str
    raw_reply: str
    truncated: bool = False
    action_summary: Optional[str] = None
    status: str = "SUCCESS"
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "raw_reply": self.raw_reply,
            "truncated": self.truncated,
            "action_summary": self.action_summary,
            "status": self.status,
            "timestamp": self.timestamp,
        }


# Sanitization regex patterns for TTS speech clarity and privacy
RE_URL = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
RE_MARKDOWN_CODEBLOCK = re.compile(r"```[\s\S]*?```")
RE_INLINE_CODE = re.compile(r"`([^`]+)`")
RE_MARKDOWN_HEADERS = re.compile(r"^#+\s*", re.MULTILINE)
RE_MARKDOWN_FORMATTING = re.compile(r"[\*_~>#]+")
RE_MARKDOWN_LINKS = re.compile(r"\[([^\]]+)\]\([^\)]+\)")
RE_STACKTRACE_HEADER = re.compile(r"Traceback \(most recent call last\):[\s\S]*", re.IGNORECASE)
RE_FILE_PATHS = re.compile(r"(?:[A-Za-z]:\\|/(?:home|usr|etc|var|tmp|app)/)[A-Za-z0-9_\-\./\\]+", re.IGNORECASE)
RE_SECRETS = re.compile(r"\b(?:password|token|secret|key|cred(?:ential)?s?)\s*[=:]\s*\S+", re.IGNORECASE)
RE_EXCEPTION_NAME = re.compile(r"\b[A-Za-z0-9_]+(?:Error|Exception):\s*", re.IGNORECASE)


def sanitize_tts_response(raw_text: str, max_chars: int = MAX_TTS_RESPONSE_CHARS) -> str:
    """
    Cleans, strips technical artifacts, and shortens response text for natural spoken audio.
    Enforces maximum length of 400 characters, strips paths, URLs, secrets, markdown,
    and stack traces.
    """
    if not raw_text or not isinstance(raw_text, str):
        return ""

    text = raw_text.strip()

    # 1. Truncate stack traces completely
    text = RE_STACKTRACE_HEADER.sub("An internal system error occurred.", text)

    # 2. Strip code blocks and inline code
    text = RE_MARKDOWN_CODEBLOCK.sub("", text)
    text = RE_MARKDOWN_LINKS.sub(r"\1", text)
    text = RE_INLINE_CODE.sub(r"\1", text)

    # 3. Strip URLs
    text = RE_URL.sub("a web link", text)

    # 4. Strip filesystem paths
    text = RE_FILE_PATHS.sub("a local file", text)

    # 5. Strip secrets and credentials
    text = RE_SECRETS.sub("[redacted]", text)
    text = text.replace("[REDACTED]", "redacted")

    # 6. Strip Markdown symbols and formatting
    text = RE_MARKDOWN_HEADERS.sub("", text)
    text = RE_MARKDOWN_FORMATTING.sub("", text)

    # 7. Strip exception class prefixes (e.g. ValueError:, RemoteActionStateError:)
    text = RE_EXCEPTION_NAME.sub("", text)

    # 8. Clean up brackets and tech tags (e.g. [INFO | NR-AI], STDOUT:, STDERR:)
    text = re.sub(r"\[.*?\]", "", text)
    text = text.replace("STDOUT:", "Result:")
    text = text.replace("STDERR:", "Error:")

    # 9. Normalize whitespace
    text = re.sub(r"\s+", " ", text).strip()

    # 10. Enforce length boundary (max 400 chars) with graceful sentence boundary break
    if len(text) > max_chars:
        trimmed = text[:max_chars]
        last_period = max(trimmed.rfind("."), trimmed.rfind("!"), trimmed.rfind("?"))
        if last_period > max_chars // 2:
            text = trimmed[: last_period + 1]
        else:
            text = trimmed.rstrip() + "..."

    return text
