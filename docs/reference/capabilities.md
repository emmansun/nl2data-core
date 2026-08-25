# Capabilities and Support

> **Reader**: decision makers evaluating the library. **Prerequisites**:
> none. Status vocabulary: **Implemented** (exists in source),
> **Conformant** (passes the deterministic conformance suite),
> **Verified** (passed a real-service/live-provider run),
> **Production Supported** (deployment contract + operational guidance).

## Capability matrix

| Capability | Status | Verification | Requires |
| --- | --- | --- | --- |
| Public facade (`NL2Data`/`create_facade`), lifecycle, protected outcomes | Implemented + conformant | Deterministic suites | — |
| Public models/errors (immutable, redacted) | Implemented + conformant | Unit + security suites | — |
| Strict versioned configuration + fingerprints | Implemented + conformant | Unit suite | — |
| Semantic IR + canonical fingerprints | Implemented + conformant | Contract suite | — |
| Semantic View resolution (fail-closed) | Implemented + conformant | Contract + security suites | — |
| Semantic Model Bundles + catalog | Implemented + conformant | Contract + security suites | — |
| Governed workflow runtime (deterministic) | Implemented + conformant | Conformance suite | — |
| Query lifecycle: clarification, cancellation, handles, capabilities/health | Implemented + conformant | Integration suite | — |
| Durable workflow state (SQLite) | Implemented + conformant | Contract suite | — |
| Durable workflow state (PostgreSQL, leases + fencing) | Implemented + verified | Real-service CI profile | `postgres` extra + service |
| Memory (in-memory) | Implemented + conformant | Conformance suite | — |
| Memory (Redis) | Implemented + verified | Real-service CI profile | `redis` extra + service |
| SQL adapter (SQLite fixtures) | Implemented + conformant | Conformance suite | `sql` extra |
| SQL adapter (PostgreSQL) | Implemented + verified | Real-service CI profile | `postgres` extra + service |
| MongoDB adapter | Implemented + verified | Real-service CI profile | `mongodb` extra + service |
| Metadata discovery + production profile | Implemented + verified | Real-service CI profile | `postgres`/`mongodb` extra + service |
| AI intent resolution + instruction contract | Implemented + conformant | Evaluation suite (fake provider) | — |
| OpenAI structured-output provider | Implemented; live-verified on demand | `run_openai_live.py` | `nl2data-openai` + credentials |
| Tenant isolation + scope fingerprints | Implemented + conformant | Conformance suite | — |
| Telemetry/audit ports (in-memory sinks) | Implemented + conformant | Contract suite | — |

## What is not supported today

- **HTTP hosting**: no `nl2data_http` package; a future host programs
  against the transport-neutral `FacadePort`.
- **Streaming wire protocols**, agent loops, and autonomous repair beyond
  the bounded extension points.
- **A public approval-required outcome status** (internal runtime event
  only).
- **Service-backed stores beyond PostgreSQL** for workflow state
  (MongoDB/HTTP state backends are not implemented).
- **Metadata cross-process lifecycle coordination** (the snapshot ledger
  is process-local; a later shared catalog must implement the same
  host-owned semantics).
- **Exactly-once external execution** — recovery is at-least-once by
  design.
- **$lookup/cross-collection joins, Atlas Search/vector stages,
  map-reduce, change streams, and writes** in the MongoDB adapter.

## Support policy

- The public `nl2data` API is the only supported application surface;
  `nl2data_core` is contributor-only and changes without notice.
- Real-service and live-provider results are **environment-dependent**:
  `skipped`/`unavailable` outcomes are never verification. Only
  `verified` counts as evidence of service compatibility.
- Supported Python versions: 3.11, 3.12, 3.13 (CI matrix).
- Supported services in CI: PostgreSQL 16, Redis 7, MongoDB 7,
  OpenAI-compatible gateways (live, on demand).

## Feature flags exposed by the facade

`facade.capabilities().features` reports bounded feature identifiers:
`async_query`, `sync_query`, `workflow_handles`, `cancellation`,
`clarification`. `configured`, `runtime` (`custom`/`deterministic`),
`provider`, `adapter`, `memory`, `tenant_scoped`, `durable_state`, and
`config_fingerprint` describe the composed instance.

## Next steps

- [Compatibility](compatibility.md) — versioning and migration policy.
- [Production readiness](production-readiness.md) — what "production"
  means for this library.
- [能力与支持 (简体中文)](capabilities.zh-CN.md)
