"""Tests for the SHOULD-level `format` warning walk.

JSON Schema 2020-12 §7.2.1 puts `format` in the format-annotation vocabulary:
a value that does not satisfy a recognised format is an annotation, never a
validation failure. apcore reports it as a warning instead, and the walk that
finds those annotations has to reach every node the data actually touches —
including combinator branches, which it previously skipped.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from apcore.config import Config
from apcore.schema.hardening import validate_schema_dict, warn_format_violations
from apcore.schema.loader import SchemaLoader

_LOGGER_NAME = "apcore.schema.hardening"


def _warnings(caplog: pytest.LogCaptureFixture, data: Any, schema: dict[str, Any]) -> list[str]:
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        result = validate_schema_dict(data, schema)
    assert result.valid is True, "a format annotation must never fail validation"
    return [r.getMessage() for r in caplog.records]


def _wrap(prop_schema: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": {"c": prop_schema}}


class TestFormatWarningWalk:
    def test_warns_on_a_plain_property(self, caplog: pytest.LogCaptureFixture) -> None:
        found = _warnings(caplog, {"c": "not-an-email"}, _wrap({"type": "string", "format": "email"}))
        assert len(found) == 1
        assert "'/c'" in found[0]
        assert "email" in found[0]

    def test_warns_through_a_type_array(self, caplog: pytest.LogCaptureFixture) -> None:
        schema = _wrap({"type": ["string", "null"], "format": "email"})
        assert len(_warnings(caplog, {"c": "not-an-email"}, schema)) == 1

    @pytest.mark.parametrize("keyword", ["anyOf", "oneOf"])
    def test_warns_inside_a_union_branch(self, caplog: pytest.LogCaptureFixture, keyword: str) -> None:
        schema = _wrap({keyword: [{"type": "string", "format": "email"}]})
        assert len(_warnings(caplog, {"c": "not-an-email"}, schema)) == 1

    def test_warns_inside_an_all_of_member(self, caplog: pytest.LogCaptureFixture) -> None:
        schema = _wrap({"allOf": [{"type": "string"}, {"format": "email"}]})
        assert len(_warnings(caplog, {"c": "not-an-email"}, schema)) == 1

    def test_warns_inside_an_additional_properties_sub_schema(self, caplog: pytest.LogCaptureFixture) -> None:
        schema = {"type": "object", "additionalProperties": {"type": "string", "format": "email"}}
        assert len(_warnings(caplog, {"c": "not-an-email"}, schema)) == 1

    def test_a_branch_the_data_does_not_satisfy_is_not_reported(self, caplog: pytest.LogCaptureFixture) -> None:
        """A sibling branch must not report a format the value never carried."""
        schema = _wrap({"anyOf": [{"type": "string", "format": "email"}, {"type": "integer"}]})
        assert _warnings(caplog, {"c": 123}, schema) == []

    def test_an_annotation_reached_twice_is_reported_once(self, caplog: pytest.LogCaptureFixture) -> None:
        schema = _wrap({"allOf": [{"format": "email"}, {"format": "email"}]})
        assert len(_warnings(caplog, {"c": "not-an-email"}, schema)) == 1

    def test_an_unrecognised_format_never_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        schema = _wrap({"type": "string", "format": "path"})
        assert _warnings(caplog, {"c": "/usr/bin/ls"}, schema) == []

    def test_a_conformant_value_never_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        schema = _wrap({"type": "string", "format": "email"})
        assert _warnings(caplog, {"c": "user@example.com"}, schema) == []

    def test_warns_on_a_nested_array_element(self, caplog: pytest.LogCaptureFixture) -> None:
        schema = _wrap({"type": "array", "items": {"type": "string", "format": "email"}})
        found = _warnings(caplog, {"c": ["a@b.com", "not-an-email"]}, schema)
        assert len(found) == 1
        assert "'/c[1]'" in found[0]


class TestWarnFormatViolationsOnAModel:
    """The executor validates through Pydantic, so the warning is emitted from the model."""

    def _model(self, tmp_path: Path, prop_schema: dict[str, Any], name: str) -> Any:
        loader = SchemaLoader(Config({}), schemas_dir=tmp_path)
        return loader.generate_model(
            {"type": "object", "properties": {"contact": prop_schema}, "required": ["contact"]},
            name,
        )

    def test_warns_for_a_generated_model(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        model = self._model(tmp_path, {"type": "string", "format": "email"}, "FmtModel")
        with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
            warn_format_violations({"contact": "not-an-email"}, model)
        assert len(caplog.records) == 1
        assert "email" in caplog.records[0].getMessage()

    def test_silent_for_a_conformant_value(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        model = self._model(tmp_path, {"type": "string", "format": "email"}, "FmtOk")
        with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
            warn_format_violations({"contact": "user@example.com"}, model)
        assert caplog.records == []

    def test_a_schema_without_format_is_short_circuited(self, tmp_path: Path) -> None:
        model = self._model(tmp_path, {"type": "string"}, "NoFmt")
        warn_format_violations({"contact": "x"}, model)
        assert model.__apcore_declares_format__ is False

    def test_a_model_without_a_source_schema_is_skipped(self, caplog: pytest.LogCaptureFixture) -> None:
        from pydantic import BaseModel

        class Native(BaseModel):
            contact: str

        with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
            warn_format_violations({"contact": "not-an-email"}, Native)
        assert caplog.records == []
