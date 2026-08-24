## 1. Semantic View Contracts

- [x] 1.1 Define immutable bounded models for view definitions, semantic descriptors, member restrictions, allowed purposes, operations, aggregations, relationships, result shapes, and provenance.
- [x] 1.2 Define trusted resolution context containing tenant scope, principal authorization fingerprint, purpose, policy/catalog fingerprints, adapter capabilities, model version, and feature flags.
- [x] 1.3 Implement canonical serialization and SHA-256 fingerprinting for view definitions and resolved projections without raw identity claims, credentials, physical bindings, or hidden policy rules.
- [x] 1.4 Define safe structured resolution outcomes and denial/stale/missing-member issue codes.

## 2. View Resolution and Projection

- [x] 2.1 Implement a bounded view registry/resolver that applies tenant, principal, purpose, policy, capability, model/catalog, and feature-flag constraints.
- [x] 2.2 Implement immutable authorized projections that expose only permitted semantic members and safe descriptions.
- [x] 2.3 Enforce fail-closed behavior for missing/inactive tenant scope, unauthorized purpose/principal, unavailable view, stale catalog/model, and unsupported capabilities.
- [x] 2.4 Add explicit unbound-IR compatibility behavior when no view registry is configured, without fabricating view identity.

## 3. IR, AI, Workflow, and Memory Integration

- [x] 3.1 Extend `SemanticQueryIR` view references/provenance and validate member references against the current resolved projection.
- [x] 3.2 Assemble model-provider context only from the authorized view projection and exclude physical metadata, credentials, restricted members, and hidden policy details.
- [x] 3.3 Record resolved-view identity/fingerprint in workflow evidence and checkpoint compatibility metadata; reject stale view checkpoints before adapter execution.
- [x] 3.4 Revalidate recalled semantic references against the current resolved-view identity/fingerprint before reuse.
- [x] 3.5 Preserve current governance, tenant, authorization, result-protection, and unbound-IR fallback behavior.

## 4. Verification and Documentation

- [x] 4.1 Add unit tests for model bounds, canonical fingerprints, projection immutability, and safe serialization.
- [x] 4.2 Add contract tests for positive resolution, include/exclude rules, operations, aggregations, relationships, result shapes, and capability restrictions.
- [x] 4.3 Add security tests for cross-tenant access, principal/purpose denial, client-hint non-authority, hidden metadata leakage, and excluded IR members.
- [x] 4.4 Add integration tests proving stale view/IR/workflow/Memory evidence fails closed before provider or adapter execution.
- [x] 4.5 Update README and internal specifications with view binding, legacy compatibility, and migration behavior; run pytest, Ruff, and Mypy.
