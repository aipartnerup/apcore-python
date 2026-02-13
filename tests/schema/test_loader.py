"""Tests for SchemaLoader."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import BaseModel, field_validator

from apcore.config import Config
from apcore.errors import SchemaNotFoundError, SchemaParseError
from apcore.schema.loader import SchemaLoader
from apcore.schema.types import ResolvedSchema


def write_yaml(path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, default_flow_style=False))
    return path


def make_loader(schemas_dir: Path) -> SchemaLoader:
    config = Config({"schema": {"root": str(schemas_dir), "strategy": "yaml_first", "max_ref_depth": 32}})
    return SchemaLoader(config, schemas_dir=schemas_dir)


def write_simple_schema(schemas_dir: Path, name: str = "simple") -> Path:
    return write_yaml(
        schemas_dir / f"{name}.schema.yaml",
        {
            "module_id": name,
            "description": f"A {name} schema",
            "input_schema": {"type": "object", "properties": {"table": {"type": "string"}}, "required": ["table"]},
            "output_schema": {"type": "object", "properties": {"rows": {"type": "array", "items": {"type": "string"}}}},
        },
    )


# === load() ===


class TestLoad:
    def test_load_valid_schema(self, tmp_path: Path) -> None:
        write_simple_schema(tmp_path)
        loader = make_loader(tmp_path)
        sd = loader.load("simple")
        assert sd.module_id == "simple"
        assert sd.description == "A simple schema"
        assert "table" in sd.input_schema["properties"]

    def test_module_id_dots_to_path(self, tmp_path: Path) -> None:
        write_yaml(
            tmp_path / "db" / "query.schema.yaml",
            {
                "module_id": "db.query",
                "description": "Query",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
            },
        )
        loader = make_loader(tmp_path)
        sd = loader.load("db.query")
        assert sd.module_id == "db.query"

    def test_missing_schema_raises(self, tmp_path: Path) -> None:
        loader = make_loader(tmp_path)
        with pytest.raises(SchemaNotFoundError):
            loader.load("nonexistent.module")

    def test_invalid_yaml_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.schema.yaml"
        bad.write_text("{{invalid: yaml: ---")
        loader = make_loader(tmp_path)
        with pytest.raises(SchemaParseError):
            loader.load("bad")

    def test_missing_required_field_raises(self, tmp_path: Path) -> None:
        write_yaml(tmp_path / "incomplete.schema.yaml", {"module_id": "incomplete", "description": "No input"})
        loader = make_loader(tmp_path)
        with pytest.raises(SchemaParseError, match="input_schema"):
            loader.load("incomplete")

    def test_empty_file_raises(self, tmp_path: Path) -> None:
        (tmp_path / "empty.schema.yaml").write_text("")
        loader = make_loader(tmp_path)
        with pytest.raises(SchemaParseError):
            loader.load("empty")

    def test_defs_and_definitions_merge(self, tmp_path: Path) -> None:
        write_yaml(
            tmp_path / "merged.schema.yaml",
            {
                "module_id": "merged",
                "description": "Merged defs",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "definitions": {
                    "Foo": {"type": "object", "properties": {"x": {"type": "string"}}},
                    "Bar": {"type": "integer"},
                },
                "$defs": {"Foo": {"type": "object", "properties": {"z": {"type": "boolean"}}}},
            },
        )
        loader = make_loader(tmp_path)
        sd = loader.load("merged")
        assert "Bar" in sd.definitions
        assert sd.definitions["Foo"]["properties"]["z"]["type"] == "boolean"

    def test_long_description_logs_warning(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        write_yaml(
            tmp_path / "long.schema.yaml",
            {
                "module_id": "long",
                "description": "x" * 201,
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
            },
        )
        loader = make_loader(tmp_path)
        with caplog.at_level(logging.WARNING, logger="apcore"):
            sd = loader.load("long")
        assert sd is not None
        assert "200 characters" in caplog.text

    def test_caching(self, tmp_path: Path) -> None:
        write_simple_schema(tmp_path)
        loader = make_loader(tmp_path)
        sd1 = loader.load("simple")
        sd2 = loader.load("simple")
        assert sd1 is sd2


# === resolve() ===


class TestResolve:
    def test_resolve_no_refs(self, tmp_path: Path) -> None:
        write_simple_schema(tmp_path)
        loader = make_loader(tmp_path)
        sd = loader.load("simple")
        input_rs, output_rs = loader.resolve(sd)
        assert input_rs.json_schema["properties"]["table"]["type"] == "string"
        assert input_rs.direction == "input"
        assert output_rs.direction == "output"

    def test_resolve_local_ref(self, tmp_path: Path) -> None:
        write_yaml(
            tmp_path / "withref.schema.yaml",
            {
                "module_id": "withref",
                "description": "With ref",
                "input_schema": {
                    "type": "object",
                    "properties": {"addr": {"$ref": "#/definitions/Address"}},
                    "definitions": {"Address": {"type": "object", "properties": {"city": {"type": "string"}}}},
                },
                "output_schema": {"type": "object"},
            },
        )
        loader = make_loader(tmp_path)
        sd = loader.load("withref")
        input_rs, _ = loader.resolve(sd)
        assert "city" in input_rs.json_schema["properties"]["addr"]["properties"]

    def test_resolve_returns_tuple(self, tmp_path: Path) -> None:
        write_simple_schema(tmp_path)
        loader = make_loader(tmp_path)
        sd = loader.load("simple")
        result = loader.resolve(sd)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], ResolvedSchema)
        assert isinstance(result[1], ResolvedSchema)


# === generate_model() ===


class TestGenerateModel:
    def _make_loader(self, tmp_path: Path) -> SchemaLoader:
        return make_loader(tmp_path)

    def test_string_field(self, tmp_path: Path) -> None:
        loader = self._make_loader(tmp_path)
        Model = loader.generate_model(
            {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}, "TestStr"
        )
        obj = Model(name="hello")
        assert obj.name == "hello"

    def test_integer_field(self, tmp_path: Path) -> None:
        loader = self._make_loader(tmp_path)
        Model = loader.generate_model(
            {"type": "object", "properties": {"count": {"type": "integer"}}, "required": ["count"]}, "TestInt"
        )
        obj = Model(count=42)
        assert obj.count == 42

    def test_number_field(self, tmp_path: Path) -> None:
        loader = self._make_loader(tmp_path)
        Model = loader.generate_model(
            {"type": "object", "properties": {"ratio": {"type": "number"}}, "required": ["ratio"]}, "TestNum"
        )
        obj = Model(ratio=3.14)
        assert obj.ratio == 3.14

    def test_boolean_field(self, tmp_path: Path) -> None:
        loader = self._make_loader(tmp_path)
        Model = loader.generate_model(
            {"type": "object", "properties": {"active": {"type": "boolean"}}, "required": ["active"]}, "TestBool"
        )
        obj = Model(active=True)
        assert obj.active is True

    def test_nested_object(self, tmp_path: Path) -> None:
        loader = self._make_loader(tmp_path)
        Model = loader.generate_model(
            {
                "type": "object",
                "properties": {
                    "address": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}
                },
                "required": ["address"],
            },
            "TestNested",
        )
        obj = Model(address={"city": "NY"})
        assert obj.address.city == "NY"

    def test_array_with_items(self, tmp_path: Path) -> None:
        loader = self._make_loader(tmp_path)
        Model = loader.generate_model(
            {
                "type": "object",
                "properties": {"tags": {"type": "array", "items": {"type": "string"}}},
                "required": ["tags"],
            },
            "TestArr",
        )
        obj = Model(tags=["a", "b"])
        assert obj.tags == ["a", "b"]

    def test_array_without_items(self, tmp_path: Path) -> None:
        loader = self._make_loader(tmp_path)
        Model = loader.generate_model(
            {"type": "object", "properties": {"data": {"type": "array"}}, "required": ["data"]}, "TestArrAny"
        )
        obj = Model(data=[1, "x", True])
        assert len(obj.data) == 3

    def test_additional_properties(self, tmp_path: Path) -> None:
        loader = self._make_loader(tmp_path)
        Model = loader.generate_model(
            {
                "type": "object",
                "properties": {"meta": {"type": "object", "additionalProperties": {"type": "string"}}},
                "required": ["meta"],
            },
            "TestAdditional",
        )
        obj = Model(meta={"key": "value"})
        assert obj.meta == {"key": "value"}

    def test_empty_schema(self, tmp_path: Path) -> None:
        loader = self._make_loader(tmp_path)
        Model = loader.generate_model(
            {"type": "object", "properties": {"payload": {}}, "required": ["payload"]}, "TestEmpty"
        )
        obj = Model(payload={"any": "thing"})
        assert obj.payload == {"any": "thing"}

    def test_nullable_type(self, tmp_path: Path) -> None:
        loader = self._make_loader(tmp_path)
        Model = loader.generate_model(
            {"type": "object", "properties": {"name": {"type": ["string", "null"]}}, "required": ["name"]}, "TestNull"
        )
        assert Model(name=None).name is None
        assert Model(name="hello").name == "hello"

    def test_enum_type(self, tmp_path: Path) -> None:
        loader = self._make_loader(tmp_path)
        Model = loader.generate_model(
            {
                "type": "object",
                "properties": {"status": {"type": "string", "enum": ["active", "inactive"]}},
                "required": ["status"],
            },
            "TestEnum",
        )
        assert Model(status="active").status == "active"
        with pytest.raises(Exception):
            Model(status="unknown")

    def test_const_type(self, tmp_path: Path) -> None:
        loader = self._make_loader(tmp_path)
        Model = loader.generate_model(
            {"type": "object", "properties": {"version": {"const": "1.0"}}, "required": ["version"]}, "TestConst"
        )
        assert Model(version="1.0").version == "1.0"
        with pytest.raises(Exception):
            Model(version="2.0")

    def test_required_field_no_default(self, tmp_path: Path) -> None:
        loader = self._make_loader(tmp_path)
        Model = loader.generate_model(
            {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}, "TestReq"
        )
        with pytest.raises(Exception):
            Model()

    def test_optional_field_default_none(self, tmp_path: Path) -> None:
        loader = self._make_loader(tmp_path)
        Model = loader.generate_model({"type": "object", "properties": {"nickname": {"type": "string"}}}, "TestOpt")
        obj = Model()
        assert obj.nickname is None

    def test_default_value(self, tmp_path: Path) -> None:
        loader = self._make_loader(tmp_path)
        Model = loader.generate_model(
            {"type": "object", "properties": {"retries": {"type": "integer", "default": 3}}}, "TestDefault"
        )
        obj = Model()
        assert obj.retries == 3

    def test_constraints_min_max(self, tmp_path: Path) -> None:
        loader = self._make_loader(tmp_path)
        Model = loader.generate_model(
            {
                "type": "object",
                "properties": {"age": {"type": "integer", "minimum": 0, "maximum": 150}},
                "required": ["age"],
            },
            "TestMinMax",
        )
        assert Model(age=25).age == 25
        with pytest.raises(Exception):
            Model(age=-1)
        with pytest.raises(Exception):
            Model(age=200)

    def test_constraints_string_length(self, tmp_path: Path) -> None:
        loader = self._make_loader(tmp_path)
        Model = loader.generate_model(
            {
                "type": "object",
                "properties": {"code": {"type": "string", "minLength": 3, "maxLength": 10}},
                "required": ["code"],
            },
            "TestStrLen",
        )
        assert Model(code="abc").code == "abc"
        with pytest.raises(Exception):
            Model(code="ab")
        with pytest.raises(Exception):
            Model(code="a" * 11)

    def test_constraints_pattern(self, tmp_path: Path) -> None:
        loader = self._make_loader(tmp_path)
        Model = loader.generate_model(
            {"type": "object", "properties": {"id": {"type": "string", "pattern": "^[A-Z]{3}$"}}, "required": ["id"]},
            "TestPattern",
        )
        assert Model(id="ABC").id == "ABC"
        with pytest.raises(Exception):
            Model(id="abc")

    def test_unique_items(self, tmp_path: Path) -> None:
        loader = self._make_loader(tmp_path)
        Model = loader.generate_model(
            {
                "type": "object",
                "properties": {"ids": {"type": "array", "items": {"type": "integer"}, "uniqueItems": True}},
                "required": ["ids"],
            },
            "TestUnique",
        )
        assert Model(ids=[1, 2, 3]).ids == [1, 2, 3]
        with pytest.raises(Exception):
            Model(ids=[1, 1, 2])

    def test_multiple_of(self, tmp_path: Path) -> None:
        loader = self._make_loader(tmp_path)
        Model = loader.generate_model(
            {"type": "object", "properties": {"qty": {"type": "integer", "multipleOf": 5}}, "required": ["qty"]},
            "TestMultiple",
        )
        assert Model(qty=10).qty == 10
        with pytest.raises(Exception):
            Model(qty=7)

    def test_one_of(self, tmp_path: Path) -> None:
        loader = self._make_loader(tmp_path)
        Model = loader.generate_model(
            {
                "type": "object",
                "properties": {"value": {"oneOf": [{"type": "string"}, {"type": "integer"}]}},
                "required": ["value"],
            },
            "TestOneOf",
        )
        assert Model(value="hello").value == "hello"
        assert Model(value=42).value == 42

    def test_any_of(self, tmp_path: Path) -> None:
        loader = self._make_loader(tmp_path)
        Model = loader.generate_model(
            {
                "type": "object",
                "properties": {"value": {"anyOf": [{"type": "string"}, {"type": "integer"}]}},
                "required": ["value"],
            },
            "TestAnyOf",
        )
        assert Model(value="hello").value == "hello"
        assert Model(value=42).value == 42

    def test_all_of(self, tmp_path: Path) -> None:
        loader = self._make_loader(tmp_path)
        Model = loader.generate_model(
            {
                "type": "object",
                "properties": {
                    "person": {
                        "allOf": [
                            {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
                            {"type": "object", "properties": {"age": {"type": "integer"}}, "required": ["age"]},
                        ]
                    }
                },
                "required": ["person"],
            },
            "TestAllOf",
        )
        obj = Model(person={"name": "Alice", "age": 30})
        assert obj.person.name == "Alice"
        assert obj.person.age == 30

    def test_all_of_conflict_raises(self, tmp_path: Path) -> None:
        loader = self._make_loader(tmp_path)
        with pytest.raises(SchemaParseError, match="conflict"):
            loader.generate_model(
                {
                    "type": "object",
                    "properties": {
                        "x": {
                            "allOf": [
                                {"type": "object", "properties": {"val": {"type": "string"}}},
                                {"type": "object", "properties": {"val": {"type": "integer"}}},
                            ]
                        }
                    },
                    "required": ["x"],
                },
                "TestConflict",
            )

    def test_not_raises(self, tmp_path: Path) -> None:
        loader = self._make_loader(tmp_path)
        with pytest.raises(SchemaParseError, match="not"):
            loader.generate_model(
                {"type": "object", "properties": {"x": {"not": {"type": "string"}}}, "required": ["x"]}, "TestNot"
            )

    def test_if_then_else_raises(self, tmp_path: Path) -> None:
        loader = self._make_loader(tmp_path)
        with pytest.raises(SchemaParseError, match="if/then/else"):
            loader.generate_model(
                {
                    "type": "object",
                    "properties": {
                        "x": {"if": {"type": "string"}, "then": {"minLength": 1}, "else": {"type": "integer"}}
                    },
                    "required": ["x"],
                },
                "TestIfThen",
            )

    def test_x_extensions_preserved(self, tmp_path: Path) -> None:
        loader = self._make_loader(tmp_path)
        Model = loader.generate_model(
            {
                "type": "object",
                "properties": {"name": {"type": "string", "x-llm-description": "AI desc", "x-sensitive": True}},
                "required": ["name"],
            },
            "TestExt",
        )
        schema = Model.model_json_schema()
        props = schema["properties"]["name"]
        assert props.get("x-llm-description") == "AI desc"
        assert props.get("x-sensitive") is True

    def test_format_not_enforced(self, tmp_path: Path) -> None:
        loader = self._make_loader(tmp_path)
        Model = loader.generate_model(
            {"type": "object", "properties": {"email": {"type": "string", "format": "email"}}, "required": ["email"]},
            "TestFormat",
        )
        obj = Model(email="not-an-email")
        assert obj.email == "not-an-email"


# === get_schema() ===


class TestGetSchema:
    def test_yaml_first_uses_yaml(self, tmp_path: Path) -> None:
        write_simple_schema(tmp_path)
        config = Config({"schema": {"root": str(tmp_path), "strategy": "yaml_first"}})
        loader = SchemaLoader(config, schemas_dir=tmp_path)
        input_rs, output_rs = loader.get_schema("simple")
        assert input_rs.module_id == "simple"

    def test_yaml_first_fallback_to_native(self, tmp_path: Path) -> None:
        class InputModel(BaseModel):
            name: str

        class OutputModel(BaseModel):
            result: str

        config = Config({"schema": {"root": str(tmp_path), "strategy": "yaml_first"}})
        loader = SchemaLoader(config, schemas_dir=tmp_path)
        input_rs, output_rs = loader.get_schema(
            "missing.module", native_input_schema=InputModel, native_output_schema=OutputModel
        )
        assert input_rs.model is InputModel
        assert output_rs.model is OutputModel

    def test_yaml_first_neither_raises(self, tmp_path: Path) -> None:
        config = Config({"schema": {"root": str(tmp_path), "strategy": "yaml_first"}})
        loader = SchemaLoader(config, schemas_dir=tmp_path)
        with pytest.raises(SchemaNotFoundError):
            loader.get_schema("nonexistent")

    def test_native_first_uses_native(self, tmp_path: Path) -> None:
        write_simple_schema(tmp_path)

        class InputModel(BaseModel):
            x: int

        class OutputModel(BaseModel):
            y: str

        config = Config({"schema": {"root": str(tmp_path), "strategy": "native_first"}})
        loader = SchemaLoader(config, schemas_dir=tmp_path)
        input_rs, _ = loader.get_schema("simple", native_input_schema=InputModel, native_output_schema=OutputModel)
        assert input_rs.model is InputModel

    def test_native_first_fallback_to_yaml(self, tmp_path: Path) -> None:
        write_simple_schema(tmp_path)
        config = Config({"schema": {"root": str(tmp_path), "strategy": "native_first"}})
        loader = SchemaLoader(config, schemas_dir=tmp_path)
        input_rs, _ = loader.get_schema("simple")
        assert input_rs.module_id == "simple"

    def test_yaml_only_ignores_native(self, tmp_path: Path) -> None:
        write_simple_schema(tmp_path)

        class InputModel(BaseModel):
            x: int

        class OutputModel(BaseModel):
            y: str

        config = Config({"schema": {"root": str(tmp_path), "strategy": "yaml_only"}})
        loader = SchemaLoader(config, schemas_dir=tmp_path)
        input_rs, _ = loader.get_schema("simple", native_input_schema=InputModel, native_output_schema=OutputModel)
        assert input_rs.model is not InputModel

    def test_yaml_only_not_found_raises(self, tmp_path: Path) -> None:
        config = Config({"schema": {"root": str(tmp_path), "strategy": "yaml_only"}})
        loader = SchemaLoader(config, schemas_dir=tmp_path)
        with pytest.raises(SchemaNotFoundError):
            loader.get_schema("nonexistent")

    def test_native_preserves_validators(self, tmp_path: Path) -> None:
        class ValidatedInput(BaseModel):
            name: str

            @field_validator("name")
            @classmethod
            def name_must_not_be_empty(cls, v: str) -> str:
                if not v:
                    raise ValueError("name must not be empty")
                return v

        class OutputModel(BaseModel):
            ok: bool

        config = Config({"schema": {"root": str(tmp_path), "strategy": "native_first"}})
        loader = SchemaLoader(config, schemas_dir=tmp_path)
        input_rs, _ = loader.get_schema("test", native_input_schema=ValidatedInput, native_output_schema=OutputModel)
        assert input_rs.model is ValidatedInput


# === clear_cache() ===


class TestClearCache:
    def test_clear_cache_invalidates(self, tmp_path: Path) -> None:
        write_simple_schema(tmp_path)
        loader = make_loader(tmp_path)
        sd1 = loader.load("simple")
        write_yaml(
            tmp_path / "simple.schema.yaml",
            {
                "module_id": "simple",
                "description": "Updated",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
            },
        )
        loader.clear_cache()
        sd2 = loader.load("simple")
        assert sd2.description == "Updated"
        assert sd1 is not sd2
