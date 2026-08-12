"""Structured logging: ContextLogger, RedactionConfig, and ObsLoggingMiddleware."""

from __future__ import annotations

import fnmatch
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from apcore.utils.redaction import PROTECTED_LOG_FIELDS as _PROTECTED_LOG_FIELDS

from apcore.context_keys import LOGGING_STARTS
from apcore.middleware.base import Middleware


_LEVELS = {
    "trace": 0,
    "debug": 10,
    "info": 20,
    "warn": 30,
    "error": 40,
    "fatal": 50,
}

_REDACTED = "***REDACTED***"

# Maximum recursion depth for nested redaction.  Matches the spec's schema
# validation depth limit (32) so the redactor can never run away on
# adversarial / cyclic-looking input.  Deeper structures stop being
# inspected — secrets buried below depth 32 are out of scope.
_MAX_REDACTION_DEPTH = 32

# Correlation-ID fields that must NEVER be redacted, by any rule.  Removing or
# scrambling these would break log/trace correlation.  Mirrors the TS
# PROTECTED_LOG_FIELDS and Rust NEVER_REDACT_FIELDS sets.
#
# Re-exported from :mod:`apcore.utils.redaction`, which owns the canonical set:
# the exemption has to hold on BOTH mandated surfaces ("Redaction MUST apply
# both at log emission and at the executor's input/output capture point"), and
# two independent literals is how it came to hold on neither of the recursive
# ones.  Kept importable from this module — it is the documented public name.
PROTECTED_LOG_FIELDS = _PROTECTED_LOG_FIELDS


@dataclass
class RedactionConfig:
    """Runtime-configurable redaction rules for observability logging.

    Applied in addition to schema-level ``x-sensitive`` annotations.
    The union of all matching fields and values is redacted.

    Fields:
        field_patterns: Glob patterns matched against field names (e.g. ``"*password*"``).
        value_patterns: Regex patterns matched against string field values (e.g. ``r"^Bearer .*"``).
        sensitive_keys: Case-insensitive substring patterns matched against field
            **names** (Issue #43 §5).  A field is redacted whenever its name
            contains one of these substrings, regardless of nesting.  Glob
            wildcards (``*``) are also honoured via :mod:`fnmatch` so legacy
            prefixes like ``"_secret_*"`` keep working.
        regex_patterns: Compiled regex patterns matched against string field
            **values** (Issue #43 §5).  Matched values are replaced with
            ``replacement``.  Patterns are compiled with ``re.IGNORECASE``.
        replacement: Substitution string for redacted values; default ``"***REDACTED***"``.
    """

    field_patterns: list[str] = field(default_factory=list)
    value_patterns: list[str] = field(default_factory=list)
    sensitive_keys: list[str] = field(default_factory=list)
    regex_patterns: list[str] = field(default_factory=list)
    replacement: str = "***REDACTED***"

    @classmethod
    def from_config(cls, config: Any) -> RedactionConfig:
        """Build a :class:`RedactionConfig` from an :class:`apcore.config.Config`.

        Reads (Issue #43 §5):
        - ``obs.redaction.regex_patterns`` (list[str])
        - ``obs.redaction.sensitive_keys`` (list[str])
        - ``obs.redaction.replacement`` (str)

        Falls back to the namespace defaults registered in
        :mod:`apcore.config` when keys are missing.  Empty lists are
        permitted; callers that want NO redaction must explicitly set
        ``sensitive_keys: []``.
        """
        from apcore.config import _DEFAULT_OBS_REDACTION_SENSITIVE_KEYS

        regex_patterns = config.get("obs.redaction.regex_patterns", []) or []
        sensitive_keys = config.get(
            "obs.redaction.sensitive_keys",
            list(_DEFAULT_OBS_REDACTION_SENSITIVE_KEYS),
        )
        # ``Config.get`` may return ``None`` when an explicit ``null`` is
        # written in YAML; coerce it to the default list rather than
        # silently disabling all key-based redaction.
        if sensitive_keys is None:
            sensitive_keys = list(_DEFAULT_OBS_REDACTION_SENSITIVE_KEYS)
        replacement = config.get("obs.redaction.replacement", "***REDACTED***") or "***REDACTED***"
        return cls(
            sensitive_keys=list(sensitive_keys),
            regex_patterns=list(regex_patterns),
            replacement=str(replacement),
        )

    @classmethod
    def default(cls) -> RedactionConfig:
        """Return a config seeded with the spec-default ``sensitive_keys`` list."""
        from apcore.config import _DEFAULT_OBS_REDACTION_SENSITIVE_KEYS

        return cls(sensitive_keys=list(_DEFAULT_OBS_REDACTION_SENSITIVE_KEYS))


