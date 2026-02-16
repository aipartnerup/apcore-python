# Task: SchemaDefinition, ResolvedSchema, LLMExtensions Types and Annotation Merging

## Goal

Define the foundational type definitions and enums for the schema system, plus annotation merging utilities that resolve conflicts between YAML and code-defined metadata with YAML > code > defaults priority.

## Files Involved

- `src/apcore/schema/types.py` -- SchemaDefinition, ResolvedSchema, SchemaValidationResult, SchemaValidationErrorDetail, LLMExtensions, SchemaStrategy, ExportProfile (109 lines)
- `src/apcore/schema/annotations.py` -- merge_annotations, merge_examples, merge_metadata (62 lines)
- `tests/test_schema_types.py` -- Type definition tests
- `tests/test_schema_annotations.py` -- Annotation merge tests

## Steps

1. **Define SchemaStrategy enum** (TDD: verify enum values yaml_first, native_first, yaml_only)
   - `str` + `Enum` subclass with three values

2. **Define ExportProfile enum** (TDD: verify enum values mcp, openai, anthropic, generic)
   - `str` + `Enum` subclass with four values

3. **Define SchemaDefinition dataclass** (TDD: verify fields, defaults)
   - `module_id`, `description`, `input_schema`, `output_schema`, `error_schema` (optional), `definitions` (default empty), `version` (default "1.0.0"), `documentation` (optional), `schema_url` (optional)

4. **Define ResolvedSchema dataclass** (TDD: verify fields)
   - `json_schema: dict`, `model: type[BaseModel]`, `module_id: str`, `direction: str`

5. **Define SchemaValidationErrorDetail and SchemaValidationResult** (TDD: verify to_error conversion)
   - ErrorDetail: `path`, `message`, `constraint` (optional), `expected` (optional), `actual` (optional)
   - Result: `valid: bool`, `errors: list[ErrorDetail]`, `to_error()` method converting to `SchemaValidationError`

6. **Define LLMExtensions dataclass** (TDD: verify defaults)
   - `llm_description`, `examples`, `sensitive: bool`, `constraints`, `deprecated`

7. **Implement merge_annotations** (TDD: test YAML override, code fallback, defaults)
   - Priority: YAML > code > defaults
   - Uses `ModuleAnnotations.__dataclass_fields__` for field enumeration

8. **Implement merge_examples** (TDD: test YAML takes full priority, code fallback)
   - YAML present: convert dicts to `ModuleExample` instances
   - YAML absent: return code_examples or empty list

9. **Implement merge_metadata** (TDD: test dict merge with YAML override)
   - Shallow merge: code dict as base, YAML keys override

## Acceptance Criteria

- All dataclasses have correct fields with proper defaults
- SchemaValidationResult.to_error() raises ValueError if result is valid
- Enums are string-based for serialization compatibility
- merge_annotations correctly applies YAML > code > defaults priority
- merge_examples uses YAML exclusively when present
- merge_metadata performs shallow merge with YAML winning on conflicts

## Dependencies

- `pydantic.BaseModel` (for ResolvedSchema.model type)
- `apcore.errors.SchemaValidationError` (for to_error conversion)
- `apcore.module.ModuleAnnotations`, `apcore.module.ModuleExample` (for annotation merging)

## Estimated Time

2 hours
