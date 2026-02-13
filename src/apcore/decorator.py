"""Module decorator, FunctionModule wrapper, and type inference helpers."""

from __future__ import annotations

import inspect
import re
import typing
from typing import Any, Callable, get_origin

from pydantic import BaseModel, ConfigDict, create_model

from apcore.context import Context
from apcore.errors import FuncMissingReturnTypeError, FuncMissingTypeHintError


def _generate_input_model(func: Any) -> type[BaseModel]:
    """Convert a function's parameter signature into a dynamic Pydantic BaseModel.

    Skips self/cls, *args, **kwargs, and Context-typed parameters.
    If **kwargs is present, the model is created with extra="allow".
    """
    # Resolve type hints safely (handles from __future__ import annotations)
    try:
        hints = typing.get_type_hints(func, include_extras=True)
    except NameError as exc:
        # Forward reference that can't be resolved
        missing_name = str(exc).split("'")[1] if "'" in str(exc) else "<forward_ref>"
        raise FuncMissingTypeHintError(
            function_name=func.__name__,
            parameter_name=missing_name,
        ) from exc

    sig = inspect.signature(func)
    field_dict: dict[str, Any] = {}
    has_kwargs = False

    for param_name, param in sig.parameters.items():
        # Skip self/cls
        if param_name in ("self", "cls"):
            continue
        # Skip *args
        if param.kind == inspect.Parameter.VAR_POSITIONAL:
            continue
        # Skip **kwargs but record their presence
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            has_kwargs = True
            continue

        # Check for type hint
        if param_name not in hints:
            raise FuncMissingTypeHintError(
                function_name=func.__name__,
                parameter_name=param_name,
            )

        annotation = hints[param_name]

        # Skip Context-typed parameters
        if annotation is Context:
            continue

        # Build (type, default) tuple
        if param.default is not inspect.Parameter.empty:
            field_dict[param_name] = (annotation, param.default)
        else:
            field_dict[param_name] = (annotation, ...)

    # Create the model, with extra="allow" if **kwargs was present
    if has_kwargs:
        return create_model("InputModel", __config__=ConfigDict(extra="allow"), **field_dict)
    return create_model("InputModel", **field_dict)


def _generate_output_model(func: Any) -> type[BaseModel]:
    """Convert a function's return type annotation into a Pydantic BaseModel.

    - dict / dict[str, T] -> permissive model (extra="allow")
    - BaseModel subclass -> returned directly
    - None -> empty permissive model
    - Other types -> model with single "result" field
    """
    try:
        hints = typing.get_type_hints(func, include_extras=True)
    except NameError:
        hints = {}

    return_type = hints.get("return")
    if return_type is None and "return" not in hints:
        raise FuncMissingReturnTypeError(function_name=func.__name__)

    # Handle None return type
    if return_type is type(None):
        return create_model("OutputModel", __config__=ConfigDict(extra="allow"))

    # Handle BaseModel subclass
    if isinstance(return_type, type) and issubclass(return_type, BaseModel):
        return return_type

    # Handle dict (bare or parameterized)
    if return_type is dict or get_origin(return_type) is dict:
        return create_model("OutputModel", __config__=ConfigDict(extra="allow"))

    # All other types: wrap in a model with a "result" field
    return create_model("OutputModel", result=(return_type, ...))


def _has_context_param(func: Any) -> tuple[bool, str | None]:
    """Check if any parameter has type annotation that is the Context class.

    Returns (True, param_name) if found, (False, None) otherwise.
    Detection is type-based, not name-based.
    """
    try:
        hints = typing.get_type_hints(func, include_extras=True)
    except NameError:
        return (False, None)

    for param_name, hint in hints.items():
        if param_name == "return":
            continue
        if hint is Context:
            return (True, param_name)

    return (False, None)


def _normalize_result(result: Any) -> dict:
    """Normalize a function's return value to a dict for the executor pipeline."""
    if result is None:
        return {}
    if isinstance(result, dict):
        return result
    if isinstance(result, BaseModel):
        return result.model_dump()
    return {"result": result}


