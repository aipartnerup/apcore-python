# Task: MetricsCollector with Counters, Histograms, and Prometheus Export

## Goal

Implement a thread-safe in-memory `MetricsCollector` that supports counter increments, histogram observations with configurable bucket boundaries, snapshot export, and Prometheus text format export.

## Files Involved

- `src/apcore/observability/metrics.py` -- `MetricsCollector` class
- `tests/test_metrics.py` -- Unit tests for MetricsCollector

## Steps

### 1. Write failing tests (TDD)

Create tests for:
- **increment()**: Increments a named counter with labels; repeated increments accumulate
- **observe()**: Records a histogram observation; updates sum, count, and bucket counts
- **Bucket boundaries**: Default buckets (0.005 to 60.0, 13 values); custom buckets via constructor
- **+Inf bucket**: Always incremented for every observation
- **snapshot()**: Returns dict with counters and histogram data
- **reset()**: Clears all counters and histograms
- **export_prometheus()**: Produces valid Prometheus text format with HELP/TYPE lines, counter lines, histogram bucket/sum/count lines
- **Labels formatting**: Labels formatted as `{key="value"}`, sorted alphabetically with `le` last for histograms
- **Thread safety**: Concurrent increments from multiple threads produce correct totals
- **Convenience methods**: `increment_calls()`, `increment_errors()`, `observe_duration()`

### 2. Implement MetricsCollector

- Internal storage using dicts keyed by `(name, labels_tuple)` tuples
- `_labels_key()` static method converts label dicts to sorted tuple of tuples
- `increment(name, labels, amount=1)`: Increment counter under lock
- `observe(name, labels, value)`: Update histogram sum/count/buckets under lock
- `snapshot()`: Return deep copy of all data under lock
- `reset()`: Clear all dicts under lock
- `export_prometheus()`: Format counters with HELP/TYPE headers, format histograms with bucket lines, +Inf, _sum, _count

### 3. Implement convenience methods

- `increment_calls(module_id, status)`: Wraps `increment("apcore_module_calls_total", ...)`
- `increment_errors(module_id, error_code)`: Wraps `increment("apcore_module_errors_total", ...)`
- `observe_duration(module_id, duration_seconds)`: Wraps `observe("apcore_module_duration_seconds", ...)`

### 4. Verify tests pass

Run `pytest tests/test_metrics.py -k "collector" -v`.

## Acceptance Criteria

- [x] Thread-safe via `threading.Lock` on all mutating operations
- [x] Counters: `increment(name, labels, amount=1)` with label-keyed storage
- [x] Histograms: `observe(name, labels, value)` with configurable buckets and +Inf
- [x] Default buckets: `[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0]`
- [x] `export_prometheus()` produces valid Prometheus text format with HELP, TYPE, metric lines
- [x] Labels formatted as `{module_id="x",le="0.1"}` with `le` sorted last
- [x] `snapshot()` returns dict copy; `reset()` clears all data
- [x] Convenience methods for module calls, errors, and duration

## Dependencies

None -- MetricsCollector is independent of other observability components.

## Estimated Time

3 hours
