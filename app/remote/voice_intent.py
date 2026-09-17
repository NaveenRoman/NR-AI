"""
NR-AI Voice Intent Schema & Parser.
Step 10 Phase 3 — Phone Voice Command & Secure Audio Pipeline.
"""

from dataclasses import dataclass, field
from enum import Enum
import json
import re
import secrets
import time
from typing import Any, Dict, Optional, Tuple

from app.remote.permissions import PROHIBITED_ACTIONS, PROHIBITED_COMMAND_PATTERNS


class VoiceIntentType(str, Enum):
    STATUS_QUERY = "STATUS_QUERY"
    SYSTEM_TIME = "SYSTEM_TIME"
    TOOLCHAIN_STATUS = "TOOLCHAIN_STATUS"
    CONVERSATION = "CONVERSATION"
    ACTION_APPROVED = "ACTION_APPROVED"
    CONFIRMATION_RESPONSE = "CONFIRMATION_RESPONSE"
    EMERGENCY_STOP = "EMERGENCY_STOP"
    PROHIBITED = "PROHIBITED"
    UNKNOWN = "UNKNOWN"


@dataclass
class VoiceIntent:
    intent_id: str
    text: str
    normalized_text: str
    intent_type: VoiceIntentType
    confidence: float
    parameters: Dict[str, Any] = field(default_factory=dict)
    requires_confirmation: bool = False
    risk_level: str = "LOW"  # "LOW", "MEDIUM", "HIGH", "CRITICAL"
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "text": self.text,
            "normalized_text": self.normalized_text,
            "intent_type": self.intent_type.value,
            "confidence": self.confidence,
            "parameters": self.parameters,
            "requires_confirmation": self.requires_confirmation,
            "risk_level": self.risk_level,
            "timestamp": self.timestamp,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


