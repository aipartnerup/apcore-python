"""Drive `pipeline_failfast_config.json` — pipeline config fail-fast (Issue #33).

middleware-system.md § Configuration safety makes two MUSTs:

* ``PIPELINE_CONFIGURATION_ERROR`` at parse time when a key of the
  ``pipeline.configure`` MAP references an unknown step,
* ``PIPELINE_DEPENDENCY_ERROR`` at strategy construction when a ``requires:``
  is unsatisfied — never deferred to the first ``call()``.

Two things this file used to get wrong, both now fixed in the canonical
fixture rather than worked around here:

* ``pipeline.configure`` was driven as a LIST of ``{name, ...}`` entries. It is
  an object map keyed by step name — what ``$defs/PipelineConfig`` declares and
  what all three SDKs parse.
* A third case drove ``pipeline.step_middleware:``, a config section no SDK has
  ever parsed. It was removed from the fixture, not xfailed here.

Assert the WIRE CODE, not the class name: all three SDKs name the class
``ConfigurationError`` while they emitted three different codes, so a
class-name assertion passed everywhere and proved nothing.
"""

from __future__ import annotations

from typing import Any

import pytest

from apcore.pipeline import (
    BaseStep,
    ConfigurationError,
    ExecutionStrategy,
    PipelineContext,
    PipelineDependencyError,
    StepResult,
)
from apcore.pipeline_config import build_strategy_from_config

from .canonical_fixtures import load_fixture

FIXTURE = load_fixture("pipeline_failfast_config.json")
CASES: dict[str, dict[str, Any]] = {tc["id"]: tc for tc in FIXTURE["test_cases"]}


class _Step(BaseStep):
    """Inert step used to assemble the fixture's `strategy.steps` declarations."""

    async def execute(self, ctx: PipelineContext) -> StepResult:
        return StepResult(action="continue")


class _StubRegistry:
    """`build_standard_strategy` only needs an object with these two methods."""

    def get(self, *args: Any, **kwargs: Any) -> None:
        return None

    def discover(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []


def _build_from_yaml(case: dict[str, Any]) -> Any:
    pipeline_section = case["input"]["yaml"]["pipeline"]
    return build_strategy_from_config(pipeline_section, registry=_StubRegistry())


def _strategy_from_fixture(case: dict[str, Any]) -> ExecutionStrategy:
    spec = case["input"]["strategy"]
    steps = [
        _Step(
            step["name"],
            requires=tuple(step.get("requires", ())),
            provides=tuple(step.get("provides", ())),
        )
        for step in spec["steps"]
    ]
    return ExecutionStrategy(spec["name"], steps)


def _assert_message_contains(case: dict[str, Any], message: str) -> None:
    needles = case["expected"]["error_message_contains"]
    if isinstance(needles, str):
        needles = [needles]
    for needle in needles:
        assert needle in message, (
            f"[{case['id']}] error message must name {needle!r}; got: {message}"
        )


class TestConfigParseFailFast:
    """`pipeline.configure` parse-time errors."""

    def test_missing_step_in_configure_raises_configuration_error(self) -> None:
        case = CASES["missing_step_in_configure_raises_configuration_error"]
        with pytest.raises(ConfigurationError) as excinfo:
            _build_from_yaml(case)
        _assert_message_contains(case, str(excinfo.value))
        expected_code = case["expected"]["error_code"]
        assert excinfo.value.code == expected_code, (
            f"[{case['id']}] the WIRE CODE is the contract, not the class name: "
            f"got {excinfo.value.code!r}, expected {expected_code!r}. All three SDKs name this "
            f"class ConfigurationError, so asserting the class name passes while the emitted "
            f"codes diverge."
        )


class TestStrategyConstructionFailFast:
    """`requires:` / `provides:` validation happens at construction, not at call()."""

    def test_unmet_requires_raises_pipeline_dependency_error(self) -> None:
        case = CASES["unmet_requires_raises_pipeline_dependency_error"]
        # OBSERVE the phase that raises. This used to read
        # `assert case["expected"]["raised_at"] == "strategy_construction"`,
        # which compares the fixture to a transcription of itself and passes
        # even if the SDK deferred validation to the first call()
        # (apcore-python#32 / aiperceivable/apcore#81).
        raised_at: str | None = None
        error: PipelineDependencyError | None = None
        strategy: ExecutionStrategy | None = None
        try:
            strategy = _strategy_from_fixture(case)
        except PipelineDependencyError as exc:
            raised_at, error = "strategy_construction", exc

        assert strategy is None, (
            f"[{case['id']}] construction returned a strategy — an unmet `requires:` was NOT "
            f"rejected at strategy construction, so it could only surface later at call()"
        )
        assert raised_at == case["expected"]["raised_at"], (
            f"[{case['id']}] the dependency failure must surface at "
            f"{case['expected']['raised_at']!r}, observed {raised_at!r}"
        )
        assert error is not None
        _assert_message_contains(case, str(error))
        assert error.step_name == "execute", (
            f"[{case['id']}] the error must name the dependent step, got {error.step_name!r}"
        )

    def test_satisfied_requires_constructs_successfully(self) -> None:
        case = CASES["satisfied_requires_constructs_successfully"]
        # Both `raises` and `strategy_callable` are now bound to observations;
        # they used to be asserted against literals, which cannot fail on SDK
        # behaviour (apcore-python#32 / aiperceivable/apcore#81).
        strategy: ExecutionStrategy | None = None
        raised: Exception | None = None
        try:
            strategy = _strategy_from_fixture(case)
        except Exception as exc:  # noqa: BLE001 - the fixture decides whether this may raise
            raised = exc

        assert (raised is not None) is case["expected"]["raises"], (
            f"[{case['id']}] a satisfied `requires:` must construct cleanly; got {raised!r}"
        )
        assert strategy is not None
        assert [s.name for s in strategy.steps] == [s["name"] for s in case["input"]["strategy"]["steps"]], (
            f"[{case['id']}] construction must preserve the declared step order"
        )
        # `strategy_callable`: every step is runnable, so the strategy can be
        # executed — construction validated cleanly and left nothing deferred.
        strategy_callable = all(callable(getattr(step, "execute", None)) for step in strategy.steps)
        assert strategy_callable is case["expected"]["strategy_callable"], (
            f"[{case['id']}] strategy_callable: every step must expose a callable execute()"
        )


def test_every_fixture_case_has_a_driver() -> None:
    driven = {
        "missing_step_in_configure_raises_configuration_error",
        "unmet_requires_raises_pipeline_dependency_error",
        "satisfied_requires_constructs_successfully",
    }
    assert set(CASES) == driven, (
        f"pipeline_failfast_config.json cases without a driver: {sorted(set(CASES) - driven)}; "
        f"drivers with no matching case: {sorted(driven - set(CASES))}"
    )
