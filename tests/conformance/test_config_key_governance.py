"""Drive `config_key_governance.json` — the configuration key-surface guard.

The fixture derives its `allowed_keys` / `canonical_defaults` from the canonical
schemas, so this test is really asking: does `apcore.config`'s idea of the config
surface still match `schemas/`?

It exists because four separate instances of the same defect shipped undetected:
`schema.validation.*` validated by every SDK and declared by no schema, a frozen
`version`/`project` default pair that made the required-field check unreachable,
`middleware.circuit_breaker.*` forbidden by `apcore-config.schema.json` yet
validated everywhere and read nowhere, and a missing Rust default table that
resolved 15 documented keys to null. None was findable by any existing test.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from apcore.config import Config, _CONSTRAINTS, _DEFAULTS, _FRAMEWORK_SECTION_KEYS
from apcore.errors import ConfigError

from .canonical_fixtures import fixtures_dir, load_fixture

FIXTURE = load_fixture("config_key_governance.json")
ALLOWED: set[str] = set(FIXTURE["allowed_keys"])
CANONICAL: dict[str, Any] = FIXTURE["canonical_defaults"]


def _flatten(tree: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Flatten a nested default table to {dot_path: value}.

    An empty dict is a leaf: it is a declared value, not a subtree to descend.
    """
    out: dict[str, Any] = {}
    for key, val in tree.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(val, dict) and val:
            out.update(_flatten(val, path))
        else:
            out[path] = val
    return out


DEFAULT_KEYS = _flatten(_DEFAULTS)
CONSTRAINT_KEYS = set(_CONSTRAINTS)

#: canonical case id -> the case body. Each test below takes its verdict from
#: the case's own ``expected`` block ("violations": [], "missing": [],
#: "mismatched": []) instead of hard-coding ``== []``: an expectation no driver
#: reads is not a contract, it only looks like one.
CASES: dict[str, Any] = {case["id"]: case for case in FIXTURE["test_cases"]}


def _values_equal(sdk_value: Any, canonical_value: Any) -> bool:
    """Fixture driver_contract: compare numerically when both sides are numbers."""
    if isinstance(sdk_value, bool) or isinstance(canonical_value, bool):
        return sdk_value is canonical_value
    if isinstance(sdk_value, (int, float)) and isinstance(canonical_value, (int, float)):
        return float(sdk_value) == float(canonical_value)
    return bool(sdk_value == canonical_value)


