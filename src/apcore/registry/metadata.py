"""Metadata and ID map loading for the registry system."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from apcore.errors import ConfigError, ConfigNotFoundError
from apcore.registry.types import DependencyInfo

logger = logging.getLogger(__name__)

__all__ = [
    "load_metadata",
    "parse_dependencies",
    "merge_module_metadata",
    "load_id_map",
]


def load_metadata(meta_path: Path) -> dict[str, Any]:
    """Load a *_meta.yaml companion metadata file.

    Returns empty dict if file does not exist (metadata is optional).
    """
    if not meta_path.exists():
        return {}

    content = meta_path.read_text(encoding="utf-8")
    try:
        parsed = yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise ConfigError(message=f"Invalid YAML in metadata file: {meta_path}") from e

    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise ConfigError(message=f"Metadata file must be a YAML mapping: {meta_path}")
    return parsed


def parse_dependencies(deps_raw: list[dict[str, Any]]) -> list[DependencyInfo]:
    """Convert raw dependency dicts from YAML to typed DependencyInfo objects."""
    if not deps_raw:
        return []

    result: list[DependencyInfo] = []
    for dep in deps_raw:
        module_id = dep.get("module_id")
        if not module_id:
            logger.warning("Dependency entry missing 'module_id', skipping: %s", dep)
            continue
        result.append(
            DependencyInfo(
                module_id=module_id,
                version=dep.get("version"),
                optional=dep.get("optional", False),
            )
        )
    return result


def merge_module_metadata(module_class: type, meta: dict[str, Any]) -> dict[str, Any]:
    """Merge YAML metadata over code-level attributes. YAML wins on conflicts."""
    code_desc = getattr(module_class, "description", "")
    code_name = getattr(module_class, "name", None)
    code_tags = getattr(module_class, "tags", [])
    code_version = getattr(module_class, "version", "1.0.0")
    code_annotations = getattr(module_class, "annotations", None)
    code_examples = getattr(module_class, "examples", [])
    code_metadata = getattr(module_class, "metadata", {})
    code_docs = getattr(module_class, "documentation", None)

    yaml_metadata = meta.get("metadata", {})
    merged_metadata = {**(code_metadata or {}), **(yaml_metadata or {})}

    return {
        "description": meta.get("description") or code_desc,
        "name": meta.get("name") or code_name,
        "tags": meta.get("tags") if meta.get("tags") is not None else (code_tags or []),
        "version": meta.get("version") or code_version,
        "annotations": (
            meta.get("annotations")
            if meta.get("annotations") is not None
            else code_annotations
        ),
        "examples": (
            meta.get("examples")
            if meta.get("examples") is not None
            else (code_examples or [])
        ),
        "metadata": merged_metadata,
        "documentation": meta.get("documentation") or code_docs,
    }


def load_id_map(id_map_path: Path) -> dict[str, dict[str, Any]]:
    """Load an ID Map YAML file for canonical ID overrides.

    Raises ConfigNotFoundError if file does not exist (ID map is explicitly requested).
    """
    if not id_map_path.exists():
        raise ConfigNotFoundError(config_path=str(id_map_path))

    content = id_map_path.read_text(encoding="utf-8")
    try:
        parsed = yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise ConfigError(message=f"Invalid YAML in ID map file: {id_map_path}") from e

    if not isinstance(parsed, dict) or "mappings" not in parsed:
        raise ConfigError(message="ID map must contain a 'mappings' list")

    mappings = parsed["mappings"]
    if not isinstance(mappings, list):
        raise ConfigError(message="ID map must contain a 'mappings' list")

    result: dict[str, dict[str, Any]] = {}
    for entry in mappings:
        file_path = entry.get("file")
        if not file_path:
            logger.warning("ID map entry missing 'file' field, skipping")
            continue
        result[file_path] = {
            "id": entry.get("id", file_path),
            "class": entry.get("class"),
        }
    return result
