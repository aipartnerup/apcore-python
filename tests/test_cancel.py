"""Tests for cooperative cancellation (CancelToken and ExecutionCancelledError)."""

from __future__ import annotations

from typing import Any

import pytest

from apcore.cancel import CancelToken, ExecutionCancelledError
from apcore.context import Context
from apcore.executor import Executor
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
