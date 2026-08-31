# semantic-assembly-yaml-authoring Specification

## Purpose
Define a safe, bounded, deterministic YAML authoring format for semantic assembly content that lowers into lifecycle-controlled drafts without granting review, approval, publication, or evidence authority.
## Requirements
### Requirement: Authoring documents use a versioned semantic-only schema
The authoring schema SHALL optionally express a bounded `verificationPlan` containing policy identity, deadlines, canonical semantic IR, fixture profile references, capability requirements, smoke assertions, and semantic contracts. It SHALL NOT accept computed plan/query fingerprints, approval bindings, runner/executor identity, statuses, observations, evidence, SQL/MQL, physical names, credentials, or native values.

#### Scenario: Verification plan lowers without lifecycle evidence
- **WHEN** valid authoring YAML contains a verification plan
- **THEN** lowering attaches the equivalent core `VerificationPlan` to the revision-zero draft without accepting or exporting fingerprints, statuses, or evidence

#### Scenario: Verification lifecycle authority is rejected
- **WHEN** verification authoring contains computed fingerprints, runner/executor identity, statuses, observations, evidence, backend syntax, or credentials
- **THEN** parsing or model validation fails before draft creation and no unsafe value is echoed

### Requirement: YAML parsing is bounded and non-executable
The authoring parser SHALL use a non-executable YAML path and SHALL accept only mappings, sequences, strings, null, booleans, integers, and finite floats according to an explicit JSON-compatible scalar profile. It SHALL reject custom tags, object construction, merge keys, duplicate mapping keys, cyclic aliases, non-string mapping keys, non-finite numbers, and unsupported implicit timestamp or YAML 1.1 boolean coercions. Input bytes, node count, nesting depth, scalar length, collection length, and alias expansion SHALL be bounded before model construction.

#### Scenario: Dangerous YAML features are rejected
- **WHEN** input contains a Python/object tag, duplicate key, `<<` merge key, cyclic alias, or alias expansion exceeding the configured bound
- **THEN** the parser returns a safe bounded diagnostic without constructing objects or performing external I/O

#### Scenario: Ambiguous scalars remain deterministic
- **WHEN** an unquoted scalar resembles a timestamp or a YAML 1.1 boolean such as `yes` or `on`
- **THEN** the explicit scalar profile treats it consistently as a string rather than changing semantic type across parser versions

#### Scenario: Oversized input stops before lowering
- **WHEN** YAML exceeds any configured byte, node, depth, scalar, collection, or alias bound
- **THEN** parsing stops with a bounds diagnostic and no partial authoring model or draft is returned

### Requirement: Semantic references validate before lowering
The authoring validator SHALL enforce unique descriptor-global entity, field, relationship, calculated-field, measure, grain, source-reference, and deployment-binding identities; valid relationship endpoints and join fields; valid measure/grain references; source consistency; and all inherited field, value-semantics, calculated-field, pii-isolation, compatibility, and safe-content rules. Unknown or ambiguous references SHALL fail closed before an `AssemblyDraft` is created.

#### Scenario: Cross-reference failure is source-located
- **WHEN** a measure names an unknown field or a relationship names an unknown entity or join field
- **THEN** validation returns a diagnostic identifying the authoring path and source location of the invalid reference

#### Scenario: Calculated fields use the governed DSL
- **WHEN** YAML declares a calculated field
- **THEN** its expression, output type, exact dependencies, bounds, non-composition rule, pii isolation, and zero-division policy are validated by the existing calculated-field models

#### Scenario: Inline deployment credential is rejected
- **WHEN** a deployment binding contains a DSN, token, password, resolved secret, or unsupported reference scheme
- **THEN** validation fails before draft creation and the diagnostic does not echo the credential value

### Requirement: Lowering creates a clean lifecycle draft deterministically
Given a valid authoring model plus trusted host inputs for `draft_id` and `author_reference`, core SHALL deterministically lower semantic members into `SemanticAssertion` records using the existing type-specific identity rules. The resulting `AssemblyDraft` SHALL be in `DRAFT` state at revision `0`; every assertion SHALL have derived identity, manual provenance, pending review state, and no review binding; review, approval, audit, and publish metadata SHALL be absent. The lowering result SHALL be independent of YAML mapping order, comments, bounded anchor/alias spelling, and presentation formatting.

#### Scenario: Trusted host owns lifecycle identity
- **WHEN** a valid document is imported with a host-authorized author reference and generated or host-selected draft ID
- **THEN** the resulting draft records those trusted inputs and ignores no caller-controlled substitute because the authoring schema cannot express one

#### Scenario: Equivalent YAML lowers identically
- **WHEN** two documents contain equivalent semantic content but differ in key order, comments, whitespace, or bounded anchor/alias use
- **THEN** they lower to the same ordered assertion IDs and payload hashes, deployment bindings, bundle metadata, and source identity

#### Scenario: Import never self-approves
- **WHEN** a valid authoring document is lowered successfully
- **THEN** every assertion remains pending and the draft must traverse the existing review, approval, and publish gates

### Requirement: Stable export contains authoring content only
The system SHALL provide a deterministic export of a valid authoring model or revision-zero authoring-derived draft using the supported authoring schema. Export SHALL order identity-keyed collections predictably and SHALL omit lifecycle state, assertion IDs, provenance, review bindings, audit references, fingerprints, resolved secrets, and other control-plane metadata. Export followed by parse and lowering SHALL preserve semantic assertion payloads and deployment binding references.

#### Scenario: Export round-trip preserves semantics
- **WHEN** a supported authoring model is exported and then parsed and lowered again with the same trusted host inputs
- **THEN** the resulting assertion identities and payload hashes equal those from the original model

#### Scenario: Export cannot leak lifecycle authority
- **WHEN** an authoring-derived draft has subsequently accumulated review metadata
- **THEN** authoring export either rejects the non-authoring lifecycle state or emits semantic content only, never review decisions or operator references

