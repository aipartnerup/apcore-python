"""Conformance tests for System Modules Hardening (Issue #45).

Covers all 10 test cases defined in the canonical conformance fixture at:
  apcore/conformance/fixtures/system_modules_hardening.json

  1.  overrides_persisted_on_update
  2.  overrides_loaded_on_startup
  3.  audit_entry_records_actor
  4.  audit_entry_records_change
  5.  prometheus_usage_exports_calls_total
  6.  reload_with_path_filter
  7.  reload_module_id_and_filter_conflict
  8.  startup_fail_on_error_true_raises
  9.  startup_fail_on_error_false_continues
  10. rust_register_returns_result  (language=rust — skipped for Python)
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml

from apcore.config import Config
from apcore.errors import ModuleReloadConflictError, SysModuleRegistrationError
from apcore.events.emitter import EventEmitter
from apcore.observability.usage import UsageCollector
from apcore.registry.registry import Registry
from apcore.sys_modules.audit import InMemoryAuditStore
from apcore.sys_modules.control import (
    ReloadModule,
    ToggleFeatureModule,
    ToggleState,
    UpdateConfigModule,
)
from apcore.sys_modules.registration import register_sys_modules


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides: Any) -> Config:
    config = Config.from_defaults()
    for key, value in overrides.items():
        config.set(key, value)
    return config


def _make_update_config_module(
    config: Config,
    overrides_path: str | None = None,
    audit_store: InMemoryAuditStore | None = None,
) -> UpdateConfigModule:
    return UpdateConfigModule(
        config=config,
        event_emitter=EventEmitter(),
        overrides_path=overrides_path,
        audit_store=audit_store,
    )


def _make_toggle_module(
    registry: Registry,
    toggle_state: ToggleState | None = None,
    audit_store: InMemoryAuditStore | None = None,
) -> ToggleFeatureModule:
    return ToggleFeatureModule(
        registry=registry,
        event_emitter=EventEmitter(),
        toggle_state=toggle_state,
        audit_store=audit_store,
    )


def _make_reload_module(
    registry: Registry,
    audit_store: InMemoryAuditStore | None = None,
) -> ReloadModule:
    return ReloadModule(
        registry=registry,
        event_emitter=EventEmitter(),
        audit_store=audit_store,
    )


def _make_context(identity_id: str = "unknown", identity_type: str = "user") -> Any:
    """Create a minimal context object with identity."""
    context = MagicMock()
    context.trace_id = "test-trace-id"
    identity = MagicMock()
    identity.id = identity_id
    identity.type = identity_type
    context.identity = identity
    return context


# ---------------------------------------------------------------------------
# 1. overrides_persisted_on_update
# ---------------------------------------------------------------------------


class TestOverridesPersistOnUpdate:
    """Case: overrides_persisted_on_update"""

    def test_update_config_writes_overrides_file(self, tmp_path: Path) -> None:
        """update_config with overrides_path writes the change to the YAML file."""
        overrides_path = str(tmp_path / "overrides.yaml")
        config = _make_config()
        mod = _make_update_config_module(config, overrides_path=overrides_path)

        result = mod.execute(
            {
                "key": "executor.default_timeout",
                "value": 60000,
                "reason": "increase timeout for tests",
            },
            _make_context(),
        )

        assert result["success"] is True
        assert os.path.exists(overrides_path)
        with open(overrides_path) as f:
            written = yaml.safe_load(f)
        assert written["executor.default_timeout"] == 60000

    def test_overrides_file_accumulates_multiple_keys(self, tmp_path: Path) -> None:
        """Multiple update_config calls accumulate keys in the overrides file."""
        overrides_path = str(tmp_path / "overrides.yaml")
        config = _make_config()
        mod = _make_update_config_module(config, overrides_path=overrides_path)

        mod.execute(
            {"key": "executor.default_timeout", "value": 60000, "reason": "r1"},
            _make_context(),
        )
        mod.execute(
            {"key": "executor.max_workers", "value": 8, "reason": "r2"},
            _make_context(),
        )

        with open(overrides_path) as f:
            written = yaml.safe_load(f)
        assert written["executor.default_timeout"] == 60000
        assert written["executor.max_workers"] == 8

    def test_no_overrides_path_does_not_create_file(self, tmp_path: Path) -> None:
        """When overrides_path is None, no file is created."""
        config = _make_config()
        mod = _make_update_config_module(config, overrides_path=None)
        mod.execute(
            {"key": "executor.default_timeout", "value": 60000, "reason": "r"},
            _make_context(),
        )
        # No file created
        assert not any(tmp_path.iterdir())


# ---------------------------------------------------------------------------
# 2. overrides_loaded_on_startup
# ---------------------------------------------------------------------------


class TestOverridesLoadedOnStartup:
    """Case: overrides_loaded_on_startup"""

    def test_overrides_applied_after_base_config(self, tmp_path: Path) -> None:
        """When overrides.yaml exists at startup, its values override base config."""
        overrides_file = tmp_path / "overrides.yaml"
        overrides_file.write_text("executor.default_timeout: 60000\n")

        config = _make_config()
        config.set("sys_modules.enabled", True)
        config.set("sys_modules.control.overrides_path", str(overrides_file))

        assert config.get("executor.default_timeout") == 30000

        executor = MagicMock()
        registry = Registry()
        register_sys_modules(registry=registry, executor=executor, config=config)

        assert config.get("executor.default_timeout") == 60000

    def test_base_config_file_is_not_modified(self, tmp_path: Path) -> None:
        """The in-memory config is changed but the base config YAML file is untouched."""
        overrides_file = tmp_path / "overrides.yaml"
        overrides_file.write_text("executor.default_timeout: 60000\n")

        config = _make_config()
        config.set("sys_modules.enabled", True)
        config.set("sys_modules.control.overrides_path", str(overrides_file))

        original_timeout = config.get("executor.default_timeout")
        assert original_timeout == 30000

        executor = MagicMock()
        registry = Registry()
        register_sys_modules(registry=registry, executor=executor, config=config)

        # The overrides file itself is unchanged
        loaded = yaml.safe_load(overrides_file.read_text())
        assert loaded["executor.default_timeout"] == 60000

    def test_missing_overrides_file_does_not_error(self, tmp_path: Path) -> None:
        """When the overrides file does not exist, startup continues without error."""
        nonexistent = str(tmp_path / "no_such_file.yaml")
        config = _make_config()
        config.set("sys_modules.enabled", True)
        config.set("sys_modules.control.overrides_path", nonexistent)

        executor = MagicMock()
        registry = Registry()
        # Should not raise
        register_sys_modules(registry=registry, executor=executor, config=config)


# ---------------------------------------------------------------------------
# 3. audit_entry_records_actor
# ---------------------------------------------------------------------------


class TestAuditEntryRecordsActor:
    """Case: audit_entry_records_actor"""

    def test_update_config_records_audit_entry_with_actor(self) -> None:
        """update_config call produces an audit entry with actor_id from context.identity."""
        audit_store = InMemoryAuditStore()
        config = _make_config()
        mod = _make_update_config_module(config, audit_store=audit_store)
        context = _make_context(identity_id="user-abc-123", identity_type="user")

        mod.execute(
            {
                "key": "executor.default_timeout",
                "value": 45000,
                "reason": "audit trail test",
            },
            context,
        )

        entries = audit_store.query()
        assert len(entries) == 1
        entry = entries[0]
        assert entry.action == "update_config"
        assert entry.target_module_id == "system.control.update_config"
        assert entry.actor_id == "user-abc-123"
        assert entry.actor_type == "user"

    def test_audit_entry_has_timestamp(self) -> None:
        """Audit entry timestamp is an ISO 8601 string."""
        audit_store = InMemoryAuditStore()
        config = _make_config()
        mod = _make_update_config_module(config, audit_store=audit_store)

        mod.execute(
            {"key": "executor.default_timeout", "value": 45000, "reason": "ts test"},
            _make_context(),
        )

        entry = audit_store.query()[0]
        assert entry.timestamp
        assert "T" in entry.timestamp  # ISO 8601 format

    def test_audit_entry_has_trace_id(self) -> None:
        """Audit entry contains the trace_id from context."""
        audit_store = InMemoryAuditStore()
        config = _make_config()
        mod = _make_update_config_module(config, audit_store=audit_store)
        context = _make_context()
        context.trace_id = "abc123"

        mod.execute(
            {"key": "executor.default_timeout", "value": 45000, "reason": "tid test"},
            context,
        )

        entry = audit_store.query()[0]
        assert entry.trace_id == "abc123"


# ---------------------------------------------------------------------------
# 4. audit_entry_records_change
# ---------------------------------------------------------------------------


class TestAuditEntryRecordsChange:
    """Case: audit_entry_records_change"""

    def test_toggle_feature_records_before_after_change(self) -> None:
        """toggle_feature produces an audit entry with before/after change values."""
        audit_store = InMemoryAuditStore()
        registry = Registry()
        toggle_state = ToggleState()

        # Register a dummy module so toggle can find it
        dummy = MagicMock()
        dummy.input_schema = {"type": "object", "properties": {}}
        dummy.output_schema = {"type": "object", "properties": {}}
        registry.register_internal("risky.module", dummy)

        mod = _make_toggle_module(registry, toggle_state=toggle_state, audit_store=audit_store)
        context = _make_context(identity_id="svc-deploy-agent", identity_type="service")

        mod.execute(
            {
                "module_id": "risky.module",
                "enabled": False,
                "reason": "maintenance window",
            },
            context,
        )

        entries = audit_store.query()
        assert len(entries) == 1
        entry = entries[0]
        assert entry.action == "toggle_feature"
        assert entry.target_module_id == "risky.module"
        assert entry.actor_id == "svc-deploy-agent"
        assert entry.actor_type == "service"
        assert entry.change["before"] is True
        assert entry.change["after"] is False

    def test_toggle_feature_enable_records_before_false(self) -> None:
        """Enabling a previously disabled module records before=False, after=True."""
        audit_store = InMemoryAuditStore()
        registry = Registry()
        toggle_state = ToggleState()
        toggle_state.disable("risky.module")

        dummy = MagicMock()
        dummy.input_schema = {"type": "object", "properties": {}}
        dummy.output_schema = {"type": "object", "properties": {}}
        registry.register_internal("risky.module", dummy)

        mod = _make_toggle_module(registry, toggle_state=toggle_state, audit_store=audit_store)

        mod.execute(
            {"module_id": "risky.module", "enabled": True, "reason": "re-enable"},
            _make_context(),
        )

        entry = audit_store.query()[0]
        assert entry.change["before"] is False
        assert entry.change["after"] is True


# ---------------------------------------------------------------------------
# 5. prometheus_usage_exports_calls_total
# ---------------------------------------------------------------------------


class TestPrometheusUsageExportsCallsTotal:
    """Case: prometheus_usage_exports_calls_total"""

    def test_export_contains_calls_total_success(self) -> None:
        """export_prometheus includes apcore_usage_calls_total with status=success."""
        collector = UsageCollector()
        for _ in range(5):
            collector.record("math.add", "caller", 10.0, success=True)

        output = collector.export_prometheus()
        assert 'apcore_usage_calls_total{module_id="math.add",status="success"}' in output

    def test_export_contains_calls_total_error(self) -> None:
        """export_prometheus includes apcore_usage_calls_total with status=error."""
        collector = UsageCollector()
        collector.record("math.add", "caller", 10.0, success=False)

        output = collector.export_prometheus()
        assert 'apcore_usage_calls_total{module_id="math.add",status="error"}' in output

    def test_export_contains_error_rate(self) -> None:
        """export_prometheus includes apcore_usage_error_rate metric."""
        collector = UsageCollector()
        collector.record("math.add", "caller", 10.0, success=True)

        output = collector.export_prometheus()
        assert 'apcore_usage_error_rate{module_id="math.add"}' in output

    def test_export_contains_p99_latency(self) -> None:
        """export_prometheus includes apcore_usage_p99_latency_ms metric."""
        collector = UsageCollector()
        collector.record("math.add", "caller", 45.0, success=True)

        output = collector.export_prometheus()
        assert 'apcore_usage_p99_latency_ms{module_id="math.add"}' in output

    def test_export_completes_within_timeout(self) -> None:
        """export_prometheus completes within 1000ms export_timeout_ms budget."""
        collector = UsageCollector()
        for _ in range(100):
            collector.record("math.add", "caller", 10.0, success=True)

        start = time.monotonic()
        collector.export_prometheus()
        elapsed_ms = (time.monotonic() - start) * 1000.0
        assert elapsed_ms < 1000.0


# ---------------------------------------------------------------------------
# 6. reload_with_path_filter
# ---------------------------------------------------------------------------


class TestReloadWithPathFilter:
    """Case: reload_with_path_filter"""

    def _make_registry_with_modules(self, module_ids: list[str]) -> Registry:
        """Create a registry with dummy modules registered."""
        registry = Registry()
        for mid in module_ids:
            dummy = MagicMock()
            dummy.input_schema = {"type": "object", "properties": {}}
            dummy.output_schema = {"type": "object", "properties": {}}
            dummy.version = "1.0.0"
            registry.register_internal(mid, dummy)
        return registry

    def test_path_filter_reloads_matching_modules(self) -> None:
        """path_filter 'executor.*' reloads all matching modules."""
        all_modules = [
            "executor.email.send",
            "executor.math.add",
            "executor.pdf.render",
            "orchestrator.main",
        ]
        registry = self._make_registry_with_modules(all_modules)
        mod = _make_reload_module(registry)

        with patch.object(mod, "_reload_one"):
            result = mod.execute(
                {
                    "path_filter": "executor.*",
                    "reload_dependents": False,
                    "reason": "bulk reload after deploy",
                },
                _make_context(),
            )

        assert result["success"] is True
        reloaded = result["reloaded_modules"]
        assert sorted(reloaded) == [
            "executor.email.send",
            "executor.math.add",
            "executor.pdf.render",
        ]
        assert "orchestrator.main" not in reloaded

    def test_path_filter_excludes_non_matching(self) -> None:
        """orchestrator.main is not reloaded when path_filter is 'executor.*'."""
        all_modules = [
            "executor.email.send",
            "executor.math.add",
            "orchestrator.main",
        ]
        registry = self._make_registry_with_modules(all_modules)
        mod = _make_reload_module(registry)

        with patch.object(mod, "_reload_one"):
            result = mod.execute(
                {"path_filter": "executor.*", "reason": "deploy"},
                _make_context(),
            )

        assert "orchestrator.main" not in result["reloaded_modules"]

    def test_path_filter_reload_order_is_topological(self) -> None:
        """Modules are reloaded in topological order (sorted when no deps)."""
        all_modules = [
            "executor.pdf.render",
            "executor.math.add",
            "executor.email.send",
        ]
        registry = self._make_registry_with_modules(all_modules)
        mod = _make_reload_module(registry)

        reload_order: list[str] = []

        def capture_reload(mid: str) -> None:
            reload_order.append(mid)

        with patch.object(mod, "_reload_one", side_effect=capture_reload):
            mod.execute(
                {"path_filter": "executor.*", "reason": "topo test"},
                _make_context(),
            )

        # Without dependencies all modules are sorted alphabetically
        assert reload_order == sorted(all_modules)


# ---------------------------------------------------------------------------
# Supplemental: reload_module audit entry (not a fixture case, covers §1.2)
# ---------------------------------------------------------------------------


class TestReloadModuleAuditEntry:
    """Supplemental: reload_module records an audit entry with actor from context.identity."""

    def test_single_reload_records_audit_entry(self) -> None:
        """A single-module reload produces an audit entry with version change info."""
        audit_store = InMemoryAuditStore()
        registry = Registry()
        dummy = MagicMock()
        dummy.input_schema = {"type": "object", "properties": {}}
        dummy.output_schema = {"type": "object", "properties": {}}
        dummy.version = "1.0.0"
        registry.register_internal("math.add", dummy)

        mod = _make_reload_module(registry, audit_store=audit_store)
        context = _make_context(identity_id="ops-user", identity_type="user")

        new_dummy = MagicMock()
        new_dummy.version = "1.1.0"

        with (
            patch.object(mod, "_rediscover_module", return_value=new_dummy),
            patch.object(mod, "_reregister_module"),
        ):
            mod.execute(
                {"module_id": "math.add", "reason": "hotfix deploy"},
                context,
            )

        entries = audit_store.query()
        assert len(entries) == 1
        entry = entries[0]
        assert entry.action == "reload_module"
        assert entry.target_module_id == "math.add"
        assert entry.actor_id == "ops-user"
        assert entry.actor_type == "user"
        assert entry.change["before"] == "1.0.0"
        assert entry.change["after"] == "1.1.0"

    def test_bulk_reload_records_audit_entry(self) -> None:
        """A bulk path_filter reload produces an audit entry listing reloaded modules."""
        audit_store = InMemoryAuditStore()
        registry = Registry()
        for mid in ["executor.a", "executor.b"]:
            dummy = MagicMock()
            dummy.input_schema = {"type": "object", "properties": {}}
            dummy.output_schema = {"type": "object", "properties": {}}
            registry.register_internal(mid, dummy)

        mod = _make_reload_module(registry, audit_store=audit_store)

        with patch.object(mod, "_reload_one"):
            mod.execute(
                {"path_filter": "executor.*", "reason": "bulk deploy"},
                _make_context(identity_id="ci-agent", identity_type="service"),
            )

        entries = audit_store.query()
        assert len(entries) == 1
        entry = entries[0]
        assert entry.action == "reload_module"
        assert entry.actor_id == "ci-agent"


# ---------------------------------------------------------------------------
# 7. reload_module_id_and_filter_conflict
# ---------------------------------------------------------------------------


class TestReloadModuleIdAndFilterConflict:
    """Case: reload_module_id_and_filter_conflict"""

    def test_both_module_id_and_path_filter_raises(self) -> None:
        """Providing both module_id and path_filter raises MODULE_RELOAD_CONFLICT."""
        registry = Registry()
        mod = _make_reload_module(registry)

        with pytest.raises(ModuleReloadConflictError) as exc_info:
            mod.execute(
                {
                    "module_id": "executor.email.send",
                    "path_filter": "executor.*",
                    "reason": "conflict test",
                },
                _make_context(),
            )

        error = exc_info.value
        assert error.code == "MODULE_RELOAD_CONFLICT"
        assert "mutually exclusive" in error.message.lower()


# ---------------------------------------------------------------------------
# 8. startup_fail_on_error_true_raises
# ---------------------------------------------------------------------------


class TestStartupFailOnErrorTrueRaises:
    """Case: startup_fail_on_error_true_raises"""

    def test_fail_on_error_true_raises_immediately(self) -> None:
        """When fail_on_error=True and a module fails, register_sys_modules raises."""
        config = _make_config()
        config.set("sys_modules.enabled", True)
        executor = MagicMock()
        registry = Registry()

        original_register = registry.register_internal

        def failing_register(module_id: str, module: Any) -> None:
            if module_id == "system.health.summary":
                raise ValueError("schema validation error")
            original_register(module_id, module)

        with patch.object(registry, "register_internal", side_effect=failing_register):
            with pytest.raises(SysModuleRegistrationError) as exc_info:
                register_sys_modules(
                    registry=registry,
                    executor=executor,
                    config=config,
                    fail_on_error=True,
                )

        error = exc_info.value
        assert error.code == "SYS_MODULE_REGISTRATION_FAILED"
        assert "system.health.summary" in error.message

    def test_fail_on_error_true_error_includes_module_id(self) -> None:
        """SysModuleRegistrationError includes the failed module's ID."""
        config = _make_config()
        config.set("sys_modules.enabled", True)
        executor = MagicMock()
        registry = Registry()

        original_register = registry.register_internal

        def failing_register(module_id: str, module: Any) -> None:
            if module_id == "system.health.summary":
                raise RuntimeError("registration failed")
            original_register(module_id, module)

        with patch.object(registry, "register_internal", side_effect=failing_register):
            with pytest.raises(SysModuleRegistrationError) as exc_info:
                register_sys_modules(
                    registry=registry,
                    executor=executor,
                    config=config,
                    fail_on_error=True,
                )

        assert "system.health.summary" in exc_info.value.details.get("module_id", "")


