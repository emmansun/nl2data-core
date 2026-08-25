## MODIFIED Requirements

### Requirement: Workflow resume is tenant-scoped
The system SHALL retrieve checkpoints by workflow/request identity and matching tenant scope fingerprint, reject missing or mismatched scope for tenant-scoped records, and require a valid current workflow lease and fencing token before resuming execution.

#### Scenario: Cross-tenant checkpoint lookup is denied
- **WHEN** tenant B requests a workflow checkpoint created under tenant A
- **THEN** the store returns no state or a structured scope conflict without exposing the snapshot

#### Scenario: Matching scope resumes safely
- **WHEN** the same tenant scope reopens a persisted non-terminal workflow and acquires the current lease
- **THEN** it receives the immutable safe snapshot and can continue through the existing transition rules

### Requirement: Idempotency prevents conflicting duplicate requests
The system SHALL bind an idempotency key to request identity, tenant scope, workflow identity, expiry, and shared ownership state, and SHALL atomically reject reuse with a different request or scope.

#### Scenario: Repeated terminal request returns the same safe reference
- **WHEN** the same tenant submits the same idempotency key after terminal completion
- **THEN** the system returns the existing workflow/outcome fingerprint without re-executing the external query

#### Scenario: Conflicting idempotency reuse is rejected
- **WHEN** a key is reused with a different request or tenant scope
- **THEN** the store returns a structured idempotency conflict

#### Scenario: Stale completion is rejected
- **WHEN** a worker completes an idempotency key using an expired or superseded fencing token
- **THEN** completion is rejected and an existing terminal record is not overwritten

### Requirement: Recovery does not claim exactly-once execution
The system SHALL represent ambiguous post-crash recovery using safe status and evidence references, SHALL use lease expiry and fencing to recover ownership, and SHALL NOT claim that an external query executed exactly once.

#### Scenario: Crash after external execution remains reconcilable
- **WHEN** a worker terminates after external execution but before terminal state commit
- **THEN** recovery after lease expiry exposes the checkpoint/evidence state for reconciliation rather than silently replaying or claiming success

### Requirement: Checkpoint compatibility includes resolved-view identity
The system SHALL record the resolved-view identity and fingerprint in checkpoint compatibility metadata for view-bound workflows and SHALL reject resume when the current resolved view, semantic model, IR, policy, lease, or fencing token no longer matches, before any adapter execution.

#### Scenario: Stale view checkpoint is rejected
- **WHEN** a workflow checkpoint was recorded under resolved view v1 and resumes under a differently resolved view v2
- **THEN** resume is rejected with a structured stale-checkpoint error and the adapter is not invoked
