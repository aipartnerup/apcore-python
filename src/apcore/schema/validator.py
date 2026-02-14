"""SchemaValidator — validates runtime data against Pydantic models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError as PydanticValidationError

from apcore.schema.types import SchemaValidationErrorDetail, SchemaValidationResult

__all__ = ["SchemaValidator"]

_PYDANTIC_TO_CONSTRAINT: dict[str, str] = {
    "missing": "required",
    "string_type": "type",
    "int_type": "type",
    "float_type": "type",
    "bool_type": "type",
    "string_too_short": "minLength",
    "string_too_long": "maxLength",
    "string_pattern_mismatch": "pattern",
    "greater_than_equal": "minimum",
    "less_than_equal": "maximum",
    "greater_than": "exclusiveMinimum",
    "less_than": "exclusiveMaximum",
    "literal_error": "enum",
    "value_error": "value",
    "extra_forbidden": "additionalProperties",
    "too_short": "minLength",
    "too_long": "maxLength",
}

_EXPECTED_KEYS = (
    "expected",
    "ge",
    "le",
    "gt",
    "lt",
    "min_length",
    "max_length",
    "pattern",
)


class SchemaValidator:
    """Validates runtime data against Pydantic models and produces apcore-standard error output."""

    def __init__(self, coerce_types: bool = True) -> None:
        self._coerce_types = coerce_types

    def validate(
        self, data: dict[str, Any], model: type[BaseModel]
    ) -> SchemaValidationResult:
        """Validate data against a Pydantic model, returning a result object."""
        try:
            model.model_validate(data, strict=not self._coerce_types)
            return SchemaValidationResult(valid=True, errors=[])
        except PydanticValidationError as e:
            return SchemaValidationResult(
                valid=False, errors=self._pydantic_error_to_details(e)
            )

    def validate_input(
        self, data: dict[str, Any], model: type[BaseModel]
    ) -> dict[str, Any]:
        """Validate input data and return the validated dict. Raises SchemaValidationError on failure."""
        return self._validate_and_dump(data, model)

    def validate_output(
        self, data: dict[str, Any], model: type[BaseModel]
    ) -> dict[str, Any]:
        """Validate output data and return the validated dict. Raises SchemaValidationError on failure."""
        return self._validate_and_dump(data, model)

    def _validate_and_dump(
        self, data: dict[str, Any], model: type[BaseModel]
    ) -> dict[str, Any]:
        """Validate data and return model_dump(). Raises SchemaValidationError on failure."""
        try:
            instance = model.model_validate(data, strict=not self._coerce_types)
            return instance.model_dump()
        except PydanticValidationError as e:
            result = SchemaValidationResult(
                valid=False, errors=self._pydantic_error_to_details(e)
            )
            raise result.to_error() from e

    def _pydantic_error_to_details(
        self, error: PydanticValidationError
    ) -> list[SchemaValidationErrorDetail]:
        """Convert Pydantic v2 ValidationError to apcore error details."""
        details: list[SchemaValidationErrorDetail] = []
        for err in error.errors():
            loc = err.get("loc", ())
            path = "/" + "/".join(str(segment) for segment in loc) if loc else "/"

            pydantic_type = err.get("type", "")
            constraint = _PYDANTIC_TO_CONSTRAINT.get(pydantic_type, pydantic_type)

            message = err.get("msg", "")

            ctx = err.get("ctx", {})
            expected: Any = None
            for key in _EXPECTED_KEYS:
                val = ctx.get(key)
                if val is not None:
                    expected = val
                    break

            actual = ctx.get("actual")
            if actual is None:
                actual = err.get("input")

            details.append(
                SchemaValidationErrorDetail(
                    path=path,
                    message=message,
                    constraint=constraint,
                    expected=expected,
                    actual=actual,
                )
            )
        return details
