# semantic-model-bundles Specification

## Purpose
Define versioned, immutable, validated Semantic Model Bundles and their safe catalog lifecycle, including reviewed metadata-discovery inputs.
## Requirements
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
The system SHALL define a replaceable catalog protocol and implementations supporting immutable publish by semantic fingerprint, lookup by bundle name/fingerprint and business version metadata, active snapshot lookup, atomic activation, supersession-chain traversal, and rollback only to a previously published valid bundle. Governed assembly publication SHALL validate the configured verification policy and require passing evidence bound to the exact plan, approved draft revision, manifest, candidate Bundle, tenant/source context, runner identity, and executor identities before making a bundle available. It SHALL persist the immutable artifact, manifest, verification summary/reference, publish audit, and supersession update atomically, SHALL be idempotent for identical semantic content and equivalent bound evidence, and SHALL never expose a partial bundle. The Admin API SHALL delegate these decisions to the core/catalog and SHALL not implement a competing verification or active pointer.

#### Scenario: Invalid bundle remains unpublished and inactive
- **WHEN** publication validation or any required verification layer fails, is unavailable, skipped, timed out, or not run
- **THEN** the catalog does not publish or activate the bundle, creates no partial verification/audit record, and returns structured issues

#### Scenario: Duplicate semantic content is idempotent
- **WHEN** an approved assembly draft publishes semantic content whose fingerprint already exists for the bundle name with an equivalent accepted verification plan/evidence binding
- **THEN** the catalog returns the existing immutable publication and its verification/audit references without creating a duplicate artifact

#### Scenario: Activation switches complete snapshots
- **WHEN** a valid verified published bundle is activated
- **THEN** subsequent View resolutions observe the complete new snapshot, while existing evidence continues to identify the previous bundle fingerprint

#### Scenario: Rollback selects an immutable version
- **WHEN** an operator rolls back to a previously published valid fingerprint
- **THEN** the catalog changes the active pointer to that version without mutating either bundle artifact, verification evidence, or assigning a new fingerprint

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

### Requirement: Verification lifecycle data stays outside semantic identity
Verification plans, case definitions, runner/executor identities, statuses, durations, protected result references, issue codes, and suite evidence SHALL NOT enter the Bundle canonical semantic payload or semantic fingerprint. They SHALL be lifecycle evidence bound by fingerprints in the publish audit. Changing only verification lifecycle data SHALL require renewed approval/verification according to policy but SHALL NOT change otherwise identical Bundle semantic identity.

#### Scenario: Verification plan change preserves semantic fingerprint
- **WHEN** two approved candidates have identical semantic payload but different verification plans
- **THEN** their candidate Bundle semantic fingerprints are equal while their plan and verification evidence fingerprints differ

