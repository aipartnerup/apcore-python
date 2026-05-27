"""Conformance tests for ModuleError serialization wire format (A-D-008) fixture.

Locks the cross-language golden form: ``ModuleError.to_dict()`` MUST emit
snake_case keys (trace_id, ai_guidance, user_fixable), snake_case the keys
inside ``details``, and omit null/None optional fields (sparse output).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from apcore.errors import ModuleError


def _fixture_path() -> Path:
    env = os.environ.get("APCORE_FIXTURES")
    if env:
        return Path(env) / "error_serialization.json"
    env_repo = os.environ.get("APCORE_SPEC_REPO")
    if env_repo:
        return Path(env_repo) / "conformance" / "fixtures" / "error_serialization.json"
    repo_root = Path(__file__).resolve().parent.parent.parent
    return repo_root.parent / "apcore" / "conformance" / "fixtures" / "error_serialization.json"


def _load_fixture() -> dict:
    with _fixture_path().open() as f:
        return json.load(f)


_FIXTURE = _load_fixture()

# Fields the driver is allowed to forward from the fixture `input` into the
# ModuleError constructor (only those present in the case are passed).
_INPUT_FIELDS = (
    "code",
    "message",
    "trace_id",
    "ai_guidance",
    "user_fixable",
    "retryable",
    "details",
)


@pytest.mark.parametrize("case", _FIXTURE["test_cases"], ids=[c["id"] for c in _FIXTURE["test_cases"]])
def test_error_serialization(case: dict) -> None:
    inp = case["input"]
    kwargs = {k: inp[k] for k in _INPUT_FIELDS if k in inp}

    err = ModuleError(**kwargs)
    serialized = err.to_dict()

    for key in case["expected_keys_present"]:
        assert key in serialized, f"expected key {key!r} present in {sorted(serialized)}"
    for key in case["expected_keys_absent"]:
        assert key not in serialized, f"expected key {key!r} absent but found in {sorted(serialized)}"

    detail_present = case.get("expected_detail_keys_present", [])
    detail_absent = case.get("expected_detail_keys_absent", [])
    if detail_present or detail_absent:
        details = serialized.get("details", {})
        for key in detail_present:
            assert key in details, f"expected detail key {key!r} present in {sorted(details)}"
        for key in detail_absent:
            assert key not in details, f"expected detail key {key!r} absent but found in {sorted(details)}"
