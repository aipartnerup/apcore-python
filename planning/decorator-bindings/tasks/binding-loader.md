# Task: BindingLoader for YAML Binding Files

## Goal

Implement the `BindingLoader` class that loads YAML binding files, resolves Python callables from `module.path:callable` target strings, and creates `FunctionModule` instances. Supports function targets, `class.method` targets, and strict validation of binding file structure.

## Files Involved

- `src/apcore/bindings.py` -- `BindingLoader` class with `load_bindings()` and `resolve_target()`
- `src/apcore/errors.py` -- Binding-related error classes
- `src/apcore/registry.py` -- `Registry` for module registration
- `tests/test_bindings.py` -- Unit tests for binding loading and target resolution

## Steps

### 1. Write failing tests (TDD)

Create tests for:

**YAML loading**:
- Valid binding file with `module_id` and `target` fields -> creates FunctionModule and registers
- Missing file -> raises `BindingFileInvalidError`
- Invalid YAML -> raises `BindingFileInvalidError`
- Empty file -> raises `BindingFileInvalidError`
- Missing `bindings` key -> raises `BindingFileInvalidError`
- Non-list `bindings` -> raises `BindingFileInvalidError`
- Entry missing `module_id` -> raises `BindingFileInvalidError`
- Entry missing `target` -> raises `BindingFileInvalidError`

**Target resolution**:
- `module.path:function_name` -> resolves to function
- `module.path:ClassName.method_name` -> instantiates class, returns bound method
- Missing `:` separator -> raises `BindingInvalidTargetError`
- Module not importable -> raises `BindingModuleNotFoundError`
- Callable not found in module -> raises `BindingCallableNotFoundError`
- Resolved target not callable -> raises `BindingNotCallableError`
- Class cannot be instantiated -> raises `BindingCallableNotFoundError`

### 2. Implement resolve_target()

- Split target by `:` -> `(module_path, callable_name)`
- Import module via `importlib.import_module(module_path)`
- If `callable_name` contains `.`: split into `(class_name, method_name)`, get class, instantiate, get method
- Otherwise: `getattr(mod, callable_name)`
- Validate result is callable

### 3. Implement load_bindings()

- Read file via `pathlib.Path.read_text()`
- Parse YAML via `yaml.safe_load()`
- Validate structure: non-None, has "bindings" key, bindings is list
- For each entry: validate module_id and target, call `_create_module_from_binding()`
- Register each module with registry

### 4. Verify tests pass

Run `pytest tests/test_bindings.py -k "load or resolve" -v`.

## Acceptance Criteria

- [x] Loads YAML binding files and creates FunctionModule instances
- [x] Resolves `module.path:callable` targets via dynamic import
- [x] Supports `class.method` binding via class instantiation
- [x] Strict validation of binding file structure with descriptive error messages
- [x] Each module is registered with the provided Registry
- [x] Specific error types for each failure mode

## Dependencies

- `type-inference` -- `_generate_input_model()`, `_generate_output_model()` for auto-schema
- `function-module` -- `FunctionModule` for wrapping resolved callables

## Estimated Time

3 hours
