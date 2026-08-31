"""ACL (Access Control List) types and implementation for apcore.

This module defines the ACLRule dataclass and the ACL class that enforces
pattern-based access control between modules.
"""

from __future__ import annotations

import contextvars
import inspect
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, ClassVar

import yaml

from apcore.acl_handlers import (
    ACLConditionHandler,
    ConditionOutcome,
    ROOT_CONDITION_PATH,
    _as_outcome,
    _ArgumentsHandler,
    _IdentityTypesHandler,
    _MaxCallDepthHandler,
    _NotHandler,
    _NotHandlerAsync,
    _OrHandler,
    _OrHandlerAsync,
    _RolesHandler,
    join_condition_path,
    validate_arguments_condition,
)
from apcore.config import Config
from apcore.context import Context
from apcore.errors import ACLRuleError, ConfigNotFoundError
from apcore.utils.pattern import match_pattern

__all__ = [
    "AccessDecision",
    "ACLRule",
    "AuditEntry",
    "ACL",
    "ConditionOutcome",
    "RuleValidationFinding",
]

_logger = logging.getLogger(__name__)

# Surfaces unevaluable conditions (PROTOCOL_SPEC §6.1.1) into the AuditEntry
# built for the current check() / async_check() invocation, keyed by condition
# **path** (§6.1.4) so the audit message can be ordered lexicographically as
# §6.1.1 rule 2 requires. By path and not by key, because a key may occur at
# several positions in a nested $or / $not tree, which leaves ordering by key
# undefined. Installed fresh at the start of each public check so nested calls
# do not leak diagnostics across audit entries.
#
# The dict is mutated in place rather than re-``set``: a ContextVar assignment
# made inside a coroutine does not necessarily propagate back to the caller's
# context, while a mutation of the shared mapping always does.
_handler_error_var: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "_apcore_acl_handler_error", default=None
)


# Condition paths for the rule's pattern fields (PROTOCOL_SPEC §6.1.4).
_CALLERS_PATH = "callers"
_TARGETS_PATH = "targets"


def _record_handler_error(path: str, reason: str) -> None:
    """Record an unevaluable condition for the in-flight check(), if any.

    Keyed by §6.1.4 condition path. The first reason recorded for a path wins:
    the same path can be reached by several rules in one check, and a stable
    diagnostic beats a last-writer one. A no-op when no check is in flight, so
    the evaluator stays usable directly.
    """
    errors = _handler_error_var.get()
    if errors is not None and path not in errors:
        errors[path] = f"{path}: {reason}"


def _handler_error_message() -> str | None:
    """Render the recorded diagnostics for the AuditEntry, or None.

    PROTOCOL_SPEC §6.1.1 rule 2: every unevaluable condition MUST be reported,
    ordered **lexicographically by condition path** and separated by ``"; "``.
    By path rather than evaluation order because the two differ across languages
    (``serde_json``'s map is ordered; Python dicts and JS objects preserve
    insertion order), and by path rather than key because a nested ``$or`` may
    carry one key at several positions.
    """
    errors = _handler_error_var.get()
    if not errors:
        return None
    return "; ".join(errors[path] for path in sorted(errors))


#: The two values PROTOCOL_SPEC §6.1.6 gives the ``approval`` rule field.
APPROVAL_REQUIRED = "required"
APPROVAL_NOT_REQUIRED = "not_required"
_APPROVAL_VALUES: frozenset[str] = frozenset({APPROVAL_REQUIRED, APPROVAL_NOT_REQUIRED})


def _validate_approval(approval: Any, effect: Any, *, where: str) -> None:
    """Enforce §6.1.6 on one rule's ``approval`` / ``effect`` pair.

    Authorization and the approval requirement are two independent results, so
    a rule carries both — but ``approval: required`` on a ``deny`` rule names a
    state that means nothing ("denied *and* put it to a human"), and acting on
    half of a governance rule is the failure mode §6.1.5 was written to end.

    Args:
        approval: The rule's ``approval`` value.
        effect: The rule's ``effect`` value.
        where: How to name the rule in the message — ``"Rule 3"`` from the
            loader, ``"ACLRule"`` from direct construction.

    Raises:
        ACLRuleError: On an unknown value or on ``required`` with ``deny``.
    """
    if approval not in _APPROVAL_VALUES:
        raise ACLRuleError(
            f"{where} has invalid approval {approval!r}, must be " f"{APPROVAL_REQUIRED!r} or {APPROVAL_NOT_REQUIRED!r}"
        )
    if approval == APPROVAL_REQUIRED and effect == "deny":
        raise ACLRuleError(
            f"{where} carries approval: {APPROVAL_REQUIRED} on a 'deny' rule. The combination has "
            "no meaning — a refusal is not a question — and PROTOCOL_SPEC §6.1.6 requires it to be "
            "rejected rather than half-applied. Use effect: allow with approval: required to ask a "
            "human, or effect: deny to refuse outright."
        )


@dataclass
class ACLRule:
    """A single access control rule.

    Rules are evaluated in order by the ACL system. Each rule specifies
    caller patterns, target patterns, and an effect (allow/deny).

    ``effect`` and ``approval`` answer two **independent** questions
    (PROTOCOL_SPEC §6.1.6): may this caller reach this target at all, and must
    this particular call be put to a human before it runs. ``approval`` defaults
    to ``"not_required"``, so every rule written before spec v1.28.0 keeps its
    meaning exactly.
    """

    callers: list[str]
    targets: list[str]
    effect: str
    description: str = ""
    conditions: dict[str, Any] | None = None
    approval: str = APPROVAL_NOT_REQUIRED

    def __post_init__(self) -> None:
        """Reject the meaningless ``deny`` + ``approval: required`` pair (§6.1.6).

        Validated on the dataclass rather than only in :meth:`ACL.load` because
        ``ACL(rules=[...])`` and :meth:`ACL.add_rule` never reach the loader's
        parser — the same door §6.1.1 case 5 and §6.1.4.1 exist for. The loader
        still checks first, so a file fault names the rule's index.
        """
        _validate_approval(self.approval, self.effect, where="ACLRule")


#: The complete set of keys an ACL rule may carry (PROTOCOL_SPEC §6.1).
#: Closed on purpose: a key nothing evaluates is otherwise dropped in silence,
#: which widens an ``allow`` rule with no warning (#107). ``approval`` joined
#: the set in spec v1.28.0 (§6.1.6) — adding it was only safe *because* the set
#: was closed first, since an SDK that still dropped unknown keys would read a
#: ``deny``-with-``approval`` rule as a bare rule and act on half of it.
_RULE_KEYS: frozenset[str] = frozenset({"callers", "targets", "effect", "description", "conditions", "approval"})

#: Reserved in earlier revisions of §6.1 and evaluated by no implementation.
#: Rejected like any other unknown key, but named as reserved in the message:
#: an operator who wrote ``actions: ["describe"]`` meant to restrict the rule and
#: is better served by "not implemented" than by "unknown key".
_RESERVED_RULE_KEYS: frozenset[str] = frozenset({"id", "actions", "priority"})


def _reject_unknown_rule_keys(index: int, raw_rule: dict) -> None:
    """Raise ``ACLRuleError`` for any key outside :data:`_RULE_KEYS`."""
    unknown = sorted(set(raw_rule) - _RULE_KEYS)
    if not unknown:
        return
    reserved = [k for k in unknown if k in _RESERVED_RULE_KEYS]
    other = [k for k in unknown if k not in _RESERVED_RULE_KEYS]
    parts = []
    if reserved:
        names = ", ".join(repr(k) for k in reserved)
        parts.append(f"{names} reserved for a future specification version and evaluated " f"by no implementation")
    if other:
        parts.append(f"{', '.join(repr(k) for k in other)} unrecognised")
    raise ACLRuleError(
        f"Rule {index} carries {'; '.join(parts)}. The rule key set is closed "
        f"({', '.join(sorted(_RULE_KEYS))}); a key nothing evaluates would be "
        f"dropped silently and leave the rule wider than written."
    )


@dataclass(frozen=True)
class AuditEntry:
    """Structured record of an ACL check decision."""

    timestamp: str  # ISO 8601
    caller_id: str
    target_id: str
    decision: str  # "allow" or "deny"
    reason: str  # "rule_match", "default_effect", "no_rules"
    matched_rule: str | None = None  # Rule description (immutable snapshot)
    matched_rule_index: int | None = None
    identity_type: str | None = None
    roles: tuple[str, ...] = field(default_factory=tuple)
    call_depth: int | None = None
    trace_id: str | None = None
    # PROTOCOL_SPEC §6.3.1: non-null IF AND ONLY IF a condition was unevaluable
    # (§6.1.1) — no registered handler, the handler raised, or an async handler
    # could not be resolved on the sync check() path. It MUST stay null for an
    # ordinary UNSATISFIED condition: that distinction is what makes "the
    # handler said no" and "no answer was obtainable" tellable apart after the
    # fact. Several unevaluable conditions in one check are joined with "; " in
    # lexicographic order of condition key.
    handler_error: str | None = None
    # PROTOCOL_SPEC §6.3.1: whether the matched rule required this call to be
    # put to a human (§6.1.6). False when no rule matched or the matched rule
    # required none. Added **beside** ``decision`` rather than widening it:
    # ``decision`` is a string downstream consumers parse, and a third value
    # would break every existing parser.
    approval_required: bool = False


