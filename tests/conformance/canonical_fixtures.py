"""Locate and load conformance fixtures from the canonical apcore spec repo.

Every conformance driver in this package resolves its fixture through here.

Why not a vendored copy: apcore-typescript and apcore-rust both read the
canonical file directly (via ``$CONFORMANCE_SPEC_REPO`` or a sibling checkout), so a
spec-side edit reaches them on the next test run. apcore-python used to keep
private copies under ``tests/conformance/fixtures/``, which meant a fixture that
gained a case in the spec repo silently left Python asserting the old snapshot —
the exact drift conformance fixtures exist to prevent. Five drivers had already
open-coded this resolver; the other four read the vendored copy.

Search order (identical to ``tests/test_conformance.py``):

1. ``$APCORE_FIXTURES`` — a fixtures directory, used by CI matrix jobs.
2. ``$CONFORMANCE_SPEC_REPO`` — the spec repo root.
3. ``../apcore/`` beside this repo — the standard workspace and CI layout.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest

__all__ = ["fixtures_dir", "fixture_path", "load_fixture", "case_ids", "spec_repo_env"]

_FIXTURES_ENV = "APCORE_FIXTURES"
_SPEC_REPO_ENV = "CONFORMANCE_SPEC_REPO"

# Transitional fallback (apcore#86). The spec-repo locator used to be
# ``APCORE_SPEC_REPO``, but PROTOCOL_SPEC §9.2 makes *every* ``APCORE_*``
# variable a config override: the suffix is lowercased and split into a dot
# path, so ``APCORE_SPEC_REPO=/path`` injected ``spec.repo`` into the declared
# config document that §9.1's required-field check runs against. The locator is
# test infrastructure, not configuration, so it moved out of the claimed
# prefix. Reading the old name keeps a developer who still exports it working.
# REMOVE once all three SDK CI workflows are on CONFORMANCE_SPEC_REPO.
_LEGACY_SPEC_REPO_ENV = "APCORE_SPEC_REPO"


def spec_repo_env() -> tuple[str, str] | None:
    """Return ``(variable_name, value)`` for the spec-repo override, if set.

    The name is returned alongside the value so failure messages can name the
    variable the developer actually set rather than the one they did not.
    """
    for name in (_SPEC_REPO_ENV, _LEGACY_SPEC_REPO_ENV):
        value = os.environ.get(name)
        if value:
            return name, value
    return None


@lru_cache(maxsize=1)
def fixtures_dir() -> Path:
    """Return the canonical ``conformance/fixtures`` directory."""
    env_fixtures = os.environ.get(_FIXTURES_ENV)
    if env_fixtures:
        candidate = Path(env_fixtures)
        if candidate.is_dir():
            return candidate
        pytest.fail(f"${_FIXTURES_ENV}={env_fixtures} is not a directory.")

    env_repo = spec_repo_env()
    if env_repo:
        name, value = env_repo
        candidate = Path(value) / "conformance" / "fixtures"
        if candidate.is_dir():
            return candidate
        pytest.fail(
            f"${name}={value} does not contain conformance/fixtures/. "
            f"Ensure the apcore protocol spec repo is at that path."
        )

    # apcore-python/tests/conformance/ → apcore-python/ → workspace root
    repo_root = Path(__file__).resolve().parent.parent.parent
    sibling = repo_root.parent / "apcore" / "conformance" / "fixtures"
    if sibling.is_dir():
        return sibling

    pytest.fail(
        "Cannot find the canonical apcore conformance fixtures.\n\n"
        "Fix one of:\n"
        f"  1. Set ${_SPEC_REPO_ENV} to the apcore spec repo path\n"
        f"  2. Set ${_FIXTURES_ENV} to a conformance/fixtures directory\n"
        "  3. Check out the apcore spec repo beside apcore-python/"
    )


def fixture_path(name: str) -> Path:
    """Return the path to canonical fixture *name* (e.g. ``acl_evaluation.json``)."""
    path = fixtures_dir() / name
    if not path.is_file():
        pytest.fail(f"Canonical conformance fixture '{name}' not found at {path}")
    return path


def load_fixture(name: str) -> dict[str, Any]:
    """Load and parse canonical fixture *name*."""
    return json.loads(fixture_path(name).read_text())  # type: ignore[no-any-return]


def case_ids(name: str) -> list[str]:
    """Return the ``id`` of every test case in canonical fixture *name*.

    Used by the drivers that assert behaviour in hand-written form rather than
    by iterating the fixture: comparing this list against the cases they cover
    turns a spec-side addition into a failing test instead of a silent gap.
    """
    fixture = load_fixture(name)
    cases = fixture.get("test_cases") or fixture.get("cases") or []
    return [case["id"] for case in cases]
