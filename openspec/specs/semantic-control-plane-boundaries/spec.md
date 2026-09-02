## Purpose

This capability pins the architectural boundaries produced by simplifying the semantic control plane: an acyclic dependency graph across contracts, assembly lifecycle, verification, publication orchestration, catalog ports, Admin adapters, and persistence; one immutable publication aggregate at the catalog boundary; narrow typed publication gate contracts; single canonical owners for control-plane contracts; complete typed Admin ports and capability-separated orchestration; focused PostgreSQL repositories over one transaction owner; shared verification evaluator mechanics; and enforceable architecture ratchets that preserve observable compatibility.

## Requirements

### Requirement: Semantic control-plane dependencies are acyclic
The semantic control plane SHALL enforce an explicit acyclic dependency graph across semantic contracts, assembly lifecycle, verification, publication orchestration, catalog ports, Admin adapters, and persistence implementations. Domain model modules SHALL NOT import repositories, Admin DTOs, database packages, or orchestration implementations. Catalog ports and implementations SHALL NOT depend on mutable `AssemblyDraft` models; publication-time draft facts SHALL cross the boundary only as immutable release contracts. CI SHALL fail on prohibited imports or newly introduced cycles.

#### Scenario: Import cycle is rejected in CI
- **WHEN** a core or package change introduces a dependency edge outside the approved control-plane graph or closes a cycle
- **THEN** an architecture conformance test fails with the source module, imported module, and violated boundary

#### Scenario: Optional packages depend inward only
- **WHEN** Admin or PostgreSQL catalog code consumes lifecycle/publication behavior
- **THEN** it imports complete core ports/contracts and core never imports the optional package implementation

### Requirement: Publication uses one immutable aggregate boundary
The publication coordinator SHALL construct an immutable `FrozenReleaseBinding` and `PublicationAggregate` from the approved semantic content before catalog persistence. The binding SHALL contain only bounded immutable publication-time identities required to validate Bundle, manifest, verification evidence, audit, tenant/source scope, approved draft revision, and approved verification plan. Catalog ports SHALL accept the aggregate rather than mutable drafts plus parallel optional arguments. The aggregate SHALL remain distinct from Bundle semantic identity and SHALL contain no credentials, raw assertions outside the accepted manifest, native objects, or unrestricted values.

#### Scenario: Mutable draft is absent from catalog publication
- **WHEN** a verified approved draft reaches the catalog boundary
- **THEN** the catalog receives one immutable publication aggregate and no `AssemblyDraft` object or independently nullable manifest/evidence/audit tuple

#### Scenario: Aggregate mismatch fails before persistence
- **WHEN** any Bundle, manifest, evidence, audit, scope, revision, or plan identity in the aggregate disagrees
- **THEN** aggregate construction or catalog validation fails closed and no partial publication record is written

### Requirement: Publication gates have narrow typed contracts
Publication SHALL be coordinated through a fixed ordered set of typed stages for freeze/authorization preconditions, Bundle emission and structural validation, Verification Suite validation, audit/aggregate construction, and atomic catalog persistence. Each stage SHALL consume and return bounded immutable contracts, SHALL NOT mutate prior stage values, and SHALL expose controlled issues rather than backend exceptions. The coordinator SHALL not duplicate domain validation owned by a stage and SHALL preserve short-circuit behavior before external work or persistence.

#### Scenario: Gate failure short-circuits later work
- **WHEN** an earlier publication stage rejects its input
- **THEN** later external verification and persistence stages are not invoked and the coordinator returns the bounded stage issues

#### Scenario: Existing publication behavior is preserved
- **WHEN** a previously valid compatibility or production publication is processed after decomposition
- **THEN** it produces the same Bundle, manifest, verification classification, audit semantics, idempotency result, and supersession behavior

### Requirement: Control-plane contracts have one canonical owner
Every public or cross-module control-plane model, enum, constrained scalar, fingerprint helper, bound, and protocol SHALL have one canonical implementation. Compatibility modules MAY re-export canonical symbols but SHALL NOT duplicate definitions or validation logic. Exact or semantically equivalent duplicate modules SHALL be rejected by CI, and import-boundary tests SHALL verify that public exports resolve to the canonical owner.

#### Scenario: Duplicate verification model module is removed
- **WHEN** callers import verification contracts through supported paths
- **THEN** all symbols resolve to the canonical verification model module and no second implementation can diverge

#### Scenario: Shared constrained values preserve wire behavior
- **WHEN** repeated identifier, fingerprint, and bounded-reference validation is consolidated
- **THEN** accepted/rejected inputs, JSON shape, error safety, and canonical fingerprints remain compatible

