"""Built-in pipeline steps for the v0.17 Pipeline v2 execution model.

Each class wraps one step of the executor pipeline, receiving its
dependencies via constructor injection.  Steps read from and write to
PipelineContext fields, returning StepResult to control pipeline flow.

Pipeline v2 steps raise domain errors directly instead of returning
abort results, enabling the executor to propagate typed exceptions.
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import logging
import time
from typing import Any, cast

import pydantic

from apcore.approval import ApprovalRequest, ApprovalResult
from apcore.cancel import ExecutionCancelledError
from apcore.context import Context
from apcore.errors import (
    ACLDeniedError,
    ApprovalDeniedError,
    ApprovalPendingError,
    ApprovalTimeoutError,
    InvalidInputError,
    ModuleDisabledError,
    ModuleNotFoundError,
    ModuleTimeoutError,
    SchemaValidationError,
)
from apcore.module import ModuleAnnotations
from apcore.pipeline import (
    BaseStep,
    ExecutionStrategy,
    PipelineContext,
    StepResult,
)
from apcore.config import Config
from apcore.utils.call_chain import guard_call_chain

__all__ = [
    "BuiltinContextCreation",
    "BuiltinCallChainGuard",
    "BuiltinModuleLookup",
    "BuiltinACLCheck",
    "BuiltinApprovalGate",
    "BuiltinInputValidation",
    "BuiltinMiddlewareBefore",
    "BuiltinExecute",
    "BuiltinOutputValidation",
    "BuiltinMiddlewareAfter",
    "BuiltinReturnResult",
    "build_standard_strategy",
    "build_internal_strategy",
    "build_testing_strategy",
    "build_performance_strategy",
    "build_minimal_strategy",
]

_logger = logging.getLogger(__name__)


def _convert_validation_errors(error: pydantic.ValidationError) -> list[dict[str, Any]]:
    """Convert a Pydantic ValidationError into a list of error dicts."""
    return [
        {
            "field": ".".join(str(loc) for loc in err["loc"]),
            "code": err["type"],
            "message": err["msg"],
        }
        for err in error.errors()
    ]


def _ensure_middleware_manager(manager: Any | None, middlewares: list[Any] | None) -> Any:
    """Return *manager* if provided, otherwise build a fresh one from *middlewares*.

    Lets ``BuiltinMiddlewareBefore`` and ``BuiltinMiddlewareAfter`` keep a
    single execution path: production passes the executor's shared
    ``MiddlewareManager`` directly, while tests/standalone callers can
    supply a raw ``middlewares`` list and have a one-off manager built for
    them on the fly.
    """
    if manager is not None:
        return manager
    from apcore.middleware.manager import MiddlewareManager

    built = MiddlewareManager()
    for mw in middlewares or []:
        built.add(mw)
    return built


# ---------------------------------------------------------------------------
# Step 1: Context Creation
# ---------------------------------------------------------------------------


class BuiltinContextCreation(BaseStep):
    """Create or inherit execution context and set global deadline."""

    def __init__(
        self,
        *,
        config: Any | None = None,
        executor: Any | None = None,
    ) -> None:
        super().__init__(
            name="context_creation",
            description="Create execution context and set global deadline",
            removable=False,
            replaceable=False,
            pure=True,
            provides=("context",),
        )
        self._config = config
        self._executor = executor
        if config is not None:
            val = config.get("executor.global_timeout")
            self._global_timeout: int = val if val is not None else Config.get_default("executor.global_timeout")
        else:
            self._global_timeout = Config.get_default("executor.global_timeout")

    async def execute(self, ctx: PipelineContext) -> StepResult:
        if ctx.context is None:
            # Fallback path: a Context was not bound at the Executor entry
            # point (e.g. PipelineContext constructed directly in tests).
            # Per PROTOCOL_SPEC §"Contract: Context.create", executor is not
            # a public constructor input; bind via the private helper.
            base_ctx = Context.create()
            base_ctx._bind_executor(self._executor)
        else:
            base_ctx = ctx.context

        # Root call detection — empty call_chain means this is a top-level
        # invocation. Per PROTOCOL_SPEC §"Contract: `global_deadline`
        # distributed semantics", the receiving Executor MUST (re)compute
        # global_deadline from local config at pipeline entry. If the
        # caller already populated global_deadline (in-process cooperation),
        # preserve it; only fill in when unset.
        is_root_call = not base_ctx.call_chain
        if is_root_call and base_ctx.global_deadline is None and self._global_timeout > 0:
            base_ctx.global_deadline = time.monotonic() + self._global_timeout / 1000.0

        # Derive child context to add module_id to call chain.
        ctx.context = base_ctx.child(ctx.module_id)
        return StepResult(action="continue")


# ---------------------------------------------------------------------------
# Step 2: Call Chain Guard
# ---------------------------------------------------------------------------


class BuiltinCallChainGuard(BaseStep):
    """Call chain guard: depth, repeat limits, cancel token."""

    def __init__(self, *, config: Any | None = None) -> None:
        super().__init__(
            name="call_chain_guard",
            description="Validate call chain depth and repeat limits",
            removable=True,
            replaceable=True,
            pure=True,
            requires=("context",),
        )
        self._config = config
        if config is not None:
            val = config.get("executor.max_call_depth")
            self._max_call_depth: int = val if val is not None else Config.get_default("executor.max_call_depth")
            val = config.get("executor.max_module_repeat")
            self._max_module_repeat: int = val if val is not None else Config.get_default("executor.max_module_repeat")
        else:
            self._max_call_depth = Config.get_default("executor.max_call_depth")
            self._max_module_repeat = Config.get_default("executor.max_module_repeat")

    async def execute(self, ctx: PipelineContext) -> StepResult:
        # D-21 / A-D-EXEC-002: short-circuit before any expensive validation
        # or middleware work if the caller already cancelled. The pipeline
        # also re-checks at the Execute step (defensive backstop for tokens
        # cancelled while later steps run), but observing the token here is
        # what makes the two-point invariant hold — single-check
        # implementations leak compute through ACL/middleware/validation.
        cancel_token = getattr(ctx.context, "cancel_token", None)
        if cancel_token is not None and getattr(cancel_token, "is_cancelled", False):
            raise ExecutionCancelledError()

        call_chain = getattr(ctx.context, "call_chain", [])
        guard_call_chain(
            ctx.module_id,
            call_chain,
            max_call_depth=self._max_call_depth,
            max_module_repeat=self._max_module_repeat,
        )
        return StepResult(action="continue")


# ---------------------------------------------------------------------------
# Step 3: Module Lookup
# ---------------------------------------------------------------------------


class BuiltinModuleLookup(BaseStep):
    """Resolve module from registry by ID with optional version hint.

    After a successful registry lookup, checks the toggle state and raises
    ``ModuleDisabledError`` if the module has been disabled via
    ``ToggleFeatureModule`` / ``ToggleState.disable()``.  This mirrors the
    MODULE_DISABLED (HTTP 403) behaviour in the TypeScript and Rust SDKs.
    """

    def __init__(
        self,
        *,
        registry: Any,
        toggle_state: Any | None = None,
    ) -> None:
        super().__init__(
            name="module_lookup",
            description="Look up module in registry",
            removable=False,
            replaceable=False,
            pure=True,
            provides=("module",),
        )
        self._registry = registry
        # Use the injected toggle_state when provided (e.g. for testing);
        # fall back to the package-level default singleton.
        if toggle_state is not None:
            self._toggle_state = toggle_state
        else:
            from apcore.sys_modules.control import _default_toggle_state

            self._toggle_state = _default_toggle_state

    async def execute(self, ctx: PipelineContext) -> StepResult:
        version_hint = getattr(ctx, "version_hint", None)
        if version_hint is not None:
            module = self._registry.get(ctx.module_id, version_hint=version_hint)
        else:
            module = self._registry.get(ctx.module_id)
        if module is None:
            raise ModuleNotFoundError(module_id=ctx.module_id)

        # Spec §"Toggle" — disabled modules must not be executed.
        # Check AFTER registry lookup so we still return ModuleNotFoundError
        # for unregistered IDs (not ModuleDisabledError).
        if self._toggle_state.is_disabled(ctx.module_id):
            raise ModuleDisabledError(module_id=ctx.module_id)

        ctx.module = module

        # Early input redaction: set context.redacted_inputs BEFORE any
        # middleware runs (step 6: BuiltinMiddlewareBefore). This ensures
        # logging middleware's before() hook sees redacted inputs instead
        # of None. Input validation (step 7) still validates; this step
        # only redacts for observability.
        if ctx.context is not None and hasattr(ctx.context, "redacted_inputs"):
            input_schema = getattr(module, "input_schema", None)
            schema_dict_fn = getattr(input_schema, "model_json_schema", None) if input_schema else None
            if schema_dict_fn is not None and callable(schema_dict_fn):
                from apcore.utils.redaction import redact_sensitive

                schema = cast(dict[str, Any], schema_dict_fn())
                ctx.context.redacted_inputs = redact_sensitive(ctx.inputs, schema)
            else:
                # No schema → no redaction needed; store raw inputs so
                # downstream consumers don't see None.
                ctx.context.redacted_inputs = dict(ctx.inputs)

        return StepResult(action="continue")


# ---------------------------------------------------------------------------
# Step 4: ACL Check
# ---------------------------------------------------------------------------


class BuiltinACLCheck(BaseStep):
    """Access control list enforcement."""

    def __init__(self, *, acl: Any | None = None) -> None:
        super().__init__(
            name="acl_check",
            description="Enforce access control policies",
            removable=True,
            replaceable=True,
            pure=True,
            requires=("context", "module"),
        )
        self._acl = acl

    def set_acl(self, acl: Any | None) -> None:
        """Replace the ACL provider used by this step at runtime."""
        self._acl = acl

    async def execute(self, ctx: PipelineContext) -> StepResult:
        if self._acl is None:
            return StepResult(action="continue")

        caller_id = getattr(ctx.context, "caller_id", "anonymous")

        # Try async_check first, fall back to sync check
        if hasattr(self._acl, "async_check"):
            allowed = await self._acl.async_check(caller_id, ctx.module_id, ctx.context)
        else:
            allowed = self._acl.check(caller_id, ctx.module_id, ctx.context)

        if not allowed:
            raise ACLDeniedError(caller_id=caller_id, target_id=ctx.module_id)
        return StepResult(action="continue")


# ---------------------------------------------------------------------------
# Step 5: Approval Gate
# ---------------------------------------------------------------------------


class BuiltinApprovalGate(BaseStep):
    """Approval handler flow for modules requiring approval.

    For modules whose annotations declare ``requires_approval=True``, calls
    the configured ApprovalHandler, emits an audit event (logging + tracing
    span event), and translates the result into either continued execution
    or the appropriate ``ApprovalError`` subclass.
    """

    def __init__(self, *, handler: Any | None = None) -> None:
        super().__init__(
            name="approval_gate",
            description="Request and verify module approval",
            removable=True,
            replaceable=True,
            pure=False,
            requires=("context", "module"),
        )
        self._handler = handler

    def set_handler(self, handler: Any | None) -> None:
        """Replace the approval handler used by this step at runtime."""
        self._handler = handler

    async def execute(self, ctx: PipelineContext) -> StepResult:
        if self._handler is None:
            return StepResult(action="continue")

        module = ctx.module
        if not _module_requires_approval(module):
            return StepResult(action="continue")

        # Phase B token resume vs fresh request
        if "_approval_token" in ctx.inputs:
            token = ctx.inputs.pop("_approval_token")
            result = await self._handler.check_approval(token)
        else:
            request = ApprovalRequest(
                module_id=ctx.module_id,
                arguments=ctx.inputs,
                context=ctx.context,
                annotations=_coerce_annotations(getattr(module, "annotations", None)),
                description=getattr(module, "description", None),
                tags=list(getattr(module, "tags", None) or []),
            )
            result = await self._handler.request_approval(request)

        self._emit_audit(result, ctx.module_id, ctx.context)

        if result.status == "approved":
            return StepResult(action="continue")
        if result.status == "rejected":
            raise ApprovalDeniedError(result=result, module_id=ctx.module_id)
        if result.status == "timeout":
            raise ApprovalTimeoutError(result=result, module_id=ctx.module_id)
        if result.status == "pending":
            raise ApprovalPendingError(result=result, module_id=ctx.module_id)
        _logger.warning(
            "Unknown approval status %r for module %s, treating as denied",
            result.status,
            ctx.module_id,
        )
        raise ApprovalDeniedError(result=result, module_id=ctx.module_id)

    @staticmethod
    def _emit_audit(result: ApprovalResult, module_id: str, ctx: Context) -> None:
        """Log the decision and append a span event when tracing is active."""
        _logger.info(
            "Approval decision: module=%s status=%s approved_by=%s reason=%s",
            module_id,
            result.status,
            result.approved_by,
            result.reason,
        )
        spans_stack: list[Any] = ctx.data.get("_apcore.mw.tracing.spans", [])
        if spans_stack:
            spans_stack[-1].events.append(
                {
                    "name": "approval_decision",
                    "module_id": module_id,
                    "status": result.status,
                    "approved_by": result.approved_by or "",
                    "reason": result.reason or "",
                    "approval_id": result.approval_id or "",
                }
            )


def _module_requires_approval(module: Any) -> bool:
    """Read ``requires_approval`` from a module's annotations (dataclass or dict)."""
    annotations = getattr(module, "annotations", None)
    if annotations is None:
        return False
    if isinstance(annotations, ModuleAnnotations):
        return annotations.requires_approval
    if isinstance(annotations, dict):
        return bool(annotations.get("requires_approval", False))
    return False


