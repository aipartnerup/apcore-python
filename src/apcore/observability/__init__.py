"""apcore observability package.

Re-exports all public observability classes for convenient access::

    from apcore.observability import (
        Span, TracingMiddleware, StdoutExporter, InMemoryExporter,
        ContextLogger, ObsLoggingMiddleware,
        MetricsCollector, MetricsMiddleware,
    )

Recommended middleware registration order (outermost to innermost):
    1. TracingMiddleware  -- captures total wall-clock time
    2. MetricsMiddleware  -- captures execution timing
    3. ObsLoggingMiddleware  -- logs with timing already set up
"""

from apcore.observability.context_logger import ContextLogger, ObsLoggingMiddleware
from apcore.observability.metrics import MetricsCollector, MetricsMiddleware
from apcore.observability.tracing import (
    InMemoryExporter,
    Span,
    SpanExporter,
    StdoutExporter,
    TracingMiddleware,
)

__all__ = [
    "ContextLogger",
    "InMemoryExporter",
    "MetricsCollector",
    "MetricsMiddleware",
    "ObsLoggingMiddleware",
    "Span",
    "SpanExporter",
    "StdoutExporter",
    "TracingMiddleware",
]
