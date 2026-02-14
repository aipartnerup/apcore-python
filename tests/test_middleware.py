"""Tests for the Middleware base class and function adapters."""

from __future__ import annotations

import abc
from typing import Any
from unittest.mock import MagicMock, Mock

from apcore.context import Context
from apcore.middleware import AfterMiddleware, BeforeMiddleware, Middleware


# === Middleware Base Class ===


class TestMiddlewareBase:
    """Tests for the Middleware base class."""

    def test_is_not_abc(self) -> None:
        """Middleware is a plain class, not an ABC."""
        assert not issubclass(Middleware, abc.ABC)

    def test_can_be_instantiated_directly(self) -> None:
        """Middleware can be instantiated without raising TypeError."""
        mw = Middleware()
        assert isinstance(mw, Middleware)

    def test_before_returns_none_by_default(self) -> None:
        """before() returns None by default."""
        mw = Middleware()
        ctx = MagicMock(spec=Context)
        result = mw.before("some.module", {"key": "val"}, ctx)
        assert result is None

    def test_after_returns_none_by_default(self) -> None:
        """after() returns None by default."""
        mw = Middleware()
        ctx = MagicMock(spec=Context)
        result = mw.after("some.module", {"key": "val"}, {"out": 1}, ctx)
        assert result is None

    def test_on_error_returns_none_by_default(self) -> None:
        """on_error() returns None by default."""
        mw = Middleware()
        ctx = MagicMock(spec=Context)
        result = mw.on_error("some.module", {"key": "val"}, RuntimeError("boom"), ctx)
        assert result is None

    def test_subclass_can_override_before_only(self) -> None:
        """Subclass overriding only before() leaves other methods as no-ops."""

        class MyMiddleware(Middleware):
            def before(self, module_id: str, inputs: dict[str, Any], context: Context) -> dict[str, Any] | None:
                return {"modified": True}

        mw = MyMiddleware()
        ctx = MagicMock(spec=Context)
        assert mw.before("mod", {}, ctx) == {"modified": True}
        assert mw.after("mod", {}, {}, ctx) is None
        assert mw.on_error("mod", {}, RuntimeError(), ctx) is None

    def test_subclass_can_override_all_methods(self) -> None:
        """Subclass can override all three methods with custom behavior."""

        class FullMiddleware(Middleware):
            def before(self, module_id: str, inputs: dict[str, Any], context: Context) -> dict[str, Any] | None:
                return {"before": True}

            def after(
                self,
                module_id: str,
                inputs: dict[str, Any],
                output: dict[str, Any],
                context: Context,
            ) -> dict[str, Any] | None:
                return {"after": True}

            def on_error(
                self,
                module_id: str,
                inputs: dict[str, Any],
                error: Exception,
                context: Context,
            ) -> dict[str, Any] | None:
                return {"error_handled": True}

        mw = FullMiddleware()
        ctx = MagicMock(spec=Context)
        assert mw.before("mod", {}, ctx) == {"before": True}
        assert mw.after("mod", {}, {}, ctx) == {"after": True}
        assert mw.on_error("mod", {}, RuntimeError(), ctx) == {"error_handled": True}


# === BeforeMiddleware Adapter ===


class TestBeforeMiddleware:
    """Tests for the BeforeMiddleware function adapter."""

    def test_is_middleware_subclass(self) -> None:
        """BeforeMiddleware wraps a callback as a Middleware subclass."""
        bm = BeforeMiddleware(lambda mid, inp, ctx: None)
        assert isinstance(bm, Middleware)

    def test_before_delegates_to_callback(self) -> None:
        """before() delegates to the wrapped callback."""
        callback = Mock(return_value={"modified": True})
        bm = BeforeMiddleware(callback)
        ctx = MagicMock(spec=Context)
        result = bm.before("mod.id", {"k": "v"}, ctx)
        assert result == {"modified": True}

    def test_after_returns_none(self) -> None:
        """after() returns None regardless of callback."""
        bm = BeforeMiddleware(lambda mid, inp, ctx: {"modified": True})
        ctx = MagicMock(spec=Context)
        assert bm.after("mod.id", {"k": "v"}, {"out": 1}, ctx) is None

    def test_on_error_returns_none(self) -> None:
        """on_error() returns None regardless of callback."""
        bm = BeforeMiddleware(lambda mid, inp, ctx: {"modified": True})
        ctx = MagicMock(spec=Context)
        assert bm.on_error("mod.id", {"k": "v"}, RuntimeError(), ctx) is None

    def test_callback_receives_correct_args(self) -> None:
        """Callback receives (module_id, inputs, context)."""
        spy = Mock(return_value=None)
        bm = BeforeMiddleware(spy)
        ctx = MagicMock(spec=Context)
        bm.before("mod.id", {"k": "v"}, ctx)
        spy.assert_called_once_with("mod.id", {"k": "v"}, ctx)


# === AfterMiddleware Adapter ===


class TestAfterMiddleware:
    """Tests for the AfterMiddleware function adapter."""

    def test_is_middleware_subclass(self) -> None:
        """AfterMiddleware wraps a callback as a Middleware subclass."""
        am = AfterMiddleware(lambda mid, inp, out, ctx: None)
        assert isinstance(am, Middleware)

    def test_after_delegates_to_callback(self) -> None:
        """after() delegates to the wrapped callback."""
        callback = Mock(return_value={"enriched": True})
        am = AfterMiddleware(callback)
        ctx = MagicMock(spec=Context)
        result = am.after("mod.id", {"k": "v"}, {"out": 1}, ctx)
        assert result == {"enriched": True}

    def test_before_returns_none(self) -> None:
        """before() returns None regardless of callback."""
        am = AfterMiddleware(lambda mid, inp, out, ctx: {"enriched": True})
        ctx = MagicMock(spec=Context)
        assert am.before("mod.id", {"k": "v"}, ctx) is None

    def test_on_error_returns_none(self) -> None:
        """on_error() returns None regardless of callback."""
        am = AfterMiddleware(lambda mid, inp, out, ctx: {"enriched": True})
        ctx = MagicMock(spec=Context)
        assert am.on_error("mod.id", {"k": "v"}, RuntimeError(), ctx) is None

    def test_callback_receives_correct_args(self) -> None:
        """Callback receives (module_id, inputs, output, context)."""
        spy = Mock(return_value=None)
        am = AfterMiddleware(spy)
        ctx = MagicMock(spec=Context)
        am.after("mod.id", {"k": "v"}, {"out": 1}, ctx)
        spy.assert_called_once_with("mod.id", {"k": "v"}, {"out": 1}, ctx)
