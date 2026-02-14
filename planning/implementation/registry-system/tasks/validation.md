# Task: Module Structural Validation

## Goal

Implement the validation function that checks module classes against structural and interface requirements before registration, ensuring they have the required attributes and correct types.

## Files Involved

- `src/apcore/registry/validation.py` -- validate_module function (46 lines)
- `tests/test_registry_validation.py` -- Validation unit tests

## Steps

1. **Implement validate_module()** (TDD: test valid module, missing each attribute, wrong types)
   - Accept `module_or_class: type | Any` (class or instance)
   - If instance, use `type(instance)` for validation
   - Return `list[str]` of error messages (empty = valid)

2. **Check input_schema** (TDD: test missing, None, non-BaseModel subclass)
   - Must be a class attribute that is a `BaseModel` subclass
   - Error: "Missing or invalid input_schema: must be a BaseModel subclass"

3. **Check output_schema** (TDD: test missing, None, non-BaseModel subclass)
   - Must be a class attribute that is a `BaseModel` subclass
   - Error: "Missing or invalid output_schema: must be a BaseModel subclass"

4. **Check description** (TDD: test missing, empty string, non-string)
   - Must be a non-empty string
   - Error: "Missing or empty description"

5. **Check execute** (TDD: test missing, not callable)
   - Must be a callable attribute
   - Error: "Missing execute method"

## Acceptance Criteria

- Valid modules return empty error list
- Each missing/invalid attribute produces exactly one error message
- Multiple missing attributes produce multiple errors (all checked, not short-circuited)
- Works with both classes and instances
- input_schema and output_schema must be actual BaseModel subclasses (not instances)
- Description must be a non-empty string (not just truthy)
- Execute must be callable (method or function)

## Dependencies

- `pydantic.BaseModel` (for subclass checking)
- `inspect` (for isclass check)

## Estimated Time

1 hour
