"""Dependency resolution via Kahn's topological sort."""

from __future__ import annotations

import logging
from collections import defaultdict, deque

from apcore.errors import CircularDependencyError, ModuleLoadError
from apcore.registry.types import DependencyInfo

logger = logging.getLogger(__name__)

__all__ = ["resolve_dependencies"]


def resolve_dependencies(
    modules: list[tuple[str, list[DependencyInfo]]],
    known_ids: set[str] | None = None,
) -> list[str]:
    """Resolve module load order using Kahn's topological sort.

    Args:
        modules: List of (module_id, dependencies) tuples.
        known_ids: Set of all module IDs in the batch. If None, derived from modules list.

    Returns:
        List of module_ids in topological load order (dependencies first).

    Raises:
        CircularDependencyError: If circular dependencies are detected.
        ModuleLoadError: If a required dependency is not in known_ids.
    """
    if not modules:
        return []

    if known_ids is None:
        known_ids = {mod_id for mod_id, _ in modules}

    # Build graph and in-degree
    graph: dict[str, set[str]] = defaultdict(set)
    in_degree: dict[str, int] = {mod_id: 0 for mod_id, _ in modules}

    for module_id, deps in modules:
        for dep in deps:
            if dep.module_id not in known_ids:
                if dep.optional:
                    logger.warning(
                        "Optional dependency '%s' for module '%s' not found, skipping",
                        dep.module_id, module_id,
                    )
                    continue
                else:
                    raise ModuleLoadError(
                        module_id=module_id,
                        reason=f"Required dependency '{dep.module_id}' not found",
                    )
            graph[dep.module_id].add(module_id)
            in_degree[module_id] += 1

    # Initialize queue with zero-in-degree nodes (sorted for determinism)
    queue: deque[str] = deque(sorted(
        mod_id for mod_id in in_degree if in_degree[mod_id] == 0
    ))

    load_order: list[str] = []
    while queue:
        mod_id = queue.popleft()
        load_order.append(mod_id)
        for dependent in sorted(graph.get(mod_id, set())):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    # Check for cycles
    if len(load_order) < len(modules):
        remaining = {mod_id for mod_id, _ in modules if mod_id not in set(load_order)}
        cycle_path = _extract_cycle(modules, remaining)
        raise CircularDependencyError(cycle_path=cycle_path)

    return load_order


def _extract_cycle(
    modules: list[tuple[str, list[DependencyInfo]]],
    remaining: set[str],
) -> list[str]:
    """Extract a cycle path from the remaining unprocessed modules."""
    dep_map: dict[str, list[str]] = {}
    for mod_id, deps in modules:
        if mod_id in remaining:
            dep_map[mod_id] = [d.module_id for d in deps if d.module_id in remaining]

    # Follow edges from any remaining node until we revisit
    start = next(iter(remaining))
    visited: list[str] = [start]
    visited_set: set[str] = {start}
    current = start

    while True:
        nexts = dep_map.get(current, [])
        if not nexts:
            break
        nxt = nexts[0]
        if nxt in visited_set:
            # Found cycle: extract from first occurrence of nxt to end, then append nxt
            idx = visited.index(nxt)
            return visited[idx:] + [nxt]
        visited.append(nxt)
        visited_set.add(nxt)
        current = nxt

    # Fallback: return all remaining as the cycle path
    return list(remaining) + [next(iter(remaining))]
