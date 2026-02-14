"""LoggingMiddleware for structured module call logging."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from apcore.middleware.base import Middleware

if TYPE_CHECKING:
    from apcore.context import Context

__all__ = ["LoggingMiddleware"]


class LoggingMiddleware(Middleware):
    """Structured logging middleware with security-aware redaction.

    Logs module call start, completion (with duration), and errors using
    context.redacted_inputs to avoid leaking sensitive data. Thread-safe
    by storing per-call state in context.data.
    """

    def __init__(
        self,
        logger: logging.Logger | None = None,
        log_inputs: bool = True,
        log_outputs: bool = True,
        log_errors: bool = True,
    ) -> None:
        self._logger = logger or logging.getLogger("apcore.middleware.logging")
        self._log_inputs = log_inputs
        self._log_outputs = log_outputs
        self._log_errors = log_errors

    def before(self, module_id: str, inputs: dict[str, Any], context: Context) -> None:
        """Record start time and log module call initiation with redacted inputs."""
        context.data["_logging_mw_start"] = time.time()

        if self._log_inputs:
            redacted = getattr(context, "redacted_inputs", inputs)
            self._logger.info(
                f"[{context.trace_id}] START {module_id}",
                extra={
                    "trace_id": context.trace_id,
                    "module_id": module_id,
                    "caller_id": context.caller_id,
                    "inputs": redacted,
                },
            )

        return None

    def after(
        self,
        module_id: str,
        inputs: dict[str, Any],
        output: dict[str, Any],
        context: Context,
    ) -> None:
        """Log module completion with duration and output."""
        start_time = context.data.get("_logging_mw_start", time.time())
        duration_ms = (time.time() - start_time) * 1000

        if self._log_outputs:
            self._logger.info(
                f"[{context.trace_id}] END {module_id} ({duration_ms:.2f}ms)",
                extra={
                    "trace_id": context.trace_id,
                    "module_id": module_id,
                    "duration_ms": duration_ms,
                    "output": output,
                },
            )

        return None

    def on_error(self, module_id: str, inputs: dict[str, Any], error: Exception, context: Context) -> None:
        """Log module error with redacted inputs and traceback."""
        if self._log_errors:
            redacted = getattr(context, "redacted_inputs", inputs)
            self._logger.error(
                f"[{context.trace_id}] ERROR {module_id}: {error}",
                extra={
                    "trace_id": context.trace_id,
                    "module_id": module_id,
                    "error": str(error),
                    "inputs": redacted,
                },
                exc_info=True,
            )

        return None
