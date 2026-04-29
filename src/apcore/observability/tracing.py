"""Tracing system: Span dataclass, SpanExporter, SpanProcessor implementations, and TracingMiddleware."""

from __future__ import annotations

import collections
import dataclasses
import json
import logging
import os
import queue
import random
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from apcore.middleware import Middleware


@dataclass
class Span:
    """A trace span representing a unit of work in the apcore pipeline."""

    trace_id: str
    name: str
    start_time: float
    span_id: str = field(default_factory=lambda: os.urandom(8).hex())
    parent_span_id: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    end_time: float | None = None
    status: str = "ok"
    events: list[dict[str, Any]] = field(default_factory=list)


def create_span(
    *,
    trace_id: str,
    name: str,
    start_time: float,
    span_id: str | None = None,
    parent_span_id: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> Span:
    """Factory function to create a Span with sensible defaults."""
    return Span(
        trace_id=trace_id,
        name=name,
        start_time=start_time,
        span_id=span_id if span_id is not None else os.urandom(8).hex(),
        parent_span_id=parent_span_id,
        attributes=attributes if attributes is not None else {},
    )


@runtime_checkable
class SpanExporter(Protocol):
    """Protocol for span export destinations."""

    def export(self, span: Span) -> None:
        """Export a completed span."""
        ...


class StdoutExporter:
    """Exports spans as JSON lines to stdout."""

    def export(self, span: Span) -> None:
        """Write span as a single JSON line to stdout."""
        data = dataclasses.asdict(span)
        sys.stdout.write(json.dumps(data, default=str) + "\n")


_tracing_logger = logging.getLogger(__name__)


class InMemoryExporter:
    """Collects spans in memory for testing.

    Thread-safe and bounded: uses a deque with a configurable max size
    to prevent unbounded memory growth.
    """

    def __init__(self, max_spans: int = 10_000) -> None:
        self._spans: collections.deque[Span] = collections.deque(maxlen=max_spans)
        self._lock = threading.Lock()

    def export(self, span: Span) -> None:
        """Add span to internal collection (thread-safe, bounded)."""
        with self._lock:
            self._spans.append(span)

    def get_spans(self) -> list[Span]:
        """Return all collected spans."""
        with self._lock:
            return list(self._spans)

    def clear(self) -> None:
        """Remove all collected spans."""
        with self._lock:
            self._spans.clear()


class OTLPExporter:
    """Exports spans via OpenTelemetry Protocol (requires opentelemetry SDK).

    Bridges apcore ``Span`` instances to OpenTelemetry by creating real OTel
    spans with matching timestamps, attributes, and status, then exporting them
    through the OTLP HTTP protocol to any compatible collector.

    Args:
        endpoint: OTLP collector endpoint URL. Defaults to OTel SDK default
            (``http://localhost:4318/v1/traces`` for HTTP).
        service_name: ``service.name`` resource attribute. Defaults to ``"apcore"``.
        attribute_allowlist: When provided, only span attributes whose key is
            in this set are exported. Apcore-owned keys (``apcore.trace_id``,
            ``apcore.span_id``, ``apcore.parent_span_id``) are always exported.
            Use this to prevent unvetted upstream attributes from leaking PII
            into the collector backend.
    """

    def __init__(
        self,
        endpoint: str | None = None,
        service_name: str = "apcore",
        attribute_allowlist: set[str] | None = None,
    ) -> None:
        """Initialize OTLPExporter with an OTel TracerProvider and OTLP exporter.

        Raises:
            ImportError: If required opentelemetry packages are not installed.
        """
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # type: ignore[import-not-found]
                OTLPSpanExporter as _OTLPSpanExporter,
            )
            from opentelemetry.sdk.resources import Resource  # type: ignore[import-not-found]
            from opentelemetry.sdk.trace import TracerProvider  # type: ignore[import-not-found]
            from opentelemetry.sdk.trace.export import SimpleSpanProcessor as _OtelSimpleSpanProcessor  # type: ignore[import-not-found]
            from opentelemetry.trace import StatusCode  # type: ignore[import-not-found]
        except ImportError:
            raise ImportError(
                "opentelemetry packages are required for OTLPExporter. "
                "Install with: pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-http"
            ) from None

        self._StatusCode = StatusCode
        self._attribute_allowlist: frozenset[str] | None = (
            frozenset(attribute_allowlist) if attribute_allowlist is not None else None
        )

        resource = Resource.create({"service.name": service_name})
        self._provider = TracerProvider(resource=resource)

        exporter_kwargs: dict[str, Any] = {}
        if endpoint is not None:
            exporter_kwargs["endpoint"] = endpoint

        otlp_exporter = _OTLPSpanExporter(**exporter_kwargs)
        self._provider.add_span_processor(_OtelSimpleSpanProcessor(otlp_exporter))
        self._tracer = self._provider.get_tracer("apcore.tracing")

    def export(self, span: Span) -> None:
        """Convert an apcore Span to an OpenTelemetry span and export via OTLP."""
        start_ns = int(span.start_time * 1e9)

        otel_span = self._tracer.start_span(name=span.name, start_time=start_ns)

        otel_span.set_attribute("apcore.trace_id", span.trace_id)
        otel_span.set_attribute("apcore.span_id", span.span_id)
        if span.parent_span_id:
            otel_span.set_attribute("apcore.parent_span_id", span.parent_span_id)

        for key, value in span.attributes.items():
            if value is None:
                continue
            if self._attribute_allowlist is not None and key not in self._attribute_allowlist:
                continue
            if isinstance(value, (str, int, float, bool)):
                otel_span.set_attribute(key, value)
            else:
                otel_span.set_attribute(key, str(value))

        if span.status == "error":
            otel_span.set_status(self._StatusCode.ERROR)

        for event in span.events:
            event_name = event.get("name", "event")
            event_attrs = {k: str(v) for k, v in event.items() if k != "name"}
            otel_span.add_event(event_name, attributes=event_attrs)

        end_ns = int(span.end_time * 1e9) if span.end_time else None
        otel_span.end(end_time=end_ns)

    def shutdown(self) -> None:
        """Flush pending spans and shut down the underlying TracerProvider."""
        self._provider.shutdown()


