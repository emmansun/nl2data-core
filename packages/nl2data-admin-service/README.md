# nl2data-admin-service

Optional transport-neutral admin control-plane service for the nl2data-core metadata-to-Bundle lifecycle.

## Overview

This package exposes a framework-neutral application service that hosts can wrap with their chosen transport (HTTP, CLI, UI, etc.). It intentionally has no HTTP, web-framework, authentication, or database-driver dependencies, keeping `nl2data` and `nl2data_core` transport-free and embeddable.

## Installation

```bash
pip install -e packages/nl2data-admin-service
```

## Usage

```python
from nl2data_admin_service import AdminService, AdminServiceConfig
from nl2data_admin_service.auth import AuthContext, Permission

# Host provides dependencies and authentication context.
service = AdminService(dependencies, AdminServiceConfig())
context = AuthContext(
    operator_id="operator-1",
    tenant_scope_fingerprint="sha256:...",
    permissions=frozenset([Permission.SNAPSHOT_READ]),
)
snapshot = service.get_snapshot("sha256:...", auth_context=context)
```

## Design

- **Transport-neutral**: Service methods accept and return bounded Pydantic DTOs.
- **Host-owned authentication**: The host supplies a trusted `AuthContext`; the service fails closed when it is missing.
- **Tenant/source scoped**: Every read and mutation is scoped by the host-provided tenant/source fingerprint.
- **Safe projection**: DTOs expose only opaque identifiers, fingerprints, versions, and bounded provenance.
- **Idempotent**: Mutating commands require an idempotency key and expected fingerprints/revisions.

## Assembly lifecycle

The service projects the core lifecycle without reimplementing it:

1. `create_draft(...)` or `create_draft_from_proposals(...)` creates an
    `AssemblyDraft` in `draft` state. Draft DTOs expose state, assertion counts,
    safe payload hashes, provenance kinds, redacted deployment-reference schemes,
    and `draft_revision`, but no Bundle fingerprint.
2. `submit_draft_for_review(...)` moves the draft to `review`.
    `decide_draft_assertion(...)` approves, rejects, or edits one assertion; each
    mutation requires the current revision, and payload edits invalidate review.
3. `approve_assembly_draft(...)` freezes a fully reviewed draft in `approved`.
4. `publish_draft(...)` delegates verification, publish-time fingerprinting,
    accepted-assertion manifest derivation, audit creation, idempotency, and
    supersession to core/catalog. Direct `publish_bundle(...)` is rejected.
5. `activate_published_fingerprint(...)` and
    `rollback_published_fingerprint(...)` move the active pointer among immutable
    publications. `list_published_versions(...)` reports `available`, `active`,
    `superseded`, `deprecated`, and `retired` state with predecessor/successor and
    audit references.

Lifecycle mutations require both the matching `Permission` and a trusted
host-authorized Author, Reviewer, Approver, or Publisher role. Draft revision
conflicts return normalized `conflict`; missing roles return
`authorization_denied`. Publish outcomes contain bounded issue codes and safe
references only, never assertion payloads, resolved connections, or raw operator
identities.

## Service Contract Versioning

The service exposes a versioned command/result schema via `nl2data_admin_service.schema.build_schema(contract_version)`. Host transports can introspect this schema to bind requests and responses without hard-coding DTO shapes.
