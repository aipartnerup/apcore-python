"""Tests for the apcore public API surface.

Verifies that all expected names are importable from the top-level
``apcore`` package and that ``__all__`` is comprehensive.
"""

import builtins
import re

import apcore


class TestPublicAPIImports:
    """Every public component must be importable from ``import apcore``."""

    # -- Core --

    def test_context_importable(self):
        from apcore import Context

        assert Context is not None

    def test_identity_importable(self):
        from apcore import Identity

        assert Identity is not None

    def test_registry_importable(self):
        from apcore import Registry

        assert Registry is not None

    def test_executor_importable(self):
        from apcore import Executor

        assert Executor is not None

    # -- Module types --

    def test_module_annotations_importable(self):
        from apcore import ModuleAnnotations

        assert ModuleAnnotations is not None

    def test_module_example_importable(self):
        from apcore import ModuleExample

        assert ModuleExample is not None

    def test_validation_result_importable(self):
        from apcore import ValidationResult

        assert ValidationResult is not None

    # -- Registry types --

    def test_module_descriptor_importable(self):
        from apcore import ModuleDescriptor

        assert ModuleDescriptor is not None

    # -- Config --

    def test_config_importable(self):
        from apcore import Config

        assert Config is not None

    # -- Errors --

    def test_module_error_importable(self):
        from apcore import ModuleError

        assert ModuleError is not None

    def test_schema_validation_error_importable(self):
        from apcore import SchemaValidationError

        assert SchemaValidationError is not None

    def test_acl_denied_error_importable(self):
        from apcore import ACLDeniedError

        assert ACLDeniedError is not None

    def test_module_not_found_error_importable(self):
        from apcore import ModuleNotFoundError

        assert ModuleNotFoundError is not None

    def test_config_error_importable(self):
        from apcore import ConfigError

        assert ConfigError is not None

    def test_circular_dependency_error_importable(self):
        from apcore import CircularDependencyError

        assert CircularDependencyError is not None

    def test_invalid_input_error_importable(self):
        from apcore import InvalidInputError

        assert InvalidInputError is not None

    def test_module_timeout_error_importable(self):
        from apcore import ModuleTimeoutError

        assert ModuleTimeoutError is not None

    def test_call_depth_exceeded_error_importable(self):
        from apcore import CallDepthExceededError

        assert CallDepthExceededError is not None

    def test_circular_call_error_importable(self):
        from apcore import CircularCallError

        assert CircularCallError is not None

    def test_call_frequency_exceeded_error_importable(self):
        from apcore import CallFrequencyExceededError

        assert CallFrequencyExceededError is not None

    # -- ACL --

    def test_acl_importable(self):
        from apcore import ACL

        assert ACL is not None

    def test_acl_rule_importable(self):
        from apcore import ACLRule

        assert ACLRule is not None

    # -- Middleware --

    def test_middleware_importable(self):
        from apcore import Middleware

        assert Middleware is not None

    def test_middleware_manager_importable(self):
        from apcore import MiddlewareManager

        assert MiddlewareManager is not None

    def test_before_middleware_importable(self):
        from apcore import BeforeMiddleware

        assert BeforeMiddleware is not None

    def test_after_middleware_importable(self):
        from apcore import AfterMiddleware

        assert AfterMiddleware is not None

    def test_logging_middleware_importable(self):
        from apcore import LoggingMiddleware

        assert LoggingMiddleware is not None

    # -- Decorators --

    def test_module_decorator_importable(self):
        from apcore import module

        assert module is not None

    def test_function_module_importable(self):
        from apcore import FunctionModule

        assert FunctionModule is not None

    # -- Bindings --

    def test_binding_loader_importable(self):
        from apcore import BindingLoader

        assert BindingLoader is not None

    # -- Utilities --

    def test_redact_sensitive_importable(self):
        from apcore import redact_sensitive

        assert redact_sensitive is not None

    def test_redacted_value_importable(self):
        from apcore import REDACTED_VALUE

        assert REDACTED_VALUE == "***REDACTED***"

    # -- Observability --

    def test_tracing_middleware_importable(self):
        from apcore import TracingMiddleware

        assert TracingMiddleware is not None

    def test_context_logger_importable(self):
        from apcore import ContextLogger

        assert ContextLogger is not None

    def test_obs_logging_middleware_importable(self):
        from apcore import ObsLoggingMiddleware

        assert ObsLoggingMiddleware is not None

    def test_metrics_middleware_importable(self):
        from apcore import MetricsMiddleware

        assert MetricsMiddleware is not None

    def test_metrics_collector_importable(self):
        from apcore import MetricsCollector

        assert MetricsCollector is not None

    def test_span_importable(self):
        from apcore import Span

        assert Span is not None

    def test_stdout_exporter_importable(self):
        from apcore import StdoutExporter

        assert StdoutExporter is not None

    def test_in_memory_exporter_importable(self):
        from apcore import InMemoryExporter

        assert InMemoryExporter is not None

    # -- Shadowing safety --

    def test_module_not_found_error_is_not_builtin(self):
        assert apcore.ModuleNotFoundError is not builtins.ModuleNotFoundError
        assert issubclass(apcore.ModuleNotFoundError, apcore.ModuleError)

    # -- Explicit exclusions --

    def test_span_exporter_not_in_top_level(self):
        assert "SpanExporter" not in apcore.__all__

    # -- Version --

    def test_version_is_set(self):
        assert hasattr(apcore, "__version__")
        assert isinstance(apcore.__version__, str)
        assert re.match(r"^\d+\.\d+\.\d+", apcore.__version__)


class TestPublicAPIAll:
    """Verify __all__ is comprehensive and matches actual exports."""

    EXPECTED_NAMES = {
        # Core
        "Context",
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
        # Errors
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
    }

    def test_all_contains_all_expected_names(self):
        actual = set(apcore.__all__)
        missing = self.EXPECTED_NAMES - actual
        assert not missing, f"Missing from __all__: {missing}"

    def test_all_has_no_unexpected_extras(self):
        actual = set(apcore.__all__)
        extra = actual - self.EXPECTED_NAMES
        assert not extra, f"Unexpected names in __all__: {extra}"

    def test_all_names_are_importable(self):
        _MISSING = object()
        for name in apcore.__all__:
            obj = getattr(apcore, name, _MISSING)
            assert (
                obj is not _MISSING
            ), f"Name '{name}' listed in __all__ but not found on module"