def _coerce_annotations(annotations: Any) -> ModuleAnnotations:
    """Coerce raw module annotations into a ``ModuleAnnotations`` instance."""
    if isinstance(annotations, ModuleAnnotations):
        return annotations
    if isinstance(annotations, dict):
        valid_fields = {f.name for f in dataclasses.fields(ModuleAnnotations)}
        return ModuleAnnotations(**{k: v for k, v in annotations.items() if k in valid_fields})
    return ModuleAnnotations()


# ---------------------------------------------------------------------------
# Step 6: Middleware Before
# ---------------------------------------------------------------------------


class BuiltinMiddlewareBefore(BaseStep):
    """Execute middleware before-chain.

    Always operates through a ``MiddlewareManager`` — when the caller does
    not supply one, an internal manager is built from the ``middlewares``
    list. This collapses what used to be two parallel execute() paths
    (manager + raw-list fallback) into one.
    """

    def __init__(
        self,
        *,
        middlewares: list[Any] | None = None,
        middleware_manager: Any | None = None,
    ) -> None:
        super().__init__(
            name="middleware_before",
            description="Run before-middleware chain",
            removable=True,
            replaceable=False,
            pure=False,
        )
        self._manager = _ensure_middleware_manager(middleware_manager, middlewares)

    async def execute(self, ctx: PipelineContext) -> StepResult:
        from apcore.middleware.manager import MiddlewareChainError

        try:
            if hasattr(self._manager, "execute_before_async"):
                inputs, executed = await self._manager.execute_before_async(
                    ctx.module_id,
                    ctx.inputs,
                    ctx.context,
                )
            else:
                inputs, executed = self._manager.execute_before(
                    ctx.module_id,
                    ctx.inputs,
                    ctx.context,
                )
            ctx.inputs = inputs
            ctx.executed_middlewares = list(executed)
        except MiddlewareChainError as exc:
            # Store executed middlewares for the executor's on_error recovery
            ctx.executed_middlewares = list(exc.executed_middlewares)
            raise
        except Exception as exc:
            # on_error recovery for non-chain errors
            if hasattr(self._manager, "execute_on_error_async"):
                recovery = await self._manager.execute_on_error_async(
                    ctx.module_id,
                    ctx.inputs,
                    exc,
                    ctx.context,
                    ctx.executed_middlewares,
                )
            elif hasattr(self._manager, "execute_on_error"):
                recovery = self._manager.execute_on_error(
                    ctx.module_id,
                    ctx.inputs,
                    exc,
                    ctx.context,
                    ctx.executed_middlewares,
                )
            else:
                recovery = None
            # Clear executed_middlewares after on_error to prevent double invocation
            ctx.executed_middlewares = []
            if recovery is not None:
                ctx.output = recovery
                return StepResult(action="skip_to", skip_to="return_result")
            raise
        return StepResult(action="continue")


