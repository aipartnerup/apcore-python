"""MiddlewareManager -- onion model execution engine for the middleware pipeline."""

from __future__ import annotations

import inspect
import logging
import threading
from typing import TYPE_CHECKING, Any

from apcore.middleware.base import Middleware

if TYPE_CHECKING:
    from apcore.context import Context

__all__ = ["MiddlewareManager", "MiddlewareChainError"]

_logger = logging.getLogger(__name__)


class MiddlewareChainError(Exception):
    """Raised when a middleware's before() fails. Carries context for error recovery."""

    def __init__(self, original: Exception, executed_middlewares: list[Middleware]) -> None:
        super().__init__(str(original))
        self.original = original
        self.executed_middlewares = executed_middlewares


class MiddlewareManager:
    """Orchestrates the middleware pipeline using onion model execution.

    Manages an ordered list of Middleware instances and provides execution
    methods for before, after, and error handling phases.
    """

    def __init__(self) -> None:
        """Initialize an empty middleware manager."""
        self._middlewares: list[Middleware] = []
        self._lock = threading.Lock()

    def add(self, middleware: Middleware) -> None:
        """Append a middleware to the end of the execution list."""
        with self._lock:
            self._middlewares.append(middleware)

    def remove(self, middleware: Middleware) -> bool:
        """Remove a middleware by identity (is). Returns True if found and removed."""
        with self._lock:
            for i, entry in enumerate(self._middlewares):
                if entry is middleware:
                    self._middlewares.pop(i)
                    return True
            return False

    def snapshot(self) -> list[Middleware]:
        """Return a snapshot (copy) of the current middleware list.

        Thread-safe: acquires lock, copies list, releases lock.
        Callers can iterate the returned list without holding the lock.
        """
        with self._lock:
            return list(self._middlewares)

    def execute_before(
        self, module_id: str, inputs: dict[str, Any], context: Context
    ) -> tuple[dict[str, Any], list[Middleware]]:
        """Execute before() on all middlewares in registration order.

        Returns a tuple of (final_inputs, executed_middlewares).
        Raises MiddlewareChainError if any middleware's before() raises.
        """
        current_inputs = inputs
        executed_middlewares: list[Middleware] = []
        middlewares = self.snapshot()

        for mw in middlewares:
            executed_middlewares.append(mw)
            try:
                result = mw.before(module_id, current_inputs, context)
            except Exception as e:
                raise MiddlewareChainError(original=e, executed_middlewares=executed_middlewares) from e
            if result is not None:
                current_inputs = result

        return current_inputs, executed_middlewares

    def execute_after(
        self,
        module_id: str,
        inputs: dict[str, Any],
        output: dict[str, Any],
        context: Context,
    ) -> dict[str, Any]:
        """Execute after() on all middlewares in REVERSE registration order.

        Returns the final output dict. Raises if any middleware's after() raises.
        """
        current_output = output
        middlewares = self.snapshot()

        for mw in reversed(middlewares):
            result = mw.after(module_id, inputs, current_output, context)
            if result is not None:
                current_output = result

        return current_output

    def execute_on_error(
        self,
        module_id: str,
        inputs: dict[str, Any],
        error: Exception,
        context: Context,
        executed_middlewares: list[Middleware],
    ) -> dict[str, Any] | None:
        """Execute on_error() on executed middlewares in reverse order.

        Returns a recovery dict from the first handler that provides one,
        or None if no handler recovers.
        """
        for mw in reversed(executed_middlewares):
            try:
                result = mw.on_error(module_id, inputs, error, context)
            except Exception:
                _logger.error("Exception in on_error handler %r", mw, exc_info=True)
                continue
            if result is not None:
                return result

        return None

    async def execute_before_async(
        self, module_id: str, inputs: dict[str, Any], context: Context
    ) -> tuple[dict[str, Any], list[Middleware]]:
        """Async-aware execute_before: awaits coroutine middlewares, calls sync ones directly."""
        current_inputs = inputs
        executed_middlewares: list[Middleware] = []
        middlewares = self.snapshot()

        for mw in middlewares:
            executed_middlewares.append(mw)
            try:
                if inspect.iscoroutinefunction(mw.before):
                    result = await mw.before(module_id, current_inputs, context)
                else:
                    result = mw.before(module_id, current_inputs, context)
            except Exception as e:
                raise MiddlewareChainError(original=e, executed_middlewares=executed_middlewares) from e
            if result is not None:
                current_inputs = result

        return current_inputs, executed_middlewares

    async def execute_after_async(
        self,
        module_id: str,
        inputs: dict[str, Any],
        output: dict[str, Any],
        context: Context,
    ) -> dict[str, Any]:
        """Async-aware execute_after: awaits coroutine middlewares, calls sync ones directly."""
        current_output = output
        middlewares = self.snapshot()

        for mw in reversed(middlewares):
            if inspect.iscoroutinefunction(mw.after):
                result = await mw.after(module_id, inputs, current_output, context)
            else:
                result = mw.after(module_id, inputs, current_output, context)
            if result is not None:
                current_output = result

        return current_output

    async def execute_on_error_async(
        self,
        module_id: str,
        inputs: dict[str, Any],
        error: Exception,
        context: Context,
        executed_middlewares: list[Middleware],
    ) -> dict[str, Any] | None:
        """Async-aware on_error chain."""
        for mw in reversed(executed_middlewares):
            try:
                if inspect.iscoroutinefunction(mw.on_error):
                    recovery = await mw.on_error(module_id, inputs, error, context)
                else:
                    recovery = mw.on_error(module_id, inputs, error, context)
                if isinstance(recovery, dict):
                    return recovery
            except Exception:
                _logger.exception("on_error handler failed in %s", type(mw).__name__)
                continue
        return None
