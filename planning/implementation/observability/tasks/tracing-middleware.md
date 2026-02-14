# Task: TracingMiddleware with Sampling Strategies

## Goal

Implement `TracingMiddleware` that integrates span creation and management into the apcore middleware pipeline. Uses stack-based context storage for safe nested module call support and supports four sampling strategies.

## Files Involved

- `src/apcore/observability/tracing.py` -- `TracingMiddleware` class
- `src/apcore/middleware/base.py` -- `Middleware` base class (parent)
- `tests/test_tracing.py` -- Unit tests for TracingMiddleware

## Steps

### 1. Write failing tests (TDD)

Create tests for:
- **before()**: Creates a span, pushes to `context.data["_tracing_spans"]` stack, sets module_id/caller_id attributes
- **after()**: Pops span, sets end_time/status="ok"/duration_ms/success=True, exports if sampled
- **on_error()**: Pops span, sets status="error"/error_code, always exports for `error_first` strategy
- **Parent-child spans**: Nested calls produce spans with correct `parent_span_id` linkage
- **Sampling strategies**:
  - `full`: Always samples (export all spans)
  - `off`: Never samples (export no spans)
  - `proportional`: Samples based on `sampling_rate` (0.0-1.0)
  - `error_first`: Same as proportional for success, always exports on error
- **Sampling inheritance**: Once a trace is sampled/unsampled, all nested spans inherit the decision
- **Validation**: `sampling_rate` outside 0.0-1.0 raises `ValueError`; invalid strategy raises `ValueError`
- **Empty stack warning**: `after()` and `on_error()` with empty span stack logs warning and returns None

### 2. Implement TracingMiddleware

- `__init__(exporter, sampling_rate=1.0, sampling_strategy="full")` with validation
- `_should_sample(context)`: Check/set `_tracing_sampled` in context.data for inheritance
- `before()`: Make sampling decision, create span with trace_id/span_id/parent_span_id/name/start_time/attributes, push to stack
- `after()`: Pop span, finalize with success metrics, export if sampled
- `on_error()`: Pop span, finalize with error metrics, export if sampled or if `error_first` strategy

### 3. Verify tests pass

Run `pytest tests/test_tracing.py -k "middleware" -v`.

## Acceptance Criteria

- [x] Creates parent-child spans using stack in `context.data["_tracing_spans"]`
- [x] Supports 4 sampling strategies: `full`, `proportional`, `error_first`, `off`
- [x] Sampling decisions are inherited across nested calls via `context.data["_tracing_sampled"]`
- [x] `error_first` always exports error spans regardless of sampling decision
- [x] Validates `sampling_rate` range and `sampling_strategy` value in `__init__`
- [x] Handles empty span stack gracefully with warning log

## Dependencies

- `exporters` -- At least one exporter (InMemoryExporter) needed for testing
- `span-model` -- Span dataclass for span creation

## Estimated Time

3 hours
