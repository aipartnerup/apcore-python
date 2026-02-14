"""Tests for SchemaValidator."""

from __future__ import annotations

from typing import Literal

import pytest
from pydantic import BaseModel, Field

from apcore.errors import SchemaValidationError
from apcore.schema.validator import SchemaValidator


# --- Inline test models ---


class SimpleModel(BaseModel):
    name: str
    age: int
    active: bool = True


class ConstrainedModel(BaseModel):
    name: str = Field(min_length=2, max_length=50)
    count: int = Field(ge=0, le=100)
    code: str = Field(pattern=r"^[A-Z]{3}$")


class NestedModel(BaseModel):
    class Address(BaseModel):
        city: str
        zip_code: str

    address: Address


class ArrayModel(BaseModel):
    class Item(BaseModel):
        quantity: int

    items: list[Item]


class EnumModel(BaseModel):
    status: Literal["active", "inactive", "pending"]


class StrictModel(BaseModel):
    model_config = {"extra": "forbid"}
    name: str
    value: int


class OptionalFieldsModel(BaseModel):
    name: str = "default"
    count: int = 0


@pytest.fixture
def validator() -> SchemaValidator:
    return SchemaValidator(coerce_types=True)


@pytest.fixture
def strict_validator() -> SchemaValidator:
    return SchemaValidator(coerce_types=False)


# === validate() ===


class TestValidate:
    def test_valid_data(self, validator: SchemaValidator) -> None:
        result = validator.validate({"name": "Alice", "age": 30}, SimpleModel)
        assert result.valid is True
        assert result.errors == []

    def test_missing_required_field(self, validator: SchemaValidator) -> None:
        result = validator.validate({"name": "Alice"}, SimpleModel)
        assert result.valid is False
        assert any(e.constraint == "required" for e in result.errors)

    def test_wrong_type_strict(self, strict_validator: SchemaValidator) -> None:
        result = strict_validator.validate(
            {"name": "Alice", "age": "not_a_number"}, SimpleModel
        )
        assert result.valid is False
        assert any(e.constraint == "type" for e in result.errors)

    def test_pattern_mismatch(self, validator: SchemaValidator) -> None:
        result = validator.validate(
            {"name": "AB", "count": 5, "code": "abc"}, ConstrainedModel
        )
        assert result.valid is False
        assert any(
            e.path == "/code" and e.constraint == "pattern" for e in result.errors
        )

    def test_below_minimum(self, validator: SchemaValidator) -> None:
        result = validator.validate(
            {"name": "AB", "count": -1, "code": "ABC"}, ConstrainedModel
        )
        assert result.valid is False
        assert any(
            e.path == "/count" and e.constraint == "minimum" for e in result.errors
        )

    def test_above_maximum(self, validator: SchemaValidator) -> None:
        result = validator.validate(
            {"name": "AB", "count": 101, "code": "ABC"}, ConstrainedModel
        )
        assert result.valid is False
        assert any(
            e.path == "/count" and e.constraint == "maximum" for e in result.errors
        )

    def test_string_too_short(self, validator: SchemaValidator) -> None:
        result = validator.validate(
            {"name": "A", "count": 5, "code": "ABC"}, ConstrainedModel
        )
        assert result.valid is False
        assert any(
            e.path == "/name" and e.constraint == "minLength" for e in result.errors
        )

    def test_string_too_long(self, validator: SchemaValidator) -> None:
        result = validator.validate(
            {"name": "A" * 51, "count": 5, "code": "ABC"}, ConstrainedModel
        )
        assert result.valid is False
        assert any(
            e.path == "/name" and e.constraint == "maxLength" for e in result.errors
        )

    def test_enum_invalid(self, validator: SchemaValidator) -> None:
        result = validator.validate({"status": "unknown"}, EnumModel)
        assert result.valid is False
        assert any(
            e.path == "/status" and e.constraint == "enum" for e in result.errors
        )

    def test_multiple_errors(self, validator: SchemaValidator) -> None:
        result = validator.validate({}, SimpleModel)
        assert result.valid is False
        assert len(result.errors) >= 2

    def test_nested_error_path(self, validator: SchemaValidator) -> None:
        result = validator.validate({"address": {"zip_code": "12345"}}, NestedModel)
        assert result.valid is False
        assert any(e.path == "/address/city" for e in result.errors)

    def test_array_item_error_path(self, strict_validator: SchemaValidator) -> None:
        result = strict_validator.validate({"items": [{"quantity": "bad"}]}, ArrayModel)
        assert result.valid is False
        assert any("/items/0/quantity" in e.path for e in result.errors)

    def test_extra_properties_strict(self, validator: SchemaValidator) -> None:
        result = validator.validate(
            {"name": "Alice", "value": 1, "extra": "nope"}, StrictModel
        )
        assert result.valid is False
        assert any(e.constraint == "additionalProperties" for e in result.errors)

    def test_optional_fields(self, validator: SchemaValidator) -> None:
        result = validator.validate({}, OptionalFieldsModel)
        assert result.valid is True


