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
The system SHALL make a semantic bundle fingerprint externally authoritative only during publish. A candidate Bundle MAY precompute the same deterministic fingerprint for validation, but it SHALL NOT be treated as published authority before the atomic catalog operation succeeds. Publish SHALL freeze the approved draft and its approved verification plan, run the verification policy required for the target profile, require every mandatory layer and case to pass, compute canonical semantic bytes, derive an immutable accepted-assertion manifest, verify that the manifest and emitted Bundle represent the same accepted semantic content, perform duplicate-content detection, persist the immutable published bundle and linked manifest, persist the publish audit and verification evidence summary, and update supersession metadata atomically. Plan content and verification evidence SHALL remain outside the Bundle semantic fingerprint domain but SHALL be bound to the approved revision and publication audit.

#### Scenario: Publish creates immutable semantic artifact
- **WHEN** an approved draft and plan pass every layer required by the selected verification policy and are published
- **THEN** the resulting published bundle has a `sha256:` semantic fingerprint and the draft remains distinct from the immutable published artifact

#### Scenario: Publish failure leaves no partial artifact
- **WHEN** required verification fails, is skipped/unavailable/timed out/not run, or fingerprint computation, catalog persistence, audit persistence, or supersession update fails
- **THEN** no externally visible partial publication exists and the draft remains approved for retry

#### Scenario: Same content and verification publish is idempotent
- **WHEN** the same approved semantic payload and bound verification plan are published more than once after equivalent passing verification
- **THEN** the system returns the existing published bundle fingerprint, verification evidence reference, and audit reference without creating duplicate immutable artifacts

#### Scenario: Published baseline retains assertion alignment
- **WHEN** an approved draft is published successfully
- **THEN** its accepted assertion IDs, types, canonical payloads, and payload hashes are persisted in an immutable manifest linked to the published fingerprint without entering the Bundle fingerprint domain

#### Scenario: Manifest mismatch blocks publication
- **WHEN** the accepted-assertion manifest and emitted Bundle do not represent the same frozen approved semantic content
- **THEN** Layer 1 and publish fail closed and persist no Bundle, manifest, verification evidence, audit record, or supersession edge

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

### Requirement: Authoring import cannot grant lifecycle authority
A semantic assembly authoring document SHALL enter the lifecycle only through a trusted lowering boundary that receives host-authorized `draft_id` and author identity separately from YAML. The boundary SHALL create a revision-zero `DRAFT` whose assertions are derived, manual, and pending, and SHALL persist it only through the existing tenant-scoped draft store and Admin creation authorization. Authoring validation or import SHALL NOT review, approve, publish, activate, or create audit evidence.

#### Scenario: Imported content starts pending
- **WHEN** a valid authoring document is imported by an authorized Author
- **THEN** the persisted draft is at revision `0`, all assertions are pending, and review and approval metadata are absent

#### Scenario: Caller cannot smuggle reviewed assertions
- **WHEN** YAML attempts to contain assertion IDs, review decisions, revision values, or approval identities
- **THEN** authoring validation rejects the document before the lifecycle draft store is called

#### Scenario: Import uses tenant-scoped create semantics
- **WHEN** two authorized tenants import documents using the same draft ID
- **THEN** each tenant receives an isolated draft, while a duplicate ID within one tenant follows the existing create conflict behavior

### Requirement: Verification plan changes invalidate assembly approval
A verification plan attached to an assembly draft SHALL be immutable, bounded, and included in the lifecycle approval binding while remaining excluded from semantic Bundle fingerprints. Adding, removing, enabling, disabling, or editing a case, assertion, deadline, fixture/deployment profile, or policy profile SHALL advance the draft revision and invalidate prior assembly approval. Publish SHALL verify the plan frozen with the exact approved revision.

#### Scenario: Approved plan edit requires reapproval
- **WHEN** any verification-plan content changes after draft approval
- **THEN** the draft returns to review or otherwise loses publish eligibility until the new plan and semantic content are approved and verified

#### Scenario: Verification does not mutate approved content
- **WHEN** a suite runs against an approved draft
- **THEN** only external verification evidence is produced; the draft, assertions, plan, and candidate Bundle remain immutable

### Requirement: Assembly lifecycle emits a coherent audit-evidence trail
The assembly lifecycle SHALL represent completed authoring/import, lint, assertion review, approval, verification, publication, activation, and rollback actions as bounded audit-evidence entries linked into a coherent trail. Each entry SHALL carry a stable event identity, event kind, subject reference, tenant/source scope fingerprints, relevant draft revision or artifact fingerprints, safe outcome/status, optional host-provided operator audit reference, predecessor references, and an entry fingerprint. Audit-evidence entries SHALL remain outside semantic Bundle fingerprints and SHALL NOT grant lifecycle authority.

#### Scenario: Review decision creates bounded evidence
- **WHEN** an authorized reviewer approves, rejects, or edits an assertion at a current draft revision
- **THEN** the lifecycle can expose a bounded audit-evidence entry linking the assertion ID, reviewed payload hash, draft revision, decision outcome, and operator audit reference without exposing raw operator identity or unsafe payload material

#### Scenario: Later draft edits do not rewrite old evidence
- **WHEN** a draft changes after prior review, approval, lint, or verification actions
- **THEN** prior audit-evidence entries remain linked to the revision/fingerprint facts that were current when the action completed and are not recomputed from the newer draft state

### Requirement: Publication audit evidence binds release readiness inputs
Publication SHALL create or reference a bounded audit-evidence entry that binds the approved draft revision, approved verification-plan fingerprint, accepted-assertion manifest fingerprint, Verification Suite evidence fingerprint, selected verification policy profile/version, lint readiness reference when present, tenant/source scope fingerprints, separation-of-duties result, publish audit reference, and immutable Bundle fingerprint. This publication audit evidence SHALL be validated before catalog persistence and SHALL remain outside the Bundle semantic fingerprint domain.

#### Scenario: Publication evidence explains authoritative release
- **WHEN** an approved draft is successfully published after required verification passes
- **THEN** the publication audit-evidence entry links the release readiness inputs and resulting Bundle fingerprint without including canonical semantic bytes, raw assertions, raw queries, credentials, physical names, or resolved deployment values

#### Scenario: Mismatched publication evidence fails closed
- **WHEN** publication audit evidence references a different draft revision, plan fingerprint, manifest fingerprint, verification evidence fingerprint, tenant/source scope, policy profile, or Bundle fingerprint than the publication aggregate
- **THEN** publication fails before catalog persistence and exposes no partial Bundle, audit, evidence, or supersession record

### Requirement: Activation and rollback preserve historical evidence links
Activation and rollback SHALL record bounded audit-evidence entries that link the requested Bundle fingerprint, prior active fingerprint when present, resulting active fingerprint, operator audit reference, tenant/source scope, validation outcome, and predecessor publication evidence. Rollback SHALL NOT republish semantic content or create a new semantic Bundle fingerprint.

#### Scenario: Activation links to publication evidence
- **WHEN** an operator activates a published Bundle
- **THEN** the activation audit-evidence entry links to the immutable publication evidence for that Bundle and records the active-pointer outcome without mutating the published artifact

#### Scenario: Rollback evidence keeps both versions explainable
- **WHEN** an operator rolls back to a prior valid Bundle fingerprint
- **THEN** the rollback audit-evidence entry identifies the previous active fingerprint, restored fingerprint, operator audit reference, and predecessor publication evidence for both versions without republishing either Bundle

