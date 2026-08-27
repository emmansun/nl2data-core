# Composition and Query Lifecycle

> **Reader**: application integrators. **Prerequisites**:
> [Quickstart](../getting-started/quickstart.md). This guide uses only the
> public `nl2data` API; internal `nl2data_core` imports are
> contributor-only (see [Adding an adapter or provider](../development/adding-adapter-or-provider.md)).

## Composition model

Applications compose the library through one public entry point —
`NL2Data` or `create_facade` — and a typed `CompositionProfile`. The
profile binds either:

1. **A pre-built transport-neutral `WorkflowRuntimePort`** — a runtime you
   received from your own composition layer or implemented yourself, or
2. **The deterministic composition parts** — query adapter, policy scope,
   authorized view, plan resolver, model provider, state store, tenant
   context, Memory, telemetry.

No field is structurally required — an empty profile is valid and yields
only the safe `NOT_CONFIGURED` fallback. Executability is a separate gate:

- With `runtime` bound, the profile executes when `runtime.is_configured()`
  is `true`.
- Without it, the deterministic profile executes only when **all four
  runtime parts** are bound: `adapter`, `policy_scope`, `view`, and
  `plan_resolver`. Missing any of them silently yields `NOT_CONFIGURED`
  outcomes, so treat the four-part gate as required even though every
  individual field is optional.

The physical `binding` is not part of the gate: it is optional compiler
context (the default SQL compiler falls back to the `sqlite` dialect
without it). Every field, its source, and worked examples are documented
in the [CompositionProfile reference](../reference/composition-profile.md).

An empty profile yields the safe `NOT_CONFIGURED` fallback and never loads
optional backends. Composition is separate from authentication: the facade
accepts a trusted tenant/subject context and configured providers, and
never authenticates users, trusts client tenant claims, or resolves
secrets itself.

```python
from nl2data import CompositionProfile, create_facade

facade = create_facade(
    composition=CompositionProfile(
        # runtime=...  or the deterministic parts (executable only with
        # adapter + policy_scope + view + plan_resolver together):
        # adapter=..., policy_scope=..., view=..., plan_resolver=...,
        # provider=..., state_store=..., tenant_context=..., memory=...,
        # telemetry=...
    )
)
```

## Lifecycle

The lifecycle is explicit and observable:

```
created -> initializing -> ready -> draining -> closed
```

| State | Meaning |
| --- | --- |
| `created` | Constructed; optional modules are **not** loaded yet. |
| `initializing` | `initialize()` is composing the runtime; the earliest point optional modules load. |
| `ready` | Queries may be submitted. |
| `draining` | `drain()` accepted; new queries are rejected while accepted work finishes. |
| `closed` | `close()` released provider, adapter, Memory, and state-store resources exactly once. |

Queries before initialization or after drain/close are rejected with
structured `LifecycleError`s (`ENGINE_NOT_READY`, `ENGINE_DRAINING`,
`ENGINE_CLOSED`). `drain()` and `close()` are idempotent.

## Query lifecycle

`await facade.aquery(request)` submits one query through the governed
runtime. The runtime owns the ordered stage graph (see
[Execution flow](../architecture/execution-flow.md)); the caller observes
only the protected outcome:

```python
outcome = await facade.aquery(
    QueryRequest(
        request_id="req-1",
        prompt="How many orders shipped yesterday?",
        options=QueryOptions(max_attempts=3, timeout_seconds=30.0),
        context=QueryContext(request_id="req-1", conversation_id="conv-1"),
    )
)
```

`QueryRequest` is immutable; prompts are bounded to 100,000 characters;
options bound attempts (1–10) and timeout (0–3600 seconds).

## Protected outcomes

Every query returns a `QueryOutcome` — never a native cursor, driver
object, or raw workflow state:

| `status` | Payload contract |
| --- | --- |
| `SUCCEEDED` | Protected `result`; no error or clarification. |
| `CLARIFICATION` | `clarification` with bounded options; no result or error. |
| `FAILED` / `REJECTED` / `NOT_CONFIGURED` | Safe structured `ErrorRecord`; no result. |

- Result rows contain only scalar values (`str`, `int`, `float`, `bool`,
  `None`); results are bounded by row, column, and metadata limits.
