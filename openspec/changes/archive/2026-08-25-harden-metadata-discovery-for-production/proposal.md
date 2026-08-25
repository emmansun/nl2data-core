## Why

The metadata discovery and inference foundation is implemented, but it is not yet production-ready as an operational capability. Real source verification, snapshot lifecycle, drift policy, observability, and failure behavior must be hardened before discovered metadata can safely feed active Semantic Model Bundles and Views.

## What Changes

- Add a production metadata discovery profile with explicit source/tenant authorization, allowlists, sampling, timeout, concurrency, and sensitive-name controls.
- Add real PostgreSQL/SQL and MongoDB discovery integration coverage using CI service containers and deterministic seed schemas.
- Define snapshot lifecycle ownership, retention, freshness, partial-result handling, and safe persistence/activation rules.
- Define schema drift severity and blocking policy for Bundle activation, View resolution, and IR/workflow compatibility.
- Add discovery health, metrics-safe evidence, failure classification, and operational diagnostics without exposing DSNs or raw metadata values.
- Verify the complete discover → infer → approve → Bundle → View chain against real source snapshots.
- Preserve manual Bundle construction, fake/contract tests, and optional discovery behavior for adapters without metadata support.

## Capabilities

### New Capabilities

- `metadata-production-readiness`: Production operating rules, health evidence, drift policy, lifecycle, and real-source verification for metadata discovery.

### Modified Capabilities

- `metadata-discovery-and-inference`: Discovery SHALL enforce production authorization/bounds and explicit snapshot freshness/partial-result semantics.
- `semantic-model-bundles`: Bundle activation SHALL apply snapshot compatibility and drift blocking rules.
- `semantic-view-resolution`: View resolution SHALL reject stale or operationally invalid metadata snapshots.

## Impact

Affected areas include metadata discovery models/protocols, SQL/MongoDB discovery implementations, snapshot comparison and lifecycle, Bundle/View integration, CI integration workflow, and operational documentation. No new LLM provider, HTTP transport, distributed metadata registry, or automatic authorization is included.
