# Production Readiness

> **Reader**: decision makers, operators, and platform engineers
> evaluating whether this library can serve a production workload.
> **Prerequisites**: [Capabilities and support](capabilities.md) and
> [Compatibility](compatibility.md).

## What "production" means here

Capability status uses four levels (see the [status vocabulary](../README.md#status-vocabulary)):

- **Implemented** — the feature exists in source.
- **Conformant** — the feature passes its deterministic conformance suite.
- **Verified** — the feature has passed a real-service or live-provider run.
- **Production Supported** — the feature is covered by a **deployment
  contract** and **operational guidance** in this documentation set.

A capability is production supported only when all four are true:
it exists, it is deterministic-conformant, it has been verified against
a real service, and this documentation set tells you how to run and
operate it. `skipped` or `unavailable` test outcomes are never
verification.

## Deployment contract

| Aspect | Contract |
| --- | --- |
| Application surface | Only the public `nl2data` API; `nl2data_core` is contributor-only and changes without notice |
| Python | 3.11, 3.12, 3.13 (CI matrix; `requires-python >=3.11`) |
| Configuration | `schema_version: 1`, fail-closed activation, deterministic fingerprints |
| Workflow state | SQLite (stdlib) always available; PostgreSQL with leases + fencing is `verified` |
| Memory | In-memory always available; Redis backend is `verified` |
| Query adapters | SQL (SQLite fixtures, PostgreSQL) and MongoDB are `verified` |
| Model provider | Deterministic fake provider built in; OpenAI structured-output provider is live-verified on demand |
| Services in CI | PostgreSQL 16, Redis 7, MongoDB 7, OpenAI-compatible gateways (live, opt-in) |

## Before going to production

1. **Pass the deterministic suites.** The mandatory entry gate for any
   optional backend is the conformance suite
   (`tests/conformance/test_workflow_runtime_conformance.py`,
   `tests/contract/test_backend_conformance.py`, plus the unit,
   contract, and security suites).
2. **Verify against real services.** Run the real-service integration
   profile (`tests/integration/test_*_real.py`) with your target
   service versions and confirm `verified` outcomes — skips are not
   evidence.
3. **Inspect the failure modes.** Read
   [Error codes](error-codes.md): configuration, governance,
   authorization, and validation errors are never retryable; adapter,
   telemetry, and workflow categories default to retryable. Post-execution
   ambiguity is surfaced for reconciliation, never silently replayed.
4. **Apply the secrets rules.** Credentials never enter models,
   fingerprints, workflow state, telemetry, or evidence. See
   [Secrets and live testing](../operations/secrets.md) for injection,
   cleanup, and rollback.
5. **Plan rollback explicitly.** Rollback never rewrites evidence:
   activating an older artifact invalidates evidence produced under the
   newer one, and stale checkpoints are rejected before any adapter
   execution. See [Compatibility](compatibility.md) for the migration
   policy.
6. **Understand the documented surface.** The public API is stable
   within the documented surface, but `nl2data_core` is not — pin your
   application to `nl2data` imports and re-run the import-boundary
   suites after upgrades.

## Operating the documented surface

- [Service configuration](../operations/services.md) — per-service
  settings, health checks, timeouts, retries, and failure
  classification for PostgreSQL, Redis, MongoDB, and OpenAI.
- [Secrets and live testing](../operations/secrets.md) — credential
  hygiene, environment injection, live evaluation profiles, cleanup,
  and rollback.
- [Troubleshooting](../operations/troubleshooting.md) — unavailable
  services, stale snapshots, lease/fencing conflicts, provider errors,
  and import-boundary issues.
- [Error codes](error-codes.md) — the machine-readable contract for
  monitoring and alerting.
- [Telemetry ports](../architecture/package-boundaries.md) — in-memory
  audit/telemetry sinks with stable, redacted payloads.

## Documentation quality gates

This documentation set is itself part of the deliverable and is
validated by `scripts/check_docs.py`, which runs in CI and must pass
before merge. It checks:

1. **Markdown and link validation** — every relative link resolves to
   an existing file, and every anchor target exists; broken repository
   links fail the run.
2. **Mermaid structure** — every `mermaid` block parses into an
   expected diagram shape (flowchart/sequenceDiagram) with balanced
   syntax; blocks that cannot be parsed structurally are reported.
3. **Secret-pattern scans** — documentation and examples must not
   contain tokens, DSNs, credentials, raw prompts, or raw
   provider/result payloads.
4. **Bilingual navigation** — every English page that has a Chinese
   translation links to it, every Chinese page links back to its
   English source, and translation status markers match the language
   table in the docs index.
5. **Smoke checks** — documented imports, the quickstart code path,
   package installation extras, and build commands are exercised so a
   documentation example can never silently drift from the code.

If you change documentation, run `python scripts/check_docs.py` locally
before committing (see [Local development](../development/local-development.md)).

## Next steps

- [Troubleshooting](../operations/troubleshooting.md) — what to do when
  something fails.
- [Compatibility](compatibility.md) — versioning and migration policy.
