"""Configuration loading, validation, and environment variable overrides (Algorithm A12).

0.15.0 additions: Config Bus (§9.4–§9.15) — namespace registry, namespace mode,
mount(), namespace(), get_typed(), bind(), per-namespace env overrides, hot-reload.
"""

from __future__ import annotations

import copy
import dataclasses
import logging
import os

import sys
import threading
from pathlib import Path
from typing import Any, Type, TypeVar

import yaml

from apcore.errors import (
    ConfigBindError,
    ConfigEnvMapConflictError,
    ConfigEnvPrefixConflictError,
    ConfigError,
    ConfigMountError,
    ConfigNamespaceDuplicateError,
    ConfigNamespaceReservedError,
    ConfigNotFoundError,
)

__all__ = ["Config", "discover_config_file"]

_logger = logging.getLogger(__name__)

#: Environment variable prefix for overrides.
_ENV_PREFIX = "APCORE_"

#: Required configuration fields (dot-paths).
_REQUIRED_FIELDS: tuple[str, ...] = (
    "version",
    "extensions.root",
    "schema.root",
    "acl.root",
    "acl.default_effect",
    "project.name",
)

#: Field constraints: field → (validator_fn, error_message).
_CONSTRAINTS: dict[str, tuple[Any, str]] = {
    "acl.default_effect": (
        lambda v: v in ("allow", "deny"),
        "must be 'allow' or 'deny'",
    ),
    "observability.tracing.sampling_rate": (
        lambda v: isinstance(v, (int, float)) and 0.0 <= v <= 1.0,
        "must be a number in [0.0, 1.0]",
    ),
    "extensions.max_depth": (
        lambda v: isinstance(v, int) and 1 <= v <= 16,
        "must be an integer in [1, 16]",
    ),
    "executor.default_timeout": (
        lambda v: isinstance(v, int) and v >= 0,
        "must be a non-negative integer (milliseconds)",
    ),
    "executor.global_timeout": (
        lambda v: isinstance(v, int) and v >= 0,
        "must be a non-negative integer (milliseconds)",
    ),
    "executor.max_call_depth": (
        lambda v: isinstance(v, int) and v >= 1,
        "must be a positive integer",
    ),
    "executor.max_module_repeat": (
        lambda v: isinstance(v, int) and v >= 1,
        "must be a positive integer",
    ),
    "sys_modules.error_history.max_entries_per_module": (
        lambda v: isinstance(v, int) and v >= 1,
        "must be a positive integer",
    ),
    "sys_modules.error_history.max_total_entries": (
        lambda v: isinstance(v, int) and v >= 1,
        "must be a positive integer",
    ),
    "sys_modules.events.thresholds.error_rate": (
        lambda v: isinstance(v, (int, float)) and 0.0 <= v <= 1.0,
        "must be a number in [0.0, 1.0]",
    ),
    "sys_modules.events.thresholds.latency_p99_ms": (
        lambda v: isinstance(v, (int, float)) and v > 0,
        "must be a positive number",
    ),
    "middleware.circuit_breaker.failure_threshold": (
        lambda v: isinstance(v, int) and v >= 1,
        "must be a positive integer",
    ),
    "middleware.circuit_breaker.recovery_window_ms": (
        lambda v: isinstance(v, int) and v >= 0,
        "must be a non-negative integer (milliseconds)",
    ),
    "middleware.circuit_breaker.success_threshold": (
        lambda v: isinstance(v, int) and v >= 1,
        "must be a positive integer",
    ),
}

#: Default configuration values.
#:
#: NOTE: ``version`` is the frozen baseline for legacy-mode configs (those
#: that omit an explicit ``version`` field). It identifies the spec version
#: whose semantics legacy mode parses against, NOT the current SDK version.
#: Do not bump this with each spec MINOR — only when legacy-mode parsing
#: semantics actually change.
_DEFAULTS: dict[str, Any] = {
    "version": "0.16.0",
    "extensions": {
        "root": "./extensions",
        "auto_discover": True,
        "max_depth": 8,
        "follow_symlinks": False,
    },
    "schema": {
        "root": "./schemas",
        "strategy": "yaml_first",
        "max_ref_depth": 32,
    },
    "acl": {
        "root": "./acl",
        "default_effect": "deny",
    },
    "executor": {
        "default_timeout": 30000,
        "global_timeout": 60000,
        "max_call_depth": 32,
        "max_module_repeat": 3,
    },
    "observability": {
        "tracing": {
            "enabled": False,
            "sampling_rate": 1.0,
        },
        "metrics": {
            "enabled": False,
        },
    },
    "project": {
        "name": "apcore",
        "source_repo": None,
        "source_root": "",
    },
    "sys_modules": {
        "enabled": False,
        "error_history": {
            "max_entries_per_module": 50,
            "max_total_entries": 1000,
        },
        "events": {
            "enabled": False,
            "thresholds": {
                "error_rate": 0.1,
                "latency_p99_ms": 5000.0,
            },
            "subscribers": [],
        },
    },
    "middleware": {
        "circuit_breaker": {
            "failure_threshold": 5,
            "recovery_window_ms": 60000,
            "success_threshold": 1,
        },
    },
    "stream": {
        "max_merge_depth": 32,
    },
}

