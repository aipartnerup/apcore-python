"""Conformance tests for Schema System Hardening (Issue #44, PROTOCOL_SPEC §4.15).

Exercises five fixture files:
  schema_hardening_union.json       — anyOf/oneOf/allOf exhaustive evaluation
  schema_hardening_recursive.json   — recursive $ref (#) via TreeNode
  schema_hardening_constraints.json — numeric + string constraint enforcement
  schema_hardening_formats.json     — format-level warnings (SHOULD, not hard error)
  schema_hardening_cache.json       — SHA-256 content-addressable deduplication
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from apcore.schema.hardening import content_hash, validate_schema_dict

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / name).read_text())


# ---------------------------------------------------------------------------
# Fixture 1: Union type evaluation (anyOf / oneOf / allOf)
# ---------------------------------------------------------------------------


class TestUnionFixture:
    """schema_hardening_union.json — PROTOCOL_SPEC §4.15.1."""

    _fixture = _load("schema_hardening_union.json")

    @pytest.mark.parametrize(
        "tc",
        [tc for tc in _fixture["test_cases"] if "schema" in tc],
        ids=lambda tc: tc["id"],
    )
    def test_validate_union(self, tc: dict[str, Any]) -> None:
        schema: dict[str, Any] = tc["schema"]
        data = tc["input"]
        expected = tc["expected"]
        result = validate_schema_dict(data, schema)
        assert result.valid == expected["valid"], (
            f"[{tc['id']}] valid mismatch: got {result.valid}, expected {expected['valid']}. " f"errors={result.errors}"
        )
        if expected.get("error_code"):
            assert result.error_code == expected["error_code"], (
                f"[{tc['id']}] error_code mismatch: got {result.error_code!r}, " f"expected {expected['error_code']!r}"
            )


# ---------------------------------------------------------------------------
# Fixture 2: Recursive schema (TreeNode with $ref: "#")
# ---------------------------------------------------------------------------


class TestRecursiveFixture:
    """schema_hardening_recursive.json — PROTOCOL_SPEC §4.15.2."""

    _fixture = _load("schema_hardening_recursive.json")

    @pytest.mark.parametrize(
        "tc",
        _fixture["test_cases"],
        ids=lambda tc: tc["id"],
    )
    def test_validate_recursive(self, tc: dict[str, Any]) -> None:
        schema: dict[str, Any] = self._fixture["schema"]
        data = tc["input"]
        expected = tc["expected"]
        result = validate_schema_dict(data, schema)
        assert result.valid == expected["valid"], (
            f"[{tc['id']}] valid mismatch: got {result.valid}, expected {expected['valid']}. " f"errors={result.errors}"
        )
        if expected.get("error_code"):
            assert result.error_code == expected["error_code"], (
                f"[{tc['id']}] error_code mismatch: got {result.error_code!r}, " f"expected {expected['error_code']!r}"
            )


# ---------------------------------------------------------------------------
# Fixture 3: Numeric and string constraints
# ---------------------------------------------------------------------------


class TestConstraintsFixture:
    """schema_hardening_constraints.json — PROTOCOL_SPEC §4.15.3."""

    _fixture = _load("schema_hardening_constraints.json")

    @pytest.mark.parametrize(
        "tc",
        _fixture["test_cases"],
        ids=lambda tc: tc["id"],
    )
    def test_validate_constraints(self, tc: dict[str, Any]) -> None:
        schema: dict[str, Any] = tc["schema"]
        data = tc["input"]
        expected = tc["expected"]
        result = validate_schema_dict(data, schema)
        assert result.valid == expected["valid"], (
            f"[{tc['id']}] valid mismatch: got {result.valid}, expected {expected['valid']}. " f"errors={result.errors}"
        )
        if expected.get("error_code"):
            assert result.error_code == expected["error_code"], (
                f"[{tc['id']}] error_code mismatch: got {result.error_code!r}, " f"expected {expected['error_code']!r}"
            )


# ---------------------------------------------------------------------------
# Fixture 4: Semantic format mapping (warn-on-invalid, not hard error)
# ---------------------------------------------------------------------------


class TestFormatsFixture:
    """schema_hardening_formats.json — PROTOCOL_SPEC §4.15.4."""

    _fixture = _load("schema_hardening_formats.json")

    @pytest.mark.parametrize(
        "tc",
        _fixture["test_cases"],
        ids=lambda tc: tc["id"],
    )
    def test_validate_formats(self, tc: dict[str, Any], caplog: pytest.LogCaptureFixture) -> None:
        schema: dict[str, Any] = tc["schema"]
        data = tc["input"]
        expected = tc["expected"]

        with caplog.at_level(logging.WARNING, logger="apcore.schema.hardening"):
            result = validate_schema_dict(data, schema)

        assert result.valid == expected["valid"], (
            f"[{tc['id']}] valid mismatch: got {result.valid}, expected {expected['valid']}. " f"errors={result.errors}"
        )

        if expected.get("error_code"):
            assert result.error_code == expected["error_code"], (
                f"[{tc['id']}] error_code mismatch: got {result.error_code!r}, " f"expected {expected['error_code']!r}"
            )

        expected_warn = expected.get("warn_logged", False)
        got_warn = any("Format violation" in r.message for r in caplog.records)
        assert got_warn == expected_warn, (
            f"[{tc['id']}] warn_logged mismatch: got {got_warn}, expected {expected_warn}. "
            f"log records: {[r.message for r in caplog.records]}"
        )


# ---------------------------------------------------------------------------
# Fixture 5: Content-addressable cache (SHA-256 deduplication)
# ---------------------------------------------------------------------------


class TestCacheFixture:
    """schema_hardening_cache.json — PROTOCOL_SPEC §4.15.5."""

    _fixture = _load("schema_hardening_cache.json")

    @pytest.mark.parametrize(
        "tc",
        _fixture["test_cases"],
        ids=lambda tc: tc["id"],
    )
    def test_content_hash(self, tc: dict[str, Any]) -> None:
        schemas: list[dict[str, Any]] = tc["schemas"]
        expected = tc["expected"]
        assert len(schemas) == 2, "Each cache test case must have exactly 2 schemas"

        hash_a = content_hash(schemas[0])
        hash_b = content_hash(schemas[1])
        same_hash = hash_a == hash_b

        assert same_hash == expected["same_hash"], (
            f"[{tc['id']}] same_hash mismatch: got {same_hash}, expected {expected['same_hash']}. "
            f"hash_a={hash_a[:12]}…, hash_b={hash_b[:12]}…"
        )
        assert len(hash_a) == 64, "SHA-256 hex digest must be 64 characters"
        assert len(hash_b) == 64, "SHA-256 hex digest must be 64 characters"
