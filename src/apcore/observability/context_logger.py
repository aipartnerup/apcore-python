"""Structured logging: ContextLogger and ObsLoggingMiddleware."""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from typing import Any

from apcore.middleware import Middleware

_LEVELS = {
    "trace": 0,
    "debug": 10,
    "info": 20,
    "warn": 30,
    "error": 40,
    "fatal": 50,
}

_REDACTED = "***REDACTED***"


class ContextLogger:
    """Standalone structured logger with context injection and redaction."""

    def __init__(
        self,
        name: str,
        format: str = "json",
        level: str = "info",
        redact_sensitive: bool = True,
        output: Any = None,
    ) -> None:
        self._name = name
        self._format = format
        self._level = level
        self._level_value = _LEVELS.get(level, 20)
        self._redact_sensitive = redact_sensitive
        self._output = output if output is not None else sys.stderr
        self._trace_id: str | None = None
        self._module_id: str | None = None
        self._caller_id: str | None = None

    @classmethod
    def from_context(cls, context: Any, name: str, **kwargs: Any) -> ContextLogger:
        """Create a logger that auto-injects trace_id, module_id, caller_id from context."""
        logger = cls(name=name, **kwargs)
        logger._trace_id = context.trace_id
        logger._module_id = context.call_chain[-1] if context.call_chain else None
        logger._caller_id = context.caller_id
        return logger

    def _emit(
        self, level_name: str, message: str, extra: dict[str, Any] | None
    ) -> None:
        level_value = _LEVELS.get(level_name, 20)
        if level_value < self._level_value:
            return

        redacted_extra = extra
        if extra is not None and self._redact_sensitive:
            redacted_extra = {
                k: (_REDACTED if k.startswith("_secret_") else v)
                for k, v in extra.items()
            }

        now = datetime.now(timezone.utc)
        entry = {
            "timestamp": now.isoformat(),
            "level": level_name,
            "message": message,
            "trace_id": self._trace_id,
            "module_id": self._module_id,
            "caller_id": self._caller_id,
            "logger": self._name,
            "extra": redacted_extra,
        }

        if self._format == "json":
            self._output.write(json.dumps(entry, default=str) + "\n")
        else:
            ts = now.strftime("%Y-%m-%d %H:%M:%S")
            lvl = level_name.upper()
            trace = self._trace_id or "none"
            mod = self._module_id or "none"
            extras_str = ""
            if redacted_extra:
                extras_str = " " + " ".join(
                    f"{k}={v}" for k, v in redacted_extra.items()
                )
            self._output.write(
                f"{ts} [{lvl}] [trace={trace}] [module={mod}] {message}{extras_str}\n"
            )

    def trace(self, message: str, extra: dict[str, Any] | None = None) -> None:
        self._emit("trace", message, extra)

    def debug(self, message: str, extra: dict[str, Any] | None = None) -> None:
        self._emit("debug", message, extra)

    def info(self, message: str, extra: dict[str, Any] | None = None) -> None:
        self._emit("info", message, extra)

    def warn(self, message: str, extra: dict[str, Any] | None = None) -> None:
        self._emit("warn", message, extra)

    def error(self, message: str, extra: dict[str, Any] | None = None) -> None:
        self._emit("error", message, extra)

    def fatal(self, message: str, extra: dict[str, Any] | None = None) -> None:
        self._emit("fatal", message, extra)


class ObsLoggingMiddleware(Middleware):
    """Structured observability logging middleware using ContextLogger.

    Uses stack-based timing in context.data for safe nested call support.
    """

    def __init__(
        self,
        logger: ContextLogger | None = None,
        log_inputs: bool = True,
        log_outputs: bool = True,
    ) -> None:
        self._logger = (
            logger if logger is not None else ContextLogger(name="apcore.obs_logging")
        )
        self._log_inputs = log_inputs
        self._log_outputs = log_outputs

    def before(
        self, module_id: str, inputs: dict[str, Any], context: Any
    ) -> dict[str, Any] | None:
        context.data.setdefault("_obs_logging_starts", []).append(time.time())
        extra: dict[str, Any] = {
            "module_id": module_id,
            "caller_id": context.caller_id,
        }
        if self._log_inputs:
            extra["inputs"] = getattr(context, "redacted_inputs", None) or inputs
        self._logger.info("Module call started", extra=extra)
        return None

    def after(
        self,
        module_id: str,
        inputs: dict[str, Any],
        output: dict[str, Any],
        context: Any,
    ) -> dict[str, Any] | None:
        start_time = context.data["_obs_logging_starts"].pop()
        duration_ms = (time.time() - start_time) * 1000
        extra: dict[str, Any] = {
            "module_id": module_id,
            "duration_ms": duration_ms,
        }
        if self._log_outputs:
            extra["output"] = output
        self._logger.info("Module call completed", extra=extra)
        return None

    def on_error(
        self, module_id: str, inputs: dict[str, Any], error: Exception, context: Any
    ) -> dict[str, Any] | None:
        start_time = context.data["_obs_logging_starts"].pop()
        duration_ms = (time.time() - start_time) * 1000
        self._logger.error(
            "Module call failed",
            extra={
                "module_id": module_id,
                "duration_ms": duration_ms,
                "error_type": type(error).__name__,
                "error_message": str(error),
            },
        )
        return None


__all__ = [
    "ContextLogger",
    "ObsLoggingMiddleware",
]
