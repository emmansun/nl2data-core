# durable-semantic-catalog Delta

## MODIFIED Requirements

### Requirement: PostgreSQL semantic catalog persists safe lifecycle artifacts
The PostgreSQL semantic catalog SHALL persist versioned safe representations of authorized MetadataSnapshots, SemanticProposalSets, AssemblyDrafts, assertion review state, immutable Semantic Model Bundle publications, publish audit records, supersession chains, deployment binding references, active pointers, and rollback history. It SHALL keep semantic catalog data separate from workflow state tables and SHALL never persist resolved credentials or raw source data.

#### Scenario: Snapshot survives process restart
- **WHEN** a valid snapshot is registered and the catalog process stops and reconnects
- **THEN** the same snapshot can be loaded by fingerprint with equivalent canonical content and scope

#### Scenario: Assembly draft survives process restart
- **WHEN** an assembly draft with assertions, review state, deployment binding references, and draft revision is persisted and a new catalog instance starts
- **THEN** the draft can be loaded with equivalent lifecycle state and without a semantic bundle fingerprint

#### Scenario: Bundle publication survives process restart
- **WHEN** a validated approved assembly is published and a new catalog instance starts
- **THEN** the published Bundle, semantic fingerprint, publish audit record, and supersession metadata can be retrieved without reconstructing them from raw source data

### Requirement: Publish and activate are atomic and idempotent
The catalog SHALL publish only verified approved semantic content, compute or verify the semantic fingerprint at the publish boundary, atomically persist the immutable artifact, publish audit record, and supersession-chain update, and atomically activate one complete compatible version per catalog scope. Repeated publish of identical semantic content SHALL be idempotent by fingerprint. Concurrent activation SHALL serialize without exposing partial content.

#### Scenario: Concurrent activation leaves one complete active version
- **WHEN** multiple workers activate compatible candidate versions concurrently
- **THEN** each completed operation observes a complete published version and the catalog exposes exactly one final active pointer

#### Scenario: Failed activation preserves the active version
- **WHEN** validation, dependency, freshness, drift, scope, or database checks fail during activation
- **THEN** no active pointer changes and the failure is safe and retry-classified where appropriate

#### Scenario: Failed publish leaves no partial lifecycle records
- **WHEN** validation, verification, fingerprinting, audit persistence, supersession update, or database commit fails during publish
- **THEN** no published bundle, audit record, or supersession edge becomes externally visible

#### Scenario: Identical publish is idempotent
- **WHEN** a caller retries publish for semantic content whose fingerprint already exists in the scoped catalog
- **THEN** the catalog returns the existing publication and audit reference without creating a duplicate artifact

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

## ADDED Requirements

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