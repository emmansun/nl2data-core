## MODIFIED Requirements

### Requirement: Semantic Views are versioned and bounded
The system SHALL represent a Semantic View definition and an immutable resolved projection with bounded semantic entity, field, relationship, operation, aggregation, result-shape, purpose, and provenance metadata. When bundle-backed catalog resolution is configured, the projection SHALL be derived from a complete active validated Semantic Model Bundle snapshot and SHALL include bundle identity/version/fingerprint in safe provenance. A projection SHALL contain only semantic references and safe descriptions, never credentials, physical bindings, hidden policy rules, or native objects.

#### Scenario: View projection is safe
- **WHEN** a valid view is resolved from an active validated Semantic Model Bundle
- **THEN** the projection contains only permitted semantic members and safe versioned bundle provenance

#### Scenario: Unbounded view is rejected
- **WHEN** a view exceeds configured member, relationship, operation, description, or context-size limits
- **THEN** resolution fails with a structured bounded validation issue before provider or adapter work

### Requirement: Resolution is tenant-, principal-, and purpose-aware
The resolver SHALL require trusted resolution context and SHALL apply tenant scope, principal authorization scope, requested purpose, policy decision, model/catalog/bundle version, adapter capabilities, and feature flags before returning a projection. Client-supplied hints SHALL NOT establish access.

#### Scenario: Tenant mismatch fails closed
- **WHEN** a requested view is resolved with a missing, inactive, or mismatched trusted tenant scope
- **THEN** resolution is denied and no tenant-bound semantic members are returned

#### Scenario: Unauthorized purpose is denied
- **WHEN** a principal requests a view for a purpose not allowed by the view or trusted policy decision
- **THEN** resolution returns a safe denial without revealing excluded members or policy internals

### Requirement: Resolved views have stable security-bound fingerprints
A resolved view SHALL expose a stable fingerprint covering view identity/version, active bundle identity/version/fingerprint when configured, model or catalog fingerprint, tenant scope fingerprint, principal authorization fingerprint, purpose, policy fingerprint, adapter capability fingerprint, and feature flags. Raw identity claims and credentials SHALL never appear in the serialized projection or fingerprint payload.

#### Scenario: Equivalent resolution has stable identity
- **WHEN** equivalent resolution inputs are supplied in different mapping orders
- **THEN** the resolved projection and fingerprint are canonical and identical

#### Scenario: Security or bundle input change invalidates view
- **WHEN** tenant scope, principal authorization, purpose, policy, catalog, active bundle, capabilities, or feature flags change
- **THEN** the resolved-view fingerprint changes and old IR/workflow evidence is not treated as current

### Requirement: Only resolved members enter planning context
The planning and model-provider context SHALL be assembled from the resolved Semantic View projection and SHALL exclude fields, relationships, operations, physical metadata, credentials, and hidden policy rules not authorized by that projection.

#### Scenario: Excluded field cannot enter context
- **WHEN** a descriptor or bundle contains a restricted field excluded by view or policy resolution
- **THEN** the field is absent from provider context and an IR reference to it fails validation

#### Scenario: Capability restriction is enforced
- **WHEN** a view permits a semantic operation but the selected adapter capabilities do not support it
- **THEN** the operation is excluded or resolution fails safely before compilation

### Requirement: Semantic Query IR binds to the resolved view
A view-bound `SemanticQueryIR` SHALL carry the resolved-view identity and fingerprint, and compilers/workflows SHALL reject an IR whose view or active bundle is missing, stale, unauthorized, or inconsistent with its provenance. Explicitly unbound IR MAY use compatibility mode only when no view registry or bundle-backed catalog is configured; legacy plan models are not supported.

#### Scenario: Stale view cannot compile
- **WHEN** an IR references a resolved view or bundle fingerprint that differs from the current authorized projection
- **THEN** validation rejects the IR before physical compilation or adapter invocation

#### Scenario: Unbound IR compatibility remains explicit
- **WHEN** an existing unbound Semantic Query IR is executed without a configured view registry or bundle-backed catalog
- **THEN** it follows the documented unbound-IR compatibility path and does not fabricate a resolved-view or bundle identity
