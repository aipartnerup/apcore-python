"""Tests for Executor.stream() async generator."""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest
from pydantic import BaseModel

from apcore.context import Context
from apcore.errors import ModuleNotFoundError
from apcore.executor import Executor
from apcore.middleware import Middleware
from apcore.registry import Registry


class SimpleInput(BaseModel):
    name: str


class SimpleOutput(BaseModel):
    greeting: str


class CountInput(BaseModel):
    count: int


class CountOutput(BaseModel):
    value: int = 0


class MockModule:
    def __init__(self) -> None:
        self.input_schema = SimpleInput
        self.output_schema = SimpleOutput

    def execute(self, inputs: dict[str, Any], context: Context) -> dict[str, Any]:
        return {"greeting": f"Hello, {inputs['name']}!"}


class StreamingModule:
    def __init__(self) -> None:
        self.input_schema = CountInput
        self.output_schema = CountOutput

    def execute(self, inputs: dict[str, Any], context: Context) -> dict[str, Any]:
        return {"value": inputs["count"]}

    async def stream(
        self, inputs: dict[str, Any], context: Context
    ) -> AsyncIterator[dict[str, Any]]:
        for i in range(1, inputs["count"] + 1):
            yield {"value": i}


class DisjointKeyModule:
    """Module that yields chunks with different keys."""

    def __init__(self) -> None:
        self.input_schema = None
        self.output_schema = None

    def execute(self, inputs: dict[str, Any], context: Context) -> dict[str, Any]:
        return {"a": "val_a", "b": "val_b"}

    async def stream(
        self, inputs: dict[str, Any], context: Context
    ) -> AsyncIterator[dict[str, Any]]:
        yield {"a": "val_a"}
        yield {"b": "val_b"}


def _make_executor(
    module: Any = None,
    module_id: str = "test.module",
    middlewares: list[Middleware] | None = None,
) -> Executor:
    reg = Registry()
    if module is not None:
        reg.register(module_id, module)
    return Executor(registry=reg, middlewares=middlewares)


class TestExecutorStream:
    async def test_fallback_single_chunk_when_no_stream(self) -> None:
        mod = MockModule()
        ex = _make_executor(module=mod)

        chunks: list[dict[str, Any]] = []
        async for chunk in ex.stream("test.module", {"name": "Alice"}):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0] == {"greeting": "Hello, Alice!"}

    async def test_yields_multiple_chunks(self) -> None:
        mod = StreamingModule()
        ex = _make_executor(module=mod, module_id="counter")

        chunks: list[dict[str, Any]] = []
        async for chunk in ex.stream("counter", {"count": 3}):
            chunks.append(chunk)

        assert len(chunks) == 3
        assert chunks[0] == {"value": 1}
        assert chunks[1] == {"value": 2}
        assert chunks[2] == {"value": 3}

    async def test_module_not_found(self) -> None:
        ex = _make_executor()

        with pytest.raises(ModuleNotFoundError):
            async for _ in ex.stream("nonexistent"):
                pass

    async def test_before_and_after_middleware(self) -> None:
        mod = StreamingModule()
        log: list[str] = []

        class TrackingMiddleware(Middleware):
            def before(
                self,
                module_id: str,
                inputs: dict[str, Any],
                context: Context,
            ) -> dict[str, Any] | None:
                log.append("before")
                return None

            def after(
                self,
                module_id: str,
                inputs: dict[str, Any],
                output: dict[str, Any],
                context: Context,
            ) -> dict[str, Any] | None:
                log.append("after")
                return None

        ex = _make_executor(
            module=mod, module_id="counter", middlewares=[TrackingMiddleware()]
        )

        chunks: list[dict[str, Any]] = []
        async for chunk in ex.stream("counter", {"count": 2}):
            chunks.append(chunk)

        assert len(chunks) == 2
        assert log == ["before", "after"]

    async def test_accumulates_via_shallow_merge(self) -> None:
        mod = DisjointKeyModule()

        after_output: dict[str, Any] = {}

        class CaptureAfter(Middleware):
            def after(
                self,
                module_id: str,
                inputs: dict[str, Any],
                output: dict[str, Any],
                context: Context,
            ) -> dict[str, Any] | None:
                nonlocal after_output
                after_output = dict(output)
                return None

        ex = _make_executor(
            module=mod, module_id="disjoint", middlewares=[CaptureAfter()]
        )

        chunks: list[dict[str, Any]] = []
        async for chunk in ex.stream("disjoint", {}):
            chunks.append(chunk)

        assert len(chunks) == 2
        assert chunks[0] == {"a": "val_a"}
        assert chunks[1] == {"b": "val_b"}
        # After-middleware receives the merged result
        assert after_output == {"a": "val_a", "b": "val_b"}