def _normalize_key_for_match(s: str) -> str:
    """Normalize a key/pattern for cross-separator substring matching.

    Lower-cases and treats ``-`` / ``_`` / whitespace as equivalent so
    ``"X-API-Key"`` matches the ``"api_key"`` substring (per the §5 spec
    example).  Glob patterns are NOT normalized — they go through
    :func:`fnmatch.fnmatchcase` instead.
    """
    return s.lower().replace("-", "_").replace(" ", "_")


def _compact_key_for_match(s: str) -> str:
    """Lower-case with ``-`` / ``_`` / space stripped entirely.

    Allows camelCase keys like ``"AccessKey"`` to match the ``"access_key"``
    substring (D-54 canonical default list expects this).
    """
    return s.lower().replace("-", "").replace("_", "").replace(" ", "")


def _key_matches_sensitive(key: str, sensitive_keys: list[str]) -> bool:
    """Return True if *key* matches any entry in ``sensitive_keys``.

    Each pattern is interpreted as either:
    - a :mod:`fnmatch`-style glob when it contains ``*`` / ``?`` / ``[`` (case-insensitive),
    - or a plain case-insensitive substring match otherwise.  Hyphen,
      underscore, and space are treated as equivalent on both sides so
      ``"X-API-Key"`` matches ``"api_key"`` (Issue #43 §5).  The match also
      collapses separators to allow camelCase keys (``AccessKey``) to match
      snake_case patterns (``access_key``).
    """
    norm_key = _normalize_key_for_match(key)
    compact_key = _compact_key_for_match(key)
    lower_key = key.lower()
    for pat in sensitive_keys:
        if not pat:
            continue
        lower_pat = pat.lower()
        if any(ch in lower_pat for ch in ("*", "?", "[")):
            if fnmatch.fnmatchcase(lower_key, lower_pat):
                return True
        else:
            norm_pat = _normalize_key_for_match(pat)
            if norm_pat in norm_key:
                return True
            if _compact_key_for_match(pat) in compact_key:
                return True
    return False


def _value_matches_regex(value: Any, regex_patterns: list[str]) -> bool:
    """Return True if *value* (stringified) matches one of ``regex_patterns``.

    All patterns are compiled with :data:`re.IGNORECASE` so SREs writing
    e.g. ``"^bearer\\s+.+$"`` match both ``Bearer ...`` and ``bearer ...``.
    """
    if not regex_patterns:
        return False
    value_str = value if isinstance(value, str) else str(value)
    for pat in regex_patterns:
        if not pat:
            continue
        try:
            if re.search(pat, value_str, flags=re.IGNORECASE) is not None:
                return True
        except re.error:
            # Bad operator-supplied regex — skip rather than crash logging.
            continue
    return False


def _apply_redaction_config(data: dict[str, Any], config: RedactionConfig) -> dict[str, Any]:
    """Return a new dict with fields/values matching RedactionConfig replaced.

    Fields named in :data:`PROTECTED_LOG_FIELDS` are exempt from field-pattern
    matching so user-supplied glob patterns (e.g. ``*_id``) cannot scramble
    correlation identifiers required for log/trace stitching.

    Issue #43 §5: redaction is the **union** of legacy ``field_patterns`` /
    ``value_patterns`` and the new ``sensitive_keys`` (case-insensitive
    substring) / ``regex_patterns`` (case-insensitive value regex) lists.
    """
    result: dict[str, Any] = {}
    for key, value in data.items():
        if key in PROTECTED_LOG_FIELDS:
            result[key] = value
            continue
        field_match = any(fnmatch.fnmatch(key, pattern) for pattern in config.field_patterns)
        value_str = str(value) if not isinstance(value, str) else value
        value_match = any(re.search(pattern, value_str) for pattern in config.value_patterns)
        sensitive_key_match = _key_matches_sensitive(key, config.sensitive_keys)
        regex_value_match = _value_matches_regex(value, config.regex_patterns)
        if field_match or value_match or sensitive_key_match or regex_value_match:
            result[key] = config.replacement
        else:
            result[key] = value
    return result


