"""Conformance tests for event delivery semantics (apcore #61) fixture."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from apcore.events.emitter import ApCoreEvent, EventEmitter, _DLQ_EVENT_TYPE
from apcore.events.retry import EventRetryConfig


def _fixture_path() -> Path:
    env = os.environ.get("APCORE_SPEC_REPO")
    if env:
        return Path(env) / "conformance" / "fixtures" / "event_delivery_semantics.json"
    repo_root = Path(__file__).resolve().parent.parent.parent
    return repo_root.parent / "apcore" / "conformance" / "fixtures" / "event_delivery_semantics.json"


def _load_fixture() -> dict:
    with _fixture_path().open() as f:
        return json.load(f)


def _make_event(name: str, payload: dict) -> ApCoreEvent:
    return ApCoreEvent(
        event_type=name,
        module_id=None,
        timestamp="2026-01-01T00:00:00Z",
        severity="info",
        data=payload,
    )


class _CountingSubscriber:
    def __init__(
        self,
        subscriber_id: str,
        fail_attempts,
        retry: EventRetryConfig,
        event_pattern: str = "*",
        subscriber_type: str = "counting",
    ) -> None:
        self.subscriber_id = subscriber_id
        self.subscriber_type = subscriber_type
        self.retry = retry
        self.event_pattern = event_pattern
        self._fail_attempts = fail_attempts  # "all" or list of 1-based attempt numbers
        self._call_count = 0

    async def on_event(self, event: ApCoreEvent) -> None:
        self._call_count += 1
        if self._fail_attempts == "all":
            raise RuntimeError(f"Simulated permanent failure (attempt {self._call_count})")
        if isinstance(self._fail_attempts, list) and self._call_count in self._fail_attempts:
            raise RuntimeError(f"Simulated transient failure (attempt {self._call_count})")

    @property
    def call_count(self) -> int:
        return self._call_count


class _DLQRecorder:
    def __init__(self) -> None:
        self.subscriber_id = "dlq-conformance-recorder"
        self.event_pattern = _DLQ_EVENT_TYPE
        self.retry = EventRetryConfig()
        self.received: list[ApCoreEvent] = []

    async def on_event(self, event: ApCoreEvent) -> None:
        self.received.append(event)


@pytest.fixture
def fixture_data() -> dict:
    return _load_fixture()


def _build_retry(retry_cfg: dict) -> EventRetryConfig:
    return EventRetryConfig(
        max_attempts=retry_cfg.get("max_attempts", 3),
        initial_backoff_ms=retry_cfg.get("initial_backoff_ms", 100),
        max_backoff_ms=retry_cfg.get("max_backoff_ms", 30_000),
        backoff_multiplier=retry_cfg.get("backoff_multiplier", 2.0),
    )


def test_fixture_retry_succeeds_before_exhaustion(fixture_data: dict) -> None:
    """Case: retry_succeeds_before_exhaustion."""
    case = next(c for c in fixture_data["test_cases"] if c["id"] == "retry_succeeds_before_exhaustion")
    setup = case["setup"]["subscriber"]
    expected = case["expected"]

    retry = _build_retry(setup["retry"])
    fail_attempts = setup["fail_attempts"]

    emitter = EventEmitter()
    dlq = _DLQRecorder()
    emitter.subscribe(dlq)

    sub = _CountingSubscriber(
        subscriber_id=setup["id"],
        fail_attempts=fail_attempts,
        retry=retry,
    )
    emitter.subscribe(sub)

    trigger = case["trigger"]["event"]
    emitter.emit(_make_event(trigger["name"], trigger.get("payload", {})))
    emitter.flush(timeout=10.0)
    emitter.shutdown()

    assert sub.call_count == expected["attempt_count"]
    assert (len(dlq.received) > 0) == expected["dlq_event_emitted"]


def test_fixture_permanent_failure_emits_dlq_event(fixture_data: dict) -> None:
    """Case: permanent_failure_emits_dlq_event."""
    case = next(c for c in fixture_data["test_cases"] if c["id"] == "permanent_failure_emits_dlq_event")
    setup = case["setup"]["subscriber"]
    expected = case["expected"]

    retry = _build_retry(setup["retry"])

    emitter = EventEmitter()
    dlq = _DLQRecorder()
    emitter.subscribe(dlq)

    sub = _CountingSubscriber(
        subscriber_id=setup["id"],
        fail_attempts=setup["fail_attempts"],
        retry=retry,
        subscriber_type=setup.get("type", "counting"),
    )
    emitter.subscribe(sub)

    trigger = case["trigger"]["event"]
    emitter.emit(_make_event(trigger["name"], trigger.get("payload", {})))
    emitter.flush(timeout=10.0)
    emitter.shutdown()

    assert sub.call_count == expected["attempt_count"]
    assert (len(dlq.received) > 0) == expected["dlq_event_emitted"]

    if expected["dlq_event_emitted"]:
        dlq_event = dlq.received[0]
        expected_dlq = expected["dlq_event"]
        assert dlq_event.event_type == expected_dlq["event_type"]
        data_contains = expected_dlq["data_contains"]
        assert dlq_event.data["subscriber_type"] == data_contains["subscriber_type"]
        assert dlq_event.data["subscriber_id"] == data_contains["subscriber_id"]
        assert dlq_event.data["attempt_count"] == data_contains["attempt_count"]
        assert dlq_event.data["original_event"]["name"] == data_contains["original_event"]["name"]
        for key in expected_dlq["data_required_keys"]:
            assert key in dlq_event.data, f"DLQ event missing required key: {key}"


def test_fixture_dlq_event_subscriber_failure_not_retried(fixture_data: dict) -> None:
    """Case: dlq_event_subscriber_failure_is_not_retried."""

    case = next(c for c in fixture_data["test_cases"] if c["id"] == "dlq_event_subscriber_failure_is_not_retried")
    setup = case["setup"]
    expected = case["expected"]

    primary_cfg = setup["primary_subscriber"]
    dlq_cfg = setup["dlq_subscriber"]

    emitter = EventEmitter()
    dlq_recorder = _DLQRecorder()
    emitter.subscribe(dlq_recorder)

    primary = _CountingSubscriber(
        subscriber_id=primary_cfg["id"],
        fail_attempts=primary_cfg["fail_attempts"],
        retry=_build_retry(primary_cfg["retry"]),
    )
    emitter.subscribe(primary)

    dlq_sub_calls: list[int] = []

    class _BreakingDLQSub:
        subscriber_id = dlq_cfg["id"]
        event_pattern = dlq_cfg["event_pattern"]
        retry = _build_retry(dlq_cfg["retry"])

        async def on_event(self, evt: ApCoreEvent) -> None:
            dlq_sub_calls.append(1)
            raise RuntimeError("dlq subscriber also broken")

    emitter.subscribe(_BreakingDLQSub())

    trigger = case["trigger"]["event"]
    emitter.emit(_make_event(trigger["name"], trigger.get("payload", {})))
    emitter.flush(timeout=10.0)
    emitter.shutdown()

    assert primary.call_count == expected["primary_attempt_count"]
    assert (len(dlq_recorder.received) > 0) == expected["dlq_event_emitted"]
    assert len(dlq_sub_calls) == expected["dlq_subscriber_attempt_count"]
    # No second-order DLQ event (dlq_recorder only gets the DLQ from primary failure)
    second_order = [e for e in dlq_recorder.received if "broken-dlq" in str(e.data.get("subscriber_id", ""))]
    assert len(second_order) == 0


def test_fixture_subscriber_id_generated_when_omitted(fixture_data: dict) -> None:
    """Case: subscriber_id_sdk_generated_when_omitted."""
    from apcore.events.subscribers import StdoutSubscriber

    case = next(c for c in fixture_data["test_cases"] if c["id"] == "subscriber_id_sdk_generated_when_omitted")
    expected = case["expected"]

    # Create two stdout subscribers without explicit IDs
    sub1 = StdoutSubscriber()
    sub2 = StdoutSubscriber()

    assert sub1.subscriber_id != sub2.subscriber_id
    pattern = expected["subscriber_ids_pattern"]
    assert re.match(pattern, sub1.subscriber_id), f"{sub1.subscriber_id!r} doesn't match {pattern}"
    assert re.match(pattern, sub2.subscriber_id), f"{sub2.subscriber_id!r} doesn't match {pattern}"
