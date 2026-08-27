"""Unevaluable ACL conditions, load-time validation, and ACL introspection.

Covers PROTOCOL_SPEC v1.22.0 §6.1.1 / §6.1.2 / §6.1.3 / §6.3 / §6.3.1 / §6.5
(apcore#100) and v1.23.0 §6.8 (apcore#101).

The defect these lock down: ``evaluate_conditions`` returned a plain boolean, so
"a handler answered no" and "no answer was obtainable" arrived at the rule loop
identically, and both meant *this rule does not match*. That is safe in one
direction only — an ``allow`` rule that cannot evaluate its condition does not
grant, but a ``deny`` rule that cannot evaluate its condition did not block.
A single misspelled key (``role:`` for ``roles:``) turned a rule its author
believed was blocking into decoration.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import pytest

from apcore.acl import ACL, ACLRule, AuditEntry, ConditionOutcome, ConditionValidationFinding
from apcore.context import Context, Identity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(roles: list[str] | None = None, identity_type: str = "user") -> Context:
    return Context.create(identity=Identity(id="u", type=identity_type, roles=tuple(roles or ())))


def _acl(
    conditions: dict[str, Any] | None,
    *,
    effect: str = "deny",
    default_effect: str = "allow",
) -> tuple[ACL, list[AuditEntry]]:
    captured: list[AuditEntry] = []
    acl = ACL(
        rules=[ACLRule(callers=["*"], targets=["*"], effect=effect, conditions=conditions)],
        default_effect=default_effect,
        audit_logger=captured.append,
    )
    return acl, captured


class _Raising:
    def evaluate(self, value: Any, context: Context) -> bool:
        raise RuntimeError("boom")


class _Suspending:
    async def evaluate(self, value: Any, context: Context) -> bool:
        await asyncio.sleep(0)
        return True


class _Answers:
    def __init__(self, answer: bool) -> None:
        self._answer = answer

    def evaluate(self, value: Any, context: Context) -> bool:
        return self._answer


@pytest.fixture
def registered():
    """Register condition handlers for the duration of one test."""
    keys: list[str] = []
    async_keys: list[str] = []

    def _register(key: str, handler: Any, *, asynchronous: bool = False) -> str:
        if asynchronous:
            ACL.register_async_condition(key, handler)
            async_keys.append(key)
        else:
            ACL.register_condition(key, handler)
            keys.append(key)
        return key

    try:
        yield _register
    finally:
        for key in keys:
            ACL._condition_handlers.pop(key, None)
        for key in async_keys:
            ACL._async_condition_handlers.pop(key, None)


# ---------------------------------------------------------------------------
# §6.1.1 — the effect rule
# ---------------------------------------------------------------------------


class TestUnevaluableResolvesTowardRefusingAccess:
    """A `deny` rule with an unevaluable condition MATCHES and DENIES."""

    def test_unknown_key_on_deny_rule_denies_over_default_allow(self) -> None:
        """The misspelled-key case #100 was opened for."""
        acl, captured = _acl({"role": ["admin"]}, effect="deny", default_effect="allow")
        assert acl.check("caller", "target", _ctx()) is False
        assert captured[0].decision == "deny"
        assert captured[0].reason == "rule_match"
        assert captured[0].matched_rule_index == 0

    def test_raising_handler_on_deny_rule_denies_over_default_allow(self, registered) -> None:
        key = registered("_t_raises", _Raising())
        acl, captured = _acl({key: True}, effect="deny", default_effect="allow")
        assert acl.check("caller", "target", _ctx()) is False
        assert captured[0].decision == "deny"

    def test_suspending_handler_on_deny_rule_denies_over_default_allow(self, registered) -> None:
        key = registered("_t_suspends", _Suspending())
        acl, captured = _acl({key: True}, effect="deny", default_effect="allow")
        assert acl.check("caller", "target", _ctx()) is False
        assert captured[0].decision == "deny"

    def test_unknown_key_on_allow_rule_still_does_not_grant(self) -> None:
        acl, captured = _acl({"role": ["admin"]}, effect="allow", default_effect="deny")
        assert acl.check("caller", "target", _ctx()) is False
        assert captured[0].reason == "default_effect"
        assert captured[0].matched_rule_index is None

    def test_unevaluable_allow_rule_falls_through_to_a_later_rule(self, registered) -> None:
        """An `allow` rule that cannot evaluate MUST NOT match — evaluation continues."""
        key = registered("_t_raises2", _Raising())
        captured: list[AuditEntry] = []
        acl = ACL(
            rules=[
                ACLRule(callers=["*"], targets=["*"], effect="allow", conditions={key: True}),
                ACLRule(callers=["*"], targets=["*"], effect="allow", description="fallback"),
            ],
            default_effect="deny",
            audit_logger=captured.append,
        )
        assert acl.check("caller", "target", _ctx()) is True
        assert captured[0].matched_rule_index == 1
        assert captured[0].matched_rule == "fallback"

    def test_an_unevaluable_condition_never_raises_out_of_check(self, registered) -> None:
        """§6.1.1 rule 4: check() still returns a boolean."""
        key = registered("_t_raises3", _Raising())
        acl, _ = _acl({key: True}, effect="deny")
        assert isinstance(acl.check("caller", "target", _ctx()), bool)

    async def test_the_effect_rule_holds_on_the_async_path(self, registered) -> None:
        key = registered("_t_raises_async", _Raising())
        acl, captured = _acl({key: True}, effect="deny", default_effect="allow")
        assert await acl.async_check("caller", "target", _ctx()) is False
        assert captured[0].handler_error is not None

    def test_an_ordinary_false_condition_on_a_deny_rule_still_does_not_match(self, registered) -> None:
        """The counterpart guarantee: UNSATISFIED must NOT be promoted to a deny."""
        key = registered("_t_false", _Answers(False))
        acl, captured = _acl({key: True}, effect="deny", default_effect="allow")
        assert acl.check("caller", "target", _ctx()) is True
        assert captured[0].reason == "default_effect"
        assert captured[0].handler_error is None


