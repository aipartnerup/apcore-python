# Feature: Core Executor

## Overview

The Core Execution Engine is the central orchestration component of apcore. It processes module calls through a structured 10-step pipeline: context creation, safety checks (call depth, circular detection, frequency throttling), module lookup from the registry, ACL enforcement, input validation with sensitive field redaction, middleware before chain, module execution with timeout enforcement, output validation, middleware after chain, and result return. The engine supports both synchronous (`call()`) and asynchronous (`call_async()`) execution paths, bridging between them via daemon threads and `asyncio.to_thread`.

## Scope

### Included

- `Context` and `Identity` data classes for call metadata propagation and caller identity
- `Config` accessor with dot-path key support for executor settings
- `Executor` class implementing the full 10-step synchronous and asynchronous pipelines
- Safety checks: call depth limits, circular call detection (cycles of length >= 2), frequency throttling
- Sync/async bridge via daemon threads, `asyncio.to_thread`, and `asyncio.wait_for`
- Thread-safe async module cache (`_async_cache` + `_async_cache_lock`)
- `redact_sensitive` utility for masking `x-sensitive` fields and `_secret_`-prefixed keys
- Structured error hierarchy (`ModuleError` base with specialized subclasses for every failure mode)
- Standalone `validate()` method for pre-flight schema checks without execution
- Middleware management via `MiddlewareManager` with before/after/on_error chains

### Excluded

- Registry implementation (consumed as a dependency)
- Schema system internals (consumed via Pydantic model validation)
- ACL rule definition and management (consumed via `ACL.check()` interface)
- Middleware implementation details (managed by `MiddlewareManager`)

## Technology Stack

- **Python 3.10+** with `from __future__ import annotations`
- **pydantic >= 2.0** for input/output schema validation and dynamic model generation
- **asyncio** for async execution paths and timeout enforcement
- **threading** for daemon thread-based timeout and sync/async bridging
- **pytest** for unit and integration testing

## Task Execution Order

| # | Task File | Description | Status |
|---|-----------|-------------|--------|
| 1 | [setup](./tasks/setup.md) | Context, Identity, and Config data classes | completed |
| 2 | [safety-checks](./tasks/safety-checks.md) | Call depth limits, circular detection, frequency throttling | completed |
| 3 | [execution-pipeline](./tasks/execution-pipeline.md) | 10-step synchronous execution pipeline with middleware and timeout | completed |
| 4 | [async-support](./tasks/async-support.md) | Async execution path (`call_async`) and sync/async bridge | completed |
| 5 | [redaction](./tasks/redaction.md) | Sensitive field redaction utility (`redact_sensitive`) | completed |

## Progress

| Total | Completed | In Progress | Pending |
|-------|-----------|-------------|---------|
| 5     | 5         | 0           | 0       |

## Reference Documents

- [Core Executor Feature Specification](../../features/core-executor.md)
