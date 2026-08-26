# Service Configuration and Failure Behavior

> **Reader**: operators and platform engineers. **Prerequisites**:
> [Installation](../getting-started/installation.md). Every optional
> service is **lazy**: installing an extra never imports a driver, and a
> missing service never blocks the base library. Real-service profiles
> skip explicitly — never a false pass.

## General principles

- **Host-owned endpoints and secrets**: connection URLs, DSNs, and
  credentials never live in configuration models, fingerprints, errors,
  or evidence. They are injected through environment variables or host
  secret injection at client build time.
- **Fail closed**: missing context, unsupported versions, and stale
  evidence deny execution before any external work.
- **Failure classification**: transient conditions surface as retryable
  public errors; configuration/authorization failures are non-retryable;
  ambiguous post-execution states are surfaced for reconciliation.

## PostgreSQL (shared workflow state)

**Extra**: `postgres` (`psycopg[binary,pool]>=3.1,<4`).

**Endpoint injection**: `NL2DATA_POSTGRES_DSN` (default
`postgresql://localhost:5432/nl2data_test`). Real-service profiles read
the DSN only when the pool is first built; it is never stored in
configuration models or included in errors.

**Key settings** (`PostgreSQLStateStore`):

| Setting | Default | Meaning |
| --- | --- | --- |
| Deployment namespace | `shared` | One bounded schema namespace per deployment (`^[A-Za-z][A-Za-z0-9_]{0,63}$`); multiple deployments sharing one database service never observe each other's records |
| `lease_ttl_seconds` | 120 | At most one active owner per workflow; renewed at stage entries |
| `lease_renewal_margin_seconds` | 20 | Renew when remaining time drops below this |
| `clock_tolerance_seconds` | 2 | Lease recoverable only after expiry plus tolerance |
| Migration target | 1 | Additive migrations applied transactionally up to target; newer-than-runtime schema rejected with `UNSUPPORTED_SCHEMA_VERSION` |

**Health checks**: a bounded ping/connect at client build; `pg_isready`
is the CI health gate (`integration.yml`). Availability is never assumed
— unreachable services report `STORE_UNAVAILABLE`.

**Timeouts and retries**: connect/command timeouts and pool acquisition
failures surface as retryable `STORE_UNAVAILABLE` / `STORE_TIMEOUT`.
Lease-busy conditions surface as retryable `LEASE_BUSY`.

**Failure classification**:

| Condition | Result |
| --- | --- |
| Unreachable service, timeout | Retryable `STORE_UNAVAILABLE` / `STORE_TIMEOUT` |
| Lease held by another worker | Retryable `LEASE_BUSY` |
| Stale owner committing after takeover | `FENCING_REJECTED` (never retried) |
| CAS/status/schema conflict | Public rejection (e.g. `STALE_CHECKPOINT`, `UNSUPPORTED_SCHEMA_VERSION`) |

## PostgreSQL (durable semantic catalog)

**Distribution**: `nl2data-semantic-catalog-postgres`
(`psycopg[binary,pool]>=3.1,<4`).

**Endpoint injection**: the DSN is injected by the host into the catalog
constructor from its own secret management (`dsn_secret_ref` names that
host-side secret; the dev/CI profile uses `NL2DATA_POSTGRES_DSN`). The
DSN is never stored in `SemanticCatalogConfig` and never included in
errors.

**Key settings** (`SemanticCatalogConfig`, all validated before any
connection):

| Setting | Default | Meaning |
| --- | --- | --- |
| Deployment namespace | required | One bounded schema per deployment (`^[A-Za-z][A-Za-z0-9_]{0,63}$`); deployments sharing one database service never observe each other's records |
| `pool_size` / timeouts | 5 / 5 s / 10 s | Bounded pool and per-command timeouts; pool checkout bounded at 5 s |
| `snapshot_retention_seconds` / `event_retention_seconds` | 604,800 | Cleanup keeps active content and dependencies; bounded passes remove only expired records |
| Envelope bounds | 1 MiB / 512 KiB | Max envelope and canonical payload bytes; oversized artifacts are rejected before persistence |
| Migration target | 1 | Additive migrations applied transactionally up to target; newer-than-runtime schema rejected with `SCHEMA_MISMATCH` |

**Health checks**: `pg_isready` is the CI health gate (`integration.yml`).
Unreachable services report retryable `CATALOG_UNAVAILABLE`;
`CATALOG_TIMEOUT` covers canceled statements. Availability is never
assumed.

**Schema and isolation**: the catalog owns its tables inside the
configured schema namespace — it never shares workflow checkpoint
tables. Every lifecycle record is scoped by tenant/source fingerprint;
cross-scope reads and activation fail closed (`UNAUTHORIZED`), and
tampered or newer-schema artifacts fail closed on read
(`ENVELOPE_REJECTED` / `FINGERPRINT_MISMATCH` / `SCHEMA_MISMATCH`).

**Failure classification**:

| Condition | Result |
| --- | --- |
| Unreachable service, timeout, missing driver | Retryable `CATALOG_UNAVAILABLE` / `CATALOG_TIMEOUT` |
| Cross-scope read/activation/rollback | `UNAUTHORIZED`, never retried |
| Schema/migration newer than runtime | `SCHEMA_MISMATCH`, never retried |
| Envelope/fingerprint/bound violation | `ENVELOPE_REJECTED` / `FINGERPRINT_MISMATCH` / `BOUNDS_EXCEEDED`, never retried |
| Unique-key/version conflict | `CONFLICT`, never retried |

## Redis (shared Memory)

**Extra**: `redis` (`redis>=5.0,<7`).

**Endpoint injection**: connection URL passed to the provider
constructor (e.g. `NL2DATA_REDIS_URL`) — never part of the
`RedisMemoryConfig` model, so a dumped configuration cannot leak
endpoints or secrets.

