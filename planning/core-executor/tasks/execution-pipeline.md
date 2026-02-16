# Task: 10-Step Synchronous Execution Pipeline

## Goal

Implement the complete synchronous execution pipeline in the `Executor.call()` method, integrating all 10 steps: context creation, safety checks, module lookup, ACL enforcement, input validation with redaction, middleware before chain, module execution with timeout, output validation, middleware after chain, and result return.

## Files Involved

- `src/apcore/executor.py` -- `Executor.__init__()`, `call()`, `validate()`, `_execute_with_timeout()`, middleware registration methods (634 lines total)
- `src/apcore/errors.py` -- `ModuleNotFoundError`, `ACLDeniedError`, `SchemaValidationError`, `ModuleTimeoutError`, `InvalidInputError`
- `src/apcore/module.py` -- `ValidationResult` dataclass
- `tests/test_executor.py` -- Full pipeline unit and integration tests

## Steps

1. **Implement Executor.__init__** (TDD: test initialization with/without config, middleware, ACL)
   - Accept `registry`, `middlewares`, `acl`, `config` parameters
   - Initialize `MiddlewareManager` and register provided middlewares
   - Read config values for `default_timeout` (30000ms), `global_timeout` (60000ms), `max_call_depth` (32), `max_module_repeat` (3)
   - Initialize `_async_cache: dict[str, bool]` with `threading.Lock`

2. **Implement middleware registration** (TDD: test use(), use_before(), use_after(), remove())
   - `use(middleware)` -- adds class-based middleware, returns self for chaining
   - `use_before(callback)` -- wraps in BeforeMiddleware adapter
   - `use_after(callback)` -- wraps in AfterMiddleware adapter
   - `remove(middleware)` -- delegates to MiddlewareManager.remove()

3. **Implement call() pipeline** (TDD: test each step in isolation and end-to-end)
   - Step 1: Create or derive Context via `Context.create()` + `child()` or `context.child()`
   - Step 2: Run `_check_safety(module_id, ctx)`
   - Step 3: `registry.get(module_id)`, raise `ModuleNotFoundError` if None
   - Step 4: `acl.check()` if ACL configured, raise `ACLDeniedError` if denied
   - Step 5: `module.input_schema.model_validate(inputs)`, build redacted_inputs
   - Step 6: `execute_before()`, handle `MiddlewareChainError` with on_error recovery
   - Step 7: `_execute_with_timeout()` with daemon thread
   - Step 8: `module.output_schema.model_validate(output)`
   - Step 9: `execute_after()` in reverse order
   - Step 10: Return output or propagate error with on_error recovery

4. **Implement _execute_with_timeout** (TDD: test normal execution, timeout, zero timeout, negative timeout)
   - Check for async module via `_is_async_module()` (cached check)
   - Sync path: daemon thread with `thread.join(timeout_ms / 1000)`
   - Raise `ModuleTimeoutError` if thread is still alive after join
   - Handle exception propagation from thread via holder dicts
   - Zero timeout: log warning, execute without timeout
   - Negative timeout: raise `InvalidInputError`

5. **Implement validate()** (TDD: test valid input, invalid input, missing schema, missing module)
   - Standalone pre-flight check without execution
   - Returns `ValidationResult(valid=True/False, errors=[...])`
   - Raises `ModuleNotFoundError` if module not found

## Acceptance Criteria

- All 10 steps execute in order for the success path
- Each step can independently raise its specific error type
- MiddlewareChainError triggers on_error recovery before re-raising
- Outer exception handler catches errors from steps 6-9 and runs on_error on executed middlewares
- Recovery output from on_error short-circuits the error and returns the recovery dict
- Timeout enforcement uses daemon threads that do not prevent process exit
- validate() returns structured errors without executing the module

## Dependencies

- Task: setup (Context, Config)
- Task: safety-checks (_check_safety method)
- Registry system (module lookup)
- Middleware system (MiddlewareManager)
- Schema system (Pydantic model validation)

## Estimated Time

4 hours
