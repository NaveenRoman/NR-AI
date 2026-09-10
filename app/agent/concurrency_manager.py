"""
NR AI Concurrency & Resource Safety Manager.

Provides robust resource and file locking with shared read / exclusive write semantics,
task ownership tracking, timeout-bounded acquisition, and automated conflict detection.
Prevents parallel agents from corrupting shared project files or race conditions.
"""

from contextlib import contextmanager
from dataclasses import dataclass, field
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, Dict, Generator, List, Optional, Set

logger = logging.getLogger("NRAI.ConcurrencyManager")


class LockAcquisitionError(Exception):
    """Raised when a file or resource lock cannot be acquired within the timeout."""
    pass


class LockConflictError(Exception):
    """Raised when a conflicting lock is actively held by another task."""
    pass


@dataclass
class ResourceLockState:
    """State of locks on a specific file or resource."""
    resource_key: str
    exclusive_writer: Optional[str] = None
    readers: Set[str] = field(default_factory=set)
    created_at: float = field(default_factory=time.time)
    last_acquired_at: float = field(default_factory=time.time)


class ResourceLockManager:
    """
    Centralized file and resource lock manager.

    Ensures that tasks editing the same file or shared resource are serialized,
    while tasks performing read/analysis operations can run safely in parallel.
    """

    def __init__(self, default_timeout: float = 10.0):
        self.default_timeout = default_timeout
        self._master_lock = threading.RLock()
        # Maps normalized resource key -> ResourceLockState
        self._resources: Dict[str, ResourceLockState] = {}
        # Condition variable for lock availability
        self._condition = threading.Condition(self._master_lock)
        # Maps task_id -> set of resource keys it holds
        self._task_locks: Dict[str, Set[str]] = {}

    def _normalize_key(self, target: str | Path) -> str:
        """Normalize file paths or resource strings to uniform identifiers."""
        if isinstance(target, Path):
            return str(target.resolve()).lower()
        target_str = str(target).strip()
        # If it looks like a path
        if "/" in target_str or "\\" in target_str or os.path.exists(target_str):
            try:
                return str(Path(target_str).resolve()).lower()
            except Exception:
                return target_str.lower()
        return target_str.lower()

    # -------------------------------------------------------------------------
    # Read Locks (Shared)
    # -------------------------------------------------------------------------

    def acquire_read_lock(
        self,
        target: str | Path,
        task_id: str,
        timeout: Optional[float] = None,
    ) -> bool:
        """
        Acquires a shared read lock for task_id.
        Blocks until no other task holds an exclusive write lock, or until timeout expires.
        """
        key = self._normalize_key(target)
        deadline = time.time() + (timeout if timeout is not None else self.default_timeout)

        with self._condition:
            while True:
                state = self._resources.setdefault(key, ResourceLockState(resource_key=key))

                # Can read if no one holds an exclusive write lock, OR if this task itself holds the write lock
                if state.exclusive_writer is None or state.exclusive_writer == task_id:
                    state.readers.add(task_id)
                    state.last_acquired_at = time.time()
                    self._task_locks.setdefault(task_id, set()).add(key)
                    logger.debug(f"[Lock] Read lock acquired on '{key}' by task '{task_id}'")
                    return True

                remaining = deadline - time.time()
                if remaining <= 0:
                    raise LockAcquisitionError(
                        f"Timeout ({timeout}s) waiting to acquire READ lock on '{key}'. "
                        f"Exclusively held by task '{state.exclusive_writer}'."
                    )
                self._condition.wait(timeout=remaining)

    def release_read_lock(self, target: str | Path, task_id: str) -> None:
        """Release a shared read lock."""
        key = self._normalize_key(target)
        with self._condition:
            state = self._resources.get(key)
            if state and task_id in state.readers:
                state.readers.remove(task_id)
                if task_id in self._task_locks:
                    self._task_locks[task_id].discard(key)
                if not state.readers and state.exclusive_writer is None:
                    self._resources.pop(key, None)
                self._condition.notify_all()
                logger.debug(f"[Lock] Read lock released on '{key}' by task '{task_id}'")

    # -------------------------------------------------------------------------
    # Write Locks (Exclusive)
    # -------------------------------------------------------------------------

    def acquire_write_lock(
        self,
        target: str | Path,
        task_id: str,
        timeout: Optional[float] = None,
    ) -> bool:
        """
        Acquires an exclusive write lock for task_id.
        Blocks until no other task holds a read lock or write lock.
        """
        key = self._normalize_key(target)
        deadline = time.time() + (timeout if timeout is not None else self.default_timeout)

        with self._condition:
            while True:
                state = self._resources.setdefault(key, ResourceLockState(resource_key=key))

                # Already held by this task
                if state.exclusive_writer == task_id:
                    return True

                # Can acquire if no writer, and readers set is either empty or contains only task_id
                other_readers = state.readers - {task_id}
                if state.exclusive_writer is None and len(other_readers) == 0:
                    state.exclusive_writer = task_id
                    state.last_acquired_at = time.time()
                    self._task_locks.setdefault(task_id, set()).add(key)
                    logger.debug(f"[Lock] Write lock acquired on '{key}' by task '{task_id}'")
                    return True

                remaining = deadline - time.time()
                if remaining <= 0:
                    conflict_holders = []
                    if state.exclusive_writer:
                        conflict_holders.append(f"writer={state.exclusive_writer}")
                    if other_readers:
                        conflict_holders.append(f"readers={list(other_readers)}")
                    raise LockAcquisitionError(
                        f"Timeout ({timeout}s) waiting to acquire WRITE lock on '{key}'. "
                        f"Held by: {', '.join(conflict_holders)}"
                    )
                self._condition.wait(timeout=remaining)

    def release_write_lock(self, target: str | Path, task_id: str) -> None:
        """Release an exclusive write lock."""
        key = self._normalize_key(target)
        with self._condition:
            state = self._resources.get(key)
            if state and state.exclusive_writer == task_id:
                state.exclusive_writer = None
                if task_id in self._task_locks:
                    self._task_locks[task_id].discard(key)
                if not state.readers:
                    self._resources.pop(key, None)
                self._condition.notify_all()
                logger.debug(f"[Lock] Write lock released on '{key}' by task '{task_id}'")

    # -------------------------------------------------------------------------
    # Context Managers
    # -------------------------------------------------------------------------

    @contextmanager
    def read_lock(
        self, target: str | Path, task_id: str, timeout: Optional[float] = None
    ) -> Generator[None, None, None]:
        self.acquire_read_lock(target, task_id, timeout)
        try:
            yield
        finally:
            self.release_read_lock(target, task_id)

    @contextmanager
    def write_lock(
        self, target: str | Path, task_id: str, timeout: Optional[float] = None
    ) -> Generator[None, None, None]:
        self.acquire_write_lock(target, task_id, timeout)
        try:
            yield
        finally:
            self.release_write_lock(target, task_id)

    # -------------------------------------------------------------------------
    # Conflict Detection & Cleanup
    # -------------------------------------------------------------------------

    def detect_conflict(
        self, target: str | Path, task_id: str, is_write: bool = True
    ) -> Optional[str]:
        """
        Check if acquiring the lock on target would conflict with an existing lock.
        Returns a description of the conflict or None if free.
        """
        key = self._normalize_key(target)
        with self._master_lock:
            state = self._resources.get(key)
            if not state:
                return None
            if is_write:
                if state.exclusive_writer and state.exclusive_writer != task_id:
                    return f"Write conflict: held by writer '{state.exclusive_writer}'"
                other_readers = state.readers - {task_id}
                if other_readers:
                    return f"Read/Write conflict: active readers {list(other_readers)}"
            else:
                if state.exclusive_writer and state.exclusive_writer != task_id:
                    return f"Read conflict: held by writer '{state.exclusive_writer}'"
            return None

    def release_all_for_task(self, task_id: str) -> int:
        """
        Safely releases all locks currently held by a given task.
        Called on task completion, cancellation, or error.
        """
        released_count = 0
        with self._condition:
            keys_to_release = list(self._task_locks.get(task_id, set()))
            for key in keys_to_release:
                state = self._resources.get(key)
                if state:
                    if state.exclusive_writer == task_id:
                        state.exclusive_writer = None
                        released_count += 1
                    if task_id in state.readers:
                        state.readers.remove(task_id)
                        released_count += 1
                    if not state.readers and state.exclusive_writer is None:
                        self._resources.pop(key, None)
            self._task_locks.pop(task_id, None)
            self._condition.notify_all()
        return released_count

    def get_active_locks(self) -> Dict[str, Any]:
        """Return diagnostic snapshot of all active locks."""
        with self._master_lock:
            return {
                k: {
                    "writer": v.exclusive_writer,
                    "readers": list(v.readers),
                    "created_at": v.created_at,
                    "last_acquired_at": v.last_acquired_at,
                }
                for k, v in self._resources.items()
            }
