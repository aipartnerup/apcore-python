"""Tests for the Registry class."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml
from pydantic import BaseModel

from apcore.errors import (
    CircularDependencyError,
    InvalidInputError,
    ModuleNotFoundError,
)
from apcore.registry.registry import Registry
from apcore.registry.types import ModuleDescriptor


# ---------------------------------------------------------------------------
# Helper schemas and modules for tests
# ---------------------------------------------------------------------------


class _TestInput(BaseModel):
    value: str


class _TestOutput(BaseModel):
    result: str


class _ValidModule:
    """A valid duck-typed test module."""

    input_schema = _TestInput
    output_schema = _TestOutput
    description = "A valid test module"
    tags = ["test", "sample"]
    version = "2.0.0"

    def execute(self, inputs: dict[str, Any], context: Any = None) -> dict[str, Any]:
        return {"result": inputs["value"]}


class _ValidModuleB:
    """Another valid test module."""

    input_schema = _TestInput
    output_schema = _TestOutput
    description = "Another valid test module"
    tags = ["test"]

    def execute(self, inputs: dict[str, Any], context: Any = None) -> dict[str, Any]:
        return {"result": "b"}


class _ModuleWithOnLoad:
    """Module with on_load lifecycle hook."""

    input_schema = _TestInput
    output_schema = _TestOutput
    description = "Module with on_load"

    def __init__(self) -> None:
        self.load_called = False

    def execute(self, inputs: dict[str, Any], context: Any = None) -> dict[str, Any]:
        return {"result": "ok"}

    def on_load(self) -> None:
        self.load_called = True


class _ModuleWithFailingOnLoad:
    """Module whose on_load raises."""

    input_schema = _TestInput
    output_schema = _TestOutput
    description = "Module with failing on_load"

    def execute(self, inputs: dict[str, Any], context: Any = None) -> dict[str, Any]:
        return {"result": "fail"}

    def on_load(self) -> None:
        raise RuntimeError("on_load failed")


class _ModuleWithOnUnload:
    """Module with on_unload lifecycle hook."""

    input_schema = _TestInput
    output_schema = _TestOutput
    description = "Module with on_unload"

    def __init__(self) -> None:
        self.unload_called = False

    def execute(self, inputs: dict[str, Any], context: Any = None) -> dict[str, Any]:
        return {"result": "ok"}

    def on_unload(self) -> None:
        self.unload_called = True


class _ModuleWithFailingOnUnload:
    """Module whose on_unload raises."""

    input_schema = _TestInput
    output_schema = _TestOutput
    description = "Module with failing on_unload"

    def execute(self, inputs: dict[str, Any], context: Any = None) -> dict[str, Any]:
        return {"result": "fail"}

    def on_unload(self) -> None:
        raise RuntimeError("on_unload failed")


# ===== Constructor =====


class TestConstructor:
    def test_no_args_defaults(self) -> None:
        """Registry() with no args defaults extension_roots to ['./extensions']."""
        reg = Registry()
        assert len(reg._extension_roots) == 1
        assert reg._extension_roots[0]["root"] == "./extensions"

    def test_extensions_dir_param(self) -> None:
        """Registry(extensions_dir='custom') uses that as single root."""
        reg = Registry(extensions_dir="custom")
        assert reg._extension_roots == [{"root": "custom"}]

    def test_extensions_dirs_param(self) -> None:
        """Registry(extensions_dirs=[...]) uses multi-root config."""
        reg = Registry(extensions_dirs=["dir_a", {"root": "dir_b", "namespace": "ns"}])
        assert len(reg._extension_roots) == 2
        assert reg._extension_roots[0] == {"root": "dir_a"}
        assert reg._extension_roots[1] == {"root": "dir_b", "namespace": "ns"}

    def test_both_extensions_dir_and_dirs_raises(self) -> None:
        """Cannot specify both extensions_dir and extensions_dirs."""
        with pytest.raises(InvalidInputError, match="Cannot specify both"):
            Registry(extensions_dir="x", extensions_dirs=["y"])

    def test_config_extensions_root(self) -> None:
        """Config with extensions.root sets the extension root."""
        from apcore.config import Config

        config = Config(data={"extensions": {"root": "/custom/path"}})
        reg = Registry(config=config)
        assert reg._extension_roots == [{"root": "/custom/path"}]

    def test_individual_param_overrides_config(self) -> None:
        """extensions_dir overrides config.extensions.root."""
        from apcore.config import Config

        config = Config(data={"extensions": {"root": "/from_config"}})
        reg = Registry(config=config, extensions_dir="/override")
        assert reg._extension_roots == [{"root": "/override"}]


# ===== register() =====


class TestRegister:
    def test_register_stores_module(self) -> None:
        """register() stores module, retrievable via get()."""
        reg = Registry()
        mod = _ValidModule()
        reg.register("test.module", mod)
        assert reg.get("test.module") is mod

    def test_register_duplicate_raises(self) -> None:
        """Registering same ID twice raises InvalidInputError."""
        reg = Registry()
        reg.register("test.module", _ValidModule())
        with pytest.raises(InvalidInputError, match="already exists"):
            reg.register("test.module", _ValidModuleB())

    def test_register_calls_on_load(self) -> None:
        """register() calls on_load() if the method exists."""
        reg = Registry()
        mod = _ModuleWithOnLoad()
        reg.register("test.module", mod)
        assert mod.load_called is True

    def test_register_on_load_failure_removes_module(self) -> None:
        """on_load() failure removes module and propagates exception."""
        reg = Registry()
        with pytest.raises(RuntimeError, match="on_load failed"):
            reg.register("test.module", _ModuleWithFailingOnLoad())
        assert reg.get("test.module") is None

    def test_register_triggers_callback(self) -> None:
        """register() triggers 'register' event callbacks."""
        reg = Registry()
        calls: list[tuple[str, Any]] = []
        reg.on("register", lambda mid, mod: calls.append((mid, mod)))
        mod = _ValidModule()
        reg.register("test.module", mod)
        assert len(calls) == 1
        assert calls[0] == ("test.module", mod)

    def test_register_without_on_load(self) -> None:
        """Module without on_load() method is registered fine."""
        reg = Registry()
        mod = _ValidModule()
        assert not hasattr(mod, "on_load")
        reg.register("test.module", mod)
        assert reg.get("test.module") is mod

    def test_register_empty_id_raises(self) -> None:
        """register('') raises InvalidInputError."""
        reg = Registry()
        with pytest.raises(InvalidInputError, match="non-empty"):
            reg.register("", _ValidModule())


# ===== unregister() =====


class TestUnregister:
    def test_unregister_existing(self) -> None:
        """Unregister existing module returns True, module gone."""
        reg = Registry()
        reg.register("test.module", _ValidModule())
        assert reg.unregister("test.module") is True
        assert reg.get("test.module") is None

    def test_unregister_nonexistent(self) -> None:
        """Unregister non-existent ID returns False."""
        reg = Registry()
        assert reg.unregister("nonexistent") is False

    def test_unregister_calls_on_unload(self) -> None:
        """Unregister calls on_unload() if the method exists."""
        reg = Registry()
        mod = _ModuleWithOnUnload()
        reg.register("test.module", mod)
        reg.unregister("test.module")
        assert mod.unload_called is True

    def test_unregister_on_unload_failure_still_unregisters(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """on_unload() failure is logged, but module is still removed."""
        reg = Registry()
        mod = _ModuleWithFailingOnUnload()
        reg.register("test.module", mod)
        with caplog.at_level(logging.ERROR):
            result = reg.unregister("test.module")
        assert result is True
        assert reg.get("test.module") is None
        assert "on_unload" in caplog.text

    def test_unregister_triggers_callback(self) -> None:
        """Unregister triggers 'unregister' event callbacks."""
        reg = Registry()
        calls: list[tuple[str, Any]] = []
        reg.on("unregister", lambda mid, mod: calls.append((mid, mod)))
        mod = _ValidModule()
        reg.register("test.module", mod)
        reg.unregister("test.module")
        assert len(calls) == 1
        assert calls[0][0] == "test.module"

    def test_unregister_clears_schema_cache(self) -> None:
        """Unregister clears schema cache for the module."""
        reg = Registry()
        reg.register("test.module", _ValidModule())
        reg._schema_cache["test.module"] = {"cached": True}
        reg.unregister("test.module")
        assert "test.module" not in reg._schema_cache


# ===== Query Methods =====


class TestQueryMethods:
    def test_get_empty_id_raises(self) -> None:
        """get('') raises ModuleNotFoundError."""
        reg = Registry()
        with pytest.raises(ModuleNotFoundError):
            reg.get("")

    def test_get_nonexistent_returns_none(self) -> None:
        """get() for missing ID returns None."""
        reg = Registry()
        assert reg.get("nonexistent") is None

    def test_get_existing(self) -> None:
        """get() returns the registered module."""
        reg = Registry()
        mod = _ValidModule()
        reg.register("test.module", mod)
        assert reg.get("test.module") is mod

    def test_has(self) -> None:
        """has() returns True/False based on registration."""
        reg = Registry()
        reg.register("test.module", _ValidModule())
        assert reg.has("test.module") is True
        assert reg.has("missing") is False

    def test_list_no_filters(self) -> None:
        """list() with no filters returns sorted list of all IDs."""
        reg = Registry()
        reg.register("c.module", _ValidModule())
        reg.register("a.module", _ValidModuleB())
        reg.register("b.module", _ValidModule())
        assert reg.list() == ["a.module", "b.module", "c.module"]

    def test_list_prefix_filter(self) -> None:
        """list(prefix='foo') returns only IDs starting with 'foo'."""
        reg = Registry()
        reg.register("foo.bar", _ValidModule())
        reg.register("foo.baz", _ValidModuleB())
        reg.register("other.thing", _ValidModule())
        assert reg.list(prefix="foo") == ["foo.bar", "foo.baz"]

    def test_list_tags_filter(self) -> None:
        """list(tags=[...]) returns modules with ALL specified tags."""
        reg = Registry()
        reg.register("mod.a", _ValidModule())  # tags=["test", "sample"]
        reg.register("mod.b", _ValidModuleB())  # tags=["test"]
        assert reg.list(tags=["test", "sample"]) == ["mod.a"]
        assert reg.list(tags=["test"]) == ["mod.a", "mod.b"]

    def test_list_prefix_and_tags(self) -> None:
        """list() with both prefix and tags applies both filters."""
        reg = Registry()
        reg.register("foo.a", _ValidModule())  # tags=["test", "sample"]
        reg.register("foo.b", _ValidModuleB())  # tags=["test"]
        reg.register("bar.a", _ValidModule())  # tags=["test", "sample"]
        result = reg.list(prefix="foo", tags=["test", "sample"])
        assert result == ["foo.a"]

    def test_iter(self) -> None:
        """iter() returns (id, module) tuples."""
        reg = Registry()
        mod_a = _ValidModule()
        mod_b = _ValidModuleB()
        reg.register("a.mod", mod_a)
        reg.register("b.mod", mod_b)
        items = list(reg.iter())
        assert len(items) == 2
        ids = {i[0] for i in items}
        assert ids == {"a.mod", "b.mod"}

    def test_count(self) -> None:
        """count property returns number of registered modules."""
        reg = Registry()
        assert reg.count == 0
        reg.register("a", _ValidModule())
        reg.register("b", _ValidModuleB())
        assert reg.count == 2

    def test_module_ids(self) -> None:
        """module_ids returns sorted list of IDs."""
        reg = Registry()
        reg.register("z.mod", _ValidModule())
        reg.register("a.mod", _ValidModuleB())
        assert reg.module_ids == ["a.mod", "z.mod"]


# ===== get_definition() =====


class TestGetDefinition:
    def test_existing_module(self) -> None:
        """get_definition() returns ModuleDescriptor for registered module."""
        reg = Registry()
        reg.register("test.module", _ValidModule())
        defn = reg.get_definition("test.module")
        assert defn is not None
        assert isinstance(defn, ModuleDescriptor)
        assert defn.module_id == "test.module"
        assert defn.description == "A valid test module"
        assert defn.version == "2.0.0"
        assert defn.tags == ["test", "sample"]
        assert "properties" in defn.input_schema
        assert "properties" in defn.output_schema

    def test_nonexistent_returns_none(self) -> None:
        """get_definition() for missing module returns None."""
        reg = Registry()
        assert reg.get_definition("missing") is None


# ===== Event Callbacks =====


class TestEventCallbacks:
    def test_on_register_callback(self) -> None:
        """on('register', cb) calls cb when module is registered."""
        reg = Registry()
        mock_cb = MagicMock()
        reg.on("register", mock_cb)
        mod = _ValidModule()
        reg.register("test.module", mod)
        mock_cb.assert_called_once_with("test.module", mod)

    def test_on_unregister_callback(self) -> None:
        """on('unregister', cb) calls cb when module is unregistered."""
        reg = Registry()
        mock_cb = MagicMock()
        reg.on("unregister", mock_cb)
        mod = _ValidModule()
        reg.register("test.module", mod)
        reg.unregister("test.module")
        mock_cb.assert_called_once_with("test.module", mod)

    def test_invalid_event_raises(self) -> None:
        """on('invalid', cb) raises InvalidInputError."""
        reg = Registry()
        with pytest.raises(InvalidInputError, match="Invalid event"):
            reg.on("invalid", lambda mid, mod: None)

    def test_callback_error_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """Callback that raises -> error logged, registration completes."""
        reg = Registry()

        def bad_cb(mid: str, mod: Any) -> None:
            raise ValueError("callback boom")

        reg.on("register", bad_cb)
        with caplog.at_level(logging.ERROR):
            reg.register("test.module", _ValidModule())
        assert reg.has("test.module")
        assert "callback boom" in caplog.text

    def test_multiple_callbacks(self) -> None:
        """Multiple callbacks all called in order."""
        reg = Registry()
        order: list[int] = []
        reg.on("register", lambda mid, mod: order.append(1))
        reg.on("register", lambda mid, mod: order.append(2))
        reg.register("test.module", _ValidModule())
        assert order == [1, 2]


# ===== discover() =====


def _write_module_file(path: Path, class_name: str, description: str) -> None:
    """Helper: write a valid module .py file."""
    content = f"""from pydantic import BaseModel