**Key settings** (`RedisMemoryConfig`, all validated before any
connection):

| Setting | Default | Bound |
| --- | --- | --- |
| `namespace` | required | `^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,63}$` — unique per application/environment |
| `max_ttl_seconds` | 365 days | Records with longer TTL rejected |
| `max_records` | 10,000 | Record capacity per session scope index |
| `max_candidates` | 1,000 | Candidate ids loaded by one recall |
| `recall_batch_size` | 100 | Candidate scan batch hint |
| `compaction_batch_size` | 500 | Index keys/members scanned per compaction pass |
| `expired_id_retention_seconds` | 3,600 | How long an expired id stays reserved |
| `connect_timeout_seconds` / `command_timeout_seconds` | 2.0 / 2.0 | Bounded client timeouts |

**Health checks**: availability is a bounded ping; on any failure
operations raise normalized `MEMORY_UNAVAILABLE` errors and the workflow
degrades statelessly to the no-Memory path — **Memory is never required
for query execution**.

**Failure classification**: any Redis failure → retryable
`MEMORY_UNAVAILABLE`; workflow continues without Memory. Record
validation failures (unsafe payload, scope mismatch, TTL over cap) are
rejections, not retries.

## MongoDB (query adapter + discovery)

**Package**: `nl2data-mongodb` (`pymongo>=4.6,<5`).

**Endpoint injection**: `NL2DATA_MONGO_URI` and `NL2DATA_MONGO_DATABASE`
(CI defaults: `mongodb://127.0.0.1:27017`, `nl2data_mongo_test`), read
by the real-service profiles when the client is first built.

**Key settings**: discovery bounds live in
`ProductionDiscoveryConfig.bounds` — `max_objects`,
`max_fields_per_object`, `max_samples`, `max_statistics`,
`timeout_seconds`, `max_concurrency`, `include_statistics` — plus
object/field allowlists that fail closed when empty.

**Health checks**: the optional PyMongo profile connects lazily and
reports `MONGO_UNAVAILABLE` when the driver is missing or the service is
unreachable — never a false pass. `mongosh ping` is the CI health gate.

**Timeouts and retries**: discovery runs inside bounded timeouts
(`timeout_seconds`) and concurrency (`max_concurrency`); PostgreSQL
discovery additionally runs inside `SET TRANSACTION READ ONLY` with a
bounded `statement_timeout`. Missing or unreachable sources raise
retryable `MetadataUnavailableError`s; errors never leak paths, DSN
material, or sampled values.

**Failure classification**:

| Condition | Result |
| --- | --- |
| Driver missing / service unreachable | `MONGO_UNAVAILABLE` (adapter), retryable `METADATA_UNAVAILABLE` (discovery) |
| Empty or non-intersecting allowlist | `METADATA_UNAUTHORIZED` before any catalog read |
| Explicit bounds failure | `METADATA_BOUNDS_EXCEEDED` |
| Bounded catalog truncation | `bounded_*` flags and a partial snapshot; never silent |
| Unsupported operation (writes, `$where`, JS, ...) | Rejected before any driver call (`MONGO_REJECTED`) |

## OpenAI (live provider evaluation)

**Package**: `nl2data-openai` (separate distribution).

**Endpoint/credential injection** (environment only, read when the
client is first built):

| Variable | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | API key (or use `api_key_resolver` / `client_factory`) |
| `OPENAI_BASE_URL` | Gateway endpoint (OpenAI-compatible) |
| `OPENAI_MODEL` | Model name |
| `OPENAI_TIMEOUT_SECONDS` | Provider timeout (default 60, 0–3600) |
| `OPENAI_LIVE_CASES` | Comma-separated case ids (default `normal-intent`) |

**Health checks**: no network call at import, construction, or
capability inspection. The live evaluation profile classifies every case
as `verified`, `unavailable`, or `skipped`; without credentials every
case is `skipped`.

**Timeouts and retries**: the provider performs exactly one vendor
request per `generate()` call; timeout, retry, and attempt-budget policy
belong to `IntentResolver` (`ModelConfig.timeout_seconds`, resolver
attempt budget).

**Failure classification**:

| Condition | Result |
| --- | --- |
| Authentication/configuration failure | Public `MODEL_INVOCATION_FAILED`, non-retryable (`details.model_code` = `INVALID_REQUEST`) |
| Timeout, connection, rate-limit, transient service error | Public `MODEL_INVOCATION_FAILED`, retryable (`details.model_code` = `MODEL_TIMEOUT` / `PROVIDER_UNAVAILABLE`) |
| Budget exhausted | `MODEL_INVOCATION_FAILED` (`model_code` = `RETRY_EXHAUSTED`); workflow budget exhaustion surfaces as `RETRY_EXHAUSTED` |

## CI service profiles

| Profile | Services | Command |
| --- | --- | --- |
| Deterministic (default) | None | `python -m pytest` |
| Real services | PostgreSQL 16, Redis 7, MongoDB 7 (containers with health checks) | integration workflow: `python -m pytest -q -rs tests/integration/test_postgres_shared_real.py tests/integration/test_postgres_discovery_real.py tests/integration/test_postgres_catalog_integration.py tests/integration/test_redis_memory_real.py tests/integration/test_mongodb_real.py tests/integration/test_mongodb_discovery_real.py tests/conformance/test_postgres_conformance.py tests/conformance/test_mongodb_conformance.py` |

Every skip reason is surfaced with `-rs`; a reachable service that fails
its tests fails the job.

## Next steps

- [Secrets and live testing](secrets.md) — ephemeral credentials,
  cleanup, and rollback.
- [Troubleshooting](troubleshooting.md) — unavailable services, stale
  snapshots, lease conflicts, and provider errors.
