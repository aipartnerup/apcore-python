"""apcore observability package.

Re-exports all public observability classes for convenient access::

    from apcore.observability import (
        Span, TracingMiddleware, StdoutExporter, InMemoryExporter,
        BatchSpanProcessor, SimpleSpanProcessor,
        ContextLogger, ObsLoggingMiddleware, RedactionConfig,
        MetricsCollector, MetricsMiddleware,
        ErrorHistory, ErrorEntry,
        ObservabilityStore, InMemoryObservabilityStore,
        PrometheusExporter,
    )

Recommended middleware registration order (outermost to innermost):
    1. TracingMiddleware  -- captures total wall-clock time
    2. MetricsMiddleware  -- captures execution timing
    3. ObsLoggingMiddleware  -- logs with timing already set up
"""

from apcore.observability.context_logger import (
    ContextLogger,
    ObsLoggingMiddleware,
    RedactionConfig,
)
from apcore.observability.error_history import (
    ErrorEntry,
    ErrorHistory,
    compute_fingerprint,
    normalize_message,
)
from apcore.observability.metrics import (
    MetricsCollector,
    MetricsMiddleware,
    estimate_p99_latency_ms,
)
from apcore.observability.prometheus_exporter import PrometheusExporter
from apcore.observability.store import (
    InMemoryObservabilityStore,
    MetricPoint,
    ObservabilityStore,
)
from apcore.observability.tracing import (
    BatchSpanProcessor,
    InMemoryExporter,
    OTLPExporter,
    SimpleSpanProcessor,
    Span,
    SpanExporter,
    SpanProcessor,
    StdoutExporter,
    TracingMiddleware,
    create_span,
)
from apcore.observability.usage import UsageCollector, UsageMiddleware

__all__ = [
    "BatchSpanProcessor",
    "compute_fingerprint",
    "ContextLogger",
    "ErrorEntry",
    "ErrorHistory",
    "estimate_p99_latency_ms",
    "InMemoryExporter",
    "InMemoryObservabilityStore",
    "MetricPoint",
    "MetricsCollector",
    "MetricsMiddleware",
    "normalize_message",
    "ObsLoggingMiddleware",
    "ObservabilityStore",
    "OTLPExporter",
    "PrometheusExporter",
    "RedactionConfig",
    "SimpleSpanProcessor",
    "Span",
    "SpanExporter",
    "SpanProcessor",
    "StdoutExporter",
    "TracingMiddleware",
    "UsageCollector",
    "UsageMiddleware",
    "create_span",
]
