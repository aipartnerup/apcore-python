"""Cross-language driver for ``bindings_dir_resolution.json``.

PROTOCOL_SPEC §5.12.6 (spec v1.35.0, apcore#114): a binding loader invoked
**without an explicit directory argument** resolves the scan directory from
``bindings.dir`` under §9.2 precedence (``APCORE_BINDINGS_DIR`` > config file >
default ``./bindings``), matches candidates against ``bindings.pattern`` through
the same chain (default ``*.binding.yaml``), yields to an explicit argument when
one is given, and **MUST NOT** be triggered automatically at client or framework
initialisation.

**The defect this pins is a MUST with no subject.** Through v1.34.0 the section
read "if ``bindings.dir`` is configured, implementations MUST scan files
matching ``pattern`` in that directory" and never said *who* scans or *when*. No
SDK satisfied it: ``bindings.dir`` was registered in all three key surfaces and
read by no code path, while ``BindingLoader`` was exported public API in all
three and called from no internal one. v1.35.0 names the subject (the loader)
and the trigger (an invocation that does not name a directory), which is what
makes the requirement testable at all.

**What makes a case here discriminating, and why the pre-existing tests are
not.** Every ``load_binding_dir`` test that predates apcore#114 passes an
explicit directory, and that path behaves identically before and after the fix —
so it proves nothing. The fixture's ``no_explicit_argument`` clause is therefore
load-bearing: cases with ``explicit_dir: null`` invoke the loader with the
directory argument genuinely *absent*, never with a directory this file computed
for it. ``config_construction`` is load-bearing for the same reason: each case's
``config_file`` block is written to a real file on disk and loaded through
``Config``, because the FILE tier is the one that was broken, and an in-memory
mapping bypasses it.

``env_isolation`` is honoured by :func:`_isolated_environment`, which *deletes*
every ``APCORE_*`` variable rather than blanking it — §9.2 makes an empty string
a valid override, so ``APCORE_BINDINGS_DIR=""`` would shadow the config file
with the empty path rather than stand down.

The loader is driven through ``BindingLoader.load_binding_dir``, the public
entry point an application calls. The fixture's ``entry_point`` clause forbids
reaching into a private resolution helper: §5.12.6's subject is the loader *as
invoked*, and a helper that computes the right directory while the public entry
point ignores it satisfies nothing.

Two divergences between this SDK and the fixture are recorded here as strict
xfails rather than papered over — see
``test_missing_configured_dir_is_not_an_error`` and
``test_fixture_binding_descriptor_uses_the_spec_field_spelling``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from apcore.bindings import BindingLoader
from apcore.client import APCore
from apcore.config import Config
from apcore.errors import BindingFileInvalidError
from apcore.registry import Registry

from .canonical_fixtures import case_ids, load_fixture, reject_unknown_expectations

FIXTURE = "bindings_dir_resolution.json"

_FIXTURE = load_fixture(FIXTURE)
_CASES: dict[str, dict[str, Any]] = {case["id"]: case for case in _FIXTURE["test_cases"]}

#: The binding descriptor the fixture writes into every case's layout. Its
#: ``module_id`` is what ``loaded_module_ids`` reports.
_BINDING_FILE: dict[str, Any] = _FIXTURE["binding_file"]

#: The descriptor's declared module ID (``greet``), which every winning layout
#: also uses as the *filename* of the file in the directory that must be
#: scanned. See :func:`_module_id_for`.
_FIXTURE_MODULE_ID: str = _BINDING_FILE["bindings"][0]["module_id"]

#: A real callable for the descriptor's target to resolve to. The fixture names
#: ``fixture_targets:greet``, which is a placeholder — the spec repo ships no
#: such module, and each SDK supplies its own. ``auto_schema: true`` means the
#: target must carry type annotations, so this points at the annotated helper
#: the rest of this repo's binding tests use.
_REAL_TARGET = "binding_helpers:typed_function"


def _case(case_id: str) -> dict[str, Any]:
    assert case_id in _CASES, f"canonical fixture {FIXTURE} no longer defines case {case_id!r}"
    return _CASES[case_id]


# ---------------------------------------------------------------------------
# Environment isolation (driver_contract: env_isolation)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Delete every ``APCORE_*`` variable; a case's ``env`` block puts its own back.

    ``delenv``, never ``setenv(name, "")``. §9.2 lowers any ``APCORE_*``
    variable — including one holding the empty string — into a config override,
    so blanking ``APCORE_BINDINGS_DIR`` sets ``bindings.dir`` to ``""`` and
    shadows the config file rather than standing aside. An inherited
    ``APCORE_BINDINGS_DIR`` would turn the discriminating case into the case
    that already passes; an inherited ``APCORE_CONFIG_FILE`` would point
    discovery somewhere else entirely.
    """
    for name in [n for n in os.environ if n.startswith("APCORE_")]:
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# Building a case's layout on disk
# ---------------------------------------------------------------------------


