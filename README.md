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

from nl2data import ErrorCode, NL2DataEngine, OutcomeStatus, QueryRequest
from nl2data_core.config.loader import load_config  # P0: internal config loader


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
- The opt-in `AIWorkflowRunner` hands validated intent to the existing governed
  execution boundary (`QueryExecutionRunner.execute_plan`); without a provider
  it preserves the P1 structured-plan path and the not-configured fallback.
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
  protected SHA-256 fingerprints of intent, plans, artifacts, policies, and
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
  and a dependent follow-up never executes a recalled plan directly.
- Memory is context only. Raw result caching is unsupported: no path stores
  or replays query results or rows, and durable or service-backed (for
  example Redis) providers remain future work behind the replaceable
  protocol.
- When Memory is unavailable the workflow degrades statelessly to the P2.1
  path; memory injection into `AIWorkflowRunner` is opt-in and keeps the
  governed boundary - validation, authorization, and plan checks still run
  on every turn.
- A deterministic conformance suite (`nl2data_core.memory.conformance`)
  exercises raw-payload rejection, scope isolation, stale reference denial,
  retention, deletion, compaction, stateless fallback, and bounded recall,
  emitting protected evidence and reports with no raw material.

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

## Development

```bash
python -m pytest        # full unit/contract/integration/security suite
python -m mypy src      # static type checking
python -m ruff check src tests  # lint
```