class TestConfigKeySurfaceGovernance:
    def test_default_table_declares_no_undeclared_key(self) -> None:
        """A default for a key no schema declares is an SDK-invented default.

        `apcore-config.schema.json` is `additionalProperties: false`, so a user
        config carrying such a key fails the canonical schema while the SDK
        quietly supplies a value for it.
        """
        expected = CASES["sdk_default_table_declares_no_undeclared_key"]["expected"]
        violations = sorted(set(DEFAULT_KEYS) - ALLOWED)
        assert violations == expected["violations"], (
            "_DEFAULTS declares keys no canonical schema allows:\n  "
            + "\n  ".join(f"{k} = {DEFAULT_KEYS[k]!r}" for k in violations)
            + "\nEither add them to the appropriate schema in apcore/schemas/ "
            "(and regenerate the fixture) or remove them from _DEFAULTS."
        )

    def test_constraint_table_declares_no_undeclared_key(self) -> None:
        """Validating a key the canonical schema forbids is worse than not
        validating it: it tells the operator the key is understood."""
        expected = CASES["sdk_constraint_table_declares_no_undeclared_key"]["expected"]
        violations = sorted(CONSTRAINT_KEYS - ALLOWED)
        assert violations == expected["violations"], (
            "_CONSTRAINTS validates keys no canonical schema allows:\n  "
            + "\n  ".join(violations)
        )

    def test_reproduces_every_canonical_default(self) -> None:
        """A missing entry means the key resolves to None here while its peers
        return the documented value."""
        expected = CASES["sdk_reproduces_every_canonical_default"]["expected"]
        missing = sorted(set(CANONICAL) - set(DEFAULT_KEYS))
        assert missing == expected["missing"], (
            "defaults.schema.json declares defaults _DEFAULTS does not carry:\n  "
            + "\n  ".join(f"{k} = {CANONICAL[k]!r}" for k in missing)
        )

    def test_default_values_match_canonical_defaults(self) -> None:
        """Compare the RESOLVED default view, per the fixture's driver_contract.

        Reading through ``Config.from_defaults().get()`` rather than indexing
        ``_DEFAULTS`` directly is what the caller actually experiences, and it
        keeps this check comparable with apcore-rust, whose serde struct
        defaults never appear in its default table at all.
        """
        expected = CASES["sdk_default_values_match_canonical_defaults"]["expected"]
        resolved = Config.from_defaults()
        mismatched = sorted(
            key for key, canonical_value in CANONICAL.items() if not _values_equal(resolved.get(key), canonical_value)
        )
        assert mismatched == expected["mismatched"], (
            "Config.from_defaults() resolves values defaults.schema.json does not declare:\n  "
            + "\n  ".join(f"{k}: SDK {resolved.get(k)!r} != canonical {CANONICAL[k]!r}" for k in mismatched)
        )

    @pytest.mark.parametrize("key", sorted(CANONICAL))
    def test_default_table_entry_matches_canonical(self, key: str) -> None:
        """Per-key form of the check above, against the table itself.

        Kept alongside the resolved-view test so a table/resolution disagreement
        (a default that only exists in the ``get()`` path, or vice versa) is
        reported against the specific key.
        """
        assert _values_equal(DEFAULT_KEYS[key], CANONICAL[key]), (
            f"{key}: _DEFAULTS has {DEFAULT_KEYS[key]!r}, "
            f"defaults.schema.json declares {CANONICAL[key]!r}"
        )

    def test_every_canonical_case_has_a_driver(self) -> None:
        """A case added on the spec side must fail here, not pass unnoticed."""
        covered = {
            "sdk_default_table_declares_no_undeclared_key",
            "sdk_constraint_table_declares_no_undeclared_key",
            "sdk_reproduces_every_canonical_default",
            "sdk_default_values_match_canonical_defaults",
            "unknown_framework_key_is_retained_by_default",
            "unknown_framework_key_is_rejected_under_strict",
        }
        assert set(CASES) == covered, (
            f"config_key_governance.json cases without a driver: {sorted(set(CASES) - covered)}; "
            f"drivers claiming cases the fixture no longer defines: {sorted(covered - set(CASES))}"
        )

    def test_fixture_is_derived_not_authored(self) -> None:
        """Guard the guard: if the fixture ever stops naming its generator, the
        next person to hand-edit it will make it a second source of truth."""
        contract = FIXTURE["driver_contract"]["sources"]
        assert "regenerated" in contract and "do NOT hand-edit" in contract
        # Check the declared sources against the SPEC REPO, not against a
        # literal transcription of themselves: a list compared to its own copy
        # cannot fail when a schema is renamed or deleted. (Same tautology
        # shape as apcore-python#32 / aiperceivable/apcore#81.)
        spec_repo = fixtures_dir().parent.parent
        declared = FIXTURE["canonical_sources"]
        assert declared, "the fixture must name the schemas it was generated from"
        missing = [src for src in declared if not (spec_repo / src).is_file()]
        assert missing == [], (
            f"canonical_sources names schema files that do not exist under {spec_repo}: {missing}"
        )


# ---------------------------------------------------------------------------
# §9.14 reject_unknown_framework_keys
# ---------------------------------------------------------------------------


def _nest(dotted: dict[str, Any]) -> dict[str, Any]:
    """Expand the fixture's flat ``{"executor.max_call_depth": 7}`` into a tree."""
    tree: dict[str, Any] = {}
    for path, value in dotted.items():
        cursor = tree
        parts = path.split(".")
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = value
    return tree


#: Keys every document needs to satisfy `_REQUIRED_FIELDS`. The fixture omits
#: them because they are not what these two cases are about; without them a
#: legacy-mode load fails on the required-field check and the strict assertion
#: below would pass for the wrong reason.
_REQUIRED_SCAFFOLD = {"version": "1.0.0", "project": {"name": "governance-test"}}


def _write_legacy(tmp_path: Path, case: dict[str, Any]) -> str:
    """Legacy mode: the whole document *is* the apcore namespace (§9.14 step 1)."""
    tree = _nest(case["config"])
    meta = tree.pop("_config", None)
    document: dict[str, Any] = {**_REQUIRED_SCAFFOLD, **tree}
    if meta is not None:
        document["_config"] = meta
    path = tmp_path / "legacy.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False))
    return str(path)


def _write_namespace(tmp_path: Path, case: dict[str, Any]) -> str:
    """Namespace mode: framework sections live under ``apcore:`` (§9.14 step 2)."""
    tree = _nest(case["config"])
    meta = tree.pop("_config", None)
    document: dict[str, Any] = {"apcore": {**_REQUIRED_SCAFFOLD, **tree}}
    if meta is not None:
        document["_config"] = meta
    path = tmp_path / "namespace.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False))
    return str(path)


