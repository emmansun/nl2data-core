## Why

Semantic assembly now has authoring, lint, review, verification, publication, activation, and rollback gates, but the audit story remains scattered across lifecycle records, Verification Suite evidence, publish audit, and catalog persistence. This change creates a coherent, safe audit-evidence trail so hosts can explain what changed, who authorized it, what evidence was used, and which immutable publication it affects without reconstructing that story from internal objects.

## What Changes

- Define a bounded assembly audit-evidence trail that links authoring/import, lint summary, assertion review, approval, verification evidence, publish audit, activation, and rollback events.
- Add immutable audit-evidence summary models with stable event IDs, event kind, subject references, revision/fingerprint bindings, operator audit references, safe outcome/status, and predecessor links.
- Require publication-time audit evidence to bind the approved draft revision, accepted-assertion manifest, verification evidence, lint readiness reference when present, policy profile, tenant/source scope, separation-of-duties result, and immutable Bundle fingerprint.
- Extend Admin inspection operations so hosts can retrieve safe audit trails by draft, assertion, bundle fingerprint, publication, activation, or rollback reference.
- Extend durable catalog requirements so PostgreSQL persistence stores/reloads audit-evidence envelopes with the same safety, versioning, tenant/source isolation, and tamper detection as publication artifacts.
- Keep audit evidence outside semantic Bundle fingerprints and prevent audit inspection from exposing raw prompts, SQL/MQL, physical names, credentials, resolved deployment values, unrestricted sample values, native objects, or raw operator identities.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `semantic-assembly-lifecycle`: Add a coherent lifecycle audit-evidence trail across authoring, lint, review, approval, verification, publication, activation, and rollback.
- `semantic-admin-api`: Add safe audit-evidence inspection operations and DTOs for draft, assertion, publication, activation, and rollback history.
- `durable-semantic-catalog`: Persist and reload bounded audit-evidence envelopes atomically and safely with lifecycle/publication records.
- `semantic-control-plane-boundaries`: Ensure audit evidence crosses boundaries only through immutable contracts and remains outside semantic identity.

## Impact

- Affected code: core assembly lifecycle/audit models, publication aggregate contracts, catalog ports, in-memory catalog, PostgreSQL catalog repositories/envelopes, Admin DTO/schema/service methods, and focused tests.
- Affected APIs: new bounded audit-evidence inspection DTOs and possible public core models for audit trail entries and summaries.
- Dependencies: no new runtime dependency expected; use existing canonical JSON, fingerprint, bounded scalar, permission, and catalog envelope conventions.
- Systems: host UIs, CI, release pipelines, and operators can inspect release readiness and lifecycle provenance without querying unsafe internals or mutable drafts.