### Requirement: Admin dependencies are complete typed ports
The Admin service SHALL depend on complete capability-oriented protocols for metadata, authoring, draft lifecycle, verification/publication, and published Bundle lifecycle operations. Protocols SHALL declare every method the service invokes. Dependency resolution and helper return types SHALL NOT use `Any` or unchecked attribute discovery for these ports. Missing capabilities SHALL fail through explicit optional port checks without changing safe error behavior.

#### Scenario: Protocol implementation is statically complete
- **WHEN** a dependency implementation is assigned to an Admin capability port
- **THEN** static type checking verifies every invoked operation and incompatible or incomplete implementations fail before runtime

#### Scenario: Compatibility facade preserves API
- **WHEN** existing callers invoke `AdminService` methods and consume existing DTO schemas
- **THEN** the facade delegates to capability services and preserves method signatures, authorization checks, normalized errors, and serialized results

### Requirement: Admin orchestration is separated by capability
The Admin implementation SHALL separate metadata/discovery, authoring, assembly lifecycle, verification/publication, and published Bundle lifecycle orchestration into focused capability services. The existing `AdminService` SHALL remain a thin compatibility facade and SHALL not reimplement capability rules. Shared authorization, scope checks, error normalization, and DTO projection SHALL be centralized without introducing a service-locator or untyped dependency bag.

#### Scenario: Capability change has bounded impact
- **WHEN** verification or authoring behavior changes without changing public Admin DTOs
- **THEN** only its capability service, relevant core port, and focused tests require modification; unrelated metadata and Bundle lifecycle services remain untouched

### Requirement: Persistence repositories share one transaction owner
The PostgreSQL catalog implementation SHALL separate draft, publication aggregate, verification/audit, and activation/history persistence into focused repositories over shared SQL execution, envelope encoding, schema-version, and error-normalization infrastructure. One catalog unit-of-work SHALL own transactions spanning repositories. Repositories SHALL NOT commit independently during atomic publish, activation, or rollback and SHALL NOT reconstruct domain policy already validated by immutable contracts except for defense-in-depth binding checks.

#### Scenario: Publication remains one transaction after repository split
- **WHEN** any Bundle, manifest, verification, audit, version, or supersession write fails
- **THEN** the unit of work rolls back every repository write and exposes no partial publication

#### Scenario: Repository tests do not require full service orchestration
- **WHEN** draft or evidence persistence behavior is tested independently
- **THEN** the focused repository can be exercised through a bounded transaction fixture without constructing Admin or unrelated catalog capabilities

### Requirement: Verification evaluators share common mechanics
Layer 2 smoke and Layer 3 semantic evaluators SHALL share canonical tagged-scalar comparison, observation selection, preflight validation, deadline derivation, status reduction, execution-cache access, cleanup handling, and layer aggregation helpers. Layer-specific modules SHALL contain only their distinct assertion/contract semantics. Consolidation SHALL preserve all fail-closed statuses, evidence fingerprints, and value-redaction guarantees.

#### Scenario: Shared status semantics cannot drift
- **WHEN** the same unavailable, timed-out, cancelled, or failed observation is evaluated in Layer 2 and Layer 3
- **THEN** both layers apply the same canonical status and cleanup precedence rules

### Requirement: Architecture complexity uses enforceable ratchets
The repository SHALL maintain a versioned architecture conformance manifest for the semantic control plane. CI SHALL enforce prohibited dependency edges, absence of exact duplicate modules, complete typed ports, approved public compatibility imports, and non-increasing hotspot budgets for coordinator/repository file size and cross-domain imports. Budget increases SHALL require an explicit architecture decision and SHALL NOT be hidden by moving code into generated or excluded files. Initial refactoring targets SHALL reduce, not merely freeze, the current Admin service, publication coordinator, and PostgreSQL store hotspots.

#### Scenario: Complexity regression requires an explicit decision
- **WHEN** a change increases an enforced hotspot or coupling budget
- **THEN** CI fails unless the architecture manifest and an approved design record intentionally revise the budget with rationale

### Requirement: Refactoring preserves observable compatibility
Architecture simplification SHALL preserve supported public imports, Admin method and DTO schemas, AssemblyDraft and Bundle wire formats, semantic and evidence fingerprints, error codes, tenant/source isolation, verification policy outcomes, publication idempotency, and catalog reload behavior unless a separate change explicitly declares a migration. Compatibility tests and golden vectors SHALL run before and after each migration stage.

#### Scenario: Internal extraction is externally invisible
- **WHEN** coordinators, ports, repositories, or common evaluators are moved or split
- **THEN** existing public contract, fingerprint, conformance, security, and integration tests remain byte-for-byte or behaviorally compatible as applicable
