# Task: MiddlewareManager with Onion Model Execution

## Goal

Implement the `MiddlewareManager` that orchestrates the middleware pipeline using the onion execution model with three phases (before, after, on_error), along with the `MiddlewareChainError` exception type and the thread-safe snapshot pattern for concurrent access.

## Files Involved

- `src/apcore/middleware/manager.py` -- MiddlewareManager class, MiddlewareChainError (129 lines)
- `tests/test_middleware_manager.py` -- Manager unit tests

## Steps

1. **Define MiddlewareChainError** (TDD: test original and executed_middlewares properties)
   - Subclass of `Exception`
   - `__init__(self, original: Exception, executed_middlewares: list[Middleware])`
   - Store `original` (root cause exception) and `executed_middlewares` (list of middlewares whose before() was called)

2. **Implement MiddlewareManager.__init__** (TDD: test empty initialization)
   - Initialize `_middlewares: list[Middleware] = []`
   - Initialize `_lock = threading.Lock()`

3. **Implement add() and remove()** (TDD: test append order, identity-based removal, return value)
   - `add(middleware)`: append under lock
   - `remove(middleware)`: identity-based (is, not ==) search under lock; return True if found, False otherwise

4. **Implement snapshot()** (TDD: test copy semantics, thread safety)
   - Acquire lock, copy list, release lock, return copy
   - Callers iterate the snapshot without holding the lock

5. **Implement execute_before()** (TDD: test order, input replacement, None passthrough, error wrapping)
   - Take snapshot, iterate in registration order
   - For each middleware: append to executed_middlewares, call `mw.before()`
   - If before() raises: wrap in `MiddlewareChainError` with executed_middlewares
   - If before() returns a dict: replace current inputs
   - If before() returns None: pass through unchanged
   - Return `(final_inputs, executed_middlewares)`

6. **Implement execute_after()** (TDD: test reverse order, output replacement, None passthrough, exception propagation)
   - Take snapshot, iterate in REVERSE registration order
   - If after() returns a dict: replace current output
   - If after() returns None: pass through unchanged
   - Exceptions propagate directly (no wrapping)
   - Return final output

7. **Implement execute_on_error()** (TDD: test reverse iteration, first-dict-wins, exception in handler)
   - Iterate `executed_middlewares` in reverse order (not full snapshot)
   - Call `mw.on_error()`; if returns a dict, return it immediately (first-wins recovery)
   - If on_error() raises: log error with exc_info and continue to next handler
   - If no handler recovers: return None

## Acceptance Criteria

- execute_before runs middlewares in registration order
- execute_after runs middlewares in reverse registration order
- execute_on_error iterates only the executed_middlewares list in reverse
- MiddlewareChainError carries original exception and executed_middlewares
- Input replacement works: non-None return from before() replaces inputs
- Output replacement works: non-None return from after() replaces output
- First-dict-wins in on_error: first non-None dict is returned immediately
- Exception in on_error handler is logged and iteration continues
- Empty middleware list: execute_before returns original inputs and empty list; execute_after returns original output; execute_on_error returns None
- Thread safety: concurrent add/remove with snapshot causes no errors or data corruption
- Snapshot returns a copy; mutations to the returned list do not affect the manager

## Dependencies

- Task: base (Middleware class)

## Estimated Time

2.5 hours
