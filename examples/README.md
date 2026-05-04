# apcore-python — Examples

Runnable demos for the Python SDK. Each top-level file is a standalone script — no setup beyond `pip install -e .` (or `pip install apcore`) at the repo root.

## Quick start

```bash
# From the apcore-python repo root
python examples/simple_client.py
```

## All examples

| File | What it demonstrates | Run |
|---|---|---|
| [`simple_client.py`](simple_client.py) | Minimal `APCore()` client with `@client.module` decorator and a sync `client.call()`. | `python examples/simple_client.py` |
| [`global_client.py`](global_client.py) | Module-level `apcore.module` / `apcore.call` — no explicit client. | `python examples/global_client.py` |
| [`cancel_token.py`](cancel_token.py) | Cooperative cancellation: cancel a long-running module via `CancelToken` mid-flight. | `python examples/cancel_token.py` |
| [`pipeline_demo.py`](pipeline_demo.py) | The 11-step `ExecutionStrategy` pipeline — introspection, step-middleware tracing, and orchestration via `insert_after` / `replace`. See note below. | `python examples/pipeline_demo.py` |
| [`bindings/format_date/run.py`](bindings/format_date/run.py) | Loading a YAML binding (`format_date.binding.yaml` → `format_date.py`) via `BindingLoader` and calling it through `Executor`. | `python examples/bindings/format_date/run.py` |

### Module reference files

The files under [`modules/`](modules/) are reusable module definitions imported by the examples above. They are not meant to be run standalone.

| File | Pattern shown |
|---|---|
| [`modules/greet.py`](modules/greet.py) | Minimal duck-typed module (`input_schema` + `output_schema` + `execute`). |
| [`modules/decorated_add.py`](modules/decorated_add.py) | The `@module` decorator from `apcore.decorator`. |
| [`modules/get_user.py`](modules/get_user.py) | Read-only module annotation. |
| [`modules/send_email.py`](modules/send_email.py) | Full-featured module: `ModuleAnnotations`, `ModuleExample`, `x-sensitive` redaction, `ContextLogger`. |

## Pipeline demo — what to look for

`pipeline_demo.py` is the deep-dive into the engine. One run prints three sections:

1. **Introspection** — the canonical 11 step names from `strategy.step_names()` / `strategy.info()`.
2. **Middleware tracing** — a `StepMiddleware` that narrates every step of one call:
   ```
   [ 1/11] context_creation    — create execution context, set global deadline
           ✓   0.07 ms · caller=anonymous trace_id=…
   ...
   [11/11] return_result       — finalize and return output
           ✓   0.01 ms · returning {…}
   ```
3. **Orchestration** — `strategy.insert_after("output_validation", AuditLogStep())` adds a 12th step (rendered as `[  +  ]` to mark it as user-inserted), then `strategy.replace("audit_log", QuietAuditLogStep())` swaps the implementation while keeping the position.

The `[N/11]` numbering stays pinned to the protocol's 11 standard steps; custom steps appear as `[  +  ]`. This makes the "11 standard + N custom" composition unmistakable in the trace output.
