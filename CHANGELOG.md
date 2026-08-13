# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).


## [Unreleased]

### Added

- **`_config.strict` now also rejects undeclared keys *inside* the framework sections (PROTOCOL_SPEC §9.6.3 clause (b), §9.14 `reject_unknown_framework_keys`).** Every section in `schemas/apcore-config.schema.json` is `additionalProperties: false`, and that closedness was enforced by nothing at load time: `executor:` with a `zz_undeclared:` typo under it loaded clean in every SDK, so the operator read a default they believed they had overridden. Enforcement now has two tiers. **Default (`_config.strict` absent or false) is unchanged and unaffected** — the key is retained and readable through `get()`, which apcore-python already did by storing the parsed document as an untyped dict, now pinned by a test that asserts the retained value by *reading it back* rather than by observing that the load did not raise (an implementation that discarded the key at parse time also does not raise). **`_config.strict: true` raises `CONFIG_INVALID`**, and the error enumerates **every** offending key rather than failing on the first, so one restart shows the whole problem instead of one restart per typo. The rule applies in **legacy mode too**, where the whole document *is* the `apcore` namespace (§9.14 step 1), not only to the `apcore:` subtree of a namespace-mode file. `allow_unknown` deliberately plays no part: §9.6.3 defines it for unknown top-level *namespaces*, and stretching one field across two granularities would make its meaning depend on where it is read. Because the schema files ship with the spec repo and not with this package, the section→keys projection lives in `config._FRAMEWORK_SECTION_KEYS`, which `tests/conformance/test_config_key_governance.py` rebuilds from `apcore-config.schema.json` — resolving `$ref` and the `oneOf` branches `ExtensionsConfig` splits `root`/`roots` across — and fails on any difference, so a section added upstream breaks a test instead of going silently unenforced; `sys_modules` unions in `sys-modules.schema.json`, which is where §9.15.3 declares its subsections. Pinned by the new `unknown_framework_key_is_retained_by_default` and `unknown_framework_key_is_rejected_under_strict` cases of `conformance/fixtures/config_key_governance.json`, both driven in legacy **and** namespace mode. **This is opt-in: a deployment that does not set `_config.strict: true` sees no behaviour change.**

### Removed

- **`apcore.middleware.namespace_keys` (a second context-key registry that had drifted).** apcore-python had two registries for the same `_apcore.*` namespace: `apcore.context_keys`, the canonical one — typed `ContextKey[T]` slots, exported from the package root, imported by the logging/retry/metrics/usage middleware that actually write those keys — and `namespace_keys`, an untyped string mirror of a subset of it with **no readers anywhere in the package**. They disagreed: the mirror declared `_apcore.mw.tracing.span_id` (docstring: *"TracingMiddleware.before() writes the active span ID for the call"*), a key **nothing writes**, while the canonical registry declares `_apcore.mw.tracing.spans`, the span **stack** `observability/tracing.py` maintains and `executor.py` / `builtin_steps.py` / `trace_context.py` read. The single-slot key came from the second `TracingMiddleware` formulation of middleware-system.md §1.3, which apcore-python never implemented and which has since been **withdrawn** — a single slot is overwritten on the first nested call, which is why the surviving contract stores a stack and links `parent_span_id` explicitly. The mirror was deleted rather than re-exported from the canonical registry: a re-export would leave two spellings of every key (`namespace_keys.LOGGING_START_TIME` alongside `LOGGING_START`) and the same ambiguity about which to import, and hand-maintained duplication is the mechanism that produced the divergence in the first place. `apcore.middleware.context_namespace` keeps its actual job — `validate_context_key` / `enforce_context_key` / `APCORE_KEY_PREFIX` / `EXT_KEY_PREFIX`, the prefix rules of middleware-system.md §1.1 — and no longer carries a copy of the key list. **Migration:** `namespace_keys.LOGGING_START_TIME` → `apcore.context_keys.LOGGING_START.name`; `namespace_keys.CIRCUIT_STATE` → `apcore.middleware.circuit_breaker.CTX_CIRCUIT_STATE` (unchanged, and next to its writer); `namespace_keys.TRACING_SPAN_ID` has no replacement because it never had a writer — the span stack is `apcore.context_keys.TRACING_SPANS`.

### Fixed

