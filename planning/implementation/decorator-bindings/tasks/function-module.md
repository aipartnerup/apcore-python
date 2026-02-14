# Task: FunctionModule Wrapper Class

## Goal

Implement the `FunctionModule` class that wraps a Python function to conform to the apcore module interface. Provides `module_id`, `input_schema`, `output_schema`, `description`, and `execute()` method. Supports both sync and async functions via separate execute closures.

## Files Involved

- `src/apcore/decorator.py` -- `FunctionModule` class, `_normalize_result()` helper
- `src/apcore/context.py` -- `Context` class for execute() parameter
- `tests/test_decorator.py` -- Unit tests for FunctionModule

## Steps

### 1. Write failing tests (TDD)

Create tests for:
- **Sync function**: `FunctionModule(sync_func, "test.sync")` creates module with correct schemas, `execute()` calls function and returns dict
- **Async function**: `FunctionModule(async_func, "test.async")` creates module, `execute()` is awaitable and returns dict
- **Context injection**: Function with Context parameter receives context when execute() is called
- **Auto schemas**: When `input_schema`/`output_schema` not provided, generates from function signature
- **Explicit schemas**: When provided, uses them directly instead of auto-generation
- **Description priority**: Explicit description > first line of docstring > fallback `"Module {name}"`
- **Optional fields**: documentation, tags, version, annotations, metadata are stored correctly
- **_normalize_result()**: None -> `{}`, dict -> passthrough, BaseModel -> `.model_dump()`, other -> `{"result": value}`

### 2. Implement _normalize_result()

```python
def _normalize_result(result: Any) -> dict:
    if result is None:
        return {}
    if isinstance(result, dict):
        return result
    if isinstance(result, BaseModel):
        return result.model_dump()
    return {"result": result}
```

### 3. Implement FunctionModule

- `__init__()` stores func, module_id, generates or uses provided schemas
- Detect Context param via `_has_context_param(func)`
- Set description: explicit > docstring first line > fallback
- Create sync or async execute closure based on `inspect.iscoroutinefunction(func)`:
  - Build kwargs from inputs, inject context if needed
  - Call function, normalize result via `_normalize_result()`

### 4. Verify tests pass

Run `pytest tests/test_decorator.py -k "function_module or normalize" -v`.

## Acceptance Criteria

- [x] Wraps sync functions with sync execute closure
- [x] Wraps async functions with async execute closure (inspect.iscoroutinefunction returns True)
- [x] Context-typed parameter is detected and injected during execute()
- [x] Auto-generates input/output schemas when not explicitly provided
- [x] Uses explicit schemas when provided
- [x] Description priority chain: explicit > docstring > fallback
- [x] `_normalize_result()` handles None, dict, BaseModel, and other return types

## Dependencies

- `type-inference` -- `_generate_input_model()`, `_generate_output_model()`, `_has_context_param()` must be implemented

## Estimated Time

2.5 hours
