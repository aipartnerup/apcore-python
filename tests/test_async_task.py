"""Tests for AsyncTaskManager, TaskStatus, and TaskInfo."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from apcore.async_task import (
    AsyncTaskManager,
    BackoffStrategy,
    InMemoryTaskStore,
    RetryPolicy,
    TaskInfo,
    TaskStatus,
    TaskStore,
)
from apcore.context import Context
from apcore.executor import Executor
from apcore.registry import Registry


# === Helper modules ===


class SimpleModule:
    """Module that returns immediately."""

    input_schema = None
    output_schema = None

    def execute(self, inputs: dict[str, Any], context: Context) -> dict[str, Any]:
        return {"value": inputs.get("x", 0)}


class AsyncSimpleModule:
    """Async module that returns immediately."""

    input_schema = None
    output_schema = None

    async def execute(self, inputs: dict[str, Any], context: Context) -> dict[str, Any]:
        return {"value": inputs.get("x", 0)}


class FailingModule:
    """Module that always raises."""

    input_schema = None
    output_schema = None

    def execute(self, inputs: dict[str, Any], context: Context) -> dict[str, Any]:
        raise RuntimeError("intentional failure")


class SlowAsyncModule:
    """Async module that sleeps for a configurable duration."""

    input_schema = None
    output_schema = None

    async def execute(self, inputs: dict[str, Any], context: Context) -> dict[str, Any]:
        delay = inputs.get("delay", 1.0)
        await asyncio.sleep(delay)
        return {"done": True}


# === Fixtures ===


@pytest.fixture
def registry() -> Registry:
    reg = Registry()
    reg.register("test.simple", SimpleModule())
    reg.register("test.async_simple", AsyncSimpleModule())
    reg.register("test.failing", FailingModule())
    reg.register("test.slow", SlowAsyncModule())
    return reg


@pytest.fixture
def executor(registry: Registry) -> Executor:
    return Executor(registry=registry)


@pytest.fixture
def manager(executor: Executor) -> AsyncTaskManager:
    return AsyncTaskManager(executor, max_concurrent=10)


# === Tests ===


class TestTaskStatusTransitions:
    """Submit task and verify PENDING -> RUNNING -> COMPLETED."""

    @pytest.mark.asyncio
    async def test_submit_completes_successfully(self, manager: AsyncTaskManager) -> None:
        task_id = await manager.submit("test.simple", {"x": 42})

        # Task should exist immediately after submit
        info = manager.get_status(task_id)
        assert info is not None
        assert info.module_id == "test.simple"

        # Allow the background task to complete
        await asyncio.sleep(0.1)

        info = manager.get_status(task_id)
        assert info is not None
        assert info.status == TaskStatus.COMPLETED
        assert info.result == {"value": 42}
        assert info.started_at is not None
        assert info.completed_at is not None
        assert info.error is None

    @pytest.mark.asyncio
    async def test_submit_async_module(self, manager: AsyncTaskManager) -> None:
        task_id = await manager.submit("test.async_simple", {"x": 7})
        await asyncio.sleep(0.1)

        info = manager.get_status(task_id)
        assert info is not None
        assert info.status == TaskStatus.COMPLETED
        assert info.result == {"value": 7}


class TestTaskFailure:
    """Submit task that fails -> status FAILED with error message."""

    @pytest.mark.asyncio
    async def test_failed_task_has_error(self, manager: AsyncTaskManager) -> None:
        task_id = await manager.submit("test.failing", {})
        await asyncio.sleep(0.1)

        info = manager.get_status(task_id)
        assert info is not None
        assert info.status == TaskStatus.FAILED
        assert info.error is not None
        assert "intentional failure" in info.error
        assert info.completed_at is not None
        assert info.result is None


class TestTaskCancellation:
    """Cancel running task -> status CANCELLED."""

    @pytest.mark.asyncio
    async def test_cancel_running_task(self, manager: AsyncTaskManager) -> None:
        task_id = await manager.submit("test.slow", {"delay": 10.0})
        # Give it time to start
        await asyncio.sleep(0.1)

        info = manager.get_status(task_id)
        assert info is not None
        assert info.status == TaskStatus.RUNNING

        result = await manager.cancel(task_id)
        assert result is True

        info = manager.get_status(task_id)
        assert info is not None
        assert info.status == TaskStatus.CANCELLED
        assert info.completed_at is not None

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_task(self, manager: AsyncTaskManager) -> None:
        result = await manager.cancel("nonexistent-id")
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_completed_task(self, manager: AsyncTaskManager) -> None:
        task_id = await manager.submit("test.simple", {"x": 1})
        await asyncio.sleep(0.1)

        info = manager.get_status(task_id)
        assert info is not None
        assert info.status == TaskStatus.COMPLETED

        result = await manager.cancel(task_id)
        assert result is False


class TestConcurrencyLimit:
    """Verify max_concurrent is respected."""

    @pytest.mark.asyncio
    async def test_max_concurrent_respected(self, executor: Executor) -> None:
        max_concurrent = 2
        mgr = AsyncTaskManager(executor, max_concurrent=max_concurrent)

        # Submit 4 slow tasks
        task_ids = []
        for _ in range(4):
            tid = await mgr.submit("test.slow", {"delay": 5.0})
            task_ids.append(tid)

        # Allow tasks to start competing for the semaphore
        await asyncio.sleep(0.2)

        tasks = mgr.list_tasks()
        running = [t for t in tasks if t.status == TaskStatus.RUNNING]
        pending = [t for t in tasks if t.status == TaskStatus.PENDING]

        assert len(running) <= max_concurrent
        # Some should still be pending since we only allow 2 concurrent
        assert len(pending) + len(running) == 4

        # Cleanup: cancel all
        for tid in task_ids:
            await mgr.cancel(tid)


class TestGetResult:
    """get_result() raises for non-completed tasks."""

    @pytest.mark.asyncio
    async def test_get_result_returns_value(self, manager: AsyncTaskManager) -> None:
        task_id = await manager.submit("test.simple", {"x": 99})
        await asyncio.sleep(0.1)

        result = manager.get_result(task_id)
        assert result == {"value": 99}

    def test_get_result_raises_for_unknown_task(self, manager: AsyncTaskManager) -> None:
        with pytest.raises(KeyError, match="Task not found"):
            manager.get_result("no-such-task")

    @pytest.mark.asyncio
    async def test_get_result_raises_for_pending_task(self, manager: AsyncTaskManager) -> None:
        # Submit a slow task so it stays in PENDING/RUNNING
        task_id = await manager.submit("test.slow", {"delay": 10.0})

        with pytest.raises(RuntimeError, match="not completed"):
            manager.get_result(task_id)

        await manager.cancel(task_id)


class TestListTasks:
    """list_tasks() returns all or filtered tasks."""

    @pytest.mark.asyncio
    async def test_list_all_tasks(self, manager: AsyncTaskManager) -> None:
        await manager.submit("test.simple", {"x": 1})
        await manager.submit("test.simple", {"x": 2})
        await asyncio.sleep(0.1)

        all_tasks = manager.list_tasks()
        assert len(all_tasks) == 2

    @pytest.mark.asyncio
    async def test_list_tasks_filtered(self, manager: AsyncTaskManager) -> None:
        await manager.submit("test.simple", {"x": 1})
        await manager.submit("test.failing", {})
        await asyncio.sleep(0.1)

        completed = manager.list_tasks(status=TaskStatus.COMPLETED)
        failed = manager.list_tasks(status=TaskStatus.FAILED)
        assert len(completed) == 1
        assert len(failed) == 1


class TestCleanup:
    """cleanup() removes old completed tasks."""

    @pytest.mark.asyncio
    async def test_cleanup_removes_old_tasks(self, manager: AsyncTaskManager) -> None:
        task_id = await manager.submit("test.simple", {"x": 1})
        await asyncio.sleep(0.1)

        info = manager.get_status(task_id)
        assert info is not None
        assert info.status == TaskStatus.COMPLETED

        # With max_age=0, everything completed should be cleaned
        removed = manager.cleanup(max_age_seconds=0.0)
        assert removed == 1
        assert manager.get_status(task_id) is None

    @pytest.mark.asyncio
    async def test_cleanup_preserves_recent_tasks(self, manager: AsyncTaskManager) -> None:
        await manager.submit("test.simple", {"x": 1})
        await asyncio.sleep(0.1)

        # With a large max_age, nothing should be removed
        removed = manager.cleanup(max_age_seconds=3600.0)
        assert removed == 0
        assert len(manager.list_tasks()) == 1

    @pytest.mark.asyncio
    async def test_cleanup_preserves_running_tasks(self, manager: AsyncTaskManager) -> None:
        task_id = await manager.submit("test.slow", {"delay": 10.0})
        await asyncio.sleep(0.1)

        # Running tasks should not be cleaned up even with max_age=0
        removed = manager.cleanup(max_age_seconds=0.0)
        assert removed == 0

        await manager.cancel(task_id)


class TestGetStatusEdgeCases:
    """get_status() edge cases."""

    def test_get_status_unknown_id(self, manager: AsyncTaskManager) -> None:
        assert manager.get_status("nonexistent") is None


class TestTaskInfo:
    """TaskInfo dataclass basics."""

    def test_task_info_creation(self) -> None:
        info = TaskInfo(
            task_id="abc",
            module_id="test.mod",
            status=TaskStatus.PENDING,
            submitted_at=time.time(),
        )
        assert info.task_id == "abc"
        assert info.module_id == "test.mod"
        assert info.status == TaskStatus.PENDING
        assert info.started_at is None
        assert info.completed_at is None
        assert info.result is None
        assert info.error is None


class TestTaskStatusEnum:
    """TaskStatus enum values."""

    def test_status_values(self) -> None:
        assert TaskStatus.PENDING == "pending"
        assert TaskStatus.RUNNING == "running"
        assert TaskStatus.COMPLETED == "completed"
        assert TaskStatus.FAILED == "failed"
        assert TaskStatus.CANCELLED == "cancelled"

    def test_status_is_str(self) -> None:
        assert isinstance(TaskStatus.PENDING, str)


class TestMaxTasksLimit:
    """submit() raises TaskLimitExceededError when task limit is reached."""

    @pytest.mark.asyncio
    async def test_submit_exceeds_max_tasks(self, executor: Executor) -> None:
        from apcore.errors import TaskLimitExceededError

        max_tasks = 3
        mgr = AsyncTaskManager(executor, max_concurrent=10, max_tasks=max_tasks)

        for _ in range(max_tasks):
            await mgr.submit("test.simple", {"x": 1})

        with pytest.raises(TaskLimitExceededError) as exc_info:
            await mgr.submit("test.simple", {"x": 1})
        assert exc_info.value.code == "TASK_LIMIT_EXCEEDED"
        assert exc_info.value.details["max_tasks"] == max_tasks

    @pytest.mark.asyncio
    async def test_submit_at_limit_after_cleanup(self, executor: Executor) -> None:
        max_tasks = 2
        mgr = AsyncTaskManager(executor, max_concurrent=10, max_tasks=max_tasks)

        await mgr.submit("test.simple", {"x": 1})
        await mgr.submit("test.simple", {"x": 2})
        await asyncio.sleep(0.1)

        # Clean up completed tasks to free slots
        mgr.cleanup(max_age_seconds=0.0)

        # Now we should be able to submit again
        task_id = await mgr.submit("test.simple", {"x": 3})
        assert task_id is not None

    @pytest.mark.asyncio
    async def test_max_tasks_counts_only_active_tasks(self, executor: Executor) -> None:
        """max_tasks is a concurrency cap, not a lifetime cap.

        Once tasks reach a terminal state, they must not count against the
        limit even without an explicit cleanup().
        """
        max_tasks = 2
        mgr = AsyncTaskManager(executor, max_concurrent=10, max_tasks=max_tasks)

        # Fill the limit with tasks and let them complete.
        for _ in range(max_tasks):
            await mgr.submit("test.simple", {"x": 1})
        await asyncio.sleep(0.1)

        # Terminal tasks stay accessible via get_status()/get_result(), but
        # they must not block new submissions.
        assert all(
            info.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED) for info in mgr.list_tasks()
        )

        # Submitting again should succeed without calling cleanup().
        new_id = await mgr.submit("test.simple", {"x": 99})
        assert mgr.get_status(new_id) is not None


class TestShutdown:
    """shutdown() cancels all pending/running tasks."""

    @pytest.fixture
    def executor(self) -> Executor:
        reg = Registry()
        reg.register("slow_module", SlowAsyncModule())
        return Executor(registry=reg)

    @pytest.mark.asyncio
    async def test_shutdown_cancels_all_tasks(self) -> None:
        reg = Registry()
        reg.register("slow_module", SlowAsyncModule())
        executor = Executor(registry=reg)
        mgr = AsyncTaskManager(executor=executor, max_concurrent=2)
        for _ in range(3):
            await mgr.submit("slow_module", {})
        await mgr.shutdown()
        for t in mgr.list_tasks():
            assert t.status in (TaskStatus.CANCELLED, TaskStatus.COMPLETED)


class TestAsyncTaskAutoCleanup:
    """Completed asyncio.Task objects are removed from _async_tasks automatically."""

    @pytest.mark.asyncio
    async def test_done_callback_removes_async_task(self, executor: Executor) -> None:
        mgr = AsyncTaskManager(executor, max_concurrent=10)
        task_id = await mgr.submit("test.simple", {"x": 1})

        # Allow the background task to complete and callback to fire
        await asyncio.sleep(0.1)

        # The asyncio.Task should have been auto-removed from _async_tasks
        assert task_id not in mgr._async_tasks

        # But the TaskInfo should still be accessible via the public API
        info = mgr.get_status(task_id)
        assert info is not None
        assert info.status == TaskStatus.COMPLETED


# =============================================================================
# New tests: TaskStore, RetryPolicy, Reaper
# =============================================================================


class _SpyStore:
    """TaskStore spy that records status at the time of each put() call."""

    def __init__(self) -> None:
        self._data: dict[str, TaskInfo] = {}
        self.put_statuses: list[TaskStatus] = []

    def get(self, task_id: str) -> TaskInfo | None:
        return self._data.get(task_id)

    def put(self, info: TaskInfo) -> None:
        self._data[info.task_id] = info
        self.put_statuses.append(info.status)

    def delete(self, task_id: str) -> None:
        self._data.pop(task_id, None)

    def list(self, status: TaskStatus | None = None) -> list[TaskInfo]:
        if status is None:
            return list(self._data.values())
        return [t for t in self._data.values() if t.status == status]


class _CountingModule:
    """Module that fails on the first N attempts, then succeeds."""

    input_schema = None
    output_schema = None

    def __init__(self, fail_times: int) -> None:
        self._fail_times = fail_times
        self._calls = 0

    def execute(self, inputs: dict[str, Any], context: Context) -> dict[str, Any]:
        self._calls += 1
        if self._calls <= self._fail_times:
            raise RuntimeError(f"intentional failure #{self._calls}")
        return {"calls": self._calls}


class TestTaskStoreProtocol:
    """TC-001/TC-002: InMemoryTaskStore satisfies TaskStore protocol."""

    def test_in_memory_store_satisfies_protocol(self) -> None:
        assert isinstance(InMemoryTaskStore(), TaskStore)

    def test_custom_store_satisfies_protocol(self) -> None:
        assert isinstance(_SpyStore(), TaskStore)


class TestInMemoryTaskStore:
    """TC-003..006: InMemoryTaskStore CRUD and list filtering."""

    def _make_info(self, status: TaskStatus = TaskStatus.COMPLETED) -> TaskInfo:
        import uuid

        return TaskInfo(
            task_id=str(uuid.uuid4()),
            module_id="test.mod",
            status=status,
            submitted_at=time.time(),
        )

    def test_put_then_get_returns_same_object(self) -> None:
        store = InMemoryTaskStore()
        info = self._make_info()
        store.put(info)
        assert store.get(info.task_id) is info

    def test_get_unknown_returns_none(self) -> None:
        assert InMemoryTaskStore().get("nonexistent") is None

    def test_delete_removes_entry(self) -> None:
        store = InMemoryTaskStore()
        info = self._make_info()
        store.put(info)
        store.delete(info.task_id)
        assert store.get(info.task_id) is None

    def test_list_status_filter(self) -> None:
        store = InMemoryTaskStore()
        completed = self._make_info(TaskStatus.COMPLETED)
        failed = self._make_info(TaskStatus.FAILED)
        store.put(completed)
        store.put(failed)
        result = store.list(status=TaskStatus.COMPLETED)
        assert len(result) == 1
        assert result[0] is completed


class TestCustomStore:
    """TC-007/TC-008: AsyncTaskManager routes all state through a custom store."""

    @pytest.mark.asyncio
    async def test_manager_accepts_custom_store(self, executor: Executor) -> None:
        spy = _SpyStore()
        mgr = AsyncTaskManager(executor, store=spy)
        task_id = await mgr.submit("test.simple", {"x": 1})
        await asyncio.sleep(0.1)

        assert spy.get(task_id) is not None
        assert spy.get(task_id).status == TaskStatus.COMPLETED  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_put_called_for_each_state_transition(self, executor: Executor) -> None:
        spy = _SpyStore()
        mgr = AsyncTaskManager(executor, store=spy)
        await mgr.submit("test.simple", {"x": 1})
        await asyncio.sleep(0.1)

        # Must see PENDING (from submit), RUNNING, COMPLETED (from _run)
        assert TaskStatus.PENDING in spy.put_statuses
        assert TaskStatus.RUNNING in spy.put_statuses
        assert TaskStatus.COMPLETED in spy.put_statuses
        # Order: PENDING first, COMPLETED last
        assert spy.put_statuses[0] == TaskStatus.PENDING
        assert spy.put_statuses[-1] == TaskStatus.COMPLETED


class TestRetryPolicyDelayFor:
    """TC-009/010/011: delay_for() formulas for each BackoffStrategy."""

    def test_fixed_strategy(self) -> None:
        policy = RetryPolicy(max_retries=3, backoff=BackoffStrategy.FIXED, base_delay_seconds=2.0)
        assert [policy.delay_for(n) for n in range(1, 4)] == [2.0, 2.0, 2.0]

    def test_linear_strategy(self) -> None:
        policy = RetryPolicy(max_retries=3, backoff=BackoffStrategy.LINEAR, base_delay_seconds=1.0)
        assert [policy.delay_for(n) for n in range(1, 4)] == [1.0, 2.0, 3.0]

    def test_exponential_strategy(self) -> None:
        policy = RetryPolicy(max_retries=3, backoff=BackoffStrategy.EXPONENTIAL, base_delay_seconds=1.0)
        assert [policy.delay_for(n) for n in range(1, 4)] == [1.0, 2.0, 4.0]


class TestRetryLifecycle:
    """TC-012..017: Retry behaviour in AsyncTaskManager."""

    @pytest.fixture
    def failing_executor(self) -> Executor:
        reg = Registry()
        reg.register("test.failing", FailingModule())
        return Executor(registry=reg)

    @pytest.mark.asyncio
    async def test_no_retry_policy_goes_straight_to_failed(self, failing_executor: Executor) -> None:
        """TC-012: without retry_policy, task goes FAILED immediately."""
        mgr = AsyncTaskManager(failing_executor)
        task_id = await mgr.submit("test.failing", {})
        await asyncio.sleep(0.1)
        info = mgr.get_status(task_id)
        assert info is not None
        assert info.status == TaskStatus.FAILED
        assert info.attempt_number == 0

    @pytest.mark.asyncio
    async def test_retry_exhausted_then_failed(self, failing_executor: Executor) -> None:
        """TC-013: after max_retries the task ends FAILED with correct attempt_number."""
        policy = RetryPolicy(max_retries=2, backoff=BackoffStrategy.FIXED, base_delay_seconds=0.01)
        mgr = AsyncTaskManager(failing_executor)
        task_id = await mgr.submit("test.failing", {}, retry_policy=policy)
        await asyncio.sleep(0.5)
        info = mgr.get_status(task_id)
        assert info is not None
        assert info.status == TaskStatus.FAILED
        assert info.attempt_number == 2
        assert info.max_retries == 2

    @pytest.mark.asyncio
    async def test_status_sequence_includes_retrying(self, failing_executor: Executor) -> None:
        """TC-015: status transitions include RETRYING between attempts."""
        spy = _SpyStore()
        policy = RetryPolicy(max_retries=1, backoff=BackoffStrategy.FIXED, base_delay_seconds=0.01)
        mgr = AsyncTaskManager(failing_executor, store=spy)
        await mgr.submit("test.failing", {}, retry_policy=policy)
        await asyncio.sleep(0.3)

        assert TaskStatus.RETRYING in spy.put_statuses
        # Verify ordering: first RUNNING before first RETRYING
        first_running = spy.put_statuses.index(TaskStatus.RUNNING)
        first_retrying = spy.put_statuses.index(TaskStatus.RETRYING)
        assert first_running < first_retrying

    @pytest.mark.asyncio
    async def test_succeeds_on_retry(self, executor: Executor) -> None:
        """TC-016: task completes on retry N with attempt_number == N."""
        counting = _CountingModule(fail_times=1)
        reg = Registry()
        reg.register("test.counting", counting)
        exec_ = Executor(registry=reg)
        policy = RetryPolicy(max_retries=2, backoff=BackoffStrategy.FIXED, base_delay_seconds=0.01)
        mgr = AsyncTaskManager(exec_)
        task_id = await mgr.submit("test.counting", {}, retry_policy=policy)
        await asyncio.sleep(0.3)
        info = mgr.get_status(task_id)
        assert info is not None
        assert info.status == TaskStatus.COMPLETED
        assert info.attempt_number == 1

    @pytest.mark.asyncio
    async def test_cancel_during_retrying(self, failing_executor: Executor) -> None:
        """TC-017: cancelling while in RETRYING state stops the retry loop."""
        # Long delay so the task will be in RETRYING when we cancel
        policy = RetryPolicy(max_retries=5, backoff=BackoffStrategy.FIXED, base_delay_seconds=10.0)
        mgr = AsyncTaskManager(failing_executor)
        task_id = await mgr.submit("test.failing", {}, retry_policy=policy)

        # Wait long enough for first attempt to fail and enter RETRYING
        await asyncio.sleep(0.2)
        info = mgr.get_status(task_id)
        assert info is not None
        assert info.status == TaskStatus.RETRYING

        cancelled = await mgr.cancel(task_id)
        assert cancelled is True
        info = mgr.get_status(task_id)
        assert info is not None
        assert info.status == TaskStatus.CANCELLED


class TestReaper:
    """TC-018..023: Reaper lifecycle and auto-cleanup."""

    @pytest.mark.asyncio
    async def test_reaper_removes_terminal_tasks(self, executor: Executor) -> None:
        """TC-018: terminal tasks are removed automatically after max_age."""
        mgr = AsyncTaskManager(executor)
        task_id = await mgr.submit("test.simple", {"x": 1})
        await asyncio.sleep(0.1)
        assert mgr.get_status(task_id) is not None

        mgr.start_reaper(interval_seconds=0.05, max_age_seconds=0.0)
        await asyncio.sleep(0.2)
        mgr.stop_reaper()

        assert mgr.get_status(task_id) is None

    @pytest.mark.asyncio
    async def test_reaper_does_not_remove_active_tasks(self, executor: Executor) -> None:
        """TC-019: active tasks are not removed by the reaper."""
        reg = Registry()
        reg.register("test.slow", SlowAsyncModule())
        exec_ = Executor(registry=reg)
        mgr = AsyncTaskManager(exec_)
        task_id = await mgr.submit("test.slow", {"delay": 10.0})
        await asyncio.sleep(0.05)

        mgr.start_reaper(interval_seconds=0.05, max_age_seconds=0.0)
        await asyncio.sleep(0.2)
        mgr.stop_reaper()

        info = mgr.get_status(task_id)
        assert info is not None
        assert info.status in (TaskStatus.PENDING, TaskStatus.RUNNING)
        await mgr.cancel(task_id)

    @pytest.mark.asyncio
    async def test_stop_reaper_halts_cleanup(self, executor: Executor) -> None:
        """TC-020: stop_reaper() prevents further automatic cleanup."""
        mgr = AsyncTaskManager(executor)
        mgr.start_reaper(interval_seconds=0.05, max_age_seconds=0.0)
        mgr.stop_reaper()

        task_id = await mgr.submit("test.simple", {"x": 1})
        await asyncio.sleep(0.2)

        # Reaper is stopped; task should still be present
        assert mgr.get_status(task_id) is not None

    @pytest.mark.asyncio
    async def test_shutdown_stops_reaper(self, executor: Executor) -> None:
        """TC-021: shutdown() stops the reaper without error."""
        mgr = AsyncTaskManager(executor)
        mgr.start_reaper(interval_seconds=60.0)
        await mgr.shutdown()
        # No exception means reaper was stopped cleanly

    def test_stop_reaper_noop_when_not_running(self, executor: Executor) -> None:
        """TC-022: stop_reaper() is safe when no reaper is active."""
        mgr = AsyncTaskManager(executor)
        mgr.stop_reaper()  # must not raise

    @pytest.mark.asyncio
    async def test_double_start_reaper_raises(self, executor: Executor) -> None:
        """TC-023: starting the reaper twice raises RuntimeError."""
        mgr = AsyncTaskManager(executor)
        mgr.start_reaper(interval_seconds=60.0)
        with pytest.raises(RuntimeError, match="already running"):
            mgr.start_reaper(interval_seconds=60.0)
        mgr.stop_reaper()


class TestRegression:
    """TC-024/025: Ensure existing behaviour is unchanged."""

    def test_all_task_statuses_present(self) -> None:
        """TC-024: TaskStatus has all six expected values."""
        expected = {
            "pending",
            "running",
            "completed",
            "failed",
            "cancelled",
            "retrying",
        }
        actual = {s.value for s in TaskStatus}
        assert expected == actual

    @pytest.mark.asyncio
    async def test_existing_api_unchanged(self, executor: Executor) -> None:
        """TC-025: basic lifecycle without optional args still works."""
        mgr = AsyncTaskManager(executor)
        task_id = await mgr.submit("test.simple", {"x": 5})
        await asyncio.sleep(0.1)

        info = mgr.get_status(task_id)
        assert info is not None
        assert info.status == TaskStatus.COMPLETED
        assert mgr.get_result(task_id) == {"value": 5}
        assert len(mgr.list_tasks()) == 1

        removed = mgr.cleanup(max_age_seconds=0.0)
        assert removed == 1

        await mgr.shutdown()