class FunctionModule:
    """Wrapper that adapts a Python function to the apcore module interface.

    Provides input_schema, output_schema, and execute() so the wrapped
    function can participate in the full executor pipeline (ACL, middleware,
    timeout, validation, async support).
    """

    def __init__(
        self,
        func: Callable,
        module_id: str,
        description: str | None = None,
        documentation: str | None = None,
        tags: list[str] | None = None,
        version: str = "1.0.0",
        annotations: dict | None = None,
        metadata: dict | None = None,
        input_schema: type[BaseModel] | None = None,
        output_schema: type[BaseModel] | None = None,
    ) -> None:
        self._func = func
        self.module_id = module_id
        self.input_schema = input_schema if input_schema is not None else _generate_input_model(func)
        self.output_schema = output_schema if output_schema is not None else _generate_output_model(func)

        has_context, context_param_name = _has_context_param(func)

        # Description priority chain
        if description is not None:
            self.description = description
        elif func.__doc__:
            self.description = func.__doc__.strip().split("\n")[0].strip()
        else:
            self.description = f"Module {func.__name__}"

        self.documentation = documentation
        self.tags = tags
        self.version = version
        self.annotations = annotations
        self.metadata = metadata

        # Create execute closures — two separate defs required so that
        # inspect.iscoroutinefunction returns the correct value.
        if inspect.iscoroutinefunction(func):

            async def _async_execute(inputs: dict, context: Context) -> dict:
                call_kwargs = dict(inputs)
                if has_context:
                    call_kwargs[context_param_name] = context
                result = await func(**call_kwargs)
                return _normalize_result(result)

            self.execute = _async_execute
        else:

            def _sync_execute(inputs: dict, context: Context) -> dict:
                call_kwargs = dict(inputs)
                if has_context:
                    call_kwargs[context_param_name] = context
                result = func(**call_kwargs)
                return _normalize_result(result)

            self.execute = _sync_execute


def _make_auto_id(func: Callable) -> str:
    """Generate a module ID from a function's module path and qualified name."""
    raw = f"{func.__module__}.{func.__qualname__}"
    raw = raw.replace("<locals>.", ".")
    raw = raw.lower()
    raw = re.sub(r"[^a-z0-9_.]", "_", raw)
    segments = raw.split(".")
    segments = [f"_{s}" if s and s[0].isdigit() else s for s in segments]
    return ".".join(segments)


def module(
    func_or_none: Callable | None = None,
    /,
    *,
    id: str | None = None,  # noqa: A002
    description: str | None = None,
    documentation: str | None = None,
    annotations: dict | None = None,
    tags: list[str] | None = None,
    version: str = "1.0.0",
    metadata: dict | None = None,
    registry: Any = None,
) -> Any:
    """Wrap a Python function as an apcore module.

    Dual-purpose: works as a decorator and as a function call.
    """

    def _wrap(func: Callable, *, return_module: bool = False) -> Any:
        module_id = id if id is not None else _make_auto_id(func)
        fm = FunctionModule(
            func=func,
            module_id=module_id,
            description=description,
            documentation=documentation,
            annotations=annotations,
            tags=tags,
            version=version,
            metadata=metadata,
        )
        if registry is not None:
            registry.register(fm.module_id, fm)
        if return_module:
            return fm
        func.apcore_module = fm  # type: ignore[attr-defined]
        return func

    if func_or_none is not None and callable(func_or_none):
        # Bare @module or module(func, id=...) call
        if id is not None or registry is not None:
            # Function call form: module(func, id="x")
            return _wrap(func_or_none, return_module=True)
        # Bare decorator: @module
        return _wrap(func_or_none, return_module=False)

    # Decorator with arguments: @module(id="x", ...)
    def decorator(func: Callable) -> Callable:
        return _wrap(func, return_module=False)

    return decorator
