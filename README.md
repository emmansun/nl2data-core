# nl2data-core

A governed and extensible Python framework for natural-language access to
heterogeneous enterprise data.

## Status

P0 foundation: public models and structured errors, configuration, query
adapter contract, workflow state foundation, plugin manifest and registry,
telemetry and audit ports, and the `NL2DataEngine` lifecycle skeleton. The
core never imports optional database, LLM, HTTP, or telemetry backend
dependencies.

## Packaging

| Aspect       | Value            |
| ------------ | ---------------- |
| Distribution | `nl2data-core`   |
| Public API   | `import nl2data` |
| Internal API | `import nl2data_core` |

- `nl2data` is the documented, stable public surface (models, errors, engine).
- `nl2data_core` holds narrow internal ports and foundations and is **not**
  a public API; it exists so optional providers can be plugged in without
  leaking into the public surface.
- Requires Python 3.11+.

## Install

```bash
pip install nl2data-core
```

For development (editable install with test/type/lint tooling):

```bash
pip install -e ".[dev]"
```

## Minimal usage

```python
import asyncio

from nl2data import ErrorCode, NL2DataEngine, OutcomeStatus, QueryRequest, load_config


async def main() -> None:
    config = load_config(
        {
            "schema_version": 1,
            "service": {"name": "example"},
        }
    )
    engine = NL2DataEngine(config=config)
    await engine.initialize()

    outcome = await engine.query(
        QueryRequest(
            request_id="req-1",
            prompt="How many orders shipped yesterday?",
        )
    )

    # P0: no workflow is configured yet, so the engine returns an explicit
    # not-configured outcome instead of fabricating a result.
    assert outcome.status == OutcomeStatus.NOT_CONFIGURED
    assert outcome.error is not None and outcome.error.code == ErrorCode.NOT_CONFIGURED

    await engine.close()


asyncio.run(main())
```

### P0 not-configured behavior

Without a registered workflow, `query()` never invokes native database, LLM,
or provider executors. It returns a stable `NOT_CONFIGURED` outcome carrying a
safe, redacted `ErrorRecord`. Queries submitted before initialization or
during drain/close are rejected with structured `LifecycleError`s, and the
engine lifecycle is explicit: `created → initializing → ready → draining →
closed`.

## Public application facade (P2.7)

The P2.7 facade makes the library embeddable: applications compose and run
the governed runtime through one public entry point (:class:`NL2Data`) and a
typed composition profile, never through internal ``nl2data_core`` modules.

- **Composition**: :class:`CompositionProfile` binds either a pre-built
  transport-neutral :class:`WorkflowRuntimePort` or the deterministic
  composition parts (AI provider, Memory, query adapter, tenant context,
  governance policy scope, authorized view, plan resolver, state store,
  telemetry). An empty profile yields the safe ``NOT_CONFIGURED`` fallback
  and never loads optional backends.
- **Lifecycle**: ``created -> initializing -> ready -> draining -> closed``.
  ``initialize()`` is the earliest point optional modules load; constructing
  a facade imports no database, LLM, HTTP, or telemetry backend.
- **Async is canonical**: ``await facade.aquery(request)`` returns only
  protected :class:`QueryOutcome` values. Unexpected runtime failures map
  to a safe failed outcome; internal details never cross the boundary.
- **Sync convenience**: ``facade.query(request)`` runs ``aquery`` only when
  no event loop is active in the current thread. Inside an active loop it
  raises the stable :class:`SyncUsageError` (``ASYNC_REQUIRED``) instead of
  nesting or blocking the loop.
- **Workflow handles**: ``facade.get_workflow(workflow_id)`` returns a
  bounded :class:`WorkflowHandle` (status, stage, cancellation flag,
  sha256 evidence fingerprints, bounded event history) or ``None``. Handles
  exist only when a durable state store is configured; without one the
  facade reports absence instead of fabricating state.
- **Cancellation**: ``facade.cancel(CancellationRequest(...))`` persists a
  cooperative cancellation flag through the state store (``CANCELLED``), or
  reports ``ALREADY_TERMINAL``/``NOT_FOUND``. A later resume fails fast
  with ``WORKFLOW_CANCELLED`` before any adapter work.
- **Capabilities and health**: ``facade.capabilities()`` returns an
  immutable :class:`FacadeCapabilities` snapshot (configured, runtime,
  provider, adapter, memory/tenant/durable flags, bounded features);
  ``facade.health()`` observes the lifecycle.