# === validate_input() ===


class TestValidateInput:
    def test_valid_returns_dict(self, validator: SchemaValidator) -> None:
        result = validator.validate_input({"name": "Alice", "age": 30}, SimpleModel)
        assert isinstance(result, dict)
        assert result["name"] == "Alice"
        assert result["age"] == 30
        assert result["active"] is True

    def test_invalid_raises(self, validator: SchemaValidator) -> None:
        with pytest.raises(SchemaValidationError) as exc_info:
            validator.validate_input({"name": "Alice"}, SimpleModel)
        assert exc_info.value.code == "SCHEMA_VALIDATION_ERROR"
        assert len(exc_info.value.details["errors"]) > 0

    def test_coercion_enabled(self, validator: SchemaValidator) -> None:
        result = validator.validate_input({"name": "Alice", "age": "30"}, SimpleModel)
        assert result["age"] == 30
        assert isinstance(result["age"], int)

    def test_strict_rejects_coercion(self, strict_validator: SchemaValidator) -> None:
        with pytest.raises(SchemaValidationError):
            strict_validator.validate_input({"name": "Alice", "age": "30"}, SimpleModel)


# === validate_output() ===


class TestValidateOutput:
    def test_valid_returns_dict(self, validator: SchemaValidator) -> None:
        result = validator.validate_output({"name": "Alice", "age": 30}, SimpleModel)
        assert isinstance(result, dict)
        assert result["name"] == "Alice"

    def test_invalid_raises(self, validator: SchemaValidator) -> None:
        with pytest.raises(SchemaValidationError):
            validator.validate_output({"name": "Alice"}, SimpleModel)


# === error conversion ===


class TestErrorConversion:
    def test_missing_constraint(self, validator: SchemaValidator) -> None:
        result = validator.validate({}, SimpleModel)
        name_err = next(e for e in result.errors if e.path == "/name")
        assert name_err.constraint == "required"

    def test_type_constraint(self, strict_validator: SchemaValidator) -> None:
        result = strict_validator.validate({"name": 123, "age": 30}, SimpleModel)
        name_err = next(e for e in result.errors if e.path == "/name")
        assert name_err.constraint == "type"

    def test_min_length_constraint(self, validator: SchemaValidator) -> None:
        result = validator.validate(
            {"name": "A", "count": 5, "code": "ABC"}, ConstrainedModel
        )
        name_err = next(e for e in result.errors if e.path == "/name")
        assert name_err.constraint == "minLength"

    def test_pattern_constraint(self, validator: SchemaValidator) -> None:
        result = validator.validate(
            {"name": "AB", "count": 5, "code": "abc"}, ConstrainedModel
        )
        code_err = next(e for e in result.errors if e.path == "/code")
        assert code_err.constraint == "pattern"

    def test_minimum_constraint(self, validator: SchemaValidator) -> None:
        result = validator.validate(
            {"name": "AB", "count": -1, "code": "ABC"}, ConstrainedModel
        )
        count_err = next(e for e in result.errors if e.path == "/count")
        assert count_err.constraint == "minimum"

    def test_enum_constraint(self, validator: SchemaValidator) -> None:
        result = validator.validate({"status": "unknown"}, EnumModel)
        status_err = next(e for e in result.errors if e.path == "/status")
        assert status_err.constraint == "enum"

    def test_additional_properties_constraint(self, validator: SchemaValidator) -> None:
        result = validator.validate(
            {"name": "Alice", "value": 1, "extra": "x"}, StrictModel
        )
        assert any(e.constraint == "additionalProperties" for e in result.errors)

    def test_loc_to_path(self, validator: SchemaValidator) -> None:
        result = validator.validate({}, SimpleModel)
        assert any(e.path == "/name" for e in result.errors)

    def test_nested_loc_to_path(self, validator: SchemaValidator) -> None:
        result = validator.validate({"address": {"zip_code": "12345"}}, NestedModel)
        assert any(e.path == "/address/city" for e in result.errors)
