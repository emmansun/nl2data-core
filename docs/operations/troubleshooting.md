# Troubleshooting

> **Reader**: operators and developers. **Prerequisites**:
> [Service configuration](services.md),
> [Error codes](../reference/error-codes.md).

## Unavailable services

| Symptom | Likely cause | Action |
| --- | --- | --- |
| `STORE_UNAVAILABLE` / `STORE_TIMEOUT` (PostgreSQL) | Service unreachable, pool timeout, or driver missing | Check service health (`pg_isready`), network, and that the `nl2data-workflow-postgres` package is installed; the error is retryable |
| `LEASE_BUSY` | Another worker holds the lease | Wait for lease expiry (TTL + clock tolerance) or investigate the owner; never force-kill the owner's commits |
| `MEMORY_UNAVAILABLE` | Redis unreachable or timed out | Check `NL2DATA_REDIS_URL`, Redis health (`redis-cli ping`); the workflow degrades statelessly — Memory is optional |
| `MONGO_UNAVAILABLE` / `METADATA_UNAVAILABLE` | PyMongo missing or MongoDB unreachable | Install the `nl2data-mongodb` package; check `NL2DATA_MONGO_URI` and `mongosh ping` |
| Tests report `skipped` | Driver or service unavailable | Expected for real-service profiles; surface reasons with `-rs`. A skip is never a pass — do not treat it as verification |

## Semantic catalog errors

| Symptom | Likely cause | Action |
| --- | --- | --- |
| `CATALOG_UNAVAILABLE` / `CATALOG_TIMEOUT` | Catalog service unreachable, pool acquisition timeout, or `psycopg` not installed | Install `nl2data-semantic-catalog-postgres`; check `pg_isready` and the injected DSN; the error is retryable |
| `SCHEMA_MISMATCH` | Runtime is older than the catalog database schema | Upgrade the runtime or align the deployment; newer schema versions fail closed, never silently downgraded |
| `ENVELOPE_REJECTED` / `FINGERPRINT_MISMATCH` / `BOUNDS_EXCEEDED` | Tampered, mislabeled, or oversized persisted artifact | Fail-closed by design; re-register the artifact from trusted metadata — never bypass validation |
| `UNAUTHORIZED` | Cross-scope access attempt | A scope fingerprint never reads another scope's records; verify the tenant/source scope |
| `CONFLICT` / admin `conflict` | Stale assembly `draft_revision` or serialization collision | Reload the current draft and retry against its revision; do not overwrite newer review decisions |
| `version_exists` | Business version already names different semantic content | Choose a new business version; identical semantic content is reused by fingerprint and needs no duplicate publication |
| `pending_assertions` / `draft_not_approved` | Publish attempted before assertion review and draft approval completed | Review every pending/invalidated assertion, approve the current revision, then publish that revision |
| `manifest_mismatch` / `audit_mismatch` | Publish artifacts do not bind to the emitted Bundle fingerprint | Treat as an atomic publish rejection; inspect emitter/verifier integration and retry only after correction |
| `verification_evidence_required` / `verification_evidence_mismatch` | Production evidence is absent, non-passing, stale, or bound to different plan/runner/executor/scope identities | Follow the [Verification Suite runbook](verification-suite.md), rerun the exact approved inputs, and never downgrade silently |

## Semantic authoring errors

| Symptom | Likely cause | Action |
| --- | --- | --- |
| `incompatible_schema` | Missing or unsupported authoring `apiVersion`/`kind` | Use `nl2data.io/semantic-assembly-authoring/v1alpha1` and `SemanticAssembly`; do not submit an internal draft envelope |
| `unsupported_yaml` | Duplicate/non-string key, merge key, custom tag, cyclic alias, or non-finite number | Remove the unsupported YAML feature; keep the document inside the documented safe subset |
| `structure_limit` / `input_too_large` | A parser, scalar, collection, alias, depth, or byte bound was exceeded | Split or simplify the semantic model; do not raise limits without reviewing the trust boundary |
| `invalid_reference` | Relationship, measure, grain, source, or calculated-field reference does not resolve | Follow the diagnostic `$` path and one-based location; define one unique target before importing |
| `unsafe_content` | Description or deployment binding resembles executable or credential material | Keep descriptions semantic-only and use an unresolved `env:`, `vault:`, or `file:` reference |
| `unsupported_export` | Draft has review state, edits, unsupported assertions, or lacks authoring-origin metadata | Export the original authoring model or return the draft to a losslessly representable revision-zero state |

## Stale snapshots and drift

| Symptom | Likely cause | Action |
| --- | --- | --- |
| `snapshot_stale` / `bundle_stale` / `catalog_stale` | The catalog changed after the snapshot was taken | Re-run discovery to register a fresh snapshot, then re-activate under the activation policy |
| `STALE_CHECKPOINT` on resume | The resolved view or bundle changed since the checkpoint | Resume is refused before any adapter execution; re-submit the request under the current view |
| Activation rejected with `blocking_drift` | Referenced objects/fields/constraints changed | Review the drift report, re-discover, and re-activate; use a scoped `DriftOverride` only for one explicit decision |
| Expired active snapshot stops resolving | Retention window passed | Run a fresh discovery and activate the replacement; `cleanup_expired` never keeps expired evidence authoritative |

