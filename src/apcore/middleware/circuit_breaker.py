"""CircuitBreakerMiddleware — per-module call protection (Issue #42)."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from apcore.errors import CircuitBreakerOpenError, CircuitOpenError
from apcore.middleware.base import Context, Middleware, RetrySignal

__all__ = ["CircuitState", "CircuitBreakerMiddleware"]

_logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass
class _CircuitRecord:
    """Per-module circuit state."""

    state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = 0
    last_failure_at: datetime | None = None
    half_open_successes: int = 0
    lock: threading.Lock = field(
        default_factory=threading.Lock, compare=False, repr=False
    )


class CircuitBreakerMiddleware(Middleware):
    """Middleware that tracks per-module failure rates and opens the circuit on threshold breach.

    State machine per ``module_id``:
        CLOSED → (consecutive_failures >= failure_threshold) → OPEN
        OPEN → (recovery_window_ms elapsed) → HALF_OPEN
        HALF_OPEN → (half_open_successes >= success_threshold) → CLOSED
        HALF_OPEN → failure → OPEN

    When the circuit is OPEN the ``before()`` hook raises :class:`~apcore.errors.CircuitBreakerOpenError`,
    short-circuiting module execution entirely.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_window_ms: int = 60_000,
        success_threshold: int = 1,
        *,
        priority: int = 100,
    ) -> None:
        super().__init__(priority=priority)
        if failure_threshold < 1:
            raise ValueError(f"failure_threshold must be >= 1, got {failure_threshold}")
        if recovery_window_ms < 0:
            raise ValueError(
                f"recovery_window_ms must be >= 0, got {recovery_window_ms}"
            )
        if success_threshold < 1:
            raise ValueError(f"success_threshold must be >= 1, got {success_threshold}")
        self._failure_threshold = failure_threshold
        self._recovery_window_ms = recovery_window_ms
        self._success_threshold = success_threshold
        self._circuits: dict[str, _CircuitRecord] = {}
        self._registry_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public introspection
    # ------------------------------------------------------------------

    def get_state(self, module_id: str) -> CircuitState:
        """Return the current circuit state for *module_id*."""
        record = self._get_record(module_id)
        with record.lock:
            self._maybe_transition_to_half_open(record, module_id)
            return record.state

    def reset(self, module_id: str) -> None:
        """Manually close the circuit for *module_id* (e.g., for testing or ops override)."""
        record = self._get_record(module_id)
        with record.lock:
            record.state = CircuitState.CLOSED
            record.consecutive_failures = 0
            record.last_failure_at = None
            record.half_open_successes = 0

    # ------------------------------------------------------------------
    # Middleware hooks
    # ------------------------------------------------------------------

    def before(
        self, module_id: str, inputs: dict[str, Any], context: Context
    ) -> dict[str, Any] | None:
        record = self._get_record(module_id)
        with record.lock:
            self._maybe_transition_to_half_open(record, module_id)
            if record.state == CircuitState.OPEN:
                raise CircuitBreakerOpenError(module_id=module_id)
        return None

    def after(
        self,
        module_id: str,
        inputs: dict[str, Any],
        output: dict[str, Any],
        context: Context,
    ) -> dict[str, Any] | None:
        record = self._get_record(module_id)
        with record.lock:
            self._record_success(record)
        return None

    def on_error(
        self,
        module_id: str,
        inputs: dict[str, Any],
        error: Exception,
        context: Context,
    ) -> dict[str, Any] | RetrySignal | None:
        if isinstance(error, CircuitBreakerOpenError):
            return None
        record = self._get_record(module_id)
        with record.lock:
            self._record_failure(record, module_id)
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_record(self, module_id: str) -> _CircuitRecord:
        with self._registry_lock:
            if module_id not in self._circuits:
                self._circuits[module_id] = _CircuitRecord()
            return self._circuits[module_id]

    def _maybe_transition_to_half_open(
        self, record: _CircuitRecord, module_id: str
    ) -> None:
        """Transition OPEN → HALF_OPEN if recovery window has elapsed. Caller holds record.lock."""
        if record.state != CircuitState.OPEN or record.last_failure_at is None:
            return
        elapsed_ms = (
            datetime.now(timezone.utc) - record.last_failure_at
        ).total_seconds() * 1000
        if elapsed_ms >= self._recovery_window_ms:
            record.state = CircuitState.HALF_OPEN
            record.half_open_successes = 0
            _logger.info(
                "Circuit HALF_OPEN for module '%s' after recovery window", module_id
            )

    def _record_success(self, record: _CircuitRecord) -> None:
        """Update state on success. Caller holds record.lock."""
        record.consecutive_failures = 0
        if record.state == CircuitState.HALF_OPEN:
            record.half_open_successes += 1
            if record.half_open_successes >= self._success_threshold:
                record.state = CircuitState.CLOSED
                record.last_failure_at = None
                record.half_open_successes = 0

    def _record_failure(self, record: _CircuitRecord, module_id: str) -> None:
        """Update state on failure. Caller holds record.lock."""
        record.consecutive_failures += 1
        record.last_failure_at = datetime.now(timezone.utc)

        opens = record.state == CircuitState.HALF_OPEN or (
            record.state == CircuitState.CLOSED
            and record.consecutive_failures >= self._failure_threshold
        )
        if opens:
            record.state = CircuitState.OPEN
            _logger.warning(
                "Circuit OPEN for module '%s' after %d consecutive failures",
                module_id,
                record.consecutive_failures,
            )
