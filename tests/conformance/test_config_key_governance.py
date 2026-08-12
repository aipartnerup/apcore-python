"""Drive `config_key_governance.json` — the configuration key-surface guard.

The fixture derives its `allowed_keys` / `canonical_defaults` from the canonical
schemas, so this test is really asking: does `apcore.config`'s idea of the config
surface still match `schemas/`?

It exists because four separate instances of the same defect shipped undetected:
`schema.validation.*` validated by every SDK and declared by no schema, a frozen
`version`/`project` default pair that made the required-field check unreachable,
`middleware.circuit_breaker.*` forbidden by `apcore-config.schema.json` yet
validated everywhere and read nowhere, and a missing Rust default table that
resolved 15 documented keys to null. None was findable by any existing test.
"""

from __future__ import annotations

from typing import Any

import pytest

from apcore.config import _CONSTRAINTS, _DEFAULTS

from .canonical_fixtures import fixtures_dir, load_fixture

FIXTURE = load_fixture("config_key_governance.json")
ALLOWED: set[str] = set(FIXTURE["allowed_keys"])
CANONICAL: dict[str, Any] = FIXTURE["canonical_defaults"]


def _flatten(tree: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Flatten a nested default table to {dot_path: value}.

    An empty dict is a leaf: it is a declared value, not a subtree to descend.
    """
    out: dict[str, Any] = {}
    for key, val in tree.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(val, dict) and val:
            out.update(_flatten(val, path))
        else:
            out[path] = val
    return out


DEFAULT_KEYS = _flatten(_DEFAULTS)
CONSTRAINT_KEYS = set(_CONSTRAINTS)


class TestConfigKeySurfaceGovernance:
    def test_default_table_declares_no_undeclared_key(self) -> None:
        """A default for a key no schema declares is an SDK-invented default.

        `apcore-config.schema.json` is `additionalProperties: false`, so a user
        config carrying such a key fails the canonical schema while the SDK
        quietly supplies a value for it.
        """
        violations = sorted(set(DEFAULT_KEYS) - ALLOWED)
        assert violations == [], (
            "_DEFAULTS declares keys no canonical schema allows:\n  "
            + "\n  ".join(f"{k} = {DEFAULT_KEYS[k]!r}" for k in violations)
            + "\nEither add them to the appropriate schema in apcore/schemas/ "
            "(and regenerate the fixture) or remove them from _DEFAULTS."
        )

    def test_constraint_table_declares_no_undeclared_key(self) -> None:
        """Validating a key the canonical schema forbids is worse than not
        validating it: it tells the operator the key is understood."""
        violations = sorted(CONSTRAINT_KEYS - ALLOWED)
        assert violations == [], (
            "_CONSTRAINTS validates keys no canonical schema allows:\n  "
            + "\n  ".join(violations)
        )

    def test_reproduces_every_canonical_default(self) -> None:
        """A missing entry means the key resolves to None here while its peers
        return the documented value."""
        missing = sorted(set(CANONICAL) - set(DEFAULT_KEYS))
        assert missing == [], (
            "defaults.schema.json declares defaults _DEFAULTS does not carry:\n  "
            + "\n  ".join(f"{k} = {CANONICAL[k]!r}" for k in missing)
        )

    @pytest.mark.parametrize("key", sorted(CANONICAL))
    def test_default_value_matches_canonical(self, key: str) -> None:
        assert DEFAULT_KEYS[key] == CANONICAL[key], (
            f"{key}: _DEFAULTS has {DEFAULT_KEYS[key]!r}, "
            f"defaults.schema.json declares {CANONICAL[key]!r}"
        )

    def test_fixture_is_derived_not_authored(self) -> None:
        """Guard the guard: if the fixture ever stops naming its generator, the
        next person to hand-edit it will make it a second source of truth."""
        contract = FIXTURE["driver_contract"]["sources"]
        assert "regenerated" in contract and "do NOT hand-edit" in contract
        # Check the declared sources against the SPEC REPO, not against a
        # literal transcription of themselves: a list compared to its own copy
        # cannot fail when a schema is renamed or deleted. (Same tautology
        # shape as apcore-python#32 / aiperceivable/apcore#81.)
        spec_repo = fixtures_dir().parent.parent
        declared = FIXTURE["canonical_sources"]
        assert declared, "the fixture must name the schemas it was generated from"
        missing = [src for src in declared if not (spec_repo / src).is_file()]
        assert missing == [], (
            f"canonical_sources names schema files that do not exist under {spec_repo}: {missing}"
        )
