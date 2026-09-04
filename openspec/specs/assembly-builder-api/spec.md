# assembly-builder-api Specification

## Purpose
Define a fluent, fail-closed programmatic builder for semantic assembly authoring documents that constructs exactly the model YAML parsing produces, traverses the unchanged shared pipeline, and adds no lifecycle, credential, or fingerprint authority.
## Requirements
### Requirement: Builder constructs the same authoring document model as YAML authoring
The system SHALL provide a fluent programmatic builder that constructs the exact same authoring document model produced by YAML parsing, covering every authoring schema section (metadata, source, entities with fields, relationships, and calculated fields, measures, grains, policy template declarations, source references, compatibility, deployment bindings, and verification plan). The builder SHALL NOT accept expression strings for calculated fields or any content class the authoring schema rejects, and its parameters SHALL mirror the schema fields. When the schema gains a section, the builder surface SHALL cover it.

#### Scenario: Builder document traverses the shared pipeline identically
- **WHEN** a builder-constructed document is validated, lowered, and exported with the same trusted host inputs as an equivalent YAML document
- **THEN** the validation summary, the ordered lowered assertion identities and payload hashes, and the export bytes are identical

#### Scenario: Builder surface covers every schema section
- **WHEN** the authoring document model declares a schema section
- **THEN** the builder exposes a corresponding construction method, and a document built without using any optional section is equivalent to the YAML document that omits it

### Requirement: Builder construction is fail-closed with no validation fork
Every builder construction step SHALL run the same model-level validation that YAML parsing runs, so content that the schema rejects — raw policy payloads, computed fingerprints, lifecycle state, approval bindings, credentials, physical names, unsafe descriptions, out-of-bounds collections, and values outside the bounded scalar profile — fails at the construction call and can never reach lowering through the builder. The builder SHALL add no validation logic of its own beyond structural misuse checks.

#### Scenario: Schema-rejected content fails at construction
- **WHEN** a builder call supplies content the authoring schema rejects
- **THEN** the construction call fails closed with a bounded diagnostic and no partially constructed document is returned

#### Scenario: No fork between entry points
- **WHEN** the same logical document is expressed through the builder and through YAML
- **THEN** any content accepted by one entry point is accepted by the other, and any content rejected by one is rejected by the other at the same pipeline stage

### Requirement: Builder construction is deterministic and order-independent
Builder output SHALL depend only on the constructed content, not on the order of independent construction calls. Documents that differ only in the call order of independent sections SHALL produce identical downstream artifacts. The builder SHALL NOT sort, normalize, or re-order content itself; identity-based ordering remains owned by lowering and export.

#### Scenario: Call order does not change downstream artifacts
- **WHEN** two builder invocations construct equivalent documents with different call orders across independent sections
- **THEN** their lowered assertion identities, payload hashes, and export bytes are identical

#### Scenario: Equivalent YAML and builder documents are byte-identical on export
- **WHEN** a builder-constructed document and a hand-written equivalent YAML document are exported
- **THEN** the exports are byte-identical

### Requirement: Builder adds no authority and presents a bounded error surface
The builder SHALL NOT expose any method or parameter that expresses review decisions, approval bindings, lifecycle state, computed fingerprints, resolved secrets, or physical credentials, and SHALL NOT introduce new bounds, identity rules, or template semantics outside the models. Construction failures SHALL raise a bounded typed error carrying an authoring path and message without echoing rejected values or attaching source marks, and structural misuse — entity-scoped calls outside an entity scope, use after completion, or repeated finalization — SHALL fail closed.

#### Scenario: No authority-bearing parameters exist
- **WHEN** the builder surface is inspected
- **THEN** no method or parameter accepts lifecycle state, review or approval bindings, computed fingerprints, resolved secrets, or physical credentials, and supplying such content through a schema-typed parameter fails at model validation

#### Scenario: Misuse and failures are bounded and non-echoing
- **WHEN** a builder call is misused or rejects content
- **THEN** the raised error names the authoring path and the failure class, does not echo the rejected value, and carries no source mark
