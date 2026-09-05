"""Tests for Config system (Algorithm A12)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from apcore.config import _CONSTRAINTS, _DEFAULTS, _REQUIRED_FIELDS, Config
from apcore.errors import ConfigError, ConfigNotFoundError


# ---------------------------------------------------------------------------
# Backward compatibility: Config(data=...) still works
# ---------------------------------------------------------------------------


class TestConfigBackwardCompat:
    def test_positional_dict(self) -> None:
        config = Config({"executor": {"default_timeout": 5000}})
        assert config.get("executor.default_timeout") == 5000

    def test_keyword_data(self) -> None:
        config = Config(data={"executor": {"max_call_depth": 10}})
        assert config.get("executor.max_call_depth") == 10

    def test_none_data(self) -> None:
        config = Config()
        assert config.get("anything") is None

    def test_get_default(self) -> None:
        config = Config()
        assert config.get("missing.key", 42) == 42

    def test_nested_dot_path(self) -> None:
        config = Config(data={"a": {"b": {"c": 99}}})
        assert config.get("a.b.c") == 99

    def test_partial_path_returns_default(self) -> None:
        config = Config(data={"a": {"b": 1}})
        assert config.get("a.b.c", "nope") == "nope"


# ---------------------------------------------------------------------------
# Config.load() from YAML
# ---------------------------------------------------------------------------


class TestConfigLoad:
    def test_load_valid_yaml(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "apcore.yaml"
        yaml_file.write_text(
            """
version: "1.0.0"
project:
  name: myproject
extensions:
  root: ./ext
schema:
  root: ./sch
acl:
  root: ./acl
  default_effect: allow
