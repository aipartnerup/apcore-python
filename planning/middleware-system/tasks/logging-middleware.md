# Task: LoggingMiddleware with Redaction Support

## Goal

Implement a structured logging middleware that logs module call start (with redacted inputs), completion (with duration and output), and errors (with redacted inputs and traceback). The middleware stores per-call timing state in `context.data` for thread safety and uses `context.redacted_inputs` to avoid leaking sensitive data.

## Files Involved

- `src/apcore/middleware/logging.py` -- LoggingMiddleware class (94 lines)
- `tests/test_middleware_logging.py` -- LoggingMiddleware unit tests

## Steps

1. **Implement LoggingMiddleware.__init__** (TDD: test default logger, custom logger, flag configuration)
   - Accept optional `logger: logging.Logger` (default: `logging.getLogger("apcore.middleware.logging")`)
   - Accept `log_inputs: bool = True`, `log_outputs: bool = True`, `log_errors: bool = True`
   - Store as instance attributes

2. **Implement before()** (TDD: test start time recording, structured log output, redacted inputs)
   - Record `time.time()` in `context.data["_logging_mw_start"]`
   - If `log_inputs` is True:
     - Use `context.redacted_inputs` if available, otherwise fall back to raw inputs
     - Log at INFO level: `[{trace_id}] START {module_id}`
     - Include extra dict: `trace_id`, `module_id`, `caller_id`, `inputs` (redacted)
   - Return None (no input modification)

3. **Implement after()** (TDD: test duration calculation, structured log output)
   - Retrieve `_logging_mw_start` from `context.data`; fall back to current time
   - Compute `duration_ms = (time.time() - start_time) * 1000`
   - If `log_outputs` is True:
     - Log at INFO level: `[{trace_id}] END {module_id} ({duration_ms:.2f}ms)`
     - Include extra dict: `trace_id`, `module_id`, `duration_ms`, `output`
   - Return None (no output modification)

4. **Implement on_error()** (TDD: test error logging with redacted inputs, exc_info)
   - If `log_errors` is True:
     - Use `context.redacted_inputs` if available, otherwise fall back to raw inputs
     - Log at ERROR level: `[{trace_id}] ERROR {module_id}: {error}`
     - Include extra dict: `trace_id`, `module_id`, `error` (str), `inputs` (redacted)
     - Log with `exc_info=True` for full traceback
   - Return None (no error recovery)

## Acceptance Criteria

- Start time is stored in context.data for per-call isolation (thread-safe)
- Duration is calculated as wall-clock milliseconds between before() and after()
- Redacted inputs are used for logging when available on context
- Raw inputs are used as fallback when redacted_inputs is None
- Each flag (log_inputs, log_outputs, log_errors) independently controls its log statement
- All log messages include trace_id in the format `[{trace_id}]`
- Error logging includes exc_info=True for full traceback
- All methods return None (LoggingMiddleware does not modify inputs, outputs, or recover from errors)
- Custom logger can be injected via constructor
- Default logger uses name "apcore.middleware.logging"

## Dependencies

- Task: base (Middleware class to subclass)
- `apcore.context.Context` (for trace_id, caller_id, redacted_inputs, data dict)

## Estimated Time

1.5 hours
