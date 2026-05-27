"""Built-in ACL condition handlers and handler protocols.

Defines the ACLConditionHandler protocol (sync and async variants),
three basic handlers (identity_types, roles, max_call_depth), and
two compound operators ($or, $not) with both sync and async variants.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol, Union, runtime_checkable

from apcore.context import Context

__all__ = [
    "SyncACLConditionHandler",
    "AsyncACLConditionHandler",
    "ACLConditionHandler",
]


@runtime_checkable
class SyncACLConditionHandler(Protocol):
    """Sync condition handler protocol."""

    def evaluate(self, value: Any, context: Context) -> bool: ...


@runtime_checkable
class AsyncACLConditionHandler(Protocol):
    """Async condition handler protocol."""

    async def evaluate(self, value: Any, context: Context) -> bool: ...


ACLConditionHandler = Union[SyncACLConditionHandler, AsyncACLConditionHandler]

# Type alias for the recursive evaluation function used by compound handlers.
_EvalFn = Callable[[dict[str, Any], Context], bool]
_AsyncEvalFn = Callable[[dict[str, Any], Context], Awaitable[bool]]


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
        if isinstance(threshold, bool) or not isinstance(threshold, int):
            return False
        return len(context.call_chain) <= threshold


# ---------------------------------------------------------------------------
# Compound handlers
# ---------------------------------------------------------------------------


class _OrHandler:
    """$or: list of condition dicts. Returns True if ANY sub-set passes."""

    def __init__(self, evaluate_fn: _EvalFn) -> None:
        self._evaluate = evaluate_fn

    def evaluate(self, value: Any, context: Context) -> bool:
        if not isinstance(value, list):
            return False
        for sub in value:
            if not isinstance(sub, dict):
                continue
            if self._evaluate(sub, context):
                return True
        return False


class _NotHandler:
    """$not: single condition dict. Returns True if the sub-set FAILS."""

    def __init__(self, evaluate_fn: _EvalFn) -> None:
        self._evaluate = evaluate_fn

    def evaluate(self, value: Any, context: Context) -> bool:
        if not isinstance(value, dict):
            return False
        return not self._evaluate(value, context)


# ---------------------------------------------------------------------------
# Async compound handlers (for use with async_check / _evaluate_conditions_async)
# ---------------------------------------------------------------------------


class _OrHandlerAsync:
    """Async $or: list of condition dicts. Returns True if ANY sub-set passes.

    Mirrors TypeScript's OrHandlerAsync — uses the async evaluation path so
    async sub-condition handlers are properly awaited.
    """

    def __init__(self, evaluate_fn: _AsyncEvalFn) -> None:
        self._evaluate = evaluate_fn

    async def evaluate(self, value: Any, context: Context) -> bool:
        if not isinstance(value, list):
            return False
        for sub in value:
            if not isinstance(sub, dict):
                continue
            if await self._evaluate(sub, context):
                return True
        return False


class _NotHandlerAsync:
    """Async $not: single condition dict. Returns True if the sub-set FAILS.

    Mirrors TypeScript's NotHandlerAsync — uses the async evaluation path so
    async sub-condition handlers are properly awaited.
    """

    def __init__(self, evaluate_fn: _AsyncEvalFn) -> None:
        self._evaluate = evaluate_fn

    async def evaluate(self, value: Any, context: Context) -> bool:
        if not isinstance(value, dict):
            return False
        return not await self._evaluate(value, context)
