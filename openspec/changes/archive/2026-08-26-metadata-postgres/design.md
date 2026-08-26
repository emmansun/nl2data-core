## Context

PostgreSQL metadata discovery already exists in `nl2data_core.adapters.sql.discovery`, while the generic SQL adapter currently contains SQLite execution and PostgreSQL dialect plumbing. The goal is package productization: isolate `psycopg`, complete the PostgreSQL execution path, and preserve the core's safe discovery and governed query contracts without duplicating semantic inference or Bundle lifecycle logic.

## Goals / Non-Goals

**Goals:**

- Publish `nl2data-postgres` as an optional backend integration package.
- Move/reuse PostgreSQL-specific connection, schema inspection, SQL execution, bounds, timeout, and error normalization behind the package boundary.
- Preserve read-only identity, allowlists, tenant/source context, safe metadata, canonical fingerprints, and partial/bounded semantics.
- Implement PostgreSQL read-only SQL execution through the core `QueryAdapter` contract and protect result values/bounds.
- Support independent package tests and PostgreSQL service integration.

**Non-Goals:**

- Redesigning `MetadataSnapshot`, discovery protocols, or backend-neutral query contracts.
- Implementing semantic inference, proposal review, Bundle storage, admin service, or query execution.
- Adding PostgreSQL dependencies to base `nl2data` imports.

## Decisions

### Keep core models, protocols, and governance authoritative

The package imports core discovery, adapter, IR, governance, and result models. It returns `MetadataSnapshot` and implements the generic `QueryAdapter` contract; it must not define a second metadata or authorization model.

### Lazy driver and explicit configuration

`psycopg` is an optional package dependency and loads only when the discoverer/client is constructed or used. DSNs come from host/environment secret injection; configuration is typed, bounded, and never included in snapshots or errors.

### Preserve PostgreSQL facts and execution semantics

Inspect configured schemas/tables, columns, types, primary/foreign keys, and bounded protected statistics. Execute only validated read-only SQL with statement timeout and result limits. Apply allowlists and object/field/statistics/time limits before unbounded work. Normalize database failures into core discovery/execution outcomes.

### Package-local tests plus root integration

Unit/contract/security tests live under the package. Cross-package and full metadata-to-Bundle tests remain under root `tests/`; CI runs both.

## Risks / Trade-offs

- [Moving code breaks existing imports] → Keep a temporary compatibility export and test both paths before deprecation.
- [Driver loads during base import] → Add `sys.modules` import-boundary tests and lazy import checks.
- [Schema inspection or query execution leaks sensitive values] → Reuse safe projections, allowlists, bounds, read-only connections, and redaction tests.
- [PostgreSQL versions differ] → Support the tested PostgreSQL 16 profile first and document compatibility.

## Migration Plan

1. Add package metadata and optional psycopg dependency.
2. Extract/adapt the current discoverer and implement PostgreSQL query execution behind the package API.
3. Preserve or deprecate the old in-core import with a compatibility test.
4. Add package tests and PostgreSQL integration tests; update CI/docs/build artifacts.
5. Switch host examples to the package import, then remove the compatibility shim in a later breaking release.

## Open Questions

- Should the compatibility shim remain for one minor release or until the next major version?
- Should PostgreSQL discovery and execution support multiple database schemas in the first package release?
