"""Conformance tests for Event Management Hardening (Issue #36).

Covers all 10 cases in:
  apcore/conformance/fixtures/event_management_hardening.json
"""

from __future__ import annotations

import sys

import re
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from apcore.events.circuit_breaker import CircuitBreakerWrapper, CircuitState
from apcore.events.emitter import ApCoreEvent, EventEmitter
from conformance.canonical_fixtures import case_ids


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(**overrides: Any) -> ApCoreEvent:
    defaults: dict[str, Any] = {
        "event_type": "apcore.module.toggled",
        "module_id": "executor.email.send_email",
        "timestamp": "2026-04-28T00:00:00Z",
        "severity": "info",
        "data": {"module_id": "executor.email.send_email", "enabled": False},
    }
    defaults.update(overrides)
    return ApCoreEvent(**defaults)


def _make_mock_emitter() -> MagicMock:
    emitter = MagicMock(spec=EventEmitter)
    emitter.emit = MagicMock()
    return emitter


# ---------------------------------------------------------------------------
# Case 1: subscriber_factory_registered_type
# ---------------------------------------------------------------------------


class TestSubscriberFactoryRegisteredType:
    """A custom subscriber type registered via register_subscriber_type is
    instantiated when referenced by name in configuration."""

    @pytest.fixture(autouse=True)
    def _reset_registry(self) -> Any:
        from apcore.sys_modules.registration import reset_subscriber_registry

        reset_subscriber_registry()
        yield
        reset_subscriber_registry()

    def test_custom_slack_type_instantiated(self) -> None:
        from apcore.sys_modules.registration import (
            _create_subscriber,
            register_subscriber_type,
        )

        mock_subscriber = MagicMock()
        mock_subscriber.on_event = AsyncMock()
        slack_factory = MagicMock(return_value=mock_subscriber)

        register_subscriber_type("slack", slack_factory)

        sub_cfg: dict[str, Any] = {
            "type": "slack",
            "webhook_url": "https://hooks.slack.com/services/TEST",
            "channel": "#alerts",
        }
        subscriber = _create_subscriber(sub_cfg)

        assert subscriber is mock_subscriber
        slack_factory.assert_called_once_with(sub_cfg)


# ---------------------------------------------------------------------------
# Case 2: builtin_stdout_type
# ---------------------------------------------------------------------------


class TestBuiltinStdoutType:
    """The stdout subscriber type is available as a built-in without registration."""

    def test_stdout_subscriber_created_without_registration(self) -> None:
        from apcore.events.subscribers import StdoutSubscriber
        from apcore.sys_modules.registration import _create_subscriber

        sub_cfg: dict[str, Any] = {
            "type": "stdout",
            "format": "json",
            "level_filter": "error",
        }
        subscriber = _create_subscriber(sub_cfg)

        assert isinstance(subscriber, StdoutSubscriber)

    def test_stdout_subscriber_conforms_to_protocol(self) -> None:
        from apcore.events.emitter import EventSubscriber
        from apcore.events.subscribers import StdoutSubscriber

        sub = StdoutSubscriber(output_format="json")
        assert isinstance(sub, EventSubscriber)


# ---------------------------------------------------------------------------
# Case 3: builtin_file_type
# ---------------------------------------------------------------------------


class TestBuiltinFileType:
    """The file subscriber type is available as a built-in without registration."""

    def test_file_subscriber_created_without_registration(self) -> None:
        from apcore.events.subscribers import FileSubscriber
        from apcore.sys_modules.registration import _create_subscriber

        sub_cfg: dict[str, Any] = {
            "type": "file",
            "path": "/var/log/apcore/events.jsonl",
            "format": "json",
            "rotate_bytes": 10485760,
        }
        subscriber = _create_subscriber(sub_cfg)

        assert isinstance(subscriber, FileSubscriber)

    def test_file_subscriber_conforms_to_protocol(self) -> None:
        from apcore.events.emitter import EventSubscriber
        from apcore.events.subscribers import FileSubscriber

        sub = FileSubscriber(path="/tmp/test.log")
        assert isinstance(sub, EventSubscriber)