"""
        )
        # Create the directories so semantic validation passes
        (tmp_path / "ext").mkdir()
        (tmp_path / "sch").mkdir()
        (tmp_path / "acl").mkdir()
        config = Config.load(str(yaml_file), validate=False)
        assert config.get("version") == "1.0.0"
        assert config.get("project.name") == "myproject"
        assert config.get("acl.default_effect") == "allow"

    def test_load_nonexistent_file(self) -> None:
        with pytest.raises(ConfigNotFoundError):
            Config.load("/nonexistent/path.yaml")

    def test_load_invalid_yaml(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "bad.yaml"
        yaml_file.write_text("key: [unclosed bracket")
        with pytest.raises(ConfigError, match="Invalid YAML"):
            Config.load(str(yaml_file))

    def test_load_non_dict_yaml(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "list.yaml"
        yaml_file.write_text("- item1\n- item2\n")
        with pytest.raises(ConfigError, match="must be a mapping"):
            Config.load(str(yaml_file))

    def test_load_empty_yaml(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "empty.yaml"
        yaml_file.write_text("")
        # Empty YAML → empty dict → defaults applied, validation may warn
        config = Config.load(str(yaml_file), validate=False)
        # Defaults should be merged
        assert config.get("executor.default_timeout") == 30000

    def test_load_merges_defaults(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "partial.yaml"
        yaml_file.write_text("executor:\n  default_timeout: 5000\n")
        config = Config.load(str(yaml_file), validate=False)
        assert config.get("executor.default_timeout") == 5000
        # Defaults for fields not in file
        assert config.get("executor.max_call_depth") == 32


# ---------------------------------------------------------------------------
# Environment variable overrides
# ---------------------------------------------------------------------------


class TestConfigEnvOverrides:
    def test_env_overrides_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        yaml_file = tmp_path / "apcore.yaml"
        yaml_file.write_text("executor:\n  default_timeout: 5000\n")
        monkeypatch.setenv("APCORE_EXECUTOR_DEFAULT__TIMEOUT", "9999")
        config = Config.load(str(yaml_file), validate=False)
        assert config.get("executor.default_timeout") == 9999

    def test_env_bool_coercion(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        yaml_file = tmp_path / "apcore.yaml"
        yaml_file.write_text("{}")
        monkeypatch.setenv("APCORE_EXTENSIONS_AUTO__DISCOVER", "false")
        config = Config.load(str(yaml_file), validate=False)
        assert config.get("extensions.auto_discover") is False

    def test_env_float_coercion(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        yaml_file = tmp_path / "apcore.yaml"
        yaml_file.write_text("{}")
        monkeypatch.setenv("APCORE_OBSERVABILITY_TRACING_SAMPLING__RATE", "0.5")
        config = Config.load(str(yaml_file), validate=False)
        assert config.get("observability.tracing.sampling_rate") == 0.5

    def test_env_string_value(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        yaml_file = tmp_path / "apcore.yaml"
        yaml_file.write_text("{}")
        monkeypatch.setenv("APCORE_PROJECT_NAME", "envproject")
        config = Config.load(str(yaml_file), validate=False)
        assert config.get("project.name") == "envproject"


# ---------------------------------------------------------------------------
# Validation (Algorithm A12)
# ---------------------------------------------------------------------------


class TestConfigValidation:
    def _make_valid_data(self, tmp_path: Path | None = None) -> dict[str, Any]:
        return {
            "version": "1.0.0",
            "extensions": {"root": "./ext"},
            "schema": {"root": "./sch"},
            "acl": {"root": "./acl", "default_effect": "deny"},
            "project": {"name": "test"},
        }

    def test_valid_config_passes(self) -> None:
        config = Config(data=self._make_valid_data())
        config.validate()  # Should not raise

    def test_missing_required_field(self) -> None:
        data = self._make_valid_data()
        del data["version"]
        config = Config(data=data)
        with pytest.raises(ConfigError, match="Missing required field.*version"):
            config.validate()

    def test_multiple_missing_fields(self) -> None:
        config = Config(data={})
        with pytest.raises(ConfigError, match="error\\(s\\)") as exc_info:
            config.validate()
        # Both required fields are reported, not just the first. There are
        # exactly two (§9.1): every other §9.1 key carries a canonical default
        # and so is never required.
        err = exc_info.value
        assert "errors" in err.details
        assert len(err.details["errors"]) == 2

    def test_invalid_acl_default_effect(self) -> None:
        data = self._make_valid_data()
        data["acl"]["default_effect"] = "maybe"
        config = Config(data=data)
        with pytest.raises(ConfigError, match="acl.default_effect"):
            config.validate()

    def test_invalid_sampling_rate(self) -> None:
        data = self._make_valid_data()
        data["observability"] = {"tracing": {"sampling_rate": 2.0}}
        config = Config(data=data)
        with pytest.raises(ConfigError, match="sampling_rate"):
            config.validate()

    def test_invalid_max_depth(self) -> None:
        data = self._make_valid_data()
        data["extensions"]["max_depth"] = 0
        config = Config(data=data)
        with pytest.raises(ConfigError, match="max_depth"):
            config.validate()

    def test_yaml_only_strategy_missing_root(self, tmp_path: Path) -> None:
        data = self._make_valid_data()
        data["schema"]["strategy"] = "yaml_only"
        data["schema"]["root"] = str(tmp_path / "nonexistent")
        config = Config(data=data)
        with pytest.raises(ConfigError, match="yaml_only"):
            config.validate()

    def test_valid_constraints_pass(self) -> None:
        data = self._make_valid_data()
        data["observability"] = {"tracing": {"sampling_rate": 0.5}}
        data["extensions"]["max_depth"] = 8
        config = Config(data=data)
        config.validate()  # Should not raise


# ---------------------------------------------------------------------------
# Config.set()
# ---------------------------------------------------------------------------


class TestConfigSet:
    def test_set_and_get(self) -> None:
        config = Config()
        config.set("executor.default_timeout", 5000)
        assert config.get("executor.default_timeout") == 5000

    def test_set_creates_intermediate_dicts(self) -> None:
        config = Config()
        config.set("a.b.c", 42)
        assert config.get("a.b.c") == 42


# ---------------------------------------------------------------------------
# Config.reload()
# ---------------------------------------------------------------------------


class TestConfigReload:
    def test_reload_rereads_file(self, tmp_path: Path) -> None:
        # reload() re-enters Config.load() with its default validate=True, so
        # the fixture must be a valid document: version + project.name.
        header = 'version: "1.0.0"\nproject:\n  name: test\n'
        yaml_file = tmp_path / "apcore.yaml"
        yaml_file.write_text(header + "executor:\n  default_timeout: 1000\n")
        config = Config.load(str(yaml_file), validate=False)
        assert config.get("executor.default_timeout") == 1000

        # Modify file
        yaml_file.write_text(header + "executor:\n  default_timeout: 9999\n")
        config.reload()
        assert config.get("executor.default_timeout") == 9999

    def test_reload_without_yaml_path_raises(self) -> None:
        config = Config(data={"a": 1})
        with pytest.raises(ConfigError, match="Cannot reload"):
            config.reload()


# ---------------------------------------------------------------------------
# Config.from_defaults()
# ---------------------------------------------------------------------------


class TestConfigFromDefaults:
    def test_has_default_values(self) -> None:
        config = Config.from_defaults()
        assert config.get("executor.default_timeout") == 30000
        assert config.get("executor.max_call_depth") == 32
        assert config.get("schema.strategy") == "yaml_first"

    def test_env_applied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APCORE_EXECUTOR_DEFAULT__TIMEOUT", "7777")
        config = Config.from_defaults()
        assert config.get("executor.default_timeout") == 7777


# ---------------------------------------------------------------------------
# Config.data property
# ---------------------------------------------------------------------------


class TestConfigData:
    def test_data_returns_copy(self) -> None:
        config = Config(data={"a": 1})
        d = config.data
        d["a"] = 999
        assert config.get("a") == 1  # Original unchanged

    def test_repr_with_yaml_path(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "apcore.yaml"
        yaml_file.write_text("{}")
        config = Config.load(str(yaml_file), validate=False)
        assert "yaml_path=" in repr(config)

    def test_repr_without_yaml_path(self) -> None:
        config = Config(data={"a": 1})
        assert "keys" in repr(config)


# ---------------------------------------------------------------------------
# sys_modules and project source fields
# ---------------------------------------------------------------------------


class TestSysModulesConfig:
    def test_config_sys_modules_enabled_default(self) -> None:
        config = Config(data=dict(_DEFAULTS))
        assert config.get("sys_modules.enabled") is False

    def test_default_table_carries_only_what_the_defaults_schema_declares(self) -> None:
        # Same rule as `test_config_declares_no_project_defaults` below:
        # `schemas/defaults.schema.json` declares `sys_modules.enabled` and
        # nothing else, and it is `additionalProperties: false`. The thirteen
        # other `sys_modules` defaults belong to
        # `schemas/sys-modules.schema.json` and reach a Config through the
        # namespace registration, not through this table.
        #
        # They used to sit here too, which made these keys readable in legacy
        # mode from apcore-python alone — apcore-typescript and apcore-rust
        # answer undefined/None over the same call, because their default
        # tables mirror `defaults.schema.json` exactly (sync finding A-D-021).
        config = Config(data=dict(_DEFAULTS))
        for key in (
            "sys_modules.error_history.max_entries_per_module",
            "sys_modules.error_history.max_total_entries",
            "sys_modules.events.enabled",
            "sys_modules.events.thresholds.error_rate",
            "sys_modules.events.thresholds.latency_p99_ms",
            "sys_modules.events.subscribers",
        ):
            assert config.get(key) is None, (
                f"{key} is not declared by defaults.schema.json, so the legacy default table must not answer for it"
            )

    def test_namespace_mode_still_supplies_the_sys_modules_defaults(self, tmp_path: Path) -> None:
        # The other half of the rule above: dropping those keys from the legacy
        # table must not make them unreachable, because §9.15.3 registers
        # `sys_modules` as a namespace that declares all fourteen.
        path = tmp_path / "apcore.json"
        path.write_text(json.dumps({"apcore": {"version": "1.0"}}))
        config = Config.load(path)
        assert config.get("sys_modules.error_history.max_entries_per_module") == 50
        assert config.get("sys_modules.error_history.max_total_entries") == 1000
        assert config.get("sys_modules.events.enabled") is False
        assert config.get("sys_modules.events.thresholds.error_rate") == 0.1
        assert config.get("sys_modules.events.thresholds.latency_p99_ms") == 5000.0

    def test_config_declares_no_project_defaults(self) -> None:
        # `schemas/defaults.schema.json` declares no `project` subtree, so
        # neither does `_DEFAULTS` (§9.1: `project.name` is required precisely
        # because it has no canonical default). Consumers that want a fallback
        # supply it at the call site.
        config = Config(data=dict(_DEFAULTS))
        assert config.get("project.source_repo") is None
        assert config.get("project.source_root") is None
        assert config.get("project.source_root", "") == ""

    def test_config_sys_modules_from_yaml(self, tmp_path: Path) -> None:
        yaml_content = "sys_modules:\n  enabled: true\nversion: '0.8.0'\nextensions:\n  root: ./extensions\nschema:\n  root: ./schemas\nacl:\n  root: ./acl\n  default_effect: deny\nproject:\n  name: test"
        config_file = tmp_path / "apcore.yaml"
        config_file.write_text(yaml_content)
        config = Config.load(str(config_file), validate=False)
        assert config.get("sys_modules.enabled") is True

    def test_config_sys_modules_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APCORE_SYS__MODULES_ENABLED", "true")
        config = Config.from_defaults()
        assert config.get("sys_modules.enabled") is True

    def test_config_project_source_repo_yaml(self, tmp_path: Path) -> None:
        yaml_content = "project:\n  name: test\n  source_repo: 'https://github.com/org/repo'\nversion: '0.8.0'\nextensions:\n  root: ./extensions\nschema:\n  root: ./schemas\nacl:\n  root: ./acl\n  default_effect: deny"
        config_file = tmp_path / "apcore.yaml"
        config_file.write_text(yaml_content)
        config = Config.load(str(config_file), validate=False)
        assert config.get("project.source_repo") == "https://github.com/org/repo"

    def test_config_project_source_root_yaml(self, tmp_path: Path) -> None:
        yaml_content = "project:\n  name: test\n  source_root: 'src/modules'\nversion: '0.8.0'\nextensions:\n  root: ./extensions\nschema:\n  root: ./schemas\nacl:\n  root: ./acl\n  default_effect: deny"
        config_file = tmp_path / "apcore.yaml"
        config_file.write_text(yaml_content)
        config = Config.load(str(config_file), validate=False)
        assert config.get("project.source_root") == "src/modules"


class TestNumericConstraintsRejectBooleans:
    """``docs/features/config-bus.md``: "Booleans are rejected for all numeric fields".

    ``bool`` subclasses ``int`` in Python, so ``isinstance(True, int)`` is True
    and every constraint lambda written as ``isinstance(v, int) and v >= 1``
    happily accepted ``true`` as the number 1 — silently turning
    ``executor.max_call_depth: true`` into a depth limit of 1. The four
    ``circuit_breaker`` entries already carried ``and not isinstance(v, bool)``;
    the rest did not. apcore-typescript and apcore-rust reject booleans for all
    of them.
    """

    # Every numeric key in `_CONSTRAINTS`, derived from the table itself so a
    # newly added key cannot quietly skip this test.
    NUMERIC_FIELDS = sorted(f for f in _CONSTRAINTS if f != "acl.default_effect")

    def test_the_numeric_field_list_is_complete(self) -> None:
        # Guards the derivation above: `acl.default_effect` is the only
        # non-numeric constrained key.
        assert len(self.NUMERIC_FIELDS) == len(_CONSTRAINTS) - 1
        assert "acl.default_effect" not in self.NUMERIC_FIELDS

    @pytest.mark.parametrize("field", NUMERIC_FIELDS)
    @pytest.mark.parametrize("value", [True, False])
    def test_boolean_is_rejected(self, field: str, value: bool) -> None:
        check_fn, _msg = _CONSTRAINTS[field]
        assert check_fn(value) is False, f"{field} accepted {value!r} as a number"

    @pytest.mark.parametrize("field", NUMERIC_FIELDS)
    def test_a_real_number_is_still_accepted(self, field: str) -> None:
        # 1 satisfies every numeric constraint in the table (>= 0, >= 1,
        # [0.0, 1.0] inclusive, > 0, and [1, 16]).
        check_fn, _msg = _CONSTRAINTS[field]
        assert check_fn(1) is True, f"{field} rejected the number 1"

    def test_boolean_surfaces_as_a_config_error_through_validate(self) -> None:
        data: dict[str, Any] = {
            "version": "1.0.0",
            "extensions": {"root": "./ext"},
            "schema": {"root": "./sch"},
            "acl": {"root": "./acl", "default_effect": "deny"},
            "project": {"name": "test"},
            "executor": {"max_call_depth": True},
        }
        with pytest.raises(ConfigError, match="max_call_depth"):
            Config(data=data).validate()


# ---------------------------------------------------------------------------
# Required fields are evaluated against the DECLARED document (PROTOCOL_SPEC
# §9.1 "What is required, and why so little is" / §9.3 step 1)
# ---------------------------------------------------------------------------


class TestRequiredFieldsAreReachable:
    """A required-field list that is checked after defaults are merged is dead code.

    ``_load_legacy_mode`` deep-merges ``_DEFAULTS`` into the parsed document and
    ``validate()`` then looked for required fields in the *merged* result — so
    the check could never fail, because the merge had already supplied every
    key. ``_DEFAULTS`` even carried an invented ``version: "0.16.0"`` and a
    ``project`` subtree that ``schemas/defaults.schema.json`` has never
    declared, which existed only to keep that check vacuous.

    PROTOCOL_SPEC §9.1: a key is required only when it has no canonical
    default; exactly ``version`` and ``project.name`` qualify. §9.3 step 1:
    requiredness is evaluated against the declared document, before defaults
    are merged.
    """

    # -- the invented defaults are gone -------------------------------------

    def test_defaults_do_not_invent_version_or_project(self) -> None:
        # schemas/defaults.schema.json declares neither, so neither may appear
        # in the SDK default table. Their presence is what made the
        # required-field check unreachable.
        assert "version" not in _DEFAULTS
        assert "project" not in _DEFAULTS

    def test_no_required_field_has_a_default(self) -> None:
        # The defining property of a required key (§9.1). If this ever fails,
        # the required-field check has silently become a no-op again.
        for field in _REQUIRED_FIELDS:
            assert Config.get_default(field) is None, (
                f"{field!r} is listed as required but _DEFAULTS supplies a value, "
                "which makes the required-field check unreachable"
            )

    def test_required_fields_are_exactly_the_two_without_defaults(self) -> None:
        assert _REQUIRED_FIELDS == ("version", "project.name")

    # -- the check actually fires ------------------------------------------

    def test_missing_version_in_file_raises(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "apcore.yaml"
        yaml_file.write_text("project:\n  name: test\n")
        with pytest.raises(ConfigError, match="Missing required field: 'version'") as exc_info:
            Config.load(str(yaml_file))
        assert exc_info.value.code == "CONFIG_INVALID"

    def test_missing_project_name_in_file_raises(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "apcore.yaml"
        yaml_file.write_text('version: "1.0.0"\n')
        with pytest.raises(ConfigError, match="Missing required field: 'project.name'") as exc_info:
            Config.load(str(yaml_file))
        assert exc_info.value.code == "CONFIG_INVALID"

    def test_empty_file_reports_both_missing_fields(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "apcore.yaml"
        yaml_file.write_text("{}\n")
        with pytest.raises(ConfigError) as exc_info:
            Config.load(str(yaml_file))
        assert exc_info.value.details["errors"] == [
            "Missing required field: 'version'",
            "Missing required field: 'project.name'",
        ]

    # -- and only fires for those two --------------------------------------

    def test_config_omitting_defaulted_keys_loads(self, tmp_path: Path) -> None:
        # extensions / schema / acl all carry canonical defaults, so omitting
        # them MUST NOT make the document invalid (§9.1). This passed before
        # too — but only because the merge had hidden the check entirely.
        yaml_file = tmp_path / "apcore.yaml"
        yaml_file.write_text('version: "1.0.0"\nproject:\n  name: test\n')
        config = Config.load(str(yaml_file))
        assert config.get("extensions.root") == "./extensions"
        assert config.get("schema.root") == "./schemas"
        assert config.get("acl.default_effect") == "deny"

    # -- requiredness is judged on the declared document -------------------

    def test_declared_excludes_merged_defaults(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "apcore.yaml"
        yaml_file.write_text('version: "1.0.0"\nproject:\n  name: test\n')
        config = Config.load(str(yaml_file))
        declared = config.declared
        assert declared == {"version": "1.0.0", "project": {"name": "test"}}
        # ...while the resolved view still carries the defaults.
        assert config.get("executor.default_timeout") == 30000

    def test_declared_is_a_copy(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "apcore.yaml"
        yaml_file.write_text('version: "1.0.0"\nproject:\n  name: test\n')
        config = Config.load(str(yaml_file))
        config.declared["version"] = "9.9.9"
        assert config.declared["version"] == "1.0.0"

    def test_defaults_cannot_satisfy_requiredness(self, tmp_path: Path) -> None:
        """The regression the old test could not catch.

        Simulates the pre-fix arrangement: put ``version``/``project.name``
        back into the default table and confirm the check still fails, because
        it reads the declared document rather than the merged one.
        """
        yaml_file = tmp_path / "apcore.yaml"
        yaml_file.write_text("extensions:\n  root: ./ext\n")
        patched = dict(_DEFAULTS)
        patched["version"] = "0.16.0"
        patched["project"] = {"name": "apcore"}
        with mock.patch.dict("apcore.config._DEFAULTS", patched, clear=True):
            with pytest.raises(ConfigError, match="Missing required field"):
                Config.load(str(yaml_file))

    def test_in_memory_config_declares_its_own_data(self) -> None:
        # Config(data=...) has no file behind it: the caller-supplied dict IS
        # the declared document, so validate() still guards it.
        config = Config(data={"version": "1.0.0", "project": {"name": "test"}})
        config.validate()
        with pytest.raises(ConfigError, match="Missing required field: 'project.name'"):
            Config(data={"version": "1.0.0"}).validate()


class TestDeclaredIncludesEnvOverrides:
    """PROTOCOL_SPEC §9.1 "What 'declared' means".

    The declared document is everything supplied by *someone* — file, env
    overrides, runtime set()/mount() — excluding only the default table.
    An env override therefore satisfies a required field. apcore-typescript
    and apcore-rust already behave this way; Python was the outlier.
    """

    def test_env_override_satisfies_a_required_field(self, tmp_path, monkeypatch):
        # File declares version but NOT project.name.
        cfg = tmp_path / "apcore.yaml"
        cfg.write_text('version: "1.0.0"\n')
        monkeypatch.setenv("APCORE_PROJECT_NAME", "from-env")

        config = Config.load(str(cfg))  # validate=True by default

        assert config.get("project.name") == "from-env"
        assert config.declared["project"]["name"] == "from-env"

    def test_missing_everywhere_still_fails(self, tmp_path, monkeypatch):
        cfg = tmp_path / "apcore.yaml"
        cfg.write_text('version: "1.0.0"\n')
        monkeypatch.delenv("APCORE_PROJECT_NAME", raising=False)

        with pytest.raises(ConfigError, match="project.name"):
            Config.load(str(cfg))

    def test_defaults_still_cannot_satisfy_requiredness(self, tmp_path, monkeypatch):
        """The env change must not have re-opened the defaults loophole."""
        cfg = tmp_path / "apcore.yaml"
        cfg.write_text('version: "1.0.0"\n')
        monkeypatch.delenv("APCORE_PROJECT_NAME", raising=False)
        monkeypatch.setitem(_DEFAULTS, "project", {"name": "from-defaults"})

        with pytest.raises(ConfigError, match="project.name"):
            Config.load(str(cfg))


class TestReloadHonoursOriginalValidateFlag:
    """PROTOCOL_SPEC §9.11 step 5.

    reload() re-validates only if the originating load() did. Re-imposing
    validation on a config deliberately loaded with validate=False would be a
    behaviour change disguised as a refresh.
    """

    def test_reload_does_not_validate_when_load_did_not(self, tmp_path):
        # A document that would fail validation: no project.name.
        cfg = tmp_path / "apcore.yaml"
        cfg.write_text('version: "1.0.0"\n')

        config = Config.load(str(cfg), validate=False)
        config.reload()  # must not raise

        assert config.get("version") == "1.0.0"

    def test_reload_validates_when_load_did(self, tmp_path):
        cfg = tmp_path / "apcore.yaml"
        cfg.write_text('version: "1.0.0"\nproject:\n  name: "ok"\n')
        config = Config.load(str(cfg))

        # Edit the file into an invalid state, then refresh.
        cfg.write_text('version: "1.0.0"\n')
        with pytest.raises(ConfigError, match="project.name"):
            config.reload()


class TestPathTypedConfigKeys:
    """PROTOCOL_SPEC §9.2.1 — the closed set of path-typed configuration keys."""

    EXPECTED = ("acl.root", "bindings.dir", "extensions.root", "extensions.roots[]", "schema.root")

    def test_accessor_matches_declared_set_in_both_directions(self):
        actual = set(Config.path_typed_keys())
        expected = set(self.EXPECTED)
        assert actual - expected == set(), f"keys the SDK invented: {sorted(actual - expected)}"
        assert expected - actual == set(), f"keys the SDK is missing: {sorted(expected - actual)}"

    def test_bindings_pattern_is_not_path_typed(self):
        # Discriminating case. `bindings.pattern` sits in the same section as
        # `bindings.dir` and its default (`*.binding.yaml`) looks like a filename,
        # so an implementation that classifies by section — or by eye — sweeps it
        # in. It is a glob matched WITHIN `bindings.dir`, never resolved itself.
        assert "bindings.pattern" not in Config.path_typed_keys()

    def test_non_path_string_keys_are_not_path_typed(self):
        # An implementation that marks every string-valued key as path-typed
        # passes any presence-only assertion and fails here.
        for key in (
            "acl.default_effect",
            "schema.strategy",
            "logging.level",
            "observability.tracing.exporter",
            "project.name",
        ):
            assert key not in Config.path_typed_keys(), key

    def test_set_is_a_property_of_the_spec_not_of_a_document(self):
        # Same answer from a defaults-only Config and from an instance that
        # declares none of these keys.
        assert Config.path_typed_keys() == self.EXPECTED

    def test_every_scalar_path_typed_key_is_in_the_declared_key_surface(self):
        # `extensions.roots[]` is the element form of the list key
        # `extensions.roots`; strip the marker before checking membership.
        for key in Config.path_typed_keys():
            base = key[:-2] if key.endswith("[]") else key
            assert Config.get_default(base, "__missing__") != "__missing__" or base in {
                "extensions.roots",
                "bindings.dir",
            }, base
