# Task: MetricsMiddleware with Stack-Based Timing

## Goal

Implement `MetricsMiddleware` that integrates `MetricsCollector` into the apcore middleware pipeline. Uses stack-based timing in `context.data` for safe nested module call support. Records call counts, error counts, and execution durations.

## Files Involved

- `src/apcore/observability/metrics.py` -- `MetricsMiddleware` class
- `src/apcore/middleware/base.py` -- `Middleware` base class (parent)
- `src/apcore/errors.py` -- `ModuleError` for error code extraction
- `tests/test_metrics.py` -- Unit tests for MetricsMiddleware

## Steps

### 1. Write failing tests (TDD)

Create tests for:
- **before()**: Pushes `time.time()` to `context.data["_metrics_starts"]` stack
- **after()**: Pops start time, records `increment_calls(module_id, "success")` and `observe_duration(module_id, duration_s)`
- **on_error()**: Pops start time, records `increment_calls(module_id, "error")`, `increment_errors(module_id, error_code)`, and `observe_duration(module_id, duration_s)`
- **Error code extraction**: Uses `error.code` for `ModuleError` subclasses, `type(error).__name__` for other exceptions
- **Nested calls**: Stack-based timing correctly attributes duration to each module in a nested call chain
- **Integration**: End-to-end test with MetricsCollector showing correct counter and histogram values after multiple calls

### 2. Implement MetricsMiddleware

```python
class MetricsMiddleware(Middleware):
    def __init__(self, collector: MetricsCollector) -> None:
        self._collector = collector

    def before(self, module_id, inputs, context):
        context.data.setdefault("_metrics_starts", []).append(time.time())
        return None

    def after(self, module_id, inputs, output, context):
        start_time = context.data["_metrics_starts"].pop()
        duration_s = time.time() - start_time
        self._collector.increment_calls(module_id, "success")
        self._collector.observe_duration(module_id, duration_s)
        return None

    def on_error(self, module_id, inputs, error, context):
        start_time = context.data["_metrics_starts"].pop()
        duration_s = time.time() - start_time
        error_code = error.code if isinstance(error, ModuleError) else type(error).__name__
        self._collector.increment_calls(module_id, "error")
        self._collector.increment_errors(module_id, error_code)
        self._collector.observe_duration(module_id, duration_s)
        return None
```

### 3. Verify tests pass

Run `pytest tests/test_metrics.py -k "middleware" -v`.

## Acceptance Criteria

- [x] Uses stack-based timing via `context.data["_metrics_starts"]` for nested call safety
- [x] On success: increments calls counter with status="success", observes duration
- [x] On error: increments calls counter with status="error", increments error counter with error code, observes duration
- [x] Error code: `error.code` for `ModuleError`, `type(error).__name__` for others
- [x] All three methods return None (no input/output modification)

## Dependencies

- `metrics-collector` -- `MetricsCollector` must be implemented for recording metrics

## Estimated Time

1.5 hours
