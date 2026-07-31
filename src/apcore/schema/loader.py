"""SchemaLoader — primary entry point for the schema system."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal, Union

import yaml
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, create_model
from pydantic.functional_validators import AfterValidator
from pydantic_core import PydanticUndefined

from apcore.config import Config
from apcore.errors import SchemaNotFoundError, SchemaParseError
from apcore.schema.hardening import content_hash
from apcore.schema.ref_resolver import RefResolver
from apcore.schema.types import ResolvedSchema, SchemaDefinition, SchemaStrategy

__all__ = ["SchemaLoader"]

logger = logging.getLogger(__name__)

_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "null": type(None),
}

# Assertions that are independent of `type` and must hold alongside it
# (JSON Schema 2020-12 §10.2). When one of these sits next to a `type` keyword,
# both have to be enforced — neither may be discarded in favour of the other.
_COMBINATOR_KEYWORDS = ("const", "enum", "oneOf", "anyOf", "allOf", "not")

# Option keywords that constrain a single instance type (§6). When `type` is an
# array each branch keeps only its own, so a numeric bound cannot land on a string.
_STRING_CONSTRAINTS = {"minLength": "min_length", "maxLength": "max_length", "pattern": "pattern"}
_NUMERIC_CONSTRAINTS = {
    "minimum": "ge",
    "maximum": "le",
    "exclusiveMinimum": "gt",
    "exclusiveMaximum": "lt",
    "multipleOf": "multiple_of",
}


def _check_unique(v: list[Any]) -> list[Any]:
    if len(v) != len(set(v)):
        raise ValueError("Items must be unique")
    return v


def _to_jsonable(value: Any) -> Any:
    """Convert a validated Pydantic value back to plain JSON data for a jsonschema check."""
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    return value


def _make_combinator_assertion(sub_schema: dict[str, Any]) -> Callable[[Any], Any]:
    """Build an AfterValidator asserting *sub_schema* on an already-typed value.

    Pydantic has no way to intersect a combinator keyword onto a type-derived
    annotation, so the sibling assertion is delegated to the jsonschema library —
    the same engine `hardening.validate_schema_dict` uses, which keeps the two
    validation paths in agreement.
    """
    validator = Draft202012Validator(sub_schema)

    def assert_combinator(value: Any) -> Any:
        errors = list(validator.iter_errors(_to_jsonable(value)))
        if errors:
            raise ValueError(errors[0].message)
        return value

    return assert_combinator


def _branch_constraints(prop_schema: dict[str, Any], type_name: str) -> dict[str, Any]:
    """Collect the option keywords that apply to *type_name* only."""
    if type_name == "string":
        mapping = _STRING_CONSTRAINTS
    elif type_name in ("integer", "number"):
        mapping = _NUMERIC_CONSTRAINTS
    else:
        return {}
    return {arg: prop_schema[keyword] for keyword, arg in mapping.items() if keyword in prop_schema}


class SchemaLoader:
    """Primary entry point for loading, resolving, and generating schemas."""

    def __init__(self, config: Config, schemas_dir: str | Path | None = None) -> None:
        self._config = config
        if schemas_dir is not None:
            self._schemas_dir = Path(schemas_dir).resolve()
        else:
            self._schemas_dir = Path(config.get("schema.root", Config.get_default("schema.root"))).resolve()
        max_depth = config.get("schema.max_ref_depth", 32)
        self._resolver = RefResolver(self._schemas_dir, max_depth=max_depth)
        self._schema_cache: dict[str, SchemaDefinition] = {}
        # Two-level content-addressable cache (PROTOCOL_SPEC §4.15 Issue #44):
        # Level 1: path index — maps module_id to SHA-256 hash of resolved schema
        # Level 2: content cache — maps hash to compiled model pair; deduplicates identical schemas
        self._path_index: dict[str, str] = {}
        self._content_cache: dict[str, tuple[ResolvedSchema, ResolvedSchema]] = {}

    def load(self, module_id: str) -> SchemaDefinition:
        """Load a schema definition from a YAML file."""
        if module_id in self._schema_cache:
            return self._schema_cache[module_id]

        file_path = self._schemas_dir / (module_id.replace(".", "/") + ".schema.yaml")
        if not file_path.exists():
            raise SchemaNotFoundError(schema_id=module_id)

        try:
            data = yaml.safe_load(file_path.read_text())
        except yaml.YAMLError as e:
            raise SchemaParseError(message=f"Invalid YAML in schema for '{module_id}': {e}") from e

        if data is None or not isinstance(data, dict):
            raise SchemaParseError(message=f"Schema file for '{module_id}' is empty or not a mapping")

        for field_name in ("input_schema", "output_schema", "description"):
            if field_name not in data:
                raise SchemaParseError(message=f"Missing required field: {field_name} in schema for '{module_id}'")

        definitions = dict(data.get("definitions", {}))
        definitions.update(data.get("$defs", {}))

        description = data["description"]
        if len(description) > 200:
            logger.warning(f"Schema description for '{module_id}' exceeds 200 characters")

        sd = SchemaDefinition(
            module_id=data.get("module_id", module_id),
            description=description,
            input_schema=data["input_schema"],
            output_schema=data["output_schema"],
            error_schema=data.get("error_schema"),
            definitions=definitions,
            version=data.get("version", "1.0.0"),
            documentation=data.get("documentation"),
            schema_url=data.get("$schema"),
        )
        self._schema_cache[module_id] = sd
        return sd

    def resolve(self, schema_def: SchemaDefinition) -> tuple[ResolvedSchema, ResolvedSchema]:
        """Resolve all $ref references in a SchemaDefinition."""
        # Pass current_file=None so local #/ refs resolve within the schema dict itself,
        # not against the whole YAML file. Cross-file refs use schemas_dir as base.
        resolved_input = self._resolver.resolve(schema_def.input_schema)
        resolved_output = self._resolver.resolve(schema_def.output_schema)

        input_model = self.generate_model(resolved_input, f"{schema_def.module_id}_Input")
        output_model = self.generate_model(resolved_output, f"{schema_def.module_id}_Output")

        input_rs = ResolvedSchema(
            json_schema=resolved_input,
            model=input_model,
            module_id=schema_def.module_id,
            direction="input",
        )
        output_rs = ResolvedSchema(
            json_schema=resolved_output,
            model=output_model,
            module_id=schema_def.module_id,
            direction="output",
        )
        return input_rs, output_rs

    def generate_model(self, json_schema: dict[str, Any], model_name: str) -> type[BaseModel]:
        """Dynamically generate a Pydantic BaseModel from a JSON Schema dict."""
        properties = json_schema.get("properties", {})
        required = set(json_schema.get("required", []))

        # Respect additionalProperties: false → Pydantic extra="forbid".
        # An object form (`{"type": "integer"}`) keeps undeclared keys but constrains
        # their values, which Pydantic expresses as extra="allow" plus a typed
        # __pydantic_extra__ annotation.
        additional = json_schema.get("additionalProperties", True)
        config = ConfigDict(extra="forbid") if additional is False else ConfigDict()

        field_definitions: dict[str, Any] = {}
        if isinstance(additional, dict) and additional:
            config = ConfigDict(extra="allow")
            extra_type = self._schema_to_type(additional, "additionalProperties", model_name)
            field_definitions["__pydantic_extra__"] = (dict[str, extra_type], ...)  # type: ignore[valid-type]
        for prop_name, prop_schema in properties.items():
            python_type, field_info = self._schema_to_field_info(prop_schema, prop_name, model_name)
            is_required = prop_name in required

            if not is_required:
                # Make type nullable if not already
                python_type = python_type | None  # type: ignore[operator]
                # Set default to None if no explicit default
                if field_info.default is PydanticUndefined:
                    is_arr = isinstance(prop_schema.get("type"), str) and prop_schema.get("type") == "array"
                    field_info = self._clone_field_with_default(prop_schema, None, is_array=is_arr)

            field_definitions[prop_name] = (python_type, field_info)

        model = create_model(model_name, __config__=config, **field_definitions)  # type: ignore[call-overload]
        # A-D-08: retain the source JSON Schema on the generated model so
        # SchemaValidator.validate() can detect a top-level oneOf/anyOf and route
        # it through the jsonschema-backed exhaustive union check (parity with the
        # TS/Rust validators, which emit SCHEMA_UNION_NO_MATCH / SCHEMA_UNION_AMBIGUOUS).
        # A top-level union produces a property-less Pydantic model that would
        # otherwise accept any value via the always-true empty-schema path.
        model.__apcore_source_schema__ = json_schema  # type: ignore[attr-defined]
        return model

    def _schema_to_field_info(self, prop_schema: dict[str, Any], prop_name: str, parent_name: str) -> tuple[Any, Any]:
        """Convert a JSON Schema property to (python_type, FieldInfo).

        `type` and its combinator siblings (`const`, `enum`, `oneOf`, `anyOf`,
        `allOf`, `not`) are independent assertions that must all hold
        (JSON Schema 2020-12 §10.2). The annotation comes from whichever keyword
        expresses it natively; every sibling that annotation does not already
        cover is intersected onto it as a jsonschema-backed check, rather than
        the first matching keyword winning and the rest being discarded.
        """
        if not prop_schema:
            return dict[str, Any], Field(default=...)

        if "if" in prop_schema:
            raise SchemaParseError(message="if/then/else not yet supported")

        python_type, consumed, is_array = self._base_annotation(prop_schema, prop_name, parent_name)

        siblings = {key: prop_schema[key] for key in _COMBINATOR_KEYWORDS if key in prop_schema and key not in consumed}
        if siblings:
            python_type = Annotated[python_type, AfterValidator(_make_combinator_assertion(siblings))]

        return python_type, self._build_field(prop_schema, is_array=is_array)

    def _base_annotation(
        self, prop_schema: dict[str, Any], prop_name: str, parent_name: str
    ) -> tuple[Any, tuple[str, ...], bool]:
        """Derive the annotation from the keyword that can express it natively.

        Returns (annotation, keywords_consumed, is_array). A keyword is consumed
        only when the annotation fully enforces it; anything left over is applied
        by the caller as a sibling assertion.
        """
        schema_type = prop_schema.get("type")

        if isinstance(schema_type, list):
            return self._union_from_types(prop_schema, schema_type, prop_name, parent_name), (), False

        if schema_type == "object":
            return self._handle_object(prop_schema, prop_name, parent_name), (), False

        if schema_type == "array":
            base_type, _ = self._handle_array(prop_schema, prop_name, parent_name)
            return base_type, (), True

        if isinstance(schema_type, str):
            return _TYPE_MAP.get(schema_type, Any), (), False

        # No `type`: let a combinator carry the annotation itself.
        if "const" in prop_schema:
            return Literal[prop_schema["const"]], ("const",), False

        if "enum" in prop_schema:
            return Literal[tuple(prop_schema["enum"])], ("enum",), False

        for keyword in ("oneOf", "anyOf"):
            if keyword in prop_schema:
                types = [
                    self._schema_to_type(sub, f"{prop_name}_{keyword}_{i}", parent_name)
                    for i, sub in enumerate(prop_schema[keyword])
                ]
                # The branch annotations are shape-only (`_schema_to_type` widens a
                # branch without `type` to Any), so the keyword stays unconsumed and
                # the exhaustive check is applied as a sibling assertion.
                return Union[tuple(types)], (), False

        if "allOf" in prop_schema:
            return self._handle_all_of(prop_schema["allOf"], prop_name, parent_name), ("allOf",), False

        if "not" in prop_schema:
            return Any, (), False

        return dict[str, Any], (), False

    def _union_from_types(
        self, prop_schema: dict[str, Any], schema_type: list[str], prop_name: str, parent_name: str
    ) -> Any:
        """Convert a `type` array to a union, keeping each type's option keywords on its own branch."""
        branches: list[Any] = []
        for type_name in schema_type:
            if type_name == "object":
                branches.append(self._handle_object(prop_schema, prop_name, parent_name))
            elif type_name == "array":
                base_type, _ = self._handle_array(prop_schema, prop_name, parent_name)
                branches.append(base_type)
            else:
                base_type = _TYPE_MAP.get(type_name, Any)
                constraints = _branch_constraints(prop_schema, type_name)
                branches.append(Annotated[base_type, Field(**constraints)] if constraints else base_type)

        if not branches:
            return type(None)
        return Union[tuple(branches)]

    def _schema_to_type(self, schema: dict[str, Any], name: str, parent_name: str) -> Any:
        """Convert a sub-schema to a Python type (for Union branches)."""
        schema_type = schema.get("type")
        if schema_type == "object" and "properties" in schema:
            return self.generate_model(schema, f"{parent_name}_{name}")
        if schema_type and isinstance(schema_type, str):
            return _TYPE_MAP.get(schema_type, Any)
        return Any

    def _handle_object(self, prop_schema: dict[str, Any], prop_name: str, parent_name: str) -> Any:
        """Handle object type schemas."""
        if "properties" in prop_schema:
            return self.generate_model(prop_schema, f"{parent_name}_{prop_name}")
        if "additionalProperties" in prop_schema:
            additional = prop_schema["additionalProperties"]
            if isinstance(additional, dict) and "type" in additional:
                value_type = _TYPE_MAP.get(additional["type"], Any)
                return dict[str, value_type]  # type: ignore[valid-type]
            return dict[str, Any]
        return dict[str, Any]

    def _handle_array(self, prop_schema: dict[str, Any], prop_name: str, parent_name: str) -> tuple[Any, Any]:
        """Handle array type schemas."""
        items = prop_schema.get("items")
        if items:
            item_type = self._schema_to_type(items, f"{prop_name}_item", parent_name)
            base_type = list[item_type]  # type: ignore[valid-type]
        else:
            base_type = list[Any]

        if prop_schema.get("uniqueItems"):
            base_type = Annotated[base_type, AfterValidator(_check_unique)]  # type: ignore[valid-type]

        return base_type, self._build_field(prop_schema, is_array=True)

    def _handle_all_of(self, sub_schemas: list[dict[str, Any]], prop_name: str, parent_name: str) -> Any:
        """Merge allOf sub-schemas into a single model."""
        merged_properties: dict[str, Any] = {}
        merged_required: list[str] = []

        for sub in sub_schemas:
            if sub.get("type") != "object" and "properties" not in sub:
                raise SchemaParseError(message=f"allOf with non-object sub-schema not supported in '{prop_name}'")
            for name, prop in sub.get("properties", {}).items():
                if name in merged_properties:
                    existing_type = merged_properties[name].get("type")
                    new_type = prop.get("type")
                    if existing_type and new_type and existing_type != new_type:
                        raise SchemaParseError(
                            message=f"allOf conflict: property '{name}' has conflicting types in '{prop_name}'"
                        )
                merged_properties[name] = prop
            merged_required.extend(sub.get("required", []))

        merged_schema = {
            "type": "object",
            "properties": merged_properties,
            "required": list(set(merged_required)),
        }
        return self.generate_model(merged_schema, f"{parent_name}_{prop_name}")

    def _build_field(self, prop_schema: dict[str, Any], is_array: bool = False) -> Any:
        """Build a Pydantic Field from JSON Schema constraints."""
        kwargs: dict[str, Any] = {"default": ...}

        if "default" in prop_schema:
            kwargs["default"] = prop_schema["default"]

        if "description" in prop_schema:
            kwargs["description"] = prop_schema["description"]
        if "title" in prop_schema:
            kwargs["title"] = prop_schema["title"]

        # A `type` array carries its option keywords per union branch (see
        # `_union_from_types`); repeating them field-wide would apply a numeric
        # bound to the string branch and vice versa.
        per_branch = isinstance(prop_schema.get("type"), list)

        # Numeric constraints
        if not per_branch:
            if "minimum" in prop_schema:
                kwargs["ge"] = prop_schema["minimum"]
            if "maximum" in prop_schema:
                kwargs["le"] = prop_schema["maximum"]
            if "exclusiveMinimum" in prop_schema:
                kwargs["gt"] = prop_schema["exclusiveMinimum"]
            if "exclusiveMaximum" in prop_schema:
                kwargs["lt"] = prop_schema["exclusiveMaximum"]
            if "multipleOf" in prop_schema:
                kwargs["multiple_of"] = prop_schema["multipleOf"]

        # String constraints
        if is_array:
            if "minItems" in prop_schema:
                kwargs["min_length"] = prop_schema["minItems"]
            if "maxItems" in prop_schema:
                kwargs["max_length"] = prop_schema["maxItems"]
        elif not per_branch:
            if "minLength" in prop_schema:
                kwargs["min_length"] = prop_schema["minLength"]
            if "maxLength" in prop_schema:
                kwargs["max_length"] = prop_schema["maxLength"]

        if "pattern" in prop_schema and not per_branch:
            kwargs["pattern"] = prop_schema["pattern"]

        # LLM extensions and format as json_schema_extra
        extra: dict[str, Any] = {}
        for key, value in prop_schema.items():
            if key.startswith("x-"):
                extra[key] = value
        if "format" in prop_schema:
            extra["format"] = prop_schema["format"]
        if extra:
            kwargs["json_schema_extra"] = extra

        return Field(**kwargs)

    def _clone_field_with_default(self, prop_schema: dict[str, Any], default: Any, is_array: bool = False) -> Any:
        """Build a new Field with the given default, preserving all constraints from schema."""
        schema_with_default = dict(prop_schema)
        schema_with_default["default"] = default
        return self._build_field(schema_with_default, is_array=is_array)

    def get_schema(
        self,
        module_id: str,
        native_input_schema: type[BaseModel] | None = None,
        native_output_schema: type[BaseModel] | None = None,
    ) -> tuple[ResolvedSchema, ResolvedSchema]:
        """Get resolved schemas using the configured loading strategy."""
        if module_id in self._path_index:
            return self._content_cache[self._path_index[module_id]]

        strategy = SchemaStrategy(self._config.get("schema.strategy", "yaml_first"))
        result: tuple[ResolvedSchema, ResolvedSchema] | None = None

        if strategy == SchemaStrategy.YAML_FIRST:
            try:
                result = self._load_and_resolve(module_id)
            except SchemaNotFoundError:
                if native_input_schema and native_output_schema:
                    result = self._wrap_native(module_id, native_input_schema, native_output_schema)
                else:
                    raise

        elif strategy == SchemaStrategy.NATIVE_FIRST:
            if native_input_schema and native_output_schema:
                result = self._wrap_native(module_id, native_input_schema, native_output_schema)
            else:
                result = self._load_and_resolve(module_id)

        elif strategy == SchemaStrategy.YAML_ONLY:
            result = self._load_and_resolve(module_id)

        if result is None:
            raise SchemaNotFoundError(schema_id=module_id)

        self._store_in_cache(module_id, result)
        return result

    def _load_and_resolve(self, module_id: str) -> tuple[ResolvedSchema, ResolvedSchema]:
        """Load and resolve a schema, using the two-level content-addressable cache."""
        if module_id in self._path_index:
            return self._content_cache[self._path_index[module_id]]
        sd = self.load(module_id)
        result = self.resolve(sd)
        self._store_in_cache(module_id, result)
        return result

    def _store_in_cache(self, module_id: str, result: tuple[ResolvedSchema, ResolvedSchema]) -> None:
        """Store a resolved schema pair in the two-level content-addressable cache."""
        combined = {"input": result[0].json_schema, "output": result[1].json_schema}
        digest = content_hash(combined)
        if digest not in self._content_cache:
            self._content_cache[digest] = result
        self._path_index[module_id] = digest

    def _wrap_native(
        self,
        module_id: str,
        input_model: type[BaseModel],
        output_model: type[BaseModel],
    ) -> tuple[ResolvedSchema, ResolvedSchema]:
        """Wrap native Pydantic models as ResolvedSchema without re-generating."""
        input_rs = ResolvedSchema(
            json_schema=input_model.model_json_schema(),
            model=input_model,
            module_id=module_id,
            direction="input",
        )
        output_rs = ResolvedSchema(
            json_schema=output_model.model_json_schema(),
            model=output_model,
            module_id=module_id,
            direction="output",
        )
        return input_rs, output_rs

    def clear_cache(self) -> None:
        """Clear all internal caches."""
        self._schema_cache.clear()
        self._path_index.clear()
        self._content_cache.clear()
        self._resolver.clear_cache()