# ---------------------------------------------------------------------------
# §6.1.1 — three-valued composition through AND, $or and $not
# ---------------------------------------------------------------------------


class TestCompoundComposition:
    def test_and_an_outright_no_wins_over_an_unevaluable_sibling(self, registered) -> None:
        """AND: any child UNSATISFIED -> UNSATISFIED, even beside an unevaluable one."""
        key = registered("_t_raises4", _Raising())
        # ``roles`` is evaluated first and answers "no", short-circuiting before
        # the raising handler is ever reached.
        acl, captured = _acl({"roles": ["admin"], key: True}, effect="deny", default_effect="allow")
        assert acl.check("caller", "target", _ctx(roles=["viewer"])) is True
        # Short-circuited child was never evaluated, so it records no diagnostic.
        assert captured[0].handler_error is None

    def test_and_unevaluable_first_then_no_is_still_unsatisfied(self, registered) -> None:
        """The decision is identical whichever order the keys are reached in."""
        key = registered("_t_raises5", _Raising())
        acl, captured = _acl({key: True, "roles": ["admin"]}, effect="deny", default_effect="allow")
        assert acl.check("caller", "target", _ctx(roles=["viewer"])) is True
        # Only the diagnostic differs: this key WAS reached, so it is reported.
        assert captured[0].handler_error is not None

    def test_and_unevaluable_with_all_others_satisfied_is_unevaluable(self, registered) -> None:
        key = registered("_t_raises6", _Raising())
        acl, _ = _acl({"roles": ["admin"], key: True}, effect="deny", default_effect="allow")
        assert acl.check("caller", "target", _ctx(roles=["admin"])) is False

    def test_or_an_outright_yes_wins_over_an_unevaluable_sibling(self, registered) -> None:
        key = registered("_t_raises7", _Raising())
        acl, _ = _acl(
            {"$or": [{"roles": ["admin"]}, {key: True}]},
            effect="allow",
            default_effect="deny",
        )
        assert acl.check("caller", "target", _ctx(roles=["admin"])) is True

    def test_or_no_yes_and_an_unevaluable_child_is_unevaluable(self, registered) -> None:
        key = registered("_t_raises8", _Raising())
        acl, captured = _acl(
            {"$or": [{"roles": ["admin"]}, {key: True}]},
            effect="deny",
            default_effect="allow",
        )
        assert acl.check("caller", "target", _ctx(roles=["viewer"])) is False
        assert captured[0].handler_error is not None

    def test_or_all_children_unsatisfied_is_unsatisfied(self) -> None:
        acl, captured = _acl(
            {"$or": [{"roles": ["admin"]}, {"identity_types": ["service"]}]},
            effect="deny",
            default_effect="allow",
        )
        assert acl.check("caller", "target", _ctx(roles=["viewer"])) is True
        assert captured[0].handler_error is None

    def test_not_of_unevaluable_is_unevaluable_not_satisfied(self, registered) -> None:
        """The bypass §6.1.1 closes one nesting level down.

        If ``$not`` negated "no answer" into "yes", a misspelled key inside a
        ``$not`` would SATISFY the very rule it was meant to gate.
        """
        acl, captured = _acl({"$not": {"mispelled": True}}, effect="allow", default_effect="deny")
        assert acl.check("caller", "target", _ctx()) is False, "an unevaluable $not must not grant"
        assert captured[0].handler_error is not None

    def test_not_of_unevaluable_on_a_deny_rule_denies(self) -> None:
        acl, _ = _acl({"$not": {"mispelled": True}}, effect="deny", default_effect="allow")
        assert acl.check("caller", "target", _ctx()) is False

    def test_not_of_satisfied_is_unsatisfied(self) -> None:
        acl, captured = _acl({"$not": {"roles": ["admin"]}}, effect="allow", default_effect="deny")
        assert acl.check("caller", "target", _ctx(roles=["admin"])) is False
        assert captured[0].handler_error is None

    def test_not_of_unsatisfied_is_satisfied(self) -> None:
        acl, _ = _acl({"$not": {"roles": ["admin"]}}, effect="allow", default_effect="deny")
        assert acl.check("caller", "target", _ctx(roles=["viewer"])) is True

    def test_not_of_empty_object_is_unsatisfied_not_unevaluable(self) -> None:
        """§6.1: `$not: {}` MUST evaluate to false, and it is a real answer."""
        acl, captured = _acl({"$not": {}}, effect="allow", default_effect="deny")
        assert acl.check("caller", "target", _ctx()) is False
        assert captured[0].handler_error is None

    def test_nested_or_inside_not_propagates_unevaluable(self, registered) -> None:
        key = registered("_t_raises9", _Raising())
        acl, _ = _acl(
            {"$not": {"$or": [{key: True}]}},
            effect="deny",
            default_effect="allow",
        )
        assert acl.check("caller", "target", _ctx()) is False

    async def test_async_not_of_unevaluable_is_unevaluable(self) -> None:
        acl, captured = _acl({"$not": {"mispelled": True}}, effect="allow", default_effect="deny")
        assert await acl.async_check("caller", "target", _ctx()) is False
        assert captured[0].handler_error is not None

    async def test_async_or_no_yes_and_an_unevaluable_child_is_unevaluable(self, registered) -> None:
        key = registered("_t_raises10", _Raising())
        acl, _ = _acl(
            {"$or": [{"roles": ["admin"]}, {key: True}]},
            effect="deny",
            default_effect="allow",
        )
        assert await acl.async_check("caller", "target", _ctx(roles=["viewer"])) is False

    def test_or_short_circuits_before_an_unevaluable_sibling_records_nothing(self, registered) -> None:
        """A child skipped by a legitimate short-circuit MUST NOT set handler_error."""
        key = registered("_t_raises11", _Raising())
        acl, captured = _acl(
            {"$or": [{"roles": ["admin"]}, {key: True}]},
            effect="allow",
            default_effect="deny",
        )
        assert acl.check("caller", "target", _ctx(roles=["admin"])) is True
        assert captured[0].handler_error is None


