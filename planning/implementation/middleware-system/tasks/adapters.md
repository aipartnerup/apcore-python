# Task: BeforeMiddleware and AfterMiddleware Function Wrappers

## Goal

Implement lightweight adapter classes that wrap plain callback functions as full `Middleware` subclass instances, reducing boilerplate for single-phase hooks. `BeforeMiddleware` wraps a before-only callback; `AfterMiddleware` wraps an after-only callback.

## Files Involved

- `src/apcore/middleware/adapters.py` -- BeforeMiddleware, AfterMiddleware classes (43 lines)
- `tests/test_middleware.py` -- Adapter unit tests

## Steps

1. **Implement BeforeMiddleware** (TDD: test it is a Middleware subclass, delegates before(), no-ops for after/on_error)
   - Subclass of `Middleware`
   - `__init__(self, callback: Callable[[str, dict[str, Any], Context], dict[str, Any] | None])`
   - Store callback as `_callback`
   - Override `before()`: delegate to `self._callback(module_id, inputs, context)`
   - `after()` and `on_error()` inherited from Middleware (return None)

2. **Implement AfterMiddleware** (TDD: test it is a Middleware subclass, delegates after(), no-ops for before/on_error)
   - Subclass of `Middleware`
   - `__init__(self, callback: Callable[[str, dict[str, Any], dict[str, Any], Context], dict[str, Any] | None])`
   - Store callback as `_callback`
   - Override `after()`: delegate to `self._callback(module_id, inputs, output, context)`
   - `before()` and `on_error()` inherited from Middleware (return None)

3. **Test argument forwarding** (TDD: verify all arguments are passed correctly to callback)
   - Verify module_id, inputs, context are forwarded to before callback
   - Verify module_id, inputs, output, context are forwarded to after callback

4. **Test return value passthrough** (TDD: verify callback return value is returned by adapter)
   - Callback returning a dict: adapter returns the dict
   - Callback returning None: adapter returns None

## Acceptance Criteria

- BeforeMiddleware is a Middleware subclass (isinstance check passes)
- AfterMiddleware is a Middleware subclass (isinstance check passes)
- BeforeMiddleware.before() delegates to the wrapped callback with correct arguments
- AfterMiddleware.after() delegates to the wrapped callback with correct arguments
- Non-overridden methods (after/on_error for Before, before/on_error for After) return None
- Callback return values are passed through unchanged
- Both adapters work correctly when used with MiddlewareManager

## Dependencies

- Task: base (Middleware class to subclass)

## Estimated Time

45 minutes
