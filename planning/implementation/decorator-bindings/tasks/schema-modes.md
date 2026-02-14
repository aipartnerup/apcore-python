# Task: Four Schema Modes for Binding Entries

## Goal

Implement four schema determination strategies in `BindingLoader._create_module_from_binding()`: `auto_schema` (type hints), inline JSON Schema, `schema_ref` (external file), and default (auto with error fallback). Each mode produces Pydantic BaseModel classes for input and output validation.

## Files Involved

- `src/apcore/bindings.py` -- `_create_module_from_binding()` and `_build_model_from_json_schema()` helper
- `tests/test_bindings.py` -- Unit tests for each schema mode

## Steps

### 1. Write failing tests (TDD)

Create tests for:

**auto_schema mode** (`auto_schema: true`):
- Function with full type hints -> generates input/output models
- Function with missing type hints -> raises `BindingSchemaMissingError`

**Inline schema mode** (`input_schema`/`output_schema` in YAML):
- Inline JSON Schema with properties and required -> creates model with correct fields
- Empty inline schema -> permissive model (extra="allow")
- Unsupported JSON Schema features (oneOf, anyOf, $ref) -> falls back to permissive model

**schema_ref mode** (`schema_ref` key):
- External YAML file with input_schema/output_schema -> loads and creates models
- Missing schema ref file -> raises `BindingFileInvalidError`
- Invalid YAML in ref file -> raises `BindingFileInvalidError`
- Empty ref file -> permissive models

**Default mode** (no schema keys):
- Function with full type hints -> auto-generates (same as auto_schema)
- Function with missing type hints -> raises `BindingSchemaMissingError`

### 2. Implement _build_model_from_json_schema()

- Check for unsupported top-level keys (oneOf, anyOf, allOf, $ref, format) -> fallback to permissive model
- Extract `properties` and `required` from schema
- No properties -> permissive model
- Map JSON Schema types to Python types via `_JSON_SCHEMA_TYPE_MAP`
- Build field dict with `(type, ...)` for required, `(type, None)` for optional
- Create model via `pydantic.create_model()`

### 3. Implement schema mode selection in _create_module_from_binding()

Priority order:
1. `auto_schema: true` -> use `_generate_input_model()` / `_generate_output_model()`
2. `input_schema` or `output_schema` keys present -> use `_build_model_from_json_schema()`
3. `schema_ref` key present -> load external file, use `_build_model_from_json_schema()`
4. Default (none of above) -> try auto_schema, raise `BindingSchemaMissingError` on failure

### 4. Verify tests pass

Run `pytest tests/test_bindings.py -k "schema" -v`.

## Acceptance Criteria

- [x] `auto_schema: true` generates models from function type hints
- [x] Inline JSON Schema creates Pydantic models with correct field types and requirements
- [x] `schema_ref` loads external YAML file relative to binding file directory
- [x] Default mode tries auto-generation, raises descriptive error on failure
- [x] Unsupported JSON Schema features fall back to permissive models
- [x] `_JSON_SCHEMA_TYPE_MAP` maps string/integer/number/boolean/array/object to Python types

## Dependencies

- `binding-loader` -- `BindingLoader` class and `_create_module_from_binding()` must exist
- `type-inference` -- `_generate_input_model()`, `_generate_output_model()` for auto_schema mode

## Estimated Time

3 hours