class InputModel(BaseModel):
    value: str

class OutputModel(BaseModel):
    result: str

class {class_name}:
    input_schema = InputModel
    output_schema = OutputModel
    description = "{description}"
    tags = ["auto"]

    def execute(self, inputs, context=None):
        return {{"result": inputs["value"]}}
"""
    path.write_text(content)


class TestDiscover:
    def test_discover_valid_modules(self, tmp_path: Path) -> None:
        """discover() finds valid modules and returns count."""
        ext = tmp_path / "extensions"
        ext.mkdir()
        _write_module_file(ext / "hello.py", "HelloModule", "Hello module")
        _write_module_file(ext / "world.py", "WorldModule", "World module")
        reg = Registry(extensions_dir=str(ext))
        count = reg.discover()
        assert count == 2
        assert reg.has("hello")
        assert reg.has("world")

    def test_discover_empty_dir(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """discover() with empty dir returns 0."""
        ext = tmp_path / "extensions"
        ext.mkdir()
        reg = Registry(extensions_dir=str(ext))
        with caplog.at_level(logging.WARNING):
            count = reg.discover()
        assert count == 0

    def test_discover_skips_invalid_modules(self, tmp_path: Path) -> None:
        """discover() skips modules that fail validation."""
        ext = tmp_path / "extensions"
        ext.mkdir()
        _write_module_file(ext / "valid.py", "ValidModule", "Valid module")
        # Write an invalid module (no execute method, no schemas)
        (ext / "invalid.py").write_text("class InvalidModule:\n    pass\n")
        reg = Registry(extensions_dir=str(ext))
        count = reg.discover()
        assert count == 1
        assert reg.has("valid")
        assert not reg.has("invalid")

    def test_discover_with_metadata(self, tmp_path: Path) -> None:
        """discover() merges companion _meta.yaml with code attributes."""
        ext = tmp_path / "extensions"
        ext.mkdir()
        _write_module_file(ext / "mymod.py", "MyModModule", "Code description")
        meta = ext / "mymod_meta.yaml"
        meta.write_text(
            yaml.dump({"description": "YAML description", "tags": ["yaml_tag"]})
        )
        reg = Registry(extensions_dir=str(ext))
        reg.discover()
        # Check that the merged metadata reflects YAML overrides
        meta_info = reg._module_meta.get("mymod", {})
        assert meta_info.get("description") == "YAML description"
        assert meta_info.get("tags") == ["yaml_tag"]

    def test_discover_on_load_called(self, tmp_path: Path) -> None:
        """discover() calls on_load() for each module."""
        ext = tmp_path / "extensions"
        ext.mkdir()
        (ext / "loadable.py").write_text(
            """