# ---------------------------------------------------------------------------
# Step 7: Input Validation
# ---------------------------------------------------------------------------


class BuiltinInputValidation(BaseStep):
    """Validate inputs against module schema and redact sensitive fields."""

    def __init__(self) -> None:
        super().__init__(
            name="input_validation",
            description="Validate inputs against schema and redact sensitive fields",
            removable=True,
            replaceable=True,
            pure=True,
            requires=("module",),
            provides=("validated_inputs",),
        )

    async def execute(self, ctx: PipelineContext) -> StepResult:
        module = ctx.module
        if module is None:
            return StepResult(action="abort", explanation="No module set on context")

        input_schema = getattr(module, "input_schema", None)
        if input_schema is None:
            ctx.validated_inputs = ctx.inputs
            return StepResult(action="continue")

        try:
            input_schema.model_validate(ctx.inputs)
        except pydantic.ValidationError as exc:
            errors = _convert_validation_errors(exc)
            raise SchemaValidationError(
                message=f"Input validation failed: {errors}",
                errors=errors,
            ) from exc

        ctx.validated_inputs = ctx.inputs

        # Redact sensitive fields after successful validation
        schema_dict_fn = getattr(input_schema, "model_json_schema", None)
        if schema_dict_fn is not None and callable(schema_dict_fn):
            from apcore.utils.redaction import redact_sensitive

            schema = cast(dict[str, Any], schema_dict_fn())
            redacted = redact_sensitive(ctx.inputs, schema)
            if ctx.context is not None and hasattr(ctx.context, "redacted_inputs"):
                ctx.context.redacted_inputs = redacted

        return StepResult(action="continue")


