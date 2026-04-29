"""Auto-registration of sys.* modules and middleware from config (PRD F1-F9)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypedDict

import yaml

from apcore.config import Config
from apcore.errors import SysModuleRegistrationError
from apcore.events.circuit_breaker import CircuitBreakerWrapper
from apcore.events.emitter import ApCoreEvent, EventEmitter, EventSubscriber
from apcore.events.subscribers import (
    A2ASubscriber,
    FileSubscriber,
    FilterSubscriber,
    StdoutSubscriber,
    WebhookSubscriber,
)
from apcore.executor import Executor
from apcore.middleware.error_history_middleware import ErrorHistoryMiddleware
from apcore.middleware.platform_notify import PlatformNotifyMiddleware
from apcore.observability.error_history import ErrorHistory
from apcore.observability.metrics import MetricsCollector
from apcore.registry.registry import Registry
from apcore.sys_modules.audit import AuditStore
from apcore.sys_modules.control import (
    ReloadModule,
    ToggleFeatureModule,
    ToggleState,
    UpdateConfigModule,
)
from apcore.sys_modules.health import HealthModule, HealthSummaryModule
from apcore.sys_modules.manifest import ManifestFullModule, ManifestModule
from apcore.sys_modules.usage import UsageModule, UsageSummaryModule
from apcore.observability.usage import UsageCollector, UsageMiddleware

logger = logging.getLogger(__name__)

__all__ = [
    "register_sys_modules",
    "register_subscriber_type",
    "unregister_subscriber_type",
    "reset_subscriber_registry",
    "SysModulesContext",
]


class SysModulesContext(TypedDict, total=False):
    """Type of the dict returned by :func:`register_sys_modules`.

    Exported at the package root (``from apcore import SysModulesContext``) for
    cross-language parity with the TypeScript and Rust SDKs.

    All keys are optional because they are only present when the corresponding
    feature is enabled in the ``sys_modules`` config section.
    """

    error_history: Any
    event_emitter: Any
    metrics_collector: Any
    usage_collector: Any
    audit_store: Any


# ---------------------------------------------------------------------------
# Subscriber type registry — extensible factory for EventSubscriber types
# ---------------------------------------------------------------------------

#: Maps subscriber type name → factory function(config_dict) → EventSubscriber
_subscriber_factories: dict[str, Callable[[dict[str, Any]], EventSubscriber]] = {}


def _default_webhook_factory(cfg: dict[str, Any]) -> EventSubscriber:
    return WebhookSubscriber(
        url=cfg["url"],
        headers=cfg.get("headers"),
        retry_count=cfg.get("retry_count", 3),
        timeout_ms=cfg.get("timeout_ms", 5000),
    )


def _default_a2a_factory(cfg: dict[str, Any]) -> EventSubscriber:
    return A2ASubscriber(
        platform_url=cfg["platform_url"],
        auth=cfg.get("auth"),
        timeout_ms=cfg.get("timeout_ms", 5000),
    )


def _default_file_factory(cfg: dict[str, Any]) -> EventSubscriber:
    return FileSubscriber(
        path=cfg["path"],
        append=cfg.get("append", True),
        output_format=cfg.get("format", "json"),
        rotate_bytes=cfg.get("rotate_bytes"),
    )


def _default_stdout_factory(cfg: dict[str, Any]) -> EventSubscriber:
    return StdoutSubscriber(
        output_format=cfg.get("format", "text"),
        level_filter=cfg.get("level_filter"),
    )


def _default_filter_factory(cfg: dict[str, Any]) -> EventSubscriber:
    delegate_cfg = {**cfg.get("delegate_config", {}), "type": cfg["delegate_type"]}
    delegate = _create_subscriber(delegate_cfg)
    return FilterSubscriber(
        delegate=delegate,
        include_events=cfg.get("include_events"),
        exclude_events=cfg.get("exclude_events"),
    )


# Built-in subscriber type registry
_BUILTIN_FACTORIES: dict[str, Callable[[dict[str, Any]], EventSubscriber]] = {
    "webhook": _default_webhook_factory,
    "a2a": _default_a2a_factory,
    "file": _default_file_factory,
    "stdout": _default_stdout_factory,
    "filter": _default_filter_factory,
}

# Register built-in types
_subscriber_factories.update(_BUILTIN_FACTORIES)


def register_subscriber_type(
    type_name: str,
    factory: Callable[[dict[str, Any]], EventSubscriber],
) -> None:
    """Register a custom subscriber type factory."""
    _subscriber_factories[type_name] = factory


def unregister_subscriber_type(type_name: str) -> None:
    """Remove a previously registered subscriber type."""
    del _subscriber_factories[type_name]


def reset_subscriber_registry() -> None:
    """Reset the subscriber registry to built-in types only."""
    _subscriber_factories.clear()
    _subscriber_factories.update(_BUILTIN_FACTORIES)


# ---------------------------------------------------------------------------
# Namespace-mode helpers — §9.15.3
# ---------------------------------------------------------------------------

_MISSING = object()


def _nested_dict_get(data: dict[str, Any], dotted_key: str, default: Any) -> Any:
    """Get a value from a nested dict using a dot-separated key path."""
    current: Any = data
    for key in dotted_key.split("."):
        if not isinstance(current, dict):
            return default
        v = current.get(key, _MISSING)
        if v is _MISSING:
            return default
        current = v
    return current


def _resolve_sys_cfg(config: Config) -> dict[str, Any] | None:
    """Return the sys_modules namespace dict in namespace mode (§9.15.3)."""
    if getattr(config, "_mode", "legacy") == "namespace":
        return config.namespace("sys_modules") or {}
    return None


def _cfg_get(sys_cfg: dict[str, Any] | None, config: Config, sub_key: str, default: Any) -> Any:
    """Get a sys_modules value — namespace dict in namespace mode, dot-path in legacy."""
    if sys_cfg is not None:
        return _nested_dict_get(sys_cfg, sub_key, default)
    return config.get(f"sys_modules.{sub_key}", default)


def _load_overrides(
    config: Config,
    overrides_path: str,
    toggle_state: Any | None = None,
) -> None:
    """Load YAML overrides file and apply each key to config (in-memory only).

    Keys prefixed with ``toggle.`` are applied to the toggle_state (if provided)
    rather than the config. All other keys are set as config dot-paths.
    """
    p = Path(overrides_path)
    if not p.exists():
        return
    try:
        with p.open("r", encoding="utf-8") as f:
            overrides: Any = yaml.safe_load(f)
        if not isinstance(overrides, dict):
            logger.warning("Overrides file %s did not contain a mapping; skipping", overrides_path)
            return
        config_count = 0
        toggle_count = 0
        for key, value in overrides.items():
            if not isinstance(key, str):
                continue
            if key.startswith("toggle.") and toggle_state is not None:
                module_id = key[len("toggle.") :]
                if isinstance(value, bool):
                    if value:
                        toggle_state.enable(module_id)
                    else:
                        toggle_state.disable(module_id)
                    toggle_count += 1
            elif not key.startswith("_"):
                config.set(key, value)
                config_count += 1
        logger.info(
            "Loaded %d config overrides and %d toggle overrides from %s",
            config_count,
            toggle_count,
            overrides_path,
        )
    except Exception as exc:
        logger.error("Failed to load overrides from %s: %s", overrides_path, exc)


def register_sys_modules(
    registry: Registry,
    executor: Executor,
    config: Config,
    metrics_collector: MetricsCollector | None = None,
    fail_on_error: bool = False,
    audit_store: AuditStore | None = None,
) -> dict[str, Any]:
    """Auto-register all sys.* modules and middleware based on config.

    Args:
        registry: Module registry.
        executor: Executor for middleware registration.
        config: Configuration with sys_modules section.
        metrics_collector: Optional MetricsCollector instance.
        fail_on_error: When True, any registration failure raises
            SysModuleRegistrationError immediately. When False (default),
            failures are logged at ERROR level and execution continues.
        audit_store: Optional audit store for control module audit trails.

    Returns:
        Dict with references to created components for testing/inspection.
    """
    result: dict[str, Any] = {}

    # §9.15.3: prefer config.namespace("sys_modules") in namespace mode
    sys_cfg = _resolve_sys_cfg(config)

    if not _cfg_get(sys_cfg, config, "enabled", False):
        return result

    # Load overrides from disk before registering modules
    overrides_path: str | None = _cfg_get(sys_cfg, config, "control.overrides_path", None)
    if overrides_path:
        _load_overrides(config, overrides_path)

    error_history = _create_error_history(config, sys_cfg)
    result["error_history"] = error_history

    eh_middleware = ErrorHistoryMiddleware(error_history)
    executor.use(eh_middleware)
    result["error_history_middleware"] = eh_middleware

    # Usage tracking
    usage_collector = UsageCollector()
    result["usage_collector"] = usage_collector
    usage_middleware = UsageMiddleware(usage_collector)
    executor.use(usage_middleware)
    result["usage_middleware"] = usage_middleware

    _register_sys_modules(
        registry,
        config,
        metrics_collector,
        error_history,
        usage_collector,
        fail_on_error=fail_on_error,
    )

    if _cfg_get(sys_cfg, config, "events.enabled", False):
        _setup_events(
            registry,
            executor,
            config,
            sys_cfg,
            metrics_collector,
            error_history,
            usage_collector,
            result,
            overrides_path=overrides_path,
            audit_store=audit_store,
            fail_on_error=fail_on_error,
        )

    return result


def _create_error_history(config: Config, sys_cfg: dict[str, Any] | None) -> ErrorHistory:
    """Create an ErrorHistory instance from config values."""
    max_per_module = _cfg_get(sys_cfg, config, "error_history.max_entries_per_module", 50)
    max_total = _cfg_get(sys_cfg, config, "error_history.max_total_entries", 1000)
    return ErrorHistory(
        max_entries_per_module=max_per_module,
        max_total_entries=max_total,
    )


def _register_sys_module(
    registry: Registry,
    module_id: str,
    module: Any,
    fail_on_error: bool = False,
) -> None:
    """Register a sys module, bypassing reserved word checks.

    When fail_on_error is True, raises SysModuleRegistrationError on failure.
    When fail_on_error is False, logs at ERROR level and continues.
    """
    try:
        registry.register_internal(module_id, module)
    except Exception as exc:
        if fail_on_error:
            raise SysModuleRegistrationError(module_id=module_id, reason=str(exc)) from exc
        logger.error("System module '%s' failed to register: %s. Continuing.", module_id, exc)


def _register_sys_modules(
    registry: Registry,
    config: Config,
    metrics_collector: MetricsCollector | None,
    error_history: ErrorHistory,
    usage_collector: UsageCollector,
    fail_on_error: bool = False,
) -> None:
    """Register all sys.* modules: health, manifest, and usage."""
    effective_metrics = metrics_collector if metrics_collector is not None else MetricsCollector()

    _register_sys_module(
        registry,
        "system.health.summary",
        HealthSummaryModule(
            registry=registry,
            metrics_collector=effective_metrics,
            error_history=error_history,
            config=config,
        ),
        fail_on_error=fail_on_error,
    )

    _register_sys_module(
        registry,
        "system.health.module",
        HealthModule(
            registry=registry,
            metrics_collector=effective_metrics,
            error_history=error_history,
        ),
        fail_on_error=fail_on_error,
    )

    _register_sys_module(
        registry,
        "system.manifest.module",
        ManifestModule(registry=registry, config=config),
        fail_on_error=fail_on_error,
    )

    _register_sys_module(
        registry,
        "system.manifest.full",
        ManifestFullModule(registry=registry, config=config),
        fail_on_error=fail_on_error,
    )

    _register_sys_module(
        registry,
        "system.usage.summary",
        UsageSummaryModule(collector=usage_collector),
        fail_on_error=fail_on_error,
    )

    _register_sys_module(
        registry,
        "system.usage.module",
        UsageModule(registry=registry, usage_collector=usage_collector),
        fail_on_error=fail_on_error,
    )


def _setup_events(
    registry: Registry,
    executor: Executor,
    config: Config,
    sys_cfg: dict[str, Any] | None,
    metrics_collector: MetricsCollector | None,
    error_history: ErrorHistory,
    usage_collector: UsageCollector,
    result: dict[str, Any],
    overrides_path: str | None = None,
    audit_store: AuditStore | None = None,
    fail_on_error: bool = False,
) -> None:
    """Set up event emitter, subscribers, PlatformNotifyMiddleware, control modules, and registry bridge."""
    event_emitter = EventEmitter()
    result["event_emitter"] = event_emitter

    error_rate_threshold = _cfg_get(sys_cfg, config, "events.thresholds.error_rate", 0.1)
    latency_p99_threshold = _cfg_get(sys_cfg, config, "events.thresholds.latency_p99_ms", 5000.0)
    pn_middleware = PlatformNotifyMiddleware(
        event_emitter=event_emitter,
        metrics_collector=metrics_collector,
        error_rate_threshold=error_rate_threshold,
        latency_p99_threshold_ms=latency_p99_threshold,
    )
    executor.use(pn_middleware)
    result["platform_notify_middleware"] = pn_middleware

    _register_control_modules(
        registry,
        config,
        event_emitter,
        overrides_path=overrides_path,
        audit_store=audit_store,
        fail_on_error=fail_on_error,
    )

    _instantiate_subscribers(config, sys_cfg, event_emitter)
    _bridge_registry_events(registry, event_emitter)


def _register_control_modules(
    registry: Registry,
    config: Config,
    event_emitter: EventEmitter,
    overrides_path: str | None = None,
    audit_store: AuditStore | None = None,
    fail_on_error: bool = False,
) -> None:
    """Register control sys modules that require an EventEmitter.

    A fresh ToggleState is created per call so multiple Registry instances
    in the same process do not share toggle state.
    """
    toggle_state = ToggleState()

    _register_sys_module(
        registry,
        "system.control.update_config",
        UpdateConfigModule(
            config=config,
            event_emitter=event_emitter,
            overrides_path=overrides_path,
            audit_store=audit_store,
        ),
        fail_on_error=fail_on_error,
    )

    _register_sys_module(
        registry,
        "system.control.reload_module",
        ReloadModule(
            registry=registry,
            event_emitter=event_emitter,
            audit_store=audit_store,
        ),
        fail_on_error=fail_on_error,
    )

    _register_sys_module(
        registry,
        "system.control.toggle_feature",
        ToggleFeatureModule(
            registry=registry,
            event_emitter=event_emitter,
            toggle_state=toggle_state,
            overrides_path=overrides_path,
            audit_store=audit_store,
        ),
        fail_on_error=fail_on_error,
    )


def _instantiate_subscribers(config: Config, sys_cfg: dict[str, Any] | None, event_emitter: EventEmitter) -> None:
    """Create and subscribe EventSubscribers from config, each wrapped in a circuit breaker."""
    subscribers_config = _cfg_get(sys_cfg, config, "events.subscribers", [])
    for sub_cfg in subscribers_config:
        try:
            subscriber = _create_subscriber(sub_cfg)
            cb_cfg: dict[str, Any] = sub_cfg.get("circuit_breaker") or {}
            wrapped = CircuitBreakerWrapper(
                subscriber=subscriber,
                emitter=event_emitter,
                timeout_ms=cb_cfg.get("timeout_ms", 5000),
                open_threshold=cb_cfg.get("open_threshold", 5),
                recovery_window_ms=cb_cfg.get("recovery_window_ms", 60000),
            )
            event_emitter.subscribe(wrapped)
        except Exception:
            logger.warning(
                "Failed to instantiate subscriber: %s",
                sub_cfg,
                exc_info=True,
            )


def _create_subscriber(sub_cfg: dict[str, Any]) -> EventSubscriber:
    """Factory for EventSubscriber from a config dict."""
    sub_type = sub_cfg.get("type", "")
    factory = _subscriber_factories.get(sub_type)
    if factory is None:
        registered = ", ".join(sorted(_subscriber_factories.keys()))
        raise ValueError(
            f"Unknown subscriber type: {sub_type!r}. "
            f"Registered types: [{registered}]. "
            f"Use register_subscriber_type() to add custom types."
        )
    return factory(sub_cfg)


def _bridge_registry_events(registry: Registry, emitter: EventEmitter) -> None:
    """Bridge registry register/unregister events to the EventEmitter."""

    def on_register(module_id: str, module: Any) -> None:
        emitter.emit(
            ApCoreEvent(
                event_type="module_registered",
                module_id=module_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                severity="info",
                data={},
            )
        )

    def on_unregister(module_id: str, module: Any) -> None:
        emitter.emit(
            ApCoreEvent(
                event_type="module_unregistered",
                module_id=module_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                severity="info",
                data={},
            )
        )

    registry.on("register", on_register)
    registry.on("unregister", on_unregister)
