"""Pipeline YAML configuration: step type registry and strategy builder."""

from __future__ import annotations

import logging
from typing import Any, Callable

from apcore.pipeline import (
    BaseStep,
    ConfigurationError,
    StepNotFoundError,
)

_logger = logging.getLogger(__name__)

# Global step type registry: name → factory (class or callable)
_step_type_registry: dict[str, type[BaseStep] | Callable[[dict[str, Any]], BaseStep]] = {}


def register_step_type(
    name: str,
    factory: type[BaseStep] | Callable[[dict[str, Any]], BaseStep],
) -> None:
    """Register a step type for YAML pipeline configuration.

    Args:
        name: Type name referenced in YAML ``type`` field.
              Must be non-empty, no whitespace, unique.
        factory: Either a BaseStep subclass or a callable ``(config_dict) -> BaseStep``.

    Raises:
        ValueError: If name is empty, contains whitespace, or is already registered.
    """
    if not name or " " in name:
        raise ValueError(f"Invalid step type name: '{name}'")
    if name in _step_type_registry:
        raise ValueError(f"Step type '{name}' is already registered")
    _step_type_registry[name] = factory


def unregister_step_type(name: str) -> bool:
    """Remove a registered step type. Returns True if found and removed."""
    return _step_type_registry.pop(name, None) is not None


def registered_step_types() -> list[str]:
    """Return a list of all registered step type names."""
    return list(_step_type_registry.keys())


def _resolve_step(step_def: dict[str, Any]) -> BaseStep:
    """Resolve a step definition dict into a BaseStep instance.

    Resolution order:
      1. ``type`` field → look up in registry
      2. ``handler`` field → dynamic import (Python-native)
      3. Neither → raise ValueError

    Args:
        step_def: Dict with at least ``name`` and one of ``type``/``handler``.

    Returns:
        Configured BaseStep instance.

    Raises:
        ValueError: If step cannot be resolved.
    """
    step_name = step_def.get("name", "")
    type_name = step_def.get("type")
    handler_path = step_def.get("handler")
    config = step_def.get("config", {})

    # Extract metadata fields
    match_modules = step_def.get("match_modules")
    if match_modules is not None:
        match_modules = tuple(match_modules)
    ignore_errors = step_def.get("ignore_errors", False)
    pure = step_def.get("pure", False)
    timeout_ms = step_def.get("timeout_ms", 0)

    # (1) Try type registry
    if type_name and type_name in _step_type_registry:
        factory = _step_type_registry[type_name]
        if isinstance(factory, type) and issubclass(factory, BaseStep):
            # Factory is a BaseStep subclass; either it provides defaults for
            # `name` (most user steps do) or the YAML supplied `config` overrides.
            step = factory(**config) if config else factory()  # type: ignore[call-arg]
        else:
            step = factory(config)
        # Override metadata from YAML
        step.name = step_name or step.name
        step.match_modules = match_modules
        step.ignore_errors = ignore_errors
        step.pure = pure
        step.timeout_ms = timeout_ms
        return step

    # (2) Try handler import (Python-native dynamic import)
    if handler_path:
        step = _import_step(handler_path, step_name, config)
        step.match_modules = match_modules
        step.ignore_errors = ignore_errors
        step.pure = pure
        step.timeout_ms = timeout_ms
        return step

    # (3) Neither
    if type_name:
        raise ValueError(
            f"Step type '{type_name}' not registered. Register with: register_step_type('{type_name}', YourStepClass)"
        )
    raise ValueError(f"Step '{step_name}' has neither 'type' nor 'handler'")


def _import_step(handler_path: str, name: str, config: dict[str, Any]) -> BaseStep:
    """Import a step class from a handler path like 'myapp.steps:RateLimitStep'."""
    if ":" not in handler_path:
        raise ValueError(f"Invalid handler path '{handler_path}'. Expected format: 'module.path:ClassName'")
    module_path, class_name = handler_path.split(":", 1)

    import importlib

    try:
        mod = importlib.import_module(module_path)
    except ImportError as exc:
        raise ValueError(f"Cannot import module '{module_path}': {exc}") from exc

    cls = getattr(mod, class_name, None)
    if cls is None:
        raise ValueError(f"Class '{class_name}' not found in module '{module_path}'")

    if config:
        step = cls(**config)
    else:
        step = cls()

    if name:
        step.name = name
    return step


def build_strategy_from_config(
    pipeline_config: dict[str, Any],
    *,
    registry: Any,
    config: Any | None = None,
    acl: Any | None = None,
    approval_handler: Any | None = None,
    middlewares: list[Any] | None = None,
    middleware_manager: Any | None = None,
    executor: Any | None = None,
) -> Any:
    """Build an ExecutionStrategy from YAML pipeline configuration.

    Starts with ``build_standard_strategy()``, then applies:
      1. ``remove`` — remove named steps
      2. ``configure`` — update existing step fields
      3. ``steps`` — resolve and insert custom steps

    Args:
        pipeline_config: The ``pipeline`` section from apcore.yaml.
        **kwargs: Forwarded to ``build_standard_strategy()``.

    Returns:
        Configured ExecutionStrategy.
    """
    from apcore.builtin_steps import build_standard_strategy

    strategy = build_standard_strategy(
        registry=registry,
        config=config,
        acl=acl,
        approval_handler=approval_handler,
        middlewares=middlewares,
        middleware_manager=middleware_manager,
        executor=executor,
    )

    # (1) Remove steps — fail-fast (Issue #33 §1.2)
    for step_name in pipeline_config.get("remove", []):
        try:
            strategy.remove(step_name)
        except StepNotFoundError as exc:
            raise ConfigurationError(
                f"Cannot remove step '{step_name}': step not found in strategy"
            ) from exc

    # (2) Configure existing step fields — fail-fast (Issue #33 §1.2)
    configure_section = pipeline_config.get("configure", {}) or {}
    for step_name, overrides in configure_section.items():
        target = next((s for s in strategy.steps if s.name == step_name), None)
        if target is None:
            raise ConfigurationError(
                f"Cannot configure step '{step_name}': step not found in strategy"
            )
        for key, value in overrides.items():
            if not hasattr(target, key):
                raise ConfigurationError(
                    f"Cannot configure step '{step_name}': unknown field '{key}'"
                )
            setattr(target, key, value)

    # (3) Resolve and insert custom steps — fail-fast (Issue #33 §1.2)
    for step_def in pipeline_config.get("steps", []):
        after = step_def.get("after")
        before = step_def.get("before")
        if not after and not before:
            raise ConfigurationError(
                f"Step '{step_def.get('name', '<unnamed>')}' must declare an 'after' or 'before' anchor"
            )
        step = _resolve_step(step_def)
        try:
            if after:
                strategy.insert_after(after, step)
            else:
                strategy.insert_before(before, step)
        except StepNotFoundError as exc:
            anchor = after or before
            raise ConfigurationError(
                f"Cannot insert step '{step.name}': anchor step '{anchor}' not found"
            ) from exc

    return strategy
