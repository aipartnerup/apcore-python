"""Conformance tests for ACL condition-handler error handling (A-D-011/012) fixture.

SECURITY golden test: a custom condition handler that raises during evaluation
MUST fail CLOSED (rule does not match) AND the emitted AuditEntry MUST carry a
non-null ``handler_error``.
"""

from __future__ import annotations


import pytest

from apcore.acl import ACL, ACLRule, AuditEntry
from apcore.context import Context
from conformance.canonical_fixtures import load_fixture


def _load_fixture() -> dict:
    """Load the canonical fixture from the apcore spec repo."""
    return load_fixture("acl_handler_error.json")


_FIXTURE = _load_fixture()
_THROWING_KEY: str = _FIXTURE["throwing_condition_key"]


class _ThrowingConditionHandler:
    """Condition handler whose evaluate() always raises (fail-closed driver)."""

    def evaluate(self, value, context: Context) -> bool:  # noqa: ANN001
        raise RuntimeError("conformance throwing condition handler")


@pytest.fixture(autouse=True)
def _register_throwing_handler():
    """Register the throwing handler for the fixture key, then clean up."""
    previous = ACL._condition_handlers.get(_THROWING_KEY)
    ACL.register_condition(_THROWING_KEY, _ThrowingConditionHandler())
    try:
        yield
    finally:
        if previous is not None:
            ACL._condition_handlers[_THROWING_KEY] = previous
        else:
            ACL._condition_handlers.pop(_THROWING_KEY, None)


def _build_rules(raw_rules: list[dict]) -> list[ACLRule]:
    return [
        ACLRule(
            callers=r["callers"],
            targets=r["targets"],
            effect=r["effect"],
            description=r.get("description", ""),
            conditions=r.get("conditions"),
        )
        for r in raw_rules
    ]


@pytest.mark.parametrize("case", _FIXTURE["test_cases"], ids=[c["id"] for c in _FIXTURE["test_cases"]])
def test_acl_handler_error(case: dict) -> None:
    captured: list[AuditEntry] = []

    acl = ACL(
        rules=_build_rules(case["rules"]),
        default_effect=case["default_effect"],
        audit_logger=captured.append,
    )

    # Conditions require a non-None context to be evaluated at all.
    context = Context.create()
    decision = acl.check(case["caller_id"], case["target_id"], context)

    assert decision is case["expected"], f"expected decision {case['expected']} (fail-closed), got {decision}"

    assert len(captured) == 1, f"expected exactly one audit entry, got {len(captured)}"
    entry = captured[0]
    if case["expected_audit_handler_error_present"]:
        assert entry.handler_error is not None, "expected AuditEntry.handler_error to be non-null"
        assert _THROWING_KEY in entry.handler_error