# ---------------------------------------------------------------------------
# Span Processors
# ---------------------------------------------------------------------------


@runtime_checkable
class SpanProcessor(Protocol):
    """Protocol for span processor implementations.

    Both ``SimpleSpanProcessor`` and ``BatchSpanProcessor`` satisfy this
    protocol, as do any user-defined processors.
    """

    def on_span_end(self, span: Span) -> None:
        """Called when a span has finished; may export synchronously or queue it."""
        ...

    def shutdown(self) -> None:
        """Flush and release resources; called once when the processor is discarded."""
        ...


class SimpleSpanProcessor:
    """Synchronous span processor: exports each span immediately on the calling thread.

    Use in development and testing environments where blocking is acceptable.
    """

    def __init__(self, exporter: SpanExporter) -> None:
        self._exporter = exporter

    def on_span_end(self, span: Span) -> None:
        """Export the span synchronously."""
        self._exporter.export(span)

    def shutdown(self) -> None:
        """No-op for the simple processor."""


class BatchSpanProcessor:
    """Non-blocking span processor that buffers spans and exports in background batches.

    Spans are enqueued immediately without blocking the caller. A background thread
    periodically drains the queue up to ``max_export_batch_size`` spans per flush.
    When the queue is full, new spans are dropped and ``spans_dropped`` is incremented.

    Args:
        exporter: The SpanExporter to deliver batches to.
        max_queue_size: Maximum buffer capacity before drops occur (default 2048).
        schedule_delay_ms: Milliseconds between successive flush attempts (default 5000).
        max_export_batch_size: Maximum spans per flush call (default 512).
        export_timeout_ms: Shutdown flush deadline in milliseconds (default 30000).
    """

    def __init__(
        self,
        exporter: SpanExporter,
        max_queue_size: int = 2048,
        schedule_delay_ms: int = 5000,
        max_export_batch_size: int = 512,
        export_timeout_ms: int = 30000,
    ) -> None:
        self._exporter = exporter
        self._max_queue_size = max_queue_size
        self._schedule_delay_ms = schedule_delay_ms
        self._max_export_batch_size = max_export_batch_size
        self._export_timeout_ms = export_timeout_ms
        self._queue: queue.Queue[Span] = queue.Queue(maxsize=max_queue_size)
        self._spans_dropped = 0
        self._dropped_lock = threading.Lock()
        self._shutdown_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    @property
    def spans_dropped(self) -> int:
        with self._dropped_lock:
            return self._spans_dropped

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    def on_span_end(self, span: Span) -> None:
        """Enqueue span or drop (non-blocking) if the queue is at capacity."""
        try:
            self._queue.put_nowait(span)
        except queue.Full:
            with self._dropped_lock:
                self._spans_dropped += 1

    def _flush(self) -> None:
        """Drain up to max_export_batch_size spans and export them."""
        batch: list[Span] = []
        try:
            while len(batch) < self._max_export_batch_size:
                batch.append(self._queue.get_nowait())
        except queue.Empty:
            pass
        for span in batch:
            try:
                self._exporter.export(span)
            except Exception:
                _tracing_logger.exception("BatchSpanProcessor: export failed")

    def _run(self) -> None:
        """Background worker: flush on each schedule_delay_ms tick, then on shutdown."""
        delay = self._schedule_delay_ms / 1000.0
        while True:
            shutdown_signaled = self._shutdown_event.wait(timeout=delay)
            self._flush()
            if shutdown_signaled:
                break

    def shutdown(self) -> None:
        """Signal shutdown; flush remaining spans within export_timeout_ms deadline."""
        self._shutdown_event.set()
        timeout = self._export_timeout_ms / 1000.0
        self._thread.join(timeout=timeout)
        # Final drain for any spans that arrived after the last flush
        self._flush()


# ---------------------------------------------------------------------------
# TracingMiddleware
# ---------------------------------------------------------------------------

_VALID_STRATEGIES = {"full", "proportional", "error_first", "off"}


