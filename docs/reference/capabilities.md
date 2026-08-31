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
| Semantic assembly drafts, assertion review, approval, publish audit | Implemented + conformant | Unit + contract + security suites | — |
| Semantic Assembly YAML validation, import, and deterministic export | Implemented + conformant | Unit + Admin contract + security suites | `PyYAML`; Admin operations require scoped host authentication |
| Semantic Model Bundles + fingerprint publication/supersession catalog | Implemented + conformant | Contract + security suites | — |
| Durable semantic catalog (PostgreSQL) | Implemented + verified | Real-service CI profile | `nl2data-semantic-catalog-postgres` + service |
| Governed workflow runtime (deterministic) | Implemented + conformant | Conformance suite | — |
| Query lifecycle: clarification, cancellation, handles, capabilities/health | Implemented + conformant | Integration suite | — |
| Durable workflow state (SQLite) | Implemented + conformant | Contract suite | — |
| Durable workflow state (PostgreSQL, leases + fencing) | Implemented + verified | Real-service CI profile | `nl2data-workflow-postgres` + service |
| Memory (in-memory) | Implemented + conformant | Conformance suite | — |
| Memory (Redis) | Implemented + verified | Real-service CI profile | `nl2data-memory-redis` package + service |
| SQL adapter (SQLite fixtures) | Implemented + conformant | Conformance suite | `sql` extra |
| SQL adapter (PostgreSQL) | Implemented + verified | Real-service CI profile | `nl2data-postgres` + service |
| MongoDB adapter | Implemented + verified | Real-service CI profile | `nl2data-mongodb` + service |
| Metadata discovery + production profile | Implemented + verified | Real-service CI profile | `nl2data-postgres`/`nl2data-mongodb` package + service |
| AI intent resolution + instruction contract | Implemented + conformant | Evaluation suite (fake provider) | — |
| OpenAI structured-output provider | Implemented; live-verified on demand | `run_openai_live.py` | `nl2data-openai` + credentials |
| Tenant isolation + scope fingerprints | Implemented + conformant | Conformance suite | — |
| Admin control-plane service (transport-neutral lifecycle DTOs) | Implemented + conformant | Contract + security suites | `nl2data-admin-service` |
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
- **Exactly-once external execution** — recovery is at-least-once by
  design.
- **$lookup/cross-collection joins, Atlas Search/vector stages,
  map-reduce, change streams, and writes** in the MongoDB adapter.

## Support policy

- The public `nl2data` API is the only supported application surface;
  `nl2data_core` is contributor-only and changes without notice.
- Optional sibling distributions (`nl2data-openai`,
  `nl2data-semantic-catalog-postgres`, `nl2data-admin-service`,
  `nl2data-postgres`, `nl2data-mongodb`, `nl2data-memory-redis`) are supported through their
  documented package surfaces; their internals change without notice.
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
