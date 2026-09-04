# durable-semantic-catalog Specification

## Purpose
Define the optional durable PostgreSQL catalog for safe metadata snapshots, reviewed proposal sets, immutable Semantic Model Bundles, active pointers, and rollback history.
## Requirements
### Requirement: PostgreSQL semantic catalog persists safe lifecycle artifacts
The PostgreSQL semantic catalog SHALL persist versioned safe representations of authorized MetadataSnapshots, SemanticProposalSets, AssemblyDrafts, assertion review state, immutable Semantic Model Bundle publications, immutable accepted-assertion manifests linked by semantic fingerprint, publish audit records, supersession chains, deployment binding references, active pointers, and rollback history. It SHALL keep semantic catalog data separate from workflow state tables and SHALL never persist resolved credentials or raw source data.

#### Scenario: Snapshot survives process restart
- **WHEN** a valid snapshot is registered and the catalog process stops and reconnects
- **THEN** the same snapshot can be loaded by fingerprint with equivalent canonical content and scope

#### Scenario: Assembly draft survives process restart
- **WHEN** an assembly draft with assertions, review state, deployment binding references, and draft revision is persisted and a new catalog instance starts
- **THEN** the draft can be loaded with equivalent lifecycle state and without a semantic bundle fingerprint

#### Scenario: Bundle publication survives process restart
- **WHEN** a validated approved assembly is published and a new catalog instance starts
- **THEN** the published Bundle, semantic fingerprint, linked accepted-assertion manifest, publish audit record, and supersession metadata can be retrieved without reconstructing them from raw source data

### Requirement: Catalog writes and reads are safe and versioned
The catalog SHALL write only bounded canonical envelopes with explicit schema and artifact kind, reject unsupported versions or unsafe payloads, and revalidate fingerprints, source compatibility, structural validity, and tenant/source scope on read.

#### Scenario: Unsafe payload is rejected
- **WHEN** a caller attempts to persist credentials, raw prompts, raw queries/results, native objects, or unbounded values
- **THEN** the write is rejected before PostgreSQL persistence

#### Scenario: Newer schema fails closed
- **WHEN** a catalog reads an envelope or migration version newer than the package supports
- **THEN** it returns a normalized incompatible-schema error and does not expose the artifact

### Requirement: Shared catalog enforces tenant and source isolation
Every scoped snapshot, proposal set, publication, active pointer, and lifecycle lookup SHALL carry the required opaque tenant scope fingerprint and source compatibility references. Raw tenant identifiers SHALL NOT be used in keys or persisted catalog identity.

#### Scenario: Cross-scope lookup is rejected
- **WHEN** a caller requests a snapshot, proposal set, or Bundle using a different tenant scope fingerprint
- **THEN** the catalog returns not-found or unauthorized semantics without revealing the other scope's content

#### Scenario: Source mismatch cannot activate
- **WHEN** a Bundle references a source snapshot or source identity incompatible with the requested production context
- **THEN** activation is rejected and the current active pointer is unchanged

### Requirement: Publish and activate are atomic and idempotent
The catalog SHALL publish only verified approved semantic content, compute or verify the semantic fingerprint at the publish boundary, validate that passing Verification Suite evidence is bound to the exact approved draft revision, plan fingerprint, manifest fingerprint, candidate Bundle fingerprint, tenant/source scope, policy profile, runner identity, and executor identities, and atomically persist the immutable artifact, linked accepted-assertion manifest, bounded verification evidence summary/reference, publish audit record, and supersession-chain update. It SHALL atomically activate one complete compatible version per catalog scope. Repeated publish of identical semantic content with equivalent bound passing evidence SHALL be idempotent by fingerprint. Concurrent publication and activation SHALL serialize without exposing partial content or mismatched evidence.

#### Scenario: Concurrent activation leaves one complete active version
- **WHEN** multiple workers activate compatible candidate versions concurrently
- **THEN** each completed operation observes a complete verified published version and the catalog exposes exactly one final active pointer

#### Scenario: Failed activation preserves the active version
- **WHEN** validation, dependency, freshness, drift, scope, database, or required verification-evidence checks fail during activation
- **THEN** no active pointer changes and the failure is safe and retry-classified where appropriate

#### Scenario: Failed publish leaves no partial lifecycle records
- **WHEN** validation, any required verification layer, evidence-binding validation, fingerprinting, manifest/verification/audit persistence, supersession update, or database commit fails
- **THEN** no published bundle, accepted-assertion manifest, verification evidence record, audit record, or supersession edge becomes externally visible

#### Scenario: Published manifest is retrieved by fingerprint
- **WHEN** incremental rediscovery selects a published Bundle fingerprint as its baseline
- **THEN** the catalog returns exactly one immutable accepted-assertion manifest linked to that fingerprint or fails closed when the manifest is missing or mismatched

