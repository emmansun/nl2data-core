# nl2data-workflow-postgres

Optional PostgreSQL-backed durable workflow state backend for `nl2data-core`.

## Overview

This package provides a production-ready PostgreSQL implementation of the core workflow state-store contracts (`StateStore`, `IdempotencyStore`, `WorkflowLeaseStore`, and `FencedStateStore`) from `nl2data-core`. It persists safe workflow snapshots, idempotency records, and execution leases in PostgreSQL while keeping `psycopg` lazy and optional.

Base `nl2data` / `nl2data-core` imports remain PostgreSQL-free; the driver is loaded only when a `PostgreSQLStateStore` is constructed.

## Installation

```bash
pip install nl2data-workflow-postgres
```

This also installs `psycopg[binary,pool]>=3.1,<4` and `nl2data-core>=0.1.0`.

## Quick Start

```python
from nl2data_core.workflow import WorkflowState
from nl2data_workflow_postgres import PostgreSQLStateStore, WorkflowPostgresConfig

store = PostgreSQLStateStore(
    dsn="postgresql://user:pass@localhost/db",
    config=WorkflowPostgresConfig(namespace="workflow"),
)

# store.create(state), store.update(...), store.acquire_lease(...), etc.
```

## Public surface

```python
from nl2data_workflow_postgres import (
    MIGRATIONS,
    SUPPORTED_SCHEMA_VERSION,
    SQL_TEMPLATES,
    PostgreSQLStateStore,
    WorkflowPostgresConfig,
    build_pool,
    driver_available,
)
```

- `PostgreSQLStateStore` — the core store-contract implementation
  (`StateStore`, `IdempotencyStore`, `WorkflowLeaseStore`, `FencedStateStore`)
  with compare-and-set updates and fenced lease checks.
- `WorkflowPostgresConfig` — bounded pool/timeout/TTL configuration with a
  derived tenant-scope namespace.
- `MIGRATIONS` / `SUPPORTED_SCHEMA_VERSION` — versioned DDL in a package-owned
  schema namespace.
- `build_pool` / `driver_available` — lazy psycopg pool construction and
  driver availability probes that never import the driver.

## Compatibility

The in-core `nl2data_core.workflow.postgres_*` modules were removed. All
PostgreSQL workflow state ships from this package; import from
`nl2data_workflow_postgres` directly. The core distribution no longer ships a
PostgreSQL workflow backend.

## Features

- **Durable workflow state** with versioned safe snapshots and compare-and-set semantics
- **Idempotency records** with reservation, conflict detection, and terminal outcome references
- **Lease ownership** with monotonic fencing tokens, renewal, release, and stale-worker takeover
- **Tenant-scope isolation** via opaque fingerprints and derived schema namespaces
- **Versioned migrations** in a package-owned schema namespace
- **Normalized errors** without leaking DSNs, credentials, or raw backend text
- **Lazy driver loading** so the base framework never imports `psycopg`

## Development

Install in editable mode from the repository root:

```bash
pip install -e packages/nl2data-workflow-postgres
```

Run package tests:

```bash
pytest packages/nl2data-workflow-postgres/tests -q
```
