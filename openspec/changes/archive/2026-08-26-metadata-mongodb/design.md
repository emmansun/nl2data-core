## Context

MongoDB metadata discovery and governed query execution already exist across `nl2data_core.adapters.mongodb`. The goal is package productization: isolate `pymongo`, provide an independently installable backend integration, and preserve MongoDB's dynamic-schema, validation, and incomplete-observation semantics.

## Goals / Non-Goals

**Goals:**

- Publish `nl2data-mongodb` as an optional backend integration package.
- Move/reuse MongoDB-specific client, collection/path inspection, pipeline execution, bounds, timeout, and error normalization behind the package boundary.
- Preserve read-only discovery, allowlists, tenant/source context, safe metadata, canonical fingerprints, and observed/inferred trust markers.
- Preserve governed MongoDB query execution, pipeline validation, result bounds, protected result mapping, and normalized errors.
- Support independent package tests and MongoDB service integration.

**Non-Goals:**

- Redesigning `MetadataSnapshot`, discovery protocols, or backend-neutral query contracts.
- Treating bounded observations as a complete MongoDB schema.
- Implementing semantic inference, proposal review, Bundle storage, admin service, or query execution.
- Adding MongoDB dependencies to base `nl2data` imports.

## Decisions

### Keep core models, protocols, and governance authoritative

The package imports core discovery, adapter, IR, governance, and result models. It returns `MetadataSnapshot` and implements the generic `QueryAdapter` contract; MongoDB-specific detail remains inside the package and is represented through common facts plus explicit completeness/trust metadata.

### Lazy driver and explicit configuration

`pymongo` is an optional package dependency and loads only when the discoverer/client is constructed or used. URIs come from host/environment secret injection; configuration is typed, bounded, and never included in snapshots or errors.

### Preserve dynamic-path and execution semantics

Inspect only allowed collections and bounded document structure. Emit canonical dotted paths without raw document values; mark observations as observed or inferred and incomplete when sampling cannot establish a full schema. Execute only validated read-only pipelines with result bounds. Normalize unavailable, unauthorized, malformed, and bounds failures.

### Package-local tests plus root integration

Unit/contract/security tests live under the package. Cross-package and full metadata-to-Bundle tests remain under root `tests/`; CI runs both.

## Risks / Trade-offs

- [Moving code breaks existing imports] → Keep a temporary compatibility export and test both paths before deprecation.
- [Driver loads during base import] → Add `sys.modules` import-boundary tests and lazy import checks.
- [Sampling or query results leak data] → Make incomplete status explicit, enforce pipeline/result bounds, and test protected mapping.
- [MongoDB deployments differ] → Support the tested MongoDB 7 profile first and document compatibility.

## Migration Plan

1. Add package metadata and optional pymongo dependency.
2. Extract/adapt the current discoverer, query adapter, and configuration behind the package API.
3. Preserve or deprecate the old in-core import with a compatibility test.
4. Add package tests and MongoDB integration tests; update CI/docs/build artifacts.
5. Switch host examples to the package import, then remove the compatibility shim in a later breaking release.

## Open Questions

- Should the first package expose sampling strategy as configuration or keep the existing bounded strategy fixed?
- Should the compatibility shim remain for one minor release or until the next major version?
