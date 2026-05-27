"""Tests for executor pipeline integration: strategy resolution, call_with_trace, introspection."""

from __future__ import annotations

from typing import Any

import pytest

from apcore.builtin_steps import (
    build_internal_strategy,
    build_performance_strategy,
    build_standard_strategy,
    build_testing_strategy,
)
from apcore.context import Context
from apcore.executor import Executor
from apcore.pipeline import (
    BaseStep,
    ExecutionStrategy,
    PipelineContext,
    PipelineTrace,
    StepResult,
    StrategyNotFoundError,
)
from apcore.registry import Registry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class EchoModule:
    """Minimal module that echoes input."""

    input_schema = None
    output_schema = None
    annotations = None

    def execute(self, inputs: dict[str, Any], context: Context) -> dict[str, Any]:
        return {"echo": "hello"}


def _make_registry() -> Registry:
    """Create a minimal registry with a dummy module."""
    reg = Registry()
    reg.register("test.echo", EchoModule())
    return reg


# ---------------------------------------------------------------------------
# Task 1: executor-refactor — strategy parameter
# ---------------------------------------------------------------------------


class TestExecutorStrategyParam:
    """Executor __init__ strategy parameter tests."""

    def test_no_strategy_defaults_to_standard(self) -> None:
        """Executor() with no strategy works as before (standard strategy)."""
        reg = _make_registry()
        ex = Executor(registry=reg)
        assert ex.current_strategy.name == "standard"
        assert len(ex.current_strategy.steps) == 11

    def test_strategy_string_internal(self) -> None:
        """Executor(strategy='internal') resolves to fewer steps."""
        reg = _make_registry()
        ex = Executor(registry=reg, strategy="internal")
        assert ex.current_strategy.name == "internal"
        step_names = ex.current_strategy.step_names()
        assert "acl_check" not in step_names
        assert "approval_gate" not in step_names
        assert len(step_names) == 9

    def test_strategy_string_testing(self) -> None:
        """Executor(strategy='testing') resolves to testing preset."""
        reg = _make_registry()
        ex = Executor(registry=reg, strategy="testing")
        assert ex.current_strategy.name == "testing"
        step_names = ex.current_strategy.step_names()
        assert "acl_check" not in step_names
        assert "approval_gate" not in step_names
        assert "call_chain_guard" not in step_names
        assert len(step_names) == 8

    def test_strategy_string_performance(self) -> None:
        """Executor(strategy='performance') resolves to performance preset."""
        reg = _make_registry()
        ex = Executor(registry=reg, strategy="performance")
        assert ex.current_strategy.name == "performance"
        step_names = ex.current_strategy.step_names()
        assert "middleware_before" not in step_names
        assert "middleware_after" not in step_names
        assert len(step_names) == 9

    def test_strategy_instance(self) -> None:
        """Executor(strategy=ExecutionStrategy(...)) uses the given instance."""
        reg = _make_registry()

        class NoopStep(BaseStep):
            async def execute(self, ctx: PipelineContext) -> StepResult:
                return StepResult(action="continue")

        custom = ExecutionStrategy("custom", [NoopStep("only", "Only step")])
        ex = Executor(registry=reg, strategy=custom)
        assert ex.current_strategy.name == "custom"
        assert ex.current_strategy.step_names() == ["only"]

    def test_strategy_unknown_name_raises(self) -> None:
        """Executor(strategy='nonexistent') raises StrategyNotFoundError."""
        reg = _make_registry()
        with pytest.raises(StrategyNotFoundError):
            Executor(registry=reg, strategy="nonexistent")

    def test_existing_call_still_works(self) -> None:
        """Executor.call() still works without strategy param (backward compat)."""
        reg = _make_registry()
        ex = Executor(registry=reg)
        result = ex.call("test.echo", {})
        assert result == {"echo": "hello"}


# ---------------------------------------------------------------------------
# Task 2: preset-strategies
# ---------------------------------------------------------------------------


