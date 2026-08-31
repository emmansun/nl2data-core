# semantic-assembly-lifecycle Specification

## Purpose
Define the governed Draft -> Review -> Approved -> Published semantic assembly lifecycle, including stable assertion identity, review invalidation, optimistic concurrency, publish-time semantic fingerprints, auditability, authorization, deployment binding safety, and immutable version rollback.

## Requirements

### Requirement: Assembly drafts capture editable semantic lifecycle state
The system SHALL represent pre-publication semantic assembly as an `AssemblyDraft` artifact with explicit lifecycle state, file `apiVersion`, bundle identity metadata, deterministic semantic assertions, safe provenance, review state, deployment bindings, and a monotonic `draft_revision`. Draft, review, and approved assembly artifacts SHALL NOT expose a semantic bundle fingerprint.

#### Scenario: Draft has no bundle fingerprint
- **WHEN** a manual YAML file or discovery output is loaded as an assembly draft
- **THEN** the draft exposes lifecycle metadata and assertions but no semantic bundle fingerprint

#### Scenario: Unknown file apiVersion fails closed
- **WHEN** an assembly file declares an unknown `apiVersion` or omits the required `apiVersion`
- **THEN** validation rejects the file before review or publish instead of best-effort parsing it

#### Scenario: Deployment binding is separate from semantic content
- **WHEN** an assembly draft declares deployment bindings for dev, test, or production environments
- **THEN** those bindings are validated as deployment metadata and are excluded from the semantic canonical payload

### Requirement: Semantic assertions are stable review units
The system SHALL represent every reviewable semantic fact as a `SemanticAssertion` with deterministic `id`, assertion type, semantic payload, safe provenance, review state, and review binding metadata. Assertion IDs SHALL derive from identity semantics rather than random values or discovery order. Calculated fields SHALL be first-class assertions whose payload includes the complete canonical definition; their expression material SHALL never be reduced to prompt identity or a name-only assertion before review and publish.

#### Scenario: Mapping payload edit keeps assertion identity
- **WHEN** a value mapping assertion keeps the same entity and field identity but changes mapped values
- **THEN** the assertion ID remains stable and semantic diff reports the assertion as modified

#### Scenario: Join identity edit creates a different assertion
- **WHEN** a relationship assertion changes its join key identity
- **THEN** the new assertion has a different assertion ID and semantic diff reports delete plus add rather than a payload modification

#### Scenario: Rejected assertion is negative evidence only
- **WHEN** a reviewer rejects an assertion with a bounded reason
- **THEN** the rejected assertion is excluded from canonical semantic payload and retained as negative evidence in the publish audit trail

#### Scenario: Calculated-field edit invalidates review
- **WHEN** a calculated field keeps its name but changes its expression, output type, dependencies, zero-division policy, label, or description
- **THEN** its assertion identity remains stable, its payload binding changes, and it returns to pending review

### Requirement: Review decisions invalidate on semantic payload changes
The system SHALL bind approved and rejected review decisions to the canonical payload hash of the assertion that was reviewed. Any semantic payload change SHALL invalidate the binding and return the assertion to pending review while leaving unaffected assertions reviewed.

#### Scenario: Edited approved assertion becomes pending
- **WHEN** an approved assertion's semantic payload is edited after review
- **THEN** its review binding no longer matches and the assertion returns to pending before approval or publish can proceed

#### Scenario: Unchanged assertions retain review state
- **WHEN** incremental rediscovery changes one assertion in a draft that contains other reviewed assertions
- **THEN** only the affected assertion becomes pending and unchanged reviewed assertions keep their valid review bindings

#### Scenario: Pending assertion blocks publish
- **WHEN** an approved draft contains any assertion with pending or invalidated review state
- **THEN** publish is rejected and no published artifact, audit record, or supersession update is created

### Requirement: Draft mutations use optimistic revision control
The system SHALL require draft edit, assertion review, approval, and publish requests to include the expected `draft_revision`. A stale revision SHALL return a conflict and leave the current draft unchanged.

#### Scenario: Stale review is rejected
- **WHEN** a reviewer submits an assertion decision against an older draft revision
- **THEN** the system returns a conflict and preserves the newer draft content and review state

#### Scenario: Successful mutation advances revision
- **WHEN** a draft edit or review decision is accepted
- **THEN** the returned draft has a strictly greater `draft_revision`

### Requirement: Publish makes fingerprint authoritative atomically
The system SHALL make a semantic bundle fingerprint externally authoritative only during publish. A candidate Bundle MAY precompute the same deterministic fingerprint for validation, but it SHALL NOT be treated as published authority before the atomic catalog operation succeeds. Publish SHALL freeze the approved draft, run the required verification suite, compute canonical semantic bytes, derive an immutable accepted-assertion manifest, verify that the manifest and emitted Bundle represent the same accepted semantic content, perform duplicate-content detection, persist the immutable published bundle and linked manifest, persist the publish audit record, and update supersession metadata atomically.

