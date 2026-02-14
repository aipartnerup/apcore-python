# Task: Async Execution Path and Sync/Async Bridge

## Goal

Implement the async counterpart to the synchronous pipeline (`call_async()`), including native async module execution, async-aware middleware handling, and the sync-to-async / async-to-sync bridge mechanisms using daemon threads and event loops.

## Files Involved

- `src/apcore/executor.py` -- `call_async()`, `_execute_async()`, `_run_async_in_sync()`, `_run_in_new_thread()`, `_execute_on_error_async()`, `_is_async_module()` (lines 377-634)
- `tests/test_executor_async.py` -- Async pipeline tests

## Steps

1. **Implement _is_async_module** (TDD: test caching behavior, sync vs async handlers)
   - Thread-safe check with `_async_cache_lock`
   - Uses `inspect.iscoroutinefunction(module.execute)` with result cached per module_id
   - Cache prevents repeated introspection

2. **Implement call_async** (TDD: test full async pipeline with async/sync modules)
   - Same 10-step structure as `call()` but with async-aware middleware execution
   - Step 6: Iterate snapshot, check `inspect.iscoroutinefunction(mw.before)`, await if async
   - Step 7: Delegate to `_execute_async()`
   - Step 9: Reverse iteration with async-aware `mw.after()`
   - Error handling: `_execute_on_error_async()` with async-aware `mw.on_error()`

3. **Implement _execute_async** (TDD: test async module direct execution, sync module via to_thread)
   - Async modules: `await module.execute(inputs, ctx)` directly
   - Sync modules: `await asyncio.to_thread(module.execute, inputs, ctx)`
   - Timeout enforcement via `asyncio.wait_for(coro, timeout=timeout_s)`
   - TimeoutError mapped to `ModuleTimeoutError`
   - Zero timeout: log warning, execute without wait_for

4. **Implement _run_async_in_sync** (TDD: test no-loop case with asyncio.run, existing-loop case with new thread)
   - When no event loop exists: `asyncio.run(wrapped)` with optional `wait_for`
   - When event loop already running: delegate to `_run_in_new_thread()`
   - TimeoutError mapped to `ModuleTimeoutError`

5. **Implement _run_in_new_thread** (TDD: test daemon thread execution, timeout, error propagation)
   - Creates a new daemon thread with its own event loop (`asyncio.new_event_loop()`)
   - Runs coroutine via `loop.run_until_complete()`
   - Timeout via `asyncio.wait_for()` within the new loop
   - Result and exception propagation via holder dicts
   - Loop is always closed in `finally` block

6. **Implement _execute_on_error_async** (TDD: test async on_error handlers, recovery, exception swallowing)
   - Reverse iteration over executed middlewares
   - Check `inspect.iscoroutinefunction(mw.on_error)`, await if async
   - First handler returning a dict provides recovery output
   - Handler exceptions are logged and iteration continues

## Acceptance Criteria

- `call_async()` works with both sync and async modules
- Async modules are executed natively with `await`
- Sync modules in async context are dispatched via `asyncio.to_thread`
- Async modules in sync context bridge through daemon thread with new event loop
- Async module cache is thread-safe and prevents redundant introspection
- Timeout enforcement works for both async paths
- Async middleware methods are properly detected and awaited
- Error recovery via async on_error handlers works identically to sync path

## Dependencies

- Task: execution-pipeline (base call() implementation)
- Task: setup (Context)

## Estimated Time

3 hours
