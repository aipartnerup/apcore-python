# Task: YAML Metadata Loading and Merging

## Goal

Implement metadata loading from companion `*_meta.yaml` files, dependency parsing from metadata, module metadata merging (YAML > code > defaults), and ID map loading for canonical ID overrides.

## Files Involved

- `src/apcore/registry/metadata.py` -- load_metadata, parse_dependencies, merge_module_metadata, load_id_map functions (123 lines)
- `tests/test_registry_metadata.py` -- Metadata unit tests

## Steps

1. **Implement load_metadata()** (TDD: test valid YAML, missing file, invalid YAML, empty file, non-mapping)
   - Accept `meta_path: Path`
   - Return empty dict if file does not exist
   - Parse YAML via `yaml.safe_load()`; raise `ConfigError` on invalid YAML
   - Return empty dict for None parse result
   - Raise `ConfigError` if parsed result is not a dict

2. **Implement parse_dependencies()** (TDD: test valid deps, missing module_id, empty list)
   - Accept `deps_raw: list[dict]`
   - For each entry: extract `module_id` (required), `version` (optional), `optional` (default False)
   - Skip entries missing `module_id` with a warning log
   - Return list of `DependencyInfo` objects

3. **Implement merge_module_metadata()** (TDD: test YAML override, code fallback, defaults)
   - Accept `module_class: type` and `meta: dict` (YAML metadata)
   - Extract code-level attributes: description, name, tags, version, annotations, examples, metadata, documentation
   - Merge priority: YAML > code > defaults
   - metadata sub-dict: shallow merge with YAML keys winning
   - Return merged dict with all standard keys

4. **Implement load_id_map()** (TDD: test valid map, missing file, invalid format, malformed entries)
   - Accept `id_map_path: Path`
   - Raise `ConfigNotFoundError` if file does not exist (ID map is explicitly requested)
   - Parse YAML; require top-level `mappings` list
   - Build dict keyed by `file` field with `id` and `class` values
   - Skip entries missing `file` field with warning log

## Acceptance Criteria

- load_metadata gracefully handles missing, empty, and non-mapping files
- parse_dependencies skips entries without module_id and logs warning
- merge_module_metadata follows YAML > code > defaults priority for all fields
- Nested metadata dict is merged (code base + YAML overlay)
- load_id_map requires explicit file existence (raises ConfigNotFoundError)
- load_id_map validates top-level structure (requires `mappings` list)
- Invalid YAML raises ConfigError with descriptive message

## Dependencies

- Task: types (DependencyInfo dataclass)

## Estimated Time

2 hours
