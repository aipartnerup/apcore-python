# Feature: Schema System

## Overview

The Schema System provides complete schema loading, validation, `$ref` resolution, and export capabilities for structured module interfaces in apcore. It bridges human-authored YAML schema definitions and runtime Pydantic models used by the executor for input/output validation. The system supports three resolution strategies (`yaml_first`, `native_first`, `yaml_only`), resolves local, cross-file, and canonical (`apcore://`) `$ref` references with circular detection (`max_depth=32`), dynamically generates Pydantic `BaseModel` subclasses from JSON Schema, validates data with structured error output mapping, and exports schemas to MCP, OpenAI, Anthropic, and generic formats. A strict mode enforces `additionalProperties: false` for LLM provider compatibility.

## Scope

### Included

- Type definitions: `SchemaDefinition`, `ResolvedSchema`, `SchemaValidationResult`, `SchemaValidationErrorDetail`, `LLMExtensions`, `SchemaStrategy`, `ExportProfile`
- `SchemaLoader` with YAML parsing, strategy-based resolution, schema/model caching, and dynamic Pydantic model generation via `create_model()`
- `RefResolver` handling local (`#/definitions/...`), cross-file, and canonical (`apcore://`) `$ref` references with circular detection and `max_depth=32`
- Dynamic Pydantic model generation supporting primitives, objects, arrays, nullable types, `oneOf`/`anyOf`/`allOf` composition, `const`, `enum`, `uniqueItems`, numeric/string constraints, and `x-*` extension fields
- `SchemaValidator` wrapping Pydantic validation with error type mapping (e.g., `missing` -> `required`, `string_too_short` -> `minLength`)
- `SchemaExporter` for MCP (preserves `x-*`), OpenAI (strict mode, underscored names), Anthropic (strips extensions, supports examples), and generic (full pass-through) formats
- Strict mode conversion: `additionalProperties: false`, all properties required, optional fields become nullable
- Annotation merging with YAML > code > defaults priority

### Excluded

- Executor pipeline integration details (consumer of the schema system)
- Registry discovery pipeline (consumer of the schema system)
- YAML schema file authoring guidelines

## Technology Stack

- **Python 3.10+** with `from __future__ import annotations`
- **pydantic >= 2.0** for runtime model generation (`create_model`), data validation, and `model_json_schema()`
- **pyyaml >= 6.0** for YAML schema file parsing
- **pytest** for unit and integration testing

## Task Execution Order

| # | Task File | Description | Status |
|---|-----------|-------------|--------|
| 1 | [types-and-annotations](./tasks/types-and-annotations.md) | SchemaDefinition, ResolvedSchema, LLMExtensions types and annotation merging | completed |
| 2 | [loader](./tasks/loader.md) | SchemaLoader with YAML parsing and strategy-based resolution | completed |
| 3 | [ref-resolver](./tasks/ref-resolver.md) | $ref resolution with circular detection (max_depth=32) | completed |
| 4 | [model-generation](./tasks/model-generation.md) | Dynamic Pydantic model generation from JSON Schema | completed |
| 5 | [validator](./tasks/validator.md) | Schema validation with Pydantic error mapping | completed |
| 6 | [exporter](./tasks/exporter.md) | Schema export to MCP/OpenAI/Anthropic/generic formats | completed |
| 7 | [strict-mode](./tasks/strict-mode.md) | Strict validation mode with additionalProperties: false | completed |

## Progress

| Total | Completed | In Progress | Pending |
|-------|-----------|-------------|---------|
| 7     | 7         | 0           | 0       |

## Reference Documents

- [Schema System Feature Specification](../../features/schema-system.md)