def _binding_document(module_id: str, *, fixture_only: bool = False) -> dict[str, Any]:
    """The fixture's ``binding_file`` block as a YAML-ready mapping.

    With ``fixture_only`` the block is returned verbatim under the fixture's own
    ``module_id``, which is what
    ``test_fixture_binding_descriptor_uses_the_spec_field_spelling`` offers to
    the loader. Otherwise the entry is translated into the field spelling this
    SDK's ``BindingLoader`` accepts:

    * ``target_id`` → ``target``. PROTOCOL_SPEC §5.12.2 makes ``target_id`` the
      required binding-item field and this fixture uses it; this SDK's loader
      requires ``target``, as does the older ``binding_errors.json`` fixture.
      That divergence is real and is pinned by its own strict xfail below — it
      is not what *this* fixture asserts, whose scope is which directory and
      pattern a loader resolves.
    * the placeholder target module is replaced by a real annotated callable, so
      that ``auto_schema: true`` has type information to work from.
    """
    document = {"bindings": [dict(entry) for entry in _BINDING_FILE["bindings"]]}
    for entry in document["bindings"]:
        entry["module_id"] = module_id
        if not fixture_only:
            entry["target"] = _REAL_TARGET
            entry.pop("target_id", None)
    return document


def _module_id_for(relative: str) -> str:
    """The module ID a binding file at *relative* registers: its filename stem.

    This is the fixture's ``scan_observation`` mechanism and it is not
    cosmetic. Every ``fs`` entry points at the SAME ``binding_file`` descriptor,
    so if every file registered the descriptor's ``greet`` verbatim then
    ``loaded_module_ids: ["greet"]`` would be satisfied by scanning ANY of the
    candidate directories and would discriminate nothing. The layouts name the
    decoys deliberately — ``from_file/file_side.binding.yaml``,
    ``from_env/env_side.binding.yaml``, ``custom_bindings/decoy.binding.yaml``
    — and place ``greet.*`` only in the directory that must win. Naming each
    module after its file is therefore what turns ``loaded_module_ids`` into an
    observation of *which directory was enumerated*, read off the loader's
    result rather than off the config value this driver supplied.
    """
    return relative.rsplit("/", 1)[-1].split(".", 1)[0]


