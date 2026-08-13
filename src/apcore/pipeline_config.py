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

# ---------------------------------------------------------------------------
# `pipeline.configure` — a CLOSED field set
# ---------------------------------------------------------------------------

#: Exactly the four behavioural modifiers of DECLARATIVE_CONFIG_SPEC.md §4.2 and
#: ``schemas/apcore-config.schema.json`` ``$defs/ConfigurableStepFields``: the §4.3
#: step-entry fields that still mean something applied to a step that already exists.
#:
#: This gate used to be ``hasattr(step, key)`` — any attribute the concrete step
#: class happened to declare. That accepted ``description``, every attribute of any
#: custom step, and — the reason it had to change — ``requires`` / ``provides``.
#: The spec's own former example (``features/middleware-system.md``) moved
#: ``input_validation`` from ``requires=('module',)`` to ``requires=('context',)``,
#: deleting the dependency ``module_lookup`` satisfies; construction then validates
#: cleanly and the ``PipelineDependencyError`` MUST can never fire for that step.
#: An open-ended gate also made a working config non-portable: keys accepted here
#: are rejected by apcore-typescript and dropped by apcore-rust
#: (aiperceivable/apcore#89).
_CONFIGURABLE_STEP_FIELDS: tuple[str, ...] = (
    "match_modules",
    "ignore_errors",
    "pure",
    "timeout_ms",
)

_CAPABILITY_CONTRACT_HINT = (
    "a step's requires/provides capability contract is declared by the step implementation "
    "(the BaseStep constructor arguments of the step class), never by configuration; "
    "configuration able to rewrite it would disable the PipelineDependencyError check"
)
_STRUCTURAL_HINT = (
    "this field is structural and belongs to the 'pipeline.steps' entry that inserts the step, "
    "not to 'pipeline.configure', which only overrides fields of a step that already exists"
)

#: Step fields an operator plausibly reaches for under ``configure:`` that are not
#: configurable, each with where it actually lives. Rejecting a key is only half the
#: message an operator needs; the other half is what to do instead.
_NON_CONFIGURABLE_STEP_FIELDS: dict[str, str] = {
    "requires": _CAPABILITY_CONTRACT_HINT,
    "provides": _CAPABILITY_CONTRACT_HINT,
    "name": _STRUCTURAL_HINT,
    "type": _STRUCTURAL_HINT,
    "handler": _STRUCTURAL_HINT,
    "after": _STRUCTURAL_HINT,
    "before": _STRUCTURAL_HINT,
    "config": (
        "'config' is constructor arguments for the step factory and belongs to the "
        "'pipeline.steps' entry that constructs the step"
    ),
}


#: The ten fields ``$defs/PipelineStep`` declares for a ``pipeline.steps`` entry
#: (DECLARATIVE_CONFIG_SPEC.md §4.3). That definition has been
#: ``additionalProperties: false`` since it was written and nothing enforced it:
#: ``_resolve_step`` read the ten it knew and ignored the rest, so
#: ``{"name": "x", "type": "noop", "after": "execute", "tiemout_ms": 5000}`` built
#: successfully with ``timeout_ms == 0`` — the operator's timeout silently absent.
#: Same failure mode as the old ``configure:`` gate, one key over.
_STEP_ENTRY_FIELDS: tuple[str, ...] = (
    "name",
    "type",
    "handler",
    "config",
    "match_modules",
    "ignore_errors",
    "pure",
    "timeout_ms",
    "after",
    "before",
)


def _unknown_step_entry_field_message(step_name: str, key: str) -> str:
    """Build the ``PIPELINE_CONFIGURATION_ERROR`` message for a rejected steps-entry key."""
    return (
        f"Step '{step_name or '<unnamed>'}': '{key}' is not a step entry field. "
        f"'pipeline.steps' entries accept exactly: {', '.join(_STEP_ENTRY_FIELDS)}."
    )


def _unconfigurable_field_message(step_name: str, key: str) -> str:
    """Build the ``PIPELINE_CONFIGURATION_ERROR`` message for a rejected key."""
    hint = _NON_CONFIGURABLE_STEP_FIELDS.get(key)
    detail = f" ({hint})" if hint else ""
    return (
        f"Cannot configure step '{step_name}': '{key}' is not a configurable field{detail}. "
        f"'pipeline.configure' accepts exactly: {', '.join(_CONFIGURABLE_STEP_FIELDS)}."
    )


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
      2. ``configure`` — override the four configurable fields of existing steps
         (``match_modules``, ``ignore_errors``, ``pure``, ``timeout_ms``); any other
         key raises ``ConfigurationError`` / ``PIPELINE_CONFIGURATION_ERROR``
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
            raise ConfigurationError(f"Cannot remove step '{step_name}': step not found in strategy") from exc

    # (2) Configure existing step fields — fail-fast (Issue #33 §1.2)
    configure_section = pipeline_config.get("configure", {}) or {}
    for step_name, overrides in configure_section.items():
        target = next((s for s in strategy.steps if s.name == step_name), None)
        if target is None:
            raise ConfigurationError(f"Cannot configure step '{step_name}': step not found in strategy")
        for key, value in overrides.items():
            # The accepted set is the declared four, NOT whatever the concrete step
            # object happens to expose — see _CONFIGURABLE_STEP_FIELDS.
            if key not in _CONFIGURABLE_STEP_FIELDS:
                raise ConfigurationError(_unconfigurable_field_message(step_name, key))
            setattr(target, key, value)

    # (3) Resolve and insert custom steps — fail-fast (Issue #33 §1.2)
    for step_def in pipeline_config.get("steps", []):
        # `$defs/PipelineStep` is additionalProperties:false — enforce it before
        # anything is constructed, so a typo is a startup error rather than a
        # field that quietly never took effect.
        for key in step_def:
            if key not in _STEP_ENTRY_FIELDS:
                raise ConfigurationError(
                    _unknown_step_entry_field_message(step_def.get("name", ""), key)
                )
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
            raise ConfigurationError(f"Cannot insert step '{step.name}': anchor step '{anchor}' not found") from exc

    return strategy
