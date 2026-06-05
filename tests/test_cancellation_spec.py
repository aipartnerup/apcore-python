"""Spec-traced contract tests for the cancellation feature.

Generated from: apcore/docs/features/cancellation.md
Feature spec declares 2 '## Contract:' blocks:
  - CancelToken.cancel
  - CancelToken.raise_if_cancelled

Each test below carries a verbatim clause id of the form
'cancellation.<method>.<kind>.<detail>' so cross-language diffs line up.

NOTE (cross-language gap): the contract block names the second method
'CancelToken.raise_if_cancelled', but the Python SDK source
(src/apcore/cancel.py) implements the cancellation check as 'check()'.
There is no 'raise_if_cancelled' symbol. Per the missing-symbol rule, every
clause under that contract is emitted as a skip documenting the gap rather
than a hard reference that would break import of the whole file.
"""

from __future__ import annotations

import asyncio

import pytest

from apcore.cancel import CancelToken, ExecutionCancelledError
from apcore.errors import ModuleError


# ---------------------------------------------------------------------------
# Contract: CancelToken.cancel
# ---------------------------------------------------------------------------


async def test_cancellation_cancel_property_thread_safe() -> None:
    """cancellation.cancel.property.thread_safe

    Launch >=8 concurrent cancel() calls on distinct tokens via
    asyncio.gather; assert no exception is raised and every token ends in a
    consistent cancelled state.
    """
    tokens = [CancelToken() for _ in range(16)]

    async def do_cancel(tok: CancelToken) -> None:
        # Yield control so calls genuinely interleave on the event loop.
        await asyncio.sleep(0)
        tok.cancel()

    await asyncio.gather(*(do_cancel(t) for t in tokens))

    # Final state must be consistent: all tokens cancelled, none raised.
    assert all(t.is_cancelled is True for t in tokens)


async def test_cancellation_cancel_property_thread_safe_shared_token() -> None:
    """cancellation.cancel.property.thread_safe

    Concurrent cancel() of a single shared token from many tasks must
    converge to exactly one consistent cancelled state with no exception.
    """
    shared = CancelToken()

    async def do_cancel() -> None:
        await asyncio.sleep(0)
        shared.cancel()

    await asyncio.gather(*(do_cancel() for _ in range(16)))

    assert shared.is_cancelled is True


async def test_cancellation_cancel_property_idempotent() -> None:
    """cancellation.cancel.property.idempotent

    Call cancel() twice with identical (no) inputs; assert identical
    observable outcome and state (is_cancelled stays True, no raise).
    """
    token = CancelToken()

    token.cancel()
    first_state = token.is_cancelled
    token.cancel()  # Second call must be a safe no-op.
    second_state = token.is_cancelled

    assert first_state is True
    assert second_state is True
    assert first_state == second_state
    # check() must behave identically after the repeated cancel.
    with pytest.raises(ExecutionCancelledError):
        token.check()


# ---------------------------------------------------------------------------
# Contract: CancelToken.raise_if_cancelled
#
# MISSING SYMBOL: the Python SDK has no 'raise_if_cancelled' method on
# CancelToken (the equivalent behavior is 'check()'). These clauses are
# recorded as skips so the cross-language naming gap is documented as a skip
# rather than a coarse import/compile failure.
# ---------------------------------------------------------------------------


def test_cancellation_raise_if_cancelled_error_execution_cancelled() -> None:
    """cancellation.raise_if_cancelled.error.EXECUTION_CANCELLED"""
    pytest.skip(
        "missing symbol: CancelToken.raise_if_cancelled (contract gap) — "
        "Python SDK implements this as CancelToken.check()"
    )


def test_cancellation_raise_if_cancelled_property_thread_safe() -> None:
    """cancellation.raise_if_cancelled.property.thread_safe"""
    pytest.skip(
        "missing symbol: CancelToken.raise_if_cancelled (contract gap) — "
        "Python SDK implements this as CancelToken.check()"
    )


def test_cancellation_raise_if_cancelled_property_pure() -> None:
    """cancellation.raise_if_cancelled.property.pure"""
    pytest.skip(
        "missing symbol: CancelToken.raise_if_cancelled (contract gap) — "
        "Python SDK implements this as CancelToken.check()"
    )


# ---------------------------------------------------------------------------
# Sanity guard: ensure the declared error type/code referenced by the
# raise_if_cancelled contract actually exists with the spec'd code, so the
# gap above is purely a method-name mismatch (not a missing error type).
# This keeps the missing-symbol skips honest and is a non-trivial assertion.
# ---------------------------------------------------------------------------


def test_cancellation_execution_cancelled_error_code_matches_spec() -> None:
    """cancellation.raise_if_cancelled.error.EXECUTION_CANCELLED (error-type guard)

    The contract requires ExecutionCancelledError(code=EXECUTION_CANCELLED).
    Verify the error TYPE and CODE field match exactly via the live check()
    path, confirming the gap is method-naming only.
    """
    token = CancelToken()
    token.cancel()
    with pytest.raises(ExecutionCancelledError) as exc_info:
        token.check()

    err = exc_info.value
    assert isinstance(err, ModuleError)
    assert err.code == "EXECUTION_CANCELLED"
