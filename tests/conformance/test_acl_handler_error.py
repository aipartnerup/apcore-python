"""Conformance tests for the ACL unevaluable-condition fixture (spec §6.1.1, #100).

SECURITY golden test. A condition that CANNOT BE EVALUATED is not the same as a
condition that is FALSE, and the rule's ``effect`` decides what the difference
means: an unevaluable condition MUST resolve the rule toward refusing access —
a ``deny`` rule takes effect and the call is denied, an ``allow`` rule does not
match and MUST NOT grant. The emitted ``AuditEntry`` MUST carry a non-null
``handler_error`` in both directions.

Fixture transition (apcore#100): the committed
``conformance/fixtures/acl_handler_error.json`` still pins the pre-v1.22.0
behaviour, under a case named ``throwing_handler_does_not_flip_default_allow_to_deny_unsafely``
that expects a ``deny`` rule with a crashing handler to let the call through.
Spec v1.22.0 §6.1.1 reverses exactly that, and the corrected fixture is staged
in the spec repo at ``planning/acl-unevaluable-conditions/staged-fixtures/``
until all three SDK drivers land. That one case is therefore expected to fail
here and carries an ``xfail`` naming the reason; every other case is enforced.
This driver already reads the corrected fixture's shape, so moving the staged
file into ``conformance/fixtures/`` needs no change on this side.
"""

from __future__ import annotations


import pytest

from apcore.acl import ACL, ACLRule, AuditEntry
from apcore.context import Context
from conformance.canonical_fixtures import load_fixture

# Superseded by spec v1.22.0 §6.1.1. Present only in the pre-#100 fixture; the
# corrected fixture replaces it with ``throwing_handler_on_deny_rule_denies``.
_SUPERSEDED_CASE_IDS = {"throwing_handler_does_not_flip_default_allow_to_deny_unsafely"}


def _load_fixture() -> dict:
    """Load the canonical fixture from the apcore spec repo."""
    return load_fixture("acl_handler_error.json")


_FIXTURE = _load_fixture()
_THROWING_KEY: str = _FIXTURE["throwing_condition_key"]
# Only the corrected fixture names an unregistered key. Absent from the old one.
_UNKNOWN_KEY: str | None = _FIXTURE.get("unknown_condition_key")


class _ThrowingConditionHandler:
    """Condition handler whose evaluate() always raises (unevaluable driver)."""

    def evaluate(self, value, context: Context) -> bool:  # noqa: ANN001
        raise RuntimeError("conformance throwing condition handler")


@pytest.fixture(autouse=True)
def _register_throwing_handler():
    """Register the throwing handler for the fixture key, then clean up.

    Nothing is registered for ``unknown_condition_key`` — that key is
    unevaluable *because* no handler resolves it. The teardown also removes it
    defensively, so an unrelated test that registered it cannot make this
    driver silently assert the wrong thing.
    """
    previous = ACL._condition_handlers.get(_THROWING_KEY)
    ACL.register_condition(_THROWING_KEY, _ThrowingConditionHandler())
    unknown_previous = ACL._condition_handlers.pop(_UNKNOWN_KEY, None) if _UNKNOWN_KEY else None
    try:
        yield
    finally:
        if previous is not None:
            ACL._condition_handlers[_THROWING_KEY] = previous
        else:
            ACL._condition_handlers.pop(_THROWING_KEY, None)
        if _UNKNOWN_KEY and unknown_previous is not None:
            ACL._condition_handlers[_UNKNOWN_KEY] = unknown_previous


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


def _referenced_condition_keys(raw_rules: list[dict]) -> set[str]:
    keys: set[str] = set()
    for rule in raw_rules:
        keys.update(rule.get("conditions") or {})
    return keys


def _case_params() -> list:
    params = []
    for case in _FIXTURE["test_cases"]:
        marks = []
        if case["id"] in _SUPERSEDED_CASE_IDS:
            marks.append(
                pytest.mark.xfail(
                    strict=True,
                    reason=(
                        "Fixture pins pre-v1.22.0 behaviour: a deny rule whose condition handler "
                        "raises used to fall through to default_effect: allow. PROTOCOL_SPEC "
                        "§6.1.1 (#100) now makes the rule take effect. The corrected fixture is "
                        "staged in the spec repo and lands once all three SDK drivers do."
                    ),
                )
            )
        params.append(pytest.param(case, id=case["id"], marks=marks))
    return params


@pytest.mark.parametrize("case", _case_params())
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

    assert decision is case["expected"], f"expected decision {case['expected']}, got {decision}"

    assert len(captured) == 1, f"expected exactly one audit entry, got {len(captured)}"
    entry = captured[0]
    if case["expected_audit_handler_error_present"]:
        assert entry.handler_error is not None, "expected AuditEntry.handler_error to be non-null"
        # Whichever key the case exercises must be the one named in the audit.
        for key in _referenced_condition_keys(case["rules"]):
            assert key in entry.handler_error
    else:
        assert entry.handler_error is None, "expected AuditEntry.handler_error to be null"
