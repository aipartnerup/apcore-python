# Task: Schema Export Utilities

## Goal

Implement schema query and export functions that extract schema information from registered modules and export it in various formats (JSON, YAML) with support for strict mode, compact mode, and platform-specific profiles (MCP, OpenAI, Anthropic, generic).

## Files Involved

- `src/apcore/registry/schema_export.py` -- get_schema, export_schema, get_all_schemas, export_all_schemas, helpers (189 lines)
- `tests/test_registry_schema_export.py` -- Schema export unit tests

## Steps

1. **Implement get_schema()** (TDD: test schema extraction from module, missing module)
   - Accept `registry` and `module_id`
   - Return None if module not found
   - Extract input_schema and output_schema via `model_json_schema()`
   - Extract annotations (convert to dict via dataclasses.asdict if ModuleAnnotations)
   - Extract examples (convert to dict via dataclasses.asdict for ModuleExample instances)
   - Return structured dict with module_id, name, description, version, tags, schemas, annotations, examples

2. **Implement export_schema()** (TDD: test JSON/YAML format, strict mode, compact mode, profile)
   - Get schema dict via `get_schema()`; raise `ModuleNotFoundError` if None
   - If `profile` specified: delegate to `_export_with_profile()`
   - If `strict`: apply `to_strict_schema()` to both schemas
   - If `compact`: apply `_apply_compact()` (truncate description, strip extensions, remove docs/examples)
   - Serialize via `_serialize()` (JSON with indent=2 or YAML)

3. **Implement get_all_schemas()** (TDD: test collecting all module schemas)
   - Iterate `registry.module_ids` and collect schemas via `get_schema()`
   - Return dict keyed by module_id

4. **Implement export_all_schemas()** (TDD: test combined export with strict/compact modes)
   - Get all schemas, apply strict or compact transformations, serialize

5. **Implement _export_with_profile()** (TDD: test profile-based export via SchemaExporter)
   - Build `SchemaDefinition` from schema dict
   - Extract module annotations, examples, name from registry module
   - Delegate to `SchemaExporter().export()` with the profile
   - Serialize the result

6. **Implement helpers** (TDD: test description truncation, extension stripping, serialization)
   - `_apply_compact()`: truncate description to first sentence, strip x-* extensions, remove documentation and examples
   - `_truncate_description()`: find first `. ` or `\n`, cut at the earliest one
   - `_serialize()`: JSON (json.dumps, indent=2) or YAML (yaml.dump)

## Acceptance Criteria

- get_schema returns None for unregistered modules
- get_schema correctly extracts JSON schemas from Pydantic model classes
- export_schema supports both JSON and YAML output formats
- Strict mode applies additionalProperties: false to both input and output schemas
- Compact mode truncates descriptions, strips extensions, removes docs/examples
- Profile-based export delegates to SchemaExporter with correct parameters
- get_all_schemas collects schemas for all registered modules
- export_all_schemas applies transformations to all schemas before serialization
- Description truncation finds the earliest sentence boundary

## Dependencies

- Task: registry-core (Registry class for module access)
- Schema system: SchemaExporter, to_strict_schema, _strip_extensions

## Estimated Time

3 hours