# =============================================================================
# Namespace registry (global, class-level, thread-safe)
# =============================================================================

_RESERVED_NAMESPACES: frozenset[str] = frozenset({"apcore", "_config"})


_DEFAULT_MAX_DEPTH: int = 5
_VALID_ENV_STYLES: frozenset[str] = frozenset({"nested", "flat", "auto"})


@dataclasses.dataclass
class _NamespaceRegistration:
    name: str
    schema: dict[str, Any] | str | None
    env_prefix: str  # auto-derived or explicit (never None after registration)
    defaults: dict[str, Any] | None
    env_style: str  # "auto" (default), "nested", or "flat"
    max_depth: int  # max nesting depth for env conversion (default 5)
    env_map: dict[str, str] | None  # bare env var → config key mapping


_GLOBAL_NS_REGISTRY: dict[str, _NamespaceRegistration] = {}
_GLOBAL_NS_REGISTRY_LOCK: threading.Lock = threading.Lock()
_GLOBAL_ENV_MAP: dict[str, str] = {}  # bare env var → top-level config key
_GLOBAL_ENV_MAP_CLAIMED: dict[str, str] = {}  # env var → owner (for conflict detection)

T = TypeVar("T")


# =============================================================================
# Private helpers
# =============================================================================


def _deep_merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *override* into a copy of *base*."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge_dicts(result[key], value)
        else:
            result[key] = value
    return result


def _get_nested(data: dict[str, Any], dot_path: str, default: Any = None) -> Any:
    """Retrieve a value from a nested dict using a dot-separated path."""
    parts = dot_path.split(".")
    current: Any = data
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


def _set_nested(data: dict[str, Any], dot_path: str, value: Any) -> None:
    """Set a value in a nested dict, creating intermediate dicts as needed."""
    parts = dot_path.split(".")
    current = data
    for part in parts[:-1]:
        if part not in current or not isinstance(current[part], dict):
            current[part] = {}
        current = current[part]
    current[parts[-1]] = value


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    """Apply APCORE_* environment variable overrides and global mappings."""
    result = copy.deepcopy(data)  # deep copy to protect shared defaults
    for env_key, env_value in os.environ.items():
        coerced = _coerce_env_value(env_value)

        # 1. Global env_map (bare env var → top-level key).
        if env_key in _GLOBAL_ENV_MAP:
            _set_nested(result, _GLOBAL_ENV_MAP[env_key], coerced)
            continue

        # 2. Standard APCORE_ prefix.
        if not env_key.startswith(_ENV_PREFIX):
            continue
        suffix = env_key[len(_ENV_PREFIX) :]
        if not suffix:
            continue
        # Convert: single _ → . (separator), double __ → literal _
        dot_path = suffix.lower().replace("__", "\x00").replace("_", ".").replace("\x00", "_")
        _set_nested(result, dot_path, coerced)
    return result


def _coerce_env_value(value: str) -> Any:
    """Coerce string env value to appropriate Python type."""
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _env_suffix_to_dot_path_with_depth(suffix: str, max_depth: int) -> str:
    """Convert env var suffix to dot-path, stopping at *max_depth* segments.

    After producing ``max_depth - 1`` dots (i.e. *max_depth* segments),
    remaining ``_`` characters are preserved as literal underscores.
    Double ``__`` always means literal ``_`` regardless of depth.
    """
    lower = suffix.lower()
    result: list[str] = []
    dot_count = 0
    i = 0
    while i < len(lower):
        ch = lower[i]
        if ch == "_":
            if i + 1 < len(lower) and lower[i + 1] == "_":
                result.append("_")  # double __ → literal _
                i += 2
            elif dot_count < max_depth - 1:
                result.append(".")
                dot_count += 1
                i += 1
            else:
                result.append("_")  # depth limit reached
                i += 1
        else:
            result.append(ch)
            i += 1
    return "".join(result).strip(".")


def _auto_resolve_suffix(
    suffix: str,
    defaults: dict[str, Any] | None,
    max_depth: int,
) -> str:
    """Resolve env var suffix using the *defaults* tree structure.

    Tries the full suffix as a flat key first, then recursively splits at
    underscore positions to match nested dict keys.  Falls back to
    ``_env_suffix_to_dot_path_with_depth`` when no match is found.
    """
    lower = suffix.lower()
    if defaults is None:
        return _env_suffix_to_dot_path_with_depth(lower, max_depth)

    result = _match_suffix_to_tree(lower, defaults, 0, max_depth)
    if result is not None:
        return result
    # Fallback: nested conversion with depth limit.
    return _env_suffix_to_dot_path_with_depth(lower, max_depth)


