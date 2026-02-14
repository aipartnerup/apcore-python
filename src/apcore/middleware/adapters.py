"""Function adapter classes for the middleware system."""

from __future__ import annotations

from typing import Any, Callable

from apcore.context import Context
from apcore.middleware.base import Middleware

__all__ = ["BeforeMiddleware", "AfterMiddleware"]


class BeforeMiddleware(Middleware):
    """Wraps a before-only callback function as a Middleware instance."""

    def __init__(
        self, callback: Callable[[str, dict[str, Any], Context], dict[str, Any] | None]
    ) -> None:
        """Store the callback for delegation."""
        self._callback = callback

    def before(
        self, module_id: str, inputs: dict[str, Any], context: Context
    ) -> dict[str, Any] | None:
        """Delegate to the wrapped callback."""
        return self._callback(module_id, inputs, context)


class AfterMiddleware(Middleware):
    """Wraps an after-only callback function as a Middleware instance."""

    def __init__(
        self,
        callback: Callable[
            [str, dict[str, Any], dict[str, Any], Context], dict[str, Any] | None
        ],
    ) -> None:
        """Store the callback for delegation."""
        self._callback = callback

    def after(
        self,
        module_id: str,
        inputs: dict[str, Any],
        output: dict[str, Any],
        context: Context,
    ) -> dict[str, Any] | None:
        """Delegate to the wrapped callback."""
        return self._callback(module_id, inputs, output, context)
