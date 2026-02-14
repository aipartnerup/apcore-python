# Feature: Decorator & Bindings

## Overview

Two complementary approaches for module creation: the `@module` decorator for zero-boilerplate function wrapping, and YAML bindings via `BindingLoader` for declarative, code-free module registration. Both produce `FunctionModule` instances that participate fully in the executor pipeline (ACL, middleware, validation, async support). The decorator system includes automatic Pydantic model generation from function signatures with Context parameter detection, while the binding system supports four distinct schema resolution modes and directory scanning.

## Scope

### Included

- `_generate_input_model()` and `_generate_output_model()` for automatic Pydantic model generation from function type annotations
- `_has_context_param()` for type-based (not name-based) Context parameter detection
- `FunctionModule` wrapper class with separate sync/async execute closures, description priority chain, and result normalization
- `@module` decorator supporting bare, with-arguments, and function-call forms with auto-ID generation from `__module__` + `__qualname__`
- `BindingLoader` for YAML binding file parsing with `module_id` and `target` fields
- Target resolution via `module.path:callable` format including class method binding with auto-instantiation
- Four schema modes: `auto_schema`, inline `input_schema`/`output_schema`, `schema_ref` (external YAML), and default (auto fallback)
- JSON Schema to Pydantic conversion with unsupported feature fallback to permissive models
- Directory scanning via `load_binding_dir()` with configurable glob pattern
- Eight specific error classes for all decorator and binding failure modes

### Excluded

- Schema validation at call time (schemas are for documentation and type inference, not runtime enforcement)
- Hot-reload of binding files (bindings are loaded once at startup)
- Decorator stacking or composition

## Technology Stack

- **Language**: Python 3.10+
- **Dependencies**: pydantic (`BaseModel`, `create_model`, `ConfigDict`), PyYAML (`yaml`), stdlib (`inspect`, `typing`, `re`, `importlib`, `pathlib`)
- **Internal**: `apcore.context.Context`, `apcore.registry.Registry`, `apcore.errors` (8 error classes)
- **Testing**: pytest

## Task Execution Order

| # | Task File | Description | Status |
|---|-----------|-------------|--------|
| 1 | [type-inference](./tasks/type-inference.md) | `_generate_input_model` and `_generate_output_model` from function type annotations | completed |
| 2 | [function-module](./tasks/function-module.md) | `FunctionModule` wrapper with sync/async execute closures and result normalization | completed |
| 3 | [module-decorator](./tasks/module-decorator.md) | `@module` decorator (bare, with-arguments, function-call) with auto-ID generation | completed |
| 4 | [binding-loader](./tasks/binding-loader.md) | `BindingLoader` for YAML binding file parsing and target resolution | completed |
| 5 | [schema-modes](./tasks/schema-modes.md) | Four schema modes: auto_schema, inline, schema_ref, default fallback | completed |
| 6 | [binding-directory](./tasks/binding-directory.md) | Directory scanning via `load_binding_dir()` with glob pattern | completed |

## Progress

| Total | Completed | In Progress | Pending |
|-------|-----------|-------------|---------|
| 6     | 6         | 0           | 0       |

## Reference Documents

- [Decorator & Bindings Feature Specification](../../features/decorator-bindings.md)