def _match_suffix_to_tree(
    suffix: str,
    tree: dict[str, Any],
    depth: int,
    max_depth: int,
) -> str | None:
    """Try to match *suffix* against keys in *tree* (recursive)."""
    # 1. Try full suffix as a flat key.
    if suffix in tree:
        return suffix

    # 2. Depth limit reached — cannot split further.
    if depth >= max_depth - 1:
        return None

    # 3. Try splitting at each underscore position (left to right).
    for i, ch in enumerate(suffix):
        if ch != "_" or i == 0 or i == len(suffix) - 1:
            continue
        prefix_part = suffix[:i]
        remainder = suffix[i + 1 :]
        subtree = tree.get(prefix_part)
        if isinstance(subtree, dict):
            sub = _match_suffix_to_tree(remainder, subtree, depth + 1, max_depth)
            if sub is not None:
                return prefix_part + "." + sub

    return None


def _resolve_env_suffix(
    suffix: str,
    registration: _NamespaceRegistration,
) -> tuple[str, bool]:
    """Resolve an env var suffix to a config key path.

    Returns ``(key, is_nested)`` where *is_nested* indicates whether the key
    contains dots (i.e. should be stored via ``_set_nested``).
    """
    if registration.env_style == "flat":
        key = suffix.lower()
        return key, False
    if registration.env_style == "auto":
        key = _auto_resolve_suffix(suffix, registration.defaults, registration.max_depth)
        return key, "." in key
    # "nested" (default)
    key = _env_suffix_to_dot_path_with_depth(suffix, registration.max_depth)
    return key, "." in key


def _apply_namespace_env_overrides(
    data: dict[str, Any],
    registrations: list[_NamespaceRegistration],
) -> dict[str, Any]:
    """Apply per-namespace env overrides using longest-prefix-match dispatch (§9.8.3).

    Handles three sources in order:
      1. Global env_map (bare env var → top-level key)
      2. Namespace env_map (bare env var → namespace key)
      3. Prefix-based dispatch (MYAPP_FOO → myapp.foo)
    """
    result = copy.deepcopy(data)

    # Build namespace env_map lookup.
    ns_env_maps: dict[str, tuple[str, str]] = {}  # env_var → (ns_name, config_key)
    for reg in registrations:
        if reg.env_map:
            for env_var, config_key in reg.env_map.items():
                ns_env_maps[env_var] = (reg.name, config_key)

    # Prefix table: sorted by length descending for longest-prefix-match.
    prefixed = sorted(
        [r for r in registrations if r.env_prefix],
        key=lambda r: len(r.env_prefix),
        reverse=True,
    )

    for env_key, env_value in os.environ.items():
        coerced = _coerce_env_value(env_value)

        # 1. Global env_map (bare env var → top-level dot-path).
        # The mapped target may be a dot-path (e.g. "server.port"); set it
        # nested at the top level so `get()` can traverse it.
        if env_key in _GLOBAL_ENV_MAP:
            _set_nested(result, _GLOBAL_ENV_MAP[env_key], coerced)
            continue

        # 2. Namespace env_map (bare env var → namespace key).
        # The mapped target may also be a dot-path within the namespace.
        if env_key in ns_env_maps:
            ns_name, config_key = ns_env_maps[env_key]
            ns_data = result.setdefault(ns_name, {})
            if not isinstance(ns_data, dict):
                ns_data = {}
                result[ns_name] = ns_data
            _set_nested(ns_data, config_key, coerced)
            continue

        # 3. Prefix-based dispatch.
        if not prefixed:
            continue
        matched = _find_matching_ns_registration(env_key, prefixed)
        if matched is None:
            continue
        prefix = matched.env_prefix
        suffix = env_key[len(prefix) :]
        if not suffix:
            continue
        if suffix.startswith("_"):
            suffix = suffix[1:]
        if not suffix:
            continue
        key, is_nested = _resolve_env_suffix(suffix, matched)
        ns_data = result.setdefault(matched.name, {})
        if is_nested:
            _set_nested(ns_data, key, coerced)
        else:
            ns_data[key] = coerced

    return result


def _find_matching_ns_registration(
    env_key: str,
    sorted_registrations: list[_NamespaceRegistration],
) -> _NamespaceRegistration | None:
    """Return the first (longest) registration whose env_prefix matches env_key."""
    for reg in sorted_registrations:
        prefix = reg.env_prefix or ""
        if env_key.startswith(prefix):
            return reg
    return None


def _apply_namespace_defaults(
    data: dict[str, Any],
    registrations: list[_NamespaceRegistration],
) -> dict[str, Any]:
    """Merge namespace defaults under their namespace keys (defaults lose to file data)."""
    result = copy.deepcopy(data)
    for reg in registrations:
        if reg.defaults:
            existing = result.get(reg.name, {})
            if not isinstance(existing, dict):
                existing = {}
            result[reg.name] = _deep_merge_dicts(reg.defaults, existing)
    return result


