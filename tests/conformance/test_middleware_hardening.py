"""Drive `middleware_hardening.json` — middleware architecture hardening (Issue #42).

apcore-typescript (`tests/conformance.test.ts`) and apcore-rust already drive
this fixture; apcore-python covered it only by hand under `tests/middleware/`
and `tests/observability/`, which cannot notice a new fixture case. This is the
fixture-driven version.

Nine of the ten cases are driven for real. The tenth (`tracing_span_created`)
describes the OpenTelemetry-shaped `TracingMiddleware` of middleware-system.md
§1.3, which apcore-python does not ship at all; it is a strict xfail naming the
gap.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from apcore.context import Context
from apcore.errors import CircuitBreakerOpenError
from apcore.middleware.circuit_breaker import CircuitBreakerMiddleware, CircuitBreakerState

from .canonical_fixtures import load_fixture

FIXTURE = load_fixture("middleware_hardening.json")
CASES: dict[str, dict[str, Any]] = {tc["id"]: tc for tc in FIXTURE["test_cases"]}

_TRACING_XFAIL = (
    "NOT-IMPLEMENTED: apcore-python ships no OpenTelemetry-shaped TracingMiddleware. "
    "middleware-system.md §1.3 and this fixture describe a SECOND, separate middleware from the "
    "one in observability.md: span name == module_id, apcore.trace_id / apcore.caller_id / "
    "apcore.module_id attributes, span id in _apcore.mw.tracing.span_id, no-op without the OTel "
    "SDK. apcore-typescript ships it as src/middleware/tracing.ts::TracingMiddleware and "
    "apcore-rust as src/middleware/otel_tracing.rs::TracingMiddleware (re-exported as "
    "apcore::OtelTracingMiddleware to avoid the name clash). Python has no apcore.middleware "
    "equivalent.\n\n"
    "NOT the gap: apcore-python's observability TracingMiddleware "
    "(src/apcore/observability/tracing.py:185). Its 'apcore.module.execute' span name, bare "
    "module_id/method/caller_id attributes and _apcore.mw.tracing.spans STACK are byte-for-byte "
    "what observability.md §'Tracing Architecture' specifies, what protocol-spec.md:6401 and "
    "conformance.md T08-007 name, and what apcore-typescript's own "
    "src/observability/tracing.ts:194-218 does. Reshaping it to satisfy this fixture would "
    "CREATE a divergence, not remove one."
)


class _CapturingEmitter:
    def __init__(self) -> None:
        self.event_types: list[str] = []

    def emit(self, event: Any) -> None:
        self.event_types.append(event.event_type)


class _FrozenClock:
    """Manually advanced clock so recovery-window timing is deterministic."""

    def __init__(self) -> None:
        self._now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self._now

    def advance_ms(self, milliseconds: float) -> None:
        self._now += timedelta(milliseconds=milliseconds)


def _context(caller_id: str, trace_id: str = "0" * 31 + "1") -> Context:
    """A Context carrying the caller_id the breaker keys its circuits by."""
    return Context(trace_id=trace_id, caller_id=caller_id)


# ---------------------------------------------------------------------------
# Context namespacing (cases 1-3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case_id",
    ["context_namespace_apcore_prefix", "context_namespace_ext_prefix", "context_namespace_violation"],
)
def test_context_namespace(case_id: str) -> None:
    """`validate_context_key` is the Python peer of apcore-typescript's
    `validateContextKey` (src/middleware/context-namespace.ts) and apcore-rust's
    `validate_context_key` (src/middleware/context_namespace.rs), pinning
    middleware-system.md §1.1: user middleware MUST NOT write `_apcore.*`,
    framework middleware MUST NOT write `ext.*`, everything else is tolerated.
    """
    from apcore.middleware import validate_context_key

    case = CASES[case_id]
    result = validate_context_key(case["input"]["writer"], case["input"]["key"])
    assert result.valid is case["expected"]["valid"], f"[{case_id}] valid mismatch"
    assert result.warning is case["expected"]["warning"], f"[{case_id}] warning mismatch"


# ---------------------------------------------------------------------------
# Circuit breaker (cases 4-7)
# ---------------------------------------------------------------------------


def _breaker_and_context(
    case: dict[str, Any],
    *,
    clock: _FrozenClock,
    emitter: _CapturingEmitter,
    open_threshold: float = 0.5,
    window_size: int = 10,
) -> tuple[CircuitBreakerMiddleware, Context, str, str]:
    breaker = CircuitBreakerMiddleware(
        open_threshold=case["input"].get("open_threshold", open_threshold),
        window_size=case["input"].get("window_size", window_size),
        recovery_window_ms=case["input"].get("recovery_window_ms", 30_000),
        min_samples=1,
        emitter=emitter,
        clock=clock,
    )
    module_id: str = case["input"]["module_id"]
    caller_id: str = case["input"]["caller_id"]
    return breaker, _context(caller_id), module_id, caller_id


def _drive_to_open(
    breaker: CircuitBreakerMiddleware,
    module_id: str,
    ctx: Context,
    *,
    errors: int,
    successes: int = 0,
) -> None:
    for _ in range(successes):
        breaker.before(module_id, {}, ctx)
        breaker.after(module_id, {}, {}, ctx)
    for _ in range(errors):
        try:
            breaker.before(module_id, {}, ctx)
        except CircuitBreakerOpenError:
            pass  # already open — the outcome below is still recorded
        breaker.on_error(module_id, {}, RuntimeError("simulated failure"), ctx)


def test_circuit_breaker_opens_at_threshold() -> None:
    case = CASES["circuit_breaker_opens_at_threshold"]
    emitter = _CapturingEmitter()
    breaker, ctx, module_id, caller_id = _breaker_and_context(case, clock=_FrozenClock(), emitter=emitter)

    _drive_to_open(
        breaker,
        module_id,
        ctx,
        errors=case["input"]["errors_in_window"],
        successes=case["input"]["successes_in_window"],
    )

    state = breaker.get_state(module_id, caller_id)
    assert state.value == case["expected"]["circuit_state"], (
        f"[{case['id']}] with {case['input']['errors_in_window']}/"
        f"{case['input']['window_size']} errors above threshold "
        f"{case['input']['open_threshold']}, state must be {case['expected']['circuit_state']}, got {state.value}"
    )
    assert case["expected"]["event_emitted"] in emitter.event_types, (
        f"[{case['id']}] expected {case['expected']['event_emitted']!r}, emitted {emitter.event_types}"
    )


def test_circuit_breaker_short_circuits_open() -> None:
    case = CASES["circuit_breaker_short_circuits_open"]
    clock = _FrozenClock()
    breaker, ctx, module_id, caller_id = _breaker_and_context(case, clock=clock, emitter=_CapturingEmitter())

    _drive_to_open(breaker, module_id, ctx, errors=10)
    assert breaker.get_state(module_id, caller_id) is CircuitBreakerState.OPEN

    # Still inside the recovery window: the call must never reach the module.
    clock.advance_ms(case["input"]["ms_since_opened"])
    module_reached = False
    with pytest.raises(CircuitBreakerOpenError) as excinfo:
        breaker.before(module_id, {}, ctx)
        module_reached = True

    # The WIRE CODE, not the class name. This asserted
    # `type(...).__name__ == "CircuitBreakerOpenError"`, a Python/TypeScript
    # class apcore-rust cannot have — Rust models these as ErrorCode variants —
    # so the case was unsatisfiable there by construction (apcore#81).
    assert excinfo.value.code == case["expected"]["error_code"], (
        f"[{case['id']}] expected error_code {case['expected']['error_code']}, "
        f"got {excinfo.value.code}"
    )
    assert module_reached is case["expected"]["module_reached"], (
        f"[{case['id']}] the module must not be reached while the circuit is OPEN"
    )


def test_circuit_breaker_half_open_probe() -> None:
    case = CASES["circuit_breaker_half_open_probe"]
    clock = _FrozenClock()
    breaker, ctx, module_id, caller_id = _breaker_and_context(case, clock=clock, emitter=_CapturingEmitter())

    _drive_to_open(breaker, module_id, ctx, errors=10)
    clock.advance_ms(case["input"]["ms_since_opened"])

    assert breaker.get_state(module_id, caller_id).value == case["expected"]["circuit_state"], (
        f"[{case['id']}] after {case['input']['ms_since_opened']}ms (recovery window "
        f"{case['input']['recovery_window_ms']}ms) the circuit must be "
        f"{case['expected']['circuit_state']}"
    )

    probe_allowed = True
    try:
        breaker.before(module_id, {}, ctx)
    except CircuitBreakerOpenError:
        probe_allowed = False
    assert probe_allowed is case["expected"]["probe_call_allowed"], (
        f"[{case['id']}] HALF_OPEN must admit the first probe"
    )

    # max_concurrent_probes: COUNT the probes HALF_OPEN actually admits and
    # compare that observation to the fixture. Asserting the fixture's own value
    # against `1` is a tautology — it passes no matter how many probes the
    # breaker lets through (apcore-python#32 / aiperceivable/apcore#81).
    max_probes = case["expected"]["max_concurrent_probes"]
    admitted = 1 if probe_allowed else 0
    for _ in range(max_probes + 1 - admitted):  # one attempt past the allowance
        try:
            breaker.before(module_id, {}, ctx)
        except CircuitBreakerOpenError:
            break
        admitted += 1
    assert admitted == max_probes, (
        f"[{case['id']}] HALF_OPEN must admit exactly {max_probes} concurrent probe(s), "
        f"but {admitted} were admitted"
    )


def test_circuit_breaker_closes_on_success() -> None:
    case = CASES["circuit_breaker_closes_on_success"]
    clock = _FrozenClock()
    emitter = _CapturingEmitter()
    breaker, ctx, module_id, caller_id = _breaker_and_context(
        case, clock=clock, emitter=emitter, open_threshold=0.5, window_size=10
    )

    _drive_to_open(breaker, module_id, ctx, errors=10)
    clock.advance_ms(30_000)
    assert breaker.get_state(module_id, caller_id) is CircuitBreakerState.HALF_OPEN

    # Driver-input precondition (NOT an SDK expectation): this driver hardcodes
    # the successful-probe path, so a fixture that switched `probe_result` must
    # fail here rather than silently keep testing the old scenario.
    assert case["input"]["probe_result"] == "success", (
        f"[{case['id']}] this driver only models a successful probe; "
        f"fixture now declares probe_result={case['input']['probe_result']!r}"
    )
    breaker.before(module_id, {}, ctx)
    breaker.after(module_id, {}, {}, ctx)

    assert breaker.get_state(module_id, caller_id).value == case["expected"]["circuit_state"], (
        f"[{case['id']}] a successful probe must close the circuit"
    )
    assert case["expected"]["event_emitted"] in emitter.event_types, (
        f"[{case['id']}] expected {case['expected']['event_emitted']!r}, emitted {emitter.event_types}"
    )


# ---------------------------------------------------------------------------
# Tracing (cases 8-9)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=_TRACING_XFAIL)
def test_tracing_span_created() -> None:
    from apcore.observability.tracing import InMemoryExporter, TracingMiddleware

    case = CASES["tracing_span_created"]
    expected = case["expected"]

    middleware = TracingMiddleware(exporter=InMemoryExporter())
    ctx = _context(case["input"]["caller_id"], trace_id=case["input"]["trace_id"])

    middleware.before(case["input"]["module_id"], {}, ctx)

    spans = ctx.data.get("_apcore.mw.tracing.spans") or []
    assert spans, f"[{case['id']}] before() must create a span"
    span = spans[-1]
    assert span.name == expected["span_name"], (
        f"[{case['id']}] span name: got {span.name!r}, expected {expected['span_name']!r}"
    )
    for attribute, value in expected["span_attributes"].items():
        assert span.attributes.get(attribute) == value, (
            f"[{case['id']}] span attribute {attribute}: got {span.attributes.get(attribute)!r}, "
            f"expected {value!r}"
        )
    assert ctx.data.get(expected["context_key"]) == span.span_id, (
        f"[{case['id']}] the span id must be stored under {expected['context_key']!r}"
    )


def test_tracing_noop_without_otel() -> None:
    """No OpenTelemetry dependency: tracing must neither import nor require it.

    apcore-python's tracing is self-contained, so `span_created: false` is
    asserted as "no OpenTelemetry span exists to create" — verified by the
    package never importing the SDK — plus the fixture's real requirement that
    execution continues without error.
    """
    import sys

    from apcore.observability.tracing import InMemoryExporter, TracingMiddleware

    case = CASES["tracing_noop_without_otel"]
    expected = case["expected"]
    # Driver-input precondition (NOT an SDK expectation): this driver models the
    # no-OpenTelemetry environment the case names.
    assert case["input"]["otel_available"] is False, (
        f"[{case['id']}] this driver only models otel_available=false"
    )

    otel_before = {name for name in sys.modules if name.startswith("opentelemetry")}
    middleware = TracingMiddleware(exporter=InMemoryExporter())
    ctx = _context(case["input"]["caller_id"])

    error: Exception | None = None
    try:
        middleware.before(case["input"]["module_id"], {}, ctx)
        middleware.after(case["input"]["module_id"], {}, {}, ctx)
    except Exception as exc:  # noqa: BLE001 - the fixture asserts nothing is raised
        error = exc

    assert (error is not None) is expected["error_raised"], (
        f"[{case['id']}] tracing must not raise when OpenTelemetry is absent; got {error!r}"
    )
    # Bind both remaining expectations to OBSERVATIONS. These used to read
    # `assert expected["execution_continues"] is True` / `... span_created is
    # False`, which restate the fixture and cannot fail on SDK behaviour
    # (apcore-python#32 / aiperceivable/apcore#81).
    execution_continued = error is None
    assert execution_continued is expected["execution_continues"], (
        f"[{case['id']}] execution must continue with no OpenTelemetry present; got {error!r}"
    )
    otel_after = {name for name in sys.modules if name.startswith("opentelemetry")}
    otel_span_created = bool(otel_after - otel_before)
    assert otel_span_created is expected["span_created"], (
        f"[{case['id']}] tracing must not pull in the OpenTelemetry SDK to create a span; "
        f"newly imported: {sorted(otel_after - otel_before)}"
    )


# ---------------------------------------------------------------------------
# Async handler detection (case 10)
# ---------------------------------------------------------------------------


def _resolve_inspect_method(case: dict[str, Any], name: str) -> Any:
    """Resolve the `inspect` predicate the fixture names, e.g. `iscoroutinefunction`.

    The fixture may spell it bare or module-qualified (`inspect.isawaitable`).
    A name `inspect` does not expose fails loudly rather than falling back.
    """
    attr = name.split(".")[-1]
    method = getattr(inspect, attr, None)
    assert callable(method), (
        f"[{case['id']}] the fixture names detection method {name!r}, which `inspect` "
        f"does not expose"
    )
    return method


def test_async_detection_coroutine_function() -> None:
    case = CASES["async_detection_coroutine_function"]
    expected = case["expected"]
    # Driver-applicability precondition (NOT an SDK expectation): a case
    # retargeted at another language must not keep silently passing here.
    assert case["input"]["language"] == "python", (
        f"[{case['id']}] this is the Python driver; case declares "
        f"language={case['input']['language']!r}"
    )
    # RESOLVE the detector the fixture names instead of asserting its name
    # against a literal copy of itself — using the value is what makes a fixture
    # change bite here (apcore-python#32 / aiperceivable/apcore#81).
    detect = _resolve_inspect_method(case, case["input"]["detection_method"])

    async def async_handler() -> None:
        return None

    def sync_handler() -> None:
        return None

    assert detect(async_handler) is expected["is_async"], (
        f"[{case['id']}] {case['input']['detection_method']} must detect an async def handler"
    )
    assert detect(sync_handler) is False, (
        f"[{case['id']}] {case['input']['detection_method']} must reject a plain def handler"
    )

    wrong = expected["incorrect_method_result"]
    detect_wrong = _resolve_inspect_method(case, wrong["method"])
    assert detect_wrong(async_handler) is wrong["result_on_uncalled_function"], (
        f"[{case['id']}] {wrong['note']}"
    )

    coroutine = async_handler()
    try:
        assert detect_wrong(coroutine) is True, (
            f"[{case['id']}] isawaitable is only true once the function has been CALLED — "
            f"which is why it cannot be used for handler detection"
        )
    finally:
        coroutine.close()


def test_every_fixture_case_has_a_driver() -> None:
    driven = {
        "context_namespace_apcore_prefix",
        "context_namespace_ext_prefix",
        "context_namespace_violation",
        "circuit_breaker_opens_at_threshold",
        "circuit_breaker_short_circuits_open",
        "circuit_breaker_half_open_probe",
        "circuit_breaker_closes_on_success",
        "tracing_span_created",
        "tracing_noop_without_otel",
        "async_detection_coroutine_function",
    }
    assert set(CASES) == driven, (
        f"middleware_hardening.json cases without a driver: {sorted(set(CASES) - driven)}; "
        f"drivers with no matching case: {sorted(driven - set(CASES))}"
    )