class TracingMiddleware(Middleware):
    """Middleware that creates and manages trace spans for module calls.

    Accepts either an ``exporter`` (wrapped in ``SimpleSpanProcessor``) or a
    ``processor`` directly.  When ``exporter`` is supplied, backward-compatible
    behavior is preserved.

    Uses stack-based context.data storage to correctly handle nested
    module-to-module call chains.
    """

    def __init__(
        self,
        exporter: SpanExporter | None = None,
        sampling_rate: float = 1.0,
        sampling_strategy: str = "full",
        *,
        processor: SpanProcessor | None = None,
        priority: int = 100,
    ) -> None:
        super().__init__(priority=priority)
        if not (0.0 <= sampling_rate <= 1.0):
            raise ValueError(f"sampling_rate must be between 0.0 and 1.0, got {sampling_rate}")
        if sampling_strategy not in _VALID_STRATEGIES:
            raise ValueError(f"sampling_strategy must be one of {_VALID_STRATEGIES}, got {sampling_strategy!r}")
        if exporter is None and processor is None:
            raise ValueError("Either 'exporter' or 'processor' must be provided.")
        self._sampling_rate = sampling_rate
        self._sampling_strategy = sampling_strategy
        if processor is not None:
            self._processor: SpanProcessor = processor
        else:
            assert exporter is not None
            self._processor = SimpleSpanProcessor(exporter)

    @property
    def _exporter(self) -> SpanExporter:
        """Backward-compatible accessor: return the exporter from the underlying processor."""
        if isinstance(self._processor, SimpleSpanProcessor):
            return self._processor._exporter
        raise AttributeError("TracingMiddleware uses a BatchSpanProcessor; access _processor._exporter instead.")

    def set_exporter(self, exporter: SpanExporter) -> None:
        """Replace the underlying exporter (wraps it in a SimpleSpanProcessor).

        Calls ``shutdown()`` on the previous processor to prevent thread leaks
        when replacing a ``BatchSpanProcessor``.
        """
        old = self._processor
        self._processor = SimpleSpanProcessor(exporter)
        old.shutdown()

    def _should_sample(self, context: Any) -> bool:
        """Make or inherit sampling decision."""
        existing = context.data.get("_apcore.mw.tracing.sampled")
        if isinstance(existing, bool):
            return existing

        if self._sampling_strategy == "full":
            decision = True
        elif self._sampling_strategy == "off":
            decision = False
        else:  # proportional or error_first
            decision = random.random() < self._sampling_rate

        context.data["_apcore.mw.tracing.sampled"] = decision
        return decision

    def before(self, module_id: str, inputs: dict[str, Any], context: Any) -> dict[str, Any] | None:
        """Create a span, push to stack, make/inherit sampling decision."""
        self._should_sample(context)

        spans_stack = context.data.setdefault("_apcore.mw.tracing.spans", [])
        parent_span_id = spans_stack[-1].span_id if spans_stack else None

        span = Span(
            trace_id=context.trace_id,
            span_id=os.urandom(8).hex(),
            parent_span_id=parent_span_id,
            name="apcore.module.execute",
            start_time=time.time(),
            attributes={
                "module_id": module_id,
                "method": "execute",
                "caller_id": context.caller_id,
            },
        )
        spans_stack.append(span)
        return None

    def after(
        self,
        module_id: str,
        inputs: dict[str, Any],
        output: dict[str, Any],
        context: Any,
    ) -> dict[str, Any] | None:
        """Pop span, finalize with success status, export if sampled."""
        spans_stack = context.data.get("_apcore.mw.tracing.spans", [])
        if not spans_stack:
            _tracing_logger.warning(
                "TracingMiddleware.after() called with empty span stack for %s",
                module_id,
            )
            return None
        span = spans_stack.pop()
        span.end_time = time.time()
        span.status = "ok"
        span.attributes["duration_ms"] = (span.end_time - span.start_time) * 1000
        span.attributes["success"] = True

        if context.data.get("_apcore.mw.tracing.sampled"):
            self._processor.on_span_end(span)
        return None

    def on_error(self, module_id: str, inputs: dict[str, Any], error: Exception, context: Any) -> dict[str, Any] | None:
        """Pop span, finalize with error status, always export for error_first. Return None."""
        spans_stack = context.data.get("_apcore.mw.tracing.spans", [])
        if not spans_stack:
            _tracing_logger.warning(
                "TracingMiddleware.on_error() called with empty span stack for %s",
                module_id,
            )
            return None
        span = spans_stack.pop()
        span.end_time = time.time()
        span.status = "error"
        span.attributes["duration_ms"] = (span.end_time - span.start_time) * 1000
        span.attributes["success"] = False
        span.attributes["error_code"] = getattr(error, "code", type(error).__name__)

        should_export = self._sampling_strategy == "error_first" or context.data.get("_apcore.mw.tracing.sampled")
        if should_export:
            self._processor.on_span_end(span)
        return None


__all__ = [
    "Span",
    "create_span",
    "SpanExporter",
    "SpanProcessor",
    "StdoutExporter",
    "InMemoryExporter",
    "OTLPExporter",
    "SimpleSpanProcessor",
    "BatchSpanProcessor",
    "TracingMiddleware",
]
