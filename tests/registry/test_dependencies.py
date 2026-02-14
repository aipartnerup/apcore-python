"""Tests for dependency resolution via Kahn's topological sort."""

from __future__ import annotations

import logging

import pytest

from apcore.errors import CircularDependencyError, ModuleLoadError
from apcore.registry.dependencies import resolve_dependencies
from apcore.registry.types import DependencyInfo


class TestNoDependencies:
    def test_no_deps_returns_all(self) -> None:
        """Modules with no dependencies all appear in output."""
        result = resolve_dependencies([("A", []), ("B", []), ("C", [])])
        assert set(result) == {"A", "B", "C"}


class TestSimpleOrdering:
    def test_a_depends_on_b(self) -> None:
        """A depends on B -> B before A."""
        result = resolve_dependencies(
            [
                ("A", [DependencyInfo(module_id="B")]),
                ("B", []),
            ]
        )
        assert result.index("B") < result.index("A")

    def test_chain(self) -> None:
        """Chain A -> B -> C -> order is C, B, A."""
        result = resolve_dependencies(
            [
                ("A", [DependencyInfo(module_id="B")]),
                ("B", [DependencyInfo(module_id="C")]),
                ("C", []),
            ]
        )
        assert result == ["C", "B", "A"]

    def test_diamond(self) -> None:
        """Diamond: A -> B,C; B,C -> D; D first, A last."""
        result = resolve_dependencies(
            [
                ("A", [DependencyInfo(module_id="B"), DependencyInfo(module_id="C")]),
                ("B", [DependencyInfo(module_id="D")]),
                ("C", [DependencyInfo(module_id="D")]),
                ("D", []),
            ]
        )
        assert result[0] == "D"
        assert result[-1] == "A"
        assert result.index("B") < result.index("A")
        assert result.index("C") < result.index("A")


class TestCircularDetection:
    def test_simple_cycle(self) -> None:
        """A -> B -> A raises CircularDependencyError."""
        with pytest.raises(CircularDependencyError) as exc_info:
            resolve_dependencies(
                [
                    ("A", [DependencyInfo(module_id="B")]),
                    ("B", [DependencyInfo(module_id="A")]),
                ]
            )
        assert "A" in exc_info.value.details["cycle_path"]
        assert "B" in exc_info.value.details["cycle_path"]

    def test_three_node_cycle(self) -> None:
        """A -> B -> C -> A raises CircularDependencyError."""
        with pytest.raises(CircularDependencyError) as exc_info:
            resolve_dependencies(
                [
                    ("A", [DependencyInfo(module_id="B")]),
                    ("B", [DependencyInfo(module_id="C")]),
                    ("C", [DependencyInfo(module_id="A")]),
                ]
            )
        path = exc_info.value.details["cycle_path"]
        assert "A" in path and "B" in path and "C" in path

    def test_partial_cycle_with_independent(self) -> None:
        """Partial cycle B <-> C with independent D raises CircularDependencyError."""
        with pytest.raises(CircularDependencyError) as exc_info:
            resolve_dependencies(
                [
                    ("A", [DependencyInfo(module_id="B")]),
                    ("B", [DependencyInfo(module_id="C")]),
                    ("C", [DependencyInfo(module_id="B")]),
                    ("D", []),
                ]
            )
        path = exc_info.value.details["cycle_path"]
        assert "B" in path and "C" in path


class TestOptionalDependencies:
    def test_missing_optional_dep_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        """Missing optional dep logs warning, still resolves."""
        with caplog.at_level(logging.WARNING):
            result = resolve_dependencies(
                [
                    ("A", [DependencyInfo(module_id="missing_dep", optional=True)]),
                ]
            )
        assert result == ["A"]
        assert "missing_dep" in caplog.text

    def test_missing_required_dep_raises(self) -> None:
        """Missing required dep raises ModuleLoadError."""
        with pytest.raises(ModuleLoadError):
            resolve_dependencies(
                [
                    ("A", [DependencyInfo(module_id="missing_dep", optional=False)]),
                ]
            )

    def test_optional_dep_present_included(self) -> None:
        """Optional dep present is included in ordering."""
        result = resolve_dependencies(
            [
                ("A", [DependencyInfo(module_id="B", optional=True)]),
                ("B", []),
            ]
        )
        assert result == ["B", "A"]


class TestEdgeCases:
    def test_empty_input(self) -> None:
        """Empty input returns empty list."""
        assert resolve_dependencies([]) == []

    def test_single_module(self) -> None:
        """Single module with no deps returns [module_id]."""
        assert resolve_dependencies([("A", [])]) == ["A"]