class TestPresetStrategies:
    """Tests for build_internal/testing/performance_strategy."""

    def test_internal_strategy_steps(self) -> None:
        """Internal strategy removes acl_check and approval_gate."""
        reg = _make_registry()
        s = build_internal_strategy(registry=reg)
        assert s.name == "internal"
        names = s.step_names()
        assert "acl_check" not in names
        assert "approval_gate" not in names
        assert "context_creation" in names
        assert "execute" in names

    def test_testing_strategy_steps(self) -> None:
        """Testing strategy removes acl_check, approval_gate, safety_check."""
        reg = _make_registry()
        s = build_testing_strategy(registry=reg)
        assert s.name == "testing"
        names = s.step_names()
        assert "acl_check" not in names
        assert "approval_gate" not in names
        assert "call_chain_guard" not in names

    def test_performance_strategy_steps(self) -> None:
        """Performance strategy removes middleware_before and middleware_after."""
        reg = _make_registry()
        s = build_performance_strategy(registry=reg)
        assert s.name == "performance"
        names = s.step_names()
        assert "middleware_before" not in names
        assert "middleware_after" not in names

    def test_standard_strategy_unchanged(self) -> None:
        """Standard strategy still has 11 steps."""
        reg = _make_registry()
        s = build_standard_strategy(registry=reg)
        assert s.name == "standard"
        assert len(s.steps) == 11


# ---------------------------------------------------------------------------
# Task 3: call_with_trace
# ---------------------------------------------------------------------------


class TestCallWithTrace:
    """Tests for call_with_trace and call_async_with_trace."""

    def test_call_with_trace_returns_tuple(self) -> None:
        """call_with_trace returns (result, trace)."""
        reg = _make_registry()
        ex = Executor(registry=reg, strategy="testing")

        result, trace = ex.call_with_trace("test.echo", {"msg": "hi"})
        assert isinstance(result, dict)
        assert isinstance(trace, PipelineTrace)
        assert trace.module_id == "test.echo"
        assert trace.success is True
        assert trace.strategy_name == "testing"
        assert len(trace.steps) > 0

    @pytest.mark.asyncio
    async def test_call_async_with_trace_returns_tuple(self) -> None:
        """call_async_with_trace returns (result, trace)."""
        reg = _make_registry()
        ex = Executor(registry=reg, strategy="testing")

        result, trace = await ex.call_async_with_trace("test.echo", {"msg": "hi"})
        assert isinstance(result, dict)
        assert isinstance(trace, PipelineTrace)
        assert trace.success is True

    def test_call_with_trace_strategy_override(self) -> None:
        """call_with_trace with strategy= overrides default."""
        reg = _make_registry()
        ex = Executor(registry=reg, strategy="standard")

        _, trace = ex.call_with_trace("test.echo", {}, strategy="testing")
        assert trace.strategy_name == "testing"

    def test_call_with_trace_accepts_version_hint(self) -> None:
        """Sync finding A-D-005 (D-19): call_with_trace accepts and forwards
        version_hint, like call(), so the trace variant shares call()'s
        version-negotiation semantics."""
        reg = _make_registry()
        ex = Executor(registry=reg, strategy="testing")

        result, trace = ex.call_with_trace("test.echo", {"msg": "hi"}, version_hint="1.0.0")
        assert isinstance(result, dict)
        assert trace.success is True

    @pytest.mark.asyncio
    async def test_call_async_with_trace_accepts_version_hint(self) -> None:
        """Async variant also accepts version_hint (A-D-005 / D-19)."""
        reg = _make_registry()
        ex = Executor(registry=reg, strategy="testing")

        result, trace = await ex.call_async_with_trace(
            "test.echo", {"msg": "hi"}, version_hint="1.0.0"
        )
        assert isinstance(result, dict)
        assert trace.success is True


# ---------------------------------------------------------------------------
# Task 4: introspection
# ---------------------------------------------------------------------------


