"""Wildcard pattern matching for module IDs."""

from __future__ import annotations

__all__ = ["match_pattern"]


def match_pattern(pattern: str, module_id: str) -> bool:
    """Match a module ID against a wildcard pattern (Algorithm A08).

    Supports '*' as a wildcard that matches any sequence of characters
    including dots.

    Args:
        pattern: The pattern to match against. May contain '*' wildcards.
        module_id: The module Canonical ID to test.

    Returns:
        True if the module_id matches the pattern, False otherwise.
    """
    if pattern == "*":
        return True
    if "*" not in pattern:
        return pattern == module_id

    segments = pattern.split("*")
    pos = 0

    if not pattern.startswith("*"):
        if not module_id.startswith(segments[0]):
            return False
        pos = len(segments[0])

    for segment in segments[1:]:
        if not segment:
            continue
        idx = module_id.find(segment, pos)
        if idx == -1:
            return False
        pos = idx + len(segment)

    if not pattern.endswith("*"):
        if not module_id.endswith(segments[-1]):
            return False

    return True
