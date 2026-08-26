## Why

PostgreSQL metadata discovery and SQL adapter behavior are already present in the core repository, but the PostgreSQL connection and execution path are not yet a cohesive independently installable integration. A separately installable backend package will let applications opt into the complete PostgreSQL path without pulling database dependencies into the base runtime.

## What Changes

- Extract/package the existing PostgreSQL metadata discoverer and add the PostgreSQL SQL execution adapter as `nl2data-postgres`.
- Keep core-owned `MetadataDiscoverer`, `MetadataSnapshot`, fingerprint, trust, provenance, and safety contracts as the output boundary.
- Preserve read-only discovery, source allowlists, tenant authorization context, object/field/statistics bounds, timeouts, and normalized failures.
- Provide read-only PostgreSQL query execution through the core `QueryAdapter` contract, including PostgreSQL connection pooling, statement timeout, result bounds, protected scalar mapping, and normalized errors.
- Load `psycopg` lazily and keep it out of base `nl2data` imports.
- Provide package-level configuration, documentation, unit/contract tests, and PostgreSQL service integration coverage.
- Define migration/compatibility behavior for hosts moving from the in-core implementation to the package.

## Capabilities

### New Capabilities

- `postgres-backend-integration`: Independently installable PostgreSQL backend integration combining metadata discovery and governed SQL query execution.

### Modified Capabilities

None. The existing core discovery, public-boundary, and configuration requirements remain unchanged; this change adds an optional implementation package.

## Impact

Affected areas include a new `packages/nl2data-postgres` distribution, PostgreSQL client/configuration and execution code, packaging and CI, documentation, and integration tests. Existing core contracts remain compatible; no Bundle catalog, admin service, UI, or HTTP behavior changes are included.
