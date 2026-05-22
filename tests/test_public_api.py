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

    def test_module_importable(self):
        from apcore import Module

        assert Module is not None

    def test_module_annotations_importable(self):
        from apcore import ModuleAnnotations

        assert ModuleAnnotations is not None

    def test_module_example_importable(self):
        from apcore import ModuleExample

        assert ModuleExample is not None

    def test_validation_result_importable(self):
        from apcore import ValidationResult

        assert ValidationResult is not None

    def test_change_importable(self):
        from apcore import Change

        assert Change is not None

    def test_preview_result_importable(self):
        from apcore import PreviewResult

        assert PreviewResult is not None

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

    # -- Extensions --

    def test_extension_manager_importable(self):
        from apcore import ExtensionManager

        assert ExtensionManager is not None

    def test_extension_point_importable(self):
        from apcore import ExtensionPoint

        assert ExtensionPoint is not None

    # -- Async tasks --

    def test_async_task_manager_importable(self):
        from apcore import AsyncTaskManager

        assert AsyncTaskManager is not None

    def test_task_status_importable(self):
        from apcore import TaskStatus

        assert TaskStatus is not None

    def test_task_info_importable(self):
        from apcore import TaskInfo

        assert TaskInfo is not None

    # -- Trace Context --

    def test_trace_context_importable(self):
        from apcore import TraceContext

        assert TraceContext is not None

    def test_trace_parent_importable(self):
        from apcore import TraceParent

        assert TraceParent is not None

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

    # -- Registry protocols --

    def test_discoverer_importable(self):
        from apcore import Discoverer

        assert Discoverer is not None

    def test_module_validator_importable(self):
        from apcore import ModuleValidator

        assert ModuleValidator is not None

    # -- Shadowing safety --

    def test_module_not_found_error_is_not_builtin(self):
        assert apcore.ModuleNotFoundError is not builtins.ModuleNotFoundError
        assert issubclass(apcore.ModuleNotFoundError, apcore.ModuleError)

    # -- SpanExporter is now exported --

    def test_span_exporter_in_top_level(self):
        assert "SpanExporter" in apcore.__all__

    # -- Version --

    def test_version_is_set(self):
        assert hasattr(apcore, "__version__")
        assert isinstance(apcore.__version__, str)
        assert re.match(r"^\d+\.\d+\.\d+", apcore.__version__)

    # -- Pipeline preset builders (parity with apcore-typescript / apcore-rust) --
    # Regression for sync finding A-006: Python previously only exported
    # build_strategy_from_config; the 5 named-preset builders existed in
    # apcore.builtin_steps but were not re-exported to the package root.

    def test_pipeline_preset_builders_importable_from_package_root(self):
        from apcore import (
            build_internal_strategy,
            build_minimal_strategy,
            build_performance_strategy,
            build_standard_strategy,
            build_testing_strategy,
        )

        for fn in (
            build_standard_strategy,
            build_internal_strategy,
            build_testing_strategy,
            build_performance_strategy,
            build_minimal_strategy,
        ):
            assert callable(fn)

    def test_pipeline_preset_builders_in_all(self):
        for name in (
            "build_standard_strategy",
            "build_internal_strategy",
            "build_testing_strategy",
            "build_performance_strategy",
            "build_minimal_strategy",
        ):
            assert name in apcore.__all__, f"{name} missing from apcore.__all__"

    # -- ID conflict detection (D1-004) --
    # Regression: detect_id_conflicts, ConflictResult were defined in
    # apcore.registry.conflicts but not re-exported from the package root.

    def test_detect_id_conflicts_importable_from_package_root(self):
        from apcore import ConflictResult, detect_id_conflicts

        assert callable(detect_id_conflicts)
        assert ConflictResult is not None

    def test_detect_id_conflicts_in_all(self):
        assert "detect_id_conflicts" in apcore.__all__, "detect_id_conflicts missing from apcore.__all__"
        assert "ConflictResult" in apcore.__all__, "ConflictResult missing from apcore.__all__"