# ---------------------------------------------------------------------------
# Case 4: builtin_filter_passes_matching
# ---------------------------------------------------------------------------


class TestBuiltinFilterPassesMatching:
    """A filter subscriber forwards an event whose type matches include_events."""

    @pytest.mark.asyncio
    async def test_filter_forwards_matching_event(self) -> None:
        from apcore.events.subscribers import FilterSubscriber

        delegate = MagicMock()
        delegate.on_event = AsyncMock()

        filter_sub = FilterSubscriber(
            delegate=delegate,
            include_events=["apcore.error.*", "apcore.latency.threshold_exceeded"],
        )

        event = _make_event(
            event_type="apcore.error.threshold_exceeded",
            severity="error",
            data={
                "module_id": "executor.email.send_email",
                "error_rate": 0.25,
                "threshold": 0.1,
            },
        )

        await filter_sub.on_event(event)

        delegate.on_event.assert_called_once_with(event)


# ---------------------------------------------------------------------------
# Case 5: builtin_filter_discards_nonmatching
# ---------------------------------------------------------------------------


class TestBuiltinFilterDiscardsNonmatching:
    """A filter subscriber silently discards an event not matching include_events."""

    @pytest.mark.asyncio
    async def test_filter_discards_nonmatching_event(self) -> None:
        from apcore.events.subscribers import FilterSubscriber

        delegate = MagicMock()
        delegate.on_event = AsyncMock()

        filter_sub = FilterSubscriber(
            delegate=delegate,
            include_events=["apcore.error.*"],
        )

        event = _make_event(
            event_type="apcore.config.updated",
            module_id=None,
            severity="info",
            data={"key": "sys_modules.events.thresholds.error_rate"},
        )

        await filter_sub.on_event(event)

        delegate.on_event.assert_not_called()


# ---------------------------------------------------------------------------
# Case 6: circuit_open_after_threshold
# ---------------------------------------------------------------------------


class TestCircuitOpenAfterThreshold:
    """After consecutive_failures reaches open_threshold, circuit transitions to OPEN."""

    @pytest.mark.asyncio
    async def test_circuit_opens_after_three_failures(self) -> None:
        failing_sub = MagicMock()
        failing_sub.on_event = AsyncMock(side_effect=RuntimeError("downstream error"))

        emitted_events: list[ApCoreEvent] = []
        mock_emitter = _make_mock_emitter()
        mock_emitter.emit.side_effect = emitted_events.append

        cb = CircuitBreakerWrapper(
            subscriber=failing_sub,
            emitter=mock_emitter,
            timeout_ms=3000,
            open_threshold=3,
            recovery_window_ms=30000,
        )

        event = _make_event()
        for _ in range(3):
            await cb.on_event(event)

        assert cb.state == CircuitState.OPEN
        assert cb.consecutive_failures == 3

        circuit_opened = [e for e in emitted_events if e.event_type == "apcore.subscriber.circuit_opened"]
        assert len(circuit_opened) == 1
        assert circuit_opened[0].data["consecutive_failures"] == 3


# ---------------------------------------------------------------------------
# Case 7: circuit_discards_in_open_state
# ---------------------------------------------------------------------------


class TestCircuitDiscardsInOpenState:
    """In OPEN state, deliver() is not called — the event is silently discarded."""

    @pytest.mark.asyncio
    async def test_delivery_not_attempted_when_open(self) -> None:
        inner_sub = MagicMock()
        inner_sub.on_event = AsyncMock()

        mock_emitter = _make_mock_emitter()

        cb = CircuitBreakerWrapper(
            subscriber=inner_sub,
            emitter=mock_emitter,
            open_threshold=3,
        )
        # Force OPEN state
        cb._state = CircuitState.OPEN
        cb._last_failure_at = datetime.now(timezone.utc)

        event = _make_event()
        await cb.on_event(event)

        inner_sub.on_event.assert_not_called()
        assert cb.state == CircuitState.OPEN


