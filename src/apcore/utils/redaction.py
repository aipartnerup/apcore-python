"""Sensitive field redaction utility (Algorithm A13)."""

from __future__ import annotations

import copy
import fnmatch
import re
from typing import Any

REDACTED_VALUE: str = "***REDACTED***"

# Correlation identifiers that MUST NEVER be redacted, whatever the configured
# ``sensitive_keys`` or ``regex_patterns`` say.
#
#   observability.md § Redaction configuration: "Implementations MUST NOT redact
#   `trace_id`, `caller_id`, `module_id`, or `span_id`; these correlation fields
#   MUST appear unmodified in every log entry."
#
# The rule is unconditional, so the exemption covers BOTH the field-name rule and
# the value-regex rule — a ``trace_id`` whose VALUE happens to match a secret
# regex must still survive, which is exactly where correlation matters most.
# Canonical home: this leaf module, so the executor capture path
# (``redact_sensitive``) and the log-emission path
# (``apcore.observability.context_logger``) share one definition instead of
# guarding at one call site and not the other. Mirrors apcore-rust
# ``NEVER_REDACT_FIELDS`` (src/observability/redaction.rs:18) and
# apcore-typescript ``PROTECTED_LOG_FIELDS``.
PROTECTED_LOG_FIELDS: frozenset[str] = frozenset({"trace_id", "span_id", "caller_id", "module_id", "target_id"})


def _default_sensitive_keys() -> list[str]:
    """Return the spec-default ``obs.redaction.sensitive_keys`` list.

    Imported lazily so this module does not pull in the full Config bus
    on import.  See :mod:`apcore.config` (Issue #43 §5) for the canonical
    list and the matching ``obs.redaction`` namespace registration.
    """
    from apcore.config import _DEFAULT_OBS_REDACTION_SENSITIVE_KEYS

    return list(_DEFAULT_OBS_REDACTION_SENSITIVE_KEYS)


def redact_sensitive(
    data: dict[str, Any],
    schema_dict: dict[str, Any],
    *,
    sensitive_keys: list[str] | None = None,
    regex_patterns: list[str] | None = None,
    replacement: str | None = None,
) -> dict[str, Any]:
    """Redact fields marked with x-sensitive in the schema.

    Implements Algorithm A13 from PROTOCOL_SPEC section 9.5.
    Returns a deep copy of data with sensitive values replaced by
    ``replacement`` (default ``"***REDACTED***"``).

    Issue #43 §5: in addition to schema-level ``x-sensitive`` annotations
    and the legacy ``_secret_*`` prefix, fields whose names match any
    pattern in ``sensitive_keys`` (case-insensitive substring or
    :mod:`fnmatch` glob) are redacted, and string values matching one of
    ``regex_patterns`` (compiled with :data:`re.IGNORECASE`) are
    redacted.  When ``sensitive_keys`` is ``None`` the spec-default list
    (which still includes ``_secret_*``) is used.

    Args:
        data: The data dict to redact.
        schema_dict: A JSON Schema dict that may contain ``x-sensitive: true``
            on individual properties.
        sensitive_keys: Optional override for the field-name match list.
        regex_patterns: Optional list of regex patterns matched against
            string values (case-insensitive).
        replacement: Optional replacement token; defaults to
            :data:`REDACTED_VALUE`.

    Returns:
        A new dict with sensitive values replaced. Original data is not modified.
    """
    keys = sensitive_keys if sensitive_keys is not None else _default_sensitive_keys()
    patterns = list(regex_patterns) if regex_patterns is not None else []
    token = replacement if replacement is not None else REDACTED_VALUE

    redacted = copy.deepcopy(data)
    _redact_fields(redacted, schema_dict, token=token)
    _redact_by_keys_and_regex(redacted, keys, patterns, token)
    return redacted


def _redact_fields(
    data: dict[str, Any],
    schema_dict: dict[str, Any],
    *,
    token: str = REDACTED_VALUE,
) -> None:
    """In-place redaction based on schema x-sensitive markers."""
    properties = schema_dict.get("properties")
    if not properties:
        return

    for field_name, field_schema in properties.items():
        if field_name not in data:
            continue

        value = data[field_name]

        # x-sensitive: true on this property
        if field_schema.get("x-sensitive") is True:
            if value is not None:
                data[field_name] = token
            continue

        # Nested object: recurse
        if field_schema.get("type") == "object" and "properties" in field_schema and isinstance(value, dict):
            _redact_fields(value, field_schema, token=token)
            continue

        # Array: redact items
        if field_schema.get("type") == "array" and "items" in field_schema and isinstance(value, list):
            items_schema = field_schema["items"]
            if items_schema.get("x-sensitive") is True:
                for i, item in enumerate(value):
                    if item is not None:
                        value[i] = token
            elif items_schema.get("type") == "object" and "properties" in items_schema:
                for item in value:
                    if isinstance(item, dict):
                        _redact_fields(item, items_schema, token=token)


