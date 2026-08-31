"""Cross-language driver for ``acl_argument_scoped_approval.json``.

PROTOCOL_SPEC §6.1.6 / §6.1.7 / §6.1.8 / §6.8.1 (spec v1.28.0, apcore#108).

An ACL rule answers two independent questions — may this caller reach this
target at all, and must *this particular call* be put to a human first. The
orthogonal ``approval`` field carries the second, and the built-in
structure-only ``arguments`` condition decides whether a rule matches this call.

The two cases worth reading before the rest are
``no_projection_must_not_grant_via_an_empty_stand_in`` and
``no_projection_makes_a_deny_rule_take_effect``: they bracket the same
fail-open bug from both directions. Substituting an empty key set for an absent
projection makes ``has_none_of`` vacuously satisfied, so an ``allow`` rule
grants for a call whose arguments were never seen — and leaves ``has_key``
unsatisfied, so a ``deny`` rule fails to take effect. Only the UNEVALUABLE
reading (§6.1.8 rule 1) refuses in both.
"""

from __future__ import annotations

from typing import Any

import pytest

from apcore.acl import ACL, ACLRule, AuditEntry
from apcore.context import Context, GovernanceProjection, Identity

from .canonical_fixtures import fixtures_dir, load_fixture

FIXTURE = "acl_argument_scoped_approval.json"


def _present() -> bool:
    return (fixtures_dir() / FIXTURE).is_file()


# The fixture lands in the spec repo one push after this driver, so that
# `check_driver_coverage.py --strict` has a driver to find for it. Until then
# the module skips and names the unexercised fixture — "not verified", never
# "passed".
pytestmark = pytest.mark.skipif(
    not _present(), reason=f"{FIXTURE} not in the spec repo yet (spec v1.28.0, #108)"
)


def _cases() -> list[dict[str, Any]]:
    return load_fixture(FIXTURE)["test_cases"] if _present() else []


def _build(case: dict[str, Any]) -> tuple[ACL, list[AuditEntry]]:
    entries: list[AuditEntry] = []
    rules = [
        ACLRule(
            callers=list(r["callers"]),
            targets=list(r["targets"]),
            effect=r["effect"],
            conditions=r.get("conditions"),
            **({"approval": r["approval"]} if "approval" in r else {}),
        )
        for r in case["rules"]
    ]
    acl = ACL(rules=rules, default_effect=case["default_effect"], audit_logger=entries.append)
    return acl, entries


def _context(case: dict[str, Any]) -> Context:
    """A context whose projection is present exactly when the case supplies one.

    ``arguments: null`` means NO PROJECTION AT ALL (§6.1.8 rule 1), which is a
    different case from an empty projection — so the field stays None rather
    than being set to ``GovernanceProjection.of({})``. The context itself is
    always present: §6.5's "conditions but no context" non-match is a separate
    rule this fixture is not about.
    """
    ctx = Context(trace_id="t-108", caller_id=case["caller_id"], identity=Identity(id="u", roles=("dev",)))
    if case["arguments"] is not None:
        ctx.governance_projection = GovernanceProjection.of(case["arguments"])
    return ctx


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["id"])
def test_argument_scoped_approval(case: dict[str, Any]) -> None:
    acl, entries = _build(case)
    ctx = _context(case)
    note = case["note"]

    decision = acl.check_access(case["caller_id"], case["target_id"], ctx)
    assert decision.access == case["expected_access"], note
    assert decision.approval_required is case["expected_approval_required"], note
    assert decision.matched_rule_index == case["expected_matched_rule_index"], note

    # §6.3.1: handler_error is non-null IF AND ONLY IF a condition was
    # unevaluable. Read before the legacy call below, which emits its own entry.
    assert len(entries) == 1, f"{note}\n  check_access must emit exactly one audit entry"
    entry = entries[0]

    # §6.8.1: the legacy boolean fails closed on an approval requirement.
    assert acl.check(case["caller_id"], case["target_id"], _context(case)) is case["expected_legacy_check"], note
    assert (entry.handler_error is not None) is case["expected_audit_handler_error_present"], (
        f"{note}\n  handler_error was {entry.handler_error!r}"
    )
    assert entry.approval_required is case["expected_approval_required"], note

    # §6.1.8 closing paragraph: cases 2-4 are decidable with no context and no
    # handler, so validate_rules() must surface them at deploy time.
    all_findings = acl.validate_rules()
    expected_path = case["expected_validation_finding_path"]
    # §6.1.8 rule 3: every faulty predicate is reported, so a case may pin the
    # exact finding set rather than the presence of one.
    expected_paths = case.get("expected_validation_finding_paths")
    if expected_paths is not None:
        assert [f.condition_path for f in all_findings] == expected_paths, note
        for finding in all_findings:
            assert finding.sync_resolvable is False, note
            assert finding.async_resolvable is False, note
    elif expected_path:
        findings = [f for f in all_findings if f.condition_path == expected_path]
        assert findings, f"{note}\n  validate_rules() reported no finding at '{expected_path}': {all_findings}"
        assert findings[0].sync_resolvable is False, note
        assert findings[0].async_resolvable is False, note
    else:
        assert all_findings == (), f"{note}\n  unexpected findings: {all_findings}"


def test_deny_plus_approval_is_rejected_at_every_entry_point() -> None:
    """§6.1.6 rule 3 — the meaningless combination cannot get in by any door."""
    from apcore.errors import ACLRuleError

    with pytest.raises(ACLRuleError):
        ACLRule(callers=["*"], targets=["x.y"], effect="deny", approval="required")