#### Scenario: Identical publish is idempotent
- **WHEN** a caller retries publish for semantic content whose fingerprint already exists in the scoped catalog with an equivalent plan and passing evidence binding
- **THEN** the catalog returns the existing publication, verification evidence reference, and audit reference without creating duplicate records

### Requirement: Rollback and reload preserve immutable history
The catalog SHALL support lookup of published versions by name, business version metadata, and semantic fingerprint; atomic rollback to a previously valid compatible version; startup reload of active pointers; and supersession-chain traversal. It SHALL never mutate or delete an artifact that is active, superseded, audit-referenced, or required by an active artifact.

#### Scenario: Rollback selects a prior valid Bundle
- **WHEN** an operator requests rollback to a previously published compatible fingerprint
- **THEN** the active pointer changes atomically, both artifact versions remain immutable, and their fingerprints remain stable

#### Scenario: Active state is reloaded
- **WHEN** a new worker initializes from the PostgreSQL catalog
- **THEN** it loads the active snapshot/Bundle references and revalidates them before query-time View resolution

#### Scenario: Supersession chain is queryable
- **WHEN** a host lists the lifecycle history for a bundle name
- **THEN** the catalog returns bounded metadata for active, superseded, deprecated, and retired published artifacts without mutating their content

### Requirement: Draft persistence enforces revision conflicts
The durable catalog SHALL store and compare `draft_revision` for every assembly draft mutation. Edit, review, approval, and publish operations with stale expected revisions SHALL fail with a conflict and SHALL NOT overwrite newer draft state.

#### Scenario: Stale draft write is rejected
- **WHEN** two workers attempt to update the same assembly draft and one submits an older expected revision
- **THEN** the older write fails with a conflict and the newer persisted draft remains unchanged

### Requirement: Deployment binding persistence is secret safe
The durable catalog SHALL persist only safe deployment binding references and redacted summaries. It SHALL reject inline cleartext credentials and SHALL NOT persist resolved env, vault, file, or host-secret values.

#### Scenario: Resolved credential is not stored
- **WHEN** publish or verification resolves a deployment binding to test connectivity
- **THEN** the catalog stores no resolved credential and exposes only the safe reference and redacted summary

### Requirement: Retention and failures are bounded and normalized
The catalog SHALL provide explicit retention and bounded cleanup for inactive artifacts and lifecycle evidence, preserve active dependencies, use bounded database timeouts, and normalize unavailable, conflict, authorization, serialization, and migration failures without leaking DSNs or backend exception text.

#### Scenario: Cleanup preserves active dependencies
- **WHEN** bounded cleanup removes expired inactive records
- **THEN** active snapshots, active Bundles, and dependencies required for active resolution remain available

#### Scenario: Database outage is safe
- **WHEN** PostgreSQL is unavailable or a catalog operation times out
- **THEN** the package returns a normalized retryable catalog error and does not report an uncommitted publication or activation as successful

### Requirement: Durable verification evidence is safe and reloadable
The durable catalog SHALL persist a versioned bounded Verification Suite evidence envelope and an immutable publication-time `FrozenReleaseBinding` atomically with publication. The frozen binding SHALL contain the approved draft identity/revision, approved verification-plan fingerprint, tenant/source scope fingerprints, manifest fingerprint, Bundle fingerprint, policy profile/version, runner identity/version, and executor identity/capability fingerprint needed for future validation. Reload SHALL validate evidence against this immutable binding, the immutable Bundle/manifest/audit records, and envelope metadata; it SHALL NOT require or compare the current mutable assembly draft row. The envelope and binding SHALL contain no raw rows, scalar values, SQL/MQL, prompts, physical names, deployment references, credentials, native values, backend exception text, or mutable review state.

#### Scenario: Verification evidence survives restart
- **WHEN** a verified Bundle is published and a new catalog worker starts
- **THEN** the worker can retrieve the same bounded suite evidence identity and layer summary linked to the immutable publication fingerprint

#### Scenario: Later draft evolution does not invalidate history
- **WHEN** the originating draft returns to review, changes revision or verification plan, or is otherwise replaced after a successful publication
- **THEN** verification evidence for the prior immutable publication remains readable and validates against its frozen release binding

#### Scenario: Tampered evidence or frozen binding fails closed
- **WHEN** persisted verification evidence or its frozen release binding has an unsupported version or mismatched plan, runner, executor, manifest, approved revision, tenant/source, or Bundle fingerprint
- **THEN** reload and activation reject the publication evidence without consulting mutable draft state, exposing partial evidence, or changing the active pointer

#### Scenario: Legacy evidence migration is explicit
- **WHEN** an older publication has verification evidence but no frozen release binding
- **THEN** the catalog classifies it through an explicit legacy compatibility path or additive backfill procedure and never fabricates a production-valid binding from the current mutable draft

