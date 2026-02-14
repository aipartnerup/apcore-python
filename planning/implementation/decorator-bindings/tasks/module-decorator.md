# Task: @module Decorator with Auto-ID Generation

## Goal

Implement the `module()` function that serves as both a decorator and a direct function call for wrapping Python functions as apcore modules. Supports three usage forms: bare `@module`, parameterized `@module(id="x")`, and direct call `module(func, id="x")`. Includes automatic module ID generation from qualified function names.

## Files Involved

- `src/apcore/decorator.py` -- `module()` function, `_make_auto_id()` helper
- `tests/test_decorator.py` -- Unit tests for all decorator forms

## Steps

### 1. Write failing tests (TDD)

Create tests for:
- **Bare decorator**: `@module` decorates function, attaches `.apcore_module` attribute with auto-generated ID
- **Parameterized decorator**: `@module(id="custom.id")` uses explicit ID
- **Function call form**: `module(func, id="custom.id")` returns FunctionModule directly (not the original function)
- **Auto-ID generation**: `_make_auto_id(func)` produces `module.path.qualified_name` in lowercase, replacing `<locals>.` with `.`, non-alphanumeric chars with `_`, prepending `_` to digit-starting segments
- **Registry integration**: When `registry` is provided, module is registered automatically
- **All kwargs forwarded**: description, documentation, annotations, tags, version, metadata passed to FunctionModule
- **Return value**: Bare and parameterized forms return original function with `.apcore_module` attached; function call form with `id=` or `registry=` returns FunctionModule

### 2. Implement _make_auto_id()

```python
def _make_auto_id(func: Callable) -> str:
    raw = f"{func.__module__}.{func.__qualname__}"
    raw = raw.replace("<locals>.", ".")
    raw = raw.lower()
    raw = re.sub(r"[^a-z0-9_.]", "_", raw)
    segments = raw.split(".")
    segments = [f"_{s}" if s and s[0].isdigit() else s for s in segments]
    return ".".join(segments)
```

### 3. Implement module() function

- Accept `func_or_none` as positional-only first argument
- Accept keyword arguments: id, description, documentation, annotations, tags, version, metadata, registry
- Inner `_wrap(func, return_module)`:
  - Generate or use explicit module ID
  - Create FunctionModule
  - Optionally register with registry
  - If `return_module`: return FunctionModule directly
  - Else: attach `.apcore_module` attribute to function, return function
- Routing logic:
  - `func_or_none` is callable and no id/registry -> bare `@module`, `_wrap(func, return_module=False)`
  - `func_or_none` is callable with id or registry -> function call form, `_wrap(func, return_module=True)`
  - `func_or_none` is None -> return decorator closure

### 4. Verify tests pass

Run `pytest tests/test_decorator.py -k "module_decorator or auto_id" -v`.

## Acceptance Criteria

- [x] Bare `@module` works: decorates function, attaches `.apcore_module`
- [x] `@module(id="x")` works: uses explicit ID, returns decorated function
- [x] `module(func, id="x")` works: returns FunctionModule directly
- [x] `_make_auto_id()` normalizes qualified names correctly
- [x] Optional `registry` parameter triggers automatic registration
- [x] All kwargs are forwarded to FunctionModule constructor

## Dependencies

- `function-module` -- `FunctionModule` class must be implemented

## Estimated Time

2.5 hours