# ---------------------------------------------------------------------------
# §6.3.1 — handler_error aggregation
# ---------------------------------------------------------------------------


class TestHandlerErrorAggregation:
    def test_multiple_unevaluable_keys_are_listed_lexicographically(self) -> None:
        """§6.1.1 rule 2: every unevaluable condition, ordered by KEY, joined by '; '.

        Lexicographic rather than evaluation order because Python dicts keep
        insertion order while serde_json's map is sorted — "the first one
        encountered" would put a different key in the audit log per SDK.
        """
        acl, captured = _acl({"zeta": True, "alpha": True, "mu": True}, effect="allow", default_effect="deny")
        assert acl.check("caller", "target", _ctx()) is False
        message = captured[0].handler_error
        assert message is not None
        parts = message.split("; ")
        assert [p.split(":")[0] for p in parts] == ["alpha", "mu", "zeta"]

    def test_each_part_names_the_key_and_the_reason(self, registered) -> None:
        key = registered("_t_raises12", _Raising())
        acl, captured = _acl({key: True}, effect="allow", default_effect="deny")
        acl.check("caller", "target", _ctx())
        assert captured[0].handler_error == f"{key}: RuntimeError: boom"

    def test_unevaluable_keys_across_several_rules_are_all_reported(self) -> None:
        captured: list[AuditEntry] = []
        acl = ACL(
            rules=[
                ACLRule(callers=["*"], targets=["*"], effect="allow", conditions={"zeta": True}),
                ACLRule(callers=["*"], targets=["*"], effect="allow", conditions={"alpha": True}),
            ],
            default_effect="deny",
            audit_logger=captured.append,
        )
        assert acl.check("caller", "target", _ctx()) is False
        assert captured[0].handler_error is not None
        assert captured[0].handler_error.startswith("alpha:")
        assert "zeta:" in captured[0].handler_error

    def test_handler_error_does_not_leak_between_checks(self) -> None:
        acl, captured = _acl({"mispelled": True}, effect="allow", default_effect="deny")
        acl.check("caller", "target", _ctx())
        acl.rules  # noqa: B018 - accessor is a pure read, must not disturb state
        acl2, captured2 = _acl(None, effect="allow", default_effect="deny")
        acl2.check("caller", "target", _ctx())
        assert captured[0].handler_error is not None
        assert captured2[0].handler_error is None

    def test_the_suspending_diagnostic_points_at_async_check(self, registered) -> None:
        key = registered("_t_suspends2", _Suspending())
        acl, captured = _acl({key: True}, effect="allow", default_effect="deny")
        acl.check("caller", "target", _ctx())
        assert captured[0].handler_error is not None
        assert "async_check()" in captured[0].handler_error


