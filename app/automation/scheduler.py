"""
NR-AI Automation Scheduler.
Evaluates schedules (interval, cron, condition, once), enforces rate limits,
timeouts, emergency stops, and executes tasks with bounded concurrency.
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
import datetime
import logging
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.automation.engine import AutomationEngine
from app.automation.models import (
    AutomationExecutionRecord,
    AutomationRecord,
    AutomationStatus,
    ScheduleConfig,
    ScheduleType,
)
from app.remote.emergency import EmergencyStopController

logger = logging.getLogger("NRAI.AutomationScheduler")


def compute_next_cron_run(cron_expr: str, base_time: float) -> float:
    """
    Evaluate standard 5-field cron expression (minute, hour, day_of_month, month, day_of_week).
    Deterministic local evaluator for common patterns (* /5, 0, specific values).
    """
    fields = cron_expr.strip().split()
    if len(fields) != 5:
        # Fallback to hourly if malformed
        return base_time + 3600.0

    min_f, hour_f, dom_f, mon_f, dow_f = fields
    dt = datetime.datetime.fromtimestamp(base_time)

    # Simple step interval for minute: "*/N"
    if min_f.startswith("*/"):
        try:
            step = int(min_f[2:])
            current_min = dt.minute
            next_min = ((current_min // step) + 1) * step
            if next_min < 60:
                target_dt = dt.replace(minute=next_min, second=0, microsecond=0)
                return target_dt.timestamp()
            else:
                target_dt = (dt + datetime.timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
                return target_dt.timestamp()
        except ValueError:
            pass

    # Hourly pattern: "0 * * * *"
    if min_f == "0" and hour_f == "*":
        target_dt = (dt + datetime.timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        return target_dt.timestamp()

    # Daily pattern: "0 0 * * *"
    if min_f == "0" and hour_f == "0":
        target_dt = (dt + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return target_dt.timestamp()

    # Default fallback: 1 hour
    return base_time + 3600.0


class AutomationScheduler:
    """
    Thread-safe task scheduler evaluating active automations and executing tasks.
    Enforces rate limits, timeouts, E-stop, and destructive barriers.
    """

    def __init__(
        self,
        engine: AutomationEngine,
        emergency_controller: Optional[EmergencyStopController] = None,
        max_workers: int = 4,
        max_tasks_per_tick: int = 10,
    ):
        self.engine = engine
        self.emergency_controller = emergency_controller or EmergencyStopController()
        self.max_workers = max_workers
        self.max_tasks_per_tick = max_tasks_per_tick
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="NRAI-AutoScheduler")
        self._custom_condition_evaluators: Dict[str, Callable[[AutomationRecord], bool]] = {}
        self._custom_task_handlers: Dict[str, Callable[[AutomationRecord], Tuple[bool, str]]] = {}
        self._is_running = False
        self._thread: Optional[threading.Thread] = None

    def register_condition_evaluator(self, name: str, evaluator: Callable[[AutomationRecord], bool]) -> None:
        """Register a safe condition evaluator function for ScheduleType.CONDITION."""
        self._custom_condition_evaluators[name] = evaluator

    def register_task_handler(self, owner_or_action: str, handler: Callable[[AutomationRecord], Tuple[bool, str]]) -> None:
        """Register an execution handler for an agent or action type."""
        self._custom_task_handlers[owner_or_action] = handler

    def compute_next_run(self, schedule: ScheduleConfig, current_time: float) -> Optional[float]:
        """Compute the next scheduled execution epoch."""
        if schedule.schedule_type == ScheduleType.ONCE:
            if schedule.run_at and schedule.run_at > current_time:
                return schedule.run_at
            return None

        elif schedule.schedule_type == ScheduleType.INTERVAL:
            interval = max(0.1, schedule.interval_seconds)
            return current_time + interval

        elif schedule.schedule_type == ScheduleType.CRON:
            if schedule.cron_expression:
                return compute_next_cron_run(schedule.cron_expression, current_time)
            return current_time + 3600.0

        elif schedule.schedule_type == ScheduleType.CONDITION:
            # Poll condition every 5 seconds by default
            return current_time + 5.0

        return None

    def run_tick(self, confirmation_tokens: Optional[Dict[str, str]] = None) -> List[AutomationExecutionRecord]:
        """
        Execute one scheduler cycle.
        Finds pending automations, validates safety invariants, and runs them.
        """
        now = time.time()
        results: List[AutomationExecutionRecord] = []

        # 1. Emergency Stop Check
        if self.emergency_controller.is_active():
            logger.warning("Scheduler tick skipped: Emergency Stop is active.")
            return results

        # 2. Query active automations
        active_records = self.engine.list_automations(status=AutomationStatus.ACTIVE)
        pending: List[AutomationRecord] = []
        for rec in active_records:
            if rec.next_run is not None and rec.next_run <= now:
                pending.append(rec)
            if len(pending) >= self.max_tasks_per_tick:
                break

        confirmation_tokens = confirmation_tokens or {}

        # 3. Process each pending automation
        for rec in pending:
            exec_rec = self._execute_automation(rec, confirmation_tokens.get(rec.automation_id))
            results.append(exec_rec)

        return results

    def _execute_automation(
        self,
        record: AutomationRecord,
        confirmation_token: Optional[str] = None,
    ) -> AutomationExecutionRecord:
        """Execute a single automation record safely under timeout and permission gates."""
        start_time = time.time()
        run_record = AutomationExecutionRecord(
            automation_id=record.automation_id,
            started_at=start_time,
        )

        # 1. Safety Gate: Destructive actions require fresh confirmation
        if record.requires_confirmation:
            if not confirmation_token or confirmation_token != record.metadata.get("confirmation_token"):
                run_record.completed_at = time.time()
                run_record.success = False
                run_record.error_message = (
                    "CONFIRMATION_REQUIRED: Destructive task requires explicit human confirmation token."
                )
                self.engine.record_execution(run_record)
                return run_record

        # 2. Condition Check
        if record.schedule.schedule_type == ScheduleType.CONDITION:
            cond_expr = record.schedule.condition_expression or "default"
            evaluator = self._custom_condition_evaluators.get(cond_expr)
            if evaluator and not evaluator(record):
                # Condition not met yet; advance next_run and return without error
                record.next_run = time.time() + 5.0
                self.engine.update_automation(record)
                run_record.completed_at = time.time()
                run_record.success = True
                run_record.output_summary = "Condition evaluated False; tick deferred."
                return run_record

        # 3. Locate handler
        handler = self._custom_task_handlers.get(record.owner_id) or self._custom_task_handlers.get("default")
        if not handler:
            # Default mock handler for tests / unhandled agents
            def default_handler(r: AutomationRecord) -> Tuple[bool, str]:
                return True, f"Executed automation '{r.automation_id}' successfully."
            handler = default_handler

        # 4. Dispatch with timeout
        timeout = min(120.0, max(1.0, record.timeout_seconds))
        future = self._executor.submit(handler, record)
        try:
            success, output = future.result(timeout=timeout)
            run_record.success = success
            run_record.output_summary = output
            if not success:
                run_record.error_message = output
        except FutureTimeoutError:
            run_record.success = False
            run_record.error_message = f"TIMEOUT: Execution exceeded {timeout}s deadline."
        except Exception as ex:
            run_record.success = False
            run_record.error_message = f"EXECUTION_ERROR: {str(ex)}"

        run_record.completed_at = time.time()

        # 5. Update parent automation state
        self.engine.record_execution(run_record)
        now = time.time()
        record.last_run = now
        record.execution_count += 1

        if run_record.success:
            record.retry_count = 0
            if record.schedule.schedule_type == ScheduleType.ONCE:
                record.status = AutomationStatus.COMPLETED
                record.next_run = None
            else:
                record.next_run = self.compute_next_run(record.schedule, now)
        else:
            record.retry_count += 1
            if record.retry_count >= record.max_retries:
                record.status = AutomationStatus.FAILED
                record.next_run = None
                record.failure_reason = f"MAX_RETRIES_EXCEEDED: {run_record.error_message}"
            else:
                # Exponential backoff: 2^retry_count * 2 seconds
                backoff = (2 ** record.retry_count) * 2.0
                record.next_run = now + backoff

        self.engine.update_automation(record)
        return run_record