- **Idempotent close**: ``drain()`` and ``close()`` are idempotent;
  ``close()`` releases provider, adapter, Memory, and state-store resources
  exactly once.

### Embedded usage

```python
import asyncio

from nl2data import (
    CancellationRequest,
    CompositionProfile,
    NL2Data,
    OutcomeStatus,
    QueryRequest,
)

# Bind a pre-built runtime port, or the deterministic composition parts:
# adapter, policy_scope, view, plan_resolver, provider, state_store,
# tenant_context, ... (all optional)
composition = CompositionProfile()


async def main() -> None:
    facade = NL2Data(composition=composition)  # or create_facade(...)
    await facade.initialize()

    outcome = await facade.aquery(
        QueryRequest(request_id="req-1", prompt="How many orders yesterday?")
    )
    assert outcome.status == OutcomeStatus.SUCCEEDED

    if outcome.workflow_id is not None:
        handle = facade.get_workflow(outcome.workflow_id)
        facade.cancel(CancellationRequest(workflow_id=outcome.workflow_id))

    await facade.close()


asyncio.run(main())
```

### Deprecation guidance for `nl2data_core` imports

``nl2data_core`` remains internal: it is not removed, but direct application
imports from it are deprecated. New code must import only ``nl2data``.
Existing applications that import ``nl2data_core`` today should migrate
through the facade:

- Replace ``NL2DataEngine`` usage with ``NL2Data``/``create_facade`` where
  possible (``NL2DataEngine`` stays for source compatibility).
- Move composition inputs into ``CompositionProfile`` instead of
  constructing internal runners, adapters, stores, or tenant contexts at
  the application boundary.
- Configuration still loads through the public ``load_config``; the typed
  configuration model remains internal until a public configuration API
  ships.

### Future `nl2data_http` hosting boundary

A later ``nl2data_http`` package (out of scope here) may host the library
behind HTTP. The boundary is already fixed: it will program against the
transport-neutral :class:`FacadePort` (async query, sync convenience,
workflow lookup, cancellation, capabilities, health, drain, close) and the
public models (query request/outcome, workflow handles, cancellation
results, capability snapshots) without importing internal types. The core
adds no HTTP dependency and never loads ``fastapi``, ``flask``, or
``starlette``; transport hosts remain optional packages outside
``nl2data`` and ``nl2data_core``.

## AI runtime boundary (P2)

The P2 AI runtime boundary keeps model providers optional and lazy, mirroring
how database adapters plug into the governed path:

- `ModelProvider` (internal `nl2data_core.ai.protocol`) is a provider-neutral
  asynchronous port for structured output. It receives a bounded invocation
  request (natural-language prompt plus an authorized context payload) and
  never receives database clients, credentials, or unfiltered catalog objects.
- The core ships a deterministic `FakeModelProvider` for contract, security,
  and evaluation tests; it requires no credentials and no network access.
- `IntentResolver` converts provider output into validated structured intent,
  clarification, or safe rejection - never raw SQL/MQL/shell/AST/driver-shaped
  output. Semantic references outside the authorized view fail closed, and
  provider calls are bounded by the configured attempt budget.
- The opt-in `AIWorkflowRunner` is a compatibility facade: without a provider
  it preserves the P1 structured-IR path and the not-configured fallback;
  with a provider it delegates the whole AI+Memory composition to the
  governed workflow runtime (see below), which owns validation, governance,
  authorization, and execution ordering.
- Vendor model providers (OpenAI, Anthropic, LangChain, ...) belong in optional
  packages behind the `ModelProvider` port, exactly like database drivers; the
  core import boundary never loads them.

## Trusted tenant-context boundary (P2)

The P2 tenant boundary lets hosts bind query execution to a trusted tenant
scope without ever trusting client-supplied claims:

- `TenantScopeContext` (internal `nl2data_core.tenancy`) is composed only from
  trusted host integration input - never from `QueryRequest` bodies or
  prompts. `QueryContext.tenant_hint` is recorded as untrusted routing
  metadata only.
- Trusted-context validation fails closed: missing context, inactive tenant
  lifecycle states, unknown or unsupported isolation profiles, and client
  hints that conflict with the trusted context are denied before any
  adapter execution with `TENANT_CONTEXT_REJECTED`.
