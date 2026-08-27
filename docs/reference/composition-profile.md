# CompositionProfile Reference

> **Reader**: application integrators and composition-layer developers.
> **Prerequisites**: [Composition and query lifecycle](../guides/composition-and-query-lifecycle.md)
> and [Semantic layer](../architecture/semantic-layer.md).
>
> [简体中文](composition-profile.zh-CN.md)

`CompositionProfile` is the typed, immutable input to the public facade
(`NL2Data` or `create_facade`). It binds either a pre-built runtime port or
the deterministic composition parts. Unknown fields are rejected at
construction time (`extra="forbid"`), so a typo fails early.

This page documents every field: what it means, where its value comes from,
whether it affects executability, and how to use it in practice.

## Two composition modes

1. **Pre-built runtime** — bind `runtime` with a `WorkflowRuntimePort`
   (for example a runtime produced by your own composition layer). The
   facade delegates entirely to it.
2. **Deterministic parts** — bind `adapter`, `policy_scope`, `view`,
   `plan_resolver`, plus optional `provider`, `state_store`,
   `tenant_context`, `memory`, `binding`, and the rest. The facade builds
   the governed runtime itself.

An empty profile is valid: every query returns `NOT_CONFIGURED` with a
safe `ErrorRecord`. Nothing is loaded, nothing is called.

## The executability gate

No field is structurally required, but a deterministic profile is
**executable only when all four runtime parts are bound**:

- `adapter` (query I/O)
- `policy_scope` (governance allow-scope)
- `view` (authorized semantic view)
- `plan_resolver` (request → `SemanticQueryIR`)

With `runtime` bound, the gate is `runtime.is_configured()` instead.
Missing any part does not raise — it silently yields `NOT_CONFIGURED`
outcomes (`facade.capabilities().configured` is `false`), so the four-part
gate is effectively required for a working deterministic profile.

`binding` is **not** part of the gate: it is optional compiler context.
Without it the default SQL compiler uses the `sqlite` dialect and the
generated artifact cannot match a real PostgreSQL column layout — present
it whenever you compile for a non-default dialect.

## Field reference

| Field | Kind | Meaning | Source / default |
| --- | --- | --- | --- |
| `runtime` | Port | Pre-built governed workflow runtime; `is_configured()` decides executability | Your composition layer / `None` |
| `provider` | Port | AI provider; `capabilities().provider_name` is reported | `nl2data-openai` or your provider / `None` |
| `memory` | Port | Memory backend; `is_available()` gates recall | `nl2data-memory-redis` or your backend / `None` |
| `adapter` | Port | Query adapter; `capabilities().adapter_type` is reported | `nl2data-postgres`, `nl2data-mongodb`, or yours / `None` |
| `telemetry` | Port | Structured log/span/metric/audit sink | Your sink / `None` |
| `tenant_context` | Opaque | Trusted tenant scope; its `scope_fingerprint` gates view resolution and tenant evidence | Your trusted scope, host-owned / `None` |
| `policy_scope` | Opaque | Governance allow-scope; `resource_ids` are **physical object names** and must match the binding; its `policy_fingerprint` binds the view definition | Host-owned, or derived from discovery bounds / `None` |
| `view` | Opaque | `AuthorizedView`: `source_id`, `root_entity_ids`, `field_ids`, optional view binding (`view_id`/`view_version`/`view_fingerprint` all-or-none) | `AuthorizedView.from_projection(projection)` or manual / `None` |
| `projection` | Opaque | The resolved `ResolvedViewProjection`; when bound, the runtime derives the authorized view and semantic references itself and records structured Bundle evidence | `ViewRegistry.resolve(...).projection` / `None` |
| `binding` | Opaque | `PhysicalBinding`: one `object_id`, `dialect`, column bindings — compiler context only | Host, from discovery bounds or source config / `None` |
| `config` | Opaque | Model invocation configuration (timeouts, attempts, temperature) | Host / `None` |
| `plan_resolver` | Opaque | Maps a `QueryRequest` to a validated `SemanticQueryIR` or `None` | `StaticPlanResolver(ir)` or your resolver / `None` |
| `state_store` | Opaque | Durable workflow state; enables workflow handles, cancellation, idempotency | `nl2data-workflow-postgres` or your store / `None` |
| `semantic_references` | Opaque | field → `SemanticReference` mapping for AI context assembly | Auto-derived from `projection`; hand-build only without one / `None` |
| `memory_budget` | Opaque | Memory recall budget (candidates, per-request caps) | Host / `None` |
| `budget` | Opaque | Workflow attempt/event/duration budgets | Host / `None` |
| `approval_required` | Opaque | Callable deciding whether a compiled IR needs approval | Host / `None` |
| `plan_compiler` | Opaque | IR → artifact compiler; defaults to the built-in SQL compiler | Host / built-in SQL compiler |
| `now` | Opaque | Clock injection for deterministic tests | Host / system clock |
| `min_confidence` | Scalar | Minimum intent confidence accepted by the resolver | `0.6` |
| `memory_ttl_seconds` | Scalar | Memory write-back TTL | `86_400` |
| `idempotency_ttl_seconds` | Scalar | Idempotency retention TTL | `86_400.0` |

**Port fields** are public protocol shapes defined in `nl2data`
(`WorkflowRuntimePort`, `ModelProviderPort`, `MemoryProviderPort`,
`QueryAdapterPort`, `TelemetryPort`). **Opaque fields** are internal types
(`PolicyScope`, `AuthorizedView`, `ResolvedViewProjection`,
`PhysicalBinding`, `ModelConfig`, ...). Applications import only
`nl2data`; the opaque values arrive from your composition layer, which may
import `nl2data_core`. Importing `nl2data_core` directly in an application
is unsupported (see [Troubleshooting](../operations/troubleshooting.md)).