# ---------------------------------------------------------------------------
# Step 8: Execute
# ---------------------------------------------------------------------------


class BuiltinExecute(BaseStep):
    """Execute the module with timeout enforcement."""

    def __init__(self, *, config: Any | None = None) -> None:
        super().__init__(
            name="execute",
            description="Execute module with timeout",
            removable=False,
            replaceable=True,
            pure=False,
            requires=("module",),
            provides=("output",),
        )
        self._config = config
        if config is not None:
            val = config.get("executor.default_timeout")
            self._default_timeout: int = val if val is not None else Config.get_default("executor.default_timeout")
        else:
            self._default_timeout = Config.get_default("executor.default_timeout")

    async def execute(self, ctx: PipelineContext) -> StepResult:
        module = ctx.module
        if module is None:
            return StepResult(action="abort", explanation="No module set on context")

        # Check cancel token
        cancel_token = getattr(ctx.context, "cancel_token", None)
        if cancel_token is not None and cancel_token.is_cancelled:
            raise ExecutionCancelledError()

        # Check global deadline
        global_deadline = getattr(ctx.context, "global_deadline", None)
        if global_deadline is not None and time.monotonic() > global_deadline:
            timeout_ms = int(self._default_timeout)
            raise ModuleTimeoutError(module_id=ctx.module_id, timeout_ms=timeout_ms)

        inputs = ctx.validated_inputs if ctx.validated_inputs is not None else ctx.inputs

        # Stream mode: set up output_stream if module satisfies StreamingModule Protocol
        from apcore.streaming import StreamingModule

        if ctx.stream and isinstance(module, StreamingModule):
            ctx.output_stream = module.stream(inputs, ctx.context)
            return StepResult(action="skip_to", skip_to="return_result")

        # Determine per-module timeout
        module_timeout_ms = getattr(module, "timeout_ms", None)
        if module_timeout_ms is not None:
            if module_timeout_ms < 0:
                raise InvalidInputError(
                    message=f"Negative timeout: {module_timeout_ms}",
                )
            timeout_s = module_timeout_ms / 1000.0
        elif self._default_timeout > 0:
            timeout_s = self._default_timeout / 1000.0
        else:
            timeout_s = None

        # Clamp to global deadline if set
        if global_deadline is not None:
            remaining = global_deadline - time.monotonic()
            if remaining <= 0:
                raise ModuleTimeoutError(module_id=ctx.module_id, timeout_ms=int(self._default_timeout))
            if timeout_s is None or remaining < timeout_s:
                timeout_s = remaining

        try:
            if inspect.iscoroutinefunction(module.execute):
                coro = module.execute(inputs, ctx.context)
            else:
                loop = asyncio.get_event_loop()
                coro = loop.run_in_executor(None, module.execute, inputs, ctx.context)

            if timeout_s is not None:
                output = await asyncio.wait_for(coro, timeout=timeout_s)
            else:
                output = await coro
            ctx.output = output
        except asyncio.TimeoutError:
            timeout_ms = int((timeout_s or 0) * 1000)
            raise ModuleTimeoutError(
                module_id=ctx.module_id,
                timeout_ms=timeout_ms,
            ) from None

        return StepResult(action="continue")


