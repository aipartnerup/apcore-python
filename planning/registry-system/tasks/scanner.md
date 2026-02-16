# Task: Multi-Root Extension Directory Scanning

## Goal

Implement recursive directory scanning that discovers Python module files from configured extension directories, supporting multi-root scanning with namespace prefixing, symlink cycle detection, depth limits, and duplicate/case-collision detection.

## Files Involved

- `src/apcore/registry/scanner.py` -- scan_extensions, scan_multi_root functions (156 lines)
- `tests/test_registry_scanner.py` -- Scanner unit tests

## Steps

1. **Implement scan_extensions()** (TDD: test basic scanning, depth limit, permission errors)
   - Accept `root: Path`, `max_depth: int = 8`, `follow_symlinks: bool = False`
   - Resolve root to absolute path; raise `ConfigNotFoundError` if not exists
   - Track visited real paths for symlink cycle detection
   - Track seen IDs and seen IDs lowercase for duplicate/collision detection

2. **Implement recursive _scan_dir()** (TDD: test directory traversal, skip patterns)
   - Skip entries starting with `.` or `_`
   - Skip `__pycache__` and `node_modules` directories
   - Skip `.pyc` files and non-`.py` files
   - For directories: recurse with incremented depth; log and stop at max_depth
   - For symlinked directories: skip if `follow_symlinks=False`, detect cycles via `visited_real_paths`

3. **Implement canonical ID derivation** (TDD: test path-to-dot-notation conversion)
   - Compute relative path from root, strip `.py` suffix, replace `os.sep` with `.`
   - Detect duplicate IDs: log error and skip
   - Detect case collisions: log warning

4. **Implement companion meta file detection** (TDD: test _meta.yaml lookup)
   - Look for `{stem}_meta.yaml` alongside each `.py` file
   - Set `meta_path = None` if companion does not exist

5. **Implement scan_multi_root()** (TDD: test namespace prefixing, duplicate namespace detection)
   - Accept `roots: list[dict]` with `root` and optional `namespace` keys
   - Validate all namespaces before scanning; raise `ConfigError` on duplicates
   - Default namespace: root directory name
   - Prefix canonical IDs with `{namespace}.` for each root
   - Aggregate results from all roots

## Acceptance Criteria

- Python files are discovered recursively up to max_depth
- Hidden files/dirs (starting with `.` or `_`) are skipped
- `__pycache__` and `node_modules` are skipped
- `.pyc` and non-`.py` files are skipped
- Symlink cycles are detected and logged when follow_symlinks=True
- Symlinks are skipped entirely when follow_symlinks=False
- Duplicate canonical IDs are detected; first wins, subsequent logged and skipped
- Case collisions produce warning logs
- Permission errors are caught and logged (not raised)
- Multi-root scanning prefixes IDs with namespace
- Duplicate namespaces across roots raise ConfigError

## Dependencies

- Task: types (DiscoveredModule dataclass)

## Estimated Time

3 hours
