"""$ref resolution for JSON Schema documents following Algorithm A05."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from apcore.errors import (
    SchemaCircularRefError,
    SchemaMaxDepthExceededError,
    SchemaNotFoundError,
    SchemaParseError,
)

__all__ = ["RefResolver"]

_INLINE_SENTINEL = Path("__inline__")


def _root_ref_aliases(document: dict[str, Any]) -> list[str]:
    """The ``$ref`` strings that denote *document* itself.

    Seeding the visited set with them makes a self-reference lazy from the very
    first encounter, so a recursive schema is never inlined even once
    (PROTOCOL_SPEC §4.15.2). The root ``$id`` is included because JSON Schema
    lets a document reference itself by identifier —
    ``{"$id": "TreeNode", ... "$ref": "TreeNode"}``.
    """
    aliases = ["#", "#/"]
    schema_id = document.get("$id")
    if isinstance(schema_id, str) and schema_id:
        aliases.append(schema_id)
    return aliases


class RefResolver:
    """Resolves $ref references in JSON Schema documents.

    Supports local (#/definitions/...), relative file, and canonical
    (apcore://...) reference formats. Detects circular references and
    caches parsed files for performance.
    """

    def __init__(self, schemas_dir: str | Path, max_depth: int = 32) -> None:
        self._schemas_dir: Path = Path(schemas_dir).resolve()
        self._max_depth: int = max_depth
        self._file_cache: dict[Path, dict[str, Any]] = {}

    def resolve(self, schema: dict[str, Any], current_file: Path | None = None) -> dict[str, Any]:
        """Resolve all $ref references in a schema dictionary.

        Returns a new dict with all $ref nodes replaced by their resolved content.
        The original schema is never modified.
        """
        result = copy.deepcopy(schema)
        # Cache the inline schema so local $ref (#/...) can resolve against it
        self._file_cache[_INLINE_SENTINEL] = result
        # Only meaningful for an inline document: with a `current_file`, `#` points
        # at that file rather than at the schema passed in.
        visited: set[str] = set() if current_file else set(_root_ref_aliases(result))
        try:
            self._resolve_node(result, current_file, visited_refs=visited, depth=0)
        finally:
            self._file_cache.pop(_INLINE_SENTINEL, None)
        return result

    def resolve_ref(
        self,
        ref_string: str,
        current_file: Path | None,
        visited_refs: set[str] | None = None,
        depth: int = 0,
        sibling_keys: dict[str, Any] | None = None,
        from_ref_chain: bool = False,
    ) -> Any:
        """Resolve a single $ref string to its target content.

        ``from_ref_chain`` marks the caller as another ``$ref`` that had this one
        as its immediate target — a ``$ref`` → ``$ref`` hop that never reaches a
        schema body. Re-entering a reference along such a chain is a genuine
        cycle (PROTOCOL_SPEC §4.15.2 "circular reference"): resolution cannot
        terminate and there is nothing to defer to, so ``SCHEMA_CIRCULAR_REF`` is
        raised. Re-entering a reference after descending through ``properties`` /
        ``items`` / a combinator is instead a *self-reference* — a recursive data
        structure — and the ``$ref`` is preserved verbatim as a lazy reference for
        the model generator to bind.
        """
        if visited_refs is None:
            visited_refs = set()

        if ref_string in visited_refs:
            if from_ref_chain:
                raise SchemaCircularRefError(ref_path=ref_string)
            return {"$ref": ref_string, **(sibling_keys or {})}

        if depth >= self._max_depth:
            raise SchemaMaxDepthExceededError(
                ref_path=f"Maximum reference depth {self._max_depth} exceeded resolving: {ref_string}"
            )

        visited_refs.add(ref_string)

        file_path, json_pointer = self._parse_ref(ref_string, current_file)
        document = self._load_file(file_path)
        target = self._resolve_json_pointer(document, json_pointer, ref_string)

        result = copy.deepcopy(target)

        if sibling_keys and isinstance(result, dict):
            result.update(sibling_keys)

        # Determine the effective file for nested resolution
        effective_file = current_file if file_path == _INLINE_SENTINEL else file_path

        if isinstance(result, dict) and "$ref" in result:
            nested_ref = result.pop("$ref")
            nested_siblings = {k: v for k, v in result.items()} if result else None
            result = self.resolve_ref(
                nested_ref,
                effective_file,
                visited_refs,
                depth + 1,
                nested_siblings if nested_siblings else None,
                from_ref_chain=True,
            )

        self._resolve_node(result, effective_file, visited_refs, depth + 1)
        return result

    def _resolve_node(self, node: Any, current_file: Path | None, visited_refs: set[str], depth: int) -> Any:
        """Recursively walk a node, resolving any $ref found. Modifies in-place."""
        if isinstance(node, dict):
            if "$ref" in node:
                ref_string = node["$ref"]
                sibling_keys = {k: v for k, v in node.items() if k != "$ref"}
                resolved = self.resolve_ref(
                    ref_string,
                    current_file,
                    visited_refs.copy(),
                    depth,
                    sibling_keys or None,
                )
                node.clear()
                if isinstance(resolved, dict):
                    node.update(resolved)
                else:
                    return resolved
            else:
                for key in list(node.keys()):
                    result = self._resolve_node(node[key], current_file, visited_refs, depth)
                    if result is not node[key]:
                        node[key] = result
        elif isinstance(node, list):
            for i, item in enumerate(node):
                result = self._resolve_node(item, current_file, visited_refs, depth)
                if result is not item:
                    node[i] = result
        return node

    def _parse_ref(self, ref_string: str, current_file: Path | None) -> tuple[Path, str]:
        """Parse a $ref string into (file_path, json_pointer)."""
        if ref_string.startswith("#"):
            pointer = ref_string[1:]
            if current_file:
                return current_file, pointer
            return _INLINE_SENTINEL, pointer

        if ref_string.startswith("apcore://"):
            return self._convert_canonical_to_path(ref_string)

        if "#" in ref_string:
            file_part, pointer = ref_string.split("#", 1)
            base = current_file.parent if current_file else self._schemas_dir
            resolved = (base / file_part).resolve()
            self._assert_within_schemas_dir(resolved, ref_string)
            return resolved, pointer

        base = current_file.parent if current_file else self._schemas_dir
        resolved = (base / ref_string).resolve()
        self._assert_within_schemas_dir(resolved, ref_string)
        return resolved, ""

    def _assert_within_schemas_dir(self, resolved_path: Path, ref_string: str) -> None:
        """Reject a $ref whose resolved path escapes the schemas directory.

        Mirrors apcore-typescript RefResolver._assertWithinSchemasDir: blocks
        path-traversal $refs (e.g. ``../../etc/passwd``) that would read files
        outside ``self._schemas_dir``. The schemas directory itself is allowed.
        """
        if resolved_path == self._schemas_dir:
            return
        if not resolved_path.is_relative_to(self._schemas_dir):
            raise SchemaNotFoundError(schema_id=f"Reference '{ref_string}' resolves outside schemas directory")

    def _convert_canonical_to_path(self, uri: str) -> tuple[Path, str]:
        """Convert an apcore:// canonical URI to (file_path, json_pointer)."""
        remainder = uri[len("apcore://") :]
        parts = remainder.split("/")
        canonical_id = parts[0]
        pointer_parts = parts[1:]

        file_rel = canonical_id.replace(".", "/") + ".schema.yaml"
        file_path = self._schemas_dir / file_rel

        pointer = "/" + "/".join(pointer_parts) if pointer_parts else ""
        return file_path.resolve(), pointer

    def _resolve_json_pointer(self, document: Any, pointer: str, ref_string: str) -> Any:
        """Navigate a document using an RFC 6901 JSON Pointer."""
        if not pointer:
            return document

        segments = pointer.split("/")
        if segments and segments[0] == "":
            segments = segments[1:]

        current = document
        for segment in segments:
            segment = segment.replace("~1", "/").replace("~0", "~")
            if isinstance(current, dict) and segment in current:
                current = current[segment]
            else:
                raise SchemaNotFoundError(schema_id=f"{ref_string} (segment '{segment}' not found)")
        return current

    def _load_file(self, file_path: Path) -> dict[str, Any]:
        """Load and parse a YAML or JSON file, using cache when available."""
        if file_path == _INLINE_SENTINEL:
            return self._file_cache.get(_INLINE_SENTINEL, {})

        file_path = file_path.resolve()
        if file_path in self._file_cache:
            return self._file_cache[file_path]

        if not file_path.exists():
            raise SchemaNotFoundError(schema_id=str(file_path))

        content = file_path.read_text()
        if not content.strip():
            self._file_cache[file_path] = {}
            return {}

        try:
            parsed = yaml.safe_load(content)
        except yaml.YAMLError as e:
            raise SchemaParseError(message=f"Invalid YAML in {file_path}: {e}") from e

        if parsed is None:
            self._file_cache[file_path] = {}
            return {}

        if not isinstance(parsed, dict):
            raise SchemaParseError(
                message=f"Schema file {file_path} must be a YAML mapping, got {type(parsed).__name__}"
            )

        self._file_cache[file_path] = parsed
        return parsed

    def clear_cache(self) -> None:
        """Clear the file cache."""
        self._file_cache.clear()
