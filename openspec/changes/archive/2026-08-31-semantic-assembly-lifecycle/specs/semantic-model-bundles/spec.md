# semantic-model-bundles Delta

## MODIFIED Requirements

### Requirement: Semantic Model Bundles are immutable and versioned
The system SHALL represent a published Semantic Model Bundle as an immutable, versioned runtime artifact emitted by the semantic assembly publish operation. A published bundle SHALL contain bounded semantic entities, fields, relationships, calculated-field definitions, measures/aggregation metadata, source/catalog references, compatibility metadata, and safe runtime metadata. Its semantic fingerprint SHALL cover only canonical semantic payload and SHALL become authoritative only at successful publish; an in-memory candidate MAY precompute the same value for validation. Bundle semantic facts MAY originate from approved assembly assertions, but a bundle SHALL contain no pending or rejected assertion authority, credentials, connection strings, raw executable SQL/MQL/code, native objects, or authorization claims. The admin API SHALL expose only safe metadata and opaque references for Bundle content.

#### Scenario: Bundle has stable semantic identity
- **WHEN** equivalent published semantic model contents are constructed from assembly drafts with different mapping insertion orders, provenance, reviewer identities, deployment bindings, or YAML presentation
- **THEN** the bundle produces the same canonical semantic serialization and SHA-256 fingerprint

#### Scenario: Calculated fields remain in semantic identity
- **WHEN** an approved calculated-field definition changes in any canonical member
- **THEN** the published semantic payload and fingerprint change, and resolved projections derive a new safe content-hash anchor from the published definition

#### Scenario: Unsafe model content is rejected
- **WHEN** a bundle contains physical credentials, connection material, executable query text, native driver values, pending assertions, rejected assertions as authority, or unapproved semantic proposals
- **THEN** validation rejects the bundle before publication or activation

#### Scenario: Pre-publication draft has no bundle fingerprint
- **WHEN** semantic content is still in Draft, Review, or Approved assembly state
- **THEN** it is not represented as a published Semantic Model Bundle and exposes no semantic bundle fingerprint

### Requirement: Bundle provenance and trust metadata are preserved
A bundle SHALL preserve safe runtime metadata and references needed to trace published semantic facts back to approved assembly assertions, source/catalog references, owner or origin references, compatibility information, quality status, and discovery proposal references. Authored, inferred, LLM-suggested, and human-approved semantic facts SHALL remain distinguishable through audit-side metadata, and inferred or LLM-suggested facts SHALL NOT become authorization decisions by themselves. Semantic fingerprint computation SHALL exclude provenance, review state, approval chains, rejected assertions, deployment bindings, activation metadata, and raw operator identity. API responses SHALL expose bounded provenance and host-provided audit references only.

#### Scenario: Inference is not authorization
- **WHEN** a bundle or audit record marks a relationship, description, mapping, or measure as inferred or LLM-suggested without trusted review approval
- **THEN** the fact may be retained as metadata but cannot independently grant View visibility, execution authority, or publication eligibility

#### Scenario: Provenance is safe
- **WHEN** bundle provenance is serialized for evidence
- **THEN** it contains bounded opaque references and status metadata without raw identities, secrets, physical source details, or deployment credentials

#### Scenario: Provenance does not affect semantic fingerprint
- **WHEN** two published bundles have identical semantic payloads but different provenance, reviewer, or approval-chain metadata
- **THEN** they have the same semantic bundle fingerprint

### Requirement: Catalog publication is atomic and replaceable
The system SHALL define a replaceable catalog protocol and implementations supporting immutable publish by semantic fingerprint, lookup by bundle name/fingerprint and business version metadata, active snapshot lookup, atomic activation, supersession-chain traversal, and rollback only to a previously published valid bundle. Publication SHALL validate and verify before making a bundle available, SHALL persist the immutable artifact and publish audit record atomically, SHALL be idempotent for identical semantic content, and SHALL never expose a partial bundle. The admin API SHALL delegate these decisions to the catalog and SHALL not implement a competing active pointer.

#### Scenario: Invalid bundle remains unpublished and inactive
- **WHEN** publication validation or verification fails
- **THEN** the catalog does not publish or activate the bundle, creates no partial audit record, and returns structured issues

#### Scenario: Duplicate semantic content is idempotent
- **WHEN** an approved assembly draft publishes semantic content whose fingerprint already exists for the bundle name
- **THEN** the catalog returns the existing immutable publication without creating a duplicate artifact

#### Scenario: Activation switches complete snapshots
- **WHEN** a valid published bundle is activated
- **THEN** subsequent View resolutions observe the complete new snapshot, while existing evidence continues to identify the previous bundle fingerprint

#### Scenario: Rollback selects an immutable version
- **WHEN** an operator rolls back to a previously published valid fingerprint
- **THEN** the catalog changes the active pointer to that version without mutating either bundle artifact or assigning a new fingerprint

## ADDED Requirements

### Requirement: Published bundle loaders reject lifecycle metadata in semantic payload
Bundle loaders SHALL reject canonical semantic payloads that include assembly-only lifecycle metadata such as `review_state`, `review_binding`, approval chains, rejected assertions, deployment bindings, activation markers, or file `apiVersion`. These fields MAY appear in surrounding safe envelopes where explicitly defined, but SHALL NOT be accepted as bundle semantic content.

#### Scenario: Lifecycle metadata in payload fails closed
- **WHEN** a bundle loader receives canonical semantic content containing a `review_state` or deployment binding field
- **THEN** loading fails with an explicit unsafe-payload or incompatible-schema result and does not activate the bundle

### Requirement: Bundle publication requires approved assembly source or explicit manual authority
Publishing a semantic bundle SHALL require either an approved assembly draft with all assertion review bindings valid or an explicit trusted host manual-authority path. The provider-neutral low-level `catalog.publish(bundle)` call is that embedded-host path: validated/approved Bundle quality and bounded Bundle provenance are its approval evidence, it is not exposed by the Admin API, and it SHALL NOT accept unreviewed discovery proposals. Unreviewed discovery output SHALL NOT publish directly.

#### Scenario: Discovery output cannot publish directly
- **WHEN** discovery produces assertions or proposals that have not been reviewed and approved
- **THEN** bundle publication rejects them as unapproved authority

#### Scenario: Manual authority is audited
- **WHEN** a host publishes hand-authored semantic content through an explicit manual authority path
- **THEN** publish records bounded audit evidence for the authorizing identity and lifecycle policy without including that evidence in the semantic fingerprint

### Requirement: Business versions identify one semantic publication
Within one tenant scope and bundle name, a business version SHALL identify at most one semantic fingerprint. Identical content proposed under another business version SHALL reuse the existing publication and report its persisted version; different content proposed under an existing version SHALL fail with `version_exists`.

#### Scenario: Reused content reports the persisted version
- **WHEN** a host proposes identical semantic content with a different business version label
- **THEN** publish reuses the existing artifact and returns the business version actually persisted for that artifact