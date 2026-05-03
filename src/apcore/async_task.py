"""Async task manager for background module execution."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
import warnings
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from apcore.context import Context
from apcore.errors import TaskLimitExceededError

__all__ = [
    "TaskStatus",
    "TaskInfo",
    "TaskStore",
    "InMemoryTaskStore",
    "RetryConfig",
    "RetryPolicy",
    "BackoffStrategy",
    "AsyncTaskManager",
    "ExecutorProtocol",
]


@runtime_checkable
class ExecutorProtocol(Protocol):
    """Minimal async-call surface required by :class:`AsyncTaskManager`.

    Provided as a ``Protocol`` so tests can inject a lightweight fake
    without constructing a full :class:`apcore.executor.Executor`. The
    concrete ``Executor`` satisfies this protocol via ``call_async``.
    """

    async def call_async(
        self,
        module_id: str,
        inputs: dict[str, Any] | None = None,
        context: Context | None = None,
        version_hint: str | None = None,
    ) -> dict[str, Any]: ...


_logger = logging.getLogger(__name__)


def _emit_retrying_deprecation() -> None:
    warnings.warn(
        "TaskStatus.RETRYING is deprecated and removed in cross-language alignment "
        "(D-12); during retry backoff the status is now TaskStatus.PENDING. "
        "Use TaskStatus.PENDING in new code.",
        DeprecationWarning,
        stacklevel=3,
    )


class _TaskStatusMeta(type(Enum)):  # type: ignore[misc]
    """Metaclass that intercepts ``TaskStatus.RETRYING`` access for one-version
    deprecation: returns ``TaskStatus.PENDING`` with a ``DeprecationWarning``.
    """

    def __getattr__(cls, name: str) -> Any:
        if name == "RETRYING":
            _emit_retrying_deprecation()
            return cls.PENDING  # type: ignore[attr-defined]
        raise AttributeError(name)


class TaskStatus(str, Enum, metaclass=_TaskStatusMeta):
    """Status of an async task.

    .. note::
        ``RETRYING`` was removed in the cross-language alignment (D-12).
        Tasks waiting between retry attempts are now reported as
        :attr:`PENDING` to match the TypeScript and Rust SDKs.  The legacy
        ``TaskStatus.RETRYING`` attribute remains accessible for one minor
        release as a deprecated alias for ``PENDING``.
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskInfo:
    """Metadata and result tracking for a submitted async task.

    .. note::
        The ``attempt_number`` field was renamed to ``retry_count`` for
        cross-language alignment (D-13).  ``attempt_number`` is kept as a
        deprecated property accessor for one minor release.
    """

    task_id: str
    module_id: str
    status: TaskStatus
    submitted_at: float
    started_at: float | None = None
    completed_at: float | None = None
    result: Any = None
    error: str | None = None
    retry_count: int = 0
    max_retries: int = 0

    # ----- Deprecated aliases (D-13) -----

    @property
    def attempt_number(self) -> int:
        """Deprecated alias for :attr:`retry_count`.

        .. deprecated:: 0.21.0
            Use :attr:`retry_count` instead.  Will be removed in a future
            release.
        """
        warnings.warn(
            "TaskInfo.attempt_number is deprecated; use retry_count instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.retry_count

    @attempt_number.setter
    def attempt_number(self, value: int) -> None:
        warnings.warn(
            "TaskInfo.attempt_number is deprecated; use retry_count instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        self.retry_count = value


@runtime_checkable
class TaskStore(Protocol):
    """Sync storage backend for :class:`TaskInfo` records.

    The default implementation is :class:`InMemoryTaskStore`. Users may
    supply a custom store (e.g. Redis, DB) via ``AsyncTaskManager(store=...)``.
    All methods are synchronous; async stores must wrap I/O in a sync adapter.

    .. note::
        The canonical write method is :meth:`save`.  ``put`` is retained as
        a deprecated shim for one minor release (D-10).
    """

    def get(self, task_id: str) -> TaskInfo | None: ...
    def save(self, info: TaskInfo) -> None: ...
    def delete(self, task_id: str) -> None: ...
    def list(self, status: TaskStatus | None = None) -> list[TaskInfo]: ...
    def list_expired(self, before_timestamp: float) -> list[TaskInfo]: ...


class InMemoryTaskStore:
    """Default in-memory :class:`TaskStore` backed by a plain dict."""

    def __init__(self) -> None:
        self._data: dict[str, TaskInfo] = {}

    def get(self, task_id: str) -> TaskInfo | None:
        return self._data.get(task_id)

    def save(self, info: TaskInfo) -> None:
        self._data[info.task_id] = info

    def put(self, info: TaskInfo) -> None:
        """Deprecated alias for :meth:`save` (D-10).

        .. deprecated:: 0.21.0
            Use :meth:`save`.  Retained for one minor release for
            backward compatibility.
        """
        warnings.warn(
            "TaskStore.put() is deprecated; use save() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        self.save(info)

    def delete(self, task_id: str) -> None:
        self._data.pop(task_id, None)

    def list(self, status: TaskStatus | None = None) -> list[TaskInfo]:
        if status is None:
            return list(self._data.values())
        return [t for t in self._data.values() if t.status == status]

    def list_expired(self, before_timestamp: float) -> list[TaskInfo]:
        """Return terminal-state tasks whose ``completed_at`` precedes
        ``before_timestamp`` (D-10).

        Non-terminal tasks (PENDING, RUNNING) are never returned —
        ``list_expired`` is intended to drive TTL-based cleanup of
        finished work, not to interrupt running tasks.
        """
        # Terminal set is duplicated locally to avoid a forward reference to
        # the module-level ``_TERMINAL_STATUSES`` constant defined below.
        terminal = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
        result: list[TaskInfo] = []
        for info in self._data.values():
            if info.status not in terminal:
                continue
            ref = info.completed_at if info.completed_at is not None else info.submitted_at
            if ref < before_timestamp:
                result.append(info)
        return result


class BackoffStrategy(str, Enum):
    """Backoff formula applied between retry attempts (legacy ``RetryPolicy``)."""

    FIXED = "fixed"
    LINEAR = "linear"
    EXPONENTIAL = "exponential"


@dataclass
class RetryConfig:
    """Retry configuration for a submitted task (canonical, sync A-002).

    Field names align with TypeScript / Rust / protocol spec:
    ``max_retries``, ``retry_delay_ms``, ``backoff_multiplier``,
    ``max_retry_delay_ms``. Delay is computed as
    ``min(retry_delay_ms * (backoff_multiplier ** attempt), max_retry_delay_ms)``
    where ``attempt`` is 0-indexed (the first retry uses attempt=0).

    The legacy :class:`RetryPolicy` (using ``backoff`` /
    ``base_delay_seconds``) remains supported as a deprecated alternative.
    """

    max_retries: int = 0
    retry_delay_ms: int = 1000
    backoff_multiplier: float = 2.0
    max_retry_delay_ms: int = 60000

    def compute_delay_ms(self, attempt: int) -> float:
        """Return the delay in milliseconds before retry ``attempt`` (0-indexed).

        Capped at :attr:`max_retry_delay_ms`.
        """
        return min(
            self.retry_delay_ms * (self.backoff_multiplier**attempt),
            self.max_retry_delay_ms,
        )

    def delay_for(self, attempt: int) -> float:
        """Return delay in **seconds** before retry ``attempt`` (1-indexed).

        Adapter that bridges the canonical (0-indexed, ms) API to the legacy
        :meth:`RetryPolicy.delay_for` (1-indexed, seconds) signature so
        ``AsyncTaskManager`` can treat both classes uniformly.
        """
        # Translate legacy 1-indexed attempt into canonical 0-indexed.
        zero_indexed = max(attempt - 1, 0)
        return self.compute_delay_ms(zero_indexed) / 1000.0


@dataclass
class RetryPolicy:
    """Legacy retry configuration for a submitted task.

    .. deprecated:: 0.21.0
        Use :class:`RetryConfig` for cross-language alignment with
        TypeScript / Rust / protocol spec field names. ``RetryPolicy`` is
        retained for backwards compatibility and will be removed in a
        future major release.

    Attributes:
        max_retries: Maximum number of retry attempts (0 = no retry).
        backoff: Delay growth strategy between attempts.
        base_delay_seconds: Base wait time fed into the backoff formula.
    """

    max_retries: int = 3
    backoff: BackoffStrategy = BackoffStrategy.EXPONENTIAL
    base_delay_seconds: float = 1.0

    def delay_for(self, attempt: int) -> float:
        """Return wait seconds before retry *attempt* (1-indexed)."""
        if self.backoff == BackoffStrategy.FIXED:
            return self.base_delay_seconds
        if self.backoff == BackoffStrategy.LINEAR:
            return self.base_delay_seconds * attempt
        # EXPONENTIAL
        return self.base_delay_seconds * (2 ** (attempt - 1))


_ACTIVE_STATUSES = frozenset({TaskStatus.PENDING, TaskStatus.RUNNING})
_TERMINAL_STATUSES = frozenset({TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED})


class AsyncTaskManager:
    """Manages background execution of modules via asyncio tasks.

    Limits concurrency with a semaphore and tracks task lifecycle.
    Accepts a pluggable :class:`TaskStore` for custom persistence.
    """

    def __init__(
        self,
        executor: ExecutorProtocol,
        max_concurrent: int = 10,
        max_tasks: int = 1000,
        store: TaskStore | None = None,
    ) -> None:
        self._executor = executor
        self._max_tasks = max_tasks
        self._store: TaskStore = store if store is not None else InMemoryTaskStore()
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._async_tasks: dict[str, asyncio.Task[Any]] = {}
        self._reaper_task: asyncio.Task[Any] | None = None
        self._reaper_interval: float = 3600.0
        self._reaper_max_age: float = 3600.0

    def _save(self, info: TaskInfo) -> None:
        """Persist ``info`` via the configured store.

        Prefers the canonical :meth:`TaskStore.save` method.  Falls back to
        the deprecated :meth:`put` for legacy custom stores that have not
        yet been updated to the post-D-10 API.
        """
        save = getattr(self._store, "save", None)
        if callable(save):
            save(info)
        else:  # pragma: no cover - legacy custom stores
            self._store.put(info)  # type: ignore[attr-defined]

    async def submit(
        self,
        module_id: str,
        inputs: dict[str, Any],
        context: Context | None = None,
        retry_policy: "RetryPolicy | RetryConfig | None" = None,
    ) -> str:
        """Submit a module for background execution.

        Creates a TaskInfo in PENDING state, spawns an asyncio.Task that
        acquires the concurrency semaphore before calling executor.call_async().

        Args:
            module_id: The module to execute.
            inputs: Input data for the module.
            context: Optional execution context.
            retry_policy: Optional retry configuration — either the canonical
                :class:`RetryConfig` or the legacy :class:`RetryPolicy`.
                None means no retries.

        Returns:
            The generated task_id (UUID4 string).
        """
        active = sum(1 for info in self._store.list() if info.status in _ACTIVE_STATUSES)
        if active >= self._max_tasks:
            raise TaskLimitExceededError(max_tasks=self._max_tasks)

        task_id = str(uuid.uuid4())
        info = TaskInfo(
            task_id=task_id,
            module_id=module_id,
            status=TaskStatus.PENDING,
            submitted_at=time.time(),
            max_retries=retry_policy.max_retries if retry_policy is not None else 0,
        )
        self._save(info)

        async_task = asyncio.create_task(self._run(task_id, module_id, inputs, context, retry_policy))
        async_task.add_done_callback(lambda _: self._async_tasks.pop(task_id, None))
        self._async_tasks[task_id] = async_task

        return task_id

    def get_status(self, task_id: str) -> TaskInfo | None:
        """Return the TaskInfo for a task, or None if not found."""
        return self._store.get(task_id)

    def get_result(self, task_id: str) -> Any:
        """Return the result of a completed task.

        Raises:
            KeyError: If the task_id is not found.
            RuntimeError: If the task is not in COMPLETED status.
        """
        info = self._store.get(task_id)
        if info is None:
            raise KeyError(f"Task not found: {task_id}")
        if info.status != TaskStatus.COMPLETED:
            raise RuntimeError(f"Task {task_id} is not completed (status={info.status.value})")
        return info.result

    async def cancel(self, task_id: str) -> bool:
        """Cancel a running, pending, or retrying task.

        Returns:
            True if the task was successfully cancelled, False otherwise.
        """
        info = self._store.get(task_id)
        if info is None:
            return False
        if info.status not in _ACTIVE_STATUSES:
            return False

        async_task = self._async_tasks.get(task_id)
        if async_task is None:
            return False

        async_task.cancel()
        try:
            await async_task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            _logger.warning(
                "Task %s raised while being cancelled: %s",
                task_id,
                exc,
                exc_info=True,
            )

        if info.status in _ACTIVE_STATUSES:
            info.status = TaskStatus.CANCELLED
            info.completed_at = time.time()
            self._save(info)

        return True

    async def shutdown(self) -> None:
        """Cancel all pending/running/retrying tasks and stop the reaper."""
        self.stop_reaper()
        for task_id in list(self._async_tasks):
            await self.cancel(task_id)

    def list_tasks(self, status: TaskStatus | None = None) -> list[TaskInfo]:
        """Return all tasks, optionally filtered by status."""
        return self._store.list(status)

    def cleanup(self, max_age_seconds: float = 3600.0) -> int:
        """Remove terminal-state tasks older than max_age_seconds.

        Terminal states: COMPLETED, FAILED, CANCELLED.

        Returns:
            The number of tasks removed.
        """
        now = time.time()
        to_remove: list[str] = []

        for info in self._store.list():
            if info.status not in _TERMINAL_STATUSES:
                continue
            ref_time = info.completed_at if info.completed_at is not None else info.submitted_at
            if now - ref_time >= max_age_seconds:
                to_remove.append(info.task_id)

        for task_id in to_remove:
            self._store.delete(task_id)
            self._async_tasks.pop(task_id, None)

        return len(to_remove)

    def start_reaper(
        self,
        interval_seconds: float = 3600.0,
        max_age_seconds: float = 3600.0,
    ) -> None:
        """Start a background asyncio task that periodically calls cleanup().

        Args:
            interval_seconds: How often to run cleanup (seconds).
            max_age_seconds: Terminal tasks older than this are removed.

        Raises:
            RuntimeError: If the reaper is already running.
        """
        if self._reaper_task is not None and not self._reaper_task.done():
            raise RuntimeError("Reaper is already running; call stop_reaper() first")
        self._reaper_interval = interval_seconds
        self._reaper_max_age = max_age_seconds
        self._reaper_task = asyncio.create_task(self._reap_loop())

    def stop_reaper(self) -> None:
        """Stop the background reaper task. No-op if not running."""
        if self._reaper_task is not None and not self._reaper_task.done():
            self._reaper_task.cancel()
        self._reaper_task = None

    async def _reap_loop(self) -> None:
        """Periodic cleanup loop executed by the reaper task."""
        try:
            while True:
                await asyncio.sleep(self._reaper_interval)
                self.cleanup(self._reaper_max_age)
        except asyncio.CancelledError:
            pass

    async def _run(
        self,
        task_id: str,
        module_id: str,
        inputs: dict[str, Any],
        context: Context | None,
        retry_policy: "RetryPolicy | RetryConfig | None",
    ) -> None:
        """Internal coroutine: execute a module with optional retry/backoff."""
        info = self._store.get(task_id)
        if info is None:
            return

        max_retries = retry_policy.max_retries if retry_policy is not None else 0

        while True:
            try:
                async with self._semaphore:
                    info.status = TaskStatus.RUNNING
                    if info.started_at is None:
                        info.started_at = time.time()
                    self._save(info)

                    result = await self._executor.call_async(module_id, inputs, context)

                    info.status = TaskStatus.COMPLETED
                    info.completed_at = time.time()
                    info.result = result
                    self._save(info)
                    return

            except asyncio.CancelledError:
                info.status = TaskStatus.CANCELLED
                info.completed_at = time.time()
                self._save(info)
                _logger.info("Task %s cancelled", task_id)
                return

            except Exception as exc:
                if retry_policy is not None and info.retry_count < max_retries:
                    info.retry_count += 1
                    delay = retry_policy.delay_for(info.retry_count)
                    # D-12: backoff state is PENDING (was RETRYING) — matches
                    # TypeScript and Rust SDKs.
                    info.status = TaskStatus.PENDING
                    self._save(info)
                    _logger.info(
                        "Task %s failed (attempt %d/%d), retrying in %.3fs",
                        task_id,
                        info.retry_count,
                        max_retries,
                        delay,
                    )
                    try:
                        await asyncio.sleep(delay)
                    except asyncio.CancelledError:
                        info.status = TaskStatus.CANCELLED
                        info.completed_at = time.time()
                        self._save(info)
                        _logger.info("Task %s cancelled during backoff", task_id)
                        return
                else:
                    info.status = TaskStatus.FAILED
                    info.completed_at = time.time()
                    info.error = str(exc)
                    self._save(info)
                    _logger.error("Task %s failed: %s", task_id, exc, exc_info=True)
                    return
