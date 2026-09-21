"""
NR-AI Persistent Automation & Scheduled Tasks Engine.
Phase 2 Foundation: SQLite-backed, crash-safe, rate-limited task automation.
"""

from app.automation.models import (
    AutomationRecord,
    AutomationStatus,
    ScheduleConfig,
    ScheduleType,
)
from app.automation.engine import AutomationEngine
from app.automation.scheduler import AutomationScheduler

__all__ = [
    "AutomationRecord",
    "AutomationStatus",
    "ScheduleConfig",
    "ScheduleType",
    "AutomationEngine",
    "AutomationScheduler",
]
