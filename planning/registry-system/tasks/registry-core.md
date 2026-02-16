# Task: Central Registry Class with 8-Step discover()

## Goal

Implement the central `Registry` class that manages the module store, coordinates the 8-step discovery pipeline, handles manual registration/unregistration, provides query methods, and supports lifecycle hooks and event callbacks with thread-safe access.

## Files Involved

- `src/apcore/registry/registry.py` -- Registry class (410 lines)
- `tests/test_registry.py` -- Registry unit tests

## Steps

1. **Implement Registry.__init__** (TDD: test initialization with various extension root configs)
   - Accept `config`, `extensions_dir`, `extensions_dirs`, `id_map_path`
   - Raise `InvalidInputError` if both `extensions_dir` and `extensions_dirs` specified
   - Determine extension roots: params > config > default ("./extensions")
   - Initialize `_modules`, `_module_meta`, `_callbacks`, `_write_lock` (RLock), `_id_map`, `_schema_cache`
   - Load ID map if `id_map_path` provided

2. **Implement discover()** (TDD: test full pipeline with fixture directories)
   - Step 1: Scan extension roots (single or multi-root based on configuration)
   - Step 2: Apply ID map overrides to canonical IDs
   - Step 3: Load metadata for each discovered module
   - Step 4: Resolve entry points with class override from ID map
   - Step 5: Validate module classes; skip invalid with warning
   - Step 6: Collect dependencies from metadata
   - Step 7: Resolve dependency order via topological sort
   - Step 8: Instantiate, merge metadata, register under RLock, call on_load, emit register event
   - Return count of successfully registered modules
   - Log warnings for zero registrations

3. **Implement register() and unregister()** (TDD: test manual registration, duplicates, lifecycle hooks)
   - `register(module_id, module)`: validate non-empty ID, check for duplicates, add under RLock, call on_load, emit event
   - `unregister(module_id)`: remove under RLock, call on_unload, emit unregister event; return False if not found
   - On_load failure: roll back registration, re-raise exception

4. **Implement query methods** (TDD: test get, has, list with filters, iter, count, module_ids)
   - `get(module_id)`: return module or None; raise ModuleNotFoundError for empty string
   - `has(module_id)`: boolean check under RLock
   - `list(tags, prefix)`: filtered sorted list; tag check against module attrs and metadata
   - `iter()`: snapshot-based iterator of (id, module) tuples
   - `count`: property returning module count
   - `module_ids`: property returning sorted list of IDs
   - `get_definition(module_id)`: build ModuleDescriptor from module and merged metadata

5. **Implement event system** (TDD: test callback registration, invocation, error swallowing)
   - `on(event, callback)`: register callback for 'register' or 'unregister' events
   - `_trigger_event(event, module_id, module)`: snapshot callbacks under RLock, invoke each, log and swallow exceptions

6. **Implement clear_cache()** (TDD: test cache clearing)
   - Clear `_schema_cache` under RLock

## Acceptance Criteria

- 8-step discovery pipeline processes modules in correct order
- Failed entry point resolution logs warning and skips the module
- Failed validation logs warning and skips the module
- Failed instantiation logs error and skips the module
- on_load failure rolls back registration and skips the module
- Manual registration rejects empty IDs and duplicates
- All public methods are thread-safe via RLock
- Event callbacks are invoked with module_id and module; exceptions are logged and swallowed
- Query methods correctly filter by tags (from module attrs and metadata) and prefix
- get_definition builds complete ModuleDescriptor from module and metadata
- discover() returns count of registered modules and logs warnings when zero

## Dependencies

- Task: scanner (scan_extensions, scan_multi_root)
- Task: metadata (load_metadata, merge_module_metadata, parse_dependencies, load_id_map)
- Task: dependencies (resolve_dependencies)
- Task: entry-point (resolve_entry_point)
- Task: validation (validate_module)
- Task: types (ModuleDescriptor, DiscoveredModule, DependencyInfo)

## Estimated Time

4 hours
