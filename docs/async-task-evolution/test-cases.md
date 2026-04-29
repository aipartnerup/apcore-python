# Test Cases: AsyncTask Evolution (Issue #34)

## Coverage Matrix

| Dimension | Cases |
|---|---|
| TaskStore protocol conformance | TC-001, TC-002 |
| InMemoryTaskStore operations | TC-003, TC-004, TC-005, TC-006 |
| AsyncTaskManager + custom store | TC-007, TC-008 |
| RetryPolicy.delay_for() formulas | TC-009, TC-010, TC-011 |
| Retry lifecycle (attempt_number, status) | TC-012, TC-013, TC-014, TC-015, TC-016 |
| Retry + cancellation interaction | TC-017 |
| Reaper auto-cleanup | TC-018, TC-019 |
| Reaper lifecycle (start/stop/shutdown) | TC-020, TC-021, TC-022 |
| Reaper guard (double-start) | TC-023 |
| Regression: existing behaviour unchanged | TC-024, TC-025 |

---

## Test Cases

### TaskStore — Protocol Conformance

**TC-001** `InMemoryTaskStore satisfies TaskStore protocol`
- Pre: none
- Steps: `from apcore.async_task import InMemoryTaskStore, TaskStore; assert isinstance(InMemoryTaskStore(), TaskStore)`
- Expected: no assertion error
- Priority: P0

**TC-002** `Custom class with get/put/delete/list satisfies TaskStore protocol`
- Pre: Define a minimal class implementing all four methods with correct signatures
- Steps: `isinstance(MinimalStore(), TaskStore)` → True
- Expected: True
- Priority: P1

---

### InMemoryTaskStore — Operations

**TC-003** `put() then get() returns same TaskInfo`
- Pre: empty store
- Steps: `store.put(info); result = store.get(info.task_id)`
- Expected: `result is info`
- Priority: P0

**TC-004** `get() for unknown id returns None`
- Pre: empty store
- Steps: `store.get("no-such-id")`
- Expected: `None`
- Priority: P0

**TC-005** `delete() removes entry; get() returns None afterwards`
- Pre: `store.put(info)`
- Steps: `store.delete(info.task_id); store.get(info.task_id)`
- Expected: `None`
- Priority: P0

**TC-006** `list() with status filter returns only matching entries`
- Pre: put one COMPLETED and one FAILED TaskInfo
- Steps: `store.list(status=TaskStatus.COMPLETED)`
- Expected: list of length 1 containing the COMPLETED entry
- Priority: P0

---

### AsyncTaskManager + Custom Store

**TC-007** `AsyncTaskManager accepts custom TaskStore at construction`
- Pre: instantiate `SpyStore` that records all calls
- Steps: `mgr = AsyncTaskManager(executor, store=spy); await mgr.submit("test.simple", {}); await asyncio.sleep(0.1)`
- Expected: `spy.put_calls` contains at least 2 entries (PENDING, then COMPLETED); `mgr.get_status()` routes through spy
- Priority: P0

**TC-008** `All task state transitions call store.put()`
- Pre: SpyStore records every `put(info)` call
- Steps: submit a simple module, wait for completion
- Expected: put called with statuses [PENDING, RUNNING, COMPLETED] in that order
- Priority: P1

---

### RetryPolicy — Delay Formulas

**TC-009** `RetryPolicy.delay_for() fixed strategy`
- Pre: `policy = RetryPolicy(max_retries=3, backoff=BackoffStrategy.FIXED, base_delay_seconds=2.0)`
- Steps: `[policy.delay_for(n) for n in range(1, 4)]`
- Expected: `[2.0, 2.0, 2.0]`
- Priority: P0

**TC-010** `RetryPolicy.delay_for() linear strategy`
- Pre: `policy = RetryPolicy(max_retries=3, backoff=BackoffStrategy.LINEAR, base_delay_seconds=1.0)`
- Steps: `[policy.delay_for(n) for n in range(1, 4)]`
- Expected: `[1.0, 2.0, 3.0]`
- Priority: P0

**TC-011** `RetryPolicy.delay_for() exponential strategy`
- Pre: `policy = RetryPolicy(max_retries=3, backoff=BackoffStrategy.EXPONENTIAL, base_delay_seconds=1.0)`
- Steps: `[policy.delay_for(n) for n in range(1, 4)]`
- Expected: `[1.0, 2.0, 4.0]`
- Priority: P0

---

### Retry Lifecycle

**TC-012** `Task with no retry_policy goes straight to FAILED on error`
- Pre: register `FailingModule`; no retry_policy
- Steps: `await mgr.submit("failing", {}); await asyncio.sleep(0.1); mgr.get_status(tid)`
- Expected: status == FAILED, attempt_number == 0
- Priority: P0