- **`system.control.reload_module` raised `DUPLICATE_MODULE_ID` on every filesystem-backed reload (apcore-python#33).** `ReloadModule` unregisters the module, calls `_rediscover_module` — which runs `Registry.discover()` — and then calls `_reregister_module`, which called `Registry.register_internal`. But `discover()` *registers* what it finds, so the module was already published under its id by the time `register_internal` was asked to publish it again, and `register_internal` rejects an already-registered id with `InvalidInputError(code=DUPLICATE_MODULE_ID)`. Neither of the two escapes that could have made the sequence benign existed: `_register_in_order` skips ids already in `_modules`, but the reload unregisters first, so the id is absent and discovery does register it; and `register_internal` has no same-instance idempotent path — `detect_id_conflicts` returns an ERROR for the exact-duplicate id regardless of what object is being registered.

  **A single reload raised. A bulk reload lied.** `_execute_single` let the error propagate, so `reload_module` with a `module_id` failed outright. `_reload_one`, reached through a `path_filter`, raised inside `_execute_bulk`'s per-module `try`, which logs and continues — the call returned `{"success": True, "reloaded_modules": []}` while reloading nothing.

  `_reregister_module` now publishes only when the registry is not already holding that exact instance. When discovery published it the reload is already complete, and its entry is the higher-fidelity one: discovery writes `_versioned_modules` and merged metadata, whereas `register_internal` deliberately skips version tracking (D11-001), so the old code would have *downgraded* a discovered module into a sys/internal entry that `get(id, version_hint=...)` cannot resolve — a second defect the fix removes. A programmatic reload that hands over an unregistered instance still goes through `register_internal` unchanged, and a *different* instance registered under the id is still a genuine duplicate and still raises.

  **Why no test caught it:** every existing test that reaches the reload path stubs one of the two halves (`_rediscover_module`, `_reregister_module`, or `_reload_one` itself), so nothing ever registered before `register_internal` ran. The new `TestReloadModuleRealDiscoveryPath` in `tests/sys_modules/test_control.py` writes a real module file to a temporary extensions root, registers it through `Registry.discover()`, and reloads it with **nothing patched** — single, bulk, and repeated — plus a case asserting the reloaded module stays version-tracked, which distinguishes this fix from an unregister-then-`register_internal` one.

- **`$APCORE_CONFIG_FILE` no longer injects a phantom `config.file` key into the declared document (apcore#88).** The variable is the documented way to point at a configuration file (PROTOCOL_SPEC §9.14 discovery, read by `discover_config_file`), but §9.2 also makes *every* `APCORE_*` variable a configuration override and nothing exempted this one. Its suffix lowered to the dot-path `config.file`, so `Config.load(path)` with the variable set produced `{'version': …, 'project': …, 'config': {'file': '/path/…'}}` — a key `schemas/` declares nowhere (checked against `conformance/fixtures/config_key_governance.json`) sitting inside the **declared** document, which is the view §9.1's required-field check runs against. It is now dropped at the parse site in `_apply_env_overrides`: the variable is an *argument to* `load()` that happens to share a namespace with configuration, consumed to locate the file and then discarded, which is what every other argument-shaped input does. No spec change and no user-visible rename; file discovery is unaffected. Both legacy and namespace mode were affected and both are pinned by `TestConfigFileEnvVarIsNotAnOverride`, which asserts the **exact** declared key set rather than the absence of `config.file` — absence alone also holds for an implementation that lost a key the file really declares. The exemption is one variable wide: `APCORE_BINDINGS_DIR` → `bindings.dir` is a declared key and keeps working, asserted by a third case in the same class. The distinguishing test for any future variable is whether its dot-path is in the canonical key surface.
- **The conformance fixtures-directory locator moved out of the `APCORE_` prefix: `$APCORE_FIXTURES` → `$CONFORMANCE_FIXTURES` (apcore#88).** The exact twin of the `APCORE_SPEC_REPO` → `CONFORMANCE_SPEC_REPO` rename of apcore#86, and for the same reason: §9.2 lowered `APCORE_FIXTURES` to the config key `fixtures`, which no schema declares. This is test infrastructure, not configuration, so it does not belong in the prefix the spec has claimed. The old name is still read as a transitional fallback (`_LEGACY_FIXTURES_ENV`), and failure messages name whichever variable was actually set. No CI workflow sets either name.
- **BEHAVIOUR CHANGE: the documented nested `retry:` block on a subscriber is now read from config, on all five built-in types (apcore#85).** `features/event-system.md` documents a per-subscriber retry policy and shows it under a heading reading *"showing the policy on multiple subscriber types"* — an `a2a` entry with `max_attempts: 5` and a `file` entry with `max_attempts: 2`. **No SDK parsed it.** An operator who copied that example got the default policy, silently, with nothing to indicate the block had been ignored: `schemas/sys-modules.schema.json` does not describe subscriber entries beyond requiring a `type`, so nothing rejected the key either.

  The capability was already built at every other layer, which is why this survived. `EventRetryConfig` (`events/retry.py`) declares exactly the four keys the document shows and is documented as the policy "for event delivery to a single subscriber"; **every** subscriber constructor already accepted one and stored `self.retry`; and `emitter._get_retry_config` reads `getattr(subscriber, "retry", None)` with no type check and no allowlist, so whatever a subscriber carries is honoured. The single missing layer was YAML → object: only `_default_webhook_factory` built a policy, and only from the *legacy flat* `retry_count` shorthand. `a2a`, `file`, `stdout` and `filter` never constructed one.

  The new `sys_modules.registration._parse_retry_config` parses the block and every built-in factory passes the result through. Partial blocks merge over the spec defaults (`max_attempts=3`, `initial_backoff_ms=100`, `max_backoff_ms=30000`, `backoff_multiplier=2.0`), as the documented `file` example requires — it declares only two of the four keys. A `retry:` that is not a mapping is ignored rather than fatal.

  **All five types, not three.** An earlier reading held that `stdout` and `filter` are local and synchronous, so a policy on them would be inert. That is wrong on inspection: `FileSubscriber.on_event` catches, logs and **re-raises**; `StdoutSubscriber` is a bare `print(...)` whose failure propagates (EPIPE, closed stdout); and `FilterSubscriber` delegates, so a retry there re-runs delegate delivery. The emitter's retry loop acts on all five.

  **Flat `retry_count` still works** for `webhook` as a deprecated alias, with its existing `max_attempts = retry_count + 1` translation — that spelling is what deployments use today. **The nested block wins when both are present.**

  **This changes delivery behaviour for anyone who had already written the documented block**: a subscriber that was silently retrying 3 times now retries as configured. Pinned by `tests/events/test_subscriber_retry_config.py`, one case per subscriber type plus an end-to-end case asserting the configured `max_attempts` drives the real number of `on_event` invocations. Every asserted value differs from the default, so a case cannot pass against a factory that ignores the block.

---

## [0.27.0] - 2026-08-12

> **Release note:** this section contains BREAKING changes. It must ship as a
> **minor** (or major) version bump, never a patch.

### Added

- **`validate_context_key` / `enforce_context_key` (Issue #42, middleware-system.md §1.1).** apcore-python shipped no context-data namespace validator, so the normative rules — user middleware MUST NOT write `_apcore.*`, framework middleware MUST NOT write `ext.*`, unprefixed keys tolerated — were enforced by nothing, while apcore-typescript (`validateContextKey`) and apcore-rust (`validate_context_key`) both had one and both drove the three `middleware_hardening.json` `context_namespace_*` cases. `apcore.middleware.context_namespace` adds the peer implementation, returning the same `NamespaceCheck(valid, warning)` pair for the same `(writer, key)`; `validate_context_key`, `NamespaceCheck`, `APCORE_KEY_PREFIX` and `EXT_KEY_PREFIX` are exported from the top-level package.

### Changed

- **BREAKING (narrow): `TraceContext.inject()` now raises `InvalidParentIdError` for a malformed `parent_id` override (Issue #32, decision D-51).** It raised a bare `ValueError` carrying no `code`, so a polyglot caller matching on `INVALID_PARENT_ID` got the code from apcore-typescript (`src/trace-context.ts` sets `code = 'INVALID_PARENT_ID'`) and apcore-rust (`ErrorCode::InvalidParentId`) and nothing from apcore-python. The new `apcore.InvalidParentIdError` subclasses **both** `ModuleError` and `ValueError`, so `.code == "INVALID_PARENT_ID"` is available while every existing `except ValueError` caller keeps working; `str(exc)` now renders `"[INVALID_PARENT_ID] parent_id must be 16 lowercase hex chars, got 'ZZZZ'"`, which is also what `docs/features/observability.md` §"Optional `parent_id` Override on `inject()`" documents its Python example asserting (that example did not hold before this change). `ErrorCodes.INVALID_PARENT_ID` is registered, so the code is reserved against module collision. `TraceContext.from_traceparent()` deliberately keeps raising a bare `ValueError`: D-51 pins the code for the `inject()` override only, and apcore-typescript's `fromTraceparent` also throws a codeless error.

- **`StepMiddleware` ordering and error wrapping corrected (Issue #33 §2.2).** `after_step` and `on_step_error` now run in **reverse** registration order (onion model) — `after_step` already did, `on_step_error` ran forward and returned the LAST non-`None` value instead of short-circuiting on the first. A `before_step` that raises is now wrapped in `MiddlewareChainError` and stops the chain: the step body does not run, and `on_step_error` fires only on the middlewares whose `before_step` had already executed. It was previously swallowed, so a broken middleware let the step run anyway. Pinned by `conformance/fixtures/pipeline_step_middleware.json`, which apcore-python now drives from disk (`tests/conformance/test_pipeline_step_middleware.py`).

- **BREAKING: a `before_step` failure is terminal and is no longer recoverable (middleware-system.md § "A `before_step` failure terminates the step — it is not recoverable").** `PipelineEngine` routed the `MiddlewareChainError` raised by a failing `before_step` into the *same* handler as a step-body failure, so an `on_step_error` handler that returned a value made that value the step's output and let the pipeline advance **past a step whose body never ran**. `acl_check` and `approval_gate` are steps in the built-in strategy, so any registered `StepMiddleware` could make its own `before_step` raise, recover from it, and skip the ACL check or the approval gate outright — a silent authorization bypass reachable from an extension point that carries no authority. The step's `ignore_errors` swallowed the chain error for the same reason. The `before_step` invocation now lives outside the step-body `try` and terminates the run: `on_step_error` still fires on the already-entered middlewares in reverse order, but **for observation and cleanup only**, through a separate `_invoke_step_cleanup_hooks` pass (mirroring apcore-typescript `_runStepCleanupHooks`) rather than the recovery-seeking `_invoke_on_step_error`: every return value is discarded, and first-recovery-wins does not apply, because no recovery is being sought and short-circuiting would strand the cleanup of every middleware registered behind the first one to return a value. `after_step` does not fire (no body ran, nothing to close over) and `ignore_errors` does not apply — `MiddlewareChainError` propagates regardless. **Anyone relying on `on_step_error` to recover from a `before_step` failure will now see the error propagate.** Recovery from a *step-body* failure is unchanged, and `after_step` correctly fires after a recovered body so the onion still closes. Pinned by the new `before_step_failure_recovery_is_discarded` and `after_step_fires_after_a_recovered_step` cases of `conformance/fixtures/pipeline_step_middleware.json`; the driver asserts the bypass by observing that the **following** step did not execute, not merely that an error was raised.

- **BREAKING: `state.outputs` no longer contains the current step in `after_step` (middleware-system.md § "What `state.outputs` contains").** `PipelineEngine` recorded `step_outputs[step.name]` *before* invoking the `after_step` hook, so `after_step` was the one hook whose `state.outputs` included the step being observed — `before_step` (not yet run) and `on_step_error` (ran, produced nothing) both excluded it. The map now holds exactly the steps that completed **before** the current one in all three hooks, and the snapshot is taken after the hook returns, which is the ordering apcore-typescript (`src/pipeline.ts`) and apcore-rust (`src/pipeline.rs`) already had — apcore-python was the sole outlier. The rule is one rule with one meaning by design: under the more obvious reading, "outputs of completed steps", a middleware would have to know which hook it was in before it could read the map. **Any middleware reading `state.outputs[current_step]` inside `after_step` will now get a `KeyError`** — the value it wants is the `result` parameter, and always was; carrying the same value down two paths is how the two drift apart. The step-body **recovery** path is corrected with it, and in apcore-python it is corrected by the *same line*: the recovery branch assigns `result = recovery` and falls through to the shared success tail, so there is one snapshot site where apcore-typescript and apcore-rust each have two. `after_step` on a recovered step therefore also sees only the earlier steps, and the recovery value is snapshotted afterwards. `run_until` predicates are unaffected — they are evaluated after the step completes and deliberately do see it, so the snapshot sits between the `after_step` hook and the predicate. Pinned at **both** snapshot sites: `state_outputs_excludes_the_current_step_in_every_hook` covers the natural success path and `after_step_fires_after_a_recovered_step` covers the recovery path, which the first case cannot reach because it recovers from nothing. Both drivers assert the **exact** key set rather than the absence of one key — `"second" not in outputs` also passes against an implementation that lost `first`, or that never populated the map at all — and both snapshot that key set *inside* the hook, because `state.outputs` aliases the engine's live map (as it does in apcore-typescript): a driver that stashed the reference and read it after `run()` returned would see the final map on every entry and could never fail.

- **`before_step` documented as an observation hook.** The protocol docstring described a `(step_name, ctx, inputs)` signature with a return value that replaced the step's inputs. A `Step` is `execute(ctx)` and has no `inputs` parameter, so that convention existed nowhere; the spec was corrected to match the three implementations rather than the reverse.

- **`PipelineContext.trace` is now assigned.** The field existed and was never written, so `Executor.call_async_with_trace` had nothing to return on the middleware-recovery path and fabricated an empty `PipelineTrace`. Parity with apcore-typescript `pipelineCtx.trace` and apcore-rust `pipeline_ctx.trace`.
- **BREAKING: the module-invocation boundary no longer coerces types.** `BuiltinInputValidation` / `BuiltinOutputValidation` now call `model_validate(strict=True)`, so `{"a": "42"}` against `{"a": {"type": "integer"}}` is rejected instead of silently becoming `42` (type-mapping §17.3). apcore-typescript and apcore-rust already rejected it. `SchemaValidator(coerce_types=…)` is unchanged as a library-level knob and does not affect this path — though its **default** flipped to `False`, matching the other two SDKs.

- **BREAKING: recursive schemas can be registered.** `RefResolver` no longer raises `SchemaCircularRefError` for a self-reference — `{"$ref": "#"}`, the root `$id`, or a `#/$defs/…` entry re-entered through `properties` / `items`. The reference is preserved and `generate_model()` binds it at validation time. A `$ref` → `$ref` cycle still raises. Previously **any** self-referencing schema failed to load, despite the specification having a Recursive Schema Support section and a conformance fixture for it — both fixtures drove a path that skipped `RefResolver` entirely.

- **BREAKING: applicator keywords are enforced.** `prefixItems`, `patternProperties`, `propertyNames`, `dependentRequired`, `dependentSchemas`, `unevaluatedItems` and `unevaluatedProperties` were dropped by `generate_model()`; they are now delegated to the jsonschema library as a sub-schema assertion, the same mechanism combinator siblings already used. Applicators on the root schema are enforced too. `if`/`then`/`else` previously raised `SchemaParseError("not yet supported")` — rejecting a valid contract outright — and is now enforced per §10.2.2.

- **BREAKING: `to_strict_schema()` hardens the objects it was skipping.** An object carrying `properties` with no `type` keyword, or `type: ["object", "null"]`, was returned unhardened, so the resulting strict schema was rejected by OpenAI structured outputs. It also now recurses into `prefixItems`.

### Fixed

- **Tautological assertions swept out of `tests/conformance/` (Issue #32, aiperceivable/apcore#81).** Twenty assertions compared a fixture value against a literal transcription of itself — `assert expected["error"]["code"] == "INVALID_PARENT_ID"`, `assert expected["span_created"] is False`, `assert case["expected"]["raised_at"] == "strategy_construction"` and the like. That shape cannot fail on SDK behaviour, and in `test_trace_context.py` it was the *only* place the driver mentioned the error code, which is why the missing `code` above shipped undetected. Every expectation is now bound to an **observed** value (the raised exception's `.code`, the number of probes the circuit breaker actually admitted, the phase that actually raised, the disk artefact that actually exists); the four remaining fixture-vs-literal comparisons are input preconditions explicitly labelled as such. Files touched: `test_trace_context.py`, `test_config_key_governance.py`, `test_middleware_hardening.py`, `test_overrides_store.py`, `test_pipeline_failfast_config.py`, `test_pipeline_step_middleware.py`, `test_usage_exporter.py`.

- **Step middlewares get their `on_step_error` when the `middleware_before` step fails.** The engine narrowed the `on_step_error` audience to `exc.executed_middlewares` for *any* `MiddlewareChainError`. That set is correct only for a `before_step` failure; the `middleware_before` step body re-raises the **module-level** chain error, whose `executed_middlewares` are `Middleware` instances, not `StepMiddleware`s — so every registered step middleware was silently swapped out for objects that have no `on_step_error`, and none was notified. Now that a `before_step` failure is handled on its own path, the step-body path simply notifies all registered step middlewares.

- **Correlation IDs are no longer redacted (MUST-violation, observability.md § Redaction configuration).** *"Implementations MUST NOT redact `trace_id`, `caller_id`, `module_id`, or `span_id`; these correlation fields MUST appear unmodified in every log entry"* — the exemption existed only in `_apply_redaction_config`, a flat non-recursive helper that **neither** mandated surface calls. The spec requires redaction *"both at log emission (in `ContextLogger`) and at the executor's input/output capture point"*, and both of those run recursive engines (`_redact_secrets_recursive` via `ContextLogger._emit`; `_redact_by_keys_and_regex` via `redact_sensitive`) that had no guard at all — so a broad `regex_patterns` or `sensitive_keys` entry scrambled `trace_id` on the value rule *and* on the name rule, breaking trace stitching exactly where it matters. `PROTECTED_LOG_FIELDS` now lives in `apcore.utils.redaction` as the single canonical set, and both engines consult it ahead of the name rule **and** the value regex, at every depth. Matching apcore-rust `NEVER_REDACT_FIELDS` (`redact_inner`): containers under a protected key are still descended into — only the protected field's own scalar value is immune, and array elements, having no key, are never protected. The `correlation_fields_never_redacted` case of `conformance/fixtures/redaction_config.json` was a `strict=True` xfail in both driver classes and now passes for real.

- **`auto_schema: strict` no longer rejects schemas OpenAI accepts.** The previous detector flagged every `anyOf` — including the nullable wrapper Pydantic emits for `X | None`, so **any optional field** failed — and rejected the supported `date-time` / `date` / `time` / `email` / `uuid` formats. It also missed genuinely unsupported keywords (`allOf`, `minLength`/`maxLength`, `patternProperties`, `uniqueItems`) and never traversed `$defs`, leaving nested models unchecked. Replaced by `apcore.schema.openai_strict`, kept separate from `to_strict_schema()` so the OpenAI dialect does not leak into general registry export.

- **Root-level combinators are enforced on every call path.** A schema whose top level is `oneOf` / `anyOf` / `allOf` / `enum` / `const` / `not` produced a field-less `extra="allow"` model that accepted any input; only `SchemaValidator._validate_top_level_union` applied the union rules, and module invocation does not go through it. The assertion now lives on the model itself.

- **`{"type": "integer"}` accepts `4.0`.** JSON Schema §6.1.1 makes any number with a zero fractional part an integer; pydantic's strict mode would have rejected it, which would have turned the coercion fix above into a *new* three-way divergence. `4.5` and `"4"` are still rejected.

- **`uniqueItems` over objects no longer raises `TypeError`.** `len(v) != len(set(v))` escaped the module call as a bare `TypeError` instead of `SCHEMA_VALIDATION_ERROR`; members are now compared by canonical JSON, so key order is correctly irrelevant.

- **A combinator on `items` is no longer dropped**, which had widened every array element to `Any`.

- **`_DictSchemaAdapter.model_validate()` accepts `strict`**, so a module declaring its schema as a raw dict keeps working.

- **BREAKING: §6/§10.3 keywords are enforced at a `type`-less position.** A sub-schema with no `type` widened to `Any` and only the eight applicator keywords were delegated onward, so `required`, `items`, `contains`, `minItems`/`maxItems`, `uniqueItems`, `minProperties`/`maxProperties`, `additionalProperties` and `properties` were asserted by *nothing* — `{"required": ["b"]}`, the usual shape of an `if` / `then` / `dependentSchemas` branch, accepted `{"a": 1}` (type-mapping §17.1 R1). apcore-typescript and apcore-rust both rejected it.

- **BREAKING: `type`-less constraints are inert on other instance types.** `minLength` / `pattern` / `minimum` and friends were collected into a Pydantic `Field` even with no `type` sibling, so `{"minLength": 3}` rejected `[1]` and `{"a": 1}` — instances §17.1 R2 requires it to pass. The `minimum` and `pattern` variants escaped as a bare `TypeError` from pydantic's `apply_known_metadata` fallback, which `BuiltinInputValidation` does not catch, killing the module call with an uncoded error. Both now route through the jsonschema delegation, which is inert by construction.

- **`allOf` with non-object members is registrable.** It raised `SchemaParseError` at model-build time, making a contract apcore-typescript and apcore-rust both accept *and enforce* impossible to register. It now widens to `Any` and the existing sibling assertion enforces every member.

- **Per-module `resources.timeout` is honoured.** `BuiltinExecute` read `module.timeout_ms`, an attribute no apcore-python module ever defines, so the per-module half of the dual-timeout model was dead code and the "negative timeout → `GENERAL_INVALID_INPUT`" rule was unreachable. It now reads `resources.timeout` from the module attribute or `annotations.extra`, matching apcore-typescript and apcore-rust; `0` means "no per-module limit" on both sides of the fallback.

- **`call_with_trace` shares `call()`'s error semantics (D-19).** It passed the raw `PipelineStepError` into recovery, so one step failure surfaced as `PIPELINE_STEP_ERROR` here and as (say) `MODULE_NOT_FOUND` through `call()` — to on_error middleware and to the caller alike. It also fabricated an empty `PipelineTrace` on the middleware-recovery path; `PipelineEngine` now publishes the live trace onto the `PipelineContext` and the real record is returned.

- **BREAKING: filesystem discovery validates module IDs.** `_validate_module_id` was reachable only from `register()` / `register_internal()`, so no ID-grammar or length check stood between a scanned or ID-map-overridden name and the registry — `{'id': 'Foo-Bar'}` or a 200-character ID went straight in. Offenders are now skipped with a warning, as in apcore-typescript and apcore-rust.

- **`register_internal` bypasses only the reserved-word check, as documented.** The `ephemeral.*` rejection raised a bare builtin `ValueError` with no error code (now `InvalidInputError` / `INVALID_MODULE_ID`, matching the other SDKs), and the streaming-annotation ⇔ streaming-interface check ran on `register()` alone.

- **ACL `handler_error` is set on every fail-closed path.** An unknown condition key and an awaitable that suspends in sync context denied with a null diagnostic, so a typo'd key (`role:` for `roles:`) was indistinguishable from a correctly-spelled unmet condition. apcore-typescript records on both.

- **`_approval_token` is stripped unconditionally (§7.4).** It was removed only inside the gated-and-handler-configured branch, so on every early return the protocol key reached input validation — where `additionalProperties: false` rejects it — and the module's `execute()`. The strip also mutated the *caller's* dict; the inputs are rebuilt instead, as in apcore-typescript.

- **BREAKING: numeric config keys reject booleans.** `bool` subclasses `int`, so ten of the fourteen constrained numeric keys read `max_call_depth: true` as a limit of 1. `docs/features/config-bus.md` states booleans are rejected for all numeric fields, and the other two SDKs reject them natively.

- **BREAKING: `ExecutionPolicy.from_dict` rejects non-boolean governance flags.** `gate_destructive: "false"` **enabled** the gate (a non-empty string is truthy) and `gate_destructive: []` disabled it, while apcore-rust's serde-typed `bool` and apcore-typescript's `_requireBoolean` reject either. `gate_destructive` is what turns a `destructive` annotation into an approval gate (§7.9.2), so the coercion was a governance decision made by accident. `null` and an absent key still mean the documented `false`.

- **BREAKING: config required-field validation is no longer dead code.** `_DEFAULTS` was deep-merged into the parsed document and `validate()` then looked for required fields in the *merged* result, so the check could never fail — the merge had already supplied every key. `_DEFAULTS` even carried an invented `version: "0.16.0"` and a `project` subtree that `schemas/defaults.schema.json` has never declared, which existed only to keep that check vacuous. Per PROTOCOL_SPEC §9.1 a key is required only when it has no canonical default, so `_REQUIRED_FIELDS` narrows from six entries to the two that qualify — `version` and `project.name` — and §9.3 step 1 evaluates them against the **declared** document, now exposed as the `Config.declared` property (apcore-rust: `Config::get_declared()`). A config omitting `extensions.root`, `schema.root`, `acl.root` or `acl.default_effect` still loads — it did before too, but for the wrong reason — while one omitting `version` or `project.name` now raises `CONFIG_INVALID` where it previously passed silently. `schemas/apcore-config.schema.json` declares the matching `required: ["version", "project"]`, and apcore-typescript is deleting the identical invented pair from `config-defaults.ts`. Consumers wanting a fallback for an undeclared `project.*` value pass it at the call site (`config.get("project.source_root", "")`), as `sys_modules` already did.

### Changed

- **Conformance drivers read the canonical fixture.** Every driver under `tests/conformance/` now resolves through `conformance.canonical_fixtures` (`$APCORE_FIXTURES` → `$APCORE_SPEC_REPO` → sibling checkout) instead of a vendored copy under `tests/conformance/fixtures/`, so a spec-side edit reaches Python on the next run rather than leaving it on a stale snapshot — the contract apcore-typescript and apcore-rust already honour. `pipeline_hardening.json` and `system_modules_hardening.json` were vendored but read by nothing; their hand-written drivers now carry a `TestFixtureCoverage` guard that fails when the canonical fixture gains a case, as do the `event_management_hardening` and `observability_hardening` drivers. `tests/conformance/fixtures/` has since been **deleted** — every remaining copy was verified byte-identical to its canonical counterpart first — and `test_no_vendored_fixture_drift.py` now asserts the directory stays gone rather than that its contents still match.

- **Conformance drivers moved to the module-invocation path.** The union and recursive hardening fixtures were driven through `validate_schema_dict()` — the raw JSON Schema validator, which is not what a module call goes through. That is what hid the recursive-schema defect. New fixtures `schema_keyword_parity.json` (119 cases), `schema_strict_conversion.json` (16) and `openai_strict_compat.json` (30) have drivers with the same contract.

## [0.26.0] - 2026-07-13

### Added

- **Execution-time governance policy (#76 RFC pilot).** New `ExecutionPolicy`, `PolicyRule`, and `PolicyDecision` types (exported from the `apcore` root) let a platform operator override the governance annotations of already-registered modules at execution time — independent of how they were registered. A policy attaches to the `Executor` via a new `policy=` parameter (also on `Executor.from_registry`) and the runtime `Executor.set_policy()` setter, and is consulted by the approval gate (Step 5). Pattern matching reuses the ACL wildcard semantics (Algorithm A08) and specificity scoring (Algorithm A10); on a specificity tie the more restrictive rule wins. A matched rule overrides the module's own declared/scanned `requires_approval` / `destructive` annotations, and every policy-driven override is recorded in the audit trail (log + tracing span event). `ExecutionPolicy.from_dict` parses a YAML/JSON governance document **strictly** — unknown keys raise `ValueError` so a typo cannot silently disable a control. `Executor.validate()` preflight now reports the same `requires_approval` verdict the gate will enforce under a policy. When the gate is policy-forced, the `ApprovalRequest.annotations` handed to the handler carries the **effective** governance values, preserving the "requires_approval is guaranteed true" contract (PROTOCOL_SPEC §7).

- **Governance events on the event bus (#77 pilot).** When the `Executor` has an `event_emitter`, the governance chain now publishes three canonical events: `apcore.approval.decision` on every approval adjudication (handler decisions and the strict fail-closed rejection; severity `info` for approved/pending, `warn` for rejected/timeout), `apcore.policy.override` whenever a policy changes a module's effective governance, and `apcore.acl.denied` (severity `warn`) when an ACL check denies a call. Payloads carry `module_id`, `trace_id`, and event-specific keys (`status`/`approved_by`/`approval_id`, `pattern`/`requires_approval`/`destructive`, or `caller_id`). Canonical names are proposed in apcore#77, pending the PROTOCOL_SPEC §9.16.2 amendment. A skipped approval gate emits nothing (parity with the no-audit-log-when-skipped contract), and the `apcore.acl.denied` event is suppressed during `validate()` preflight (dry-run) so a probe never emits a spurious denial.

### Removed

- **Legacy dual-emission of unprefixed event names (#78).** The registry bridge and `PlatformNotifyMiddleware` no longer emit the deprecated unprefixed aliases `module_registered` / `module_unregistered` / `error_threshold_exceeded` / `latency_threshold_exceeded` alongside their canonical `apcore.registry.*` / `apcore.health.*` names. PROTOCOL_SPEC §9.16 declared these removed as of v0.22.0 (`MUST` emit only canonical names); the code had kept dual-emitting them for a back-compat window. An ecosystem audit found no remaining subscriber to the bare names, so the aliases are now gone — subscribers must use the canonical `apcore.<subsystem>.<event>` names (a `*` / `apcore.*` glob subscription is unaffected). Aligns Python with the TypeScript SDK, which already emitted canonical-only.

### Changed

- **Resolve `destructive` ↔ approval semantics (#76).** `ExecutionPolicy(gate_destructive=True)` makes any module whose effective `destructive` annotation is true require approval even when `requires_approval` is false — the opt-in resolution of the long-standing footgun where an inferred `DELETE` was `destructive=True` yet ungated. Orthogonality remains the default (no behavior change without a policy).

- **Approval gate fails loud, not silent (#76, security principle).** When a module needs approval but no `ApprovalHandler` is configured, the gate keeps the PROTOCOL_SPEC §7.4 skip behavior but now logs a `warning` (once per module) instead of silently no-opping. `ExecutionPolicy(strict=True)` upgrades this to fail **closed** (raises `ApprovalDeniedError`). A module annotated `destructive=True` that no approval gate covers is likewise warned about once per module. Existing behavior without a policy and with a handler configured is unchanged.

## [0.25.0] - 2026-06-22

### Added

- **Config-driven ACL discovery (#74, D-64).** New `ACL.discover(config)` classmethod resolves `acl.root` (default `./acl`) relative to the config file's directory, loads an ACL only when the path exists, and returns `None` otherwise. An `acl.root` pointing at a directory loads `<root>/global_acl.yaml`; pointing at a file loads that file directly. **Critical invariant:** a missing path attaches **no** ACL — it never synthesizes a default-deny ACL. Discovery is auto-wired in `APCore.__init__`, and is skipped when the caller supplies their own `Executor` so an explicitly configured ACL is never clobbered. New tests and `examples/acl_config_driven.py` cover the behavior; the cross-language contract is locked by the apcore conformance fixture `acl_root_discovery.json`.

- **Registry module-id constants are now part of the public surface (#30).** `MAX_MODULE_ID_LENGTH`, `RESERVED_WORDS`, `REGISTRY_EVENTS`, `EPHEMERAL_NAMESPACE_PREFIX`, `DEFAULT_MODULE_VERSION`, and `MODULE_ID_PATTERN` are re-exported from both the `apcore.registry` package and the `apcore` root (previously reachable only via the internal `apcore.registry.registry` module). Canonical `MAX_MODULE_ID_LENGTH` is now public; the `MAX_MODULE_ID_LEN` alias is retained. Export surface only — no behavior change.

## [0.24.1] - 2026-06-18

### Changed
- rename private _bind_executor to public bind_executor on Context
- add deprecated alias _bind_executor with deprecation warning
- replace all internal calls to _bind_executor with bind_executor
- update docstrings to reflect the new method name

## [0.24.0] - 2026-06-12

### Changed

- **`ToggleState` is now per-`APCore`-instance instead of process-global (#71).** Each `APCore` instance owns one `ToggleState` (`APCore.toggle_state`), injected into BOTH the write path (`ToggleFeatureModule`, via `register_sys_modules(..., toggle_state=...)` → `_register_control_modules`) and the read path (`BuiltinModuleLookup`, via the Executor's strategy). `Executor.__init__` gains a keyword-only `toggle_state` parameter that is threaded into `build_standard_strategy(..., toggle_state=...)` (and the internal/testing/performance/minimal factories that build a `BuiltinModuleLookup`). Disabling a module on one `APCore` no longer disables it on another instance in the same process, and an instance's toggles survive a registry reload of that instance (re-scopes A-D-12 from process-global to instance-scoped). The module-global `_default_toggle_state` is retained as the fallback only for the free `is_module_disabled(module_id)` function (signature unchanged) and for Executors constructed directly without a `toggle_state` (back-compat).

### Added

- **Conformance coverage for per-instance ToggleState isolation and AI-agent ACL governance (#71, #72).** Wired two new cross-language fixtures into `tests/test_conformance.py`: `toggle_state_isolation.json` (4 cases) constructs real `APCore` instances in one process and asserts that toggles written through one instance's `disable`/`enable`/`reload` write path are observed only through that instance's read path; `acl_agent_scoping.json` (19 cases) locks the canonical default-deny agent-tool-governance ruleset, exercising first-match-wins with `{roles, max_call_depth}` conditions and the `@external` special caller (`max_call_depth` is inclusive: `depth == max` is allowed). All 23 cases pass against the existing ACL engine with no engine changes required.

### Fixed

- **ACL `max_call_depth` now accepts an integral float threshold [A-D-004].** A threshold loaded from YAML/JSON as `5.0` (instead of `5`) previously failed the `isinstance(..., int)` check in `_MaxCallDepthHandler`, so the allow-rule silently did not match and the call fell through to default-deny. The handler now accepts a `float` threshold when it is integral (`5.0` → depth 5) — same limit, no security change — matching apcore-typescript. `bool` thresholds and non-integral floats (e.g. `5.5`) are still rejected (fail closed).

- **Registry custom discovery guards against a non-list discoverer result [A-D-014].** `Registry._discover_custom` previously iterated the value returned by a custom discoverer directly; a non-list return (e.g. `None` or a scalar) raised an uncaught `TypeError`. It now checks the result is a `list`, logs a warning, and returns `0`, matching apcore-typescript's `!Array.isArray` guard.

- **README built-in namespaces table corrected [B-011, B-012].** Added the missing pre-registered `obs` / `APCORE_OBS` namespace (redaction.*) and fixed the `sys_modules` key paths to the actual nesting under `events.thresholds.*` (`events.thresholds.error_rate`, `events.thresholds.latency_p99_ms`).


## [0.23.0] - 2026-06-10

### Added

- **AI error-recovery metadata is now populated at the source (#70).** Framework-deterministic errors carry default recovery metadata so the contract flows to every surface (MCP/CLI/A2A) from one definition instead of being backfilled per adapter. A new declarative `_USER_FIXABLE_BY_CODE` policy in `errors.py` resolves `user_fixable` from the error code in `ModuleError.__init__`: `True` for caller-fixable codes (`SCHEMA_VALIDATION_ERROR`, `GENERAL_INVALID_INPUT`, `MODULE_NOT_FOUND`, `VERSION_CONSTRAINT_INVALID`, `BINDING_SCHEMA_INFERENCE_FAILED`, `BINDING_SCHEMA_MODE_CONFLICT`, `BINDING_STRICT_SCHEMA_INCOMPATIBLE`, `DEPENDENCY_NOT_FOUND`, `DEPENDENCY_VERSION_MISMATCH`); `False` for governance/system/structural/transient codes (`ACL_DENIED`, `APPROVAL_DENIED`, `APPROVAL_TIMEOUT`, `MODULE_TIMEOUT`, `MODULE_DISABLED`, `CALL_DEPTH_EXCEEDED`, `CIRCULAR_CALL`, `CALL_FREQUENCY_EXCEEDED`, `GENERAL_INTERNAL_ERROR`). Codes absent from the policy (e.g. `MODULE_EXECUTE_ERROR`) leave `user_fixable` unset for the module author to supply. Missing `ai_guidance` defaults are filled on `InvalidInputError` and `CallFrequencyExceededError`. Explicit constructor values still override the policy; `to_dict()` now emits `user_fixable` for the mapped codes. Locked across SDKs by the new conformance fixture `error_recovery_metadata.json`. `suggestion` is intentionally left unset (redundant with `ai_guidance`); `x-*` metadata remains author-owned.


### Changed (breaking)

- **`CircuitBreakerMiddleware` rewritten to the spec-mandated rolling-window error-rate model [D11-001].** Per `middleware-system.md` §1.2, the breaker now tracks a bounded rolling window of recent outcomes per **`(module_id, caller_id)`** pair (keyed off `context.caller_id`, empty string when absent) and opens when the window error rate meets or exceeds `open_threshold` once at least `min_samples` outcomes are recorded. The old consecutive-failure-count model is gone. **Breaking constructor change**: now keyword-only — `open_threshold` (default `0.5`), `recovery_window_ms` (default `30000`), `window_size` (default `20`), `min_samples` (default `5`), plus `emitter`, `priority` (`100`), and an optional `clock` seam for tests. The previous `failure_threshold` / `success_threshold` parameters are **removed**. `get_state()` and `reset()` now accept an optional `caller_id`. The middleware writes the current state string to `context.data["_apcore.mw.circuit.state"]` on every call and emits `apcore.circuit.opened` / `apcore.circuit.closed` (payload `{module_id, caller_id, error_rate}`) via the injected `EventEmitter` on transitions. `CircuitBreakerOpenError` now also carries `caller_id` (new property; `module_id` and the legacy `CircuitOpenError` alias unchanged). Config keys under `middleware.circuit_breaker` change accordingly: `failure_threshold`/`success_threshold` → `open_threshold` (number in `[0,1]`), `window_size` (int ≥ 1), `min_samples` (int ≥ 1); `recovery_window_ms` default is now `30000`. Matches the Rust and TypeScript SDKs. **Breaking** for callers constructing the middleware with the old positional/threshold parameters — pre-1.0 0.x, acceptable.


### Fixed

- **DLQ `original_event` now nests `module_id`/`timestamp` under `metadata` [D11-002].** The `apcore.event.delivery_failed` payload built by `EventEmitter._emit_dlq` previously emitted `original_event.metadata` as an empty `{}`, dropping the originating module and timestamp. It now carries `metadata: {module_id, timestamp}` from the original `ApCoreEvent` envelope, matching the canonical `{name, payload, metadata:{...}}` shape in `event-system.md`.

- **`A2ASubscriber` no longer retries 4xx responses (#69).** It previously raised on any HTTP `status >= 400`, contradicting the spec (`event-system.md`: 4xx MUST NOT be retried, for both Webhook and A2A) and diverging from `WebhookSubscriber`. `A2ASubscriber.on_event` now mirrors Webhook: 5xx (and connection/timeout) → raise → retried → `apcore.event.delivery_failed` on exhaustion; 4xx → logged permanent, no retry, no DLQ. Per-SDK regression tests lock both subscribers' 4xx/5xx behavior.


## [0.22.0] - 2026-05-28

### Changed

- **`Context.create()` signature unified across all SDKs (apcore PROTOCOL_SPEC §"Contract: Context.create", [apcore#66](https://github.com/aiperceivable/apcore/issues/66)).** The factory now accepts **exactly** six caller-supplied fields in this order: `identity`, `trace_parent`, `cancel_token`, `data`, `services`, `global_deadline`. The previous `executor=` parameter is **removed**; `executor` is now bound by the Executor at pipeline entry via the new SDK-internal `Context._bind_executor(executor)` helper (see PROTOCOL_SPEC §"Contract: Executor binding to Context"). `caller_id` remains managed exclusively by `Context.child()` and was never a public input. Two parameters are newly first-class: `cancel_token` (eliminates the post-hoc `ctx.cancel_token = token` anti-pattern) and `global_deadline` (previously only settable via mutation). `Executor.call` / `call_async` / `stream` / `call_async_with_trace` / `_validate_async` all bind `self` to the supplied or auto-created Context before pipeline step 1; same-instance rebinds are idempotent noops, cross-Executor rebinds raise the new `ContextBindingError` (code `CONTEXT_BINDING_ERROR`). The root-call `global_deadline` computation moved out of "context-was-None" branch into a general "root call" check in `BuiltinContextStep`, ensuring local-config-driven recomputation per PROTOCOL_SPEC §"Contract: `global_deadline` distributed semantics" — including deserialized Contexts arriving at remote nodes. Tests and `examples/cancel_token.py` are updated to the new shape. **Breaking** for callers that passed `executor=` to `Context.create()` — pre-release v0.22.0, acceptable.

- **`TaskStore` Protocol is now fully async (D-17 / A-D-AT-04).** All five methods (`save`, `get`, `delete`, `list`, `list_expired`) are declared `async def` on the Protocol and on `InMemoryTaskStore`. Custom stores written against the pre-0.22.x sync surface must be migrated; `AsyncTaskManager` keeps a transitional compatibility shim that awaits a returned coroutine if a legacy store still exposes sync methods. The uniform async shape unblocks Redis/SQL/network-backed stores without an extra blocking adapter, matching the TypeScript and Rust SDKs.

- **`ReaperHandle.stop()` and `AsyncTaskManager.stop_reaper()` are now `async` and drain the reaper task (D-11 / A-D-AT-03).** Callers must `await handle.stop()` / `await manager.stop_reaper()`. After the coroutine returns the underlying `asyncio.Task` is guaranteed to be settled — previously the sync `stop()` only requested cancellation and required a manual `await asyncio.sleep(0)` for the task to finish. `AsyncTaskManager.shutdown()` now awaits `stop_reaper()` directly.

- **`AsyncTaskManager.get_status()` and `list_tasks()` return defensive snapshots (A-D-AT-06).** Both methods now hand back shallow copies of `TaskInfo` via `dataclasses.replace`, matching the TypeScript SDK's `{ ...info }` and the Rust SDK's `info.clone()`. Mutating the returned objects no longer corrupts the live store. Async-friendly twins `get_status_async()` / `list_tasks_async()` are available for I/O-backed stores.

- **`AsyncTaskManager.cleanup()` is now `async`.** Required because the store contract is async; callers must `await manager.cleanup(...)`. The reaper background loop already awaits internally — only direct in-process callers are affected.

- **Legacy `RetryPolicy` defaults `max_retries` to `0` and emits `DeprecationWarning` on instantiation (D-14 / A-D-AT-09).** Earlier builds silently enabled three retries when callers used `RetryPolicy()` without arguments, contradicting the opt-in retry contract. The class is retained for one release; new code should use `RetryConfig` (canonical, ms-based, no deprecation noise).

- **BREAKING: `Config.get()` no longer falls back to the implicit `apcore` namespace for a scalar segment + remainder (A-D-049).** In namespace mode, when a top-level segment resolves to a non-dict scalar and a deeper sub-path is requested (e.g. data `{foo: 5, apcore: {foo: {bar: 9}}}`, `get("foo.bar")`), Python previously recursed into `apcore.foo.bar` and returned `9`. Spec §9.9.1 does not define this fallback and the Rust SDK is spec-correct, so `get()` now returns the default (`None`). **Breaking** for any caller that relied on the implicit-apcore scalar fallback — pre-1.0 0.x, acceptable.

- **BREAKING: `Config.set()` is now namespace-aware, symmetric with `get()` (A-D-050).** `set()` previously wrote via a plain dot-path while `get()` resolved the registered-namespace longest-prefix. For a registered namespace whose name contains dots (e.g. `my.app`), `set("my.app.transport", v)` wrote to `data["my"]["app"]["transport"]` while `get("my.app.transport")` read `data["my.app"]["transport"]` — a silent round-trip failure. `set()` now resolves the registered-namespace prefix before writing, so `set`/`get` round-trip to the same location. **Breaking** only for callers that depended on the previous asymmetric naive-split write — pre-1.0 0.x, acceptable.

- **Middleware circuit-breaker state enum renamed `CircuitState` → `CircuitBreakerState` (audit D2-001).** Adopts the cross-SDK canonical name for naming parity (matches `apcore-rust`; the TypeScript SDK uses `MiddlewareCircuitState`) and disambiguates from the unrelated events circuit-breaker `CircuitState` (`apcore.events`), which is unchanged. The top-level export `from apcore import CircuitBreakerState` and `apcore.middleware.CircuitBreakerState` now resolve to the renamed enum; members (`CLOSED`, `OPEN`, `HALF_OPEN`) are unchanged. No deprecation alias is provided. **Breaking** for direct importers of the middleware `CircuitState` — pre-1.0 0.x, acceptable.

### Fixed

- **Cancel token observed at Step 2 of the execution pipeline (D-21 / A-D-EXEC-002).** `BuiltinCallChainGuard` now checks `context.cancel_token.is_cancelled` before running any guard work; a cancelled token short-circuits with `ExecutionCancelledError` before ACL, middleware, validation, or module execution. Combined with the existing Step 8 check, the pipeline now satisfies the two-point cancel-token invariant — single-check implementations were leaking compute through ACL/middleware/validation even when the caller had already cancelled.

- **`MiddlewareChainError` unwrap rule (D-22 / A-D-EXEC-005).** `Executor._recover_from_call_error` now unwraps `MiddlewareChainError` and propagates the original typed cause (e.g. `ApprovalDeniedError`, `ACLDeniedError`) unchanged. Previously the wrapper was collapsed to a generic `ModuleExecuteError`, breaking callers that dispatch on the typed error (notably MCP/A2A bridges keying on `APPROVAL_DENIED` vs `MODULE_EXECUTE_ERROR`). Mirrors the TypeScript and Rust SDK semantics.

- **`Registry.register_internal` ephemeral-namespace rejection covers bare `"ephemeral"` (A-D-REG-002).** Previously the check used `module_id.startswith("ephemeral.")`, which missed the bare ID `"ephemeral"` and contradicted the canonical `_is_ephemeral` classifier used everywhere else in the registry. The helper is now shared between both call sites.

- **Discover-path registration enforces the Issue #65 deferred-publish invariant (A-D-REG-003).** `_register_in_order` now reserves an in-flight slot, runs `on_load()` outside the lock, and only publishes into `_modules` / `_versioned_modules` on success. Previously the discover path published *before* invoking `on_load` and relied on rollback, leaving a window in which `registry.get()` callers could observe a module whose `on_load`-installed state (warmed pools, primed caches) was incomplete.

- **`Registry.register_internal` enforces the Issue #65 deferred-publish invariant (A-D-REG-004).** `register_internal` now routes through the same three-phase protocol as `register()`: reserve in-flight slot → run `on_load` outside the lock → publish. On failure it removes the in-flight slot, emits `apcore.registry.module_load_failed`, and re-raises the original exception unchanged. The invariant now holds uniformly for every registration path (public `register`, `register_internal`, and discover).

- **Discover-path emits `apcore.registry.module_load_failed` on `on_load` failure (A-D-REG-005).** Earlier the discover path logged at ERROR and silently dropped the module; subscribers had no portable hook to detect partial-init failures from the auto-discovery pipeline. The event payload matches the one emitted by `register()` (Issue #65) so a single subscriber covers all registration paths.
- **`InMemoryTaskStore.list_expired` no longer reaps terminal tasks with a null `completed_at` (A-D-004).** The method previously fell back to `submitted_at` when `completed_at` was `None`, so a terminal task lacking a completion timestamp could be returned (and reaped). The `TaskStore.list_expired` contract — and the TypeScript and Rust SDKs — require `completed_at` to be non-null; the fallback is removed. `AsyncTaskManager.cleanup()` retains its own distinct, contract-specified `submitted_at` reference selection and is unaffected. Found via `/apcore-skills:sync --scope core`.

- **ACL `max_call_depth` rejects boolean thresholds — fail-closed security fix (A-D-D-012).** Because Python `bool` is a subclass of `int`, an ACL condition like `max_call_depth: true` previously coerced to threshold `1` and could ALLOW a shallow call (call-chain length ≤ 1) where the TypeScript and Rust SDKs fail closed. `_MaxCallDepthHandler` now rejects `bool` explicitly (including inside the `{lte: ...}` form) and does not match — closing an allow/deny divergence.

- **Cancellation short-circuits before `on_error` middleware recovery (D-20 / A-D-003, A-D-004).** When a pipeline step (module or middleware) raises `ExecutionCancelledError`, the engine wraps it in `PipelineStepError`; `Executor.call_async` and `Executor.stream` previously routed that into `_recover_from_call_error`, so a recovering `on_error` handler could swallow the cancellation. Both paths now re-check the unwrapped cause (also unwrapping `MiddlewareChainError`) and re-raise `ExecutionCancelledError` immediately, skipping the recovery chain — matching the Rust SDK.

- **`Executor.validate()` is non-throwing on a foreign-bound Context (A-D-008).** When the supplied Context was already bound to a *different* Executor, the unguarded `_bind_executor` call let `ContextBindingError` escape `validate()`, which must always return a `PreflightResult`. The bind is now wrapped; a binding conflict yields `PreflightResult(valid=False)` with a failed `executor_binding` check instead of raising. Mirrors the Rust SDK.

- **`SchemaValidator.validate` populates the canonical `error_code` (A-D-036 / A-D-034).** The public Pydantic validation path returned a `SchemaValidationResult` with `error_code=None` on failure; it now sets `error_code = "SCHEMA_VALIDATION_ERROR"` (spec §8.2) on plain validation failure, aligning with `SchemaValidationError.code` and cross-SDK expectations.

- **`$ref` depth-cap exhaustion now raises `SchemaMaxDepthExceededError` (`SCHEMA_MAX_DEPTH_EXCEEDED`) instead of `SchemaCircularRefError` (A-D-038).** `RefResolver.resolve_ref` previously emitted `SchemaCircularRefError` (`SCHEMA_CIRCULAR_REF`) for both genuine cycles **and** depth-cap exhaustion (`depth >= max_depth`). The two are now distinct: the depth-cap branch raises the new `SchemaMaxDepthExceededError` while genuine cycle detection still raises `SchemaCircularRefError`, matching the Rust SDK and spec §8.2 (`apcore/docs/features/error-system.md`). The new error class is **additive (non-breaking)** and exported from the top-level `apcore` package, but this is a **code CHANGE for the depth-cap path**: callers that caught `SchemaCircularRefError` / `SCHEMA_CIRCULAR_REF` to handle depth-cap exhaustion must now also handle `SchemaMaxDepthExceededError` / `SCHEMA_MAX_DEPTH_EXCEEDED`.

### Removed

- **Misplaced spec-style docs (B-005).** Deleted `docs/features/async-task-evolution.md`, `docs/features/middleware-architecture-hardening.md`, and `docs/async-task-evolution/test-cases.md`. Per the apcore protocol-spec repo policy (`apcore/CLAUDE.md`), implementation repos contain only code and a README — feature specs, test-case matrices, and design notes live in the apcore spec repo. The deleted files were also stale (referenced the obsolete `TaskStore.put` method and the removed `TaskStatus.RETRYING` enum value); the canonical authority is the implementation plus the upstream `apcore/docs/features/async-tasks.md` spec.

### Added

- **`Config.reserved_namespaces()` classmethod + top-level `RESERVED_NAMESPACES` constant (PROTOCOL_SPEC §9.9.5, [apcore#60](https://github.com/aiperceivable/apcore/issues/60)).** Implements the new normative requirement that all SDKs MUST expose a public, read-only query API returning the set of reserved top-level namespace names. Returns the existing private `_RESERVED_NAMESPACES` `frozenset` — single source of truth, no parallel list — so `Config.register_namespace("apcore")` continues to raise `ConfigNamespaceReservedError` and the query API reports exactly the names that drive that enforcement. Class-level access (no `Config()` instance needed). Intended for third-party consumers (custom CLIs, framework integrations like `django-apcore`, application code) that accept user-supplied namespace names and need fail-fast pre-validation. The private constant `_RESERVED_NAMESPACES` is unchanged — internal callers keep using it.

- **Unified event delivery semantics: per-subscriber retry, DLQ, and `on_failure` callback ([apcore#61](https://github.com/aiperceivable/apcore/issues/61)).** `EventEmitter._deliver` now runs each subscriber's delivery in an independent async task via `asyncio.gather`, with an exponential-backoff retry loop controlled by the new `EventRetryConfig` frozen dataclass (`max_attempts`, `initial_backoff_ms`, `max_backoff_ms`, `backoff_multiplier`). On retry exhaustion, emits an `apcore.event.delivery_failed` dead-letter event (DLQ) to all non-wildcard subscribers; calls the optional `on_failure(event, error, attempt_count)` hook. DLQ delivery is single-attempt: a DLQ subscriber that fails is logged at ERROR and discarded — no second-order DLQ. Subscribers gain `subscriber_id` (explicit or auto-generated `{type}-{n}`), `retry: EventRetryConfig`, `event_pattern: str`, and `subscriber_type` fields. `EventRetryConfig` is exported from `apcore.events.retry` and the top-level `apcore` package.

- **`StreamingModule` Protocol with registration-time signature validation ([apcore#62](https://github.com/aiperceivable/apcore/issues/62)).** New `@runtime_checkable` `StreamingModule` Protocol in `apcore.streaming` declares `async def stream(self, inputs: dict, context: Context) -> AsyncIterator[dict]`. `Registry.register` validates the signature (arity, async, method presence) when `annotations.streaming = True`, raising `StreamingInterfaceError` (code `STREAMING_INTERFACE_MISMATCH`) on mismatch with `mismatch_reason` literals `missing_marker | not_async | wrong_arity`. `builtin_steps.BuiltinExecute` now uses `isinstance(module, StreamingModule)` instead of `hasattr`. Both `StreamingModule` and `StreamingInterfaceError` are exported from the top-level `apcore` package.

- **`ContextKey[T]` promoted as documented public API ([apcore#63](https://github.com/aiperceivable/apcore/issues/63)).** `ContextKey`, all 6 built-in constants (`TRACING_SPANS`, `TRACING_SAMPLED`, `METRICS_STARTS`, `LOGGING_START`, `REDACTED_OUTPUT`, `RETRY_COUNT_BASE`), and their `_apcore.*` identifier strings are confirmed exported at the top-level `apcore` package. `ContextKey.scoped(suffix)` creates `{name}.{suffix}` sub-keys for vendor namespacing.

- **Duplicate middleware detection in `MiddlewareManager.use()` ([apcore#64](https://github.com/aiperceivable/apcore/issues/64)).** New `MiddlewareManager.use(middleware, *, allow_duplicate=False, identity_key=None)` method. Identity defaults to `{module}.{qualname}`; overridable via `identity_key`. On duplicate detection (same identity, `allow_duplicate=False`), emits a `WARNING` log naming both call sites. Registration always proceeds; order is preserved. Keys starting with `apcore.` are reserved for framework middleware.

- **`Registry` on_load ordering — deferred-publish invariant ([apcore#65](https://github.com/aiperceivable/apcore/issues/65)).** `Registry.register` now uses a three-phase deferred-publish protocol: (1) validate + mark `module_id` in `_in_flight` set; (2) call `on_load()` outside the lock — module is NOT visible during this phase; (3) atomically insert into visible store. `get()`, `list()`, and all discovery APIs see the module only after `on_load` completes. On `on_load` failure: module is never added to the visible store, `register()` re-raises the original exception, and an `apcore.registry.module_load_failed` event is emitted (payload: `module_id`, `callback_name`, `error_type`, `error_message`, `timestamp`). Concurrent same-ID registrations during `on_load` raise `InvalidInputError(code=DUPLICATE_MODULE_ID)`. Distinct-ID `on_load` callbacks run in parallel (no global lock held during `on_load`).

### Changed

- **`A2ASubscriber` follows unified retry policy ([apcore#61](https://github.com/aiperceivable/apcore/issues/61)).** Removed A2A's previous "log-and-suppress on failure" behavior. `A2ASubscriber.on_event` now raises on HTTP/network errors so the `EventEmitter` retry loop handles backoff and DLQ emission. Adds `skill_id` constructor parameter (default `"apevo.event_receiver"`) replacing the hardcoded constant.

- **`Registry.register` concurrent same-ID rejection via in-flight set ([apcore#65](https://github.com/aiperceivable/apcore/issues/65)).** A second `register()` call for a module_id that is already executing `on_load()` now raises `InvalidInputError(code=DUPLICATE_MODULE_ID)` immediately, preventing concurrent duplicate registrations and making the registry's visible-store consistent with the deferred-publish invariant.

---

## [0.21.0] - 2026-05-06

### Added

- **`Module.preview()` + `PreflightResult.predicted_changes` (PROTOCOL_SPEC v0.21.0 §5.6 / §12.8 — promoted from RFC `apcore/docs/spec/rfc-preview-method.md`, apcore commit [`c191b85`](https://github.com/aiperceivable/apcore))** — New optional `preview(inputs, context)` method on the `Module` Protocol. Modules implementing `preview()` return a `PreviewResult` (or `None` when prediction is unavailable) whose `changes` list is a structured prediction of the state changes the call would produce — answering the AI-orchestrator-driven question *"if I were to call this module with these inputs, what would change in the world?"*. Detection mirrors the existing `preflight()` optional-method pattern (`hasattr(module, "preview") and callable(module.preview)`). `Executor.validate()` invokes the method (awaiting the result if it's a coroutine — both sync and async implementations are supported), folds `PreviewResult.changes` into the new `PreflightResult.predicted_changes` field, and records a `module_preview` advisory check on `PreflightResult.checks`. Exception semantics match `preflight()`: a raised exception is surfaced as a warning on the `module_preview` check and **does not** fail validation. New `Change` and `PreviewResult` pydantic models exported from the top-level `apcore` package. `Change` uses the Python idiomatic encoding called out in the RFC's "Change.x-* extension fields" cross-SDK schema-encoding table — `pydantic.ConfigDict(extra='allow')` paired with a model-validator that rejects extra keys not matching `^x-`, mirroring the `^x-` extension convention used elsewhere in the protocol (§4.6). Reference parity: `apcore-typescript` PR #29.

- **`ephemeral.*` namespace + `discoverable` annotation pilot (apcore RFC `docs/spec/rfc-ephemeral-modules.md`, [#25](https://github.com/aiperceivable/apcore-python/issues/25))** — Pilot implementation **ahead of upstream RFC acceptance** (RFC is in `Draft / RFC` state). Reserves the `ephemeral.*` namespace for programmatically-registered modules synthesized at runtime by LLM-agent pipelines (e.g. ToolMaker, ACL 2025, arXiv 2502.11705). Filesystem discovery now refuses to register any ID that falls under `ephemeral.*` and raises `InvalidInputError` with code `INVALID_MODULE_ID`; the namespace is reachable only via `Registry.register()`. New `discoverable: bool = True` field on `ModuleAnnotations` — when `False`, the module is excluded from `Registry.list()` (default behaviour; `include_hidden=True` returns the full set), `Registry.iter()`, `Registry.module_ids`, and downstream manifest export, while remaining callable via `Registry.get()` / `Executor.execute()`. New `Registry.set_event_emitter(emitter)` opt-in: when wired, `ephemeral.*` registrations / unregistrations emit canonical `apcore.registry.module_registered` / `apcore.registry.module_unregistered` events whose `data` payload mirrors the D-35 contextual-audit shape (`caller_id` defaulting to `"@external"`, plus a redacted `identity` snapshot when `context.identity` is set). Without an emitter the same audit information is logged at INFO so it never silently disappears. `Registry.register()` / `Registry.unregister()` accept an optional `context=` keyword used solely to enrich those audit payloads. A soft `logging.warning(...)` fires when an `ephemeral.*` module is registered without `requires_approval=True` (per the RFC). Lifecycle is caller-managed via `Registry.unregister()`; TTL/GC sweeper and host-side sandboxing are deliberately out of scope for the v1 pilot. New top-level constant `EPHEMERAL_NAMESPACE_PREFIX` exported from `apcore.registry.registry`. **Pilot disclaimer:** the upstream RFC is not yet accepted; downstream SDKs (`apcore-typescript`, `apcore-rust`) will follow once Python pilot findings are reported back.

### Changed

- **iter-11 alignment with upstream apcore RFC `rfc-ephemeral-modules.md` (apcore commit [`81df336`](https://github.com/aiperceivable/apcore/commit/81df336))** — Tightens the `ephemeral.*` pilot against two new normative rules added during the RFC iter-11 reconciliation round:
  1. **Audit-event single-emit rule** (RFC §"Audit-event single-emit rule"). The legacy `_bridge_registry_events` callback in `apcore.sys_modules.registration` now short-circuits for `ephemeral.*` module IDs so that exactly **one** `apcore.registry.module_registered` / `apcore.registry.module_unregistered` event is emitted per registration — the rich registry-side direct emit carrying the full D-35 contextual payload — instead of being followed by a second empty-payload copy from the bridge. Non-ephemeral registrations are unaffected (legacy bridge behaviour preserved for backwards compatibility).
  2. **`register_internal()` rejection** (RFC §"`register_internal()` interaction"). `Registry.register_internal()` now raises `ValueError` when called with an `ephemeral.*` module ID, directing the caller to `Registry.register()`. Rationale: namespace → registration-mechanism is a 1:1 mapping; mixing the two paths blurs the audit-trail distinction between framework-emitted (`system.*`) and caller-emitted (`ephemeral.*`) modules.

## [0.20.0] - 2026-05-05

### Added

- **Pluggable `OverridesStore` interface (sync alignment, CRITICAL #1)** — New `apcore.sys_modules.overrides` module exposes the `OverridesStore` Protocol with default `InMemoryOverridesStore` and `FileOverridesStore` (atomic YAML write via tempfile + `os.replace`) implementations, mirroring TypeScript's `apcore-typescript/src/sys-modules/overrides.ts` (`OverridesStore` / `InMemoryOverridesStore` / `FileOverridesStore`). `register_sys_modules(..., overrides_store=...)` accepts any `OverridesStore`; loaded overrides are applied to `Config` (and the live `ToggleState` for `toggle.*` keys) at startup, and `UpdateConfigModule` / `ToggleFeatureModule` persist back through the store on every successful mutation. The legacy `sys_modules.control.overrides_path` config key is retained as a backwards-compat shim that auto-constructs a `FileOverridesStore`. The previously-private `_load_overrides` helper now delegates to `FileOverridesStore` for symmetry. New top-level exports: `OverridesStore`, `InMemoryOverridesStore`, `FileOverridesStore`.

- **Public `SubscriberFactory` API (Issue #36)** — `apcore.events.register_subscriber_factory(type_name, factory)` and `apcore.events.create_subscriber_from_config(config)` (also re-exported from `apcore`) bring Python to parity with TypeScript's `createSubscriberFromConfig` / `registerSubscriberFactory` and Rust's `create_subscriber` / `register_factory`. Built-in factory types (`webhook`, `a2a`, `file`, `stdout`, `filter`) are auto-registered on import. The previously-private `_create_subscriber` helper remains for back-compat.
- **Pipeline `StepMiddleware` (Issue #33 §2.2)** — Formal middleware mechanism for pipeline steps. New `StepMiddleware` Protocol exposes optional `before_step(step_name, state)`, `after_step(step_name, state, result)`, and `on_step_error(step_name, state, error)` hooks; both sync and async implementations are supported (return values detected via `inspect.isawaitable()`, mirroring the Issue #42 async `on_error` fix). `ExecutionStrategy` gains a `step_middlewares: list[StepMiddleware]` field plus an `add_step_middleware()` registration method. `before_step` and `on_step_error` run in registration order; `after_step` runs in reverse (onion semantics). A non-`None` return from `on_step_error` is treated as a recovery `StepResult` and execution continues normally; returning `None` lets the original exception propagate. Exported from `apcore` as `StepMiddleware`.
- **`apcore.observability.batch_span_processor` module (Issue #43 §2)** — Dedicated module hosting the canonical `BatchSpanProcessor` and `SimpleSpanProcessor` implementations, mirroring the layout of the TypeScript (`src/observability/batch-span-processor.ts`) and Rust (`src/observability/processor.rs`) SDKs. Adds a synchronous `force_flush(timeout_ms=30000) -> bool` method that drains the queue while the processor remains alive (returns `True` once empty, `False` on deadline). `shutdown()` is now idempotent. Queue-full enqueues now log a (rate-limited) `WARNING` so dropped spans surface in operator logs rather than only via the `spans_dropped` counter. The classes remain re-exported from `apcore.observability.tracing` and `apcore.observability` for backward compatibility.
- **Generic pluggable `StorageBackend`** (PROTOCOL_SPEC Issue #43 §1) — New `apcore.observability.storage` module defines a namespaced `save / get / list / delete` Protocol and the default `InMemoryStorageBackend` implementation, mirroring the shape of `TaskStore`. `ErrorHistory`, `MetricsCollector`, and `UsageCollector` now accept an optional `storage: StorageBackend | None = None` constructor argument; when supplied, error entries are mirrored to the backend's `"errors"` namespace keyed by fingerprint, enabling cross-process persistence. External backends (Redis, SQL, S3) remain user-supplied. Exports: `StorageBackend`, `InMemoryStorageBackend` from `apcore.observability` and the top-level `apcore` package.
- **`TaskStore.list_expired(before_timestamp)`** (cross-language alignment D-10) — New method on the `TaskStore` Protocol returning terminal-state (`COMPLETED` / `FAILED` / `CANCELLED`) tasks whose `completed_at` precedes `before_timestamp`. Implemented on `InMemoryTaskStore`. Drives TTL-based reaper logic; non-terminal tasks are never returned. The method is REQUIRED on the Protocol — custom stores written before this release must add an implementation.
- **`Registry.discover_multi_class(file_path, extensions_root="extensions")`** (cross-language alignment D-15) — New instance method on `Registry` wrapping the existing free function `apcore.registry.multi_class.discover_multi_class`. The registry's configured `pre_approval_hook` is forwarded to the underlying scanner so signature-verification and audit policies apply uniformly. The free function remains importable for existing callers; new code SHOULD prefer the method.
- Granular reload via `path_filter` input in `ReloadModule` (#45.4) — `Registry.discover(path_filter=...)` accepts a glob string or list of patterns and only walks matching files; previously-registered modules outside the filter remain untouched. Patterns are matched (via `pathlib.PurePath.match`) against both the absolute file path and its path relative to each configured extension root.
- Error fingerprinting in `ErrorHistory` — dedup by (error_code, top-frame hash, sanitized message template) (#43 §4). New `compute_error_fingerprint(error, module_id)` folds the deepest stack-frame `file:lineno:func` (basename only, for cross-machine stability) into the SHA-256 digest in addition to the existing code/module/normalized-message inputs. Long hex runs (≥ 8 chars) are now collapsed to `<HEX>` alongside the existing UUID/timestamp/integer placeholders. Legacy 3-arg `compute_fingerprint` retained.
- Configurable redaction via `obs.redaction.regex_patterns` and `obs.redaction.sensitive_keys` Config keys (#43 §5). New `obs` namespace ships with sensible defaults (`password`, `secret`, `token`, `api_key`, `authorization`, `cookie`, `_secret_*`, …); operators can override via `apcore.yaml`. `RedactionConfig.from_config(config)` / `RedactionConfig.default()` build the runtime config; `_secret_` prefix matching becomes a default entry rather than a hard-coded rule. Field-name match is case-insensitive substring with `-`/`_`/space normalization (so `"X-API-Key"` matches `"api_key"`); value-regex match is case-insensitive. `apcore.utils.redaction.redact_sensitive` accepts new keyword overrides (`sensitive_keys`, `regex_patterns`, `replacement`).

### Changed

- **Event names normalized to `apcore.<subsystem>.<event>` form (#36)** — Four legacy event types (`module_registered`, `module_unregistered`, `error_threshold_exceeded`, `latency_threshold_exceeded`) now also emit canonical aliases `apcore.registry.module_registered`, `apcore.registry.module_unregistered`, `apcore.health.error_threshold_exceeded`, `apcore.health.latency_threshold_exceeded`. Both forms are emitted during the deprecation window so existing subscribers keep working; the legacy emission carries `deprecated: true` in `data`. Glob subscribers using `apcore.registry.*` and `apcore.health.*` now match correctly. **Deprecation:** legacy bare names will be removed in v0.22.0.
- **Contextual auditing for system control modules (Issue #45.2)** — Audit events emitted by `system.control.update_config` (`apcore.config.updated`), `system.control.toggle_feature` (`apcore.module.toggled`), and `system.control.reload_module` (`apcore.module.reloaded`) now include the requester's `caller_id` from `context.caller_id` (defaults to the `@external` sentinel when unset) and a redacted `identity` dict (`id`, `type`, `roles`) when `context.identity` is present.
- **Pipeline configuration is fail-fast (Issue #33 §1.2)** — `build_strategy_from_config` now raises `ConfigurationError` (new typed error, code `PIPELINE_CONFIGURATION_ERROR`) instead of logging a warning when YAML refers to a step that does not exist (in `remove`, `configure`, or `insert.before`/`insert.after`), assigns an unknown field via `configure`, or omits both `after` and `before` anchors on an inserted step. Misconfigurations now surface at start-up rather than producing inscrutable runtime failures.
- **Pipeline strategy dependency validation is fail-fast (Issue #33 §2.1)** — `ExecutionStrategy.__init__` and `insert_after`/`insert_before` now raise `PipelineDependencyError` (new typed error, code `PIPELINE_DEPENDENCY_ERROR`) when a step's `requires` keys are not provided by any preceding step's `provides`. The error names the offending step and the missing keys. A new `validate_dependencies: bool = True` keyword on `ExecutionStrategy.__init__` lets internal callers (e.g. `Executor.stream`'s post-stream sub-strategy) opt out when assembling derived strategies from an already-validated parent. Both new errors are exported from `apcore`.
- **Cross-language alignment (sync A-001)** — Renamed `CircuitOpenError` (code `CIRCUIT_OPEN`) to canonical `CircuitBreakerOpenError` (code `CIRCUIT_BREAKER_OPEN`) to match TypeScript and Rust SDKs and the protocol spec. The legacy `CircuitOpenError` class is retained as a deprecated subclass alias of `CircuitBreakerOpenError` so existing `except CircuitOpenError:` blocks raising the legacy class continue to work; the legacy class will be removed in a future major release. The wire error code emitted by `CircuitBreakerMiddleware` is now `CIRCUIT_BREAKER_OPEN` for both classes. New `ErrorCodes.CIRCUIT_BREAKER_OPEN` constant added; `ErrorCodes.CIRCUIT_OPEN` retained as a deprecated alias. `CircuitBreakerOpenError` is exported from the top-level `apcore` package.
- **`TaskStore.put` → `save`** (cross-language alignment D-10) — Renamed the canonical write method on the `TaskStore` Protocol. `InMemoryTaskStore.put` is retained as a deprecated shim that delegates to `save` and emits a `DeprecationWarning`; it will be removed in a future minor release. Internal `AsyncTaskManager` calls now route through a `_save` helper that prefers `save` and falls back to `put` for legacy custom stores.
- **`TaskStatus.RETRYING` removed** (cross-language alignment D-12) — During retry backoff, the task status is now `TaskStatus.PENDING` to match the TypeScript and Rust SDKs and the protocol spec. `TaskStatus.RETRYING` remains accessible for one minor release as a deprecated attribute that resolves to `TaskStatus.PENDING` and emits a `DeprecationWarning` on access. The `"retrying"` enum value is no longer present in `TaskStatus.__members__`.
- **`TaskInfo.attempt_number` → `retry_count`** (cross-language alignment D-13) — Renamed the dataclass field. `attempt_number` is retained as a deprecated property (with both getter and setter) that reads/writes `retry_count` and emits a `DeprecationWarning`. It will be removed in a future minor release.
- **`ErrorHistory` eviction is min-heap-based** (PROTOCOL_SPEC Issue #43 §3) — Confirmed the in-place O(log N) min-heap eviction keyed on `last_occurred` with lazy deletion of stale entries from dedup-driven timestamp refreshes. Replaces the prior O(excess × M) linear scan for the global-oldest entry; per-insert eviction cost is bounded regardless of the number of tracked modules.
- `AsyncTaskManager.start_reaper` aligned with TS / Rust D-11 surface — accepts `ttl_seconds` (seconds) and `sweep_interval_ms` (milliseconds) keyword arguments and returns a new `ReaperHandle` (with `stop()` / `is_running()`). The legacy `interval_seconds` / `max_age_seconds` arguments still work but emit `DeprecationWarning`; passing both legacy and new aliases for the same value raises `TypeError`. `ReaperHandle` is exported from `apcore.async_task`.
- **`AsyncTaskManager.start_reaper` default `sweep_interval_ms` aligned to 300_000 (sync alignment, WARNING #5)** — Default sweep cadence changed from 3_600_000 ms (1 hour) to **300_000 ms (5 minutes)**, matching TypeScript and Rust. Callers that relied on the 1-hour default must now pass `sweep_interval_ms=3_600_000` explicitly.

### Fixed

- Async `on_error` middleware now detects awaitable **return values** via `inspect.isawaitable(...)` rather than `inspect.iscoroutinefunction(mw.on_error)` (#42). The previous gate missed `functools.partial` wrappers and decorator-wrapped async handlers (no `__wrapped__`), causing the recovery coroutine to be silently dropped — `isinstance(recovery, dict)` then evaluated against an un-awaited coroutine and the chain aborted. The same fix applies to `execute_before` and `execute_after`. Truly synchronous handlers continue to run through `asyncio.to_thread` so blocking calls (`time.sleep` in `RetryMiddleware`) do not stall the event loop.

### Added — PROTOCOL_SPEC hardening (Issues #32–#45)

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