def _normalize_for_match(s: str) -> str:
    """Lower-case with ``-`` / ``_`` / spaces collapsed to ``_``.

    Lets ``"X-API-Key"`` match the ``"api_key"`` substring as required
    by the Issue #43 §5 spec example.
    """
    return s.lower().replace("-", "_").replace(" ", "_")


def _compact_for_match(s: str) -> str:
    """Lower-case with ``-`` / ``_`` / space stripped entirely.

    Allows camelCase keys like ``"AccessKey"`` to match the ``"access_key"``
    substring (D-54 canonical default list expects this).
    """
    return s.lower().replace("-", "").replace("_", "").replace(" ", "")


def _key_matches(key: str, sensitive_keys: list[str]) -> bool:
    """Case-insensitive substring + glob match against ``sensitive_keys``."""
    if not sensitive_keys:
        return False
    norm_key = _normalize_for_match(key)
    compact_key = _compact_for_match(key)
    lower_key = key.lower()
    for pat in sensitive_keys:
        if not pat:
            continue
        lower_pat = pat.lower()
        if any(ch in lower_pat for ch in ("*", "?", "[")):
            if fnmatch.fnmatchcase(lower_key, lower_pat):
                return True
        else:
            if _normalize_for_match(pat) in norm_key:
                return True
            # Also match the compact (separator-stripped) form so that
            # camelCase keys like "AccessKey" match the "access_key" pattern.
            if _compact_for_match(pat) in compact_key:
                return True
    return False


def _value_matches(value: Any, regex_patterns: list[str]) -> bool:
    """Case-insensitive regex match against the string form of *value*."""
    if not regex_patterns:
        return False
    if not isinstance(value, str):
        return False
    for pat in regex_patterns:
        if not pat:
            continue
        try:
            if re.search(pat, value, flags=re.IGNORECASE) is not None:
                return True
        except re.error:
            continue
    return False


def _redact_by_keys_and_regex(
    data: dict[str, Any],
    sensitive_keys: list[str],
    regex_patterns: list[str],
    token: str,
) -> None:
    """In-place redaction by name (substring/glob) or value (regex), at any depth.

    Replaces the legacy ``_redact_secret_prefix`` walk (Issue #43 §5).
    The default ``sensitive_keys`` list still contains ``_secret_*`` so
    every existing call site retains its prior behaviour.

    Keys in :data:`PROTECTED_LOG_FIELDS` are exempt from both rules at every
    depth, matching apcore-rust's per-entry ``NEVER_REDACT_FIELDS`` check.
    """
    for key in data:
        value = data[key]
        if value is None:
            continue
        # A correlation identifier is exempt from BOTH rules below. Note the
        # guard does not stop the walk: a container under a protected key is
        # still descended into, only the protected field's own scalar value is
        # immune (apcore-rust `redact_inner`: "Containers below a protected key
        # are still descended into ... only the protected field's own scalar
        # value is immune").
        protected = key in PROTECTED_LOG_FIELDS
        if not protected and _key_matches(key, sensitive_keys):
            data[key] = token
            continue
        if not protected and _value_matches(value, regex_patterns):
            data[key] = token
            continue
        if isinstance(value, dict):
            _redact_by_keys_and_regex(value, sensitive_keys, regex_patterns, token)
        elif isinstance(value, list):
            _redact_in_list(value, sensitive_keys, regex_patterns, token)


def _redact_in_list(
    items: list[Any],
    sensitive_keys: list[str],
    regex_patterns: list[str],
    token: str,
) -> None:
    """Traverse a list, redacting dict children and recursing into nested lists."""
    for index, item in enumerate(items):
        if item is None:
            continue
        if isinstance(item, dict):
            _redact_by_keys_and_regex(item, sensitive_keys, regex_patterns, token)
        elif isinstance(item, list):
            _redact_in_list(item, sensitive_keys, regex_patterns, token)
        elif _value_matches(item, regex_patterns):
            items[index] = token
