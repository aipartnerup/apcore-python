"""Helper callables for BindingLoader tests."""

from __future__ import annotations


def typed_function(name: str, count: int = 1) -> dict:
    """A simple typed function for binding tests."""
    return {"name": name, "count": count}


def untyped_function(name, count=1):  # noqa: ANN001, ANN201
    """Function with no type hints."""
    return {"name": name, "count": count}


class SimpleService:
    """Service class with no-arg constructor."""

    def greet(self, name: str) -> str:
        return f"Hello, {name}"


class ComplexService:
    """Service class requiring constructor args."""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def call(self) -> str:
        return "called"


NOT_CALLABLE = 42