@dataclass(frozen=True)
class AccessDecision:
    """The structured result of an ACL check (PROTOCOL_SPEC §6.8.1).

    A boolean can carry authorization but not the second axis of §6.1.6, so the
    structured accessors :meth:`ACL.check_access` and
    :meth:`ACL.async_check_access` return this instead. The boolean entry points
    are kept and unchanged in name.

    Attributes:
        access: ``"allow"`` or ``"deny"`` — the authorization verdict, with the
            same semantics the boolean always had.
        approval_required: Whether **this call** must be put to a human before
            it runs. Only ever true alongside ``access == "allow"``: §6.1.6
            rejects the other combination at load.
        matched_rule_index: Index of the deciding rule in definition order, or
            None when the decision came from ``default_effect``.
        reason: ``"rule_match"``, ``"default_effect"`` or ``"no_rules"`` — which
            branch of §6.3 produced the decision.

    Note:
        ``access == "allow"`` is **not** the legacy boolean. A call that is
        allowed but needs approval makes :meth:`ACL.check` return ``False``
        (§6.8.1), because a non-Executor caller can only read a boolean as "let
        it through". Read :attr:`access` and :attr:`approval_required`
        separately, or call ``check()`` and accept its fail-closed answer.
    """

    access: str
    approval_required: bool = False
    matched_rule_index: int | None = None
    reason: str = "default_effect"


@dataclass(frozen=True)
class RuleValidationFinding:
    """One structural or registry fault found by the §6.1.4 precheck.

    Returned by :meth:`ACL.validate_rules` (PROTOCOL_SPEC §6.1.2 rule 3).

    Attributes:
        rule_index: Index of the offending rule in definition order.
        condition_path: Where the fault sits (§6.1.4) — ``roles``,
            ``$or[1].mispelled``, ``$`` for a non-mapping ``conditions``, or
            ``callers`` / ``targets`` for a malformed pattern field.
        condition_key: The key itself, for readers who do not need the path, or
            None for a fault that has no key — a malformed pattern field, a
            non-mapping ``conditions``, or a malformed ``$or`` element.
        effect: The rule's effect. A finding on a ``deny`` rule is the
            consequential one — that rule now denies every call it matches.
        sync_resolvable: Whether the condition resolves for :meth:`ACL.check`.
            False for a keyless structural fault.
        async_resolvable: Whether it resolves for :meth:`ACL.async_check`.
            False for a keyless structural fault.

    The two flags are reported separately and MUST NOT be collapsed into one
    boolean (§6.1.3). They mean **resolvable on that evaluation path**, not
    "present in that registry": ``async_check()`` falls back to the sync
    registry, so ``async_resolvable`` is the union of both and every built-in
    leaf handler is resolvable on both paths. A finding with
    ``sync_resolvable=False, async_resolvable=True`` is an async-only handler —
    usable under ``async_check()``, unevaluable under ``check()``.
    """

    rule_index: int
    condition_path: str
    condition_key: str | None
    effect: str
    sync_resolvable: bool
    async_resolvable: bool


@dataclass(frozen=True)
class _Fault:
    """An internal precheck finding, before it is bound to a rule index.

    ``key`` is None for a fault that is not attached to a condition key — a
    malformed pattern field, a non-mapping ``conditions``, a malformed ``$or``
    element. The two flags mean "can this fault be resolved by evaluating on
    that path", not "is the key present in that registry": a structural fault
    resolves on neither, which is why they default to False. Reading them as a
    registry lookup would report a malformed ``$or`` *value* as resolvable on
    both paths, since ``$or`` itself has a handler.
    """

    path: str
    key: str | None
    reason: str
    sync_resolvable: bool = False
    async_resolvable: bool = False


