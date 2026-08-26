# durable-semantic-catalog Specification

## Purpose
Define the optional durable PostgreSQL catalog for safe metadata snapshots, reviewed proposal sets, immutable Semantic Model Bundles, active pointers, and rollback history.

## Requirements

### Requirement: PostgreSQL semantic catalog persists safe lifecycle artifacts
The PostgreSQL semantic catalog SHALL persist versioned safe representations of authorized MetadataSnapshots, SemanticProposalSets, immutable Semantic Model Bundle publications, active pointers, and bounded lifecycle evidence. It SHALL keep semantic catalog data separate from workflow state tables.

#### Scenario: Snapshot survives process restart
- **WHEN** a valid snapshot is registered and the catalog process stops and reconnects
- **THEN** the same snapshot can be loaded by fingerprint with equivalent canonical content and scope

#### Scenario: Bundle publication survives process restart
- **WHEN** a validated Bundle is published and a new catalog instance starts
- **THEN** the published Bundle and its version/fingerprint can be retrieved without reconstructing it from raw source data

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
The catalog SHALL publish only validated immutable artifacts, reject duplicate identity/version conflicts, and atomically activate one complete compatible version per catalog scope. Concurrent activation SHALL serialize without exposing partial content.

#### Scenario: Concurrent activation leaves one complete active version
- **WHEN** multiple workers activate compatible candidate versions concurrently
- **THEN** each completed operation observes a complete published version and the catalog exposes exactly one final active pointer

#### Scenario: Failed activation preserves the active version
- **WHEN** validation, dependency, freshness, drift, scope, or database checks fail during activation
- **THEN** no active pointer changes and the failure is safe and retry-classified where appropriate

### Requirement: Rollback and reload preserve immutable history
The catalog SHALL support lookup of published versions, atomic rollback to a previously valid compatible version, and startup reload of active pointers. It SHALL never mutate or delete an artifact that is active or required by an active artifact.

#### Scenario: Rollback selects a prior valid Bundle
- **WHEN** an operator requests rollback to a previously published compatible version
- **THEN** the active pointer changes atomically, both artifact versions remain immutable, and their fingerprints remain stable

#### Scenario: Active state is reloaded
- **WHEN** a new worker initializes from the PostgreSQL catalog
- **THEN** it loads the active snapshot/Bundle references and revalidates them before query-time View resolution

### Requirement: Retention and failures are bounded and normalized
The catalog SHALL provide explicit retention and bounded cleanup for inactive artifacts and lifecycle evidence, preserve active dependencies, use bounded database timeouts, and normalize unavailable, conflict, authorization, serialization, and migration failures without leaking DSNs or backend exception text.

#### Scenario: Cleanup preserves active dependencies
- **WHEN** bounded cleanup removes expired inactive records
- **THEN** active snapshots, active Bundles, and dependencies required for active resolution remain available

#### Scenario: Database outage is safe
- **WHEN** PostgreSQL is unavailable or a catalog operation times out
- **THEN** the package returns a normalized retryable catalog error and does not report an uncommitted publication or activation as successful