- Tenant scope travels as deterministic SHA-256 scope fingerprints through
  governance facts, policy scopes, execution authorization, workflow state,
  telemetry context, and tenant-scoped namespace/cache-key primitives - raw
  tenant IDs, principal IDs, delegation actors, entitlement claims, and
  client hints are never persisted or exposed. Outcomes expose only the
  bounded opaque `tenant_scope_fingerprint` reference.
- Isolation profiles (`pooled`, `schema_isolated`, `database_isolated`,
  `deployment_isolated`) declare the minimum enforcement obligations the
  host must guarantee; missing or unsupported profile data denies
  tenant-scoped execution instead of silently downgrading.
- A deterministic conformance suite (`nl2data_core.tenancy.conformance`)
  exercises positive propagation, cross-tenant reuse, inactive tenants,
  delegation, namespace separation, missing context, and adversarial client
  claims, emitting protected evidence and reports with no raw identity.

### Host-integration responsibilities

Authentication, durable state, and cryptographic trust remain outside this
boundary and are owned by the host integration:

- **Authentication**: verifying end-user and principal identity before
  composing `SubjectContext`.
- **Durable tenant state**: managing tenant lifecycle, entitlements, and
  delegation records that feed trusted contexts.
- **Memory**: long-lived cross-request state (sessions, caches, audit
  stores) is host-owned; this core only defines bounded scope references for
  future namespace primitives.
- **HTTP authentication**: request authentication and tenant routing are
  host gateway concerns; the core treats `tenant_hint` as untrusted input.
- **Cryptographic trust**: key management, token signing, and trust
  establishment for fingerprints and authorization artifacts are host
  responsibilities; scope fingerprints are deterministic references and are
  never treated as authentication.

## Durable workflow-state boundary (P2)

An optional durable workflow-state store (internal `nl2data_core.workflow`)
persists safe workflow snapshots and idempotency records behind the
replaceable `StateStore` protocol, so hosts can resume and deduplicate query
work across restarts:

- `SQLiteStateStore` implements the protocol with the Python standard library
  `sqlite3` - no external database dependency. Records are keyed by
  `(scope_namespace, workflow_id)`; tenant-scoped workflows are isolated in
  opaque `tenant:workflow:<fingerprint>` namespaces derived only from scope
  fingerprints, and unscoped lookups can never observe scoped records.
- Snapshots are canonical JSON with an explicit `schema_version`; raw payload
  fields (prompts, queries, SQL, results, rows, credentials, secrets, tokens)
  are rejected on write and on read, so durable state never leaks raw input
  or tenant identity.
- Updates run inside `BEGIN IMMEDIATE` transactions with monotonic revision
  compare-and-set: missing records, status changes, stale revisions, and
  tenant-scope mismatches fail with structured `WorkflowStateError`
  conflicts - stale writers never silently overwrite newer state.
- Idempotency-key records bind a request identity to one workflow within its
  scope namespace; reuse with a different request raises
  `IDEMPOTENCY_CONFLICT`, and completed keys store only a safe terminal
  outcome fingerprint reference.
- When a `state_store` is bound, `QueryExecutionRunner` persists workflow
  transitions, reserves the request id, and replays completed work as
  `REJECTED` with the public `DUPLICATE_REQUEST` error code instead of
  re-executing it. Recovery from a `RUNNING` checkpoint re-executes at
  least once; this core never claims exactly-once external execution.
- Retention is host-owned: `cleanup()` removes bounded batches of terminal
  snapshots and expired idempotency records older than the given cutoffs;
  active or running workflows are never touched.
- SQLite file locking serializes writers, bounding this store to local
  workers; the protocol stays replaceable for a future service-backed store.

## Memory / multi-turn context boundary (P2)

The P2 memory boundary lets hosts bind multi-turn context to query execution
while keeping Memory strictly contextual - it never caches or replays raw
results:

- Immutable, bounded `MemoryRecord` models (internal `nl2data_core.memory`)
  cover working state, session summaries, query references, semantic
  decisions, and audit references. They store only logical facts and
  protected SHA-256 fingerprints of intent, IRs, artifacts, policies, and
  catalogs - never raw prompts, SQL/MQL, result rows or documents, secrets,
  or native objects.
- The replaceable `MemoryProvider` protocol (append, recall, compare-and-set,
  compact, expire, delete) ships with a deterministic in-memory
  implementation scoped by tenant, session, and conversation fingerprints.
  Scope mismatches are denied, records expire by TTL, recall honors bounded
  budgets, and unavailable providers raise normalized errors.
