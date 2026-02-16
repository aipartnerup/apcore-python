# Task: Span Dataclass and SpanExporter Protocol

## Goal

Define the `Span` dataclass representing a unit of work in the apcore tracing pipeline, and the `SpanExporter` protocol defining the interface for span export destinations.

## Files Involved

- `src/apcore/observability/tracing.py` -- `Span` dataclass and `SpanExporter` protocol
- `tests/test_tracing.py` -- Unit tests for Span construction and SpanExporter protocol compliance

## Steps

### 1. Write failing tests (TDD)

Create tests for:
- Span construction with all required fields (`trace_id`, `name`, `start_time`)
- Default values: `span_id` auto-generated (16-char hex from `os.urandom(8)`), `parent_span_id=None`, `attributes={}`, `end_time=None`, `status="ok"`, `events=[]`
- Span with explicit parent_span_id creates a child relationship
- Span attributes can be set and accessed
- SpanExporter protocol: verify that any class implementing `export(span: Span) -> None` satisfies `isinstance(obj, SpanExporter)`

### 2. Implement Span dataclass

```python
@dataclass
class Span:
    trace_id: str
    name: str
    start_time: float
    span_id: str = field(default_factory=lambda: os.urandom(8).hex())
    parent_span_id: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    end_time: float | None = None
    status: str = "ok"
    events: list[dict[str, Any]] = field(default_factory=list)
```

### 3. Implement SpanExporter protocol

```python
@runtime_checkable
class SpanExporter(Protocol):
    def export(self, span: Span) -> None: ...
```

Use `@runtime_checkable` to enable `isinstance()` checks.

### 4. Verify tests pass

Run `pytest tests/test_tracing.py -k "span" -v`.

## Acceptance Criteria

- [x] `Span` dataclass has all 9 fields with correct defaults
- [x] `span_id` is auto-generated as 16-char hex string
- [x] `SpanExporter` is a `@runtime_checkable` Protocol
- [x] Any class with `export(span: Span) -> None` satisfies `isinstance(obj, SpanExporter)`

## Dependencies

None -- this is the foundational data model for the tracing system.

## Estimated Time

30 minutes