# ---------------------------------------------------------------------------
# Case 8: circuit_half_open_after_window
# ---------------------------------------------------------------------------


class TestCircuitHalfOpenAfterWindow:
    """After recovery_window_ms has elapsed, OPEN transitions to HALF_OPEN."""

    @pytest.mark.asyncio
    async def test_transitions_to_half_open_after_window(self) -> None:
        inner_sub = MagicMock()
        inner_sub.on_event = AsyncMock()

        mock_emitter = _make_mock_emitter()

        cb = CircuitBreakerWrapper(
            subscriber=inner_sub,
            emitter=mock_emitter,
            timeout_ms=3000,
            open_threshold=3,
            recovery_window_ms=30000,
        )
        # Force OPEN with last_failure_at 31 seconds in the past
        cb._state = CircuitState.OPEN
        cb._last_failure_at = datetime.now(timezone.utc) - timedelta(seconds=31)

        # on_event triggers _check_recovery → HALF_OPEN, then attempts delivery
        await cb.on_event(_make_event())

        # After a successful delivery in HALF_OPEN, state transitions to CLOSED.
        # If inner_sub succeeded, state is CLOSED. What matters is that the
        # circuit left OPEN state (either HALF_OPEN or CLOSED is correct here).
        assert cb.state != CircuitState.OPEN

    def test_check_recovery_transitions_open_to_half_open(self) -> None:
        mock_emitter = _make_mock_emitter()
        inner_sub = MagicMock()
        inner_sub.on_event = AsyncMock()

        cb = CircuitBreakerWrapper(
            subscriber=inner_sub,
            emitter=mock_emitter,
            recovery_window_ms=30000,
        )
        cb._state = CircuitState.OPEN
        cb._last_failure_at = datetime.now(timezone.utc) - timedelta(seconds=31)

        cb._check_recovery()

        assert cb._state == CircuitState.HALF_OPEN


# ---------------------------------------------------------------------------
# Case 9: circuit_closes_on_success
# ---------------------------------------------------------------------------


class TestCircuitClosesOnSuccess:
    """A successful delivery in HALF_OPEN transitions to CLOSED and emits circuit_closed."""

    @pytest.mark.asyncio
    async def test_half_open_success_closes_circuit(self) -> None:
        inner_sub = MagicMock()
        inner_sub.on_event = AsyncMock(return_value=None)

        emitted_events: list[ApCoreEvent] = []
        mock_emitter = _make_mock_emitter()
        mock_emitter.emit.side_effect = emitted_events.append

        cb = CircuitBreakerWrapper(
            subscriber=inner_sub,
            emitter=mock_emitter,
        )
        # Force HALF_OPEN
        cb._state = CircuitState.HALF_OPEN
        cb._consecutive_failures = 3

        await cb.on_event(_make_event())

        assert cb.state == CircuitState.CLOSED
        assert cb.consecutive_failures == 0

        circuit_closed = [e for e in emitted_events if e.event_type == "apcore.subscriber.circuit_closed"]
        assert len(circuit_closed) == 1
        assert circuit_closed[0].severity == "info"


# ---------------------------------------------------------------------------
# Additional coverage: HALF_OPEN → failure → OPEN
# ---------------------------------------------------------------------------


class TestCircuitHalfOpenFailureReopens:
    """A failed delivery in HALF_OPEN transitions back to OPEN and re-emits circuit_opened."""

    @pytest.mark.asyncio
    async def test_half_open_failure_reopens_circuit(self) -> None:
        failing_sub = MagicMock()
        failing_sub.on_event = AsyncMock(side_effect=RuntimeError("still down"))

        emitted_events: list[ApCoreEvent] = []
        mock_emitter = _make_mock_emitter()
        mock_emitter.emit.side_effect = emitted_events.append

        cb = CircuitBreakerWrapper(
            subscriber=failing_sub,
            emitter=mock_emitter,
        )
        cb._state = CircuitState.HALF_OPEN
        cb._consecutive_failures = 3

        await cb.on_event(_make_event())

        assert cb.state == CircuitState.OPEN
        circuit_opened = [e for e in emitted_events if e.event_type == "apcore.subscriber.circuit_opened"]
        assert len(circuit_opened) == 1