- `MultiTurnResolver` projects recalled references into the P2.1 provider
  context and revalidates them on every turn: tenant scope, policy/catalog
  fingerprints, semantic view, adapter/artifact references, and record
  expiry. Stale or out-of-scope references fail closed into clarification,
  and a dependent follow-up never executes a recalled IR directly.
- Memory is context only. Raw result caching is unsupported: no path stores
  or replays query results or rows.
- An optional Redis-backed provider (`nl2data_core.memory.RedisMemoryProvider`,
  install the `redis` extra) implements the same protocol against a shared
  Redis service so separate workers and Pods observe one bounded context.
  It stores only the same safe serialization envelopes, keeps record ids
  atomic through a namespaced registry, and revalidates every recalled
  record - so a stale or foreign index member can never authorize a record.
- **Redis configuration**: hosts own the connection url (never stored in
  configuration models) and the namespace - one unique namespace per
  application/environment, since providers never share or assume ownership
  of keys outside it. `RedisMemoryConfig` validates the namespace pattern
  and bounds TTL, capacity, recall candidates/batches, compaction batches,
  expired-id retention, and connect/command timeouts before any
  connection is made.
- **TTL behavior**: records expire by their own `ttl_seconds`; an explicitly
  expired record id stays reserved for `expired_id_retention_seconds`
  before it may be reused. Availability is a bounded ping; on any failure
  operations raise normalized `MEMORY_UNAVAILABLE` errors and the workflow
  degrades statelessly to the P2.1 path - Memory is never required for
  query execution.
- Memory provides no workflow execution fencing: shared storage does not
  serialize or fence workflow runs. Execution ordering, cancellation, and
  idempotency remain owned by the workflow runtime and durable state
  store, exactly as with the in-memory provider.
- When Memory is unavailable the workflow degrades statelessly to the P2.1
  path; memory injection into `AIWorkflowRunner` is opt-in and keeps the
  governed boundary - validation, authorization, and IR checks still run
  on every turn.
- A deterministic conformance suite (`nl2data_core.memory.conformance`)
  exercises raw-payload rejection, scope isolation, stale reference denial,
  retention, deletion, compaction, stateless fallback, and bounded recall,
  emitting protected evidence and reports with no raw material.

## Governed workflow runtime (P2)

The P2.5 governed workflow runtime (internal `nl2data_core.workflow`) owns
one explicit order for the existing boundaries instead of nesting
conditionals inside runners:

- A framework-neutral `WorkflowRuntime` protocol with typed immutable
  `WorkflowExecutionContext`, stage results, deadlines, cancellation, safe
  errors, and protected evidence. The core never imports or depends on
  LangGraph; a deterministic reference runtime implements the same contract
  and is the conformance baseline.
- One explicit ordered stage graph:
  `initialize -> memory -> intent -> plan -> validate -> govern -> authorize
  -> execute -> protect -> persist -> complete`. Clarification, denial,
  timeout, cancellation, retry exhaustion, and approval-required are typed
  terminal or controlled branch outcomes - never generic provider
  exceptions. `SUCCEEDED` and `CLARIFICATION` map one-to-one onto public
  outcomes; the rest normalize to public `REJECTED`/`FAILED` outcomes with
  specific error codes.
- Mandatory gates: the adapter is never invoked unless current tenant
  scope, IR validation, governance, artifact validation, and
  authorization evidence are present and fresh. Denial or malformed input
  stops before any external work starts, and a future optional backend must
  pass the same gate assertions.
- Cooperative cancellation and request deadlines: every stage that can
  perform external work receives a bounded deadline/cancellation context
  and stops before starting the next external operation. The runtime never
  claims it cancelled an already-running external call; ambiguous
  post-execution states are recorded for reconciliation.
- Checkpoints persist only safe evidence: stage name, workflow state,
  tenant scope, configuration/policy/catalog/semantic/artifact
  fingerprints, and bounded retry/repair counters. Raw prompts, queries,
  IRs, results, provider, and native objects never enter runtime state.

### At-least-once recovery and idempotency

- Checkpoints persist through the replaceable P2.3 `StateStore` at stage
  boundaries; restart resumes only compatible non-terminal checkpoints.
  Stale configuration/policy/catalog/semantic/artifact snapshots and
  cross-tenant checkpoints are rejected, never resumed.
