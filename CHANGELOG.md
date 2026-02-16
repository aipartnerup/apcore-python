# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).


## [0.2.2] - 2026-02-16

### Removed

#### Planning & Documentation
- **planning/features/** - Moved all feature specifications to `apcore/docs/features/` for better organization with documentation
- **planning/implementation/** - Restructured implementation planning to consolidate with overall project architecture

### Changed

#### Planning & Documentation Structure
- **Implementation planning** - Reorganized implementation plans to streamline project structure and improve maintainability



## [0.2.1] - 2026-02-14

### Added

#### Planning & Documentation Infrastructure
- **code-forge integration** - Added `.code-forge.json` configuration (v0.2.0 spec) with `_tool` metadata, directory mappings, and execution settings
- **Feature specifications** - 7 feature documents in `planning/features/` covering all core modules: core-executor, schema-system, registry-system, middleware-system, acl-system, observability, decorator-bindings
- **Implementation plans** - Complete implementation plans in `planning/implementation/` for all 7 features, each containing `overview.md`, `plan.md`, `tasks/*.md`, and `state.json`
- **Project-level overview** - Auto-generated `planning/implementation/overview.md` with module dependency graph, progress tracking, and phased implementation order
- **Task breakdown** - 42 task files with TDD-oriented steps, acceptance criteria, dependency tracking, and time estimates (~91 hours total estimated effort)

## [0.2.0] - 2026-02-14

### Fixed

#### Thread Safety
- **MiddlewareManager** - Added internal locking and snapshot pattern; `add()`, `remove()`, `execute_before()`, `execute_after()` are now thread-safe
- **Executor** - Added lock to async module cache; use `snapshot()` for middleware iteration in `call_async()` and `middlewares` property
- **ACL** - Internally synchronized; `check()`, `add_rule()`, `remove_rule()`, `reload()` are now safe for concurrent use
- **Registry** - Extended existing `RLock` to cover all read paths (`get`, `has`, `count`, `module_ids`, `list`, `iter`, `get_definition`, `on`, `_trigger_event`, `clear_cache`)

#### Memory Leak
- **InMemoryExporter** - Replaced unbounded `list` with `collections.deque(maxlen=10_000)` and added `threading.Lock` for thread-safe access

#### Robustness
- **TracingMiddleware** - Added empty span stack guard in `after()` and `on_error()` to log a warning instead of raising `IndexError`
- **Executor** - Set `daemon=True` on timeout and async bridge threads to prevent blocking process exit

### Added

#### Development Tooling
- **apdev integration** - Added `apdev[dev]` as development dependency for code quality checks and project tooling
- **pip install support** - Moved dev dependencies to `[project.optional-dependencies]` so `pip install -e ".[dev]"` works alongside `uv sync --group dev`
- **pre-commit hooks** - Fixed `check-chars` and `check-imports` hooks to run as local hooks via `apdev` instead of incorrectly nesting under `ruff-pre-commit` repo

### Changed

- **Context.child()** - Added docstring clarifying that `data` is intentionally shared between parent and child for middleware state propagation

## [0.1.0] - 2026-02-13

### Added

#### Core Framework
- **Schema-driven modules** - Define modules with Pydantic input/output schemas and automatic validation
- **@module decorator** - Zero-boilerplate decorator to turn functions into schema-aware modules
- **Executor** - 10-step execution pipeline with comprehensive safety and security checks
- **Registry** - Module registration and discovery system with metadata support

#### Security & Safety
- **Access Control (ACL)** - Pattern-based, first-match-wins rule system with wildcard support
- **Call depth limits** - Prevent infinite recursion and stack overflow
- **Circular call detection** - Detect and prevent circular module calls
- **Frequency throttling** - Rate limit module execution
- **Timeout support** - Configure execution timeouts per module

#### Middleware System
- **Composable pipeline** - Before/after hooks for request/response processing
- **Error recovery** - Graceful error handling and recovery in middleware chain
- **LoggingMiddleware** - Structured logging for all module calls
- **TracingMiddleware** - Distributed tracing with span support for observability

#### Bindings & Configuration
- **YAML bindings** - Register modules declaratively without modifying source code
- **Configuration system** - Centralized configuration management
- **Environment support** - Environment-based configuration override

#### Observability
- **Tracing** - Span-based distributed tracing integration
- **Metrics** - Built-in metrics collection for execution monitoring
- **Context logging** - Structured logging with execution context propagation

#### Async Support
- **Sync/Async modules** - Seamless support for both synchronous and asynchronous execution
- **Async executor** - Non-blocking execution for async-first applications

#### Developer Experience
- **Type safety** - Full type annotations across the framework (Python 3.11+)
- **Comprehensive tests** - 90%+ test coverage with unit and integration tests
- **Documentation** - Quick start guide, examples, and API documentation
- **Examples** - Sample modules demonstrating decorator-based and class-based patterns

### Dependencies

- **pydantic** >= 2.0 - Schema validation and serialization
- **pyyaml** >= 6.0 - YAML binding support
- **pluggy** >= 1.0 - Plugin system for registry discovery

### Supported Python Versions

- Python 3.11+

---

[0.2.1]: https://github.com/aipartnerup/apcore-python/releases/tag/v0.2.1
[0.2.0]: https://github.com/aipartnerup/apcore-python/releases/tag/v0.2.0
[0.1.0]: https://github.com/aipartnerup/apcore-python/releases/tag/v0.1.0