**TC-013** `Task with retry_policy retries up to max_retries then FAILED`
- Pre: `FailingModule` always raises; `RetryPolicy(max_retries=2, backoff=FIXED, base_delay_seconds=0.01)`
- Steps: submit, wait long enough for all retries, check final TaskInfo
- Expected: status == FAILED, attempt_number == 2, max_retries == 2
- Priority: P0

**TC-014** `attempt_number increments on each retry`
- Pre: SpyStore records all put() calls; `RetryPolicy(max_retries=2, base_delay_seconds=0.01)`; FailingModule
- Steps: submit, wait, inspect put() history
- Expected: put() called with attempt_number 0, 1, 2 over the task lifetime
- Priority: P1

**TC-015** `Status transitions: RUNNING → RETRYING → RUNNING → FAILED`
- Pre: SpyStore; `RetryPolicy(max_retries=1, backoff=FIXED, base_delay_seconds=0.01)`; FailingModule
- Steps: collect statuses from all put() calls
- Expected: sequence includes RUNNING, RETRYING, RUNNING, FAILED
- Priority: P0

**TC-016** `Task succeeds on retry N: status COMPLETED, attempt_number == N`
- Pre: Module that fails on attempt 0, succeeds on attempt 1;
  `RetryPolicy(max_retries=2, backoff=FIXED, base_delay_seconds=0.01)`
- Steps: submit, wait, check final TaskInfo
- Expected: status == COMPLETED, attempt_number == 1
- Priority: P0

**TC-017** `Cancelling a RETRYING task stops the retry loop`
- Pre: FailingModule; `RetryPolicy(max_retries=5, base_delay_seconds=10.0)` (long delay so task is in RETRYING state)
- Steps: submit, wait for RETRYING status, cancel
- Expected: status == CANCELLED, further retries do not execute
- Priority: P1

---

### Reaper — Auto-cleanup

**TC-018** `Reaper removes terminal tasks older than max_age automatically`
- Pre: `mgr = AsyncTaskManager(executor); await mgr.start_reaper(interval_seconds=0.05, max_age_seconds=0.0)`
- Steps: submit simple module, wait for completion, wait 2× interval, check
- Expected: task no longer returned by `get_status()`
- Priority: P0

**TC-019** `Reaper does NOT remove active (PENDING/RUNNING/RETRYING) tasks`
- Pre: reaper running; slow module submitted
- Steps: wait one interval cycle, check slow task
- Expected: slow task still present with status PENDING or RUNNING
- Priority: P0

---

### Reaper Lifecycle

**TC-020** `stop_reaper() halts background cleanup after next cycle`
- Pre: start reaper with short interval
- Steps: `mgr.stop_reaper()`; submit and complete a task; wait 2× interval
- Expected: completed task still present (reaper no longer running)
- Priority: P1

**TC-021** `shutdown() stops the reaper automatically`
- Pre: reaper started
- Steps: `await mgr.shutdown()`
- Expected: no background reaper task remains; no errors raised
- Priority: P0

**TC-022** `stop_reaper() when no reaper is running is a no-op`
- Pre: reaper never started
- Steps: `mgr.stop_reaper()`
- Expected: no error raised
- Priority: P1

**TC-023** `start_reaper() twice raises RuntimeError`
- Pre: reaper already started
- Steps: call `mgr.start_reaper()` again
- Expected: `RuntimeError` raised; original reaper continues running
- Priority: P0

---

### Regression

**TC-024** `All existing TaskStatus values still present`
- Pre: none
- Steps: check PENDING, RUNNING, COMPLETED, FAILED, CANCELLED, RETRYING all exist
- Expected: all six values accessible on `TaskStatus`
- Priority: P0

**TC-025** `Existing AsyncTaskManager API unchanged when no retry_policy / no store / no reaper`
- Pre: construct `AsyncTaskManager(executor)` with no optional args
- Steps: run existing lifecycle (submit → complete, cancel, cleanup, shutdown)
- Expected: all existing tests pass without modification
- Priority: P0

---

## Gap Analysis

| Gap | Risk | Mitigation |
|---|---|---|
| Reaper timer precision under test may be flaky | Medium | Use `asyncio.sleep` multiples of interval in tests; keep intervals ≥ 50ms |
| SpyStore must be thread-safe if reaper runs concurrently | Low | asyncio single-threaded; list.append is safe |
| `delay_for(attempt=0)` edge case not explicitly tested | Low | Fixed/linear/exp all defined for attempt ≥ 1; document that attempt is 1-indexed |
| Retry + concurrency semaphore interaction | Low | RETRYING does not hold semaphore (sleep is outside `async with self._semaphore`) |