# ---------------------------------------------------------------------------
# Step 9: Output Validation
# ---------------------------------------------------------------------------


class BuiltinOutputValidation(BaseStep):
    """Validate output against module schema and redact sensitive fields."""

    def __init__(self) -> None:
        super().__init__(
            name="output_validation",
            description="Validate output against schema and redact sensitive fields",
            removable=True,
            replaceable=True,
            pure=True,
            requires=("module", "output"),
            provides=("validated_output",),
        )

    async def execute(self, ctx: PipelineContext) -> StepResult:
        module = ctx.module
        if module is None:
            return StepResult(action="abort", explanation="No module set on context")

        output_schema = getattr(module, "output_schema", None)
        if output_schema is None:
            ctx.validated_output = ctx.output
            return StepResult(action="continue")

        if ctx.output is None:
            ctx.validated_output = None
            return StepResult(action="continue")

        try:
            output_schema.model_validate(ctx.output)
        except pydantic.ValidationError as exc:
            errors = _convert_validation_errors(exc)
            raise SchemaValidationError(
                message=f"Output validation failed: {errors}",
                errors=errors,
            ) from exc

        ctx.validated_output = ctx.output

        # Store redacted output as first-class Context field (symmetric with
        # redacted_inputs). Previously stored under ctx.context.data["_apcore.
        # executor.redacted_output"] which was filtered out by serialize().
        if ctx.context is not None and hasattr(ctx.context, "redacted_output"):
            schema_dict_fn = getattr(output_schema, "model_json_schema", None)
            if schema_dict_fn is not None and callable(schema_dict_fn):
                from apcore.utils.redaction import redact_sensitive

                schema = cast(dict[str, Any], schema_dict_fn())
                ctx.context.redacted_output = redact_sensitive(ctx.output, schema)
            else:
                ctx.context.redacted_output = dict(ctx.output) if ctx.output else None

        return StepResult(action="continue")


