"""Strict mode conversion for JSON Schemas (Algorithm A23)."""

from __future__ import annotations

import copy
from typing import Any


def to_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert a JSON Schema to OpenAI/Anthropic Strict Mode format.

    Deep-copies the input, strips extensions and defaults, then enforces
    strict mode rules (additionalProperties: false, all properties required,
    optional fields become nullable).
    """
    result = copy.deepcopy(schema)
    _strip_extensions(result)
    _convert_to_strict(result)
    return result


def _apply_llm_descriptions(node: Any) -> None:
    """Replace description with x-llm-description where present.

    Mutates the node in place. Called by exporters before to_strict_schema().
    """
    if not isinstance(node, dict):
        return

    if "x-llm-description" in node and "description" in node:
        node["description"] = node["x-llm-description"]

    # Recurse into nested structures
    if "properties" in node and isinstance(node["properties"], dict):
        for prop in node["properties"].values():
            _apply_llm_descriptions(prop)
    if "items" in node and isinstance(node["items"], dict):
        _apply_llm_descriptions(node["items"])
    for keyword in ("oneOf", "anyOf", "allOf"):
        if keyword in node and isinstance(node[keyword], list):
            for sub in node[keyword]:
                _apply_llm_descriptions(sub)
    for defs_key in ("definitions", "$defs"):
        if defs_key in node and isinstance(node[defs_key], dict):
            for defn in node[defs_key].values():
                _apply_llm_descriptions(defn)


def _strip_extensions(node: Any) -> None:
    """Remove all x-* keys and default keys recursively. Mutates in place."""
    if not isinstance(node, dict):
        return

    keys_to_remove = [
        k for k in node if (isinstance(k, str) and k.startswith("x-")) or k == "default"
    ]
    for k in keys_to_remove:
        del node[k]

    for value in node.values():
        if isinstance(value, dict):
            _strip_extensions(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _strip_extensions(item)


def _convert_to_strict(node: Any) -> None:
    """Enforce strict mode rules on an object schema. Mutates in place."""
    if not isinstance(node, dict):
        return

    if node.get("type") == "object" and "properties" in node:
        node["additionalProperties"] = False
        existing_required = set(node.get("required", []))
        all_names = list(node["properties"].keys())
        optional_names = [n for n in all_names if n not in existing_required]

        for name in optional_names:
            prop = node["properties"][name]
            if "type" in prop:
                if isinstance(prop["type"], str):
                    prop["type"] = [prop["type"], "null"]
                elif isinstance(prop["type"], list):
                    if "null" not in prop["type"]:
                        prop["type"].append("null")
            else:
                # Pure $ref or composition — wrap in oneOf with null
                node["properties"][name] = {"oneOf": [prop, {"type": "null"}]}

        node["required"] = sorted(all_names)

    # Recurse into nested structures
    if "properties" in node and isinstance(node["properties"], dict):
        for prop in node["properties"].values():
            _convert_to_strict(prop)
    if "items" in node and isinstance(node["items"], dict):
        _convert_to_strict(node["items"])
    for keyword in ("oneOf", "anyOf", "allOf"):
        if keyword in node and isinstance(node[keyword], list):
            for sub in node[keyword]:
                _convert_to_strict(sub)
    for defs_key in ("definitions", "$defs"):
        if defs_key in node and isinstance(node[defs_key], dict):
            for defn in node[defs_key].values():
                _convert_to_strict(defn)
