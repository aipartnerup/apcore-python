"""Tracing system: Span dataclass, SpanExporter implementations, and TracingMiddleware."""

from __future__ import annotations

import collections
import dataclasses
import json
import logging
import os
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
    """

    def __init__(
        self,
        endpoint: str | None = None,
        service_name: str = "apcore",
    ) -> None:
        """Initialize OTLPExporter with an OTel TracerProvider and OTLP exporter.

        Raises:
            ImportError: If required opentelemetry packages are not installed.
        """
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter as _OTLPSpanExporter,
            )
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import SimpleSpanProcessor
            from opentelemetry.trace import StatusCode
        except ImportError:
            raise ImportError(
                "opentelemetry packages are required for OTLPExporter. "
                "Install with: pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-http"
            ) from None

        self._StatusCode = StatusCode

        resource = Resource.create({"service.name": service_name})
        self._provider = TracerProvider(resource=resource)

        exporter_kwargs: dict[str, Any] = {}
        if endpoint is not None:
            exporter_kwargs["endpoint"] = endpoint

        otlp_exporter = _OTLPSpanExporter(**exporter_kwargs)
        self._provider.add_span_processor(SimpleSpanProcessor(otlp_exporter))
        self._tracer = self._provider.get_tracer("apcore.tracing")

    def export(self, span: Span) -> None:
        """Convert an apcore Span to an OpenTelemetry span and export via OTLP.

        Apcore-specific identifiers (``trace_id``, ``span_id``,
        ``parent_span_id``) are added as span attributes prefixed with
        ``apcore.`` so they can be correlated in the collector backend.
        """
        start_ns = int(span.start_time * 1e9)

        otel_span = self._tracer.start_span(name=span.name, start_time=start_ns)

        # Carry apcore IDs as attributes for correlation
        otel_span.set_attribute("apcore.trace_id", span.trace_id)
        otel_span.set_attribute("apcore.span_id", span.span_id)
        if span.parent_span_id:
            otel_span.set_attribute("apcore.parent_span_id", span.parent_span_id)

        # Copy span attributes (only primitive types supported by OTel)
        for key, value in span.attributes.items():
            if value is not None:
                if isinstance(value, (str, int, float, bool)):
                    otel_span.set_attribute(key, value)
                else:
                    otel_span.set_attribute(key, str(value))

        # Set status
        if span.status == "error":
            otel_span.set_status(self._StatusCode.ERROR)

        # Replay events
        for event in span.events:
            event_name = event.get("name", "event")
            event_attrs = {k: str(v) for k, v in event.items() if k != "name"}
            otel_span.add_event(event_name, attributes=event_attrs)

        # End with original end_time
        end_ns = int(span.end_time * 1e9) if span.end_time else None
        otel_span.end(end_time=end_ns)

    def shutdown(self) -> None:
        """Flush pending spans and shut down the underlying TracerProvider."""
        self._provider.shutdown()


_VALID_STRATEGIES = {"full", "proportional", "error_first", "off"}


class TracingMiddleware(Middleware):
    """Middleware that creates and manages trace spans for module calls.

    Uses stack-based context.data storage to correctly handle nested
    module-to-module call chains.
    """

    def __init__(
        self,
        exporter: SpanExporter,
        sampling_rate: float = 1.0,
        sampling_strategy: str = "full",
    ) -> None:
        if not (0.0 <= sampling_rate <= 1.0):
            raise ValueError(f"sampling_rate must be between 0.0 and 1.0, got {sampling_rate}")
        if sampling_strategy not in _VALID_STRATEGIES:
            raise ValueError(f"sampling_strategy must be one of {_VALID_STRATEGIES}, got {sampling_strategy!r}")
        self._exporter = exporter
        self._sampling_rate = sampling_rate
        self._sampling_strategy = sampling_strategy

    def _should_sample(self, context: Any) -> bool:
        """Make or inherit sampling decision."""
        existing = context.data.get("_tracing_sampled")
        if isinstance(existing, bool):
            return existing

        if self._sampling_strategy == "full":
            decision = True
        elif self._sampling_strategy == "off":
            decision = False
        else:  # proportional or error_first
            decision = random.random() < self._sampling_rate

        context.data["_tracing_sampled"] = decision
        return decision

    def before(self, module_id: str, inputs: dict[str, Any], context: Any) -> dict[str, Any] | None:
        """Create a span, push to stack, make/inherit sampling decision."""
        self._should_sample(context)

        spans_stack = context.data.setdefault("_tracing_spans", [])
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
        spans_stack = context.data.get("_tracing_spans", [])
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

        if context.data.get("_tracing_sampled"):
            self._exporter.export(span)
        return None

    def on_error(self, module_id: str, inputs: dict[str, Any], error: Exception, context: Any) -> dict[str, Any] | None:
        """Pop span, finalize with error status, always export for error_first. Return None."""
        spans_stack = context.data.get("_tracing_spans", [])
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

        should_export = self._sampling_strategy == "error_first" or context.data.get("_tracing_sampled")
        if should_export:
            self._exporter.export(span)
        return None


__all__ = [
    "Span",
    "SpanExporter",
    "StdoutExporter",
    "InMemoryExporter",
    "OTLPExporter",
    "TracingMiddleware",
]