#: Both modes, because §9.6.3 clause (b) and §9.14 step 1 say the rule holds in
#: legacy mode too — a fix applied only to `_validate_namespace_mode` would leave
#: the older and more common file shape unenforced.
_MODES = {"legacy": _write_legacy, "namespace": _write_namespace}


def _partition_config_keys(case: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Split the case's config keys into (declared, undeclared) by `allowed_keys`.

    Asking the fixture's own canonical projection which key is undeclared beats
    recognising the ``zz_`` prefix: rename the key in the fixture and this still
    tests what the case is named after.
    """
    keys = [key for key in case["config"] if not key.startswith("_config.")]
    return [k for k in keys if k in ALLOWED], [k for k in keys if k not in ALLOWED]


class TestUnknownFrameworkKeys:
    """`_config.strict` governs keys *inside* the framework sections (§9.14).

    Non-strict retains them; strict rejects them and names every one.
    """

    @pytest.mark.parametrize("mode", sorted(_MODES))
    def test_unknown_framework_key_is_retained_by_default(self, tmp_path: Path, mode: str) -> None:
        """Default tier: the key survives the load and reads back through get().

        Asserted by READING IT BACK, per the fixture's
        `default_tier_must_be_asserted_by_reading_it_back` contract. A load that
        merely does not raise is equally true of an implementation that dropped
        the key at parse time, which is the defect this case exists to catch.
        """
        case = CASES["unknown_framework_key_is_retained_by_default"]
        expected = case["expected"]
        declared, undeclared = _partition_config_keys(case)
        assert declared and undeclared, "the fixture case must carry one of each kind of key"

        path = _MODES[mode](tmp_path, case)
        config = Config.load(path, validate=True)  # no `_config.strict` in this case
        assert expected["load_succeeds"] is True and expected["error_raised"] is False

        assert config.get(declared[0]) == expected["get_declared_key"], (
            f"[{mode}] declared key {declared[0]!r} did not survive the load"
        )
        assert config.get(undeclared[0]) == expected["get_undeclared_key"], (
            f"[{mode}] undeclared key {undeclared[0]!r} was discarded at parse time. "
            "§9.14: with `_config.strict` absent or false the key MUST be retained "
            "and readable — 'the operator wrote it and it vanished' is "
            "indistinguishable from 'the operator never wrote it'."
        )

    @pytest.mark.parametrize("mode", sorted(_MODES))
    def test_unknown_framework_key_is_rejected_under_strict(self, tmp_path: Path, mode: str) -> None:
        """Strict tier: CONFIG_INVALID naming EVERY offending key.

        The case declares two undeclared keys in two different sections on
        purpose. Asserting only that a load raised would be satisfied by an
        implementation that stops at the first one and makes the operator
        restart once per typo.
        """
        case = CASES["unknown_framework_key_is_rejected_under_strict"]
        expected = case["expected"]
        assert expected["load_succeeds"] is False

        path = _MODES[mode](tmp_path, case)
        with pytest.raises(ConfigError) as exc:
            Config.load(path, validate=True)

        assert exc.value.code == expected["error_code"]
        message = str(exc.value)
        missing = [key for key in expected["error_names_all_offending_keys"] if key not in message]
        assert missing == [], (
            f"[{mode}] the error named only some offending keys; missing {missing}.\n"
            f"Error was:\n{message}\n"
            "§9.14 step 3: collect every unknown key and raise once, so one "
            "restart shows the operator the whole problem."
        )

    def test_allow_unknown_does_not_relax_strict(self, tmp_path: Path) -> None:
        """`allow_unknown` is about top-level NAMESPACES, not section keys.

        §9.14: stretching one field across two granularities would make its
        meaning depend on where it is read, so `allow_unknown: true` must not
        buy back a key `strict` rejects.
        """
        case = CASES["unknown_framework_key_is_rejected_under_strict"]
        tree = _nest(case["config"])
        meta = tree.pop("_config")
        meta["allow_unknown"] = True
        document = {**_REQUIRED_SCAFFOLD, **tree, "_config": meta}
        path = tmp_path / "allow_unknown.yaml"
        path.write_text(yaml.safe_dump(document, sort_keys=False))

        with pytest.raises(ConfigError) as exc:
            Config.load(str(path), validate=True)
        assert exc.value.code == "CONFIG_INVALID"

    def test_strict_accepts_a_fully_declared_document(self, tmp_path: Path) -> None:
        """The counterweight: strict must not reject keys the schema declares.

        Without this, `_FRAMEWORK_SECTION_KEYS` could be emptied and every test
        above would still pass.
        """
        document = {
            **_REQUIRED_SCAFFOLD,
            "_config": {"strict": True},
            "executor": {"max_call_depth": 7, "default_timeout": 1000},
            "acl": {"root": "./acl", "default_effect": "deny", "audit": {"enabled": True}},
            "observability": {"tracing": {"enabled": False}},
            # sys_modules subsections are declared by sys-modules.schema.json,
            # not by SysModulesConfig; taking only the narrower of the two would
            # reject this document.
            "sys_modules": {"enabled": True, "usage": {"enabled": True}},
        }
        path = tmp_path / "strict_ok.yaml"
        path.write_text(yaml.safe_dump(document, sort_keys=False))

        config = Config.load(str(path), validate=True)
        assert config.get("executor.max_call_depth") == 7
        assert config.get("sys_modules.usage.enabled") is True


def _resolve_ref(node: Any, doc: dict[str, Any]) -> Any:
    """Follow local ``$ref`` pointers within *doc*."""
    seen = 0
    while isinstance(node, dict) and isinstance(node.get("$ref"), str) and node["$ref"].startswith("#/"):
        target: Any = doc
        for part in node["$ref"].lstrip("#/").split("/"):
            target = target[part]
        node = target
        seen += 1
        assert seen <= 16, f"$ref cycle at {node}"
    return node


def _declared_keys(node: Any, doc: dict[str, Any]) -> set[str]:
    """Every property name a schema node declares, including combinator branches.

    `ExtensionsConfig` splits `root` and `roots` across a `oneOf`, so reading
    `properties` alone would report both as undeclared.
    """
    node = _resolve_ref(node, doc)
    if not isinstance(node, dict):
        return set()
    keys = set(node.get("properties") or {})
    for combinator in ("allOf", "anyOf", "oneOf"):
        for branch in node.get(combinator) or []:
            keys |= _declared_keys(branch, doc)
    return keys


def test_framework_section_map_is_derived_from_the_canonical_schema() -> None:
    """`_FRAMEWORK_SECTION_KEYS` must equal what the canonical schemas declare.

    This is the drift guard the runtime table depends on: the schema files ship
    with the spec repo, not with this package, so the enforcement in
    `_collect_unknown_framework_keys` reads a mirror. A section added to
    `apcore-config.schema.json` must fail here rather than go unenforced.
    """
    schemas = fixtures_dir().parent.parent / "schemas"
    config_schema = json.loads((schemas / "apcore-config.schema.json").read_text())

    derived = {}
    for section, node in config_schema["properties"].items():
        keys = _declared_keys(node, config_schema)
        if keys:  # `version` / `$schema` are scalars, not sections
            derived[section] = keys

    sys_modules_schema = json.loads((schemas / "sys-modules.schema.json").read_text())
    derived["sys_modules"] = derived.get("sys_modules", set()) | set(sys_modules_schema.get("properties") or {})

    actual = {section: set(keys) for section, keys in _FRAMEWORK_SECTION_KEYS.items()}
    assert actual == derived, (
        "_FRAMEWORK_SECTION_KEYS has drifted from schemas/apcore-config.schema.json.\n"
        f"  sections only in the schema: {sorted(set(derived) - set(actual))}\n"
        f"  sections only in the table:  {sorted(set(actual) - set(derived))}\n"
        "  key differences: "
        + str({s: sorted(derived[s] ^ actual[s]) for s in set(derived) & set(actual) if derived[s] != actual[s]})
    )


def test_every_section_the_schema_closes_is_enforced() -> None:
    """Guard the guard: every section is `additionalProperties: false` upstream.

    If a section ever stops being closed, enforcing closedness against it under
    strict becomes a rejection the canonical schema does not back.
    """
    schemas = fixtures_dir().parent.parent / "schemas"
    config_schema = json.loads((schemas / "apcore-config.schema.json").read_text())
    open_sections = []
    for section in _FRAMEWORK_SECTION_KEYS:
        node = _resolve_ref(config_schema["properties"][section], config_schema)
        closed = node.get("additionalProperties") is False or node.get("unevaluatedProperties") is False
        if not closed:
            open_sections.append(section)
    assert open_sections == [], (
        f"these sections are enforced as closed but the canonical schema leaves them open: {open_sections}"
    )
