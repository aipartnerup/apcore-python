"""Tests for redact_sensitive() utility function."""

from __future__ import annotations

import copy

from apcore.executor import redact_sensitive


class TestRedactSensitiveBasic:
    """Tests for basic field redaction."""

    def test_sensitive_field_redacted(self) -> None:
        """Simple field with x-sensitive: true gets redacted."""
        data = {"password": "secret123", "username": "alice"}
        schema = {
            "type": "object",
            "properties": {
                "password": {"type": "string", "x-sensitive": True},
                "username": {"type": "string"},
            },
        }
        result = redact_sensitive(data, schema)
        assert result["password"] == "***REDACTED***"
        assert result["username"] == "alice"

    def test_non_sensitive_fields_unchanged(self) -> None:
        """Fields without x-sensitive are unchanged."""
        data = {"name": "Alice", "age": 30}
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
        }
        result = redact_sensitive(data, schema)
        assert result == {"name": "Alice", "age": 30}

    def test_mixed_sensitive_and_non_sensitive(self) -> None:
        """Only sensitive fields get redacted, others stay."""
        data = {"username": "alice", "password": "secret", "email": "a@b.com"}
        schema = {
            "type": "object",
            "properties": {
                "username": {"type": "string"},
                "password": {"type": "string", "x-sensitive": True},
                "email": {"type": "string"},
            },
        }
        result = redact_sensitive(data, schema)
        assert result["username"] == "alice"
        assert result["password"] == "***REDACTED***"
        assert result["email"] == "a@b.com"


class TestRedactSensitiveNested:
    """Tests for nested object redaction."""

    def test_nested_object_sensitive_field(self) -> None:
        """Nested object with sensitive field gets redacted."""
        data = {"profile": {"name": "Alice", "ssn": "123-45-6789"}}
        schema = {
            "type": "object",
            "properties": {
                "profile": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "ssn": {"type": "string", "x-sensitive": True},
                    },
                },
            },
        }
        result = redact_sensitive(data, schema)
        assert result["profile"]["name"] == "Alice"
        assert result["profile"]["ssn"] == "***REDACTED***"


class TestRedactSensitiveArray:
    """Tests for array item redaction."""

    def test_array_items_redacted(self) -> None:
        """Array items with x-sensitive on items schema are redacted."""
        data = {"tokens": ["abc", "def"]}
        schema = {
            "type": "object",
            "properties": {
                "tokens": {
                    "type": "array",
                    "items": {"type": "string", "x-sensitive": True},
                },
            },
        }
        result = redact_sensitive(data, schema)
        assert result["tokens"] == ["***REDACTED***", "***REDACTED***"]


class TestRedactSensitiveNullAndMissing:
    """Tests for None values and missing fields."""

    def test_none_value_kept_as_none(self) -> None:
        """None field values stay as None even if marked sensitive."""
        data = {"password": None, "username": "alice"}
        schema = {
            "type": "object",
            "properties": {
                "password": {"type": "string", "x-sensitive": True},
                "username": {"type": "string"},
            },
        }
        result = redact_sensitive(data, schema)
        assert result["password"] is None
        assert result["username"] == "alice"

    def test_missing_field_not_added(self) -> None:
        """Fields marked sensitive but missing from data are not added."""
        data = {"username": "alice"}
        schema = {
            "type": "object",
            "properties": {
                "password": {"type": "string", "x-sensitive": True},
                "username": {"type": "string"},
            },
        }
        result = redact_sensitive(data, schema)
        assert "password" not in result
        assert result["username"] == "alice"


class TestRedactSensitiveSecretPrefix:
    """Tests for _secret_ prefix convention."""

    def test_secret_prefix_redacted(self) -> None:
        """Keys starting with _secret_ are redacted regardless of schema."""
        data = {"_secret_api_key": "sk-abc123", "normal_key": "value"}
        schema: dict[str, object] = {}
        result = redact_sensitive(data, schema)
        assert result["_secret_api_key"] == "***REDACTED***"
        assert result["normal_key"] == "value"


class TestRedactSensitiveDeepCopy:
    """Tests for deep copy behavior."""

    def test_original_data_not_modified(self) -> None:
        """Deep copy ensures original data is not modified."""
        data = {"password": "secret123", "nested": {"key": "val"}}
        data_copy = copy.deepcopy(data)
        schema = {
            "type": "object",
            "properties": {
                "password": {"type": "string", "x-sensitive": True},
            },
        }
        redact_sensitive(data, schema)
        assert data == data_copy


class TestRedactSensitiveEdgeCases:
    """Tests for edge cases."""

    def test_empty_data_returns_empty(self) -> None:
        """Empty data returns empty dict."""
        schema = {"type": "object", "properties": {"x": {"type": "string", "x-sensitive": True}}}
        result = redact_sensitive({}, schema)
        assert result == {}

    def test_no_properties_returns_data_as_is(self) -> None:
        """No properties in schema returns data unchanged."""
        data = {"key": "value"}
        result = redact_sensitive(data, {})
        assert result == {"key": "value"}