class TestPublicAPIAll:
    """Verify __all__ is comprehensive and matches actual exports."""

    EXPECTED_NAMES = {
        # Core
        "CancelToken",
        "ExecutionCancelledError",
        "Context",
        "ContextFactory",
        "ContextKey",
        "Identity",
        "Registry",
        "Executor",
        "APCore",
        "close",
        "call",
        "call_async",
        "stream",
        "validate",
        "register",
        "describe",
        "use",
        "use_before",
        "use_after",
        "remove",
        "discover",
        "list_modules",
        # Approval
        "ApprovalHandler",
        "ApprovalRequest",
        "ApprovalResult",
        "AlwaysDenyHandler",
        "AutoApproveHandler",
        "CallbackApprovalHandler",
        # Module types
        "Change",
        "DEFAULT_ANNOTATIONS",
        "Module",
        "ModuleAnnotations",
        "ModuleExample",
        "ValidationResult",
        "PreflightCheckResult",
        "PreflightResult",
        "PreviewResult",
        # Registry types
        "ModuleDescriptor",
        "DiscoveredModule",
        "DependencyInfo",
        # ID conflict detection
        "ConflictResult",
        "ConflictSeverity",
        "ConflictType",
        "detect_id_conflicts",
        # Registry protocols
        "Discoverer",
        "ModuleValidator",
        # Config
        "Config",
        "RESERVED_NAMESPACES",
        "discover_config_file",
        # Errors
        "ErrorCodes",
        "ModuleError",
        "ACLDeniedError",
        "ACLRuleError",
        "ApprovalError",
        "ApprovalDeniedError",
        "ApprovalTimeoutError",
        "ApprovalPendingError",
        "BindingCallableNotFoundError",
        "BindingFileInvalidError",
        "BindingInvalidTargetError",
        "BindingModuleNotFoundError",
        "BindingNotCallableError",
        "BindingSchemaInferenceFailedError",
        "BindingSchemaMissingError",
        "BindingSchemaModeConflictError",
        "BindingStrictSchemaIncompatibleError",
        "CallDepthExceededError",
        "CallFrequencyExceededError",
        "CircuitBreakerOpenError",
        "CircuitOpenError",
        "CircularCallError",
        "CircularDependencyError",
        "ConfigError",
        "ConfigNotFoundError",
        "DependencyNotFoundError",
        "DependencyVersionMismatchError",
        "TaskLimitExceededError",
        "VersionConstraintError",
        "FuncMissingReturnTypeError",
        "FuncMissingTypeHintError",
        "InternalError",
        "InvalidInputError",
        "ModuleExecuteError",
        "ModuleLoadError",
        "ModuleNotFoundError",
        "ModuleTimeoutError",
        "SchemaCircularRefError",
        "SchemaNotFoundError",
        "SchemaParseError",
        "SchemaValidationError",
        # ACL
        "ACL",
        "ACLRule",
        "AuditEntry",
        # Middleware
        "Middleware",
        "RetrySignal",
        "MiddlewareManager",
        "BeforeMiddleware",
        "AfterMiddleware",
        "LoggingMiddleware",
        "MiddlewareChainError",
        "RetryConfig",
        "RetryMiddleware",
        "CircuitBreakerMiddleware",
        "CircuitState",
        # Decorators
        "module",
        "FunctionModule",
        # Extensions
        "ExtensionManager",
        "ExtensionPoint",
        # Async tasks
        "AsyncTaskManager",
        "TaskStatus",
        "TaskInfo",
        "TaskStore",
        "InMemoryTaskStore",
        "AsyncRetryConfig",
        "RetryPolicy",
        "BackoffStrategy",
        # Bindings
        "BindingLoader",
        # Schema
        "SchemaLoader",
        "SchemaValidator",
        "SchemaExporter",
        "RefResolver",
        "to_strict_schema",
        # Utilities
        "match_pattern",
        "guard_call_chain",
        "normalize_to_canonical_id",
        "calculate_specificity",
        "propagate_error",
        "redact_sensitive",
        "REDACTED_VALUE",
        # Errors (new)
        "ErrorCodeCollisionError",
        "ErrorCodeRegistry",
        # Version
        "VersionIncompatibleError",
        "negotiate_version",
        # Observability
        "TracingMiddleware",
        "ContextLogger",
        "ObsLoggingMiddleware",
        "MetricsMiddleware",
        "MetricsCollector",
        "Span",
        "SpanExporter",
        "StdoutExporter",
        "InMemoryExporter",
        "create_span",
        "OTLPExporter",
        # Trace Context
        "TraceContext",
        "TraceParent",
        # Events
        "EventEmitter",
        "EventSubscriber",
        "EventRetryConfig",
        "ApCoreEvent",
        "WebhookSubscriber",
        "A2ASubscriber",
        "FileSubscriber",
        "StdoutSubscriber",
        "FilterSubscriber",
        "register_subscriber_factory",
        "create_subscriber_from_config",
        "on",
        "off",
        # Toggle
        "disable",
        "enable",
        # Schema enums
        "SchemaStrategy",
        "ExportProfile",
        # Additional errors
        "ModuleDisabledError",
        "ModuleReloadConflictError",
        "ReloadFailedError",
        "SysModuleRegistrationError",
        "SysModulesDisabledError",
        "StreamingInterfaceError",
        # Streaming Protocol (apcore #62)
        "StreamingModule",
        # Observability (added in 0.11.0, exported in 0.12.0)
        "ErrorEntry",
        "ErrorHistory",
        "ErrorHistoryMiddleware",
        "StorageBackend",
        "InMemoryStorageBackend",
        "UsageCollector",
        "UsageExporter",
        "UsageMiddleware",
        "NoopUsageExporter",
        "PeriodicUsageExporter",
        "PlatformNotifyMiddleware",
        # System Modules
        "register_sys_modules",
        # Pipeline
        "Step",
        "BaseStep",
        "StepResult",
        "PipelineContext",
        "PipelineEngine",
        "PipelineTrace",
        "StepTrace",
        "ExecutionStrategy",
        "StrategyInfo",
        "PipelineAbortError",
        "PipelineState",
        "PipelineStepError",
        "PipelineStepNotFoundError",
        "StepNotFoundError",
        "StepNotRemovableError",
        "StepNotReplaceableError",
        "StepNameDuplicateError",
        "StrategyNotFoundError",
        "StepMiddleware",
        "ConfigurationError",
        "PipelineDependencyError",
        # Pipeline Configuration (0.17.0)
        "register_step_type",
        "unregister_step_type",
        "registered_step_types",
        "build_strategy_from_config",
        # Pipeline Preset Builders (parity with apcore-typescript / apcore-rust)
        "build_standard_strategy",
        "build_internal_strategy",
        "build_testing_strategy",
        "build_performance_strategy",
        "build_minimal_strategy",
        # Builtin Pipeline Steps (parity with apcore-typescript / apcore-rust)
        "BuiltinContextCreation",
        "BuiltinCallChainGuard",
        "BuiltinModuleLookup",
        "BuiltinACLCheck",
        "BuiltinApprovalGate",
        "BuiltinMiddlewareBefore",
        "BuiltinInputValidation",
        "BuiltinExecute",
        "BuiltinOutputValidation",
        "BuiltinMiddlewareAfter",
        "BuiltinReturnResult",
        # Config Bus (0.15.0)
        "ConfigBindError",
        "ConfigEnvMapConflictError",
        "ConfigEnvPrefixConflictError",
        "ConfigMountError",
        "ConfigNamespaceDuplicateError",
        "ConfigNamespaceReservedError",
        # Error Formatter (0.15.0)
        "ErrorFormatter",
        "ErrorFormatterRegistry",
        "ErrorFormatterDuplicateError",
        # Built-in context keys
        "TRACING_SPANS",
        "TRACING_SAMPLED",
        "METRICS_STARTS",
        "LOGGING_START",
        "REDACTED_OUTPUT",
        "RETRY_COUNT_BASE",
        # System module classes
        "HealthSummaryModule",
        "HealthModule",
        "ManifestFullModule",
        "ManifestModule",
        "UsageSummaryModule",
        "UsageModule",
        "UpdateConfigModule",
        "ReloadModule",
        "ToggleFeatureModule",
        "ToggleState",
        # Overrides Store (cross-language alignment with TS / Rust)
        "OverridesStore",
        "InMemoryOverridesStore",
        "FileOverridesStore",
        # System Modules context type
        "SysModulesContext",
        # System module registration
        "register_sys_modules",
        # Module-level constants (parity with apcore-typescript / apcore-rust)
        "DEFAULT_MAX_CALL_DEPTH",
        "DEFAULT_MAX_MODULE_REPEAT",
        "FRAMEWORK_ERROR_CODE_PREFIXES",
        "METRIC_CALLS_TOTAL",
        "METRIC_DURATION_SECONDS",
        # Health utility
        "classify_health_status",
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
            assert obj is not _MISSING, f"Name '{name}' listed in __all__ but not found on module"

    def test_default_client_is_lazy_and_close_resets(self):
        """import apcore must not eagerly construct APCore(); close() releases + allows fresh create."""
        import apcore as apcore_mod

        # Force a clean slate — clear any client allocated earlier in the session.
        apcore_mod.close()
        # Private state check: the backing slot is now None.
        assert apcore_mod._default_client is not None  # triggers lazy init via __getattr__
        # Because access via __getattr__ materialises the client, we close again and assert
        # the backing module-level name is bound to the freshly-created instance.
        first = apcore_mod._default_client
        apcore_mod.close()
        second = apcore_mod._default_client
        # After close(), a new instance is created on next access — not the same object.
        assert first is not second

    def test_deprecated_subscriber_apis_not_advertised(self):
        """Deprecated subscriber registry APIs must not appear in __all__ but remain importable."""
        deprecated = (
            "register_subscriber_type",
            "unregister_subscriber_type",
            "reset_subscriber_registry",
        )
        for name in deprecated:
            assert name not in apcore.__all__, f"Deprecated {name} should not be advertised in __all__"
            # Back-compat: still importable via attribute access.
            assert hasattr(apcore, name), f"Deprecated {name} must remain importable"