class TestIntrospection:
    """Tests for list_strategies, current_strategy, describe_pipeline, register_strategy."""

    def test_current_strategy_property(self) -> None:
        """current_strategy returns the strategy set at init."""
        reg = _make_registry()
        ex = Executor(registry=reg, strategy="internal")
        assert ex.current_strategy.name == "internal"

    def test_describe_pipeline_readable(self) -> None:
        """describe_pipeline returns a StrategyInfo with readable string representation."""
        reg = _make_registry()
        ex = Executor(registry=reg, strategy="testing")
        desc = ex.describe_pipeline()
        assert desc.step_count == 8
        assert "execute" in desc.step_names
        desc_str = str(desc)
        assert desc_str.startswith("8-step pipeline:")
        assert "\u2192" in desc_str

    def test_list_strategies_includes_current(self) -> None:
        """list_strategies includes the current strategy."""
        reg = _make_registry()
        ex = Executor(registry=reg)
        strategies = ex.list_strategies()
        names = [s.name for s in strategies]
        assert "standard" in names

    def test_register_strategy_makes_available(self) -> None:
        """register_strategy makes a strategy available by name."""

        class NoopStep(BaseStep):
            async def execute(self, ctx: PipelineContext) -> StepResult:
                return StepResult(action="continue")

        custom = ExecutionStrategy("my_custom", [NoopStep("a", "Step A")])

        try:
            Executor.register_strategy("my_custom", custom)
            reg = _make_registry()
            ex = Executor(registry=reg, strategy="my_custom")
            assert ex.current_strategy.name == "my_custom"
        finally:
            # Clean up class-level state
            Executor._registered_strategies.pop("my_custom", None)

    def test_list_strategies_includes_registered(self) -> None:
        """list_strategies includes registered strategies."""

        class NoopStep(BaseStep):
            async def execute(self, ctx: PipelineContext) -> StepResult:
                return StepResult(action="continue")

        custom = ExecutionStrategy("extra", [NoopStep("b", "Step B")])

        try:
            Executor.register_strategy("extra", custom)
            reg = _make_registry()
            ex = Executor(registry=reg)
            strategies = ex.list_strategies()
            names = [s.name for s in strategies]
            assert "standard" in names
            assert "extra" in names
        finally:
            Executor._registered_strategies.pop("extra", None)


# ---------------------------------------------------------------------------
# Regression: skip_to trace ordering (C5)
# ---------------------------------------------------------------------------


class TestSkipToTraceOrdering:
    """Verify trace ordering when a step returns action='skip_to'.

    The pipeline engine must record the skipping step itself first, then mark
    any steps between the skipper and the target as skipped, and finally
    execute the target. Regression for the "is target_idx step recorded
    twice" class of bugs in pipeline.py PipelineEngine.run.
    """

    @pytest.mark.asyncio
    async def test_skip_to_records_skipped_prelude_then_target(self) -> None:
        class SkipperStep(BaseStep):
            async def execute(self, ctx: PipelineContext) -> StepResult:
                return StepResult(action="skip_to", skip_to="target")

        class SkippedStep(BaseStep):
            called = False

            async def execute(self, ctx: PipelineContext) -> StepResult:
                type(self).called = True
                return StepResult(action="continue")

        class TargetStep(BaseStep):
            called = False

            async def execute(self, ctx: PipelineContext) -> StepResult:
                type(self).called = True
                return StepResult(action="continue")

        strategy = ExecutionStrategy(
            "skip_trace",
            [
                SkipperStep("skipper", "Skipper"),
                SkippedStep("between", "Between"),
                TargetStep("target", "Target"),
            ],
        )

        ctx = PipelineContext(module_id="x.y", inputs={}, context=None)
        from apcore.pipeline import PipelineEngine

        engine = PipelineEngine()
        _, trace = await engine.run(strategy, ctx)

        assert TargetStep.called is True
        assert SkippedStep.called is False  # never executed

        # Trace contains all three steps, in order: skipper, between (skipped), target
        names = [s.name for s in trace.steps]
        assert names == ["skipper", "between", "target"]

        # Confirm the between step is flagged as skipped.
        between = next(s for s in trace.steps if s.name == "between")
        assert between.skipped is True

        # The target step should NOT be marked skipped — it actually ran.
        target = next(s for s in trace.steps if s.name == "target")
        assert target.skipped is False
