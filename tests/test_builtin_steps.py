"""Tests for built-in pipeline steps."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from apcore.builtin_steps import (
    BuiltinACLCheck,
    BuiltinApprovalGate,
    BuiltinContextCreation,
    BuiltinExecute,
    BuiltinInputValidation,
    BuiltinMiddlewareAfter,
    BuiltinMiddlewareBefore,
    BuiltinModuleLookup,
    BuiltinOutputValidation,
    BuiltinReturnResult,
    BuiltinCallChainGuard,
    build_standard_strategy,
)
from apcore.errors import InvalidInputError
from apcore.pipeline import (
    BaseStep,
    ExecutionStrategy,
    PipelineContext,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(
    module_id: str = "test.module",
    inputs: dict[str, Any] | None = None,
    context: Any = None,
    module: Any = None,
) -> PipelineContext:
    """Create a minimal PipelineContext for testing."""
    return PipelineContext(
        module_id=module_id,
        inputs=inputs or {},
        context=context,
        module=module,
    )


class FakeRegistry:
    """Minimal registry that returns a module by ID."""

    def __init__(self, modules: dict[str, Any] | None = None) -> None:
        self._modules = modules or {}

    def get(self, module_id: str, **kwargs: Any) -> Any:
        return self._modules.get(module_id)


class FakeModule:
    """Minimal module for testing."""

    def __init__(
        self,
        *,
        input_schema: Any = None,
        output_schema: Any = None,
        annotations: Any = None,
    ) -> None:
        self.input_schema = input_schema
        self.output_schema = output_schema
        self.annotations = annotations

    def execute(self, inputs: dict[str, Any], context: Any) -> dict[str, Any]:
        return {"result": "ok"}


class FakeContext:
    """Minimal context for testing."""

    def __init__(self, caller_id: str = "user-1") -> None:
        self.caller_id = caller_id
        self.call_chain: list[str] = []
        self.global_deadline: float | None = None

    def child(self, module_id: str) -> FakeContext:
        c = FakeContext(caller_id=self.caller_id)
        c.call_chain = [*self.call_chain, module_id]
        return c


# ---------------------------------------------------------------------------
# Instantiation tests
# ---------------------------------------------------------------------------


class TestStepInstantiation:
    """Verify each step can be instantiated and is a BaseStep."""

    def test_context_creation(self) -> None:
        step = BuiltinContextCreation()
        assert isinstance(step, BaseStep)
        assert step.name == "context_creation"

    def test_safety_check(self) -> None:
        step = BuiltinCallChainGuard()
        assert isinstance(step, BaseStep)
        assert step.name == "call_chain_guard"

    def test_module_lookup(self) -> None:
        step = BuiltinModuleLookup(registry=FakeRegistry())
        assert isinstance(step, BaseStep)
        assert step.name == "module_lookup"

    def test_acl_check(self) -> None:
        step = BuiltinACLCheck()
        assert isinstance(step, BaseStep)
        assert step.name == "acl_check"

    def test_approval_gate(self) -> None:
        step = BuiltinApprovalGate()
        assert isinstance(step, BaseStep)
        assert step.name == "approval_gate"

    def test_input_validation(self) -> None:
        step = BuiltinInputValidation()
        assert isinstance(step, BaseStep)
        assert step.name == "input_validation"

    def test_middleware_before(self) -> None:
        step = BuiltinMiddlewareBefore()
        assert isinstance(step, BaseStep)
        assert step.name == "middleware_before"

    def test_execute(self) -> None:
        step = BuiltinExecute()
        assert isinstance(step, BaseStep)
        assert step.name == "execute"

    def test_output_validation(self) -> None:
        step = BuiltinOutputValidation()
        assert isinstance(step, BaseStep)
        assert step.name == "output_validation"

    def test_middleware_after(self) -> None:
        step = BuiltinMiddlewareAfter()
        assert isinstance(step, BaseStep)
        assert step.name == "middleware_after"

    def test_return_result(self) -> None:
        step = BuiltinReturnResult()
        assert isinstance(step, BaseStep)
        assert step.name == "return_result"


# ---------------------------------------------------------------------------
# Removable / replaceable flags
# ---------------------------------------------------------------------------


class TestStepFlags:
    """Verify removable and replaceable flags match the spec."""

    @pytest.mark.parametrize(
        "step_factory,expected_removable,expected_replaceable",
        [
            (lambda: BuiltinContextCreation(), False, False),
            (lambda: BuiltinCallChainGuard(), True, True),
            (lambda: BuiltinModuleLookup(registry=FakeRegistry()), False, False),
            (lambda: BuiltinACLCheck(), True, True),
            (lambda: BuiltinApprovalGate(), True, True),
            (lambda: BuiltinInputValidation(), True, True),
            (lambda: BuiltinMiddlewareBefore(), True, False),
            (lambda: BuiltinExecute(), False, True),
            (lambda: BuiltinOutputValidation(), True, True),
            (lambda: BuiltinMiddlewareAfter(), True, False),
            (lambda: BuiltinReturnResult(), False, False),
        ],
        ids=[
            "context_creation",
            "call_chain_guard",
            "module_lookup",
            "acl_check",
            "approval_gate",
            "input_validation",
            "middleware_before",
            "execute",
            "output_validation",
            "middleware_after",
            "return_result",
        ],
    )
    def test_flags(
        self,
        step_factory: Any,
        expected_removable: bool,
        expected_replaceable: bool,
    ) -> None:
        step = step_factory()
        assert step.removable is expected_removable
        assert step.replaceable is expected_replaceable


# ---------------------------------------------------------------------------
# build_standard_strategy
# ---------------------------------------------------------------------------


class TestBuildStandardStrategy:
    """Verify the factory creates the correct strategy."""

    def test_creates_11_steps(self) -> None:
        strategy = build_standard_strategy(registry=FakeRegistry())
        assert isinstance(strategy, ExecutionStrategy)
        assert len(strategy.steps) == 11

    def test_step_names_ordered(self) -> None:
        strategy = build_standard_strategy(registry=FakeRegistry())
        expected = [
            "context_creation",
            "call_chain_guard",
            "module_lookup",
            "acl_check",
            "approval_gate",
            "middleware_before",
            "input_validation",
            "execute",
            "output_validation",
            "middleware_after",
            "return_result",
        ]
        assert strategy.step_names() == expected

    def test_strategy_name(self) -> None:
        strategy = build_standard_strategy(registry=FakeRegistry())
        assert strategy.name == "standard"


# ---------------------------------------------------------------------------
# Step execution: happy path (async tests)
# ---------------------------------------------------------------------------


class TestContextCreationStep:
    """Test BuiltinContextCreation execute."""

    async def test_creates_context_when_none(self) -> None:
        ctx = _make_ctx(context=None)
        step = BuiltinContextCreation()
        result = await step.execute(ctx)
        assert result.action == "continue"
        assert ctx.context is not None

    async def test_preserves_existing_context(self) -> None:
        fake_ctx = FakeContext()
        ctx = _make_ctx(context=fake_ctx)
        step = BuiltinContextCreation()
        result = await step.execute(ctx)
        assert result.action == "continue"


class TestSafetyCheckStep:
    """Test BuiltinCallChainGuard execute."""

    async def test_passes_normal(self) -> None:
        fake_ctx = FakeContext()
        ctx = _make_ctx(context=fake_ctx)
        step = BuiltinCallChainGuard()
        result = await step.execute(ctx)
        assert result.action == "continue"


class TestModuleLookupStep:
    """Test BuiltinModuleLookup execute."""

    async def test_sets_module_on_found(self) -> None:
        module = FakeModule()
        registry = FakeRegistry({"test.module": module})
        ctx = _make_ctx(module_id="test.module")
        step = BuiltinModuleLookup(registry=registry)
        result = await step.execute(ctx)
        assert result.action == "continue"
        assert ctx.module is module

    async def test_raises_on_not_found(self) -> None:
        from apcore.errors import ModuleNotFoundError

        registry = FakeRegistry({})
        ctx = _make_ctx(module_id="missing.module")
        step = BuiltinModuleLookup(registry=registry)
        with pytest.raises(ModuleNotFoundError):
            await step.execute(ctx)


class TestACLCheckStep:
    """Test BuiltinACLCheck execute."""

    async def test_continues_when_no_acl(self) -> None:
        ctx = _make_ctx(context=FakeContext())
        step = BuiltinACLCheck(acl=None)
        result = await step.execute(ctx)
        assert result.action == "continue"

    async def test_continues_when_allowed(self) -> None:
        acl = MagicMock(spec=["check"])
        acl.check.return_value = True
        ctx = _make_ctx(context=FakeContext())
        step = BuiltinACLCheck(acl=acl)
        result = await step.execute(ctx)
        assert result.action == "continue"

    async def test_raises_when_denied(self) -> None:
        from apcore.errors import ACLDeniedError

        acl = MagicMock(spec=["check"])
        acl.check.return_value = False
        ctx = _make_ctx(context=FakeContext())
        step = BuiltinACLCheck(acl=acl)
        with pytest.raises(ACLDeniedError):
            await step.execute(ctx)


class TestApprovalGateStep:
    """Test BuiltinApprovalGate execute."""

    async def test_continues_when_no_handler(self) -> None:
        ctx = _make_ctx(context=FakeContext(), module=FakeModule())
        step = BuiltinApprovalGate(handler=None)
        result = await step.execute(ctx)
        assert result.action == "continue"

    async def test_continues_when_no_approval_needed(self) -> None:
        handler = AsyncMock()
        ctx = _make_ctx(context=FakeContext(), module=FakeModule())
        step = BuiltinApprovalGate(handler=handler)
        result = await step.execute(ctx)
        assert result.action == "continue"

    async def test_rejects_non_string_approval_token(self) -> None:
        # A non-string _approval_token must be rejected with
        # GENERAL_INVALID_INPUT before reaching the handler (security gate;
        # mirrors Rust).
        handler = AsyncMock()
        module = FakeModule(annotations={"requires_approval": True})
        ctx = _make_ctx(
            inputs={"_approval_token": 12345},
            context=FakeContext(),
            module=module,
        )
        step = BuiltinApprovalGate(handler=handler)
        with pytest.raises(InvalidInputError) as exc_info:
            await step.execute(ctx)
        assert exc_info.value.code == "GENERAL_INVALID_INPUT"
        handler.check_approval.assert_not_awaited()


class TestInputValidationStep:
    """Test BuiltinInputValidation execute."""

    async def test_sets_validated_inputs_no_schema(self) -> None:
        module = FakeModule(input_schema=None)
        ctx = _make_ctx(inputs={"a": 1}, module=module)
        step = BuiltinInputValidation()
        result = await step.execute(ctx)
        assert result.action == "continue"
        assert ctx.validated_inputs == {"a": 1}

    async def test_aborts_when_no_module(self) -> None:
        ctx = _make_ctx(module=None)
        step = BuiltinInputValidation()
        result = await step.execute(ctx)
        assert result.action == "abort"


class TestMiddlewareBeforeStep:
    """Test BuiltinMiddlewareBefore execute."""

    async def test_continues_empty_middlewares(self) -> None:
        ctx = _make_ctx(context=FakeContext())
        step = BuiltinMiddlewareBefore(middlewares=[])
        result = await step.execute(ctx)
        assert result.action == "continue"


class TestExecuteStep:
    """Test BuiltinExecute execute."""

    async def test_sets_output(self) -> None:
        module = FakeModule()
        ctx = _make_ctx(inputs={"x": 1}, context=FakeContext(), module=module)
        ctx.validated_inputs = {"x": 1}
        step = BuiltinExecute()
        result = await step.execute(ctx)
        assert result.action == "continue"
        assert ctx.output == {"result": "ok"}

    async def test_aborts_when_no_module(self) -> None:
        ctx = _make_ctx(module=None)
        step = BuiltinExecute()
        result = await step.execute(ctx)
        assert result.action == "abort"


class TestOutputValidationStep:
    """Test BuiltinOutputValidation execute."""

    async def test_sets_validated_output_no_schema(self) -> None:
        module = FakeModule(output_schema=None)
        ctx = _make_ctx(module=module)
        ctx.output = {"val": 42}
        step = BuiltinOutputValidation()
        result = await step.execute(ctx)
        assert result.action == "continue"
        assert ctx.validated_output == {"val": 42}

    async def test_aborts_when_no_module(self) -> None:
        ctx = _make_ctx(module=None)
        step = BuiltinOutputValidation()
        result = await step.execute(ctx)
        assert result.action == "abort"


class TestMiddlewareAfterStep:
    """Test BuiltinMiddlewareAfter execute."""

    async def test_continues_empty_middlewares(self) -> None:
        ctx = _make_ctx(context=FakeContext())
        step = BuiltinMiddlewareAfter(middlewares=[])
        result = await step.execute(ctx)
        assert result.action == "continue"


class TestReturnResultStep:
    """Test BuiltinReturnResult execute."""

    async def test_continues(self) -> None:
        ctx = _make_ctx()
        step = BuiltinReturnResult()
        result = await step.execute(ctx)
        assert result.action == "continue"
