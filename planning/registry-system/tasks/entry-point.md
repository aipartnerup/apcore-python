# Task: Entry Point Resolution

## Goal

Implement entry point resolution that dynamically imports Python files and resolves module classes, supporting both auto-inference via duck-type detection and explicit class specification from metadata.

## Files Involved

- `src/apcore/registry/entry_point.py` -- resolve_entry_point, snake_to_pascal, _is_module_class, _import_module_from_file functions (91 lines)
- `src/apcore/errors.py` -- ModuleLoadError
- `tests/test_registry_entry_point.py` -- Entry point resolution tests

## Steps

1. **Implement snake_to_pascal()** (TDD: test conversion cases, empty string)
   - Convert snake_case to PascalCase by splitting on `_` and capitalizing each part
   - Return empty string for empty input

2. **Implement _import_module_from_file()** (TDD: test successful import, import failure)
   - Use `importlib.util.spec_from_file_location()` with module name `apcore_ext_{stem}`
   - Create module from spec and execute via `spec.loader.exec_module()`
   - Raise `ModuleLoadError` if spec is None or loader is None
   - Wrap import exceptions in `ModuleLoadError`

3. **Implement _is_module_class()** (TDD: test duck-type detection criteria)
   - Check `cls.__module__` matches the loaded module name (excludes imported classes)
   - Check for `input_schema` and `output_schema` attributes (both non-None)
   - Both must be classes that are `BaseModel` subclasses
   - Check for callable `execute` attribute
   - All checks must pass for True

4. **Implement resolve_entry_point()** (TDD: test meta override, auto-inference, no candidates, ambiguous)
   - Import the file via `_import_module_from_file()`
   - **Meta override mode**: if `entry_point` in meta, extract class name after `:`, get from loaded module
   - **Auto-infer mode**: scan all classes via `inspect.getmembers()`, filter by `_is_module_class()`
   - Single candidate: return it
   - No candidates: raise `ModuleLoadError` ("No Module subclass found")
   - Multiple candidates: raise `ModuleLoadError` ("Ambiguous entry point")

## Acceptance Criteria

- Auto-inference finds the single module class in a file via duck-type detection
- Imported classes from other modules are excluded (cls.__module__ check)
- Meta-specified entry points resolve to the named class
- Missing meta-specified classes raise ModuleLoadError
- Files with no module classes raise ModuleLoadError
- Files with multiple module classes raise ModuleLoadError (ambiguous)
- Import failures wrap the cause in ModuleLoadError
- snake_to_pascal correctly handles multi-segment names

## Dependencies

- Task: types (for understanding module class interface)

## Estimated Time

2 hours
