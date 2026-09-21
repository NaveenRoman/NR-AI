"""
NR-AI Automation Subsystem Data Models.
Standardized dataclasses and enums for persistent task automations.
"""

from dataclasses import asdict, dataclass, field
from enum import Enum
import json
import time
import uuid
from typing import Any, Dict, List, Optional


class ScheduleType(str, Enum):
    """Supported scheduling strategies for persistent automations."""
    ONCE = "ONCE"             # Run once at specific timestamp
    INTERVAL = "INTERVAL"     # Run every N seconds
    CRON = "CRON"             # Run based on cron expression pattern
    CONDITION = "CONDITION"   # Run when safe monitored condition evaluates to True


class AutomationStatus(str, Enum):
    """Lifecycle states of an automation."""
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    DISABLED = "DISABLED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass
class ScheduleConfig:
    """Detailed scheduling configuration."""
    schedule_type: ScheduleType = ScheduleType.ONCE
    interval_seconds: float = 0.0
    cron_expression: Optional[str] = None
    run_at: Optional[float] = None
    condition_expression: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schedule_type": self.schedule_type.value,
            "interval_seconds": self.interval_seconds,
            "cron_expression": self.cron_expression,
            "run_at": self.run_at,
            "condition_expression": self.condition_expression,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScheduleConfig":
        raw_type = data.get("schedule_type", ScheduleType.ONCE.value)
        try:
            stype = ScheduleType(raw_type)
        except ValueError:
            stype = ScheduleType.ONCE
        return cls(
            schedule_type=stype,
            interval_seconds=float(data.get("interval_seconds", 0.0)),
            cron_expression=data.get("cron_expression"),
            run_at=float(data["run_at"]) if data.get("run_at") is not None else None,
            condition_expression=data.get("condition_expression"),
        )


@dataclass
class AutomationRecord:
    """Authoritative persistent record of a background automation."""
    automation_id: str = field(default_factory=lambda: f"auto_{uuid.uuid4().hex[:12]}")
    owner_id: str = "system"
    task_id: str = field(default_factory=lambda: f"task_{uuid.uuid4().hex[:12]}")
    project_id: Optional[str] = None
    prompt: str = ""
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    status: AutomationStatus = AutomationStatus.ACTIVE
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_run: Optional[float] = None
    next_run: Optional[float] = None
    failure_reason: Optional[str] = None
    execution_count: int = 0
    retry_count: int = 0
    max_retries: int = 3
    timeout_seconds: float = 30.0
    evidence_references: List[str] = field(default_factory=list)
    workspace_scope: str = "global"
    requires_confirmation: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "automation_id": self.automation_id,
            "owner_id": self.owner_id,
            "task_id": self.task_id,
            "project_id": self.project_id,
            "prompt": self.prompt,
            "schedule": self.schedule.to_dict(),
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_run": self.last_run,
            "next_run": self.next_run,
            "failure_reason": self.failure_reason,
            "execution_count": self.execution_count,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "timeout_seconds": self.timeout_seconds,
            "evidence_references": self.evidence_references,
            "workspace_scope": self.workspace_scope,
            "requires_confirmation": self.requires_confirmation,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AutomationRecord":
        raw_status = data.get("status", AutomationStatus.ACTIVE.value)
        try:
            status = AutomationStatus(raw_status)
        except ValueError:
            status = AutomationStatus.ACTIVE

        raw_sched = data.get("schedule", {})
        if isinstance(raw_sched, dict):
            schedule = ScheduleConfig.from_dict(raw_sched)
        else:
            schedule = ScheduleConfig()

        return cls(
            automation_id=data.get("automation_id", f"auto_{uuid.uuid4().hex[:12]}"),
            owner_id=data.get("owner_id", "system"),
            task_id=data.get("task_id", f"task_{uuid.uuid4().hex[:12]}"),
            project_id=data.get("project_id"),
            prompt=data.get("prompt", ""),
            schedule=schedule,
            status=status,
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
            last_run=float(data["last_run"]) if data.get("last_run") is not None else None,
            next_run=float(data["next_run"]) if data.get("next_run") is not None else None,
            failure_reason=data.get("failure_reason"),
            execution_count=int(data.get("execution_count", 0)),
            retry_count=int(data.get("retry_count", 0)),
            max_retries=int(data.get("max_retries", 3)),
            timeout_seconds=float(data.get("timeout_seconds", 30.0)),
            evidence_references=list(data.get("evidence_references", [])),
            workspace_scope=data.get("workspace_scope", "global"),
            requires_confirmation=bool(data.get("requires_confirmation", False)),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class AutomationExecutionRecord:
    """Audit log record for an automation run."""
    run_id: str = field(default_factory=lambda: f"run_{uuid.uuid4().hex[:12]}")
    automation_id: str = ""
    started_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    success: bool = False
    output_summary: str = ""
    error_message: Optional[str] = None
    evidence_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "automation_id": self.automation_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "success": self.success,
            "output_summary": self.output_summary,
            "error_message": self.error_message,
            "evidence_path": self.evidence_path,
        }
