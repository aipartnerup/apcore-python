# Task: Context, Identity, and Config Data Classes

## Goal

Implement the foundational data classes that carry execution state through the pipeline: `Context` for per-call metadata and propagation, `Identity` for caller representation, and `Config` for dot-path configuration access.

## Files Involved

- `src/apcore/context.py` -- Context and Identity dataclasses (66 lines)
- `src/apcore/config.py` -- Config accessor class (29 lines)
- `tests/test_context.py` -- Unit tests for Context and Identity
- `tests/test_config.py` -- Unit tests for Config

## Steps

1. **Define Identity dataclass** (TDD: write tests for frozen Identity with id, type, roles, attrs)
   - Frozen dataclass with `id: str`, `type: str = "user"`, `roles: list[str]`, `attrs: dict[str, Any]`
   - Verify immutability and default values

2. **Define Context dataclass** (TDD: write tests for create, child, call_chain propagation)
   - Mutable dataclass with `trace_id`, `caller_id`, `call_chain`, `executor`, `identity`, `redacted_inputs`, `data`
   - `create()` class method generates UUID v4 trace_id and initializes empty call_chain
   - `child()` method creates new Context with `target_module_id` appended to call_chain, `caller_id` derived from last chain entry, and shared `data` dict reference

3. **Define Config class** (TDD: write tests for dot-path navigation, default values, missing keys)
   - `__init__` accepts optional `data: dict[str, Any]`
   - `get(key, default)` splits key on "." and navigates nested dicts
   - Returns `default` when any segment is missing or current is not a dict

## Acceptance Criteria

- Identity is frozen (raises on attribute assignment)
- Context.create() produces unique trace_ids (UUID v4 format)
- Context.child() shares the `data` dict reference (mutations visible to parent)
- Context.child() sets `caller_id` to the last entry in the parent's call_chain
- Config.get() navigates nested dicts via dot-separated keys
- Config.get() returns default when key path is missing

## Dependencies

- None (foundational task)

## Estimated Time

1.5 hours