class ACL:
    """Access Control List with pattern-based rules and first-match-wins evaluation.

    Implements PROTOCOL_SPEC section 6 for module access control.

    Surface:
        :meth:`check` / :meth:`async_check` — the decision.
        :meth:`add_rule`, :meth:`remove_rule`, :meth:`reload` — mutation.
        :attr:`default_effect`, :attr:`rules` — read-only introspection (§6.8).
        :meth:`validate_rules` — diagnostics for malformed rules and unregistered
        condition keys, to run once handler registration is complete (§6.1.2).

    Thread safety:
        Internally synchronized. All public methods (check, add_rule,
        remove_rule, reload) and the read-only accessors are safe to call
        concurrently. The accessors take the same snapshot the check path does.
    """

    _condition_handlers: ClassVar[dict[str, ACLConditionHandler]] = {
        "identity_types": _IdentityTypesHandler(),
        "roles": _RolesHandler(),
        "max_call_depth": _MaxCallDepthHandler(),
        # PROTOCOL_SPEC §6.1.7: `arguments` is built-in, and registered here
        # beside the other built-ins rather than through a new registration
        # point — `register_condition` writes runtime code into a process-wide
        # registry, and a deployment-registered argument handler is exactly the
        # unauditable host code §7.9.6 rule 2 keeps out of a governance verdict.
        # Being an ordinary registry entry also means §6.1.4's precheck covers
        # it for free: `argument:` written for `arguments:` is an unregistered
        # key, so the rule is unevaluable rather than silently inert.
        "arguments": _ArgumentsHandler(),
    }

    # Async condition handlers: used by _evaluate_conditions_async for compound
    # operators ($or, $not) that need to await sub-condition evaluations.
    # Falls back to _condition_handlers for keys not present here.
    _async_condition_handlers: ClassVar[dict[str, ACLConditionHandler]] = {}

    @classmethod
    def register_condition(cls, key: str, handler: ACLConditionHandler) -> None:
        """Register a condition handler. Replaces existing handler for same key."""
        cls._condition_handlers[key] = handler

    @classmethod
    def register_async_condition(cls, key: str, handler: ACLConditionHandler) -> None:
        """Register an async-aware condition handler used by async_check().

        Async handlers are used instead of the sync handler when the async
        evaluation path is active. This allows compound operators like $or/$not
        to properly await sub-condition handlers.

        Mirrors ``apcore-typescript.ACL.registerAsyncCondition``.
        """
        cls._async_condition_handlers[key] = handler

    # -- Evaluation (PROTOCOL_SPEC §6.1.1) ---------------------------------

    @classmethod
    def _evaluate_conditions(
        cls,
        conditions: dict[str, Any],
        context: Context,
        path_prefix: str = "",
    ) -> ConditionOutcome:
        """Evaluate a ``conditions`` object on the sync path (PROTOCOL_SPEC §6.1.1).

        Returns one of three outcomes, not a boolean. A key whose handler
        answered "no" is ``UNSATISFIED``; a key the implementation cannot answer
        **as written** is ``UNEVALUABLE``. Collapsing the two is the defect
        §6.1.1 exists to prevent: it made a ``deny`` rule with a misspelled
        condition key fail **open**.

        A ``conditions`` object ANDs its keys, so §6.1.1's composition table
        applies: an outright ``UNSATISFIED`` wins even if a sibling was
        unevaluable, and ``UNEVALUABLE`` otherwise propagates. Short-circuiting
        on the decisive ``UNSATISFIED`` child is permitted (and a child skipped
        that way was never evaluated, so it records no diagnostic); short-
        circuiting on an ``UNEVALUABLE`` child is NOT, because a later sibling
        may still produce the decisive answer.

        Structural and registry faults are normally caught by :meth:`_precheck_conditions`
        before this runs (§6.1.4), which is what makes those diagnostics
        deterministic across implementations. The guards here are the same rules
        applied where the evaluator is entered directly.

        Args:
            conditions: The condition object to evaluate.
            context: The execution context.
            path_prefix: §6.1.4 path of the enclosing node, so a nested fault is
                reported as ``$or[1].k`` rather than as a bare key.
        """
        if not isinstance(conditions, dict):
            return cls._malformed_conditions(conditions, path_prefix)

        saw_unevaluable = False
        for key, value in conditions.items():
            outcome = cls._evaluate_condition(key, value, context, join_condition_path(path_prefix, key))
            if outcome is ConditionOutcome.UNSATISFIED:
                # Decisive: an outright "no" wins the AND. Remaining keys are
                # not evaluated, and therefore record nothing.
                return ConditionOutcome.UNSATISFIED
            if outcome is ConditionOutcome.UNEVALUABLE:
                saw_unevaluable = True
        return ConditionOutcome.UNEVALUABLE if saw_unevaluable else ConditionOutcome.SATISFIED

    @classmethod
    def _malformed_conditions(cls, conditions: Any, path_prefix: str = "") -> ConditionOutcome:
        """Classify a non-mapping ``conditions`` value as UNEVALUABLE (§6.1.1 case 5).

        ``ACLRule.conditions`` is annotated ``dict[str, Any] | None``, but the
        annotation binds nobody: ``ACL(rules=[...])`` and ``add_rule()`` build
        rules programmatically and never reach ``ACL.load``'s parser, so a scalar
        or a list arrives here intact. Iterating it raised ``AttributeError``
        straight out of ``check()``, which the ``ACL.check`` contract forbids.

        It is UNEVALUABLE rather than UNSATISFIED because a malformed block is a
        misconfiguration, not a handler answering "no": calling it UNSATISFIED
        would let a ``deny`` rule fall through to ``default_effect``, which is
        the bypass §6.1.1 exists to close.
        """
        path = path_prefix or ROOT_CONDITION_PATH
        type_name = type(conditions).__name__
        _logger.warning(
            "ACL conditions at %s must be a mapping, got %s — unevaluable (PROTOCOL_SPEC §6.1.1): "
            "a 'deny' rule takes effect, an 'allow' rule does not grant",
            path,
            type_name,
        )
        _record_handler_error(path, f"ACL conditions must be a mapping, got {type_name}")
        return ConditionOutcome.UNEVALUABLE

    @classmethod
    def _evaluate_condition(
        cls,
        key: str,
        value: Any,
        context: Context,
        path: str,
    ) -> ConditionOutcome:
        """Evaluate one condition key on the sync path, at *path* (§6.1.4)."""
        handler = cls._condition_handlers.get(key)
        if handler is None:
            # A typo'd key (`role:` for `roles:`) is not "condition not met" —
            # no answer is obtainable, so the rule resolves toward refusing
            # access and `AuditEntry.handler_error` says why.
            cls._record_unresolvable_key(key, path)
            return ConditionOutcome.UNEVALUABLE

        # Snapshot before invoking so a compound operator that merely *propagates*
        # a child's UNEVALUABLE does not also record a generic entry of its own —
        # the child already named the precise path.
        before = cls._recorded_condition_paths()
        try:
            result = cls._invoke_handler(handler, value, context, path)
        except Exception as exc:
            _logger.exception("Handler for condition %s raised — unevaluable (PROTOCOL_SPEC §6.1.1)", path)
            _record_handler_error(path, f"{type(exc).__name__}: {exc}")
            return ConditionOutcome.UNEVALUABLE

        if inspect.isawaitable(result):
            # Try to advance the coroutine one synchronous step. If the
            # coroutine returns without hitting an ``await`` (sync-only
            # body wrapped in async fn), StopIteration carries the value.
            # Otherwise it suspends — unevaluable on this path.
            try:
                result.send(None)  # type: ignore[attr-defined]
            except StopIteration as stop:
                return cls._coerce(stop.value, path, before)
            except Exception as exc:
                _logger.exception(
                    "Handler for condition %s raised during sync resolution — unevaluable (PROTOCOL_SPEC §6.1.1)",
                    path,
                )
                _record_handler_error(path, f"{type(exc).__name__}: {exc}")
                return ConditionOutcome.UNEVALUABLE
            else:
                # Coroutine suspended — genuinely async, can't run in sync path.
                # This is a *configuration* fault, not an unmet condition, so it
                # is one of §6.1.1's unevaluable situations (parity with
                # apcore-typescript's "Async condition … in sync context" and
                # apcore-rust's ``Poll::Pending`` arm).
                result.close()  # type: ignore[attr-defined]
                _logger.warning(
                    "Async condition %s suspended in sync context — unevaluable "
                    "(PROTOCOL_SPEC §6.1.1). Use async_check() for handlers needing await.",
                    path,
                )
                _record_handler_error(path, "async condition suspended in sync context — use async_check()")
                return ConditionOutcome.UNEVALUABLE

        return cls._coerce(result, path, before)

    @classmethod
    async def _evaluate_conditions_async(
        cls,
        conditions: dict[str, Any],
        context: Context,
        path_prefix: str = "",
    ) -> ConditionOutcome:
        """Async variant. Uses async handler if registered, falls back to sync.

        Same three-valued contract and same composition rules as
        :meth:`_evaluate_conditions`.
        """
        if not isinstance(conditions, dict):
            return cls._malformed_conditions(conditions, path_prefix)

        saw_unevaluable = False
        for key, value in conditions.items():
            outcome = await cls._evaluate_condition_async(key, value, context, join_condition_path(path_prefix, key))
            if outcome is ConditionOutcome.UNSATISFIED:
                return ConditionOutcome.UNSATISFIED
            if outcome is ConditionOutcome.UNEVALUABLE:
                saw_unevaluable = True
        return ConditionOutcome.UNEVALUABLE if saw_unevaluable else ConditionOutcome.SATISFIED

    @classmethod
    async def _evaluate_condition_async(
        cls,
        key: str,
        value: Any,
        context: Context,
        path: str,
    ) -> ConditionOutcome:
        """Evaluate one condition key on the async path, at *path* (§6.1.4)."""
        # Prefer async-specific handler (e.g., _OrHandlerAsync) so compound
        # operators recurse through the async path and properly await. Falls
        # back to the sync registry per PROTOCOL_SPEC §6.1.3.
        handler = cls._async_condition_handlers.get(key) or cls._condition_handlers.get(key)
        if handler is None:
            cls._record_unresolvable_key(key, path)
            return ConditionOutcome.UNEVALUABLE
        before = cls._recorded_condition_paths()
        try:
            result = cls._invoke_handler(handler, value, context, path)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            _logger.exception("Handler for condition %s raised — unevaluable (PROTOCOL_SPEC §6.1.1)", path)
            _record_handler_error(path, f"{type(exc).__name__}: {exc}")
            return ConditionOutcome.UNEVALUABLE
        return cls._coerce(result, path, before)

    @staticmethod
    def _invoke_handler(handler: Any, value: Any, context: Context, path: str) -> Any:
        """Call a handler, handing the built-in compound operators their path.

        ``$or`` / ``$not`` recurse, so a fault beneath them must be reported at
        its position in the tree. They advertise the capability with
        ``path_aware``; every other handler keeps the two-argument protocol.
        """
        if getattr(handler, "path_aware", False):
            return handler.evaluate_at(value, context, path)
        return handler.evaluate(value, context)

    @classmethod
    def _coerce(cls, result: Any, path: str, before: frozenset[str]) -> ConditionOutcome:
        """Coerce a handler result, recording a diagnostic if it is UNEVALUABLE.

        A handler may report UNEVALUABLE itself, and that has to reach
        ``handler_error`` or §6.1.1 rule 2 is not met. But ``$or`` / ``$not``
        also return UNEVALUABLE when *propagating* a child's, and the child has
        already recorded the precise path — so a generic entry at the operator's
        own path would be a duplicate naming a less useful location. ``before``
        distinguishes the two: anything recorded during the call means the
        subtree spoke for itself.
        """
        outcome = _as_outcome(result)
        if outcome is ConditionOutcome.UNEVALUABLE and not (cls._recorded_condition_paths() - before):
            _record_handler_error(path, "handler could not evaluate the condition as written")
        return outcome

    @staticmethod
    def _record_unresolvable_key(key: str, path: str) -> None:
        """Warn and record an unresolvable condition key at *path*."""
        _logger.warning(
            "Unknown ACL condition %s (key %r) — unevaluable (PROTOCOL_SPEC §6.1.1): "
            "a 'deny' rule takes effect, an 'allow' rule does not grant",
            path,
            key,
        )
        _record_handler_error(path, "unknown ACL condition")

    # -- Structural and registry precheck (PROTOCOL_SPEC §6.1.4) -----------

    @classmethod
    def _precheck_patterns(cls, rule: ACLRule) -> list[_Fault]:
        """Check that ``callers`` and ``targets`` are each a list of strings (§6.1.4.1).

        A value that is not a list of strings is a malformed rule, not a pattern
        set. A bare string is iterable, so ``callers: "admin.*"`` written where
        ``callers: ["admin.*"]`` was meant is read character by character — and
        ``*`` is a valid pattern matching everything, so an ``allow`` rule
        carrying that typo granted access to **every caller**. A non-subscriptable
        scalar raised ``TypeError`` out of ``check()``, which its contract forbids.

        Both fields are examined; the check does not stop at the first fault, so
        the finding set is a pure function of the rule (§6.1.4 determinism).
        """
        faults: list[_Fault] = []
        for path, value in ((_CALLERS_PATH, rule.callers), (_TARGETS_PATH, rule.targets)):
            if isinstance(value, list) and all(isinstance(pattern, str) for pattern in value):
                continue
            faults.append(
                _Fault(
                    path=path,
                    key=None,
                    reason=f"{path} must be a list of strings, got {type(value).__name__}",
                )
            )
        return faults

    @classmethod
    def _precheck_conditions(cls, conditions: Any, *, async_path: bool, path_prefix: str = "") -> list[_Fault]:
        """Walk the whole ``conditions`` tree for structural and registry faults (§6.1.4).

        Context-independent, invokes no handler, and **does not short-circuit** —
        its completeness is what makes §6.1.1 rule 2's deterministic
        ``handler_error`` achievable across implementations. It determines:

        - whether ``conditions`` is a mapping (§6.1.1 case 5);
        - for each key, whether a handler is resolvable on the evaluation path in
          use (§6.1.1 case 1, §6.1.3);
        - for each compound operator, whether its value has the required shape
          (§6.1.1 case 4).

        Args:
            conditions: The condition object (or malformed value) to examine.
            async_path: True when the enclosing call is :meth:`async_check`, which
                resolves against the async registry and falls back to the sync one.
            path_prefix: §6.1.4 path of the enclosing node.
        """
        faults: list[_Fault] = []
        if not isinstance(conditions, dict):
            path = path_prefix or ROOT_CONDITION_PATH
            return [
                _Fault(
                    path=path,
                    key=None,
                    reason=f"ACL conditions must be a mapping, got {type(conditions).__name__}",
                )
            ]

        for key, value in conditions.items():
            path = join_condition_path(path_prefix, key)
            if key == "$or":
                if not isinstance(value, list):
                    faults.append(
                        _Fault(path=path, key=key, reason=f"$or value must be a list, got {type(value).__name__}")
                    )
                    continue
                for index, sub in enumerate(value):
                    branch = f"{path}[{index}]"
                    if not isinstance(sub, dict):
                        faults.append(
                            _Fault(
                                path=branch,
                                key=None,
                                reason=f"$or element must be a mapping, got {type(sub).__name__}",
                            )
                        )
                        continue
                    faults.extend(cls._precheck_conditions(sub, async_path=async_path, path_prefix=branch))
            elif key == "$not":
                if not isinstance(value, dict):
                    faults.append(
                        _Fault(path=path, key=key, reason=f"$not value must be a mapping, got {type(value).__name__}")
                    )
                    continue
                faults.extend(cls._precheck_conditions(value, async_path=async_path, path_prefix=path))
            elif key == "arguments":
                # §6.1.7's vocabulary is closed and its predicate values are
                # lists of strings, both of which are decidable without a
                # context and without running the handler — so they belong in
                # the precheck, where the finding is a pure function of the rule
                # and identical across implementations (§6.1.4 determinism).
                # Attached to the `arguments` key, like a malformed `$or` value:
                # the flags stay False because a structural fault resolves on
                # neither evaluation path, however well registered the key is.
                # §6.1.8: descend to the offending predicate where one can be
                # named, exactly as §6.1.4 descends into `$or[1].k`, and report
                # EVERY faulty predicate rather than stopping at the first.
                for suffix, reason in validate_arguments_condition(value):
                    faults.append(_Fault(path=f"{path}{suffix}", key=key, reason=reason))
            else:
                sync_resolvable = key in cls._condition_handlers
                async_resolvable = sync_resolvable or key in cls._async_condition_handlers
                resolvable = async_resolvable if async_path else sync_resolvable
                if not resolvable:
                    faults.append(
                        _Fault(
                            path=path,
                            key=key,
                            reason="unknown ACL condition",
                            sync_resolvable=sync_resolvable,
                            async_resolvable=async_resolvable,
                        )
                    )
        return faults

    @classmethod
    def _precheck_rule(cls, rule: ACLRule, *, async_path: bool) -> list[_Fault]:
        """Full §6.1.4 precheck for one rule: pattern fields, then conditions."""
        faults = cls._precheck_patterns(rule)
        if rule.conditions is not None:
            faults.extend(cls._precheck_conditions(rule.conditions, async_path=async_path))
        return faults

    @classmethod
    def _warn_unregistered_condition_keys(cls, rules: list[ACLRule], *, base_index: int = 0) -> None:
        """Warn — never fail — for rules that fail the §6.1.4 precheck.

        PROTOCOL_SPEC §6.1.2: ``register_condition`` writes to a runtime,
        process-wide registry and ``acl.root`` discovery commonly runs during
        framework bootstrap, ahead of application code. Loading MUST NOT fail on
        an unregistered key, but MUST warn, naming the rule index, the key and
        the rule's ``effect`` — the ``effect`` because a misconfigured ``deny``
        rule is the consequential case. :meth:`validate_rules` is the
        deterministic check to run once registration is complete.
        """
        for offset, rule in enumerate(rules):
            for fault in cls._precheck_rule(rule, async_path=False):
                _logger.warning(
                    "ACL rule %d (effect=%s) fails the structural/registry precheck at %s: %s. "
                    "On the sync check() path the rule is unevaluable (PROTOCOL_SPEC §6.1.1), so a "
                    "'deny' rule takes effect and an 'allow' rule does not grant. Register a handler "
                    "with ACL.register_condition(), or call ACL.validate_rules() after bootstrap to "
                    "assert on this.",
                    base_index + offset,
                    rule.effect,
                    fault.path,
                    fault.reason,
                )

    def validate_rules(self) -> tuple[RuleValidationFinding, ...]:
        """Report every rule that fails the §6.1.4 structural and registry precheck.

        The explicit validation entry point PROTOCOL_SPEC §6.1.2 rule 3
        requires. It is named ``validate_rules`` rather than
        ``validate_conditions`` because it reports structural faults in
        ``callers`` and ``targets`` as well (§6.1.4.1), not only condition keys.

        Loading an ACL only warns, because handler registration is a runtime,
        process-wide act that legitimately happens after discovery; this method
        is what a deployment calls once registration is complete, so it can turn
        a broken rule into a startup error of its own choosing.

        A finding is emitted whenever ``sync_resolvable`` is false — **including**
        when ``async_resolvable`` is true (§6.1.3 rule 3). An async-only handler
        is a working condition under :meth:`async_check` and an unevaluable one
        under :meth:`check`; an application that only ever calls ``async_check``
        may ignore such a finding, but that judgement belongs to the caller, not
        to the validator.

        Pure read: it does not mutate the ACL, register handlers, or emit an
        audit event.

        Returns:
            A possibly-empty tuple of :class:`RuleValidationFinding`, ordered by
            ``rule_index`` and then lexicographically by ``condition_path`` — by
            path and not by key, because a nested ``$or`` may carry the same key
            at several positions, which leaves ordering by key undefined. Empty
            means every rule currently passes the precheck on the sync path; it
            is not a guarantee about the future, since a later :meth:`add_rule`
            can introduce a new fault.
        """
        with self._lock:
            rules = list(self._rules)

        cls = type(self)
        findings: list[RuleValidationFinding] = []
        for index, rule in enumerate(rules):
            faults = cls._precheck_rule(rule, async_path=False)
            for fault in sorted(faults, key=lambda f: f.path):
                findings.append(
                    RuleValidationFinding(
                        rule_index=index,
                        condition_path=fault.path,
                        condition_key=fault.key,
                        effect=rule.effect,
                        sync_resolvable=fault.sync_resolvable,
                        async_resolvable=fault.async_resolvable,
                    )
                )
        return tuple(findings)

    def __init__(
        self,
        rules: list[ACLRule] | None = None,
        default_effect: str = "deny",
        *,
        audit_logger: Callable[[AuditEntry], None] | None = None,
    ) -> None:
        """Initialize ACL with ordered rules and a default effect.

        Args:
            rules: Ordered list of ACL rules (first match wins). Defaults to [].
            default_effect: Effect when no rule matches ('allow' or 'deny').
            audit_logger: Optional callback invoked with an AuditEntry for
                every check() call. Useful for structured audit trails.
        """
        if default_effect not in {"allow", "deny"}:
            raise ACLRuleError(
                f"Invalid default_effect '{default_effect}': must be 'allow' or 'deny'. "
                "Cross-language parity with apcore-typescript constructor validation (sync A-D-025)."
            )

        self._rules = list(rules) if rules is not None else []

        self._default_effect: str = default_effect
        self._yaml_path: str | None = None
        self._audit_logger: Callable[[AuditEntry], None] | None = audit_logger
        self._logger: logging.Logger = logging.getLogger(__name__)
        self._lock = threading.Lock()
        self.debug: bool = False
        # PROTOCOL_SPEC §6.5: warn the *first* time a conditional rule is
        # skipped for want of a context. Keyed by (rule index, effect) so a
        # hot path does not flood the log.
        self._warned_missing_context: set[tuple[int | None, str]] = set()

        # PROTOCOL_SPEC §6.1.2 rule 4: every entry point that accepts rules is
        # covered, direct construction included — ACL.load() and reload() both
        # funnel through here.
        type(self)._warn_unregistered_condition_keys(self._rules)

    @property
    def default_effect(self) -> str:
        """The effect applied when no rule matches — ``"allow"`` or ``"deny"``.

        Read-only accessor required by PROTOCOL_SPEC §6.8. A pure read: it
        emits no audit event and mutates nothing, and it reflects the reloaded
        file after :meth:`reload`.
        """
        with self._lock:
            return self._default_effect

    @property
    def rules(self) -> tuple[ACLRule, ...]:
        """The current rule list, in definition order.

        Read-only accessor required by PROTOCOL_SPEC §6.8. Returns an immutable
        tuple taken under the same snapshot discipline :meth:`check` uses, so a
        caller cannot reach through it to mutate the ACL's own list. Reflects
        the reloaded file after :meth:`reload`.
        """
        with self._lock:
            return tuple(self._rules)

    @classmethod
    def load(cls, yaml_path: str) -> ACL:
        """Load ACL configuration from a YAML file.

        Args:
            yaml_path: Path to the YAML configuration file.

        A rule referencing a condition key with no handler registered at load
        time is **not** an error: ``register_condition`` writes to a runtime,
        process-wide registry, and ``acl.root`` discovery commonly runs during
        framework bootstrap ahead of application code, so failing here would
        reject valid configurations on ordering alone (PROTOCOL_SPEC §6.1.2).
        A warning naming the rule index, the key and the rule's ``effect`` is
        emitted instead; :meth:`validate_rules` is the deterministic check
        to run once registration is complete.

        Returns:
            A new ACL instance configured from the YAML file.

        Raises:
            ConfigNotFoundError: If the file does not exist.
            ACLRuleError: If the YAML is invalid or has structural errors —
                including a ``conditions`` value that is not a mapping, which a
                file has no legitimate reason to carry and which would otherwise
                surface only as a §6.1.1 case-5 denial at the first ``check()``.
        """
        if not os.path.isfile(yaml_path):
            raise ConfigNotFoundError(config_path=yaml_path)

        with open(yaml_path, encoding="utf-8") as f:
            try:
                data = yaml.safe_load(f)
            except yaml.YAMLError as e:
                raise ACLRuleError(f"Invalid YAML in {yaml_path}: {e}") from e

        if not isinstance(data, dict):
            raise ACLRuleError(f"ACL config must be a mapping, got {type(data).__name__}")

        if "rules" not in data:
            raise ACLRuleError("ACL config missing required 'rules' key")

        raw_rules = data["rules"]
        if not isinstance(raw_rules, list):
            raise ACLRuleError(f"'rules' must be a list, got {type(raw_rules).__name__}")

        default_effect: str = data.get("default_effect", "deny")
        rules: list[ACLRule] = []

        for i, raw_rule in enumerate(raw_rules):
            if not isinstance(raw_rule, dict):
                raise ACLRuleError(f"Rule {i} must be a mapping, got {type(raw_rule).__name__}")

            for key in ("callers", "targets", "effect"):
                if key not in raw_rule:
                    raise ACLRuleError(f"Rule {i} missing required key '{key}'")

            # A missing key was already rejected above so an omission cannot
            # render a rule inert; an unknown key is the same hazard pointing
            # the other way, and was dropped in silence until #107.
            _reject_unknown_rule_keys(i, raw_rule)

            effect = raw_rule["effect"]
            if effect not in ("allow", "deny"):
                raise ACLRuleError(f"Rule {i} has invalid effect '{effect}', must be 'allow' or 'deny'")

            # PROTOCOL_SPEC §6.1.6. Checked here as well as in ACLRule.__post_init__
            # so a file fault names the offending rule's index, which the
            # dataclass cannot know.
            approval = raw_rule.get("approval", APPROVAL_NOT_REQUIRED)
            _validate_approval(approval, effect, where=f"Rule {i}")

            callers = raw_rule["callers"]
            if not isinstance(callers, list):
                raise ACLRuleError(f"Rule {i} 'callers' must be a list, got {type(callers).__name__}")

            targets = raw_rule["targets"]
            if not isinstance(targets, list):
                raise ACLRuleError(f"Rule {i} 'targets' must be a list, got {type(targets).__name__}")

            raw_conditions = raw_rule.get("conditions")
            if raw_conditions is not None and not isinstance(raw_conditions, dict):
                # A YAML file cannot reach §6.1.1 case 5 — the parser rejects it
                # here, as apcore-typescript's `_parseAclRule` does. Direct
                # construction and add_rule() still can, which is what the
                # runtime precheck (§6.1.4) is for.
                raise ACLRuleError(f"Rule {i} 'conditions' must be a mapping, got {type(raw_conditions).__name__}")

            rules.append(
                ACLRule(
                    callers=callers,
                    targets=targets,
                    effect=effect,
                    description=raw_rule.get("description", ""),
                    conditions=raw_conditions,
                    approval=approval,
                )
            )

        acl = cls(rules=rules, default_effect=default_effect)
        acl._yaml_path = yaml_path
        return acl

    @classmethod
    def discover(cls, config: Config) -> ACL | None:
        """Activate ``acl.root`` config-driven ACL discovery (D-64, Recommendation A).

        Resolves the ``acl.root`` config key (default ``"./acl"``) and loads an
        ACL from it when the path exists. The path is resolved relative to the
        directory of the config's source file when known (``config.source_path``),
        otherwise relative to the current working directory.

        ``acl.root`` is a directory by spec convention (``acl/{scope}_acl.yaml``,
        PROTOCOL_SPEC §3.1). When the resolved path is a directory, the
        conventional ``global_acl.yaml`` within it is loaded; if that file is
        absent the result is ``None`` (the missing-path no-op still holds). When
        the resolved path is itself a file, it is loaded directly. Either way the
        actual load goes through :meth:`load`.

        Critical invariant: a missing path returns ``None`` and attaches NOTHING.
        It MUST NOT synthesize an empty default-deny ACL — doing so would silently
        deny every inter-module call in every project that lacks an ``acl`` dir.
        Missing path means "no enforcement", identical to pre-D-64 behavior. The
        ``acl.default_effect`` config key only takes effect once a real ACL file
        is loaded (it is read by :meth:`load` from the ACL file itself); it never
        feeds a synthesized ACL here.

        Args:
            config: The loaded configuration to read ``acl.root`` from.

        Returns:
            A loaded :class:`ACL` when the resolved path exists, otherwise
            ``None``.

        Raises:
            ConfigNotFoundError: Never raised for a missing root (that path
                returns ``None``); only propagated from :meth:`load` if the
                path disappears between the existence check and the load.
            ACLRuleError: If the ACL file exists but is structurally invalid.
        """
        root = config.get("acl.root", Config.get_default("acl.root"))
        if root is None:
            return None

        root_path = Path(str(root))
        if not root_path.is_absolute():
            source_path = config.source_path
            if source_path is not None:
                base = Path(source_path).resolve().parent
            else:
                base = Path.cwd()
            root_path = base / root_path

        if not root_path.exists():
            # Missing path -> no enforcement. Do NOT synthesize an ACL.
            return None

        if root_path.is_dir():
            # Directory convention: acl/{scope}_acl.yaml (PROTOCOL_SPEC §3.1).
            acl_file = root_path / "global_acl.yaml"
            if not acl_file.is_file():
                # Directory present but no conventional ACL file -> no-op.
                return None
            return cls.load(str(acl_file))

        return cls.load(str(root_path))

    def check(
        self,
        caller_id: str | None,
        target_id: str,
        context: Context | None = None,
    ) -> bool:
        """Check if a call from caller_id to target_id is allowed.

        A rule whose conditions cannot be **evaluated** at all — no registered
        handler, a handler that raised, or an async handler on this sync path —
        resolves toward refusing access per PROTOCOL_SPEC §6.1.1: a ``deny``
        rule takes effect and the call is denied, an ``allow`` rule does not
        match and does not grant — but an ``allow`` rule that carried
        ``approval: required`` does not take the requirement with it, which
        becomes **pending** and composes with whatever grants later (§6.1.1
        rule 5). The emitted :class:`AuditEntry` carries a non-null
        ``handler_error`` naming the key and the reason. An unevaluable
        condition never raises out of this method.

        **This boolean fails closed on an approval requirement** (§6.8.1): a
        *decision* resolving to ``allow`` with an approval requirement returns
        ``False`` here, because a boolean can only be read as "let it through /
        do not" and letting it through would run a call the ACL said needed a
        human. Callers that need the two axes apart use :meth:`check_access`.

        Args:
            caller_id: The calling module ID, or None for external calls.
            target_id: The target module ID being called.
            context: Optional execution context for conditional rules.

        Returns:
            True if the call is allowed, False if denied **or if it is allowed
            but requires approval** — see :meth:`check_access`.
        """
        return self._as_legacy_boolean(self.check_access(caller_id, target_id, context))

    def check_access(
        self,
        caller_id: str | None,
        target_id: str,
        context: Context | None = None,
    ) -> AccessDecision:
        """Resolve a call to a structured :class:`AccessDecision` (PROTOCOL_SPEC §6.8.1).

        An ACL rule answers two independent questions (§6.1.6) — may this caller
        reach this target at all, and must this particular call be put to a
        human before it runs. :meth:`check` can carry only the first, so this is
        the accessor the Executor uses. Emits exactly one audit entry, like
        :meth:`check`; the two are the same decision reported at different
        widths, not two decisions.

        ``approval_required`` is the matched rule's own union any requirement
        left **pending** by an unevaluable ``allow`` rule (§6.1.1 rule 5), so it
        may originate in a rule that did not match and may accompany
        ``matched_rule_index: None`` when ``default_effect: allow`` granted.

        Args:
            caller_id: The calling module ID, or None for external calls.
            target_id: The target module ID being called.
            context: Optional execution context for conditional rules.

        Returns:
            An :class:`AccessDecision` carrying ``access``,
            ``approval_required``, ``matched_rule_index`` and ``reason``.
        """
        effective_caller, rules, default_effect, audit_logger = self._snapshot(caller_id)

        token = _handler_error_var.set({})
        try:
            matched: tuple[int, ACLRule] | None = None
            pending_approval = False
            for idx, rule in enumerate(rules):
                outcome = self._matches_rule(rule, effective_caller, target_id, context, rule_index=idx)
                if outcome is ConditionOutcome.UNEVALUABLE:
                    if self._unevaluable_rule_takes_effect(rule):
                        matched = (idx, rule)
                        break
                    # The rule steps aside (§6.1.1 rule 1) but its approval
                    # requirement does not go with it (rule 5).
                    pending_approval = pending_approval or self._raises_pending_approval(rule)
                    continue
                if outcome is ConditionOutcome.SATISFIED:
                    matched = (idx, rule)
                    break

            return self._finalize_check(
                log_method="check",
                caller_id=caller_id,
                effective_caller=effective_caller,
                target_id=target_id,
                rules_present=bool(rules),
                default_effect=default_effect,
                matched=matched,
                pending_approval=pending_approval,
                audit_logger=audit_logger,
                context=context,
            )
        finally:
            _handler_error_var.reset(token)

    @staticmethod
    def _as_legacy_boolean(decision: AccessDecision) -> bool:
        """Collapse an :class:`AccessDecision` to the legacy boolean, failing closed.

        PROTOCOL_SPEC §6.8.1: a **decision** resolving to ``allow`` with
        ``approval_required: true`` MUST make :meth:`check` return ``False``.
        The decision and not the matched rule, since spec v1.29.0: the
        requirement may be a pending one raised by an unevaluable rule that did
        not itself match, or carried through ``default_effect: allow`` (§6.1.1
        rule 5), and the boolean fails closed on those identically. Reading
        :attr:`AccessDecision.approval_required` rather than the rule is what
        makes that automatic.

        ``check()`` is public API consumed by callers that are not the Executor
        — tooling, preflight helpers, third-party integrations — and such a
        caller can only read a boolean as "let it through / do not". Returning
        True would run a call the ACL said needed a human. False is wrong in the
        benign direction: the caller sees a refusal where the truth was "ask
        first". The Executor reads :meth:`check_access` and is unaffected, and a
        legacy caller only meets this at all once an operator has authored a
        rule carrying ``approval``.
        """
        return decision.access == "allow" and not decision.approval_required

    async def async_check(
        self,
        caller_id: str | None,
        target_id: str,
        context: Context | None = None,
    ) -> bool:
        """Async ACL check. Supports both sync and async condition handlers.

        Same §6.1.1 three-outcome contract as :meth:`check`. One situation
        cannot arise here — an async handler unresolvable on the sync path —
        since this path awaits genuine async handlers; the async registry is
        also consulted first, so an async-only key resolves here and not there.

        Args:
            caller_id: The calling module ID, or None for external calls.
            target_id: The target module ID being called.
            context: Optional execution context for conditional rules.

        Returns:
            True if the call is allowed, False if denied **or if it is allowed
            but requires approval** — see :meth:`async_check_access`.
        """
        return self._as_legacy_boolean(await self.async_check_access(caller_id, target_id, context))

    async def async_check_access(
        self,
        caller_id: str | None,
        target_id: str,
        context: Context | None = None,
    ) -> AccessDecision:
        """Async :meth:`check_access` (PROTOCOL_SPEC §6.8.1).

        Same structured result and the same single audit entry; the async
        condition registry is consulted first, exactly as in
        :meth:`async_check`.
        """
        effective_caller, rules, default_effect, audit_logger = self._snapshot(caller_id)

        token = _handler_error_var.set({})
        try:
            matched: tuple[int, ACLRule] | None = None
            pending_approval = False
            for idx, rule in enumerate(rules):
                outcome = await self._matches_rule_async(rule, effective_caller, target_id, context, rule_index=idx)
                if outcome is ConditionOutcome.UNEVALUABLE:
                    if self._unevaluable_rule_takes_effect(rule):
                        matched = (idx, rule)
                        break
                    # Same §6.1.1 rule 5 bookkeeping as the sync twin; the two
                    # entry points MUST NOT drift on a governance result.
                    pending_approval = pending_approval or self._raises_pending_approval(rule)
                    continue
                if outcome is ConditionOutcome.SATISFIED:
                    matched = (idx, rule)
                    break

            return self._finalize_check(
                log_method="async_check",
                caller_id=caller_id,
                effective_caller=effective_caller,
                target_id=target_id,
                rules_present=bool(rules),
                default_effect=default_effect,
                matched=matched,
                pending_approval=pending_approval,
                audit_logger=audit_logger,
                context=context,
            )
        finally:
            _handler_error_var.reset(token)

    def _snapshot(self, caller_id: str | None) -> tuple[str, list[ACLRule], str, Callable[[AuditEntry], None] | None]:
        """Atomically snapshot mutable state for a check call."""
        effective_caller = "@external" if caller_id is None else caller_id
        with self._lock:
            return effective_caller, list(self._rules), self._default_effect, self._audit_logger

    def _finalize_check(
        self,
        *,
        log_method: str,
        caller_id: str | None,
        effective_caller: str,
        target_id: str,
        rules_present: bool,
        default_effect: str,
        matched: tuple[int, ACLRule] | None,
        pending_approval: bool,
        audit_logger: Callable[[AuditEntry], None] | None,
        context: Context | None,
    ) -> AccessDecision:
        """Log the decision, emit an audit entry, and return the structured result.

        Shared by both check_access() and async_check_access() so that audit +
        logging logic lives in exactly one place.

        The approval requirement is the matched rule's own **union any pending
        requirement** raised during the scan (PROTOCOL_SPEC §6.9 rows 1-2 as
        amended in spec v1.29.0): there is no default approval *source*, so an
        unremarkable no-match still means ``False``, but a requirement raised by
        an unevaluable ``allow`` rule composes with whatever granted — including
        ``default_effect: allow``, which is the one route with no rule to carry
        it. A ``deny`` rule can never carry one of its own; §6.1.6 rejects that
        pair at construction.
        """
        if matched is not None:
            matched_idx, matched_rule = matched
            access = "allow" if matched_rule.effect == "allow" else "deny"
            approval_required = matched_rule.approval == APPROVAL_REQUIRED
            rule_label: str = matched_rule.description or "(no description)"
            reason = "rule_match"
        else:
            matched_idx = None
            matched_rule = None
            access = "allow" if default_effect == "allow" else "deny"
            approval_required = False
            rule_label = "default"
            reason = "default_effect" if rules_present else "no_rules"

        # §6.1.1 rule 5 (spec v1.29.0, #109). Composed here rather than inside
        # either branch above because a pending requirement outlives the rule
        # that carried it: it rides through a later ``allow`` rule *and* through
        # ``default_effect: allow``, which is what makes ``approval_required:
        # True`` with ``matched_rule_index: None`` a legal combination. A denial
        # clears it — "denied *and* put it to a human" is the same meaningless
        # state §6.1.6 rejects on a rule — while ``matched_rule_index`` keeps
        # naming the rule that actually decided, not the unevaluable one.
        approval_required = access == "allow" and (approval_required or pending_approval)

        self._logger.debug(
            "ACL %s: caller_id=%s target_id=%s decision=%s approval_required=%s rule=%s",
            log_method,
            caller_id,
            target_id,
            access,
            approval_required,
            rule_label,
        )

        if audit_logger is not None:
            audit_logger(
                self._build_audit_entry(
                    caller_id=effective_caller,
                    target_id=target_id,
                    decision=access,
                    reason=reason,
                    matched_rule=matched_rule,
                    matched_rule_index=matched_idx,
                    context=context,
                    approval_required=approval_required,
                )
            )

        return AccessDecision(
            access=access,
            approval_required=approval_required,
            matched_rule_index=matched_idx,
            reason=reason,
        )

    def _match_patterns(self, patterns: list[str], value: str, context: Context | None = None) -> bool:
        """Match a list of patterns against a value.

        Implements compound operators ($or, $not) in pattern lists.
        """
        if not patterns:
            return False

        # Check for compound operators
        first = patterns[0]
        if first == "$or":
            return any(self._match_pattern(p, value, context) for p in patterns[1:])
        if first == "$not":
            # $not expects exactly one subsequent pattern
            if len(patterns) < 2:
                return False
            return not self._match_pattern(patterns[1], value, context)

        # Standard OR behavior for flat list
        return any(self._match_pattern(p, value, context) for p in patterns)

    def _build_audit_entry(
        self,
        *,
        caller_id: str,
        target_id: str,
        decision: str,
        reason: str,
        matched_rule: ACLRule | None,
        matched_rule_index: int | None,
        context: Context | None,
        approval_required: bool = False,
    ) -> AuditEntry:
        """Build an AuditEntry, extracting optional fields from context."""
        identity_type: str | None = None
        roles: tuple[str, ...] = ()
        call_depth: int | None = None
        trace_id: str | None = None

        if context is not None:
            trace_id = context.trace_id
            call_depth = len(context.call_chain)
            if context.identity is not None:
                identity_type = context.identity.type
                roles = tuple(context.identity.roles)

        return AuditEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            caller_id=caller_id,
            target_id=target_id,
            decision=decision,
            reason=reason,
            matched_rule=matched_rule.description if matched_rule is not None else None,
            matched_rule_index=matched_rule_index,
            identity_type=identity_type,
            roles=roles,
            call_depth=call_depth,
            trace_id=trace_id,
            handler_error=_handler_error_message(),
            approval_required=approval_required,
        )

    def _match_pattern(self, pattern: str, value: str, context: Context | None = None) -> bool:
        """Match a single pattern against a value, with special pattern handling.

        Handles @external and @system patterns locally, delegates all
        other patterns to the foundation match_pattern() utility.
        """
        if pattern == "@external":
            return value == "@external"
        if pattern == "@system":
            return context is not None and context.identity is not None and context.identity.type == "system"
        return match_pattern(pattern, value)

    def _matches_rule(
        self,
        rule: ACLRule,
        caller: str,
        target: str,
        context: Context | None,
        *,
        rule_index: int | None = None,
    ) -> ConditionOutcome:
        """Resolve a single rule against the caller and target (three-valued).

        Returns ``SATISFIED`` when the rule matches, ``UNSATISFIED`` when it does
        not, and ``UNEVALUABLE`` when the rule is malformed or its conditions
        could not be evaluated (PROTOCOL_SPEC §6.1.1) — which the caller resolves
        toward refusing access.

        Order is normative (§6.1.4 rule 4):

        a. The **structure** of ``callers`` / ``targets`` is prechecked before any
           pattern is read, because reading a malformed one is the §6.1.4.1
           fail-open.
        b. A malformed pattern field makes the rule unevaluable — its scope is
           unknowable, so it resolves per §6.1.1's effect table.
        c. Both fields well-formed and either failing to match means the rule
           **does not apply to this call**. Its conditions are not consulted, its
           faults do not reach ``handler_error``, and it does not change this
           decision. Otherwise one misspelled key in a narrowly scoped rule would
           decide calls it was never written about — ``callers: ["api.*"]`` with
           a typo'd condition denying a ``worker.*`` caller — which breaks
           first-match-wins. The fault is still real; :meth:`validate_rules`
           looks at every rule and no call, and is where it surfaces.
        d. Only then is the conditions tree prechecked, in full and context-free,
           **before** §6.5's no-context check. That ordering closes the bypass
           where ``conditions: {mispelled: true}`` on a ``deny`` rule passed
           traffic simply because the caller carried no identity.

        A rule that *passes* the precheck and then finds no context takes §6.5's
        path and does not match. ``roles`` is answerable in principle; this
        caller merely supplied no input for it.
        """
        pattern_faults = self._precheck_patterns(rule)
        if pattern_faults:
            self._report_faults(pattern_faults, rule_index, rule)
            return ConditionOutcome.UNEVALUABLE

        if not self._match_patterns(rule.callers, caller, context):
            return ConditionOutcome.UNSATISFIED

        if not self._match_patterns(rule.targets, target, context):
            return ConditionOutcome.UNSATISFIED

        if rule.conditions is None:
            return ConditionOutcome.SATISFIED

        return self._resolve_conditions(rule, context, rule_index, async_path=False)

    async def _matches_rule_async(
        self,
        rule: ACLRule,
        caller: str,
        target: str,
        context: Context | None,
        *,
        rule_index: int | None = None,
    ) -> ConditionOutcome:
        """Async version of :meth:`_matches_rule`, using the async evaluator.

        The precheck resolves against the async registry with a fallback to the
        sync one (§6.1.3), so an async-only handler is a fault on the sync path
        and not on this one.
        """
        pattern_faults = self._precheck_patterns(rule)
        if pattern_faults:
            self._report_faults(pattern_faults, rule_index, rule)
            return ConditionOutcome.UNEVALUABLE

        if not self._match_patterns(rule.callers, caller, context):
            return ConditionOutcome.UNSATISFIED

        if not self._match_patterns(rule.targets, target, context):
            return ConditionOutcome.UNSATISFIED

        if rule.conditions is None:
            return ConditionOutcome.SATISFIED

        faults = self._precheck_conditions(rule.conditions, async_path=True)
        if faults:
            self._report_faults(faults, rule_index, rule)
            return ConditionOutcome.UNEVALUABLE

        if context is None:
            self._warn_conditional_rule_without_context(rule_index, rule.effect)
            return ConditionOutcome.UNSATISFIED

        before = self._recorded_condition_paths()
        outcome = await self._evaluate_conditions_async(rule.conditions, context)
        if outcome is ConditionOutcome.UNEVALUABLE:
            self._warn_unevaluable_conditions(rule_index, rule, before)
        return outcome

    def _resolve_conditions(
        self,
        rule: ACLRule,
        context: Context | None,
        rule_index: int | None,
        *,
        async_path: bool,
    ) -> ConditionOutcome:
        """Precheck, then the §6.5 context check, then evaluate — in that order.

        A missing context is deliberately NOT one of §6.1.1's unevaluable
        situations: calling with no context is a legitimate shape for external
        entry points, not a misconfiguration, and treating it as a failure would
        flip the decision for every ``@external`` call meeting a conditional
        ``deny`` rule (PROTOCOL_SPEC §6.5). It stays a plain non-match, with a
        warning so the consequence is visible. The precheck runs first precisely
        so that a *malformed* rule does not reach that lenient path.
        """
        assert rule.conditions is not None  # guarded by both callers
        faults = self._precheck_conditions(rule.conditions, async_path=async_path)
        if faults:
            self._report_faults(faults, rule_index, rule)
            return ConditionOutcome.UNEVALUABLE

        if context is None:
            self._warn_conditional_rule_without_context(rule_index, rule.effect)
            return ConditionOutcome.UNSATISFIED

        before = self._recorded_condition_paths()
        outcome = self._evaluate_conditions(rule.conditions, context)
        if outcome is ConditionOutcome.UNEVALUABLE:
            self._warn_unevaluable_conditions(rule_index, rule, before)
        return outcome

    def _report_faults(self, faults: list[_Fault], rule_index: int | None, rule: ACLRule) -> None:
        """Record precheck faults into ``handler_error`` and warn (§6.1.1 rules 2-3)."""
        for fault in faults:
            _record_handler_error(fault.path, fault.reason)
        self._logger.warning(
            "ACL rule %s (effect=%s) is unevaluable — PROTOCOL_SPEC §6.1.4 precheck failed at %s. "
            "A 'deny' rule takes effect and the call is denied; an 'allow' rule does not grant.%s",
            "?" if rule_index is None else rule_index,
            rule.effect,
            "; ".join(f"{f.path}: {f.reason}" for f in sorted(faults, key=lambda f: f.path)),
            self._pending_approval_clause(rule),
        )

    @staticmethod
    def _recorded_condition_paths() -> frozenset[str]:
        """Snapshot the condition paths already diagnosed in this check()."""
        errors = _handler_error_var.get()
        return frozenset(errors) if errors else frozenset()

    def _warn_unevaluable_conditions(self, rule_index: int | None, rule: ACLRule, before: frozenset[str]) -> None:
        """Warn that a rule's conditions were unevaluable during execution (§6.1.1 rule 3).

        The message names the condition path(s), the rule's index and the rule's
        ``effect``; the ``effect`` is required because a misconfigured ``deny``
        rule is the consequential case. Only paths diagnosed *by this rule* are
        listed — ``before`` filters out ones an earlier rule already reported.
        """
        paths = sorted(self._recorded_condition_paths() - before)
        self._logger.warning(
            "ACL rule %s (effect=%s) has unevaluable condition(s) %s — PROTOCOL_SPEC §6.1.1: "
            "a 'deny' rule takes effect and the call is denied, an 'allow' rule does not grant.%s",
            "?" if rule_index is None else rule_index,
            rule.effect,
            ", ".join(repr(path) for path in paths) if paths else "(unreported)",
            self._pending_approval_clause(rule),
        )

    @classmethod
    def _pending_approval_clause(cls, rule: ACLRule) -> str:
        """The tail both §6.1.1 warnings carry when the rule gated a human.

        Without it the message reads as though the rule had no further effect,
        which is exactly the reading §6.1.1 rule 5 corrects: the requirement it
        carried is still live, and an operator debugging why a forced push was
        approved — or why an ordinary one now asks — needs to be told which.
        """
        if not cls._raises_pending_approval(rule):
            return ""
        return (
            " The rule carried approval: required, so per §6.1.1 rule 5 that requirement is PENDING "
            "and composes with whatever grants this call, including default_effect: allow."
        )

    def _warn_conditional_rule_without_context(self, rule_index: int | None, effect: str) -> None:
        """Warn once that a conditional rule was skipped for want of a context (§6.5)."""
        marker = (rule_index, effect)
        if marker in self._warned_missing_context:
            return
        self._warned_missing_context.add(marker)
        self._logger.warning(
            "ACL rule %s (effect=%s) has conditions but the check supplied no context, so the "
            "rule does not match (PROTOCOL_SPEC §6.5). A conditional 'deny' rule is therefore "
            "not a backstop for context-less callers — express a backstop as an unconditional "
            "'deny' rule or as default_effect: deny.",
            "?" if rule_index is None else rule_index,
            effect,
        )

    @staticmethod
    def _unevaluable_rule_takes_effect(rule: ACLRule) -> bool:
        """Apply §6.1.1's effect rule to a rule whose conditions were unevaluable.

        Returns True when the rule takes effect (a ``deny`` rule matches and the
        call is denied), False when evaluation continues to the next rule (an
        ``allow`` rule MUST NOT grant). Either way the audit entry already
        carries ``handler_error`` and a warning has been emitted.
        """
        return rule.effect == "deny"

    @staticmethod
    def _raises_pending_approval(rule: ACLRule) -> bool:
        """Does this unevaluable ``allow`` rule leave a **pending** approval requirement?

        PROTOCOL_SPEC §6.1.1 rule 5 (spec v1.29.0, #109).
        :meth:`_unevaluable_rule_takes_effect` answers the authorization axis;
        this answers the second one §6.1.6 added. "MUST NOT grant" was a
        complete instruction while a rule carried a single axis — the rule steps
        aside, and stepping aside was harmless because whatever granted next
        also said ``allow``. It is not complete for a rule that also carried
        ``approval: required``: the narrow rule gating ``git push --force``
        steps aside, a broader ``allow`` grants, and the call the operator gated
        runs with no human asked. So the requirement is recorded and composed by
        disjunction with whatever grants later, rather than discarded.

        **Scope is a precondition, and the caller has already enforced it.** A
        rule whose ``callers`` / ``targets`` do not match resolves to
        UNSATISFIED in :meth:`_matches_rule` and never reaches here (§6.1.4
        rule 4), so a rule written about one caller cannot attach a human to
        calls it was never written about. A rule whose pattern field is itself
        **malformed** does reach here, from the §6.1.4.1 precheck that runs
        before any pattern is read — deliberately, per rule 5's last clause: its
        scope cannot be read, so it cannot be shown not to apply here, which is
        the posture that field already produces under ``deny``, where an
        unreadable scope denies every call.

        The ``effect == "allow"`` test is not redundant with the caller's
        ``deny`` branch: :class:`ACLRule` validates the ``approval`` / ``effect``
        pair but not ``effect`` itself, and :meth:`_finalize_check` reads any
        other value as ``deny``. Both places must read it the same way.
        """
        return rule.effect == "allow" and rule.approval == APPROVAL_REQUIRED

    def add_rule(
        self,
        rule: ACLRule | None = None,
        *,
        callers: list[str] | str | None = None,
        targets: list[str] | str | None = None,
        effect: str = "deny",
        description: str = "",
        conditions: dict[str, Any] | None = None,
        approval: str = APPROVAL_NOT_REQUIRED,
    ) -> None:
        """Add a rule at position 0 (highest priority).

        Args:
            rule: Optional pre-built ACLRule.
            callers: Caller pattern(s) if *rule* is None.
            targets: Target pattern(s) if *rule* is None.
            effect: Rule effect if *rule* is None.
            description: Rule description if *rule* is None.
            conditions: Rule conditions if *rule* is None.
            approval: ``"required"`` or ``"not_required"`` (default) if *rule*
                is None — whether a matching call must be put to a human
                (PROTOCOL_SPEC §6.1.6).

        Raises:
            ACLRuleError: When ``approval="required"`` is combined with
                ``effect="deny"``, which §6.1.6 makes meaningless.

        Note:
            A condition key with no registered handler warns and does not
            raise, exactly as on :meth:`load` (PROTOCOL_SPEC §6.1.2 rule 4).
            The warning names index ``0``, where the rule lands.
        """
        if rule is None:
            if callers is None or targets is None:
                raise ValueError("Must provide either 'rule' or both 'callers' and 'targets'")

            def _to_list(v: list[str] | str) -> list[str]:
                return [v] if isinstance(v, str) else list(v)

            rule = ACLRule(
                callers=_to_list(callers),
                targets=_to_list(targets),
                effect=effect,
                description=description,
                conditions=conditions,
                approval=approval,
            )

        with self._lock:
            self._rules.insert(0, rule)

        # PROTOCOL_SPEC §6.1.2 rule 4: runtime insertion is an entry point that
        # MUST be covered, not just file loading. The rule lands at index 0, so
        # that is the index the warning names. Warn-never-fail, as on load.
        type(self)._warn_unregistered_condition_keys([rule])

    def remove_rule(
        self,
        callers: list[str],
        targets: list[str],
        conditions: dict | None = None,
    ) -> bool:
        """Remove the first rule matching the given callers, targets, and (optional) conditions.

        Args:
            callers: The caller patterns to match.
            targets: The target patterns to match.
            conditions: When provided, also disambiguate by ACLRule.conditions
                via deep equality. Two rules with identical callers+targets but
                different conditions can be selectively removed by passing the
                conditions to match. Cross-language parity with apcore-typescript
                removeRule (sync finding A-D-026).

        Returns:
            True if a rule was found and removed, False otherwise.
        """
        with self._lock:
            for i, rule in enumerate(self._rules):
                if rule.callers != callers or rule.targets != targets:
                    continue
                if conditions is not None and rule.conditions != conditions:
                    continue
                self._rules.pop(i)
                return True
            return False

    def reload(self) -> None:
        """Re-read the ACL from the original YAML file.

        Only works if the ACL was created via ACL.load().
        Raises ACLRuleError if no YAML path was stored.
        """
        with self._lock:
            yaml_path = self._yaml_path
        if yaml_path is None:
            raise ACLRuleError("Cannot reload: ACL was not loaded from a YAML file")
        reloaded = ACL.load(yaml_path)
        with self._lock:
            self._rules = reloaded._rules
            self._default_effect = reloaded._default_effect
            # The §6.5 once-per-rule warning is keyed by rule index, which the
            # reload may have repointed at a different rule. Start fresh.
            self._warned_missing_context.clear()


# ---------------------------------------------------------------------------
# Auto-register compound operators at module load time
# ---------------------------------------------------------------------------
ACL.register_condition("$or", _OrHandler(ACL._evaluate_conditions))
ACL.register_condition("$not", _NotHandler(ACL._evaluate_conditions))

# Async-aware variants: used by async_check() so $or/$not properly await
# async sub-condition handlers (mirrors TypeScript registerAsyncCondition).
ACL.register_async_condition("$or", _OrHandlerAsync(ACL._evaluate_conditions_async))
ACL.register_async_condition("$not", _NotHandlerAsync(ACL._evaluate_conditions_async))
