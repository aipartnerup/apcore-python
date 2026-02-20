"""Executor and related utilities for apcore."""

from __future__ import annotations

import asyncio
import copy
import inspect
import logging
import threading
from collections.abc import AsyncIterator
from typing import Any, Callable

import pydantic

from apcore.acl import ACL
from apcore.config import Config
from apcore.context import Context
from apcore.errors import (
    ACLDeniedError,
    CallDepthExceededError,
    CallFrequencyExceededError,
    CircularCallError,
    InvalidInputError,
    ModuleNotFoundError,
    ModuleTimeoutError,
    SchemaValidationError,
)
from apcore.middleware import AfterMiddleware, BeforeMiddleware, Middleware
from apcore.middleware.manager import MiddlewareChainError, MiddlewareManager
from apcore.module import ValidationResult
from apcore.registry import Registry

__all__ = ["redact_sensitive", "REDACTED_VALUE", "Executor"]

REDACTED_VALUE: str = "***REDACTED***"

_logger = logging.getLogger(__name__)


# =============================================================================
# Redaction utilities (from section-05)
# =============================================================================


def redact_sensitive(data: dict[str, Any], schema_dict: dict[str, Any]) -> dict[str, Any]:
    """Redact fields marked with x-sensitive in the schema.

    Implements Algorithm A13 from PROTOCOL_SPEC section 9.5.
    Returns a deep copy of data with sensitive values replaced by "***REDACTED***".
    Also redacts any keys starting with "_secret_" regardless of schema.

    Args:
        data: The data dict to redact.
        schema_dict: A JSON Schema dict that may contain "x-sensitive": true
            on individual properties.

    Returns:
        A new dict with sensitive values replaced. Original data is not modified.
    """
    redacted = copy.deepcopy(data)
    _redact_fields(redacted, schema_dict)
    _redact_secret_prefix(redacted)
    return redacted


def _redact_fields(data: dict[str, Any], schema_dict: dict[str, Any]) -> None:
    """In-place redaction based on schema x-sensitive markers."""
    properties = schema_dict.get("properties")
    if not properties:
        return

    for field_name, field_schema in properties.items():
        if field_name not in data:
            continue

        value = data[field_name]

        # x-sensitive: true on this property
        if field_schema.get("x-sensitive") is True:
            if value is not None:
                data[field_name] = REDACTED_VALUE
            continue

        # Nested object: recurse
        if field_schema.get("type") == "object" and "properties" in field_schema and isinstance(value, dict):
            _redact_fields(value, field_schema)
            continue

        # Array: redact items
        if field_schema.get("type") == "array" and "items" in field_schema and isinstance(value, list):
            items_schema = field_schema["items"]
            if items_schema.get("x-sensitive") is True:
                for i, item in enumerate(value):
                    if item is not None:
                        value[i] = REDACTED_VALUE
            elif items_schema.get("type") == "object" and "properties" in items_schema:
                for item in value:
                    if isinstance(item, dict):
                        _redact_fields(item, items_schema)


def _redact_secret_prefix(data: dict[str, Any]) -> None:
    """In-place redaction of keys starting with _secret_."""
    for key in data:
        if key.startswith("_secret_") and data[key] is not None:
            data[key] = REDACTED_VALUE


# =============================================================================
# Executor class (section-06)
# =============================================================================


