## Why

Semantic authoring, lifecycle review, three-layer verification, publication, Admin orchestration, and durable persistence are now functionally complete, but their representations and coordinators have become tightly coupled. The current architecture contains an actual historical-evidence defect, an exact duplicate verification module, core dependency cycles, incomplete ports, and several thousand-line change hotspots; these must be reduced before adding more cross-cutting semantic features.

## What Changes

- Fix historical verification evidence so a published record is validated against an immutable frozen release binding rather than the current mutable `AssemblyDraft` row; later draft review/edit must not invalidate prior publication evidence.
- Introduce an immutable `PublicationAggregate`/`FrozenReleaseBinding` contract that groups Bundle, accepted manifest, verification evidence, audit, supersession identity, tenant/source scope, and approved draft/plan fingerprints at the atomic boundary.
- Replace the large `publish_assembly` parameter list and transaction script with a typed publication request/context and an ordered fail-closed gate pipeline whose stages have narrow inputs/outputs.
- **BREAKING (internal only)**: remove the duplicate `verification/runner.py` model implementation, preserve intentional compatibility imports if needed, and establish one canonical verification contract module.
- Establish acyclic semantic control-plane dependency rules: shared value contracts at the bottom; assembly lifecycle, verification, publication orchestration, and catalog ports above them; persistence/Admin depend inward through typed ports only.
- Define a complete typed lifecycle catalog port covering publication, lookup, verification evidence, audit, versions, activation, rollback, and state transitions; remove catalog/authorizer/emitter/verifier `Any` escape hatches from Admin service helpers.
- Split Admin orchestration into capability services (authoring, assembly lifecycle, verification/publication, metadata, bundle lifecycle) behind the existing `AdminService` compatibility facade and unchanged DTO behavior.
- Split PostgreSQL persistence into focused repositories for drafts, publications, evidence/audit, and activation/history over shared transaction, envelope, SQL execution, and error-normalization infrastructure; preserve one atomic publication transaction.
- Consolidate common Layer 2/3 verification mechanics: tagged scalar comparison, observation selection, preflight, status reduction, deadlines, shared execution cache, and layer aggregation.
- Consolidate repeated identifier/fingerprint constrained types and bounds where doing so does not change public serialization or validation messages.
- Add architecture conformance tests for import direction, duplicate-module detection, complete port usage, immutable publication reload, transaction boundaries, and agreed hotspot budgets.
- Freeze new cross-cutting feature development in this control-plane path until the defect and Now-phase boundaries are complete; this change adds no new business capability.

## Capabilities

### New Capabilities
- `semantic-control-plane-boundaries`: Acyclic dependency rules, immutable publication aggregate, typed ports, coordinator/repository boundaries, duplication controls, and architecture conformance budgets for the semantic control plane.

### Modified Capabilities
- `durable-semantic-catalog`: Verification evidence reload must use immutable publication-time release bindings and remain valid after the originating mutable draft evolves.

## Impact

- **Core**: `assembly`, `verification`, `bundles`, and new shared publication/control-plane contract modules; internal import paths may move with compatibility re-exports where justified.
- **Admin package**: internal service decomposition and complete dependency protocols; existing transport-neutral `AdminService` methods and DTO schemas remain compatible.
- **PostgreSQL catalog**: repository extraction and an additive frozen-release binding envelope/columns if existing immutable records are insufficient; no loss or rewrite of published artifacts.
- **Tests**: regression for publish → draft evolution → restart → historical evidence lookup, import-cycle/duplicate checks, protocol conformance, transaction tests, and complexity budget checks.
- **Operations/docs**: update package-boundary diagrams and contributor guidance; runtime semantics, Bundle fingerprints, tenant isolation, and publication outputs remain unchanged.
