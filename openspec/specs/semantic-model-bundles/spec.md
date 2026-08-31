# semantic-model-bundles Specification

## Purpose
Define versioned, immutable, validated Semantic Model Bundles and their safe catalog lifecycle, including reviewed metadata-discovery inputs.

## Requirements

### Requirement: Semantic Model Bundles are immutable and versioned
The system SHALL represent a Semantic Model Bundle as an immutable, versioned artifact containing bounded semantic entities, fields, relationships, measures/aggregation metadata, source/catalog references, compatibility metadata, and safe provenance. Bundle semantic facts MAY originate from approved discovery proposals, but a bundle SHALL contain no unapproved authority, credentials, connection strings, raw executable SQL/MQL/code, native objects, or authorization claims. The admin API SHALL expose only safe metadata and opaque references for Bundle content.

#### Scenario: Bundle has stable identity
- **WHEN** equivalent semantic model contents are constructed with different mapping insertion orders
- **THEN** the bundle produces the same canonical serialization and SHA-256 fingerprint

#### Scenario: Unsafe model content is rejected
- **WHEN** a bundle contains physical credentials, connection material, executable query text, native driver values, or unapproved semantic proposals
- **THEN** validation rejects the bundle before publication

### Requirement: Bundle structure and references are validated
Bundle validation SHALL enforce bounded collection sizes, unique entity/field/relationship identifiers, valid field types and aggregations, valid relationship endpoints, source identity matching the wrapped descriptor, semantic grain constraints, dependency references, self-dependency rejection, and supported schema/version compatibility. Declared catalog compatibility SHALL include the wrapped descriptor's catalog fingerprint when one is present, and approved discovery proposal references SHALL match the source snapshot fingerprint. API commands SHALL invoke this validation before publication or activation.

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
A bundle SHALL record safe provenance including bundle identity/version, source/catalog references, owner or origin references, compatibility information, quality status, and discovery proposal references. Authored, inferred, and human-approved semantic facts SHALL remain distinguishable, and inferred facts SHALL NOT become authorization decisions by themselves. API responses SHALL expose bounded provenance and host-provided audit references only.

#### Scenario: Inference is not authorization
- **WHEN** a bundle marks a relationship or description as inferred without trusted approval
- **THEN** the fact may be retained as metadata but cannot independently grant View visibility or execution authority

#### Scenario: Provenance is safe
- **WHEN** bundle provenance is serialized for evidence
- **THEN** it contains bounded opaque references and status metadata without raw identities, secrets, or physical source details

### Requirement: Catalog publication is atomic and replaceable
The system SHALL define a replaceable catalog protocol and implementations supporting immutable publish, lookup by bundle/version, active snapshot lookup, atomic activation, and rollback only to a previously published valid bundle. Publication SHALL validate before making a bundle available, activation SHALL never expose a partial bundle, and a shared implementation SHALL coordinate concurrent workers transactionally. The admin API SHALL delegate these decisions to the catalog and SHALL not implement a competing active pointer.

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
Bundle loaders SHALL validate schema version, model version, source identity, dependency fingerprints, source snapshot fingerprint, freshness, completeness, and compatibility constraints before returning or activating a bundle. Incompatible, stale, expired, unauthorized, or blocking-drift bundles SHALL fail closed and SHALL not be silently downgraded. A partial discovery snapshot SHALL not satisfy production activation by default. API activation and rollback commands SHALL surface these outcomes without weakening them.

#### Scenario: Stale dependency blocks activation
- **WHEN** a bundle depends on an unavailable or incompatible catalog/model fingerprint
- **THEN** loading or activation is rejected before a View can resolve against it

#### Scenario: Compatible snapshot permits activation
- **WHEN** a bundle source snapshot is fresh, authorized, complete, and has no blocking drift against its declared compatibility baseline
- **THEN** the bundle may be activated after normal Bundle validation

### Requirement: Catalog publication applies discovery drift policy
Bundle catalog publication SHALL validate source snapshot compatibility and production drift policy before making a bundle available, and activation SHALL never expose a partial or blocking-drift snapshot.

#### Scenario: Blocking drift remains inactive
- **WHEN** a candidate bundle is based on a snapshot with a blocking drift decision
- **THEN** the catalog rejects publication or activation and preserves the current active bundle

### Requirement: Semantic Views consume bundle snapshots
When bundle-backed catalog resolution is configured, Semantic Views SHALL resolve against an active validated Semantic Model Bundle snapshot, include the bundle identity/version/fingerprint in provenance, and invalidate when the active bundle changes. Descriptor-only resolution MAY remain as an explicit compatibility mode until the migration window ends. A worker loading from a shared catalog SHALL resolve only after active content has been revalidated. The admin API SHALL return references suitable for a host to construct or reload the authorized View, but SHALL not bypass View resolution.

#### Scenario: View binds active bundle
- **WHEN** a view resolves while bundle-backed catalog resolution is active
- **THEN** its projection references the active bundle identity/version/fingerprint and only members from that snapshot

