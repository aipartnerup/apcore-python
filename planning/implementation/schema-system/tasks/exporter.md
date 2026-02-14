# Task: Schema Export to MCP/OpenAI/Anthropic/Generic Formats

## Goal

Implement the `SchemaExporter` that converts `SchemaDefinition` objects into platform-specific export formats for MCP, OpenAI, Anthropic, and generic integrations, handling extension field stripping and strict mode conversion as appropriate for each target.

## Files Involved

- `src/apcore/schema/exporter.py` -- SchemaExporter class (99 lines)
- `tests/test_schema_exporter.py` -- Exporter unit tests

## Steps

1. **Implement export() dispatcher** (TDD: test routing to correct format method)
   - Accept `schema_def`, `profile: ExportProfile`, optional `annotations`, `examples`, `name`
   - Route to `export_mcp`, `export_openai`, `export_anthropic`, or `export_generic` based on profile

2. **Implement export_mcp()** (TDD: test MCP format output, annotations mapping)
   - Output format: `{ name, description, inputSchema, annotations: { readOnlyHint, destructiveHint, idempotentHint, openWorldHint } }`
   - `name` uses provided name or `schema_def.module_id`
   - `inputSchema` is the raw input_schema (preserves x-* fields)
   - Annotations default: readonly=False, destructive=False, idempotent=False, openWorld=True

3. **Implement export_openai()** (TDD: test OpenAI format, strict mode, name transformation)
   - Deep copy input_schema
   - Apply `_apply_llm_descriptions()` to replace description with x-llm-description
   - Apply `to_strict_schema()` for strict mode conversion
   - Output format: `{ type: "function", function: { name, description, parameters, strict: true } }`
   - Name: replace "." with "_" in module_id

4. **Implement export_anthropic()** (TDD: test Anthropic format, extension stripping, examples)
   - Deep copy input_schema
   - Apply `_apply_llm_descriptions()` then `_strip_extensions()`
   - Output format: `{ name, description, input_schema }`
   - Name: replace "." with "_" in module_id
   - If examples provided: add `input_examples` list of input dicts

5. **Implement export_generic()** (TDD: test generic format pass-through)
   - Output format: `{ module_id, description, input_schema, output_schema, definitions }`
   - No modifications to schemas

## Acceptance Criteria

- MCP format preserves x-* extension fields in inputSchema
- OpenAI format applies strict mode (additionalProperties: false, all required, nullable optionals)
- OpenAI format replaces module_id dots with underscores in name
- Anthropic format strips x-* extensions and defaults
- Anthropic format includes input_examples when examples are provided
- Generic format passes through all schema data unmodified
- x-llm-description replaces description where present (OpenAI and Anthropic)
- SchemaExporter is stateless (no instance state beyond methods)

## Dependencies

- Task: types-and-annotations (SchemaDefinition, ExportProfile)
- Task: strict-mode (_apply_llm_descriptions, _strip_extensions, to_strict_schema)

## Estimated Time

2.5 hours
