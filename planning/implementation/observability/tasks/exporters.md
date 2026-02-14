# Task: Span Exporters (Stdout, InMemory, OTLP)

## Goal

Implement three `SpanExporter` implementations: `StdoutExporter` for JSON line output, `InMemoryExporter` for testing with bounded memory, and `OTLPExporter` for bridging to OpenTelemetry collectors.

## Files Involved

- `src/apcore/observability/tracing.py` -- All three exporter classes
- `tests/test_tracing.py` -- Unit tests for each exporter

## Steps

### 1. Write failing tests (TDD)

Create tests for:

**StdoutExporter**:
- Exports span as JSON line to stdout
- Output is valid JSON containing all span fields
- Satisfies `SpanExporter` protocol

**InMemoryExporter**:
- `export()` adds span to internal collection
- `get_spans()` returns all collected spans as list
- `clear()` removes all spans
- Thread-safe: concurrent exports from multiple threads produce correct count
- Bounded: `max_spans=5` with 10 exports retains only last 5 (deque behavior)
- Default bound is 10000
- Satisfies `SpanExporter` protocol

**OTLPExporter**:
- Raises `ImportError` with helpful message when opentelemetry packages not installed
- (Integration test, if otel available) Creates OTel spans with apcore attributes
- Satisfies `SpanExporter` protocol

### 2. Implement StdoutExporter

- Convert span to dict via `dataclasses.asdict(span)`
- Write JSON line to `sys.stdout` using `json.dumps(data, default=str)`

### 3. Implement InMemoryExporter

- Use `collections.deque(maxlen=max_spans)` for bounded storage
- Protect with `threading.Lock` for thread safety
- Provide `get_spans() -> list[Span]` and `clear()` methods

### 4. Implement OTLPExporter

- Lazy-import opentelemetry packages in `__init__`, raise `ImportError` if missing
- Create `TracerProvider` with `Resource(service.name=service_name)`
- Configure `SimpleSpanProcessor` with `OTLPSpanExporter`
- In `export()`: create OTel span, set apcore IDs as attributes, copy attributes, replay events, set status, end with original timestamps
- Provide `shutdown()` to flush and close

### 5. Verify tests pass

Run `pytest tests/test_tracing.py -k "exporter" -v`.

## Acceptance Criteria

- [x] `StdoutExporter` writes valid JSON lines to stdout
- [x] `InMemoryExporter` is bounded via `deque(maxlen=10000)` default
- [x] `InMemoryExporter` is thread-safe via `threading.Lock`
- [x] `InMemoryExporter` provides `get_spans()` and `clear()` methods
- [x] `OTLPExporter` raises descriptive `ImportError` when otel packages missing
- [x] `OTLPExporter` maps apcore Span fields to OTel span attributes with `apcore.` prefix
- [x] `OTLPExporter` provides `shutdown()` for clean teardown
- [x] All three satisfy the `SpanExporter` protocol

## Dependencies

- `span-model` -- `Span` dataclass and `SpanExporter` protocol must be defined

## Estimated Time

3 hours
