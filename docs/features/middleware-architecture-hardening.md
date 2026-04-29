# Feature: Middleware Architecture Hardening (Issue #42)

## Goal
Harden the apcore middleware system with a proper CircuitBreakerMiddleware, fix
priority initialization in TracingMiddleware and RetryMiddleware, add YAML config
support for circuit-breaker defaults, and fix the async detection bug where sync
`on_error` handlers block the event loop.

## Scope

### In Scope
- New `CircuitBreakerMiddleware` in `src/apcore/middleware/circuit_breaker.py`
- Fix `TracingMiddleware.__init__` to accept and forward `priority` via `super().__init__()`
- Fix `RetryMiddleware.__init__` to accept and forward `priority` via `super().__init__()`
- Add `middleware.circuit_breaker.*` YAML config keys to `config.py` with validation
- Fix `MiddlewareManager.execute_on_error_async` to use `asyncio.to_thread` for blocking sync handlers
- Export `CircuitBreakerMiddleware` from `middleware/__init__.py`

### Out of Scope
- Changes to `events/circuit_breaker.py` (EventSubscriber wrapper — separate concern)
- New exporters for TracingMiddleware
- Changes to retry logic or retry backoff strategy

## Affected Modules
- `src/apcore/middleware/circuit_breaker.py` — new file
- `src/apcore/middleware/__init__.py` — add `CircuitBreakerMiddleware` export
- `src/apcore/middleware/manager.py` — fix async on_error blocking
- `src/apcore/middleware/retry.py` — add `priority` param + `super().__init__()`
- `src/apcore/observability/tracing.py` — add `priority` param + `super().__init__()`
- `src/apcore/config.py` — add `middleware.circuit_breaker.*` config + constraints
- `src/apcore/errors.py` — add `CircuitOpenError`

## New Modules
- `src/apcore/middleware/circuit_breaker.py` — `CircuitBreakerMiddleware` with per-module state machine

## Technical Approach

### CircuitBreakerMiddleware
Per-module state machine (CLOSED → OPEN → HALF_OPEN) tracked in `self._state` dict
keyed by `module_id`. Thread-safe with `threading.Lock`. In `before()`, if circuit is
OPEN and recovery window hasn't elapsed, raise `CircuitOpenError`. In `on_error()`,
increment failure counter; open circuit when `consecutive_failures >= failure_threshold`.
In `after()`, record success; in HALF_OPEN, close the circuit.

Config parameters:
- `failure_threshold: int = 5` — consecutive failures before opening
- `recovery_window_ms: int = 60_000` — ms before OPEN → HALF_OPEN
- `success_threshold: int = 1` — successes in HALF_OPEN before closing

### Priority fix
`TracingMiddleware.__init__` and `RetryMiddleware.__init__` both need to:
1. Accept `priority: int = 100`
2. Call `super().__init__(priority=priority)`

### Async on_error fix
`execute_on_error_async` currently calls sync `on_error` directly. For handlers
that may block (e.g., `RetryMiddleware.on_error` with `time.sleep`), this blocks
the event loop. Fix: wrap sync handlers with `asyncio.to_thread`.

### YAML config
Add to `_CONSTRAINTS`:
```
"middleware.circuit_breaker.failure_threshold": int >= 1
"middleware.circuit_breaker.recovery_window_ms": int >= 0
"middleware.circuit_breaker.success_threshold": int >= 1
```

## Acceptance Criteria
- `CircuitBreakerMiddleware` opens after N consecutive failures per module
- `CircuitBreakerMiddleware` transitions OPEN → HALF_OPEN after recovery window
- `CircuitBreakerMiddleware` closes on success in HALF_OPEN
- `TracingMiddleware(exporter, priority=200)` sets priority to 200 without error
- `RetryMiddleware(config, priority=50)` sets priority to 50 without error
- `execute_on_error_async` does not block the event loop for sync `on_error` handlers
- YAML config `middleware.circuit_breaker.failure_threshold` is validated
- All existing tests continue to pass
