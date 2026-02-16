# Task: Dynamic Pydantic Model Generation from JSON Schema

## Goal

Implement the dynamic Pydantic `BaseModel` generation logic within `SchemaLoader` that converts JSON Schema property definitions into Python types and Pydantic `Field` instances, supporting the full range of JSON Schema types and composition keywords.

## Files Involved

- `src/apcore/schema/loader.py` -- `generate_model()`, `_schema_to_field_info()`, `_schema_to_type()`, `_handle_object()`, `_handle_array()`, `_handle_all_of()`, `_build_field()`, `_clone_field_with_default()` (lines 118-318)
- `tests/test_schema_model_generation.py` -- Model generation unit tests

## Steps

1. **Implement generate_model()** (TDD: test simple model, required/optional fields)
   - Accept `json_schema: dict` and `model_name: str`
   - Extract `properties` and `required` from schema
   - For each property: call `_schema_to_field_info()` to get `(python_type, FieldInfo)`
   - Optional fields: make type nullable (`type | None`), set default to None if no explicit default
   - Create model via `pydantic.create_model()`

2. **Implement _schema_to_field_info()** (TDD: test all JSON Schema types, const, enum, composition)
   - Handle empty schema: return `dict[str, Any]` with `Field(default=...)`
   - Reject unsupported keywords: `not`, `if/then/else` raise `SchemaParseError`
   - `const`: return `Literal[val]`
   - `enum`: return `Literal[tuple(values)]`
   - `oneOf`/`anyOf`: build `Union[types]` via `_schema_to_type()` per sub-schema
   - `allOf`: delegate to `_handle_all_of()` for property merging
   - Nullable types (`["string", "null"]`): combine with `| None`
   - `object` type: delegate to `_handle_object()`
   - `array` type: delegate to `_handle_array()`
   - Primitive types: map via `_TYPE_MAP` (string->str, integer->int, number->float, boolean->bool, null->NoneType)

3. **Implement _handle_object()** (TDD: test nested objects, additionalProperties, plain object)
   - With `properties`: recursively call `generate_model()` for nested model
   - With `additionalProperties`: return `dict[str, value_type]`
   - Plain object: return `dict[str, Any]`

4. **Implement _handle_array()** (TDD: test typed arrays, untyped arrays, uniqueItems)
   - With `items`: determine item type via `_schema_to_type()`, return `list[item_type]`
   - Without `items`: return `list[Any]`
   - `uniqueItems: true`: wrap with `Annotated[..., AfterValidator(_check_unique)]`

5. **Implement _handle_all_of()** (TDD: test property merging, type conflict detection)
   - Merge properties from all sub-schemas
   - Detect conflicting types on same property name: raise `SchemaParseError`
   - Collect required fields from all sub-schemas (deduplicate)
   - Generate merged model

6. **Implement _build_field()** (TDD: test numeric, string, array constraints, x-* extensions)
   - Map JSON Schema constraints to Pydantic Field kwargs:
     - `minimum` -> `ge`, `maximum` -> `le`, `exclusiveMinimum` -> `gt`, `exclusiveMaximum` -> `lt`
     - `multipleOf` -> `multiple_of`
     - `minLength/maxLength` for strings, `minItems/maxItems` for arrays
     - `pattern` -> `pattern`
   - Collect `x-*` keys and `format` into `json_schema_extra`
   - Handle `default` values

## Acceptance Criteria

- Generated models enforce required vs optional fields correctly
- All primitive types map to correct Python types
- `const` and `enum` produce `Literal` types
- `oneOf`/`anyOf` produce `Union` types
- `allOf` merges properties and detects type conflicts
- Nested objects generate nested Pydantic models
- Arrays with typed items enforce item type
- `uniqueItems` validates list uniqueness at runtime
- Numeric and string constraints (min, max, pattern) are enforced
- `x-*` extension fields are preserved in `json_schema_extra`
- Optional fields default to None with nullable types

## Dependencies

- Task: types-and-annotations (type definitions)
- Task: ref-resolver (resolved schemas as input)

## Estimated Time

3 hours
