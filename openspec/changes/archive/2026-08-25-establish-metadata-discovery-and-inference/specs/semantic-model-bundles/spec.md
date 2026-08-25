## MODIFIED Requirements

### Requirement: Semantic Model Bundles are immutable and versioned
The system SHALL represent a Semantic Model Bundle as an immutable, versioned artifact containing bounded semantic entities, fields, relationships, measures/aggregation metadata, source/catalog references, compatibility metadata, and safe provenance. Bundle semantic facts MAY originate from approved discovery proposals, but a bundle SHALL contain no unapproved authority, credentials, connection strings, raw executable SQL/MQL/code, native objects, or authorization claims.

#### Scenario: Bundle has stable identity
- **WHEN** equivalent semantic model contents are constructed with different mapping insertion orders
- **THEN** the bundle produces the same canonical serialization and SHA-256 fingerprint

#### Scenario: Unsafe model content is rejected
- **WHEN** a bundle contains physical credentials, connection material, executable query text, native driver values, or unapproved semantic proposals
- **THEN** validation rejects the bundle before publication

### Requirement: Bundle structure and references are validated
Bundle validation SHALL enforce bounded collection sizes, unique entity/field/relationship identifiers, valid field types and aggregations, valid relationship endpoints, source identity matching the wrapped descriptor, semantic grain constraints, dependency references, self-dependency rejection, and supported schema/version compatibility. Declared catalog compatibility SHALL include the wrapped descriptor's catalog fingerprint when one is present, and approved discovery proposal references SHALL match the source snapshot fingerprint.

#### Scenario: Invalid relationship cannot publish
- **WHEN** a relationship references an entity that is not present in the bundle
- **THEN** bundle validation returns a structured issue and publication is rejected

#### Scenario: Unsupported bundle version is rejected
- **WHEN** a loader receives a bundle schema version that the runtime does not support
- **THEN** loading fails with an explicit incompatible-schema result and does not activate the bundle

#### Scenario: Stale proposal cannot publish
- **WHEN** an approved proposal was generated against a different metadata snapshot than the bundle source
- **THEN** bundle publication rejects it as stale before activation

### Requirement: Bundle provenance and trust metadata are preserved
A bundle SHALL record safe provenance including bundle identity/version, source/catalog references, owner or origin references, compatibility information, quality status, and discovery proposal references. Authored, inferred, and human-approved semantic facts SHALL remain distinguishable, and inferred facts SHALL NOT become authorization decisions by themselves.

#### Scenario: Inference is not authorization
- **WHEN** a bundle marks a relationship or description as inferred without trusted approval
- **THEN** the fact may be retained as metadata but cannot independently grant View visibility or execution authority

#### Scenario: Provenance is safe
- **WHEN** bundle provenance is serialized for evidence
- **THEN** it contains bounded opaque references and status metadata without raw identities, secrets, or physical source details

### Requirement: Catalog publication is atomic and replaceable
The system SHALL define a replaceable catalog protocol and a bounded reference implementation supporting immutable publish, lookup by bundle/version, active snapshot lookup, atomic activation, and rollback only to a previously published valid bundle. Publication SHALL validate before making a bundle available, and activation SHALL never expose a partial bundle.

#### Scenario: Invalid bundle remains inactive
- **WHEN** publication validation fails
- **THEN** the catalog does not publish or activate the bundle and returns structured issues

#### Scenario: Activation switches complete snapshots
- **WHEN** a valid bundle is activated
- **THEN** subsequent View resolutions observe the complete new snapshot, while existing evidence continues to identify the previous bundle fingerprint

#### Scenario: Rollback selects an immutable version
- **WHEN** an operator rolls back to a previously published valid version
- **THEN** the catalog changes the active pointer to that version without mutating either bundle artifact

### Requirement: Bundle loading and compatibility are explicit
Bundle loaders SHALL validate schema version, model version, source identity, dependency fingerprints, and compatibility constraints before returning a bundle. Incompatible or stale bundles SHALL fail closed and SHALL not be silently downgraded.

#### Scenario: Stale dependency blocks activation
- **WHEN** a bundle depends on an unavailable or incompatible catalog/model fingerprint
- **THEN** loading or activation is rejected before a View can resolve against it

### Requirement: Semantic Views consume bundle snapshots
When bundle-backed catalog resolution is configured, Semantic Views SHALL resolve against an active validated Semantic Model Bundle snapshot, include the bundle identity/version/fingerprint in provenance, and invalidate when the active bundle changes. Descriptor-only resolution MAY remain as an explicit compatibility mode until the migration window ends.

#### Scenario: View binds active bundle
- **WHEN** a view resolves while bundle-backed catalog resolution is active
- **THEN** its projection references the active bundle identity/version/fingerprint and only members from that snapshot

#### Scenario: Bundle change invalidates view evidence
- **WHEN** the active bundle changes or is rolled back
- **THEN** a previously resolved view fingerprint is not treated as current for new IR planning or workflow resume