def _validate_namespace_schema(
    namespace: str,
    ns_data: dict[str, Any],
    schema: dict[str, Any] | str,
) -> None:
    """Validate ns_data against schema."""
    import jsonschema  # type: ignore[import-untyped]

    if isinstance(schema, str):
        schema_path = Path(schema)
        if not schema_path.is_file():
            _logger.warning("Schema file for namespace %r not found: %s", namespace, schema)
            return
        with open(schema_path, encoding="utf-8") as f:
            import json

            loaded_schema = json.load(f)
    else:
        loaded_schema = schema

    try:
        jsonschema.validate(ns_data, loaded_schema)
    except jsonschema.ValidationError as exc:
        raise ConfigError(
            message=f"Namespace {namespace!r} failed schema validation: {exc.message}",
            details={"namespace": namespace, "path": list(exc.absolute_path)},
        ) from exc


def discover_config_file() -> str | None:
    """Search for a config file in the standard discovery order (§9.14)."""
    env_path = os.environ.get("APCORE_CONFIG_FILE")
    if env_path:
        return env_path

    cwd_candidates = [
        Path("project.yaml"),
        Path("project.yml"),
        Path("apcore.yaml"),
        Path("apcore.yml"),
    ]
    for candidate in cwd_candidates:
        if candidate.is_file():
            return str(candidate)

    if sys.platform == "darwin":
        xdg_config = Path.home() / "Library" / "Application Support" / "apcore" / "config.yaml"
    else:
        xdg_config = Path.home() / ".config" / "apcore" / "config.yaml"

    if xdg_config.is_file():
        return str(xdg_config)

    legacy = Path.home() / ".apcore" / "config.yaml"
    if legacy.is_file():
        return str(legacy)

    return None