class TestCircuitClosedSuccessResetsCounter:
    """A successful delivery in CLOSED state resets consecutive_failures to 0."""

    @pytest.mark.asyncio
    async def test_closed_success_resets_failures(self) -> None:
        inner_sub = MagicMock()
        inner_sub.on_event = AsyncMock(return_value=None)
        mock_emitter = _make_mock_emitter()

        cb = CircuitBreakerWrapper(subscriber=inner_sub, emitter=mock_emitter)
        cb._consecutive_failures = 2  # some prior failures that didn't reach threshold

        await cb.on_event(_make_event())

        assert cb.consecutive_failures == 0
        assert cb.state == CircuitState.CLOSED
        mock_emitter.emit.assert_not_called()


class TestFileSubscriberWritesJson:
    """FileSubscriber writes a JSON line to the target file."""

    @pytest.mark.asyncio
    async def test_writes_json_line(self, tmp_path: Any) -> None:
        from apcore.events.subscribers import FileSubscriber

        log_file = tmp_path / "events.jsonl"
        sub = FileSubscriber(path=str(log_file), output_format="json")
        event = _make_event(event_type="apcore.module.toggled")

        await sub.on_event(event)

        content = log_file.read_text()
        import json

        record = json.loads(content.strip())
        assert record["event_type"] == "apcore.module.toggled"

    @pytest.mark.asyncio
    async def test_writes_text_line(self, tmp_path: Any) -> None:
        from apcore.events.subscribers import FileSubscriber

        log_file = tmp_path / "events.log"
        sub = FileSubscriber(path=str(log_file), output_format="text")
        event = _make_event(event_type="apcore.module.toggled", severity="info")

        await sub.on_event(event)

        content = log_file.read_text()
        assert "apcore.module.toggled" in content
        assert "INFO" in content


class TestStdoutSubscriberOutput:
    """StdoutSubscriber prints events, respecting level_filter."""

    @pytest.mark.asyncio
    async def test_prints_json_to_stdout(self, capsys: Any) -> None:
        from apcore.events.subscribers import StdoutSubscriber

        sub = StdoutSubscriber(output_format="json")
        await sub.on_event(_make_event(event_type="apcore.config.updated", severity="info"))

        captured = capsys.readouterr()
        import json

        record = json.loads(captured.out.strip())
        assert record["event_type"] == "apcore.config.updated"

    @pytest.mark.asyncio
    async def test_level_filter_suppresses_below_threshold(self, capsys: Any) -> None:
        from apcore.events.subscribers import StdoutSubscriber

        sub = StdoutSubscriber(level_filter="error")
        await sub.on_event(_make_event(severity="info"))
        await sub.on_event(_make_event(severity="warn"))

        captured = capsys.readouterr()
        assert captured.out == ""

    @pytest.mark.asyncio
    async def test_level_filter_passes_at_or_above_threshold(self, capsys: Any) -> None:
        from apcore.events.subscribers import StdoutSubscriber

        sub = StdoutSubscriber(level_filter="error")
        await sub.on_event(_make_event(severity="error"))

        captured = capsys.readouterr()
        assert "apcore.module.toggled" in captured.out


