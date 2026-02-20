"""apcore - Schema-driven module development framework."""

from __future__ import annotations

# Core
from apcore.context import Context, ContextFactory, Identity
from apcore.registry import Registry
from apcore.registry.registry import MODULE_ID_PATTERN, REGISTRY_EVENTS
from apcore.registry.types import ModuleDescriptor
from apcore.executor import Executor, redact_sensitive, REDACTED_VALUE

# Module types
from apcore.module import ModuleAnnotations, ModuleExample, ValidationResult

# Config
from apcore.config import Config

# Errors
from apcore.errors import (
    ACLDeniedError,
    CallDepthExceededError,
    CallFrequencyExceededError,
    CircularCallError,
    CircularDependencyError,
    ConfigError,
    ErrorCodes,
    InvalidInputError,
    ModuleError,
    ModuleNotFoundError,
    ModuleTimeoutError,
    SchemaValidationError,
)

# ACL
from apcore.acl import ACL, ACLRule

# Middleware
from apcore.middleware import (
    AfterMiddleware,
    BeforeMiddleware,
    LoggingMiddleware,
    Middleware,
    MiddlewareManager,
)

# Decorators
from apcore.decorator import FunctionModule, module

# Bindings
from apcore.bindings import BindingLoader

# Observability
from apcore.observability import (
    ContextLogger,
    InMemoryExporter,
    MetricsCollector,
    MetricsMiddleware,
    ObsLoggingMiddleware,
    Span,
    StdoutExporter,
    TracingMiddleware,
)

__version__ = "0.3.0"

__all__ = [
    # Core
    "Context",
    "ContextFactory",
    "Identity",
    "Registry",
    "Executor",
    # Module types
    "ModuleAnnotations",
    "ModuleExample",
    "ValidationResult",
    # Registry types
    "ModuleDescriptor",
    # Config
    "Config",
    # Registry constants
    "REGISTRY_EVENTS",
    "MODULE_ID_PATTERN",
    # Errors
    "ErrorCodes",
    "ModuleError",
    "SchemaValidationError",
    "ACLDeniedError",
    "ModuleNotFoundError",
    "ConfigError",
    "CircularDependencyError",
    "InvalidInputError",
    "ModuleTimeoutError",
    "CallDepthExceededError",
    "CircularCallError",
    "CallFrequencyExceededError",
    # ACL
    "ACL",
    "ACLRule",
    # Middleware
    "Middleware",
    "MiddlewareManager",
    "BeforeMiddleware",
    "AfterMiddleware",
    "LoggingMiddleware",
    # Decorators
    "module",
    "FunctionModule",
    # Bindings
    "BindingLoader",
    # Utilities
    "redact_sensitive",
    "REDACTED_VALUE",
    # Observability
    "TracingMiddleware",
    "ContextLogger",
    "ObsLoggingMiddleware",
    "MetricsMiddleware",
    "MetricsCollector",
    "Span",
    "StdoutExporter",
    "InMemoryExporter",
]
