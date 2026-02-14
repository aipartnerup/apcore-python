# Task: $ref Resolution with Circular Detection

## Goal

Implement the `RefResolver` that handles `$ref` resolution within JSON Schema documents, supporting local, cross-file, and canonical reference formats with circular reference detection and a configurable max_depth (default 32).

## Files Involved

- `src/apcore/schema/ref_resolver.py` -- RefResolver class (206 lines)
- `src/apcore/errors.py` -- SchemaCircularRefError, SchemaNotFoundError, SchemaParseError
- `tests/test_ref_resolver.py` -- RefResolver unit tests

## Steps

1. **Implement RefResolver.__init__** (TDD: test initialization with schemas_dir, max_depth)
   - Accept `schemas_dir` (resolved to absolute Path) and `max_depth` (default 32)
   - Initialize `_file_cache: dict[Path, dict]` for parsed file caching

2. **Implement resolve()** (TDD: test deep copy, inline sentinel caching, no mutation of input)
   - Deep copy the input schema to avoid mutation
   - Cache the copy under `_INLINE_SENTINEL` for local ref resolution
   - Call `_resolve_node()` on the copy
   - Clean up the sentinel from cache in `finally` block
   - Return the resolved copy

3. **Implement _parse_ref()** (TDD: test local refs, cross-file refs, canonical URIs)
   - `#/path` -- local reference: return `(current_file or INLINE_SENTINEL, pointer)`
   - `apcore://module.id/path` -- canonical: convert to file path via `_convert_canonical_to_path()`
   - `file.yaml#/path` -- cross-file: resolve relative to current_file parent or schemas_dir
   - `file.yaml` (no fragment) -- cross-file with empty pointer

4. **Implement resolve_ref()** (TDD: test circular detection, max_depth, sibling key merging, nested refs)
   - Check `ref_string` against `visited_refs` set; raise `SchemaCircularRefError` if duplicate
   - Check `depth >= max_depth`; raise `SchemaCircularRefError` if exceeded
   - Add ref_string to visited_refs
   - Parse ref, load file, resolve JSON pointer
   - Deep copy the target; merge sibling keys if present
   - If result still contains `$ref`, resolve recursively with incremented depth
   - Call `_resolve_node()` on the result for nested refs

5. **Implement _resolve_node()** (TDD: test dict traversal, list traversal, in-place mutation)
   - For dicts with `$ref`: extract sibling keys, resolve via `resolve_ref()`, clear dict and update with result
   - For dicts without `$ref`: recurse into all values
   - For lists: recurse into each item
   - Uses `visited_refs.copy()` for each $ref to allow the same ref in different branches

6. **Implement _load_file()** (TDD: test file caching, missing file, invalid YAML, empty file)
   - Return cached result if available
   - Handle inline sentinel
   - Read and parse YAML; cache the result
   - Raise SchemaNotFoundError for missing files, SchemaParseError for invalid YAML
   - Return empty dict for empty/null YAML content

7. **Implement _resolve_json_pointer()** (TDD: test RFC 6901 navigation, tilde escaping)
   - Navigate document using `/`-separated segments
   - Apply tilde unescaping: `~1` -> `/`, `~0` -> `~`
   - Raise SchemaNotFoundError if segment not found

## Acceptance Criteria

- Local `$ref` references resolve correctly within the same document
- Cross-file references load and inline from external YAML files
- Canonical `apcore://` URIs are converted to file paths and resolved
- Circular references are detected and raise SchemaCircularRefError
- Max depth exceeded raises SchemaCircularRefError with descriptive message
- Sibling keys alongside `$ref` are merged into the resolved result
- File cache prevents redundant YAML parsing
- Original schema is never mutated (deep copy)
- RFC 6901 JSON Pointer navigation with tilde escaping works correctly

## Dependencies

- Task: types-and-annotations (error types)

## Estimated Time

3 hours
