"""Built-in ACL condition handlers and handler protocols.

Defines the ACLConditionHandler protocol (sync and async variants), the
three-valued :class:`ConditionOutcome` of PROTOCOL_SPEC §6.1.1, three basic
handlers (identity_types, roles, max_call_depth), and two compound operators
($or, $not) with both sync and async variants.

The compound operators are **path-aware** (§6.1.4): they receive the condition
path they sit at so a fault nested inside them can be reported as
``$or[1].$not.k`` rather than as a bare key. A key can occur at several
positions in one tree, which is why diagnostics are ordered by path and not by
key.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Awaitable, Callable, ClassVar, Protocol, Union, runtime_checkable

from apcore.context import Context

__all__ = [
    "ConditionOutcome",
    "SyncACLConditionHandler",
    "AsyncACLConditionHandler",
    "ACLConditionHandler",
]


class ConditionOutcome(Enum):
    """Three-valued result of evaluating an ACL condition (PROTOCOL_SPEC §6.1.1).

    A condition that **is false** and a condition that **cannot be evaluated**
    are different outcomes and MUST NOT be represented the same way: on a
    ``deny`` rule the first means "this rule does not apply" while the second
    means "deny". Collapsing them into a boolean is the defect §6.1.1 exists to
    prevent — a misspelled key (``role:`` for ``roles:``) turned a ``deny`` rule
    its author believed was blocking into decoration.

    Values:
        SATISFIED: A registered handler ran to completion and answered "yes".
        UNSATISFIED: A registered handler ran to completion and answered "no".
        UNEVALUABLE: No answer was obtainable. Exactly three situations qualify
            (§6.1.1): the condition key has no registered handler; the handler
            raised; the handler was asynchronous and could not be resolved on
            the synchronous ``check()`` path.

    A custom condition handler MAY return a :class:`ConditionOutcome` directly
    to report ``UNEVALUABLE`` for its own reasons; returning a plain ``bool``
    keeps the ordinary satisfied/unsatisfied meaning.
    """

    SATISFIED = "satisfied"
    UNSATISFIED = "unsatisfied"
    UNEVALUABLE = "unevaluable"


#: Path of the ``conditions`` object itself (PROTOCOL_SPEC §6.1.4). The JSONPath
#: root, used when the fault is the whole block rather than a key inside it —
#: it is the only path with no key component, and it keeps the notation
#: consistent with ``$or[1].$not.k``.
ROOT_CONDITION_PATH = "$"


def join_condition_path(prefix: str, key: str) -> str:
    """Compose a §6.1.4 condition path from a prefix and a key.

    ``("", "roles")`` -> ``roles``; ``("$or[1]", "k")`` -> ``$or[1].k``;
    ``("$not", "k")`` -> ``$not.k``. Paths nest, so a key inside ``$not`` inside
    the second ``$or`` branch is ``$or[1].$not.k``.
    """
    return f"{prefix}.{key}" if prefix else key


def _as_outcome(value: Any) -> ConditionOutcome:
    """Coerce a handler return value to a :class:`ConditionOutcome`.

    A handler that already speaks the three-valued vocabulary is passed through
    untouched; anything else keeps the historical boolean meaning.
    """
    if isinstance(value, ConditionOutcome):
        return value
    return ConditionOutcome.SATISFIED if value else ConditionOutcome.UNSATISFIED


@runtime_checkable
class SyncACLConditionHandler(Protocol):
    """Sync condition handler protocol."""

    def evaluate(self, value: Any, context: Context) -> bool | ConditionOutcome: ...


@runtime_checkable
class AsyncACLConditionHandler(Protocol):
    """Async condition handler protocol."""

    async def evaluate(self, value: Any, context: Context) -> bool | ConditionOutcome: ...


ACLConditionHandler = Union[SyncACLConditionHandler, AsyncACLConditionHandler]

# Type alias for the recursive evaluation function used by compound handlers.
# Compound operators recurse through the three-valued evaluator so that an
# unevaluable sub-condition propagates per §6.1.1's composition table rather
# than being flattened into "false" one nesting level down.
_EvalFn = Callable[[dict[str, Any], Context, str], ConditionOutcome]
_AsyncEvalFn = Callable[[dict[str, Any], Context, str], Awaitable[ConditionOutcome]]


# ---------------------------------------------------------------------------
# Basic handlers
# ---------------------------------------------------------------------------


class _IdentityTypesHandler:
    """Check context.identity.type matches allowed value(s).

    Per spec, identity_types condition value MUST be a list.
    """

    def evaluate(self, value: Any, context: Context) -> bool:
        if context.identity is None:
            return False
        if not isinstance(value, list):
            return False
        return context.identity.type in value


class _RolesHandler:
    """Check role overlap between identity and required roles.

    Per spec, roles condition value MUST be a list.
    """

    def evaluate(self, value: Any, context: Context) -> bool:
        if context.identity is None:
            return False
        if not isinstance(value, list):
            return False
        return bool(set(context.identity.roles) & set(value))


class _MaxCallDepthHandler:
    """Check call chain length does not exceed threshold."""

    def evaluate(self, value: Any, context: Context) -> bool:
        threshold = value.get("lte") if isinstance(value, dict) else value
        # Reject bool explicitly: Python ``bool`` is a subclass of ``int``, so a
        # YAML ``max_call_depth: true`` would coerce to threshold=1 and could
        # ALLOW a shallow call where TS/Rust fail closed. Fail closed here too.
        if isinstance(threshold, bool):
            return False
        # A-D-004: accept an integral numeric threshold consistently across SDKs.
        # YAML/JSON may surface an integer depth as a float (``5.0``); TS has no
        # int/float distinction and treats ``5.0`` as 5, so Python must too —
        # otherwise the rule silently fails to match and falls through to deny.
        # A non-integral float (``5.5``) is a meaningless depth and fails closed,
        # mirroring TypeScript's ``Number.isInteger`` guard.
        if isinstance(threshold, float):
            if not threshold.is_integer():
                return False
            threshold = int(threshold)
        elif not isinstance(threshold, int):
            return False
        return len(context.call_chain) <= threshold


# ---------------------------------------------------------------------------
# Compound handlers
# ---------------------------------------------------------------------------
#
# PROTOCOL_SPEC §6.1.1 composition table (three-valued / Kleene logic):
#
#   $or  — any child SATISFIED                 -> SATISFIED
#          no child SATISFIED, >=1 UNEVALUABLE -> UNEVALUABLE
#          every child UNSATISFIED             -> UNSATISFIED
#   $not — child SATISFIED                     -> UNSATISFIED
#          child UNSATISFIED                   -> SATISFIED
#          child UNEVALUABLE                   -> UNEVALUABLE
#
# Short-circuiting is permitted on the decisive child (SATISFIED for $or) but
# MUST NOT happen on an UNEVALUABLE one: a later sibling may still decide it.
# Short-circuiting applies to handler *execution* only — §6.1.4's precheck walks
# the whole tree first, so a malformed operator or a misspelled key is always
# found and always reported, whatever the execution order turns out to be.
#
# A malformed operand — `$or` that is not a list, `$not` that is not an object,
# an `$or` element that is not an object — is UNEVALUABLE, not UNSATISFIED
# (§6.1.1 case 4). A handler handed `$or: "not-a-list"` can return false and
# look exactly like one that answered "no"; recording that as UNSATISFIED puts a
# `deny` rule carrying `$or: "typo"` back into the inert state §6.1.1 exists to
# end. In practice §6.1.4's precheck catches these before any handler runs;
# the guards below are the same rule applied where the evaluator is entered
# directly.


class _CompoundHandler:
    """Base for the built-in compound operators, which are **path-aware**.

    An ordinary handler answers about one key and needs no path. ``$or`` and
    ``$not`` recurse, so a fault beneath them has to be reported at its position
    in the tree (``$or[1].k``) rather than as a bare key. The evaluator detects
    the capability through :attr:`path_aware` and calls :meth:`evaluate_at`.
    """

    path_aware: ClassVar[bool] = True

    def evaluate_at(self, value: Any, context: Context, path: str) -> ConditionOutcome:
        raise NotImplementedError

    def evaluate(self, value: Any, context: Context) -> ConditionOutcome:
        """Path-less entry point, for a caller invoking the handler directly."""
        return self.evaluate_at(value, context, "")


class _AsyncCompoundHandler:
    """Async counterpart of :class:`_CompoundHandler`."""

    path_aware: ClassVar[bool] = True

    async def evaluate_at(self, value: Any, context: Context, path: str) -> ConditionOutcome:
        raise NotImplementedError

    async def evaluate(self, value: Any, context: Context) -> ConditionOutcome:
        return await self.evaluate_at(value, context, "")


class _OrHandler(_CompoundHandler):
    """$or: list of condition dicts. SATISFIED if ANY sub-set is satisfied."""

    def __init__(self, evaluate_fn: _EvalFn) -> None:
        self._evaluate = evaluate_fn

    def evaluate_at(self, value: Any, context: Context, path: str) -> ConditionOutcome:
        if not isinstance(value, list):
            return ConditionOutcome.UNEVALUABLE
        saw_unevaluable = False
        for index, sub in enumerate(value):
            if not isinstance(sub, dict):
                # An element that is not a condition object asks no question.
                saw_unevaluable = True
                continue
            outcome = self._evaluate(sub, context, f"{path}[{index}]")
            if outcome is ConditionOutcome.SATISFIED:
                return ConditionOutcome.SATISFIED
            if outcome is ConditionOutcome.UNEVALUABLE:
                saw_unevaluable = True
        return ConditionOutcome.UNEVALUABLE if saw_unevaluable else ConditionOutcome.UNSATISFIED


class _NotHandler(_CompoundHandler):
    """$not: single condition dict. SATISFIED if the sub-set is UNSATISFIED.

    ``$not`` of an UNEVALUABLE child is UNEVALUABLE, never SATISFIED: negating
    "no answer" into "yes" would let a misspelled key inside a ``$not`` satisfy
    the very rule it was meant to gate.
    """

    def __init__(self, evaluate_fn: _EvalFn) -> None:
        self._evaluate = evaluate_fn

    def evaluate_at(self, value: Any, context: Context, path: str) -> ConditionOutcome:
        if not isinstance(value, dict):
            return ConditionOutcome.UNEVALUABLE
        outcome = self._evaluate(value, context, path)
        if outcome is ConditionOutcome.UNEVALUABLE:
            return ConditionOutcome.UNEVALUABLE
        # An empty object evaluates to SATISFIED, so ``$not: {}`` is UNSATISFIED
        # (fail-closed), as PROTOCOL_SPEC §6.1 requires.
        return ConditionOutcome.UNSATISFIED if outcome is ConditionOutcome.SATISFIED else ConditionOutcome.SATISFIED


# ---------------------------------------------------------------------------
# Async compound handlers (for use with async_check / _evaluate_conditions_async)
# ---------------------------------------------------------------------------


class _OrHandlerAsync(_AsyncCompoundHandler):
    """Async $or: list of condition dicts. SATISFIED if ANY sub-set is satisfied.

    Mirrors TypeScript's OrHandlerAsync — uses the async evaluation path so
    async sub-condition handlers are properly awaited.
    """

    def __init__(self, evaluate_fn: _AsyncEvalFn) -> None:
        self._evaluate = evaluate_fn

    async def evaluate_at(self, value: Any, context: Context, path: str) -> ConditionOutcome:
        if not isinstance(value, list):
            return ConditionOutcome.UNEVALUABLE
        saw_unevaluable = False
        for index, sub in enumerate(value):
            if not isinstance(sub, dict):
                saw_unevaluable = True
                continue
            outcome = await self._evaluate(sub, context, f"{path}[{index}]")
            if outcome is ConditionOutcome.SATISFIED:
                return ConditionOutcome.SATISFIED
            if outcome is ConditionOutcome.UNEVALUABLE:
                saw_unevaluable = True
        return ConditionOutcome.UNEVALUABLE if saw_unevaluable else ConditionOutcome.UNSATISFIED


class _NotHandlerAsync(_AsyncCompoundHandler):
    """Async $not: single condition dict. SATISFIED if the sub-set is UNSATISFIED.

    Mirrors TypeScript's NotHandlerAsync — uses the async evaluation path so
    async sub-condition handlers are properly awaited. As on the sync path,
    ``$not`` of an UNEVALUABLE child stays UNEVALUABLE.
    """

    def __init__(self, evaluate_fn: _AsyncEvalFn) -> None:
        self._evaluate = evaluate_fn

    async def evaluate_at(self, value: Any, context: Context, path: str) -> ConditionOutcome:
        if not isinstance(value, dict):
            return ConditionOutcome.UNEVALUABLE
        outcome = await self._evaluate(value, context, path)
        if outcome is ConditionOutcome.UNEVALUABLE:
            return ConditionOutcome.UNEVALUABLE
        return ConditionOutcome.UNSATISFIED if outcome is ConditionOutcome.SATISFIED else ConditionOutcome.SATISFIED
