# Task: Strict Validation Mode

## Goal

Implement strict mode conversion for JSON Schemas, producing schemas compatible with OpenAI/Anthropic strict mode requirements. This includes stripping x-* extensions and defaults, enforcing `additionalProperties: false`, making all properties required, and wrapping optional fields as nullable.

## Files Involved

- `src/apcore/schema/strict.py` -- to_strict_schema, _apply_llm_descriptions, _strip_extensions, _convert_to_strict (105 lines)
- `tests/test_schema_strict.py` -- Strict mode unit tests

## Steps

1. **Implement _strip_extensions()** (TDD: test x-* removal, default removal, recursive)
   - In-place removal of all keys starting with `x-` and `default` keys
   - Recurse into nested dicts and lists of dicts
   - Handles properties, items, oneOf/anyOf/allOf, definitions/$defs

2. **Implement _apply_llm_descriptions()** (TDD: test x-llm-description override, nested recursion)
   - In-place replacement: if both `x-llm-description` and `description` exist, replace description with x-llm-description value
   - Recurse into properties, items, oneOf/anyOf/allOf, definitions/$defs

3. **Implement _convert_to_strict()** (TDD: test additionalProperties, required list, nullable wrapping)
   - For object schemas with properties:
     - Set `additionalProperties: false`
     - Identify optional properties (not in existing required list)
     - For optional properties with `type` string: convert to `[type, "null"]`
     - For optional properties with `type` list: append `"null"` if not present
     - For optional properties without `type` (pure $ref or composition): wrap in `oneOf: [original, {type: "null"}]`
     - Set `required` to sorted list of all property names
   - Recurse into nested structures: properties, items, oneOf/anyOf/allOf, definitions/$defs

4. **Implement to_strict_schema()** (TDD: test full pipeline: deep copy, strip, convert)
   - Deep copy the input schema
   - Call `_strip_extensions()` then `_convert_to_strict()`
   - Return the result

## Acceptance Criteria

- All `x-*` keys and `default` keys are removed recursively
- `x-llm-description` replaces `description` where both exist
- `additionalProperties: false` is set on all object schemas with properties
- All properties are listed in `required` (sorted alphabetically)
- Optional string-typed properties become `["string", "null"]`
- Optional list-typed properties have `"null"` appended
- Optional $ref properties are wrapped in `oneOf: [original, {type: "null"}]`
- Input schema is never mutated (deep copy)
- Recursive conversion handles nested objects, arrays, and composition keywords

## Dependencies

- None (standalone utility, used by exporter task)

## Estimated Time

2 hours
