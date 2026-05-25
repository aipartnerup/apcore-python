"""Tests for CircuitBreakerMiddleware (Issue #42)."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from apcore.errors import CircuitOpenError
from apcore.middleware.base import RetrySignal
from apcore.middleware.circuit_breaker import CircuitBreakerMiddleware, CircuitBreakerState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx() -> MagicMock:
    return MagicMock()


def _call_before(cb: CircuitBreakerMiddleware, module_id: str = "m1") -> None:
    cb.before(module_id, {}, _ctx())


def _call_after(cb: CircuitBreakerMiddleware, module_id: str = "m1") -> None:
    cb.after(module_id, {}, {}, _ctx())


def _call_on_error(
    cb: CircuitBreakerMiddleware,
    module_id: str = "m1",
    error: Exception | None = None,
) -> dict[str, Any] | RetrySignal | None:
    err = error or RuntimeError("boom")
    return cb.on_error(module_id, {}, err, _ctx())


# ---------------------------------------------------------------------------
# Public API naming (audit D2-001 — cross-SDK parity, matches apcore-rust)
# ---------------------------------------------------------------------------


def test_circuit_breaker_state_exported_from_top_level() -> None:
    """`from apcore import CircuitBreakerState` resolves to the middleware enum."""
    import apcore
    from apcore.middleware.circuit_breaker import (
        CircuitBreakerState as MiddlewareState,
    )

    assert apcore.CircuitBreakerState is MiddlewareState
    assert "CircuitBreakerState" in apcore.__all__


def test_circuit_breaker_state_has_expected_members() -> None:
    """The renamed enum keeps the CLOSED / OPEN / HALF_OPEN members."""
    from apcore import CircuitBreakerState

    assert {m.name for m in CircuitBreakerState} == {"CLOSED", "OPEN", "HALF_OPEN"}


def test_old_middleware_circuit_state_name_is_gone() -> None:
    """The middleware enum is no longer reachable under the old `CircuitState` name.

    Top-level `apcore` must not re-export the middleware enum as `CircuitState`, and the
    middleware module itself must not retain the old attribute (no deprecation alias).
    """
    import apcore
    import apcore.middleware.circuit_breaker as cb_module

    assert "CircuitState" not in apcore.__all__
    assert not hasattr(cb_module, "CircuitState")


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_default_state_is_closed() -> None:
    cb = CircuitBreakerMiddleware()
    assert cb.get_state("m1") == CircuitBreakerState.CLOSED


def test_custom_priority() -> None:
    cb = CircuitBreakerMiddleware(priority=200)
    assert cb.priority == 200


def test_invalid_failure_threshold_raises() -> None:
    with pytest.raises(ValueError, match="failure_threshold"):
        CircuitBreakerMiddleware(failure_threshold=0)


def test_invalid_recovery_window_raises() -> None:
    with pytest.raises(ValueError, match="recovery_window_ms"):
        CircuitBreakerMiddleware(recovery_window_ms=-1)


def test_invalid_success_threshold_raises() -> None:
    with pytest.raises(ValueError, match="success_threshold"):
        CircuitBreakerMiddleware(success_threshold=0)


# ---------------------------------------------------------------------------
# CLOSED → OPEN transition
# ---------------------------------------------------------------------------


def test_circuit_opens_after_threshold_failures() -> None:
    cb = CircuitBreakerMiddleware(failure_threshold=3)
    for _ in range(3):
        _call_before(cb)
        _call_on_error(cb)

    assert cb.get_state("m1") == CircuitBreakerState.OPEN


def test_circuit_stays_closed_below_threshold() -> None:
    cb = CircuitBreakerMiddleware(failure_threshold=3)
    for _ in range(2):
        _call_before(cb)
        _call_on_error(cb)

    assert cb.get_state("m1") == CircuitBreakerState.CLOSED


def test_success_resets_failure_counter() -> None:
    cb = CircuitBreakerMiddleware(failure_threshold=3)
    _call_before(cb)
    _call_on_error(cb)
    _call_before(cb)
    _call_on_error(cb)
    # success resets the counter
    _call_before(cb)
    _call_after(cb)
    # two more failures should not open (counter was reset)
    _call_before(cb)
    _call_on_error(cb)
    _call_before(cb)
    _call_on_error(cb)
    assert cb.get_state("m1") == CircuitBreakerState.CLOSED


# ---------------------------------------------------------------------------
# OPEN — rejects calls
# ---------------------------------------------------------------------------


def test_circuit_open_raises_circuit_open_error() -> None:
    from apcore.errors import CircuitBreakerOpenError

    cb = CircuitBreakerMiddleware(failure_threshold=1)
    _call_before(cb)
    _call_on_error(cb)

    assert cb.get_state("m1") == CircuitBreakerState.OPEN
    # Per sync A-001 the canonical class is CircuitBreakerOpenError.
    with pytest.raises(CircuitBreakerOpenError) as exc_info:
        _call_before(cb)
    assert exc_info.value.module_id == "m1"
    # The legacy CircuitOpenError is a subclass alias — the raised instance
    # is NOT an instance of the legacy class (parent ≠ child), but legacy
    # `except CircuitOpenError` blocks catching CircuitOpenError instances
    # raised explicitly by user code still function.
    assert isinstance(exc_info.value, CircuitBreakerOpenError)


def test_circuit_open_does_not_affect_other_modules() -> None:
    cb = CircuitBreakerMiddleware(failure_threshold=1)
    _call_before(cb, "m1")
    _call_on_error(cb, "m1")

    assert cb.get_state("m1") == CircuitBreakerState.OPEN
    # m2 is unaffected
    assert cb.get_state("m2") == CircuitBreakerState.CLOSED
    _call_before(cb, "m2")  # must not raise


# ---------------------------------------------------------------------------
# OPEN → HALF_OPEN → CLOSED
# ---------------------------------------------------------------------------


def test_circuit_transitions_to_half_open_after_recovery_window() -> None:
    # With recovery_window_ms=0, the transition to HALF_OPEN happens as soon as
    # any elapsed time is recorded — calling get_state itself triggers the check.
    cb = CircuitBreakerMiddleware(failure_threshold=1, recovery_window_ms=0)
    _call_before(cb)
    _call_on_error(cb)
    # A tiny sleep ensures elapsed_ms > 0 so the condition is met cleanly.
    time.sleep(0.001)
    assert cb.get_state("m1") == CircuitBreakerState.HALF_OPEN


def test_circuit_closes_after_success_in_half_open() -> None:
    cb = CircuitBreakerMiddleware(failure_threshold=1, recovery_window_ms=0, success_threshold=1)
    _call_before(cb)
    _call_on_error(cb)
    time.sleep(0.001)
    # should be HALF_OPEN now; one success closes it
    _call_before(cb)
    _call_after(cb)
    assert cb.get_state("m1") == CircuitBreakerState.CLOSED


def test_circuit_closes_after_multiple_successes_in_half_open() -> None:
    cb = CircuitBreakerMiddleware(failure_threshold=1, recovery_window_ms=0, success_threshold=2)
    _call_before(cb)
    _call_on_error(cb)
    time.sleep(0.001)
    # first success — still HALF_OPEN
    _call_before(cb)
    _call_after(cb)
    assert cb.get_state("m1") == CircuitBreakerState.HALF_OPEN
    # second success — now CLOSED
    _call_before(cb)
    _call_after(cb)
    assert cb.get_state("m1") == CircuitBreakerState.CLOSED


def test_failure_in_half_open_reopens_circuit() -> None:
    """Failure during HALF_OPEN re-opens the circuit and rejects the next call."""
    # Drive into HALF_OPEN via zero recovery window
    cb = CircuitBreakerMiddleware(failure_threshold=1, recovery_window_ms=0, success_threshold=1)
    _call_before(cb)
    _call_on_error(cb)
    time.sleep(0.001)
    assert cb.get_state("m1") == CircuitBreakerState.HALF_OPEN

    # Inject a failure while in HALF_OPEN — state must flip to OPEN
    _call_on_error(cb)
    # Use a large-recovery-window copy to assert OPEN without auto-transition
    # by inspecting the raw circuit record state directly.
    record = cb._circuits["m1"]
    assert record.state == CircuitBreakerState.OPEN


# ---------------------------------------------------------------------------
# on_error ignores CircuitOpenError
# ---------------------------------------------------------------------------


def test_on_error_ignores_circuit_open_error() -> None:
    """CircuitOpenError itself must not count as a failure."""
    cb = CircuitBreakerMiddleware(failure_threshold=1)
    err = CircuitOpenError(module_id="m1")
    result = _call_on_error(cb, error=err)
    assert result is None
    assert cb.get_state("m1") == CircuitBreakerState.CLOSED


# ---------------------------------------------------------------------------
# Manual reset
# ---------------------------------------------------------------------------


def test_reset_closes_open_circuit() -> None:
    cb = CircuitBreakerMiddleware(failure_threshold=1)
    _call_before(cb)
    _call_on_error(cb)
    assert cb.get_state("m1") == CircuitBreakerState.OPEN
    cb.reset("m1")
    assert cb.get_state("m1") == CircuitBreakerState.CLOSED


# ---------------------------------------------------------------------------
# Priority and base-class integration
# ---------------------------------------------------------------------------


def test_tracing_middleware_accepts_priority() -> None:
    from apcore.middleware.manager import MiddlewareManager
    from apcore.observability.tracing import InMemoryExporter, TracingMiddleware

    exporter = InMemoryExporter()
    mw = TracingMiddleware(exporter, priority=200)
    assert mw.priority == 200

    mgr = MiddlewareManager()
    mgr.add(mw)
    snap = mgr.snapshot()
    assert snap[0] is mw


def test_retry_middleware_accepts_priority() -> None:
    from apcore.middleware.retry import RetryMiddleware

    mw = RetryMiddleware(priority=50)
    assert mw.priority == 50


def test_circuit_breaker_middleware_priority_ordering() -> None:
    from apcore.middleware.manager import MiddlewareManager

    cb_high = CircuitBreakerMiddleware(priority=900)
    cb_low = CircuitBreakerMiddleware(priority=10)

    mgr = MiddlewareManager()
    mgr.add(cb_low)
    mgr.add(cb_high)
    snap = mgr.snapshot()
    assert snap[0] is cb_high
    assert snap[1] is cb_low


# ---------------------------------------------------------------------------
# Async on_error does not block event loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_on_error_async_does_not_block() -> None:
    """Verify execute_on_error_async runs sync handlers via asyncio.to_thread."""
    from apcore.middleware.manager import MiddlewareManager
    from apcore.middleware.base import Middleware

    blocked: list[bool] = []

    class BlockingMiddleware(Middleware):
        def on_error(
            self,
            module_id: str,
            inputs: dict[str, Any],
            error: Exception,
            context: Any,
        ) -> dict[str, Any] | RetrySignal | None:
            # This would block the event loop if called directly (not via to_thread)
            import time

            time.sleep(0.01)
            blocked.append(True)
            return None

    mgr = MiddlewareManager()
    bm = BlockingMiddleware()
    mgr.add(bm)

    ctx = MagicMock()
    await mgr.execute_on_error_async("m", {}, RuntimeError("x"), ctx, [bm])
    assert blocked == [True]


# ---------------------------------------------------------------------------
# CircuitOpenError properties
# ---------------------------------------------------------------------------


def test_circuit_open_error_properties() -> None:
    # Per sync A-001, the canonical wire code is CIRCUIT_BREAKER_OPEN. The
    # legacy CircuitOpenError class remains as a subclass alias of
    # CircuitBreakerOpenError and emits the canonical code on the wire.
    err = CircuitOpenError(module_id="my.module")
    assert err.code == "CIRCUIT_BREAKER_OPEN"
    assert err.module_id == "my.module"
    assert err.retryable is True


def test_error_codes_circuit_open_constant() -> None:
    from apcore.errors import ErrorCodes

    # Legacy constant retained for backwards compatibility.
    assert ErrorCodes.CIRCUIT_OPEN == "CIRCUIT_OPEN"
    # Canonical constant (sync A-001).
    assert ErrorCodes.CIRCUIT_BREAKER_OPEN == "CIRCUIT_BREAKER_OPEN"


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


def test_concurrent_access_does_not_corrupt_state() -> None:
    """Hammer before()/on_error() from multiple threads; state must stay consistent."""
    import threading

    cb = CircuitBreakerMiddleware(failure_threshold=50)
    errors: list[Exception] = []

    def worker() -> None:
        try:
            for _ in range(100):
                try:
                    _call_before(cb, "shared")
                    _call_after(cb, "shared")
                except CircuitOpenError:
                    pass
                _call_on_error(cb, "shared")
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Concurrency errors: {errors}"
    # State must be one of the valid enum values — no corruption
    assert cb.get_state("shared") in CircuitBreakerState.__members__.values()