# ---------------------------------------------------------------------------
# Step 10: Middleware After
# ---------------------------------------------------------------------------


class BuiltinMiddlewareAfter(BaseStep):
    """Execute middleware after-chain.

    Always operates through a ``MiddlewareManager`` — when the caller does
    not supply one, an internal manager is built from the ``middlewares``
    list. Mirrors :class:`BuiltinMiddlewareBefore` so the two steps share
    the same execution model.
    """

    def __init__(
        self,
        *,
        middlewares: list[Any] | None = None,
        middleware_manager: Any | None = None,
    ) -> None:
        super().__init__(
            name="middleware_after",
            description="Run after-middleware chain",
            removable=True,
            replaceable=False,
            pure=False,
        )
        self._manager = _ensure_middleware_manager(middleware_manager, middlewares)

    async def execute(self, ctx: PipelineContext) -> StepResult:
        if hasattr(self._manager, "execute_after_async"):
            output = await self._manager.execute_after_async(
                ctx.module_id,
                ctx.inputs,
                ctx.output or {},
                ctx.context,
            )
        else:
            output = self._manager.execute_after(
                ctx.module_id,
                ctx.inputs,
                ctx.output or {},
                ctx.context,
            )
        ctx.output = output
        return StepResult(action="continue")


# ---------------------------------------------------------------------------
# Step 11: Return Result
# ---------------------------------------------------------------------------


