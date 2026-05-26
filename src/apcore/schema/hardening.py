"""Schema hardening utilities: content-addressable hash and standards-compliant validation.

Implements PROTOCOL_SPEC §4.15 (Issue #44):
- SHA-256 content hash for schema deduplication (canonical JSON form)
- JSON Schema–direct validation (anyOf/oneOf exhaustive, recursive $ref, constraints)
- Format-level warning (SHOULD-level, not hard error)
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import re
from datetime import date, datetime, time
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from collections.abc import Callable

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonschemaError

from apcore.schema.types import SchemaValidationErrorDetail, SchemaValidationResult

__all__ = ["content_hash", "validate_schema_dict"]

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_URI_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+\-.]*://")


def _is_datetime(v: str) -> bool:
    try:
        datetime.fromisoformat(v.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _is_date(v: str) -> bool:
    try:
        date.fromisoformat(v)
        return True
    except ValueError:
        return False


def _is_time(v: str) -> bool:
    try:
        time.fromisoformat(v)
        return True
    except ValueError:
        return False


def _is_uuid(v: str) -> bool:
    try:
        UUID(v)
        return True
    except ValueError:
        return False


def _is_ipv4(v: str) -> bool:
    try:
        ipaddress.IPv4Address(v)
        return True
    except ValueError:
        return False


def _is_ipv6(v: str) -> bool:
    try:
        ipaddress.IPv6Address(v)
        return True
    except ValueError:
        return False


_FORMAT_CHECKERS: dict[str, Callable[[str], bool]] = {
    "date-time": _is_datetime,
    "date": _is_date,
    "time": _is_time,
    "email": lambda v: bool(_EMAIL_RE.match(v)),
    "uri": lambda v: bool(_URI_SCHEME_RE.match(v)),
    "uuid": _is_uuid,
    "ipv4": _is_ipv4,
    "ipv6": _is_ipv6,
}


def content_hash(schema: dict[str, Any]) -> str:
    """Return the SHA-256 hex digest of the canonical JSON form of schema.

    Canonical form: json.dumps with sort_keys=True and no extra whitespace.
    Schemas with identical content but different key ordering produce the same hash.
    """
    canonical = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def validate_schema_dict(data: Any, schema: dict[str, Any]) -> SchemaValidationResult:
    """Validate *data* against a raw JSON Schema dict.

    Uses Draft202012Validator (jsonschema library) so all of the following are
    handled correctly and exhaustively:
    - anyOf: accepted if ≥1 branch matches
    - oneOf: accepted if exactly 1 branch matches; SCHEMA_UNION_AMBIGUOUS if >1
    - allOf: accepted only if every branch matches
    - Recursive $ref (self-referencing via $id or "#")
    - Numeric and string constraints (minimum, maximum, minLength, maxLength, pattern)
    - not keyword

    Format violations (date-time, email, uri, …) emit a logger.warning but do NOT
    fail validation (SHOULD-level enforcement per PROTOCOL_SPEC §4.15.4).
    """
    validator = Draft202012Validator(schema)
    raw_errors = list(validator.iter_errors(data))

    if raw_errors:
        error_code = _map_error_code(raw_errors)
        details = [_error_to_detail(e) for e in raw_errors]
        return SchemaValidationResult(valid=False, errors=details, error_code=error_code)

    _check_formats_and_warn(data, schema)
    return SchemaValidationResult(valid=True, errors=[])


def _map_error_code(errors: list[JsonschemaError]) -> str:
    """Map jsonschema validation errors to apcore error codes."""
    for error in errors:
        if error.validator == "oneOf":
            if "is valid under each of" in error.message:
                return "SCHEMA_UNION_AMBIGUOUS"
            return "SCHEMA_UNION_NO_MATCH"
        if error.validator == "anyOf":
            return "SCHEMA_UNION_NO_MATCH"
    return "SCHEMA_VALIDATION_ERROR"


def _error_to_detail(error: JsonschemaError) -> SchemaValidationErrorDetail:
    path_parts = list(error.absolute_path)
    path = "/" + "/".join(str(p) for p in path_parts) if path_parts else "/"
    raw_validator = error.validator
    constraint: str | None = str(raw_validator) if raw_validator is not None else None
    return SchemaValidationErrorDetail(
        path=path,
        message=error.message,
        constraint=constraint,
        expected=None,
        actual=error.instance,
    )


def _check_formats_and_warn(data: Any, schema: Any, _path: str = "") -> None:
    """Walk the data/schema tree and log warnings for format violations.

    Sync finding A-D-032: previously this function only iterated
    ``schema['properties']`` at the top level — nested invalid date-time /
    uuid / etc. formats at e.g. ``/user/created_at`` emitted no warning
    in Python while apcore-typescript and apcore-rust did warn. The
    walker now recurses into nested ``properties`` AND ``items`` so
    cross-language conformance fixtures see the same warning set.
    """
    if not isinstance(schema, dict):
        return

    # Object: walk each property + recurse on its sub-schema
    if isinstance(data, dict):
        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for prop_name, prop_schema in properties.items():
                if not isinstance(prop_schema, dict):
                    continue
                value = data.get(prop_name)
                if value is None:
                    continue
                child_path = f"{_path}/{prop_name}" if _path else f"/{prop_name}"
                fmt = prop_schema.get("format")
                if fmt and isinstance(value, str):
                    checker = _FORMAT_CHECKERS.get(fmt)
                    if checker is not None and not checker(value):
                        logger.warning(
                            "Format violation (non-fatal): field %r declared format=%r but value %r is not conformant",
                            child_path,
                            fmt,
                            value,
                        )
                # Recurse into nested objects and arrays.
                _check_formats_and_warn(value, prop_schema, child_path)

    # Array: walk each element against the schema's `items` declaration.
    if isinstance(data, list):
        items_schema = schema.get("items")
        if isinstance(items_schema, dict):
            for idx, value in enumerate(data):
                child_path = f"{_path}[{idx}]"
                if isinstance(value, str):
                    fmt = items_schema.get("format")
                    if fmt:
                        checker = _FORMAT_CHECKERS.get(fmt)
                        if checker is not None and not checker(value):
                            logger.warning(
                                "Format violation (non-fatal): field %r declared format=%r but value %r is not conformant",
                                child_path,
                                fmt,
                                value,
                            )
                _check_formats_and_warn(value, items_schema, child_path)
