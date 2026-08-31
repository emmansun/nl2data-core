# semantic-model-bundles Delta

## ADDED Requirements

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
- **WHEN** a calculated field is declared with a name equal to an existing field id of the same entity, or duplicating another calculated field's name
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
