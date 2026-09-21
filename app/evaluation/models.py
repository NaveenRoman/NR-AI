"""
Evaluation and Learning Data Models for NR-AI.
Defines test categories, deterministic evaluation statuses, evidence records, and result schemas.

Invariants:
- 13 formal evaluation categories covering all NR-AI subsystems.
- 4 deterministic evaluation statuses: PASS, FAIL, BLOCKED, NOT_VERIFIED.
- Execution evidence strictly dominates model claims:
  A model claiming success without empirical evidence is marked FAIL or NOT_VERIFIED.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class EvaluationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    NOT_VERIFIED = "NOT_VERIFIED"


class EvaluationCategory(str, Enum):
    COMMAND_ROUTING = "COMMAND_ROUTING"
    AGENT_EXECUTION = "AGENT_EXECUTION"
    VOICE_PIPELINE = "VOICE_PIPELINE"
    TASK_AUTOMATION = "TASK_AUTOMATION"
    KNOWLEDGE_RETRIEVAL = "KNOWLEDGE_RETRIEVAL"
    SECURITY_GUARDRAILS = "SECURITY_GUARDRAILS"
    GALAXY_UI = "GALAXY_UI"
    DESKTOP_SHELL = "DESKTOP_SHELL"
    MCP_INTEGRATION = "MCP_INTEGRATION"
    TOOL_CALLING = "TOOL_CALLING"
    LATENCY_PERFORMANCE = "LATENCY_PERFORMANCE"
    COMPANION_CONNECTIVITY = "COMPANION_CONNECTIVITY"
    REGRESSION_DEFENSE = "REGRESSION_DEFENSE"


class EvidenceType(str, Enum):
    EXIT_CODE = "EXIT_CODE"
    ARTIFACT_FILE = "ARTIFACT_FILE"
    REGEX_MATCH = "REGEX_MATCH"
    TELEMETRY_METRIC = "TELEMETRY_METRIC"
    API_RESPONSE = "API_RESPONSE"
    NONE = "NONE"


@dataclass
class EvidenceRecord:
    evidence_type: EvidenceType
    verified: bool
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_type": self.evidence_type.value if isinstance(self.evidence_type, EvidenceType) else str(self.evidence_type),
            "verified": self.verified,
            "details": self.details,
            "timestamp": self.timestamp,
        }


@dataclass
class EvaluationCase:
    case_id: str
    category: EvaluationCategory
    title: str
    description: str
    input_data: Dict[str, Any] = field(default_factory=dict)
    expected_criteria: Dict[str, Any] = field(default_factory=dict)
    timeout_seconds: float = 10.0
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "category": self.category.value if isinstance(self.category, EvaluationCategory) else str(self.category),
            "title": self.title,
            "description": self.description,
            "input_data": self.input_data,
            "expected_criteria": self.expected_criteria,
            "timeout_seconds": self.timeout_seconds,
            "tags": self.tags,
        }


@dataclass
class EvaluationResult:
    case_id: str
    category: EvaluationCategory
    status: EvaluationStatus
    score: float  # 0.0 to 1.0
    execution_time_ms: float
    claimed_success: bool
    evidence_verified: bool
    evidence_records: List[EvidenceRecord] = field(default_factory=list)
    message: str = ""
    timestamp: float = field(default_factory=time.time)
    eval_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self):
        # Enforce execution evidence dominance:
        # If claimed_success is True but no empirical evidence verified, downgrade to FAIL or NOT_VERIFIED
        if self.claimed_success and not self.evidence_verified:
            if self.status == EvaluationStatus.PASS:
                self.status = EvaluationStatus.FAIL
                self.score = 0.0
                self.message = (
                    f"EVIDENCE DOMINANCE VIOLATION: Claimed success without empirical evidence. "
                    f"Original message: {self.message}"
                )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "eval_id": self.eval_id,
            "case_id": self.case_id,
            "category": self.category.value if isinstance(self.category, EvaluationCategory) else str(self.category),
            "status": self.status.value if isinstance(self.status, EvaluationStatus) else str(self.status),
            "score": round(self.score, 3),
            "execution_time_ms": round(self.execution_time_ms, 2),
            "claimed_success": self.claimed_success,
            "evidence_verified": self.evidence_verified,
            "evidence_records": [rec.to_dict() for rec in self.evidence_records],
            "message": self.message,
            "timestamp": self.timestamp,
        }
