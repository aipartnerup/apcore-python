# Feature: AsyncTask Evolution (Issue #34)

## Goal

Evolve `AsyncTaskManager` with three capabilities: a pluggable `TaskStore` abstraction for
task-state storage, a `RetryPolicy` mechanism with configurable backoff for failed tasks,
and a background `Reaper` that automatically removes stale terminal-state tasks.

## Scope

### In Scope

- `TaskStore` protocol (sync) with `get / put / delete / list` operations
- `InMemoryTaskStore` as the default implementation (replaces `self._tasks: dict`)
- `AsyncTaskManager.__init__` accepts optional `store: TaskStore` (default: `InMemoryTaskStore`)
- `RetryPolicy` dataclass: `max_retries`, `backoff` (`"fixed" | "linear" | "exponential"`),
  `base_delay_seconds`
- `submit()` accepts optional `retry_policy: RetryPolicy | None`
- `TaskInfo` gains `attempt_number: int = 0` and `max_retries: int = 0`
- New `TaskStatus.RETRYING` state visible during the backoff delay window
- Reaper: `AsyncTaskManager.start_reaper(interval_seconds, max_age_seconds)` and
  `stop_reaper()` methods; `shutdown()` stops the reaper automatically
- Updated public exports in `apcore/__init__.py`:
  `TaskStore`, `InMemoryTaskStore`, `RetryPolicy`
- Full test coverage (≥90%) for all new paths

### Out of Scope

- Persistent store implementations (Redis, PostgreSQL, SQLite)
- Async `TaskStore` variant (can be added later following the ACL handler dual-protocol pattern)
- Task priorities or dependencies
- Distributed task queues

## Affected Modules

- `src/apcore/async_task.py` — all new logic lives here; `TaskInfo`, `TaskStatus`,
  `AsyncTaskManager` all extended
- `src/apcore/__init__.py` — add `TaskStore`, `InMemoryTaskStore`, `RetryPolicy` to exports
- `src/apcore/errors.py` — no new errors expected; existing `TaskLimitExceededError` unchanged
- `tests/test_async_task.py` — extend with new test classes for store, retry, reaper

## New Types / Modules

No new files. All additions are in `src/apcore/async_task.py`.

## Technical Approach

### TaskStore Protocol + InMemoryTaskStore

```python
class TaskStore(Protocol):
    def get(self, task_id: str) -> TaskInfo | None: ...
    def put(self, info: TaskInfo) -> None: ...
    def delete(self, task_id: str) -> None: ...
    def list(self, status: TaskStatus | None = None) -> list[TaskInfo]: ...

class InMemoryTaskStore:
    def __init__(self) -> None:
        self._data: dict[str, TaskInfo] = {}
    def get(self, task_id: str) -> TaskInfo | None: ...
    def put(self, info: TaskInfo) -> None: ...
    def delete(self, task_id: str) -> None: ...
    def list(self, status: TaskStatus | None = None) -> list[TaskInfo]: ...
```

`AsyncTaskManager.__init__` changes:
- Remove `self._tasks: dict[str, TaskInfo]`
- Add `self._store: TaskStore = store or InMemoryTaskStore()`
- All internal references to `self._tasks` route through `self._store`

### RetryPolicy + RETRYING Status

```python
class BackoffStrategy(str, Enum):
    FIXED = "fixed"
    LINEAR = "linear"
    EXPONENTIAL = "exponential"

@dataclass
class RetryPolicy:
    max_retries: int = 3
    backoff: BackoffStrategy = BackoffStrategy.EXPONENTIAL
    base_delay_seconds: float = 1.0

    def delay_for(self, attempt: int) -> float:
        """Return wait seconds before attempt `attempt` (1-indexed)."""
```

Delay formulas (attempt is 1-indexed retry number):
- `fixed`:       `base_delay_seconds`
- `linear`:      `base_delay_seconds * attempt`
- `exponential`: `base_delay_seconds * 2 ** (attempt - 1)`

`TaskInfo` new fields:
```python
attempt_number: int = 0   # how many times this task has been tried
max_retries: int = 0      # copied from RetryPolicy.max_retries at submit time
```

`TaskStatus.RETRYING = "retrying"` — set while waiting during backoff delay.

`_run()` retry loop:
```
while True:
    try: execute → COMPLETED; break
    except CancelledError: CANCELLED; break
    except Exception:
        if attempt_number < max_retries:
            status = RETRYING
            await asyncio.sleep(policy.delay_for(attempt_number + 1))
            attempt_number += 1
        else:
            status = FAILED; break
```

### Reaper

```python
def start_reaper(
    self,
    interval_seconds: float = 3600.0,
    max_age_seconds: float = 3600.0,
) -> None: ...

def stop_reaper(self) -> None: ...
```

Implementation: creates an `asyncio.Task` running a loop:
```
while True:
    await asyncio.sleep(interval_seconds)
    self.cleanup(max_age_seconds)
```

`shutdown()` calls `stop_reaper()` before cancelling worker tasks.

Double-start guard: calling `start_reaper()` when already running raises `RuntimeError`.

## Acceptance Criteria

- `TaskStore` protocol is structurally compatible with `InMemoryTaskStore` (verified by
  `isinstance` check or type: `assert isinstance(InMemoryTaskStore(), TaskStore)`)
- `AsyncTaskManager` accepts a custom `TaskStore` at construction time; all task state
  flows through it
- A task that fails and has `RetryPolicy(max_retries=2)` retries twice before entering
  `FAILED`; `attempt_number` on the final `TaskInfo` equals 2
- Status transitions during retry: RUNNING → RETRYING → RUNNING → ... → FAILED
- Delay values match the formula for each backoff strategy
- After `start_reaper()`, terminal-state tasks older than `max_age_seconds` are removed
  automatically without calling `cleanup()` manually
- `stop_reaper()` halts the background task within one sleep cycle
- `shutdown()` stops the reaper and cancels all worker tasks
- Calling `start_reaper()` twice raises `RuntimeError`
- All existing tests continue to pass (no regressions)
- `TaskStore`, `InMemoryTaskStore`, `RetryPolicy` are importable from `apcore`