class Executor:
    """Central execution engine that orchestrates the module call pipeline.

    The Executor implements a 10-step synchronous flow: context creation,
    call chain safety checks, module lookup, ACL enforcement, input validation
    with redaction, middleware before chain, module execution, output validation,
    middleware after chain, and result return.
    """

    def __init__(
        self,
        registry: Registry,
        middlewares: list[Middleware] | None = None,
        acl: ACL | None = None,
        config: Config | None = None,
    ) -> None:
        """Initialize the Executor.

        Args:
            registry: Module registry for looking up modules by ID.
            middlewares: Optional list of middleware instances to register.
            acl: Optional ACL for access control enforcement.
            config: Optional configuration for timeout/depth settings.
        """
        self._registry = registry
        self._middleware_manager = MiddlewareManager()
        self._acl = acl
        self._config = config

        if middlewares:
            for mw in middlewares:
                self._middleware_manager.add(mw)

        if config is not None:
            val = config.get("executor.default_timeout")
            self._default_timeout: int = val if val is not None else 30000
            val = config.get("executor.global_timeout")
            self._global_timeout: int = val if val is not None else 60000
            val = config.get("executor.max_call_depth")
            self._max_call_depth: int = val if val is not None else 32
            val = config.get("executor.max_module_repeat")
            self._max_module_repeat: int = val if val is not None else 3
        else:
            self._default_timeout = 30000
            self._global_timeout = 60000
            self._max_call_depth = 32
            self._max_module_repeat = 3

        self._async_cache: dict[str, bool] = {}
        self._async_cache_lock = threading.Lock()

    @classmethod
    def from_registry(
        cls,
        registry: "Registry",
        middlewares: list | None = None,
        acl: "ACL | None" = None,
        config: "Config | None" = None,
    ) -> "Executor":
        """Convenience factory for creating an Executor from a Registry.

        Args:
            registry: The module registry.
            middlewares: Optional middleware list.
            acl: Optional access control list.
            config: Optional configuration.

        Returns:
            A configured Executor instance.
        """
        return cls(registry=registry, middlewares=middlewares, acl=acl, config=config)

    @property
    def registry(self) -> Registry:
        """Return the Registry instance."""
        return self._registry

    @property
    def middlewares(self) -> list[Middleware]:
        """Return a copy of the current middleware list."""
        return self._middleware_manager.snapshot()

    def use(self, middleware: Middleware) -> Executor:
        """Add class-based middleware and return self for chaining."""
        self._middleware_manager.add(middleware)
        return self

    def use_before(self, callback: Callable[..., Any]) -> Executor:
        """Wrap callback in BeforeMiddleware adapter and add it."""
        self._middleware_manager.add(BeforeMiddleware(callback))
        return self

    def use_after(self, callback: Callable[..., Any]) -> Executor:
        """Wrap callback in AfterMiddleware adapter and add it."""
        self._middleware_manager.add(AfterMiddleware(callback))
        return self

    def remove(self, middleware: Middleware) -> bool:
        """Remove middleware by identity. Returns True if found and removed."""
        return self._middleware_manager.remove(middleware)

    def call(
        self,
        module_id: str,
        inputs: dict[str, Any] | None = None,
        context: Context | None = None,
    ) -> dict[str, Any]:
        """Execute a module through the 10-step pipeline.

        Args:
            module_id: The module to execute.
            inputs: Input data dict. None is treated as {}.
            context: Optional execution context. Auto-created if None.

        Returns:
            The module output dict, possibly modified by middleware.
        """
        if inputs is None:
            inputs = {}

        # Step 1 -- Context
        if context is None:
            ctx = Context.create(executor=self)
            ctx = ctx.child(module_id)
        else:
            ctx = context.child(module_id)

        # Step 2 -- Safety Checks
        self._check_safety(module_id, ctx)

        # Step 3 -- Lookup
        module = self._registry.get(module_id)
        if module is None:
            raise ModuleNotFoundError(module_id=module_id)

        # Step 4 -- ACL
        if self._acl is not None:
            allowed = self._acl.check(ctx.caller_id, module_id, ctx)
            if not allowed:
                raise ACLDeniedError(caller_id=ctx.caller_id, target_id=module_id)

        # Step 5 -- Input Validation and Redaction
        if hasattr(module, "input_schema") and module.input_schema is not None:
            try:
                module.input_schema.model_validate(inputs)
            except pydantic.ValidationError as e:
                errors = [
                    {
                        "field": ".".join(str(loc) for loc in err["loc"]),
                        "code": err["type"],
                        "message": err["msg"],
                    }
                    for err in e.errors()
                ]
                raise SchemaValidationError(
                    message="Input validation failed",
                    errors=errors,
                ) from e

            ctx.redacted_inputs = redact_sensitive(inputs, module.input_schema.model_json_schema())

        executed_middlewares: list[Middleware] = []

        try:
            # Step 6 -- Middleware Before
            try:
                inputs, executed_middlewares = self._middleware_manager.execute_before(module_id, inputs, ctx)
            except MiddlewareChainError as mce:
                executed_middlewares = mce.executed_middlewares
                recovery = self._middleware_manager.execute_on_error(
                    module_id, inputs, mce.original, ctx, executed_middlewares
                )
                if recovery is not None:
                    return recovery
                executed_middlewares = []  # Prevent double on_error in outer except
                raise mce.original from mce

            # Step 7 -- Execute with timeout
            output = self._execute_with_timeout(module, module_id, inputs, ctx)

            # Step 8 -- Output Validation
            if hasattr(module, "output_schema") and module.output_schema is not None:
                try:
                    module.output_schema.model_validate(output)
                except pydantic.ValidationError as e:
                    errors = [
                        {
                            "field": ".".join(str(loc) for loc in err["loc"]),
                            "code": err["type"],
                            "message": err["msg"],
                        }
                        for err in e.errors()
                    ]
                    raise SchemaValidationError(
                        message="Output validation failed",
                        errors=errors,
                    ) from e

            # Step 9 -- Middleware After
            output = self._middleware_manager.execute_after(module_id, inputs, output, ctx)

        except Exception as exc:
            # Error handling for steps 6-9
            if executed_middlewares:
                recovery = self._middleware_manager.execute_on_error(module_id, inputs, exc, ctx, executed_middlewares)
                if recovery is not None:
                    return recovery
            raise

        # Step 10 -- Return
        return output

    def validate(
        self,
        module_id: str,
        inputs: dict[str, Any],
    ) -> ValidationResult:
        """Validate inputs against a module's schema without execution.

        Args:
            module_id: The module to validate against.
            inputs: Input data to validate.

        Returns:
            ValidationResult with valid=True or valid=False with errors.

        Raises:
            ModuleNotFoundError: If the module is not found.
        """
        module = self._registry.get(module_id)
        if module is None:
            raise ModuleNotFoundError(module_id=module_id)

        if not hasattr(module, "input_schema") or module.input_schema is None:
            return ValidationResult(valid=True, errors=[])

        try:
            module.input_schema.model_validate(inputs)
            return ValidationResult(valid=True, errors=[])
        except pydantic.ValidationError as e:
            errors = [
                {
                    "field": ".".join(str(loc) for loc in err["loc"]),
                    "code": err["type"],
                    "message": err["msg"],
                }
                for err in e.errors()
            ]
            return ValidationResult(valid=False, errors=errors)

    def _check_safety(self, module_id: str, ctx: Context) -> None:
        """Run call chain safety checks (step 2)."""
        call_chain = ctx.call_chain

        # Depth check
        if len(call_chain) > self._max_call_depth:
            raise CallDepthExceededError(
                depth=len(call_chain),
                max_depth=self._max_call_depth,
                call_chain=list(call_chain),
            )

        # Circular detection (strict cycles of length >= 2)
        # call_chain already includes module_id at the end (from child()),
        # so check prior entries only.
        prior_chain = call_chain[:-1]
        if module_id in prior_chain:
            last_idx = len(prior_chain) - 1 - prior_chain[::-1].index(module_id)
            subsequence = prior_chain[last_idx + 1 :]
            if len(subsequence) > 0:
                raise CircularCallError(
                    module_id=module_id,
                    call_chain=list(call_chain),
                )

        # Frequency check
        count = call_chain.count(module_id)
        if count > self._max_module_repeat:
            raise CallFrequencyExceededError(
                module_id=module_id,
                count=count,
                max_repeat=self._max_module_repeat,
                call_chain=list(call_chain),
            )

    def _is_async_module(self, module_id: str, module: Any) -> bool:
        """Check if a module's execute method is async, with caching."""
        with self._async_cache_lock:
            if module_id in self._async_cache:
                return self._async_cache[module_id]
            is_async = inspect.iscoroutinefunction(module.execute)
            self._async_cache[module_id] = is_async
            return is_async

    def _execute_with_timeout(
        self, module: Any, module_id: str, inputs: dict[str, Any], ctx: Context
    ) -> dict[str, Any]:
        """Execute module with timeout enforcement (sync path)."""
        timeout_ms = self._default_timeout

        if timeout_ms < 0:
            raise InvalidInputError(message=f"Negative timeout: {timeout_ms}ms")

        # Async module in sync context: bridge to async
        if self._is_async_module(module_id, module):
            return self._run_async_in_sync(module.execute(inputs, ctx), module_id, timeout_ms)

        if timeout_ms == 0:
            _logger.warning("Timeout disabled for module %s", module_id)
            return module.execute(inputs, ctx)

        result_holder: dict[str, Any] = {}
        exception_holder: dict[str, Exception] = {}

        def run_module() -> None:
            try:
                result_holder["output"] = module.execute(inputs, ctx)
            except Exception as e:
                exception_holder["error"] = e

        thread = threading.Thread(target=run_module, daemon=True)
        thread.start()
        thread.join(timeout=timeout_ms / 1000.0)

        if thread.is_alive():
            raise ModuleTimeoutError(module_id=module_id, timeout_ms=timeout_ms)

        if "error" in exception_holder:
            raise exception_holder["error"]

        return result_holder["output"]

    def _run_async_in_sync(self, coro: Any, module_id: str, timeout_ms: int) -> Any:
        """Run an async coroutine from a sync context."""
        timeout_s = timeout_ms / 1000.0 if timeout_ms > 0 else None

        try:
            asyncio.get_running_loop()
            has_loop = True
        except RuntimeError:
            has_loop = False

        if not has_loop:
            if timeout_s is not None:
                wrapped = asyncio.wait_for(coro, timeout=timeout_s)
            else:
                wrapped = coro
            try:
                return asyncio.run(wrapped)
            except asyncio.TimeoutError:
                raise ModuleTimeoutError(module_id=module_id, timeout_ms=timeout_ms)
        else:
            return self._run_in_new_thread(coro, module_id, timeout_s)

    def _run_in_new_thread(self, coro: Any, module_id: str, timeout_s: float | None) -> Any:
        """Run coroutine in a new thread with its own event loop."""
        result_holder: dict[str, Any] = {}
        exception_holder: dict[str, Exception] = {}

        def thread_target() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                if timeout_s is not None:
                    result_holder["output"] = loop.run_until_complete(asyncio.wait_for(coro, timeout=timeout_s))
                else:
                    result_holder["output"] = loop.run_until_complete(coro)
            except asyncio.TimeoutError:
                exception_holder["error"] = ModuleTimeoutError(
                    module_id=module_id, timeout_ms=int((timeout_s or 0) * 1000)
                )
            except Exception as e:
                exception_holder["error"] = e
            finally:
                loop.close()

        thread = threading.Thread(target=thread_target, daemon=True)
        thread.start()
        thread.join()

        if "error" in exception_holder:
            raise exception_holder["error"]
        return result_holder["output"]

    async def call_async(
        self,
        module_id: str,
        inputs: dict[str, Any] | None = None,
        context: Context | None = None,
    ) -> dict[str, Any]:
        """Async counterpart to call(). Supports async modules natively.

        Args:
            module_id: The module to execute.
            inputs: Input data dict. None is treated as {}.
            context: Optional execution context. Auto-created if None.

        Returns:
            The module output dict, possibly modified by middleware.
        """
        if inputs is None:
            inputs = {}

        # Step 1 -- Context
        if context is None:
            ctx = Context.create(executor=self)
            ctx = ctx.child(module_id)
        else:
            ctx = context.child(module_id)

        # Step 2 -- Safety Checks
        self._check_safety(module_id, ctx)

        # Step 3 -- Lookup
        module = self._registry.get(module_id)
        if module is None:
            raise ModuleNotFoundError(module_id=module_id)

        # Step 4 -- ACL
        if self._acl is not None:
            allowed = self._acl.check(ctx.caller_id, module_id, ctx)
            if not allowed:
                raise ACLDeniedError(caller_id=ctx.caller_id, target_id=module_id)

        # Step 5 -- Input Validation and Redaction
        if hasattr(module, "input_schema") and module.input_schema is not None:
            try:
                module.input_schema.model_validate(inputs)
            except pydantic.ValidationError as e:
                errors = [
                    {
                        "field": ".".join(str(loc) for loc in err["loc"]),
                        "code": err["type"],
                        "message": err["msg"],
                    }
                    for err in e.errors()
                ]
                raise SchemaValidationError(
                    message="Input validation failed",
                    errors=errors,
                ) from e
            ctx.redacted_inputs = redact_sensitive(inputs, module.input_schema.model_json_schema())

        executed_middlewares: list[Middleware] = []

        try:
            # Step 6 -- Middleware Before (async-aware)
            for mw in self._middleware_manager.snapshot():
                if inspect.iscoroutinefunction(mw.before):
                    result = await mw.before(module_id, inputs, ctx)
                else:
                    result = mw.before(module_id, inputs, ctx)
                executed_middlewares.append(mw)
                if result is not None:
                    inputs = result

            # Step 7 -- Execute (async)
            output = await self._execute_async(module, module_id, inputs, ctx)

            # Step 8 -- Output Validation
            if hasattr(module, "output_schema") and module.output_schema is not None:
                try:
                    module.output_schema.model_validate(output)
                except pydantic.ValidationError as e:
                    errors = [
                        {
                            "field": ".".join(str(loc) for loc in err["loc"]),
                            "code": err["type"],
                            "message": err["msg"],
                        }
                        for err in e.errors()
                    ]
                    raise SchemaValidationError(
                        message="Output validation failed",
                        errors=errors,
                    ) from e

            # Step 9 -- Middleware After (async-aware, reverse order)
            for mw in reversed(self._middleware_manager.snapshot()):
                if inspect.iscoroutinefunction(mw.after):
                    after_result = await mw.after(module_id, inputs, output, ctx)
                else:
                    after_result = mw.after(module_id, inputs, output, ctx)
                if after_result is not None:
                    output = after_result

        except Exception as exc:
            # Error handling for steps 6-9
            if executed_middlewares:
                recovery = await self._execute_on_error_async(module_id, inputs, exc, ctx, executed_middlewares)
                if recovery is not None:
                    return recovery
            raise

        # Step 10 -- Return
        return output

    async def stream(
        self,
        module_id: str,
        inputs: dict[str, Any] | None = None,
        context: Context | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Async generator that streams module output chunks.

        Steps 1-6 are identical to call_async(). If the module has no stream()
        method, falls back to call_async() and yields a single chunk. If the
        module has stream(), iterates it, yields each chunk, and accumulates
        results via shallow merge. After all chunks, validates accumulated
        output and runs after-middleware.

        Args:
            module_id: The module to execute.
            inputs: Input data dict. None is treated as {}.
            context: Optional execution context. Auto-created if None.

        Yields:
            Dict chunks from the module's stream() or a single call_async() result.
        """
        effective_inputs: dict[str, Any] = dict(inputs) if inputs is not None else {}

        # Step 1 -- Context
        if context is None:
            ctx = Context.create(executor=self)
            ctx = ctx.child(module_id)
        else:
            ctx = context.child(module_id)

        # Step 2 -- Safety Checks
        self._check_safety(module_id, ctx)

        # Step 3 -- Lookup
        module = self._registry.get(module_id)
        if module is None:
            raise ModuleNotFoundError(module_id=module_id)

        # Step 4 -- ACL
        if self._acl is not None:
            allowed = self._acl.check(ctx.caller_id, module_id, ctx)
            if not allowed:
                raise ACLDeniedError(caller_id=ctx.caller_id, target_id=module_id)

        # Step 5 -- Input Validation and Redaction
        if hasattr(module, "input_schema") and module.input_schema is not None:
            try:
                module.input_schema.model_validate(effective_inputs)
            except pydantic.ValidationError as e:
                errors = [
                    {
                        "field": ".".join(str(loc) for loc in err["loc"]),
                        "code": err["type"],
                        "message": err["msg"],
                    }
                    for err in e.errors()
                ]
                raise SchemaValidationError(
                    message="Input validation failed",
                    errors=errors,
                ) from e
            ctx.redacted_inputs = redact_sensitive(effective_inputs, module.input_schema.model_json_schema())

        executed_middlewares: list[Middleware] = []

        try:
            # Step 6 -- Middleware Before (async-aware, inline dispatch)
            for mw in self._middleware_manager.snapshot():
                if inspect.iscoroutinefunction(mw.before):
                    result = await mw.before(module_id, effective_inputs, ctx)
                else:
                    result = mw.before(module_id, effective_inputs, ctx)
                executed_middlewares.append(mw)
                if result is not None:
                    effective_inputs = result

            # Step 7 -- Stream or fallback
            if not hasattr(module, "stream") or module.stream is None:
                # Fallback: delegate to _execute_async, yield single chunk
                output = await self._execute_async(module, module_id, effective_inputs, ctx)

                # Step 8 -- Output Validation
                if hasattr(module, "output_schema") and module.output_schema is not None:
                    try:
                        module.output_schema.model_validate(output)
                    except pydantic.ValidationError as e:
                        errors = [
                            {
                                "field": ".".join(str(loc) for loc in err["loc"]),
                                "code": err["type"],
                                "message": err["msg"],
                            }
                            for err in e.errors()
                        ]
                        raise SchemaValidationError(
                            message="Output validation failed",
                            errors=errors,
                        ) from e

                # Step 9 -- Middleware After (async-aware, reverse order)
                for mw in reversed(self._middleware_manager.snapshot()):
                    if inspect.iscoroutinefunction(mw.after):
                        after_result = await mw.after(module_id, effective_inputs, output, ctx)
                    else:
                        after_result = mw.after(module_id, effective_inputs, output, ctx)
                    if after_result is not None:
                        output = after_result

                yield output
            else:
                # Streaming path: iterate module.stream(), accumulate via shallow merge
                accumulated: dict[str, Any] = {}
                async for chunk in module.stream(effective_inputs, ctx):
                    accumulated = {**accumulated, **chunk}
                    yield chunk

                # Step 8 -- Output Validation on accumulated result
                if hasattr(module, "output_schema") and module.output_schema is not None:
                    try:
                        module.output_schema.model_validate(accumulated)
                    except pydantic.ValidationError as e:
                        errors = [
                            {
                                "field": ".".join(str(loc) for loc in err["loc"]),
                                "code": err["type"],
                                "message": err["msg"],
                            }
                            for err in e.errors()
                        ]
                        raise SchemaValidationError(
                            message="Output validation failed",
                            errors=errors,
                        ) from e

                # Step 9 -- Middleware After on accumulated result (async-aware, reverse)
                for mw in reversed(self._middleware_manager.snapshot()):
                    if inspect.iscoroutinefunction(mw.after):
                        after_result = await mw.after(module_id, effective_inputs, accumulated, ctx)
                    else:
                        after_result = mw.after(module_id, effective_inputs, accumulated, ctx)
                    if after_result is not None:
                        accumulated = after_result

        except Exception as exc:
            # Error handling with middleware recovery
            if executed_middlewares:
                recovery = await self._execute_on_error_async(
                    module_id,
                    effective_inputs,
                    exc,
                    ctx,
                    executed_middlewares,
                )
                if recovery is not None:
                    yield recovery
                    return
            raise

    async def _execute_async(self, module: Any, module_id: str, inputs: dict[str, Any], ctx: Context) -> dict[str, Any]:
        """Execute module asynchronously with timeout."""
        timeout_ms = self._default_timeout

        if timeout_ms < 0:
            raise InvalidInputError(message=f"Negative timeout: {timeout_ms}ms")

        timeout_s = timeout_ms / 1000.0 if timeout_ms > 0 else None
        is_async = self._is_async_module(module_id, module)

        if is_async:
            coro = module.execute(inputs, ctx)
        else:
            coro = asyncio.to_thread(module.execute, inputs, ctx)

        if timeout_s is not None:
            try:
                return await asyncio.wait_for(coro, timeout=timeout_s)
            except asyncio.TimeoutError:
                raise ModuleTimeoutError(module_id=module_id, timeout_ms=timeout_ms)
        else:
            if timeout_ms == 0:
                _logger.warning("Timeout disabled for module %s", module_id)
            return await coro

    async def _execute_on_error_async(
        self,
        module_id: str,
        inputs: dict[str, Any],
        error: Exception,
        ctx: Context,
        executed_middlewares: list[Middleware],
    ) -> dict[str, Any] | None:
        """Async-aware on_error chain."""
        for mw in reversed(executed_middlewares):
            try:
                if inspect.iscoroutinefunction(mw.on_error):
                    recovery = await mw.on_error(module_id, inputs, error, ctx)
                else:
                    recovery = mw.on_error(module_id, inputs, error, ctx)
                if isinstance(recovery, dict):
                    return recovery
            except Exception:
                _logger.exception("on_error handler failed in %s", type(mw).__name__)
                continue
        return None
