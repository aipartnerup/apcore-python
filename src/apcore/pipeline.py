"""Execution pipeline types for configurable step-based module invocation."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from collections.abc import Sequence
from typing import Any, Callable, Protocol, runtime_checkable

from apcore.errors import ModuleError
from apcore.utils.pattern import match_pattern

_logger = logging.getLogger(__name__)


def _any_match(patterns: tuple[str, ...], module_id: str) -> bool:
    """Return True if any glob pattern matches the module_id (Algorithm A09)."""
    return any(match_pattern(p, module_id) for p in patterns)


@runtime_checkable
class Step(Protocol):
    """Protocol for pipeline steps."""

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def removable(self) -> bool: ...

    @property
    def replaceable(self) -> bool: ...

    async def execute(self, ctx: PipelineContext) -> StepResult: ...


class BaseStep(ABC):
    """Convenience base class for pipeline steps."""

    def __init__(
        self,
        name: str,
        description: str = "",
        *,
        removable: bool = True,
        replaceable: bool = True,
        match_modules: tuple[str, ...] | None = None,
        ignore_errors: bool = False,
        pure: bool = False,
        timeout_ms: int = 0,
        requires: tuple[str, ...] = (),
        provides: tuple[str, ...] = (),
    ) -> None:
        self.name = name
        self.description = description
        self.removable = removable
        self.replaceable = replaceable
        self.match_modules = match_modules
        self.ignore_errors = ignore_errors
        self.pure = pure
        self.timeout_ms = timeout_ms
        self.requires = requires
        self.provides = provides

    @abstractmethod
    async def execute(self, ctx: PipelineContext) -> StepResult: ...


@dataclass
class StepResult:
    """Result returned by a pipeline step execution."""

    action: str  # "continue", "skip_to", "abort"
    skip_to: str | None = None
    explanation: str | None = None
    confidence: float | None = None
    alternatives: list[str] | None = None


@dataclass
class PipelineContext:
    """Holds all state flowing through the pipeline."""

    module_id: str
    inputs: dict[str, Any]
    context: Any  # Context object
    module: Any | None = None
    validated_inputs: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    validated_output: dict[str, Any] | None = None
    stream: bool = False
    output_stream: Any | None = None
    strategy: ExecutionStrategy | None = None
    trace: PipelineTrace | None = None
    # New in v0.17
    dry_run: bool = False
    version_hint: str | None = None
    executed_middlewares: list[Any] = field(default_factory=list)
    # New in v0.20
    run_until: Callable[[PipelineState], bool] | None = None


@dataclass
class StepTrace:
    """Records execution details for a single step."""

    name: str
    duration_ms: float
    result: StepResult
    skipped: bool = False
    decision_point: bool = False
    skip_reason: str | None = None


@dataclass
class PipelineTrace:
    """Records execution details for the entire pipeline run."""

    module_id: str
    strategy_name: str
    steps: list[StepTrace] = field(default_factory=list)
    total_duration_ms: float = 0.0
    success: bool = False


@dataclass
class PipelineState:
    """Snapshot passed to run_until predicates after each step completes."""

    step_name: str
    outputs: dict[str, Any]
    context: PipelineContext


@runtime_checkable
class StepMiddleware(Protocol):
    """Protocol for pipeline step middlewares (Issue #33 §2.2).

    Step middlewares wrap individual pipeline steps the same way module
    middlewares wrap whole module calls.  All three hooks are optional and
    each method may be either synchronous or ``async``.

    Hook semantics:

    * ``before_step(step_name, state)`` — invoked just before the step runs.
    * ``after_step(step_name, state, result)`` — invoked after a successful step.
    * ``on_step_error(step_name, state, error)`` — invoked when a step raises.
      Returning a truthy value is treated as a *recovery result* (a
      :class:`StepResult`) and execution continues normally.  Returning
      ``None`` lets the original exception propagate.

    Both sync and async returns are supported: the engine awaits any return
    value that ``inspect.isawaitable()`` reports as awaitable, mirroring the
    pattern used by Issue #42's async ``on_error`` fix.
    """

    def before_step(self, step_name: str, state: "PipelineState") -> Any: ...

    def after_step(self, step_name: str, state: "PipelineState", result: "StepResult") -> Any: ...

    def on_step_error(self, step_name: str, state: "PipelineState", error: Exception) -> Any: ...


@dataclass
class StrategyInfo:
    """AI-introspectable description of an execution strategy."""

    name: str
    step_count: int
    step_names: list[str]
    description: str

    def __str__(self) -> str:
        return f"{self.step_count}-step pipeline: " + " \u2192 ".join(self.step_names)


class ExecutionStrategy:
    """An ordered sequence of steps that defines how a module is executed."""

    def __init__(
        self,
        name: str,
        steps: Sequence[Step],
        *,
        validate_dependencies: bool = True,
    ) -> None:
        """Construct an :class:`ExecutionStrategy`.

        Args:
            name: A human-readable name for the strategy (used in traces).
            steps: The ordered sequence of :class:`Step` instances.
            validate_dependencies: When ``True`` (default), a step whose
                ``requires`` keys are not provided by a preceding step's
                ``provides`` triggers a :class:`PipelineDependencyError`
                (Issue #33 §2.1). Internal callers that assemble derived
                strategies from a parent strategy already known to be
                consistent (e.g. the streaming executor splitting validation
                steps into a post-stream sub-strategy) may pass ``False`` to
                skip this check.
        """
        self.name = name
        self.steps = list(steps)
        # StepMiddlewares attached to this strategy (Issue #33 §2.2).
        # Lazily mutated via ``add_step_middleware``.
        self.step_middlewares: list[StepMiddleware] = []
        # Validate unique step names
        names = [s.name for s in self.steps]
        if len(names) != len(set(names)):
            dupes = [n for n in names if names.count(n) > 1]
            raise StepNameDuplicateError(f"Duplicate step names: {set(dupes)}")
        self._rebuild_index()
        self._validate_dependencies_enabled = validate_dependencies
        if validate_dependencies:
            self._validate_dependencies()

    def add_step_middleware(self, middleware: StepMiddleware) -> None:
        """Register a :class:`StepMiddleware` for this strategy.

        Middlewares are invoked in registration order for ``before_step`` and
        ``on_step_error``, and in reverse order for ``after_step`` (onion
        semantics).
        """
        self.step_middlewares.append(middleware)

    def _rebuild_index(self) -> None:
        """Rebuild the O(1) name→index map. Called after any mutation."""
        self._name_to_idx: dict[str, int] = {s.name: i for i, s in enumerate(self.steps)}

    def _validate_dependencies(self) -> None:
        """Raise :class:`PipelineDependencyError` if a step's ``requires`` are
        not satisfied by any preceding step's ``provides`` (Issue #33 §2.1).

        This is fail-fast: a misconfigured strategy must not silently produce
        runtime errors deep inside step execution.
        """
        provided: set[str] = set()
        for step in self.steps:
            requires: tuple[str, ...] = getattr(step, "requires", ())
            missing = set(requires) - provided
            if missing:
                raise PipelineDependencyError(
                    step_name=step.name,
                    missing=tuple(sorted(missing)),
                )
            provides: tuple[str, ...] = getattr(step, "provides", ())
            provided.update(provides)

    def insert_after(self, anchor: str, step: Step) -> None:
        """Insert a step after the named anchor step."""
        if step.name in self._name_to_idx:
            raise StepNameDuplicateError(f"Step '{step.name}' already exists")
        anchor_idx = self._name_to_idx.get(anchor)
        if anchor_idx is None:
            raise StepNotFoundError(f"Anchor step '{anchor}' not found")
        self.steps.insert(anchor_idx + 1, step)
        self._rebuild_index()
        if self._validate_dependencies_enabled:
            self._validate_dependencies()

    def insert_before(self, anchor: str, step: Step) -> None:
        """Insert a step before the named anchor step."""
        if step.name in self._name_to_idx:
            raise StepNameDuplicateError(f"Step '{step.name}' already exists")
        anchor_idx = self._name_to_idx.get(anchor)
        if anchor_idx is None:
            raise StepNotFoundError(f"Anchor step '{anchor}' not found")
        self.steps.insert(anchor_idx, step)
        self._rebuild_index()
        if self._validate_dependencies_enabled:
            self._validate_dependencies()

    def remove(self, step_name: str) -> None:
        """Remove a step by name. Raises if the step is not removable."""
        idx = self._name_to_idx.get(step_name)
        if idx is None:
            raise StepNotFoundError(f"Step '{step_name}' not found")
        if not self.steps[idx].removable:
            raise StepNotRemovableError(f"Step '{step_name}' is not removable")
        self.steps.pop(idx)
        self._rebuild_index()

    def replace(self, step_name: str, new_step: Step) -> None:
        """Replace a step by name. Raises if the step is not replaceable."""
        idx = self._name_to_idx.get(step_name)
        if idx is None:
            raise StepNotFoundError(f"Step '{step_name}' not found")
        if not self.steps[idx].replaceable:
            raise StepNotReplaceableError(f"Step '{step_name}' is not replaceable")
        self.steps[idx] = new_step
        self._rebuild_index()

    def configure_step(self, step_name: str, new_step: Step) -> None:
        """Replace a step by name (replace semantic: idempotent, no duplicate).

        Calling configure_step twice with the same step_name always leaves
        exactly one step at that position.
        """
        idx = self._name_to_idx.get(step_name)
        if idx is None:
            raise PipelineStepNotFoundError(step_name)
        if not self.steps[idx].replaceable:
            raise StepNotReplaceableError(f"Step '{step_name}' is not replaceable")
        self.steps[idx] = new_step
        self._rebuild_index()

    def step_names(self) -> list[str]:
        """Return the ordered list of step names."""
        return [s.name for s in self.steps]

    def info(self) -> StrategyInfo:
        """Return an AI-introspectable description of this strategy."""
        return StrategyInfo(
            name=self.name,
            step_count=len(self.steps),
            step_names=self.step_names(),
            description=" \u2192 ".join(self.step_names()),
        )


async def _invoke_step_mw_hook(
    middlewares: Sequence[StepMiddleware],
    hook: str,
    *args: Any,
    reverse: bool = False,
) -> Any:
    """Invoke ``hook`` on each middleware that defines it, awaiting awaitables.

    Returns the first non-None return value seen (used by ``on_step_error``
    for recovery); for ``before_step``/``after_step`` callers ignore the
    return.
    """
    iterable = reversed(middlewares) if reverse else middlewares
    last_value: Any = None
    for mw in iterable:
        fn = getattr(mw, hook, None)
        if fn is None:
            continue
        try:
            value = fn(*args)
            if inspect.isawaitable(value):
                value = await value
        except Exception:
            _logger.exception("StepMiddleware %r raised in %s", type(mw).__name__, hook)
            continue
        if value is not None:
            last_value = value
    return last_value


class PipelineEngine:
    """Executes a pipeline strategy step by step."""

    async def run(
        self,
        strategy: ExecutionStrategy,
        ctx: PipelineContext,
        *,
        run_until: Callable[[PipelineState], bool] | None = None,
    ) -> tuple[Any, PipelineTrace]:
        """Run all steps in the strategy, returning the final output and trace."""
        trace = PipelineTrace(
            module_id=ctx.module_id,
            strategy_name=strategy.name,
        )
        start = time.monotonic()
        steps = strategy.steps
        step_mws = strategy.step_middlewares
        name_to_idx = strategy._name_to_idx  # O(1) step lookup (§1.5)
        step_outputs: dict[str, Any] = {}
        effective_run_until = run_until or ctx.run_until
        i = 0

        # Index-based loop (not for-each) to support skip_to.
        while i < len(steps):
            step = steps[i]

            # ── Read declarative metadata (getattr for backward compat) ──
            match_modules = getattr(step, "match_modules", None)
            ignore_errors = getattr(step, "ignore_errors", False)
            pure = getattr(step, "pure", False)
            timeout_ms = getattr(step, "timeout_ms", 0)

            # (1) match_modules filter
            if match_modules is not None and not _any_match(match_modules, ctx.module_id):
                trace.steps.append(
                    StepTrace(
                        name=step.name,
                        duration_ms=0,
                        result=StepResult(action="continue"),
                        skipped=True,
                        skip_reason="no_match",
                    )
                )
                i += 1
                continue

            # (2) dry_run filter: skip impure steps
            if getattr(ctx, "dry_run", False) and not pure:
                trace.steps.append(
                    StepTrace(
                        name=step.name,
                        duration_ms=0,
                        result=StepResult(action="continue"),
                        skipped=True,
                        skip_reason="dry_run",
                    )
                )
                i += 1
                continue

            # (3) Execute with optional per-step timeout
            step_start = time.monotonic()

            # ── Step middleware: before_step ──
            if step_mws:
                pre_state = PipelineState(
                    step_name=step.name,
                    outputs=step_outputs,
                    context=ctx,
                )
                await _invoke_step_mw_hook(step_mws, "before_step", step.name, pre_state)

            try:
                if timeout_ms > 0:
                    result = await asyncio.wait_for(
                        step.execute(ctx),
                        timeout=timeout_ms / 1000,
                    )
                else:
                    result = await step.execute(ctx)
            except asyncio.TimeoutError:
                duration = (time.monotonic() - step_start) * 1000
                if ignore_errors:
                    _logger.warning(
                        "Step '%s' timed out after %dms (ignored)",
                        step.name,
                        timeout_ms,
                    )
                    trace.steps.append(
                        StepTrace(
                            name=step.name,
                            duration_ms=duration,
                            result=StepResult(
                                action="continue",
                                explanation=f"Timeout after {timeout_ms}ms (ignored)",
                            ),
                            skip_reason="error_ignored",
                        )
                    )
                    i += 1
                    continue
                trace.steps.append(
                    StepTrace(
                        name=step.name,
                        duration_ms=duration,
                        result=StepResult(
                            action="abort",
                            explanation=f"Step timed out after {timeout_ms}ms",
                        ),
                    )
                )
                trace.total_duration_ms = (time.monotonic() - start) * 1000
                raise PipelineAbortError(
                    step=step.name,
                    explanation=f"Step timed out after {timeout_ms}ms",
                    trace=trace,
                    abort_reason=AbortReason.MODULE_TIMEOUT,
                )
            except Exception as exc:
                duration = (time.monotonic() - step_start) * 1000

                # ── Step middleware: on_step_error ──
                # A non-None return is a recovery value (treated as the step's
                # ``StepResult``); None means propagate.
                recovery: Any = None
                if step_mws:
                    err_state = PipelineState(
                        step_name=step.name,
                        outputs=step_outputs,
                        context=ctx,
                    )
                    recovery = await _invoke_step_mw_hook(step_mws, "on_step_error", step.name, err_state, exc)

                if recovery is not None:
                    # Coerce non-StepResult recoveries (e.g. plain dicts) into
                    # a continue StepResult, matching middleware-on-error semantics.
                    if not isinstance(recovery, StepResult):
                        recovery = StepResult(
                            action="continue",
                            explanation=f"Recovered from {type(exc).__name__}",
                        )
                    result = recovery
                    # Fall through to the success path below.
                else:
                    # (4) ignore_errors: log and continue
                    if ignore_errors:
                        _logger.warning("Step '%s' failed (ignored): %s", step.name, exc)
                        trace.steps.append(
                            StepTrace(
                                name=step.name,
                                duration_ms=duration,
                                result=StepResult(
                                    action="continue",
                                    explanation=str(exc),
                                ),
                                skip_reason="error_ignored",
                            )
                        )
                        i += 1
                        continue
                    # Fail-fast (§1.1): wrap in PipelineStepError with step name and cause
                    trace.steps.append(
                        StepTrace(
                            name=step.name,
                            duration_ms=duration,
                            result=StepResult(action="abort", explanation=str(exc)),
                        )
                    )
                    trace.total_duration_ms = (time.monotonic() - start) * 1000
                    raise PipelineStepError(step_name=step.name, cause=exc, trace=trace) from exc

            # (5) Record successful step trace
            step_trace = StepTrace(
                name=step.name,
                duration_ms=(time.monotonic() - step_start) * 1000,
                result=result,
                decision_point=result.confidence is not None,
            )
            trace.steps.append(step_trace)

            # (6) Snapshot output for run_until predicates (shallow copy prevents
            # later in-place mutations from altering the historical record)
            step_outputs[step.name] = dict(ctx.output) if ctx.output is not None else None

            # ── Step middleware: after_step (reverse order, onion semantics) ──
            if step_mws:
                post_state = PipelineState(
                    step_name=step.name,
                    outputs=step_outputs,
                    context=ctx,
                )
                await _invoke_step_mw_hook(step_mws, "after_step", step.name, post_state, result, reverse=True)

            if result.action == "abort":
                trace.total_duration_ms = (time.monotonic() - start) * 1000
                raise PipelineAbortError(
                    step=step.name,
                    explanation=result.explanation,
                    alternatives=result.alternatives,
                    trace=trace,
                )
            elif result.action == "skip_to":
                target = result.skip_to
                if target is None:
                    raise StepNotFoundError("skip_to target is None")
                # O(1) lookup via pre-built index (§1.5)
                target_idx = name_to_idx.get(target)
                if target_idx is None or target_idx <= i:
                    raise StepNotFoundError(f"skip_to target '{target}' not found")
                # Record skipped steps in trace
                for j in range(i + 1, target_idx):
                    trace.steps.append(
                        StepTrace(
                            name=steps[j].name,
                            duration_ms=0,
                            result=StepResult(action="continue"),
                            skipped=True,
                            decision_point=False,
                        )
                    )
                i = target_idx
                continue

            # (7) run_until: evaluate predicate only on clean continue (§1.4)
            if effective_run_until is not None:
                state = PipelineState(
                    step_name=step.name,
                    outputs=step_outputs,
                    context=ctx,
                )
                if effective_run_until(state):
                    trace.success = True
                    trace.total_duration_ms = (time.monotonic() - start) * 1000
                    return ctx.output, trace

            i += 1

        trace.success = True
        trace.total_duration_ms = (time.monotonic() - start) * 1000
        # Use ctx.output — it's the most up-to-date (middleware_after may modify it)
        return ctx.output, trace


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


class AbortReason(str, Enum):
    """Stable, typed discriminator for why a pipeline step aborted.

    Prefer this over inspecting ``PipelineAbortError.explanation`` strings:
    the explanation is user-facing text that may change between releases,
    while ``AbortReason`` is a committed contract consumed by the executor
    when translating aborts back to typed errors.
    """

    MODULE_NOT_FOUND = "module_not_found"
    ACL_DENIED = "acl_denied"
    INPUT_VALIDATION_FAILED = "input_validation_failed"
    OUTPUT_VALIDATION_FAILED = "output_validation_failed"
    MODULE_CANCELLED = "module_cancelled"
    MODULE_TIMEOUT = "module_timeout"
    OTHER = "other"


class PipelineAbortError(ModuleError):
    """Raised when a pipeline is aborted at a step."""

    def __init__(
        self,
        step: str,
        explanation: str | None = None,
        alternatives: list[str] | None = None,
        trace: PipelineTrace | None = None,
        abort_reason: AbortReason | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            code="PIPELINE_ABORT",
            message=f"Pipeline aborted at step '{step}': {explanation or 'no explanation'}",
            **kwargs,
        )
        self.step = step
        self.explanation = explanation
        self.alternatives = alternatives
        self.pipeline_trace = trace
        self.abort_reason: AbortReason = abort_reason or AbortReason.OTHER

    @property
    def step_name(self) -> str:
        """Alias for ``step``, matching the naming convention of ``PipelineStepError``."""
        return self.step


class StepNotFoundError(ModuleError):
    """Raised when a referenced step does not exist."""

    def __init__(self, message: str = "", **kwargs: Any) -> None:
        super().__init__(code="STEP_NOT_FOUND", message=message, **kwargs)


class StepNotRemovableError(ModuleError):
    """Raised when attempting to remove a non-removable step."""

    def __init__(self, message: str = "", **kwargs: Any) -> None:
        super().__init__(code="STEP_NOT_REMOVABLE", message=message, **kwargs)


class StepNotReplaceableError(ModuleError):
    """Raised when attempting to replace a non-replaceable step."""

    def __init__(self, message: str = "", **kwargs: Any) -> None:
        super().__init__(code="STEP_NOT_REPLACEABLE", message=message, **kwargs)


class StepNameDuplicateError(ModuleError):
    """Raised when a step name already exists in the strategy."""

    def __init__(self, message: str = "", **kwargs: Any) -> None:
        super().__init__(code="STEP_NAME_DUPLICATE", message=message, **kwargs)


class StrategyNotFoundError(ModuleError):
    """Raised when a referenced strategy does not exist."""

    def __init__(self, message: str = "", **kwargs: Any) -> None:
        super().__init__(code="STRATEGY_NOT_FOUND", message=message, **kwargs)


class PipelineStepError(ModuleError):
    """Raised when a pipeline step fails (fail-fast, §1.1).

    Wraps the original exception from the step, carrying the step name for
    diagnostics. When ignore_errors is true on the step, this error is NOT
    raised; execution continues instead.
    """

    def __init__(
        self,
        step_name: str,
        cause: Exception | None = None,
        trace: PipelineTrace | None = None,
        **kwargs: Any,
    ) -> None:
        cause_msg = str(cause) if cause is not None else "unknown error"
        super().__init__(
            code="PIPELINE_STEP_ERROR",
            message=f"Pipeline step '{step_name}' failed: {cause_msg}",
            cause=cause,
            details={"step_name": step_name},
            **kwargs,
        )
        self.step_name = step_name
        self.pipeline_trace = trace


class PipelineStepNotFoundError(ModuleError):
    """Raised when configure_step targets a step name that does not exist."""

    def __init__(self, step_name: str = "", **kwargs: Any) -> None:
        super().__init__(
            code="PIPELINE_STEP_NOT_FOUND",
            message=f"Pipeline step not found: '{step_name}'",
            **kwargs,
        )
        self.step_name = step_name


class ConfigurationError(ModuleError):
    """Raised when pipeline YAML configuration is invalid (Issue #33 §1.2).

    Replaces the prior warning-and-continue behaviour: when YAML refers to a
    step name that does not exist (in ``remove``, ``configure``, or
    ``insert.before``/``insert.after``), or assigns an unknown field via
    ``configure``, a :class:`ConfigurationError` is raised so the misconfig
    surfaces at start-up rather than at runtime.
    """

    def __init__(self, message: str = "", **kwargs: Any) -> None:
        super().__init__(code="PIPELINE_CONFIGURATION_ERROR", message=message, **kwargs)


class PipelineDependencyError(ModuleError):
    """Raised when a step's ``requires`` are not satisfied by preceding
    ``provides`` (Issue #33 §2.1).

    The error names the offending step and the missing keys so the operator
    can correct the strategy assembly.
    """

    def __init__(
        self,
        step_name: str = "",
        missing: tuple[str, ...] = (),
        **kwargs: Any,
    ) -> None:
        msg = f"Pipeline step '{step_name}' requires {set(missing)}, " f"but no preceding step provides them."
        super().__init__(
            code="PIPELINE_DEPENDENCY_ERROR",
            message=msg,
            details={"step_name": step_name, "missing": list(missing)},
            **kwargs,
        )
        self.step_name = step_name
        self.missing = missing
