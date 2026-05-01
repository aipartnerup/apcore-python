"""Module protocol and related data types."""

from __future__ import annotations

from dataclasses import dataclass, field

import logging
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pydantic import BaseModel

    from apcore.context import Context

__all__ = [
    "DEFAULT_ANNOTATIONS",
    "Module",
    "ModuleAnnotations",
    "ModuleExample",
    "PreflightCheckResult",
    "PreflightResult",
    "ValidationResult",
]

_logger = logging.getLogger(__name__)


@runtime_checkable
class Module(Protocol):
    """Protocol for apcore modules.

    Any class with ``input_schema``, ``output_schema``, ``description``,
    and an ``execute(inputs, context)`` method satisfies this protocol.
    Inheriting from ``Module`` is optional but provides IDE autocompletion.

    At runtime, ``@runtime_checkable`` only checks attribute existence.
    Static type checkers (pyright) will verify the full signature.

    Optional lifecycle hooks (detected via hasattr by the Registry/Executor):
    - ``on_load()`` — called after module is registered; raise to abort.
    - ``on_unload()`` — called after module is unregistered.
    - ``on_suspend()`` — called before hot-reload; returns optional state dict.
    - ``on_resume(state)`` — called after hot-reload; receives suspended state.
    - ``preflight(inputs, context)`` — custom validation before execution;
      return a ``PreflightResult``-compatible object.
    - ``stream(inputs, context)`` — async generator variant (streaming mode).

    Cross-language parity with apcore-typescript Module interface and
    apcore-rust Module trait (sync finding A-017).
    """

    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    description: str

    def execute(self, inputs: dict[str, Any], context: Context) -> dict[str, Any]: ...


_CANONICAL_FIELDS = {
    "readonly",
    "destructive",
    "idempotent",
    "requires_approval",
    "open_world",
    "streaming",
    "cacheable",
    "cache_ttl",
    "cache_key_fields",
    "paginated",
    "pagination_style",
    "extra",
}


@dataclass(frozen=True)
class ModuleAnnotations:
    """Behavioral annotations for a module.

    Attributes:
        readonly: Whether the module only reads data (no side effects).
        destructive: Whether the module may irreversibly modify data.
        idempotent: Whether repeated calls produce the same result.
        requires_approval: Whether human approval is needed before execution.
        open_world: Whether the module interacts with external systems.
        streaming: Whether the module supports streaming execution.
        cacheable: Whether the module's results can be cached.
        cache_ttl: Cache time-to-live in seconds (0 means no expiry).
        cache_key_fields: Input fields used to compute the cache key (None = all).
        paginated: Whether the module supports paginated results.
        pagination_style: Pagination strategy (default "cursor"). Accepts any string.
        extra: Extension dictionary for ecosystem package metadata.
    """

    readonly: bool = False
    destructive: bool = False
    idempotent: bool = False
    requires_approval: bool = False
    open_world: bool = True
    streaming: bool = False
    cacheable: bool = False
    cache_ttl: int = 0
    cache_key_fields: tuple[str, ...] | None = None
    paginated: bool = False
    pagination_style: str = "cursor"
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Enforce immutability constraints on frozen dataclass."""
        # Convert list to tuple for cache_key_fields
        if isinstance(self.cache_key_fields, list):
            object.__setattr__(self, "cache_key_fields", tuple(self.cache_key_fields))
        # Shallow copy extra to detach from caller's mutable dict
        object.__setattr__(self, "extra", dict(self.extra))
        # Validate cache_ttl >= 0
        if self.cache_ttl < 0:
            _logger.warning("cache_ttl %d is negative, clamping to 0", self.cache_ttl)
            object.__setattr__(self, "cache_ttl", 0)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModuleAnnotations:
        """Deserialize from dict per PROTOCOL_SPEC §4.4.1 wire format.

        - The canonical form carries extension data under a nested ``extra`` object.
        - Legacy top-level overflow keys (unknown keys at the annotations root) are
          tolerated for backward compatibility and merged into ``extra``.
        - When the same key appears in BOTH the nested ``extra`` AND as a top-level
          overflow key, the nested value wins (§4.4.1 rule 7).
        """
        known = {k: v for k, v in data.items() if k in _CANONICAL_FIELDS}
        overflow = {k: v for k, v in data.items() if k not in _CANONICAL_FIELDS}
        explicit_extra = known.pop("extra", {})
        if not isinstance(explicit_extra, dict):
            explicit_extra = {}
        # §4.4.1 rule 7: nested explicit `extra` wins over legacy top-level overflow.
        extra: dict[str, Any] = {**overflow, **explicit_extra}
        return cls(**known, extra=extra)


DEFAULT_ANNOTATIONS = ModuleAnnotations()


@dataclass
class ModuleExample:
    """An example invocation of a module.

    Attributes:
        title: Short title for the example.
        inputs: Example input data dict.
        output: Expected output data dict.
        description: Optional description of the example.
    """

    title: str
    inputs: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    description: str | None = None


@dataclass
class ValidationResult:
    """Result of input validation.

    Attributes:
        valid: Whether validation passed.
        errors: List of error dicts, each with 'field', 'code', 'message' keys.
    """

    valid: bool
    errors: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class PreflightCheckResult:
    """Result of a single preflight check.

    Attributes:
        check: Check name ("module_id", "module_lookup", "call_chain", "acl",
            "approval", "schema", "module_preflight").
        passed: Whether this check passed.
        error: Error details when passed is False; None when passed is True.
        warnings: Non-fatal issues that do not prevent execution.
    """

    check: str
    passed: bool
    error: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class PreflightResult:
    """Result of Executor.validate() preflight check.

    Duck-type compatible with ValidationResult: has ``valid`` and ``errors``
    properties so existing consumers continue to work.

    Attributes:
        valid: True only if all checks passed.
        checks: Per-step check results.
        requires_approval: True if the module has requires_approval annotation.
    """

    valid: bool
    checks: list[PreflightCheckResult] = field(default_factory=list)
    requires_approval: bool = False

    @property
    def errors(self) -> list[dict[str, Any]]:
        """All failed check errors, compatible with ValidationResult.errors."""
        return [c.error for c in self.checks if not c.passed and c.error is not None]
