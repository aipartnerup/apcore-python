# Task: ContextLogger with Structured Logging and Redaction

## Goal

Implement a standalone `ContextLogger` that provides structured logging in JSON and text formats, with context injection (trace_id, module_id, caller_id), configurable log levels, and automatic redaction of sensitive fields.

## Files Involved

- `src/apcore/observability/context_logger.py` -- `ContextLogger` class
- `tests/test_context_logger.py` -- Unit tests for ContextLogger

## Steps

### 1. Write failing tests (TDD)

Create tests for:
- **JSON format**: Output is valid JSON with fields: timestamp, level, message, trace_id, module_id, caller_id, logger, extra
- **Text format**: Output follows pattern `YYYY-MM-DD HH:MM:SS [LEVEL] [trace=X] [module=X] message key=value`
- **Log levels**: 6 levels (trace=0, debug=10, info=20, warn=30, error=40, fatal=50); messages below configured level are suppressed
- **Level methods**: `trace()`, `debug()`, `info()`, `warn()`, `error()`, `fatal()` convenience methods
- **Redaction**: Keys starting with `_secret_` are replaced with `***REDACTED***` in extra dict
- **Redaction disabled**: When `redact_sensitive=False`, secrets are not redacted
- **from_context()**: Creates logger with trace_id, module_id, caller_id injected from Context
- **Custom output**: Writes to provided output stream (e.g., `io.StringIO` for testing)
- **Default output**: Writes to `sys.stderr` when no output specified

### 2. Implement ContextLogger

- `__init__(name, format="json", level="info", redact_sensitive=True, output=None)` with defaults
- `from_context(context, name, **kwargs)` classmethod for context injection
- `_emit(level_name, message, extra)`: Check level threshold, apply redaction, format and write
- JSON format: `json.dumps(entry, default=str)` + newline
- Text format: `{ts} [{LVL}] [trace={trace}] [module={mod}] {message} key=value`
- Convenience methods: `trace()`, `debug()`, `info()`, `warn()`, `error()`, `fatal()`

### 3. Verify tests pass

Run `pytest tests/test_context_logger.py -v`.

## Acceptance Criteria

- [x] JSON format produces valid JSON with all expected fields
- [x] Text format produces human-readable structured log lines
- [x] 6 log levels with correct threshold filtering
- [x] Sensitive field redaction for keys starting with `_secret_`
- [x] `from_context()` injects trace_id, module_id (last in call_chain), caller_id
- [x] Configurable output stream, defaults to `sys.stderr`
- [x] Timestamps in UTC ISO format (JSON) and `YYYY-MM-DD HH:MM:SS` (text)

## Dependencies

None -- ContextLogger is a standalone component with no internal dependencies beyond `apcore.middleware.Middleware` (only used by ObsLoggingMiddleware, not ContextLogger itself).

## Estimated Time

2.5 hours