## Lease and fencing conflicts

- `FENCING_REJECTED` after takeover is **correct behavior**: a stale
  owner can never commit. Do not retry the stale owner — resume through
  the current owner.
- Ambiguous post-execution states (external work finished but terminal
  persistence fenced out) are surfaced for reconciliation. Reconcile
  against the external system before retrying; never silently replay or
  claim success.
- Check clock skew: lease takeover requires expiry plus
  `clock_tolerance_seconds` (default 2 s). Large skew between workers
  causes premature takeovers or delayed recovery.

## Provider errors

| Symptom | Likely cause | Action |
| --- | --- | --- |
| `MODEL_INVOCATION_FAILED`, non-retryable (`model_code` = `INVALID_REQUEST`) | Authentication/configuration failure | Check the API key, model name, and base URL; keys are injected at client build time — a changed environment requires a new client |
| `MODEL_INVOCATION_FAILED`, retryable (`model_code` = `MODEL_TIMEOUT` / `PROVIDER_UNAVAILABLE`) | Timeout, connection, rate limit, transient service error | Retry with the resolver budget; check `OPENAI_TIMEOUT_SECONDS` and gateway status |
| `MODEL_INVOCATION_FAILED` (`model_code` = `RETRY_EXHAUSTED`) or `RETRY_EXHAUSTED` | Attempt budget consumed | Reduce load, raise the bounded budget, or investigate provider stability |
| Every live case `skipped` | No credentials injected | Provide `api_key_resolver`/`client_factory` or `OPENAI_API_KEY`; `skipped` is never verification |

## Import-boundary issues

| Symptom | Likely cause | Action |
| --- | --- | --- |
| `ModuleNotFoundError: nl2data_core` at application runtime | Application imported the internal package directly | Import only `nl2data`; compose through `CompositionProfile`. `nl2data_core` is contributor-only |
| Optional driver imported at package import | Wrong import style | Drivers must be imported lazily at client build time; import-boundary tests (AST + `sys.modules`) enforce this |
| `ASYNC_REQUIRED` from `facade.query()` | Called inside an active event loop | Use `await facade.aquery(request)` — the sync convenience never runs inside a loop |
| `ENGINE_NOT_READY` / `ENGINE_DRAINING` / `ENGINE_CLOSED` | Query before initialize, during drain, or after close | Follow the lifecycle: `initialize()` → queries → `drain()`/`close()`; both are idempotent |
| `NOT_CONFIGURED` outcome | No executable runtime bound | Bind a `WorkflowRuntimePort` or the deterministic composition parts; an empty profile is the safe fallback, not a bug |
| `DUPLICATE_REQUEST` / `IDEMPOTENCY_CONFLICT` | Request id reused | Use a fresh `request_id` per logical request, or reuse the same id deliberately for idempotent replay |
| `UNSUPPORTED_SCHEMA_VERSION` | Runtime older than the deployment schema/config | Upgrade the runtime or align the deployment; downgrading is a deployment decision, never automatic |

## Multi-entity join planning errors

| Symptom | Likely cause | Action |
| --- | --- | --- |
| `JOIN_PATH_NOT_FOUND` | Required entities are not connected by any authorized `RelationshipEdge` | Add the missing relationship to the `RelationshipGraph` for the source, or restrict the view to connected entities |
| `JOIN_PATH_AMBIGUOUS` | Multiple shortest paths exist between the required entities | Keep a single authoritative relationship per entity pair; if both are needed, restrict the view's `allowed_relationships` or split the source into separate relationship graphs |
| `JOIN_EDGE_UNAUTHORIZED` | The `RelationshipGraph` contains a relationship not in the view's `allowed_relationships`, or an entity outside `root_entity_ids` | Update the authorized view to include the relationship/entity, or remove it from the graph |
| `MULTI_ENTITY_UNSUPPORTED` | A multi-entity intent was resolved but no `JoinPlanner` is bound to the runtime | Bind a `JoinPlanner` with the current `RelationshipGraph` and `AuthorizedView`, or route multi-entity requests through a feature-gated path |

Relationship graph authoring tips:

- Keep edges governed: every `RelationshipEdge` must be explicitly authorized via `allowed_relationships` in the view.
- Prefer shortest unique paths: the planner deterministically picks the shortest path and rejects ambiguity.
- Use stable identifiers: `edge_id` and `relationship_id` are part of the graph fingerprint; changing them invalidates cached plans.
- Never put physical table/column names in the graph; the compiler maps semantic entity/field ids through the `PhysicalBinding`.

## Documentation checks

Run the documentation quality gates locally:

```powershell
python scripts/check_docs.py
```

Failures mean broken repository links, malformed Mermaid blocks, secret
patterns, or bilingual navigation problems — fix the documentation, not
the check.

## Still stuck?

- Read the [error codes](../reference/error-codes.md) reference and the
  relevant [architecture](../architecture/overview.md) page for the
  boundary involved.
- Check whether the profile is deterministic (no service), real-service
  (service containers), or live-provider (credentials) — each has
  different failure semantics.
- Real-service profiles skip explicitly when the service is absent;
  `skipped` is never a pass, so verify the service actually ran.