def _write_layout(root: Path, case: dict[str, Any]) -> None:
    """Materialise a case's ``config_file`` and ``fs`` blocks under *root*."""
    config_file = case.get("config_file")
    if config_file is not None:
        path = root / config_file["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(config_file["content"] or {}, sort_keys=False), encoding="utf-8")

    for relative, content in (case.get("fs") or {}).items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        assert content == "binding_file", f"unhandled fs content marker {content!r} in case {case['id']!r}"
        path.write_text(yaml.safe_dump(_binding_document(_module_id_for(relative)), sort_keys=False), encoding="utf-8")


def _load_config(root: Path, case: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> Config | None:
    """Load the case's config file through ``Config``, with its ``env`` applied.

    ``validate=False``: the fixture's ``config_file.content`` blocks declare no
    ``version``, which §9.1's required-field check rejects. The requirement
    under test is that the FILE tier of ``bindings.dir`` reaches the loader, and
    validation of unrelated required fields would stop every case before it
    started.
    """
    for name, value in (case.get("env") or {}).items():
        monkeypatch.setenv(name, value)

    config_file = case.get("config_file")
    if config_file is None:
        return None
    return Config.load(str(root / config_file["path"]), validate=False)


def _module_ids(modules: list[Any]) -> list[str]:
    return sorted(module.module_id for module in modules)


# ---------------------------------------------------------------------------
# Clause 1 — resolve the directory from ``bindings.dir``
# ---------------------------------------------------------------------------


def _drive(
    case_id: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    known_expectations: set[str] | None = None,
) -> tuple[dict[str, Any], list[Any]]:
    """Build the case's layout, invoke the loader as the case dictates, return its result.

    The explicit-directory argument is passed **only** when the case declares
    one. When ``explicit_dir`` is null the call omits the parameter entirely
    rather than passing ``None`` alongside a directory this driver worked out —
    the fixture's ``no_explicit_argument`` clause, which is the difference
    between exercising §5.12.6 and exercising the call shape that already
    worked.
    """
    case = _case(case_id)
    reject_unknown_expectations(FIXTURE, case, known_expectations or {"expected"})

    monkeypatch.chdir(tmp_path)
    _write_layout(tmp_path, case)
    config = _load_config(tmp_path, case, monkeypatch)

    registry = Registry()
    loader = BindingLoader()

    explicit_dir = case.get("explicit_dir")
    if explicit_dir is None:
        modules = loader.load_binding_dir(registry=registry, config=config)
    else:
        modules = loader.load_binding_dir(explicit_dir, registry, config=config)

    return case, modules


def test_config_file_dir_is_scanned_with_env_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """THE discriminating case: file tier, env unset, no explicit argument.

    This is the case no SDK passed before v1.35.0 and the only one that
    separates the corrected behaviour from the status quo. Every pre-existing
    loader test passes an explicit directory (works either way) and TypeScript's
    pre-existing raw ``process.env`` read covered the env tier alone.
    """
    case, modules = _drive("config_file_dir_is_scanned_with_env_unset", tmp_path, monkeypatch)

    assert case["expected"]["scanned"] is True
    assert _module_ids(modules) == case["expected"]["loaded_module_ids"]
    _assert_scan_was_of(case, modules)


def test_default_dir_when_key_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No ``bindings.dir`` anywhere and no explicit argument: ``./bindings`` applies."""
    case, modules = _drive("default_dir_when_key_absent", tmp_path, monkeypatch)

    assert case["expected"]["scanned"] is True
    assert _module_ids(modules) == case["expected"]["loaded_module_ids"]
    _assert_scan_was_of(case, modules)


def test_env_overrides_config_file_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """§9.2 precedence, top tier: ``APCORE_BINDINGS_DIR`` beats the config file.

    Both candidate directories exist and each holds a binding file, so exactly
    one answer passes. A layout where only the env directory existed would pass
    on an implementation that ignores the env tier and simply finds nothing.
    """
    case, modules = _drive("env_overrides_config_file_dir", tmp_path, monkeypatch)

    assert case["expected"]["scanned"] is True
    assert _module_ids(modules) == case["expected"]["loaded_module_ids"]
    _assert_scan_was_of(case, modules)


def test_explicit_argument_wins_over_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """§5.12.6 clause 2: explicit > env > file > default, with all three present."""
    case, modules = _drive("explicit_argument_wins_over_config", tmp_path, monkeypatch)

    assert case["expected"]["scanned"] is True
    assert _module_ids(modules) == case["expected"]["loaded_module_ids"]
    _assert_scan_was_of(case, modules)


def test_config_file_pattern_is_honoured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``bindings.pattern`` travels the same chain, not a loader-signature default.

    The directory holds one file matching the configured pattern and one
    matching only the ``*.binding.yaml`` default, so an implementation that
    keeps the pattern in its signature loads the *wrong* file rather than none —
    a distinguishable wrong answer instead of an empty result.
    """
    case, modules = _drive("config_file_pattern_is_honoured", tmp_path, monkeypatch)

    assert case["expected"]["scanned"] is True
    assert _module_ids(modules) == case["expected"]["loaded_module_ids"]
    _assert_scan_was_of(case, modules)


def test_default_pattern_when_key_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No ``bindings.pattern``: ``*.binding.yaml`` applies and a sibling is skipped."""
    case, modules = _drive("default_pattern_when_key_absent", tmp_path, monkeypatch)

    assert case["expected"]["scanned"] is True
    assert _module_ids(modules) == case["expected"]["loaded_module_ids"]
    _assert_scan_was_of(case, modules)


def _assert_scan_was_of(case: dict[str, Any], modules: list[Any]) -> None:
    """The enumerated directory, read off the loader's result and the layout.

    The fixture's ``scan_observation`` clause forbids reading ``scanned_dir``
    back off the config value this driver supplied. It is derived instead from
    the two things the fixture *does* provide: the module IDs the loader
    produced, and the layout that says which file sits in which directory. Every
    ID produced must belong to a file under ``scanned_dir``, and no ID belonging
    to a file in any other candidate directory may appear — which is what makes
    the multi-directory layouts (``from_file`` / ``from_env`` /
    ``from_argument``) prove that one directory was enumerated rather than
    merely that one file was found.
    """
    scanned_dir = case["expected"]["scanned_dir"]
    produced = set(_module_ids(modules))

    inside = {_module_id_for(rel) for rel in (case.get("fs") or {}) if rel.rsplit("/", 1)[0] == scanned_dir}
    outside = {_module_id_for(rel) for rel in (case.get("fs") or {}) if rel.rsplit("/", 1)[0] != scanned_dir}

    assert produced <= inside, (
        f"loader produced {sorted(produced - inside)}, which no file under {scanned_dir!r} declares"
    )
    assert produced & outside == set(), (
        f"loader enumerated a directory other than {scanned_dir!r}: it produced {sorted(produced & outside)}"
    )


# ---------------------------------------------------------------------------
# A missing directory — the one behavioural divergence in this fixture
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "SDK/fixture divergence, reported not papered over: the fixture states that a configured "
        "bindings.dir which does not exist yields an empty result and no error ('discovery is "
        "opportunistic; the key carries a default most projects never create'). apcore-python "
        "raises BindingFileInvalidError instead — see tests/test_bindings_dir_resolution.py "
        "::test_missing_configured_dir_raises, which pins the raise deliberately. §5.12.6 states "
        "no outcome for a missing directory, so neither side is provably wrong; this xfail turns "
        "green->red the day the two agree."
    ),
)
def test_missing_configured_dir_is_not_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A configured ``bindings.dir`` that does not exist: empty result, no error."""
    case, modules = _drive("missing_configured_dir_is_not_an_error", tmp_path, monkeypatch)

    assert case["expected"]["scanned"] is True
    assert case["expected"]["error"] is None
    assert _module_ids(modules) == case["expected"]["loaded_module_ids"]


def test_missing_configured_dir_divergence_is_exactly_a_raise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The shape of the divergence above, pinned so it cannot drift unnoticed.

    The xfail says "this SDK does not return an empty result". This says what it
    does instead — raises ``BindingFileInvalidError`` naming the resolved
    directory — so that an SDK change from "raises" to some third behaviour is a
    failure here rather than a still-xfailing test.
    """
    case = _case("missing_configured_dir_is_not_an_error")
    monkeypatch.chdir(tmp_path)
    _write_layout(tmp_path, case)
    config = _load_config(tmp_path, case, monkeypatch)

    with pytest.raises(BindingFileInvalidError) as raised:
        BindingLoader().load_binding_dir(registry=Registry(), config=config)

    assert case["expected"]["scanned_dir"] in str(raised.value), (
        "the raise must still name the directory the loader resolved from bindings.dir; "
        "otherwise this SDK is not even reaching the configured directory"
    )


# ---------------------------------------------------------------------------
# Clause 3 — no scan at initialisation
# ---------------------------------------------------------------------------


def test_no_auto_scan_at_init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """§5.12.6 clause 3: constructing a client MUST NOT enumerate a binding directory.

    The fixture's ``no_startup_scan`` clause requires more than "construction
    succeeded", which passes trivially: the configured directory exists and
    holds a well-formed binding file that *would* load cleanly if anything
    scanned, and its module ID must be absent from the registry afterwards. This
    is the reading the pre-v1.35.0 wording invited, and adopting it would put
    filesystem I/O in every client's startup.
    """
    case = _case("no_auto_scan_at_init")
    reject_unknown_expectations(FIXTURE, case, {"expected"})
    assert case.get("invoke_loader") is False, "this case is defined by NOT invoking the loader"

    monkeypatch.chdir(tmp_path)
    _write_layout(tmp_path, case)
    config = _load_config(tmp_path, case, monkeypatch)

    # The file that would be loaded, proving the layout is not the reason
    # nothing was registered.
    would_load = sorted((tmp_path / "custom_bindings").glob("*.binding.yaml"))
    assert would_load, "layout is wrong: the case needs a loadable binding file under the configured dir"

    client = APCore(config=config)

    assert case["expected"]["scanned"] is False
    registered = client.registry.list()
    for module_id in (entry["module_id"] for entry in _BINDING_FILE["bindings"]):
        assert module_id not in registered, (
            f"client construction scanned a binding directory and registered {module_id!r}"
        )
    assert case["expected"]["registered_module_ids"] == [], "fixture expects an empty registry contribution"

    # And the loader still works when the application asks for it — clause 3
    # forbids the implicit scan, not the explicit one.
    modules = BindingLoader().load_binding_dir(registry=Registry(), config=config)
    assert _module_ids(modules) == [entry["module_id"] for entry in _BINDING_FILE["bindings"]]


# ---------------------------------------------------------------------------
# The fixture's binding descriptor field spelling
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "SDK/fixture divergence, reported not papered over: PROTOCOL_SPEC §5.12.2 makes 'target_id' "
        "the required binding-item field and bindings_dir_resolution.json uses it, but this SDK's "
        "BindingLoader requires 'target' — as does the older binding_errors.json fixture, so the "
        "spec repo's own fixtures disagree with each other. Not what this fixture asserts (its "
        "scope is which directory and pattern a loader resolves), so the other cases translate the "
        "spelling; this xfail keeps the divergence visible."
    ),
)
def test_fixture_binding_descriptor_uses_the_spec_field_spelling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fixture's ``binding_file`` block, verbatim, loads through this SDK."""
    monkeypatch.chdir(tmp_path)
    directory = tmp_path / "bindings"
    directory.mkdir()
    (directory / "greet.binding.yaml").write_text(
        yaml.safe_dump(_binding_document(_FIXTURE_MODULE_ID, fixture_only=True), sort_keys=False),
        encoding="utf-8",
    )

    modules = BindingLoader().load_binding_dir(registry=Registry())

    assert _module_ids(modules) == [entry["module_id"] for entry in _BINDING_FILE["bindings"]]


# ---------------------------------------------------------------------------
# Coverage cross-check and the runner contract
# ---------------------------------------------------------------------------


COVERED: dict[str, str] = {
    "config_file_dir_is_scanned_with_env_unset": "test_config_file_dir_is_scanned_with_env_unset",
    "default_dir_when_key_absent": "test_default_dir_when_key_absent",
    "env_overrides_config_file_dir": "test_env_overrides_config_file_dir",
    "explicit_argument_wins_over_config": "test_explicit_argument_wins_over_config",
    "config_file_pattern_is_honoured": "test_config_file_pattern_is_honoured",
    "default_pattern_when_key_absent": "test_default_pattern_when_key_absent",
    "missing_configured_dir_is_not_an_error": "test_missing_configured_dir_is_not_an_error",
    "no_auto_scan_at_init": "test_no_auto_scan_at_init",
}


def test_every_canonical_case_is_driven() -> None:
    """A case added upstream is a failure here, never a silent gap."""
    canonical = set(case_ids(FIXTURE))
    claimed = set(COVERED)
    assert canonical - claimed == set(), f"canonical fixture {FIXTURE} gained case(s) with no driver here"
    assert claimed - canonical == set(), f"this file claims case(s) {FIXTURE} no longer defines"


def test_every_claimed_driver_exists() -> None:
    import sys

    module = sys.modules[__name__]
    missing = [name for name in COVERED.values() if not hasattr(module, name)]
    assert missing == [], f"claimed driver function(s) not defined: {missing}"


def test_layout_convention_matches_the_fixture_descriptor() -> None:
    """The stem convention :func:`_module_id_for` uses is the fixture's own.

    In every case whose ``expected.loaded_module_ids`` is non-empty, the file
    the fixture places in ``scanned_dir`` is named after the descriptor's
    ``module_id`` and the expectation names exactly that ID. Pinning the
    correspondence here means a fixture that renames the descriptor or the
    winning file fails loudly instead of quietly making every case
    non-discriminating.
    """
    checked = 0
    for case in _FIXTURE["test_cases"]:
        expected_ids = (case.get("expected") or {}).get("loaded_module_ids")
        if not expected_ids:
            continue
        scanned_dir = case["expected"]["scanned_dir"]
        winners = [rel for rel in (case.get("fs") or {}) if rel.rsplit("/", 1)[0] == scanned_dir]
        stems = sorted({_module_id_for(rel) for rel in winners})
        assert _FIXTURE_MODULE_ID in stems, (
            f"case {case['id']!r}: no file under {scanned_dir!r} is named after the descriptor's "
            f"module_id {_FIXTURE_MODULE_ID!r}, so loaded_module_ids cannot discriminate"
        )
        assert expected_ids == [_FIXTURE_MODULE_ID]
        checked += 1
    assert checked >= 5, "expected most cases to state a non-empty loaded_module_ids"


def test_driver_contract_is_not_a_case() -> None:
    """``driver_contract`` and ``binding_file`` are runner inputs, not test cases."""
    assert "driver_contract" in _FIXTURE
    assert "binding_file" in _FIXTURE
    ids = set(case_ids(FIXTURE))
    assert "driver_contract" not in ids
    assert "binding_file" not in ids
    assert {
        "entry_point",
        "config_construction",
        "env_isolation",
        "no_explicit_argument",
        "scan_observation",
        "no_startup_scan",
    } <= set(_FIXTURE["driver_contract"])
