# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).


## [0.20.0] - 2026-04-29

### Added

- **AsyncTaskManager Evolution** (PROTOCOL_SPEC Issue #34) — Pluggable `TaskStore` protocol with `InMemoryTaskStore` default; custom backends (Redis, SQL) can be injected at construction time. Per-task retry configuration via new `RetryPolicy` dataclass (`max_retries`, `retry_delay_ms`, `backoff_multiplier`, `max_retry_delay_ms`) and `BackoffStrategy` enum; tasks move to `TaskStatus.RETRYING` between attempts and `FAILED` after exhaustion. `AsyncTaskManager.start_reaper(interval_seconds, max_age_seconds)` / `stop_reaper()` — opt-in background task for automatic TTL-based deletion of terminal-state (`COMPLETED`, `FAILED`, `CANCELLED`) tasks. Exports: `TaskStore`, `InMemoryTaskStore`, `RetryPolicy`, `BackoffStrategy`.
- **Observability Hardening** (PROTOCOL_SPEC Issue #43) — Pluggable `ObservabilityStore` protocol with `InMemoryObservabilityStore` default (`apcore.observability.store`). `BatchSpanProcessor` for non-blocking OTEL span export with configurable queue and drop-on-full `spans_dropped` counter (now exported from `apcore.observability.tracing`). O(log N) `ErrorHistory` eviction via min-heap keyed on `last_occurred` plus O(1) fingerprint index replacing prior O(M) ring-buffer scan. `compute_fingerprint()` — SHA-256 content-addressable error deduplication with UUID/timestamp normalization (exported from `apcore.observability.error_history`). `RedactionConfig` in `ContextLogger` for glob `field_patterns` and regex `value_patterns` applied at log time. `PrometheusExporter` HTTP server serving `/metrics` (Prometheus text format), `/healthz` (liveness), and `/readyz` (readiness) endpoints (`apcore.observability.prometheus_exporter`). `MetricsCollector.export_prometheus()` emits `apcore_module_calls_total`, `apcore_module_errors_total`, `apcore_module_duration_seconds`. `UsageCollector.export_prometheus()` emits `apcore_usage_calls_total`, `apcore_usage_error_rate`, `apcore_usage_p50/p95/p99_latency_ms`.
- **System Modules Hardening** (PROTOCOL_SPEC Issue #45) — `overrides_path` parameter for `register_sys_modules()` loads a YAML/JSON override file after base config on startup (via `AuditStore` / `OverridesStore` pattern). Structured audit trail: `AuditEntry`, `AuditStore` protocol, and `InMemoryAuditStore` default record all state-modifying control-module calls with timestamp, action, actor_id, actor_type, trace_id, and before/after change dict (`apcore.sys_modules.audit`). `fail_on_error: bool = False` on `register_sys_modules()` — when `True` raises `SysModuleRegistrationError`; when `False` (default) logs `ERROR` and continues. `path_filter` glob on `system.control.reload_module` for bulk reload in dependency topological order; mutually exclusive with `module_id` (raises `ModuleReloadConflictError` on conflict). New error classes exported from `apcore`: `SysModuleRegistrationError` (code `SYS_MODULE_REGISTRATION_FAILED`), `ModuleReloadConflictError` (code `MODULE_RELOAD_CONFLICT`).
- **Schema System Hardening** (PROTOCOL_SPEC Issue #44) — New `apcore.schema.hardening` module: `content_hash(schema)` returns the SHA-256 of canonical JSON for content-addressable schema deduplication; `validate_schema_dict(schema, data)` uses `Draft202012Validator` to exhaustively evaluate all `anyOf`/`oneOf` branches (no short-circuit), resolve recursive `$ref`, enforce numerical/string constraints, and emit SHOULD-level warnings (not hard errors) on unrecognized format values. Conformance fixtures added: `schema_hardening_union.json`, `schema_hardening_recursive.json`, `schema_hardening_constraints.json`, `schema_hardening_formats.json`, `schema_hardening_cache.json`.
- **Multi-Class Module Discovery** (PROTOCOL_SPEC §2.1.1) — New `apcore.registry.multi_class` module: `@multi_class` decorator opts a class into multi-class per-file scanning; `class_name_to_segment()` derives a snake_case ID segment from a class name (CamelCase → `snake_case`); `discover_multi_class()` scans a file and produces IDs of the form `base_id.class_segment`. Single-class files with one decorated class receive the bare `base_id` (backward-compatible). `ModuleIdConflictError` (code `MODULE_ID_CONFLICT`) raised when two classes in the same file produce identical snake_case segments.
- **Middleware Architecture Hardening** (PROTOCOL_SPEC Issue #42) — `CircuitBreakerMiddleware` tracks per-module consecutive failures in a rolling window; transitions through CLOSED → OPEN → HALF_OPEN state machine. When OPEN, `before()` raises `CircuitOpenError` (code `CIRCUIT_OPEN`) to short-circuit execution entirely. On state changes emits `apcore.circuit.opened` / `apcore.circuit.closed` events. `CircuitState` enum and `CircuitBreakerMiddleware` are exported from `apcore`.
- **Event Management Hardening** (PROTOCOL_SPEC Issue #36) — `CircuitBreakerWrapper` in `apcore.events.circuit_breaker` wraps any `EventSubscriber` with independent circuit-breaker protection (CLOSED/OPEN/HALF_OPEN state machine, configurable `open_threshold`, `recovery_window_ms`); emits `apcore.subscriber.circuit_opened` / `apcore.subscriber.circuit_closed` events via the parent `EventEmitter`.
- **Conformance test suite expansion** — `tests/conformance/test_pipeline_hardening.py` (5 cases: fail-fast, continue-on-ignored-error, replace semantic, `run_until` termination, O(1) lookup), `tests/conformance/test_schema_hardening.py` (35 cases across union, recursive, constraints, formats, cache fixtures), `tests/conformance/test_system_modules_hardening.py` (10 cases: overrides persistence, audit entry extraction, Prometheus metrics, path_filter bulk reload, conflict error, fail_on_error behaviour).

---

## [0.19.0] - 2026-04-19

### Added

- **`DependencyNotFoundError`** (error code `DEPENDENCY_NOT_FOUND`) — raised by `resolve_dependencies` when a module's required dependency is not registered. Aligns Python with PROTOCOL_SPEC §5.15.2, which has always mandated this error code. Details include `module_id` and `dependency_id`. Exported from `apcore`.
- **`DependencyVersionMismatchError`** (error code `DEPENDENCY_VERSION_MISMATCH`) — raised by `resolve_dependencies` when a declared `version` constraint is not satisfied by the registered version of the target module. Details include `module_id`, `dependency_id`, `required`, `actual`. Exported from `apcore`.
- **`TaskLimitExceededError`** (error code `TASK_LIMIT_EXCEEDED`) — raised by `AsyncTaskManager.submit` when the manager is at capacity. Replaces the previous untyped `RuntimeError` and makes the failure dispatchable via `error.code` across language SDKs. Retryable=True.
- **`VersionConstraintError`** (error code `VERSION_CONSTRAINT_INVALID`) — raised by `matches_version_hint` / `VersionedStore` callers on malformed constraint strings (empty, operator-without-operand, non-digit-leading operand such as `"v1.0"` or `"latest"`). Previously, malformed constraints silently degraded to `(0,0,0)` comparisons that always passed.
- **`resolve_dependencies(..., module_versions=...)`** — new optional keyword argument mapping `module_id → version_string`. When provided, declared dependency version constraints are enforced per PROTOCOL_SPEC §5.3. When absent, the `DependencyInfo.version` field is silently ignored (back-compat for callers that do not wire versions through yet). `ModuleRegistry._resolve_load_order` now populates this map from YAML version / class `version` attr / `DEFAULT_MODULE_VERSION` (`"1.0.0"`) fallback, and includes already-registered modules (from `_versioned_modules`) so inter-batch constraints resolve against the live registry's multi-version state — not just the latest-only primary map.
- **Caret (`^`) and tilde (`~`) constraint support** in `matches_version_hint` / `select_best_version` (npm/Cargo semantics): `^1.2.3 → >=1.2.3,<2.0.0`, `^0.2.3 → >=0.2.3,<0.3.0`, `^0.0.3 → >=0.0.3,<0.0.4`, `~1.2.3 → >=1.2.3,<1.3.0`, `~1.2 → >=1.2.0,<1.3.0`, `~1 → >=1.0.0,<2.0.0`.
- **`apcore.registry.registry.DEFAULT_MODULE_VERSION`** constant (`"1.0.0"`) — canonical default applied by every registration path (`register`, `_register_in_order`, `ModuleDescriptor.version` fallback) for modules without an explicit `version=` argument or `version` class/instance attribute.
- **`ExecutorProtocol`** — Protocol describing the minimal async-call surface required by `AsyncTaskManager`. Concrete `Executor` still satisfies it. Decouples the task manager from the concrete executor for testing.
- **`Executor.close()`** plus sync (`__enter__` / `__exit__`) and async (`__aenter__` / `__aexit__`) context-manager support — releases the cached `_sync_loop` deterministically. Long-lived singleton executors can continue to ignore this; short-lived executors (per-request, per-test) should call `close()` or use `with Executor(...) as executor:`.
- **`Registry.get_callback_errors(event=None)`** — public accessor returning the per-event callback-exception count recorded by `_trigger_event`. Ops can watch these counters to spot misbehaving subscribers. Event-callback exceptions remain logged + suppressed so the registry's register/unregister contract stays crash-free.

### Fixed

- **`resolve_dependencies` cycle path accuracy** — `_extract_cycle` previously returned a phantom path (all remaining nodes plus the first one re-appended) when the arbitrarily-picked start node had no outgoing edge inside `remaining`. Rewritten to DFS from each remaining node (sorted) and return a true back-edge cycle `[n0, ..., nk, n0]`. When no back-edge exists (e.g., Kahn's sort stalled on a non-cycle blocker), the resolver now raises `ModuleLoadError` naming the blocked modules instead of emitting a `CircularDependencyError` with a phantom `sorted(remaining)` path.
- **`Registry._register_in_order` populates `_versioned_modules` and `_versioned_meta`** — discover()-path modules were previously written only to the latest-only `_modules` map, leaving `Registry.get(id, version_hint=…)` unable to resolve them (version-hint queries route through the versioned store first). All registration paths now produce equivalent state.
- **Default version alignment** — `Registry.register()` with no `version=` now falls back to `DEFAULT_MODULE_VERSION` (`"1.0.0"`), matching `_resolve_load_order` and `ModuleDescriptor.version`. Previously `register()` used `"0.0.0"` while `_resolve_load_order` used `"1.0.0"` — the same module routed through the two paths got different effective versions.
- **Non-string versions warn-and-coerce** — a module with `version: 1` in YAML (integer) or a numeric class attribute is no longer silently dropped from constraint enforcement. The resolver logs a WARN naming the module and coerces via `str(...)`.
- **Malformed version constraints raise `VersionConstraintError`** — previously `">=not_a_version"`, `"v1.0"`, and `"~"` alone passed `_CONSTRAINT_RE` and degraded to `(0,0,0)` comparisons that always returned True. The constraint regex now requires a digit-leading operand, and `_check_single_constraint` raises explicitly on malformed input.
- **Scanner confines `follow_symlinks=True` to the extension root** — symlinks whose real path escapes the root (e.g., into `/etc`, `$HOME`, a sibling project) are now refused with a WARN log. The previous code would walk any target once per real-path visit. Combined with a WARN log on the first discover() when `follow_symlinks=True` is configured, this makes the trust boundary visible.
- **`Executor._run_in_new_thread` bounds `thread.join()` by `_global_timeout`** — a dead-locked coroutine can no longer indefinitely hang the sync caller. On timeout the daemon thread is left running (process exit stays clean) and the caller receives a `ModuleTimeoutError`.
- **`Executor.stream` post-stream validation failures log at WARNING (not DEBUG)** — unvalidated output that already reached the consumer is worth investigating even if it can't be un-sent. Previously such failures were invisible in default production observability.
- **`AsyncTaskManager.cancel` narrows its `except`** — catches `asyncio.CancelledError` specifically (the expected cancellation path). Unexpected exceptions from the cancelled task are now logged at WARNING with a stack trace instead of being silently swallowed alongside CancelledError.
- **`errors.py __all__`** — adds `DependencyNotFoundError`, `DependencyVersionMismatchError`, `TaskLimitExceededError`, `VersionConstraintError`. `from apcore.errors import *` now picks them up.
- **Inline `__import__('time')` / `__import__('os')`** in `_ModuleChangeHandler` replaced with top-level imports. No behavior change.

### Changed (BREAKING)

- **Missing required dependencies now raise `DependencyNotFoundError` (code `DEPENDENCY_NOT_FOUND`) instead of `ModuleLoadError` (code `MODULE_LOAD_ERROR`).** Brings Python into compliance with PROTOCOL_SPEC §5.15.2 which has always mandated `DEPENDENCY_NOT_FOUND`. Upgrade path: catch `DependencyNotFoundError` specifically, or catch the `ModuleError` base class for any dependency-related failure. The error-code-based dispatch (via `ErrorCodes.DEPENDENCY_NOT_FOUND`) also works and is recommended for cross-language consumers.
- **`AsyncTaskManager.submit` now raises `TaskLimitExceededError`, not `RuntimeError`.** Callers catching `RuntimeError("Task limit reached …")` must migrate to `except TaskLimitExceededError:` (or catch `ModuleError` for any task-manager failure).
- **`Registry.register()` default version is now `"1.0.0"` instead of `"0.0.0"`** for modules registered without an explicit `version=` argument and without a class/instance `version` attribute. Callers that relied on `"0.0.0"` as an "unset" marker must pass `version="0.0.0"` explicitly. `ModuleDescriptor.version` has always defaulted to `"1.0.0"` — this aligns the internal state with the externally-visible view.
- **Malformed version constraint strings now raise `VersionConstraintError`** instead of silently evaluating to False (which, via the degraded-parse-semver path, effectively became True). Callers that relied on silent no-op behavior must wrap in try/except or sanitize upstream.
- **Dependency upper bounds** — `pydantic`, `pyyaml`, and `jsonschema` are now pinned to `<3`, `<7`, `<5` respectively. Prevents silent breakage from downstream major releases; raise the cap deliberately after a compatibility check.

### Added

- **`DECLARATIVE_CONFIG_SPEC.md` v1.0** — Canonical spec for bindings, pipeline config, and entry-point YAML. Lives in `apcore/docs/spec/`. Defines cross-SDK YAML syntax parity, error model, configurable policy limits, and auto_schema semantics. All three SDKs (Python, TypeScript, Rust) now conform to this unified specification.
- **`auto_schema: true | permissive | strict`** — Explicit auto-schema mode selection. `strict` mode enforces OpenAI/Anthropic-compatible schemas (`additionalProperties: false`, all properties required, restricted type set). Incompatible features produce `BindingStrictSchemaIncompatibleError` at parse time.
- **`auto_schema` as implicit default** — When no schema mode is specified in a binding entry, auto-schema inference is attempted automatically. This formalizes Python's existing behavior as cross-SDK spec.
- **Schema mode conflict detection** — Specifying multiple schema modes (e.g., `auto_schema` + `input_schema`) now produces `BindingSchemaModeConflictError` at parse time.
- **`spec_version` field** in binding YAML files. Defaults to `"1.0"` with deprecation warning when absent (mandatory in spec 1.1).
- **New canonical error classes**: `BindingSchemaInferenceFailedError`, `BindingSchemaModeConflictError`, `BindingStrictSchemaIncompatibleError`, `BindingPolicyViolationError`. Each includes file path, line, module ID, and spec section reference.
- **`display` field** support on `FunctionModule` and binding YAML entries. Surface overlay for CLI/MCP/A2A presentation per `binding.schema.json#/DisplayOverlay`.
- **`documentation`, `annotations`, `metadata`** fields now round-trip through `BindingLoader` → `FunctionModule`.
- **Cross-SDK conformance fixtures** in `apcore/conformance/fixtures/`: `binding_yaml_canonical.yaml` (YAML parse parity), `binding_errors.json` (error message parity).

### Changed

- **`Context.create(trace_parent=...)`** — strict input validation per PROTOCOL_SPEC §10.5. trace_ids that are all-zero or all-f (W3C-invalid) now trigger regeneration + WARN log, matching the other SDKs. No auto-normalization (dashed-UUID stripping or case folding) is performed at `Context.create`; such normalization is the caller's ContextFactory responsibility. Previously valid 32-hex inputs remain accepted verbatim. Covered by new conformance fixture `context_trace_parent.json`.
- **`BindingSchemaMissingError`** is now a deprecated alias for `BindingSchemaInferenceFailedError`. Error code changed from `BINDING_SCHEMA_MISSING` to `BINDING_SCHEMA_INFERENCE_FAILED`. Existing `except BindingSchemaMissingError` catch clauses continue to work.
- **`BindingLoader._create_module_from_binding`** rewritten to implement DECLARATIVE_CONFIG_SPEC.md §3.4 schema resolution: explicit schemas > schema_ref > explicit auto > implicit auto default. Replaces the previous if/elif chain.
- **`FunctionModule.__init__`** now accepts `display: dict | None` parameter.
- **`Annotations` in `binding.schema.json`** expanded from 5 to 12 fields (added `streaming`, `cacheable`, `cache_ttl`, `cache_key_fields`, `paginated`, `pagination_style`, `extra`) to align with `module-meta.schema.json`.

### Removed

- **Implicit auto_schema as Python-specific behavior** — this is now a cross-SDK spec-defined behavior, not a Python-only quirk.

## [0.18.0] - 2026-04-15

### Added

- **`APCore` unified client class** (`apcore.client.APCore`) — High-level facade over `Registry` + `Executor` providing a single entry point for all module operations. Constructor accepts optional `registry`, `executor`, `config`, and `metrics_collector` (auto-created when `sys_modules.enabled`). Public API surface:
  - **Module management**: `module()` decorator, `register()`, `list_modules(tags=, prefix=)`, `discover()`, `describe()`
  - **Execution**: `call()`, `call_async()`, `stream()`, `validate()` — all accept `version_hint` for semver negotiation (A14)
  - **Middleware**: `use()`, `use_before()`, `use_after()`, `remove()` — `use`/`use_before`/`use_after` return `self` for chaining
  - **Events**: `events` property, `on(event_type, handler)`, `off(subscriber)` — requires `sys_modules.events.enabled` in config
  - **Module toggle**: `disable(module_id, reason=)`, `enable(module_id, reason=)` — wrappers around `system.control.toggle_feature`
  - Cross-language parity: matches apcore-typescript `APCore` class and apcore-rust `APCore` struct public API surface
- **Package-level global convenience functions** (`apcore.call`, `apcore.call_async`, `apcore.stream`, `apcore.validate`, `apcore.register`, `apcore.describe`, `apcore.use`, `apcore.use_before`, `apcore.use_after`, `apcore.remove`, `apcore.discover`, `apcore.list_modules`, `apcore.on`, `apcore.off`, `apcore.disable`, `apcore.enable`, `apcore.module`) — delegate to a module-level `_default_client = APCore()` instance for zero-setup usage (`import apcore; apcore.call("math.add", {"a": 1, "b": 2})`). Python-specific ergonomic; apcore-typescript and apcore-rust require explicit client construction.
- **Pipeline preset builders re-exported at package root** — `build_standard_strategy`, `build_internal_strategy`, `build_testing_strategy`, `build_performance_strategy`, `build_minimal_strategy` are now importable directly from `apcore`. These functions existed in `apcore.builtin_steps` but were not previously in `apcore.__all__`. Parity with apcore-typescript (`buildXxxStrategy`) and apcore-rust (`build_xxx_strategy` at the crate root).
- **`TestRegisterInternalValidation`** test class in `tests/registry/test_registry.py` (6 parity tests covering empty rejection, pattern rejection, over-length rejection, reserved-word bypass, duplicate rejection, accept-at-max-length) plus `test_pipeline_preset_builders_*` in `tests/test_public_api.py`.
- **`Registry.export_schema()`** — New method returning a module's schema definition as a dict, with optional `strict=True` for OpenAI/Anthropic strict schema compliance (`additionalProperties: false`). Aligned with apcore-rust `Registry::export_schema()`.

### Changed

- **`Executor.describe_pipeline()` now returns `StrategyInfo` instead of `str`** — Provides structured access to `step_count`, `step_names`, `name`, and `description`. `str(result)` produces the original formatted string via `StrategyInfo.__str__`. Aligned with apcore-typescript `describePipeline() -> StrategyInfo`.

- **`Executor.call()` and `Executor.call_with_trace()` are now thin sync wrappers** over `call_async()` and `call_async_with_trace()` via a shared `_run_async_in_sync(coro, module_id)` dispatcher. The cached-event-loop / thread-bridge logic that was previously inlined in three places lives in one helper. Sync semantics preserved: nested calls inside a running event loop still route through a background thread.
- **`Executor.call_async_with_trace()` now uses the unified A11 error recovery path** (`_translate_abort` + `_recover_from_call_error` + middleware `on_error` chain). Previously it called `engine.run` raw and let `PipelineAbortError` leak; behavior now matches `call_async`. When a middleware `on_error` recovers, the recovery dict is returned alongside a sentinel `PipelineTrace` (per-step trace detail is unavailable in the recovery branch — use `call_async` if you don't need the trace, or attach a tracing middleware).
- **`BuiltinApprovalGate` now self-contains the full approval flow.** Audit-log emission, span-event emission, and full status→error mapping (including `timeout` and unknown-status warning) used to live on private methods of `Executor`, with `BuiltinApprovalGate` reaching into them via `hasattr(executor, '_check_approval_async')`. The reach-into-private cheat is gone; `BuiltinApprovalGate` does everything itself. The `executor=` parameter on `BuiltinApprovalGate.__init__` is removed (was unused after consolidation). **Approval audit logs are now emitted from logger `apcore.builtin_steps`** (was `apcore.executor`) — update any log filters accordingly.
- **`BuiltinACLCheck` and `BuiltinApprovalGate` now expose public `set_acl()` / `set_handler()` setters.** `Executor.set_acl` and `set_approval_handler` use the public setters instead of poking step `._acl` / `._handler`. Custom user-supplied ACL or approval steps without these setters are silently skipped — re-register the strategy if you need to swap providers on a custom step.
- **`Registry._discover_default()` decomposed** from a 153-line god method into a 23-line orchestrator + 9 named stage helpers (`_scan_params`, `_scan_roots`, `_apply_id_map_overrides`, `_load_all_metadata`, `_resolve_all_entry_points`, `_validate_all`, `_resolve_load_order`, `_filter_id_conflicts`, `_register_in_order`, `_invoke_on_load`). Pure refactor — no behavior change. Mirrors the structure of `apcore-typescript`'s `_discoverDefault`.
- **`ACL.check()` and `ACL.async_check()` consolidated** via shared `_snapshot()` and `_finalize_check()` helpers. Audit-entry construction and debug-logging now live in exactly one place (was duplicated four times). Fixed `_matches_rule_async` to call `_match_patterns()` instead of inlining a variant that bypassed compound operators (`$or`/`$not`).
- **ACL singular condition handler aliases removed** (`identity_type`, `role`, `call_depth`). Spec §6.1 only defines the plural forms (`identity_types`, `roles`, `max_call_depth`); the singular aliases were a python-only divergence.
- **`builtin_steps.py` strategy builders no longer use `object.__setattr__`** for the `name` field. `ExecutionStrategy` was never a frozen dataclass — `s.name = X` always worked. Cargo-cult code removed.
- **`ErrorCodes` class `__setattr__`/`__delattr__` traps dropped.** The traps only fired on *instance* attribute mutation (`ErrorCodes().X = ...`), never on *class* attribute mutation (`ErrorCodes.X = ...`) which is how `ErrorCodes` is actually used. Cargo-cult immutability that gave a false sense of protection. Aligned with apcore-typescript (`Object.freeze`) and apcore-rust (enum).
- **Pydantic v1/v2/dataclass/constructor fallback cascade collapsed in `config.py`.** Previously maintained a 4-branch compatibility chain for Pydantic v1 → v2 migration. The project requires Pydantic v2 since 0.16.0; dead branches removed.
- **`Registry._handle_file_change()` refactored** — replaced fragile `dir(mod)` module-attribute discovery with explicit registry lookup. More predictable behavior on hot-reload events.
- **`Registry.register()` / `register_internal()` now populate `_module_meta`** at registration time, not lazily at first `get_definition()` call. Consistent with `_discover_default` path.
- **31 pre-existing pyright type errors resolved** across `executor.py`, `config.py`, `registry.py`, `builtin_steps.py`, and `acl.py`. No runtime behavior change; strict type-checking now passes cleanly.
- **`MAX_MODULE_ID_LENGTH` raised from 128 to 192** (`apcore.registry.registry`). Tracks PROTOCOL_SPEC §2.7 EBNF constraint #1 update — accommodates Java/.NET deep-namespace FQN-derived IDs while remaining filesystem-safe (`192 + len('.binding.yaml') = 205 < 255`-byte filename limit on ext4/xfs/NTFS/APFS/btrfs). Module IDs valid before this change remain valid; only the upper bound moved. **Forward-compatible relaxation:** older 0.17.x/0.18.x readers will reject IDs in the 129–192 range emitted by this version.
- **`Registry.register()` and `Registry.register_internal()` now share a `_validate_module_id()` helper** that runs validation in canonical order (empty → EBNF pattern → length → reserved word per-segment). The reserved-word check is the only step `register_internal()` skips (so sys modules can use the `system.*` prefix); empty/pattern/length/duplicate now apply uniformly. Aligned cross-language with apcore-typescript and apcore-rust.
- **`register_internal()` now enforces empty / pattern / length / duplicate checks.** Previously bypassed every validation step. Production callers (`apcore.sys_modules.*`) all use canonical-shape IDs so no in-tree caller is broken; external adapters that used `register_internal` as a generic escape hatch should review.
- **Duplicate registration error message canonicalized** to `"Module ID '<id>' is already registered"` (was `"Module already exists: <id>"` for `register_internal`). Both `register()` and `register_internal()` now emit the same message via the shared error path. Aligned with apcore-rust and apcore-typescript byte-for-byte.

### Removed

- **`FeatureNotImplementedError` and `DependencyNotFoundError`** — zero raise-sites across the codebase; `grep -rn` confirmed no production or test code instantiated either class. Error codes `GENERAL_NOT_IMPLEMENTED` and `DEPENDENCY_NOT_FOUND` remain in `ErrorCodes` for use via the generic `ModuleError` constructor. Aligned with apcore-typescript (commit `01ea84d`).

### Removed (BREAKING)

- **`Context.to_dict()` and `Context.from_dict()`** — superseded by the spec-compliant `Context.serialize()` and `Context.deserialize()` (shipped in v0.16.0). The two pairs were silently inconsistent (`to_dict` always emitted `redacted_inputs` even when `None` while `serialize` omitted it; `serialize` included `_context_version: 1`, `to_dict` did not), so mixing them produced divergent dicts. Migration:
  - `ctx.to_dict()` → `ctx.serialize()`
  - `Context.from_dict(data, executor=x)` → `Context.deserialize(data); ctx.executor = x` (the `executor=` parameter is removed; reassign directly on the returned `Context`, which is non-frozen)
- **Private `Executor` approval helpers removed** as part of the `BuiltinApprovalGate` consolidation. No public API impact unless your code reached into `Executor._check_approval_async`, `_build_approval_request`, `_handle_approval_result`, `_emit_approval_event`, `_needs_approval`, `_check_approval_sync`, the timeout-aware `_run_async_in_sync` (the new same-named method has a different `(coro, module_id)` signature), `_async_cache`, or `_async_cache_lock`.
- **Legacy event aliases removed.** Per the §9.16 naming convention shipped in v0.15, the dual-emission transition period for `module_health_changed` and `config_changed` ended in this release (the original removal deadline was v0.16.0). Listeners that subscribed to these legacy names will no longer receive events. Migrate subscriptions to the canonical names:
  - `module_health_changed` → `apcore.module.toggled` (from `system.control.toggle_feature`) **or** `apcore.health.recovered` (from `PlatformNotifyMiddleware`)
  - `config_changed` → `apcore.config.updated` (from `system.control.update_config`) **or** `apcore.module.reloaded` (from `system.control.reload_module`)
- **Renamed private method `_emit_config_changed` → `_emit_module_reloaded`** in `system.control.reload_module` to reflect the canonical event it emits. Private API, no public-surface impact.

### Fixed

- **Global convenience functions `call()`, `call_async()`, `stream()` missing `version_hint` parameter** — These `apcore/__init__.py` wrappers previously forwarded only `(module_id, inputs, context)` to the `APCore` client, silently dropping `version_hint`. Users calling `apcore.call(..., version_hint=">=1.0.0")` would have had the hint ignored. Now all three wrappers accept and forward `version_hint: str | None = None`, matching the `APCore` class signature and cross-language SDKs.
- **Spec §4.13 annotation merge — YAML annotations are no longer silently dropped at registration.** Two coupled bugs were repaired: (1) `registry/metadata.py:merge_module_metadata` was doing whole-replacement of the `annotations` field instead of the field-level merge mandated by §4.13 ("If YAML only defines `readonly: true`, other fields **must** retain values from code or defaults."), and (2) `registry/registry.py:get_definition` was ignoring even that broken merge result and reading directly from the module's class attribute. The fix wires the previously-unwired `apcore.schema.annotations.merge_annotations` and `merge_examples` (which were defined and unit-tested but never called from production) into the registry pipeline, and updates `get_definition` to consume the merged metadata. **User-observable behavior change:** modules that supplied `annotations:` in their `*_meta.yaml` companion files were previously seeing those annotations silently ignored. Those annotations will now be honored. Modules that relied on the broken behavior should audit their `*_meta.yaml`. Adds 5 regression tests covering field-level merge, YAML-only, neither-defined, examples override, and an end-to-end `discover() → get_definition()` round-trip.
- **`ModuleAnnotations.from_dict` precedence inversion** — Per PROTOCOL_SPEC §4.4.1 rule 7, when the same key appears both in a nested `extra` object and as a top-level overflow key, the **nested value now wins** (previously the top-level overflow would silently overwrite it). Behavior change is observable only in the pathological case where an input contains both forms of the same key — no conformant producer emits this. Top-level overflow keys are still tolerated and merged into `extra` for backward compatibility.

## [0.17.1] - 2026-04-06

### Added

- **`build_minimal_strategy()`** — 4-step pipeline (context → lookup → execute → return) for pre-validated internal hot paths. Registered as `"minimal"` in Executor preset builders.
- **`requires` / `provides` on `BaseStep`** — Optional advisory fields declaring step dependencies. `ExecutionStrategy` validates dependency chains at construction and insertion, emitting warnings for unmet `requires`.

### Fixed

- **`"minimal"` added to preset builders** — `Executor(strategy="minimal")` now works. Previously missing from `_resolve_strategy_name()` preset dict.
- **Executor docstrings updated** — Constructor and `_resolve_strategy_name` docstrings now list all 5 presets (was missing `"minimal"`).

---

## [0.17.0] - 2026-04-05

### Added

- **Step Metadata**: Four declarative fields on `BaseStep`: `match_modules` (glob patterns for selective execution), `ignore_errors` (fault-tolerant steps), `pure` (safe for `validate()` dry-run), `timeout_ms` (per-step timeout).
- **YAML Pipeline Configuration**: `register_step_type()`, `unregister_step_type()`, `registered_step_types()`, `build_strategy_from_config()` — configure pipeline steps via `apcore.yaml` at startup.
- **PipelineContext fields**: `dry_run`, `version_hint`, `executed_middlewares` for pipeline-aware execution.
- **StepTrace**: `skip_reason` field for understanding why steps were skipped ("no_match", "dry_run", "error_ignored").

### Changed

- **Step order**: `middleware_before` now runs BEFORE `input_validation` (was after). Middleware input transforms are now validated by the schema check.
- **Executor delegation**: `call()`, `call_async()`, `validate()`, and `stream()` fully delegate to `PipelineEngine.run()`. Removed ~300 lines of duplicated inline step code.
- **Renamed**: `safety_check` step → `call_chain_guard` (accurately describes call-chain depth/cycle/repeat checking).
- **Renamed**: `BuiltinSafetyCheck` class → `BuiltinCallChainGuard`.

### Fixed

- Middleware input transforms were never re-validated against the schema (now validated after middleware runs).
- `validate()` was hardcoded to 7 inline checks; now uses `dry_run=True` pipeline mode — user-added `pure=True` steps automatically participate.

---

## [0.16.0] - 2026-04-05

### Added

- **Config Bus**: `env_style` (auto/nested/flat), `max_depth`, `env_prefix` auto-derivation, `env_map` (namespace + global), `Config.env_map()`, `CONFIG_ENV_MAP_CONFLICT` error.
- **Context**: `ContextKey[T]` typed accessor with `get()`/`set()`/`delete()`/`exists()`/`scoped()`. Built-in key constants (`TRACING_SPANS`, `METRICS_STARTS`, etc.). `Context.serialize()`/`deserialize()` with `_context_version: 1`.
- **Annotations**: `extra: dict[str, Any]` extension field on `ModuleAnnotations`. `pagination_style` changed from `Literal` to `str`. `DEFAULT_ANNOTATIONS` constant. `from_dict()` classmethod with unknown key capture.
- **ACL**: `SyncACLConditionHandler` / `AsyncACLConditionHandler` protocols. `ACL.register_condition()`. `$or`/`$not` compound operators. `async_check()` method. Fail-closed for unknown conditions.
- **Pipeline**: `Step` protocol, `BaseStep` ABC, `StepResult`, `PipelineContext`, `PipelineTrace`, `ExecutionStrategy`, `PipelineEngine`. 11 `BuiltinStep` classes. Preset strategies (standard/internal/testing/performance). `Executor.strategy` parameter. `call_with_trace()`/`call_async_with_trace()`. `register_strategy()`/`list_strategies()`/`describe_pipeline()`.

### Changed

- Middleware data keys migrated from legacy names (`_metrics_starts` etc.) to `_apcore.mw.*` convention using typed `ContextKey`.

---

## [0.15.1] - 2026-03-31

### Changed

- **Env prefix convention simplified** — Removed the `^APCORE_[A-Z0-9]` reservation rule from `Config._validate_env_prefix()`. Sub-packages now use single-underscore prefixes (`APCORE_MCP`, `APCORE_OBSERVABILITY`, `APCORE_SYS`) instead of the double-underscore form. Only the exact `APCORE` prefix is reserved for the core namespace.
- Built-in namespace env prefixes: `APCORE__OBSERVABILITY` → `APCORE_OBSERVABILITY`, `APCORE__SYS` → `APCORE_SYS`.

---

## [0.15.0] - 2026-03-30

### Added

#### Config Bus Architecture (§9.4–§9.14)
- **`Config.register_namespace(name, schema=None, env_prefix=None, defaults=None)`** — Class-level namespace registration. Any package can claim a named config subtree with optional JSON Schema validation, env prefix, and default values. Global registry is shared across all `Config` instances. Late registration is allowed; call `config.reload()` afterward to apply defaults and env overrides.
- **`config.get("namespace.key.path")`** — Dot-path access with namespace resolution. First segment resolves to a registered namespace; remaining segments traverse the subtree.
- **`config.namespace(name)`** — Returns the full config subtree for a registered namespace as a dict.
- **`config.bind(ns, type)` / `config.get_typed(path, type)`** — Typed namespace access; `bind` returns a view of the namespace deserialized into `type`, `get_typed` deserializes a single dot-path value.
- **`config.mount(namespace, from_file=...|from_dict=...)`** — Attach external config sources to a namespace without a unified YAML file. Primary integration path for third-party packages with existing config systems.
- **`Config.registered_namespaces()`** — Class-level introspection; returns names of all registered namespaces.
- **Unified YAML with namespace partitioning** — Single YAML file with namespace-keyed top-level sections. Automatic mode detection: legacy mode (no `apcore:` key, fully backward compatible) vs. namespace mode (`apcore:` key present). `_config` is a reserved meta-namespace (`strict`, `allow_unknown`).
- **Per-namespace env override with longest-prefix-match dispatch** — Each namespace declares its own `env_prefix`. Apcore sub-packages use `APCORE_` prefixed names (e.g., `APCORE_OBSERVABILITY`, `APCORE_SYS`); the longest-prefix-match dispatch algorithm resolves any ambiguity with the core `APCORE` prefix.
- **Hot-reload namespace support** — `config.reload()` re-reads YAML, re-detects mode, re-applies namespace defaults and env overrides, re-validates, and re-reads mounted files.
- **New error codes** — `CONFIG_NAMESPACE_DUPLICATE`, `CONFIG_NAMESPACE_RESERVED`, `CONFIG_ENV_PREFIX_CONFLICT`, `CONFIG_MOUNT_ERROR`, `CONFIG_BIND_ERROR`

#### Error Formatter Registry (§8.8)
- **`ErrorFormatter` protocol** — Interface for adapter-specific error formatters. Implementations transform `ModuleError` into the surface-specific wire format (e.g., MCP camelCase, JSON-RPC code mapping).
- **`ErrorFormatterRegistry`** — Shared registry for surface-specific formatters:
  - `ErrorFormatterRegistry.register(surface, formatter)` — register a formatter for a named surface
  - `ErrorFormatterRegistry.get(surface)` — retrieve a registered formatter
  - `ErrorFormatterRegistry.format(surface, error)` — format an error, falling back to `error.to_dict()` if no formatter is registered for that surface
- **New error code** — `ERROR_FORMATTER_DUPLICATE`

#### Built-in Namespace Registrations (§9.15)
- **`observability` namespace** (`APCORE_OBSERVABILITY` env prefix) — apcore pre-registers this namespace, promoting the existing `apcore.observability.*` flat config keys (tracing, metrics, logging, error_history, platform_notify) into a named subtree. Adapter packages (apcore-mcp, apcore-a2a, apcore-cli) should read from this namespace rather than independent logging defaults.
- **`sys_modules` namespace** (`APCORE_SYS` env prefix) — apcore pre-registers this namespace, promoting the existing `apcore.sys_modules.*` flat keys into a named subtree. `register_sys_modules()` prefers `config.namespace("sys_modules")` in namespace mode with `config.get("sys_modules.*")` legacy fallback. Both registrations are 1:1 migrations of existing keys; there are no breaking changes.

#### Event Type Naming Convention and Collision Fix (§9.16)
- **Canonical event names** — Two confirmed event type collisions in apcore-python are resolved:
  - `"module_health_changed"` (previously used for both enable/disable toggles and error-rate recovery) split into `apcore.module.toggled` (toggle on/off) and `apcore.health.recovered` (error rate recovery)
  - `"config_changed"` (previously used for both key updates and module reload) split into `apcore.config.updated` (runtime key update via `system.control.update_config`) and `apcore.module.reloaded` (hot-reload via `system.control.reload_module`)
- **Naming convention** — `apcore.*` is reserved for core framework events. Adapter packages use their own prefix: `apcore-mcp.*`, `apcore-a2a.*`, `apcore-cli.*`.
- **Transition aliases** — All four legacy short-form names (`module_health_changed`, `config_changed`) continue to be emitted alongside the canonical names during the transition period.

---

## [0.14.0] - 2026-03-24

### Added
- **Middleware priority** — `Middleware` base class now accepts `priority: int` (0-1000, default 0). Higher priority executes first; equal priority preserves registration order. `BeforeMiddleware` and `AfterMiddleware` adapters also accept `priority`.
- **Priority range validation** — `ValueError` raised for priority values outside 0-1000

### Breaking Changes
- Middleware default priority changed from `0` to `100` per PROTOCOL_SPEC §11.2. Middleware without explicit priority will now execute before priority-0 middleware.


## [0.13.2] - 2026-03-22

### Changed
- Rebrand: aipartnerup → aiperceivable

## [0.13.1] - 2026-03-19

### Added
- **Dict schema support** — Modules can now define `input_schema` / `output_schema` as plain JSON Schema dicts instead of Pydantic model classes. A `_DictSchemaAdapter` transparently wraps dict schemas at registration time so all internal code paths (executor, schema exporter, `get_definition`) work without changes.

### Fixed
- **`get_definition()` crash on dict schemas** — Previously called `.model_json_schema()` on dict objects, causing `AttributeError`
- **Executor crash on dict schemas** — `call()`, `call_async()`, and `stream()` all called `.model_validate()` on dict objects

### Improved
- **File header docstrings** — Enhanced docstrings for `errors.py`, `executor.py`, and `version.py`

---

## [0.13.0] - 2026-03-12

### Added
- **Caching/pagination annotations** — `ModuleAnnotations` gains 5 new fields: `cacheable`, `cache_ttl`, `cache_key_fields`, `paginated`, `pagination_style` (all optional with defaults, backward compatible)
- **`pagination_style` Literal type** — Typed as `Literal["cursor", "offset", "page"]` instead of free-form `str`
- **`sunset_date`** — New field on `ModuleDescriptor` for module deprecation lifecycle (ISO 8601 date)
- **`on_suspend()` / `on_resume()` lifecycle hooks** — Duck-typed optional hooks for state preservation during hot-reload; integrated into `ReloadModuleModule` and registry watchdog
- **MCP `_meta` export** — Schema exporter includes `cacheable`, `cacheTtl`, `cacheKeyFields`, `paginated`, `paginationStyle` in `_meta` sub-dict
- **Suspend/resume tests** — `tests/test_suspend_resume.py` covering state transfer, backward compatibility, error handling

### Changed
- **Rebranded** — "module development framework" → "module standard" in pyproject.toml, `__init__.py`, README, and internal docstrings
- **`Module` Protocol** — `on_suspend`/`on_resume` deliberately kept OUT of Protocol (duck-typed via `hasattr`/`callable`)

---

## [0.12.0] - 2026-03-10

### Changed
- **`ExecutionCancelledError`** now extends `ModuleError` (was bare `Exception`) with error code `EXECUTION_CANCELLED`, aligning with PROTOCOL_SPEC §8.7 error hierarchy
- **`ErrorCodes`** — Added `EXECUTION_CANCELLED` constant

---

## [0.11.0] - 2026-03-08

### Added
- **Full lifecycle integration tests** (`tests/integration/test_full_lifecycle.py`) — 8 tests covering the complete 11-step pipeline with all gates (ACL + Approval + Middleware + Schema validation) enabled simultaneously, nested module calls, shared `context.data`, error propagation, and ACL conditions.

#### System Modules — AI Bidirectional Introspection
Built-in `system.*` modules that allow AI agents to query, monitor

- **`system.health.summary`** — Aggregate health status across all registered modules (healthy/degraded/unhealthy classification based on error rate thresholds).
- **`system.health.module`** — Per-module health detail including recent errors from `ErrorHistory`.
- **`system.manifest.module`** — Single module introspection (schema, annotations, tags, source path).
- **`system.manifest.full`** — Full registry manifest with filtering by tags/prefix.
- **`system.usage.summary`** — Usage statistics across all modules (call counts, error rates, avg latency).
- **`system.usage.module`** — Per-module usage detail with hourly trend data.
- **`system.control.update_config`** — Runtime config hot-patching with constraint validation.
- **`system.control.reload_module`** — Hot-reload a module from disk without restart.
- **`system.control.toggle_feature`** — Enable/disable modules at runtime with reason tracking.
- **`register_sys_modules()`** — Auto-registration wiring for all system modules.

#### Observability
- **`ErrorHistory`** — Ring buffer tracking recent errors with deduplication and per-module querying.
- **`ErrorHistoryMiddleware`** — Middleware that records `ModuleError` details into `ErrorHistory`.
- **`UsageCollector`** — Per-module call counting, latency histograms, and hourly bucketed trend data.
- **`PlatformNotifyMiddleware`** — Threshold-based sensor that emits events on error rate spikes.

#### Event System
- **`EventEmitter`** — Global event bus with async subscriber dispatch and thread-pool execution.
- **`EventSubscriber`** protocol — Interface for event consumers.
- **`ApCoreEvent`** — Frozen dataclass for typed events (module lifecycle, errors, config changes).
- **`WebhookSubscriber`** — HTTP POST event delivery with retry.
- **`A2ASubscriber`** — Agent-to-Agent protocol event bridge.

#### APCore Unified Client
- **`APCore.on()`** / **`APCore.off()`** — Event subscription management via the unified client.
- **`APCore.disable()`** / **`APCore.enable()`** — Module toggle control via the unified client.
- **`APCore.discover()`** / **`APCore.list_modules()`** — Discovery and listing via the unified client.

#### Public API Exports
- **`ModuleDisabledError`** — Error class for `MODULE_DISABLED` code, raised when a disabled module is called.
- **`ReloadFailedError`** — Error class for `RELOAD_FAILED` code (retryable).
- **`SchemaStrategy`** — Enum for schema resolution strategy (`yaml_first`, `native_first`, `yaml_only`).
- **`ExportProfile`** — Enum for schema export profiles (`mcp`, `openai`, `anthropic`, `generic`).

#### Registry
- **Module toggle** — APCore client now supports `disable()`/`enable()` for module toggling via `system.control.toggle_feature`, with `ModuleDisabledError` enforcement and event emission.
- **Version negotiation** — `negotiate_version()` for SDK/module version compatibility checking.


### Changed
- **`WebhookSubscriber` / `A2ASubscriber`** now require optional dependency `aiohttp`. Install with `pip install apcore[events]`. Core SDK no longer fails to import when `aiohttp` is not installed.

### Fixed
- **`aiohttp` hard import** in `events/subscribers.py` broke core SDK import when `aiohttp` was not installed. Changed to `try/except ImportError` guard with clear error message at runtime.
- **`A2ASubscriber.on_event`** `ImportError` for missing `aiohttp` was silently swallowed by the broad `except Exception` block. Moved guard before the `try` block to surface the error correctly.
- README Access Control example now includes required `Executor` and `Registry` imports.
- `pyproject.toml` repository/issues/changelog URLs now point to `apcore-python` (was incorrectly pointing to `apcore`).
- CHANGELOG `[0.7.1]` compare link added (was missing from link references).

---

## [0.10.0] - 2026-03-07

### Added

#### APCore Unified Client
- **`APCore.stream()`** — Stream module output chunk by chunk via the unified client.
- **`APCore.validate()`** — Non-destructive preflight check via the unified client.
- **`APCore.describe()`** — Get module description info (for AI/LLM use).
- **`APCore.use_before()`** — Add before function middleware via the unified client.
- **`APCore.use_after()`** — Add after function middleware via the unified client.
- **`APCore.remove()`** — Remove middleware by identity via the unified client.

#### Global Entry Points (`apcore.*`)
- **`apcore.stream()`** — Global convenience for streaming module calls.
- **`apcore.validate()`** — Global convenience for preflight validation.
- **`apcore.register()`** — Global convenience for direct module registration.
- **`apcore.describe()`** — Global convenience for module description.
- **`apcore.use()`** — Global convenience for adding middleware.
- **`apcore.use_before()`** — Global convenience for adding before middleware.
- **`apcore.use_after()`** — Global convenience for adding after middleware.
- **`apcore.remove()`** — Global convenience for removing middleware.

#### Error Hierarchy
- **`FeatureNotImplementedError`** — New error class for `GENERAL_NOT_IMPLEMENTED` code (renamed from `NotImplementedError` to avoid Python stdlib clash).
- **`DependencyNotFoundError`** — New error class for `DEPENDENCY_NOT_FOUND` code.

### Changed
- APCore client and `apcore.*` global functions now provide full feature parity with `Executor`.

---

## [0.9.0] - 2026-03-06

### Added

#### Enhanced Executor.validate() Preflight
- **`PreflightCheckResult`** — New frozen dataclass representing a single preflight check result with `check`, `passed`, and `error` fields.
- **`PreflightResult`** — New dataclass returned by `Executor.validate()`, containing per-check results and `requires_approval` flag. Duck-type compatible with `ValidationResult` via `.valid` and `.errors` properties.
- **Full 6-check preflight** — `validate()` now runs Steps 1–6 of the pipeline (module_id format, module lookup, call chain safety, ACL, approval detection, schema validation) without executing module code or middleware.

### Changed

#### Executor Pipeline
- **Step renumbering** — Approval Gate renumbered from Step 4.5 to Step 5; all subsequent steps shifted +1 (now 11 clean steps).
- **`validate()` return type** — Changed from `ValidationResult` to `PreflightResult`. Backward compatible: `.valid` and `.errors` still work identically for existing consumers (e.g., apcore-mcp router).
- **`validate()` signature** — Added optional `context` parameter for call-chain checks; `inputs` now defaults to `{}`.

#### Public API
- Exported `PreflightCheckResult` and `PreflightResult` from `apcore` top-level package.

## [0.8.0] - 2026-03-05

### Added

#### Executor Enhancements
- **Dual-timeout model** — Global deadline enforcement (`executor.global_timeout`) alongside per-module timeout. The shorter of the two is applied, preventing nested call chains from exceeding the global budget.
- **Cooperative cancellation** — On module timeout, the executor sends `CancelToken.cancel()` and waits a 5-second grace period before raising `ModuleTimeoutError`. Modules that check `cancel_token` can clean up gracefully.
- **Error propagation (Algorithm A11)** — All execution paths (sync, async, stream) now wrap exceptions via `propagate_error()`, ensuring middleware always receives `ModuleError` instances with trace context.
- **Deep merge for streaming** — Streaming chunk accumulation uses recursive deep merge (depth-capped at 32) instead of shallow merge, correctly handling nested response structures.

#### Error System
- **ErrorCodeRegistry** — Custom module error codes are validated against framework prefixes and other modules to prevent collisions. Raises `ErrorCodeCollisionError` on conflict.
- **VersionIncompatibleError** — New error class for SDK/config version mismatches with `negotiate_version()` utility.
- **MiddlewareChainError** — Now explicitly `_default_retryable = False` per PROTOCOL_SPEC §8.6.

#### Utilities
- **`guard_call_chain()`** — Standalone Algorithm A20 implementation for call chain safety checks (depth, circular, frequency). Executor delegates to this utility.
- **`propagate_error()`** — Standalone Algorithm A11 implementation for error wrapping and trace context attachment.
- **`normalize_to_canonical_id()`** — Cross-language module ID normalization (Python snake_case, Go PascalCase, etc.).
- **`calculate_specificity()`** — ACL pattern specificity scoring for deterministic rule ordering.
- **`parse_docstring()`** — Docstring parser for extracting parameter descriptions from function docstrings.

#### ACL Enhancements
- **Audit logging** — `ACL` constructor accepts optional `audit_logger` callback. All access decisions emit `AuditEntry` with timestamp, caller/target IDs, matched rule, identity, and trace context.
- **Condition-based rules** — ACL rules support `conditions` for identity type, role, and call depth filtering.

#### Config System
- **Full validation** — `Config.validate()` checks schema structure, value types, and range constraints.
- **Hot reload** — `Config.reload()` re-reads the YAML source and re-validates.
- **Environment overrides** — `APCORE_*` environment variables override config values (e.g., `APCORE_EXECUTOR_DEFAULT_TIMEOUT=5000`).
- **`Config.from_defaults()`** — Factory method for default configuration.

#### Middleware
- **RetryMiddleware** — Configurable retry with exponential/fixed backoff, jitter, and max delay. Only retries errors marked `retryable=True`.

#### Registry Enhancements
- **ID conflict detection** — Registry detects and prevents registration of conflicting module IDs.
- **Safe unregister** — `safe_unregister()` with drain timeout for graceful module removal.

#### Context
- **Generic `services` typing** — `Context[T]` supports typed dependency injection via the `services` field.

#### Testing
- **Conformance test suite** — JSON fixture-driven tests for error codes, call chain safety, ACL evaluation, pattern matching, specificity, ID normalization, and version negotiation.
- **New unit tests** — 17 new test files covering all added features.

### Changed

#### Executor Internals
- `_check_safety()` now delegates to standalone `guard_call_chain()` instead of inline logic.
- Error handling wraps exceptions with `propagate_error()` and re-raises with `raise wrapped from exc`.
- Global deadline set on root call only, propagated to child contexts via `Context._global_deadline`.

#### Public API
- Expanded `__all__` in `apcore.__init__` with new exports: `RetryMiddleware`, `RetryConfig`, `ErrorCodeRegistry`, `ErrorCodeCollisionError`, `VersionIncompatibleError`, `negotiate_version`, `guard_call_chain`, `propagate_error`, `normalize_to_canonical_id`, `calculate_specificity`, `AuditEntry`, `parse_docstring`.

## [0.7.1] - 2026-03-04

### Added

#### Public API Extensions
- **Module Protocol** — Introduced `Module` protocol in `apcore.module` for standardized module typing.
- **Schema System** — Exposed schema APIs (`SchemaLoader`, `SchemaValidator`, `SchemaExporter`, `RefResolver`, `to_strict_schema`) to the top-level `apcore` exports.
- **Utilities** — Exposed `match_pattern` utility to the top-level `apcore` exports.

## [0.7.0] - 2026-03-01

### Added

#### Approval System (PROTOCOL_SPEC §7)
- **ApprovalHandler Protocol** - Async protocol for pluggable approval handlers with `request_approval()` and `check_approval()` methods
- **ApprovalRequest / ApprovalResult** - Frozen dataclasses carrying invocation context and handler decisions with `Literal` status typing
- **Phase A (synchronous)** - Handler blocks until approval decision; denied/timeout raise immediately
- **Phase B (asynchronous)** - `pending` status returns `_approval_token` for async resume via `check_approval()`
- **Built-in handlers** - `AlwaysDenyHandler` (safe default), `AutoApproveHandler` (testing), `CallbackApprovalHandler` (custom logic)
- **Approval errors** - `ApprovalError`, `ApprovalDeniedError`, `ApprovalTimeoutError`, `ApprovalPendingError` with `result`, `module_id`, and `reason` properties
- **Audit events (Level 3)** - Dual-channel emission: `logging.info()` always + span events when tracing is active
- **Extension point** - `approval_handler` registered as a built-in extension point in `ExtensionManager`
- **ErrorCodes** - Added `APPROVAL_DENIED`, `APPROVAL_TIMEOUT`, `APPROVAL_PENDING` constants

#### Executor Integration
- **Step 4.5 approval gate** - Inserted between ACL (Step 4) and input validation (Step 5) in `call()`, `call_async()`, and `stream()`
- **Executor.set_approval_handler()** - Runtime handler configuration
- **Executor.from_registry()** - Added `approval_handler` parameter
- **Dict and dataclass annotations** - Both `ModuleAnnotations` and dict-style `requires_approval` supported
- **Unknown status fail-closed** - Unrecognized approval statuses treated as denied with warning log

### Changed

#### Structural Alignment
- Approval errors re-exported from `apcore.approval` for multi-language SDK consistency; canonical definitions remain in `errors.py`
- `ApprovalResult.status` typed as `Literal["approved", "rejected", "timeout", "pending"]` per PROTOCOL_SPEC §7.3.2

## [0.6.0] - 2026-02-23

### Added

#### Extension System
- **ExtensionManager / ExtensionPoint** - Added a unified extension-point framework for `discoverer`, `middleware`, `acl`, `span_exporter`, and `module_validator`
- **Extension wiring** - Added `apply()` support to connect registered extensions into `Registry` and `Executor`

#### Async Task & Cancellation
- **AsyncTaskManager** - Added background task orchestration with status tracking, cancellation, concurrency limits, shutdown, and cleanup
- **TaskStatus / TaskInfo** - Added task lifecycle enum and metadata dataclass for async task management
- **CancelToken / ExecutionCancelledError** - Added cooperative cancellation primitives and integrated cancellation checks into executor flows

#### Trace Context & Observability
- **TraceContext / TraceParent** - Added W3C Trace Context utilities for `inject()`, `extract()`, and strict parsing via `from_traceparent()`
- **Context.create(trace_parent=...)** - Added distributed-tracing entry support by accepting inbound trace context
- **OTLPExporter top-level export** - Added OTLP exporter re-exports in observability and top-level public API

#### Registry Enhancements
- **Custom discoverer/validator hooks** - Added `set_discoverer()` and `set_validator()` integration paths
- **Module describe support** - Added `Registry.describe()` for human-readable module descriptions
- **Hot-reload APIs** - Added `watch()`, `unwatch()`, and file-change handling helpers for extension directories
- **Validation constants/protocols** - Added `MAX_MODULE_ID_LENGTH`, `RESERVED_WORDS`, `Discoverer`, and `ModuleValidator` exports

### Changed

#### Public API Surface
- Expanded top-level `apcore` exports to include cancellation, extensions, async task types, trace context types, additional registry protocols/constants, and new error classes

#### Error System
- Added `ModuleExecuteError` and `InternalError` to the framework error hierarchy and exports
- Extended `ErrorCodes` with additional constants used by newer execution/extension paths

### Fixed

#### Execution & Redaction
- **executor** - Added recursive `_secret_` key redaction for nested dictionaries
- **executor** - Preserved explicit cancellation semantics by re-raising `ExecutionCancelledError`

#### Import Graph Robustness
- Reduced import-coupling risk across middleware/observability/trace typing paths while preserving existing runtime behavior and public interfaces

## [0.5.0] - 2026-02-22

### Changed

#### API Naming
- **decorator** - Renamed `_generate_input_model` / `_generate_output_model` to `generate_input_model` / `generate_output_model` as public API
- **context_logger** - Renamed `format` parameter to `output_format` to avoid shadowing Python builtin
- **registry** - Renamed `_write_lock` to `_lock` for clearer intent

#### Type Annotations
- **decorator** - Replaced bare `dict` with `dict[str, Any]` in `_normalize_result`, `annotations`, `metadata`, `_async_execute`, `_sync_execute`
- **bindings** - Fixed `_build_model_from_json_schema` parameter type from `dict` to `dict[str, Any]`
- **scanner** - Fixed `roots` parameter type from `list[dict]` to `list[dict[str, Any]]`
- **metrics** - Fixed `snapshot` return type from `dict` to `dict[str, Any]`
- **executor** - Removed redundant string-quoted forward references in `from_registry`; fixed `middlewares` parameter type to `list[Middleware] | None`

#### Code Quality
- **executor** - Extracted `_convert_validation_errors()` helper to eliminate 6 duplicated validation error conversion patterns
- **executor** - Refactored `call_async()` and `stream()` to use new async middleware manager methods
- **executor** - Removed internal `_execute_on_error_async` method (replaced by `MiddlewareManager.execute_on_error_async`)
- **loader** - Use `self._resolver.clear_cache()` instead of accessing private `_file_cache` directly
- **tracing** - Replaced `print()` with `sys.stdout.write()` in `StdoutExporter`
- **acl / loader** - Changed hardcoded logger names to `logging.getLogger(__name__)`

### Added

#### Level 2 Conformance (Phase 1)
- **ExtensionManager** and **ExtensionPoint** for unified extension point management (discoverer, middleware, acl, span_exporter, module_validator) with `register()`, `get()`, `get_all()`, `unregister()`, `apply()`, `list_points()` methods
- **AsyncTaskManager**, **TaskStatus**, **TaskInfo** for async task execution with status tracking (PENDING, RUNNING, COMPLETED, FAILED, CANCELLED), cancellation, and concurrency limiting
- **TraceContext** and **TraceParent** for W3C Trace Context support with `inject()`, `extract()`, and `from_traceparent()` methods
- `Context.create()` now accepts optional `trace_parent` parameter for distributed trace propagation

#### Async Middleware
- **MiddlewareManager** - Added `execute_before_async()`, `execute_after_async()`, `execute_on_error_async()` for proper async middleware dispatch with `inspect.iscoroutinefunction` detection
- **RefResolver** - Added `clear_cache()` public method for cache management
- **Executor** - Added `clear_async_cache()` public method
#### Schema Export
- **SchemaExporter** - Added `streaming` hint to `export_mcp()` annotations from `ModuleAnnotations`

### Fixed

#### Memory Safety
- **context** - Changed `Identity.roles` from mutable `list[str]` to immutable `tuple[str, ...]` in frozen dataclass

#### Observability
- **context_logger / metrics** - Handle cases where `before()` was never called in `ObsLoggingMiddleware` and `MetricsMiddleware`

#### Security
- **acl** - Added explicit `encoding="utf-8"` to YAML file open


## [0.4.0] - 2026-02-20

### Added

#### Streaming Support
- **Executor.stream()** - New async generator method for streaming module execution
  - Implements same 6-step pipeline as `call_async()` (context, safety, lookup, ACL, input validation, middleware before)
  - Falls back to `call_async()` yielding single chunk for non-streaming modules
  - For streaming modules, iterates `module.stream()` and yields each chunk
  - Accumulates chunks via shallow merge for output validation and after-middleware
  - Full error handling with middleware recovery
- **ModuleAnnotations.streaming** - New `streaming: bool = False` field to indicate if a module supports streaming execution
- **Test coverage** - Added 5 comprehensive tests in `test_executor_stream.py`:
  - Fallback behavior for non-streaming modules
  - Multi-chunk streaming
  - Module not found error handling
  - Before/after middleware integration
  - Disjoint key accumulation via shallow merge


## [0.3.0] - 2026-02-20

### Added

#### Public API Extensions
- **ErrorCodes** - New `ErrorCodes` class with all framework error code constants; replaces hardcoded error strings
- **ContextFactory Protocol** - New `ContextFactory` protocol for creating Context from framework-specific requests (e.g., Django, FastAPI)
- **Registry constants** - Exported `REGISTRY_EVENTS` dict and `MODULE_ID_PATTERN` regex for consistent module ID validation
- **Executor.from_registry()** - Convenience factory method for creating an Executor from a Registry with optional middlewares, ACL, and config

#### Schema System
- **Comprehensive schema system** - Full implementation with loading, validation, and export capabilities
  - Schema loading from JSON/YAML files
  - Runtime schema validation
  - Schema export functionality

### Fixed
- **ErrorCodes class** - Prevent attribute deletion to ensure error code constants remain immutable
- **Planning documentation** - Updated progress bar style in overview.md


## [0.2.3] - 2026-02-20

### Added

#### Public API
- **ContextFactory Protocol** - New `ContextFactory` protocol for creating Context from framework-specific requests (e.g., Django, FastAPI)
- **ErrorCodes** - New `ErrorCodes` class with all framework error code constants; replaces hardcoded error strings
- **Registry constants** - Exported `REGISTRY_EVENTS` dict and `MODULE_ID_PATTERN` regex for consistent module ID validation
- **Executor.from_registry()** - Convenience factory method for creating an Executor from a Registry with optional middlewares, ACL, and config

### Changed

#### Core Improvements
- **Module ID validation** - Strengthened to enforce lowercase letters, digits, underscores, and dots only; no hyphens allowed. Pattern: `^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*)*$`
- **Registry events** - Replaced hardcoded event strings with `REGISTRY_EVENTS` constant dict
- **Test fixtures** - Updated registry test module IDs to comply with new module ID pattern

#### Configuration
- **.code-forge.json** - Updated directory mappings: `base` from `planning/` to `./`; `input` from `features/` to `../apcore/docs/features`

### Improved
- Better type hints and protocol definitions for framework integration
- Consistent error handling with standardized error codes


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

### Supported Python Versions

- Python 3.11+

---

[0.13.0]: https://github.com/aiperceivable/apcore-python/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/aiperceivable/apcore-python/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/aiperceivable/apcore-python/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/aiperceivable/apcore-python/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/aiperceivable/apcore-python/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/aiperceivable/apcore-python/compare/v0.7.1...v0.8.0
[0.7.1]: https://github.com/aiperceivable/apcore-python/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/aiperceivable/apcore-python/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/aiperceivable/apcore-python/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/aiperceivable/apcore-python/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/aiperceivable/apcore-python/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/aiperceivable/apcore-python/compare/v0.2.3...v0.3.0
[0.2.3]: https://github.com/aiperceivable/apcore-python/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/aiperceivable/apcore-python/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/aiperceivable/apcore-python/releases/tag/v0.2.1
[0.2.0]: https://github.com/aiperceivable/apcore-python/releases/tag/v0.2.0
[0.1.0]: https://github.com/aiperceivable/apcore-python/releases/tag/v0.1.0