### `view` vs `projection`

- `view` is the governance contract the runtime checks every IR against.
- `projection` is the evidence-carrying resolution result. When it is
  bound, the runtime builds `view` (via `AuthorizedView.from_projection`)
  and the semantic references automatically, and checkpoints,
  authorization, and result-lineage evidence carry the resolved-view and
  Bundle fingerprints. Without it, you must build `view` and
  `semantic_references` yourself and Bundle identity commits only
  transitively through the view fingerprint.

Prefer binding `projection` whenever you use the metadata lifecycle.

## Examples

### 1. Empty profile — safe fallback

```python
from nl2data import CompositionProfile, create_facade

facade = create_facade(composition=CompositionProfile())
await facade.initialize()

outcome = await facade.aquery(request)
assert outcome.status == "not_configured"
```

### 2. Pre-built runtime port

```python
from nl2data import CompositionProfile, create_facade

facade = create_facade(composition=CompositionProfile(runtime=my_runtime))
```

### 3. Deterministic parts

The internal types below are supplied by your composition layer; they are
shown here to make the shape concrete.

```python
from nl2data import CompositionProfile, NL2Data
from nl2data_core.governance.models import PolicyScope
from nl2data_core.planning.validation import AuthorizedView
from nl2data_core.workflow.runner import StaticPlanResolver

profile = CompositionProfile(
    provider=model_provider,
    adapter=query_adapter,              # allowed_objects={"orders"}
    policy_scope=PolicyScope(
        policy_id="demo-policy",
        source_ids=frozenset({"sales"}),
        resource_ids=frozenset({"orders"}),  # physical object names
        operation_ids=frozenset({"select"}),
        field_ids=frozenset({"order_id", "amount", "region", "status"}),
    ),
    view=AuthorizedView(
        source_id="sales",
        root_entity_ids=frozenset({"order"}),  # semantic entity ids
        field_ids=frozenset({"order_id", "amount", "region", "status"}),
    ),
    plan_resolver=StaticPlanResolver(ir),
    binding=physical_binding,           # object_id="orders", dialect, columns
    state_store=state_store,
    tenant_context=scope,
)

facade = NL2Data(composition=profile)
```

### 4. Metadata lifecycle — projection first

Resolve first (policy and tenant must exist before resolution), then fold
the projection into the profile. The full recipe is in
[Metadata to active Bundle](../guides/metadata-to-bundle.md).

```python
from nl2data import CompositionProfile, NL2Data
from nl2data_core.planning.validation import AuthorizedView

projection = registry.resolve("sales_view", trusted_resolution_context).projection

profile = CompositionProfile(
    provider=model_provider,
    adapter=query_adapter,
    policy_scope=policy_scope,                      # resource_ids = physical objects
    view=AuthorizedView.from_projection(projection),
    projection=projection,                          # bundle evidence end to end
    binding=physical_binding,
    plan_resolver=plan_resolver,
    state_store=state_store,
    tenant_context=scope,
)
```

### 5. Multi-root data source

A source with several root tables (for example `orders` and `customers`)
gets **one profile per physical object**: every query has exactly one root
entity, and the SQL compiler emits one statement per profile binding.

```python
# Shared semantic layer: one descriptor + one Bundle with entities
# "order" and "customer". Two view definitions restrict to one entity
# each; two resolutions produce two projections.

orders_profile = CompositionProfile(
    provider=model_provider,
    adapter=orders_adapter,              # allowed_objects={"orders"}
    policy_scope=orders_policy,          # resource_ids={"orders"}
    view=AuthorizedView.from_projection(orders_projection),  # root: order
    projection=orders_projection,
    binding=orders_binding,              # object_id="orders"
    plan_resolver=orders_plan_resolver,  # IRs rooted at "order"
)

customers_profile = CompositionProfile(
    provider=model_provider,
    adapter=customers_adapter,            # allowed_objects={"customers"}
    policy_scope=customers_policy,        # resource_ids={"customers"}
    view=AuthorizedView.from_projection(customers_projection),  # root: customer
    projection=customers_projection,
    binding=customers_binding,            # object_id="customers"
    plan_resolver=customers_plan_resolver,  # IRs rooted at "customer"
)
```

Each profile is an independent facade. They share the provider, descriptor,
and Bundle; only the object-scoped parts differ. When several roots live in
**one** table, a single profile can authorize them all — the view's
`root_entity_ids` is a set and the binding covers all their fields.

## Common mistakes

- **Semantic vs physical names**: `view.root_entity_ids` uses semantic
  entity ids (`"order"`); `policy_scope.resource_ids` and
  `binding.object_id` use physical names (`"orders"`). Mixing them up
  yields `GOVERNANCE_DENIED` or compile failures.
- **Hand-building `view` when a `projection` exists**: you lose automatic
  semantic-reference derivation and structured Bundle evidence. Use
  `AuthorizedView.from_projection` + `projection`.
- **Treating the four-part gate as optional**: a profile with only
  `adapter` + `view` never fails loudly — every query just returns
  `NOT_CONFIGURED`.
- **Importing `nl2data_core` in applications**: build a composition layer
  once; applications consume `nl2data` only.

## Related pages

- [Composition and query lifecycle](../guides/composition-and-query-lifecycle.md)
- [Semantic layer](../architecture/semantic-layer.md)
- [Metadata to active Bundle](../guides/metadata-to-bundle.md)
- [Architecture overview](../architecture/overview.md)