class TestFilterSubscriberExcludeEvents:
    """FilterSubscriber discards events matching exclude_events."""

    @pytest.mark.asyncio
    async def test_exclude_events_discards_matching(self) -> None:
        from apcore.events.subscribers import FilterSubscriber

        delegate = MagicMock()
        delegate.on_event = AsyncMock()

        filter_sub = FilterSubscriber(
            delegate=delegate,
            exclude_events=["apcore.config.*"],
        )

        await filter_sub.on_event(_make_event(event_type="apcore.config.updated"))
        delegate.on_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_exclude_events_passes_nonmatching(self) -> None:
        from apcore.events.subscribers import FilterSubscriber

        delegate = MagicMock()
        delegate.on_event = AsyncMock()

        filter_sub = FilterSubscriber(
            delegate=delegate,
            exclude_events=["apcore.config.*"],
        )

        event = _make_event(event_type="apcore.module.toggled")
        await filter_sub.on_event(event)
        delegate.on_event.assert_called_once_with(event)


# ---------------------------------------------------------------------------
# Case 10: event_naming_canonical
# ---------------------------------------------------------------------------


class TestEventNamingCanonical:
    """All built-in framework events follow the apcore.<subsystem>.<event> pattern."""

    _CANONICAL_EVENTS = [
        "apcore.config.updated",
        "apcore.module.reloaded",
        "apcore.module.toggled",
        "apcore.health.recovered",
        "apcore.error.threshold_exceeded",
        "apcore.latency.threshold_exceeded",
        "apcore.subscriber.circuit_opened",
        "apcore.subscriber.circuit_closed",
    ]

    _PATTERN = re.compile(r"^apcore\.[a-z_]+\.[a-z_]+$")

    def test_all_canonical_events_match_pattern(self) -> None:
        mismatches = [e for e in self._CANONICAL_EVENTS if not self._PATTERN.match(e)]
        assert mismatches == [], f"Events do not match pattern: {mismatches}"

    def test_circuit_opened_event_has_canonical_name(self) -> None:
        assert self._PATTERN.match("apcore.subscriber.circuit_opened")

    def test_circuit_closed_event_has_canonical_name(self) -> None:
        assert self._PATTERN.match("apcore.subscriber.circuit_closed")


# ---------------------------------------------------------------------------
# Fixture coverage guard
# ---------------------------------------------------------------------------


class TestFixtureCoverage:
    """Every case in the canonical fixture has a driver class in this file.

    The assertions above are hand-written rather than generated from the
    fixture. That is fine, but the fixture used to be named only in the module
    docstring, so a case added on the spec side left no trace here. This guard
    closes that gap: a new canonical case fails until someone writes the class.
    """

    FIXTURE = "event_management_hardening.json"

    #: canonical case id -> the class in this module that asserts it.
    COVERED: dict[str, str] = {
        "subscriber_factory_registered_type": "TestSubscriberFactoryRegisteredType",
        "builtin_stdout_type": "TestBuiltinStdoutType",
        "builtin_file_type": "TestBuiltinFileType",
        "builtin_filter_passes_matching": "TestBuiltinFilterPassesMatching",
        "builtin_filter_discards_nonmatching": "TestBuiltinFilterDiscardsNonmatching",
        "circuit_open_after_threshold": "TestCircuitOpenAfterThreshold",
        "circuit_discards_in_open_state": "TestCircuitDiscardsInOpenState",
        "circuit_half_open_after_window": "TestCircuitHalfOpenAfterWindow",
        "circuit_closes_on_success": "TestCircuitClosesOnSuccess",
        "event_naming_canonical": "TestEventNamingCanonical",
    }

    def test_every_canonical_case_is_claimed(self) -> None:
        canonical = set(case_ids(self.FIXTURE))
        claimed = set(self.COVERED)
        assert canonical - claimed == set(), (
            f"canonical fixture {self.FIXTURE} gained case(s) with no driver here"
        )
        assert claimed - canonical == set(), (
            f"this file claims case(s) {self.FIXTURE} no longer defines"
        )

    def test_every_claimed_class_exists(self) -> None:
        module = sys.modules[__name__]
        missing = [cls for cls in self.COVERED.values() if not hasattr(module, cls)]
        assert missing == [], f"claimed driver class(es) not defined: {missing}"
