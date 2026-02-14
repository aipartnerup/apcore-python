# Task: SchemaLoader with YAML Parsing and Strategy-Based Resolution

## Goal

Implement the primary schema loading entry point that reads YAML schema files, delegates to RefResolver for `$ref` resolution, generates Pydantic models, and supports three resolution strategies (yaml_first, native_first, yaml_only) with caching.

## Files Involved

- `src/apcore/schema/loader.py` -- SchemaLoader class (391 lines)
- `tests/test_schema_loader.py` -- Loader unit tests

## Steps

1. **Implement SchemaLoader.__init__** (TDD: test initialization with config, custom schemas_dir)
   - Accept `config: Config` and optional `schemas_dir`
   - Resolve schemas directory from config (`schema.root`, default `./schemas`)
   - Initialize `RefResolver` with schemas_dir and `max_depth` from config (`schema.max_ref_depth`, default 32)
   - Initialize `_schema_cache` and `_model_cache` dicts

2. **Implement load()** (TDD: test YAML parsing, required fields, caching, missing file)
   - Return cached `SchemaDefinition` if available
   - Convert module_id to file path: replace "." with "/" + ".schema.yaml"
   - Read and parse YAML via `yaml.safe_load()`
   - Validate required fields: `input_schema`, `output_schema`, `description`
   - Merge `definitions` and `$defs` into unified definitions dict
   - Warn if description exceeds 200 characters
   - Construct and cache `SchemaDefinition`

3. **Implement resolve()** (TDD: test $ref resolution, model generation for input/output)
   - Delegate input_schema and output_schema to `RefResolver.resolve()`
   - Generate Pydantic models via `generate_model()` for each
   - Return tuple of `(ResolvedSchema, ResolvedSchema)` for input and output

4. **Implement get_schema()** (TDD: test all three strategies, native fallback, caching)
   - Read strategy from config (`schema.strategy`, default `yaml_first`)
   - `yaml_first`: try YAML, fall back to native if SchemaNotFoundError
   - `native_first`: use native if available, fall back to YAML
   - `yaml_only`: YAML only, raise on missing
   - Cache result in `_model_cache`

5. **Implement _wrap_native()** (TDD: test wrapping native Pydantic models)
   - Wrap existing `BaseModel` subclasses as `ResolvedSchema` without re-generating
   - Extract json_schema via `model_json_schema()`

6. **Implement clear_cache()** (TDD: test cache clearing)
   - Clear `_schema_cache`, `_model_cache`, and `_resolver._file_cache`

## Acceptance Criteria

- YAML files are correctly parsed into SchemaDefinition
- Missing required fields raise SchemaParseError
- Empty or non-mapping YAML raises SchemaParseError
- Invalid YAML syntax raises SchemaParseError with cause
- Schema and model caches prevent redundant parsing
- All three strategies work correctly with proper fallback behavior
- Native models are wrapped without re-generation
- Long descriptions produce a warning log

## Dependencies

- Task: types-and-annotations (SchemaDefinition, ResolvedSchema, SchemaStrategy)
- Task: ref-resolver (RefResolver for $ref resolution)
- Task: model-generation (generate_model method)

## Estimated Time

3 hours