#### Scenario: Publish creates immutable semantic artifact
- **WHEN** an approved draft passes verification and is published
- **THEN** the resulting published bundle has a `sha256:` semantic fingerprint and the draft remains distinct from the immutable published artifact

#### Scenario: Publish failure leaves no partial artifact
- **WHEN** verification, fingerprint computation, catalog persistence, audit persistence, or supersession update fails during publish
- **THEN** no externally visible partial publication exists and the draft remains approved for retry

#### Scenario: Same content publish is idempotent
- **WHEN** the same approved semantic payload is published more than once
- **THEN** the system returns the existing published bundle fingerprint and audit reference without creating duplicate immutable artifacts

#### Scenario: Published baseline retains assertion alignment
- **WHEN** an approved draft is published successfully
- **THEN** its accepted assertion IDs, types, canonical payloads, and payload hashes are persisted in an immutable manifest linked to the published fingerprint without entering the Bundle fingerprint domain

#### Scenario: Manifest mismatch blocks publication
- **WHEN** the accepted-assertion manifest and emitted Bundle do not represent the same frozen approved semantic content
- **THEN** publish fails closed and persists no Bundle, manifest, audit record, or supersession edge

### Requirement: Canonical semantic fingerprint excludes lifecycle metadata
Published bundle fingerprints SHALL be derived from deterministic canonical bytes of semantic payload only. The semantic fingerprint domain SHALL exclude provenance, review state, review bindings, reviewer identity, approval chains, rejected assertions, deployment bindings, file-format metadata, audit records, activation state, and supersession metadata.

#### Scenario: Provenance does not change semantic fingerprint
- **WHEN** two published bundles have identical semantic payloads but different assertion provenance or reviewer identities
- **THEN** they produce the same semantic bundle fingerprint

#### Scenario: Deployment binding does not change semantic fingerprint
- **WHEN** a bundle is published with the same semantic payload but different deployment binding references
- **THEN** the semantic bundle fingerprint is unchanged

#### Scenario: YAML presentation does not change semantic fingerprint
- **WHEN** equivalent assembly content is represented with different YAML key order, comments, anchors, or indentation
- **THEN** canonical semantic bytes and the resulting fingerprint are identical

### Requirement: Provenance transfer preserves responsibility and auditability
The system SHALL update assertion provenance responsibility when a human reviewer edits semantic payload. Human-edited accepted content SHALL become manual responsibility while preserving bounded seed/source references as audit metadata outside the semantic fingerprint domain.

#### Scenario: Edited LLM suggestion becomes manual
- **WHEN** a reviewer edits a `llm-suggested` assertion and accepts the edited content
- **THEN** the accepted assertion records manual responsibility and preserves the LLM seed reference only as audit metadata

#### Scenario: Accepted suggestion still requires explicit review
- **WHEN** a reviewer accepts an LLM-suggested assertion without changing its payload
- **THEN** the assertion may retain `llm-suggested` provenance but only becomes publishable because an explicit approved review decision exists

### Requirement: Lifecycle authorization is host supplied and core enforced
The system SHALL model Author, Reviewer, Approver, and Publisher lifecycle actions as distinct authorization decisions. Hosts SHALL supply trusted operator identity, tenant/source scope, and separation-of-duties policy; core SHALL enforce lifecycle preconditions and record bounded audit references.

#### Scenario: Unauthorized lifecycle mutation is denied
- **WHEN** a host submits a lifecycle mutation without the required trusted role and scope
- **THEN** the mutation is rejected without changing draft, review, publication, or activation state

#### Scenario: Solo mode waiver is audited
- **WHEN** a host policy allows one identity to perform multiple lifecycle roles
- **THEN** the publish audit record records the separation-of-duties waiver as bounded metadata

### Requirement: Published versions support supersession and rollback
The system SHALL model published bundle versions as immutable artifacts identified by bundle name and semantic fingerprint. Publishing different content for the same bundle name SHALL append to a supersession chain without mutating older artifacts. Rollback SHALL change only the active pointer to a previously published valid artifact.

#### Scenario: New content supersedes previous active version
- **WHEN** a new semantic payload for an existing bundle name is published and activated
- **THEN** the previous published artifact remains immutable and the new artifact records its superseded predecessor

#### Scenario: Rollback does not republish
- **WHEN** an operator rolls back to a previous published fingerprint
- **THEN** the active pointer changes to that artifact without assigning a new semantic fingerprint

### Requirement: Deployment bindings never persist raw secrets
Deployment bindings SHALL permit only safe connection reference forms and SHALL reject inline cleartext secrets. Secret resolution SHALL occur at runtime or verification time without storing resolved credentials in semantic payload, publish audit records, or admin service responses.

#### Scenario: Inline secret blocks validation
- **WHEN** a deployment binding contains an inline password, token, or credential-like DSN
- **THEN** validation rejects the assembly draft before publish

#### Scenario: Audit record redacts connection reference
- **WHEN** publish records deployment binding evidence
- **THEN** the audit record contains only the reference form and a bounded redacted summary without resolved credentials