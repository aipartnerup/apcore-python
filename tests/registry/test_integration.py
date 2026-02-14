"""Integration tests for the registry system and public API validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml
from pydantic import BaseModel

from apcore.errors import CircularDependencyError


# ---------------------------------------------------------------------------
# Module file helpers
# ---------------------------------------------------------------------------


def _write_module_file(
    directory: Path,
    filename: str,
    class_name: str,
    description: str = "A test module",
    tags: list[str] | None = None,
) -> Path:
    """Write a valid module .py file to a directory."""
    tags_str = repr(tags or [])
    content = f"""from pydantic import BaseModel

class TestInput(BaseModel):
    value: str

class TestOutput(BaseModel):
    result: str

class {class_name}:
    input_schema = TestInput
    output_schema = TestOutput
    description = "{description}"
    tags = {tags_str}

    def execute(self, inputs, context=None):
        return {{"result": inputs["value"]}}
"""
    file_path = directory / filename
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)
    return file_path


def _write_meta_yaml(directory: Path, module_stem: str, meta: dict[str, Any]) -> Path:
    """Write a companion _meta.yaml file."""
    meta_path = directory / f"{module_stem}_meta.yaml"
    meta_path.write_text(yaml.dump(meta, default_flow_style=False))
    return meta_path


# =========================================================================
# Public API Import Tests
# =========================================================================


class TestPublicAPI:
    def test_import_registry(self) -> None:
        """from apcore.registry import Registry works."""
        from apcore.registry import Registry

        assert Registry is not None
        assert callable(Registry)

    def test_import_module_descriptor(self) -> None:
        """from apcore.registry import ModuleDescriptor works."""
        from apcore.registry import ModuleDescriptor

        assert ModuleDescriptor is not None

    def test_import_validate_module(self) -> None:
        """from apcore.registry import validate_module works."""
        from apcore.registry import validate_module

        assert callable(validate_module)

    def test_import_scan_extensions(self) -> None:
        """from apcore.registry import scan_extensions works."""
        from apcore.registry import scan_extensions

        assert callable(scan_extensions)

    def test_import_resolve_entry_point(self) -> None:
        """from apcore.registry import resolve_entry_point works."""
        from apcore.registry import resolve_entry_point

        assert callable(resolve_entry_point)

    def test_import_resolve_dependencies(self) -> None:
        """from apcore.registry import resolve_dependencies works."""
        from apcore.registry import resolve_dependencies

        assert callable(resolve_dependencies)

    def test_import_load_metadata_and_id_map(self) -> None:
        """from apcore.registry import load_metadata, load_id_map works."""
        from apcore.registry import load_id_map, load_metadata

        assert callable(load_metadata)
        assert callable(load_id_map)

    def test_import_scan_multi_root_and_snake_to_pascal(self) -> None:
        """from apcore.registry import scan_multi_root, snake_to_pascal works."""
        from apcore.registry import scan_multi_root, snake_to_pascal

        assert callable(scan_multi_root)
        assert callable(snake_to_pascal)

    def test_import_discovered_module_and_dependency_info(self) -> None:
        """from apcore.registry import DiscoveredModule, DependencyInfo works."""
        from apcore.registry import DependencyInfo, DiscoveredModule

        assert DependencyInfo is not None
        assert DiscoveredModule is not None

    def test_all_public_names_in_all(self) -> None:
        """__all__ contains all expected public names."""
        import apcore.registry

        expected = {
            "Registry",
            "ModuleDescriptor",
            "DiscoveredModule",
            "DependencyInfo",
            "validate_module",
            "scan_extensions",
            "scan_multi_root",
            "resolve_entry_point",
            "snake_to_pascal",
            "resolve_dependencies",
            "load_metadata",
            "load_id_map",
        }
        assert expected.issubset(set(apcore.registry.__all__))


# =========================================================================
# Integration Tests: End-to-End Flows
# =========================================================================


class TestEndToEnd:
    def test_discover_get_export_pipeline(self, tmp_path: Path) -> None:
        """discover() -> get() -> export_schema() end-to-end pipeline."""
        from apcore.registry.registry import Registry
        from apcore.registry.schema_export import export_schema

        ext = tmp_path / "extensions"
        ext.mkdir()
        _write_module_file(ext, "hello.py", "HelloModule", "Hello module")

        reg = Registry(extensions_dir=str(ext))
        count = reg.discover()
        assert count == 1

        mod = reg.get("hello")
        assert mod is not None

        json_str = export_schema(reg, "hello", format="json")
        parsed = json.loads(json_str)
        assert "input_schema" in parsed
        assert "output_schema" in parsed
        assert parsed["module_id"] == "hello"

    def test_register_unregister_callbacks(self) -> None:
        """register() -> on callback fired -> unregister() -> on callback fired."""
        from apcore.registry.registry import Registry

        reg = Registry()

        class TestMod:
            input_schema = type("I", (BaseModel,), {"__annotations__": {"value": str}})
            output_schema = type("O", (BaseModel,), {"__annotations__": {"result": str}})
            description = "test"

            def execute(self, inputs: dict[str, Any], context: Any = None) -> dict[str, Any]:
                return {"result": "ok"}

        register_mock = MagicMock()
        unregister_mock = MagicMock()
        reg.on("register", register_mock)
        reg.on("unregister", unregister_mock)

        mod = TestMod()
        reg.register("test.mod", mod)
        register_mock.assert_called_once_with("test.mod", mod)

        assert reg.unregister("test.mod") is True
        unregister_mock.assert_called_once_with("test.mod", mod)

    def test_circular_dependency_detection(self, tmp_path: Path) -> None:
        """Circular dependency across 3 modules raises CircularDependencyError."""
        from apcore.registry.registry import Registry

        ext = tmp_path / "extensions"
        ext.mkdir()
        _write_module_file(ext, "mod_a.py", "ModAModule", "Module A")
        _write_module_file(ext, "mod_b.py", "ModBModule", "Module B")
        _write_module_file(ext, "mod_c.py", "ModCModule", "Module C")
        _write_meta_yaml(ext, "mod_a", {"dependencies": [{"module_id": "mod_b"}]})
        _write_meta_yaml(ext, "mod_b", {"dependencies": [{"module_id": "mod_c"}]})
        _write_meta_yaml(ext, "mod_c", {"dependencies": [{"module_id": "mod_a"}]})

        reg = Registry(extensions_dir=str(ext))
        with pytest.raises(CircularDependencyError) as exc_info:
            reg.discover()
        path = exc_info.value.details["cycle_path"]
        assert "mod_a" in path
        assert "mod_b" in path
        assert "mod_c" in path
        assert reg.count == 0

    def test_multi_root_namespace_isolation(self, tmp_path: Path) -> None:
        """Multi-root discovery with namespace isolation."""
        from apcore.registry.registry import Registry

        root_a = tmp_path / "root_a"
        root_a.mkdir()
        root_b = tmp_path / "root_b"
        root_b.mkdir()
        _write_module_file(root_a, "process.py", "ProcessModuleA", "Process A")
        _write_module_file(root_b, "process.py", "ProcessModuleB", "Process B")

        reg = Registry(
            extensions_dirs=[
                {"root": str(root_a), "namespace": "alpha"},
                {"root": str(root_b), "namespace": "beta"},
            ]
        )
        count = reg.discover()
        assert count == 2
        assert reg.has("alpha.process")
        assert reg.has("beta.process")
        assert reg.get("alpha.process") is not reg.get("beta.process")

    def test_metadata_yaml_overrides(self, tmp_path: Path) -> None:
        """Metadata YAML overrides code-level description and tags."""
        from apcore.registry.registry import Registry

        ext = tmp_path / "extensions"
        ext.mkdir()
        _write_module_file(ext, "mymod.py", "MyModule", "Code description", tags=["code-tag"])
        _write_meta_yaml(
            ext,
            "mymod",
            {
                "description": "YAML description",
                "tags": ["yaml-tag"],
            },
        )

        reg = Registry(extensions_dir=str(ext))
        reg.discover()

        defn = reg.get_definition("mymod")
        assert defn is not None
        assert defn.description == "YAML description"
        assert "yaml-tag" in defn.tags

    def test_mixed_valid_invalid_modules(self, tmp_path: Path) -> None:
        """discover() with mixed valid/invalid modules registers only valid ones."""
        from apcore.registry.registry import Registry

        ext = tmp_path / "extensions"
        ext.mkdir()
        _write_module_file(ext, "valid_mod.py", "ValidModule", "Valid")
        # broken module - class without required attributes
        (ext / "broken_mod.py").write_text("class BrokenModule:\n    pass\n")
        # empty module - no module class at all
        (ext / "empty_mod.py").write_text("x = 42\n")

        reg = Registry(extensions_dir=str(ext))
        count = reg.discover()
        assert count == 1
        assert reg.has("valid_mod")
        assert not reg.has("broken_mod")
        assert not reg.has("empty_mod")

    def test_conftest_fixtures_smoke(self, registry: Any, sample_module_class: type) -> None:
        """Smoke test: conftest fixtures work correctly."""
        from apcore.registry.registry import Registry

        assert isinstance(registry, Registry)
        assert hasattr(sample_module_class, "input_schema")
        assert hasattr(sample_module_class, "output_schema")
        assert hasattr(sample_module_class, "description")
