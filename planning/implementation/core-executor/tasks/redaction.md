# Task: Sensitive Field Redaction Utility

## Goal

Implement the `redact_sensitive` utility that walks input/output dictionaries and replaces values of fields marked `x-sensitive: true` in the schema with `***REDACTED***`. This ensures sensitive data never appears in logs or error reports.

## Files Involved

- `src/apcore/executor.py` -- `redact_sensitive()`, `_redact_fields()`, `_redact_secret_prefix()`, `REDACTED_VALUE` constant (lines 34-106)
- `tests/test_redaction.py` -- Redaction unit tests

## Steps

1. **Define REDACTED_VALUE constant** (TDD: verify constant value is "***REDACTED***")
   - `REDACTED_VALUE: str = "***REDACTED***"`

2. **Implement redact_sensitive** (TDD: test deep copy, no mutation of original data)
   - Accept `data: dict[str, Any]` and `schema_dict: dict[str, Any]`
   - Deep copy the data to avoid mutating the original
   - Call `_redact_fields()` for schema-based redaction
   - Call `_redact_secret_prefix()` for key-prefix-based redaction
   - Return the redacted copy

3. **Implement _redact_fields** (TDD: test flat fields, nested objects, arrays)
   - In-place redaction on the deep copy
   - Read `properties` from `schema_dict`; skip if missing
   - For each property: if `x-sensitive: true`, replace value with `REDACTED_VALUE` (skip None)
   - For nested objects (`type: object` with `properties`): recurse into the value dict
   - For arrays (`type: array` with `items`): if items are `x-sensitive`, redact each item; if items are objects with properties, recurse into each dict item

4. **Implement _redact_secret_prefix** (TDD: test keys starting with _secret_, non-matching keys)
   - In-place redaction of any key starting with `_secret_`
   - Replace non-None values with `REDACTED_VALUE`

## Acceptance Criteria

- Original data dict is never mutated (deep copy)
- Fields with `x-sensitive: true` in schema are replaced with `***REDACTED***`
- None values are not redacted (remain None)
- Nested object fields are recursively redacted
- Array items with `x-sensitive` are individually redacted
- Array items that are objects with properties are recursively redacted
- Keys starting with `_secret_` are redacted regardless of schema
- Non-sensitive fields pass through unchanged

## Dependencies

- None (standalone utility, used by execution-pipeline task at step 5)

## Estimated Time

1.5 hours
