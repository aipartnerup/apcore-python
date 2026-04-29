"""Middleware base class and adapters for apcore."""

from apcore.middleware.adapters import AfterMiddleware, BeforeMiddleware
from apcore.middleware.base import Middleware, RetrySignal
from apcore.middleware.circuit_breaker import CircuitBreakerMiddleware, CircuitState
from apcore.middleware.error_history_middleware import ErrorHistoryMiddleware
from apcore.middleware.logging import LoggingMiddleware
from apcore.middleware.manager import MiddlewareChainError, MiddlewareManager
from apcore.middleware.platform_notify import PlatformNotifyMiddleware
from apcore.middleware.retry import RetryConfig, RetryMiddleware

__all__ = [
    "Middleware",
    "RetrySignal",
    "BeforeMiddleware",
    "AfterMiddleware",
    "MiddlewareManager",
    "MiddlewareChainError",
    "LoggingMiddleware",
    "RetryConfig",
    "RetryMiddleware",
    "ErrorHistoryMiddleware",
    "PlatformNotifyMiddleware",
    "CircuitBreakerMiddleware",
    "CircuitState",
]
