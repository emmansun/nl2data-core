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
