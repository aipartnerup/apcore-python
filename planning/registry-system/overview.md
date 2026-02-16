# Feature: Registry System

## Overview

The Module Registry and Discovery System is the central hub for discovering, registering, and querying modules within apcore. It implements an 8-step discovery pipeline that scans extension directories for Python module files, applies ID map overrides, loads YAML metadata, resolves entry points via duck-type auto-inference or explicit meta override, validates module structural integrity, collects and resolves dependencies via Kahn's topological sort with cycle detection, and registers modules with lifecycle hooks (`on_load`/`on_unload`) and event callbacks (`register`/`unregister`). The registry provides thread-safe access via `threading.RLock` and supports flexible querying by tags, prefixes, and IDs, along with schema export utilities supporting multiple profiles and formats.

## Scope

### Included

- Type definitions: `DiscoveredModule`, `ModuleDescriptor`, `DependencyInfo` dataclasses
- `scan_extensions()` and `scan_multi_root()` for recursive directory scanning with symlink cycle detection, depth limits, duplicate/case-collision detection, and namespace prefixing
- `load_metadata()`, `merge_module_metadata()`, `parse_dependencies()`, `load_id_map()` for YAML metadata handling with YAML > code > defaults merge priority
- `resolve_dependencies()` using Kahn's topological sort with cycle path extraction
- `resolve_entry_point()` with duck-type auto-inference (checks `input_schema`, `output_schema`, `execute`) and meta-specified class override
- `validate_module()` checking `input_schema` (BaseModel subclass), `output_schema` (BaseModel subclass), `description` (non-empty string), `execute` (callable)
- `Registry` class with `discover()`, `register()`, `unregister()`, `get()`, `has()`, `list()`, `iter()`, `get_definition()`, event system (`on()`, `_trigger_event()`), and `clear_cache()`
- Schema export functions: `get_schema()`, `export_schema()`, `get_all_schemas()`, `export_all_schemas()` with profile (MCP/OpenAI/Anthropic/generic), strict, and compact modes

### Excluded

- Schema system internals (consumed as a dependency for schema export)
- Executor pipeline (consumer of the registry)
- Module implementation details (registry handles discovery and registration only)

## Technology Stack

- **Python 3.10+** with `from __future__ import annotations`
- **pydantic >= 2.0** for BaseModel subclass detection in validation and schema export
- **pyyaml >= 6.0** for YAML metadata and ID map file parsing
- **threading** (`RLock`) for thread-safe registry access
- **pytest** for unit and integration testing

## Task Execution Order

| # | Task File | Description | Status |
|---|-----------|-------------|--------|
| 1 | [types](./tasks/types.md) | DiscoveredModule, ModuleDescriptor, DependencyInfo type definitions | completed |
| 2 | [scanner](./tasks/scanner.md) | Multi-root extension directory scanning with symlink and depth handling | completed |
| 3 | [metadata](./tasks/metadata.md) | YAML metadata loading, dependency parsing, and merge logic | completed |
| 4 | [dependencies](./tasks/dependencies.md) | Topological sort via Kahn's algorithm with cycle detection | completed |
| 5 | [entry-point](./tasks/entry-point.md) | Entry point resolution with auto-inference and meta override | completed |
| 6 | [validation](./tasks/validation.md) | Module structural validation (schemas, description, execute method) | completed |
| 7 | [registry-core](./tasks/registry-core.md) | Central Registry class with 8-step discover() and query methods | completed |
| 8 | [schema-export](./tasks/schema-export.md) | Schema export utilities: get_schema, export_schema, get_all_schemas | completed |

## Progress

| Total | Completed | In Progress | Pending |
|-------|-----------|-------------|---------|
| 8     | 8         | 0           | 0       |

## Reference Documents

- [Registry System Feature Specification](../../features/registry-system.md)
