## Why

MongoDB metadata discovery and query adapter behavior are already implemented and verified inside the core repository, but MongoDB's optional driver and dynamic-schema/execution semantics deserve an independently installable package boundary. A dedicated backend integration lets applications opt into the complete MongoDB path while keeping the base runtime free of database dependencies.

## What Changes

- Extract/package the existing MongoDB metadata discoverer and governed query adapter as `nl2data-mongodb`.
- Keep core-owned `MetadataDiscoverer`, `MetadataSnapshot`, fingerprint, trust, provenance, and safety contracts as the output boundary.
- Preserve collection allowlists, tenant authorization context, bounded document/path inspection, timeouts, normalized failures, and no-raw-value behavior.
- Provide governed MongoDB query execution through the core `QueryAdapter` contract, including client lifecycle, pipeline validation, result bounds, protected result mapping, and normalized errors.
- Preserve MongoDB-specific `observed`/`inferred` and incomplete-path semantics; discovery must not claim a complete schema from bounded observations.
- Load `pymongo` lazily and keep it out of base `nl2data` imports.
- Provide package-level configuration, documentation, unit/contract tests, and MongoDB service integration coverage.
- Define migration/compatibility behavior for hosts moving from the in-core implementation to the package.

## Capabilities

### New Capabilities

- `mongodb-backend-integration`: Independently installable MongoDB backend integration combining metadata discovery and governed query execution.

### Modified Capabilities

None. The existing core discovery, public-boundary, and configuration requirements remain unchanged; this change adds an optional implementation package.

## Impact

Affected areas include a new `packages/nl2data-mongodb` distribution, MongoDB client/configuration and execution code, packaging and CI, documentation, and integration tests. Existing core contracts remain compatible; no Bundle catalog, admin service, UI, or HTTP behavior changes are included.