def _redact_secrets_recursive(
    value: Any,
    depth: int = 0,
    config: RedactionConfig | None = None,
) -> Any:
    """Recursively redact secret-bearing keys/values up to ``_MAX_REDACTION_DEPTH``.

    Issue #43 §5: matching is driven by a :class:`RedactionConfig` rather
    than the hard-coded ``_secret_`` prefix.  Keys whose names match
    :attr:`RedactionConfig.sensitive_keys` (case-insensitive substring or
    glob) have their values replaced with the configured replacement
    token.  String values matching :attr:`RedactionConfig.regex_patterns`
    (case-insensitive) are also redacted, regardless of key name.

    When *config* is ``None`` the spec-default sensitive-keys list is used
    (which includes ``_secret_*`` so legacy callers stay protected).
    Recursion descends into nested dicts and lists.  Beyond
    :data:`_MAX_REDACTION_DEPTH` the current node is returned as-is —
    secrets deeper than that threshold are not inspected (defensive bound
    matching the schema validation depth limit).

    Keys in :data:`PROTECTED_LOG_FIELDS` are exempt from both the name rule
    and the value regex, at every depth.  Only the protected field's own scalar
    value is immune: a container under a protected key is still descended into,
    and array elements — having no key of their own — are never protected.
    """
    if config is None:
        config = RedactionConfig.default()
    if depth > _MAX_REDACTION_DEPTH:
        return value

    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            # A correlation-ID key is exempt from BOTH rules below, not just the
            # name one: observability.md states the exemption unconditionally, so
            # a trace_id whose VALUE happens to match a secret regex must survive
            # too. Decided once per entry, as apcore-rust does in `redact_inner`.
            protected = isinstance(k, str) and k in PROTECTED_LOG_FIELDS
            if not protected and isinstance(k, str) and _key_matches_sensitive(k, config.sensitive_keys):
                out[k] = config.replacement
            elif not protected and isinstance(v, str) and _value_matches_regex(v, config.regex_patterns):
                out[k] = config.replacement
            elif protected and not isinstance(v, (dict, list)):
                # Only the protected field's OWN scalar value is immune; a
                # container under a protected key falls through and is descended
                # into, matching apcore-rust.
                out[k] = v
            else:
                out[k] = _redact_secrets_recursive(v, depth + 1, config)
        return out
    if isinstance(value, list):
        # Array elements have no key of their own, so none of them is protected
        # — including elements directly under a protected key.
        return [_redact_secrets_recursive(item, depth + 1, config) for item in value]
    if isinstance(value, str) and _value_matches_regex(value, config.regex_patterns):
        return config.replacement
    return value


class ContextLogger:
    """Standalone structured logger with context injection and redaction."""

    def __init__(
        self,
        name: str = "apcore",
        *,
        format: str | None = None,
        output_format: str = "json",
        level: str = "info",
        redact_sensitive: bool = True,
        output: Any = None,
        redaction_config: RedactionConfig | None = None,
    ) -> None:
        self._name = name
        self._output_format = format if format is not None else output_format
        self._level = level
        self._level_value = _LEVELS.get(level, 20)
        self._redact_sensitive = redact_sensitive
        self._output = output if output is not None else sys.stderr
        # Issue #43 §5: redaction is config-driven.  When no explicit config
        # is supplied, fall back to the spec-default sensitive_keys list so
        # legacy callers still get ``_secret_*`` redaction.
        self._redaction_config = redaction_config if redaction_config is not None else RedactionConfig.default()
        self._trace_id: str | None = None
        self._module_id: str | None = None
        self._caller_id: str | None = None

    @classmethod
    def from_context(cls, context: Any, name: str, **kwargs: Any) -> ContextLogger:
        """Create a logger that auto-injects trace_id, module_id, caller_id from context."""
        logger = cls(name=name, **kwargs)
        logger._trace_id = context.trace_id
        logger._module_id = context.call_chain[-1] if context.call_chain else None
        logger._caller_id = context.caller_id
        return logger

    def _emit(self, level_name: str, message: str, extra: dict[str, Any] | None) -> None:
        level_value = _LEVELS.get(level_name, 20)
        if level_value < self._level_value:
            return

        redacted_extra = extra
        if extra is not None and self._redact_sensitive:
            # Recursive defense-in-depth: redact any nested keys that match
            # the configured sensitive_keys / regex_patterns up to
            # _MAX_REDACTION_DEPTH (matches schema validation depth limit).
            redacted_extra = _redact_secrets_recursive(extra, config=self._redaction_config)

        now = datetime.now(timezone.utc)
        entry = {
            "timestamp": now.isoformat(),
            "level": level_name,
            "message": message,
            "trace_id": self._trace_id,
            "module_id": self._module_id,
            "caller_id": self._caller_id,
            "logger": self._name,
            "extra": redacted_extra,
        }

        if self._output_format == "json":
            self._output.write(json.dumps(entry, default=str) + "\n")
        else:
            ts = now.strftime("%Y-%m-%d %H:%M:%S")
            lvl = level_name.upper()
            trace = self._trace_id or "none"
            mod = self._module_id or "none"
            extras_str = ""
            if redacted_extra:
                extras_str = " " + " ".join(f"{k}={v}" for k, v in redacted_extra.items())
            self._output.write(f"{ts} [{lvl}] [trace={trace}] [module={mod}] {message}{extras_str}\n")

    def trace(self, message: str, extra: dict[str, Any] | None = None) -> None:
        self._emit("trace", message, extra)

    def debug(self, message: str, extra: dict[str, Any] | None = None) -> None:
        self._emit("debug", message, extra)

    def info(self, message: str, extra: dict[str, Any] | None = None) -> None:
        self._emit("info", message, extra)

    def warn(self, message: str, extra: dict[str, Any] | None = None) -> None:
        self._emit("warn", message, extra)

    def error(self, message: str, extra: dict[str, Any] | None = None) -> None:
        self._emit("error", message, extra)

    def fatal(self, message: str, extra: dict[str, Any] | None = None) -> None:
        self._emit("fatal", message, extra)