- Recovery is at-least-once: an interrupted workflow may re-run stages, and
  this core never claims exactly-once external execution. Completed
  terminal outcomes replay idempotently through durable idempotency-key
  records without re-executing finished external work.

### Optional LangGraph backend

A future optional backend (for example `nl2data-langgraph`) may translate
the core stage contract to LangGraph nodes and checkpoints behind the
`WorkflowBackend`/`WorkflowBackendProfile` contract. It must pass the same
mandatory conformance suite (`tests/contract/test_backend_conformance.py`,
`tests/conformance/test_workflow_runtime_conformance.py`) and cannot bypass
core gates; activation happens only after conformance passes.

### Unsupported today

Streaming wire protocols, distributed or multi-worker execution,
autonomous repair or agent loops (only bounded extension points exist), a
public approval-required outcome status (internal runtime event only), and
service-backed stores (MongoDB, HTTP transport) are out of scope for this
runtime.

## PostgreSQL conformance profile

The P1 query-execution foundation ships an optional PostgreSQL conformance
profile that reuses the SQLite fixture's logical schema, seed data, policy
cases, and protected result assertions.

- Install the optional driver: `pip install -e ".[postgres]"` (psycopg 3).
- Point the profile at a developer-managed service with the
  `NL2DATA_POSTGRES_DSN` environment variable (default:
  `postgresql://localhost:5432/nl2data_test`).
- The profile never requires the service: when the driver or service is
  unavailable, conformance tests are skipped and evaluation outcomes are
  reported as `skipped`/`unavailable` - never as a pass.
- Relevant suites: `tests/integration/test_fixtures.py` and
  `tests/conformance/test_postgres_conformance.py`.

## MongoDB adapter profile (P2)

The P2 query-execution foundation adds a structured, read-only MongoDB
specialization behind the same generic `QueryAdapter` lifecycle, with the
same governed order (IR validation, governance, authorization, adapter
guard, protected results) as SQL.

- **Optional installation**: `pip install nl2data-core[mongodb]` (PyMongo
  4.6+). The base package never imports PyMongo; MongoDB models, the
  validator, and the deterministic fake executor work with no driver, and
  import-boundary tests enforce that no MongoDB type or dependency enters
  the public `nl2data` API or the framework-neutral adapter contracts.
- **Supported operations**: `find`, bounded `aggregate` pipelines
  (`$match`, `$project`, `$sort`, `$skip`, `$limit`, `$group`, `$count`,
  `$unwind`), and `count_documents`. Writes, administrative commands,
  JavaScript, `$where`, regex evaluation, wildcard projections, and
  unbounded operations are rejected before any driver call.
- **Safe defaults**: specifications are strict JSON wire forms validated
  against collection/field/operator/stage allowlists; results are bounded
  by document, column, byte, and wall-clock limits; supported BSON values
  are normalized conservatively into scalar `ExecutionResult` rows, and
  unsupported native values fail with safe structured errors that never
  expose the raw value. Canonical normalization makes fingerprints stable
  across runs.
- **Tenant and governance integration**: MongoDB query facts (collections,
  fields, operators, stages, result shape, tenant obligations) feed the
  existing governance, tenant-scope, execution-authorization, and result
  protection gates; pooled profiles require mandatory tenant predicates,
  and non-pooled profiles require routing evidence, failing closed when
  the adapter profile cannot enforce them.
- **Driver/service availability**: the optional PyMongo profile connects
  lazily and reports `MONGO_UNAVAILABLE` when the driver is missing or the
  service is unreachable - never a false pass. Real-service conformance is
  skipped (`skipped`/`unavailable` outcomes) unless both exist; the
  deterministic fake executor covers the same conformance and
  SQL/Mongo equivalence cases without any service.
- **Deferred capabilities**: `$lookup`/cross-collection joins, Atlas
  Search/vector stages, map-reduce, change streams, writes, explain-based
  cost estimation, and native BSON identifier/date forms beyond the
  normalized scalar set are out of scope for the first profile.
- Relevant suites: `tests/unit/test_mongodb_specs.py`,
  `tests/contract/test_mongodb_adapter.py`,
  `tests/security/test_mongodb_security.py`,
  `tests/conformance/test_mongodb_conformance.py`,
  `tests/integration/test_mongodb_governance.py`, and
  `tests/integration/test_mongodb_real.py` (optional service).

## Development

```bash
python -m pytest        # full unit/contract/integration/security suite
python -m mypy src      # static type checking
python -m ruff check src tests  # lint
```