# ---------------------------------------------------------------------------
# §6.1.1 rule 3 / §6.1.2 rule 2 — warnings
# ---------------------------------------------------------------------------


class TestWarnings:
    def test_evaluation_warns_naming_key_index_and_effect(self, caplog: pytest.LogCaptureFixture) -> None:
        acl, _ = _acl({"mispelled": True}, effect="deny", default_effect="allow")
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            acl.check("caller", "target", _ctx())
        joined = "\n".join(r.message for r in caplog.records)
        assert "'mispelled'" in joined
        assert "effect=deny" in joined
        assert "rule 0" in joined

    def test_construction_warns_naming_index_key_and_effect(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            ACL(
                rules=[
                    ACLRule(callers=["*"], targets=["*"], effect="allow"),
                    ACLRule(callers=["*"], targets=["*"], effect="deny", conditions={"mispelled": True}),
                ],
                default_effect="deny",
            )
        joined = "\n".join(r.message for r in caplog.records)
        assert "ACL rule 1" in joined
        assert "effect=deny" in joined
        assert "'mispelled'" in joined

    def test_construction_does_not_raise_for_an_unregistered_key(self) -> None:
        """§6.1.2 rule 1: loading MUST NOT fail — handler registration is runtime."""
        acl = ACL(rules=[ACLRule(callers=["*"], targets=["*"], effect="deny", conditions={"nope": 1})])
        assert len(acl.rules) == 1

    def test_load_warns_but_succeeds(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        path = tmp_path / "acl.yaml"
        path.write_text(
            "default_effect: allow\n"
            "rules:\n"
            "  - callers: ['*']\n"
            "    targets: ['*']\n"
            "    effect: deny\n"
            "    conditions:\n"
            "      mispelled: true\n",
            encoding="utf-8",
        )
        with caplog.at_level(logging.WARNING):
            acl = ACL.load(str(path))
        assert len(acl.rules) == 1
        joined = "\n".join(r.message for r in caplog.records)
        assert "ACL rule 0" in joined and "'mispelled'" in joined

    def test_add_rule_warns_naming_index_zero(self, caplog: pytest.LogCaptureFixture) -> None:
        """§6.1.2 rule 4: runtime insertion is an entry point that MUST be covered."""
        acl = ACL(default_effect="allow")
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            acl.add_rule(callers=["*"], targets=["*"], effect="deny", conditions={"mispelled": True})
        joined = "\n".join(r.message for r in caplog.records)
        assert "ACL rule 0" in joined
        assert "effect=deny" in joined
        assert "'mispelled'" in joined

    def test_add_rule_does_not_warn_for_a_registered_key(self, caplog: pytest.LogCaptureFixture) -> None:
        acl = ACL(default_effect="allow")
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            acl.add_rule(callers=["*"], targets=["*"], effect="deny", conditions={"roles": ["admin"]})
        assert not [r for r in caplog.records if "condition key" in r.message]

    def test_nested_keys_are_warned_about(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            ACL(
                rules=[
                    ACLRule(
                        callers=["*"],
                        targets=["*"],
                        effect="deny",
                        conditions={"$or": [{"roles": ["a"]}, {"$not": {"deeply_mispelled": True}}]},
                    )
                ]
            )
        joined = "\n".join(r.message for r in caplog.records)
        assert "'deeply_mispelled'" in joined

    def test_a_conditional_rule_skipped_for_want_of_context_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        """§6.5: not an unevaluable condition, but the consequence must be visible."""
        acl, captured = _acl({"roles": ["admin"]}, effect="deny", default_effect="allow")
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            assert acl.check("caller", "target", None) is True
        joined = "\n".join(r.message for r in caplog.records)
        assert "no context" in joined
        assert "effect=deny" in joined
        # A missing context is NOT unevaluable, so no handler_error is recorded.
        assert captured[0].handler_error is None


# ---------------------------------------------------------------------------
# §6.1.2 rule 3 / §6.1.3 — validate_conditions()
# ---------------------------------------------------------------------------


class TestValidateConditions:
    def test_empty_when_every_key_resolves(self) -> None:
        acl = ACL(
            rules=[
                ACLRule(callers=["*"], targets=["*"], effect="deny", conditions={"roles": ["a"]}),
                ACLRule(callers=["*"], targets=["*"], effect="allow"),
            ]
        )
        assert acl.validate_conditions() == ()

    def test_reports_rule_index_key_and_effect(self) -> None:
        acl = ACL(
            rules=[
                ACLRule(callers=["*"], targets=["*"], effect="allow"),
                ACLRule(callers=["*"], targets=["*"], effect="deny", conditions={"mispelled": True}),
            ]
        )
        assert acl.validate_conditions() == (
            ConditionValidationFinding(
                rule_index=1,
                condition_key="mispelled",
                effect="deny",
                sync_registered=False,
                async_registered=False,
            ),
        )

    def test_reports_keys_nested_in_compound_operators(self) -> None:
        acl = ACL(
            rules=[
                ACLRule(
                    callers=["*"],
                    targets=["*"],
                    effect="deny",
                    conditions={"$or": [{"roles": ["a"]}, {"$not": {"nested_typo": True}}]},
                )
            ]
        )
        assert [f.condition_key for f in acl.validate_conditions()] == ["nested_typo"]

    def test_the_builtin_compound_operators_are_never_findings(self) -> None:
        acl = ACL(rules=[ACLRule(callers=["*"], targets=["*"], effect="deny", conditions={"$not": {"roles": ["a"]}})])
        assert acl.validate_conditions() == ()

    def test_an_async_only_key_is_reported_with_both_flags(self, registered) -> None:
        """§6.1.3 rule 2: a finding is emitted whenever sync_registered is false.

        check() consults only the sync registry, so an async-only key is a
        working condition under async_check() and an UNEVALUABLE one under
        check(). One collapsed boolean cannot express that.
        """
        key = registered("_t_async_only", _Answers(True), asynchronous=True)
        acl = ACL(rules=[ACLRule(callers=["*"], targets=["*"], effect="deny", conditions={key: True})])
        findings = acl.validate_conditions()
        assert len(findings) == 1
        assert findings[0].sync_registered is False
        assert findings[0].async_registered is True

    def test_a_sync_only_key_resolves_on_both_paths(self, registered) -> None:
        """The built-ins are sync-only and resolve on async_check via fallback."""
        key = registered("_t_sync_only", _Answers(True))
        acl = ACL(rules=[ACLRule(callers=["*"], targets=["*"], effect="deny", conditions={key: True})])
        assert acl.validate_conditions() == ()

    def test_an_async_only_key_really_is_unevaluable_on_the_sync_path(self, registered) -> None:
        """The finding is not cosmetic: it predicts a real behavioural difference."""
        key = registered("_t_async_only2", _Answers(True), asynchronous=True)
        acl, _ = _acl({key: True}, effect="deny", default_effect="allow")
        assert acl.check("caller", "target", _ctx()) is False, "sync check(): unevaluable -> deny rule fires"
        assert asyncio.run(acl.async_check("caller", "target", _ctx())) is False, "async_check(): condition satisfied"

    def test_rules_without_conditions_are_never_reported(self) -> None:
        acl = ACL(rules=[ACLRule(callers=["*"], targets=["*"], effect="deny")])
        assert acl.validate_conditions() == ()

    def test_is_a_pure_read(self) -> None:
        captured: list[AuditEntry] = []
        acl = ACL(
            rules=[ACLRule(callers=["*"], targets=["*"], effect="deny", conditions={"mispelled": True})],
            audit_logger=captured.append,
        )
        before = acl.rules
        acl.validate_conditions()
        assert acl.rules == before
        assert acl.default_effect == "deny"
        assert captured == [], "validate_conditions() MUST NOT emit an audit event"

    def test_reflects_a_key_registered_after_construction(self, registered) -> None:
        acl = ACL(rules=[ACLRule(callers=["*"], targets=["*"], effect="deny", conditions={"_t_late": True})])
        assert len(acl.validate_conditions()) == 1
        registered("_t_late", _Answers(True))
        assert acl.validate_conditions() == ()

    def test_findings_are_ordered_by_rule_then_by_key_position(self) -> None:
        acl = ACL(
            rules=[
                ACLRule(callers=["*"], targets=["*"], effect="deny", conditions={"b_typo": 1, "a_typo": 1}),
                ACLRule(callers=["*"], targets=["*"], effect="allow", conditions={"c_typo": 1}),
            ]
        )
        assert [(f.rule_index, f.condition_key) for f in acl.validate_conditions()] == [
            (0, "b_typo"),
            (0, "a_typo"),
            (1, "c_typo"),
        ]


# ---------------------------------------------------------------------------
# §6.8 — ACL introspection
# ---------------------------------------------------------------------------


class TestIntrospection:
    def test_default_effect_is_readable(self) -> None:
        assert ACL(default_effect="allow").default_effect == "allow"
        assert ACL(default_effect="deny").default_effect == "deny"

    def test_default_effect_matches_what_check_applies(self) -> None:
        for effect, expected in (("allow", True), ("deny", False)):
            acl = ACL(default_effect=effect)
            assert acl.default_effect == effect
            assert acl.check("caller", "target") is expected

    def test_rules_are_returned_in_definition_order(self) -> None:
        first = ACLRule(callers=["a"], targets=["*"], effect="allow", description="first")
        second = ACLRule(callers=["b"], targets=["*"], effect="deny", description="second")
        acl = ACL(rules=[first, second])
        assert [r.description for r in acl.rules] == ["first", "second"]

    def test_rules_does_not_hand_out_a_mutable_reference(self) -> None:
        """§6.8 rule 3: the caller must not be able to mutate the ACL's own list."""
        acl = ACL(rules=[ACLRule(callers=["a"], targets=["*"], effect="allow")])
        snapshot = acl.rules
        assert isinstance(snapshot, tuple)
        acl.add_rule(callers=["b"], targets=["*"], effect="deny")
        assert len(snapshot) == 1, "an earlier snapshot must not observe later mutations"
        assert len(acl.rules) == 2

    def test_accessors_reflect_a_reload(self, tmp_path: Path) -> None:
        """§6.8 rule 4: both read the live object, never a cached parse."""
        path = tmp_path / "acl.yaml"
        path.write_text(
            "default_effect: deny\nrules:\n  - callers: ['a']\n    targets: ['*']\n    effect: allow\n",
            encoding="utf-8",
        )
        acl = ACL.load(str(path))
        assert acl.default_effect == "deny"
        assert len(acl.rules) == 1

        path.write_text(
            "default_effect: allow\n"
            "rules:\n"
            "  - callers: ['a']\n"
            "    targets: ['*']\n"
            "    effect: allow\n"
            "  - callers: ['b']\n"
            "    targets: ['*']\n"
            "    effect: deny\n",
            encoding="utf-8",
        )
        acl.reload()
        assert acl.default_effect == "allow"
        assert len(acl.rules) == 2

    def test_accessors_are_pure_reads(self) -> None:
        captured: list[AuditEntry] = []
        acl = ACL(rules=[ACLRule(callers=["*"], targets=["*"], effect="allow")], audit_logger=captured.append)
        acl.default_effect  # noqa: B018
        acl.rules  # noqa: B018
        assert captured == [], "an accessor MUST NOT emit an audit event"

    def test_accessors_are_not_underscore_prefixed(self) -> None:
        """§6.8 rule 1 + api-surface-conventions §3: a public path, not a private name."""
        assert isinstance(type(ACL).__getattribute__(ACL, "default_effect"), property)
        assert isinstance(type(ACL).__getattribute__(ACL, "rules"), property)


# ---------------------------------------------------------------------------
# ConditionOutcome as a handler return value
# ---------------------------------------------------------------------------


class TestConditionOutcomeReturnValue:
    def test_a_handler_may_report_unevaluable_itself(self, registered) -> None:
        class _SelfReporting:
            def evaluate(self, value: Any, context: Context) -> ConditionOutcome:
                return ConditionOutcome.UNEVALUABLE

        key = registered("_t_self_unevaluable", _SelfReporting())
        acl, _ = _acl({key: True}, effect="deny", default_effect="allow")
        assert acl.check("caller", "target", _ctx()) is False

    def test_a_handler_returning_the_satisfied_member_is_satisfied(self, registered) -> None:
        class _SelfReporting:
            def evaluate(self, value: Any, context: Context) -> ConditionOutcome:
                return ConditionOutcome.SATISFIED

        key = registered("_t_self_satisfied", _SelfReporting())
        acl, _ = _acl({key: True}, effect="allow", default_effect="deny")
        assert acl.check("caller", "target", _ctx()) is True

    def test_a_plain_bool_keeps_its_historical_meaning(self, registered) -> None:
        key = registered("_t_plain_bool", _Answers(True))
        acl, _ = _acl({key: True}, effect="allow", default_effect="deny")
        assert acl.check("caller", "target", _ctx()) is True
