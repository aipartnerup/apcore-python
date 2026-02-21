"""SchemaLoader — primary entry point for the schema system."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Any, Literal, Union

import yaml
from pydantic import BaseModel, Field, create_model
from pydantic.fields import PydanticUndefined
from pydantic.functional_validators import AfterValidator

from apcore.config import Config
from apcore.errors import SchemaNotFoundError, SchemaParseError
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


def _check_unique(v: list[Any]) -> list[Any]:
    if len(v) != len(set(v)):
        raise ValueError("Items must be unique")
    return v


class SchemaLoader:
    """Primary entry point for loading, resolving, and generating schemas."""

    def __init__(self, config: Config, schemas_dir: str | Path | None = None) -> None:
        self._config = config
        if schemas_dir is not None:
            self._schemas_dir = Path(schemas_dir).resolve()
        else:
            self._schemas_dir = Path(config.get("schema.root", "./schemas")).resolve()
        max_depth = config.get("schema.max_ref_depth", 32)
        self._resolver = RefResolver(self._schemas_dir, max_depth=max_depth)
        self._schema_cache: dict[str, SchemaDefinition] = {}
        self._model_cache: dict[str, tuple[ResolvedSchema, ResolvedSchema]] = {}

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

        field_definitions: dict[str, Any] = {}
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

        return create_model(model_name, **field_definitions)  # type: ignore[call-overload]

    def _schema_to_field_info(self, prop_schema: dict[str, Any], prop_name: str, parent_name: str) -> tuple[Any, Any]:
        """Convert a JSON Schema property to (python_type, FieldInfo)."""
        if not prop_schema:
            return dict[str, Any], Field(default=...)

        # Unsupported keywords
        if "not" in prop_schema:
            raise SchemaParseError(message="'not' keyword not yet supported")
        if "if" in prop_schema:
            raise SchemaParseError(message="if/then/else not yet supported")

        # const
        if "const" in prop_schema:
            val = prop_schema["const"]
            return Literal[val], Field(default=...)  # type: ignore[valid-type]

        # enum
        if "enum" in prop_schema:
            values = tuple(prop_schema["enum"])
            return Literal[values], Field(default=...)  # type: ignore[valid-type]

        # Composition
        if "oneOf" in prop_schema:
            types = [
                self._schema_to_type(s, f"{prop_name}_oneOf_{i}", parent_name)
                for i, s in enumerate(prop_schema["oneOf"])
            ]
            return Union[tuple(types)], Field(default=...)  # type: ignore[valid-type]

        if "anyOf" in prop_schema:
            types = [
                self._schema_to_type(s, f"{prop_name}_anyOf_{i}", parent_name)
                for i, s in enumerate(prop_schema["anyOf"])
            ]
            return Union[tuple(types)], Field(default=...)  # type: ignore[valid-type]

        if "allOf" in prop_schema:
            return self._handle_all_of(prop_schema["allOf"], prop_name, parent_name), Field(default=...)

        # Type-based dispatch
        schema_type = prop_schema.get("type")

        if schema_type is None:
            return dict[str, Any], Field(default=...)

        # Nullable type: ["string", "null"]
        if isinstance(schema_type, list):
            non_null = [t for t in schema_type if t != "null"]
            has_null = "null" in schema_type
            if non_null:
                base_type = _TYPE_MAP.get(non_null[0], Any)
            else:
                base_type = type(None)
            if has_null:
                base_type = base_type | None  # type: ignore[operator]
            return base_type, self._build_field(prop_schema)

        if schema_type == "object":
            return self._handle_object(prop_schema, prop_name, parent_name), self._build_field(prop_schema)

        if schema_type == "array":
            return self._handle_array(prop_schema, prop_name, parent_name)

        # Primitive types
        python_type = _TYPE_MAP.get(schema_type, Any)
        return python_type, self._build_field(prop_schema)

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

        # Numeric constraints
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
        if not is_array:
            if "minLength" in prop_schema:
                kwargs["min_length"] = prop_schema["minLength"]
            if "maxLength" in prop_schema:
                kwargs["max_length"] = prop_schema["maxLength"]
        else:
            if "minItems" in prop_schema:
                kwargs["min_length"] = prop_schema["minItems"]
            if "maxItems" in prop_schema:
                kwargs["max_length"] = prop_schema["maxItems"]

        if "pattern" in prop_schema:
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
        if module_id in self._model_cache:
            return self._model_cache[module_id]

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

        self._model_cache[module_id] = result
        return result

    def _load_and_resolve(self, module_id: str) -> tuple[ResolvedSchema, ResolvedSchema]:
        """Load and resolve a schema, using model cache."""
        if module_id in self._model_cache:
            return self._model_cache[module_id]
        sd = self.load(module_id)
        result = self.resolve(sd)
        self._model_cache[module_id] = result
        return result

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
        self._model_cache.clear()
        self._resolver.clear_cache()
