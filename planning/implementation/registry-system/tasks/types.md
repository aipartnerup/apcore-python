# Task: DiscoveredModule, ModuleDescriptor, DependencyInfo Types

## Goal

Define the foundational type definitions for the registry system: `DiscoveredModule` for representing scanned module candidates, `ModuleDescriptor` for cross-language module descriptions, and `DependencyInfo` for parsed dependency metadata.

## Files Involved

- `src/apcore/registry/types.py` -- DiscoveredModule, ModuleDescriptor, DependencyInfo dataclasses (51 lines)
- `tests/test_registry_types.py` -- Type definition tests

## Steps

1. **Define ModuleDescriptor dataclass** (TDD: test fields, defaults, factory fields)
   - `module_id: str` -- unique identifier
   - `name: str | None` -- human-readable name
   - `description: str` -- module description
   - `documentation: str | None` -- extended documentation
   - `input_schema: dict[str, Any]` -- JSON Schema for input
   - `output_schema: dict[str, Any]` -- JSON Schema for output
   - `version: str = "1.0.0"` -- semver
   - `tags: list[str]` -- default_factory=list
   - `annotations: ModuleAnnotations | None = None` -- from apcore.module
   - `examples: list[ModuleExample]` -- default_factory=list
   - `metadata: dict[str, Any]` -- default_factory=dict

2. **Define DiscoveredModule dataclass** (TDD: test fields, optional meta_path)
   - `file_path: Path` -- absolute path to the Python file
   - `canonical_id: str` -- dot-notation module ID derived from relative path
   - `meta_path: Path | None = None` -- path to companion `*_meta.yaml` file
   - `namespace: str | None = None` -- namespace prefix for multi-root scanning

3. **Define DependencyInfo dataclass** (TDD: test fields, defaults)
   - `module_id: str` -- the dependency module ID
   - `version: str | None = None` -- optional version constraint
   - `optional: bool = False` -- whether the dependency is optional

## Acceptance Criteria

- All dataclasses have correct fields with proper types and defaults
- Factory fields (lists, dicts) use `field(default_factory=...)` to avoid mutable defaults
- ModuleDescriptor includes both input and output schemas as dicts
- DiscoveredModule.canonical_id represents the dot-notation module path
- DependencyInfo.optional defaults to False

## Dependencies

- `apcore.module.ModuleAnnotations` and `apcore.module.ModuleExample` (for ModuleDescriptor)
- `pathlib.Path` (for file paths)

## Estimated Time

45 minutes