from pydantic import BaseModel

class Input(BaseModel):
    value: str

class Output(BaseModel):
    result: str

_load_called = False

class LoadableModule:
    input_schema = Input
    output_schema = Output
    description = "Loadable"

    def execute(self, inputs, context=None):
        return {"result": "ok"}

    def on_load(self):
        global _load_called
        _load_called = True
"""
        )
        reg = Registry(extensions_dir=str(ext))
        reg.discover()
        assert reg.has("loadable")

    def test_discover_on_load_failure_skips(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """discover() skips module whose on_load() fails."""
        ext = tmp_path / "extensions"
        ext.mkdir()
        _write_module_file(ext / "good.py", "GoodModule", "Good")
        (ext / "bad_load.py").write_text(
            """
from pydantic import BaseModel

class Input(BaseModel):
    value: str

class Output(BaseModel):
    result: str

class BadLoadModule:
    input_schema = Input
    output_schema = Output
    description = "Bad loader"

    def execute(self, inputs, context=None):
        return {"result": "ok"}

    def on_load(self):
        raise RuntimeError("on_load exploded")
"""
        )
        reg = Registry(extensions_dir=str(ext))
        with caplog.at_level(logging.ERROR):
            count = reg.discover()
        # good.py loaded, bad_load.py skipped
        assert count == 1
        assert reg.has("good")
        assert not reg.has("bad_load")
        assert "on_load" in caplog.text

    def test_discover_with_dependencies(self, tmp_path: Path) -> None:
        """discover() loads modules respecting dependency order."""
        ext = tmp_path / "extensions"
        ext.mkdir()
        # Module A depends on B
        _write_module_file(ext / "mod_a.py", "ModAModule", "Module A")
        (ext / "mod_a_meta.yaml").write_text(
            yaml.dump(
                {
                    "dependencies": [{"module_id": "mod_b"}],
                }
            )
        )
        _write_module_file(ext / "mod_b.py", "ModBModule", "Module B")
        reg = Registry(extensions_dir=str(ext))
        count = reg.discover()
        assert count == 2
        assert reg.has("mod_a")
        assert reg.has("mod_b")

    def test_discover_circular_deps_raises(self, tmp_path: Path) -> None:
        """discover() with circular deps raises CircularDependencyError."""
        ext = tmp_path / "extensions"
        ext.mkdir()
        _write_module_file(ext / "cycle_a.py", "CycleAModule", "Cycle A")
        (ext / "cycle_a_meta.yaml").write_text(
            yaml.dump(
                {
                    "dependencies": [{"module_id": "cycle_b"}],
                }
            )
        )
        _write_module_file(ext / "cycle_b.py", "CycleBModule", "Cycle B")
        (ext / "cycle_b_meta.yaml").write_text(
            yaml.dump(
                {
                    "dependencies": [{"module_id": "cycle_a"}],
                }
            )
        )
        reg = Registry(extensions_dir=str(ext))
        with pytest.raises(CircularDependencyError):
            reg.discover()
        # No modules registered when circular deps detected
        assert reg.count == 0

    def test_discover_nonexistent_root_raises(self, tmp_path: Path) -> None:
        """discover() with nonexistent extension root raises ConfigNotFoundError."""
        from apcore.errors import ConfigNotFoundError

        reg = Registry(extensions_dir=str(tmp_path / "nonexistent"))
        with pytest.raises(ConfigNotFoundError):
            reg.discover()

    def test_discover_multi_root(self, tmp_path: Path) -> None:
        """discover() with multiple extension roots registers from all roots."""
        root_a = tmp_path / "ext_a"
        root_a.mkdir()
        root_b = tmp_path / "ext_b"
        root_b.mkdir()
        _write_module_file(root_a / "mod_a.py", "ModAModule", "Module A")
        _write_module_file(root_b / "mod_b.py", "ModBModule", "Module B")
        reg = Registry(
            extensions_dirs=[
                {"root": str(root_a), "namespace": "ns_a"},
                {"root": str(root_b), "namespace": "ns_b"},
            ]
        )
        count = reg.discover()
        assert count == 2
        assert reg.has("ns_a.mod_a")
        assert reg.has("ns_b.mod_b")

    def test_discover_get_definition_with_metadata(self, tmp_path: Path) -> None:
        """get_definition() after discover reflects merged metadata."""
        ext = tmp_path / "extensions"
        ext.mkdir()
        _write_module_file(ext / "defmod.py", "DefModModule", "Code desc")
        (ext / "defmod_meta.yaml").write_text(
            yaml.dump(
                {
                    "description": "YAML desc",
                    "version": "3.0.0",
                    "tags": ["yaml_tag"],
                }
            )
        )
        reg = Registry(extensions_dir=str(ext))
        reg.discover()
        defn = reg.get_definition("defmod")
        assert defn is not None
        assert defn.description == "YAML desc"
        assert defn.version == "3.0.0"
        assert defn.tags == ["yaml_tag"]


# ===== clear_cache() =====


class TestClearCache:
    def test_clear_cache(self) -> None:
        """clear_cache() empties _schema_cache."""
        reg = Registry()
        reg._schema_cache["a"] = {"cached": True}
        reg._schema_cache["b"] = {"cached": True}
        reg.clear_cache()
        assert len(reg._schema_cache) == 0


# ===== Thread Safety =====


class TestThreadSafety:
    def test_concurrent_get(self) -> None:
        """Concurrent get() calls don't raise."""
        reg = Registry()
        reg.register("test.module", _ValidModule())
        errors: list[Exception] = []

        def reader() -> None:
            try:
                for _ in range(100):
                    reg.get("test.module")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []

    def test_concurrent_register_no_corruption(self) -> None:
        """Concurrent register() of same ID: one succeeds, others raise."""
        reg = Registry()
        successes: list[str] = []
        failures: list[str] = []

        def registerer(idx: int) -> None:
            try:
                reg.register("shared.module", _ValidModule())
                successes.append(f"thread-{idx}")
            except InvalidInputError:
                failures.append(f"thread-{idx}")

        threads = [threading.Thread(target=registerer, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(successes) == 1
        assert len(failures) == 9

    def test_list_returns_snapshot(self) -> None:
        """list() returns snapshot not affected by concurrent mutations."""
        reg = Registry()
        for i in range(10):
            reg.register(f"mod.{i:02d}", _ValidModule())
        snapshot = reg.list()
        assert len(snapshot) == 10
        # Unregister in another thread
        reg.unregister("mod.05")
        # Original snapshot is still 10 items
        assert len(snapshot) == 10

    def test_concurrent_has_and_register(self) -> None:
        """Concurrent has() and register() should not raise."""
        reg = Registry()
        errors: list[Exception] = []

        def registerer() -> None:
            try:
                for i in range(50):
                    mid = f"thread.mod.{threading.current_thread().name}.{i}"
                    reg.register(mid, _ValidModule())
            except Exception as e:
                errors.append(e)

        def reader() -> None:
            try:
                for _ in range(200):
                    reg.has("thread.mod.0")
                    reg.count  # noqa: B018
                    reg.module_ids
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=registerer, name=f"w-{i}") for i in range(3)]
        threads += [threading.Thread(target=reader) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []

    def test_concurrent_iter_and_unregister(self) -> None:
        """iter() snapshot is safe during concurrent unregister()."""
        reg = Registry()
        for i in range(20):
            reg.register(f"mod.{i:02d}", _ValidModule())
        errors: list[Exception] = []

        def iterator() -> None:
            try:
                for _ in range(50):
                    list(reg.iter())
            except Exception as e:
                errors.append(e)

        def unregisterer() -> None:
            try:
                for i in range(20):
                    reg.unregister(f"mod.{i:02d}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=iterator) for _ in range(5)]
        threads.append(threading.Thread(target=unregisterer))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
