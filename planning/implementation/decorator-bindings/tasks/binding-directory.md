# Task: Directory Scanning for Binding Files

## Goal

Implement `BindingLoader.load_binding_dir()` that scans a directory for binding files matching a glob pattern and loads all of them. Provides bulk loading capability for projects with multiple binding files.

## Files Involved

- `src/apcore/bindings.py` -- `BindingLoader.load_binding_dir()` method
- `tests/test_bindings.py` -- Unit tests for directory scanning

## Steps

### 1. Write failing tests (TDD)

Create tests for:
- **Valid directory**: Directory with 2 binding files matching `*.binding.yaml` -> loads both, returns combined list
- **Empty directory**: No matching files -> returns empty list
- **Custom pattern**: `pattern="*.yaml"` matches different file naming convention
- **Nonexistent directory**: Raises `BindingFileInvalidError` with "Directory does not exist"
- **Sorted loading**: Files are loaded in sorted order (deterministic behavior)
- **Mixed files**: Only files matching pattern are loaded; other files ignored

### 2. Implement load_binding_dir()

```python
def load_binding_dir(
    self,
    dir_path: str,
    registry: Registry,
    pattern: str = "*.binding.yaml",
) -> list[FunctionModule]:
    p = pathlib.Path(dir_path)
    if not p.is_dir():
        raise BindingFileInvalidError(file_path=dir_path, reason="Directory does not exist")

    results: list[FunctionModule] = []
    for f in sorted(p.glob(pattern)):
        results.extend(self.load_bindings(str(f), registry))
    return results
```

### 3. Verify tests pass

Run `pytest tests/test_bindings.py -k "dir" -v`.

## Acceptance Criteria

- [x] Scans directory for files matching glob pattern (default `*.binding.yaml`)
- [x] Loads all matching files via `load_bindings()` and aggregates results
- [x] Files are loaded in sorted order for deterministic behavior
- [x] Nonexistent directory raises `BindingFileInvalidError`
- [x] Custom pattern parameter overrides default
- [x] Returns combined list of all FunctionModule instances

## Dependencies

- `binding-loader` -- `load_bindings()` must be implemented for individual file loading

## Estimated Time

1 hour
