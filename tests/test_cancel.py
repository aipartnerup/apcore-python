"""Tests for cooperative cancellation (CancelToken and ExecutionCancelledError)."""

from __future__ import annotations

from typing import Any

import pytest

from apcore.cancel import CancelToken, ExecutionCancelledError
from apcore.context import Context
from apcore.executor import Executor
from apcore.middleware.base import Middleware
from apcore.registry import Registry


class TestCancelToken:
    """Tests for CancelToken behavior."""

    def test_token_initially_not_cancelled(self) -> None:
        """New token has is_cancelled == False."""
        token = CancelToken()
        assert token.is_cancelled is False

    def test_cancel_sets_flag(self) -> None:
        """After cancel(), is_cancelled == True."""
        token = CancelToken()
        token.cancel()
        assert token.is_cancelled is True

    def test_check_does_nothing_when_not_cancelled(self) -> None:
        """check() does not raise when token is not cancelled."""
        token = CancelToken()
        token.check()  # Should not raise

    def test_check_raises_when_cancelled(self) -> None:
        """check() raises ExecutionCancelledError when cancelled."""
        token = CancelToken()
        token.cancel()
        with pytest.raises(ExecutionCancelledError):
            token.check()

    def test_reset_clears_cancellation(self) -> None:
        """After reset(), is_cancelled == False and check() does not raise."""
        token = CancelToken()
        token.cancel()
        assert token.is_cancelled is True
        token.reset()
        assert token.is_cancelled is False
        token.check()  # Should not raise

    def test_raise_if_cancelled_does_nothing_when_not_cancelled(self) -> None:
        """raise_if_cancelled() does not raise when token is not cancelled.

        Canonical spec method name (docs/features/cancellation.md, "Contract:
        CancelToken.raise_if_cancelled"), additive alongside check() — both
        names must behave identically.
        """
        token = CancelToken()
        token.raise_if_cancelled()  # Should not raise

    def test_raise_if_cancelled_raises_when_cancelled(self) -> None:
        """raise_if_cancelled() raises ExecutionCancelledError when cancelled."""
        token = CancelToken()
        token.cancel()
        with pytest.raises(ExecutionCancelledError):
            token.raise_if_cancelled()

    def test_raise_if_cancelled_and_check_agree(self) -> None:
        """Both names observe the same state — neither is a separate flag."""
        token = CancelToken()
        token.raise_if_cancelled()
        token.check()
        token.cancel()
        with pytest.raises(ExecutionCancelledError):
            token.check()
        with pytest.raises(ExecutionCancelledError):
            token.raise_if_cancelled()


class TestExecutorCancellation:
    """Tests for Executor respecting cancel tokens."""

    def test_executor_respects_cancellation(self) -> None:
        """Executor raises ExecutionCancelledError when cancel_token is cancelled."""

        class SimpleModule:
            input_schema = None
            output_schema = None

            def execute(self, inputs: dict[str, Any], context: Context) -> dict[str, Any]:
                return {"result": "ok"}

        registry = Registry()
        registry.register("test.module", SimpleModule())
        executor = Executor(registry=registry)

        token = CancelToken()
        token.cancel()
        ctx = Context.create(cancel_token=token)

        with pytest.raises(ExecutionCancelledError):
            executor.call("test.module", {}, context=ctx)


class _RecoveringMiddleware(Middleware):
    """on_error always recovers with a sentinel dict (records invocation)."""

    def __init__(self) -> None:
        super().__init__()
        self.on_error_called = False

    def on_error(
        self,
        module_id: str,
        inputs: dict[str, Any],
        error: Exception,
        context: Context,
    ) -> dict[str, Any] | None:
        self.on_error_called = True
        return {"recovered": True}


class _CancellingModule:
    """A module that raises ExecutionCancelledError mid-execution."""

    input_schema = None
    output_schema = None

    def execute(self, inputs: dict[str, Any], context: Context) -> dict[str, Any]:
        raise ExecutionCancelledError()


class TestCancellationShortCircuitsOnError:
    """A-D-003 / A-D-004 (D-20): step-raised ExecutionCancelledError must
    short-circuit BEFORE the on_error middleware recovery chain."""

    @pytest.mark.asyncio
    async def test_call_async_cancellation_skips_on_error(self) -> None:
        registry = Registry()
        registry.register("test.cancel", _CancellingModule())
        mw = _RecoveringMiddleware()
        executor = Executor(registry=registry, middlewares=[mw])

        ctx = Context.create()
        with pytest.raises(ExecutionCancelledError):
            await executor.call_async("test.cancel", {}, context=ctx)
        assert mw.on_error_called is False

    @pytest.mark.asyncio
    async def test_stream_cancellation_skips_on_error(self) -> None:
        registry = Registry()
        registry.register("test.cancel", _CancellingModule())
        mw = _RecoveringMiddleware()
        executor = Executor(registry=registry, middlewares=[mw])

        ctx = Context.create()
        with pytest.raises(ExecutionCancelledError):
            async for _chunk in executor.stream("test.cancel", {}, context=ctx):
                pass
        assert mw.on_error_called is False

    @pytest.mark.asyncio
    async def test_call_with_trace_cancellation_skips_on_error(self) -> None:
        """A-D-001 (D-19/D-20): call_with_trace must mirror call() — a
        step-raised ExecutionCancelledError must propagate directly and NOT be
        observed or swallowed by on_error middleware. The pipeline wraps the
        cancellation in a PipelineStepError, so the trace variant's
        ``except Exception`` path must still short-circuit it."""
        registry = Registry()
        registry.register("test.cancel", _CancellingModule())
        mw = _RecoveringMiddleware()
        executor = Executor(registry=registry, middlewares=[mw])

        ctx = Context.create()
        with pytest.raises(ExecutionCancelledError):
            await executor.call_async_with_trace("test.cancel", {}, context=ctx)
        assert mw.on_error_called is False
