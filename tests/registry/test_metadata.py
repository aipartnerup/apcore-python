"""Tests for metadata and ID map loading."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from apcore.errors import ConfigError, ConfigNotFoundError
from apcore.registry.metadata import (
    load_id_map,
    load_metadata,
    merge_module_metadata,
    parse_dependencies,
)


# === load_metadata() ===


class TestLoadMetadata:
    def test_valid_yaml_all_fields(self, tmp_path: Path) -> None:
        """Valid YAML with all fields returns parsed dict."""
        meta = tmp_path / "mod_meta.yaml"
        meta.write_text(
            yaml.dump(
                {
                    "description": "Test module",
                    "tags": ["tag1", "tag2"],
                    "version": "2.0.0",
                    "dependencies": [{"module_id": "foo.bar"}],
                    "entry_point": "mod:MyModule",
                }
            )
        )
        result = load_metadata(meta)
        assert result["description"] == "Test module"
        assert result["tags"] == ["tag1", "tag2"]
        assert result["version"] == "2.0.0"

    def test_nonexistent_file_returns_empty(self, tmp_path: Path) -> None:
        """Non-existent file returns empty dict."""
        assert load_metadata(tmp_path / "nonexistent_meta.yaml") == {}

    def test_invalid_yaml_raises(self, tmp_path: Path) -> None:
        """Invalid YAML content raises ConfigError."""
        meta = tmp_path / "bad_meta.yaml"
        meta.write_text("{{invalid yaml:")
        with pytest.raises(ConfigError):
            load_metadata(meta)

    def test_partial_fields(self, tmp_path: Path) -> None:
        """YAML with only some fields returns partial dict."""
        meta = tmp_path / "partial_meta.yaml"
        meta.write_text(yaml.dump({"description": "partial", "tags": ["t1"]}))
        result = load_metadata(meta)
        assert result["description"] == "partial"
        assert result["tags"] == ["t1"]
        assert "version" not in result

    def test_empty_file_returns_empty(self, tmp_path: Path) -> None:
        """Empty file returns empty dict."""
        meta = tmp_path / "empty_meta.yaml"
        meta.write_text("")
        assert load_metadata(meta) == {}


# === parse_dependencies() ===


class TestParseDependencies:
    def test_list_of_dicts(self) -> None:
        """List of dicts returns DependencyInfo objects."""
        result = parse_dependencies([{"module_id": "foo.bar"}, {"module_id": "baz.qux"}])
        assert len(result) == 2
        assert result[0].module_id == "foo.bar"
        assert result[0].optional is False
        assert result[0].version is None

    def test_optional_true(self) -> None:
        """Dependency with optional=True."""
        result = parse_dependencies([{"module_id": "foo", "optional": True}])
        assert result[0].optional is True

    def test_version_constraint(self) -> None:
        """Dependency with version constraint."""
        result = parse_dependencies([{"module_id": "foo", "version": ">=1.0.0"}])
        assert result[0].version == ">=1.0.0"

    def test_empty_list(self) -> None:
        """Empty list returns empty list."""
        assert parse_dependencies([]) == []


# === merge_module_metadata() ===


class TestMergeModuleMetadata:
    def test_yaml_overrides_code(self) -> None:
        """YAML values override code attributes."""

        class MockModule:
            description = "code desc"
            tags = ["code_tag"]
            version = "1.0.0"

        meta = {"description": "yaml desc", "tags": ["yaml_tag"], "version": "2.0.0"}
        result = merge_module_metadata(MockModule, meta)
        assert result["description"] == "yaml desc"
        assert result["tags"] == ["yaml_tag"]
        assert result["version"] == "2.0.0"

    def test_code_attrs_when_yaml_empty(self) -> None:
        """Code attributes used when YAML is empty."""

        class MockModule:
            description = "code desc"
            tags = ["t1"]
            version = "1.0.0"

        result = merge_module_metadata(MockModule, {})
        assert result["description"] == "code desc"
        assert result["tags"] == ["t1"]
        assert result["version"] == "1.0.0"

    def test_defaults_for_missing(self) -> None:
        """Minimal class with empty meta gives defaults."""

        class MockModule:
            description = "d"

        result = merge_module_metadata(MockModule, {})
        assert result["description"] == "d"
        assert result["tags"] == []
        assert result["version"] == "1.0.0"

    def test_metadata_dicts_merged(self) -> None:
        """Nested metadata dicts are shallow-merged (YAML wins on conflicts)."""

        class MockModule:
            description = "d"
            metadata = {"key1": "val1", "shared": "code"}

        meta = {"metadata": {"key2": "val2", "shared": "yaml"}}
        result = merge_module_metadata(MockModule, meta)
        assert result["metadata"]["key1"] == "val1"
        assert result["metadata"]["key2"] == "val2"
        assert result["metadata"]["shared"] == "yaml"


# === load_id_map() ===


class TestLoadIdMap:
    def test_valid_id_map(self, tmp_path: Path) -> None:
        """Valid ID map returns dict keyed by file path."""
        f = tmp_path / "id_map.yaml"
        f.write_text(
            yaml.dump(
                {
                    "mappings": [
                        {
                            "file": "extensions/email/send.py",
                            "id": "email.send",
                            "class": "SendModule",
                        },
                        {"file": "extensions/legacy/old.py", "id": "legacy.old"},
                    ]
                }
            )
        )
        result = load_id_map(f)
        assert len(result) == 2
        assert result["extensions/email/send.py"]["id"] == "email.send"
        assert result["extensions/email/send.py"]["class"] == "SendModule"

    def test_class_override_present(self, tmp_path: Path) -> None:
        """Entry with class has class field in result."""
        f = tmp_path / "id_map.yaml"
        f.write_text(yaml.dump({"mappings": [{"file": "mod.py", "id": "m", "class": "MyClass"}]}))
        result = load_id_map(f)
        assert result["mod.py"]["class"] == "MyClass"

    def test_nonexistent_raises(self, tmp_path: Path) -> None:
        """Non-existent file raises ConfigNotFoundError."""
        with pytest.raises(ConfigNotFoundError):
            load_id_map(tmp_path / "missing.yaml")

    def test_invalid_yaml_raises(self, tmp_path: Path) -> None:
        """Invalid YAML raises ConfigError."""
        f = tmp_path / "bad.yaml"
        f.write_text("{{invalid")
        with pytest.raises(ConfigError):
            load_id_map(f)

    def test_no_class_field(self, tmp_path: Path) -> None:
        """Entry without class field has class as None."""
        f = tmp_path / "id_map.yaml"
        f.write_text(yaml.dump({"mappings": [{"file": "mod.py", "id": "my.mod"}]}))
        result = load_id_map(f)
        assert result["mod.py"]["class"] is None