class BuiltinReturnResult(BaseStep):
    """Finalize pipeline output. Output is already on ctx."""

    def __init__(self) -> None:
        super().__init__(
            name="return_result",
            description="Finalize pipeline result",
            removable=False,
            replaceable=False,
            pure=True,
            requires=("output",),
        )

    async def execute(self, ctx: PipelineContext) -> StepResult:
        return StepResult(action="continue")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_standard_strategy(
    *,
    registry: Any,
    config: Any | None = None,
    acl: Any | None = None,
    approval_handler: Any | None = None,
    middlewares: list[Any] | None = None,
    middleware_manager: Any | None = None,
    executor: Any | None = None,
) -> ExecutionStrategy:
    """Build the standard 11-step execution strategy.

    Args:
        registry: Module registry for looking up modules by ID.
        config: Optional configuration for timeout/depth settings.
        acl: Optional ACL for access control enforcement.
        approval_handler: Optional approval handler for the approval gate.
        middlewares: Optional list of middleware instances.
        middleware_manager: Optional MiddlewareManager for production parity.
        executor: Optional executor reference for context creation and approval.

    Returns:
        An ExecutionStrategy containing the 11 built-in steps.
    """
    return ExecutionStrategy(
        "standard",
        [
            BuiltinContextCreation(config=config, executor=executor),
            BuiltinCallChainGuard(config=config),
            BuiltinModuleLookup(registry=registry),
            BuiltinACLCheck(acl=acl),
            BuiltinApprovalGate(handler=approval_handler),
            BuiltinMiddlewareBefore(
                middlewares=middlewares or [],
                middleware_manager=middleware_manager,
            ),
            BuiltinInputValidation(),
            BuiltinExecute(config=config),
            BuiltinOutputValidation(),
            BuiltinMiddlewareAfter(
                middlewares=middlewares or [],
                middleware_manager=middleware_manager,
            ),
            BuiltinReturnResult(),
        ],
    )


def build_internal_strategy(**kwargs: Any) -> ExecutionStrategy:
    """Build an internal strategy: standard minus acl_check and approval_gate.

    Suitable for trusted internal calls that skip access control.

    Args:
        **kwargs: Forwarded to build_standard_strategy().

    Returns:
        An ExecutionStrategy named "internal".
    """
    s = build_standard_strategy(**kwargs)
    s.remove("acl_check")
    s.remove("approval_gate")
    s.name = "internal"
    return s


def build_testing_strategy(**kwargs: Any) -> ExecutionStrategy:
    """Build a testing strategy: standard minus acl, approval, and call chain guard.

    Suitable for unit/integration tests that need minimal overhead.

    Args:
        **kwargs: Forwarded to build_standard_strategy().

    Returns:
        An ExecutionStrategy named "testing".
    """
    s = build_standard_strategy(**kwargs)
    s.remove("acl_check")
    s.remove("approval_gate")
    s.remove("call_chain_guard")
    s.name = "testing"
    return s


def build_performance_strategy(**kwargs: Any) -> ExecutionStrategy:
    """Build a performance strategy: standard minus middleware steps.

    Suitable for latency-sensitive calls that skip middleware overhead.

    Args:
        **kwargs: Forwarded to build_standard_strategy().

    Returns:
        An ExecutionStrategy named "performance".
    """
    s = build_standard_strategy(**kwargs)
    s.remove("middleware_before")
    s.remove("middleware_after")
    s.name = "performance"
    return s


def build_minimal_strategy(**kwargs: Any) -> ExecutionStrategy:
    """Build a minimal strategy: context → lookup → execute → return only.

    Suitable for pre-validated internal hot paths where ACL, approval,
    middleware, and schema validation are unnecessary. Use with caution —
    no safety checks, no input/output validation, no middleware.

    Args:
        **kwargs: Forwarded to build_standard_strategy().

    Returns:
        An ExecutionStrategy named "minimal" with 4 steps.
    """
    s = build_standard_strategy(**kwargs)
    s.remove("call_chain_guard")
    s.remove("acl_check")
    s.remove("approval_gate")
    s.remove("middleware_before")
    s.remove("input_validation")
    s.remove("output_validation")
    s.remove("middleware_after")
    s.name = "minimal"
    return s
