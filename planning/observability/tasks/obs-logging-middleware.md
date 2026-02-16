# Task: ObsLoggingMiddleware with Stack-Based Timing

## Goal

Implement `ObsLoggingMiddleware` that integrates `ContextLogger` into the apcore middleware pipeline for structured observability logging. Uses stack-based timing in `context.data` for safe nested module call support. Logs module call start, completion, and failure events.

## Files Involved

- `src/apcore/observability/context_logger.py` -- `ObsLoggingMiddleware` class
- `src/apcore/middleware/base.py` -- `Middleware` base class (parent)
- `tests/test_context_logger.py` -- Unit tests for ObsLoggingMiddleware

## Steps

### 1. Write failing tests (TDD)

Create tests for:
- **before()**: Pushes start time to `context.data["_obs_logging_starts"]` stack; logs "Module call started" with module_id, caller_id, and optionally inputs
- **after()**: Pops start time, calculates duration_ms; logs "Module call completed" with module_id, duration_ms, and optionally output
- **on_error()**: Pops start time, calculates duration_ms; logs "Module call failed" at error level with module_id, duration_ms, error_type, error_message
- **log_inputs option**: When `log_inputs=True` (default), inputs are included in before() log; when False, omitted
- **log_outputs option**: When `log_outputs=True` (default), output is included in after() log; when False, omitted
- **Redacted inputs**: Uses `context.redacted_inputs` if available, falls back to raw inputs
- **Default logger**: Creates `ContextLogger(name="apcore.obs_logging")` when no logger provided
- **Nested calls**: Stack-based timing correctly attributes duration to each module

### 2. Implement ObsLoggingMiddleware

```python
class ObsLoggingMiddleware(Middleware):
    def __init__(self, logger=None, log_inputs=True, log_outputs=True):
        self._logger = logger or ContextLogger(name="apcore.obs_logging")
        self._log_inputs = log_inputs
        self._log_outputs = log_outputs

    def before(self, module_id, inputs, context):
        context.data.setdefault("_obs_logging_starts", []).append(time.time())
        extra = {"module_id": module_id, "caller_id": context.caller_id}
        if self._log_inputs:
            extra["inputs"] = getattr(context, "redacted_inputs", None) or inputs
        self._logger.info("Module call started", extra=extra)
        return None

    def after(self, module_id, inputs, output, context):
        start_time = context.data["_obs_logging_starts"].pop()
        duration_ms = (time.time() - start_time) * 1000
        extra = {"module_id": module_id, "duration_ms": duration_ms}
        if self._log_outputs:
            extra["output"] = output
        self._logger.info("Module call completed", extra=extra)
        return None

    def on_error(self, module_id, inputs, error, context):
        start_time = context.data["_obs_logging_starts"].pop()
        duration_ms = (time.time() - start_time) * 1000
        self._logger.error("Module call failed", extra={
            "module_id": module_id,
            "duration_ms": duration_ms,
            "error_type": type(error).__name__,
            "error_message": str(error),
        })
        return None
```

### 3. Verify tests pass

Run `pytest tests/test_context_logger.py -k "middleware" -v`.

## Acceptance Criteria

- [x] Uses stack-based timing via `context.data["_obs_logging_starts"]` for nested call safety
- [x] Logs "Module call started" at info level in `before()`
- [x] Logs "Module call completed" at info level with duration_ms in `after()`
- [x] Logs "Module call failed" at error level with duration_ms, error_type, error_message in `on_error()`
- [x] Configurable `log_inputs` and `log_outputs` flags
- [x] Uses `context.redacted_inputs` when available for privacy
- [x] Creates default ContextLogger when none provided
- [x] All three methods return None

## Dependencies

- `context-logger` -- `ContextLogger` must be implemented for structured logging output

## Estimated Time

1.5 hours