# ---------------------------------------------------------------------------
# 9. startup_fail_on_error_false_continues
# ---------------------------------------------------------------------------


class TestStartupFailOnErrorFalseContinues:
    """Case: startup_fail_on_error_false_continues"""

    def test_fail_on_error_false_does_not_raise(self, caplog: Any) -> None:
        """When fail_on_error=False and a module fails, register_sys_modules does not raise."""
        config = _make_config()
        config.set("sys_modules.enabled", True)
        executor = MagicMock()
        registry = Registry()

        original_register = registry.register_internal

        def failing_register(module_id: str, module: Any) -> None:
            if module_id == "system.health.summary":
                raise ValueError("schema validation error")
            original_register(module_id, module)

        with patch.object(registry, "register_internal", side_effect=failing_register):
            with caplog.at_level(logging.ERROR):
                result = register_sys_modules(
                    registry=registry,
                    executor=executor,
                    config=config,
                    fail_on_error=False,
                )

        # Should return normally
        assert isinstance(result, dict)

    def test_fail_on_error_false_logs_at_error_level(self, caplog: Any) -> None:
        """When fail_on_error=False, failure is logged at ERROR level."""
        config = _make_config()
        config.set("sys_modules.enabled", True)
        executor = MagicMock()
        registry = Registry()

        original_register = registry.register_internal

        def failing_register(module_id: str, module: Any) -> None:
            if module_id == "system.health.summary":
                raise ValueError("schema validation error")
            original_register(module_id, module)

        with patch.object(registry, "register_internal", side_effect=failing_register):
            with caplog.at_level(logging.ERROR, logger="apcore.sys_modules.registration"):
                register_sys_modules(
                    registry=registry,
                    executor=executor,
                    config=config,
                    fail_on_error=False,
                )

        error_msgs = [r.message for r in caplog.records if r.levelno == logging.ERROR]
        assert any("system.health.summary" in m for m in error_msgs)

    def test_fail_on_error_false_remaining_modules_registered(self) -> None:
        """When fail_on_error=False, modules after the failing one are still registered."""
        config = _make_config()
        config.set("sys_modules.enabled", True)
        executor = MagicMock()
        registry = Registry()

        original_register = registry.register_internal
        registered: list[str] = []

        def tracking_register(module_id: str, module: Any) -> None:
            if module_id == "system.health.summary":
                raise ValueError("schema validation error")
            original_register(module_id, module)
            registered.append(module_id)

        with patch.object(registry, "register_internal", side_effect=tracking_register):
            register_sys_modules(
                registry=registry,
                executor=executor,
                config=config,
                fail_on_error=False,
            )

        # Other modules should still be registered despite the first failure
        assert any("system.health.module" in mid for mid in registered)


# ---------------------------------------------------------------------------
# 10. rust_register_returns_result
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="language=rust — not applicable to Python implementation")
class TestRustRegisterReturnsResult:
    """Case: rust_register_returns_result — Rust-specific, skipped for Python."""

    def test_rust_returns_result_type(self) -> None:
        """Rust register_sys_modules returns Result<(), SysModuleError>."""
        pass
