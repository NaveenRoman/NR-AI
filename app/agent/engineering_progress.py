"""
NR-AI Engineering Progress Tracking Subsystem.

Provides:
- Strongly-typed EngineeringProgress dataclass conforming to authoritative schema
- Thread-safe EngineeringProgressTracker singleton
- Real-time progress updates, stages, percentages, and evidence tracking
- ASCII progress bar rendering for terminal and UI chat bubbles
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger("NRAI.EngineeringProgress")


class ProgressState(str, Enum):
    QUEUED = "QUEUED"
    UNDERSTANDING = "UNDERSTANDING"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    WAITING = "WAITING"
    VERIFYING = "VERIFYING"
    RECOVERING = "RECOVERING"
    RETRYING = "RETRYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


def render_ascii_progress_bar(percent: int, width: int = 14) -> str:
    """Renders a standard ASCII progress bar: [████░░░░░░░░░░] 30%."""
    pct = max(0, min(100, int(percent)))
    filled = int(round((pct / 100.0) * width))
    empty = width - filled
    bar = "█" * filled + "░" * empty
    return f"[{bar}] {pct}%"


@dataclass
class EngineeringProgress:
    task_id: str
    command: str
    agent: str = "Droid"
    stage: str = "QUEUED"
    progress: int = 0
    status: str = ProgressState.QUEUED.value
    message: str = "Task queued"
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    evidence: List[str] = field(default_factory=list)
    recovery_count: int = 0
    error: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["started_at_iso"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.started_at))
        d["updated_at_iso"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.updated_at))
        d["ascii_bar"] = render_ascii_progress_bar(self.progress)
        return d


class EngineeringProgressTracker:
    """
    Thread-safe singleton tracking active engineering progress.
    Exposes real-time state for Galaxy UI polling and SSE/WebSocket streaming.
    """
    _instance: Optional[EngineeringProgressTracker] = None
    _lock = threading.Lock()

    def __init__(self):
        self._current: Optional[EngineeringProgress] = None
        self._history: List[EngineeringProgress] = []
        self._history_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> EngineeringProgressTracker:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def start_task(
        self,
        command: str,
        agent: str = "Droid",
        stage: str = "UNDERSTANDING",
        progress: int = 10,
        message: str = "Understanding command...",
        evidence: Optional[List[str]] = None,
        **kwargs,
    ) -> EngineeringProgress:
        with self._history_lock:
            now = time.time()
            task = EngineeringProgress(
                task_id=f"eng_{uuid.uuid4().hex[:8]}",
                command=command,
                agent=agent,
                stage=stage,
                progress=progress,
                status=ProgressState.UNDERSTANDING.value,
                message=message,
                started_at=now,
                updated_at=now,
                evidence=list(evidence or []),
                extra=dict(kwargs),
            )
            self._current = task
            logger.info(f"[Progress] Started {task.task_id} for '{command}' ({stage} {progress}%)")
            return task

    def update_stage(
        self,
        stage: str,
        progress: int,
        status: Union[ProgressState, str] = ProgressState.EXECUTING,
        message: str = "",
        evidence: Optional[Union[str, List[str]]] = None,
        error: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Optional[EngineeringProgress]:
        with self._history_lock:
            if self._current is None:
                return None
            
            status_val = status.value if isinstance(status, ProgressState) else str(status)
            self._current.stage = stage
            self._current.progress = max(0, min(100, int(progress)))
            self._current.status = status_val
            self._current.message = message
            self._current.updated_at = time.time()
            
            if error:
                self._current.error = error
            if evidence:
                if isinstance(evidence, list):
                    for ev in evidence:
                        if ev not in self._current.evidence:
                            self._current.evidence.append(ev)
                elif isinstance(evidence, str) and evidence not in self._current.evidence:
                    self._current.evidence.append(evidence)
            if extra:
                self._current.extra.update(extra)
            if kwargs:
                self._current.extra.update(kwargs)

            logger.info(
                f"[Progress] {self._current.task_id}: {stage} ({self._current.progress}%) "
                f"[{status_val}] - {message}"
            )
            return self._current

    def record_recovery(self, message: str, evidence: Optional[str] = None, **kwargs) -> Optional[EngineeringProgress]:
        with self._history_lock:
            if self._current is None:
                return None
            self._current.recovery_count += 1
            self._current.stage = "RECOVERING"
            self._current.status = ProgressState.RECOVERING.value
            self._current.message = message
            self._current.updated_at = time.time()
            if evidence and evidence not in self._current.evidence:
                self._current.evidence.append(evidence)
            if kwargs:
                self._current.extra.update(kwargs)
            return self._current

    def complete_task(
        self,
        message: str = "Completed successfully",
        evidence: Optional[List[str]] = None,
        extra: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Optional[EngineeringProgress]:
        with self._history_lock:
            if self._current is None:
                return None
            now = time.time()
            self._current.stage = "COMPLETED"
            self._current.progress = 100
            self._current.status = ProgressState.COMPLETED.value
            self._current.message = message
            self._current.updated_at = now
            if evidence:
                for ev in evidence:
                    if ev not in self._current.evidence:
                        self._current.evidence.append(ev)
            if extra:
                self._current.extra.update(extra)
            if kwargs:
                self._current.extra.update(kwargs)
            
            self._history.append(self._current)
            logger.info(f"[Progress] Completed {self._current.task_id} in {now - self._current.started_at:.2f}s")
            return self._current

    def fail_task(self, error: str, message: Optional[str] = None, **kwargs) -> Optional[EngineeringProgress]:
        with self._history_lock:
            if self._current is None:
                return None
            now = time.time()
            self._current.stage = "FAILED"
            self._current.status = ProgressState.FAILED.value
            self._current.error = error
            self._current.message = message or f"Task failed: {error}"
            self._current.updated_at = now
            if kwargs:
                self._current.extra.update(kwargs)
            self._history.append(self._current)
            logger.error(f"[Progress] Failed {self._current.task_id}: {error}")
            return self._current

    def get_current(self) -> Optional[EngineeringProgress]:
        with self._history_lock:
            return self._current

    def get_current_dict(self) -> Dict[str, Any]:
        with self._history_lock:
            if self._current is None:
                return {
                    "active": False,
                    "task_id": None,
                    "stage": "IDLE",
                    "progress": 0,
                    "status": "IDLE",
                    "message": "Standing by",
                    "ascii_bar": render_ascii_progress_bar(0),
                    "evidence": [],
                }
            d = self._current.to_dict()
            d["active"] = self._current.status not in (ProgressState.COMPLETED.value, ProgressState.FAILED.value, ProgressState.CANCELLED.value)
            return d
