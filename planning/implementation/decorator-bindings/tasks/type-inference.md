# Task: Type Inference for Input/Output Model Generation

## Goal

Implement `_generate_input_model()` and `_generate_output_model()` helper functions that convert Python function signatures and return types into Pydantic BaseModel classes. These are the foundation for automatic schema generation used by both the `@module` decorator and the `BindingLoader`.

## Files Involved

- `src/apcore/decorator.py` -- `_generate_input_model()`, `_generate_output_model()`, `_has_context_param()`
- `src/apcore/errors.py` -- `FuncMissingTypeHintError`, `FuncMissingReturnTypeError`
- `tests/test_decorator.py` -- Unit tests for type inference

## Steps

### 1. Write failing tests (TDD)

Create tests for `_generate_input_model()`:
- Function with typed required params -> model with required fields
- Function with default values -> model with optional fields
- Function with `**kwargs` -> model with `extra="allow"` config
- Function with `self`/`cls` parameter -> skipped
- Function with `*args` -> skipped
- Function with `Context`-typed parameter -> skipped (type-based detection, not name-based)
- Function with missing type hint -> raises `FuncMissingTypeHintError`
- Function with forward reference that cannot be resolved -> raises `FuncMissingTypeHintError`

Create tests for `_generate_output_model()`:
- Return type `dict` or `dict[str, T]` -> permissive model (extra="allow")
- Return type `BaseModel` subclass -> returned directly
- Return type `None` -> empty permissive model
- Return type other (e.g., `str`, `int`) -> model with single "result" field
- No return annotation -> raises `FuncMissingReturnTypeError`

Create tests for `_has_context_param()`:
- Function with `Context`-typed param -> returns `(True, param_name)`
- Function without Context param -> returns `(False, None)`
- Detection is type-based (works regardless of parameter name)

### 2. Implement _generate_input_model()

- Use `typing.get_type_hints(func, include_extras=True)` to resolve type hints
- Iterate `inspect.signature(func).parameters`, skipping self/cls, *args, **kwargs (record presence), Context-typed
- Build field dict with `(type, default)` or `(type, ...)` tuples
- Create model via `pydantic.create_model("InputModel", ...)` with `extra="allow"` if **kwargs present

### 3. Implement _generate_output_model()

- Resolve return type from hints
- Handle None -> raise `FuncMissingReturnTypeError`
- Handle `type(None)` -> empty permissive model
- Handle BaseModel subclass -> return directly
- Handle `dict` / `dict[str, T]` -> permissive model
- Handle other types -> model with "result" field

### 4. Implement _has_context_param()

- Resolve type hints, iterate non-return hints
- Check `hint is Context` (identity check, not isinstance)
- Return `(True, param_name)` or `(False, None)`

### 5. Verify tests pass

Run `pytest tests/test_decorator.py -k "generate or context_param" -v`.

## Acceptance Criteria

- [x] `_generate_input_model()` produces correct Pydantic models from function signatures
- [x] self/cls, *args, Context-typed params are skipped
- [x] **kwargs presence sets `extra="allow"` on the model
- [x] Missing type hints raise `FuncMissingTypeHintError` with function/parameter names
- [x] `_generate_output_model()` handles dict, BaseModel, None, and other return types
- [x] Missing return annotation raises `FuncMissingReturnTypeError`
- [x] `_has_context_param()` uses type-based detection (not name-based)

## Dependencies

None -- these are foundation utilities with no internal dependencies beyond `apcore.context.Context` (for type comparison) and `apcore.errors` (for error types).

## Estimated Time

3 hours
