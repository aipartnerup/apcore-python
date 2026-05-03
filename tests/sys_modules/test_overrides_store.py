"""Tests for the pluggable ``OverridesStore`` interface (cross-language alignment).

Mirrors apcore-typescript's ``OverridesStore`` / ``InMemoryOverridesStore``
/ ``FileOverridesStore`` contract. Validates:

1. ``InMemoryOverridesStore`` and ``FileOverridesStore`` both satisfy the
   :class:`OverridesStore` Protocol.
2. ``save()`` persists overrides; subsequent ``load()`` returns the same map.
3. ``register_sys_modules`` accepts an ``overrides_store=`` kwarg, applies
   loaded overrides to ``Config`` / ``ToggleState`` on startup, and persists
   subsequent ``update_config`` / ``toggle_feature`` calls back through the
   store.
4. The legacy ``overrides_path=`` kwarg path on ``UpdateConfigModule`` /
   ``ToggleFeatureModule`` continues to work (backwards-compat shim).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from apcore.config import Config
from apcore.events.emitter import EventEmitter
from apcore.executor import Executor
from apcore.registry.registry import Registry
from apcore.sys_modules.audit import InMemoryAuditStore
from apcore.sys_modules.control import (
    ToggleFeatureModule,
    ToggleState,
    UpdateConfigModule,
)
from apcore.sys_modules.overrides import (
    FileOverridesStore,
    InMemoryOverridesStore,
    OverridesStore,
)
from apcore.sys_modules.registration import register_sys_modules


# ---------------------------------------------------------------------------
# Protocol satisfaction
# ---------------------------------------------------------------------------


class TestProtocol:
    def test_in_memory_satisfies_protocol(self) -> None:
        store = InMemoryOverridesStore()
        assert isinstance(store, OverridesStore)

    def test_file_satisfies_protocol(self, tmp_path: Path) -> None:
        store = FileOverridesStore(tmp_path / "overrides.yaml")
        assert isinstance(store, OverridesStore)


# ---------------------------------------------------------------------------
# InMemoryOverridesStore
# ---------------------------------------------------------------------------


class TestInMemoryStore:
    def test_load_returns_empty_dict_when_unset(self) -> None:
        store = InMemoryOverridesStore()
        assert store.load() == {}

    def test_save_then_load_returns_same_mapping(self) -> None:
        store = InMemoryOverridesStore()
        store.save({"a.b": 1, "toggle.x": True})
        assert store.load() == {"a.b": 1, "toggle.x": True}

    def test_load_returns_a_copy_so_callers_cannot_mutate_internal_state(self) -> None:
        store = InMemoryOverridesStore()
        store.save({"a.b": 1})
        snapshot = store.load()
        snapshot["a.b"] = 999  # type: ignore[index]
        assert store.load()["a.b"] == 1


# ---------------------------------------------------------------------------
# FileOverridesStore
# ---------------------------------------------------------------------------


class TestFileStore:
    def test_load_missing_file_returns_empty_dict(self, tmp_path: Path) -> None:
        store = FileOverridesStore(tmp_path / "missing.yaml")
        assert store.load() == {}

    def test_save_then_load_yaml_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "overrides.yaml"
        store = FileOverridesStore(path)
        store.save({"executor.default_timeout": 60000, "toggle.foo": False})
        assert path.exists()
        loaded = store.load()
        assert loaded == {"executor.default_timeout": 60000, "toggle.foo": False}

    def test_save_uses_yaml_format(self, tmp_path: Path) -> None:
        path = tmp_path / "overrides.yaml"
        store = FileOverridesStore(path)
        store.save({"k": "v"})
        # File must be parseable as YAML (which is also valid JSON for this case).
        with path.open() as f:
            assert yaml.safe_load(f) == {"k": "v"}

    def test_save_overwrites_existing_keys(self, tmp_path: Path) -> None:
        path = tmp_path / "overrides.yaml"
        store = FileOverridesStore(path)
        store.save({"k": 1})
        store.save({"k": 2, "j": 3})
        assert store.load() == {"k": 2, "j": 3}

    def test_load_falls_back_to_json_when_yaml_unavailable(self, tmp_path: Path) -> None:
        """If the file contains valid JSON, ``load()`` returns it.

        YAML is a superset of JSON, so a ``.yaml`` file containing JSON must
        round-trip correctly.
        """
        path = tmp_path / "overrides.yaml"
        path.write_text(json.dumps({"a.b": 1}), encoding="utf-8")
        store = FileOverridesStore(path)
        assert store.load() == {"a.b": 1}


# ---------------------------------------------------------------------------
# Wiring through register_sys_modules
# ---------------------------------------------------------------------------


def _enable_sys_modules(config: Config) -> None:
    config.set("sys_modules.enabled", True)
    config.set("sys_modules.events.enabled", True)


class TestRegisterSysModulesWithStore:
    def test_overrides_store_kwarg_loads_existing_overrides_on_startup(self) -> None:
        """Pre-existing overrides in the store are applied to Config + ToggleState."""
        store = InMemoryOverridesStore()
        store.save(
            {
                "executor.default_timeout": 12345,
                "toggle.system.health.module": False,
            }
        )

        config = Config.from_defaults()
        _enable_sys_modules(config)
        registry = Registry()
        executor = Executor(registry=registry)

        ctx = register_sys_modules(
            registry=registry,
            executor=executor,
            config=config,
            overrides_store=store,
        )

        assert config.get("executor.default_timeout") == 12345
        # The toggle override must have been applied to the live ToggleState
        # owned by the toggle_feature sys-module.
        toggle_module = registry.get("system.control.toggle_feature")
        assert toggle_module is not None
        assert toggle_module._toggle_state.is_disabled("system.health.module") is True
        # Returned context dict shape — registration succeeded
        assert "event_emitter" in ctx

    def test_update_config_persists_through_store(self) -> None:
        """A successful update_config call writes back to the store."""
        store = InMemoryOverridesStore()
        config = Config.from_defaults()
        _enable_sys_modules(config)
        registry = Registry()
        executor = Executor(registry=registry)

        register_sys_modules(
            registry=registry,
            executor=executor,
            config=config,
            overrides_store=store,
        )

        update_mod = registry.get("system.control.update_config")
        assert update_mod is not None
        update_mod.execute(
            {
                "key": "executor.default_timeout",
                "value": 9999,
                "reason": "test persist",
            },
            None,
        )

        assert store.load()["executor.default_timeout"] == 9999

    def test_toggle_feature_persists_through_store(self) -> None:
        """A successful toggle_feature call writes back to the store."""
        store = InMemoryOverridesStore()
        config = Config.from_defaults()
        _enable_sys_modules(config)
        registry = Registry()
        executor = Executor(registry=registry)

        register_sys_modules(
            registry=registry,
            executor=executor,
            config=config,
            overrides_store=store,
        )

        toggle_mod = registry.get("system.control.toggle_feature")
        assert toggle_mod is not None
        toggle_mod.execute(
            {
                "module_id": "system.health.module",
                "enabled": False,
                "reason": "test persist",
            },
            None,
        )

        loaded = store.load()
        assert loaded["toggle.system.health.module"] is False

    def test_overrides_path_kwarg_back_compat_constructs_file_store(
        self, tmp_path: Path
    ) -> None:
        """The legacy ``overrides_path`` kwarg still loads + persists via FileOverridesStore."""
        path = tmp_path / "overrides.yaml"
        # Pre-seed: existing override applied on startup.
        path.write_text(yaml.safe_dump({"executor.default_timeout": 7777}), encoding="utf-8")

        config = Config.from_defaults()
        _enable_sys_modules(config)
        config.set("sys_modules.control.overrides_path", str(path))
        registry = Registry()
        executor = Executor(registry=registry)

        register_sys_modules(
            registry=registry,
            executor=executor,
            config=config,
        )

        assert config.get("executor.default_timeout") == 7777

        # Subsequent update writes back to the same file.
        update_mod = registry.get("system.control.update_config")
        assert update_mod is not None
        update_mod.execute(
            {"key": "executor.default_timeout", "value": 8888, "reason": "shim"},
            None,
        )
        with path.open() as f:
            written = yaml.safe_load(f)
        assert written["executor.default_timeout"] == 8888


# ---------------------------------------------------------------------------
# Top-level re-exports
# ---------------------------------------------------------------------------


class TestPackageReExports:
    def test_overrides_classes_exported_from_apcore(self) -> None:
        import apcore

        assert apcore.OverridesStore is OverridesStore
        assert apcore.InMemoryOverridesStore is InMemoryOverridesStore
        assert apcore.FileOverridesStore is FileOverridesStore
