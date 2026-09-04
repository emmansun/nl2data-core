## ADDED Requirements

### Requirement: Closed policy template registry with typed parameter schemas
The system SHALL provide a closed, code-owned registry of governance policy templates — `tenant-isolation`, `row-restriction`, `purpose-gating`, and `field-masking` — each with a fixed, typed, bounded parameter schema. Template names, parameter names, value kinds, and list bounds SHALL be validated against the registry; unknown template names, unknown parameter keys, missing required parameters, wrong value kinds, and out-of-bounds lists SHALL fail closed. The registry SHALL NOT be extensible by document content, configuration, or host input.

#### Scenario: Unknown template is rejected
- **WHEN** an authoring document declares a policy whose template name is not in the registry
- **THEN** validation fails with a source-located diagnostic before any draft is created

#### Scenario: Unknown or missing parameter is rejected
- **WHEN** a policy declaration omits a required parameter, supplies an unknown parameter key, or supplies a value of the wrong kind
- **THEN** validation fails with a diagnostic naming the template, the parameter, and the source location

#### Scenario: Parameter bounds are enforced
- **WHEN** a policy declaration exceeds any registry bound (declaration count per document, list lengths, scalar lengths)
- **THEN** validation fails with a bounds diagnostic and no partial expansion is produced

### Requirement: Template targets validate against document content before lowering
Each template parameter that references document content (entity, field, or entity.field references) SHALL resolve against the declared entities and fields; unresolved references SHALL fail closed with a source-located diagnostic. Expanded policy identities SHALL be unique within the document; duplicate identity SHALL fail closed rather than shadow an earlier declaration.

#### Scenario: Undeclared target reference fails with source location
- **WHEN** a policy declaration references an entity or field that the document does not declare
- **THEN** validation returns a diagnostic identifying the authoring path and source location of the unresolved reference

#### Scenario: Duplicate expanded identity is rejected
- **WHEN** two policy declarations expand to the same policy identity within one document
- **THEN** validation fails with a diagnostic naming the conflicting identity and both source locations

### Requirement: Expansion produces standard pending policy assertions deterministically
The system SHALL expand valid policy template declarations into ordinary policy `SemanticAssertion` records during lowering, using the existing type-specific identity rules, manual provenance, and pending review state. Expansion SHALL be deterministic and independent of YAML mapping order, comments, whitespace, and presentation formatting; equivalent documents SHALL produce identical assertion identities and payload hashes. Expanded assertions SHALL traverse the existing review, approval, verification, and publish gates without any template-specific path.

#### Scenario: Equivalent YAML expands identically
- **WHEN** two documents contain equivalent policy declarations but differ in key order, comments, whitespace, or bounded anchor/alias use
- **THEN** lowering produces identical ordered policy assertion identities and payload hashes

#### Scenario: Expanded assertions carry no lifecycle authority
- **WHEN** a valid document with policy declarations is lowered
- **THEN** every expanded policy assertion is pending with manual provenance, no review binding, and no fingerprint

### Requirement: Expanded policy identity is deterministic and target-derived
The expanded policy identity SHALL be derived deterministically from the template name and the template's identifying target references (entity and field, sorted purposes, or sorted field references). Value parameters that do not identify the target (such as claim, allowed values, effect, or replacement) SHALL be excluded from identity, so changing them preserves the assertion identity while changing its payload. The rendered dotted identity form SHALL be used only when it is injective; when the rendered identity would be ambiguous because identifying components contain the separator character, or when it would exceed the identifier bound, the system SHALL fall back to a deterministic bounded digest of the canonical identifying target.

#### Scenario: Value parameter change preserves identity
- **WHEN** a policy declaration's non-identifying value parameter changes (for example its allowed values)
- **THEN** the expanded assertion identity is unchanged and only the payload differs

#### Scenario: Distinct ambiguous targets receive distinct identities
- **WHEN** two declarations have identifying components that would render to the same dotted string (for example entity `a` with field `b.c` versus entity `a.b` with field `c`)
- **THEN** the digest fallback assigns each a distinct identity within the identifier bound

#### Scenario: Identity stays within the identifier bound
- **WHEN** the rendered identity would exceed the identifier length bound
- **THEN** the deterministic digest fallback produces an identity matching the identifier pattern

### Requirement: Template form never enters the canonical payload or fingerprint domain
The canonical payload of an expanded policy assertion SHALL contain only resolved policy semantics (policy identity, policy kind, and typed parameter values) and SHALL NOT contain the template reference form, raw parameter mapping, or any authoring-only construct. The draft, manifest, bundle, and fingerprint domains SHALL observe only expanded assertions; no lifecycle, review, or fingerprint computation SHALL depend on the presence or absence of template declarations.

#### Scenario: Canonical payload contains resolved semantics only
- **WHEN** an expanded policy assertion's canonical payload is computed
- **THEN** it contains the resolved policy identity, kind, and parameter values and contains no template reference, raw parameter mapping, or authoring-only key

#### Scenario: Draft carries no template construct
- **WHEN** a document with policy declarations is lowered
- **THEN** the resulting draft contains only expanded policy assertions and no template declaration state
