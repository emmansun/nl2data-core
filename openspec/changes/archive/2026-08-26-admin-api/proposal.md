## Why

The core now has the metadata discovery, semantic proposal review, Bundle validation, and publish/activate/rollback contracts needed for a governed semantic data lifecycle, but there is no application-service control plane for operators and data stewards. An independent admin service package will make these existing capabilities automatable and reviewable while allowing each host application to choose its own HTTP, CLI, worker, or UI orchestration.

## What Changes

- Add an optional `nl2data-admin-service` package exposing a transport-neutral application service for semantic catalog administration.
- Provide service commands for bounded discovery, snapshot/proposal inspection, proposal approve/reject/revise, Bundle validation/publication, activation, active-version lookup, drift review, and rollback.
- Map service inputs and outputs to bounded core models and safe status/error contracts; never expose raw credentials, DSNs, prompts, queries, results, native clients, or unrestricted metadata values.
- Require trusted host authentication/authorization integration, tenant/source scope, operator audit references, idempotency, and safe concurrency behavior for mutating operations.
- Make long-running discovery and catalog operations job-oriented with status polling, bounded execution, cancellation where supported, and retry-safe behavior.
- Keep HTTP/CLI/UI adapters, authentication middleware, persistence implementation, and frontend UI outside `nl2data-core`; support the PostgreSQL semantic catalog package as an injected backend.
- Add service contract/schema documentation, public contract tests, security tests, and integration coverage for the control-plane lifecycle. HTTP/OpenAPI mapping is a host concern and is not required by this change.

## Capabilities

### New Capabilities

- `semantic-admin-service`: Authenticated, scoped, bounded application service for metadata discovery, proposal review, semantic Bundle lifecycle, drift decisions, and operational status.

### Modified Capabilities

- `public-library-conformance`: Define the admin service boundary, safe serialization, and compatibility expectations for host transport adapters without changing the Python facade boundary.
- `metadata-discovery-and-inference`: Define service/job orchestration semantics for discovery and proposal review while preserving provider-neutral, bounded, and non-authoritative inference rules.
- `semantic-model-bundles`: Define service behavior for publication, activation, active lookup, and rollback while preserving immutable artifacts and fingerprint/compatibility checks.
- `configuration-foundation`: Add strict optional admin service configuration and host-owned authentication references without persisting secrets.

## Impact

Affected areas include a new `packages/nl2data-admin-service` distribution, application-service commands and result schemas, dependency injection for semantic catalog and discoverer ports, authentication/authorization hooks, job execution, tests, CI, and operational documentation. No UI or HTTP adapter is included. No HTTP, authentication, or web framework dependency is added to `nl2data` or `nl2data_core`.