- Unexpected runtime failures are mapped to a safe failed outcome —
  internal details never cross the boundary. Unknown exception types
  become non-retryable `INTERNAL_ERROR` records with a redacted message.
- Outcomes expose only the bounded opaque `tenant_scope_fingerprint`
  reference — never raw tenant or principal identity.

```python
from nl2data import OutcomeStatus

if outcome.status == OutcomeStatus.SUCCEEDED:
    print(outcome.result.column_names)
    for row in outcome.result.rows:
        print(row)
elif outcome.status == OutcomeStatus.CLARIFICATION:
    print(outcome.clarification.question)
    for option in outcome.clarification.options:
        print(option.option_id, option.label)
else:
    print(outcome.error.code, outcome.error.retryable)
```

## Clarification

When a request is ambiguous — for example, a recalled memory reference no
longer matches the current view, or intent resolution cannot bind a
semantic reference — the runtime returns `CLARIFICATION` instead of
guessing. Clarifications carry a bounded question and up to 10 options;
application code presents them to the user and submits a follow-up request
(optionally with conversation context for Memory-based multi-turn).

## Cancellation

`facade.cancel(CancellationRequest(...))` requests **cooperative**
cancellation: the flag is persisted through the state store and observed
at stage boundaries before external work starts.

| `CancellationStatus` | Meaning |
| --- | --- |
| `CANCELLED` | Workflow was non-terminal; the cancellation flag is now persisted. |
| `ALREADY_TERMINAL` | Workflow already finished; no cancellation recorded. |
| `NOT_FOUND` | No such workflow (for example, no durable state store configured). |

A later resume of a cancelled workflow fails fast with
`WORKFLOW_CANCELLED` before any adapter work. The runtime never claims it
cancelled an already-running external call; ambiguous post-execution
states are recorded for reconciliation.

```python
from nl2data import CancellationRequest

result = facade.cancel(
    CancellationRequest(workflow_id="wf-1", reason="operator stop")
)
assert result.status in {"cancelled", "already_terminal", "not_found"}
```

## Workflow handles

`facade.get_workflow(workflow_id)` returns a bounded `WorkflowHandle` —
workflow identity, status, current stage, cancellation flag, SHA-256
evidence fingerprints, and a bounded transition history — or `None`.
Handles exist only when a durable state store is configured; without one
the facade reports absence instead of fabricating state. A handle is a
reference, not a claim that durable state exists.

## Capabilities and health

- `facade.capabilities()` returns an immutable `FacadeCapabilities`
  snapshot: `configured`, `runtime` (`custom` or `deterministic`),
  `provider`, `adapter`, `memory`, `tenant_scoped`, `durable_state`,
  bounded `features` (`async_query`, `sync_query`, `workflow_handles`,
  `cancellation`, `clarification`), and `config_fingerprint`.
- `facade.health()` observes the lifecycle: `healthy` when ready,
  `degraded` when not ready or draining, `unhealthy` when closed.

```python
caps = facade.capabilities()
print(caps.configured, caps.runtime, caps.provider, caps.adapter)
print(caps.features)

health = facade.health()
print(health.status, health.message)
```

## Sync convenience

`facade.query(request)` runs `aquery` only when no event loop is active in
the current thread. Inside an active loop it raises the stable
`SyncUsageError` (`ASYNC_REQUIRED`) instead of nesting or blocking the
loop — async applications must call `aquery`.

## Idempotency and duplication

When a durable state store is bound, the runtime reserves the request id
and replays completed work as `REJECTED` with the public
`DUPLICATE_REQUEST` error code instead of re-executing it. Idempotency-key
records bind one request identity to one workflow within its scope
namespace; reuse with a different request raises `IDEMPOTENCY_CONFLICT`.
Recovery is at-least-once — this core never claims exactly-once external
execution.

## Next steps

- [CompositionProfile reference](../reference/composition-profile.md) —
  every profile field with construction examples.
- [Semantic layer](../architecture/semantic-layer.md) — descriptors,
  views, projections, and multi-root sources.
- [Execution flow](../architecture/execution-flow.md) — what happens
  between `aquery` and the protected outcome.
- [Workflow state](../architecture/workflow-state.md) — leases, fencing,
  and durability semantics.
- [Service configuration](../operations/services.md) — optional backends.