class VoiceIntentParser:
    """
    Deterministic voice intent parser.
    Extracts structured VoiceIntent from transcribed speech without allowing
    unrestricted LLM code execution or shell commands.
    """

    # Exact trigger patterns
    EMERGENCY_WORDS = {"stop", "emergency stop", "halt", "freeze", "kill all", "abort"}
    CONFIRM_WORDS = {"yes", "confirm", "proceed", "yes confirm", "confirm action"}
    CANCEL_WORDS = {"cancel", "no", "abort action", "cancel action", "do not"}

    VOICE_PROHIBITED_PATTERNS = [
        re.compile(r"\b(format\s+[a-z]:?|del\s+/[fqs]|delete\s+system32|rmdir\s+/s)\b", re.IGNORECASE),
        re.compile(r"\b(powershell(\.exe)?|cmd\.exe|bash(\.exe)?|wscript(\.exe)?|cscript(\.exe)?)\b", re.IGNORECASE),
        re.compile(r"\b(adb\s+shell|adb\s+push|adb\s+pull|adb\s+root)\b", re.IGNORECASE),
        re.compile(r"\b(taskkill|shutdown(\s+-[a-z])?|disk\.format|reg\.edit)\b", re.IGNORECASE),
        re.compile(r"\b(remote\.click|remote\.type|mouse_click|desktop_control)\b", re.IGNORECASE),
    ]

    @classmethod
    def normalize(cls, raw: str) -> str:
        if not raw:
            return ""
        s = raw.strip().lower()
        # Remove common punctuation but preserve hyphens, colons, slashes
        s = re.sub(r"[^\w\s\-\:\/]", "", s)
        # Collapse whitespace
        s = re.sub(r"\s+", " ", s).strip()
        return s

    @classmethod
    def parse_intent(cls, transcript: str) -> VoiceIntent:
        intent_id = f"INT-{secrets.token_hex(8).upper()}"
        raw_text = (transcript or "").strip()
        normalized = cls.normalize(raw_text)

        if not normalized:
            return VoiceIntent(
                intent_id=intent_id,
                text=raw_text,
                normalized_text="",
                intent_type=VoiceIntentType.UNKNOWN,
                confidence=0.0,
                risk_level="MEDIUM",
            )

        # 1. Check explicit cancellation words first
        if normalized in cls.CANCEL_WORDS:
            return VoiceIntent(
                intent_id=intent_id,
                text=raw_text,
                normalized_text=normalized,
                intent_type=VoiceIntentType.CONFIRMATION_RESPONSE,
                confidence=0.95,
                parameters={"confirmed": False},
                requires_confirmation=False,
                risk_level="LOW",
            )

        # 2. Emergency Stop Check (highest precedence, zero LLM dependency)
        for w in cls.EMERGENCY_WORDS:
            if normalized == w or normalized.startswith(w + " "):
                return VoiceIntent(
                    intent_id=intent_id,
                    text=raw_text,
                    normalized_text=normalized,
                    intent_type=VoiceIntentType.EMERGENCY_STOP,
                    confidence=1.0,
                    requires_confirmation=False,
                    risk_level="CRITICAL",
                )

        # 3. Prohibited patterns check
        for pat in cls.VOICE_PROHIBITED_PATTERNS:
            if pat.search(normalized):
                return VoiceIntent(
                    intent_id=intent_id,
                    text=raw_text,
                    normalized_text=normalized,
                    intent_type=VoiceIntentType.PROHIBITED,
                    confidence=1.0,
                    requires_confirmation=False,
                    risk_level="CRITICAL",
                )

        for pat in PROHIBITED_COMMAND_PATTERNS:
            if pat.search(normalized):
                return VoiceIntent(
                    intent_id=intent_id,
                    text=raw_text,
                    normalized_text=normalized,
                    intent_type=VoiceIntentType.PROHIBITED,
                    confidence=1.0,
                    requires_confirmation=False,
                    risk_level="CRITICAL",
                )

        # 4. Confirmation responses
        if normalized in cls.CONFIRM_WORDS or normalized.startswith("confirm"):
            return VoiceIntent(
                intent_id=intent_id,
                text=raw_text,
                normalized_text=normalized,
                intent_type=VoiceIntentType.CONFIRMATION_RESPONSE,
                confidence=0.95,
                parameters={"confirmed": True},
                requires_confirmation=False,
                risk_level="MEDIUM",
            )

        # 4. Sensitive action requiring confirmation
        sensitive_triggers = ["restart agent", "clear memory", "revoke device", "purge sessions"]
        for st in sensitive_triggers:
            if st in normalized:
                return VoiceIntent(
                    intent_id=intent_id,
                    text=raw_text,
                    normalized_text=normalized,
                    intent_type=VoiceIntentType.ACTION_APPROVED,
                    confidence=0.9,
                    requires_confirmation=True,
                    risk_level="HIGH",
                    parameters={"action": st},
                )

        # 5. System time check
        time_triggers = ["what time is it", "current time", "tell me the time", "what time", "time now"]
        for tt in time_triggers:
            if tt in normalized:
                return VoiceIntent(
                    intent_id=intent_id,
                    text=raw_text,
                    normalized_text=normalized,
                    intent_type=VoiceIntentType.SYSTEM_TIME,
                    confidence=0.95,
                    requires_confirmation=False,
                    risk_level="LOW",
                )

        # 6. Status & Toolchain Queries
        toolchain_triggers = ["toolchain", "android status", "unity status", "unreal status", "environment status"]
        for tct in toolchain_triggers:
            if tct in normalized:
                return VoiceIntent(
                    intent_id=intent_id,
                    text=raw_text,
                    normalized_text=normalized,
                    intent_type=VoiceIntentType.TOOLCHAIN_STATUS,
                    confidence=0.9,
                    requires_confirmation=False,
                    risk_level="LOW",
                    parameters={"query": tct},
                )

        status_triggers = ["status", "how are you", "system status", "health check", "ping"]
        for stt in status_triggers:
            if normalized == stt or normalized.startswith(stt + " "):
                return VoiceIntent(
                    intent_id=intent_id,
                    text=raw_text,
                    normalized_text=normalized,
                    intent_type=VoiceIntentType.STATUS_QUERY,
                    confidence=0.9,
                    requires_confirmation=False,
                    risk_level="LOW",
                )

        # 7. Conversational queries
        conv_prefixes = ["what", "who", "where", "why", "how", "when", "tell me", "explain", "describe", "hello", "hi"]
        for cp in conv_prefixes:
            if normalized.startswith(cp + " ") or normalized == cp:
                return VoiceIntent(
                    intent_id=intent_id,
                    text=raw_text,
                    normalized_text=normalized,
                    intent_type=VoiceIntentType.CONVERSATION,
                    confidence=0.85,
                    requires_confirmation=False,
                    risk_level="LOW",
                )

        # 8. Default fallback to UNKNOWN for unrecognized phrases
        return VoiceIntent(
            intent_id=intent_id,
            text=raw_text,
            normalized_text=normalized,
            intent_type=VoiceIntentType.UNKNOWN,
            confidence=0.0,
            requires_confirmation=False,
            risk_level="MEDIUM",
        )
