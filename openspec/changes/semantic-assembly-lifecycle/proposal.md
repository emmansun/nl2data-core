# semantic-assembly-lifecycle Proposal

## Why

Semantic assembly currently mixes discovery proposals, reviewed bundle inputs, immutable bundle artifacts, and activation state in a way that makes pre-publication lifecycle rules implicit. DDS-020 v1.1 defines the missing control-plane model: semantic facts should move through Draft -> Review -> Approved -> Published with stable assertion identity, review invalidation, publish-time fingerprints, audit evidence, and deployment bindings outside the semantic fingerprint domain.

This library has no external production users yet, so the change intentionally favors a clean semantic contract over preserving the current internal bundle fingerprint behavior. The main compatibility work is updating integration tests, demos, and documentation.

## What Changes

- Add a governed **semantic assembly lifecycle**: `DRAFT`, `REVIEW`, `APPROVED`, and `PUBLISHED`, with publish as the only point where a semantic bundle fingerprint becomes externally visible.
- Add `AssemblyDraft` / review workspace models containing deterministic `SemanticAssertion` records, provenance, review state, review bindings, deployment bindings, draft revision, and file `apiVersion` metadata.
- Add deterministic assertion identity and diff semantics: assertion IDs derive from identity semantics, while payload changes invalidate review and report as modifications when identity is stable.
- **BREAKING**: align bundle fingerprint semantics with DDS-020 by excluding provenance, review state, deployment bindings, audit metadata, and file-format metadata from the canonical semantic payload. Existing golden fingerprints, demos, and docs will be updated.
- **BREAKING**: revise publish semantics from duplicate-version conflict toward content-idempotent publish by semantic fingerprint, with explicit supersession metadata and separate activation pointer changes.
- Add publish audit records that are transactionally written with the immutable published bundle and version-chain update.
- Add lifecycle authorization hooks for Author, Reviewer, Approver, and Publisher roles. Core enforces the lifecycle invariants; hosts supply identity, authorization, and separation-of-duties policy.
- Extend the admin service surface so it exposes lifecycle-safe assembly/review/publish operations without bypassing core validation, review invalidation, fingerprint computation, publish atomicity, or audit generation.
- Preserve the completed `calculated-field-semantics` contracts: calculated fields are reviewed as complete semantic assertions, remain in the published semantic fingerprint domain, and produce safe projection content-hash anchors without exposing expression material.

## Capabilities

### New Capabilities

- `semantic-assembly-lifecycle`: the Draft -> Review -> Approved -> Published assembly workflow, `SemanticAssertion` identity/review model, review invalidation, optimistic draft concurrency, deployment binding separation, publish-time fingerprinting, publish audit records, lifecycle authorization hooks, and published-version supersession/rollback semantics.

### Modified Capabilities

- `semantic-model-bundles`: redefine published bundle identity as a semantic-payload fingerprint assigned at publish, exclude audit/provenance/deployment metadata from the semantic fingerprint domain, and clarify the boundary between pre-publication assembly drafts and immutable published runtime bundles.
- `metadata-discovery-and-inference`: adapt discovery proposals into assembly assertions, preserve trust/provenance as review metadata, support incremental rediscovery by assertion identity, and prevent unreviewed or stale assertions from becoming bundle authority.
- `durable-semantic-catalog`: persist assembly drafts, review state, immutable published bundles, publish audit records, supersession chains, active pointers, rollback history, and safe deployment binding references with atomic/idempotent publish semantics.
- `semantic-admin-api`: expose transport-neutral lifecycle operations for assembly draft creation, assertion-level review, approval, publish, activation, rollback, audit lookup, and version listing with host-provided authorization and safe bounded DTOs.

## Impact

- **Core code**: new lifecycle/assembly models and validators; canonical fingerprint helpers may need JCS/NFC normalization tightening; bundle model/catalog publication semantics will change.
- **Metadata pipeline**: proposal conversion becomes assertion/workspace assembly before published bundle emission.
- **Admin service**: DTOs and service methods will change from proposal/bundle-centric operations to assembly/review/publish lifecycle operations.
- **Durable catalog packages**: PostgreSQL catalog schema and repository operations will need new lifecycle artifacts, audit records, idempotency keys, supersession metadata, and deployment binding references.
- **Tests**: update unit/contract/security/integration tests and golden fingerprint expectations; add review invalidation, concurrency conflict, secret handling, publish atomicity, idempotency, and rollback tests.
- **Demo/docs**: update metadata-to-bundle guides, semantic-layer docs, admin-service docs, and use ADR-046 through ADR-052 for DDS-020 after calculated fields took ADR-045.