class ObsLoggingMiddleware(Middleware):
    """Structured observability logging middleware using ContextLogger.

    Supports ``RedactionConfig`` for runtime-configurable field/value redaction
    applied in addition to schema-level ``x-sensitive`` annotations.
    Uses stack-based timing in context.data for safe nested call support.
    """

    def __init__(
        self,
        logger: ContextLogger | None = None,
        log_inputs: bool = True,
        log_outputs: bool = True,
        redaction_config: RedactionConfig | None = None,
    ) -> None:
        self._logger = logger if logger is not None else ContextLogger(name="apcore.obs_logging")
        self._log_inputs = log_inputs
        self._log_outputs = log_outputs
        self._redaction_config = redaction_config
        # Issue #43 §5: when an explicit RedactionConfig is supplied, override
        # the logger's default (spec-default sensitive_keys) so the secondary
        # _redact_secrets_recursive pass in ContextLogger._emit honours the
        # *same* rules and does not double-redact with a broader default list.
        if redaction_config is not None:
            self._logger._redaction_config = redaction_config

    def _redact(self, data: dict[str, Any]) -> dict[str, Any]:
        """Apply RedactionConfig rules if configured."""
        if self._redaction_config is None:
            return data
        return _apply_redaction_config(data, self._redaction_config)

    def before(self, module_id: str, inputs: dict[str, Any], context: Any) -> dict[str, Any] | None:
        starts = LOGGING_STARTS.get(context, default=[])
        starts.append(time.time())
        LOGGING_STARTS.set(context, starts)
        extra: dict[str, Any] = {
            "module_id": module_id,
            "caller_id": context.caller_id,
        }
        if self._log_inputs:
            base = context.redacted_inputs if context.redacted_inputs is not None else inputs
            extra["inputs"] = self._redact(base)
        self._logger.info("Module call started", extra=extra)
        return None

    def after(
        self,
        module_id: str,
        inputs: dict[str, Any],
        output: dict[str, Any],
        context: Any,
    ) -> dict[str, Any] | None:
        starts = LOGGING_STARTS.get(context, default=[])
        if not starts:
            return None
        start_time = starts.pop()
        duration_ms = (time.time() - start_time) * 1000
        extra: dict[str, Any] = {
            "module_id": module_id,
            "duration_ms": duration_ms,
        }
        if self._log_outputs:
            extra["output"] = self._redact(output)
        self._logger.info("Module call completed", extra=extra)
        return None

    def on_error(self, module_id: str, inputs: dict[str, Any], error: Exception, context: Any) -> dict[str, Any] | None:
        starts = LOGGING_STARTS.get(context, default=[])
        if not starts:
            return None
        start_time = starts.pop()
        duration_ms = (time.time() - start_time) * 1000
        self._logger.error(
            "Module call failed",
            extra={
                "module_id": module_id,
                "duration_ms": duration_ms,
                "error_type": type(error).__name__,
                "error_message": str(error),
            },
        )
        return None


__all__ = [
    "ContextLogger",
    "ObsLoggingMiddleware",
    "RedactionConfig",
]
