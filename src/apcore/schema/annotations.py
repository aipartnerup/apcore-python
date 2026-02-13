"""Annotation conflict resolution — merge YAML and code metadata."""

from __future__ import annotations

from typing import Any

from apcore.module import ModuleAnnotations, ModuleExample

__all__ = ["merge_annotations", "merge_examples", "merge_metadata"]

_ANNOTATION_FIELDS = frozenset(ModuleAnnotations.__dataclass_fields__.keys())


def merge_annotations(
    yaml_annotations: dict[str, Any] | None,
    code_annotations: ModuleAnnotations | None,
) -> ModuleAnnotations:
    """Merge YAML and code annotations with priority: YAML > code > defaults."""
    defaults = ModuleAnnotations()
    values: dict[str, Any] = {f: getattr(defaults, f) for f in _ANNOTATION_FIELDS}

    if code_annotations is not None:
        for f in _ANNOTATION_FIELDS:
            values[f] = getattr(code_annotations, f)

    if yaml_annotations is not None:
        for key, val in yaml_annotations.items():
            if key in _ANNOTATION_FIELDS:
                values[key] = val

    return ModuleAnnotations(**values)


def merge_examples(
    yaml_examples: list[dict[str, Any]] | None,
    code_examples: list[ModuleExample] | None,
) -> list[ModuleExample]:
    """Merge YAML and code examples. YAML takes full priority when present."""
    if yaml_examples is not None:
        return [
            ModuleExample(
                title=d["title"],
                inputs=d.get("inputs", {}),
                output=d.get("output", {}),
                description=d.get("description"),
            )
            for d in yaml_examples
        ]
    if code_examples is not None:
        return code_examples
    return []


def merge_metadata(
    yaml_metadata: dict[str, Any] | None,
    code_metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge YAML and code metadata dicts. YAML keys override code keys."""
    result = dict(code_metadata) if code_metadata is not None else {}
    if yaml_metadata is not None:
        result.update(yaml_metadata)
    return result