### Requirement: Durable catalog persists audit-evidence envelopes safely
The durable semantic catalog SHALL persist versioned bounded audit-evidence envelopes for lifecycle, publication, activation, and rollback entries. Each envelope SHALL include explicit schema version, artifact kind, tenant/source scope fingerprints, subject references, event identity, event kind, predecessor links, safe outcome/status, entry fingerprint, and bounded payload fields. The catalog SHALL reject unsupported versions, mismatched fingerprints, unsafe payloads, credentials, raw prompts, SQL/MQL, physical names, resolved deployment values, native objects, unrestricted sample values, and raw backend exceptions on write or read.

#### Scenario: Audit evidence survives restart
- **WHEN** lifecycle and publication audit-evidence entries are persisted and a new catalog worker starts
- **THEN** the worker can reload the same bounded entries by scoped subject reference and validate their fingerprints and envelope metadata

#### Scenario: Unsafe audit payload is rejected
- **WHEN** a caller attempts to persist an audit-evidence entry containing raw credentials, resolved deployment values, SQL/MQL, physical names, raw sample rows, native objects, or unbounded text
- **THEN** the catalog rejects the write before persistence and exposes only a normalized safe error

### Requirement: Durable audit evidence validates immutable publication cross-links
For published artifacts, durable audit-evidence reload SHALL validate entries against immutable publication records, accepted-assertion manifest, Verification Suite evidence, publish audit record, frozen release binding, tenant/source scope, and Bundle fingerprint. Reload SHALL NOT require or compare the current mutable assembly draft row for immutable publication history.

#### Scenario: Tampered publication audit evidence fails closed
- **WHEN** persisted audit evidence references a different manifest, verification evidence, publish audit, frozen release binding, tenant/source scope, or Bundle fingerprint than the immutable publication records
- **THEN** reload and activation reject the affected publication evidence without exposing partial records or changing the active pointer

#### Scenario: Draft evolution does not invalidate publication history
- **WHEN** the source draft changes revision, review state, lint status, or verification plan after a publication succeeds
- **THEN** durable audit evidence for the prior publication remains readable and validates against immutable publication records rather than the current draft row

### Requirement: Durable audit history is queryable with bounded retention
The durable catalog SHALL provide scoped bounded lookup of audit-evidence entries by draft, assertion, Bundle fingerprint, publication, activation, rollback, and predecessor reference. Retention and cleanup SHALL preserve entries required by active publications, active pointers, supersession chains, rollback targets, and configured audit retention policy.

#### Scenario: Scoped lookup returns ordered history
- **WHEN** a host lists audit-evidence entries for a scoped Bundle fingerprint or draft reference
- **THEN** the catalog returns a deterministic bounded sequence with cursor metadata when more entries exist

#### Scenario: Cleanup preserves active audit dependencies
- **WHEN** bounded cleanup removes expired inactive audit-evidence entries
- **THEN** entries required to explain active publications, active pointers, supersession chains, rollback targets, and retained publish audit records remain available

### Requirement: Durable envelopes record and validate canonicalization profiles
The durable semantic catalog SHALL record the canonicalization profile used for fingerprint-critical persisted envelopes, including metadata snapshots, proposal sets, assembly drafts, published Bundles, accepted manifests, verification evidence, audit evidence, active pointers, workflow-compatible references, and publication aggregates where applicable. Reload SHALL validate the declared profile, canonical bytes, fingerprint, schema version, tenant/source scope, and artifact kind before exposing an artifact as current.

#### Scenario: Reload rejects unsupported profile
- **WHEN** a persisted envelope declares an unsupported or unknown canonicalization profile
- **THEN** the catalog returns a safe incompatible-profile error and does not expose the artifact as validated current content

#### Scenario: Legacy profile is preserved explicitly
- **WHEN** an existing persisted record was written before strict JCS-compatible canonicalization metadata existed
- **THEN** the catalog classifies it through an explicit legacy profile or migration path and never silently rewrites its fingerprint as current-profile evidence

### Requirement: Catalog envelope payloads are JSON-safe before canonicalization
Catalog persistence SHALL validate fingerprint-critical envelope payloads as JSON-safe values before canonicalization. It SHALL reject native objects, datetimes, sets, bytes, callables, exceptions, non-finite numbers, raw prompts, raw queries/results, credentials, resolved deployment values, physical names outside approved artifact boundaries, and unbounded text before write or reload validation.

#### Scenario: Unsafe envelope write is rejected
- **WHEN** a catalog write attempts to store an unsafe native value or forbidden sensitive value in a fingerprint-critical envelope
- **THEN** the write fails before persistence and returns a normalized safe error without producing a fingerprint

