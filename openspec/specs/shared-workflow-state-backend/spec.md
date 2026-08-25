# shared-workflow-state-backend Specification

## Purpose
Define the shared PostgreSQL workflow state, idempotency, lease ownership, fencing, and recovery boundary for multi-worker execution.

## Requirements

### Requirement: Shared backend persists safe tenant-scoped state
The shared workflow backend SHALL implement the existing StateStore and IdempotencyStore contracts using PostgreSQL, persist versioned safe snapshots and bounded idempotency records, and isolate records by opaque tenant scope namespace. It SHALL reject raw prompts, queries, artifacts, results, credentials, provider objects, and native clients.

#### Scenario: State is visible across workers
- **WHEN** worker A stores a valid workflow snapshot and worker B opens the same configured backend
- **THEN** worker B can retrieve the same safe snapshot only within the matching tenant scope

#### Scenario: Cross-tenant lookup is isolated
- **WHEN** worker B requests a workflow or idempotency record under a different or missing tenant scope
- **THEN** the backend returns no record or a safe scope conflict without exposing stored state

### Requirement: PostgreSQL schema and failures are bounded
The backend SHALL use versioned migrations, bounded connection/command timeouts, connection pooling limits, and safe normalized workflow errors. Schema metadata and records SHALL be explicitly qualified by the configured namespace. It SHALL reject unsupported newer schemas and SHALL not expose DSNs, credentials, or raw backend exception text.

#### Scenario: Unsupported schema is rejected
- **WHEN** the database schema version is newer than the supported runtime
- **THEN** initialization fails with an explicit incompatible-schema error without modifying the database

#### Scenario: Fresh deployment bootstraps its schema
- **WHEN** a store opens a database for the first time with no prior schema
- **THEN** it creates its bounded schema namespace and applies the versioned migrations up to the configured target

#### Scenario: Database outage is retryable and safe
- **WHEN** a state operation cannot reach PostgreSQL or exceeds its command timeout
- **THEN** it returns a retryable structured workflow error without leaking connection details

### Requirement: Shared updates are transactional compare-and-set
State updates SHALL atomically verify workflow identity, expected revision/version, expected status, tenant scope, and current fencing ownership when supplied. A snapshot identity or tenant scope different from the requested mutation SHALL be rejected before mutation. Stale writers SHALL never silently overwrite newer state.

#### Scenario: Concurrent update has one winner
- **WHEN** two workers update one workflow from the same expected revision
- **THEN** exactly one succeeds and the other receives a structured conflict

#### Scenario: Fenced stale update is rejected
- **WHEN** a worker updates with an expired or superseded fencing token
- **THEN** the update is rejected and the stored state remains unchanged

#### Scenario: Terminal snapshot is never overwritten
- **WHEN** a worker attempts to update a workflow whose stored status is terminal
- **THEN** the update is rejected and the terminal snapshot remains unchanged

### Requirement: Idempotency is atomic across workers
The backend SHALL atomically reserve idempotency keys by request identity, workflow identity, tenant scope, and expiry, and SHALL atomically complete them with one terminal outcome fingerprint. Conflicting reuse and completion after another terminal result SHALL be rejected.

#### Scenario: Concurrent reservation has one binding
- **WHEN** two workers reserve the same key concurrently with different requests or workflows
- **THEN** one binding wins and the other receives a structured idempotency conflict

#### Scenario: Completed request is replay-safe
- **WHEN** the same tenant submits a completed idempotency key
- **THEN** the backend returns its safe workflow/outcome fingerprint and no new external execution is started

### Requirement: Workflow lease ownership is durable
The backend SHALL provide lease acquire, renew, release, and inspect operations keyed by tenant scope and workflow identity. A lease SHALL have an opaque owner, bounded expiry, and a monotonically increasing fencing token. Acquisition SHALL be atomic and shall allow takeover only after expiry.

#### Scenario: Active lease excludes another worker
- **WHEN** worker A holds a valid lease and worker B attempts to acquire the same workflow lease
- **THEN** worker B is rejected or told the lease is busy without changing ownership

#### Scenario: Expired lease can be recovered
- **WHEN** worker A's lease expires and worker B acquires the workflow lease
- **THEN** worker B receives a greater fencing token and worker A's token is no longer valid

### Requirement: Fencing protects workflow mutations and execution handoff
Lease renewal, protected state updates, idempotency completion, and the runtime handoff to adapter execution SHALL require the current owner and fencing token. Losing ownership SHALL stop further state commits and SHALL prevent the stale worker from claiming completion.

#### Scenario: Lost owner cannot commit completion
- **WHEN** worker A loses its lease before terminal persistence and worker B has taken ownership
- **THEN** worker A's completion is rejected and cannot overwrite worker B's state

#### Scenario: Execution handoff requires current fencing
- **WHEN** a worker attempts adapter execution with a missing, expired, or superseded lease token
- **THEN** the runtime rejects the handoff before starting the adapter operation

### Requirement: Recovery remains at-least-once and bounded
The backend and runtime SHALL represent crash recovery using safe checkpoint/evidence state, bounded lease expiry/retry behavior, and explicit reconciliation for ambiguous post-execution states. They SHALL NOT claim exactly-once external execution.

#### Scenario: Crash after adapter execution is ambiguous
- **WHEN** a worker crashes after external execution but before fenced terminal persistence
- **THEN** another worker can recover the safe checkpoint after lease expiry, and the system marks the external execution as potentially repeated rather than claiming exactly-once behavior

### Requirement: Cleanup never removes active ownership
Cleanup SHALL remove only bounded batches of terminal snapshots, expired idempotency records, and expired lease records. It SHALL never delete a running workflow or valid lease.

#### Scenario: Running workflow survives cleanup
- **WHEN** cleanup runs while a workflow is running under a valid lease
- **THEN** its snapshot and lease remain available
