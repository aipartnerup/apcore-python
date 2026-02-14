# Task: Schema Validation Implementation

## Goal

Implement the `SchemaValidator` that validates runtime data against Pydantic models and produces structured error output in the apcore-standard format, mapping Pydantic v2 error types to meaningful constraint names.

## Files Involved

- `src/apcore/schema/validator.py` -- SchemaValidator class (109 lines)
- `tests/test_schema_validator.py` -- Validator unit tests

## Steps

1. **Define Pydantic-to-constraint mapping** (TDD: verify mapping completeness)
   - `_PYDANTIC_TO_CONSTRAINT` dict mapping Pydantic error types to apcore constraint names:
     - `missing` -> `required`, `string_type` -> `type`, `int_type` -> `type`
     - `string_too_short` -> `minLength`, `string_too_long` -> `maxLength`
     - `string_pattern_mismatch` -> `pattern`
     - `greater_than_equal` -> `minimum`, `less_than_equal` -> `maximum`
     - `literal_error` -> `enum`, `extra_forbidden` -> `additionalProperties`

2. **Implement SchemaValidator.__init__** (TDD: test coerce_types flag)
   - Accept `coerce_types: bool = True` parameter
   - When True: use non-strict Pydantic validation (allows type coercion)
   - When False: use `strict=True` for exact type matching

3. **Implement validate()** (TDD: test valid data returns valid result, invalid data returns errors)
   - Accept `data: dict` and `model: type[BaseModel]`
   - Call `model.model_validate(data, strict=not self._coerce_types)`
   - On success: return `SchemaValidationResult(valid=True, errors=[])`
   - On `PydanticValidationError`: convert to `SchemaValidationResult` with error details

4. **Implement validate_input() and validate_output()** (TDD: test validation and dump, error raising)
   - Call `_validate_and_dump()` which validates and returns `model_dump()` on success
   - On failure: convert to `SchemaValidationResult` and raise via `result.to_error()`

5. **Implement _pydantic_error_to_details()** (TDD: test error mapping for various constraint types)
   - Convert each Pydantic error to `SchemaValidationErrorDetail`:
     - `path`: `/`-joined location segments from error `loc`
     - `constraint`: mapped from Pydantic error type via `_PYDANTIC_TO_CONSTRAINT`
     - `message`: from error `msg`
     - `expected`: extracted from error `ctx` (check `expected`, `ge`, `le`, `gt`, `lt`, `min_length`, `max_length`, `pattern` keys)
     - `actual`: from `ctx.actual` or error `input`

## Acceptance Criteria

- Valid data returns `SchemaValidationResult(valid=True, errors=[])`
- Invalid data returns structured errors with path, message, constraint, expected, actual
- Pydantic error types are correctly mapped to apcore constraint names
- Unknown Pydantic types pass through unmapped
- `coerce_types=True` allows type coercion (e.g., "123" -> 123 for int fields)
- `coerce_types=False` rejects type mismatches
- `validate_input`/`validate_output` raise `SchemaValidationError` on failure
- Error paths use `/`-separated segments with leading `/`

## Dependencies

- Task: types-and-annotations (SchemaValidationResult, SchemaValidationErrorDetail)
- Task: model-generation (Pydantic models to validate against)

## Estimated Time

2 hours
