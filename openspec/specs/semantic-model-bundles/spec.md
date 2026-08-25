## ADDED Requirements

### Requirement: Semantic Model Bundles are immutable and versioned
The system SHALL represent a Semantic Model Bundle as an immutable, versioned artifact containing bounded semantic entities, fields, relationships, measures/aggregation metadata, source/catalog references, compatibility metadata, and safe provenance. A bundle SHALL contain no credentials, connection strings, raw executable SQL/MQL/code, native objects, or authorization claims.

#### Scenario: Bundle has stable identity

#### Scenario: Unsafe model content is rejected

### Requirement: Bundle structure and references are validated
Bundle validation SHALL enforce bounded collection sizes, unique entity/field/relationship identifiers, valid field types and aggregations, valid relationship endpoints, source identity matching the wrapped descriptor, semantic grain constraints, dependency references, self-dependency rejection, and supported schema/version compatibility. Declared catalog compatibility SHALL include the wrapped descriptor's catalog fingerprint when one is present.

#### Scenario: Invalid relationship cannot publish

#### Scenario: Unsupported bundle version is rejected

### Requirement: Bundle provenance and trust metadata are preserved
A bundle SHALL record safe provenance including bundle identity/version, source/catalog references, owner or origin references, compatibility information, and quality status. Authored, inferred, and human-approved semantic facts SHALL remain distinguishable, and inferred facts SHALL NOT become authorization decisions by themselves.

#### Scenario: Inference is not authorization

#### Scenario: Provenance is safe

### Requirement: Catalog publication is atomic and replaceable
The system SHALL define a replaceable catalog protocol and a bounded reference implementation supporting immutable publish, lookup by bundle/version, active snapshot lookup, atomic activation, and rollback only to a previously published valid bundle. Publication SHALL validate before making a bundle available, and activation SHALL never expose a partial bundle.

#### Scenario: Invalid bundle remains inactive

#### Scenario: Activation switches complete snapshots

#### Scenario: Rollback selects an immutable version

### Requirement: Bundle loading and compatibility are explicit
Bundle loaders SHALL validate schema version, model version, source identity, dependency fingerprints, and compatibility constraints before returning a bundle. Incompatible or stale bundles SHALL fail closed and SHALL not be silently downgraded.

#### Scenario: Stale dependency blocks activation

### Requirement: Semantic Views consume bundle snapshots
When bundle-backed catalog resolution is configured, Semantic Views SHALL resolve against an active validated Semantic Model Bundle snapshot, include the bundle identity/version/fingerprint in provenance, and invalidate when the active bundle changes. Descriptor-only resolution MAY remain as an explicit compatibility mode until the migration window ends.

#### Scenario: View binds active bundle

#### Scenario: Bundle change invalidates view evidence

### Requirement: Discovery proposals require explicit approval
Bundle publication MAY consume discovery-generated inputs only when every included proposal is explicitly approved against the compatible source snapshot. Published bundle provenance SHALL preserve the source snapshot and proposal evidence references; inferred or unreviewed facts SHALL remain non-authoritative.

#### Scenario: Unreviewed discovery input is rejected
- **WHEN** a bundle input contains a pending, rejected, revised, or snapshot-mismatched discovery proposal
- **THEN** the input is not eligible for active bundle publication
### Requirement: Bundle loading and compatibility are explicit
