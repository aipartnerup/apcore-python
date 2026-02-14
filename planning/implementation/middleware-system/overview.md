# Feature: Middleware System

## Overview

Composable middleware pipeline using the onion execution model with before/after/on_error phases. Each middleware can inspect and modify inputs before module execution, transform outputs after execution, and participate in error recovery when failures occur. The system supports both full subclass-based middleware and lightweight function adapters, with a built-in logging middleware for structured, security-aware call logging. Thread safety is ensured via a lock-protected snapshot pattern.

## Scope

### Included

- `Middleware` base class with no-op default implementations for all three lifecycle phases
- `MiddlewareManager` orchestrating onion-model execution with registration-order before, reverse-order after, and reverse-of-executed on_error phases
- `MiddlewareChainError` exception carrying the original exception and the list of executed middlewares for targeted error recovery
- Thread-safe middleware list management via `threading.Lock` and the snapshot pattern
- `BeforeMiddleware` and `AfterMiddleware` adapters wrapping single callback functions as full `Middleware` subclasses
- `LoggingMiddleware` with structured logging, `context.redacted_inputs` for security-aware redaction, per-call duration tracking via `context.data`, and configurable `log_inputs`/`log_outputs`/`log_errors` flags

### Excluded

- Async middleware execution (all phases are synchronous)
- Middleware ordering beyond simple registration order (no priority system)
- Persistent middleware state across calls (state is per-call via `context.data`)

## Technology Stack

- **Language**: Python 3.10+
- **Dependencies**: stdlib only (`threading`, `logging`, `time`)
- **Internal**: `apcore.context.Context` for execution context
- **Testing**: pytest

## Task Execution Order

| # | Task File | Description | Status |
|---|-----------|-------------|--------|
| 1 | [base](./tasks/base.md) | Middleware base class with no-op default implementations for before/after/on_error | completed |
| 2 | [manager](./tasks/manager.md) | MiddlewareManager with onion-model execution, snapshot pattern, and MiddlewareChainError | completed |
| 3 | [adapters](./tasks/adapters.md) | BeforeMiddleware and AfterMiddleware function wrapper adapters | completed |
| 4 | [logging-middleware](./tasks/logging-middleware.md) | LoggingMiddleware with structured logging, redaction, and duration tracking | completed |

## Progress

| Total | Completed | In Progress | Pending |
|-------|-----------|-------------|---------|
| 4     | 4         | 0           | 0       |

## Reference Documents

- [Middleware System Feature Specification](../../features/middleware-system.md)
