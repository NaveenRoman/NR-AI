"""
NR-AI Prompt, PII, and Secret Guardrails Engine.
OpenJarvis-inspired, NR-AI-native pure Python deterministic security scanning and redaction.

Guarantees:
- Zero external native dependencies; 100% deterministic Python regex.
- Detects and scrubs API keys, bearer tokens, passwords, private keys, database URLs,
  authorization headers, cookies, session tokens, and common PII (SSN, credit card, phone, email).
- Structured redaction tokens: [SECRET_REDACTED:{TYPE}] and [PII_REDACTED:{TYPE}].
- Preserves useful engineering context (variable names, types, syntax) without destroying code structure.
- Pre-cloud model filter: prevents leakage to external AI endpoints.
- Three operational modes: WARN, REDACT, BLOCK.
"""

from dataclasses import dataclass, field
from enum import Enum
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("NRAI.Guardrails")


class RedactionMode(str, Enum):
    """Action to take upon detecting sensitive patterns."""
    WARN = "WARN"
    REDACT = "REDACT"
    BLOCK = "BLOCK"


class FindingSeverity(str, Enum):
    """Severity classification of detected secrets or PII."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class SecurityBlockError(Exception):
    """Raised when mode is BLOCK and critical security violations are detected."""
    pass


@dataclass
class SecurityFinding:
    """Detailed record of a matched secret or PII instance."""
    category: str  # "SECRET", "PII", "HEADER", "CREDENTIAL"
    pattern_name: str
    severity: FindingSeverity
    start: int
    end: int
    masked_preview: str
    description: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "pattern_name": self.pattern_name,
            "severity": self.severity.value,
            "start": self.start,
            "end": self.end,
            "masked_preview": self.masked_preview,
            "description": self.description,
        }


@dataclass
class ScanResult:
    """Outcome of a text security audit."""
    is_clean: bool
    findings: List[SecurityFinding] = field(default_factory=list)
    redacted_text: str = ""
    blocked: bool = False
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_clean": self.is_clean,
            "findings": [f.to_dict() for f in self.findings],
            "redacted_text": self.redacted_text,
            "blocked": self.blocked,
            "reasons": self.reasons,
        }


# =============================================================================
# SECRET AND CREDENTIAL PATTERNS
# =============================================================================
SECRET_RULES: Dict[str, Tuple[re.Pattern, FindingSeverity, str]] = {
    "OPENAI_KEY": (
        re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
        FindingSeverity.CRITICAL,
        "OpenAI API Key",
    ),
    "ANTHROPIC_KEY": (
        re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"),
        FindingSeverity.CRITICAL,
        "Anthropic API Key",
    ),
    "GOOGLE_API_KEY": (
        re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
        FindingSeverity.CRITICAL,
        "Google Cloud / AIza API Key",
    ),
    "AWS_ACCESS_KEY": (
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        FindingSeverity.CRITICAL,
        "AWS Access Key ID",
    ),
    "GITHUB_TOKEN": (
        re.compile(r"\b(?:ghp|gho|ghs|ghr|github_pat)_[A-Za-z0-9_]{36,}\b"),
        FindingSeverity.CRITICAL,
        "GitHub Personal Access Token",
    ),
    "SLACK_TOKEN": (
        re.compile(r"\bxox[bpors]-[A-Za-z0-9\-]{10,}\b"),
        FindingSeverity.HIGH,
        "Slack Token",
    ),
    "STRIPE_KEY": (
        re.compile(r"\b(?:sk|pk)_(?:test|live)_[A-Za-z0-9]{20,}\b"),
        FindingSeverity.CRITICAL,
        "Stripe Secret / Public Key",
    ),
    "PRIVATE_KEY": (
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        FindingSeverity.CRITICAL,
        "Cryptographic Private Key Block",
    ),
    "BEARER_TOKEN": (
        re.compile(r"(?i)\bbearer\s+([A-Za-z0-9_\-\.]{20,})\b"),
        FindingSeverity.HIGH,
        "Authorization Bearer Token",
    ),
    "DB_CONNECTION_STRING": (
        re.compile(r"(?i)\b(?:postgres|postgresql|mysql|mongodb|redis)://[^\s'\"<>]{8,}\b"),
        FindingSeverity.HIGH,
        "Database Connection URL with Embedded Credentials",
    ),
    "PASSWORD_ASSIGNMENT": (
        re.compile(r"""(?i)(?:[a-zA-Z0-9_]*(?:password|passwd|pwd|pass)[a-zA-Z0-9_]*|secret_key|api_secret)\s*[=:]\s*['"]([^'"]{4,})['"]"""),
        FindingSeverity.HIGH,
        "Hardcoded Password or Secret Assignment",
    ),
    "AUTHORIZATION_HEADER": (
        re.compile(r"(?i)(?:authorization|proxy-authorization)\s*:\s*(?:basic|bearer|token)\s+[A-Za-z0-9_\-\.=]+"),
        FindingSeverity.HIGH,
        "HTTP Authorization Header",
    ),
    "COOKIE_SESSION": (
        re.compile(r"(?i)(?:session_id|sessiontoken|auth_token|connect\.sid)\s*=\s*[A-Za-z0-9_\-\.]{16,}"),
        FindingSeverity.MEDIUM,
        "Session Cookie Identifier",
    ),
}

# =============================================================================
# PII (PERSONALLY IDENTIFIABLE INFORMATION) PATTERNS
# =============================================================================
PII_RULES: Dict[str, Tuple[re.Pattern, FindingSeverity, str]] = {
    "EMAIL": (
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b"),
        FindingSeverity.MEDIUM,
        "Email Address",
    ),
    "US_SSN": (
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        FindingSeverity.CRITICAL,
        "US Social Security Number",
    ),
    "CREDIT_CARD": (
        re.compile(r"\b(?:4\d{3}|5[1-5]\d{2}|3[47]\d{2})[- ]?\d{4}[- ]?\d{4}[- ]?\d{3,4}\b"),
        FindingSeverity.CRITICAL,
        "Credit Card Number (Visa / Mastercard / Amex)",
    ),
    "PHONE_NUMBER": (
        re.compile(r"\b(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
        FindingSeverity.MEDIUM,
        "Phone Number",
    ),
}


class GuardrailsEngine:
    """
    Deterministic security guardrails engine.
    Audits and scrubs text prompts and model message histories before transit.
    """

    def __init__(self, mode: RedactionMode = RedactionMode.REDACT):
        self.mode = mode

    def scan(self, text: str) -> ScanResult:
        """Exhaustively scan text for secret and PII violations."""
        if not text or not isinstance(text, str):
            return ScanResult(is_clean=True, findings=[], redacted_text=text or "")

        findings: List[SecurityFinding] = []
        redacted = text

        # 1. Scan Secrets
        for rule_name, (pattern, severity, desc) in SECRET_RULES.items():
            for match in pattern.finditer(text):
                matched_span = match.group(0)
                # Mask preview: keep first 3 and last 2 characters
                preview = matched_span[:3] + "..." + matched_span[-2:] if len(matched_span) > 6 else "***"
                findings.append(SecurityFinding(
                    category="SECRET",
                    pattern_name=rule_name,
                    severity=severity,
                    start=match.start(),
                    end=match.end(),
                    masked_preview=preview,
                    description=desc,
                ))

        # 2. Scan PII
        for rule_name, (pattern, severity, desc) in PII_RULES.items():
            for match in pattern.finditer(text):
                matched_span = match.group(0)
                preview = matched_span[:2] + "..." + matched_span[-2:] if len(matched_span) > 5 else "***"
                findings.append(SecurityFinding(
                    category="PII",
                    pattern_name=rule_name,
                    severity=severity,
                    start=match.start(),
                    end=match.end(),
                    masked_preview=preview,
                    description=desc,
                ))

        # Sort findings by start position
        findings.sort(key=lambda f: f.start)
        is_clean = len(findings) == 0

        # Build redacted text
        redacted = self._apply_redactions(text, findings)

        # Evaluate blocking condition
        blocked = False
        reasons: List[str] = []
        if not is_clean and self.mode == RedactionMode.BLOCK:
            has_critical = any(f.severity in (FindingSeverity.CRITICAL, FindingSeverity.HIGH) for f in findings)
            if has_critical:
                blocked = True
                reasons = [f"Security block triggered: {len(findings)} sensitive token(s) detected."]

        return ScanResult(
            is_clean=is_clean,
            findings=findings,
            redacted_text=redacted,
            blocked=blocked,
            reasons=reasons,
        )

    def redact(self, text: str) -> str:
        """Quickly scrub all secrets and PII from text, returning safe string."""
        return self.scan(text).redacted_text

    def sanitize_payload(
        self,
        messages: List[Dict[str, str]],
        mode: Optional[RedactionMode] = None,
    ) -> Tuple[List[Dict[str, str]], ScanResult]:
        """
        Sanitize an entire chat messages payload before sending to cloud model providers.
        """
        current_mode = mode or self.mode
        combined_findings: List[SecurityFinding] = []
        sanitized_messages: List[Dict[str, str]] = []
        total_blocked = False
        reasons: List[str] = []

        for msg in messages:
            content = msg.get("content", "")
            scan_res = self.scan(content)
            combined_findings.extend(scan_res.findings)

            if scan_res.blocked:
                total_blocked = True
                reasons.extend(scan_res.reasons)

            sanitized_content = scan_res.redacted_text if current_mode != RedactionMode.WARN else content
            new_msg = dict(msg)
            new_msg["content"] = sanitized_content
            sanitized_messages.append(new_msg)

        if total_blocked and current_mode == RedactionMode.BLOCK:
            raise SecurityBlockError(f"Cloud request blocked by Guardrails: {'; '.join(reasons)}")

        overall_result = ScanResult(
            is_clean=len(combined_findings) == 0,
            findings=combined_findings,
            redacted_text="",
            blocked=total_blocked,
            reasons=reasons,
        )
        return sanitized_messages, overall_result

    def _apply_redactions(self, text: str, findings: List[SecurityFinding]) -> str:
        """Applies structured replacement tokens without overlapping offsets."""
        if not findings:
            return text

        # Perform replacements on unique non-overlapping segments
        # To avoid index corruption, substitute directly via regex rule replacements
        result = text

        # Replace secrets
        for rule_name, (pattern, _, _) in SECRET_RULES.items():
            if rule_name == "PASSWORD_ASSIGNMENT":
                # Special handling: preserve variable name, redact only quote contents
                def pw_repl(m):
                    full = m.group(0)
                    val = m.group(1)
                    return full.replace(f'"{val}"', '"[SECRET_REDACTED:PASSWORD]"').replace(f"'{val}'", "'[SECRET_REDACTED:PASSWORD]'")
                result = pattern.sub(pw_repl, result)
            elif rule_name == "BEARER_TOKEN":
                def bearer_repl(m):
                    return f"Bearer [SECRET_REDACTED:BEARER_TOKEN]"
                result = pattern.sub(bearer_repl, result)
            else:
                tag = f"[SECRET_REDACTED:{rule_name}]"
                result = pattern.sub(tag, result)

        # Replace PII
        for rule_name, (pattern, _, _) in PII_RULES.items():
            tag = f"[PII_REDACTED:{rule_name}]"
            result = pattern.sub(tag, result)

        return result


# Global singleton instance
global_guardrails = GuardrailsEngine(mode=RedactionMode.REDACT)