#### Scenario: Bundle change invalidates view evidence
- **WHEN** the active bundle changes or is rolled back
- **THEN** a previously resolved view fingerprint is not treated as current for new IR planning or workflow resume

### Requirement: New optional semantic members are fingerprint-stable when unset
Introducing a new optional semantic member (value semantics, and later calculated fields or metrics) SHALL NOT change the fingerprint of any bundle whose contents do not declare the member. The member's content SHALL enter the fingerprint domain only when set.

#### Scenario: Bundles without value semantics keep their identity
- **WHEN** value-semantics support lands and an existing bundle's fields declare none
- **THEN** the bundle fingerprint and all bundle-derived fingerprints are unchanged

#### Scenario: Descriptor-level fingerprints feed snapshot compatibility unchanged
- **WHEN** a descriptor leaves the new member unset
- **THEN** its catalog fingerprint is unchanged and existing snapshot compatibility relationships hold

### Requirement: ValueSemantics content changes require republication against the new snapshot
A bundle whose descriptor adopts or edits any ValueSemantics content SHALL be republished against the resulting catalog snapshot; validation against a newer snapshot without republication SHALL fail closed with a compatibility issue. The documented upgrade path SHALL cover: content change, catalog snapshot fingerprint change, bundle republication, and stale-evidence re-audit.

#### Scenario: Old-snapshot bundle fails validation after a content change
- **WHEN** a descriptor's ValueSemantics content is edited and a bundle built from the prior snapshot is validated against the new snapshot
- **THEN** validation fails with a compatibility issue naming the snapshot mismatch

#### Scenario: Republication restores validity
- **WHEN** the bundle is rebuilt and republished against the new snapshot
- **THEN** validation succeeds and previously issued evidence for the old bundle identity is treated as stale

### Requirement: CalculatedField is an optional entity-level member with N6 fingerprint stability
Introducing `CalculatedField` as an optional entity-level descriptor member SHALL NOT change the fingerprint of any descriptor, snapshot, or bundle whose contents do not declare it: an unset member SHALL be omitted from the entity canonical payload entirely (never serialized as `null`). A set member SHALL be non-empty, immutable, bounded, JSON-wire safe, and fully inside the fingerprint domain; names SHALL be unique across the descriptor and SHALL NOT collide with any descriptor field id because IR references are not entity-qualified.

#### Scenario: Entities without calculated fields keep their identity
- **WHEN** calculated-field support lands and an existing entity declares none
- **THEN** the entity descriptor fingerprint, the catalog snapshot fingerprint, and all bundle fingerprints are byte-identical to their pre-introduction values

#### Scenario: Declaring a calculated field changes the fingerprint
- **WHEN** an entity declares its first calculated field
- **THEN** the descriptor fingerprint changes, and through it the snapshot and bundle fingerprints change

#### Scenario: A provided-but-empty member is rejected
- **WHEN** an entity provides an empty calculated-field member
- **THEN** validation fails rather than treating the member as set with no content

#### Scenario: Calculated-field names cannot shadow field ids
- **WHEN** a calculated field is declared with a name equal to any descriptor field id, or duplicating another calculated field's name
- **THEN** validation fails and the ambiguous namespace never exists

### Requirement: Pii declarations never cover fields referenced by calculated fields
Bundle validation SHALL enforce the governance-state direction of the calculated-field/pii isolation (bidirectional with the calculated-field definition-time rule): a `pii: true` declaration applied to a field already referenced by a declared calculated field of the same entity SHALL fail bundle validation with a structured `CF_004` error, regardless of which feature arrived first. The bundle SHALL not be publishable while the combination exists. Future field-masking policy targets SHALL join the same check when that policy model is introduced.

#### Scenario: Masking applied over a referenced field blocks publication
- **WHEN** a `pii` declaration is applied to a field that a declared calculated field references
- **THEN** bundle validation fails with a structured `CF_004` error and the bundle is not publishable until the calculated field is removed or the policy retargeted

#### Scenario: Either arrival order is rejected
- **WHEN** the calculated field and the pii declaration are introduced in either order across publications
- **THEN** the first publication containing the combination fails validation; the isolation rule is order-independent

### Requirement: CalculatedField content changes require republication against the new snapshot
A bundle whose descriptor adopts or edits any calculated-field content SHALL be republished against the resulting catalog snapshot; validation against a newer snapshot without republication SHALL fail closed with a compatibility issue. The documented upgrade path SHALL cover: content change, catalog snapshot fingerprint change, bundle republication, and stale-evidence re-audit. Queries referencing calculated fields additionally require adapter capability support before they can execute.

#### Scenario: Old-snapshot bundle fails validation after a content change
- **WHEN** a descriptor's calculated-field content is edited and a bundle built from the prior snapshot is validated against the new snapshot
- **THEN** validation fails with a compatibility issue naming the snapshot mismatch

#### Scenario: Republication restores validity
- **WHEN** the bundle is rebuilt and republished against the new snapshot
- **THEN** validation succeeds and previously issued evidence for the old bundle identity is treated as stale
