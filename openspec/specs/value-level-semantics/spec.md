## Purpose

Define value-level semantics for the semantic layer: an optional, fingerprinted, field-level business-word to stored-value mapping that intent resolution honors deterministically before the canonical IR freezes, with bounded observable outcomes and fail-closed behavior everywhere semantics are absent or ambiguous.

## Requirements

### Requirement: Value semantics are a fingerprinted field-level asset
The system SHALL support an optional `ValueSemantics` member on each semantic field descriptor carrying a business-word to stored-value mapping, ordered display terms, non-binding sample values, a privacy flag, and an unknown-value policy. The member SHALL be immutable, bounded, and fully inside the descriptor fingerprint domain. The mapping direction SHALL be business word to stored value; the reverse direction (result interpretation) SHALL NOT be a semantic-layer responsibility.

#### Scenario: Value semantics attach to a field and enter the fingerprint domain
- **WHEN** a semantic field descriptor is defined with a `value_mapping` of business words to stored values
- **THEN** the mapping content changes the descriptor fingerprint and, through it, the catalog and bundle fingerprints

#### Scenario: Sample values never constrain SQL generation
- **WHEN** a field declares `sample_values` without a `value_mapping`
- **THEN** no SQL constraint, filter expansion, or enum-domain enforcement derives from the sample values

#### Scenario: Unknown values follow the declared policy
- **WHEN** value resolution encounters a filter value outside the declared `value_mapping`
- **THEN** the outcome follows `unknown_value_policy`: `reject` fails resolution with a structured error, and `warn` proceeds with a recorded warning

#### Scenario: An empty mapping is not silently set
- **WHEN** a `ValueSemantics` is provided with an empty `value_mapping`
- **THEN** validation fails rather than treating the member as set with no content

### Requirement: Value resolution completes before IR freeze
The system SHALL resolve enum-filter values against the declared `value_mapping` during intent resolution, before the canonical IR is built and frozen. Mapping lookups SHALL read the descriptor snapshot referenced by the active bundle (located by catalog fingerprint), never a live registry; an unavailable snapshot SHALL fail resolution closed. The IR fingerprint SHALL equal the final query semantics: no value rewriting SHALL occur after IR freeze. Deterministic lookup against a governed mapping SHALL be permitted and SHALL NOT be treated as planner construction; probabilistic value construction SHALL remain rejected.

#### Scenario: Business word resolves to the stored value
- **WHEN** a resolved filter references a business word present in the field's `value_mapping`
- **THEN** the frozen IR carries the stored value and compiles without further value interpretation

#### Scenario: Lookups are anchored to the bundle-referenced snapshot
- **WHEN** the active bundle anchors descriptor snapshot S1 and the registry has since evolved to a different mapping
- **THEN** resolution reads S1's mapping so the frozen IR stays consistent with the evidence's bundle fingerprint

#### Scenario: Unresolved business word fails closed with self-explaining context
- **WHEN** a filter value misses the mapping under `unknown_value_policy: reject`
- **THEN** resolution returns a structured failure with a stable error code, the attempted value, and the list of known business terms, and no IR is produced

#### Scenario: Fields without value semantics are untouched
- **WHEN** a filter targets a field with no value semantics declared
- **THEN** resolution proceeds exactly as before this capability existed

### Requirement: Stored values pass through under controlled conditions
A filter value that is neither a declared business word nor a member of the declared mapping's stored values SHALL fail closed under the applicable policy. A filter value equal to a stored value in the declared mapping SHALL pass through unchanged. In v4.1, filters on fields with declared value semantics SHALL be limited to the `eq` and `in` operators; other operators on mapped fields SHALL be rejected with a structured operator error.

#### Scenario: Planner emits the correct stored value directly
- **WHEN** a filter value matches a stored value in the declared mapping
- **THEN** the IR carries it unchanged and resolution records a pass-through outcome

#### Scenario: Out-of-domain values still fail closed
- **WHEN** a filter value is neither a business word nor a stored value of the declared mapping
- **THEN** resolution fails under the declared policy without guessing

#### Scenario: Comparison-unsafe operators on mapped fields are rejected
- **WHEN** a filter applies an operator other than `eq` or `in` to a field with declared value semantics
- **THEN** resolution is rejected with a structured `VS_002` operator error naming the field, the attempted operator, and the allowed operators

#### Scenario: Membership checks are type-strict across the wire
- **WHEN** a filter value arrives over the wire with a stringified representation while the mapping declares a different scalar type
- **THEN** the value is explicitly canonicalized to the declared domain type before comparison and never silently coerced, and a value that cannot be uniquely canonicalized is treated as a miss

#### Scenario: Mixed IN lists resolve per value with pre-freeze dedup
- **WHEN** an `in` filter mixes business words and stored values and resolution produces duplicate stored values
- **THEN** each value resolves independently, duplicates are removed before the IR freezes, and a per-value miss under the reject policy fails the whole filter

### Requirement: Resolution outcomes are observable outside compilation evidence
The resolver SHALL produce a structured resolution outcome per filter value — hit, pass-through, warned, miss, or unpolicied — aggregated per filter occurrence, together with the fingerprint of the descriptor snapshot used, consumable by orchestration and evaluation layers. In evaluation scenarios the outcome SHALL attach to evaluation-layer evidence so attribution survives offline report reruns. The outcome SHALL NOT enter compilation evidence, which remains fingerprints-only.

#### Scenario: Warn policy is observable
- **WHEN** a warn-policy miss occurs
- **THEN** the outcome channel records a warned outcome and the IR proceeds

#### Scenario: Successful resolution is observable
- **WHEN** a filter resolves from the mapping or passes through as a stored value
- **THEN** the outcome channel records the hit or pass-through even though the only other artifact is the frozen IR itself

#### Scenario: The channel never reaches compilation evidence
- **WHEN** any resolution outcome is produced
- **THEN** compilation evidence contains no outcome records and remains limited to fingerprints

### Requirement: Unset optional semantic members are fingerprint-stable (N6)
An optional semantic member that is unset MUST be omitted from its owner's canonical payload, so that introducing the member cannot change the fingerprint of any descriptor, catalog snapshot, or bundle that does not use it.

#### Scenario: Adding an unset value semantics changes no fingerprint
- **WHEN** `ValueSemantics` support is introduced and a descriptor does not declare it
- **THEN** the descriptor fingerprint, the catalog snapshot fingerprint, and the bundle fingerprint are identical to their pre-introduction values

#### Scenario: Setting a value changes the fingerprint
- **WHEN** a descriptor declares `ValueSemantics` where none was set
- **THEN** the descriptor fingerprint changes, and through it the snapshot and bundle fingerprints change

### Requirement: Value-mapping edits are snapshot-breaking
Editing a declared value mapping SHALL change the catalog snapshot fingerprint such that bundles validated against the prior snapshot fail compatibility validation until republished against the new snapshot. The documented upgrade path SHALL include bundle republication, not only evidence re-audit.

#### Scenario: Editing a mapping invalidates the old snapshot binding
- **WHEN** a bundle built from a catalog snapshot is validated after a value-mapping edit produces a new snapshot
- **THEN** validation fails closed with a compatibility issue until the bundle is republished against the new snapshot
