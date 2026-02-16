# Feature: Observability

## Overview

Comprehensive observability stack providing distributed tracing, metrics collection, and structured context logging for the apcore module execution pipeline. The tracing pillar uses span-based distributed traces with multiple exporters (stdout, in-memory, OpenTelemetry OTLP) and four configurable sampling strategies. The metrics pillar provides thread-safe counters and histograms with Prometheus text format export. The logging pillar offers a standalone structured logger with JSON/text formats, trace context injection, and sensitive field redaction. All three pillars integrate with the middleware pipeline via stack-based `context.data` tracking for safe nested module-to-module call support.

## Scope

### Included

- `Span` dataclass with trace_id, span_id, parent_span_id, name, timing, status, attributes, and events
- `SpanExporter` runtime-checkable protocol with three implementations: `StdoutExporter`, `InMemoryExporter` (bounded deque), `OTLPExporter` (OpenTelemetry bridge)
- `TracingMiddleware` with stack-based span management and four sampling strategies (full, proportional, error_first, off)
- `MetricsCollector` with thread-safe counters, histograms (configurable buckets), and Prometheus text export
- `MetricsMiddleware` recording call counts, error counts, and execution duration per module
- `ContextLogger` with JSON/text output, trace context injection, `_secret_` prefix field redaction, and `from_context()` factory
- `ObsLoggingMiddleware` with stack-based timing and configurable input/output logging

### Excluded

- Distributed context propagation across process boundaries (tracing is per-process)
- Persistent metrics storage (in-memory only, exported via Prometheus scrape)
- Log aggregation or shipping (ContextLogger writes to a configurable output stream)

## Technology Stack

- **Language**: Python 3.10+
- **Dependencies**: stdlib (`collections`, `threading`, `time`, `json`, `random`, `dataclasses`, `datetime`), optional `opentelemetry-sdk` + `opentelemetry-exporter-otlp-proto-http` for `OTLPExporter`
- **Internal**: `apcore.middleware.Middleware` (base class), `apcore.context.Context`, `apcore.errors.ModuleError`
- **Testing**: pytest

## Task Execution Order

| # | Task File | Description | Status |
|---|-----------|-------------|--------|
| 1 | [span-model](./tasks/span-model.md) | `Span` dataclass and `SpanExporter` runtime-checkable protocol | completed |
| 2 | [exporters](./tasks/exporters.md) | `StdoutExporter` (JSON lines), `InMemoryExporter` (bounded deque), `OTLPExporter` (OpenTelemetry bridge) | completed |
| 3 | [tracing-middleware](./tasks/tracing-middleware.md) | `TracingMiddleware` with stack-based spans, sampling strategies, parent-child linking | completed |
| 4 | [metrics-collector](./tasks/metrics-collector.md) | `MetricsCollector` with counters, histograms, Prometheus text export, convenience methods | completed |
| 5 | [metrics-middleware](./tasks/metrics-middleware.md) | `MetricsMiddleware` recording calls, errors, and duration with stack-based timing | completed |
| 6 | [context-logger](./tasks/context-logger.md) | `ContextLogger` with JSON/text formats, context injection, `_secret_` redaction | completed |
| 7 | [obs-logging-middleware](./tasks/obs-logging-middleware.md) | `ObsLoggingMiddleware` with stack-based timing and configurable logging | completed |

## Progress

| Total | Completed | In Progress | Pending |
|-------|-----------|-------------|---------|
| 7     | 7         | 0           | 0       |

## Reference Documents

- [Observability Feature Specification](../../features/observability.md)
