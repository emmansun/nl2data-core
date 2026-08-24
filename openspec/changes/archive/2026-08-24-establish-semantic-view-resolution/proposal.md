## Why

The canonical Semantic Query IR now provides a backend-neutral logical request, but it does not yet define which semantic members a planner or model provider is allowed to see. Without a resolved, purpose- and tenant-aware Semantic View, context selection can expose unauthorized fields or allow plans to reference semantic objects outside the caller's governed scope.

## What Changes

- Add versioned Semantic View definitions and immutable resolved-view projections.
- Resolve views using trusted tenant scope, principal authorization facts, purpose, model bundle/version, adapter capabilities, and feature flags.
- Support bounded entity/field include and exclude rules, aliases, operation/aggregation restrictions, relationship traversal, result-shape constraints, and domain clarification metadata.
- Produce stable view fingerprints and safe provenance evidence for planning, governance, workflow checkpoints, telemetry, and memory revalidation.
- Ensure planner/provider context contains only authorized semantic members and never exposes hidden physical metadata, credentials, or policy internals.
- Add compatibility behavior for existing IR paths when no Semantic View is configured, while failing closed when a requested view is explicitly unavailable or unauthorized.
- Add unit, contract, security, and integration tests for cross-tenant, purpose, principal, capability, stale-view, and excluded-member cases.

## Capabilities

### New Capabilities

- `semantic-view-resolution`: Versioned policy-resolved semantic views and bounded authorized projections for planning and execution.

### Modified Capabilities

- `canonical-semantic-query-ir`: Semantic Query IR view references SHALL bind to a resolved view identity/fingerprint before planning or compilation.
- `workflow-state-foundation`: Workflow compatibility evidence SHALL carry the resolved-view fingerprint when a workflow is view-bound.

## Impact

Affected areas include `src/nl2data_core/planning`, new semantic view models/resolver modules, AI context assembly, tenant/governance integration, workflow and Memory compatibility evidence, and security/conformance tests. Existing unscoped IR callers remain supported only when no view registry is configured; explicit view references always fail closed when unavailable or unauthorized. No complete Semantic Model DSL or model bundle publisher is included; view definitions consume bounded semantic descriptors supplied by the host or a later model-bundle capability.
