"""Conformance tests for registry on_load ordering invariants (apcore #65) fixture."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from apcore.errors import InvalidInputError
from apcore.events.emitter import ApCoreEvent, EventEmitter
from apcore.registry import Registry


def _fixture_path() -> Path:
    env = os.environ.get("APCORE_SPEC_REPO")
    if env:
        return Path(env) / "conformance" / "fixtures" / "registry_load_ordering.json"
    repo_root = Path(__file__).resolve().parent.parent.parent
    return repo_root.parent / "apcore" / "conformance" / "fixtures" / "registry_load_ordering.json"


def _load_fixture() -> dict:
    with _fixture_path().open() as f:
        return json.load(f)


@pytest.fixture
def fixture_data() -> dict:
    return _load_fixture()


def test_fixture_visibility_after_successful_on_load(fixture_data: dict) -> None:
    """Case: visibility_after_successful_on_load."""
    case = next(c for c in fixture_data["test_cases"] if c["id"] == "visibility_after_successful_on_load")
    setup = case["setup"]["module"]
    expected = case["expected"]

    delay_ms = setup.get("on_load_delay_ms", 0)
    mod_id = setup["id"]
    reg = Registry()

    # Capture visibility at 25ms (mid on_load)
    concurrent_list_result: list[bool] = []
    concurrent_get_result: list[Any] = []

    class _SpyModule:
        data: dict[str, Any] = {}

        def on_load(self) -> None:
            time.sleep(delay_ms / 2 / 1000)
            concurrent_list_result.append(mod_id in reg.list())
            concurrent_get_result.append(reg.get(mod_id))
            time.sleep(delay_ms / 2 / 1000)
            _SpyModule.data["_test.warmed"] = True

    _SpyModule.data.clear()
    module = _SpyModule()
    reg.register(mod_id, module)

    # After register() returns, module IS visible
    assert expected["registration_succeeds"] is True
    if expected["post_register_visible"]:
        assert mod_id in reg.list()
        assert reg.get(mod_id) is not None

    # During on_load, module was NOT visible
    if not expected["concurrent_check_visible"]:
        assert concurrent_list_result == [False]
        assert concurrent_get_result == [None]

    if expected.get("on_load_observed_data"):
        for key, val in expected["on_load_observed_data"].items():
            assert _SpyModule.data.get(key) == val


def test_fixture_callback_failure_blocks_visibility(fixture_data: dict) -> None:
    """Case: callback_failure_blocks_visibility."""
    case = next(c for c in fixture_data["test_cases"] if c["id"] == "callback_failure_blocks_visibility")
    setup = case["setup"]["module"]
    expected = case["expected"]

    mod_id = setup["id"]
    error_cfg = setup["on_load_raises"]

    emitter = EventEmitter()
    reg = Registry()
    reg.set_event_emitter(emitter)

    load_failed_events: list[ApCoreEvent] = []

    class _DLQRecorder:
        async def on_event(self, event: ApCoreEvent) -> None:
            if event.event_type == "apcore.registry.module_load_failed":
                load_failed_events.append(event)

    emitter.subscribe(_DLQRecorder())

    class _FailingModule:
        def on_load(self) -> None:
            raise ConnectionError(error_cfg["message"])

    with pytest.raises(ConnectionError, match=error_cfg["message"]):
        reg.register(mod_id, _FailingModule())

    emitter.flush(timeout=5.0)
    emitter.shutdown()

    assert mod_id not in reg.list()
    assert reg.get(mod_id) is None

    if expected.get("load_failed_event_emitted"):
        assert len(load_failed_events) == 1
        evt = load_failed_events[0]
        expected_evt = expected["load_failed_event"]
        assert evt.event_type == expected_evt["event_type"]
        data_contains = expected_evt["data_contains"]
        assert evt.data["module_id"] == data_contains["module_id"]
        assert evt.data["error_type"] == data_contains["error_type"]
        assert data_contains["error_message"] in evt.data["error_message"]
        for key in expected_evt["data_required_keys"]:
            assert key in evt.data, f"load_failed event missing: {key}"


def test_fixture_concurrent_same_id_rejects_duplicate(fixture_data: dict) -> None:
    """Case: concurrent_same_id_rejects_duplicate."""
    case = next(c for c in fixture_data["test_cases"] if c["id"] == "concurrent_same_id_rejects_duplicate")
    setup = case["setup"]
    expected = case["expected"]

    reg = Registry()
    results: dict[str, Any] = {"success": 0, "errors": []}
    lock = threading.Lock()
    delay_ms = setup["module_a"].get("on_load_delay_ms", 0)

    class _SlowModule:
        def on_load(self) -> None:
            time.sleep(delay_ms / 1000)

    def do_register() -> None:
        try:
            mod_id = setup["module_a"]["id"]
            reg.register(mod_id, _SlowModule())
            with lock:
                results["success"] += 1
        except InvalidInputError as e:
            with lock:
                results["errors"].append(e)

    t1 = threading.Thread(target=do_register)
    t2 = threading.Thread(target=do_register)
    t1.start()
    t2.start()
    t1.join(timeout=5.0)
    t2.join(timeout=5.0)

    assert results["success"] == 1
    assert len(results["errors"]) == 1
    assert results["errors"][0].code == expected["raised_error_code"]

    mod_id = setup["module_a"]["id"]
    if expected["post_register_visible"]:
        assert reg.get(mod_id) is not None
    assert len(reg.list()) == expected["post_register_count"]


def test_fixture_concurrent_distinct_ids_run_in_parallel(fixture_data: dict) -> None:
    """Case: concurrent_distinct_ids_run_in_parallel."""
    case = next(c for c in fixture_data["test_cases"] if c["id"] == "concurrent_distinct_ids_run_in_parallel")
    setup = case["setup"]
    expected = case["expected"]

    reg = Registry()

    def make_slow_module(delay_ms: int):
        class _SM:
            def on_load(self) -> None:
                time.sleep(delay_ms / 1000)

        return _SM()

    start = time.monotonic()
    t1 = threading.Thread(
        target=reg.register,
        args=(setup["module_x"]["id"], make_slow_module(setup["module_x"]["on_load_delay_ms"])),
    )
    t2 = threading.Thread(
        target=reg.register,
        args=(setup["module_y"]["id"], make_slow_module(setup["module_y"]["on_load_delay_ms"])),
    )
    t1.start()
    t2.start()
    t1.join(timeout=5.0)
    t2.join(timeout=5.0)
    elapsed_ms = (time.monotonic() - start) * 1000

    assert reg.get(setup["module_x"]["id"]) is not None
    assert reg.get(setup["module_y"]["id"]) is not None
    assert len(reg.list()) == expected["post_register_count"]
    assert elapsed_ms < expected["wall_clock_ms_less_than"], (
        f"Expected parallel execution < {expected['wall_clock_ms_less_than']}ms, " f"got {elapsed_ms:.0f}ms"
    )
