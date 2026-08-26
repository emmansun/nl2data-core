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

## Service Contract Versioning

The service exposes a versioned command/result schema via `nl2data_admin_service.schema.build_schema(contract_version)`. Host transports can introspect this schema to bind requests and responses without hard-coding DTO shapes.