class Config:
    """Configuration system with YAML loading, env overrides, and validation.

    Merge priority (highest wins): environment variables > config file > defaults.

    Backward compatible: ``Config(data={...})`` still works for in-memory
    configuration without file loading or validation.

    0.15.0: Namespace mode is detected when the top-level key ``"apcore"`` is
    present. In namespace mode, ``get()`` resolves the first path segment as a
    namespace name and looks up nested keys within it.
    """

    def __init__(
        self,
        data: dict[str, Any] | None = None,
        env_style: str = "auto",
    ) -> None:
        """Initialize configuration system.

        Args:
            data: Optional in-memory configuration data.
            env_style: Default env var conversion strategy ('auto', 'nested', 'flat').
        """
        self._data: dict[str, Any] = data or {}
        self._yaml_path: str | None = None
        self._lock = threading.Lock()
        self._mode: str = "legacy"
        self._mounts: dict[str, dict[str, Any]] = {}
        self._env_style: str = env_style

    # ------------------------------------------------------------------
    # Default value resolution (single source of truth)
    # ------------------------------------------------------------------

    @classmethod
    def get_default(cls, key: str, fallback: Any = None) -> Any:
        """Resolve a default value from _DEFAULTS by dot-path.

        This is the single source of truth for all default values.
        Components MUST use this instead of hardcoding defaults.
        """
        parts = key.split(".")
        node: Any = _DEFAULTS
        for part in parts:
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return fallback
        return node

    # ------------------------------------------------------------------
    # Namespace registry (class-level)
    # ------------------------------------------------------------------

    @classmethod
    def register_namespace(
        cls,
        name: str,
        schema: dict[str, Any] | str | None = None,
        env_prefix: str | None = None,
        defaults: dict[str, Any] | None = None,
        env_style: str | None = None,
        max_depth: int | None = None,
        env_map: dict[str, str] | None = None,
    ) -> None:
        """Register a namespace globally.

        Args:
            name: Namespace name (must not be reserved or already registered).
            schema: Optional JSON Schema dict or path to a JSON Schema file.
            env_prefix: Env var prefix. When ``None``, auto-derived from
                ``name`` via ``name.upper().replace("-", "_")``.  When an
                explicit string, used as-is.
            defaults: Optional default values for this namespace.
            env_style: Env var key conversion strategy (default ``"auto"``).
            max_depth: Max nesting depth for env key conversion (default 5).
            env_map: Explicit mapping of bare env var names to config keys
                within this namespace (e.g. ``{"REDIS_URL": "cache_url"}``).

        Raises:
            ConfigNamespaceReservedError: If ``name`` is a reserved namespace.
            ConfigNamespaceDuplicateError: If ``name`` is already registered.
            ConfigEnvPrefixConflictError: If ``env_prefix`` conflicts.
            ConfigEnvMapConflictError: If an ``env_map`` key is already claimed.
        """
        resolved_style = env_style or "auto"
        if resolved_style not in _VALID_ENV_STYLES:
            msg = f"env_style must be one of {sorted(_VALID_ENV_STYLES)}, got {resolved_style!r}"
            raise ValueError(msg)
        resolved_depth = max_depth if max_depth is not None else _DEFAULT_MAX_DEPTH
        resolved_prefix = env_prefix if env_prefix is not None else name.upper().replace("-", "_")

        if name in _RESERVED_NAMESPACES:
            raise ConfigNamespaceReservedError(name=name)

        with _GLOBAL_NS_REGISTRY_LOCK:
            if name in _GLOBAL_NS_REGISTRY:
                raise ConfigNamespaceDuplicateError(name=name)

            cls._validate_env_prefix(resolved_prefix)

            if env_map:
                cls._validate_env_map(env_map, owner=name)

            _GLOBAL_NS_REGISTRY[name] = _NamespaceRegistration(
                name=name,
                schema=schema,
                env_prefix=resolved_prefix,
                defaults=defaults,
                env_style=resolved_style,
                max_depth=resolved_depth,
                env_map=env_map,
            )

    @classmethod
    def env_map(cls, mapping: dict[str, str]) -> None:
        """Register global bare env var → top-level config key mappings.

        Args:
            mapping: Dict of env var names to config keys
                (e.g. ``{"PORT": "port", "DATABASE_URL": "db_url"}``).

        Raises:
            ConfigEnvMapConflictError: If an env var is already claimed.
        """
        with _GLOBAL_NS_REGISTRY_LOCK:
            for env_var in mapping:
                if env_var in _GLOBAL_ENV_MAP_CLAIMED:
                    owner = _GLOBAL_ENV_MAP_CLAIMED[env_var]
                    raise ConfigEnvMapConflictError(env_var=env_var, owner=owner)
            # All clean — register.
            for env_var, config_key in mapping.items():
                _GLOBAL_ENV_MAP[env_var] = config_key
                _GLOBAL_ENV_MAP_CLAIMED[env_var] = "__global__"

    @classmethod
    def _validate_env_prefix(cls, env_prefix: str) -> None:
        """Raise ConfigEnvPrefixConflictError if env_prefix is already in use."""
        for reg in _GLOBAL_NS_REGISTRY.values():
            if reg.env_prefix == env_prefix:
                raise ConfigEnvPrefixConflictError(env_prefix=env_prefix)

    @classmethod
    def _validate_env_map(cls, env_map: dict[str, str], owner: str) -> None:
        """Raise ConfigEnvMapConflictError if any env var is already claimed."""
        for env_var in env_map:
            if env_var in _GLOBAL_ENV_MAP_CLAIMED:
                existing_owner = _GLOBAL_ENV_MAP_CLAIMED[env_var]
                raise ConfigEnvMapConflictError(env_var=env_var, owner=existing_owner)
        # All clean — claim them.
        for env_var in env_map:
            _GLOBAL_ENV_MAP_CLAIMED[env_var] = owner

    @classmethod
    def registered_namespaces(cls) -> list[dict[str, Any]]:
        """Return a list of dicts describing all registered namespaces."""
        with _GLOBAL_NS_REGISTRY_LOCK:
            return [
                {
                    "name": reg.name,
                    "env_prefix": reg.env_prefix,
                    "has_schema": reg.schema is not None,
                }
                for reg in _GLOBAL_NS_REGISTRY.values()
            ]

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, yaml_path: str | None = None, *, validate: bool = True) -> Config:
        """Load configuration from a YAML file with env overrides.

        Detects namespace mode when the top-level key ``"apcore"`` is present.

        Args:
            yaml_path: Path to the YAML configuration file.
            validate: If True, run full validation after loading.

        Returns:
            A new Config instance with merged data (defaults < file < env).

        Raises:
            ConfigNotFoundError: If the file does not exist.
            ConfigError: If the YAML is invalid or validation fails.
        """
        if yaml_path is None:
            yaml_path = discover_config_file()
            if yaml_path is None:
                return cls.from_defaults()

        path = Path(yaml_path)
        if not path.is_file():
            raise ConfigNotFoundError(config_path=str(path))

        with open(path, encoding="utf-8") as f:
            try:
                file_data = yaml.safe_load(f)
            except yaml.YAMLError as e:
                raise ConfigError(message=f"Invalid YAML in {yaml_path}: {e}") from e

        if file_data is None:
            file_data = {}
        if not isinstance(file_data, dict):
            raise ConfigError(message=f"Config file must be a mapping, got {type(file_data).__name__}")

        # Namespace mode requires "apcore" key to be a dict, not null/scalar/list.
        apcore_val = file_data.get("apcore")
        if isinstance(apcore_val, dict):
            config = cls._load_namespace_mode(file_data, validate=validate)
        else:
            config = cls._load_legacy_mode(file_data, validate=validate)

        config._yaml_path = str(path)
        return config

    @classmethod
    def _load_legacy_mode(cls, file_data: dict[str, Any], *, validate: bool) -> Config:
        """Load in legacy (flat) mode: defaults < file < env."""
        merged = _deep_merge_dicts(_DEFAULTS, file_data)
        merged = _apply_env_overrides(merged)
        config = cls(data=merged)
        config._mode = "legacy"
        if validate:
            config.validate()
        return config

    @classmethod
    def _load_namespace_mode(cls, file_data: dict[str, Any], *, validate: bool) -> Config:
        """Load in namespace mode: namespace defaults < file < env overrides."""
        with _GLOBAL_NS_REGISTRY_LOCK:
            registrations = list(_GLOBAL_NS_REGISTRY.values())

        # Apply namespace defaults (lower priority than file data)
        merged = _apply_namespace_defaults(file_data, registrations)

        # Apply legacy APCORE_ overrides to the "apcore" namespace only
        apcore_ns = merged.get("apcore", {})
        apcore_ns = _apply_env_overrides(apcore_ns)
        merged["apcore"] = apcore_ns

        # Apply per-namespace env overrides for registered namespaces
        merged = _apply_namespace_env_overrides(merged, registrations)

        config = cls(data=merged)
        config._mode = "namespace"
        if validate:
            config._validate_namespace_mode()
        return config

    @classmethod
    def from_defaults(cls) -> Config:
        """Create a Config from default values with env overrides applied."""
        data = _apply_env_overrides(dict(_DEFAULTS))
        return cls(data=data)

    # ------------------------------------------------------------------
    # Access
    # ------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value by dot-path key.

        In namespace mode, the first path segment is the namespace name.
        Namespace names may contain hyphens; the longest matching registered
        namespace prefix is resolved first.
        """
        with self._lock:
            if self._mode == "namespace":
                return self._get_namespace_mode(key, default)
            return _get_nested(self._data, key, default)

    def _get_namespace_mode(self, key: str, default: Any) -> Any:
        """Resolve a dot-path key in namespace mode."""
        namespace, remainder = self._split_namespace_key(key)
        if namespace is None:
            # §9.9.1: Fallback to the implicit "apcore" namespace.
            return self._get_namespace_mode(f"apcore.{key}", default)

        ns_data = self._data.get(namespace)
        if ns_data is None:
            # §9.9.1: Fallback to implicit "apcore" namespace when the resolved
            # top-level segment does not exist in data. Avoid infinite recursion
            # once we are already probing under "apcore".
            if namespace != "apcore":
                return self._get_namespace_mode(f"apcore.{key}", default)
            return default
        if remainder is None:
            if isinstance(ns_data, dict):
                return copy.deepcopy(ns_data)
            # Bare top-level value (e.g. set via global env_map with a single-
            # segment target like ``port``). Return the value directly.
            return ns_data
        if not isinstance(ns_data, dict):
            # Remainder requested but the resolved top-level value is not a
            # dict — fall back to implicit apcore namespace.
            if namespace != "apcore":
                return self._get_namespace_mode(f"apcore.{key}", default)
            return default
        return _get_nested(ns_data, remainder, default)

    def _split_namespace_key(self, key: str) -> tuple[str | None, str | None]:
        """Split key into (namespace, remainder) using longest-prefix matching.

        Resolution order (matches TypeScript ``resolveNamespacePath``):
          1. Longest registered namespace prefix.
          2. Built-in ``apcore`` namespace.
          3. Naive first-segment split — allows top-level keys injected via
             global ``env_map`` (e.g. ``server.port``) or unmatched ``APCORE_*``
             fallback paths (e.g. ``executor.timeout``) to resolve against the
             raw data root. Matches cross-language behavior per §9.8 / §9.9.1.

        Returns (namespace, None) if the key is exactly the namespace.
        Returns (namespace, remainder) otherwise.
        Returns (None, None) only for a completely empty key.
        """
        with _GLOBAL_NS_REGISTRY_LOCK:
            known_names = sorted(_GLOBAL_NS_REGISTRY.keys(), key=len, reverse=True)

        for name in known_names:
            if key == name:
                return name, None
            if key.startswith(name + "."):
                return name, key[len(name) + 1 :]

        # Also handle the built-in "apcore" namespace in namespace mode
        if key == "apcore":
            return "apcore", None
        if key.startswith("apcore."):
            return "apcore", key[len("apcore") + 1 :]

        # Naive first-segment fallback for unregistered top-level keys.
        if not key:
            return None, None
        dot_index = key.find(".")
        if dot_index == -1:
            return key, None
        return key[:dot_index], key[dot_index + 1 :]

    def set(self, key: str, value: Any) -> None:
        """Set a configuration value by dot-path key."""
        with self._lock:
            _set_nested(self._data, key, value)

    @property
    def data(self) -> dict[str, Any]:
        """Return a deep copy of the raw config data."""
        with self._lock:
            return copy.deepcopy(self._data)

    # ------------------------------------------------------------------
    # Namespace-mode instance methods
    # ------------------------------------------------------------------

    def mount(
        self,
        namespace: str,
        *,
        from_file: str | None = None,
        from_dict: dict[str, Any] | None = None,
    ) -> None:
        """Attach external configuration to a namespace.

        Exactly one of ``from_file`` or ``from_dict`` must be provided.

        Args:
            namespace: The namespace to mount into (must not be ``"_config"``).
            from_file: Path to a YAML file to load.
            from_dict: A dict to merge into the namespace.

        Raises:
            ConfigMountError: If both or neither source is provided, if the
                namespace is ``"_config"``, or if ``from_file`` does not exist.
        """
        if namespace == "_config":
            raise ConfigMountError(message="Cannot mount into the reserved namespace '_config'")
        if from_file is None and from_dict is None:
            raise ConfigMountError(message="Exactly one of 'from_file' or 'from_dict' must be provided")
        if from_file is not None and from_dict is not None:
            raise ConfigMountError(message="Exactly one of 'from_file' or 'from_dict' must be provided")

        if from_file is not None:
            path = Path(from_file)
            if not path.is_file():
                raise ConfigMountError(message=f"Mount file not found: {from_file}")
            with open(path, encoding="utf-8") as f:
                try:
                    loaded = yaml.safe_load(f)
                except yaml.YAMLError as exc:
                    raise ConfigMountError(message=f"Invalid YAML in mount file {from_file}: {exc}") from exc
            if loaded is None:
                loaded = {}
            if not isinstance(loaded, dict):
                raise ConfigMountError(message=f"Mount file must be a mapping: {from_file}")
            mount_data: dict[str, Any] = loaded
        else:
            mount_data = dict(from_dict or {})

        with self._lock:
            existing = self._mounts.get(namespace, {})
            self._mounts[namespace] = _deep_merge_dicts(existing, mount_data)
            ns_existing = self._data.get(namespace, {})
            if not isinstance(ns_existing, dict):
                ns_existing = {}
            self._data[namespace] = _deep_merge_dicts(ns_existing, mount_data)

    def namespace(self, name: str) -> dict[str, Any]:
        """Return a deep copy of the namespace subtree dict.

        Args:
            name: Namespace name.

        Returns:
            Deep copy of the namespace data dict (empty dict if not present).
        """
        with self._lock:
            ns_data = self._data.get(name, {})
            if not isinstance(ns_data, dict):
                return {}
            return copy.deepcopy(ns_data)

    def get_typed(self, path: str, type_: Type[T]) -> T:
        """Get a configuration value coerced to ``type_``.

        Args:
            path: Dot-path to the value.
            type_: Target Python type to coerce to.

        Returns:
            The value coerced to ``type_``.

        Raises:
            ConfigBindError: If the value is missing or cannot be coerced.
        """
        value = self.get(path)
        if value is None:
            raise ConfigBindError(message=f"No value at path {path!r}")
        try:
            return type_(value)  # type: ignore[call-arg]
        except (TypeError, ValueError) as exc:
            raise ConfigBindError(message=f"Cannot coerce value at {path!r} to {type_.__name__}: {exc}") from exc

    def bind(self, namespace: str, model_class: type) -> Any:
        """Deserialize the namespace into a Pydantic model or dataclass.

        Args:
            namespace: The namespace whose data to bind.
            model_class: A Pydantic model class or Python dataclass.

        Returns:
            An instance of ``model_class`` populated with namespace data.

        Raises:
            ConfigBindError: If deserialization fails.
        """
        ns_data = self.namespace(namespace)
        return _instantiate_model(model_class, ns_data, namespace)

    # ------------------------------------------------------------------
    # Validation (Algorithm A12)
    # ------------------------------------------------------------------

    def validate(self) -> None:
        """Validate the configuration per Algorithm A12.

        Checks required fields, type constraints, and semantic rules.
        Collects all errors before raising.

        Raises:
            ConfigError: With all validation errors in the message.
        """
        errors: list[str] = []

        # 1. Required field check
        for field in _REQUIRED_FIELDS:
            value = _get_nested(self._data, field)
            if value is None:
                errors.append(f"Missing required field: '{field}'")

        # 2. Constraint validation
        for field, (check_fn, err_msg) in _CONSTRAINTS.items():
            value = _get_nested(self._data, field)
            if value is not None and not check_fn(value):
                errors.append(f"Invalid value for '{field}': {err_msg} (got {value!r})")

        # 3. Semantic validation
        ext_root = _get_nested(self._data, "extensions.root")
        auto_discover = _get_nested(self._data, "extensions.auto_discover", False)
        if auto_discover and ext_root and not Path(str(ext_root)).exists():
            _logger.warning(
                "extensions.auto_discover=true but extensions.root '%s' does not exist",
                ext_root,
            )

        schema_strategy = _get_nested(self._data, "schema.strategy")
        schema_root = _get_nested(self._data, "schema.root")
        if schema_strategy == "yaml_only" and schema_root and not Path(str(schema_root)).exists():
            errors.append(f"schema.strategy='yaml_only' but schema.root '{schema_root}' does not exist")

        if errors:
            raise ConfigError(
                message=f"Configuration validation failed ({len(errors)} error(s)):\n"
                + "\n".join(f"  - {e}" for e in errors),
                details={"errors": errors},
            )

    def _validate_namespace_mode(self) -> None:
        """Run A12-NS validation for namespace mode (§9.14).

        1. Run original A12 on ``data["apcore"]``.
        2. For each other top-level key: if registered and has a schema, validate.
        3. In strict mode (``_config.strict=True``): raise on unknown namespaces.
        """
        apcore_data = self._data.get("apcore", {})
        if not isinstance(apcore_data, dict):
            apcore_data = {}

        # C-4: In namespace mode the "apcore:" key is metadata (e.g. version),
        # not a standalone config.  Run only CONSTRAINTS — not REQUIRED_FIELDS —
        # so that a minimal namespace-mode YAML (empty apcore: block) is accepted,
        # consistent with the TypeScript implementation.
        errors: list[str] = []
        for field, (check_fn, err_msg) in _CONSTRAINTS.items():
            value = _get_nested(apcore_data, field)
            if value is not None and not check_fn(value):
                errors.append(f"Invalid value for 'apcore.{field}': {err_msg} (got {value!r})")
        if errors:
            raise ConfigError(
                message=f"Configuration validation failed ({len(errors)} error(s)):\n"
                + "\n".join(f"  - {e}" for e in errors),
                details={"errors": errors},
            )

        with _GLOBAL_NS_REGISTRY_LOCK:
            registry_snapshot = dict(_GLOBAL_NS_REGISTRY)

        strict = False
        config_meta = self._data.get("_config", {})
        if isinstance(config_meta, dict):
            strict = bool(config_meta.get("strict", False))

        for key, value in self._data.items():
            if key in ("apcore", "_config"):
                continue
            if not isinstance(value, dict):
                continue
            reg = registry_snapshot.get(key)
            if reg is None:
                if strict:
                    raise ConfigError(
                        message=f"Unknown namespace {key!r} in strict mode",
                        details={"namespace": key},
                    )
                continue
            if reg.schema is not None:
                _validate_namespace_schema(key, value, reg.schema)

    # ------------------------------------------------------------------
    # Hot-reload
    # ------------------------------------------------------------------

    def reload(self) -> None:
        """Re-read configuration from the original YAML file.

        Re-applies namespace defaults, env overrides, and stored mounts.

        Only works if the Config was created via ``Config.load()``.

        Raises:
            ConfigError: If no YAML path was stored or reload fails.
        """
        with self._lock:
            yaml_path = self._yaml_path
            if yaml_path is None:
                raise ConfigError(message="Cannot reload: Config was not loaded from a YAML file")
            stored_mounts = copy.deepcopy(self._mounts)

        reloaded = Config.load(yaml_path)

        with self._lock:
            self._data = reloaded._data
            self._mode = reloaded._mode
            # Re-apply mounts
            for namespace, mount_data in stored_mounts.items():
                ns_existing = self._data.get(namespace, {})
                if not isinstance(ns_existing, dict):
                    ns_existing = {}
                self._data[namespace] = _deep_merge_dicts(ns_existing, mount_data)
            self._mounts = stored_mounts

    # ------------------------------------------------------------------
    # Dunder
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        if self._yaml_path:
            return f"Config(yaml_path={self._yaml_path!r})"
        return f"Config(data=<{len(self._data)} keys>)"


# =============================================================================
# Private: model instantiation helper
# =============================================================================


def _instantiate_model(model_class: type, data: dict[str, Any], namespace: str) -> Any:
    """Bind a config namespace dict to a Pydantic v2 model, dataclass, or plain class.

    Pydantic v2 models are instantiated via ``model_validate`` (which runs
    field validation); dataclasses and any other class accepting a keyword
    init are instantiated via ``model_class(**data)``. Pydantic v1's
    ``parse_obj`` form is no longer attempted because ``pyproject.toml``
    requires ``pydantic>=2.0``.
    """
    try:
        if hasattr(model_class, "model_validate"):
            return model_class.model_validate(data)
        return model_class(**data)
    except Exception as exc:
        raise ConfigBindError(
            message=f"Failed to bind namespace {namespace!r} to {model_class.__name__}: {exc}"
        ) from exc


# =============================================================================
# Bootstrap: register apcore built-in namespaces (§9.15)
# =============================================================================

Config.register_namespace(
    "observability",
    env_prefix="APCORE_OBSERVABILITY",
    defaults={
        "tracing": {
            "enabled": False,
            "strategy": "full",
            "sampling_rate": 1.0,
            "exporter": "stdout",
            "otlp_endpoint": None,
        },
        "metrics": {"enabled": False, "exporter": "stdout"},
        "logging": {
            "enabled": True,
            "level": "info",
            "format": "json",
            "redact_sensitive": True,
        },
        "error_history": {
            "max_entries_per_module": 50,
            "max_total_entries": 1000,
        },
        "platform_notify": {
            "enabled": False,
            "error_rate_threshold": 0.1,
            "latency_p99_threshold_ms": 5000.0,
        },
    },
)

Config.register_namespace(
    "sys_modules",
    env_prefix="APCORE_SYS",
    defaults={
        "enabled": True,
        "health": {"enabled": True},
        "manifest": {"enabled": True},
        "usage": {
            "enabled": True,
            "retention_hours": 168,
            "bucketing_strategy": "hourly",
        },
        "control": {"enabled": True},
        "events": {
            "enabled": False,
            "thresholds": {"error_rate": 0.1, "latency_p99_ms": 5000.0},
        },
    },
)
