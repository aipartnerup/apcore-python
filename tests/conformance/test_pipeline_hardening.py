"""Conformance tests for Pipeline Hardening (Issue #33, core-executor.md §Pipeline Hardening).

Exercises five fixture cases:
  fail_fast_on_step_error        — §1.1 fail-fast error wrapping
  continue_on_ignored_error      — §1.1 ignore_errors: true continues
  replace_semantic_no_duplicate  — §1.2 configure_step is idempotent
  run_until_stops_early          — §1.4 run_until predicate halts pipeline
  step_lookup_is_not_linear      — §1.5 O(1) _name_to_idx map present
"""

from __future__ import annotations

from typing import Any

import pytest

from apcore.pipeline import (
    BaseStep,
    ExecutionStrategy,
    PipelineContext,
    PipelineEngine,
    PipelineState,
    PipelineStepError,
    PipelineStepNotFoundError,
    StepResult,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _ContinueStep(BaseStep):
    async def execute(self, ctx: PipelineContext) -> StepResult:
        return StepResult(action="continue")


class _RaisingStep(BaseStep):
    async def execute(self, ctx: PipelineContext) -> StepResult:
        raise ValueError(f"step '{self.name}' intentionally raised")


def _make_simple_strategy(step_names: list[str]) -> ExecutionStrategy:
    steps = [_ContinueStep(n, f"step {n}") for n in step_names]
    return ExecutionStrategy("test", steps)


# ---------------------------------------------------------------------------
# Fixture 1: fail_fast_on_step_error
# ---------------------------------------------------------------------------


class TestFailFastOnStepError:
    """§1.1 — step raises exception → pipeline stops, PipelineStepError raised."""

    @pytest.mark.asyncio
    async def test_fail_fast_on_step_error(self) -> None:
        # Pipeline: context_creation → module_lookup → validate_input (raises)
        steps = [
            _ContinueStep("context_creation"),
            _ContinueStep("module_lookup"),
            _RaisingStep("validate_input"),
            _ContinueStep("execute"),
        ]
        strategy = ExecutionStrategy("test", steps)
        ctx = PipelineContext(module_id="demo.process", inputs={}, context=None)
        engine = PipelineEngine()

        with pytest.raises(PipelineStepError) as exc_info:
            await engine.run(strategy, ctx)

        err = exc_info.value
        assert err.code == "PIPELINE_STEP_ERROR"
        assert err.step_name == "validate_input"
        assert isinstance(err.cause, ValueError)
        assert err.pipeline_trace is not None

        executed = [s.name for s in err.pipeline_trace.steps if not s.skipped]
        assert "context_creation" in executed
        assert "module_lookup" in executed
        assert "validate_input" in executed
        assert "execute" not in executed

    @pytest.mark.asyncio
    async def test_fail_fast_stops_subsequent_steps(self) -> None:
        steps_run: list[str] = []

        class _TrackingStep(BaseStep):
            async def execute(self, ctx: PipelineContext) -> StepResult:
                steps_run.append(self.name)
                return StepResult(action="continue")

        class _TrackingRaisingStep(BaseStep):
            async def execute(self, ctx: PipelineContext) -> StepResult:
                steps_run.append(self.name)
                raise RuntimeError("boom")

        steps = [
            _TrackingStep("step_a"),
            _TrackingRaisingStep("step_b"),
            _TrackingStep("step_c"),
        ]
        strategy = ExecutionStrategy("test", steps)
        ctx = PipelineContext(module_id="m", inputs={}, context=None)
        engine = PipelineEngine()

        with pytest.raises(PipelineStepError):
            await engine.run(strategy, ctx)

        assert "step_a" in steps_run
        assert "step_b" in steps_run
        assert "step_c" not in steps_run


# ---------------------------------------------------------------------------
# Fixture 2: continue_on_ignored_error
# ---------------------------------------------------------------------------


class TestContinueOnIgnoredError:
    """§1.1 — ignore_errors: true → step failure logs warning, pipeline continues."""

    @pytest.mark.asyncio
    async def test_continue_on_ignored_error(self) -> None:
        steps_run: list[str] = []

        class _TrackingStep(BaseStep):
            async def execute(self, ctx: PipelineContext) -> StepResult:
                steps_run.append(self.name)
                return StepResult(action="continue")

        class _IgnoredRaisingStep(BaseStep):
            async def execute(self, ctx: PipelineContext) -> StepResult:
                steps_run.append(self.name)
                raise ValueError("ignored error")

        steps = [
            _TrackingStep("step_a"),
            _IgnoredRaisingStep("validate_input", ignore_errors=True),
            _TrackingStep("step_c"),
        ]
        strategy = ExecutionStrategy("test", steps)
        ctx = PipelineContext(module_id="demo.process", inputs={}, context=None, output={"result": 42})
        engine = PipelineEngine()

        result, trace = await engine.run(strategy, ctx)

        assert result == {"result": 42}
        assert "validate_input" in steps_run
        assert "step_c" in steps_run
        assert trace.success is True

        ignored_step = next(s for s in trace.steps if s.name == "validate_input")
        assert ignored_step.skip_reason == "error_ignored"

    @pytest.mark.asyncio
    async def test_ignored_step_output_is_absent(self) -> None:
        """Step with ignore_errors does not set ctx.output; downstream sees prior value."""

        class _SetOutputStep(BaseStep):
            async def execute(self, ctx: PipelineContext) -> StepResult:
                ctx.output = {"prior": True}
                return StepResult(action="continue")

        class _IgnoredRaisingStep(BaseStep):
            async def execute(self, ctx: PipelineContext) -> StepResult:
                raise RuntimeError("ignored")

        steps = [
            _SetOutputStep("set_output"),
            _IgnoredRaisingStep("bad_step", ignore_errors=True),
        ]
        strategy = ExecutionStrategy("test", steps)
        ctx = PipelineContext(module_id="m", inputs={}, context=None)
        engine = PipelineEngine()

        result, trace = await engine.run(strategy, ctx)
        assert result == {"prior": True}
        assert trace.success is True


# ---------------------------------------------------------------------------
# Fixture 3: replace_semantic_no_duplicate
# ---------------------------------------------------------------------------


class TestReplaceSemanticNoDuplicate:
    """§1.2 — configure_step is idempotent: calling twice yields exactly one step."""

    def test_configure_step_no_duplicate(self) -> None:
        strategy = _make_simple_strategy(["a", "validate_input", "b"])

        new_step = _ContinueStep("validate_input", "custom validator")
        strategy.configure_step("validate_input", new_step)
        count = sum(1 for s in strategy.steps if s.name == "validate_input")
        assert count == 1

    def test_configure_step_twice_still_one(self) -> None:
        strategy = _make_simple_strategy(["a", "validate_input", "b"])

        first = _ContinueStep("validate_input", "first replacement")
        second = _ContinueStep("validate_input", "second replacement")
        strategy.configure_step("validate_input", first)
        strategy.configure_step("validate_input", second)

        count = sum(1 for s in strategy.steps if s.name == "validate_input")
        assert count == 1
        assert strategy.steps[strategy._name_to_idx["validate_input"]] is second

    def test_configure_step_preserves_position(self) -> None:
        strategy = _make_simple_strategy(["a", "validate_input", "b"])
        original_idx = strategy._name_to_idx["validate_input"]

        new_step = _ContinueStep("validate_input", "replacement")
        strategy.configure_step("validate_input", new_step)

        assert strategy._name_to_idx["validate_input"] == original_idx

    def test_configure_step_not_found_raises(self) -> None:
        strategy = _make_simple_strategy(["a", "b"])
        with pytest.raises(PipelineStepNotFoundError) as exc_info:
            strategy.configure_step("nonexistent", _ContinueStep("nonexistent"))
        assert exc_info.value.code == "PIPELINE_STEP_NOT_FOUND"


# ---------------------------------------------------------------------------
# Fixture 4: run_until_stops_early
# ---------------------------------------------------------------------------


class TestRunUntilStopsEarly:
    """§1.4 — run_until predicate halts pipeline after matching step."""

    @pytest.mark.asyncio
    async def test_run_until_stops_after_module_lookup(self) -> None:
        steps_run: list[str] = []

        class _TrackingStep(BaseStep):
            async def execute(self, ctx: PipelineContext) -> StepResult:
                steps_run.append(self.name)
                return StepResult(action="continue")

        steps = [
            _TrackingStep("context_creation"),
            _TrackingStep("module_lookup"),
            _TrackingStep("execute"),
            _TrackingStep("return_result"),
        ]
        strategy = ExecutionStrategy("test", steps)
        ctx = PipelineContext(module_id="m", inputs={}, context=None)
        engine = PipelineEngine()

        def stop_after_module_lookup(state: PipelineState) -> bool:
            return state.step_name == "module_lookup"

        _, trace = await engine.run(strategy, ctx, run_until=stop_after_module_lookup)

        assert "context_creation" in steps_run
        assert "module_lookup" in steps_run
        assert "execute" not in steps_run
        assert "return_result" not in steps_run
        assert trace.success is True

    @pytest.mark.asyncio
    async def test_run_until_state_has_correct_outputs(self) -> None:
        observed_states: list[PipelineState] = []

        class _OutputStep(BaseStep):
            def __init__(self, name: str, value: Any) -> None:
                super().__init__(name)
                self._value = value

            async def execute(self, ctx: PipelineContext) -> StepResult:
                ctx.output = self._value
                return StepResult(action="continue")

        steps = [
            _OutputStep("step_a", {"a": 1}),
            _OutputStep("step_b", {"b": 2}),
        ]
        strategy = ExecutionStrategy("test", steps)
        ctx = PipelineContext(module_id="m", inputs={}, context=None)
        engine = PipelineEngine()

        def capture_and_continue(state: PipelineState) -> bool:
            observed_states.append(
                PipelineState(
                    step_name=state.step_name,
                    outputs=dict(state.outputs),
                    context=state.context,
                )
            )
            return False

        await engine.run(strategy, ctx, run_until=capture_and_continue)

        assert len(observed_states) == 2
        assert observed_states[0].step_name == "step_a"
        assert observed_states[0].outputs["step_a"] == {"a": 1}
        assert observed_states[1].step_name == "step_b"
        assert observed_states[1].outputs["step_b"] == {"b": 2}

    @pytest.mark.asyncio
    async def test_run_until_never_true_runs_full_pipeline(self) -> None:
        steps = [_ContinueStep(f"step_{i}") for i in range(5)]
        strategy = ExecutionStrategy("test", steps)
        ctx = PipelineContext(module_id="m", inputs={}, context=None)
        engine = PipelineEngine()

        _, trace = await engine.run(strategy, ctx, run_until=lambda _: False)

        assert trace.success is True
        assert len([s for s in trace.steps if not s.skipped]) == 5

    @pytest.mark.asyncio
    async def test_run_until_via_pipeline_context(self) -> None:
        """run_until can be passed through PipelineContext.run_until."""
        steps_run: list[str] = []

        class _TrackStep(BaseStep):
            async def execute(self, ctx: PipelineContext) -> StepResult:
                steps_run.append(self.name)
                return StepResult(action="continue")

        steps = [_TrackStep("a"), _TrackStep("b"), _TrackStep("c")]
        strategy = ExecutionStrategy("test", steps)
        ctx = PipelineContext(
            module_id="m",
            inputs={},
            context=None,
            run_until=lambda state: state.step_name == "b",
        )
        engine = PipelineEngine()

        await engine.run(strategy, ctx)

        assert steps_run == ["a", "b"]


# ---------------------------------------------------------------------------
# Fixture 5: step_lookup_is_not_linear (O(1) compliance)
# ---------------------------------------------------------------------------


class TestStepLookupIsNotLinear:
    """§1.5 — ExecutionStrategy maintains a _name_to_idx hash map."""

    def test_name_to_idx_exists_and_is_dict(self) -> None:
        strategy = _make_simple_strategy(["a", "b", "c"])
        assert isinstance(strategy._name_to_idx, dict)

    def test_name_to_idx_covers_all_steps(self) -> None:
        names = ["context_creation", "module_lookup", "execute", "return_result"]
        strategy = _make_simple_strategy(names)
        assert set(strategy._name_to_idx.keys()) == set(names)

    def test_name_to_idx_has_correct_indices(self) -> None:
        names = ["a", "b", "c", "d"]
        strategy = _make_simple_strategy(names)
        for expected_idx, name in enumerate(names):
            assert strategy._name_to_idx[name] == expected_idx

    def test_name_to_idx_updated_after_insert(self) -> None:
        strategy = _make_simple_strategy(["a", "c"])
        strategy.insert_after("a", _ContinueStep("b"))
        assert strategy._name_to_idx["a"] == 0
        assert strategy._name_to_idx["b"] == 1
        assert strategy._name_to_idx["c"] == 2

    def test_name_to_idx_updated_after_remove(self) -> None:
        strategy = _make_simple_strategy(["a", "b", "c"])
        strategy.remove("b")
        assert "b" not in strategy._name_to_idx
        assert strategy._name_to_idx["a"] == 0
        assert strategy._name_to_idx["c"] == 1

    def test_name_to_idx_updated_after_replace(self) -> None:
        strategy = _make_simple_strategy(["a", "b", "c"])
        strategy.replace("b", _ContinueStep("b", "replaced"))
        assert strategy._name_to_idx["b"] == 1

    @pytest.mark.asyncio
    async def test_skip_to_uses_o1_lookup(self) -> None:
        """skip_to with 9 steps uses O(1) lookup (verified by behavior)."""

        class _SkipStep(BaseStep):
            def __init__(self, name: str, target: str) -> None:
                super().__init__(name)
                self._target = target

            async def execute(self, ctx: PipelineContext) -> StepResult:
                return StepResult(action="skip_to", skip_to=self._target)

        base_steps: list[BaseStep] = [_ContinueStep(f"step_{i}") for i in range(9)]
        base_steps[0] = _SkipStep("step_0", "step_8")
        strategy = ExecutionStrategy("test", base_steps)
        ctx = PipelineContext(module_id="m", inputs={}, context=None)
        engine = PipelineEngine()

        _, trace = await engine.run(strategy, ctx)

        executed_non_skipped = [s.name for s in trace.steps if not s.skipped]
        assert "step_0" in executed_non_skipped
        assert "step_8" in executed_non_skipped
        skipped = [s.name for s in trace.steps if s.skipped]
        for i in range(1, 8):
            assert f"step_{i}" in skipped

    @pytest.mark.asyncio
    async def test_skip_to_backward_target_raises(self) -> None:
        """skip_to a target at or before the current step raises StepNotFoundError.

        Before the O(1) refactor, the linear scan implicitly rejected backward
        targets because it only searched forward from i+1. The O(1) lookup must
        enforce the same guard explicitly to prevent infinite loops.
        """

        class _BackwardSkipStep(BaseStep):
            async def execute(self, ctx: PipelineContext) -> StepResult:
                return StepResult(action="skip_to", skip_to="step_a")

        steps: list[BaseStep] = [
            _ContinueStep("step_a"),
            _ContinueStep("step_b"),
            _BackwardSkipStep("step_c"),
        ]
        strategy = ExecutionStrategy("test", steps)
        ctx = PipelineContext(module_id="m", inputs={}, context=None)
        engine = PipelineEngine()

        from apcore.pipeline import StepNotFoundError

        with pytest.raises(StepNotFoundError):
            await engine.run(strategy, ctx)
