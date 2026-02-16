# Task: Topological Sort with Cycle Detection

## Goal

Implement dependency resolution using Kahn's topological sort algorithm to determine module load order, with cycle detection that extracts and reports the full cycle path for debugging.

## Files Involved

- `src/apcore/registry/dependencies.py` -- resolve_dependencies, _extract_cycle functions (112 lines)
- `src/apcore/errors.py` -- CircularDependencyError, ModuleLoadError
- `tests/test_registry_dependencies.py` -- Dependency resolution unit tests

## Steps

1. **Implement resolve_dependencies()** (TDD: test linear chain, diamond, wide graph, empty input)
   - Accept `modules: list[tuple[str, list[DependencyInfo]]]` and optional `known_ids: set[str]`
   - If `known_ids` is None, derive from modules list
   - Build adjacency graph and in-degree map
   - For unknown required dependencies: raise `ModuleLoadError`
   - For unknown optional dependencies: log warning and skip
   - Initialize queue with sorted zero-in-degree nodes (deterministic order)

2. **Implement Kahn's algorithm** (TDD: test correct ordering for various DAG shapes)
   - Process queue: dequeue node, add to load_order, decrement in-degree of dependents
   - When in-degree reaches 0, add dependent to queue (sorted for determinism)
   - Continue until queue is empty

3. **Implement cycle detection** (TDD: test simple A->B->A cycle, multi-node cycle, self-cycle)
   - After Kahn's: if `len(load_order) < len(modules)`, a cycle exists
   - Collect remaining unprocessed module IDs
   - Extract cycle path via `_extract_cycle()`

4. **Implement _extract_cycle()** (TDD: test cycle path extraction for various topologies)
   - Build dependency map for remaining modules only
   - Follow edges from arbitrary start until revisiting a node
   - Extract cycle: from first occurrence of revisited node to end, plus revisited node
   - Fallback: return all remaining nodes if no clean cycle found
   - Raise `CircularDependencyError` with the extracted cycle path

## Acceptance Criteria

- Linear dependency chains produce correct order (A depends on B: B loads first)
- Diamond dependencies (A->B, A->C, B->D, C->D) produce valid topological order
- Wide graphs with no dependencies produce sorted alphabetical order
- Empty input returns empty list
- Unknown required dependencies raise ModuleLoadError with descriptive reason
- Unknown optional dependencies are silently skipped with warning log
- Circular dependencies raise CircularDependencyError with full cycle path
- Algorithm is deterministic: sorted queue initialization ensures consistent ordering

## Dependencies

- Task: types (DependencyInfo dataclass)

## Estimated Time

2 hours
