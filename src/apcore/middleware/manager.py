"""MiddlewareManager -- onion model execution engine for the middleware pipeline."""

from __future__ import annotations

import logging
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

    def add(self, middleware: Middleware) -> None:
        """Append a middleware to the end of the execution list."""
        self._middlewares.append(middleware)

    def remove(self, middleware: Middleware) -> bool:
        """Remove a middleware by identity (is). Returns True if found and removed."""
        for i, entry in enumerate(self._middlewares):
            if entry is middleware:
                self._middlewares.pop(i)
                return True
        return False

    def execute_before(
        self, module_id: str, inputs: dict[str, Any], context: Context
    ) -> tuple[dict[str, Any], list[Middleware]]:
        """Execute before() on all middlewares in registration order.

        Returns a tuple of (final_inputs, executed_middlewares).
        Raises MiddlewareChainError if any middleware's before() raises.
        """
        current_inputs = inputs
        executed_middlewares: list[Middleware] = []

        for mw in self._middlewares:
            executed_middlewares.append(mw)
            try:
                result = mw.before(module_id, current_inputs, context)
            except Exception as e:
                raise MiddlewareChainError(original=e, executed_middlewares=executed_middlewares) from e
            if result is not None:
                current_inputs = result

        return current_inputs, executed_middlewares

    def execute_after(
        self, module_id: str, inputs: dict[str, Any], output: dict[str, Any], context: Context
    ) -> dict[str, Any]:
        """Execute after() on all middlewares in REVERSE registration order.

        Returns the final output dict. Raises if any middleware's after() raises.
        """
        current_output = output

        for mw in reversed(self._middlewares):
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
